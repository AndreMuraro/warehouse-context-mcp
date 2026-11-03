from pathlib import Path

import pytest

from warehouse_context_mcp import cli
from warehouse_context_mcp.settings import DatabricksSettings, SnowflakeSettings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "ACCOUNT",
        "USER",
        "PASSWORD",
        "SCHEMA",
        "ROLE",
        "PRIVATE_KEY_PATH",
        "CONNECTION_NAME",
    ):
        monkeypatch.delenv(f"SNOWFLAKE_{key}", raising=False)
    for key in ("HOST", "HTTP_PATH", "TOKEN", "CATALOG", "SCHEMA"):
        monkeypatch.delenv(f"DATABRICKS_{key}", raising=False)


def test_env_file_with_windows_line_endings_and_blank_secrets(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_bytes(
        b'SNOWFLAKE_ACCOUNT=org-acct\r\nSNOWFLAKE_USER="me"\r\n'
        b"SNOWFLAKE_PASSWORD=\r\n# comment\r\nSNOWFLAKE_SCHEMA=OTHER\r\n"
    )
    settings = SnowflakeSettings.from_env(env)
    assert (settings.account, settings.user, settings.schema_name) == ("org-acct", "me", "OTHER")
    assert settings.password is None  # blank means unset, so browser sign-in is used
    assert settings.role == "WCM_READER"


def test_environment_variables_win_over_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text("SNOWFLAKE_ACCOUNT=from-file\nSNOWFLAKE_USER=me\n", encoding="utf-8")
    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "from-env")
    assert SnowflakeSettings.from_env(env).account == "from-env"


def test_secrets_never_appear_in_reprs(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "DATABRICKS_HOST=https://dbc-1.cloud.databricks.com/\n"
        "DATABRICKS_HTTP_PATH=/sql/1.0/warehouses/abc\nDATABRICKS_TOKEN=dapi-secret\n",
        encoding="utf-8",
    )
    settings = DatabricksSettings.from_env(env)
    assert settings.host == "dbc-1.cloud.databricks.com"
    assert "dapi-secret" not in repr(settings)
    assert settings.token.get_secret_value() == "dapi-secret"


def test_cli_reports_configuration_errors(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--backend", "duckdb"]) == 2
    assert "--duckdb PATH" in capsys.readouterr().err


def test_cli_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="0"):
        cli.main(["--version"])
    assert "warehouse-context-mcp" in capsys.readouterr().out


def test_tokens_can_come_from_docker_secret_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "databricks_token").write_text("dapi-from-secret\n", encoding="utf-8")
    monkeypatch.setenv("DATABRICKS_HOST", "dbc-1.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_HTTP_PATH", "/sql/1.0/warehouses/abc")
    settings = DatabricksSettings(_env_file=None, _secrets_dir=secrets)
    assert settings.token.get_secret_value() == "dapi-from-secret"
    assert "dapi-from-secret" not in repr(settings)


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("dbc-1.cloud.databricks.com", True),
        ("adb-1.2.azuredatabricks.net", True),
        ("attacker.example", False),
        ("dbc-1.cloud.databricks.com.attacker.example", False),
    ],
)
def test_databricks_token_only_goes_to_databricks_domains(
    tmp_path: Path, host: str, *, allowed: bool
) -> None:
    from warehouse_context_mcp.domain import ConfigurationError  # noqa: PLC0415

    env = tmp_path / ".env"
    env.write_text(
        f"DATABRICKS_HOST={host}\nDATABRICKS_HTTP_PATH=/sql/x\nDATABRICKS_TOKEN=t\n",
        encoding="utf-8",
    )
    if allowed:
        assert DatabricksSettings.from_env(env).host == host
    else:
        with pytest.raises(ConfigurationError, match="not a Databricks domain"):
            DatabricksSettings.from_env(env)
