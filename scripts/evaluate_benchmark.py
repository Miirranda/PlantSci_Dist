#!/usr/bin/env python3
"""对照 benchmark 金标评测系统预测（检索 + 信息失真分类）。

默认对齐方式：sample_id 后缀（P001-A002-C01 ↔ C01）或 claim 文本。
分类轨兼容旧扁平 primary_type 与新 primary_label（level1/level2）。

Usage:
    python scripts/evaluate_benchmark.py \\
        --benchmark data/annotations/P001/P001_A002_benchmark.json \\
        --predictions outputs/P001/A002/classification.json \\
        --output outputs/P001/A002/eval_report.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from hallu.config import normalize_classification  # noqa: E402


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p.resolve()


def _norm_claim(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip())


def _norm_en(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def _texts_align(left: str, right: str) -> bool:
    if not left or not right:
        return True
    a, b = _norm_en(left), _norm_en(right)
    return a == b or a in b or b in a


def load_sentence_table(path: Path) -> dict[int, str]:
    import csv

    table: dict[int, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                sid = int(row.get("sentence_id"))
            except (TypeError, ValueError):
                continue
            table[sid] = str(row.get("text") or "")
    return table


def load_index_meta(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _claim_key(sample_id: str) -> str:
    """P001-A002-C01 → C01；已是 C01 则原样。"""
    s = str(sample_id or "").strip()
    m = re.search(r"(C\d+)\s*$", s, re.I)
    return m.group(1).upper() if m else s.upper()


def _as_int_ids(items: Any) -> list[int]:
    ids: list[int] = []
    for item in items or []:
        if isinstance(item, dict):
        raw = item.get("sentence_id", item.get("id"))
        else:
            raw = item
        try:
            sid = int(raw)
        except (TypeError, ValueError):
            continue
        if sid >= 0 and sid not in ids:
            ids.append(sid)
    return ids


def _load_benchmark_doc(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return data
    raise ValueError("无法解析 benchmark: %s" % path)


def _load_benchmark(path: Path) -> list[dict[str, Any]]:
    data = _load_benchmark_doc(path)
    return list(data.get("samples") or [])


def _load_predictions(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("results", "predictions", "items", "samples"):
            if isinstance(data.get(key), list):
                return data[key]
    raise ValueError("无法解析预测文件结构: %s" % path)


def _pred_index(preds: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_claim: dict[str, dict[str, Any]] = {}
    for row in preds:
        cid = _claim_key(
            str(row.get("claim_id") or row.get("sample_id") or row.get("id") or "")
        )
        if cid:
            by_id[cid] = row
        claim = _norm_claim(
            str(row.get("claim_text") or row.get("claim_zh") or "")
        )
        if claim:
            by_claim[claim] = row
    return {"by_id": by_id, "by_claim": by_claim}


def _match_pred(
    gold: dict[str, Any], index: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    cid = _claim_key(str(gold.get("sample_id") or ""))
    if cid and cid in index["by_id"]:
        return index["by_id"][cid]
    claim = _norm_claim(str(gold.get("claim_zh") or ""))
    if claim and claim in index["by_claim"]:
        return index["by_claim"][claim]
    return None


def _pred_sentence_ids(pred: dict[str, Any]) -> list[int]:
    evid = pred.get("evidence_sentences")
    if evid:
        return _as_int_ids(evid)
    sys_ret = pred.get("system_retrieval") or {}
    return _as_int_ids(sys_ret.get("classify_evidences"))


def _pred_classification(pred: dict[str, Any]) -> dict[str, Any]:
    clf = pred.get("classification") or pred.get("gold_classification") or {}
    view = normalize_classification(clf)
    return {
        "evidence_level": view.get("evidence_level"),
        "has_distortion": view.get("has_distortion"),
        "primary_label": view.get("primary_label"),
        "primary_type": view.get("primary_type") or "",
        "secondary_types": list(view.get("secondary_types") or []),
        "is_accurate": view.get("is_accurate"),
        "severity": view.get("severity"),
        "uncovered_phenomenon": view.get("uncovered_phenomenon") or "",
    }


def _safe_div(num: float, den: float) -> float:
    return float(num) / float(den) if den else 0.0


def _retrieval_metrics(
    gold_ids: list[int], pred_ids: list[int], *, k: int
) -> dict[str, Any]:
    topk = pred_ids[:k]
    gold_set = set(gold_ids)
    hit_ids = [sid for sid in topk if sid in gold_set]
    hit = 1 if (not gold_set and not topk) else (1 if hit_ids else 0)
    # 无金标句：若系统也空则 hit=1；若金标空而系统非空，按 miss（检索不应乱推）
    if not gold_set:
        hit = 1 if not topk else 0
        recall = 1.0 if not topk else 0.0
        precision = 1.0 if not topk else 0.0
    else:
        recall = _safe_div(len(set(hit_ids)), len(gold_set))
        precision = _safe_div(len(hit_ids), len(topk))
    return {
        "hit": hit,
        "recall": recall,
        "precision": precision,
        "gold_ids": gold_ids,
        "pred_ids": topk,
        "hit_ids": hit_ids,
    }


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


def _pred_sentence_items(pred: dict[str, Any]) -> list[dict[str, Any]]:
    evid = pred.get("evidence_sentences")
    if evid:
        return list(evid)
    sys_ret = pred.get("system_retrieval") or {}
    return list(sys_ret.get("classify_evidences") or [])


def _check_id_text(
    sid: int,
    text: str,
    table: dict[int, str],
) -> str | None:
    if sid not in table:
        return "missing_in_table"
    if text and not _texts_align(text, table[sid]):
        return "text_mismatch"
    return None


def evaluate(
    gold_samples: list[dict[str, Any]],
    predictions: list[dict[str, Any]],
    *,
    k: int = 5,
    sentence_table: dict[int, str] | None = None,
    expected_index_version: str = "",
    actual_index_version: str = "",
) -> dict[str, Any]:
    index = _pred_index(predictions)
    per_sample: list[dict[str, Any]] = []
    missing: list[str] = []

    hit_sum = 0
    recall_sum = 0.0
    precision_sum = 0.0
    n_ret = 0

    n_level = n_level_ok = 0
    n_primary = n_primary_ok = 0
    n_level1 = n_level1_ok = 0
    n_acc = n_acc_ok = 0
    n_dist = n_dist_ok = 0
    n_sev = n_sev_ok = 0
    sec_tp = sec_fp = sec_fn = 0
    n_id_mismatch = 0
    n_id_missing = 0
    table = sentence_table or {}
    version_mismatch = bool(
        expected_index_version
        and actual_index_version
        and expected_index_version != actual_index_version
    )

    for gold in gold_samples:
        sid = str(gold.get("sample_id") or "")
        pred = _match_pred(gold, index)
        if pred is None:
            missing.append(sid)
            per_sample.append(
                {
                    "sample_id": sid,
                    "matched": False,
                    "error": "no_prediction",
                }
            )
            continue

        g_ret = gold.get("gold_retrieval") or {}
        g_clf = gold.get("gold_classification") or {}
        gold_ids = _as_int_ids(g_ret.get("sentence_ids"))
        pred_ids = _pred_sentence_ids(pred)
        ret = _retrieval_metrics(gold_ids, pred_ids, k=k)
        n_ret += 1
        hit_sum += ret["hit"]
        recall_sum += ret["recall"]
        precision_sum += ret["precision"]

        sample_id_issues: list[dict[str, Any]] = []
        if table:
            gold_texts = {}
            for item in g_ret.get("evidences") or []:
                if not isinstance(item, dict):
                    continue
                try:
                    evid_id = int(item.get("sentence_id"))
                except (TypeError, ValueError):
                    continue
                gold_texts[evid_id] = str(item.get("text") or "")
            for evid_id in gold_ids:
                issue = _check_id_text(evid_id, gold_texts.get(evid_id, ""), table)
                if issue == "missing_in_table":
                    n_id_missing += 1
                    sample_id_issues.append({"sentence_id": evid_id, "source": "gold", "issue": issue})
                elif issue == "text_mismatch":
                    n_id_mismatch += 1
                    sample_id_issues.append({"sentence_id": evid_id, "source": "gold", "issue": issue})
            for item in _pred_sentence_items(pred):
                try:
                    evid_id = int(item.get("sentence_id", item.get("id")))
                except (TypeError, ValueError):
                    continue
                pred_text = str(item.get("text") or item.get("sentence") or "")
                issue = _check_id_text(evid_id, pred_text, table)
                if issue == "missing_in_table":
                    n_id_missing += 1
                    sample_id_issues.append({"sentence_id": evid_id, "source": "pred", "issue": issue})
                elif issue == "text_mismatch":
                    n_id_mismatch += 1
                    sample_id_issues.append({"sentence_id": evid_id, "source": "pred", "issue": issue})

        p_clf = _pred_classification(pred)
        g_view = normalize_classification(g_clf)
        g_level = g_view.get("evidence_level")
        g_primary = g_view.get("primary_type")
        g_level1 = (g_view.get("primary_label") or {}).get("level1") or ""
        g_accurate = g_view.get("is_accurate")
        g_dist = g_view.get("has_distortion")
        g_sev = g_view.get("severity")
        g_sec = set(g_view.get("secondary_types") or [])
        p_sec = set(p_clf.get("secondary_types") or [])
        p_level1 = (p_clf.get("primary_label") or {}).get("level1") or ""

        if g_level is not None:
            n_level += 1
            level_ok = g_level == p_clf["evidence_level"]
            n_level_ok += int(level_ok)
        else:
            level_ok = None

        if g_primary is not None:
            n_primary += 1
            primary_ok = g_primary == p_clf["primary_type"]
            n_primary_ok += int(primary_ok)
        else:
            primary_ok = None

        if g_level1 and p_level1:
            n_level1 += 1
            level1_ok = g_level1 == p_level1
            n_level1_ok += int(level1_ok)
        else:
            level1_ok = None

        if g_accurate is not None and p_clf.get("is_accurate") is not None:
            n_acc += 1
            acc_ok = bool(g_accurate) == bool(p_clf["is_accurate"])
            n_acc_ok += int(acc_ok)
        else:
            acc_ok = None

        if g_dist is not None and p_clf.get("has_distortion") is not None:
            n_dist += 1
            dist_ok = bool(g_dist) == bool(p_clf["has_distortion"])
            n_dist_ok += int(dist_ok)
        else:
            dist_ok = None

        if g_sev is not None:
            n_sev += 1
            sev_ok = g_sev == p_clf["severity"]
            n_sev_ok += int(sev_ok)
        else:
            sev_ok = None

        sec_tp += len(g_sec & p_sec)
        sec_fp += len(p_sec - g_sec)
        sec_fn += len(g_sec - p_sec)

        per_sample.append(
            {
                    "sample_id": sid,
                    "matched": True,
                    "claim_zh": gold.get("claim_zh"),
                    "retrieval": ret,
                    "index_id_issues": sample_id_issues,
                "classification": {
                    "gold": {
                        "evidence_level": g_level,
                        "has_distortion": g_dist,
                        "primary_label": g_view.get("primary_label"),
                        "primary_type": g_primary,
                        "secondary_types": sorted(g_sec),
                        "is_accurate": g_accurate,
                        "severity": g_sev,
                    },
                    "pred": p_clf,
                    "evidence_level_ok": level_ok,
                    "primary_type_ok": primary_ok,
                    "level1_ok": level1_ok,
                    "has_distortion_ok": dist_ok,
                    "is_accurate_ok": acc_ok,
                    "severity_ok": sev_ok,
                },
            }
        )

    sec_p = _safe_div(sec_tp, sec_tp + sec_fp)
    sec_r = _safe_div(sec_tp, sec_tp + sec_fn)

    summary = {
        "sample_count": len(gold_samples),
        "matched_count": n_ret,
        "missing_predictions": missing,
        "retrieval": {
            "k": k,
            "hit_at_k": _safe_div(hit_sum, n_ret),
            "recall_at_k": _safe_div(recall_sum, n_ret),
            "precision_at_k": _safe_div(precision_sum, n_ret),
        },
        "index": {
            "expected_version": expected_index_version,
            "actual_version": actual_index_version,
            "version_mismatch": version_mismatch,
            "id_text_mismatches": n_id_mismatch,
            "ids_missing_in_table": n_id_missing,
        },
        "classification": {
            "evidence_level_accuracy": _safe_div(n_level_ok, n_level),
            "primary_type_accuracy": _safe_div(n_primary_ok, n_primary),
            "level1_accuracy": _safe_div(n_level1_ok, n_level1),
            "has_distortion_accuracy": _safe_div(n_dist_ok, n_dist),
            "is_accurate_accuracy": _safe_div(n_acc_ok, n_acc),
            "severity_accuracy": _safe_div(n_sev_ok, n_sev),
            "secondary_types_micro": {
                "precision": sec_p,
                "recall": sec_r,
                "f1": _f1(sec_p, sec_r),
                "tp": sec_tp,
                "fp": sec_fp,
                "fn": sec_fn,
            },
        },
    }

    # 便于快速看错在哪
    primary_conf: Counter[str] = Counter()
    for row in per_sample:
        clf = row.get("classification")
        if not clf:
            continue
        g = (clf.get("gold") or {}).get("primary_type")
        p = (clf.get("pred") or {}).get("primary_type")
        if g is not None and p is not None:
            primary_conf["%s -> %s" % (g, p)] += 1
    summary["primary_type_confusion"] = dict(primary_conf.most_common())

    return {
        "schema_version": "1.0",
        "status": "eval_report",
        "evaluated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": summary,
        "samples": per_sample,
    }


def _print_summary(report: dict[str, Any]) -> None:
    s = report["summary"]
    r = s["retrieval"]
    c = s["classification"]
    print("评测完成")
    print("  matched: %d / %d" % (s["matched_count"], s["sample_count"]))
    if s["missing_predictions"]:
        print("  missing: %s" % ", ".join(s["missing_predictions"]))
    idx = s.get("index") or {}
    if idx.get("version_mismatch"):
        print(
            "  警告: index_version 不一致 gold=%s pred=%s（sentence_id 可能错位）"
            % (idx.get("expected_version"), idx.get("actual_version"))
        )
    if idx.get("id_text_mismatches") or idx.get("ids_missing_in_table"):
        print(
            "  警告: sentence_id 原文校验 mismatch=%s missing_in_table=%s"
            % (idx.get("id_text_mismatches"), idx.get("ids_missing_in_table"))
        )
    print(
        "  retrieval Hit@%d=%.3f  Recall@%d=%.3f  P@%d=%.3f"
        % (
            r["k"],
            r["hit_at_k"],
            r["k"],
            r["recall_at_k"],
            r["k"],
            r["precision_at_k"],
        )
    )
    print(
        "  classification evidence_level=%.3f  level2=%.3f  level1=%.3f  "
        "has_distortion=%.3f  is_accurate=%.3f"
        % (
            c["evidence_level_accuracy"],
            c["primary_type_accuracy"],
            c.get("level1_accuracy") or 0.0,
            c.get("has_distortion_accuracy") or 0.0,
            c["is_accurate_accuracy"],
        )
    )
    sec = c["secondary_types_micro"]
    print(
        "  secondary micro-F1=%.3f (P=%.3f R=%.3f)"
        % (sec["f1"], sec["precision"], sec["recall"])
    )
    if s.get("primary_type_confusion"):
        print("  level2/primary_type 对照:")
        for pair, n in s["primary_type_confusion"].items():
            print("    %s  x%d" % (pair, n))


def main() -> int:
    parser = argparse.ArgumentParser(description="benchmark vs predictions 评测")
    parser.add_argument(
        "--benchmark",
        default="data/annotations/P001/P001_A002_benchmark.json",
        help="金标 benchmark.json",
    )
    parser.add_argument(
        "--predictions",
        default="outputs/P001/A002/classification.json",
        help="系统预测（classification.json）",
    )
    parser.add_argument(
        "--output",
        default="outputs/P001/A002/eval_report.json",
        help="评测报告输出路径",
    )
    parser.add_argument("--k", type=int, default=5, help="检索 Hit@k / Recall@k")
    parser.add_argument(
        "--sentence-table",
        default="",
        help="冻结句表 CSV；提供则校验 gold/pred 的 sentence_id 与原文是否一致",
    )
    parser.add_argument(
        "--index-meta",
        default="",
        help="系统当前 index_meta.json；与 benchmark.index_version 对照",
    )
    args = parser.parse_args()

    bench_path = _resolve(args.benchmark)
    pred_path = _resolve(args.predictions)
    out_path = _resolve(args.output)
    if not bench_path.is_file():
        print("benchmark 不存在: %s" % bench_path)
        return 1
    if not pred_path.is_file():
        print("predictions 不存在: %s" % pred_path)
        return 1

    bench_doc = _load_benchmark_doc(bench_path)
    gold_samples = list(bench_doc.get("samples") or [])
    sentence_table = None
    table_path = _resolve(args.sentence_table) if args.sentence_table else (
        _PROJECT_ROOT / "data" / "annotations" / str(bench_doc.get("paper_id") or "P001") / (
            "%s_sentences.csv" % (bench_doc.get("paper_id") or "P001")
        )
    )
    if table_path.is_file():
        sentence_table = load_sentence_table(table_path)
    if not args.index_meta:
        paper_id = str(bench_doc.get("paper_id") or "P001")
        meta_path = (
            _PROJECT_ROOT / "data" / "index" / paper_id / "index_meta.json"
        )
        if not meta_path.is_file():
            for candidate in (
                _PROJECT_ROOT / "data" / "index" / "index_meta.json",
                _PROJECT_ROOT / "arag-main" / "data" / "index" / "index_meta.json",
            ):
                if candidate.is_file():
                    meta_path = candidate
                    break
    else:
        meta_path = _resolve(args.index_meta)
    actual_version = str(load_index_meta(meta_path).get("index_version") or "")
    expected_version = str(bench_doc.get("index_version") or "")

    report = evaluate(
        gold_samples,
        _load_predictions(pred_path),
        k=args.k,
        sentence_table=sentence_table,
        expected_index_version=expected_version,
        actual_index_version=actual_version,
    )
    report["benchmark_path"] = str(bench_path)
    report["predictions_path"] = str(pred_path)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    _print_summary(report)
    print("  report: %s" % out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
