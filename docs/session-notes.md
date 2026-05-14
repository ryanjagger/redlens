
# Session notes — MVP implementation and first live target

**Date:** 2026-05-12 / 2026-05-13
**Goal:** Get the RedLens MVP from plan to working code, then connect the first live target to the local OpenEMR `oe-ai-agent` sidecar.

---

## What we built

- Scaffolded RedLens as a local-first FastAPI + React/Vite application with SQLite persistence.
- Added backend concepts for `Target`, `ThreatCategory`, `Evaluation`, `EvaluationRun`, `EvaluationResult`, and `Finding`.
- Seeded the initial MVP threat categories and 12 focused adversarial evaluations: two each for prompt injection, data exfiltration, state corruption, tool misuse, denial of service, and identity/role exploitation.
- Implemented a mock target adapter for deterministic local runs and a live OpenEMR sidecar adapter for `/v1/chat` and `/v1/documents/extract`.
- Implemented deterministic judging with forbidden substrings, regex checks, required safe-boundary markers, and response length limits.
- Built a React control panel with Dashboard, Targets, Threat Model, Evaluations, Runs, Run Detail, and Findings views.
- Added finding promotion from failed/error results.
- Added a living threat model document for the Clinical Co-Pilot attack surface.

## Important target decision

We decided the first live target should connect directly to `oe-ai-agent`, not through the OpenEMR web app.

Reason:

- RedLens MVP is validating the adversarial behavior of the AI sidecar endpoints first.
- Direct sidecar targeting gives cleaner signal for `/v1/chat` and `/v1/documents/extract`.
- Going through the OpenEMR app is still important later for end-to-end auth/session/module behavior, but it adds extra moving parts before the evaluation substrate is proven.

The first live target created was:

```txt
Name: OpenEMR Local OE-AI-Agent
Mode: live
Base URL: http://127.0.0.1:8400
Internal auth env: OPENEMR_INTERNAL_AUTH_SECRET
Bearer token env: OPENEMR_BEARER_TOKEN
FHIR base URL: http://openemr/apis/default/fhir
```

The sidecar health check was verified at:

```txt
http://127.0.0.1:8400/healthz
```

## Token helper

The OpenEMR internal auth secret in the dev-easy stack defaults to:

```txt
dev-internal-auth-secret
```

The OpenEMR bearer token is not static. The AI module mints a short-lived user-scoped OAuth token through `BearerTokenMinter`; the token lasts 5 minutes and inherits the OpenEMR user's FHIR permissions.

To make live RedLens runs possible, we added a RedLens dev helper that runs inside the OpenEMR Docker container:

```txt
scripts/mint_openemr_token.sh
scripts/mint_openemr_token.php
```

Usage:

```bash
cd /Users/ryan/gauntlet/redlens
eval "$(bash scripts/mint_openemr_token.sh --username admin --pid 1 --env)"
```

It exports:

```txt
OPENEMR_INTERNAL_AUTH_SECRET
OPENEMR_BEARER_TOKEN
OPENEMR_FHIR_BASE_URL
OPENEMR_PATIENT_UUID
```

OpenEMR debug logs go to stderr; the `eval "$( ... )"` path captures only clean exports from stdout.

## Live run flow

Current manual live-run flow:

1. Mint a fresh OpenEMR token with the helper.
2. Start or restart the RedLens backend in the same shell so it inherits the exported env vars.
3. Trigger a RedLens run against target `2`.
4. Inspect the run from the UI or `/api/runs/{id}`.

Example:

```bash
cd /Users/ryan/gauntlet/redlens
eval "$(bash scripts/mint_openemr_token.sh --username admin --pid 1 --env)"

cd backend
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Smoke run:

```bash
curl -s -X POST http://127.0.0.1:8000/api/runs \
  -H 'Content-Type: application/json' \
  -d '{"target_id":2,"evaluation_ids":[1]}'
```

Observed successful live smoke result:

```txt
Run #3
Target: OpenEMR Local OE-AI-Agent
Total: 1
Passed: 1
Failed: 0
Errors: 0
```

## UI/debug fixes made during live setup

- The first live run errored because the RedLens backend had been started before the OpenEMR token env vars were exported. Restarting the backend with a freshly minted token fixed it.
- Added a backend fallback so a live target with no stored `patient_uuid` can read `OPENEMR_PATIENT_UUID` from the backend environment.
- Changed the frontend API client to use same-origin `/api` calls and added a Vite proxy to `http://127.0.0.1:8000`.
- Added visible loading/error states to the Runs page so missing API data does not look like an empty dropdown.
- Patched Run Detail to validate route params and show real API errors instead of staying on `Loading run...`.

