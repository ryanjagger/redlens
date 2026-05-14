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
                "base_url": "http://127.0.0.1:8400",
                "user_uuid": "00000000-0000-0000-0000-000000000001",
                "patient_uuid": "eval-current-patient",
                "fhir_base_url": "http://openemr/apis/default/fhir",
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


def test_campaign_lifecycle_and_live_mutual_exclusion() -> None:
    with TestClient(app) as client:
        mock_target = client.get("/api/targets").json()[0]

        campaign_response = client.post(
            "/api/campaigns",
            json={
                "target_id": mock_target["id"],
                "focus_hint": "prompt_injection_direct",
                "max_attempts": 2,
                "max_wall_clock_seconds": 60,
                "max_cost_usd": 0.25,
                "llm_mode": "deterministic",
            },
        )
        assert campaign_response.status_code == 201
        campaign = campaign_response.json()
        assert campaign["status"] == "draft"
        assert campaign["target_mode_snapshot"] == "mock"
        assert campaign["target_name_snapshot"] == mock_target["name"]

        start_response = client.post(f"/api/campaigns/{campaign['id']}/start")
        assert start_response.status_code == 200
        started = start_response.json()
        assert started["status"] == "completed"
        assert started["attempt_count"] == 2
        assert started["stop_reason"] == "max_attempts_reached"

        detail_response = client.get(f"/api/campaigns/{campaign['id']}")
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert len(detail["attempts"]) == 2
        assert all(attempt["verdicts"] for attempt in detail["attempts"])

        live_target_response = client.post(
            "/api/targets",
            json={
                "name": "Concurrency Live Sidecar",
                "mode": "live",
                "base_url": "http://127.0.0.1:8400",
                "user_uuid": "00000000-0000-0000-0000-000000000002",
                "patient_uuid": "eval-current-patient",
                "fhir_base_url": "http://openemr/apis/default/fhir",
            },
        )
        assert live_target_response.status_code == 201
        live_target = live_target_response.json()

        first_live = client.post("/api/campaigns", json={"target_id": live_target["id"]}).json()
        second_live = client.post("/api/campaigns", json={"target_id": live_target["id"]}).json()
        assert first_live["status"] == "needs_live_approval"
        assert second_live["status"] == "needs_live_approval"

        approve_first = client.post(f"/api/campaigns/{first_live['id']}/approve-live")
        assert approve_first.status_code == 200
        assert approve_first.json()["status"] == "running"
        assert approve_first.json()["live_approved_at"] is not None

        approve_second_blocked = client.post(f"/api/campaigns/{second_live['id']}/approve-live")
        assert approve_second_blocked.status_code == 409
        assert "another live campaign" in approve_second_blocked.json()["detail"]

        cancel_first = client.post(f"/api/campaigns/{first_live['id']}/cancel")
        assert cancel_first.status_code == 200
        assert cancel_first.json()["status"] == "cancelled"

        approve_second = client.post(f"/api/campaigns/{second_live['id']}/approve-live")
        assert approve_second.status_code == 200
        assert approve_second.json()["status"] == "running"


def test_promoted_eval_draft_approval_creates_enabled_evaluation() -> None:
    with TestClient(app) as client:
        mock_target = client.get("/api/targets").json()[0]
        campaign = client.post(
            "/api/campaigns",
            json={
                "target_id": mock_target["id"],
                "focus_hint": "state_context_poisoning",
                "max_attempts": 1,
            },
        ).json()

        start_response = client.post(f"/api/campaigns/{campaign['id']}/start")
        assert start_response.status_code == 200
        assert start_response.json()["exploit_count"] == 1

        detail = client.get(f"/api/campaigns/{campaign['id']}").json()
        draft = detail["attempts"][0]["promoted_eval_drafts"][0]
        assert draft["status"] == "pending"

        approval_response = client.post(
            f"/api/promoted-eval-drafts/{draft['id']}/approve",
            json={"review_notes": "approved in test"},
        )
        assert approval_response.status_code == 200
        approved = approval_response.json()
        assert approved["status"] == "accepted"
        assert approved["accepted_evaluation_id"] is not None
        assert approved["review_notes"] == "approved in test"

        drafts_response = client.get("/api/promoted-eval-drafts")
        assert drafts_response.status_code == 200
        drafts = drafts_response.json()
        assert any(row["id"] == approved["id"] and row["status"] == "accepted" for row in drafts)

        evaluations = client.get("/api/evaluations").json()
        promoted = next(
            evaluation for evaluation in evaluations if evaluation["id"] == approved["accepted_evaluation_id"]
        )
        assert promoted["enabled"] is True
        assert promoted["key"].startswith("promoted_state_context_poisoning")

        findings = client.get("/api/findings").json()
        linked = next(finding for finding in findings if finding["id"] == approved["finding_id"])
        assert linked["linked_evaluation_id"] == approved["accepted_evaluation_id"]
