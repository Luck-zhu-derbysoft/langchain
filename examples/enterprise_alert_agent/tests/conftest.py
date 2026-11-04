import os
import sys
from pathlib import Path

# 必须在 import app.* 之前设置：settings 在 import 期加载并校验密钥，CI 没有 .env 会直接 sys.exit(1)
if not os.getenv("RUN_LIVE_TESTS"):
    _TEST_ENV = {
        "APP_ENV": "test",
        "ADMIN_JWT_SECRET": "test-jwt-secret-32-bytes-minimum!!",
        "REDIS_PASSWORD": "test",
        "PG_PASSWORD": "test",
        "DASHSCOPE_API_KEY": "test",
        "REDIS_HOST": "",  # 单测不连真实 Redis
        "PG_HOST": "",
        "MYSQL_HOST": "",
        "RATE_LIMIT_STORAGE_URI": "memory://",
        "LANGSMITH_TRACING": "false",  # 避免后台线程向 LangSmith 上报
    }
    for _key, _value in _TEST_ENV.items():
        os.environ[_key] = _value  # 环境变量优先级高于 .env，保证结果与本机 .env 无关

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """tests/integration 下的用例自动打 integration 标记，便于 -m "not integration" 分层运行。"""
    for item in items:
        if "integration" in Path(str(item.fspath)).parts:
            item.add_marker(pytest.mark.integration)


@pytest.fixture
def fixed_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> str:
    from app.config.settings import settings

    secret = "unit-test-secret-key-0123456789abcd"
    monkeypatch.setattr(settings, "admin_jwt_secret", secret)
    monkeypatch.setattr(settings, "admin_jwt_secret_previous", "")
    return secret
