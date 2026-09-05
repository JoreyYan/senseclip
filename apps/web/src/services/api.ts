import { API_BASE } from "@/constants";
import type { NetworkResponse, DirectedGraphResponse, PersonDetail, PersonGraph, AskResponse } from "@/types";
import { supabase } from "@/lib/supabase";

async function getAuthHeaders(): Promise<Record<string, string>> {
  const { data: { session } } = await supabase.auth.getSession();
  const token = session?.access_token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function fetchNetwork(): Promise<NetworkResponse> {
  const res = await fetch(`${API_BASE}/api/person-network`);
  if (!res.ok) throw new Error("Failed to fetch network");
  return res.json();
}

export async function fetchDirectedGraph(limit = 500, minConfidence = 0.3): Promise<DirectedGraphResponse> {
  const res = await fetch(`${API_BASE}/api/person-relations/graph?limit=${limit}&min_confidence=${minConfidence}`);
  if (!res.ok) throw new Error("Failed to fetch directed graph");
  return res.json();
}

export async function fetchPersonDetail(personId: string): Promise<PersonDetail> {
  const res = await fetch(`${API_BASE}/api/person-network/person/${personId}`);
  if (!res.ok) throw new Error("Failed to fetch person detail");
  return res.json();
}

export async function fetchPersonRelations(personId: string): Promise<PersonGraph> {
  const res = await fetch(`${API_BASE}/api/person-relations/${personId}`);
  if (!res.ok) throw new Error("Failed to fetch person relations");
  return res.json();
}

export async function askPerson(personId: string, question: string): Promise<AskResponse> {
  const res = await fetch(`${API_BASE}/api/person-network/person/${personId}/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) throw new Error("Failed to ask question");
  return res.json();
}

export async function rebuildNetwork(useAi = false) {
  const res = await fetch(`${API_BASE}/api/person-network/rebuild`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ use_ai: useAi }),
  });
  if (!res.ok) throw new Error("Rebuild failed");
  return res.json();
}

// ── Merge / Alias ────────────────────────────────────────────────────────────

export async function mergePersons(
  sourceName: string,
  targetName: string,
  useAi = false,
): Promise<{ merged: boolean; source_name: string; target_name: string; alias_count: number; rebuild_job_id?: string }> {
  return apiJson("/api/person-network/merge", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source_name: sourceName, target_name: targetName, use_ai: useAi }),
  });
}

export async function renamePerson(
  personId: string,
  newName: string,
): Promise<{ success: boolean; rebuild_job_id?: string }> {
  return apiJson(`/api/person-network/person/${personId}/rename`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ new_name: newName }),
  });
}

export async function getRebuildStatus(jobId: string): Promise<{ status: string; error?: string | null }> {
  return apiJson(`/api/person-network/rebuild/status/${jobId}`);
}

/** 轮询后台群英图重建直到完成（最长 waitMs），完成返回 true */
export async function waitForRebuild(jobId: string, waitMs = 300000): Promise<boolean> {
  const start = Date.now();
  while (Date.now() - start < waitMs) {
    await new Promise((r) => setTimeout(r, 3000));
    try {
      const st = await getRebuildStatus(jobId);
      if (st.status === "done") return true;
      if (st.status === "error") return false;
    } catch { /* transient */ }
  }
  return false;
}

export async function fetchAliases(): Promise<Record<string, { canonical: string; type: string }>> {
  return apiJson("/api/aliases");
}

export async function createAlias(
  alias: string,
  canonical: string,
  type = "person",
): Promise<{ ok: boolean }> {
  return apiJson("/api/aliases", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ alias, canonical, type }),
  });
}

export async function deleteAlias(alias: string): Promise<{ ok: boolean }> {
  return apiJson(`/api/aliases/${encodeURIComponent(alias)}`, { method: "DELETE" });
}

// ── Ingest / Library / Settings helpers ──────────────────────────────────────

export interface JobStatus {
  id: string;
  youtube_url: string;
  video_id: string | null;
  status: "pending" | "downloading" | "uploading" | "transcribing" | "processing" | "completed" | "error";
  current_step: string | null;
  error_message: string | null;
  audio_url: string | null;
  created_at: string;
  updated_at: string;
}

export interface VideoAsset {
  youtube_url: string;
  video_id: string | null;
  audio_url: string | null;
  title: string | null;
  status:
    | "pending" | "audio_ready" | "transcribed" | "atomized" | "segmented"
    | "entities_extracted" | "topics_extracted" | "vectorized" | "processed" | "error";
  db_video_id: string | null;
  atom_count?: number;
  segment_count?: number;
  entity_count?: number;
  embedding_count?: number;
  analysis_ready?: boolean;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  recent_jobs: Array<{
    id: string;
    status: string;
    current_step: string | null;
    error_message: string | null;
    last_successful_step?: string | null;
    failed_step?: string | null;
    created_at: string;
  }>;
}

async function apiJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    if (res.status === 402) {
      throw new Error("INSUFFICIENT_CREDITS");
    }
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `请求失败 (${res.status})`);
  }
  return res.json();
}

export async function submitVideo(youtubeUrl: string): Promise<{ job_id: string; status: string }> {
  return apiJson("/api/ingest", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ youtube_url: youtubeUrl }),
  });
}

export async function getJobStatus(jobId: string): Promise<JobStatus> {
  return apiJson(`/api/ingest/${jobId}`);
}

export async function uploadAudio(file: File): Promise<{ job_id: string; status: string }> {
  const form = new FormData();
  form.append("file", file);
  return apiJson("/api/ingest/upload", { method: "POST", body: form });
}

export async function getLibrary(): Promise<{ assets: VideoAsset[] }> {
  return apiJson("/api/library");
}

export async function retryAsset(youtubeUrl: string): Promise<{ job_id: string }> {
  return apiJson("/api/library/retry", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ youtube_url: youtubeUrl }),
  });
}

export async function uploadCookies(file: File): Promise<{ success: boolean; message: string }> {
  const form = new FormData();
  form.append("file", file);
  return apiJson("/api/settings/cookies", { method: "POST", body: form });
}

export async function getCookiesStatus(): Promise<{ has_cookies: boolean; updated_at: string | null }> {
  return apiJson("/api/settings/cookies");
}

// ── Billing ──────────────────────────────────────────────────────────────────

export interface CreditTransaction {
  id: string;
  user_id: string;
  amount: number;
  type: "signup_bonus" | "purchase" | "admin_adjustment" | "ingest" | "chat" | "consult" | "subscription";
  description: string;
  stripe_session_id: string | null;
  job_id: string | null;
  created_at: string;
}

export interface CreditInfo {
  balance: number;
  transactions: CreditTransaction[];
  plan?: string | null;           // "pro" | "max" | null
  sub_period_end?: string | null; // 当前订阅周期截止
  lu_cost?: number;               // 鲁社长模式单次积分
}

export async function getCredits(): Promise<CreditInfo> {
  const authHeaders = await getAuthHeaders();
  return apiJson("/api/billing/credits", { headers: authHeaders });
}

export async function createCheckout(pack: string): Promise<{ checkout_url: string; session_id: string }> {
  const authHeaders = await getAuthHeaders();
  return apiJson("/api/billing/checkout", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({ pack }),
  });
}

export async function adjustCredits(email: string, amount: number, reason: string): Promise<{ email: string; new_balance: number }> {
  const authHeaders = await getAuthHeaders();
  return apiJson("/api/admin/adjust-credits", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({ email, amount, reason }),
  });
}
