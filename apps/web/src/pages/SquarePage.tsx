import { useState, useEffect, useRef, useCallback } from "react";
import { Users, Loader2, Play, X, Plus, History } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { fetchPersonas, type PersonaInfo } from "@/services/chatApi";
import { API_BASE } from "@/constants";
import { supabase } from "@/lib/supabase";
import { useAuth } from "@/hooks/useAuth";
import type { ChatMessage, Citation } from "@/types/chat";
import { Link } from "react-router-dom";

interface Turn {
  persona: string;
  label: string;
  avatar?: string;
  round: number;
  content: string;
  streaming?: boolean;
  host?: boolean;
  citations: Array<{
    ref_num: number; atom_id: string; video_id: string; text: string;
    start_ms: number; end_ms?: number; url?: string;
  }>;
}

interface HistoryItem {
  id: string;
  topic: string;
  personas: string[];
  status: string;
  created_at: string;
}

const SPEAKER_COLORS = [
  "bg-amber-600", "bg-sky-600", "bg-violet-600", "bg-emerald-600", "bg-rose-600",
];

async function authHeaders(): Promise<Record<string, string>> {
  const { data: { session } } = await supabase.auth.getSession();
  const token = session?.access_token || "";
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export default function SquarePage() {
  const { user, billingEnabled } = useAuth();
  const [personas, setPersonas] = useState<PersonaInfo[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [topic, setTopic] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [progress, setProgress] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [currentTopic, setCurrentTopic] = useState("");
  const [error, setError] = useState("");
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [panelCitations, setPanelCitations] = useState<Citation[] | null>(null);
  const [rtStatus, setRtStatus] = useState<string>("");
  const [note, setNote] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const pollToken = useRef(0);

  useEffect(() => {
    fetchPersonas().then((ps) => {
      setPersonas(ps);
      setSelected(ps.map((p) => p.key));
    });
  }, []);

  const loadHistory = useCallback(async () => {
    if (!user) { setHistory([]); return; }
    try {
      const r = await fetch(`${API_BASE}/api/roundtable/list`, { headers: await authHeaders() });
      if (r.ok) setHistory((await r.json()).items || []);
    } catch { /* ignore */ }
  }, [user]);

  useEffect(() => { loadHistory(); }, [loadHistory]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, progress]);

  const colorOf = (key: string) =>
    SPEAKER_COLORS[Math.max(0, personas.findIndex((p) => p.key === key)) % SPEAKER_COLORS.length];

  /** 轮询一场圆桌直到结束;每 2 秒刷新,发言边生成边显示 */
  const pollRoundtable = useCallback(async (rid: string) => {
    const myToken = ++pollToken.current;
    setIsRunning(true);
    const deadline = Date.now() + 12 * 60 * 1000;
    try {
      while (Date.now() < deadline && pollToken.current === myToken) {
        let pr: Response;
        try {
          pr = await fetch(`${API_BASE}/api/roundtable/${rid}`);
        } catch { await new Promise((r) => setTimeout(r, 2000)); continue; }
        if (pr.ok) {
          const pd = await pr.json();
          setTurns(pd.turns || []);
          setCurrentTopic(pd.topic || "");
          setRtStatus(pd.status || "");
          setProgress(pd.status === "running" ? (pd.progress || "") : "");
          if (pd.status === "done") { loadHistory(); return; }
          if (pd.status === "error") throw new Error(pd.progress || "讨论失败");
        }
        await new Promise((r) => setTimeout(r, 2000));
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "出错了");
      setProgress("");
    } finally {
      if (pollToken.current === myToken) setIsRunning(false);
    }
  }, [loadHistory]);

  const start = async () => {
    if (!topic.trim() || isRunning || selected.length < 2) return;
    setError("");
    setTurns([]);
    setProgress("正在开场…");
    setIsRunning(true);
    try {
      const sub = await fetch(`${API_BASE}/api/roundtable/submit`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(await authHeaders()) },
        body: JSON.stringify({ topic: topic.trim(), personas: selected, rounds: 2 }),
      });
      if (sub.status === 429) throw new Error("今日免费次数已用完,登录后可继续");
      if (sub.status === 402) throw new Error("积分不足,圆桌需要 20 积分");
      if (!sub.ok) throw new Error("发起失败,请稍后重试");
      const { roundtable_id } = await sub.json();
      setActiveId(roundtable_id);
      setCurrentTopic(topic.trim());
      setTopic("");
      await pollRoundtable(roundtable_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "出错了");
      setProgress("");
      setIsRunning(false);
    }
  };

  const openHistory = async (item: HistoryItem) => {
    pollToken.current++;
    setActiveId(item.id);
    setError("");
    setTurns([]);
    setCurrentTopic(item.topic);
    try {
      const r = await fetch(`${API_BASE}/api/roundtable/${item.id}`);
      if (!r.ok) return;
      const pd = await r.json();
      setTurns(pd.turns || []);
      setRtStatus(pd.status || "");
      if (pd.status === "running") pollRoundtable(item.id);
      else { setProgress(""); setIsRunning(false); }
    } catch { /* ignore */ }
  };

  const newSession = () => {
    pollToken.current++;
    setActiveId(null);
    setTurns([]);
    setCurrentTopic("");
    setProgress("");
    setError("");
    setIsRunning(false);
    setRtStatus("");
    setNote("");
  };

  /** 在当前圆桌上再聊一轮(可带一句追问) */
  const continueRound = async () => {
    if (!activeId || isRunning) return;
    setError("");
    setIsRunning(true);
    try {
      const r = await fetch(`${API_BASE}/api/roundtable/${activeId}/continue`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(await authHeaders()) },
        body: JSON.stringify({ rounds: 1, note: note.trim() }),
      });
      if (r.status === 429) throw new Error("今日免费次数已用完,登录后可继续");
      if (r.status === 402) throw new Error("积分不足,续聊一轮需要 10 积分");
      if (r.status === 403) throw new Error("只能续聊自己发起的对谈");
      if (!r.ok) throw new Error("续聊失败,请稍后重试");
      setNote("");
      await pollRoundtable(activeId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "出错了");
      setIsRunning(false);
    }
  };

  const toMessage = (t: Turn, i: number): ChatMessage => ({
    id: `turn-${i}`,
    role: t.host ? "user" : "assistant",
    content: t.streaming ? `${t.content}▍` : t.content,
    avatar: t.avatar,
    citations: t.citations?.map((c) => ({
      index: c.ref_num, atom_id: c.atom_id, video_id: c.video_id, text: c.text,
      start_ms: c.start_ms, end_ms: c.end_ms, url: c.url || undefined,
    })),
    created_at: "",
  });

  return (
    <div className="flex h-full">
      {/* 历史侧栏(登录用户) */}
      {user && (
        <div className="w-60 shrink-0 border-r border-border/30 bg-background flex flex-col">
          <div className="p-3">
            <Button variant="outline" size="sm" className="w-full gap-1.5 text-xs" onClick={newSession}>
              <Plus className="h-3.5 w-3.5" /> 新对谈
            </Button>
          </div>
          <ScrollArea className="flex-1">
            <div className="px-2 pb-3 space-y-0.5">
              {history.length === 0 && (
                <p className="text-[11px] text-muted-foreground px-2 py-2 flex items-center gap-1">
                  <History className="h-3 w-3" /> 还没有历史对谈
                </p>
              )}
              {history.map((h) => (
                <button
                  key={h.id}
                  onClick={() => openHistory(h)}
                  className={`w-full text-left px-2 py-1.5 rounded-md text-xs truncate transition-colors ${
                    activeId === h.id ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                  }`}
                  title={h.topic}
                >
                  {h.status === "running" && <Loader2 className="inline h-3 w-3 mr-1 animate-spin" />}
                  {h.topic}
                </button>
              ))}
            </div>
          </ScrollArea>
        </div>
      )}

      <div className="flex-1 flex flex-col min-w-0">
        {/* 话题输入区 */}
        <div className="border-b border-border/30 bg-background p-4">
          <div className="max-w-4xl mx-auto space-y-2">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <Users className="h-4 w-4 text-primary" />
              广场 · 圆桌对谈
              <span className="text-[10px] font-normal text-muted-foreground">
                选择嘉宾,抛一个话题,他们各自查库亮观点、逐轮交锋{billingEnabled ? " · 20积分/场" : ""}
              </span>
            </div>
            <div className="flex items-center gap-1.5 flex-wrap">
              {personas.map((p, i) => (
                <button
                  key={p.key}
                  onClick={() =>
                    setSelected((s) =>
                      s.includes(p.key) ? s.filter((k) => k !== p.key) : [...s, p.key])}
                  className={`flex items-center gap-1.5 pl-1 pr-3 py-1 rounded-full text-xs font-medium transition-colors ${
                    selected.includes(p.key)
                      ? `${SPEAKER_COLORS[i % SPEAKER_COLORS.length]} text-white`
                      : "bg-accent/50 text-muted-foreground"
                  }`}
                >
                  <img src={p.avatar || "/avatar.png"} alt="" className="h-5 w-5 rounded-full object-cover" />
                  {p.label}
                </button>
              ))}
            </div>
            <div className="flex gap-2">
              <input
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && start()}
                placeholder="抛一个话题,比如:普通人现在该把钱放在哪?"
                disabled={isRunning}
                className="flex-1 rounded-xl border border-border/50 bg-card px-3 py-2 text-sm focus:outline-none focus:border-primary/50"
              />
              <Button onClick={start} disabled={isRunning || !topic.trim() || selected.length < 2} className="gap-1.5">
                {isRunning ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                开始
              </Button>
            </div>
            {error && <p className="text-xs text-red-500">{error}</p>}
          </div>
        </div>

        {/* 对谈内容 */}
        <ScrollArea className="flex-1">
          <div className="max-w-4xl mx-auto py-6 px-4 space-y-6">
            {currentTopic && (
              <div className="text-xs text-muted-foreground">话题:<span className="text-foreground font-medium">{currentTopic}</span></div>
            )}
            {turns.length === 0 && !isRunning && (
              <div className="text-center text-sm text-muted-foreground pt-16">
                还没有对谈。选好嘉宾、输入话题,点「开始」。
              </div>
            )}
            {turns.map((t, i) => (
              <div key={i}>
                <div className="flex items-center gap-2 mb-1.5">
                  <span className={`text-[10px] text-white px-2 py-0.5 rounded-full ${t.host ? "bg-zinc-500 ml-auto" : colorOf(t.persona)}`}>
                    {t.host ? "观众 · 插话" : `${t.label} · 第${t.round}轮`}
                  </span>
                </div>
                {(t.content || !t.streaming) ? (
                  <MessageBubble
                    message={toMessage(t, i)}
                    onCitationClick={(cits) => setPanelCitations(cits)}
                  />
                ) : (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground pl-10">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> 正在组织语言…
                  </div>
                )}
              </div>
            ))}
            {!isRunning && rtStatus === "done" && turns.length > 0 && activeId && (
              <div className="rounded-xl border border-border/50 bg-card p-3 space-y-2">
                <p className="text-xs text-muted-foreground">还想听他们聊下去?可以插一句追问,或者直接让他们再来一轮。</p>
                <div className="flex gap-2">
                  <input
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && continueRound()}
                    placeholder="可选:插一句话 / 追问 / 抛个新料…"
                    className="flex-1 rounded-lg border border-border/50 bg-background px-3 py-1.5 text-sm focus:outline-none focus:border-primary/50"
                  />
                  <Button size="sm" onClick={continueRound} className="gap-1.5">
                    <Play className="h-3.5 w-3.5" /> 再聊一轮{billingEnabled ? " · 10积分" : ""}
                  </Button>
                </div>
              </div>
            )}
            {isRunning && progress && turns.length === 0 && (
              <div className="flex items-center gap-2 text-xs text-muted-foreground animate-pulse">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                {progress}
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        </ScrollArea>
      </div>

      {/* 引用面板 */}
      {panelCitations && panelCitations.length > 0 && (
        <div className="w-[360px] shrink-0 border-l border-border bg-card flex flex-col">
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <span className="text-sm font-semibold">参考原子</span>
            <button onClick={() => setPanelCitations(null)} className="text-muted-foreground hover:text-foreground">
              <X className="h-4 w-4" />
            </button>
          </div>
          <ScrollArea className="flex-1">
            <div className="p-3 space-y-3">
              {panelCitations.map((c) => (
                <div key={c.index} className="p-3 rounded-lg border border-border/30 bg-secondary/30 text-xs">
                  <div className="flex items-center justify-between text-muted-foreground mb-1.5">
                    <span className="font-medium text-primary">[{c.index}]</span>
                    <span>{Math.round((c.start_ms || 0) / 1000)}s</span>
                  </div>
                  <div className="text-xs text-muted-foreground mb-1.5 flex items-center justify-between">
                    <span className="truncate">来源: {c.video_id}</span>
                    <Link
                      to={`/analysis?projectId=${c.video_id}&atomId=${c.atom_id}`}
                      className="text-primary hover:text-primary/80 underline shrink-0 ml-2"
                    >
                      查看原子
                    </Link>
                  </div>
                  <p className="text-foreground/80 leading-relaxed">{c.text}</p>
                </div>
              ))}
            </div>
          </ScrollArea>
        </div>
      )}
    </div>
  );
}
