from __future__ import annotations

import re
from typing import Any

import pandas as pd


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    return df.where(pd.notna(df), None).to_dict(orient="records")


def _append_rows(rows: list[dict[str, Any]], table_name: str, df: pd.DataFrame) -> None:
    if df.empty:
        return
    for record in _records(df):
        record["source_table"] = table_name
        rows.append(record)


METADATA_COLUMNS = {"source_file", "sheet_name", "source_row_no"}
PATIENT_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "sex": ("性别", "sex"),
    "age": ("年龄", "age"),
    "screening_no": ("筛查", "screening"),
    "inpatient_no": ("住院号", "病案号", "住院编号", "inpatient"),
    "lab_no": ("检验号", "检验编号", "检验单号", "lab_no", "lis"),
    "sample_no": ("样本号", "样本编号", "sample_no", "sample"),
    "admission_date": ("入院日期", "入院时间", "admission_date", "admission"),
    "admission_diagnosis": ("入院诊断", "临床诊断", "admission_diagnosis", "diagnosis"),
    "pre_sampling_diagnosis": ("送检前诊断", "采样前诊断", "pre_sampling_diagnosis"),
    "post_sampling_diagnosis": ("结果后诊断", "送检后诊断", "post_sampling_diagnosis"),
    "discharge_date": ("出院日期", "出院时间", "discharge_date"),
    "discharge_diagnosis": ("出院诊断", "discharge_diagnosis"),
    "sample_collect_time": ("采样时间", "送检时间", "采集时间", "sample_collect_time"),
    "sample_receive_time": ("接收时间", "接样时间", "sample_receive_time"),
    "sample_accept_time": ("受理时间", "核收时间", "sample_accept_time"),
    "sample_audit_time": ("审核时间", "sample_audit_time"),
}

CULTURE_HINTS = ("培养", "药敏", "涂片", "真菌", "细菌", "鉴定", "esbl", "tb", "抗酸")
IMAGING_HINTS = ("影像", "ct", "mr", "mri", "平扫", "增强", "报告", "阅片", "检查所见", "印象")


HEADER_HINTS = (
    "\u6027\u522b",
    "\u5e74\u9f84",
    "\u7b5b\u9009",
    "\u4f4f\u9662",
    "\u75c5\u6848",
    "\u6837\u672c",
    "\u91c7\u6837",
    "\u9001\u68c0",
    "\u8bca\u65ad",
    "\u68c0\u67e5",
    "\u68c0\u9a8c",
    "\u5f71\u50cf",
    "\u57f9\u517b",
    "\u62a5\u544a",
    "sex",
    "age",
    "screening",
    "inpatient",
    "sample",
    "diagnosis",
    "report",
)
LAYOUT_SKIP_HINTS = ("his", "lis", "\u586b\u5199", "\u7cfb\u7edf", "\u6807\u51c6")


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[\s\-_]+", "", value).lower()


def _find_column(columns: list[str], aliases: tuple[str, ...]) -> str | None:
    normalized_columns = {_normalize_text(column): column for column in columns}
    for alias in aliases:
        alias_norm = _normalize_text(alias)
        for norm, original in normalized_columns.items():
            if alias_norm and alias_norm in norm:
                return original
    return None


def _safe_token(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    cleaned = re.sub(r"[^0-9a-zA-Z_]+", "_", text).strip("_")
    return cleaned or fallback


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and pd.isna(value)) or pd.isna(value)


