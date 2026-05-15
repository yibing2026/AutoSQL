from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.config import get_settings
from app.schemas.workbook_chat import (
    WorkbookChatAction,
    WorkbookChatPathRequest,
    WorkbookChatPlan,
    WorkbookChatResponse,
)
from app.services.import_jobs import read_workbook_sheets


NULL_LIKE_VALUES = {"", "/", "nan", "none", "null", "n/a", "na", "未填", "空"}
NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


class WorkbookChatAgentService:
    def __init__(self) -> None:
        self.settings = get_settings()
        if not self.settings.openai_api_key.strip():
            raise ValueError("OPENAI_API_KEY is missing. Please set it in .env before using workbook chat.")
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("The openai package is not installed. Run `pip install -r requirements.txt` first.") from exc
        self.client = OpenAI(api_key=self.settings.openai_api_key)

    def run_uploaded_workbook_chat(
        self,
        *,
        filename: str,
        content: bytes,
        instruction: str,
        sheet_name: str = "",
        preview_rows: int = 5,
        save_output: bool = True,
    ) -> WorkbookChatResponse:
        suffix = Path(filename or "upload.xlsx").suffix.lower()
        if suffix not in {".xlsx", ".xls", ".csv"}:
            raise ValueError("Only .xlsx, .xls, and .csv files are supported for workbook chat.")

        temp_path: str | None = None
        try:
            fd, temp_path = tempfile.mkstemp(prefix="autosql_chat_", suffix=suffix or ".xlsx")
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
            return self.run_path_workbook_chat(
                WorkbookChatPathRequest(
                    workbook_source=temp_path,
                    instruction=instruction,
                    sheet_name=sheet_name,
                    preview_rows=preview_rows,
                    save_output=save_output,
                ),
                display_source=filename,
            )
        finally:
            if temp_path and Path(temp_path).exists():
                Path(temp_path).unlink(missing_ok=True)

    def run_path_workbook_chat(
        self,
        request: WorkbookChatPathRequest,
        *,
        display_source: str | None = None,
    ) -> WorkbookChatResponse:
        source = Path(request.workbook_source).expanduser()
        if not source.is_file():
            raise ValueError(f"Workbook source does not exist: {request.workbook_source}")

        workbook = self._load_workbook(source)
        if not workbook:
            raise ValueError("No readable worksheet content was found.")

        before_preview = self._build_preview(workbook, request.preview_rows)
        plan = self._plan_actions(
            workbook=workbook,
            instruction=request.instruction,
            preferred_sheet_name=request.sheet_name,
        )
        updated_workbook, changed_sheets = self._apply_actions(workbook, plan.actions)
        after_preview = self._build_preview(updated_workbook, request.preview_rows)

        edited_workbook_path: str | None = None
        if request.save_output and changed_sheets:
            edited_workbook_path = self._save_workbook(updated_workbook, source)

        return WorkbookChatResponse(
            model=self.settings.openai_chat_model,
            source=display_source or str(source),
            target_sheet=plan.target_sheet,
            available_sheets=list(updated_workbook.keys()),
            changed_sheets=changed_sheets,
            plan=plan,
            preview_before=before_preview,
            preview_after=after_preview,
            edited_workbook_path=edited_workbook_path,
        )

    def _load_workbook(self, source: Path) -> dict[str, pd.DataFrame]:
        workbook: dict[str, pd.DataFrame] = {}
        for sheet_name, df in read_workbook_sheets(source):
            workbook[str(sheet_name)] = df.copy()
        return workbook

    def _build_preview(
        self,
        workbook: dict[str, pd.DataFrame],
        preview_rows: int,
    ) -> dict[str, list[dict[str, Any]]]:
        preview: dict[str, list[dict[str, Any]]] = {}
        for sheet_name, df in workbook.items():
            preview[sheet_name] = self._records_from_df(df.head(preview_rows))
        return preview

    def _records_from_df(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for row in df.to_dict(orient="records"):
            normalized: dict[str, Any] = {}
            for key, value in row.items():
                normalized[str(key)] = self._json_value(value)
            records.append(normalized)
        return records

    def _json_value(self, value: Any) -> Any:
        if pd.isna(value):
            return None
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        return value

    def _plan_actions(
        self,
        *,
        workbook: dict[str, pd.DataFrame],
        instruction: str,
        preferred_sheet_name: str,
    ) -> WorkbookChatPlan:
        workbook_summary = {
            "preferred_sheet_name": preferred_sheet_name or None,
            "sheets": [
                {
                    "sheet_name": sheet_name,
                    "rows": int(len(df)),
                    "columns": [str(col) for col in df.columns],
                    "preview_rows": self._records_from_df(df.head(3)),
                }
                for sheet_name, df in workbook.items()
            ],
        }

        system_prompt = (
            "You are a workbook-editing planner for a clinical Excel import tool. "
            "You must analyze the workbook summary and produce a minimal JSON plan using only the allowed actions. "
            "Never invent sheet names or column names. If the instruction is ambiguous, keep actions empty and ask a concise follow-up question. "
            "Allowed actions are: rename_column, drop_column, replace_values, convert_to_numeric, trim_whitespace, standardize_nulls. "
            "For convert_to_numeric, set extract_number=true when values contain units such as 29岁 or 1岁9天. "
            "For replace_values, use replacements as a list of objects with from_value and to_value. "
            "For standardize_nulls or trim_whitespace, either set sheet_name only to affect all columns in that sheet, or provide columns. "
            "Return JSON only with this exact top-level shape: "
            "{assistant_response, summary, target_sheet, warnings, actions}. "
            "Each action may contain: action, sheet_name, column, new_name, columns, replacements, extract_number, reason."
        )
        user_prompt = json.dumps(
            {
                "instruction": instruction,
                "workbook_summary": workbook_summary,
            },
            ensure_ascii=False,
        )

        response = self.client.responses.create(
            model=self.settings.openai_chat_model,
            instructions=system_prompt,
            input=user_prompt,
        )
        raw_text = (getattr(response, "output_text", "") or "").strip()
        if not raw_text:
            raise RuntimeError("OpenAI returned an empty planning response.")

        plan_payload = self._extract_json(raw_text)
        return WorkbookChatPlan.model_validate(plan_payload)

    def _extract_json(self, text: str) -> dict[str, Any]:
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise RuntimeError("OpenAI response was not valid JSON.")

    def _apply_actions(
        self,
        workbook: dict[str, pd.DataFrame],
        actions: list[WorkbookChatAction],
    ) -> tuple[dict[str, pd.DataFrame], list[str]]:
        updated_workbook = {name: df.copy() for name, df in workbook.items()}
        changed_sheets: list[str] = []

        for action in actions:
            sheet_name = self._resolve_sheet_name(updated_workbook, action.sheet_name)
            if sheet_name is None:
                continue

            df = updated_workbook[sheet_name]
            if self._apply_single_action(df, action):
                if sheet_name not in changed_sheets:
                    changed_sheets.append(sheet_name)
                updated_workbook[sheet_name] = df

        return updated_workbook, changed_sheets

    def _resolve_sheet_name(
        self,
        workbook: dict[str, pd.DataFrame],
        requested_name: str | None,
    ) -> str | None:
        if not requested_name:
            return next(iter(workbook.keys()), None)

        requested_normalized = self._normalize_token(requested_name)
        for sheet_name in workbook:
            if self._normalize_token(sheet_name) == requested_normalized:
                return sheet_name
        return None

    def _resolve_column(self, df: pd.DataFrame, requested_name: str | None) -> str | None:
        if not requested_name:
            return None
        requested_normalized = self._normalize_token(requested_name)
        for column in df.columns:
            if self._normalize_token(str(column)) == requested_normalized:
                return str(column)
        return None

    def _normalize_token(self, value: str) -> str:
        return re.sub(r"\s+", "", value).strip().lower()

    def _apply_single_action(self, df: pd.DataFrame, action: WorkbookChatAction) -> bool:
        if action.action == "rename_column":
            column = self._resolve_column(df, action.column)
            if not column or not action.new_name:
                return False
            df.rename(columns={column: action.new_name}, inplace=True)
            return True

        if action.action == "drop_column":
            column = self._resolve_column(df, action.column)
            if not column:
                return False
            df.drop(columns=[column], inplace=True)
            return True

        if action.action == "replace_values":
            column = self._resolve_column(df, action.column)
            if not column or not action.replacements:
                return False
            replacements = {item.from_value: item.to_value for item in action.replacements}
            df[column] = df[column].replace(replacements)
            return True

        if action.action == "convert_to_numeric":
            column = self._resolve_column(df, action.column)
            if not column:
                return False
            if action.extract_number:
                df[column] = df[column].map(self._extract_number)
            else:
                df[column] = pd.to_numeric(df[column], errors="coerce")
            return True

        if action.action == "trim_whitespace":
            target_columns = self._resolve_columns(df, action.columns)
            if not target_columns:
                target_columns = [str(col) for col in df.columns]
            for column in target_columns:
                df[column] = df[column].map(self._trim_value)
            return True

        if action.action == "standardize_nulls":
            target_columns = self._resolve_columns(df, action.columns)
            if not target_columns:
                target_columns = [str(col) for col in df.columns]
            for column in target_columns:
                df[column] = df[column].map(self._normalize_null_like)
            return True

        return False

    def _resolve_columns(self, df: pd.DataFrame, requested: list[str]) -> list[str]:
        columns: list[str] = []
        for name in requested:
            resolved = self._resolve_column(df, name)
            if resolved and resolved not in columns:
                columns.append(resolved)
        return columns

    def _trim_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return value.strip()
        return value

    def _normalize_null_like(self, value: Any) -> Any:
        if pd.isna(value):
            return None
        if isinstance(value, str) and value.strip().lower() in NULL_LIKE_VALUES:
            return None
        return value

    def _extract_number(self, value: Any) -> float | None:
        if pd.isna(value):
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        text = str(value).strip()
        if not text:
            return None
        matches = NUMBER_RE.findall(text)
        if not matches:
            return None
        if "岁" in text or "月" in text or "天" in text:
            total = 0.0
            year_match = re.search(r"(\d+(?:\.\d+)?)\s*岁", text)
            month_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:个月|月)", text)
            day_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:天|日)", text)
            if year_match:
                total += float(year_match.group(1))
            if month_match:
                total += float(month_match.group(1)) / 12
            if day_match:
                total += float(day_match.group(1)) / 365
            return round(total, 3)
        return float(matches[0])

    def _save_workbook(self, workbook: dict[str, pd.DataFrame], source: Path) -> str:
        output_dir = Path("data/workbook_chat_outputs")
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stem = self._safe_name(source.stem)

        if source.suffix.lower() == ".csv" and len(workbook) == 1:
            output_path = output_dir / f"{stem}_{timestamp}.csv"
            next(iter(workbook.values())).to_csv(output_path, index=False, encoding="utf-8-sig")
            return str(output_path.resolve())

        output_path = output_dir / f"{stem}_{timestamp}.xlsx"
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            for sheet_name, df in workbook.items():
                safe_sheet = sheet_name[:31] or "Sheet1"
                df.to_excel(writer, sheet_name=safe_sheet, index=False)
        return str(output_path.resolve())

    def _safe_name(self, value: str) -> str:
        cleaned = re.sub(r"[^0-9a-zA-Z_\-\u4e00-\u9fff]+", "_", value).strip("_")
        return cleaned or "workbook"
