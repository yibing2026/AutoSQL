from pathlib import Path
import json
from io import BytesIO
import sys

from fastapi.testclient import TestClient
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.main import app


def main() -> None:
    payload_path = PROJECT_ROOT / "data" / "sample_import_request.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    upload_buffer = BytesIO()
    pd.DataFrame([{"name": "demo", "value": 1}]).to_excel(upload_buffer, index=False)
    upload_buffer.seek(0)

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200, health.text

        status = client.get("/api/v1/status")
        assert status.status_code == 200, status.text

        run_import = client.post("/api/v1/imports/run", json=payload)
        assert run_import.status_code == 200, run_import.text
        run_import_json = run_import.json()
        assert run_import_json["requested_mode"] == payload["mode"]
        assert run_import_json["history_id"] >= 1

        upload_import = client.post(
            "/api/v1/imports/upload",
            files={
                "file": (
                    "demo_upload.xlsx",
                    upload_buffer.getvalue(),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
            data={"dry_run": "true", "workbook_table_prefix": "demo_upload"},
        )
        assert upload_import.status_code == 200, upload_import.text
        upload_json = upload_import.json()
        assert upload_json["requested_mode"] == "workbook_file"
        assert upload_json["jobs"][0]["discovered"] is True

        history = client.get("/api/v1/imports/history")
        assert history.status_code == 200, history.text
        history_json = history.json()
        assert len(history_json) >= 1

    print("Smoke test passed.")
    print(f"Import status: {run_import_json['status']}")
    print(f"History entries: {len(history_json)}")


if __name__ == "__main__":
    main()
