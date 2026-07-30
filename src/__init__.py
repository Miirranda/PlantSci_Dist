"""兼容层已移除。请使用 ``hallu`` + ``arag-main``。

  python scripts/run.py --article <文章.md> --paper <论文.pdf>
"""

raise ImportError(
    "src 包已废弃。请使用 hallu（分类/证据链）与 arag-main（LLM抽句+检索）。\n"
    "入口: python scripts/run.py --article ... --paper ..."
)
