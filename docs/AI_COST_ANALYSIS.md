# RedLens AI Cost Analysis

**Status:** Planning document.
**Date:** May 14, 2026.
**Scope:** Actual observed RedLens development spend and projected production
costs for running the adversarial platform at 100, 1K, 10K, and 100K test runs.

---

## Summary

RedLens has two different cost profiles:

- **Exploration campaigns** are LLM-assisted. They use the Red Team and Judge
  agents to discover new vulnerabilities. This is where most AI spend occurs.
- **Regression runs** should be deterministic wherever possible. They replay
  accepted evaluations against a target to catch known failures. RedLens
  controller LLM spend should be near zero for these runs, although the target
  AI system may still incur its own model cost.

The current production prototype is affordable at MVP scale. Based on observed
campaign data, RedLens has spent about **$1.03** of OpenRouter controller cost
across **54 LLM-assisted attempts**, or about **$0.0191 per test attempt**.
That number is useful, but it is not the full platform cost. Production cost
also includes target-side model use, Railway compute and storage, Langfuse
trace ingestion and retention, reports/artifacts, retries, rate limits, and
human review.

The core cost-control strategy is therefore architectural, not only model
selection: use LLM exploration to find and document failures, then promote
high-quality findings into deterministic regression evaluations that can run
frequently without paying the Red Team/Judge loop every time.

---

## Definition: Test Run

For this document, a **test run** means one executed adversarial test against a
target. In current RedLens terms, this is closest to one exploration campaign
attempt or one regression evaluation execution.

This is intentionally smaller than a campaign. A campaign is a container that
may run 5, 20, 100, or more test attempts. A regression job is also a container
that may execute many enabled evaluations.

The projection tables assume **monthly production volume**:

- 100 test runs / month
- 1,000 test runs / month
- 10,000 test runs / month
- 100,000 test runs / month

---

## Actual Observed Development Spend

Observed from the production RedLens database on May 14, 2026:

| Metric | Value |
| --- | ---: |
| LLM-assisted campaigns recorded | 5 |
| Total attempts recorded | 54 |
| Exploit verdicts recorded | 3 |
| RedLens-recorded OpenRouter spend | $1.030510 |
| Average RedLens controller spend / attempt | $0.019084 |
| Average RedLens controller spend / campaign | $0.206102 |
| Average controller spend / exploit found | $0.343503 |

Campaign-level spend observed:

| Campaign | Status | Mode | Target Mode | Attempts | Exploits | RedLens Spend |
| ---: | --- | --- | --- | ---: | ---: | ---: |
| 1 | completed | llm_assisted | live | 5 | 0 | $0.079064 |
| 2 | cancelled | llm_assisted | live | 4 | 0 | $0.075545 |
| 3 | completed | llm_assisted | live | 5 | 0 | $0.082058 |
| 4 | completed | llm_assisted | live | 20 | 0 | $0.362533 |
| 5 | completed | llm_assisted | live | 20 | 3 | $0.431309 |

Important caveats:

- This is RedLens controller LLM cost only, as recorded from OpenRouter usage
  metadata.
- It excludes the target `oe-ai-agent` model/API cost.
- It excludes Railway compute, storage, networking, and build cost.
- It excludes Langfuse subscription or usage overage.
- It excludes developer labor and manual review time.
- It reflects a small sample size and should be treated as an early planning
  baseline, not a stable long-term average.

---

## Current Pricing Inputs

Current production defaults use OpenRouter for RedLens LLM calls. At the time
of this document:

