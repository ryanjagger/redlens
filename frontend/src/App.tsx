import { FormEvent, useMemo, useState } from "react";
import { NavLink, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import halLogo from "./assets/hal.jpg";
import {
  api,
  Attempt,
  Campaign,
  CampaignCreate,
  Evaluation,
  Finding,
  PromotedEvalDraft,
  Result,
  RunSummary,
  TargetCreate,
  Verdict
} from "./api";

const statusClass: Record<string, string> = {
  passed: "status status-pass",
  failed: "status status-fail",
  error: "status status-error",
  completed: "status status-complete",
  running: "status status-running",
  open: "status status-open",
  draft: "status",
  needs_live_approval: "status status-warning",
  budget_exhausted: "status status-warning",
  cancelled: "status status-muted",
  judged: "status status-complete",
  documented: "status status-complete",
  exploit: "status status-fail",
  safe: "status status-pass",
  uncertain: "status status-warning",
  accepted: "status status-pass",
  saved_disabled: "status status-muted",
  rejected: "status status-fail",
  needs_revision: "status status-warning",
  pending: "status status-warning",
  fix_validated: "status status-pass",
  regression_confirmed: "status status-fail"
};

export default function App() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <img className="brand-mark" src={halLogo} alt="" draggable={false} />
          <div>
            <strong>RedLens AI</strong>
            <span>Adversarial evals</span>
          </div>
        </div>
        <nav>
          <NavLink to="/">Dashboard</NavLink>
          <NavLink to="/targets">Targets</NavLink>
          <NavLink to="/threat-model">Threat Model</NavLink>
          <NavLink to="/evaluations">Evaluations</NavLink>
          <NavLink to="/campaigns">Exploration Campaigns</NavLink>
          <NavLink to="/runs">Regression Runs</NavLink>
          <NavLink to="/findings">Findings</NavLink>
        </nav>
      </aside>
      <main className="content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/targets" element={<Targets />} />
          <Route path="/threat-model" element={<ThreatModel />} />
          <Route path="/evaluations" element={<Evaluations />} />
          <Route path="/campaigns" element={<Campaigns />} />
          <Route path="/campaigns/:id" element={<CampaignDetail />} />
          <Route path="/runs" element={<Runs />} />
          <Route path="/runs/:id" element={<RunDetail />} />
          <Route path="/findings" element={<Findings />} />
        </Routes>
      </main>
    </div>
  );
}

