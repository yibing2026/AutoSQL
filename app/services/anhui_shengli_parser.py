from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import pandas as pd


ANHUI_NAME_TOKENS = ("\u5b89\u5fbd\u7701\u7acb", "\u7701\u7acb\u533b\u9662")
ANHUI_SHEET_HINTS = ("\u524d\u77bb\u6837\u672c\u4fe1\u606f", "\u6837\u672c\u4fe1\u606f", "\u4fe1\u606f\u5927\u8868")
DATE_RE = re.compile(r"^\d{4}[./-]\d{1,2}[./-]\d{1,2}$")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null"} or text == "/":
        return None
    return text


def _safe_cell(row: pd.Series, index_1_based: int) -> str | None:
    if index_1_based <= 0 or index_1_based > len(row):
        return None
    return _text(row.iloc[index_1_based - 1])


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[\s\-_]+", "", value).lower()


def _normalize_lines(text: str | None) -> list[str]:
    if not text:
        return []

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for delimiter in ("\uff1b", ";", "\uff5c", "|"):
        normalized = normalized.replace(delimiter, "\n")

    return [
        line.strip()
        for line in normalized.split("\n")
        if line and line.strip() and line.strip() != "/"
    ]


def _split_item_result(line: str) -> tuple[str, str] | None:
    for delimiter in ("\uff1a", ":", "=", "\uff5c", "|"):
        if delimiter not in line:
            continue
        item_name, item_result = line.split(delimiter, 1)
        item_name = item_name.strip()
        item_result = item_result.strip()
        if item_name and item_result and item_result != "/":
            return item_name, item_result
    return None


def _parse_item_result_block(
    text: str | None,
    *,
    default_item: str | None = None,
) -> list[tuple[str, str]]:
    lines = _normalize_lines(text)
    if not lines:
        return []

    pairs: list[tuple[str, str]] = []
    plain_lines: list[str] = []

    for line in lines:
        if re.match(r"^[^:=\uff1a|]+[:=\uff1a|]\s*/\s*$", line):
            continue
        pair = _split_item_result(line)
        if pair is None:
            plain_lines.append(line)
            continue
        pairs.append(pair)

    if pairs:
        fallback_item = default_item or "item"
        pairs.extend((fallback_item, line) for line in plain_lines)
        return pairs

    merged = "\n".join(plain_lines)
    return [(default_item or "item", merged)] if merged else []


def _derive_group_from_payload(text: str | None) -> str | None:
    lines = _normalize_lines(text)
    for line in lines:
        if re.match(r"^[^:=\uff1a|]+[:=\uff1a|]\s*/?\s*$", line):
            continue
        pair = _split_item_result(line)
        if pair and not _looks_like_identifier(pair[0]):
            return pair[0]
    return None


def _looks_like_date(value: str | None) -> bool:
    return bool(value and DATE_RE.match(value))


def _extract_date_token(value: str | None) -> str | None:
    text = _text(value)
    if not text:
        return None
    match = re.search(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}", text)
    return match.group(0) if match else None


def _parse_age_years(value: str | None) -> float | None:
    text = _text(value)
    if not text:
        return None

    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)

    total_years = 0.0
    matched = False

    year_match = re.search(r"(\d+(?:\.\d+)?)\s*\u5c81", text)
    if year_match:
        total_years += float(year_match.group(1))
        matched = True

    month_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:\u4e2a\u6708|\u6708)", text)
    if month_match:
        total_years += float(month_match.group(1)) / 12
        matched = True

    day_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:\u5929|\u65e5)", text)
    if day_match:
        total_years += float(day_match.group(1)) / 365
        matched = True

    if matched:
        return round(total_years, 3)

    fallback = re.search(r"\d+(?:\.\d+)?", text)
    return float(fallback.group(0)) if fallback else None


def _safe_token(value: str | None, fallback: str) -> str:
    raw = (value or "").strip()
    if not raw or raw in {"\u5f85\u5b9a", "/"}:
        return fallback
    return "".join(ch if ch.isalnum() else "_" for ch in raw).strip("_") or fallback


