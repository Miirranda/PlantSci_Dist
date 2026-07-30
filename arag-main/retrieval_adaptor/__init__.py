"""跨语言检索适配层（阶段 2）。

原创代码层：把阶段 1 的 ``api_client`` 与原生 A-RAG 的 ReAct 循环粘接成
"中文断言 -> 英文论文结构化证据" 的检索底座。

代码归属约定：
* 本目录（``retrieval_adaptor/``）—— 全部为本项目原创；
* ``src/arag/`` 下的改动 —— 基于开源 A-RAG 改良，文件头均标注改良点；
* ``api_client/`` —— 阶段 1 的统一 API 客户端。

典型用法::

    from retrieval_adaptor import CrossLingualRetrievalPipeline

    with CrossLingualRetrievalPipeline() as pipeline:
        output = pipeline.retrieve("大语言模型的幻觉率可以通过检索增强降低一半")
        print(output.to_json())
"""

from __future__ import annotations

from .config import (
    CACHE_DIR,
    CHUNKS_FILE,
    CORPUS_DIR,
    INDEX_DIR,
    PAPERS_DIR,
    WECHAT_DIR,
    AgentRuntimeConfig,
    RetrievalConfig,
    ThresholdConfig,
    ensure_dirs,
)
from .evidence_board import Candidate, EvidenceBoard
from .index_store import IndexStore, normalize
from .claim_extractor import (
    extract_claims_from_article,
    save_claims_json,
    save_claims_jsonl,
    split_article_candidates,
)
from .pipeline import CrossLingualRetrievalPipeline, load_claims_from_file
from .qwen_agent_adapter import QwenAgentAdapter
from .schemas import (
    SCHEMA_VERSION,
    VERDICT_INCONCLUSIVE,
    VERDICT_NO_EVIDENCE,
    VERDICT_SUPPORTED,
    BilingualTerm,
    EvidenceRecord,
    PaperMetadata,
    ParagraphContext,
    RetrievalOutput,
)
from .thresholds import DualThresholdGate, GateDecision

__version__ = "1.0.0"

__all__ = [
    # 流水线
    "CrossLingualRetrievalPipeline",
    "load_claims_from_file",
    # LLM 观点句提取
    "extract_claims_from_article",
    "save_claims_json",
    "save_claims_jsonl",
    "split_article_candidates",
    # 组件
    "QwenAgentAdapter",
    "IndexStore",
    "EvidenceBoard",
    "Candidate",
    "DualThresholdGate",
    "GateDecision",
    "normalize",
    # Schema
    "RetrievalOutput",
    "EvidenceRecord",
    "PaperMetadata",
    "ParagraphContext",
    "BilingualTerm",
    "SCHEMA_VERSION",
    "VERDICT_SUPPORTED",
    "VERDICT_INCONCLUSIVE",
    "VERDICT_NO_EVIDENCE",
    # 配置
    "RetrievalConfig",
    "ThresholdConfig",
    "AgentRuntimeConfig",
    "ensure_dirs",
    "CHUNKS_FILE",
    "INDEX_DIR",
    "CORPUS_DIR",
    "CACHE_DIR",
    "PAPERS_DIR",
    "WECHAT_DIR",
]
