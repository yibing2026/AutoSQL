from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from app.core.config import get_settings
from app.repositories.import_history_repo import ImportHistoryRepository
from app.schemas.data_import import ImportHistoryItem, ImportRunRequest, ImportRunResponse
from app.schemas.system import AppStatusResponse
from app.schemas.workbook_chat import WorkbookChatPathRequest, WorkbookChatResponse
from app.services.data_import_agent import DataImportAgentService
from app.services.workbook_chat_agent import WorkbookChatAgentService

router = APIRouter(prefix="/api/v1", tags=["data-import-agent"])


@router.get("/status", response_model=AppStatusResponse)
def get_status() -> AppStatusResponse:
    settings = get_settings()
    return AppStatusResponse(
        status="ok",
        service=settings.app_name,
        database=settings.import_db_name,
        cached_summary_root=settings.import_cached_summary_root,
        downloaded_root=settings.import_downloaded_root,
    )


@router.post("/imports/run", response_model=ImportRunResponse)
def run_import_agent(request: ImportRunRequest) -> ImportRunResponse:
    try:
        service = DataImportAgentService()
        return service.run_import(request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/imports/upload", response_model=ImportRunResponse)
async def upload_and_import_workbook(
    file: UploadFile = File(...),
    dry_run: bool = Form(default=True),
    workbook_table_prefix: str = Form(default=""),
    target_database_name: str = Form(default=""),
) -> ImportRunResponse:
    try:
        service = DataImportAgentService()
        content = await file.read()
        return service.run_uploaded_workbook(
            filename=file.filename or "upload.xlsx",
            content=content,
            dry_run=dry_run,
            table_prefix=workbook_table_prefix,
            target_database_name=target_database_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/imports/history", response_model=list[ImportHistoryItem])
def list_import_history(limit: int = Query(default=10, ge=1, le=50)) -> list[ImportHistoryItem]:
    repository = ImportHistoryRepository()
    return repository.list_history(limit=limit)


@router.post("/workbook-chat/path", response_model=WorkbookChatResponse)
def chat_edit_workbook_path(request: WorkbookChatPathRequest) -> WorkbookChatResponse:
    try:
        service = WorkbookChatAgentService()
        return service.run_path_workbook_chat(request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/workbook-chat/upload", response_model=WorkbookChatResponse)
async def chat_edit_workbook_upload(
    file: UploadFile = File(...),
    instruction: str = Form(...),
    sheet_name: str = Form(default=""),
    preview_rows: int = Form(default=5),
    save_output: bool = Form(default=True),
) -> WorkbookChatResponse:
    try:
        service = WorkbookChatAgentService()
        content = await file.read()
        return service.run_uploaded_workbook_chat(
            filename=file.filename or "upload.xlsx",
            content=content,
            instruction=instruction,
            sheet_name=sheet_name,
            preview_rows=preview_rows,
            save_output=save_output,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
