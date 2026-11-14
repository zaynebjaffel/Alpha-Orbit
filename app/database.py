from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

BASE_DIR = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def database_path() -> Path:
    return Path(os.getenv("FIELD_SHIFT_DATABASE", str(BASE_DIR / "data" / "field_shift.sqlite3")))


def connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database() -> None:
    connection = connect()
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
            migration_version = int(migration.name.split("_", 1)[0])
            if migration_version <= version:
                continue
            connection.executescript(migration.read_text(encoding="utf-8"))
            connection.execute(f"PRAGMA user_version = {migration_version}")
    finally:
        connection.close()

