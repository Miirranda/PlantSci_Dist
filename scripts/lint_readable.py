#!/usr/bin/env python3
"""校验 annotation_draft_readable.json 是否可被解析，并报告结构错误。

兼容 intentional 裸换行（strict=False），但会捕获：
  - human_verified 后裸备注
  - sample 间裸粘贴文本
  - 其它 JSON 结构破坏

用法::
    python scripts/lint_readable.py data/annotations/P001/P001_A001_annotation_draft_readable.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_HV_BARE_NOTE = re.compile(
    r'"human_verified"\s*:\s*(?:true|false)\s*:\s*[^\n\r,}]+',
    re.IGNORECASE,
)
_TRAILING_COMMA = re.compile(r",(\s*[\]}])")
_ORPHAN_TEXT = re.compile(
    r'\}\s*,\s*\n\s*(?!["\{\[])[^\n\{]+',
)


def _line_no(raw: str, pos: int) -> int:
    return raw.count("\n", 0, pos) + 1


def _context(raw: str, line: int, radius: int = 2) -> str:
    lines = raw.splitlines()
    out = []
    for i in range(max(0, line - 1 - radius), min(len(lines), line + radius)):
        mark = ">>" if i == line - 1 else "  "
        out.append("%s %4d| %s" % (mark, i + 1, lines[i][:120]))
    return "\n".join(out)


def lint_file(path: Path) -> int:
    raw = path.read_text(encoding="utf-8")
    errors: list[str] = []

    for m in _HV_BARE_NOTE.finditer(raw):
        ln = _line_no(raw, m.start())
        errors.append(
            "L%d: human_verified 后存在裸备注（请写入 human_note 字段）\n%s"
            % (ln, _context(raw, ln))
        )

    for m in _ORPHAN_TEXT.finditer(raw):
        snippet = m.group(0).strip()[:80]
        if snippet.startswith("},"):
            continue
        ln = _line_no(raw, m.start())
        if re.match(r'\}\s*,\s*\n\s*\{', m.group(0)):
            continue
        errors.append(
            "L%d: sample 之间可能存在裸粘贴文本\n%s"
            % (ln, _context(raw, ln))
        )

    sanitized = raw
    prev = None
    while prev != sanitized:
        prev = sanitized
        sanitized = _TRAILING_COMMA.sub(r"\1", sanitized)

    try:
        data = json.loads(sanitized, strict=False)
    except json.JSONDecodeError as exc:
        ln = exc.lineno or 1
        errors.append(
            "L%d: JSON 解析失败: %s\n%s" % (ln, exc.msg, _context(raw, ln))
        )
        data = None

    if data is not None:
        samples = data.get("samples") or []
        for s in samples:
            sid = s.get("sample_id", "?")
            if s.get("human_verified") and not isinstance(s.get("human_verified"), bool):
                errors.append("%s: human_verified 不是布尔值" % sid)
            for item in ((s.get("system_retrieval") or {}).get("review_evidences") or []):
                en = (item.get("text") or "").strip()
                zh = (item.get("text_zh") or "").strip()
                if en and zh:
                    # 简单启发：译文含中文但 EN 首词完全对不上
                    pass

    if errors:
        print("[FAIL] %s — %d 个问题:\n" % (path, len(errors)))
        for i, err in enumerate(errors, 1):
            print("--- #%d ---\n%s\n" % (i, err))
        return 1

    n = len((data or {}).get("samples") or [])
    verified = sum(1 for s in (data or {}).get("samples") or [] if s.get("human_verified"))
    print("[OK] %s — %d samples, %d human_verified" % (path, n, verified))
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Lint readable annotation JSON")
    p.add_argument("path", type=Path, nargs="+")
    args = p.parse_args()
    code = 0
    for path in args.path:
        if not path.exists():
            print("[FAIL] 文件不存在: %s" % path)
            code = 1
            continue
        code = max(code, lint_file(path))
    sys.exit(code)


if __name__ == "__main__":
    main()
