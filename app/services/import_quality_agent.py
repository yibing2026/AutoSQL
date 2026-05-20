from __future__ import annotations

import json
import re
from typing import Any

import pandas as pd

from app.core.config import get_settings
from app.schemas.data_import import ImportQualityFinding, ImportQualityReview


DATE_HINTS = ("date", "time", "日期", "时间")
AGE_HINTS = ("age", "年龄")
UNIT_RE = re.compile(r"(?:mmol|mg|g/l|g\/l|u/l|iu/l|ng/ml|pg/ml|ml/min|cfu|copies|%|x10\^|10\^)", re.IGNORECASE)
RANGE_RE = re.compile(r"(?:参考值|正常值|范围|区间|--|~|～|-)")
NUMERIC_TEXT_RE = re.compile(r"^\s*-?\d+(?:\.\d+)?\s*$")
EMPTY_AFTER_COLON_RE = re.compile(r"[:：]\s*$")
NULL_LIKE_RE = re.compile(r"^\s*/\s*$")


class ImportQualityAgentService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def review_tables(
        self,
        *,
        tables: dict[str, pd.DataFrame],
        notes: list[str],
        source: str,
        sample_rows: int = 3,
        prefer_ai: bool = False,
    ) -> ImportQualityReview:
        target_tables = self._pick_target_tables(tables)
        findings = self._build_findings(target_tables)
        previews = self._build_previews(target_tables, sample_rows)
        summary = self._build_local_summary(findings, notes, target_tables)

        used_ai = False
        model: str | None = None
        if prefer_ai:
            ai_summary, model = self._try_ai_summary(
                source=source,
                tables=target_tables,
                notes=notes,
                findings=findings,
                previews=previews,
            )
            if ai_summary:
                summary = ai_summary
                used_ai = True

        return ImportQualityReview(
            enabled=True,
            used_ai=used_ai,
            model=model,
            summary=summary,
            findings=findings,
            table_previews=previews,
        )

    def _pick_target_tables(self, tables: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
        std_tables = {name: df for name, df in tables.items() if "_std_" in name}
        return std_tables or tables

    def _build_previews(
        self,
        tables: dict[str, pd.DataFrame],
        sample_rows: int,
    ) -> dict[str, list[dict[str, str | int | float | None]]]:
        previews: dict[str, list[dict[str, str | int | float | None]]] = {}
        for table_name, df in tables.items():
            previews[table_name] = self._records(df.head(sample_rows))
        return previews

    def _records(self, df: pd.DataFrame) -> list[dict[str, str | int | float | None]]:
        records: list[dict[str, str | int | float | None]] = []
        for row in df.to_dict(orient="records"):
            normalized: dict[str, str | int | float | None] = {}
            for key, value in row.items():
                normalized[str(key)] = self._json_value(value)
            records.append(normalized)
        return records

    def _json_value(self, value: Any) -> str | int | float | None:
        if pd.isna(value):
            return None
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if isinstance(value, (int, float, str)):
            return value
        return str(value)

    def _build_findings(self, tables: dict[str, pd.DataFrame]) -> list[ImportQualityFinding]:
        findings: list[ImportQualityFinding] = []
        findings.extend(self._check_relationship_health(tables))

        for table_name, df in tables.items():
            if df.empty:
                continue
            findings.extend(self._check_age_columns(table_name, df))
            findings.extend(self._check_date_columns(table_name, df))
            findings.extend(self._check_test_group_columns(table_name, df))
            findings.extend(self._check_empty_result_columns(table_name, df))
            findings.extend(self._check_slash_nulls(table_name, df))
            findings.extend(self._check_unit_range_presence(table_name, df))
            findings.extend(self._check_import_issue_table(table_name, df))

        severity_order = {"critical": 0, "warning": 1, "info": 2}
        return sorted(findings, key=lambda item: (severity_order.get(item.severity, 9), item.table_name, item.issue_code))

    def _check_relationship_health(self, tables: dict[str, pd.DataFrame]) -> list[ImportQualityFinding]:
        findings: list[ImportQualityFinding] = []
        patient_rows = self._row_count_by_suffix(tables, "_std_patient")
        sample_rows = self._row_count_by_suffix(tables, "_std_sample")
        event_rows = sum(
            len(df)
            for name, df in tables.items()
            if any(name.endswith(suffix) for suffix in ("_std_lab_item", "_std_culture_item", "_std_imaging_item"))
        )
        if event_rows > 0 and patient_rows == 0:
            findings.append(
                ImportQualityFinding(
                    severity="critical",
                    table_name="*",
                    issue_code="missing_patient_dimension",
                    description="标准事件表已有数据，但 patient 标准表为空，后续按患者统计会受影响。",
                    suggestion="优先补医院模板中的主索引字段识别，例如住院号、筛选号、样本编号、性别、年龄。",
                    evidence=[f"event_rows={event_rows}", f"patient_rows={patient_rows}", f"sample_rows={sample_rows}"],
                )
            )
        return findings

    def _row_count_by_suffix(self, tables: dict[str, pd.DataFrame], suffix: str) -> int:
        for table_name, df in tables.items():
            if table_name.endswith(suffix):
                return len(df)
        return 0

    def _check_age_columns(self, table_name: str, df: pd.DataFrame) -> list[ImportQualityFinding]:
        findings: list[ImportQualityFinding] = []
        for column in df.columns:
            if not any(token in str(column).lower() for token in AGE_HINTS):
                continue
            series = df[column].dropna()
            if series.empty:
                continue
            numeric = pd.to_numeric(series, errors="coerce")
            bad_mask = numeric.isna()
            bad_ratio = float(bad_mask.mean())
            if bad_ratio > 0.2:
                samples = series[bad_mask].astype(str).head(3).tolist()
                findings.append(
                    ImportQualityFinding(
                        severity="warning",
                        table_name=table_name,
                        column_name=str(column),
                        issue_code="age_not_numeric",
                        description="年龄列中仍有较多无法直接转成数值的内容。",
                        suggestion="导入前或导入后把年龄统一标准化为数字，必要时抽取文本中的数字部分。",
                        evidence=[f"bad_ratio={bad_ratio:.0%}", *samples],
                    )
                )
        return findings

    def _check_date_columns(self, table_name: str, df: pd.DataFrame) -> list[ImportQualityFinding]:
        findings: list[ImportQualityFinding] = []
        for column in df.columns:
            column_name = str(column)
            normalized = column_name.lower()
            if not any(token in normalized for token in DATE_HINTS):
                continue
            series = df[column].dropna()
            if len(series) < 5:
                continue
            parsed = pd.to_datetime(series, errors="coerce")
            bad_ratio = float(parsed.isna().mean())
            if bad_ratio >= 0.4:
                samples = series[parsed.isna()].astype(str).head(3).tolist()
                findings.append(
                    ImportQualityFinding(
                        severity="warning",
                        table_name=table_name,
                        column_name=column_name,
                        issue_code="date_column_non_date_values",
                        description="日期/时间列中包含较多非日期内容，疑似列错位或字段映射偏差。",
                        suggestion="检查该列是否应拆成 report_date 与 report_text，或调整模板解析规则。",
                        evidence=[f"bad_ratio={bad_ratio:.0%}", *samples],
                    )
                )
        return findings

    def _check_test_group_columns(self, table_name: str, df: pd.DataFrame) -> list[ImportQualityFinding]:
        findings: list[ImportQualityFinding] = []
        if "test_group" not in df.columns:
            return findings
        series = df["test_group"].dropna().astype(str).str.strip()
        if len(series) < 5:
            return findings
        numeric_ratio = float(series.map(lambda value: bool(NUMERIC_TEXT_RE.match(value))).mean())
        if numeric_ratio >= 0.3:
            findings.append(
                ImportQualityFinding(
                    severity="warning",
                    table_name=table_name,
                    column_name="test_group",
                    issue_code="test_group_numeric_pollution",
                    description="test_group 列出现了较多纯数字，通常意味着结果列错位到了分组列。",
                    suggestion="检查原模板的多级表头映射，必要时把数字结果重新归入 item_result。",
                    evidence=[f"numeric_ratio={numeric_ratio:.0%}", *series.head(3).tolist()],
                )
            )
        return findings

    def _check_empty_result_columns(self, table_name: str, df: pd.DataFrame) -> list[ImportQualityFinding]:
        findings: list[ImportQualityFinding] = []
        for column_name in ("item_result", "report_text"):
            if column_name not in df.columns:
                continue
            series = df[column_name].dropna().astype(str)
            bad = series[series.str.contains(EMPTY_AFTER_COLON_RE, na=False)]
            if not bad.empty:
                findings.append(
                    ImportQualityFinding(
                        severity="warning",
                        table_name=table_name,
                        column_name=column_name,
                        issue_code="empty_after_colon",
                        description="结果文本中存在冒号后没有内容的记录，可能是断词或拆分失败。",
                        suggestion="检查该模板的复合结果切分规则，必要时改为整段保留或按换行拆分。",
                        evidence=bad.head(3).tolist(),
                    )
                )
        return findings

    def _check_slash_nulls(self, table_name: str, df: pd.DataFrame) -> list[ImportQualityFinding]:
        findings: list[ImportQualityFinding] = []
        for column in df.columns:
            if not pd.api.types.is_object_dtype(df[column]):
                continue
            series = df[column].dropna().astype(str)
            bad = series[series.str.match(NULL_LIKE_RE, na=False)]
            if not bad.empty:
                findings.append(
                    ImportQualityFinding(
                        severity="info",
                        table_name=table_name,
                        column_name=str(column),
                        issue_code="slash_null_remaining",
                        description="仍检测到仅包含 / 的伪空值。",
                        suggestion="继续把 / 统一标准化为 null，避免统计时被当成有效文本。",
                        evidence=[f"count={len(bad)}", *bad.head(3).tolist()],
                    )
                )
                break
        return findings

    def _check_unit_range_presence(self, table_name: str, df: pd.DataFrame) -> list[ImportQualityFinding]:
        findings: list[ImportQualityFinding] = []
        if not any(table_name.endswith(suffix) for suffix in ("_std_lab_item", "_std_culture_item")):
            return findings
        if "item_result" not in df.columns:
            return findings
        series = df["item_result"].dropna().astype(str)
        if len(series) < 10:
            return findings
        unit_ratio = float(series.str.contains(UNIT_RE, na=False).mean())
        range_ratio = float(series.str.contains(RANGE_RE, na=False).mean())
        if unit_ratio < 0.1 and range_ratio < 0.1:
            findings.append(
                ImportQualityFinding(
                    severity="info",
                    table_name=table_name,
                    column_name="item_result",
                    issue_code="unit_range_not_explicit",
                    description="当前结果文本中很少看到单位或正常值范围，后续若要做更细的医学统计，可能还需要继续结构化。",
                    suggestion="如业务需要，可继续拆出 result_value、result_unit、reference_range 三列。",
                    evidence=[f"unit_ratio={unit_ratio:.0%}", f"range_ratio={range_ratio:.0%}"],
                )
            )
        return findings

    def _check_import_issue_table(self, table_name: str, df: pd.DataFrame) -> list[ImportQualityFinding]:
        findings: list[ImportQualityFinding] = []
        if not table_name.endswith("_std_import_issue") or df.empty:
            return findings
        preview = []
        for _, row in df.head(5).iterrows():
            issue_type = row.get("issue_type")
            issue_count = row.get("issue_count")
            preview.append(f"{issue_type}: {issue_count}")
        findings.append(
            ImportQualityFinding(
                severity="info",
                table_name=table_name,
                issue_code="structured_import_issues_present",
                description="本次导入已经记录了结构化 issue，可直接作为人工复核入口。",
                suggestion="优先查看 issue_count 较高的类型，再决定是否需要补解析规则。",
                evidence=preview,
            )
        )
        return findings

    def _build_local_summary(
        self,
        findings: list[ImportQualityFinding],
        notes: list[str],
        tables: dict[str, pd.DataFrame],
    ) -> str:
        critical = sum(1 for item in findings if item.severity == "critical")
        warning = sum(1 for item in findings if item.severity == "warning")
        info = sum(1 for item in findings if item.severity == "info")
        table_summary = ", ".join(f"{name}={len(df)}" for name, df in tables.items())
        note_summary = " ".join(notes[-3:]) if notes else ""
        return (
            f"自动质检完成：共检查 {len(tables)} 张表，发现 critical={critical}、warning={warning}、info={info}。"
            f" 表行数概览：{table_summary}。"
            f" 最近导入说明：{note_summary}".strip()
        )

    def _try_ai_summary(
        self,
        *,
        source: str,
        tables: dict[str, pd.DataFrame],
        notes: list[str],
        findings: list[ImportQualityFinding],
        previews: dict[str, list[dict[str, str | int | float | None]]],
    ) -> tuple[str | None, str | None]:
        if not self.settings.openai_api_key.strip():
            return None, None
        try:
            from openai import OpenAI
        except ImportError:
            return None, None

        client = OpenAI(api_key=self.settings.openai_api_key)
        payload = {
            "source": source,
            "notes": notes[-10:],
            "table_row_counts": {name: len(df) for name, df in tables.items()},
            "findings": [item.model_dump(mode="json") for item in findings[:20]],
            "previews": previews,
        }
        system_prompt = (
            "You are a clinical data import quality reviewer. "
            "Given row counts, heuristic findings, and sample rows, write a concise Chinese review. "
            "Focus on: column misalignment, suspicious date fields, numeric pollution in test_group, null handling, "
            "and whether the current result text appears to preserve units or reference ranges. "
            "Return plain Chinese text with 3 short parts: overall judgment, main risks, next fixes."
        )

        try:
            response = client.responses.create(
                model=self.settings.openai_chat_model,
                instructions=system_prompt,
                input=json.dumps(payload, ensure_ascii=False),
            )
            text = (getattr(response, "output_text", "") or "").strip()
            if not text:
                return None, self.settings.openai_chat_model
            return text, self.settings.openai_chat_model
        except Exception:
            return None, self.settings.openai_chat_model
