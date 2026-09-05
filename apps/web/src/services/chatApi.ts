import { API_BASE } from "@/constants";
import { supabase } from "@/lib/supabase";
import type { ChatApiResponse, Citation, WebCitation } from "@/types/chat";

interface RawChatResponse {
  message?: string;
  reply?: string;
  citations?: Array<{
    ref_num?: number;
    index?: number;
    atom_id: string;
    video_id: string;
    text: string;
    start_ms?: number;
    end_ms?: number;
    similarity?: number;
    url?: string;
  }>;
  sources?: Citation[];
  log_id?: string | null;
}

export type ChatMode = string; // "standard" 或任意人格 key(lu/sun/动态注册的)

export interface PersonaInfo {
  key: string;
  label: string;
  desc: string;
  cost: number;
  avatar?: string;
}

let _personasCache: PersonaInfo[] | null = null;

export async function fetchPersonas(): Promise<PersonaInfo[]> {
  if (_personasCache) return _personasCache;
  try {
    const res = await fetch(`${API_BASE}/api/personas`);
    if (res.ok) {
      const d = await res.json();
      _personasCache = d.personas || [];
      return _personasCache!;
    }
  } catch { /* 网络失败用兜底 */ }
  return [
    { key: "lu", label: "鲁社长", desc: "", cost: 10 },
    { key: "sun", label: "孙割", desc: "", cost: 10 },
  ];
}

export interface ChatAttachment {
  type: "image" | "file";
  name: string;
  media_type: string;
  data: string; // base64, 无 data: 前缀
}

export async function sendChatMessage(
  messages: { role: string; content: string }[],
  videoIds?: string[] | null,
  mode: ChatMode = "standard",
  attachments?: ChatAttachment[],
  signal?: AbortSignal,
  onProgress?: (status: string) => void
): Promise<ChatApiResponse> {
  const { data: { session } } = await supabase.auth.getSession();
  const token = session?.access_token || "";
  const headers = {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
  const body = JSON.stringify({
    messages,
    video_ids: videoIds ?? null,
    attachments: attachments && attachments.length > 0 ? attachments : null,
    persona: mode !== "standard" ? mode : null,
  });

  let raw: RawChatResponse;
  if (mode !== "standard") {
    // 博主模式耗时 2-3 分钟,跨境长连接易被掐断:
    // 改为 提交任务(秒回 job_id)+ 每 3 秒短连接轮询,网络抖动自动续查
    const sub = await fetch(`${API_BASE}/api/consult/submit`, {
      method: "POST", headers, body, signal,
    });
    if (sub.status === 429) throw new Error("GUEST_LIMIT");
    if (sub.status === 402) throw new Error("INSUFFICIENT_CREDITS");
    if (!sub.ok) throw new Error("Failed to send message");
    const { job_id } = await sub.json();

    const deadline = Date.now() + 8 * 60 * 1000;
    let result: RawChatResponse | null = null;
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 3000));
      if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
      let pr: Response;
      try {
        pr = await fetch(`${API_BASE}/api/consult/poll?job_id=${job_id}`, { signal });
      } catch (e) {
        if (signal?.aborted) throw e;
        continue; // 单次轮询失败(网络抖动)不影响任务,继续查
      }
      if (!pr.ok) continue;
      const pd = await pr.json();
      if (pd.status === "done") { result = pd; break; }
      if (pd.status === "error") throw new Error(pd.detail || "Failed to send message");
      if (pd.progress) onProgress?.(pd.progress);
    }
    if (!result) throw new Error("Failed to send message");
    raw = result;
  } else {
    const res = await fetch(`${API_BASE}/api/chat`, {
      method: "POST", headers, body, signal,
    });
    if (res.status === 429) throw new Error("GUEST_LIMIT");
    if (res.status === 402) throw new Error("INSUFFICIENT_CREDITS");
    if (!res.ok) throw new Error("Failed to send message");
    raw = await res.json();
  }

  // Normalize backend field names: message→reply, citations→sources
  const reply = raw.reply ?? raw.message ?? "";
  const sources: Citation[] = (raw.sources ?? raw.citations ?? []).map((c, i) => ({
    index: c.ref_num ?? c.index ?? i,
    atom_id: c.atom_id,
    video_id: c.video_id,
    text: c.text,
    start_ms: c.start_ms ?? 0,
    end_ms: c.end_ms,
    similarity: c.similarity,
    url: c.url || undefined,
  }));
  const webSources: WebCitation[] = ((raw as any).web_citations ?? []).map((w: any, i: number) => ({
    index: w.ref_num ?? i + 1,
    title: w.title ?? "",
    url: w.url ?? "",
    content: w.content ?? "",
  }));

  return { reply, sources, webSources, logId: raw.log_id || undefined };
}

export async function reportError(payload: { question: string; error: string; mode: string }): Promise<void> {
  const { data: { session } } = await supabase.auth.getSession();
  const token = session?.access_token || "";
  await fetch(`${API_BASE}/api/report-error`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(payload),
  });
}

export async function sendFeedback(logId: string, rating: 1 | -1): Promise<void> {
  await fetch(`${API_BASE}/api/feedback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ log_id: logId, rating }),
  });
}
