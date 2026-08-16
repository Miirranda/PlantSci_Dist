"""跨语言检索适配层的配置。

原创代码（非 A-RAG 开源部分）。所有取值优先级：显式入参 > 环境变量 > 默认值。
密钥一律由阶段 1 的 api_client 从 .env 读取，本模块不碰密钥。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from api_client.config import get_bool, get_env, get_float, get_int

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
WECHAT_DIR = DATA_DIR / "wechat"
PAPERS_DIR = DATA_DIR / "papers"
CORPUS_DIR = DATA_DIR / "corpus"
INDEX_DIR = DATA_DIR / "index"
CACHE_DIR = DATA_DIR / "cache"

CHUNKS_FILE = CORPUS_DIR / "chunks.json"
INDEX_FILE = INDEX_DIR / "sentence_index.pkl"
TERM_CACHE_FILE = CACHE_DIR / "bilingual_terms.json"


@dataclass
class ThresholdConfig:
    """双阈值检索终止规则的参数。

    high        : 高置信阈值，rerank 分数 ≥ 此值视为强证据
    low         : 低置信阈值，全部候选 < 此值视为无支撑证据
    min_hits    : 达到 high 的证据条数下限，满足后才允许判 SUPPORTED
                  （默认 2：单条强证据不足以终止，适度拉高证据广度）
    keep_score  : 进入最终输出的最低分，低于此值的候选直接丢弃
    """

    high: float = 0.70
    low: float = 0.30
    min_hits: int = 2
    keep_score: float = 0.08

    @classmethod
    def from_env(cls) -> ThresholdConfig:
        return cls(
            high=get_float("ARAG_THRESHOLD_HIGH", 0.70),
            low=get_float("ARAG_THRESHOLD_LOW", 0.30),
            min_hits=get_int("ARAG_THRESHOLD_MIN_HITS", 2),
            keep_score=get_float("ARAG_THRESHOLD_KEEP", 0.08),
        )

    def validate(self) -> None:
        if not 0.0 <= self.low < self.high <= 1.0:
            raise ValueError(
                "阈值区间非法：要求 0 <= low(%.2f) < high(%.2f) <= 1" % (self.low, self.high)
            )
        if self.min_hits < 1:
            raise ValueError("min_hits 至少为 1，当前 %d" % self.min_hits)


@dataclass
class RetrievalConfig:
    """检索流水线配置。"""

    chunks_file: Path = CHUNKS_FILE
    index_dir: Path = INDEX_DIR
    term_cache_file: Path = TERM_CACHE_FILE
    paper_id: str = ""

    # 单轮语义检索默认返回条数（Agent 未显式传 top_k 时使用）
    default_top_k: int = 10
    # 粗召回倍数：向量层先取 top_k * recall_multiplier 条送进重排
    recall_multiplier: int = 5
    # 单次重排最多处理多少候选，超出由 api_client 自动分片
    rerank_candidates: int = 80
    # 段落上下文取前后各多少个句子
    context_window: int = 2
    # 命中句邻句扩展窗口（同 chunk 内 ±N），0 关闭
    neighbor_window: int = 1
    # 邻句继承父句分数的衰减系数
    neighbor_score_decay: float = 0.85
    # 多样性截断：0=纯按分数；越大越偏重与已选句的差异（建议 0.2~0.4）
    diversity_lambda: float = 0.30
    # 除主查询外，是否用双语术语再并集一路向量召回
    multi_query_from_terms: bool = True
    # 最终写入 evidences 的上限
    max_evidences: int = 12

    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)

    @classmethod
    def from_env(cls) -> RetrievalConfig:
        config = cls(
            chunks_file=Path(get_env("ARAG_CHUNKS_FILE") or CHUNKS_FILE),
            index_dir=Path(get_env("ARAG_INDEX_DIR") or INDEX_DIR),
            term_cache_file=Path(get_env("ARAG_TERM_CACHE") or TERM_CACHE_FILE),
            paper_id=str(get_env("ARAG_PAPER_ID") or "").strip().upper(),
            default_top_k=get_int("ARAG_DEFAULT_TOP_K", 10),
            recall_multiplier=get_int("ARAG_RECALL_MULTIPLIER", 5),
            rerank_candidates=get_int("ARAG_RERANK_CANDIDATES", 80),
            context_window=get_int("ARAG_CONTEXT_WINDOW", 2),
            neighbor_window=get_int("ARAG_NEIGHBOR_WINDOW", 1),
            neighbor_score_decay=get_float("ARAG_NEIGHBOR_DECAY", 0.85),
            diversity_lambda=get_float("ARAG_DIVERSITY_LAMBDA", 0.30),
            multi_query_from_terms=get_bool("ARAG_MULTI_QUERY_TERMS", True),
            max_evidences=get_int("ARAG_MAX_EVIDENCES", 12),
            thresholds=ThresholdConfig.from_env(),
        )
        if config.paper_id and not get_env("ARAG_INDEX_DIR"):
            from .paper_registry import apply_layout

            apply_layout(config, config.paper_id)
        return config

    @classmethod
    def for_paper(cls, paper_id: str) -> RetrievalConfig:
        """只加载 ``data/index/<paper_id>/``，不扫其它论文。"""
        config = cls.from_env()
        from .paper_registry import apply_layout

        return apply_layout(config, paper_id)


@dataclass
class AgentRuntimeConfig:
    """Agent 运行时配置。模型固定为 Qwen，工具调用固定开启。"""

    max_loops: int = 12
    max_token_budget: int = 120000
    verbose: bool = False

    @classmethod
    def from_env(cls) -> AgentRuntimeConfig:
        return cls(
            max_loops=get_int("ARAG_MAX_LOOPS", 12),
            max_token_budget=get_int("ARAG_MAX_TOKEN_BUDGET", 120000),
            verbose=get_bool("ARAG_VERBOSE", False),
        )


def ensure_dirs() -> None:
    """幂等地建好所有产物目录。"""
    for directory in (CORPUS_DIR, INDEX_DIR, CACHE_DIR):
        directory.mkdir(parents=True, exist_ok=True)
