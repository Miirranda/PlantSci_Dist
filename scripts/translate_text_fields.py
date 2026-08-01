#!/usr/bin/env python3
"""
为标注 JSON 文件中 review_evidences 的英文 "text" 字段翻译为中文，添加 "text_zh" 字段。

后端：网易有道词典 API
用法：
    # 单文件模式（原行为）
    python scripts/translate_text_fields.py <input.json> [output.json]

    # 拼接 JSON 模式（支持即翻译即写入 + 断点续传）
    python scripts/translate_text_fields.py --concat <input.json> [output.json]

机制：
    - 仅翻译 system_retrieval.review_evidences[*].text
    - 去重后逐条调用有道 API 翻译
    - 翻译结果缓存到 .translation_cache.json，再次运行不会重复翻译
    - --concat 模式：每完成一个 sample 即写入输出 JSON，中断可续跑
"""

import argparse
import json
import hashlib
import random
import sys
import time
import urllib.request
import urllib.parse
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


def load_cache():
    """加载翻译缓存"""
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_cache(cache):
    """保存翻译缓存"""
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


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
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start is not None:
                segment = raw[start:i + 1]
                try:
                    obj = json.loads(segment, strict=False)
                    batches.append(obj)
                except json.JSONDecodeError as e:
                    print(f"  [warn] 跳过无效 JSON 片段 (pos {start}): {e}")
                start = None

    if not batches:
        raise ValueError("未找到有效的 JSON 对象，请确认文件格式。")

    print(f"  [concat] 解析到 {len(batches)} 个 batch")

    # 合并元数据
    merged = dict(batches[0])
    seen = {}  # sample_id -> sample

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


# ── 文本收集 ──────────────────────────────────────────────────────────────

def collect_review_texts(data):
    """收集所有 review_evidences 中的唯一 text 值（去重）。"""
    texts = {}
    for sample in data.get("samples", []):
        review = (sample.get("system_retrieval", {}).get("review_evidences") or [])
        for item in review:
            t = item.get("text", "").strip()
            if t and t not in texts:
                texts[t] = item.get("sentence_id")
    return texts  # {text: sentence_id}


def collect_sample_texts_needing_translation(sample):
    """收集单个 sample 中 review_evidences 缺少 text_zh 的 text。

    返回: {text: sentence_id} 或空 dict
    """
    texts = {}
    review = (sample.get("system_retrieval", {}).get("review_evidences") or [])
    for item in review:
        t = item.get("text", "").strip()
        # 只收集缺少 text_zh 或 text_zh 为空的条目
        if t and not item.get("text_zh", "").strip():
            texts[t] = item.get("sentence_id")
    return texts


# ── 翻译 ──────────────────────────────────────────────────────────────────

