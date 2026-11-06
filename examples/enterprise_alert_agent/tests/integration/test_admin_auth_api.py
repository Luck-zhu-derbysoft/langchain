import hashlib
from collections.abc import Iterator

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config.settings import settings
from app.infrastructure.security.auth import Role, create_access_token
from app.main import app, limiter


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> Iterator[None]:
    limiter.reset()  # 内存限流器进程内共享，避免用例间互相触发 429
    yield
    limiter.reset()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "app_env", "production")  # 关闭 test 环境的免鉴权后门
    monkeypatch.setattr(
        settings,
        "admin_principals",
        {
            "admin-user": {
                "api_key_hash": hashlib.sha256(b"admin-key").hexdigest(),
                "role": "admin",
                "tenant_id": "tenant-a",
            },
            "auditor-user": {
                "api_key_hash": hashlib.sha256(b"auditor-key").hexdigest(),
                "role": "auditor",
                "tenant_id": "tenant-b",
            },
        },
    )
    return TestClient(app, raise_server_exceptions=False)


def _bearer(user: str, role: Role, tenant: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user, role, tenant)}"}


def test_token_issued_with_server_side_role_and_tenant(client: TestClient) -> None:
    resp = client.post(
        "/admin/token",
        json={"user_id": "auditor-user", "api_key": "auditor-key", "role": "admin"},
    )

    assert resp.status_code == 200
    claims = jwt.decode(
        resp.json()["token"], settings.admin_jwt_secret, algorithms=[settings.admin_jwt_algorithm]
    )
    assert claims["role"] == "auditor"  # 客户端自报的 role 被忽略
    assert claims["tenant_id"] == "tenant-b"


@pytest.mark.parametrize(
    "payload",
    [
        {"user_id": "admin-user", "api_key": "wrong"},
        {"user_id": "ghost", "api_key": "admin-key"},
    ],
)
def test_invalid_credentials_return_401_error_contract(client: TestClient, payload: dict) -> None:
    resp = client.post("/admin/token", json=payload)

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


@pytest.mark.parametrize("path", ["/admin/config", "/ingest/stats", "/admin/dlq/stats"])
def test_protected_endpoints_require_token(client: TestClient, path: str) -> None:
    resp = client.get(path)

    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_INVALID_TOKEN"


def test_rbac_blocks_auditor_from_admin_only_endpoint(client: TestClient) -> None:
    resp = client.get("/admin/config", headers=_bearer("auditor-user", Role.AUDITOR, "tenant-b"))

    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTH_FORBIDDEN"


def test_admin_can_read_config(client: TestClient) -> None:
    resp = client.get("/admin/config", headers=_bearer("admin-user", Role.ADMIN, "tenant-a"))

    assert resp.status_code == 200


def test_expired_token_is_rejected(client: TestClient) -> None:
    token = create_access_token("admin-user", Role.ADMIN, "tenant-a", expires_minutes=-1)

    resp = client.get("/admin/config", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
