# RedLens Exploration Loop Implementation Plan

**Status:** Draft for review before implementation.
**Date:** 2026-05-13
**Related docs:** `docs/ARCHITECTURE.md`, `docs/THREAT_MODEL.md`, `backend/app/data/threat_registry.md`

## Purpose

This plan turns the multi-agent architecture into an implementation sequence. The goal is to add the exploration loop without weakening the existing regression loop.

The regression loop remains the cheap, deterministic harness for known attacks. The exploration loop becomes the agentic discovery system that finds new attacks, judges them, documents them, and drafts regression evaluations for human approval.

The implementation should keep the app working at the end of every phase.

## Settled Decisions

- Use Postgres for normal local development and production deployment. Keep SQLite only for fast tests and temporary fallback.
- Use database rows as the durable handoff between workers and agents.
- Do not use a shared filesystem or Railway volume as the cross-worker coordination mechanism.
- Store normal JSON evidence in Postgres at first. Add artifact references now so larger payloads can move to bucket/object storage later.
- Add LangGraph state graphs early, but start with deterministic nodes where practical.
- Support both mock and live targets from the start. Mock campaigns start without approval; live campaigns require explicit operator approval.
- Enforce mutual exclusion for live targets: only one running live campaign may target a given live target at a time.
- Do not require real LLM calls in the first implementation slice. Add LLM nodes incrementally, starting with Red Team attack-plan generation.
- Use one normal promotion approval gate: approving a draft creates a real `evaluations` row with `enabled=true`.
- Also offer a secondary "save disabled" path for cases where the draft is useful but should not enter the regression harness yet.
- Build an operational Campaigns UI, not just a minimal CRUD screen.
- Use explicit product language in the UI: **Exploration Campaigns** for the agentic discovery loop and **Regression Runs** for deterministic evaluation sweeps. Regression Runs should not list campaigns.

## Target Lifecycle

The intended exploration lifecycle is:

```text
campaign created
  -> live campaign waits for approval, mock campaign can run immediately
  -> Orchestrator selects focus area and creates attempt work
  -> Red Team executes attempt against target
  -> Judge writes verdict
  -> Documenter creates finding + report + promoted eval draft for exploits
  -> Operator approves draft
  -> enabled evaluation joins regression loop
```

The regression lifecycle remains:

```text
enabled evaluations
  -> EvaluationRunner executes against target
  -> deterministic judge writes evaluation results
  -> known vulnerabilities stay fixed, reopen, or regress
```

## Phase 0: Local Postgres Parity

Purpose: make local development match production before adding worker-oriented schema and behavior.

### Tasks

- Add `psycopg` or equivalent Postgres driver to backend dependencies.
- Add local `compose.yaml` for a RedLens Postgres service.
- Update `.env.example` to make Postgres the default local app database.
- Verify `alembic upgrade head` against local Postgres.
- Keep existing fast API/unit tests on SQLite for now.
- Document the local Postgres startup flow in `README.md`.

### Acceptance Criteria

- A developer can start Postgres locally with one command.
- The backend can run against local Postgres using `REDLENS_DATABASE_URL`.
- Existing migrations apply cleanly to Postgres.
- Existing backend tests still pass.

## Phase 1: Exploration Schema

Purpose: add the durable state model before implementing agent behavior.

### New Tables

`campaigns`

- Parent record for one exploration run.
- Tracks target, status, budget, focus, cost, totals, approval state, timing, and summary.
- Stores a campaign-time target snapshot, including target mode, so audit history remains stable even if the target configuration changes later.
- Enforces one running live campaign per target. Mock targets may run concurrently.

`attempts`

- One adversarial attack attempt inside a campaign.
- Stores focus area, vector, status, attack plan, transcript/evidence, timing, cost metadata, and errors.

`verdicts`

- Judge output for an attempt.
- Stores deterministic or LLM tier, verdict, severity, confidence, rationale, model metadata, and raw judge output.

`promoted_eval_drafts`

- Documenter's proposed bridge into the regression harness.
- Stores candidate evaluation JSON, review status, report link, accepted evaluation link, and review notes.

`artifacts`

- Metadata for larger payloads and generated files.
- Stores owner, kind, storage backend, URI/path, hash, MIME type, size, and redaction status.

### Existing Table Extensions

`findings`

- Add optional `linked_attempt_id`.
- Add optional `linked_evaluation_id`.
- Add optional `report_path`.

`evaluation_runs`

- Add nullable `campaign_id` so regression runs can be linked back to the campaign or draft that triggered them.

`evaluations`

- Consider optional `linked_finding_id` if it simplifies approval traceability. This can also be represented through `promoted_eval_drafts.accepted_evaluation_id`.

### Initial State Values

Campaign statuses:

```text
draft
needs_live_approval
running
completed
budget_exhausted
cancelled
failed
```

Live-target mutual exclusion:

