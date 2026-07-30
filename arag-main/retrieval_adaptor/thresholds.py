"""双阈值检索终止规则。

原创代码（非 A-RAG 开源部分）。原生 A-RAG 让 Agent 自行判断"检索够了没有"，在跨语言
幻觉检测场景下这个判断不可靠——中文断言与英文论据的字面重合度很低，Agent 容易在证据不足
时提前收手。这里用 bge-reranker 的相关性分数给出可量化的三分支终止条件：

* 高阈值：有 ``min_hits`` 条候选的 rerank 分数 ≥ ``high``  -> 停止，判 SUPPORTED
* 低阈值：全部候选的 rerank 分数 < ``low``                 -> 停止，判 NO_EVIDENCE
* 夹在中间：既不够强也不够弱                                -> 继续换词重检，直到轮次上限
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .config import ThresholdConfig
from .schemas import (
    VERDICT_INCONCLUSIVE,
    VERDICT_NO_EVIDENCE,
    VERDICT_SUPPORTED,
)

# 终止原因取值，写进 RetrievalOutput.stop_reason
STOP_HIGH_THRESHOLD = "high_threshold_met"
STOP_LOW_THRESHOLD = "low_threshold_all_below"
STOP_NO_CANDIDATE = "no_candidate_retrieved"
STOP_ROUND_LIMIT = "round_limit_reached"
CONTINUE_SEARCH = "continue_search"


@dataclass
class GateDecision:
    """一次判定的结果。"""

    verdict: str
    should_stop: bool
    reason: str
    strong_hits: int = 0
    weak_hits: int = 0
    top_score: float = 0.0
    candidate_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "verdict": self.verdict,
            "should_stop": self.should_stop,
            "reason": self.reason,
            "strong_hits": self.strong_hits,
            "weak_hits": self.weak_hits,
            "top_score": round(self.top_score, 6),
            "candidate_count": self.candidate_count,
        }

    def describe(self) -> str:
        """给 Agent 看的一句话说明，附在工具返回里引导它决定下一步。"""
        if self.reason == STOP_HIGH_THRESHOLD:
            return (
                "STOP: 已有 %d 条证据达到高阈值，证据充分，请停止检索并给出最终答案。"
                % self.strong_hits
            )
        if self.reason == STOP_LOW_THRESHOLD:
            return (
                "STOP: 全部候选相关性低于低阈值（最高 %.4f），判定为无支撑证据，"
                "请停止检索并明确说明论文库中找不到支撑。" % self.top_score
            )
        if self.reason == STOP_NO_CANDIDATE:
            return "STOP: 没有召回任何候选，请确认术语翻译是否正确，或停止检索并报告无证据。"
        if self.reason == STOP_ROUND_LIMIT:
            return "STOP: 已达检索轮次上限，请基于现有证据给出结论并标注证据不足。"
        if self.strong_hits > 0:
            return (
                "CONTINUE: 目前只有 %d 条强证据（最高 %.4f），尚未达到停止所需的条数；"
                "请针对尚未覆盖的子断言换词再检索一轮，以扩大证据面。"
                % (self.strong_hits, self.top_score)
            )
        return (
            "CONTINUE: 相关性处于中间区间（最高 %.4f），证据不足以定论，"
            "请更换英文术语或改写查询再检索一轮。" % self.top_score
        )


class DualThresholdGate:
    """按双阈值规则判定是否终止检索。"""

    def __init__(self, config: ThresholdConfig | None = None) -> None:
        self.config = config or ThresholdConfig()
        self.config.validate()

    def label(self, score: float) -> str:
        """给单条证据打强度标签。"""
        if score >= self.config.high:
            return VERDICT_SUPPORTED
        if score < self.config.low:
            return VERDICT_NO_EVIDENCE
        return VERDICT_INCONCLUSIVE

    def keep(self, score: float) -> bool:
        """是否值得写进最终输出。"""
        return score >= self.config.keep_score

    def evaluate(
        self,
        scores: Sequence[float],
        *,
        round_index: int = 1,
        max_rounds: int | None = None,
    ) -> GateDecision:
        """对一轮检索的全部候选分数做判定。

        参数
        ----
        scores      : 本轮（含历史累积）候选的 rerank 分数
        round_index : 当前是第几轮检索，从 1 开始
        max_rounds  : 轮次上限，达到后即便证据不足也必须停止
        """
        values = [float(score) for score in scores]
        candidate_count = len(values)
        top_score = max(values, default=0.0)
        strong_hits = sum(1 for score in values if score >= self.config.high)
        weak_hits = sum(1 for score in values if score < self.config.low)

        if not values:
            return GateDecision(
                verdict=VERDICT_NO_EVIDENCE,
                should_stop=True,
                reason=STOP_NO_CANDIDATE,
                candidate_count=0,
            )

        # 高阈值分支：证据够强且够多，立即收手
        if strong_hits >= self.config.min_hits:
            return GateDecision(
                verdict=VERDICT_SUPPORTED,
                should_stop=True,
                reason=STOP_HIGH_THRESHOLD,
                strong_hits=strong_hits,
                weak_hits=weak_hits,
                top_score=top_score,
                candidate_count=candidate_count,
            )

        # 低阈值分支：全员低于低阈值，认定论文库中无支撑
        if top_score < self.config.low:
            return GateDecision(
                verdict=VERDICT_NO_EVIDENCE,
                should_stop=True,
                reason=STOP_LOW_THRESHOLD,
                strong_hits=0,
                weak_hits=weak_hits,
                top_score=top_score,
                candidate_count=candidate_count,
            )

        # 中间区间：还有换词重检的价值，除非轮次已经用完
        exhausted = max_rounds is not None and round_index >= max_rounds
        return GateDecision(
            verdict=VERDICT_INCONCLUSIVE,
            should_stop=exhausted,
            reason=STOP_ROUND_LIMIT if exhausted else CONTINUE_SEARCH,
            strong_hits=strong_hits,
            weak_hits=weak_hits,
            top_score=top_score,
            candidate_count=candidate_count,
        )
