from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg2
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


def create_database_if_needed(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    admin_database: str,
) -> None:
    conn = psycopg2.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        dbname=admin_database,
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
    if cur.fetchone() is None:
        cur.execute(f'CREATE DATABASE "{database}"')
    cur.close()
    conn.close()


def get_engine(
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
) -> Engine:
    return create_engine(
        f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{database}"
    )


def sanitize_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value or value == "/" or value.lower() in {"nan", "none", "null"}:
            return None
    return value


def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    return df.apply(lambda col: col.map(sanitize_value))


def read_table_file(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin1"):
            try:
                return normalize_df(pd.read_csv(path, encoding=encoding))
            except Exception:
                continue
        raise ValueError(f"Unable to read CSV: {path}")
    if suffix == ".xlsx":
        return normalize_df(pd.read_excel(path))
    if suffix == ".xls":
        try:
            return normalize_df(pd.read_excel(path, engine="xlrd"))
        except Exception:
            for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin1"):
                try:
                    return normalize_df(pd.read_csv(path, sep="\t", encoding=encoding))
                except Exception:
                    continue
        raise ValueError(f"Unable to read XLS/TXT: {path}")
    raise ValueError(f"Unsupported file type: {path}")


def read_workbook_sheets(path: Path):
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        with pd.ExcelFile(path) as workbook:
            for sheet_name in workbook.sheet_names:
                yield sheet_name, normalize_df(pd.read_excel(path, sheet_name=sheet_name))
        return
    if suffix == ".xls":
        try:
            with pd.ExcelFile(path, engine="xlrd") as workbook:
                for sheet_name in workbook.sheet_names:
                    yield sheet_name, normalize_df(
                        pd.read_excel(path, sheet_name=sheet_name, engine="xlrd")
                    )
            return
        except Exception:
            pass
    yield "Sheet1", read_table_file(path)


def find_column(columns: list[Any], aliases: tuple[str, ...]) -> Any | None:
    for alias in aliases:
        alias_lower = alias.lower()
        for column in columns:
            if alias_lower in str(column).strip().lower():
                return column
    return None


def rename_by_aliases(
    df: pd.DataFrame,
    alias_map: dict[str, tuple[str, ...]],
) -> pd.DataFrame:
    rename_map: dict[Any, str] = {}
    columns = list(df.columns)
    for target, aliases in alias_map.items():
        match = find_column(columns, aliases)
        if match is not None:
            rename_map[match] = target
    return df.rename(columns=rename_map)


def drop_sensitive_columns(df: pd.DataFrame) -> pd.DataFrame:
    keep = []
    for column in df.columns:
        column_text = str(column)
        if any(token in column_text.lower() for token in ("patient_name", "doctor")):
            continue
        if any(token in column_text for token in ("姓名", "患者姓名", "医生", "医师")):
            continue
        keep.append(column)
    return df[keep].copy()


def sanitize_identifier(value: str, fallback: str = "table") -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z_]+", "_", value.strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = fallback
    if cleaned[0].isdigit():
        cleaned = f"{fallback}_{cleaned}"
    return cleaned[:63]


def ensure_unique_table_name(name: str, used: set[str]) -> str:
    candidate = name[:63]
    if candidate not in used:
        used.add(candidate)
        return candidate

    base = candidate[:58] if len(candidate) > 58 else candidate
    index = 2
    while True:
        suffix = f"_{index}"
        candidate = f"{base[:63 - len(suffix)]}{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
        index += 1


SUMMARY_ALIAS_MAP: dict[str, tuple[str, ...]] = {
    "sex": ("性别", "sex"),
    "age": ("年龄", "age"),
    "clinical_diagnosis": ("临床诊断", "诊断", "clinical"),
    "test_date": ("日期", "检测日期", "test_date"),
    "ngs_no": ("NGS编号", "编号", "ngs_no"),
    "ngs_sample_code": ("NGS样本", "样本编码", "sample_code"),
    "sample_note": ("备注", "样本备注", "sample_note"),
    "ngs_result": ("NGS结果", "结果", "ngs_result"),
    "clinical_match_code": ("符合临床", "match", "clinical_match"),
    "suspicious_genus_1": ("可疑菌属1", "菌属1", "genus_1"),
    "suspicious_species_1": ("可疑菌种1", "菌种1", "species_1"),
    "genus_abundance_pct_1": ("丰度", "abundance"),
    "smrng_1": ("SMRNG", "smrng_1"),
    "smrn_1": ("SMRN", "smrn_1"),
    "suspicious_genus_2": ("可疑菌属2", "菌属2", "genus_2"),
    "suspicious_species_2": ("可疑菌种2", "菌种2", "species_2"),
    "submitting_department": ("送检科室", "科室", "department"),
    "inpatient_id": ("住院号", "病案号", "inpatient"),
}


