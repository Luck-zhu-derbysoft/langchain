from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.config.settings import settings
from app.infrastructure.skill.registry import SkillDescriptor, skill_registry

TIME_TOOLS_METADATA = [
    {
        "type": "function",
        "function": {
            "name": "get_current_datetime",
            "description": "Get current local datetime from system clock. Must be used for date/time questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {
                        "type": "string",
                        "description": "IANA timezone name, e.g. Asia/Shanghai. Optional.",
                    }
                },
                "required": [],
            },
        },
    }
]


async def get_current_datetime(timezone: str | None = None) -> dict[str, Any]:
    tz_name = timezone or settings.app_timezone
    now = datetime.now(ZoneInfo(tz_name))
    weekday_map = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    payload = {
        "iso": now.isoformat(),
        "date": f"{now.year}-{now.month:02d}-{now.day:02d}",
        "time": now.strftime("%H:%M:%S"),
        "weekday_cn": weekday_map[now.weekday()],
        "timezone": tz_name,
        "timestamp": int(now.timestamp()),
    }
    return {
        "status": "success",
        "error_code": "",
        "message": "ok",
        "latency_ms": 0,
        "row_count": 1,
        "data": [payload],
    }


TIME_SKILL_MAP: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {
    "get_current_datetime": get_current_datetime,
}

skill_registry.register(
    SkillDescriptor(
        name="get_current_datetime",
        func=get_current_datetime,  # 占位函数，实际调用通过 skills_map 获取
        metadata=TIME_TOOLS_METADATA[0],
        enabled=settings.time_tool_enabled,
    )
)
