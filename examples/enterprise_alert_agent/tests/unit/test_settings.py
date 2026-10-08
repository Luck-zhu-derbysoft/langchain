import pytest

from app.config.settings import Settings, _validate_secrets


def _make_settings(
    *,
    mysql_host: str,
    mysql_password: str,
    admin_jwt_secret: str = "test-admin-jwt-secret-with-at-least-32-characters",
    app_env: str = "test",
    langgraph_checkpoint_dsn: str = "",
    pg_password: str = "test-pg-password",
) -> Settings:
    return Settings(
        app_env=app_env,
        dashscope_api_key="test-dashscope-key",
        admin_jwt_secret=admin_jwt_secret,
        mysql_host=mysql_host,
        mysql_password=mysql_password,
        redis_password="test-redis-password",
        pg_password=pg_password,
        langgraph_checkpoint_dsn=langgraph_checkpoint_dsn,
    )


def test_mysql_password_is_optional_when_mysql_host_is_unset() -> None:
    _validate_secrets(_make_settings(mysql_host="", mysql_password=""))


def test_mysql_password_is_required_when_mysql_host_is_set() -> None:
    with pytest.raises(SystemExit):
        _validate_secrets(_make_settings(mysql_host="localhost", mysql_password=""))


def test_dev_generates_missing_admin_jwt_secret() -> None:
    settings = _make_settings(
        mysql_host="",
        mysql_password="",
        admin_jwt_secret="",
        app_env="dev",
    )

    _validate_secrets(settings)

    assert len(settings.admin_jwt_secret) >= 32


def test_production_requires_admin_jwt_secret() -> None:
    settings = _make_settings(
        mysql_host="",
        mysql_password="",
        admin_jwt_secret="",
        app_env="prod",
    )

    with pytest.raises(SystemExit):
        _validate_secrets(settings)


def test_postgres_settings_are_loaded_from_checkpoint_dsn() -> None:
    settings = _make_settings(
        mysql_host="",
        mysql_password="",
        pg_password="",
        langgraph_checkpoint_dsn="postgresql://db-user:p%40ssword@db-host:5544/db-name",
    )

    assert settings.pg_host == "db-host"
    assert settings.pg_port == 5544
    assert settings.pg_user == "db-user"
    assert settings.pg_password == "p@ssword"
    assert settings.pg_db == "db-name"