## Verification completed

- `uv run pytest` passed.
- `npm run build` passed.
- PHP syntax validation passed for the token helper inside the OpenEMR container.
- Vite `/api/targets` and `/api/runs` proxy paths returned backend data.
- Live sidecar run against `/v1/chat` completed successfully.

## Current rough edges

- Live tokens expire after 5 minutes and still require restarting the backend if the backend process does not already have fresh env vars.
- RedLens should grow an on-demand token provider instead of depending on exported env vars and backend restarts.
- Full live-suite runs may be expensive or noisy because the live sidecar can use real LLM calls.
- The direct sidecar target does not yet test the OpenEMR module's browser/session/token-minting route end to end.

---

# Session notes — multi-agent architecture design

**Date:** 2026-05-12 / 2026-05-13
**Goal:** Produce `docs/ARCHITECTURE.md` defining the RedLens multi-agent platform architecture, with agent roles, inter-agent communication, orchestration strategy, regression-harness story, observability, and known tradeoffs. No implementation — design only.
**Session** claude --resume f174b55e-85be-4139-98de-e8b6cab34627
---

## Decisions made (and why)

### Core architectural claim: two-loop model

- **Regression loop** — deterministic, cheap, runs in CI on every commit + nightly cron. Engine is the existing `EvaluationRunner` over the `evaluations` table.
- **Exploration loop** — agentic, expensive, on-demand or nightly cron with a conservative budget. Four agents (Orchestrator, Red Team, Judge, Documenter).
- **Promotion gate** — the bridge between the two. Confirmed exploits get distilled by the Documenter into a draft `evaluations` row (`enabled=false`) and a human reviewer flips `enabled=true`.

**Why two loops, not one.** Cheap-deterministic-regression and expensive-novel-exploration are different cost-structure problems. Collapsing into one agent-driven loop forces LLM costs into CI, which is untenable.

### Attack-generation model: B (agent-generated only)

- Considered three options: (A) static seeded floor + agent ceiling, (B) agent owns 100% of attack generation, (C) hybrid where agent can edit seeded rows.
- Chose **B with anchors**: the regression suite is populated *exclusively* by promoted findings, with ~5–10 hand-written "anchor" evals tied to threat-model P0s to make day-1 CI credible and let us measure agent recall ("did the agent re-derive what we know is exploitable?").
- Anchors are framed as "the agent's responsibility to re-derive," not as a hand-curated floor.

### Framework choices

