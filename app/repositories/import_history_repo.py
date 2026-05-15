import json

from app.core.database import get_connection
from app.schemas.data_import import ImportHistoryItem, ImportRunResponse


class ImportHistoryRepository:
    def save_import_run(self, response: ImportRunResponse) -> int:
        with get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO import_run_history (
                    requested_mode,
                    dry_run,
                    status,
                    database_name,
                    report_json
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    response.requested_mode.value,
                    1 if response.dry_run else 0,
                    response.status,
                    response.database.database,
                    "",
                ),
            )
            history_id = int(cursor.lastrowid)
            payload = response.model_copy(update={"history_id": history_id})
            connection.execute(
                """
                UPDATE import_run_history
                SET report_json = ?
                WHERE id = ?
                """,
                (
                    json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, indent=2),
                    history_id,
                ),
            )
            connection.commit()
            return history_id

    def list_history(self, limit: int = 20) -> list[ImportHistoryItem]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT id, requested_mode, dry_run, status, database_name, report_json, created_at
                FROM import_run_history
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        return [
            ImportHistoryItem(
                id=row["id"],
                requested_mode=row["requested_mode"],
                dry_run=bool(row["dry_run"]),
                status=row["status"],
                database_name=row["database_name"],
                report_json=row["report_json"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

