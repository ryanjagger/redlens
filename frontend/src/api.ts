const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export type Target = {
  id: number;
  name: string;
  mode: "mock" | "live";
  base_url: string;
  internal_auth_env: string | null;
  bearer_token_env: string | null;
  fhir_base_url: string | null;
  patient_uuid: string | null;
  created_at: string;
};

export type TargetCreate = Omit<Target, "id" | "created_at">;

export type ThreatCategory = {
  id: number;
  key: string;
  name: string;
  description: string;
  evaluation_count: number;
  last_failed_count: number;
};

export type Evaluation = {
  id: number;
  key: string;
  name: string;
  category_id: number;
  category_key: string;
  category_name: string;
  endpoint: string;
  method: string;
  severity: string;
  expected_behavior: string;
  success_condition: string;
  enabled: boolean;
  last_status: string | null;
};

export type RunSummary = {
  id: number;
  target_id: number;
  target_name: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  total_count: number;
  passed_count: number;
  failed_count: number;
  error_count: number;
};

export type Result = {
  id: number;
  run_id: number;
  evaluation_id: number;
  evaluation_key: string;
  evaluation_name: string;
  category_key: string;
  category_name: string;
  endpoint: string;
  severity: string;
  status: string;
  request_json: unknown;
  response_json: unknown;
  status_code: number | null;
  latency_ms: number;
  judge_name: string;
  judge_reason: string;
  created_at: string;
};

export type RunDetail = RunSummary & {
  results: Result[];
};

export type Finding = {
  id: number;
  result_id: number;
  title: string;
  severity: string;
  category_key: string;
  endpoint: string;
  status: string;
  reproduction_steps: string;
  created_at: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    },
    ...init
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed with HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  targets: () => request<Target[]>("/api/targets"),
  createTarget: (payload: TargetCreate) =>
    request<Target>("/api/targets", { method: "POST", body: JSON.stringify(payload) }),
  categories: () => request<ThreatCategory[]>("/api/threat-categories"),
  evaluations: () => request<Evaluation[]>("/api/evaluations"),
  runs: () => request<RunSummary[]>("/api/runs"),
  run: (id: number) => request<RunDetail>(`/api/runs/${id}`),
  createRun: (targetId: number) =>
    request<RunSummary>("/api/runs", {
      method: "POST",
      body: JSON.stringify({ target_id: targetId })
    }),
  findings: () => request<Finding[]>("/api/findings"),
  promote: (resultId: number) =>
    request<Finding>(`/api/results/${resultId}/promote`, { method: "POST" })
};