PATIENT_LIST_ALIAS_MAP: dict[str, tuple[str, ...]] = {
    "sample_type": ("样本类型", "sample_type", "unnamed: 0"),
    "ngs_report_time": ("NGS报告时间", "report_time"),
    "sample_no": ("样本号", "sample_no"),
    "disease_label": ("label", "疾病标签"),
    "age": ("年龄", "age"),
    "first_admission_date": ("首次入院", "admission"),
    "white_blood_cell_text": ("白细胞", "wbc_text"),
    "white_blood_cell": ("白细胞（无单位）", "white_blood_cell"),
    "neutrophil_count_text": ("中性粒细胞计数", "neutrophil_text"),
    "neutrophil_count": ("中性粒细胞计数（无单位）", "neutrophil_count"),
    "esr_text": ("血沉", "esr_text"),
    "esr": ("血沉（无单位）", "esr"),
    "crp_text": ("C-反应蛋白", "crp_text"),
    "crp": ("C-反应蛋白（无单位）", "crp"),
    "pct_text": ("降钙素原", "pct_text"),
    "pct": ("降钙素原（无单位）", "pct"),
    "cryptococcus_result": ("隐球菌检测结果", "cryptococcus"),
    "tspot_result": ("T-SPOT", "tspot"),
    "cea_text": ("癌胚抗原", "cea"),
    "first_symptom_date": ("首次出现临床症状时间", "first_symptom"),
}


ANNOTATION_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "sample_in_row": ("#sample", "sample"),
    "latin_name": ("latin",),
    "chinese_name": ("chinese", "中文"),
    "genus_latin": ("genuslatin", "genus_latin"),
    "genus_chinese": ("genuschi", "genus_chinese"),
    "gram_type": ("gram",),
    "latin_relative_abundance": ("latin_re_abu", "relative_abundance"),
    "latin_absolute_abundance": ("latin_abs_abu", "absolute_abundance"),
    "latin_mrn": ("latin_mrn",),
    "latin_smrn": ("latin_smrn",),
    "genus_mrn": ("g_mrn", "genus_mrn"),
    "genus_smrn": ("g_smrn", "genus_smrn"),
    "coverage": ("coverage",),
    "coverage_rate": ("covrate", "coverage_rate"),
    "depth": ("depth",),
    "reference_id": ("refid", "reference_id"),
    "latin_id": ("latinid", "latin_id"),
}


def extract_fields_from_row(row: dict[str, Any]) -> dict[str, Any]:
    extracted: dict[str, Any] = {}
    for target, aliases in ANNOTATION_FIELD_ALIASES.items():
        match = find_column(list(row.keys()), aliases)
        extracted[target] = row.get(match) if match is not None else None
    return extracted


def pick_sheet_name(path: Path, preferred_name: str | None, fallback_index: int = 0) -> str:
    with pd.ExcelFile(path) as workbook:
        if preferred_name and preferred_name in workbook.sheet_names:
            return preferred_name
        if fallback_index < len(workbook.sheet_names):
            return workbook.sheet_names[fallback_index]
    raise ValueError(f"No worksheet available in {path}")


