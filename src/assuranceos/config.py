from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _int_env(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name)
    value = default if raw is None else int(raw)
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _database_url() -> str:
    explicit = os.getenv("ASSURANCEOS_DATABASE_URL")
    if explicit:
        return explicit
    path = Path(os.getenv("ASSURANCEOS_DATABASE_PATH", "./var/assuranceos.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


@dataclass(frozen=True)
class Settings:
    environment: str
    database_url: str
    agent_root: Path
    demo_root: Path
    control_test_root: Path
    control_test_public_key: Path
    model_mode: str
    gemini_model: str
    evidence_root: Path
    evidence_export_root: Path
    evidence_storage: str
    evidence_bucket: str | None
    max_evidence_upload_bytes: int
    auth_mode: str
    auth_jwt_issuer: str | None
    auth_jwt_audience: str | None
    auth_jwt_secret: str | None
    auth_jwks_url: str | None
    auth_jwt_algorithms: tuple[str, ...]
    auth_clock_leeway_seconds: int
    auto_create_schema: bool
    trusted_hosts: tuple[str, ...]
    export_signing_private_key: Path | None
    export_signing_public_key: Path | None
    export_signing_key_id: str
    execution_signing_private_key: Path | None
    execution_signing_key_id: str
    execution_envelope_ttl_seconds: int

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"prod", "production"}

    @classmethod
    def from_env(cls) -> "Settings":
        environment = os.getenv("ASSURANCEOS_ENV", "local").strip().lower()
        auth_mode = os.getenv("ASSURANCEOS_AUTH_MODE", "disabled").strip().lower()
        algorithms = tuple(
            item.strip()
            for item in os.getenv("ASSURANCEOS_AUTH_JWT_ALGORITHMS", "HS256").split(",")
            if item.strip()
        )
        trusted_hosts = tuple(
            item.strip()
            for item in os.getenv("ASSURANCEOS_TRUSTED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
            if item.strip()
        )
        private_key = os.getenv("ASSURANCEOS_EXPORT_SIGNING_PRIVATE_KEY")
        public_key = os.getenv("ASSURANCEOS_EXPORT_SIGNING_PUBLIC_KEY")
        execution_private_key = os.getenv("ASSURANCEOS_EXECUTION_SIGNING_PRIVATE_KEY")
        settings = cls(
            environment=environment,
            database_url=_database_url(),
            agent_root=Path(os.getenv("ASSURANCEOS_AGENT_ROOT", "./agents")),
            demo_root=Path(os.getenv("ASSURANCEOS_DEMO_ROOT", "./demo/asteria")),
            control_test_root=Path(os.getenv("ASSURANCEOS_CONTROL_TEST_ROOT", "./tests-library")),
            control_test_public_key=Path(os.getenv("ASSURANCEOS_CONTROL_TEST_PUBLIC_KEY", "./security/release-keys/control-test-release-public.pem")),
            model_mode=os.getenv("ASSURANCEOS_MODEL_MODE", "mock"),
            gemini_model=os.getenv("ASSURANCEOS_GEMINI_MODEL", "gemini-2.5-flash"),
            evidence_root=Path(os.getenv("ASSURANCEOS_EVIDENCE_ROOT", "./var/evidence")),
            evidence_export_root=Path(
                os.getenv("ASSURANCEOS_EVIDENCE_EXPORT_ROOT", "./var/evidence-exports")
            ),
            evidence_storage=os.getenv("ASSURANCEOS_EVIDENCE_STORAGE", "local").strip().lower(),
            evidence_bucket=os.getenv("ASSURANCEOS_EVIDENCE_BUCKET"),
            max_evidence_upload_bytes=_int_env(
                "ASSURANCEOS_MAX_EVIDENCE_UPLOAD_BYTES", 50 * 1024 * 1024, minimum=1
            ),
            auth_mode=auth_mode,
            auth_jwt_issuer=os.getenv("ASSURANCEOS_AUTH_JWT_ISSUER"),
            auth_jwt_audience=os.getenv("ASSURANCEOS_AUTH_JWT_AUDIENCE"),
            auth_jwt_secret=os.getenv("ASSURANCEOS_AUTH_JWT_SECRET"),
            auth_jwks_url=os.getenv("ASSURANCEOS_AUTH_JWKS_URL"),
            auth_jwt_algorithms=algorithms,
            auth_clock_leeway_seconds=_int_env(
                "ASSURANCEOS_AUTH_CLOCK_LEEWAY_SECONDS", 30, minimum=0
            ),
            auto_create_schema=_bool_env(
                "ASSURANCEOS_AUTO_CREATE_SCHEMA", environment in {"local", "test"}
            ),
            trusted_hosts=trusted_hosts,
            export_signing_private_key=Path(private_key) if private_key else None,
            export_signing_public_key=Path(public_key) if public_key else None,
            export_signing_key_id=os.getenv("ASSURANCEOS_EXPORT_SIGNING_KEY_ID", "local-ed25519-v1"),
            execution_signing_private_key=(
                Path(execution_private_key) if execution_private_key else None
            ),
            execution_signing_key_id=os.getenv(
                "ASSURANCEOS_EXECUTION_SIGNING_KEY_ID", "assuranceos-execution-v1"
            ),
            execution_envelope_ttl_seconds=_int_env(
                "ASSURANCEOS_EXECUTION_ENVELOPE_TTL_SECONDS", 900, minimum=1
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.auth_mode not in {"disabled", "jwt"}:
            raise ValueError("ASSURANCEOS_AUTH_MODE must be 'disabled' or 'jwt'")
        if self.evidence_storage not in {"local", "gcs"}:
            raise ValueError("ASSURANCEOS_EVIDENCE_STORAGE must be 'local' or 'gcs'")
        if self.evidence_storage == "gcs" and not self.evidence_bucket:
            raise ValueError("ASSURANCEOS_EVIDENCE_BUCKET is required for GCS storage")
        if self.auth_mode == "jwt":
            if not self.auth_jwt_issuer or not self.auth_jwt_audience:
                raise ValueError("JWT issuer and audience are required when authentication is enabled")
            if bool(self.auth_jwt_secret) == bool(self.auth_jwks_url):
                raise ValueError("configure exactly one of JWT secret or JWKS URL")
            if not self.auth_jwt_algorithms or any(
                item.lower() == "none" for item in self.auth_jwt_algorithms
            ):
                raise ValueError("a signed JWT algorithm is required")
            if self.auth_jwt_secret:
                if len(self.auth_jwt_secret.encode("utf-8")) < 32:
                    raise ValueError("JWT HMAC secret must contain at least 32 bytes")
                if any(not item.upper().startswith("HS") for item in self.auth_jwt_algorithms):
                    raise ValueError("JWT HMAC secrets may only be used with HS algorithms")
            if self.auth_jwks_url:
                if any(item.upper().startswith("HS") for item in self.auth_jwt_algorithms):
                    raise ValueError("JWKS verification cannot use HS algorithms")
                if self.is_production and not self.auth_jwks_url.startswith("https://"):
                    raise ValueError("production JWKS URL must use HTTPS")
        if self.export_signing_private_key and not self.export_signing_private_key.is_file():
            raise ValueError("configured export-signing private key does not exist")
        if self.export_signing_public_key and not self.export_signing_public_key.is_file():
            raise ValueError("configured export-signing public key does not exist")
        if not self.control_test_public_key.is_file():
            raise ValueError("configured control-test release public key does not exist")
        if self.execution_signing_private_key and not self.execution_signing_private_key.is_file():
            raise ValueError("configured execution-envelope signing key does not exist")
        if self.is_production:
            if self.auth_mode == "disabled":
                raise ValueError("authentication cannot be disabled in production")
            if self.database_url.startswith("sqlite"):
                raise ValueError("production requires PostgreSQL; SQLite is a local/test profile")
            if self.auto_create_schema:
                raise ValueError("production schema changes must run through Alembic migrations")
            if not self.trusted_hosts or "*" in self.trusted_hosts:
                raise ValueError("production trusted hosts must be explicit")


settings = Settings.from_env()
