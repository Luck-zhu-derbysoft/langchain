from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.application.services.chat_service import ChatService
from app.config.settings import settings
from app.infrastructure.security.auth import Role, create_access_token
from app.main import app
from app.schemas.errors import ERROR_SPECS, AppError, ErrorCode, error_payload


@pytest.fixture
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def boom_routes() -> Iterator[None]:
    def raise_unexpected() -> None:
        raise RuntimeError("secret-internal-detail")

    def raise_app_error() -> None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "自定义提示", details={"key": "x"})

    before = len(app.router.routes)
    app.router.add_api_route("/__test/unexpected", raise_unexpected)
    app.router.add_api_route("/__test/app-error", raise_app_error)
    try:
        yield
    finally:
        del app.router.routes[before:]


def _assert_error(body: dict, code: ErrorCode) -> dict:
    error = body["error"]
    assert error["code"] == code.value
    assert error["retryable"] is ERROR_SPECS[code].retryable
    assert "request_id" in error
    assert "detail" not in body
    return error


def test_every_error_code_has_spec() -> None:
    assert set(ERROR_SPECS) == set(ErrorCode)
    for spec in ERROR_SPECS.values():
        assert spec.message


def test_error_payload_shape() -> None:
    body = error_payload(ErrorCode.RATE_LIMIT_EXCEEDED, request_id="rid-1")

    assert body == {
        "error": {
            "code": "RATE_LIMIT_EXCEEDED",
            "message": ERROR_SPECS[ErrorCode.RATE_LIMIT_EXCEEDED].message,
            "retryable": True,
            "request_id": "rid-1",
        }
    }


def test_app_error_uses_spec_status_and_custom_message() -> None:
    err = AppError(ErrorCode.REQUEST_IN_PROGRESS)
    custom = AppError(ErrorCode.PAYLOAD_TOO_LARGE, "超过 1MB")

    assert err.status_code == 409
    assert err.message == ERROR_SPECS[ErrorCode.REQUEST_IN_PROGRESS].message
    assert custom.status_code == 413
    assert custom.message == "超过 1MB"


def test_sse_error_frame_uses_error_code_without_internal_detail() -> None:
    frame = ChatService._error_frame(ErrorCode.MODEL_UPSTREAM_ERROR, "req-1", "trace-1")

    assert frame["type"] == "error"
    assert frame["code"] == "MODEL_UPSTREAM_ERROR"
    assert frame["retryable"] is True
    assert frame["request_id"] == "req-1"
    assert frame["trace_id"] == "trace-1"
    assert frame["message"] == ERROR_SPECS[ErrorCode.MODEL_UPSTREAM_ERROR].message


def test_missing_token_returns_auth_invalid_token(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "app_env", "production")

    response = client.get("/ingest/stats", headers={"X-Request-ID": "rid-auth"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    error = _assert_error(response.json(), ErrorCode.AUTH_INVALID_TOKEN)
    assert error["request_id"] == "rid-auth"


def test_validation_error_lists_fields(client: TestClient) -> None:
    token = create_access_token("err-user", Role.OPERATOR, "err-tenant")

    response = client.post(
        "/chat/stream",
        json={"query": ""},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
    error = _assert_error(response.json(), ErrorCode.VALIDATION_FAILED)
    assert any(item["field"].endswith("query") for item in error["details"])


def test_unknown_route_returns_resource_not_found(client: TestClient) -> None:
    response = client.get("/no-such-route")

    assert response.status_code == 404
    _assert_error(response.json(), ErrorCode.RESOURCE_NOT_FOUND)


def test_unhandled_exception_hides_internal_detail(client: TestClient, boom_routes: None) -> None:
    response = client.get("/__test/unexpected")

    assert response.status_code == 500
    _assert_error(response.json(), ErrorCode.INTERNAL_ERROR)
    assert "secret-internal-detail" not in response.text


def test_app_error_keeps_custom_message_and_details(client: TestClient, boom_routes: None) -> None:
    response = client.get("/__test/app-error")

    assert response.status_code == 404
    error = _assert_error(response.json(), ErrorCode.RESOURCE_NOT_FOUND)
    assert error["message"] == "自定义提示"
    assert error["details"] == {"key": "x"}
