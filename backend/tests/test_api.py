from __future__ import annotations

import os

os.environ.setdefault("REDLENS_DATABASE_URL", "sqlite:///:memory:")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def test_seeded_data_and_mock_run() -> None:
    with TestClient(app) as client:
        targets = client.get("/api/targets")
        assert targets.status_code == 200
        assert targets.json()[0]["mode"] == "mock"

        categories = client.get("/api/threat-categories")
        assert categories.status_code == 200
        assert len(categories.json()) == 6

        evaluations = client.get("/api/evaluations")
        assert evaluations.status_code == 200
        assert len(evaluations.json()) == 12

        run_response = client.post("/api/runs", json={"target_id": targets.json()[0]["id"]})
        assert run_response.status_code == 201
        run = run_response.json()
        assert run["total_count"] == 12
        assert run["failed_count"] >= 1
        assert run["error_count"] == 0

        detail_response = client.get(f"/api/runs/{run['id']}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert len(detail["results"]) == 12
        assert any(result["status"] == "failed" for result in detail["results"])


def test_target_create_and_promote_finding() -> None:
    with TestClient(app) as client:
        create_response = client.post(
            "/api/targets",
            json={
                "name": "Local Live Sidecar",
                "mode": "live",
                "base_url": "http://127.0.0.1:8001",
                "internal_auth_env": "OPENEMR_INTERNAL_AUTH_SECRET",
                "bearer_token_env": "OPENEMR_BEARER_TOKEN",
                "fhir_base_url": "http://127.0.0.1:8300/apis/default/fhir",
                "patient_uuid": "eval-current-patient",
            },
        )
        assert create_response.status_code == 201

        mock_target = client.get("/api/targets").json()[0]
        run = client.post("/api/runs", json={"target_id": mock_target["id"]}).json()
        detail = client.get(f"/api/runs/{run['id']}").json()
        failed = next(result for result in detail["results"] if result["status"] == "failed")

        finding_response = client.post(f"/api/results/{failed['id']}/promote")
        assert finding_response.status_code == 201
        finding = finding_response.json()
        assert finding["result_id"] == failed["id"]
        assert finding["status"] == "open"
        assert "judge_reason" in finding["reproduction_steps"]

