from __future__ import annotations

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