def build_cached_summary_tables(source_file: Path) -> dict[str, pd.DataFrame]:
    summary_sheet = pick_sheet_name(source_file, "汇总表", 0)
    summary_df = normalize_df(pd.read_excel(source_file, sheet_name=summary_sheet))
    summary_df.insert(0, "source_row_no", range(2, len(summary_df) + 2))
    summary_df = rename_by_aliases(summary_df, SUMMARY_ALIAS_MAP)
    summary_df = drop_sensitive_columns(summary_df)
    summary_df["source_file"] = source_file.name

    failed_sheet = pick_sheet_name(source_file, "失败", min(1, len(pd.ExcelFile(source_file).sheet_names) - 1))
    failed_df = normalize_df(pd.read_excel(source_file, sheet_name=failed_sheet, header=None))
    failed_df.insert(0, "source_row_no", range(1, len(failed_df) + 1))
    failed_columns = [
        "patient_name",
        "sample_type",
        "excel_serial_date",
        "status_text",
        "reserved_1",
        "failure_reason",
        "doctor_name",
        "submitting_department",
        "inpatient_id",
        "sex",
        "age",
        "reserved_code",
        "clinical_diagnosis",
        "reserved_2",
        "reserved_3",
        "reserved_4",
        "ngs_failure_result",
        "reserved_5",
        "reserved_6",
        "reserved_7",
        "reserved_8",
        "clinical_comment",
    ]
    padded_columns = ["source_row_no"] + failed_columns[: max(0, failed_df.shape[1] - 1)]
    failed_df.columns = padded_columns
    failed_df = failed_df.drop(columns=[c for c in ("patient_name", "doctor_name") if c in failed_df.columns])
    failed_df["source_file"] = source_file.name

    myco_sheet = "Sheet3"
    workbook = pd.ExcelFile(source_file)
    if myco_sheet not in workbook.sheet_names:
        myco_sheet = workbook.sheet_names[min(2, len(workbook.sheet_names) - 1)]
    myco_df = normalize_df(pd.read_excel(source_file, sheet_name=myco_sheet))
    myco_df.insert(0, "source_row_no", range(2, len(myco_df) + 2))
    myco_df = rename_by_aliases(
        myco_df,
        {
            "ngs_no": ("NGS编号", "编号", "ngs_no"),
            "clinical_diagnosis": ("临床诊断", "诊断", "clinical"),
            "mycobacterial_culture_result": ("分枝杆菌培养", "培养结果", "culture_result"),
        },
    )
    myco_df = drop_sensitive_columns(myco_df)
    myco_df["source_file"] = source_file.name

    return {
        "fdzs_mngs_summary": summary_df,
        "fdzs_mngs_failed": failed_df,
        "fdzs_mycobacterial_culture": myco_df,
    }


def write_tables(engine: Engine, tables: dict[str, pd.DataFrame]) -> None:
    with engine.begin() as conn:
        for table_name in tables:
            conn.execute(text(f"DROP TABLE IF EXISTS {table_name} CASCADE"))
    for table_name, df in tables.items():
        df.to_sql(
            table_name,
            engine,
            index=False,
            if_exists="replace",
            chunksize=1000,
            method="multi",
        )


