#!/usr/bin/env python3
"""本地审核服务器：为 annotation draft 提供人工审核前端。

用法::

    python scripts/review_server.py --paper P001 --article A001 [--port 8765]

- 数据源   : data/annotations/{paper}/{paper}_{article}_annotation_draft_2_translated.json
- 审核结果 : data/annotations/{paper}/{paper}_{article}_review_results.json
- 前端     : scripts/review_ui/index.html

纯标准库 http.server，无第三方依赖。审核结果实时写回 review_results.json，
可后续用脚本把 human_verified / 人工标签导回 draft。
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
UI_DIR = SCRIPT_DIR / "review_ui"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hallu.config import (  # noqa: E402
    DISTORTION_LABELS,
    LEVEL1_LABELS,
    NO_DISTORTION,
    SEVERITY_VALUES,
    UNCOVERED_PHENOMENA,
)


# ---------------------------------------------------------------------------
# 失真分类体系（供前端渲染失真判断指南）
# ---------------------------------------------------------------------------

def build_taxonomy() -> dict[str, Any]:
    level1 = {k: {"zh": v["zh"], "en": v["en"]} for k, v in LEVEL1_LABELS.items()}
    labels: dict[str, Any] = {}
    for slug, info in DISTORTION_LABELS.items():
        labels[slug] = {
            "level1": info["level1"],
            "zh": info["zh"],
            "en": info["en"],
            "definition": info["definition"],
        }
    return {
        "version": "distortion-v0.1",
        "level1": level1,
        "labels": labels,
        "no_distortion": NO_DISTORTION,
        "severity": list(SEVERITY_VALUES),
        "uncovered": UNCOVERED_PHENOMENA,
        "evidence_levels": ["With_Evidence", "Weak_Evidence", "No_Evidence"],
    }


# ---------------------------------------------------------------------------
# 数据加载
# ---------------------------------------------------------------------------

def load_draft(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_results(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    if isinstance(raw, dict) and isinstance(raw.get("results"), dict):
        return raw
    return {}


def _pick_sample_fields(sample: dict[str, Any]) -> dict[str, Any]:
    """抽出前端需要的字段，去掉无关的重字段。"""
    sr = sample.get("system_retrieval") or {}
    classify = sr.get("classify_evidences") or []
    review = sr.get("review_evidences") or []

    def slim_ev(ev: dict[str, Any]) -> dict[str, Any]:
        return {
            "rank": ev.get("rank"),
            "sentence_id": ev.get("sentence_id"),
            "text": ev.get("text") or "",
            "text_zh": ev.get("text_zh") or "",
        }

    gr = sample.get("gold_retrieval") or {}
    gc = sample.get("gold_classification") or {}
    analysis = sample.get("analysis") or {}

    return {
        "sample_id": sample.get("sample_id") or "",
        "claim_zh": sample.get("claim_zh") or "",
        "human_verified": bool(sample.get("human_verified")),
        "human_note": sample.get("human_note") or "",
        "classify_evidences": [slim_ev(e) for e in classify],
        "review_evidences": [slim_ev(e) for e in review],
        "gold_retrieval": {
            "sentence_ids": gr.get("sentence_ids") or [],
            "is_answerable": gr.get("is_answerable"),
        },
        "gold_classification": {
            "evidence_level": gc.get("evidence_level") or "",
            "has_distortion": gc.get("has_distortion"),
            "primary_label": gc.get("primary_label") or {},
            "secondary_label": gc.get("secondary_label") or {},
            "severity": gc.get("severity"),
            "needs_manual_review": bool(gc.get("needs_manual_review")),
            "uncovered_phenomenon": gc.get("uncovered_phenomenon") or "",
            "reason": gc.get("reason") or "",
        },
        "analysis": {
            "evidence_judgement": analysis.get("evidence_judgement") or "",
            "classification_reason": analysis.get("classification_reason") or "",
            "key_differences": analysis.get("key_differences") or [],
            "rag_review": analysis.get("rag_review") or {},
            "unsupported_diagnosis": analysis.get("unsupported_diagnosis") or {},
            "manual_check_hints": analysis.get("manual_check_hints") or "",
            "needs_manual_review": bool(analysis.get("needs_manual_review")),
            "review_focus": analysis.get("review_focus") or [],
            "ai_confidence": analysis.get("ai_confidence") or "",
        },
    }


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class ReviewHandler(BaseHTTPRequestHandler):
    server_version = "ReviewServer/1.0"

    # 由 server 实例注入的共享状态
    samples: list[dict[str, Any]] = []
    taxonomy: dict[str, Any] = {}
    results: dict[str, Any] = {}
    results_path: Path | None = None

    def log_message(self, fmt: str, *args: Any) -> None:  # 简化日志
        sys.stderr.write("[%s] %s\n" % (self.address_string(), fmt % args))

    # -- 响应辅助 --

    def _send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    # -- 路由 --

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            html = (UI_DIR / "index.html")
            if html.exists():
                self._send_file(html, "text/html; charset=utf-8")
            else:
                self._send_json({"error": "前端文件缺失: %s" % html}, 500)
        elif path == "/api/data":
            self._send_json(
                {
                    "samples": self.samples,
                    "taxonomy": self.taxonomy,
                    "results": self.results,
                }
            )
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/save":
            self._send_json({"error": "not found"}, 404)
            return
        payload = self._read_body()
        sample_id = str(payload.get("sample_id") or "").strip()
        if not sample_id:
            self._send_json({"ok": False, "error": "缺少 sample_id"}, 400)
            return
        # 只在白名单字段里取，避免写脏数据
        record = {
            "human_verified": bool(payload.get("human_verified")),
            "evidence_level": str(payload.get("evidence_level") or "").strip(),
            "primary_level2": str(payload.get("primary_level2") or "").strip(),
            "secondary_level2": str(payload.get("secondary_level2") or "").strip(),
            "severity": str(payload.get("severity") or "").strip(),
            "uncovered_phenomenon": str(payload.get("uncovered_phenomenon") or "").strip(),
            "note": str(payload.get("note") or "").strip(),
        }
        self.results.setdefault("results", {})[sample_id] = record
        if self.results_path is not None:
            tmp = self.results_path.with_suffix(self.results_path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                json.dump(self.results, f, ensure_ascii=False, indent=2)
                f.write("\n")
            tmp.replace(self.results_path)
        self._send_json({"ok": True, "record": record})


def main() -> int:
    parser = argparse.ArgumentParser(description="标注草稿本地审核服务器")
    parser.add_argument("--paper", required=True, help="如 P001")
    parser.add_argument("--article", required=True, help="如 A001")
    parser.add_argument("--draft", default="", help="草稿 JSON 路径（默认 translated 版）")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    paper = args.paper.strip().upper()
    article = args.article.strip().upper()

    if args.draft:
        draft_path = Path(args.draft)
    else:
        draft_path = (
            ROOT / "data" / "annotations" / paper
            / ("%s_%s_annotation_draft_2_translated.json" % (paper, article))
        )
    if not draft_path.is_absolute():
        draft_path = ROOT / draft_path
    draft_path = draft_path.resolve()
    if not draft_path.exists():
        raise SystemExit("找不到草稿: %s" % draft_path)

    results_path = (
        ROOT / "data" / "annotations" / paper
        / ("%s_%s_review_results.json" % (paper, article))
    )

    draft = load_draft(draft_path)
    samples = [_pick_sample_fields(s) for s in (draft.get("samples") or [])]
    if not samples:
        raise SystemExit("草稿里没有 samples: %s" % draft_path)

    taxonomy = build_taxonomy()
    results = load_results(results_path)

    handler = ReviewHandler
    handler.samples = samples
    handler.taxonomy = taxonomy
    handler.results = results
    handler.results_path = results_path

    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = "http://127.0.0.1:%d/" % args.port
    print("=" * 56)
    print("  标注审核服务器已启动")
    print("  草稿   : %s (%d 条)" % (draft_path, len(samples)))
    print("  结果   : %s" % results_path)
    print("  地址   : %s" % url)
    print("  退出   : Ctrl+C")
    print("=" * 56, flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