```text
for target_mode = live:
  at most one campaign for the same target_id may be running

for target_mode = mock:
  concurrent campaigns are allowed
```

Implementation preference:

- Store `target_mode_snapshot` on `campaigns`.
- Add a Postgres partial unique index for running live campaigns, for example:

```sql
CREATE UNIQUE INDEX uq_campaigns_one_running_live_per_target
ON campaigns (target_id)
WHERE target_mode_snapshot = 'live' AND status = 'running';
```

- Also check this condition in application code before live approval/start so the API can return a clear operator-facing error.

Attempt statuses:

```text
pending
claimed
executing
completed
judged
documented
error
```

Verdicts:

```text
safe
exploit
uncertain
```

Draft statuses:

```text
pending
accepted
rejected
needs_revision
saved_disabled
```

### Acceptance Criteria

- Alembic migration creates all new schema cleanly on Postgres.
- Existing SQLite-based tests still run or are adjusted only where necessary.
- API/schema tests can create a campaign, attempt, verdict, draft, and linked finding.
- No agent implementation is required in this phase.

## Phase 2: LangGraph Skeleton

Purpose: introduce the real graph shape early while keeping node behavior deterministic enough to test.

### Graph Structure

Use one campaign-level Orchestrator graph and role-specific subgraphs:

```text
Orchestrator graph
  load_campaign
  check_approval
  check_budget
  select_focus
  dispatch_red_team
  run_judge
  run_documenter_if_needed
  decide_continue
  finalize_campaign

Red Team subgraph
  read_focus_context
  generate_attack_plan
  execute_target
  persist_attempt

Judge subgraph
  deterministic_fast_path
  persist_verdict

Documenter subgraph
  create_finding
  draft_evaluation_json
  create_report_artifact
  persist_draft
```

### First Behavior

- Orchestrator selects focus areas from the threat registry and campaign hint.
- Red Team uses deterministic seeded vectors first.
- Judge uses existing deterministic judge-style checks first.
- Documenter uses a template-based report and draft evaluation first.
- Budget enforcement includes max attempts and wall-clock time from day one.
- Cost fields exist but can be zero or estimated until OpenRouter usage is wired.

### Acceptance Criteria

- A mock campaign can run through LangGraph from creation to completion.
- Attempts, verdicts, findings, drafts, and artifacts are persisted through the DB lifecycle.
- The graph can stop because of max attempts or wall-clock budget.
- No OpenRouter key is required.

## Phase 3: Campaign APIs

Purpose: expose the exploration lifecycle to the frontend and tests.

### Endpoints

- `POST /api/campaigns` creates a campaign.
- `GET /api/campaigns` lists campaigns.
- `GET /api/campaigns/{id}` returns campaign detail with attempts, verdicts, drafts, and artifacts.
- `POST /api/campaigns/{id}/approve-live` approves and starts a live campaign.
- `POST /api/campaigns/{id}/start` starts a mock or already-approved campaign.
- `POST /api/campaigns/{id}/cancel` cancels a running or pending campaign.
- `POST /api/promoted-eval-drafts/{id}/approve` creates an enabled evaluation by default.
- `POST /api/promoted-eval-drafts/{id}/save-disabled` creates a disabled evaluation.
- `POST /api/promoted-eval-drafts/{id}/reject` rejects a draft.
- `POST /api/promoted-eval-drafts/{id}/needs-revision` marks a draft for revision.

### Acceptance Criteria

- API tests cover mock campaign creation and execution.
- API tests cover live campaign requiring approval before execution.
- API tests cover live-target mutual exclusion: a second campaign for the same live target cannot start while one is running.
- API tests cover draft approval creating an `evaluations` row.
- Approved-and-enabled drafts appear in the existing evaluations list and future regression runs.

## Phase 4: Operational Campaign UI

Purpose: build the operator console for campaigns, agent activity, evidence, budgets, and promotion review.

### Campaign List

UI label: **Exploration Campaigns**.

Show:

- Campaign ID.
- Target and target mode.
- Status.
- Focus hint.
- Attempt count.
- Exploit count.
- Cost/spend fields.
- Started/finished times.
- Last activity.

Actions:

- New campaign.
- Approve live campaign when applicable.
- Cancel campaign.
- Open campaign detail.

### New Campaign Form

Inputs:

- Target.
- Focus hint.
- Max attempts.
- Max wall-clock minutes.
- Max cost USD.
- LLM mode: deterministic or LLM-assisted.

Behavior:

- Mock target campaigns can start immediately.
- Live target campaigns enter `needs_live_approval`.

### Campaign Detail

Include:

