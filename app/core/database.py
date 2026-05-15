import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.core.config import get_settings


def initialize_database() -> None:
    settings = get_settings()
    db_path = Path(settings.app_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS import_run_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requested_mode TEXT NOT NULL,
                dry_run INTEGER NOT NULL,
                status TEXT NOT NULL,
                database_name TEXT NOT NULL,
                report_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.commit()


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    connection = sqlite3.connect(settings.app_db_path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()

