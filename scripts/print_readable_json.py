#!/usr/bin/env python
"""Generate a human-readable copy of an annotation draft JSON,
with ``text`` fields line-wrapped at ~N English words per line
and real newlines so reviewers can read without horizontal scrolling.

Usage::

    python scripts/print_readable_json.py data/annotations/P001/P001_A001_annotation_draft_smoke10.json
    python scripts/print_readable_json.py data/annotations/P001/P001_A001_annotation_draft_smoke10.json 11
"""

import json
import sys
from pathlib import Path

WORDS_PER_LINE = 11


# ── text wrapping ────────────────────────────────────────────────

def wrap_english_text(text: str, words_per_line: int = WORDS_PER_LINE) -> str:
    """Insert ``\\n`` every *words_per_line* words."""
    if not text:
        return text
    words = text.split()
    lines = [' '.join(words[i:i + words_per_line])
             for i in range(0, len(words), words_per_line)]
    return '\n'.join(lines)


def transform_text_fields(obj, words_per_line: int = WORDS_PER_LINE):
    """Recursively wrap every ``text`` key whose value is a non-empty string."""
    if isinstance(obj, dict):
        result = {}
        for k, v in obj.items():
            if k == 'text' and isinstance(v, str) and v:
                result[k] = wrap_english_text(v, words_per_line)
            else:
                result[k] = transform_text_fields(v, words_per_line)
        return result
    if isinstance(obj, list):
        return [transform_text_fields(item, words_per_line) for item in obj]
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


def serialize_readable(obj, indent: int = 0) -> str:
    """Custom JSON serializer.

    Produces standard JSON formatting with ``indent=2`` **except** for
    string values that contain real ``\\n`` — those are written as
    multi-line string literals with continuation-line indentation,
    making them easy to read without horizontal scrolling.

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
            lines = obj.split('\n')
            out = '"' + _escape_str(lines[0])
            for line in lines[1:]:
                out += '\n' + sp1 + _escape_str(line)
            out += '"'
            return out
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
            val_str = serialize_readable(v, indent + 1)
            items.append(f'{sp1}{key_str}: {val_str}')
        return '{\n' + ',\n'.join(items) + f'\n{sp}}}'

    # fallback
    return json.dumps(obj, ensure_ascii=False)


# ── CLI ──────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(
            f'Usage: python {sys.argv[0]} <annotation_draft.json>'
            f' [words_per_line={WORDS_PER_LINE}]'
        )
        sys.exit(1)

    src = Path(sys.argv[1])
    wpl = int(sys.argv[2]) if len(sys.argv) > 2 else WORDS_PER_LINE

    if not src.exists():
        print(f'File not found: {src}', file=sys.stderr)
        sys.exit(1)

    with open(src, encoding='utf-8') as f:
        data = json.load(f)

    data = transform_text_fields(data, wpl)

    out = src.parent / f'{src.stem}_readable{src.suffix}'
    with open(out, 'w', encoding='utf-8') as f:
        f.write(serialize_readable(data))
        f.write('\n')

    print(f'Done → {out}')


if __name__ == '__main__':
    main()
