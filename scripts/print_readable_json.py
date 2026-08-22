#!/usr/bin/env python
"""Generate a human-readable copy of an annotation draft JSON.

换行规则：
  - ``text_zh``：默认每 45 字
  - 其它中文字段（_description / evidence_judgement 等）：每 100 字
  - 续行与引号内正文首字对齐

Usage::

    python scripts/print_readable_json.py draft.json
    python scripts/print_readable_json.py draft.json 45
    python scripts/print_readable_json.py translated.json --append-to readable.json
    python scripts/print_readable_json.py readable.json --rewrite
"""

import argparse
import json
import re
import sys
from pathlib import Path

# text_zh 每 45 字；其余中文字段每 100 字
CHARS_PER_LINE_TEXT_ZH = 45
CHARS_PER_LINE_OTHER = 100
# 兼容旧 CLI 位置参数（覆盖 text_zh 字宽；其它字段仍用 100）
CHARS_PER_LINE = CHARS_PER_LINE_TEXT_ZH


# ── text wrapping ────────────────────────────────────────────────

def unwrap_readable_text(text: str) -> str:
    """去掉 readable 续行产生的换行+缩进，保留正文空格。

    序列化后字符串形如 ``...word\\n<indent> next``；
    只删除换行及其后的缩进空白，在「非空白正文」之前停止，避免吃掉词间空格。
    """
    if not text:
        return text
    text = text.replace("\r", "")
    # 换行 + 缩进（后接非空白）→ 直接拼接，保留词间空格在行首的情况见 wrap 侧保证
    text = re.sub(r"\n[ \t]+(?=\S)", "", text)
    # 残留换行（空行等）去掉
    text = text.replace("\n", "")
    return text


def wrap_chinese_text(text: str, chars_per_line: int) -> str:
    """去掉旧续行缩进后，按 *chars_per_line* 插入换行。

    保证每一行不以空白开头，把行首空白并入上一行末尾，
    这样下次 unwrap 时不会把词间空格当成缩进删掉。
    """
    if not text:
        return text
    text = unwrap_readable_text(text)
    if not text:
        return text

    chunks = [text[i:i + chars_per_line]
              for i in range(0, len(text), chars_per_line)]
    if not chunks:
        return text

    fixed = [chunks[0]]
    for chunk in chunks[1:]:
        m = re.match(r"^(\s*)(.*)$", chunk, flags=re.DOTALL)
        lead, rest = (m.group(1), m.group(2)) if m else ("", chunk)
        fixed[-1] = fixed[-1] + lead
        if rest:
            fixed.append(rest)
    # 去掉可能产生的空行块
    fixed = [c for c in fixed if c]
    return "\n".join(fixed)


# 其余中文长字段（非 text_zh）
_WRAP_TEXT_KEYS = frozenset({
    "_description",
    "evidence_judgement",
    "classification_reason",
    "manual_check_hints",
    "human_note",
    "notes",
    "reason",
    "reasoning",
    "suggested_sentence_ranges",
    "description",
})


def transform_text_zh_fields(
    obj,
    chars_per_line: int = CHARS_PER_LINE_TEXT_ZH,
    other_chars_per_line: int = CHARS_PER_LINE_OTHER,
):
    """``text_zh`` 按 *chars_per_line* 换行；其它中文字段按 *other_chars_per_line*。"""
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if k == "text_zh" and isinstance(v, str) and v:
                result[k] = wrap_chinese_text(v, chars_per_line)
            elif k in _WRAP_TEXT_KEYS and isinstance(v, str) and v:
                result[k] = wrap_chinese_text(v, other_chars_per_line)
            elif isinstance(v, str) and "\n" in v:
                # 其它含换行字段：清续行缩进，保留词间空格
                result[k] = unwrap_readable_text(v)
            else:
                result[k] = transform_text_zh_fields(
                    v, chars_per_line, other_chars_per_line
                )
        return result
    if isinstance(obj, list):
        return [
            transform_text_zh_fields(item, chars_per_line, other_chars_per_line)
            for item in obj
        ]
    return obj


