#!/usr/bin/env python3
"""一键：翻译 batch 文件的 review_evidences → 追加到 readable 文件。

用法::

    python scripts/build_readable.py
"""

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
ANN_DIR = ROOT / "data" / "annotations" / "P001"
READABLE = ANN_DIR / "P001_A001_annotation_draft_readable.json"

BATCHES = [
    "P001_A001_annotation_draft_C11_C20.json",
    "P001_A001_annotation_draft_C20_C30.json",
    "P001_A001_annotation_draft_C31_C40.json",
    "P001_A001_annotation_draft_C40_C53.json",
]


def run(cmd, desc):
    print(f"\n{'='*50}\n  {desc}\n{'='*50}")
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        print(f"\n[FAIL] {desc}")
        sys.exit(r.returncode)


def main():
    for i, name in enumerate(BATCHES, 1):
        src = ANN_DIR / name
        tmp = ANN_DIR / f"{src.stem}_translated.json"

        # Step: translate
        run(
            [sys.executable, str(SCRIPT_DIR / "translate_text_fields.py"),
             str(src), str(tmp)],
            f"[{i}/{len(BATCHES)}] Translate {name}",
        )

        # Step: append to readable
        run(
            [sys.executable, str(SCRIPT_DIR / "print_readable_json.py"),
             str(tmp), "--append-to", str(READABLE)],
            f"[{i}/{len(BATCHES)}] Append {name} → readable",
        )

    print(f"\n{'='*50}")
    print(f"  DONE → {READABLE}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
