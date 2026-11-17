"""Ortam değişkenlerini (.env) tek bir yerden okur."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")


def _dsn(user_var: str, password_var: str) -> str:
    return (
        f"host={os.getenv('POSTGRES_HOST', 'localhost')} "
        f"port={os.getenv('POSTGRES_PORT', '5432')} "
        f"dbname={os.getenv('POSTGRES_DB', 'olist')} "
        f"user={os.environ[user_var]} "
        f"password={os.environ[password_var]}"
    )


def admin_dsn() -> str:
    """Veri yüklemek için yönetici bağlantısı."""
    return _dsn("POSTGRES_ADMIN_USER", "POSTGRES_ADMIN_PASSWORD")


def reader_dsn() -> str:
    """Agent'ların kullandığı salt-okunur bağlantı."""
    return _dsn("POSTGRES_READER_USER", "POSTGRES_READER_PASSWORD")
