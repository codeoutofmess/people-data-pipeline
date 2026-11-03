import os
from pathlib import Path

import psycopg

# Local default: the Homebrew Postgres on this machine, no password. Override with DATABASE_URL.
DEFAULT_URL = "postgresql:///people"
SCHEMA = Path(__file__).with_name("schema.sql")


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or os.environ.get("DATABASE_URL", DEFAULT_URL))


def init_db(conn: psycopg.Connection) -> None:
    """Create every table. Running it again changes nothing."""
    conn.execute(SCHEMA.read_text())
    conn.commit()
