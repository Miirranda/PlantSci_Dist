#!/usr/bin/env python3
"""
为标注 JSON 文件中 review_evidences 的英文 "text" 字段翻译为中文，添加 "text_zh" 字段。

后端：网易有道词典 API
用法：
    python scripts/translate_text_fields.py <input.json> [output.json]

机制：
    - 仅翻译 system_retrieval.review_evidences[*].text
    - 去重后逐条调用有道 API 翻译
    - 翻译结果缓存到 .translation_cache.json，再次运行不会重复翻译
"""

import json
import hashlib
import random
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

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


def add_review_zh(data, translation_map):
    """仅给 review_evidences 添加 text_zh 字段"""
    count = 0
    for sample in data.get("samples", []):
        review = (sample.get("system_retrieval", {}).get("review_evidences") or [])
        for item in review:
            t = item.get("text", "").strip()
            if t in translation_map:
                item["text_zh"] = translation_map[t]
                count += 1
    return count


def main():
    if len(sys.argv) < 2:
        print(f"用法: python {sys.argv[0]} <input.json> [output.json]")
        print(f"示例: python {sys.argv[0]} data/annotations/P001/P001_A001_annotation_draft_smoke10_readable.json")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) > 2 else input_file

    print(f"[Input]  {input_file}")
    print(f"[Output] {output_file}")
    print(f"[Backend] Youdao API")

    # 加载
    print("\n[Loading] JSON...")
    with open(input_file, "r", encoding="utf-8") as f:
        raw = f.read()
    data = json.loads(raw, strict=False)

    # 收集 review_evidences 中的 text
    print("[Collect] review_evidences text fields...")
    review_texts = collect_review_texts(data)  # {text: sentence_id}
    print(f"  unique texts in review_evidences: {len(review_texts)}")

    # 统计每个 sample 的 review 条数
    sample_counts = {}
    for sample in data.get("samples", []):
        sid = sample.get("sample_id", "?")
        n = len(sample.get("system_retrieval", {}).get("review_evidences") or [])
        sample_counts[sid] = n
    print(f"  review counts per sample: {sample_counts}")

    # 加载缓存
    cache = load_cache()
    print(f"  cached translations: {len(cache)}")

    # 找出待翻译的
    to_translate = {t: sid for t, sid in review_texts.items() if t not in cache}
    print(f"  need translate: {len(to_translate)}")

    if not to_translate:
        print("[OK] All texts already translated (cache hit)")
    else:
        total = len(to_translate)
        for i, (text, sid) in enumerate(to_translate.items(), 1):
            print(f"\n[{i}/{total}] sentence_id={sid}")
            # 显示原文前 80 字
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

            # 有道免费版 QPS 限制约 1次/秒
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


if __name__ == "__main__":
    main()
