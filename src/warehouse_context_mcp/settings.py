"""Connection settings, read from the environment and an optional ``.env`` file.

Real environment variables win over the file. Empty values count as unset, so a template
``.env`` with blank secrets behaves as if those lines were absent.
"""

from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_COMMON = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    env_ignore_empty=True,
    extra="ignore",
    frozen=True,
)


class _EnvSettings(BaseSettings):
    @classmethod
    def from_env(cls, env_file: Path | None = Path(".env")) -> Self:
        return cls(_env_file=env_file)


class SnowflakeSettings(_EnvSettings):
    model_config = _COMMON | SettingsConfigDict(env_prefix="SNOWFLAKE_")

    account: str
    user: str
    role: str = "WCM_READER"
    warehouse: str = "WCM_XS"
    database: str = "WCM_DEMO"
    schema_name: str = Field(default="SHIPPING", validation_alias="SNOWFLAKE_SCHEMA")
    private_key_path: Path | None = None
    password: SecretStr | None = None
    statement_timeout_s: int = Field(default=60, gt=0)


class DatabricksSettings(_EnvSettings):
    model_config = _COMMON | SettingsConfigDict(env_prefix="DATABRICKS_")

    host: str
    http_path: str
    token: SecretStr
    catalog: str = "workspace"
    schema_name: str = Field(default="wcm_demo", validation_alias="DATABRICKS_SCHEMA")

    @field_validator("host")
    @classmethod
    def _bare_hostname(cls, value: str) -> str:
        return value.removeprefix("https://").rstrip("/")