def _normalize_lines(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for delimiter in ("\uff1b", ";", "\uff5c", "|"):
        normalized = normalized.replace(delimiter, "\n")
    return [line.strip() for line in normalized.split("\n") if line and line.strip() and line.strip() != "/"]


def _split_compound_value(value: Any, default_item: str) -> list[tuple[str, str]]:
    if _is_missing(value):
        return []

    text = str(value).strip()
    if not text or text == "/":
        return []

    lines = _normalize_lines(text)
    pairs: list[tuple[str, str]] = []
    plain_lines: list[str] = []

    for line in lines:
        matched = False
        for delimiter in ("\uff1a", ":", "="):
            if delimiter not in line:
                continue
            item_name, item_result = line.split(delimiter, 1)
            item_name = item_name.strip()
            item_result = item_result.strip()
            if item_name and item_result and item_result != "/":
                pairs.append((item_name, item_result))
                matched = True
                break
        if not matched:
            plain_lines.append(line)

    if pairs:
        pairs.extend((default_item, line) for line in plain_lines)
        return pairs

    return [(default_item, "\n".join(plain_lines))] if plain_lines else []


def _classify_event(sheet_name: str, column_name: str, value: Any) -> str:
    signal = " ".join([sheet_name, column_name, str(value) if not _is_missing(value) else ""]).lower()
    if any(token in signal for token in IMAGING_HINTS):
        return "imaging"
    if any(token in signal for token in CULTURE_HINTS):
        return "culture"
    return "lab"


def _unique_rows(rows: list[dict[str, Any]], key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        key = tuple(row.get(field) for field in key_fields)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _looks_like_unnamed_layout(columns: list[str]) -> bool:
    business_columns = [column for column in columns if column not in METADATA_COLUMNS]
    if not business_columns:
        return False
    unnamed_count = sum(1 for column in business_columns if str(column).startswith("Unnamed:"))
    return unnamed_count / len(business_columns) >= 0.6


def _header_score(values: list[Any]) -> int:
    score = 0
    for value in values:
        normalized = _normalize_text(str(value))
        if not normalized:
            continue
        if any(hint in normalized for hint in HEADER_HINTS):
            score += 1
    return score


def _header_max_length(values: list[Any]) -> int:
    lengths = [len(str(value).strip()) for value in values if not _is_missing(value)]
    return max(lengths, default=0)


def _candidate_layout_header_rows(df: pd.DataFrame) -> list[int]:
    best_row = -1
    best_score = 0

    limit = min(len(df), 20)
    business_columns = [column for column in df.columns if column not in METADATA_COLUMNS]
    for row_index in range(limit):
        values = [df.iloc[row_index][column] for column in business_columns]
        score = _header_score(values)
        if score > best_score:
            best_score = score
            best_row = row_index

    if best_row < 0 or best_score < 3:
        return []

    header_rows = [best_row]
    for offset in (1, 2):
        row_index = best_row - offset
        if row_index < 0:
            break
        values = [df.iloc[row_index][column] for column in business_columns]
        if _header_score(values) < 3:
            break
        if _header_max_length(values) > 40:
            break
        header_rows.insert(0, row_index)
        if len(header_rows) >= 3:
            return header_rows

    for offset in (1, 2):
        row_index = best_row + offset
        if row_index >= len(df):
            break
        values = [df.iloc[row_index][column] for column in business_columns]
        if _header_score(values) < 2:
            break
        if _header_max_length(values) > 40:
            break
        header_rows.append(row_index)
        if len(header_rows) >= 3:
            break
    return header_rows


def _merge_header_cells(values: list[Any]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for value in values:
        if _is_missing(value):
            continue
        text = str(value).strip()
        if not text:
            continue
        text = re.sub(r"\s+", " ", text)
        if text not in seen:
            seen.add(text)
            parts.append(text)
    return " | ".join(parts)


def _unique_column_names(columns: list[str]) -> list[str]:
    output: list[str] = []
    seen: dict[str, int] = {}
    for column in columns:
        base = column.strip() or "column"
        count = seen.get(base, 0)
        seen[base] = count + 1
        output.append(base if count == 0 else f"{base}_{count + 1}")
    return output


def _looks_like_layout_instruction(value: Any) -> bool:
    if _is_missing(value):
        return False
    normalized = _normalize_text(str(value))
    if not normalized:
        return False
    return any(hint in normalized for hint in LAYOUT_SKIP_HINTS)


def _detect_layout_data_start(df: pd.DataFrame, header_rows: list[int], columns: list[str]) -> int:
    start = min(max(header_rows) + 1, len(df))
    candidate_id_columns = [
        _find_column(columns, PATIENT_FIELD_ALIASES[field_name])
        for field_name in ("screening_no", "inpatient_no", "sample_no")
    ]
    candidate_id_columns = [column for column in candidate_id_columns if column]

    for row_index in range(start, min(len(df), start + 10)):
        record = df.iloc[row_index].to_dict()
        first_value = next((record.get(column) for column in columns if column not in METADATA_COLUMNS), None)
        if _looks_like_layout_instruction(first_value):
            continue

        if candidate_id_columns:
            if any(
                not _is_missing(record.get(column)) and not _looks_like_layout_instruction(record.get(column))
                for column in candidate_id_columns
            ):
                return row_index

        non_empty_count = sum(1 for column in columns if column not in METADATA_COLUMNS and not _is_missing(record.get(column)))
        if non_empty_count >= 3:
            return row_index

    return start


def _prepare_layout_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, str | None]:
    columns = [str(column) for column in df.columns]
    if not _looks_like_unnamed_layout(columns):
        return df, None

    header_rows = _candidate_layout_header_rows(df)
    if not header_rows:
        return df, None

    renamed_columns: list[str] = []
    for column in df.columns:
        if column in METADATA_COLUMNS:
            renamed_columns.append(str(column))
            continue
        header_values = [df.iloc[row_index][column] for row_index in header_rows]
        merged_header = _merge_header_cells(header_values)
        renamed_columns.append(merged_header or str(column))

    renamed_columns = _unique_column_names(renamed_columns)
    prepared = df.copy()
    prepared.columns = renamed_columns
    data_start = _detect_layout_data_start(prepared, header_rows, renamed_columns)
    prepared = prepared.iloc[data_start:].reset_index(drop=True)

    header_row_text = ", ".join(str(index + 1) for index in header_rows)
    return prepared, f"Detected layout header rows {header_row_text} and started data rows from row {data_start + 1}."


def build_standard_medical_tables(
    source_tables: dict[str, pd.DataFrame],
    *,
    table_prefix: str = "med",
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    prefix = (table_prefix or "med").lower()

    patient_rows: list[dict[str, Any]] = []
    encounter_rows: list[dict[str, Any]] = []
    sample_rows: list[dict[str, Any]] = []
    lab_rows: list[dict[str, Any]] = []
    culture_rows: list[dict[str, Any]] = []
    imaging_rows: list[dict[str, Any]] = []
    issue_rows: list[dict[str, Any]] = []

    patient_table = next((df for name, df in source_tables.items() if name.endswith("_pt")), pd.DataFrame())

    for record in _records(patient_table):
        patient_rows.append(
            {
                "patient_id": record.get("patient_id"),
                "sex": record.get("sex"),
                "age": record.get("age"),
                "screening_no": record.get("screening_no"),
                "source_file": record.get("source_file"),
                "source_row_no": record.get("source_row_no"),
            }
        )
        encounter_rows.append(
            {
                "encounter_id": record.get("encounter_id"),
                "patient_id": record.get("patient_id"),
                "inpatient_no": record.get("inpatient_no"),
                "admission_date": record.get("admission_date"),
                "admission_diagnosis": record.get("admission_diagnosis"),
                "pre_sampling_diagnosis": record.get("pre_sampling_diagnosis"),
                "post_sampling_diagnosis": record.get("post_sampling_diagnosis"),
                "discharge_date": record.get("discharge_date"),
                "discharge_diagnosis": record.get("discharge_diagnosis"),
                "source_file": record.get("source_file"),
                "source_row_no": record.get("source_row_no"),
            }
        )
        sample_rows.append(
            {
                "sample_id": record.get("sample_id"),
                "patient_id": record.get("patient_id"),
                "encounter_id": record.get("encounter_id"),
                "lab_no": record.get("lab_no"),
                "sample_no": record.get("sample_no"),
                "sample_collect_time": record.get("sample_collect_time"),
                "sample_receive_time": record.get("sample_receive_time"),
                "sample_accept_time": record.get("sample_accept_time"),
                "sample_audit_time": record.get("sample_audit_time"),
                "source_file": record.get("source_file"),
                "source_row_no": record.get("source_row_no"),
            }
        )

    for table_name, df in source_tables.items():
        suffix = table_name.split("_", 1)[-1]
        if suffix in {"pt"}:
            continue
        if suffix in {"csf_rtn", "bio", "imm", "other"}:
            for record in _records(df):
                lab_rows.append(
                    {
                        "patient_id": record.get("patient_id"),
                        "sample_id": None,
                        "encounter_id": None,
                        "event_type": suffix,
                        "test_date": record.get("test_date"),
                        "test_group": record.get("test_group"),
                        "item_name": record.get("item_name"),
                        "item_result": record.get("item_result"),
                        "extra_result": record.get("extra_result"),
                        "source_file": record.get("source_file"),
                        "source_row_no": record.get("source_row_no"),
                        "source_table": table_name,
                    }
                )
        elif suffix in {"csf_cul", "smear", "micro"}:
            for record in _records(df):
                culture_rows.append(
                    {
                        "patient_id": record.get("patient_id"),
                        "sample_id": None,
                        "encounter_id": None,
                        "event_type": suffix,
                        "test_date": record.get("test_date"),
                        "test_group": record.get("test_group"),
                        "item_name": record.get("item_name"),
                        "item_result": record.get("item_result"),
                        "extra_result": record.get("extra_result"),
                        "source_file": record.get("source_file"),
                        "source_row_no": record.get("source_row_no"),
                        "source_table": table_name,
                    }
                )
        elif suffix in {"img"}:
            for record in _records(df):
                imaging_rows.append(
                    {
                        "patient_id": record.get("patient_id"),
                        "sample_id": None,
                        "encounter_id": None,
                        "report_date": record.get("test_date"),
                        "report_name": record.get("test_group"),
                        "report_item": record.get("item_name"),
                        "report_text": record.get("item_result"),
                        "extra_result": record.get("extra_result"),
                        "source_file": record.get("source_file"),
                        "source_row_no": record.get("source_row_no"),
                        "source_table": table_name,
                    }
                )
        elif suffix in {"issue_audit"}:
            for record in _records(df):
                issue_rows.append(
                    {
                        "issue_type": record.get("issue_type"),
                        "issue_count": record.get("issue_count"),
                        "sample_source_rows": record.get("sample_source_rows"),
                        "source_file": record.get("source_file"),
                        "source_table": table_name,
                    }
                )

    notes = [
        "Built unified medical schema tables for cross-hospital expansion.",
        "Current standard schema includes patient, encounter, sample, lab_item, culture_item, imaging_item, and import_issue.",
    ]

    standard_tables = {
        f"{prefix}_patient": pd.DataFrame(patient_rows),
        f"{prefix}_encounter": pd.DataFrame(encounter_rows),
        f"{prefix}_sample": pd.DataFrame(sample_rows),
        f"{prefix}_lab_item": pd.DataFrame(lab_rows),
        f"{prefix}_culture_item": pd.DataFrame(culture_rows),
        f"{prefix}_imaging_item": pd.DataFrame(imaging_rows),
        f"{prefix}_import_issue": pd.DataFrame(issue_rows),
    }
    notes.extend(
        [
            f"{prefix}_patient rows: {len(patient_rows)}.",
            f"{prefix}_encounter rows: {len(encounter_rows)}.",
            f"{prefix}_sample rows: {len(sample_rows)}.",
            f"{prefix}_lab_item rows: {len(lab_rows)}.",
            f"{prefix}_culture_item rows: {len(culture_rows)}.",
            f"{prefix}_imaging_item rows: {len(imaging_rows)}.",
            f"{prefix}_import_issue rows: {len(issue_rows)}.",
        ]
    )
    return standard_tables, notes


def build_generic_standard_medical_tables(
    source_tables: dict[str, pd.DataFrame],
    *,
    table_prefix: str = "med",
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    prefix = (table_prefix or "med").lower()

    patient_rows: list[dict[str, Any]] = []
    encounter_rows: list[dict[str, Any]] = []
    sample_rows: list[dict[str, Any]] = []
    lab_rows: list[dict[str, Any]] = []
    culture_rows: list[dict[str, Any]] = []
    imaging_rows: list[dict[str, Any]] = []
    issue_rows: list[dict[str, Any]] = []

    recognized_sheet_count = 0
    recognizer_notes: list[str] = []

    for table_name, raw_df in source_tables.items():
        if raw_df.empty:
            continue

        df, layout_note = _prepare_layout_dataframe(raw_df)
        if layout_note:
            recognizer_notes.append(f"{table_name}: {layout_note}")

        columns = [str(column) for column in df.columns]
        matched_fields = {
            field_name: _find_column(columns, aliases)
            for field_name, aliases in PATIENT_FIELD_ALIASES.items()
        }
        recognized_field_count = sum(1 for column_name in matched_fields.values() if column_name)
        if recognized_field_count:
            recognized_sheet_count += 1
        else:
            issue_rows.append(
                {
                    "issue_type": "id_fields_not_recognized",
                    "issue_count": len(df),
                    "sample_source_rows": None,
                    "source_file": next((value for value in df.get("source_file", pd.Series(dtype=object)).dropna().head(1)), None),
                    "source_table": table_name,
                }
            )

        for row in _records(df):
            source_file = row.get("source_file")
            sheet_name = row.get("sheet_name") or table_name
            source_row_no = row.get("source_row_no")

            screening_no = row.get(matched_fields["screening_no"]) if matched_fields["screening_no"] else None
            inpatient_no = row.get(matched_fields["inpatient_no"]) if matched_fields["inpatient_no"] else None
            lab_no = row.get(matched_fields["lab_no"]) if matched_fields["lab_no"] else None
            sample_no = row.get(matched_fields["sample_no"]) if matched_fields["sample_no"] else None

            patient_seed = screening_no or inpatient_no or sample_no or f"{table_name}_{source_row_no}"
            patient_id = f"{prefix}_p_{_safe_token(patient_seed, f'row_{source_row_no}')}"
            encounter_seed = inpatient_no or patient_seed or f"{table_name}_{source_row_no}"
            encounter_id = f"{prefix}_e_{_safe_token(encounter_seed, f'row_{source_row_no}')}"
            sample_seed = lab_no or sample_no or encounter_seed or f"{table_name}_{source_row_no}"
            sample_id = f"{prefix}_s_{_safe_token(sample_seed, f'row_{source_row_no}')}"

            if recognized_field_count:
                patient_rows.append(
                    {
                        "patient_id": patient_id,
                        "sex": row.get(matched_fields["sex"]) if matched_fields["sex"] else None,
                        "age": row.get(matched_fields["age"]) if matched_fields["age"] else None,
                        "screening_no": screening_no,
                        "source_file": source_file,
                        "source_row_no": source_row_no,
                        "source_sheet": sheet_name,
                    }
                )
                encounter_rows.append(
                    {
                        "encounter_id": encounter_id,
                        "patient_id": patient_id,
                        "inpatient_no": inpatient_no,
                        "admission_date": row.get(matched_fields["admission_date"]) if matched_fields["admission_date"] else None,
                        "admission_diagnosis": row.get(matched_fields["admission_diagnosis"]) if matched_fields["admission_diagnosis"] else None,
                        "pre_sampling_diagnosis": row.get(matched_fields["pre_sampling_diagnosis"]) if matched_fields["pre_sampling_diagnosis"] else None,
                        "post_sampling_diagnosis": row.get(matched_fields["post_sampling_diagnosis"]) if matched_fields["post_sampling_diagnosis"] else None,
                        "discharge_date": row.get(matched_fields["discharge_date"]) if matched_fields["discharge_date"] else None,
                        "discharge_diagnosis": row.get(matched_fields["discharge_diagnosis"]) if matched_fields["discharge_diagnosis"] else None,
                        "source_file": source_file,
                        "source_row_no": source_row_no,
                        "source_sheet": sheet_name,
                    }
                )
                sample_rows.append(
                    {
                        "sample_id": sample_id,
                        "patient_id": patient_id,
                        "encounter_id": encounter_id,
                        "lab_no": lab_no,
                        "sample_no": sample_no,
                        "sample_collect_time": row.get(matched_fields["sample_collect_time"]) if matched_fields["sample_collect_time"] else None,
                        "sample_receive_time": row.get(matched_fields["sample_receive_time"]) if matched_fields["sample_receive_time"] else None,
                        "sample_accept_time": row.get(matched_fields["sample_accept_time"]) if matched_fields["sample_accept_time"] else None,
                        "sample_audit_time": row.get(matched_fields["sample_audit_time"]) if matched_fields["sample_audit_time"] else None,
                        "source_file": source_file,
                        "source_row_no": source_row_no,
                        "source_sheet": sheet_name,
                    }
                )

            base_columns = {column_name for column_name in matched_fields.values() if column_name}
            base_columns.update(METADATA_COLUMNS)

            for column_name in columns:
                if column_name in base_columns:
                    continue
                value = row.get(column_name)
                if _is_missing(value):
                    continue

                event_type = _classify_event(str(sheet_name), column_name, value)
                pairs = _split_compound_value(value, default_item=column_name)
                target_rows = (
                    imaging_rows if event_type == "imaging" else
                    culture_rows if event_type == "culture" else
                    lab_rows
                )

                if event_type == "imaging":
                    if pairs:
                        for item_name, item_result in pairs:
                            target_rows.append(
                                {
                                    "patient_id": patient_id,
                                    "sample_id": sample_id,
                                    "encounter_id": encounter_id,
                                    "report_date": None,
                                    "report_name": column_name,
                                    "report_item": item_name,
                                    "report_text": item_result,
                                    "extra_result": None,
                                    "source_file": source_file,
                                    "source_row_no": source_row_no,
                                    "source_sheet": sheet_name,
                                    "source_table": table_name,
                                }
                            )
                    else:
                        target_rows.append(
                            {
                                "patient_id": patient_id,
                                "sample_id": sample_id,
                                "encounter_id": encounter_id,
                                "report_date": None,
                                "report_name": column_name,
                                "report_item": column_name,
                                "report_text": value,
                                "extra_result": None,
                                "source_file": source_file,
                                "source_row_no": source_row_no,
                                "source_sheet": sheet_name,
                                "source_table": table_name,
                            }
                        )
                    continue

                normalized_pairs = pairs or [(column_name, value)]
                for item_name, item_result in normalized_pairs:
                    target_rows.append(
                        {
                            "patient_id": patient_id,
                            "sample_id": sample_id,
                            "encounter_id": encounter_id,
                            "event_type": column_name,
                            "test_date": None,
                            "test_group": column_name,
                            "item_name": item_name,
                            "item_result": item_result,
                            "extra_result": None,
                            "source_file": source_file,
                            "source_row_no": source_row_no,
                            "source_sheet": sheet_name,
                            "source_table": table_name,
                        }
                    )

    patient_rows = _unique_rows(patient_rows, ("patient_id",))
    encounter_rows = _unique_rows(encounter_rows, ("encounter_id",))
    sample_rows = _unique_rows(sample_rows, ("sample_id",))

    notes = [
        "Built generic medical schema tables from workbook structure recognition.",
        f"Recognized candidate patient/sample fields in {recognized_sheet_count} worksheet(s).",
        "Generic recognizer classifies remaining columns into lab, culture, or imaging buckets using header and cell-content hints.",
    ]
    notes.extend(recognizer_notes)

    standard_tables = {
        f"{prefix}_std_patient": pd.DataFrame(patient_rows),
        f"{prefix}_std_encounter": pd.DataFrame(encounter_rows),
        f"{prefix}_std_sample": pd.DataFrame(sample_rows),
        f"{prefix}_std_lab_item": pd.DataFrame(lab_rows),
        f"{prefix}_std_culture_item": pd.DataFrame(culture_rows),
        f"{prefix}_std_imaging_item": pd.DataFrame(imaging_rows),
        f"{prefix}_std_import_issue": pd.DataFrame(issue_rows),
    }
    notes.extend(
        [
            f"{prefix}_std_patient rows: {len(patient_rows)}.",
            f"{prefix}_std_encounter rows: {len(encounter_rows)}.",
            f"{prefix}_std_sample rows: {len(sample_rows)}.",
            f"{prefix}_std_lab_item rows: {len(lab_rows)}.",
            f"{prefix}_std_culture_item rows: {len(culture_rows)}.",
            f"{prefix}_std_imaging_item rows: {len(imaging_rows)}.",
            f"{prefix}_std_import_issue rows: {len(issue_rows)}.",
        ]
    )
    return standard_tables, notes
