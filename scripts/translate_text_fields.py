#!/usr/bin/env python3
"""
为标注 JSON 文件中 review_evidences 的英文 "text" 字段翻译为中文，添加 "text_zh" 字段。

后端：网易有道词典 API
用法：
    # 单文件模式（默认覆盖输入文件）
    python scripts/translate_text_fields.py <input.json> [output.json]

    # 拼接 JSON 模式（支持即翻译即写入 + 断点续传）
    python scripts/translate_text_fields.py --concat <input.json> [output.json]

机制：
    - 仅翻译 system_retrieval.review_evidences[*].text
    - 已有非空 text_zh 的句子不请求 API，并回灌本地缓存
    - 按规范化原文 + paper_id:sentence_id 去重/命中缓存
    - 翻译结果缓存到 .translation_cache.json，再次运行不会重复翻译
    - 成功请求间隔约 0.2s；411 限流指数退避
    - --concat 模式：每完成一个 sample 即写入输出 JSON，中断可续跑
"""

import argparse
import hashlib
import json
import random
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── 有道 API 配置 ─────────────────────────────────────────────────────────────
YOUDAO_APP_KEY = "79afc673b6d36195"
YOUDAO_API_KEY = "V1SnSlfxhM3oyH6JxcsfM8w5os3MZf7B"
YOUDAO_API_URL = "https://openapi.youdao.com/api"

CACHE_FILE = Path(__file__).resolve().parent.parent / ".translation_cache.json"

# 有道默认约 QPS=10；0.2s ≈ 5 QPS，留余量
SUCCESS_INTERVAL_SEC = 0.2
RATE_LIMIT_BASE_SEC = 3.0


def empty_cache():
    return {"by_text": {}, "by_sid": {}}


def normalize_text(text):
    """折叠换行与多余空白，避免近重复原文各打一次 API。"""
    if not text:
        return ""
    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", text).strip()


def make_sid_key(paper_id, sentence_id):
    if paper_id is None or sentence_id is None or sentence_id == "":
        return ""
    return "%s:%s" % (str(paper_id).strip(), str(sentence_id).strip())


def load_cache():
    """加载翻译缓存。兼容旧版 {原文: 译文} 扁平结构。"""
    if not CACHE_FILE.exists():
        return empty_cache()
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return empty_cache()
    if not isinstance(raw, dict):
        return empty_cache()

    if "by_text" in raw or "by_sid" in raw:
        by_text = {}
        for k, v in (raw.get("by_text") or {}).items():
            if isinstance(v, str) and v.strip():
                by_text[normalize_text(k)] = v.strip()
        by_sid = {}
        for k, v in (raw.get("by_sid") or {}).items():
            if isinstance(v, str) and v.strip() and k:
                by_sid[str(k)] = v.strip()
        return {"by_text": by_text, "by_sid": by_sid}

    by_text = {}
    for k, v in raw.items():
        if isinstance(v, str) and v.strip() and k not in ("by_text", "by_sid"):
            by_text[normalize_text(k)] = v.strip()
    return {"by_text": by_text, "by_sid": {}}


def save_cache(cache):
    payload = {
        "by_text": cache.get("by_text") or {},
        "by_sid": cache.get("by_sid") or {},
    }
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def cache_size(cache):
    return len(cache.get("by_text") or {}) + len(cache.get("by_sid") or {})


def cache_get(cache, text, paper_id=None, sentence_id=None):
    sid_key = make_sid_key(paper_id, sentence_id)
    by_sid = cache.get("by_sid") or {}
    if sid_key and sid_key in by_sid:
        return by_sid[sid_key]
    norm = normalize_text(text)
    if not norm:
        return None
    return (cache.get("by_text") or {}).get(norm)


def cache_put(cache, text, zh, paper_id=None, sentence_id=None):
    """写入缓存。已有相同译文时返回 False。"""
    zh = (zh or "").strip()
    norm = normalize_text(text)
    if not zh:
        return False
    changed = False
    by_text = cache.setdefault("by_text", {})
    by_sid = cache.setdefault("by_sid", {})
    if norm and by_text.get(norm) != zh:
        by_text[norm] = zh
        changed = True
    sid_key = make_sid_key(paper_id, sentence_id)
    if sid_key and by_sid.get(sid_key) != zh:
        by_sid[sid_key] = zh
        changed = True
    return changed