def _make_patient_keys(
    *,
    prefix: str,
    patient_index: int,
    inpatient_no: str | None,
    lab_no: str | None,
    sample_no: str | None,
) -> dict[str, str]:
    patient_stub = f"p{patient_index:04d}"
    patient_id = f"{prefix}_{patient_stub}"
    encounter_id = f"{prefix}_e_{_safe_token(inpatient_no, patient_stub)}"
    sample_seed = lab_no or sample_no or inpatient_no or patient_stub
    sample_id = f"{prefix}_s_{_safe_token(sample_seed, patient_stub)}"
    return {
        "patient_id": patient_id,
        "encounter_id": encounter_id,
        "sample_id": sample_id,
    }


def _new_section_row(
    *,
    patient_id: str,
    source_row_no: int,
    source_file: str,
    test_date: str | None,
    test_group: str | None,
    item_name: str,
    item_result: str,
    extra_result: str | None = None,
) -> dict[str, Any]:
    return {
        "patient_id": patient_id,
        "source_row_no": source_row_no,
        "source_file": source_file,
        "test_date": test_date,
        "test_group": test_group,
        "item_name": item_name,
        "item_result": item_result,
        "extra_result": extra_result,
    }


def _append_issue(
    issue_counts: dict[str, int],
    issue_rows: dict[str, list[int]],
    issue_key: str,
    source_row_no: int,
) -> None:
    issue_counts[issue_key] += 1
    if source_row_no not in issue_rows[issue_key] and len(issue_rows[issue_key]) < 5:
        issue_rows[issue_key].append(source_row_no)


def _looks_like_identifier(value: str | None) -> bool:
    return bool(value and re.fullmatch(r"\d{6,}", value))


def is_anhui_shengli_workbook(source_file: Path) -> bool:
    source_label = _normalize_text(source_file.stem)

    try:
        with pd.ExcelFile(source_file) as workbook:
            sheet_names = workbook.sheet_names
            first_sheet = pd.read_excel(source_file, sheet_name=0, header=None, nrows=12)
    except Exception:
        return False

    normalized_sheet_names = [_normalize_text(name) for name in sheet_names]
    joined_sheet_names = " ".join(normalized_sheet_names)
    joined_head = _normalize_text(" ".join(_text(value) or "" for value in first_sheet.to_numpy().flatten()))

    has_anhui_name = any(token in source_file.stem for token in ANHUI_NAME_TOKENS) or any(
        _normalize_text(token) in joined_sheet_names for token in ANHUI_NAME_TOKENS
    )
    has_template_hint = any(
        _normalize_text(token) in joined_sheet_names
        or _normalize_text(token) in joined_head
        or _normalize_text(token) in source_label
        for token in ANHUI_SHEET_HINTS
    )

    return has_anhui_name and has_template_hint and first_sheet.shape[1] >= 50