# ── readable serializer ──────────────────────────────────────────

def _escape_str(s: str) -> str:
    """JSON-escape a string segment **except** newlines — we want real
    line-breaks in the output for multi-line readability."""
    s = s.replace('\\', '\\\\')
    s = s.replace('"', '\\"')
    s = s.replace('\t', '\\t')
    s = s.replace('\r', '\\r')
    # NOTE: \n is intentionally left unescaped
    return s


def _serialize_multiline_string(text: str, cont_prefix: str) -> str:
    """Write a multi-line string; continuation lines use *cont_prefix*."""
    lines = text.split("\n")
    out = '"' + _escape_str(lines[0])
    for line in lines[1:]:
        out += "\n" + cont_prefix + _escape_str(line)
    out += '"'
    return out


def serialize_readable(obj, indent: int = 0) -> str:
    """Custom JSON serializer.

    多行字符串续行与引号内**正文首字**对齐，例如::

        "_description": "标注草稿：……观点句。
                        人工审核顺序：……"

            "text_zh": "通过对黄瓜……揭示了下
                        生子房是由……"

    .. warning::
        The output is **not** valid JSON (unescaped newlines inside
        strings).  Keep the original file for machine consumption.
    """
    sp = '  ' * indent
    sp1 = '  ' * (indent + 1)

    if obj is None:
        return 'null'

    if isinstance(obj, bool):
        return 'true' if obj else 'false'

    if isinstance(obj, (int, float)):
        return json.dumps(obj)

    if isinstance(obj, str):
        if '\n' in obj:
            return _serialize_multiline_string(obj, sp1)
        return json.dumps(obj, ensure_ascii=False)

    if isinstance(obj, list):
        if not obj:
            return '[]'
        items = [sp1 + serialize_readable(v, indent + 1) for v in obj]
        return '[\n' + ',\n'.join(items) + f'\n{sp}]'

    if isinstance(obj, dict):
        if not obj:
            return '{}'
        items = []
        for k, v in obj.items():
            key_str = json.dumps(k, ensure_ascii=False)
            if isinstance(v, str) and "\n" in v:
                # 续行对齐到 "key": " 之后正文首字
                cont = " " * (len(sp1) + len(key_str) + len(': "'))
                val_str = _serialize_multiline_string(v, cont)
            else:
                val_str = serialize_readable(v, indent + 1)
            items.append(f'{sp1}{key_str}: {val_str}')
        return '{\n' + ',\n'.join(items) + f'\n{sp}}}'

    # fallback
    return json.dumps(obj, ensure_ascii=False)


# ── append mode ──────────────────────────────────────────────────