def dump_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def sample_paper_id(sample, data=None):
    return (
        sample.get("paper_id")
        or (data or {}).get("paper_id")
        or ""
    )


def iter_review_items(data):
    for sample in data.get("samples") or []:
        paper_id = sample_paper_id(sample, data)
        review = (sample.get("system_retrieval") or {}).get("review_evidences") or []
        for item in review:
            yield sample, paper_id, item


# ── 拼接 JSON 解析 ─────────────────────────────────────────────────────────

def parse_concatenated_json(filepath: str) -> dict:
    """解析由多个顶层 JSON 对象拼接而成的文件，合并为一个 JSON。

    每个顶层对象代表一个 batch，包含 ``samples`` 数组。
    合并规则：
    - samples 按 sample_id 去重（重复时保留最后一个）
    - 元数据取第一个 batch 的字段，sample_count 更新为实际总数
    """
    with open(filepath, "r", encoding="utf-8") as f:
        raw = f.read()

    batches = []
    depth = 0
    start = None

    for i, ch in enumerate(raw):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                segment = raw[start : i + 1]
                try:
                    obj = json.loads(segment, strict=False)
                    batches.append(obj)
                except json.JSONDecodeError as e:
                    print(f"  [warn] 跳过无效 JSON 片段 (pos {start}): {e}")
                start = None

    if not batches:
        raise ValueError("未找到有效的 JSON 对象，请确认文件格式。")

    print(f"  [concat] 解析到 {len(batches)} 个 batch")

    merged = dict(batches[0])
    seen = {}

    for batch in batches:
        for sample in batch.get("samples", []):
            sid = sample.get("sample_id", "")
            if sid:
                if sid in seen:
                    print(f"  [concat] 重复 sample_id={sid}，以最后出现的版本覆盖")
                seen[sid] = sample

    merged["samples"] = list(seen.values())
    merged["sample_count"] = len(merged["samples"])
    merged["limit"] = f"C01:C{merged['sample_count']}"
    merged["_description"] = (
        merged.get("_description", "")
        + f"\n  [合并] 由 {len(batches)} 个 batch 拼接，去重后 {len(merged['samples'])} 个样本。"
    )
    print(f"  [concat] 合并后样本数: {len(merged['samples'])}")
    return merged


# ── 文本收集 / 回填 ─────────────────────────────────────────────────────────

def seed_cache_from_data(data, cache):
    """把稿里已有的 text_zh 回灌缓存，避免 cache 丢失后整份重译。"""
    added = 0
    for _, paper_id, item in iter_review_items(data):
        t = item.get("text") or ""
        zh = (item.get("text_zh") or "").strip()
        if not normalize_text(t) or not zh:
            continue
        if cache_put(cache, t, zh, paper_id, item.get("sentence_id")):
            added += 1
    return added


def fill_from_cache(data, cache, sample=None):
    """给缺少 text_zh 的条目用缓存回填。sample 为 None 时处理全文。"""
    count = 0
    samples = [sample] if sample is not None else (data.get("samples") or [])
    for s in samples:
        paper_id = sample_paper_id(s, data)
        review = (s.get("system_retrieval") or {}).get("review_evidences") or []
        for item in review:
            t = item.get("text") or ""
            if not normalize_text(t):
                continue
            if (item.get("text_zh") or "").strip():
                continue
            hit = cache_get(cache, t, paper_id, item.get("sentence_id"))
            if hit:
                item["text_zh"] = hit
                count += 1
    return count


def collect_pending_unique(data, cache, sample=None):
    """收集仍需打 API 的唯一原文（规范化去重）。

    返回: {norm_text: {"paper_id", "sentence_id"}}
    """
    pending = {}
    samples = [sample] if sample is not None else (data.get("samples") or [])
    for s in samples:
        paper_id = sample_paper_id(s, data)
        review = (s.get("system_retrieval") or {}).get("review_evidences") or []
        for item in review:
            t = item.get("text") or ""
            norm = normalize_text(t)
            if not norm:
                continue
            if (item.get("text_zh") or "").strip():
                continue
            if cache_get(cache, t, paper_id, item.get("sentence_id")):
                continue
            if norm not in pending:
                pending[norm] = {
                    "paper_id": paper_id,
                    "sentence_id": item.get("sentence_id"),
                }
    return pending


