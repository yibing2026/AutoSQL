from datetime import datetime
import os
import tempfile
from pathlib import Path

from app.core.config import get_settings
from app.repositories.import_history_repo import ImportHistoryRepository
from app.schemas.data_import import (
    ImportDatabaseConfig,
    ImportJobResult,
    ImportMode,
    ImportRunRequest,
    ImportRunResponse,
)
from app.services.anhui_shengli_parser import (
    build_anhui_shengli_tables,
    is_anhui_shengli_workbook,
)
from app.services.import_jobs import (
    build_cached_summary_tables,
    build_downloaded_tables,
    build_workbook_tables,
    collect_counts,
    create_database_if_needed,
    get_engine,
    write_tables,
)
from app.services.medical_schema import build_standard_medical_tables


class DataImportAgentService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.repository = ImportHistoryRepository()

    def _database_config(self, database_name: str | None = None) -> ImportDatabaseConfig:
        return ImportDatabaseConfig(
            host=self.settings.import_db_host,
            port=self.settings.import_db_port,
            user=self.settings.import_db_user,
            database=database_name or self.settings.import_db_name,
            admin_database=self.settings.import_db_admin_name,
        )

    def _resolve_cached_summary_source(self, explicit_path: str) -> Path | None:
        if explicit_path.strip():
            path = Path(explicit_path).expanduser()
            return path if path.is_file() else None

        root = Path(self.settings.import_cached_summary_root).expanduser()
        if not root.is_dir():
            return None

        for path in root.iterdir():
            if path.is_file() and path.suffix.lower() in {".xlsx", ".xls"} and "mq" in path.name.lower():
                return path
        return None

    def _resolve_downloaded_root(self, explicit_path: str) -> Path | None:
        if explicit_path.strip():
            path = Path(explicit_path).expanduser()
            return path if path.is_dir() else None
        root = Path(self.settings.import_downloaded_root).expanduser()
        return root if root.is_dir() else None

    def _resolve_workbook_source(self, explicit_path: str) -> Path | None:
        if not explicit_path.strip():
            return None
        path = Path(explicit_path).expanduser()
        return path if path.is_file() else None

    def _sanitize_db_name(self, value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in value.strip().lower())
        while "__" in cleaned:
            cleaned = cleaned.replace("__", "_")
        cleaned = cleaned.strip("_")
        if not cleaned:
            cleaned = "autosql"
        if cleaned[0].isdigit():
            cleaned = f"db_{cleaned}"
        return cleaned[:63]

    def _generate_workbook_database_name(self, seed: str) -> str:
        timestamp = datetime.now().strftime("%m%d_%H%M%S")
        compact_seed = self._sanitize_db_name(seed)[:12] or "wb"
        return self._sanitize_db_name(f"as_{compact_seed}_{timestamp}")

    def _resolve_database_name(
        self,
        *,
        requested_name: str,
        workbook_seed: str | None = None,
        prefer_new_database: bool = False,
    ) -> str:
        if requested_name.strip():
            return self._sanitize_db_name(requested_name)
        if prefer_new_database and workbook_seed:
            return self._generate_workbook_database_name(workbook_seed)
        return self.settings.import_db_name

    def _run_workbook_path_job(
        self,
        *,
        source_file: Path,
        table_prefix: str,
        dry_run: bool,
        target_database_name: str,
        display_source: str | None = None,
    ) -> tuple[ImportJobResult, str]:
        prefix_seed = table_prefix.strip() or Path(display_source or source_file.name).stem or "workbook"
        normalized_prefix = self._sanitize_db_name(prefix_seed)
        if is_anhui_shengli_workbook(source_file):
            tables, notes = build_anhui_shengli_tables(
                source_file,
                table_prefix=normalized_prefix,
            )
            standard_tables, standard_notes = build_standard_medical_tables(
                tables,
                table_prefix=f"{normalized_prefix}_std",
            )
            tables = {**tables, **standard_tables}
            notes.extend(standard_notes)
            notes.append("Used specialized Anhui Shengli template parser.")
        else:
            tables, notes = build_workbook_tables(
                source_file,
                table_prefix=normalized_prefix,
            )
        resolved_database_name = self._resolve_database_name(
            requested_name=target_database_name,
            workbook_seed=normalized_prefix,
            prefer_new_database=True,
        )
        table_counts = {table_name: len(df) for table_name, df in tables.items()}
        source_label = display_source or str(source_file)

        if dry_run:
            notes.append("Dry run only: database write was skipped.")
            return (
                ImportJobResult(
                    job_name="workbook_file",
                    discovered=True,
                    executed=False,
                    source=source_label,
                    tables=table_counts,
                    notes=notes,
                ),
                resolved_database_name,
            )

        create_database_if_needed(**self._postgres_admin_settings(resolved_database_name))
        engine = get_engine(**self._postgres_settings(resolved_database_name))
        write_tables(engine, tables)
        actual_counts = collect_counts(engine, list(tables.keys()))
        return (
            ImportJobResult(
                job_name="workbook_file",
                discovered=True,
                executed=True,
                source=source_label,
                tables=actual_counts,
                notes=notes,
            ),
            resolved_database_name,
        )

    def _postgres_settings(self, database_name: str) -> dict[str, str | int]:
        return {
            "host": self.settings.import_db_host,
            "port": self.settings.import_db_port,
            "user": self.settings.import_db_user,
            "password": self.settings.import_db_password,
            "database": database_name,
        }

    def _postgres_admin_settings(self, database_name: str) -> dict[str, str | int]:
        return {
            **self._postgres_settings(database_name),
            "admin_database": self.settings.import_db_admin_name,
        }

    def _run_cached_summary_job(self, request: ImportRunRequest) -> ImportJobResult:
        source_file = self._resolve_cached_summary_source(request.cached_summary_source)
        if source_file is None:
            return ImportJobResult(
                job_name="cached_summary",
                discovered=False,
                executed=False,
                skipped_reason="No cached summary workbook was found.",
            )

        tables = build_cached_summary_tables(source_file)
        table_counts = {table_name: len(df) for table_name, df in tables.items()}
        notes = [
            "Imported from the cached MQ summary workbook.",
            "Patient and doctor name columns are removed before loading.",
        ]

        if request.dry_run:
            notes.append("Dry run only: database write was skipped.")
            return ImportJobResult(
                job_name="cached_summary",
                discovered=True,
                executed=False,
                source=str(source_file),
                tables=table_counts,
                notes=notes,
            )

        resolved_database_name = self._resolve_database_name(
            requested_name=request.target_database_name,
        )
        create_database_if_needed(**self._postgres_admin_settings(resolved_database_name))
        engine = get_engine(**self._postgres_settings(resolved_database_name))
        write_tables(engine, tables)
        actual_counts = collect_counts(engine, list(tables.keys()))
        return ImportJobResult(
            job_name="cached_summary",
            discovered=True,
            executed=True,
            source=str(source_file),
            tables=actual_counts,
            notes=notes,
        )

    def _run_workbook_file_job(self, request: ImportRunRequest) -> ImportJobResult:
        source_file = self._resolve_workbook_source(request.workbook_source)
        if source_file is None:
            return ImportJobResult(
                job_name="workbook_file",
                discovered=False,
                executed=False,
                skipped_reason="No workbook_source was provided or the file does not exist.",
            )
        job, _ = self._run_workbook_path_job(
            source_file=source_file,
            table_prefix=request.workbook_table_prefix,
            dry_run=request.dry_run,
            target_database_name=request.target_database_name,
        )
        return job

    def run_uploaded_workbook(
        self,
        *,
        filename: str,
        content: bytes,
        dry_run: bool,
        table_prefix: str = "",
        target_database_name: str = "",
    ) -> ImportRunResponse:
        suffix = Path(filename or "upload.xlsx").suffix.lower()
        if suffix not in {".xlsx", ".xls", ".csv"}:
            raise ValueError("Only .xlsx, .xls, and .csv files are supported for upload.")

        temp_path: str | None = None
        try:
            fd, temp_path = tempfile.mkstemp(prefix="autosql_upload_", suffix=suffix)
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)

            job, resolved_database_name = self._run_workbook_path_job(
                source_file=Path(temp_path),
                table_prefix=table_prefix,
                dry_run=dry_run,
                target_database_name=target_database_name,
                display_source=filename,
            )
            status = "completed" if job.executed else "planned"
            response = ImportRunResponse(
                requested_mode=ImportMode.workbook_file,
                dry_run=dry_run,
                status=status,
                database=self._database_config(resolved_database_name),
                jobs=[job],
                history_id=0,
            )
            history_id = self.repository.save_import_run(response)
            return response.model_copy(update={"history_id": history_id})
        finally:
            if temp_path and Path(temp_path).exists():
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except PermissionError:
                    pass

    def _run_downloaded_data_job(self, request: ImportRunRequest) -> ImportJobResult:
        root = self._resolve_downloaded_root(request.downloaded_root)
        if root is None:
            return ImportJobResult(
                job_name="downloaded_data",
                discovered=False,
                executed=False,
                skipped_reason="No downloaded data root directory was found.",
            )

        tables, notes = build_downloaded_tables(root)
        table_counts = {table_name: len(df) for table_name, df in tables.items()}
        has_rows = any(count > 0 for count in table_counts.values())

        if not has_rows:
            return ImportJobResult(
                job_name="downloaded_data",
                discovered=False,
                executed=False,
                source=str(root),
                tables=table_counts,
                notes=notes,
                skipped_reason="Downloaded root exists, but no importable data was found.",
            )

        if request.dry_run:
            notes.append("Dry run only: database write was skipped.")
            return ImportJobResult(
                job_name="downloaded_data",
                discovered=True,
                executed=False,
                source=str(root),
                tables=table_counts,
                notes=notes,
            )

        resolved_database_name = self._resolve_database_name(
            requested_name=request.target_database_name,
        )
        create_database_if_needed(**self._postgres_admin_settings(resolved_database_name))
        engine = get_engine(**self._postgres_settings(resolved_database_name))
        write_tables(engine, tables)
        actual_counts = collect_counts(engine, list(tables.keys()))
        return ImportJobResult(
            job_name="downloaded_data",
            discovered=True,
            executed=True,
            source=str(root),
            tables=actual_counts,
            notes=notes,
        )

    def run_import(self, request: ImportRunRequest) -> ImportRunResponse:
        jobs: list[ImportJobResult] = []
        resolved_database_name = self._resolve_database_name(
            requested_name=request.target_database_name,
        )

        if request.mode == ImportMode.workbook_file:
            workbook_source = self._resolve_workbook_source(request.workbook_source)
            if workbook_source is None:
                jobs.append(
                    ImportJobResult(
                        job_name="workbook_file",
                        discovered=False,
                        executed=False,
                        skipped_reason="No workbook_source was provided or the file does not exist.",
                    )
                )
            else:
                workbook_job, resolved_database_name = self._run_workbook_path_job(
                    source_file=workbook_source,
                    table_prefix=request.workbook_table_prefix,
                    dry_run=request.dry_run,
                    target_database_name=request.target_database_name,
                    display_source=request.workbook_source or None,
                )
                jobs.append(workbook_job)
        if request.mode in {ImportMode.auto, ImportMode.cached_summary}:
            jobs.append(self._run_cached_summary_job(request))
        if request.mode in {ImportMode.auto, ImportMode.downloaded_data}:
            jobs.append(self._run_downloaded_data_job(request))

        if any(job.executed for job in jobs):
            status = "completed"
        elif any(job.discovered for job in jobs):
            status = "planned" if request.dry_run else "ready"
        else:
            status = "no_sources"

        response = ImportRunResponse(
            requested_mode=request.mode,
            dry_run=request.dry_run,
            status=status,
            database=self._database_config(resolved_database_name),
            jobs=jobs,
            history_id=0,
        )
        history_id = self.repository.save_import_run(response)
        return response.model_copy(update={"history_id": history_id})
