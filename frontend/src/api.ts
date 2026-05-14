const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export type Target = {
  id: number;
  name: string;
  mode: "mock" | "live";
  base_url: string;
  internal_auth_env: string | null;
  bearer_token_env: string | null;
  fhir_base_url: string | null;
  user_uuid: string | null;
  patient_uuid: string | null;
  created_at: string;
};

export type TargetCreate = Omit<Target, "id" | "created_at">;

export type TargetUpdate = TargetCreate;

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
  origin_finding_id: number | null;
  origin_finding_status: string | null;
  origin_report_path: string | null;
  origin_draft_id: number | null;
  created_at: string;
};

export type RunDetail = RunSummary & {
  results: Result[];
};

export type Finding = {
  id: number;
  result_id: number | null;
  title: string;
  severity: string;
  category_key: string;
  endpoint: string;
  status: string;
  reproduction_steps: string;
  linked_attempt_id: number | null;
  linked_evaluation_id: number | null;
  report_path: string | null;
  created_at: string;
};

export type FindingReport = {
  finding_id: number;
  report_path: string;
  content: string;
  artifact_id: number | null;
  storage_backend: string;
  sha256: string;
  mime_type: string;
  size_bytes: number;
  redaction_status: string;
  generation_metadata: Record<string, unknown>;
};

export type CampaignReport = {
  campaign_id: number;
  report_path: string;
  content: string;
  artifact_id: number | null;
  storage_backend: string;
  sha256: string;
  mime_type: string;
  size_bytes: number;
  redaction_status: string;
  generation_metadata: Record<string, unknown>;
};

export type Campaign = {
  id: number;
  target_id: number;
  target_name_snapshot: string;
  target_mode_snapshot: "mock" | "live";
  target_base_url_snapshot: string;
  target_user_uuid_snapshot: string | null;
  target_patient_uuid_snapshot: string | null;
  status: string;
  focus_hint: string | null;
  llm_mode: "deterministic" | "llm_assisted";
  max_attempts: number;
  max_wall_clock_seconds: number;
  max_cost_usd: number;
  spent_cost_usd: number;
  attempt_count: number;
  exploit_count: number;
  stop_reason: string | null;
  summary: string | null;
  langfuse: Record<string, unknown> | null;
  live_approved_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  last_activity_at: string;
  created_at: string;
};

export type Verdict = {
  id: number;
  attempt_id: number;
  tier: string;
  verdict: "safe" | "exploit" | "uncertain";
  severity: string | null;
  confidence: number | null;
  rationale: string;
  judge_model: string | null;
  judge_prompt_version: string | null;
  judge_temperature: number | null;
  raw_output: Record<string, unknown>;
  created_at: string;
};

export type PromotedEvalDraft = {
  id: number;
  finding_id: number;
  attempt_id: number;
  verdict_id: number;
  status: string;
  evaluation_json: Record<string, unknown>;
  report_path: string | null;
  review_notes: string | null;
  accepted_evaluation_id: number | null;
  created_at: string;
  reviewed_at: string | null;
};

export type Attempt = {
  id: number;
  campaign_id: number;
  target_id: number;
  status: string;
  focus_area: string;
  vector_key: string | null;
  attack_plan: Record<string, unknown>;
  transcript: Record<string, unknown>;
  request_json: Record<string, unknown>;
  response_json: Record<string, unknown>;
  execution_metadata: Record<string, unknown>;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  verdicts: Verdict[];
  promoted_eval_drafts: PromotedEvalDraft[];
};

export type CampaignDetail = Campaign & {
  attempts: Attempt[];
};

export type CampaignCreate = {
  target_id: number;
  focus_hint: string | null;
  max_attempts: number;
  max_wall_clock_seconds: number;
  max_cost_usd: number;
  llm_mode: "deterministic" | "llm_assisted";
};

export type DraftReviewPayload = {
  review_notes?: string | null;
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
  if (response.status === 204) {
    return undefined as T;
  }
  return response.json() as Promise<T>;
}

export const api = {
  targets: () => request<Target[]>("/api/targets"),
  createTarget: (payload: TargetCreate) =>
    request<Target>("/api/targets", { method: "POST", body: JSON.stringify(payload) }),
  updateTarget: (id: number, payload: TargetUpdate) =>
    request<Target>(`/api/targets/${id}`, { method: "PUT", body: JSON.stringify(payload) }),
  deleteTarget: (id: number) =>
    request<void>(`/api/targets/${id}`, { method: "DELETE" }),
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
  findingReport: (id: number) => request<FindingReport>(`/api/findings/${id}/report`),
  promote: (resultId: number) =>
    request<Finding>(`/api/results/${resultId}/promote`, { method: "POST" }),
  promotedEvalDrafts: () => request<PromotedEvalDraft[]>("/api/promoted-eval-drafts"),
  campaigns: () => request<Campaign[]>("/api/campaigns"),
  campaign: (id: number) => request<CampaignDetail>(`/api/campaigns/${id}`),
  campaignReport: (id: number) => request<CampaignReport>(`/api/campaigns/${id}/report`),
  createCampaign: (payload: CampaignCreate) =>
    request<Campaign>("/api/campaigns", { method: "POST", body: JSON.stringify(payload) }),
  startCampaign: (id: number) =>
    request<Campaign>(`/api/campaigns/${id}/start`, { method: "POST" }),
  approveLiveCampaign: (id: number) =>
    request<Campaign>(`/api/campaigns/${id}/approve-live`, { method: "POST" }),
  cancelCampaign: (id: number) =>
    request<Campaign>(`/api/campaigns/${id}/cancel`, { method: "POST" }),
  approvePromotedEvalDraft: (id: number, payload: DraftReviewPayload = {}) =>
    request<PromotedEvalDraft>(`/api/promoted-eval-drafts/${id}/approve`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  savePromotedEvalDraftDisabled: (id: number, payload: DraftReviewPayload = {}) =>
    request<PromotedEvalDraft>(`/api/promoted-eval-drafts/${id}/save-disabled`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  rejectPromotedEvalDraft: (id: number, payload: DraftReviewPayload = {}) =>
    request<PromotedEvalDraft>(`/api/promoted-eval-drafts/${id}/reject`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  requestPromotedEvalDraftRevision: (id: number, payload: DraftReviewPayload = {}) =>
    request<PromotedEvalDraft>(`/api/promoted-eval-drafts/${id}/needs-revision`, {
      method: "POST",
      body: JSON.stringify(payload)
    })
};