def apply_translation(data, cache, text, zh, paper_id=None, sentence_id=None, sample=None):
    """写入缓存，并回填所有匹配（规范化原文或同 paper+sentence_id）的缺译文条目。"""
    cache_put(cache, text, zh, paper_id, sentence_id)
    return fill_from_cache(data, cache, sample=sample)


def count_review_stats(data):
    unique = set()
    total = 0
    missing = 0
    for _, _, item in iter_review_items(data):
        norm = normalize_text(item.get("text") or "")
        if not norm:
            continue
        total += 1
        unique.add(norm)
        if not (item.get("text_zh") or "").strip():
            missing += 1
    return len(unique), total, missing


# ── 翻译 ──────────────────────────────────────────────────────────────────

def translate_youdao(text, from_lang="en", to_lang="zh-CHS", max_retries=3):
    """调用有道翻译 API 翻译单条文本。

    返回: 翻译后的中文文本，失败返回 None
    """
    query = normalize_text(text)
    if not query:
        return None

    for attempt in range(max_retries):
        salt = str(random.randint(1, 65536))
        sign_str = YOUDAO_APP_KEY + query + salt + YOUDAO_API_KEY
        sign = hashlib.md5(sign_str.encode("utf-8")).hexdigest()

        params = {
            "q": query,
            "from": from_lang,
            "to": to_lang,
            "appKey": YOUDAO_APP_KEY,
            "salt": salt,
            "sign": sign,
        }

        try:
            payload = urllib.parse.urlencode(params).encode("utf-8")
            req = urllib.request.Request(YOUDAO_API_URL, data=payload)
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            error_code = result.get("errorCode", "")
            if error_code == "0":
                translation = result.get("translation", [])
                if translation:
                    return translation[0]
                return None
            if error_code == "411":
                if attempt < max_retries - 1:
                    wait = RATE_LIMIT_BASE_SEC * (2 ** attempt)
                    print(f"    [411] rate limit, backoff {wait:.1f}s")
                    time.sleep(wait)
                    continue
                print(f"    [ERR] Youdao rate limit (411), text[:50]={query[:50]}...")
                return None
            print(f"    [ERR] Youdao errorCode={error_code}, text[:50]={query[:50]}...")
            return None

        except Exception as e:
            print(f"    [ERR] attempt {attempt+1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return None

    return None


# ── 主流程 ────────────────────────────────────────────────────────────────

def process_single_file(input_file, output_file, cache):
    """单文件模式：跳过已有译文 → 缓存回填 → 只对缺口打 API → 就地写回。"""
    print("[Loading] JSON...")
    with open(input_file, "r", encoding="utf-8") as f:
        raw = f.read()
    data = json.loads(raw, strict=False)

    unique_n, total_n, missing_before = count_review_stats(data)
    print("[Collect] review_evidences text fields...")
    print(f"  review items: {total_n}")
    print(f"  unique texts (normalized): {unique_n}")
    print(f"  missing text_zh: {missing_before}")

    seeded = seed_cache_from_data(data, cache)
    if seeded:
        save_cache(cache)
        print(f"  seeded cache from existing text_zh: {seeded}")

    filled = fill_from_cache(data, cache)
    if filled:
        print(f"  filled from cache (no API): {filled}")

    pending = collect_pending_unique(data, cache)
    print(f"  cache entries: {cache_size(cache)}")
    print(f"  need translate (API): {len(pending)}")

    api_ok = 0
    api_fail = 0
    if pending:
        total = len(pending)
        for i, (text, meta) in enumerate(pending.items(), 1):
            sid = meta.get("sentence_id")
            paper_id = meta.get("paper_id")
            print(f"\n[{i}/{total}] sentence_id={sid}")
            preview = text[:80]
            print(f"  EN: {preview}...")

            translation = translate_youdao(text)
            if translation:
                n = apply_translation(
                    data, cache, text, translation, paper_id, sid
                )
                save_cache(cache)
                dump_json(output_file, data)
                api_ok += 1
                print(f"  ZH: {translation[:80]}...")
                print(f"  [OK] filled {n} field(s), cache={cache_size(cache)}")
            else:
                api_fail += 1
                print("  [WARN] translation failed, skip (re-run to continue)")

            if i < total:
                time.sleep(SUCCESS_INTERVAL_SEC)

    # 再扫一遍，吃掉本轮新写入的 cache
    extra = fill_from_cache(data, cache)
    dump_json(output_file, data)

    _, _, missing_after = count_review_stats(data)
    print("[Done]!")
    print(f"\n  unique review texts: {unique_n}")
    print(f"  API ok/fail: {api_ok}/{api_fail}")
    print(f"  extra cache fills: {extra}")
    print(f"  missing text_zh after: {missing_after}")
    print(f"  output: {output_file}")


def process_concat(input_file, output_file, cache):
    """拼接 JSON 模式：按 sample 逐个翻译 → 即翻译即写入（断点续传）"""
    print("[Loading] concatenated JSON...")
    data = parse_concatenated_json(input_file)

    samples = data.get("samples", [])
    total_samples = len(samples)
    print(f"  total samples: {total_samples}")

    seeded = seed_cache_from_data(data, cache)
    if seeded:
        save_cache(cache)
        print(f"  seeded cache from existing text_zh: {seeded}")

    unique_n, total_n, missing_before = count_review_stats(data)
    print(f"  review items: {total_n}")
    print(f"  unique texts (normalized): {unique_n}")
    print(f"  missing text_zh: {missing_before}")
    print(f"  cache entries: {cache_size(cache)}")

    if missing_before == 0:
        print("[OK] All samples already have text_zh in review_evidences")
        dump_json(output_file, data)
        print("[Done]!")
        return

    processed_count = 0
    translated_this_run = 0

    for i, sample in enumerate(samples):
        sid = sample.get("sample_id", f"sample_{i}")
        filled = fill_from_cache(data, cache, sample=sample)
        processed_count += filled
        pending = collect_pending_unique(data, cache, sample=sample)

        if not pending:
            if filled:
                dump_json(output_file, data)
            continue

        print(f"\n[{i+1}/{total_samples}] {sid} — {len(pending)} texts to translate")
        paper_id = sample_paper_id(sample, data)

        for j, (text, meta) in enumerate(pending.items(), 1):
            sentence_id = meta.get("sentence_id")
            preview = text[:80]
            print(f"  [{j}/{len(pending)}] sentence_id={sentence_id}")
            print(f"    EN: {preview}...")

            translation = translate_youdao(text)
            if translation:
                n = apply_translation(
                    data,
                    cache,
                    text,
                    translation,
                    paper_id,
                    sentence_id,
                    sample=sample,
                )
                save_cache(cache)
                processed_count += n
                translated_this_run += 1
                print(f"    ZH: {translation[:80]}...")
                print(f"    [OK] filled {n}, cache={cache_size(cache)}")
            else:
                print("    [WARN] translation failed, skip (re-run to continue)")

            dump_json(output_file, data)
            if j < len(pending):
                time.sleep(SUCCESS_INTERVAL_SEC)

        dump_json(output_file, data)

    extra = fill_from_cache(data, cache)
    dump_json(output_file, data)

    print("\n[Done]!")
    print(f"  total samples: {total_samples}")
    print(f"  text_zh fields added this run: {processed_count + extra}")
    print(f"  new translations (API calls): {translated_this_run}")
    print(f"  cache entries: {cache_size(cache)}")
    print(f"  output: {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description="为 review_evidences 英文文本翻译为中文，添加 text_zh 字段"
    )
    parser.add_argument(
        "input",
        help="输入 JSON 文件路径",
    )
    parser.add_argument(
        "output",
        nargs="?",
        default=None,
        help="输出 JSON 文件路径（默认覆盖输入文件）",
    )
    parser.add_argument(
        "--concat",
        "-c",
        action="store_true",
        help="启用拼接 JSON 模式：解析多对象拼接的 JSON，按 sample 逐个翻译并即写输出",
    )
    args = parser.parse_args()

    input_file = args.input
    output_file = args.output if args.output else input_file

    print(f"[Input]  {input_file}")
    print(f"[Output] {output_file}")
    print(f"[Mode]  {'concat (incremental write)' if args.concat else 'single file'}")
    print("[Backend] Youdao API")

    cache = load_cache()

    if args.concat:
        process_concat(input_file, output_file, cache)
    else:
        process_single_file(input_file, output_file, cache)


if __name__ == "__main__":
    main()
