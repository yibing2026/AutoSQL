from enum import Enum

from pydantic import BaseModel, Field


class ImportMode(str, Enum):
    auto = "auto"
    cached_summary = "cached_summary"
    downloaded_data = "downloaded_data"
    workbook_file = "workbook_file"


class ImportDatabaseConfig(BaseModel):
    host: str
    port: int = Field(default=5432, ge=1, le=65535)
    user: str
    database: str
    admin_database: str


class ImportRunRequest(BaseModel):
    mode: ImportMode = ImportMode.auto
    dry_run: bool = False
    target_database_name: str = Field(
        default="",
        description="Optional PostgreSQL database name for this import run.",
    )
    workbook_source: str = Field(
        default="",
        description="Optional explicit path to a local workbook file to import directly.",
    )
    workbook_table_prefix: str = Field(
        default="",
        description="Optional table-name prefix for workbook sheets.",
    )
    cached_summary_source: str = Field(
        default="",
        description="Optional explicit path to the cached summary workbook.",
    )
    downloaded_root: str = Field(
        default="",
        description="Optional explicit path to the downloaded data root directory.",
    )


class ImportJobResult(BaseModel):
    job_name: str
    discovered: bool
    executed: bool
    source: str | None = None
    tables: dict[str, int] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    skipped_reason: str | None = None


class ImportRunResponse(BaseModel):
    requested_mode: ImportMode
    dry_run: bool
    status: str
    database: ImportDatabaseConfig
    jobs: list[ImportJobResult]
    history_id: int


class ImportHistoryItem(BaseModel):
    id: int
    requested_mode: str
    dry_run: bool
    status: str
    database_name: str
    report_json: str
    created_at: str
