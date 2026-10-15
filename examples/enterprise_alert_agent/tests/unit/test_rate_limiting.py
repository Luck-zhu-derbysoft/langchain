import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.config.settings import settings
from app.infrastructure.security.auth import Role, create_access_token
from app.main import app, get_user_rate_limit_key


def _request(*, authorization: str | None = None) -> Request:
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode("ascii")))
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/chat/metrics/request-id",
            "headers": headers,
            "client": ("203.0.113.10", 12345),
            "query_string": b"",
            "scheme": "https",
            "server": ("testserver", 443),
        }
    )


def test_rate_limit_key_uses_verified_tenant_and_user() -> None:
    token = create_access_token("rate-user", Role.OPERATOR, "rate-tenant")

    key = get_user_rate_limit_key(_request(authorization=f"Bearer {token}"))

    assert key == "tenant:rate-tenant:user:rate-user"


def test_rate_limit_key_falls_back_to_ip_for_invalid_token() -> None:
    key = get_user_rate_limit_key(_request(authorization="Bearer invalid-token"))

    assert key == "ip:203.0.113.10"


def test_rate_limit_key_falls_back_to_ip_without_token() -> None:
    key = get_user_rate_limit_key(_request())

    assert key == "ip:203.0.113.10"


def test_rate_limit_response_includes_retry_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "admin_api_key", "rate-limit-api-key")
    client = TestClient(app)
    payload = {
        "user_id": "rate-user",
        "tenant_id": "rate-tenant",
        "api_key": "rate-limit-api-key",
    }

    responses = [client.post("/admin/token", json=payload) for _ in range(6)]

    assert all(response.status_code == 200 for response in responses[:5])
    limited_response = responses[-1]
    assert limited_response.status_code == 429
    assert limited_response.headers["x-ratelimit-limit"] == "5"
    assert int(limited_response.headers["retry-after"]) > 0
