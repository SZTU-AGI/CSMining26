"""FRECA Agent 框架升级包.

提供三层 Agent 组件,仅在流水线显式启用时生效;默认配置等价于旧行为.

* :mod:`planner` - Tier 1 规划,决定查哪些 Track
* :mod:`critic`  - Tier 3 自我审视,flag 污染/反证/近答案
* :mod:`tools`  - 给 Agent 用的工具函数(预注入 prompt,非 tool calling)
* :mod:`memory` - FailureModeMemory + CaseMemory
* :mod:`escalation` - 3 模型升级仲裁
"""
from __future__ import annotations

__all__ = ["planner", "critic", "tools", "memory", "escalation"]