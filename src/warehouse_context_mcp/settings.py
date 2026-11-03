"""Connection settings from the environment, an optional ``.env`` file and Docker secrets.

Real environment variables win over the file, which wins over secret files. Empty values
count as unset, so a template ``.env`` with blank secrets behaves as if those lines were
absent.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .domain.errors import ConfigurationError

SECRETS_DIR = Path("/run/secrets")
"""Docker/Compose secrets: a file named like the variable (``databricks_token``) is read
as its value, so secrets need not appear in the container's environment."""

_COMMON = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    env_ignore_empty=True,
    extra="ignore",
    frozen=True,
    # Only when present: pydantic-settings warns about a missing directory.
    secrets_dir=SECRETS_DIR if SECRETS_DIR.is_dir() else None,
)


DATABRICKS_DOMAINS = (
    ".cloud.databricks.com",
    ".gcp.databricks.com",
    ".azuredatabricks.net",
    ".databricks.us",
    ".databricks.azure.us",
)


class _EnvSettings(BaseSettings):
    @classmethod
    def from_env(cls, env_file: Path | None = Path(".env")) -> Self:
        try:
            return cls(_env_file=env_file)
        except ValidationError as err:
            problems = "; ".join(_describe(e) for e in err.errors())
            msg = f"invalid {cls.__name__.removesuffix('Settings')} settings: {problems}"
            raise ConfigurationError(msg) from err


def _describe(error: Mapping[str, object]) -> str:
    loc = error.get("loc")
    field = ".".join(str(p) for p in loc) if isinstance(loc, tuple) else ""
    message = str(error.get("msg", "")).removeprefix("Value error, ")
    return f"{field}: {message}" if field else message


class SnowflakeSettings(_EnvSettings):
    """Either a Snowflake CLI connection name, or an account and user.

    With ``SNOWFLAKE_CONNECTION_NAME``, the named connection from Snowflake CLI's
    ``connections.toml`` supplies the account and sign-in method. Any other value set here
    overrides it; the defaults below do not.
    """

    model_config = _COMMON | SettingsConfigDict(env_prefix="SNOWFLAKE_")

    connection_name: str | None = None
    account: str | None = None
    user: str | None = None
    role: str = "WCM_READER"
    warehouse: str = "WCM_XS"
    database: str = "WCM_DEMO"
    schema_name: str = Field(default="SHIPPING", validation_alias="SNOWFLAKE_SCHEMA")
    private_key_path: Path | None = None
    private_key_passphrase: SecretStr | None = None
    password: SecretStr | None = None
    statement_timeout_s: int = Field(default=60, gt=0)
    require_pii_tags: bool = True
    """Refuse to start if PII tags cannot be read (needs Enterprise edition and grants)."""

    @model_validator(mode="after")
    def _connection_or_account(self) -> Self:
        if self.connection_name is None and not (self.account and self.user):
            msg = (
                "set SNOWFLAKE_CONNECTION_NAME (a Snowflake CLI connection) "
                "or both SNOWFLAKE_ACCOUNT and SNOWFLAKE_USER"
            )
            raise ValueError(msg)
        return self

    def is_set(self, field: str) -> bool:
        """Whether a value came from the environment or .env rather than a default."""
        return field in self.model_fields_set


class DatabricksSettings(_EnvSettings):
    model_config = _COMMON | SettingsConfigDict(env_prefix="DATABRICKS_")

    host: str
    http_path: str
    token: SecretStr
    catalog: str = "workspace"
    schema_name: str = Field(default="wcm_demo", validation_alias="DATABRICKS_SCHEMA")
    require_pii_tags: bool = True
    """Refuse to start if Unity Catalog column tags cannot be read."""
    allow_custom_host: bool = False
    """Accept a host outside the Databricks cloud domains (private link, proxies)."""

    @field_validator("host")
    @classmethod
    def _bare_hostname(cls, value: str) -> str:
        return value.removeprefix("https://").rstrip("/").lower()

    @model_validator(mode="after")
    def _databricks_domain(self) -> Self:
        # The token is sent to this host: a planted .env must not be able to redirect it.
        if not self.allow_custom_host and not self.host.endswith(DATABRICKS_DOMAINS):
            msg = (
                f"DATABRICKS_HOST {self.host!r} is not a Databricks domain; set "
                "DATABRICKS_ALLOW_CUSTOM_HOST=true if that is intended"
            )
            raise ValueError(msg)
        return self
