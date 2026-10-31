from functools import lru_cache
from pathlib import Path
import os
import secrets

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='DORMPRINT_', env_file='.env.local', extra='ignore')
    environment: str = 'development'
    database_url: str = 'postgresql+psycopg://dormprint:local-test-only@127.0.0.1:55432/dormprint'
    redis_url: str = 'redis://127.0.0.1:56379/0'
    storage_root: Path = Path('.data')
    secret_key: str = ''
    public_origin: str = 'http://localhost:5173'
    secure_cookies: bool = False
    max_request_bytes: int = 220 * 1024 * 1024
    idempotency_hours: int = 24
    soffice_path: str = 'soffice'
    ghostscript_path: str = 'gs'
    conversion_timeout_seconds: int = 120
    worker_lease_seconds: int = 180
    mail_allowed_hosts: str = ''
    allow_insecure_smtp: bool = False
    allow_internal_smtp: bool = False
    mail_timeout_seconds: int = 10
    export_max_rows: int = 10000

    @property
    def cookie_secure(self):
        return self.secure_cookies or self.environment == 'production'

    def master_secret(self) -> str:
        if self.secret_key:
            if len(self.secret_key) < 32:
                raise ValueError('DORMPRINT_SECRET_KEY must contain at least 32 characters')
            return self.secret_key
        if self.environment == 'production':
            raise ValueError('DORMPRINT_SECRET_KEY is required in production')
        self.storage_root.mkdir(parents=True, exist_ok=True)
        key_path = self.storage_root / '.development-master-key'
        try:
            descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return key_path.read_text().strip()
        with os.fdopen(descriptor, 'w') as file:
            value = secrets.token_urlsafe(48)
            file.write(value)
        return value


@lru_cache
def get_settings():
    settings = Settings()
    if settings.environment == 'production' and not settings.public_origin.startswith('https://'):
        raise ValueError('Production PUBLIC_ORIGIN must use HTTPS')
    settings.master_secret()
    return settings
