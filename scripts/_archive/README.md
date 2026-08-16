# scripts/_archive

一次性 / 已被替代的脚本归档，**不要作为日常流水线入口**。

| 文件 | 原用途 | 替代 |
|------|--------|------|
| `generate_c11_c20_draft.py` 等分段稿 | 按 claim 区间手写 annotation draft | `generate_draft_from_pairs.py` |
| `_extract_claims.py` | 从 pairs 切 C20–C30 输入 | 同上 |
| `generate_smoke10.py` | 冒烟草稿 | 同上 |
| `generate_annotation_draft.py` | 旧 9 类硬编码草稿 | API 草稿脚本 + 规范 v0.1 |
| `migrate_annotation_draft.py` | 旧草稿字段迁移 | 迁移完成后不再需要 |
| `add_linebreaks.py` / `enrich_sentence_ids.py` | P001 草稿排版补丁 | `build_readable.py` / `print_readable_json.py` |

日常入口见仓库根目录 `README.md`：`run.py`、`extract_claims_for_review.py`、`export_locked_claims.py`、`generate_draft_from_pairs.py`、`export_benchmark.py`。
