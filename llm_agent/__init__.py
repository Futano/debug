"""
大模型智能体模块
用于构建 LLM 提示词和调用大模型
"""

from .prompt_builder import PromptGenerator
from .prompt_templates import (
    GUIContextTemplate,
    FunctionMemoryTemplate,
    TestHistoryTemplate,  # Backward compatibility alias
    SupervisorPromptTemplate,  # NEW: 监管者提示词模板
    UserContext,  # NEW: 用户上下文信息
    build_initial_prompt,
    build_test_prompt,
    build_feedback_prompt
)
from .llm_client import LLMClient
from .memory_manager import TestingSequenceMemorizer
from .supervisor import SupervisorModel, ReviewResult  # NEW: 监管者模型
from .exploration_cache import ExplorationCache  # 探索缓存

__all__ = [
    "PromptGenerator",
    "GUIContextTemplate",
    "FunctionMemoryTemplate",
    "TestHistoryTemplate",
    "SupervisorPromptTemplate",  # NEW
    "UserContext",  # NEW
    "build_initial_prompt",
    "build_test_prompt",
    "build_feedback_prompt",
    "LLMClient",
    "TestingSequenceMemorizer",
    "SupervisorModel",  # NEW
    "ReviewResult",  # NEW
    "ExplorationCache"  # 探索缓存
]