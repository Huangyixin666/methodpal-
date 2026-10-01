import { createWorkspaceWriteQueue } from './workspaceWrites';
const API_BASE = (import.meta.env.VITE_API_BASE_URL || "/api").replace(/\/+$/, "");
export type AuthSession = { user: { id: string; username: string; email?: string | null; email_verified?: boolean }; csrf_token: string; expires_at: number };
let session: AuthSession | null = null;
export class ApiError extends Error { constructor(message: string, public status: number) { super(message); } }
export function getCurrentUserId(): string { return session?.user.id || ""; }
export function getCurrentUsername(): string { return session?.user.username || ""; }
const workspaceWrites = createWorkspaceWriteQueue(getCurrentUserId);

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const ownerAtStart = session?.user.id;
  const headers = new Headers(init?.headers || {});
  headers.set("X-MethodPal-Request", "1");
  if (session) headers.set("X-CSRF-Token", session.csrf_token);
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers, credentials: "include" });
  if (!path.startsWith("/auth/") && ownerAtStart !== session?.user.id) throw new Error("登录账户已变化，请重新打开工作区");
  if (!res.ok) {
    if (res.status === 401 && path !== "/auth/login") {
      session = null;
      window.dispatchEvent(new Event("methodpal:unauthorized"));
    }
    const error = await res.json().catch(() => ({}));
    throw new ApiError(typeof error.detail === "string" ? error.detail : `请求失败 (${res.status})`, res.status);
  }
  return res.json();
}
export async function restoreSession() {
  session = await request<AuthSession>("/auth/me");
  return session;
}
export async function authenticate(mode: "login" | "register", username: string, password: string, verification: any = {}) {
  session = await request<AuthSession>(`/auth/${mode}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username, password, ...verification }) });
  return session;
}
export function clearAuthSession() { session = null; }
export async function logout() {
  await request("/auth/logout", { method: "POST" });
  session = null;
  window.dispatchEvent(new Event("methodpal:unauthorized"));
  const channel = new BroadcastChannel("methodpal-auth");
  channel.postMessage("logout");
  channel.close();
}

export type VariableType = "ID型" | "连续型" | "整数型" | "计数型" | "分类型" | "二分类" | "有序型" | "文本型" | "未知";

export type CellIssue = {
  type: "missing" | "outlier_high" | "outlier_low";
  suggestion: string;
  label: string;
};

export type Cell = {
  value: string;
  issue?: CellIssue | null;
  fixed?: boolean | null;
};

export type DatasetProfileColumn = {
  name: string;
  type: VariableType | string;
  missing_count: number;
  missing_rate: number;
  unique_count: number;
  role_suggestion?: string | null;
  non_missing_count?: number;
  numeric_count?: number;
  numeric_rate?: number;
  mean?: number | null;
  std?: number | null;
  min?: number | null;
  q1?: number | null;
  median?: number | null;
  q3?: number | null;
  max?: number | null;
  top_values?: Array<{ value: string; count: number; rate: number }>;
};

export type DatasetProfile = {
  row_count: number;
  column_count: number;
  issue_count: number;
  missing_count: number;
  outlier_count: number;
  columns: DatasetProfileColumn[];
  summary?: string | null;
};

export type DatasetResponse = {
  data_fingerprint?: string | null;
  dataset_revision?: number;
  dataset_id: string;
  name: string;
  headers: string[];
  rows: string[][];
  cells: Cell[][];
  profile: DatasetProfile;
  message: string;
  preview_row_count?: number;
  full_row_count?: number;
  is_preview?: boolean;
  source_kind?: string | null;
  header_mode?: "row" | "none" | null;
  header_row?: number | null;
  can_reparse_header?: boolean;
};

export type DatasetRowsResponse = {
  dataset_id: string;
  offset: number;
  limit: number;
  total_rows: number;
  rows: string[][];
  cells: Cell[][];
  has_more: boolean;
};

export type DatasetCellChange = {
  row: number;
  col: number;
  value: string;
};

export type DatasetDeleteResponse = {
  ok: boolean;
  dataset_id: string;
  affected_project_ids?: string[];
  analysis_ids?: string[];
  deleted?: Record<string, number>;
};

export type DataTransformRequest = {
  operation: Record<string, any>;
  selected_variables?: any;
  create_new_dataset?: boolean;
};

export type DataTransformResponse = {
  ok: boolean;
  operation: Record<string, any>;
  message: string;
  summary?: Record<string, any>;
  preview_rows?: any[];
  warnings?: string[];
  dataset?: DatasetResponse;
  variable_selection_patch?: any;
};

export type VariableSelection = {
  dependent_variable: string;
  independent_variables: string[];
  control_variables: string[];
  mediator_variables?: string[];
  moderator_variables?: string[];
  group_variable?: string | null;
  variable_types?: Record<string, VariableType | string>;
  analysis_goal?: string;
  // Legacy aliases kept for report/explanation cache compatibility.
  dependent_variables?: string[];
  target?: string | null;
  independent_vars?: string[];
  predictors?: string[];
  control_vars?: string[];
  variable_roles?: Record<string, any>;
  roles?: Record<string, any>;
  types?: Record<string, VariableType | string>;
};

export type ProjectResponse = {
  project_id: string;
  name: string;
  description?: string | null;
  research_goal?: string | null;
  analysis_goal?: string | null;
  research_spec?: Record<string, any> | null;
  progress?: number;
  dataset_id?: string | null;
  analysis_id?: string | null;
  selected_variables?: VariableSelection | null;
  selected_model?: string | null;
  model_options?: Record<string, any>;
  data_review_status?: string;
  dataset_version?: number;
  ai_explanation?: any | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type MethodRoleRequirement = {
  role: string;
  label: string;
  min_count: number;
  max_count?: number | null;
  allowed_types: string[];
  description?: string;
};

export type MethodRequirement = {
  summary: string;
  required_roles: MethodRoleRequirement[];
  optional_roles: MethodRoleRequirement[];
  min_rows?: number;
};

export type MethodItem = {
  method_id: string;
  id: string;
  name: string;
  category: string;
  family: string;
  scene: string;
  score: number;
  reason: string;
  assumptions: string[];
  outputs: string[];
  tags: string[];
  requirement: MethodRequirement;
  default_options: Record<string, any>;
  implemented: boolean;
  enabled: boolean;
  selectable: boolean;
  availability: "ready" | "needs_configuration" | "incompatible" | "unsupported" | "selectable" | string;
  missing_roles: string[];
  validation_messages: string[];
  disabled_reason?: string | null;
};

export type MethodCatalogResponse = { methods: MethodItem[] };
export type MethodRecommendResponse = { recommended: MethodItem[]; message: string };



export type AgentContext = {
  project_id?: string | null;
  project_name?: string | null;
  research_goal?: string | null;
  analysis_goal?: string | null;
  research_spec?: Record<string, any> | null;
  dataset_id?: string | null;
  analysis_id?: string | null;
  page?: string;
  selected_variables?: any;
  selected_model?: string | null;
  model_options?: Record<string, any>;
  data_review_status?: string;
  dataset_version?: number;
  analysis_result?: any;
  dataset_summary?: any;
  variable_options?: any[];
};

export type AgentRequest = {
  message?: string;
  action?: "plan" | "recommend_variables" | "recommend_methods" | "execute" | string;
  mode?: "manual" | "assisted" | "autopilot" | string;
  thread_id?: string | null;
  answers?: Record<string, any>;
  context?: AgentContext;
  options?: Record<string, any>;
};

export type AgentQuestion = {
  id: string;
  question: string;
  kind: "text" | "single_choice" | "column_select" | string;
  reason?: string;
  required?: boolean;
  options?: Array<{ value: string; label: string; description?: string }>;
};

export type AgentResponse = {
  ok: boolean;
  source: string;
  action: string;
  message: string;
  llm_used?: boolean;
  conversation_intent?: string;
  knowledge_sources?: {id:string;title:string}[];
  dataset_summary?: any;
  variable_suggestions?: any[];
  method_suggestions?: any[];
  plan?: any[];
  actions?: any[];
  execution_result?: any | null;
  warnings?: string[];
  workflow_state?: any;
  allowed_actions?: string[];
  trace?: any[];
  status?: "waiting_for_user" | "planning" | "ready_to_execute" | "completed" | "failed" | "blocked" | "cancelled" | string;
  thread_id?: string | null;
  questions?: AgentQuestion[];
  research_spec?: Record<string, any>;
  analysis_plan?: Record<string, any>;
};

export type AgentChatMessage = {
  role: "user" | "ai" | "assistant" | "system" | string;
  content: string;
  time?: string | null;
  actions?: any[];
  meta?: Record<string, any>;
};

export type AgentChatHistoryResponse = {
  project_id: string;
  messages: AgentChatMessage[];
  updated_at?: string | null;
};

export async function createProject(payload: { name: string; description?: string; research_goal?: string }): Promise<ProjectResponse> {
  return request<ProjectResponse>("/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
}

export async function listProjects(): Promise<ProjectResponse[]> {
  return request<ProjectResponse[]>("/projects");
}

export async function getProject(projectId: string): Promise<ProjectResponse> {
  const owner = getCurrentUserId();
  await workspaceWrites.flush(projectId);
  if (owner !== getCurrentUserId()) throw new Error('登录账户已变化，请重新打开项目。');
  return request<ProjectResponse>(`/projects/${projectId}`);
}

export async function deleteProject(projectId: string): Promise<{ ok: boolean; project_id: string; deleted?: Record<string, number> }> {
  return request<{ ok: boolean; project_id: string; deleted?: Record<string, number> }>(`/projects/${projectId}`, {
    method: "DELETE",
  });
}

export async function updateProjectWorkspace(projectId: string, payload: Partial<{
  research_goal: string | null;
  analysis_goal: string | null;
  research_spec: Record<string, any> | null;
  dataset_id: string | null;
  analysis_id: string | null;
  selected_variables: VariableSelection | null;
  selected_model: string | null;
  model_options: Record<string, any>;
  data_review_status: string;
  dataset_version: number;
  ai_explanation: any | null;
  progress: number;
}>): Promise<ProjectResponse> {
  return workspaceWrites.enqueue(projectId, () => request<ProjectResponse>(`/projects/${projectId}/workspace`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  }));
}

export async function getUserStorage(): Promise<{ user_id: string; storage_dir: string }> {
  return request<{ user_id: string; storage_dir: string }>("/projects/__user_storage");
}

export async function getSampleDataset(): Promise<DatasetResponse> {
  return request<DatasetResponse>("/datasets/sample", { method: "POST" });
}

export async function uploadDataset(file: File, options: { header_mode?: "row" | "none"; header_row?: number } = {}): Promise<DatasetResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("header_mode", options.header_mode || "row");
  form.append("header_row", String(options.header_row || 1));
  return request<DatasetResponse>("/datasets/upload", { method: "POST", body: form });
}

export async function pasteDataset(payload: { name?: string; headers: string[]; rows: string[][] }): Promise<DatasetResponse>;
export async function pasteDataset(headers: string[], rows: string[][], name?: string): Promise<DatasetResponse>;
export async function pasteDataset(arg1: any, arg2?: string[][], arg3?: string): Promise<DatasetResponse> {
  const payload = Array.isArray(arg1) ? { headers: arg1, rows: arg2 || [], name: arg3 } : arg1;
  return request<DatasetResponse>("/datasets/paste", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
}

export async function updateDataset(datasetId: string, payload: { name?: string; headers: string[]; rows: string[][] }): Promise<DatasetResponse> {
  return request<DatasetResponse>(`/datasets/${datasetId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
}

export async function getDataset(datasetId: string): Promise<DatasetResponse> {
  return request<DatasetResponse>(`/datasets/${datasetId}`);
}

export async function getDatasetRows(datasetId: string, offset = 0, limit = 200): Promise<DatasetRowsResponse> {
  const query = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  return request<DatasetRowsResponse>(`/datasets/${datasetId}/rows?${query.toString()}`);
}

export async function patchDatasetCells(datasetId: string, changes: DatasetCellChange[]): Promise<DatasetResponse> {
  return request<DatasetResponse>(`/datasets/${datasetId}/cells`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify({ changes }),
  });
}


export async function reparseDatasetHeader(datasetId: string, payload: { header_mode: "row" | "none"; header_row?: number }): Promise<DatasetResponse> {
  return request<DatasetResponse>(`/datasets/${datasetId}/header/reparse`, { method: "POST", headers: { "Content-Type": "application/json; charset=utf-8" }, body: JSON.stringify({ header_mode: payload.header_mode, header_row: payload.header_row || 1 }) });
}

export async function insertDatasetRows(datasetId: string, payload: { index?: number | null; count?: number } = {}): Promise<DatasetResponse> {
  return request<DatasetResponse>(`/datasets/${datasetId}/rows/insert`, { method: "POST", headers: { "Content-Type": "application/json; charset=utf-8" }, body: JSON.stringify(payload) });
}

export async function deleteDatasetRows(datasetId: string, indices: number[]): Promise<DatasetResponse> {
  return request<DatasetResponse>(`/datasets/${datasetId}/rows/delete`, { method: "POST", headers: { "Content-Type": "application/json; charset=utf-8" }, body: JSON.stringify({ indices }) });
}

export async function insertDatasetColumn(datasetId: string, payload: { name: string; index?: number | null; default_value?: string }): Promise<DatasetResponse> {
  return request<DatasetResponse>(`/datasets/${datasetId}/columns/insert`, { method: "POST", headers: { "Content-Type": "application/json; charset=utf-8" }, body: JSON.stringify(payload) });
}

export async function deleteDatasetColumns(datasetId: string, indices: number[]): Promise<DatasetResponse> {
  return request<DatasetResponse>(`/datasets/${datasetId}/columns/delete`, { method: "POST", headers: { "Content-Type": "application/json; charset=utf-8" }, body: JSON.stringify({ indices }) });
}

export async function deleteDataset(datasetId: string): Promise<DatasetDeleteResponse> {
  return request<DatasetDeleteResponse>(`/datasets/${datasetId}`, {
    method: "DELETE",
  });
}

export async function getDatasetProfile(datasetId: string): Promise<DatasetProfile> {
  return request<DatasetProfile>(`/datasets/${datasetId}/profile`);
}

export async function suggestClean(datasetId: string): Promise<any> {
  return request<any>(`/datasets/${datasetId}/clean/suggest`, { method: "POST" });
}

export async function applyClean(datasetId: string, payload: any = {}): Promise<DatasetResponse> {
  return request<DatasetResponse>(`/datasets/${datasetId}/clean/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
}

export async function previewDatasetTransform(datasetId: string, payload: DataTransformRequest): Promise<DataTransformResponse> {
  return request<DataTransformResponse>(`/datasets/${datasetId}/transform/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
}

export async function applyDatasetTransform(datasetId: string, payload: DataTransformRequest): Promise<DataTransformResponse> {
  return request<DataTransformResponse>(`/datasets/${datasetId}/transform/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
}

export async function inferVariables(payload: any): Promise<any> {
  return request<any>("/variables/infer", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
}

export async function saveVariables(payload: any): Promise<any> {
  return workspaceWrites.enqueue(payload.project_id, () => request<any>("/variables/save", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  }));
}

export async function getMethodCatalog(): Promise<MethodCatalogResponse> {
  return request<MethodCatalogResponse>("/methods/catalog");
}

export async function recommendMethods(payload: any): Promise<MethodRecommendResponse> {
  return request<MethodRecommendResponse>("/methods/recommend", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
}

export async function runAnalysis(payload: any): Promise<any> {
  return runBackgroundJob('analysis', payload);
}

export async function getAnalysis(analysisId: string): Promise<any> {
  return request<any>(`/analysis/${analysisId}`);
}

export async function explain(payload: any): Promise<any> {
  return request<any>("/ai/explain", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
}

export async function chat(payload: any): Promise<any> {
  return request<any>("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
}

export async function generateReport(payload: any): Promise<any> {
  return runBackgroundJob('report', payload);
}

export async function getReport(reportId: string): Promise<any> {
  return request<any>(`/reports/${reportId}`);
}




export async function getAgentHistory(projectId: string): Promise<AgentChatHistoryResponse> {
  return request<AgentChatHistoryResponse>(`/agent/history/${projectId}`);
}

export async function saveAgentHistory(projectId: string, messages: AgentChatMessage[]): Promise<AgentChatHistoryResponse> {
  return request<AgentChatHistoryResponse>(`/agent/history/${projectId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify({ project_id: projectId, messages }),
  });
}

export async function clearAgentHistory(projectId: string): Promise<AgentChatHistoryResponse> {
  return request<AgentChatHistoryResponse>(`/agent/history/${projectId}`, {
    method: "DELETE",
  });
}


export async function getAgentState(projectId: string): Promise<any> {
  return request<any>(`/agent/state/${projectId}`);
}

export async function saveAgentState(projectId: string, payload: any): Promise<any> {
  return request<any>(`/agent/state/${projectId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
}

export async function clearAgentState(projectId: string): Promise<any> {
  return request<any>(`/agent/state/${projectId}`, { method: "DELETE" });
}

export async function agentMessage(payload: AgentRequest): Promise<AgentResponse> {
  if (payload.action !== 'chat' && (payload.mode === 'autopilot' || ['autopilot', 'resume', 'execute'].includes(payload.action || ''))) return runBackgroundJob('agent', payload);
  return request<AgentResponse>("/agent/message", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
}

export async function agentResume(payload: AgentRequest): Promise<AgentResponse> {
  return runBackgroundJob('agent', { ...payload, action: 'resume', mode: 'autopilot' });
}

export async function agentPlan(payload: AgentRequest): Promise<AgentResponse> {
  return request<AgentResponse>("/agent/plan", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
}

export async function agentRecommendVariables(payload: AgentRequest): Promise<AgentResponse> {
  return request<AgentResponse>("/agent/recommend-variables", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
}

export async function agentRecommendMethods(payload: AgentRequest): Promise<AgentResponse> {
  return request<AgentResponse>("/agent/recommend-methods", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
}

export async function agentExecute(payload: AgentRequest): Promise<AgentResponse> {
  return request<AgentResponse>("/agent/execute", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
}


export type ChartRecommendation = {
  chart_type: string;
  title: string;
  description?: string;
  reason?: string;
  priority?: number;
  available?: boolean;
  disabled_reason?: string | null;
};

export type ChartSpec = {
  chart_id: string;
  analysis_id: string;
  chart_type: string;
  title: string;
  subtitle?: string;
  x_key?: string | null;
  y_key?: string | null;
  x_label?: string | null;
  y_label?: string | null;
  layout?: string | null;
  data?: Array<Record<string, any>>;
  series?: Array<Record<string, any>>;
  matrix?: any[][];
  row_labels?: string[];
  column_labels?: string[];
  notes?: string[];
  meta?: Record<string, any>;
  artifact_status?: "current" | "stale" | "unknown" | string;
  stale_reason?: string | null;
  context_signature?: string | null;
};

export type ChartRecommendResponse = {
  ok: boolean;
  analysis_id?: string | null;
  dataset_id?: string | null;
  recommendations: ChartRecommendation[];
  warnings?: string[];
};

export type ChartGenerateResponse = {
  ok: boolean;
  chart: ChartSpec;
  warnings?: string[];
};

export async function recommendCharts(payload: { analysis_id?: string | null; dataset_id?: string | null; max_charts?: number; context?: any }): Promise<ChartRecommendResponse> {
  return request<ChartRecommendResponse>("/charts/recommend", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
}

export async function generateChart(payload: { analysis_id?: string | null; dataset_id?: string | null; chart_type: string; options?: any }): Promise<ChartGenerateResponse> {
  return request<ChartGenerateResponse>("/charts/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json; charset=utf-8" },
    body: JSON.stringify(payload),
  });
}

export async function getChart(chartId: string): Promise<ChartSpec> {
  return request<ChartSpec>(`/charts/${chartId}`);
}

export const api = {
  createProject,
  listProjects,
  getProject,
  deleteProject,
  updateProjectWorkspace,
  getUserStorage,
  getSampleDataset,
  uploadDataset,
  pasteDataset,
  updateDataset,
  getDataset,
  getDatasetRows,
  patchDatasetCells,
  deleteDataset,
  getDatasetProfile,
  suggestClean,
  applyClean,
  previewDatasetTransform,
  applyDatasetTransform,
  inferVariables,
  saveVariables,
  getMethodCatalog,
  recommendMethods,
  runAnalysis,
  getAnalysis,
  explain,
  getAgentHistory,
  saveAgentHistory,
  clearAgentHistory,
  getAgentState,
  saveAgentState,
  clearAgentState,
  agentMessage,
  agentResume,
  agentPlan,
  agentRecommendVariables,
  agentRecommendMethods,
  agentExecute,
  generateReport,
  getReport,
  recommendCharts,
  generateChart,
  getChart,
  async chat(payloadOrMessage: any, page: string = "home", context: any = undefined): Promise<any> {
    if (typeof payloadOrMessage === "string") return chat({ message: payloadOrMessage, page, context });
    return chat(payloadOrMessage);
  },
  async health(): Promise<any> {
    return request<any>("/health");
  },
};

export type BackgroundJob = { job_id: string; project_id?: string; kind: string; status: string; stage: string; progress: number; result?: any; error?: string; workspace_changed?: boolean };
export async function listJobs(projectId?: string): Promise<{items: BackgroundJob[]}> {
  return request(`/jobs${projectId ? '?project_id=' + encodeURIComponent(projectId) : ''}`);
}
export type AuthConfig = {captcha_required: boolean; email_enabled: boolean; email_required: boolean; registration_open: boolean};
export function getAuthConfig() {return request<AuthConfig>('/auth/config');}
export function getCaptcha() {return request<{captcha_id: string; image: string; expires_in: number}>('/auth/captcha');}
function authPost(path: string, body: any) {return request<any>(path, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});}
export function sendEmailCode(body: any) {return authPost('/auth/email/code', body);}
export function resetPassword(body: any) {return authPost('/auth/password/reset', body);}
export function bindEmail(body: any) {return authPost('/auth/email/bind', body);}
export async function changePassword(body: any) {
  const result = await authPost('/auth/password/change',body);
  clearAuthSession(); window.dispatchEvent(new Event('methodpal:unauthorized'));
  const channel = new BroadcastChannel('methodpal-auth'); channel.postMessage('logout'); channel.close();
  return result;
}
export function listSessions() {return request<{items:Array<{session_id:string;created_at:number;expires_at:number;current:boolean}>}>('/auth/sessions');}
export function revokeOtherSessions() {return request<any>('/auth/sessions/others',{method:'DELETE'});}
export async function getJob(id: string): Promise<BackgroundJob> { return request(`/jobs/${id}`); }
export async function cancelJob(id: string) { return request(`/jobs/${id}/cancel`, {method: 'POST'}); }
export async function retryJob(id: string) { return request(`/jobs/${id}/retry`, {method: 'POST'}); }
export async function runBackgroundJob(kind: string, payload: any): Promise<any> {
  const owner = getCurrentUserId();
  await workspaceWrites.flush(payload.project_id || payload.context?.project_id);
  if (getCurrentUserId() !== owner) throw new Error('登录账户已变化，请重新打开项目。');
  let job = await request<BackgroundJob>('/jobs', {method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({kind, payload, idempotency_key: crypto.randomUUID()})});
  window.dispatchEvent(new Event('methodpal:jobs'));
  while (['queued', 'running'].includes(job.status)) {
    await new Promise(resolve => setTimeout(resolve, 700));
    if (getCurrentUserId() !== owner) throw new Error('账户已切换；任务保存在原账户的历史记录中。');
    job = await getJob(job.job_id);
  }
  window.dispatchEvent(new Event('methodpal:jobs'));
  if (job.status !== 'completed') throw new Error(job.error || `任务${job.status === 'cancelled' ? '已取消' : '未完成'}，可在任务栏重试。`);
  if (job.workspace_changed) throw new Error('任务已经完成，但工作区配置在运行期间发生变化。结果保留在服务器历史中，没有覆盖你的新设置。请按当前配置重新运行。');
  return job.result;
}
export async function listCharts(projectId: string): Promise<{items: ChartSpec[]}> { return request(`/charts?project_id=${encodeURIComponent(projectId)}`); }
export async function listReports(projectId: string): Promise<{items: any[]}> { return request(`/reports?project_id=${encodeURIComponent(projectId)}`); }
export async function saveReportDraft(id: string, content: string) { return request(`/reports/${id}/draft`, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({content})}); }
export async function downloadArtifact(path: string, filename: string) {
  const owner = getCurrentUserId();
  const response = await fetch(`${API_BASE}${path}`, {credentials: 'include'});
  if (!response.ok) { const e = await response.json().catch(() => ({})); throw new Error(e.detail || '下载失败'); }
  const blob = await response.blob();
  if (owner !== getCurrentUserId()) throw new Error('账户已变化，请重新下载');
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a'); a.href = url; a.download = filename; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