function Dashboard() {
  const categories = useQuery({ queryKey: ["categories"], queryFn: api.categories });
  const evaluations = useQuery({ queryKey: ["evaluations"], queryFn: api.evaluations });
  const campaigns = useQuery({ queryKey: ["campaigns"], queryFn: api.campaigns });
  const drafts = useQuery({ queryKey: ["promotedEvalDrafts"], queryFn: api.promotedEvalDrafts });
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const findings = useQuery({ queryKey: ["findings"], queryFn: api.findings });
  const latest = runs.data?.[0];
  const evaluationRows = evaluations.data ?? [];
  const campaignRows = campaigns.data ?? [];
  const draftRows = drafts.data ?? [];
  const enabledEvaluations = evaluationRows.filter((evaluation) => evaluation.enabled).length;
  const pendingDrafts = draftRows.filter((draft) => draft.status === "pending").length;
  const needsRevisionDrafts = draftRows.filter((draft) => draft.status === "needs_revision").length;
  const acceptedDrafts = draftRows.filter((draft) => draft.status === "accepted").length;
  const runningCampaigns = campaignRows.filter((campaign) => campaign.status === "running").length;
  const liveApprovals = campaignRows.filter((campaign) => campaign.status === "needs_live_approval").length;

  return (
    <section>
      <PageTitle title="Dashboard" subtitle="Regression coverage and exploration campaign status." />
      <div className="metric-grid">
        <Metric label="Enabled evaluations" value={enabledEvaluations} />
        <Metric label="Latest regression failures" value={latest?.failed_count ?? 0} tone="fail" />
        <Metric label="Exploration campaigns" value={campaignRows.length} />
        <Metric label="Pending drafts" value={pendingDrafts} tone={pendingDrafts > 0 ? "error" : undefined} />
      </div>
      <div className="split">
        <Panel title="Regression Coverage">
          <div className="coverage-list">
            {(categories.data ?? []).map((category) => (
              <div className="coverage-row" key={category.key}>
                <div>
                  <strong>{category.name}</strong>
                  <span>{category.evaluation_count} evaluations</span>
                </div>
                <span className={category.last_failed_count > 0 ? "risk-pill fail" : "risk-pill"}>
                  {category.last_failed_count} latest failures
                </span>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="Exploration Queue">
          <div className="budget-list">
            <BudgetRow label="Running campaigns" value={String(runningCampaigns)} />
            <BudgetRow label="Awaiting live approval" value={String(liveApprovals)} />
            <BudgetRow label="Pending drafts" value={String(pendingDrafts)} />
            <BudgetRow label="Needs revision" value={String(needsRevisionDrafts)} />
            <BudgetRow label="Accepted drafts" value={String(acceptedDrafts)} />
            <BudgetRow label="Open findings" value={String(findings.data?.length ?? 0)} />
          </div>
        </Panel>
      </div>
      <Panel title="Latest Regression Run">
        {latest ? <RunSummaryBlock run={latest} /> : <EmptyState text="No regression runs yet." />}
      </Panel>
    </section>
  );
}

function Targets() {
  const queryClient = useQueryClient();
  const targets = useQuery({ queryKey: ["targets"], queryFn: api.targets });
  const [form, setForm] = useState<TargetCreate>({
    name: "",
    mode: "mock",
    base_url: "mock://openemr",
    internal_auth_env: "",
    bearer_token_env: "",
    fhir_base_url: "mock://openemr/fhir",
    user_uuid: "",
    patient_uuid: "eval-current-patient"
  });
  const create = useMutation({
    mutationFn: api.createTarget,
    onSuccess: () => {
      setForm({
        name: "",
        mode: "mock",
        base_url: "mock://openemr",
        internal_auth_env: "",
        bearer_token_env: "",
        fhir_base_url: "mock://openemr/fhir",
        user_uuid: "",
        patient_uuid: "eval-current-patient"
      });
      queryClient.invalidateQueries({ queryKey: ["targets"] });
    }
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    create.mutate({
      ...form,
      internal_auth_env: form.internal_auth_env || null,
      bearer_token_env: form.bearer_token_env || null,
      fhir_base_url: form.fhir_base_url || null,
      user_uuid: form.user_uuid || null,
      patient_uuid: form.patient_uuid || null
    });
  }

  return (
    <section>
      <PageTitle title="Targets" subtitle="Systems under adversarial evaluation." />
      <Panel title="Create Target">
        <form className="target-form" onSubmit={submit}>
          <label>
            Name
            <input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} required />
          </label>
          <label>
            Mode
            <select
              value={form.mode}
              onChange={(event) =>
                setForm({
                  ...form,
                  mode: event.target.value as TargetCreate["mode"],
                  base_url: event.target.value === "mock" ? "mock://openemr" : "http://127.0.0.1:8400"
                })
              }
            >
              <option value="mock">mock</option>
              <option value="live">live</option>
            </select>
          </label>
          <label>
            Base URL
            <input value={form.base_url} onChange={(event) => setForm({ ...form, base_url: event.target.value })} />
          </label>
          <label>
            Internal auth env
            <input
              value={form.internal_auth_env ?? ""}
              onChange={(event) => setForm({ ...form, internal_auth_env: event.target.value })}
              placeholder="OPENEMR_INTERNAL_AUTH_SECRET"
            />
          </label>
          <label>
            Bearer token env
            <input
              value={form.bearer_token_env ?? ""}
              onChange={(event) => setForm({ ...form, bearer_token_env: event.target.value })}
              placeholder="OPENEMR_BEARER_TOKEN"
            />
          </label>
          <label>
            FHIR base URL
            <input
              value={form.fhir_base_url ?? ""}
              onChange={(event) => setForm({ ...form, fhir_base_url: event.target.value })}
            />
          </label>
          <label>
            User UUID
            <input
              value={form.user_uuid ?? ""}
              onChange={(event) => setForm({ ...form, user_uuid: event.target.value })}
            />
          </label>
          <label>
            Patient UUID
            <input
              value={form.patient_uuid ?? ""}
              onChange={(event) => setForm({ ...form, patient_uuid: event.target.value })}
            />
          </label>
          <button type="submit" disabled={create.isPending}>
            {create.isPending ? "Creating..." : "Create Target"}
          </button>
          {create.error ? <p className="error-text">{create.error.message}</p> : null}
        </form>
      </Panel>
      <Panel title="Configured Targets">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Mode</th>
              <th>Base URL</th>
              <th>Patient</th>
              <th>User</th>
              <th>Auth</th>
            </tr>
          </thead>
          <tbody>
            {(targets.data ?? []).map((target) => (
              <tr key={target.id}>
                <td>{target.name}</td>
                <td><span className="mono">{target.mode}</span></td>
                <td>{target.base_url}</td>
                <td>{target.patient_uuid ?? "not set"}</td>
                <td>{target.user_uuid ?? "not set"}</td>
                <td>{target.internal_auth_env ? `env:${target.internal_auth_env}` : "none"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </section>
  );
}

function Campaigns() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const targets = useQuery({ queryKey: ["targets"], queryFn: api.targets });
  const campaigns = useQuery({ queryKey: ["campaigns"], queryFn: api.campaigns });
  const [form, setForm] = useState<CampaignCreate>({
    target_id: 0,
    focus_hint: "",
    max_attempts: 5,
    max_wall_clock_seconds: 300,
    max_cost_usd: 0.5,
    llm_mode: "deterministic"
  });
  const selectedTargetId = form.target_id || targets.data?.[0]?.id || 0;
  const selectedTarget = (targets.data ?? []).find((target) => target.id === selectedTargetId);
  const create = useMutation({
    mutationFn: api.createCampaign,
    onSuccess: (campaign) => {
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
      navigate(`/campaigns/${campaign.id}`);
    }
  });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedTargetId) {
      return;
    }
    create.mutate({
      ...form,
      target_id: selectedTargetId,
      focus_hint: form.focus_hint || null
    });
  }

  const campaignRows = campaigns.data ?? [];
  const runningCount = campaignRows.filter((campaign) => campaign.status === "running").length;
  const livePending = campaignRows.filter((campaign) => campaign.status === "needs_live_approval").length;
  const totalExploits = campaignRows.reduce((sum, campaign) => sum + campaign.exploit_count, 0);

  return (
    <section>
      <PageTitle title="Exploration Campaigns" subtitle="Agentic discovery campaigns and promotion review." />
      <div className="metric-grid">
        <Metric label="Campaigns" value={campaignRows.length} />
        <Metric label="Running" value={runningCount} />
        <Metric label="Live approvals" value={livePending} tone={livePending > 0 ? "error" : undefined} />
        <Metric label="Exploits" value={totalExploits} tone={totalExploits > 0 ? "fail" : undefined} />
      </div>
      <div className="split">
        <Panel title="New Campaign">
          <form className="campaign-form" onSubmit={submit}>
            <label>
              Target
              <select
                value={selectedTargetId}
                onChange={(event) => setForm({ ...form, target_id: Number(event.target.value) })}
              >
                {(targets.data ?? []).map((target) => (
                  <option value={target.id} key={target.id}>
                    {target.name} ({target.mode})
                  </option>
                ))}
              </select>
            </label>
            <label>
              Focus hint
              <input
                value={form.focus_hint ?? ""}
                onChange={(event) => setForm({ ...form, focus_hint: event.target.value })}
                placeholder="prompt_injection_indirect"
              />
            </label>
            <label>
              Max attempts
              <input
                type="number"
                min={1}
                max={500}
                value={form.max_attempts}
                onChange={(event) => setForm({ ...form, max_attempts: Number(event.target.value) })}
              />
            </label>
            <label>
              Max minutes
              <input
                type="number"
                min={1}
                value={Math.round(form.max_wall_clock_seconds / 60)}
                onChange={(event) =>
                  setForm({ ...form, max_wall_clock_seconds: Number(event.target.value) * 60 })
                }
              />
            </label>
            <label>
              Max cost USD
              <input
                type="number"
                min={0}
                step={0.01}
                value={form.max_cost_usd}
                onChange={(event) => setForm({ ...form, max_cost_usd: Number(event.target.value) })}
              />
            </label>
            <label>
              LLM mode
              <select
                value={form.llm_mode}
                onChange={(event) =>
                  setForm({ ...form, llm_mode: event.target.value as CampaignCreate["llm_mode"] })
                }
              >
                <option value="deterministic">deterministic</option>
                <option value="llm_assisted">llm assisted</option>
              </select>
            </label>
            {selectedTarget?.mode === "live" ? (
              <div className="approval-note">
                <strong>Live approval required</strong>
                <span>{selectedTarget.base_url}</span>
              </div>
            ) : null}
            <button type="submit" disabled={!selectedTargetId || create.isPending}>
              {create.isPending ? "Creating..." : "Create Campaign"}
            </button>
            {create.error ? <p className="error-text">{create.error.message}</p> : null}
          </form>
        </Panel>
        <Panel title="Campaign Queue">
          {campaigns.isLoading ? (
            <EmptyState text="Loading campaigns..." />
          ) : campaignRows.length === 0 ? (
            <EmptyState text="No campaigns yet." />
          ) : (
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Target</th>
                  <th>Status</th>
                  <th>Attempts</th>
                  <th>Budget</th>
                  <th>Activity</th>
                </tr>
              </thead>
              <tbody>
                {campaignRows.map((campaign) => (
                  <tr key={campaign.id} onClick={() => navigate(`/campaigns/${campaign.id}`)} className="click-row">
                    <td>#{campaign.id}</td>
                    <td>
                      <strong>{campaign.target_name_snapshot}</strong>
                      <span className="subtle-line">{campaign.target_mode_snapshot}</span>
                    </td>
                    <td><Status value={campaign.status} /></td>
                    <td>{campaign.attempt_count} / {campaign.max_attempts}</td>
                    <td>${campaign.spent_cost_usd.toFixed(2)} / ${campaign.max_cost_usd.toFixed(2)}</td>
                    <td>{formatDate(campaign.last_activity_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Panel>
      </div>
    </section>
  );
}

function CampaignDetail() {
  const { id } = useParams();
  const campaignId = Number.parseInt(id ?? "", 10);
  const hasValidCampaignId = Number.isFinite(campaignId);
  const queryClient = useQueryClient();
  const campaign = useQuery({
    queryKey: ["campaign", campaignId],
    queryFn: () => api.campaign(campaignId),
    enabled: hasValidCampaignId
  });
  const refreshCampaigns = () => {
    queryClient.invalidateQueries({ queryKey: ["campaigns"] });
    queryClient.invalidateQueries({ queryKey: ["campaign", campaignId] });
  };
  const start = useMutation({ mutationFn: api.startCampaign, onSuccess: refreshCampaigns });
  const approveLive = useMutation({ mutationFn: api.approveLiveCampaign, onSuccess: refreshCampaigns });
  const cancel = useMutation({ mutationFn: api.cancelCampaign, onSuccess: refreshCampaigns });
  const mutationError = [start.error, approveLive.error, cancel.error].find(Boolean);
  const errorText = mutationError instanceof Error ? mutationError.message : null;

  if (!hasValidCampaignId) {
    return <EmptyState text="Invalid campaign id." />;
  }

  if (campaign.isLoading || campaign.isFetching) {
    return <EmptyState text={`Loading campaign #${campaignId}...`} />;
  }

  if (campaign.error instanceof Error) {
    return <div className="empty-state error-text">Campaign API: {campaign.error.message}</div>;
  }

  if (!campaign.data) {
    return <EmptyState text={`Campaign #${campaignId} was not found.`} />;
  }

  const row = campaign.data;
  const elapsedSeconds = elapsed(row);
  const attemptsRemaining = Math.max(row.max_attempts - row.attempt_count, 0);
  const isTerminal = ["completed", "budget_exhausted", "cancelled", "failed"].includes(row.status);

  return (
    <section>
      <PageTitle
        title={`Campaign #${row.id}`}
        subtitle={`${row.target_name_snapshot} · ${row.target_mode_snapshot} · ${row.llm_mode}`}
      />
      <div className="metric-grid compact">
        <Metric label="Attempts" value={row.attempt_count} />
        <Metric label="Remaining" value={attemptsRemaining} />
        <Metric label="Exploits" value={row.exploit_count} tone={row.exploit_count > 0 ? "fail" : undefined} />
        <Metric label="Elapsed sec" value={elapsedSeconds} />
      </div>
      {row.target_mode_snapshot === "live" && row.status === "needs_live_approval" ? (
        <Panel title="Live Approval Gate">
          <div className="approval-panel">
            <div>
              <Status value={row.status} />
              <h3>{row.target_base_url_snapshot}</h3>
              <dl>
                <dt>User UUID</dt>
                <dd>{row.target_user_uuid_snapshot ?? "not set"}</dd>
                <dt>Patient UUID</dt>
                <dd>{row.target_patient_uuid_snapshot ?? "not set"}</dd>
                <dt>Limits</dt>
                <dd>{row.max_attempts} attempts · {Math.round(row.max_wall_clock_seconds / 60)} min · ${row.max_cost_usd.toFixed(2)}</dd>
              </dl>
            </div>
            <button onClick={() => approveLive.mutate(row.id)} disabled={approveLive.isPending}>
              {approveLive.isPending ? "Approving..." : "Approve & Start"}
            </button>
          </div>
        </Panel>
      ) : null}
      <div className="split">
        <Panel title="Controls" className="controls-panel">
          <div className="campaign-actions">
            <Status value={row.status} />
            <button
              onClick={() => start.mutate(row.id)}
              disabled={row.status !== "draft" || start.isPending}
            >
              {start.isPending ? "Starting..." : "Start Campaign"}
            </button>
            <button
              className="secondary"
              onClick={() => cancel.mutate(row.id)}
              disabled={isTerminal || cancel.isPending}
            >
              {cancel.isPending ? "Cancelling..." : "Cancel Campaign"}
            </button>
            {errorText ? <span className="error-text">{errorText}</span> : null}
          </div>
        </Panel>
        <Panel title="Budget">
          <div className="budget-list">
            <BudgetRow label="Cost" value={`$${row.spent_cost_usd.toFixed(2)} / $${row.max_cost_usd.toFixed(2)}`} />
            <BudgetRow label="Wall clock" value={`${elapsedSeconds}s / ${row.max_wall_clock_seconds}s`} />
            <BudgetRow label="Attempts" value={`${row.attempt_count} / ${row.max_attempts}`} />
            <BudgetRow label="Stop reason" value={row.stop_reason ?? "none"} />
          </div>
        </Panel>
      </div>
      <Panel title="Agent Timeline">
        <div className="timeline">
          <TimelineItem title="Campaign created" meta={formatDate(row.created_at)} active />
          <TimelineItem
            title={row.target_mode_snapshot === "live" ? "Live target approval" : "Mock target ready"}
            meta={
              row.target_mode_snapshot === "live"
                ? row.live_approved_at ? formatDate(row.live_approved_at) : "pending"
                : "no approval required"
            }
            active={row.target_mode_snapshot !== "live" || Boolean(row.live_approved_at)}
          />
          <TimelineItem
            title="Orchestrator running"
            meta={row.started_at ? formatDate(row.started_at) : "not started"}
            active={row.status === "running"}
          />
          <TimelineItem title="Red Team attempts" meta={`${row.attempt_count} recorded`} active={row.attempt_count > 0} />
          <TimelineItem title="Judge verdicts" meta={`${row.exploit_count} exploits`} active={row.exploit_count > 0} />
        </div>
      </Panel>
      <Panel title="Attempts & Verdicts">
        {row.attempts.length === 0 ? (
          <EmptyState text="No attempts recorded yet." />
        ) : (
          <div className="attempt-list">
            {row.attempts.map((attempt) => (
              <AttemptCard attempt={attempt} key={attempt.id} />
            ))}
          </div>
        )}
      </Panel>
      <Panel title="Campaign Evidence">
        <div className="evidence-grid">
          <details open>
            <summary>Target Snapshot</summary>
            <pre>{JSON.stringify(targetSnapshot(row), null, 2)}</pre>
          </details>
          <details open>
            <summary>Campaign Limits</summary>
            <pre>{JSON.stringify(campaignLimits(row), null, 2)}</pre>
          </details>
        </div>
      </Panel>
    </section>
  );
}

function ThreatModel() {
  const categories = useQuery({ queryKey: ["categories"], queryFn: api.categories });
  return (
    <section>
      <PageTitle title="Threat Model" subtitle="Living categories mapped to executable coverage." />
      <div className="category-grid">
        {(categories.data ?? []).map((category) => (
          <article className="threat-panel" key={category.key}>
            <div className="threat-header">
              <h2>{category.name}</h2>
              <span className={category.last_failed_count > 0 ? "risk-pill fail" : "risk-pill"}>
                {category.evaluation_count} evals
              </span>
            </div>
            <p>{category.description}</p>
            <div className="threat-footer">
              <span>{category.key}</span>
              <strong>{category.last_failed_count} failures in latest run</strong>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function Evaluations() {
  const evaluations = useQuery({ queryKey: ["evaluations"], queryFn: api.evaluations });
  const grouped = useMemo(() => groupEvaluations(evaluations.data ?? []), [evaluations.data]);
  return (
    <section>
      <PageTitle title="Evaluations" subtitle="Seeded adversarial cases derived from the threat model." />
      {Object.entries(grouped).map(([category, rows]) => (
        <Panel title={category} key={category}>
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Endpoint</th>
                <th>Severity</th>
                <th>Last</th>
                <th>Expected Behavior</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((evaluation) => (
                <tr key={evaluation.id}>
                  <td>{evaluation.name}</td>
                  <td><span className="mono">{evaluation.endpoint}</span></td>
                  <td>{evaluation.severity}</td>
                  <td>{evaluation.last_status ? <Status value={evaluation.last_status} /> : "not run"}</td>
                  <td>{evaluation.expected_behavior}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      ))}
    </section>
  );
}

function Runs() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const targets = useQuery({ queryKey: ["targets"], queryFn: api.targets });
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const [targetId, setTargetId] = useState<number | "">("");
  const create = useMutation({
    mutationFn: api.createRun,
    onSuccess: (run) => {
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      queryClient.invalidateQueries({ queryKey: ["evaluations"] });
      queryClient.invalidateQueries({ queryKey: ["categories"] });
      navigate(`/runs/${run.id}`);
    }
  });
  const selectedTarget = targetId || targets.data?.[0]?.id || "";
  const targetsError = targets.error instanceof Error ? targets.error.message : null;
  const runsError = runs.error instanceof Error ? runs.error.message : null;

  return (
    <section>
      <PageTitle title="Regression Runs" subtitle="Execute and inspect deterministic evaluation sweeps." />
      <Panel title="Start Run">
        <div className="run-controls">
          {targets.isLoading ? (
            <span className="muted-text">Loading targets...</span>
          ) : targetsError ? (
            <span className="error-text">Targets API: {targetsError}</span>
          ) : (
            <select
              value={selectedTarget}
              onChange={(event) => setTargetId(Number(event.target.value))}
            >
              {(targets.data ?? []).map((target) => (
                <option value={target.id} key={target.id}>
                  {target.name}
                </option>
              ))}
            </select>
          )}
          <button
            onClick={() => selectedTarget && create.mutate(Number(selectedTarget))}
            disabled={!selectedTarget || create.isPending}
          >
            {create.isPending ? "Running..." : "Run All Enabled Evaluations"}
          </button>
          {create.error ? <span className="error-text">{create.error.message}</span> : null}
        </div>
      </Panel>
      <Panel title="Run History">
        {runs.isLoading ? (
          <EmptyState text="Loading runs..." />
        ) : runsError ? (
          <div className="empty-state error-text">Runs API: {runsError}</div>
        ) : (
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>Target</th>
              <th>Status</th>
              <th>Passed</th>
              <th>Failed</th>
              <th>Errors</th>
              <th>Started</th>
            </tr>
          </thead>
          <tbody>
            {(runs.data ?? []).map((run) => (
              <tr key={run.id} onClick={() => navigate(`/runs/${run.id}`)} className="click-row">
                <td>#{run.id}</td>
                <td>{run.target_name}</td>
                <td><Status value={run.status} /></td>
                <td>{run.passed_count}</td>
                <td>{run.failed_count}</td>
                <td>{run.error_count}</td>
                <td>{formatDate(run.started_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        )}
      </Panel>
    </section>
  );
}

function RunDetail() {
  const { id } = useParams();
  const runId = Number.parseInt(id ?? "", 10);
  const hasValidRunId = Number.isFinite(runId);
  const queryClient = useQueryClient();
  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => api.run(runId),
    enabled: hasValidRunId
  });
  const promote = useMutation({
    mutationFn: api.promote,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["findings"] });
    }
  });
  const runError = run.error instanceof Error ? run.error.message : null;

  if (!hasValidRunId) {
    return <EmptyState text="Invalid run id." />;
  }

  if (run.isLoading || run.isFetching) {
    return <EmptyState text={`Loading run #${runId}...`} />;
  }

  if (runError) {
    return <div className="empty-state error-text">Run detail API: {runError}</div>;
  }

  if (!run.data) {
    return <EmptyState text={`Run #${runId} was not found.`} />;
  }

  return (
    <section>
      <PageTitle title={`Run #${run.data.id}`} subtitle={`${run.data.target_name} · ${formatDate(run.data.started_at)}`} />
      <div className="metric-grid compact">
        <Metric label="Passed" value={run.data.passed_count} tone="pass" />
        <Metric label="Failed" value={run.data.failed_count} tone="fail" />
        <Metric label="Errors" value={run.data.error_count} tone="error" />
        <Metric label="Total" value={run.data.total_count} />
      </div>
      <Panel title="Results">
        <div className="result-list">
          {run.data.results.map((result) => (
            <ResultCard
              result={result}
              key={result.id}
              onPromote={() => promote.mutate(result.id)}
              promoting={promote.isPending}
            />
          ))}
        </div>
      </Panel>
    </section>
  );
}

function Findings() {
  const findings = useQuery({ queryKey: ["findings"], queryFn: api.findings });
  return (
    <section>
      <PageTitle title="Findings" subtitle="Promoted failures with reproduction evidence." />
      <Panel title="Open Findings">
        {(findings.data ?? []).length === 0 ? (
          <EmptyState text="No promoted findings yet." />
        ) : (
          <div className="finding-list">
            {(findings.data ?? []).map((finding) => (
              <FindingCard finding={finding} key={finding.id} />
            ))}
          </div>
        )}
      </Panel>
    </section>
  );
}

function ResultCard({ result, onPromote, promoting }: { result: Result; onPromote: () => void; promoting: boolean }) {
  return (
    <article className="result-card">
      <div className="result-title">
        <div>
          <h3>{result.evaluation_name}</h3>
          <span>{result.category_name} · {result.endpoint} · {result.latency_ms}ms</span>
        </div>
        <Status value={result.status} />
      </div>
      <p className="judge">{result.judge_reason}</p>
      {result.origin_finding_id ? <ResultOrigin result={result} /> : null}
      <div className="evidence-grid">
        <details>
          <summary>Request Evidence</summary>
          <pre>{JSON.stringify(result.request_json, null, 2)}</pre>
        </details>
        <details>
          <summary>Response Evidence</summary>
          <pre>{JSON.stringify(result.response_json, null, 2)}</pre>
        </details>
      </div>
      {result.status !== "passed" ? (
        <button className="secondary" onClick={onPromote} disabled={promoting}>
          Promote to Finding
        </button>
      ) : null}
    </article>
  );
}

function ResultOrigin({ result }: { result: Result }) {
  return (
    <div className="origin-meta">
      <span>promoted finding #{result.origin_finding_id}</span>
      {result.origin_finding_status ? <span>{result.origin_finding_status}</span> : null}
      {result.origin_draft_id ? <span>draft #{result.origin_draft_id}</span> : null}
      {result.origin_report_path ? <span>{result.origin_report_path}</span> : null}
    </div>
  );
}

function FindingCard({ finding }: { finding: Finding }) {
  return (
    <article className="finding-card">
      <div className="result-title">
        <div>
          <h3>{finding.title}</h3>
          <span>{finding.category_key} · {finding.endpoint} · {finding.severity}</span>
        </div>
        <Status value={finding.status} />
      </div>
      <div className="finding-meta">
        {finding.report_path ? <span>{finding.report_path}</span> : null}
        {finding.linked_attempt_id ? <span>attempt #{finding.linked_attempt_id}</span> : null}
        {finding.linked_evaluation_id ? <span>evaluation #{finding.linked_evaluation_id}</span> : null}
      </div>
      <pre>{finding.reproduction_steps}</pre>
    </article>
  );
}

function Panel({
  title,
  children,
  className
}: {
  title: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={className ? `panel ${className}` : "panel"}>
      <h2>{title}</h2>
      {children}
    </section>
  );
}

function PageTitle({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <header className="page-title">
      <h1>{title}</h1>
      <p>{subtitle}</p>
    </header>
  );
}

function Metric({ label, value, tone }: { label: string; value: number; tone?: "pass" | "fail" | "error" }) {
  return (
    <div className={`metric ${tone ?? ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Status({ value }: { value: string }) {
  return <span className={statusClass[value] ?? "status"}>{value}</span>;
}

function AttemptCard({ attempt }: { attempt: Attempt }) {
  const verdict = attempt.verdicts[attempt.verdicts.length - 1];
  const routing = getRecord(attempt.execution_metadata.orchestrator);
  const queryClient = useQueryClient();
  const refreshDrafts = () => {
    queryClient.invalidateQueries({ queryKey: ["campaign", attempt.campaign_id] });
    queryClient.invalidateQueries({ queryKey: ["campaigns"] });
    queryClient.invalidateQueries({ queryKey: ["promotedEvalDrafts"] });
    queryClient.invalidateQueries({ queryKey: ["evaluations"] });
    queryClient.invalidateQueries({ queryKey: ["findings"] });
  };
  const approveDraft = useMutation({
    mutationFn: (draftId: number) => api.approvePromotedEvalDraft(draftId),
    onSuccess: refreshDrafts
  });
  const saveDisabled = useMutation({
    mutationFn: (draftId: number) => api.savePromotedEvalDraftDisabled(draftId),
    onSuccess: refreshDrafts
  });
  const rejectDraft = useMutation({
    mutationFn: (draftId: number) => api.rejectPromotedEvalDraft(draftId),
    onSuccess: refreshDrafts
  });
  const reviseDraft = useMutation({
    mutationFn: (draftId: number) => api.requestPromotedEvalDraftRevision(draftId),
    onSuccess: refreshDrafts
  });
  const draftError = [approveDraft.error, saveDisabled.error, rejectDraft.error, reviseDraft.error].find(Boolean);
  const draftErrorText = draftError instanceof Error ? draftError.message : null;

  return (
    <article className="attempt-card">
      <div className="result-title">
        <div>
          <h3>Attempt #{attempt.id}</h3>
          <span>{attempt.focus_area} · {attempt.vector_key ?? "no vector"} · {attemptStatusTime(attempt)}</span>
        </div>
        <div className="attempt-statuses">
          <Status value={attempt.status} />
          {verdict ? <Status value={verdict.verdict} /> : null}
        </div>
      </div>
      {verdict ? <p className="judge">{verdict.rationale}</p> : null}
      {verdict ? <JudgeMetadata verdict={verdict} /> : null}
      <div className="evidence-grid">
        <details>
          <summary>Attack Plan</summary>
          <pre>{JSON.stringify(attempt.attack_plan, null, 2)}</pre>
        </details>
        <details>
          <summary>Verdict</summary>
          <pre>{JSON.stringify(verdict ?? {}, null, 2)}</pre>
        </details>
        {routing ? (
          <details>
            <summary>Orchestrator Routing</summary>
            <pre>{JSON.stringify(routing, null, 2)}</pre>
          </details>
        ) : null}
        <details>
          <summary>Request</summary>
          <pre>{JSON.stringify(attempt.request_json, null, 2)}</pre>
        </details>
        <details>
          <summary>Response</summary>
          <pre>{JSON.stringify(attempt.response_json, null, 2)}</pre>
        </details>
      </div>
      {attempt.promoted_eval_drafts.length > 0 ? (
        <div className="draft-list">
          {attempt.promoted_eval_drafts.map((draft) => (
            <DraftReviewCard
              draft={draft}
              key={draft.id}
              approve={() => approveDraft.mutate(draft.id)}
              saveDisabled={() => saveDisabled.mutate(draft.id)}
              reject={() => rejectDraft.mutate(draft.id)}
              requestRevision={() => reviseDraft.mutate(draft.id)}
              busy={approveDraft.isPending || saveDisabled.isPending || rejectDraft.isPending || reviseDraft.isPending}
            />
          ))}
          {draftErrorText ? <p className="error-text">{draftErrorText}</p> : null}
        </div>
      ) : null}
    </article>
  );
}

function JudgeMetadata({ verdict }: { verdict: Verdict }) {
  const usage = getRecord(verdict.raw_output.usage);
  const cost = getNumber(verdict.raw_output.cost_usd);
  const totalTokens = usage ? getNumber(usage.total_tokens) : null;

  return (
    <div className="judge-meta">
      <span>{verdict.tier} judge</span>
      {verdict.judge_model ? <span>{verdict.judge_model}</span> : null}
      {typeof verdict.confidence === "number" ? (
        <span>{Math.round(verdict.confidence * 100)}% confidence</span>
      ) : null}
      {totalTokens !== null ? <span>{totalTokens} tokens</span> : null}
      {cost !== null ? <span>${cost.toFixed(4)}</span> : null}
    </div>
  );
}

function getRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function getNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function DraftReviewCard({
  draft,
  approve,
  saveDisabled,
  reject,
  requestRevision,
  busy
}: {
  draft: PromotedEvalDraft;
  approve: () => void;
  saveDisabled: () => void;
  reject: () => void;
  requestRevision: () => void;
  busy: boolean;
}) {
  const canReview = draft.status === "pending";
  return (
    <section className="draft-card">
      <div className="result-title">
        <div>
          <h4>Promotion Draft #{draft.id}</h4>
          <span>{draft.report_path ?? "no report path"} · finding #{draft.finding_id}</span>
        </div>
        <Status value={draft.status} />
      </div>
      {canReview ? (
        <div className="draft-actions">
          <button onClick={approve} disabled={busy}>
            Approve & Enable
          </button>
          <button className="secondary" onClick={saveDisabled} disabled={busy}>
            Save Disabled
          </button>
          <button className="secondary" onClick={requestRevision} disabled={busy}>
            Needs Revision
          </button>
          <button className="secondary danger" onClick={reject} disabled={busy}>
            Reject
          </button>
        </div>
      ) : (
        <div className="draft-outcome">
          <p>{draftOutcomeText(draft)}</p>
          {draft.accepted_evaluation_id ? (
            <NavLink className="inline-link" to="/evaluations">View in Evaluations</NavLink>
          ) : null}
        </div>
      )}
      <details className="draft-detail">
        <summary>Proposed Evaluation JSON</summary>
        <pre>{JSON.stringify(draft.evaluation_json, null, 2)}</pre>
      </details>
    </section>
  );
}

function draftOutcomeText(draft: PromotedEvalDraft) {
  if (draft.status === "accepted" && draft.accepted_evaluation_id) {
    return `Accepted as evaluation #${draft.accepted_evaluation_id}. It is enabled for Regression Runs.`;
  }
  if (draft.status === "saved_disabled" && draft.accepted_evaluation_id) {
    return `Saved as disabled evaluation #${draft.accepted_evaluation_id}. It will not run until enabled later.`;
  }
  if (draft.status === "rejected") {
    return "Rejected. No regression evaluation was created.";
  }
  if (draft.status === "needs_revision") {
    return "Needs revision. No regression evaluation was created.";
  }
  return "This draft is no longer pending.";
}

function BudgetRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="budget-row">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function TimelineItem({ title, meta, active }: { title: string; meta: string; active?: boolean }) {
  return (
    <div className={active ? "timeline-item active" : "timeline-item"}>
      <i className="timeline-dot" />
      <strong>{title}</strong>
      <span>{meta}</span>
    </div>
  );
}

function RunSummaryBlock({ run }: { run: RunSummary }) {
  return (
    <div className="run-summary">
      <Status value={run.status} />
      <h3>Run #{run.id}</h3>
      <p>{run.target_name}</p>
      <div className="run-counts">
        <span>{run.passed_count} passed</span>
        <span>{run.failed_count} failed</span>
        <span>{run.error_count} errors</span>
      </div>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="empty-state">{text}</div>;
}

function groupEvaluations(evaluations: Evaluation[]) {
  return evaluations.reduce<Record<string, Evaluation[]>>((groups, evaluation) => {
    groups[evaluation.category_name] = groups[evaluation.category_name] ?? [];
    groups[evaluation.category_name].push(evaluation);
    return groups;
  }, {});
}

function elapsed(campaign: Campaign) {
  if (!campaign.started_at) {
    return 0;
  }
  const end = campaign.finished_at ? new Date(campaign.finished_at).getTime() : Date.now();
  return Math.max(Math.round((end - new Date(campaign.started_at).getTime()) / 1000), 0);
}

function targetSnapshot(campaign: Campaign) {
  return {
    target_id: campaign.target_id,
    name: campaign.target_name_snapshot,
    mode: campaign.target_mode_snapshot,
    base_url: campaign.target_base_url_snapshot,
    user_uuid: campaign.target_user_uuid_snapshot,
    patient_uuid: campaign.target_patient_uuid_snapshot
  };
}

function campaignLimits(campaign: Campaign) {
  return {
    focus_hint: campaign.focus_hint,
    llm_mode: campaign.llm_mode,
    max_attempts: campaign.max_attempts,
    max_wall_clock_seconds: campaign.max_wall_clock_seconds,
    max_cost_usd: campaign.max_cost_usd,
    spent_cost_usd: campaign.spent_cost_usd,
    stop_reason: campaign.stop_reason
  };
}

function attemptStatusTime(attempt: Attempt) {
  if (attempt.finished_at) {
    return formatDate(attempt.finished_at);
  }
  if (attempt.started_at) {
    return formatDate(attempt.started_at);
  }
  return formatDate(attempt.created_at);
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}
