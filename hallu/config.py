"""统一配置：路径、模型名、信息失真标签定义。

API 密钥从 ``arag-main/.env`` 加载，经 ``api_client`` 读取。

分类体系权威：
  - 仓库根目录《植物科学科普文本信息失真标注规范 v0.1.md》
  - 仓库根目录《信息失真标签优先级和冲突决策树.md》
  - ``README_1.md`` 仅为项目内索引与冻结口径（含 evidence_level）
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 项目路径
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
HALLU_DIR = PROJECT_ROOT / "hallu"
DATA_DIR = PROJECT_ROOT / "data"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

# 项目内嵌的 arag 模块（检索 + api_client）
ARAG_ROOT = PROJECT_ROOT / "arag-main"
ARAG_API_CLIENT = ARAG_ROOT / "api_client"

# ---------------------------------------------------------------------------
# 将 arag-main 加入 sys.path，复用 api_client
# ---------------------------------------------------------------------------
if ARAG_ROOT.exists() and str(ARAG_ROOT) not in sys.path:
    sys.path.insert(0, str(ARAG_ROOT))

from api_client.config import load_env as _load_env  # noqa: E402

_LOADED = False


def ensure_env() -> None:
    """加载 arag-main/.env（幂等）。"""
    global _LOADED
    if _LOADED:
        return
    env_path = ARAG_ROOT / ".env"
    if env_path.exists():
        _load_env(env_path, override=False)
    _LOADED = True


ensure_env()

# ---------------------------------------------------------------------------
# LLM 配置
# ---------------------------------------------------------------------------
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-plus")
QWEN_LONG_MODEL = os.getenv("QWEN_LONG_MODEL", "qwen-long")
QWEN_TEMPERATURE = float(os.getenv("QWEN_TEMPERATURE", "0.0"))

# ---------------------------------------------------------------------------
# 信息失真分类体系（distortion-v0.1）
# ---------------------------------------------------------------------------
TAXONOMY_VERSION = "distortion-v0.1"

EVIDENCE_LEVELS = ("With_Evidence", "Weak_Evidence", "No_Evidence")

LEVEL1_LABELS = {
    "omission": {"zh": "信息删减", "en": "Omission"},
    "addition": {"zh": "信息添加", "en": "Addition"},
    "substitution": {"zh": "信息替换", "en": "Substitution"},
}

# Level-1 冲突优先级：替换 > 添加 > 删减
LEVEL1_PRIORITY = ("substitution", "addition", "omission")

NO_DISTORTION = "no_distortion"

DISTORTION_LABELS = {
    "context_omission": {
        "level1": "omission",
        "zh": "背景限定删减",
        "en": "Context omission",
        "definition": (
            "删除论文中限定研究对象、环境、实验条件的信息（物种/品种/组织/"
            "细胞类型/发育阶段/环境或处理条件），使结论看起来适用于更广情境。"
            "核心问题：若恢复被删信息，公众号结论的适用范围是否会明显缩小？"
        ),
    },
    "evidence_uncertainty_omission": {
        "level1": "omission",
        "zh": "证据与不确定性删减",
        "en": "Evidence/Uncertainty omission",
        "definition": (
            "删除论文中表达证据强度、不确定程度或研究限制的信息（may/might/"
            "suggest/indicate/potentially/preliminary），使结论显得更确定。"
            "核心问题：删除的信息是否影响「这个结论有多确定」？"
        ),
    },
    "mechanism_omission": {
        "level1": "omission",
        "zh": "机制删减",
        "en": "Mechanism omission",
        "definition": (
            "删除论文中的关键作用机制，使研究发现被简化为更直接、更强的功能关系。"
            "核心问题：删除机制后，科学关系是否被改变？合理机制压缩不算失真。"
        ),
    },
    "function_application_addition": {
        "level1": "addition",
        "zh": "功能/应用添加",
        "en": "Function/Application addition",
        "definition": (
            "增加论文没有证明的功能、用途或应用价值（如抗旱功能、育种应用）。"
            "核心问题：公众号是否提出论文实验没有支持的新功能？"
        ),
    },
    "significance_addition": {
        "level1": "addition",
        "zh": "意义/重要性添加",
        "en": "Significance addition",
        "definition": (
            "增加论文没有支持的重要性评价（first/breakthrough/revolutionary/"
            "key/critical 等）。已有「major regulator」转述为「重要作用」通常不算。"
        ),
    },
    "relation_substitution": {
        "level1": "substitution",
        "zh": "关系替换",
        "en": "Relation substitution",
        "definition": (
            "改变科学关系类型：相关→因果、关联→调控、影响→决定。"
            "注意：contribute to / lead to / result in / drive 本身是因果动词，"
            "对等翻译不算替换。"
        ),
    },
    "magnitude_substitution": {
        "level1": "substitution",
        "zh": "作用程度替换",
        "en": "Magnitude substitution",
        "definition": (
            "改变作用强弱、重要程度或贡献大小（如 contributes → determines）。"
            "正常程度弱化（strongly increases → increases）通常不算失真。"
        ),
    },
    "mechanism_substitution": {
        "level1": "substitution",
        "zh": "机制替换",
        "en": "Mechanism substitution",
        "definition": (
            "将论文中的真实机制替换成另一种机制解释。"
            "同义表达（regulates ABA pathway → participates in ABA signaling）不算。"
        ),
    },
}

# 明确不扩类的现象：8 类覆盖不了时标 needs_manual_review，勿硬塞
UNCOVERED_PHENOMENA = {
    "numerical_change": (
        "精确数值被方向性改动（如 60%→超六成、10 亿→14 亿），"
        "且不能归入 magnitude_substitution（程度词 contributes→determines）。"
    ),
    "semantic_contradiction": (
        "与论文科学含义正负相反，且不能归入 relation_substitution / "
        "mechanism_substitution。"
    ),
    "other": "现有 8 类无法覆盖的独立信息变化。",
}

SEVERITY_VALUES = ("none", "mild", "moderate", "severe")

# 旧 9 类（hallu-9class）只用于读取已有金标；禁止再写入新标注
LEGACY_TAXONOMY_VERSION = "hallu-9class"
LEGACY_LABELS = {
    "certainty_amplification": {"zh": "确定性放大"},
    "mechanism_simplification": {"zh": "机制简化"},
    "scope_generalization": {"zh": "范围泛化"},
    "numerical_distortion": {"zh": "数值失真"},
    "causality_distortion": {"zh": "因果扭曲"},
    "context_stripping": {"zh": "语境剥离"},
    "fact_addition": {"zh": "事实添加"},
    "semantic_contradiction": {"zh": "反义矛盾"},
    "accurate": {"zh": "准确传达"},
}

# 旧→新的提示性对照（不可用于自动改金标；仅文档/评测提示）
LEGACY_TO_NEW_HINT = {
    "accurate": NO_DISTORTION,
    "certainty_amplification": "evidence_uncertainty_omission",
    "scope_generalization": "context_omission",
    "context_stripping": "context_omission",
    "mechanism_simplification": "mechanism_omission",
    "causality_distortion": "relation_substitution",
    "fact_addition": "function_application_addition|significance_addition",
    "numerical_distortion": "uncovered:numerical_change",
    "semantic_contradiction": "uncovered:semantic_contradiction",
}

# 兼容旧 import 名
HALLUCINATION_LABELS = LEGACY_LABELS


def level1_of(level2: str) -> str:
    if level2 == NO_DISTORTION:
        return ""
    info = DISTORTION_LABELS.get(level2) or {}
    return str(info.get("level1") or "")


def label_zh(level2: str) -> str:
    if not level2:
        return ""
    if level2 == NO_DISTORTION:
        return "无失真"
    info = DISTORTION_LABELS.get(level2) or LEGACY_LABELS.get(level2) or {}
    return str(info.get("zh") or level2)


def _as_label_obj(raw: Any) -> dict[str, str]:
    if isinstance(raw, dict):
        level2 = str(raw.get("level2") or "").strip()
        level1 = str(raw.get("level1") or "").strip().lower()
        if level2 and not level1:
            level1 = level1_of(level2)
        return {"level1": level1, "level2": level2}
    if isinstance(raw, str) and raw.strip():
        slug = raw.strip()
        return {"level1": level1_of(slug), "level2": slug}
    return {"level1": "", "level2": ""}


def normalize_classification(clf: dict[str, Any] | None) -> dict[str, Any]:
    """把新旧 classification 字段收成统一视图，不改写原对象。"""
    clf = clf or {}
    evidence_level = clf.get("evidence_level")

    primary = _as_label_obj(clf.get("primary_label"))
    if not primary["level2"]:
        primary = _as_label_obj(clf.get("primary_type"))

    secondary_objs: list[dict[str, str]] = []
    sec_label = clf.get("secondary_label")
    if sec_label:
        obj = _as_label_obj(sec_label)
        if obj["level2"]:
            secondary_objs.append(obj)
    for item in clf.get("secondary_types") or []:
        obj = _as_label_obj(item)
        if obj["level2"] and obj["level2"] not in {s["level2"] for s in secondary_objs}:
            secondary_objs.append(obj)

    level2 = primary["level2"]
    has_distortion = clf.get("has_distortion")
    if has_distortion is None:
        is_accurate = clf.get("is_accurate")
        if evidence_level in ("No_Evidence", "Weak_Evidence"):
            has_distortion = None
        elif is_accurate is not None:
            has_distortion = not bool(is_accurate)
        elif level2 in ("", NO_DISTORTION, "accurate"):
            has_distortion = False if evidence_level == "With_Evidence" else None
        elif level2:
            has_distortion = True

    is_accurate = clf.get("is_accurate")
    if is_accurate is None:
        if evidence_level == "With_Evidence":
            is_accurate = has_distortion is False
        else:
            is_accurate = None

    return {
        "evidence_level": evidence_level,
        "has_distortion": has_distortion,
        "is_accurate": is_accurate,
        "primary_label": primary,
        "secondary_labels": secondary_objs,
        "primary_type": level2,
        "secondary_types": [s["level2"] for s in secondary_objs],
        "severity": clf.get("severity"),
        "needs_manual_review": bool(clf.get("needs_manual_review")),
        "uncovered_phenomenon": str(clf.get("uncovered_phenomenon") or ""),
        "reason": clf.get("reason") or clf.get("reasoning") or "",
    }


def has_distortion(
    evidence_level: str,
    primary_level2: str,
    *,
    explicit: bool | None = None,
) -> bool | None:
    """仅在 With_Evidence 时判定是否存在信息失真。

    Weak_Evidence / No_Evidence：返回 None（不可核实 ≠ 已判定失真类型）。
    """
    if explicit is not None:
        if evidence_level != "With_Evidence":
            return None
        return bool(explicit)
    if evidence_level != "With_Evidence":
        return None
    if not primary_level2 or primary_level2 in (NO_DISTORTION, "accurate"):
        return False
    return True


def is_hallucination(evidence_level: str, primary_type: str) -> bool:
    """已废弃：旧幻觉判定。新代码请用 has_distortion()。

    保留仅为读取旧结果时的兼容；Weak/No 不再视为「幻觉=True」。
    """
    result = has_distortion(evidence_level, primary_type)
    return bool(result)
