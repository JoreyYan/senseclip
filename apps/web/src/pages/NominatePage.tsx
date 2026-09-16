import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Vote, Youtube, Twitter, ThumbsUp, Loader2, Sparkles, CheckCircle2, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  fetchNominations, submitNomination, voteNomination,
  type Nomination, type NominationBoard,
} from "@/services/nominationApi";

const STATUS_STYLE: Record<string, string> = {
  pending: "bg-zinc-100 text-zinc-600",
  accepted: "bg-sky-100 text-sky-700",
  building: "bg-amber-100 text-amber-700",
  live: "bg-emerald-100 text-emerald-700",
};

function PlatformIcon({ platform }: { platform: string }) {
  return platform === "x"
    ? <Twitter className="h-3.5 w-3.5 text-zinc-500" />
    : <Youtube className="h-3.5 w-3.5 text-red-500" />;
}

export default function NominatePage() {
  const [board, setBoard] = useState<NominationBoard | null>(null);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [votingId, setVotingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setBoard(await fetchNominations());
    } catch {
      /* 保留旧数据 */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const submit = async () => {
    if (!name.trim() || !url.trim() || submitting) return;
    setSubmitting(true);
    setMessage(null);
    try {
      const r = await submitNomination(name.trim(), url.trim(), reason.trim());
      if (r.merged) {
        setMessage({
          ok: true,
          text: r.already_voted
            ? `「${r.name}」已经有人提名过,你之前也投过票了`
            : `「${r.name}」已经有人提名过,已为 TA 加一票`,
        });
      } else {
        setMessage({ ok: true, text: "提名成功!票数越高越优先上线" });
      }
      setName(""); setUrl(""); setReason("");
      load();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "提名失败" });
    } finally {
      setSubmitting(false);
    }
  };

  const vote = async (n: Nomination) => {
    if (n.voted || votingId) return;
    setVotingId(n.id);
    setBoard((b) => b && {
      ...b,
      nominations: b.nominations.map((x) => x.id === n.id ? { ...x, voted: true, votes: x.votes + 1 } : x),
    });
    try {
      await voteNomination(n.id);
    } catch {
      load();
    } finally {
      setVotingId(null);
    }
  };

  const nominations = board?.nominations || [];
  const coming = board?.coming_soon || [];
  const live = board?.live || [];

  return (
    <ScrollArea className="flex-1">
      <div className="max-w-3xl mx-auto px-4 py-8 space-y-8">
        {/* 活动头图 */}
        <section className="relative overflow-hidden rounded-2xl border border-orange-200/60 bg-gradient-to-br from-orange-50 via-amber-50 to-rose-50 p-6">
          <div className="flex items-center gap-2 text-orange-600 text-xs font-semibold mb-2">
            <Vote className="h-4 w-4" /> 提名活动进行中
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground mb-2">你最想和哪位博主对话?</h1>
          <p className="text-sm text-muted-foreground leading-relaxed">
            贴上 TA 的 YouTube 频道或 X(推特)账号。我们会把 TA 的公开内容整理成记忆库,
            让 AI 以 TA 的口吻回答你的问题,句句可溯源。票数越高的越优先上线。
          </p>
          {live.length > 0 && (
            <div className="flex items-center gap-2 mt-4 flex-wrap">
              <span className="text-xs text-muted-foreground">已上线:</span>
              {live.map((p) => (
                <span key={p.key} className="inline-flex items-center gap-1.5 rounded-full bg-white/80 border border-border/50 pl-0.5 pr-2.5 py-0.5 text-xs">
                  <img src={p.avatar} alt="" className="h-5 w-5 rounded-full object-cover" />
                  {p.label}
                </span>
              ))}
            </div>
          )}
        </section>

        {/* 即将上线 */}
        {coming.length > 0 && (
          <section>
            <h2 className="flex items-center gap-1.5 text-sm font-semibold mb-3">
              <Sparkles className="h-4 w-4 text-amber-500" /> 即将上线
            </h2>
            <div className="grid gap-3 sm:grid-cols-2">
              {coming.map((p) => (
                <div key={p.key} className="flex items-center gap-3 rounded-xl border border-border/50 bg-card p-3">
                  <img src={p.avatar} alt="" className="h-11 w-11 rounded-full object-cover shrink-0" />
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{p.label}</span>
                      <span className="text-[10px] rounded-full bg-amber-100 text-amber-700 px-1.5 py-0.5">资料整理中</span>
                    </div>
                    <p className="text-xs text-muted-foreground line-clamp-2">{p.desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* 提名表单 */}
        <section className="rounded-2xl border border-border/50 bg-card p-5 space-y-3">
          <h2 className="text-sm font-semibold">提名一位博主</h2>
          <div className="grid gap-3 sm:grid-cols-[1fr_2fr]">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="博主名字"
              maxLength={40}
              className="rounded-lg border border-border/60 bg-background px-3 py-2 text-sm focus:outline-none focus:border-primary/50"
            />
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="youtube.com/@xxx 或 x.com/xxx"
              className="rounded-lg border border-border/60 bg-background px-3 py-2 text-sm focus:outline-none focus:border-primary/50"
            />
          </div>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="可选:为什么想和 TA 对话?最想问 TA 什么?"
            maxLength={300}
            rows={2}
            className="w-full resize-none rounded-lg border border-border/60 bg-background px-3 py-2 text-sm focus:outline-none focus:border-primary/50"
          />
          <div className="flex items-center justify-between gap-3 flex-wrap">
            <p className={`text-xs ${message ? (message.ok ? "text-emerald-600" : "text-rose-600") : "text-muted-foreground"}`}>
              {message ? message.text : "同一个频道被多人提名会自动合并计票"}
            </p>
            <Button onClick={submit} disabled={!name.trim() || !url.trim() || submitting} className="gap-1.5">
              {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Vote className="h-4 w-4" />}
              提交提名
            </Button>
          </div>
        </section>

        {/* 提名榜 */}
        <section>
          <h2 className="text-sm font-semibold mb-3">提名榜</h2>
          {loading ? (
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" /> 加载中…
            </div>
          ) : nominations.length === 0 ? (
            <p className="text-sm text-muted-foreground rounded-xl border border-dashed border-border/60 p-6 text-center">
              还没有人提名,来做第一个吧
            </p>
          ) : (
            <ol className="space-y-2">
              {nominations.map((n, i) => (
                <li key={n.id} className="flex items-center gap-3 rounded-xl border border-border/50 bg-card px-3 py-2.5">
                  <span className={`w-6 text-center text-sm font-bold ${i < 3 ? "text-orange-500" : "text-muted-foreground"}`}>
                    {i + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium truncate">{n.name}</span>
                      <a href={n.url} target="_blank" rel="noopener noreferrer"
                         className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
                        <PlatformIcon platform={n.platform} />
                        {n.platform === "x" ? `@${n.handle}` : n.handle.startsWith("UC") ? "频道" : `@${n.handle}`}
                      </a>
                      {n.status !== "pending" && (
                        <span className={`text-[10px] rounded-full px-1.5 py-0.5 ${STATUS_STYLE[n.status] || STATUS_STYLE.pending}`}>
                          {n.status_label}
                        </span>
                      )}
                    </div>
                    {n.reason && <p className="text-xs text-muted-foreground line-clamp-1 mt-0.5">{n.reason}</p>}
                  </div>
                  <button
                    onClick={() => vote(n)}
                    disabled={n.voted || votingId === n.id}
                    className={`flex items-center gap-1 rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors ${
                      n.voted
                        ? "border-orange-200 bg-orange-50 text-orange-600"
                        : "border-border/60 hover:border-orange-300 hover:text-orange-600"
                    }`}
                  >
                    {n.voted ? <CheckCircle2 className="h-3.5 w-3.5" /> : <ThumbsUp className="h-3.5 w-3.5" />}
                    {n.votes}
                  </button>
                </li>
              ))}
            </ol>
          )}
        </section>

        <p className="text-center text-xs text-muted-foreground">
          所有人格都是基于公开内容的 AI 模拟,不代表本人。
          <Link to="/" className="ml-1 inline-flex items-center gap-0.5 text-orange-600 hover:underline">
            去对话 <ArrowRight className="h-3 w-3" />
          </Link>
        </p>
      </div>
    </ScrollArea>
  );
}
