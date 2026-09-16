import { API_BASE } from "@/constants";
import { supabase } from "@/lib/supabase";

export interface Nomination {
  id: string;
  name: string;
  platform: "youtube" | "x";
  url: string;
  handle: string;
  reason: string;
  votes: number;
  status: "pending" | "accepted" | "building" | "live" | "rejected";
  status_label: string;
  voted: boolean;
  created_at: string;
}

export interface PersonaBrief {
  key: string;
  label: string;
  desc?: string;
  avatar: string;
}

export interface NominationBoard {
  nominations: Nomination[];
  coming_soon: PersonaBrief[];
  live: PersonaBrief[];
}

async function authHeaders(): Promise<Record<string, string>> {
  try {
    const { data: { session } } = await supabase.auth.getSession();
    return session?.access_token ? { Authorization: `Bearer ${session.access_token}` } : {};
  } catch {
    return {};
  }
}

export async function fetchNominations(): Promise<NominationBoard> {
  const res = await fetch(`${API_BASE}/api/nominations`, { headers: await authHeaders() });
  if (!res.ok) throw new Error("加载失败");
  return res.json();
}

export interface NominateResult {
  ok: boolean;
  merged: boolean;
  already_voted?: boolean;
  id: string;
  name?: string;
  votes?: number;
}

export async function submitNomination(name: string, url: string, reason: string): Promise<NominateResult> {
  const res = await fetch(`${API_BASE}/api/nominations`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ name, url, reason }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.detail || "提名失败,请稍后再试");
  return data;
}

export async function voteNomination(id: string): Promise<{ ok: boolean; already_voted?: boolean; votes?: number }> {
  const res = await fetch(`${API_BASE}/api/nominations/${id}/vote`, {
    method: "POST",
    headers: await authHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.detail || "投票失败");
  return data;
}
