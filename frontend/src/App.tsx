import { FormEvent, useMemo, useState } from "react";
import { NavLink, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Evaluation, Finding, Result, RunSummary, TargetCreate } from "./api";

const statusClass: Record<string, string> = {
  passed: "status status-pass",
  failed: "status status-fail",
  error: "status status-error",
  completed: "status status-complete",
  running: "status status-running",
  open: "status status-open"
};

export default function App() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">RL</span>
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
          <NavLink to="/runs">Runs</NavLink>
          <NavLink to="/findings">Findings</NavLink>
        </nav>
      </aside>
      <main className="content">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/targets" element={<Targets />} />
          <Route path="/threat-model" element={<ThreatModel />} />
          <Route path="/evaluations" element={<Evaluations />} />
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
  const runs = useQuery({ queryKey: ["runs"], queryFn: api.runs });
  const findings = useQuery({ queryKey: ["findings"], queryFn: api.findings });
  const latest = runs.data?.[0];
  const totalCoverage = categories.data?.reduce((sum, category) => sum + category.evaluation_count, 0) ?? 0;

  return (
    <section>
      <PageTitle title="Dashboard" subtitle="Current adversarial coverage and latest execution status." />
      <div className="metric-grid">
        <Metric label="Seeded evaluations" value={totalCoverage} />
        <Metric label="Latest failures" value={latest?.failed_count ?? 0} tone="fail" />
        <Metric label="Latest passes" value={latest?.passed_count ?? 0} tone="pass" />
        <Metric label="Open findings" value={findings.data?.length ?? 0} />
      </div>
      <div className="split">
        <Panel title="Coverage">
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
        <Panel title="Latest Run">
          {latest ? <RunSummaryBlock run={latest} /> : <EmptyState text="No runs yet." />}
        </Panel>
      </div>
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
                  base_url: event.target.value === "mock" ? "mock://openemr" : "http://127.0.0.1:8001"
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
                <td>{target.internal_auth_env ? `env:${target.internal_auth_env}` : "none"}</td>
              </tr>
            ))}
          </tbody>
        </table>
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
      <PageTitle title="Runs" subtitle="Execute and inspect adversarial evaluation runs." />
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

function FindingCard({ finding }: { finding: Finding }) {
  return (
    <article className="finding-card">
      <div className="result-title">
        <div>
          <h3>{finding.title}</h3>
          <span>{finding.category_key} · {finding.endpoint}</span>
        </div>
        <Status value={finding.status} />
      </div>
      <pre>{finding.reproduction_steps}</pre>
    </article>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="panel">
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

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}
