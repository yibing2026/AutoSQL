from pydantic import BaseModel


class AppStatusResponse(BaseModel):
    status: str
    service: str
    database: str
    cached_summary_root: str
    downloaded_root: str