| Service | Pricing input used in this analysis | Source |
| --- | --- | --- |
| Claude Sonnet 4.5 via OpenRouter | $3 / 1M input tokens, $15 / 1M output tokens | [OpenRouter Claude Sonnet 4.5](https://openrouter.ai/anthropic/claude-sonnet-4.5) |
| GPT-5 via OpenRouter | $1.25 / 1M input tokens, $10 / 1M output tokens | [OpenRouter GPT-5](https://openrouter.ai/openai/gpt-5) |
| Claude Haiku 4.5 via OpenRouter | $1 / 1M input tokens, $5 / 1M output tokens | [OpenRouter Claude Haiku 4.5](https://openrouter.ai/anthropic/claude-haiku-4.5) |
| Railway Hobby / Pro plans | Hobby $5/month, Pro $20/month; usage-based CPU, RAM, egress, and volume storage | [Railway Pricing Docs](https://docs.railway.com/pricing) |
| Railway resource prices | RAM $10/GB/month, CPU $20/vCPU/month, egress $0.05/GB, volume storage $0.15/GB/month | [Railway Pricing Docs](https://docs.railway.com/pricing) |
| Langfuse Cloud Hobby | Free, 50K units/month included, 30 days data access | [Langfuse Pricing](https://langfuse.com/pricing) |
| Langfuse Cloud Core | $29/month, 100K units/month included, $8/additional 100K units | [Langfuse Pricing](https://langfuse.com/pricing) |

RedLens should continue treating OpenRouter `usage.cost` as the source of truth
for actual controller spend. Token formulas are useful for planning, but the
recorded provider cost is better for billing reconciliation.

---

## Cost Components

### 1. RedLens Controller LLM Cost

This is the cost of RedLens agents:

- Orchestrator routing and campaign planning
- Red Team attack generation
- Judge adjudication for uncertain cases
- Optional Documenter report generation if an LLM documenter is enabled

Current production behavior:

- `REDLENS_RED_TEAM_MODEL=anthropic/claude-sonnet-4.5`
- If `REDLENS_JUDGE_MODEL` is unset, the Judge uses the Red Team model.
- If `REDLENS_DOCUMENTER_MODEL` is unset, report generation is template-based
  and does not add documenter LLM cost.

The observed average is **$0.0191 per LLM-assisted attempt**.

### 2. Target AI System Cost

Live RedLens campaigns call the target `oe-ai-agent` service. That service may
make its own LLM calls and tool calls. RedLens does not currently capture that
target-side cost in its own cost ledger.

For projections, this document uses a placeholder target allowance of
**$0.0100 per live target test run**. This should be replaced once the target
returns cost metadata or once RedLens pulls target-side usage into campaign
accounting.

Mock target runs have no target AI cost.

### 3. Observability Cost

Langfuse traces are valuable because RedLens needs to debug agent behavior,
judge decisions, routing, prompts, model outputs, and evidence. At scale,
observability becomes a real cost center.

This projection assumes **8 Langfuse billable units per test run**. That is a
planning estimate for a trace containing campaign, red team, judge, and target
spans. The exact number should be measured from Langfuse usage after sustained
campaigns.

### 4. Infrastructure Cost

Railway cost is not proportional to tokens. It depends on service uptime,
concurrency, worker count, CPU/RAM allocation, database/storage size, network
egress, build frequency, and whether RedLens runs a separate worker service.

The current single-service deployment is enough for MVP demos, but production
volume introduces background workers, Postgres, queues, object storage, and
retention policies.

### 5. Human Review Cost

Promotion is intentionally human-gated. Human time does not scale with every
test run; it scales with findings, promotion drafts, severity review, and
remediation coordination.

For planning, a useful formula is:

```text
monthly_review_hours =
  promoted_findings_per_month * average_review_minutes_per_finding / 60
```

Example: 20 promoted findings/month at 10 minutes each is about 3.3 hours/month
of review time.

---

## Projection Assumptions

The table below models an exploration-heavy production workload where each test
run is LLM-assisted and targets a live AI service.

Assumptions:

- RedLens controller LLM cost: **$0.019084 / test run**, based on observed
  production data.
- Target AI system allowance: **$0.010000 / test run**, placeholder until
  target cost telemetry is available.
- Langfuse usage: **8 billable units / test run**.
- Langfuse plan: Hobby where it fits, Core where Hobby limits are exceeded.
- Infrastructure estimates include application hosting, worker capacity,
  database/storage, and operational headroom. They are planning estimates, not
  Railway invoices.
- Documenter LLM cost is excluded because current production defaults use the
  template documenter. If an LLM documenter is enabled, add report-generation
  cost per exploit, not per safe test run.

---

## Production Cost Projection

| Monthly Test Runs | RedLens Controller LLM | Target AI Allowance | AI Subtotal | Langfuse Estimate | Infra Estimate | Estimated Monthly Total |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | $1.91 | $1.00 | $2.91 | $0 | $20 | $22.91 |
| 1,000 | $19.08 | $10.00 | $29.08 | $0 | $75 | $104.08 |
| 10,000 | $190.84 | $100.00 | $290.84 | $29 | $225 | $544.84 |
| 100,000 | $1,908.35 | $1,000.00 | $2,908.35 | $85 | $900 | $3,893.35 |

Interpretation:

- At 100 and 1K runs/month, human time and product iteration matter more than
  infrastructure optimization.
- At 10K runs/month, worker design, database choice, trace retention, target
  rate limits, and retry behavior start to matter.
- At 100K runs/month, the workload should not be mostly LLM-assisted
  exploration. The economically correct shape is a small amount of exploration
  plus a large amount of deterministic regression.

---

## Regression-Heavy Cost Shape

The projection above intentionally models the expensive path. A mature RedLens
deployment should shift most volume to deterministic regression.

For deterministic regression runs:

| Component | Expected Cost Behavior |
| --- | --- |
| RedLens Red Team LLM | $0 per run |
| RedLens Judge LLM | $0 for deterministic judges; nonzero only for evals that explicitly require LLM judging |
| RedLens Documenter LLM | $0 during normal regression |
| Target AI model | Nonzero if the live target calls a model |
| Infrastructure | Grows with concurrency, storage, and retention |
| Observability | Grows with trace volume unless sampled |

This is why promotion matters. Once an exploit is accepted into `evaluations`,
RedLens should rerun it as a stable regression test instead of asking an LLM to
rediscover the same failure repeatedly.

Using the same target, observability, and infrastructure assumptions as the
exploration-heavy projection, but setting RedLens controller LLM cost to zero
for deterministic regression, the estimated cost shape is:

| Monthly Test Runs | RedLens Controller LLM | Target AI Allowance | Langfuse Estimate | Infra Estimate | Estimated Monthly Total |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | $0 | $1.00 | $0 | $20 | $21.00 |
| 1,000 | $0 | $10.00 | $0 | $75 | $85.00 |
| 10,000 | $0 | $100.00 | $29 | $225 | $354.00 |
| 100,000 | $0 | $1,000.00 | $85 | $900 | $1,985.00 |

If regression runs use the mock target instead of the live target, the target
AI allowance also drops to zero. In that case, the main costs are hosting,
storage, and observability.

---

## Architectural Changes By Scale

### 100 Test Runs / Month

Current architecture is sufficient.

Expected shape:

- Single Railway service for API and campaign execution
- SQLite volume acceptable for demos and short-lived MVP usage
- Template documenter
- Manual campaign start and review
- Langfuse full tracing enabled
- Strict `max_attempts` and `max_cost_usd` caps

Main risks:

- Small sample sizes make average cost noisy.
- SQLite is acceptable but not ideal for concurrent live campaigns.
- Target-side AI spend is invisible unless tracked manually.

Recommended work:

- Keep cost caps visible in the UI.
- Add target-side cost metadata to target adapter responses.
- Keep live-target approval gates enabled.

### 1K Test Runs / Month

Move from prototype persistence to production persistence.

Expected shape:

- Railway app plus managed Postgres
- Background worker process or separate worker service for campaigns
- Target-level mutual exclusion or concurrency limits
- Per-campaign and per-target cost ledger
- Full Langfuse tracing still reasonable

Main risks:

- SQLite write contention and operational fragility.
- Campaign execution competing with API responsiveness.
- Live target overload if multiple campaigns hit the same endpoint.
- Cost attribution gaps between RedLens and target AI service.

Recommended work:

- Move production from SQLite volume to Postgres.
- Split long-running campaign work from the web API.
- Add target locks or target concurrency limits.
- Store provider cost, token counts, model, and trace IDs per LLM call.

### 10K Test Runs / Month

Treat RedLens as a worker platform, not just a web app.

Expected shape:

- API service
- Worker pool for exploration and regression jobs
- Managed Postgres with indexes for campaigns, attempts, verdicts, findings,
  and traces
- Queue-backed job dispatch
- Object storage for large request/response artifacts and reports
- Scheduled regression batches
- Observability sampling or retention rules

Main risks:

- Retry storms can inflate model and target cost.
- Large transcripts can bloat the database.
- Langfuse trace volume can become noisy and expensive.
- Live target rate limits become a product constraint, not just a technical
  nuisance.

Recommended work:

- Add a queue, idempotency keys, and retry budgets.
- Move large artifacts out of the relational database.
- Add trace sampling by status: full traces for exploits/errors, sampled traces
  for safe attempts.
- Add per-endpoint and per-target rate limits.
- Add reporting that separates exploration cost, regression cost, target cost,
  and observability cost.

### 100K Test Runs / Month

RedLens needs a regression-first architecture at this scale.

Expected shape:

- Most test runs are deterministic regression evaluations.
- LLM-assisted exploration is budgeted separately and used for novel discovery.
- Worker pools are horizontally scalable.
- Postgres tables are partitioned or aggressively indexed.
- Artifacts have retention policies.
- Observability is sampled, tiered, or self-hosted.
- Model routing uses smaller models for routing/judging and larger models only
  for creative attack generation.

Main risks:

- Running 100K tests as LLM-assisted exploration wastes money and produces
  inconsistent signal.
- Full trace retention for every safe attempt can dominate operational cost.
- Target services can be hammered without target locks and schedule controls.
- Human review can become the bottleneck if promotion drafts are noisy.

Recommended work:

- Make regression the default high-volume path.
- Use exploration budgets by threat category and target.
- Route Orchestrator and Judge work to cheaper models where quality permits.
- Keep Sonnet-class models for novel Red Team generation.
- Add content-addressed artifacts and prompt/payload deduplication.
- Add summary tables for cost, coverage, exploit yield, and false-positive rate.
- Consider self-hosted Langfuse or an enterprise observability plan if trace
  retention, data residency, or HIPAA controls require it.

---

## Cost-Control Levers

The most important levers are:

| Lever | Effect |
| --- | --- |
| Deterministic regression promotion | Moves volume away from LLM-assisted exploration |
| Model routing | Uses cheaper models for routing and judging, stronger models for creative attacks |
| Deterministic judge fast path | Avoids LLM judge calls for obvious safe/exploit outcomes |
| Target locks and rate limits | Prevents accidental overload and runaway live-target cost |
| `max_cost_usd` per campaign | Hard stop for exploration spend |
| `max_attempts` per campaign | Predictable upper bound on test count |
| Trace sampling | Keeps observability useful without logging every safe attempt forever |
| Artifact retention | Prevents transcripts and reports from growing unbounded |
| Target-side cost telemetry | Makes live target cost visible in RedLens reporting |
| Promotion quality gates | Prevents noisy drafts from inflating the regression suite |

---

## Recommended Next Measurements

RedLens should add or improve the following measurements before trusting larger
production projections:

1. **Target AI cost per request.** The target adapter should capture target-side
   model, token, and cost metadata when available.
2. **Per-agent cost breakdown.** Store Red Team, Judge, Orchestrator, and
   Documenter spend separately.
3. **Per-endpoint cost and yield.** Track cost and exploit rate by endpoint,
   such as chat versus document extraction.
4. **Langfuse billable units per attempt.** Pull actual usage from Langfuse
   billing or usage exports instead of estimating 8 units/run.
5. **Retry and error cost.** Separate successful attempts from retries,
   provider errors, target errors, and cancelled campaigns.
6. **Human review time.** Track promotion decisions and time-to-approval so the
   platform can estimate operational review load.
7. **Regression cost baseline.** Measure deterministic regression sweeps
   separately from LLM-assisted exploration campaigns.

---

## Product Recommendation

For the next production iteration, RedLens should keep the current default of
LLM-assisted exploration plus human promotion, but invest in cost accounting
before increasing volume:

1. Add target-side cost telemetry to target adapter responses.
2. Move production persistence to Postgres before sustained 1K+ monthly runs.
3. Split campaign execution into a worker before sustained 10K+ monthly runs.
4. Keep documenter LLM disabled by default until report quality requires it.
5. Make regression the high-volume path and exploration the discovery path.
6. Add a cost dashboard that separates RedLens controller cost, target AI cost,
   infra estimate, Langfuse trace cost, and cost per promoted finding.

This keeps RedLens aligned with its two-loop architecture: spend money on LLMs
where they create new security signal, then convert the result into cheap,
repeatable regression coverage.
