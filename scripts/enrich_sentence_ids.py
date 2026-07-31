"""将 P001_sentences.csv 中的句子文本补充到 annotation draft 的 sentence_ids 中。

将 gold_retrieval.sentence_ids 从纯整数数组转换为包含 id + text 的对象数组，
方便人工审核时直接看到对应句子的内容。
"""

import csv
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
JSON_PATH = PROJECT_ROOT / "data" / "annotations" / "P001" / "P001_A001_annotation_draft.json"
CSV_PATH = PROJECT_ROOT / "data" / "annotations" / "P001" / "P001_sentences.csv"


def load_sentence_map(csv_path: Path) -> dict[int, str]:
    """从 CSV 中加载 sentence_id → text 的映射."""
    sentence_map: dict[int, str] = {}
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = int(row["sentence_id"])
            sentence_map[sid] = row["text"]
    print(f"Loaded {len(sentence_map)} sentences from CSV.")
    return sentence_map


def enrich_sentence_ids(data: dict, sentence_map: dict[int, str]) -> dict:
    """遍历所有 sample，将 sentence_ids 从整数数组转为对象数组."""
    samples = data.get("samples", [])
    total_ids = 0
    not_found_ids: list[tuple[str, int]] = []  # (sample_id, sentence_id)

    for sample in samples:
        gold_retrieval = sample.get("gold_retrieval", {})
        old_ids = gold_retrieval.get("sentence_ids", [])

        new_ids = []
        for sid in old_ids:
            text = sentence_map.get(sid, "NOT_FOUND")
            if text == "NOT_FOUND":
                not_found_ids.append((sample["sample_id"], sid))
            new_ids.append({"id": sid, "text": text})
            total_ids += 1

        gold_retrieval["sentence_ids"] = new_ids

    print(f"Processed {total_ids} sentence_ids across {len(samples)} samples.")
    if not_found_ids:
        print(f"WARNING: {len(not_found_ids)} IDs not found in CSV:")
        for sample_id, sid in not_found_ids:
            print(f"  - sample={sample_id}, sentence_id={sid}")
    else:
        print("All sentence IDs found in CSV.")

    return data


def main() -> None:
    if not CSV_PATH.exists():
        print(f"CSV not found: {CSV_PATH}", file=sys.stderr)
        sys.exit(1)
    if not JSON_PATH.exists():
        print(f"JSON not found: {JSON_PATH}", file=sys.stderr)
        sys.exit(1)

    sentence_map = load_sentence_map(CSV_PATH)

    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)

    data = enrich_sentence_ids(data, sentence_map)

    # 写回 JSON（保持中文可读性）
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Updated JSON written to: {JSON_PATH}")


if __name__ == "__main__":
    main()