- Status strip: target, mode, status, elapsed time, attempts, exploits, budget used.
- Live approval panel for live campaigns.
- Agent timeline showing Orchestrator, Red Team, Judge, and Documenter events.
- Attempts table with focus, vector, status, verdict, severity, latency, and cost.
- Evidence drawer with attack plan, request JSON, response JSON, transcript, and judge rationale.
- Verdict section showing deterministic vs LLM tier, confidence, rationale, and model snapshot if present.
- Draft promotion queue with report preview, proposed evaluation JSON, approve/enable, save disabled, reject, and needs revision.
- Budget/cost panel with max cost, estimated/spent cost, tokens, cost by agent role, attempts remaining, and stop reason.

### Acceptance Criteria

- Operator can create and inspect campaigns without using raw API calls.
- Operator can approve a live campaign from the UI.
- Operator sees a clear blocked state when another live campaign is already running against the selected target.
- Operator can inspect attempt evidence and judge rationale.
- Operator can approve a draft into the regression harness from the UI.

## Phase 5: Real LLM Nodes

Purpose: add model-powered intelligence incrementally without changing the DB lifecycle.

### Order

1. Add OpenRouter client/config and cost metadata capture.
2. Replace Red Team `generate_attack_plan` with an LLM-assisted node.
3. Add LLM Judge tier for uncertain attempts.
4. Add LLM Documenter report generation.
5. Optionally add Orchestrator summary/routing LLM assistance.

### Acceptance Criteria

- The app works without OpenRouter credentials in deterministic mode.
- LLM-assisted mode requires explicit configuration and displays model/cost metadata.
- Red Team LLM output is normalized before hitting the target.
- Judge LLM rows record model, prompt version, temperature, and raw output.
- Cost accounting is visible in the Campaign UI.

## Phase 6: Reports And Artifact Storage

Purpose: make findings reproducible and prepare production artifact handling.

### Tasks

- Generate local `docs/findings/F-NNN.md` reports for confirmed exploits.
- Store report metadata in `artifacts`.
- Add storage abstraction for local filesystem and S3-compatible bucket storage.
- Keep Postgres as the source of truth for artifact metadata and small JSON evidence.
- Move large transcripts, generated PDFs, screenshots, and raw LLM payloads to bucket storage when needed.

### Acceptance Criteria

- Each exploit finding has a report artifact.
- Report metadata links back to campaign, attempt, verdict, and draft.
- Local development can use filesystem artifacts.
- Production can be configured for bucket/object storage without changing agent handoff logic.

## Phase 7: Regression Feedback Integration

Purpose: connect promoted exploration findings back into the existing deterministic regression loop.

UI label: **Regression Runs**.

### Tasks

- Link accepted drafts to inserted evaluations.
- Link future evaluation results back to the originating finding where possible.
- Update finding status based on regression results:
  - `open` when exploit still reproduces.
  - `fix_validated` when the regression eval passes after remediation.
  - `regression_confirmed` when a previously passing promoted eval fails again.
- Add campaign or draft references to run details when relevant.

### Acceptance Criteria

- A promoted draft appears in later regression runs.
- Run details show the origin of promoted evaluations.
- Finding status can be updated from regression results.

## Suggested Implementation Order

1. Phase 0: Postgres parity.
2. Phase 1: schema and migrations.
3. Phase 2: LangGraph skeleton using deterministic nodes.
4. Phase 3: campaign APIs backed by the graph/service layer.
5. Phase 4: operational Campaign UI.
6. Phase 5: real LLM nodes.
7. Phase 6: report/artifact storage.
8. Phase 7: regression feedback integration.

Phase 2 and Phase 3 can overlap, but the API should stay thin and call the graph/service layer rather than embedding agent behavior directly in route handlers.

## Risks And Mitigations

- **Schema drift between SQLite tests and Postgres app runtime.**
  - Keep migrations Postgres-verified and add targeted Postgres integration tests for worker-claiming behavior.

- **Agent behavior becomes hard to replay.**
  - Keep all inter-agent handoffs in DB rows and store model/prompt metadata on LLM-produced rows.

- **Campaigns pressure the web process.**
  - Keep concurrency bounded in v1 and make worker split a later deployment change, not a schema change.

- **Promotion accidentally weakens regression quality.**
  - Keep human approval as the only normal path into enabled regression evaluations.

- **Artifacts grow too large for JSON columns.**
  - Use the `artifacts` table from the beginning and move large payloads to bucket storage when size warrants it.

- **Live target campaigns become costly or risky during development.**
  - Keep mock as default, require live approval, enforce one running live campaign per target, and enforce max attempts/wall-clock budget before adding LLM cost enforcement.

## Open Review Questions

Before implementation, confirm:

- Should Phase 1 include only the `artifacts` metadata table, or should it also include the first local filesystem storage adapter?
- Should `saved_disabled` be a draft status, or should saving disabled still count as `accepted` with an inserted disabled evaluation?
- Should the first campaign API run the LangGraph synchronously for MVP simplicity, or enqueue background execution immediately?
- Should local Postgres be required for all developers, or documented as the default while SQLite remains supported for quick starts?
