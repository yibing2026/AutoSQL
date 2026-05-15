from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


WorkbookActionType = Literal[
    "rename_column",
    "drop_column",
    "replace_values",
    "convert_to_numeric",
    "trim_whitespace",
    "standardize_nulls",
]


class WorkbookValueReplacement(BaseModel):
    from_value: str | None = Field(default=None)
    to_value: str | None = Field(default=None)


class WorkbookChatAction(BaseModel):
    action: WorkbookActionType
    sheet_name: str | None = Field(default=None)
    column: str | None = Field(default=None)
    new_name: str | None = Field(default=None)
    columns: list[str] = Field(default_factory=list)
    replacements: list[WorkbookValueReplacement] = Field(default_factory=list)
    extract_number: bool = Field(default=False)
    reason: str = Field(default="")


class WorkbookChatPlan(BaseModel):
    assistant_response: str
    summary: str = Field(default="")
    target_sheet: str | None = Field(default=None)
    warnings: list[str] = Field(default_factory=list)
    actions: list[WorkbookChatAction] = Field(default_factory=list)


class WorkbookChatPathRequest(BaseModel):
    workbook_source: str = Field(description="Local workbook path to edit.")
    instruction: str = Field(description="Natural-language editing instruction.")
    sheet_name: str = Field(default="", description="Optional preferred sheet name.")
    preview_rows: int = Field(default=5, ge=1, le=10)
    save_output: bool = Field(default=True)


class WorkbookChatResponse(BaseModel):
    model: str
    source: str
    target_sheet: str | None = None
    available_sheets: list[str] = Field(default_factory=list)
    changed_sheets: list[str] = Field(default_factory=list)
    plan: WorkbookChatPlan
    preview_before: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    preview_after: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    edited_workbook_path: str | None = None