def collect_counts(engine: Engine, table_names: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    with engine.begin() as conn:
        for table_name in table_names:
            counts[table_name] = int(
                conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar() or 0
            )
    return counts


def find_first_wave_list(root: Path) -> Path | None:
    for path in root.rglob("*.xlsx"):
        if any(token in path.name for token in ("病人列表", "患者列表", "名单")):
            return path
    return None


def find_summary_root(root: Path) -> Path | None:
    for path in root.rglob("*"):
        if path.is_dir() and any(token in path.name for token in ("excel汇总表", "汇总表")):
            return path
    return None


def build_first_wave_patient_list(root: Path) -> tuple[pd.DataFrame, Path | None]:
    path = find_first_wave_list(root)
    if path is None:
        return pd.DataFrame(), None

    rows = []
    with pd.ExcelFile(path) as workbook:
        for sheet_name in workbook.sheet_names:
            df = normalize_df(pd.read_excel(path, sheet_name=sheet_name))
            df = drop_sensitive_columns(df)
            df = rename_by_aliases(df, PATIENT_LIST_ALIAS_MAP)
            df.insert(0, "sheet_name", sheet_name)
            df.insert(0, "source_row_no", range(2, len(df) + 2))
            df["source_file"] = path.name
            rows.append(df)

    return pd.concat(rows, ignore_index=True), path


def build_summary_rows(root: Path) -> tuple[pd.DataFrame, Path | None, list[Path]]:
    summary_root = find_summary_root(root)
    if summary_root is None:
        return pd.DataFrame(), None, []

    rows = []
    files: list[Path] = []
    for path in summary_root.iterdir():
        if not path.is_file() or path.suffix.lower() not in {".xlsx", ".xls"}:
            continue
        files.append(path)
        for sheet_name, df in read_workbook_sheets(path):
            df = drop_sensitive_columns(df)
            for idx, row in df.iterrows():
                payload = {
                    str(key): sanitize_value(value)
                    for key, value in row.to_dict().items()
                    if sanitize_value(value) is not None
                }
                if not payload:
                    continue
                rows.append(
                    {
                        "source_relpath": str(path.relative_to(root)),
                        "source_file": path.name,
                        "sheet_name": sheet_name,
                        "source_row_no": int(idx) + 2,
                        "row_json": json.dumps(payload, ensure_ascii=False, default=str),
                    }
                )

    return pd.DataFrame(rows), summary_root, files


def detect_category(name: str) -> str | None:
    lowered = name.lower()
    for category in ("bac", "fungi", "parasite", "virus"):
        if f".{category}." in lowered:
            return category
    return None


def file_rank(name: str) -> int:
    lowered = name.lower()
    if "edited-" in lowered and "final.anno" in lowered:
        return 0
    if "final.anno" in lowered:
        return 1
    if "merge.anno" in lowered:
        return 2
    if ".anno" in lowered:
        return 3
    return 99


def is_candidate_annotation_file(path: Path) -> bool:
    lowered = path.name.lower()
    if path.suffix.lower() not in {".csv", ".xls", ".xlsx"}:
        return False
    if ".anno" not in lowered:
        return False
    if any(token in lowered for token in ("top10", "top20", "filter", "figure", "cross", "data.stat", ".info")):
        return False
    return detect_category(path.name) is not None


def sample_id_from_name(name: str) -> str:
    return name.split(".")[0]


def should_skip_sample_id(sample_id: str) -> bool:
    value = sample_id.upper()
    return value.startswith("N") or value.startswith("CONTROL")


def build_pathogen_annotations(root: Path) -> tuple[pd.DataFrame, int, int]:
    sample_roots = [path for path in root.iterdir() if path.is_dir()]
    selected: dict[tuple[str, str, str], tuple[tuple[int, int, str], Path]] = {}
    total_candidates = 0

    for base in sample_roots:
        for path in base.rglob("*"):
            if not path.is_file() or not is_candidate_annotation_file(path):
                continue
            total_candidates += 1
            sample_id = sample_id_from_name(path.name)
            if should_skip_sample_id(sample_id):
                continue
            category = detect_category(path.name)
            if category is None:
                continue
            key = (str(path.parent.relative_to(root)), sample_id, category)
            rank_key = (file_rank(path.name), len(path.name), str(path))
            current = selected.get(key)
            if current is None or rank_key < current[0]:
                selected[key] = (rank_key, path)

    rows = []
    for (_, sample_id, category), (_, path) in selected.items():
        try:
            df = read_table_file(path)
        except Exception:
            continue
        for seq, (_, row) in enumerate(df.iterrows(), start=2):
            payload = {
                str(key): sanitize_value(value)
                for key, value in row.to_dict().items()
                if sanitize_value(value) is not None
            }
            if not payload:
                continue
            extracted = extract_fields_from_row(payload)
            rows.append(
                {
                    "source_relpath": str(path.relative_to(root)),
                    "source_file": path.name,
                    "sample_id": sample_id,
                    "category": category,
                    "file_role": file_rank(path.name),
                    "source_row_no": seq,
                    **extracted,
                    "row_json": json.dumps(payload, ensure_ascii=False, default=str),
                }
            )

    return pd.DataFrame(rows), total_candidates, len(selected)


def build_downloaded_tables(root: Path) -> tuple[dict[str, pd.DataFrame], list[str]]:
    patient_list_df, patient_list_path = build_first_wave_patient_list(root)
    summary_rows_df, summary_root, summary_files = build_summary_rows(root)
    annotations_df, total_candidates, selected_files = build_pathogen_annotations(root)

    notes = [
        f"Summary workbook count: {len(summary_files)}.",
        f"Annotation candidate files: {total_candidates}; selected files: {selected_files}.",
    ]
    if patient_list_path is not None:
        notes.append(f"First wave patient list source: {patient_list_path}.")
    if summary_root is not None:
        notes.append(f"Summary root: {summary_root}.")

    return (
        {
            "fdzs_first_wave_patient_list": patient_list_df,
            "fdzs_excel_summary_rows": summary_rows_df,
            "fdzs_pathogen_annotations": annotations_df,
        },
        notes,
    )


def build_workbook_tables(
    source_file: Path,
    *,
    table_prefix: str = "",
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    prefix = sanitize_identifier(table_prefix or source_file.stem, fallback="workbook")
    tables: dict[str, pd.DataFrame] = {}
    used_names: set[str] = set()
    notes: list[str] = []

    with pd.ExcelFile(source_file) as workbook:
        notes.append(f"Workbook sheet count: {len(workbook.sheet_names)}.")
        for sheet_name in workbook.sheet_names:
            df = normalize_df(pd.read_excel(source_file, sheet_name=sheet_name))
            df.insert(0, "source_row_no", range(2, len(df) + 2))
            df.insert(0, "sheet_name", sheet_name)
            df.insert(0, "source_file", source_file.name)

            sheet_part = sanitize_identifier(sheet_name, fallback="sheet")
            table_name = ensure_unique_table_name(f"{prefix}_{sheet_part}", used_names)
            tables[table_name] = df
            notes.append(f"{sheet_name} -> {table_name} ({len(df)} rows).")

    return tables, notes