def translate_youdao(text, from_lang="en", to_lang="zh-CHS", max_retries=3):
    """调用有道翻译 API 翻译单条文本。

    返回: 翻译后的中文文本，失败返回 None
    """
    for attempt in range(max_retries):
        salt = str(random.randint(1, 65536))
        sign_str = YOUDAO_APP_KEY + text + salt + YOUDAO_API_KEY
        sign = hashlib.md5(sign_str.encode("utf-8")).hexdigest()

        params = {
            "q": text,
            "from": from_lang,
            "to": to_lang,
            "appKey": YOUDAO_APP_KEY,
            "salt": salt,
            "sign": sign,
        }

        try:
            data = urllib.parse.urlencode(params).encode("utf-8")
            req = urllib.request.Request(YOUDAO_API_URL, data=data)
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            error_code = result.get("errorCode", "")
            if error_code == "0":
                translation = result.get("translation", [])
                if translation:
                    return translation[0]
                return None
            elif error_code == "411":
                # Rate limit, wait and retry
                if attempt < max_retries - 1:
                    time.sleep(3)
                    continue
                print(f"    [ERR] Youdao rate limit (411), text[:50]={text[:50]}...")
                return None
            else:
                print(f"    [ERR] Youdao errorCode={error_code}, text[:50]={text[:50]}...")
                return None

        except Exception as e:
            print(f"    [ERR] attempt {attempt+1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return None

    return None


# ── 回填 ──────────────────────────────────────────────────────────────────

def add_review_zh(data, translation_map):
    """仅给 review_evidences 添加 text_zh 字段（批量回填）"""
    count = 0
    for sample in data.get("samples", []):
        review = (sample.get("system_retrieval", {}).get("review_evidences") or [])
        for item in review:
            t = item.get("text", "").strip()
            if t in translation_map:
                item["text_zh"] = translation_map[t]
                count += 1
    return count


def add_review_zh_to_sample(sample, translation_map):
    """仅给单个 sample 的 review_evidences 添加 text_zh 字段。

    返回: 该 sample 中新增的 text_zh 数量
    """
    count = 0
    review = (sample.get("system_retrieval", {}).get("review_evidences") or [])
    for item in review:
        t = item.get("text", "").strip()
        if t in translation_map:
            item["text_zh"] = translation_map[t]
            count += 1
    return count


# ── 主流程 ────────────────────────────────────────────────────────────────

def process_single_file(input_file, output_file, cache):
    """单文件模式：收集全部 → 翻译 → 一次性写入（原行为）"""
    print("[Loading] JSON...")
    with open(input_file, "r", encoding="utf-8") as f:
        raw = f.read()
    data = json.loads(raw, strict=False)

    # 收集 review_evidences 中的 text
    print("[Collect] review_evidences text fields...")
    review_texts = collect_review_texts(data)
    print(f"  unique texts in review_evidences: {len(review_texts)}")

    # 统计每个 sample 的 review 条数
    sample_counts = {}
    for sample in data.get("samples", []):
        sid = sample.get("sample_id", "?")
        n = len(sample.get("system_retrieval", {}).get("review_evidences") or [])
        sample_counts[sid] = n

    # 找出待翻译的
    to_translate = {t: sid for t, sid in review_texts.items() if t not in cache}
    print(f"  cached translations: {len(cache)}")
    print(f"  need translate: {len(to_translate)}")

    if to_translate:
        total = len(to_translate)
        for i, (text, sid) in enumerate(to_translate.items(), 1):
            print(f"\n[{i}/{total}] sentence_id={sid}")
            preview = text[:80].replace("\n", " ")
            print(f"  EN: {preview}...")

            translation = translate_youdao(text)
            if translation:
                cache[text] = translation
                save_cache(cache)
                print(f"  ZH: {translation[:80]}...")
                print(f"  [OK] cached ({len(cache)} entries)")
            else:
                print(f"  [WARN] translation failed, skip (re-run to continue)")

            if i < total:
                time.sleep(1.2)

    # 回填
    print(f"\n[Writing] adding text_zh to review_evidences...")
    translation_map = {k: v for k, v in cache.items() if k in review_texts}
    count = add_review_zh(data, translation_map)
    print(f"  added {count} text_zh fields")

    # 写入
    print(f"\n[Saving] {output_file}...")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print("[Done]!")
    print(f"\n  unique review texts: {len(review_texts)}")
    print(f"  translated: {len(translation_map)}")
    print(f"  text_zh fields: {count}")


def process_concat(input_file, output_file, cache):
    """拼接 JSON 模式：按 sample 逐个翻译 → 即翻译即写入（断点续传）"""
    print("[Loading] concatenated JSON...")
    data = parse_concatenated_json(input_file)

    samples = data.get("samples", [])
    total_samples = len(samples)
    print(f"  total samples: {total_samples}")

    # 统计缺失 text_zh 的情况
    total_missing = 0
    for sample in samples:
        missing = collect_sample_texts_needing_translation(sample)
        total_missing += len(missing)
    print(f"  total review_evidences missing text_zh: {total_missing}")
    print(f"  cached translations: {len(cache)}")

    if total_missing == 0:
        print("[OK] All samples already have text_zh in review_evidences")
        print(f"\n[Saving] {output_file}...")
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("[Done]!")
        return

    # 按 sample 逐个处理
    processed_count = 0
    translated_this_run = 0

    for i, sample in enumerate(samples):
        sid = sample.get("sample_id", f"sample_{i}")
        missing = collect_sample_texts_needing_translation(sample)

        if not missing:
            continue

        # 过滤出缓存中已有的和需要翻译的
        to_translate = {t: sid for t, sid in missing.items() if t not in cache}
        already_cached = {t: sid for t, sid in missing.items() if t in cache}

        if already_cached:
            # 从缓存回填
            cached_map = {t: cache[t] for t in already_cached}
            n = add_review_zh_to_sample(sample, cached_map)
            processed_count += n

        if to_translate:
            print(f"\n[{i+1}/{total_samples}] {sid} — {len(to_translate)} texts to translate")

            for j, (text, sentence_id) in enumerate(to_translate.items(), 1):
                preview = text[:80].replace("\n", " ")
                print(f"  [{j}/{len(to_translate)}] sentence_id={sentence_id}")
                print(f"    EN: {preview}...")

                translation = translate_youdao(text)
                if translation:
                    cache[text] = translation
                    save_cache(cache)
                    cache_map = {text: translation}
                    n = add_review_zh_to_sample(sample, cache_map)
                    processed_count += n
                    translated_this_run += 1
                    print(f"    ZH: {translation[:80]}...")
                    print(f"    [OK] cached ({len(cache)} entries)")
                else:
                    print(f"    [WARN] translation failed, skip (re-run to continue)")

                # 即翻译即写入：每完成一条翻译就写回输出文件
                if j < len(to_translate):
                    with open(output_file, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)

                # API 限速
                if j < len(to_translate):
                    time.sleep(1.2)
        else:
            # 全部命中缓存，只回填不调 API
            cached_map = {t: cache[t] for t in missing}
            n = add_review_zh_to_sample(sample, cached_map)
            processed_count += n

        # 每个 sample 处理完写入一次（保证即翻译即写入）
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        # 限速（sample 之间也稍作停顿）
        if to_translate and i + 1 < total_samples:
            time.sleep(0.5)

    print(f"\n[Done]!")
    print(f"  total samples: {total_samples}")
    print(f"  text_zh fields added this run: {processed_count}")
    print(f"  new translations (API calls): {translated_this_run}")
    print(f"  cache entries: {len(cache)}")
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
        "--concat", "-c",
        action="store_true",
        help="启用拼接 JSON 模式：解析多对象拼接的 JSON，按 sample 逐个翻译并即写输出",
    )
    args = parser.parse_args()

    input_file = args.input
    output_file = args.output if args.output else input_file

    print(f"[Input]  {input_file}")
    print(f"[Output] {output_file}")
    print(f"[Mode]  {'concat (incremental write)' if args.concat else 'single file'}")
    print(f"[Backend] Youdao API")

    # 加载缓存
    cache = load_cache()

    if args.concat:
        process_concat(input_file, output_file, cache)
    else:
        process_single_file(input_file, output_file, cache)


if __name__ == "__main__":
    main()
