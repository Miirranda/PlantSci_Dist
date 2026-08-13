#!/usr/bin/env python3
"""一键：翻译 batch draft → 追加/生成 readable（给人审）。

用法::

    # 分批（如 P001/A001）
    python scripts/build_readable.py \\
      --paper P001 --article A001 \\
      --batches C11_C20 C20_C30 C31_C40 C40_C53

    # 自动收集分批文件
    python scripts/build_readable.py \\
      --paper P001 --article A001 --auto-glob

    # 单文件 smoke（batch 后缀 = smoke）
    python scripts/build_readable.py \\
      --paper P001 --article A002 --batches smoke

    # 已有 translated 时跳过翻译；只重排 readable
    python scripts/build_readable.py \\
      --paper P001 --article A002 --batches smoke --skip-translate
    python scripts/build_readable.py \\
      --paper P001 --article A001 --rewrite-only
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent

_BATCH_RE = re.compile(
    r"^(?P<prefix>.+)_annotation_draft_(?P<batch>C\d+_C\d+)\.json$",
    re.IGNORECASE,
)


def run(cmd: list[str], desc: str) -> None:
    print(f"\n{'=' * 50}\n  {desc}\n{'=' * 50}", flush=True)
    r = subprocess.run(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        print(f"\n[FAIL] {desc}", flush=True)
        sys.exit(r.returncode)


def _batch_sort_key(batch: str) -> tuple:
    """按 Cxx 数值排序；非 Cxx_Cyy 后缀（如 smoke）排在后面。"""
    m = re.fullmatch(r"C(\d+)_C(\d+)", batch, flags=re.IGNORECASE)
    if m:
        return (0, int(m.group(1)), int(m.group(2)), batch)
    return (1, batch)


def resolve_batches(
    ann_dir: Path,
    prefix: str,
    batches: list[str] | None,
    auto_glob: bool,
) -> list[str]:
    """返回 batch 后缀列表，如 ['C11_C20', 'smoke']。"""
    found: list[str] = []

    if batches:
        found.extend(batches)

    if auto_glob:
        for path in sorted(ann_dir.glob(f"{prefix}_C*_C*.json")):
            name = path.name
            if "_translated" in name:
                continue
            m = _BATCH_RE.match(name)
            if not m:
                continue
            if m.group("prefix") != prefix:
                continue
            batch = m.group("batch")
            if batch not in found:
                found.append(batch)

    if not found:
        raise SystemExit(
            "未指定任何 batch。请使用 --batches ... 或 --auto-glob。"
        )

    if batches and not auto_glob:
        seen: set[str] = set()
        ordered: list[str] = []
        for b in found:
            if b not in seen:
                seen.add(b)
                ordered.append(b)
        return ordered

    return sorted(set(found), key=_batch_sort_key)


def batch_paths(ann_dir: Path, prefix: str, batch: str) -> tuple[Path, Path]:
    src = ann_dir / f"{prefix}_{batch}.json"
    translated = ann_dir / f"{prefix}_{batch}_translated.json"
    return src, translated


def collapse_hard_wrap(text: str) -> str:
    """将草稿中的硬换行拼回单行，英文词间补空格，中文直接拼接。

    draft 常把长英文按行切开写成 ``the\\nformation``；若直接删换行会粘连。
    规则：换行两侧均为 ASCII 非空白 → 换成空格；否则去掉换行（及后续缩进）。
    """
    if not text or "\n" not in text:
        return text
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    parts = re.split(r"\n[ \t]*", text)
    if len(parts) == 1:
        return text
    out = parts[0]
    for part in parts[1:]:
        if not part:
            continue
        if (
            out
            and part
            and out[-1].isascii()
            and (not out[-1].isspace())
            and part[0].isascii()
            and (not part[0].isspace())
        ):
            out += " " + part
        else:
            out += part
    return out


def normalize_hard_wraps(obj):
    """递归去掉字符串硬换行，供 print_readable 前使用。"""
    if isinstance(obj, dict):
        return {k: normalize_hard_wraps(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_hard_wraps(v) for v in obj]
    if isinstance(obj, str):
        return collapse_hard_wrap(obj)
    return obj


def write_normalized_json(src: Path) -> Path:
    """读取 src，规范化硬换行后写入临时 JSON，返回路径。"""
    with open(src, encoding="utf-8") as f:
        data = json.load(f)
    data = normalize_hard_wraps(data)
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".json",
        prefix=f"{src.stem}_norm_",
        dir=str(src.parent),
        delete=False,
    )
    try:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.write("\n")
    finally:
        tmp.close()
    return Path(tmp.name)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="翻译 annotation draft batches 并生成/追加 readable JSON",
    )
    p.add_argument("--paper", required=True, help="如 P001")
    p.add_argument("--article", required=True, help="如 A001 / A002")
    p.add_argument(
        "--ann-dir",
        default=None,
        help="标注根目录（默认：data/annotations）",
    )
    p.add_argument(
        "--batches",
        nargs="+",
        default=None,
        help="分批后缀列表，如 C11_C20 C21_C30；单文件可用 smoke",
    )
    p.add_argument(
        "--auto-glob",
        action="store_true",
        help="自动收集 {paper}_{article}_annotation_draft_C*_C*.json（排除 translated）",
    )
    p.add_argument(
        "--skip-translate",
        action="store_true",
        help="已有 *_translated.json 时跳过翻译；若无则直接用源 draft",
    )
    p.add_argument(
        "--seed-from-first",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="第一批：readable 不存在则新建，否则 append（默认开启）",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="若已存在 readable，先删除再重建（避免 append 去重跳过）",
    )
    p.add_argument(
        "--rewrite-only",
        action="store_true",
        help="只对已有 readable 做 --rewrite，不翻译、不 append",
    )
    p.add_argument(
        "--final-rewrite",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="全部 append 完成后对 readable 再 --rewrite 一次（默认开启）",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    ann_root = Path(args.ann_dir) if args.ann_dir else ROOT / "data" / "annotations"
    if not ann_root.is_absolute():
        ann_root = ROOT / ann_root
    ann_dir = ann_root / args.paper
    prefix = f"{args.paper}_{args.article}_annotation_draft"
    readable = ann_dir / f"{prefix}_readable.json"

    translate_py = SCRIPT_DIR / "translate_text_fields.py"
    print_py = SCRIPT_DIR / "print_readable_json.py"

    if not ann_dir.is_dir():
        raise SystemExit(f"标注目录不存在: {ann_dir}")

    if args.rewrite_only:
        if not readable.exists():
            raise SystemExit(f"readable 不存在，无法 --rewrite-only: {readable}")
        run(
            [sys.executable, str(print_py), str(readable), "--rewrite"],
            f"Rewrite {readable.name}",
        )
        print(f"\n{'=' * 50}\n  DONE → {readable}\n{'=' * 50}", flush=True)
        return

    batches = resolve_batches(ann_dir, prefix, args.batches, args.auto_glob)
    print(f"[Plan] paper={args.paper} article={args.article}", flush=True)
    print(f"  ann_dir  = {ann_dir}", flush=True)
    print(f"  readable = {readable}", flush=True)
    print(f"  batches  = {batches}", flush=True)
    print(
        f"  skip_translate={args.skip_translate} "
        f"seed_from_first={args.seed_from_first} "
        f"force={args.force} "
        f"final_rewrite={args.final_rewrite}",
        flush=True,
    )

    if args.force and readable.exists():
        readable.unlink()
        print(f"[Force] removed existing readable: {readable.name}", flush=True)

    for i, batch in enumerate(batches, 1):
        src, translated = batch_paths(ann_dir, prefix, batch)
        if not src.exists():
            raise SystemExit(f"缺少 batch 文件: {src}")

        if args.skip_translate:
            if translated.exists():
                tmp = translated
                print(
                    f"\n[{i}/{len(batches)}] skip-translate → 使用 {tmp.name}",
                    flush=True,
                )
            else:
                tmp = src
                print(
                    f"\n[{i}/{len(batches)}] skip-translate → "
                    f"无 translated，直接用源文件 {src.name}",
                    flush=True,
                )
        else:
            run(
                [sys.executable, str(translate_py), str(src), str(translated)],
                f"[{i}/{len(batches)}] Translate {src.name}",
            )
            tmp = translated

        if not readable.exists() and not args.seed_from_first:
            raise SystemExit(
                f"readable 不存在且未开启 --seed-from-first: {readable}"
            )

        # 规范化硬换行后再交给 print_readable，避免英文词粘连
        norm_path = write_normalized_json(tmp)
        try:
            run(
                [
                    sys.executable,
                    str(print_py),
                    str(norm_path),
                    "--append-to",
                    str(readable),
                ],
                f"[{i}/{len(batches)}] Append {tmp.name} → {readable.name}",
            )
        finally:
            try:
                norm_path.unlink(missing_ok=True)
            except OSError:
                pass

    if args.final_rewrite and readable.exists():
        run(
            [sys.executable, str(print_py), str(readable), "--rewrite"],
            f"Final rewrite {readable.name}",
        )

    print(f"\n{'=' * 50}", flush=True)
    print(f"  DONE → {readable}", flush=True)
    print(f"{'=' * 50}", flush=True)


if __name__ == "__main__":
    main()
