"""为 annotation draft JSON 中的长文本字段添加换行，方便审核阅读。

处理 evidence_judgement 和 classification_reason 两个字段，
在自然断句处插入换行符。
"""

import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
JSON_PATH = (
    PROJECT_ROOT / "data" / "annotations" / "P001" / "P001_A001_annotation_draft.json"
)


def add_line_breaks(text: str) -> str:
    """在中文长文本的自然断句处插入换行。"""
    # 1. 在中文句号后换行
    text = text.replace("。", "。\n")

    # 2. 在中文分号后换行
    text = text.replace("；", "；\n")

    # 3. 在编号列表 (1)(2)(3) 前换行
    text = re.sub(r"(?<!\n)(?=\(\d+\))", "\n", text)

    # 4. 在 primary_type= / secondary_types / severity= 前换行（classification_reason 常见模式）
    text = re.sub(r"(?<!\n)(?=primary_type=)", "\n", text)
    text = re.sub(r"(?<!\n)(?=secondary_types)", "\n", text)
    text = re.sub(r"(?<!\n)(?=severity=)", "\n", text)

    # 5. 在转折词前换行
    for keyword in ["区分 ", "整体来看", "需注意", "综上，", "因此，", "但需注意"]:
        text = re.sub(rf"(?<!\n)(?={keyword})", "\n", text)

    # 6. 清理多余空行（连续 \n 超过2个压缩为2个）
    text = re.sub(r"\n{3,}", "\n\n", text)

    # 7. 去掉每行首尾多余空格，并去除整体首尾空白
    lines = text.split("\n")
    lines = [line.strip() for line in lines if line.strip()]
    text = "\n".join(lines)

    return text


def process_json(data: dict) -> dict:
    """遍历所有 sample，处理长文本字段。"""
    for sample in data.get("samples", []):
        analysis = sample.get("analysis", {})
        for field in ["evidence_judgement", "classification_reason"]:
            if field in analysis and analysis[field]:
                analysis[field] = add_line_breaks(analysis[field])
    return data


def main() -> None:
    if not JSON_PATH.exists():
        print(f"File not found: {JSON_PATH}", file=sys.stderr)
        sys.exit(1)

    with open(JSON_PATH, encoding="utf-8") as f:
        data = json.load(f)

    data = process_json(data)

    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"Done. Updated: {JSON_PATH}")


if __name__ == "__main__":
    main()