- **LangGraph** for intra-agent state graphs and the Orchestrator's long-lived campaign loop, with the checkpointer for pause/resume across restarts.
- **OpenRouter** for all LLM access. Single billing surface, runtime model swaps, automatic provider failover. Accepted tradeoff: loss of some provider-native features (Anthropic batches API, etc.).
- **DB rows as the inter-agent message bus.** Not Redis, not in-memory queues. Survives restarts, replayable, matches the existing substrate's idiom. SQLite locally, Postgres in production.
- **Langfuse** for LLM-call tracing (consistent with `oe-ai-agent`'s observability stack).

### Approval gates (exactly two)

1. **Live-target campaign start** — operator confirms before agents hit live oe-ai-agent. Mock targets unattended.
2. **Promotion to regression harness** — humans always approve `evaluations` row enablement. The regression harness is ground truth and agents must not write to it autonomously.

No other gates. Every gate is a place where the platform stops being autonomous; we placed them where autonomy is genuinely a safety problem and nowhere else.

### Agent specifics

- **Orchestrator** — small/cheap model (Haiku). Priority-weighted sampling over threat-registry categories with a P0 floor share. Stopping: earliest of cost budget, wall-clock budget, attempts ceiling.
- **Red Team Agent** — strong creative model (Sonnet 4.6, fallback GPT-5 / Gemini 2.5 Pro). Capabilities: `target.chat`, `target.extract_document`, `threat_registry.lookup`, `transcript.append`. *No FHIR write access, no INTERNAL_AUTH_SECRET*. Memory: per-campaign in-context plus read-only access to prior findings in the same category.
- **Judge** — two-tier. Deterministic fast path handles clearly-safe / clearly-exploit. LLM tier (Sonnet 4.6, pinned snapshot for replayability, no fallback) handles `uncertain`. Severity proposed by Judge, overridable by Operator at promotion.
- **Documenter** — medium model (Sonnet 4.6). Produces both `docs/findings/F-NNN.md` (human-readable, per brief) *and* drafted regression-eval row. One agent owns both artifacts; splitting into Documenter + Curator is premature.

### Cost discipline

- Per-campaign caps: `max_cost_usd` ($5 live default, $0.50 mock), `max_wall_clock_seconds` (30 min default), `min_attempts` floor (5 default).
- Hard cutoff at 90% of either budget cap; in-flight attempts complete normally.
- Per-agent ceilings prevent any one agent type from starving the others.
- OpenRouter usage metadata is the source of truth for cost accounting.

### Threat registry split

- **`docs/THREAT_MODEL.md`** stays narrative, human-first, low-churn.
- **`backend/app/data/threat_registry.md`** is the agent-facing companion: dense, structured, growing. Read by the Orchestrator and Red Team Agent each campaign. The Documenter appends prior-finding entries on promotion approval.
- **Why outside `docs/`:** the user clarified that `docs/` is a working folder for them. The registry is a *system artifact* (runtime input, deployed with the FastAPI app), not a working doc. Picked `backend/app/data/` so it bundles with the deployed backend, loads via `Path(__file__).parent / "data" / "threat_registry.md"`, and leaves room for sibling agent artifacts (severity rubrics, prompt templates).
- A CI lint check is described (not implemented) that fails when threat-model categories drift from registry sections.

---

## Artifacts produced

| File | Status | Purpose |
| ---- | ------ | ------- |
| `docs/ARCHITECTURE.md` | New (~5,300 words; 525-word executive summary) | The multi-agent architecture design doc, deliverable for this task |
| `backend/app/data/threat_registry.md` | New (~1,900 words) | Agent-facing threat catalogue, seeded from THREAT_MODEL.md. Six categories + cross-cutting invariants + out-of-scope list, each with YAML metadata, concrete seed payloads, judge hints, and prior-findings placeholders |

ARCHITECTURE.md includes a Mermaid agent-interaction flowchart, an ASCII finding-lifecycle state machine, and tables for: agent roster, approval gates, AI vs deterministic tooling choices, and the state-and-coordination framework.

---

## Open items (not done this session)

- **THREAT_REGISTRY.md → DB lint.** Architecture mentions a CI check that pairs threat-model categories with registry sections. Not implemented.
- **Schema additions.** The doc describes new tables (`campaigns`, `attempts`, `verdicts`, `promoted_eval_drafts`) and additions to existing rows (`findings.linked_attempt_id`, `findings.linked_evaluation_id`, `evaluation_runs.campaign_id`). No Alembic migration written.
- **LangGraph scaffolding.** Architecture defines the four agents' state graphs at a node level but no code exists.
- **OpenRouter integration.** Not wired into the backend yet.
- **Langfuse integration.** Not wired in (oe-ai-agent uses it; redlens does not yet).
- **Anchor evaluations.** Decision was to ship with ~5–10 anchor evals tied to P0s. Need to write them as the first batch of `evaluations` rows.

---

## Decisions worth surfacing later

These were called out as "v2 / out of scope" in ARCHITECTURE.md but may need revisiting:

1. **Worker / web service split** on Railway, with a real queue. Triggered if a single campaign meaningfully pressures the FastAPI event loop.
2. **Bandit-style orchestrator** — only if priority-weighted sampling demonstrably underperforms.
3. **Full transcript memory** for the Red Team — only if we observe duplicated dead-ends across campaigns.
4. **Continuous-until-plateau campaign mode** — bootstrap coverage faster on a fresh install. Deferred for runaway-cost risk.
5. **Separate Curator agent** distinct from Documenter — only if distillation prompts diverge enough to justify the split.
6. **GitHub Issue / Slack disclosure adapters** for the Documenter, behind their own approval gate.

---

## Process notes

- User explicitly asked to be "grilled" before any drafting. Sent a 15-question list grouped into four tiers (design-defining, operational, agent-specifics, deliverable). User accepted defaults for ~12 and pushed back on two (LangGraph + OpenRouter choice; the static-vs-agent attack-generation question).
- The static-vs-agent question got a dedicated round of explanation (the "two loops + promotion" walk-through) before the user committed to option B.
- User overrode the initial `docs/THREAT_REGISTRY.md` location partway through, prompting the move to `backend/app/data/` and the rationale-paragraph addition in ARCHITECTURE.md.