def build_anhui_shengli_tables(
    source_file: Path,
    *,
    table_prefix: str = "ahs",
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    df = pd.read_excel(source_file, sheet_name=0, header=None)
    prefix = (table_prefix or "ahs").lower()

    base_rows: list[dict[str, Any]] = []
    csf_rtn_rows: list[dict[str, Any]] = []
    bio_rows: list[dict[str, Any]] = []
    csf_cul_rows: list[dict[str, Any]] = []
    smear_rows: list[dict[str, Any]] = []
    imm_rows: list[dict[str, Any]] = []
    micro_rows: list[dict[str, Any]] = []
    other_rows: list[dict[str, Any]] = []
    img_rows: list[dict[str, Any]] = []

    issue_counts: dict[str, int] = defaultdict(int)
    issue_rows: dict[str, list[int]] = defaultdict(list)
    age_parse_failures = 0

    current_patient: dict[str, Any] | None = None

    def append_section_rows(
        bucket: list[dict[str, Any]],
        *,
        section_label: str,
        source_row_no: int,
        test_date: str | None,
        test_group: str | None,
        payload_text: str | None,
        extra_result: str | None = None,
        extra_item_name: str | None = None,
    ) -> None:
        if current_patient is None:
            return

        normalized_test_date = _extract_date_token(test_date)
        if test_date and normalized_test_date is None and test_date != "\u4e0d\u8be6":
            _append_issue(issue_counts, issue_rows, f"{section_label}_invalid_test_date", source_row_no)

        if not any((normalized_test_date, test_group, payload_text, extra_result)):
            return

        effective_group = test_group
        if _looks_like_identifier(effective_group):
            _append_issue(issue_counts, issue_rows, f"{section_label}_numeric_test_group", source_row_no)
            effective_group = None

        if not effective_group:
            effective_group = _derive_group_from_payload(payload_text)

        parsed_pairs = _parse_item_result_block(
            payload_text,
            default_item=effective_group or section_label,
        )

        if not parsed_pairs and effective_group and extra_result and not _looks_like_date(extra_result):
            parsed_pairs = [(effective_group, extra_result)]
            extra_result = None

        for item_name, item_result in parsed_pairs:
            bucket.append(
                _new_section_row(
                    patient_id=current_patient["patient_id"],
                    source_row_no=source_row_no,
                    source_file=source_file.name,
                    test_date=normalized_test_date,
                    test_group=effective_group or section_label,
                    item_name=item_name,
                    item_result=item_result,
                )
            )

        if extra_result:
            if _looks_like_date(extra_result):
                _append_issue(issue_counts, issue_rows, f"{section_label}_date_like_extra_result", source_row_no)
            else:
                bucket.append(
                    _new_section_row(
                        patient_id=current_patient["patient_id"],
                        source_row_no=source_row_no,
                        source_file=source_file.name,
                        test_date=normalized_test_date,
                        test_group=effective_group or section_label,
                        item_name=extra_item_name or "extra_result",
                        item_result=extra_result,
                    )
                )

    def parse_standard_section(
        bucket: list[dict[str, Any]],
        *,
        section_label: str,
        row: pd.Series,
        source_row_no: int,
        test_date_col: int,
        payload_col: int,
        test_group_col: int | None = None,
        extra_result_col: int | None = None,
        extra_item_name: str | None = None,
    ) -> None:
        append_section_rows(
            bucket,
            section_label=section_label,
            source_row_no=source_row_no,
            test_date=_safe_cell(row, test_date_col),
            test_group=_safe_cell(row, test_group_col) if test_group_col else None,
            payload_text=_safe_cell(row, payload_col),
            extra_result=_safe_cell(row, extra_result_col) if extra_result_col else None,
            extra_item_name=extra_item_name,
        )

    def parse_micro_section(row: pd.Series, source_row_no: int) -> None:
        micro_date = _safe_cell(row, 45)
        micro_sample_no = _safe_cell(row, 46)
        micro_group = _safe_cell(row, 47)
        micro_payload = _safe_cell(row, 48)
        micro_extra = _safe_cell(row, 49)

        if not any((micro_date, micro_group, micro_payload, micro_extra)):
            return

        shifted_left = (
            micro_date is not None
            and _extract_date_token(micro_date) is None
            and micro_sample_no is not None
            and micro_group is not None
            and micro_payload is None
        )

        if shifted_left:
            _append_issue(issue_counts, issue_rows, "micro_shifted_left", source_row_no)
            micro_date = None
            micro_group, micro_payload = micro_sample_no, micro_group

        if _looks_like_identifier(micro_group) and micro_sample_no and not _looks_like_identifier(micro_sample_no):
            _append_issue(issue_counts, issue_rows, "micro_numeric_test_group", source_row_no)
            micro_group = micro_sample_no

        if not any((micro_date, micro_group, micro_payload)) and _looks_like_date(micro_extra):
            _append_issue(issue_counts, issue_rows, "micro_orphan_shifted_date", source_row_no)
            return

        if _looks_like_date(micro_extra):
            _append_issue(issue_counts, issue_rows, "micro_shifted_other_date", source_row_no)
            micro_extra = None

        append_section_rows(
            micro_rows,
            section_label="micro",
            source_row_no=source_row_no,
            test_date=micro_date,
            test_group=micro_group,
            payload_text=micro_payload,
            extra_result=micro_extra,
            extra_item_name="susceptibility",
        )

    def parse_other_section(row: pd.Series, source_row_no: int) -> None:
        col49 = _safe_cell(row, 49)
        col50 = _safe_cell(row, 50)
        col51 = _safe_cell(row, 51)
        col52 = _safe_cell(row, 52)
        col53 = _safe_cell(row, 53)

        if not any((col49, col50, col51, col52, col53)):
            return

        if not any((col49, col50, col51, col52)) and _looks_like_date(col53):
            _append_issue(issue_counts, issue_rows, "other_orphan_trailing_date", source_row_no)
            return

        shifted_left = _looks_like_date(col49) and not _looks_like_date(col50) and any((col50, col51, col52, col53))

        if shifted_left:
            _append_issue(issue_counts, issue_rows, "other_shifted_left", source_row_no)
            test_date = col49
            test_group = col50
            payload_text = col51
            extra_result = col52 if col52 and not _looks_like_date(col52) else None
            if col53 and not extra_result:
                extra_result = col53
        else:
            test_date = col50
            test_group = col51
            payload_text = col52
            extra_result = col53

        if _looks_like_identifier(test_group):
            _append_issue(issue_counts, issue_rows, "other_numeric_test_group", source_row_no)
            test_group = None

        append_section_rows(
            other_rows,
            section_label="other",
            source_row_no=source_row_no,
            test_date=test_date,
            test_group=test_group,
            payload_text=payload_text,
            extra_result=extra_result,
            extra_item_name="conclusion",
        )

    for row_index in range(9, len(df)):
        row = df.iloc[row_index]
        source_row_no = row_index + 1
        seq_text = _safe_cell(row, 1)

        if seq_text:
            try:
                patient_seq = int(float(seq_text))
            except ValueError:
                patient_seq = len(base_rows) + 1

            inpatient_no = _safe_cell(row, 6)
            lab_no = _safe_cell(row, 7)
            sample_no = _safe_cell(row, 8)
            age_raw = _safe_cell(row, 12)
            age_value = _parse_age_years(age_raw)
            if age_raw and age_value is None:
                age_parse_failures += 1

            patient_index = len(base_rows) + 1

            keys = _make_patient_keys(
                prefix=prefix,
                patient_index=patient_index,
                inpatient_no=inpatient_no,
                lab_no=lab_no,
                sample_no=sample_no,
            )

            current_patient = {
                "patient_seq": patient_seq,
                "patient_id": keys["patient_id"],
                "encounter_id": keys["encounter_id"],
                "sample_id": keys["sample_id"],
                "subsidy_paid": _safe_cell(row, 4),
                "screening_no": _safe_cell(row, 5),
                "inpatient_no": inpatient_no,
                "lab_no": lab_no,
                "sample_no": sample_no,
                "sex": _safe_cell(row, 11),
                "age": age_value,
                "sample_collect_time": _safe_cell(row, 16),
                "sample_receive_time": _safe_cell(row, 17),
                "sample_accept_time": _safe_cell(row, 18),
                "sample_audit_time": _safe_cell(row, 19),
                "admission_date": _safe_cell(row, 20),
                "admission_diagnosis": _safe_cell(row, 21),
                "pre_sampling_diagnosis": _safe_cell(row, 22),
                "post_sampling_diagnosis": _safe_cell(row, 23),
                "discharge_date": _safe_cell(row, 24),
                "discharge_diagnosis": _safe_cell(row, 25),
                "first_symptom_text": _safe_cell(row, 26),
                "sample_day_symptom_text": _safe_cell(row, 27),
                "sample_day_sign_text": _safe_cell(row, 28),
                "pre_sampling_medication": _safe_cell(row, 29),
                "post_result_medication": _safe_cell(row, 30),
                "treatment_effect_text": _safe_cell(row, 31),
                "remark": _safe_cell(row, 57),
                "work_hours": _safe_cell(row, 59),
                "source_file": source_file.name,
                "source_row_no": source_row_no,
            }
            base_rows.append(current_patient.copy())

        if current_patient is None:
            continue

        parse_standard_section(
            csf_rtn_rows,
            section_label="csf_routine",
            row=row,
            source_row_no=source_row_no,
            test_date_col=32,
            payload_col=33,
        )
        parse_standard_section(
            bio_rows,
            section_label="biochemistry",
            row=row,
            source_row_no=source_row_no,
            test_date_col=34,
            payload_col=35,
        )
        parse_standard_section(
            csf_cul_rows,
            section_label="csf_culture",
            row=row,
            source_row_no=source_row_no,
            test_date_col=36,
            payload_col=38,
            test_group_col=37,
        )
        parse_standard_section(
            smear_rows,
            section_label="smear",
            row=row,
            source_row_no=source_row_no,
            test_date_col=39,
            payload_col=41,
            test_group_col=40,
        )
        parse_standard_section(
            imm_rows,
            section_label="host_immune",
            row=row,
            source_row_no=source_row_no,
            test_date_col=42,
            payload_col=43,
        )
        parse_micro_section(row, source_row_no)
        parse_other_section(row, source_row_no)
        parse_standard_section(
            img_rows,
            section_label="imaging",
            row=row,
            source_row_no=source_row_no,
            test_date_col=54,
            payload_col=56,
            test_group_col=55,
        )

    tables = {
        f"{prefix}_pt": pd.DataFrame(base_rows),
        f"{prefix}_csf_rtn": pd.DataFrame(csf_rtn_rows),
        f"{prefix}_bio": pd.DataFrame(bio_rows),
        f"{prefix}_csf_cul": pd.DataFrame(csf_cul_rows),
        f"{prefix}_smear": pd.DataFrame(smear_rows),
        f"{prefix}_imm": pd.DataFrame(imm_rows),
        f"{prefix}_micro": pd.DataFrame(micro_rows),
        f"{prefix}_other": pd.DataFrame(other_rows),
        f"{prefix}_img": pd.DataFrame(img_rows),
    }

    notes = [
        "Detected Anhui Shengli clinical workbook template.",
        "Patient base table keeps age as numeric and removes patient/recorder names.",
        "Detail tables now link by patient_id only; repeated patient columns are removed.",
        "Slash-only values are normalized to null before loading.",
        f"{prefix}_pt rows: {len(base_rows)}.",
        f"{prefix}_csf_rtn rows: {len(csf_rtn_rows)}.",
        f"{prefix}_bio rows: {len(bio_rows)}.",
        f"{prefix}_csf_cul rows: {len(csf_cul_rows)}.",
        f"{prefix}_smear rows: {len(smear_rows)}.",
        f"{prefix}_imm rows: {len(imm_rows)}.",
        f"{prefix}_micro rows: {len(micro_rows)}.",
        f"{prefix}_other rows: {len(other_rows)}.",
        f"{prefix}_img rows: {len(img_rows)}.",
    ]

    if age_parse_failures:
        notes.append(f"Quality warning: {age_parse_failures} age values could not be converted to numeric.")

    issue_labels = {
        "micro_orphan_shifted_date": "micro rows with only a shifted date were ignored",
        "micro_shifted_left": "micro rows were auto realigned from a left-shifted layout",
        "micro_shifted_other_date": "micro rows carried a shifted date that likely belongs to the other-method section",
        "micro_numeric_test_group": "micro rows had a numeric test_group and were reset from nearby text",
        "micro_invalid_test_date": "micro rows had a non-date test_date",
        "csf_culture_numeric_test_group": "csf-culture rows had a numeric test_group and were reset from payload text",
        "other_shifted_left": "other-method rows were auto realigned from a left-shifted layout",
        "other_numeric_test_group": "other-method rows had a numeric test_group and were reset to the default label",
        "other_orphan_trailing_date": "other-method rows with only a trailing date were ignored",
        "other_invalid_test_date": "other-method rows had a non-date test_date",
        "other_date_like_extra_result": "other-method rows still contain a date-like extra_result for review",
    }
    for issue_key, label in issue_labels.items():
        count = issue_counts.get(issue_key, 0)
        if not count:
            continue
        rows = ", ".join(str(row_no) for row_no in issue_rows.get(issue_key, []))
        notes.append(f"Quality check: {count} {label}. Sample source rows: {rows}.")

    return tables, notes
