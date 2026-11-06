import hashlib
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.config.settings import settings
from app.infrastructure.security.auth import (
    Role,
    TokenPayload,
    create_access_token,
    get_current_user,
    get_principal_tenant,
    get_rate_limit_identity,
    require_roles,
    verify_principal,
)
from app.schemas.errors import AppError, ErrorCode


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


@pytest.fixture
def principals(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings,
        "admin_principals",
        {
            "alice": {"api_key_hash": _hash("alice-key"), "role": "admin", "tenant_id": "t-a"},
            "nokey": {"api_key_hash": "", "role": "admin", "tenant_id": "t-n"},
            "norole": {"api_key_hash": _hash("k"), "role": "", "tenant_id": "t-r"},
        },
    )


def test_create_access_token_uses_integer_exp_claim() -> None:
    token = create_access_token("test-user", Role.ADMIN, "test-tenant")
    payload = jwt.decode(
        token,
        settings.admin_jwt_secret,
        algorithms=[settings.admin_jwt_algorithm],
    )

    assert isinstance(payload["exp"], int)
    parsed = TokenPayload.model_validate(payload)
    assert parsed.sub == "test-user"
    assert parsed.tenant_id == "test-tenant"


@pytest.mark.usefixtures("principals")
def test_verify_principal_accepts_only_matching_key() -> None:
    assert verify_principal("alice", "alice-key") == Role.ADMIN
    assert verify_principal("alice", "wrong") is None
    assert verify_principal("ghost", "alice-key") is None
    assert verify_principal("nokey", "") is None  # 未配置哈希的主体不得放行
    assert verify_principal("norole", "k") is None


@pytest.mark.usefixtures("principals")
def test_tenant_is_derived_from_principal_table() -> None:
    assert get_principal_tenant("alice") == "t-a"
    assert get_principal_tenant("ghost") is None


@pytest.mark.usefixtures("fixed_jwt_secret")
def test_valid_token_round_trip() -> None:
    token = create_access_token("bob", Role.OPERATOR, "t-b")

    user = get_current_user(token)

    assert (user.sub, user.role, user.tenant_id) == ("bob", Role.OPERATOR, "t-b")


@pytest.mark.usefixtures("fixed_jwt_secret")
def test_expired_token_is_rejected() -> None:
    token = create_access_token("bob", Role.OPERATOR, "t-b", expires_minutes=-1)

    with pytest.raises(AppError) as exc:
        get_current_user(token)

    assert exc.value.code == ErrorCode.AUTH_INVALID_TOKEN


@pytest.mark.usefixtures("fixed_jwt_secret")
def test_token_signed_with_unknown_secret_is_rejected() -> None:
    forged = jwt.encode(
        {
            "sub": "eve",
            "role": "admin",
            "tenant_id": "t-x",
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        },
        "attacker-secret-attacker-secret-0123",
        algorithm=settings.admin_jwt_algorithm,
    )

    with pytest.raises(AppError) as exc:
        get_current_user(forged)

    assert exc.value.status_code == 401


@pytest.mark.usefixtures("fixed_jwt_secret")
def test_alg_none_token_is_rejected() -> None:
    unsigned = jwt.encode(
        {"sub": "eve", "role": "admin", "tenant_id": "t", "exp": 4102444800}, "", "none"
    )

    with pytest.raises(AppError):
        get_current_user(unsigned)


def test_rotation_accepts_previous_secret_until_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "admin_jwt_secret", "old-secret-old-secret-old-secret-01")
    old_token = create_access_token("bob", Role.ADMIN, "t-b")

    monkeypatch.setattr(settings, "admin_jwt_secret", "new-secret-new-secret-new-secret-01")
    monkeypatch.setattr(
        settings, "admin_jwt_secret_previous", "old-secret-old-secret-old-secret-01"
    )
    assert get_current_user(old_token).sub == "bob"
    assert get_rate_limit_identity(old_token) == "tenant:t-b:user:bob"

    monkeypatch.setattr(settings, "admin_jwt_secret_previous", "")
    with pytest.raises(AppError):
        get_current_user(old_token)


def test_missing_token_outside_test_env_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "production")

    with pytest.raises(AppError) as exc:
        get_current_user(None)

    assert exc.value.code == ErrorCode.AUTH_INVALID_TOKEN


def test_missing_token_in_test_env_gets_test_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "app_env", "test")

    assert get_current_user(None).role == Role.ADMIN


def test_require_roles_blocks_unlisted_role() -> None:
    checker = require_roles(Role.ADMIN)
    auditor = TokenPayload(sub="a", role=Role.AUDITOR, exp=4102444800, tenant_id="t")
    admin = auditor.model_copy(update={"role": Role.ADMIN})

    with pytest.raises(AppError) as exc:
        checker(auditor)

    assert exc.value.code == ErrorCode.AUTH_FORBIDDEN
    assert checker(admin) is admin