def append_to_readable(source_data: dict, target_path: Path, chars_per_line: int):
    """将 source_data 中的新 samples 追加到已有的 readable 文件末尾。

    规则：
    - 读取 target_path 中已有的 samples
    - 从 source_data 中筛选出不在目标中的新 sample_id
    - 追加到 samples 数组末尾
    - 更新顶层元数据
    """
    print(f"[Append] reading existing readable file: {target_path}")
    with open(target_path, "r", encoding="utf-8") as f:
        raw = f.read()
    existing = json.loads(raw, strict=False)

    existing_ids = {
        s.get("sample_id", "") for s in existing.get("samples", [])
    }
    print(f"  existing samples: {len(existing_ids)}")

    source_samples = source_data.get("samples", [])
    new_samples = [
        s for s in source_samples
        if s.get("sample_id", "") not in existing_ids
    ]

    if not new_samples:
        print("[Append] No new samples to append — already up to date.")
        return

    print(f"  new samples to append: {len(new_samples)}")
    for s in new_samples:
        print(f"    + {s.get('sample_id', '?')}")

    # 换行：text_zh=45（可用 CLI 覆盖），其它中文字段=100
    new_samples = [
        transform_text_zh_fields(s, chars_per_line, CHARS_PER_LINE_OTHER)
        for s in new_samples
    ]

    # 追加
    existing["samples"].extend(new_samples)
    existing["sample_count"] = len(existing["samples"])

    # 更新 limit
    all_ids = sorted(
        [s.get("sample_id", "") for s in existing["samples"]],
        key=lambda x: (x.split("-C")[0] if "-C" in x else x,
                       int(x.split("-C")[1]) if "-C" in x and x.split("-C")[1].isdigit() else 0)
    )
    if all_ids:
        existing["limit"] = f"{all_ids[0]}–{all_ids[-1]}"

    # 更新 description
    desc = wrap_chinese_text(existing.get("_description", "") or "", CHARS_PER_LINE_OTHER)
    note = f"[追加] 新增 {len(new_samples)} 个样本。"
    existing["_description"] = wrap_chinese_text(
        (desc.replace("\n", "") + note), CHARS_PER_LINE_OTHER
    )

    # 写入
    print(f"\n[Save] writing to {target_path}...")
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(serialize_readable(existing))
        f.write('\n')

    print(f"[Done] total samples: {existing['sample_count']}")


# ── CLI ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="生成 annotation draft 的可读 JSON（text_zh 换行）"
    )
    parser.add_argument(
        "input",
        help="输入 JSON 文件路径",
    )
    parser.add_argument(
        "chars_per_line",
        nargs="?",
        type=int,
        default=CHARS_PER_LINE_TEXT_ZH,
        help=(
            f"text_zh 每行字符数（默认 {CHARS_PER_LINE_TEXT_ZH}）；"
            f"其它中文字段固定 {CHARS_PER_LINE_OTHER}"
        ),
    )
    parser.add_argument(
        "--append-to",
        default=None,
        help="追加模式：将输入中的新 samples 追加到指定的已有 readable JSON 文件末尾",
    )
    parser.add_argument(
        "--rewrite",
        action="store_true",
        help="就地重写模式：读取已有 readable（允许非严格 JSON），重新 wrap + 序列化，不调用翻译",
    )
    args = parser.parse_args()

    src = Path(args.input)
    cpl = args.chars_per_line

    if not src.exists():
        print(f'File not found: {src}', file=sys.stderr)
        sys.exit(1)

    if args.rewrite:
        # ── 就地重写（本地，无需翻译）──
        raw = src.read_text(encoding="utf-8")
        data = json.loads(raw, strict=False)
        data = transform_text_zh_fields(data, cpl, CHARS_PER_LINE_OTHER)
        src.write_text(serialize_readable(data) + "\n", encoding="utf-8")
        n = len(data.get("samples") or [])
        print(
            f"Rewrote {src} ({n} samples, "
            f"text_zh={cpl}, other={CHARS_PER_LINE_OTHER})"
        )
        return

    with open(src, encoding='utf-8') as f:
        data = json.load(f)

    if args.append_to:
        # ── 追加模式 ──
        target = Path(args.append_to)
        if not target.exists():
            print(f'Target readable file not found: {target}', file=sys.stderr)
            print(f'Creating new readable file from source instead.')
            data = transform_text_zh_fields(data, cpl)
            out = target
            with open(out, 'w', encoding='utf-8') as f:
                f.write(serialize_readable(data))
                f.write('\n')
            print(f'Done -> {out}')
        else:
            append_to_readable(data, target, cpl)
    else:
        # ── 独立生成模式（原行为）──
        data = transform_text_zh_fields(data, cpl)
        out = src.parent / f'{src.stem}_readable{src.suffix}'
        with open(out, 'w', encoding='utf-8') as f:
            f.write(serialize_readable(data))
            f.write('\n')
        print(f'Done -> {out}')


if __name__ == '__main__':
    main()
