import jwt

from app.config.settings import settings
from app.infrastructure.security.auth import Role, TokenPayload, create_access_token


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
