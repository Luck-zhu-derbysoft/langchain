"""技能注册表：技能模块自行登记，主流程（chat_service）只消费注册表，无需感知具体技能。"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)
SkillFunc = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class SkillDescriptor:
    """技能描述符"""

    name: str
    func: SkillFunc
    metadata: dict[str, Any]
    enabled: bool = True


class SkillRegistry:
    def __init__(self) -> None:
        self._registry: dict[str, SkillDescriptor] = {}

    def register(self, descriptor: SkillDescriptor) -> None:
        if descriptor.name in self._registry:
            logger.warning("Skill %s is already registered. Overwriting.", descriptor.name)
        self._registry[descriptor.name] = descriptor
        logger.info("Skill registered: %s (enabled=%s)", descriptor.name, descriptor.enabled)

    # 描述工具叫什么、参数是什么，**不包含执行逻辑**
    def metadata(self) -> list[dict[str, Any]]:
        return [
            {
                "name": descriptor.name,
                "metadata": descriptor.metadata,
            }
            for descriptor in self._registry.values()
        ]

    # key = 工具名，value 是可 await 的函数。
    # 返回所有已启用技能的映射，key = 工具名，value = 可 await 的函数。

    def skills_map(self) -> dict[str, Callable[..., Any]]:
        return {
            descriptor.name: descriptor.func
            for descriptor in self._registry.values()
            if descriptor.enabled
        }


skill_registry = SkillRegistry()
