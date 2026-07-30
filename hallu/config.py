"""统一配置：路径、模型名、幻觉标签定义。

API 密钥从 ``arag-main/.env`` 加载，经 ``api_client`` 读取。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

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
# 幻觉分类体系（9 类标签定义）
# ---------------------------------------------------------------------------
HALLUCINATION_LABELS = {
    "certainty_amplification": {
        "zh": "确定性放大",
        "definition": "将论文中的hedging/审慎表述（如may, suggest, indicate）强化为确定性断言",
    },
    "mechanism_simplification": {
        "zh": "机制简化",
        "definition": "将多层次复杂机制简化为单一关键因子，丢失了机制的复杂性和多层次调控",
    },
    "scope_generalization": {
        "zh": "范围泛化",
        "definition": "将特定条件（某物种、某组织、某发育阶段）下的结论扩展至更广范围",
    },
    "numerical_distortion": {
        "zh": "数值失真",
        "definition": "数值被改变、模糊化或选择性引用，如百分比、样本量、统计值等",
    },
    "causality_distortion": {
        "zh": "因果扭曲",
        "definition": "将相关性表述为因果、因果方向颠倒、或夸大因果强度",
    },
    "context_stripping": {
        "zh": "语境剥离",
        "definition": "关键实验条件、方法局限、样本信息被省略，使结论看似普适",
    },
    "fact_addition": {
        "zh": "事实添加",
        "definition": "加入论文中不存在的断言、数据或结论",
    },
    "semantic_contradiction": {
        "zh": "反义矛盾",
        "definition": "与论文原文意思直接相反或明显矛盾",
    },
    "accurate": {
        "zh": "准确传达",
        "definition": "无信息失真，准确传达论文原意",
    },
}
