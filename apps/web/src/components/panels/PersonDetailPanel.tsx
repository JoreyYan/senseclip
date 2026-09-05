import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { X, Send, Loader2 } from "lucide-react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { fetchPersonDetail, fetchPersonRelations, askPerson } from "@/services/api";
import { CATEGORY_ZH, CATEGORY_COLORS, RELATION_TYPE_ZH, RELATION_EDGE_STYLES } from "@/constants";
import type { CareerEvent, PersonGraph } from "@/types";

interface PersonDetailPanelProps {
  personId: string;
  personName: string;
  onClose: () => void;
  layout?: "side" | "bottom";
  allVideos?: Array<{ id: string; title: string }>;
}

const EVENT_TYPE_ZH: Record<string, string> = {
  appointment: "任命",
  promotion: "晋升",
  transfer: "调动",
  removal_or_investigation: "免职/调查",
};

function CareerTimeline({ events }: { events: CareerEvent[] }) {
  const sorted = [...events].sort((a, b) => {
    const yr = (t: string | null | undefined) => { const m = (t || "").match(/(\d{4})/); return m ? parseInt(m[1]) : 0; };
    return yr(a.time_start) - yr(b.time_start);
  });
  if (sorted.length === 0) return <p className="text-xs text-slate-400">暂无履历数据</p>;
  return (
    <div className="space-y-1 max-h-52 overflow-auto">
      {sorted.map((e, i) => (
        <div key={i} className="border-b border-slate-800 py-1 text-xs">
          <div className="text-slate-100">{e.organization || e.event_type} · {e.time_start || "时间不详"}</div>
          <div className="text-slate-400">{e.position}{e.power_level ? ` (${e.power_level})` : ""}</div>
        </div>
      ))}
    </div>
  );
}

function KGCareerTimeline({ events }: { events: CareerEvent[] }) {
  if (events.length === 0) return <p className="text-xs text-slate-400">暂无知识图谱履历</p>;
  return (
    <div className="space-y-1 max-h-52 overflow-auto">
      {events.map((c, i) => {
        const t = [c.time_start, c.time_end].filter(Boolean).join(" ~ ") || "时间不明";
        return (
          <div key={i} className="border-b border-slate-800/60 py-1 text-xs">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-slate-100 font-medium">{c.position}</span>
              {c.organization && <span className="text-slate-400">@ {c.organization}</span>}
              {c.power_level && <span className="px-1 rounded bg-sky-900/40 text-sky-300">{c.power_level}</span>}
            </div>
            <div className="text-slate-500 mt-0.5">{t} · {EVENT_TYPE_ZH[c.event_type] || c.event_type || "任职"}</div>
          </div>
        );
      })}
    </div>
  );
}

function RelationTypeStats({ relations }: { relations: PersonGraph["direct_relations"] }) {
  const counts: Record<string, number> = {};
  relations.forEach((r) => { counts[r.relation_type] = (counts[r.relation_type] || 0) + 1; });
  if (Object.keys(counts).length === 0) return <p className="text-xs text-slate-400">暂无数据</p>;
  return (
    <div className="space-y-1">
      {Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([type, count]) => {
        const color = RELATION_EDGE_STYLES[type]?.color || "#6b7280";
        return (
          <div key={type} className="flex items-center justify-between text-xs">
            <span style={{ color }}>{RELATION_TYPE_ZH[type] || type}</span>
            <span className="text-slate-400">{count}</span>
          </div>
        );
      })}
    </div>
  );
}

function PowerRelations({ relations, personId }: { relations: PersonGraph["direct_relations"]; personId: string }) {
  if (relations.length === 0) return <p className="text-xs text-slate-400">暂无有向关系数据</p>;
  return (
    <div className="space-y-1 max-h-64 overflow-auto">
      {[...relations].sort((a, b) => b.confidence - a.confidence).slice(0, 20).map((r) => {
        const isA = r.person_a_id === personId;
        const other = isA ? r.person_b_name : r.person_a_name;
        const arrow = r.direction === "bidirectional" ? "↔" : isA ? "→" : "←";
        const color = RELATION_EDGE_STYLES[r.relation_type]?.color || "#94a3b8";
        const typeLabel = RELATION_TYPE_ZH[r.relation_type] || r.relation_type;
        return (
          <div key={r.id} className="flex items-start gap-2 border-b border-slate-800/60 py-1 text-xs">
            <span className="shrink-0 font-bold text-sm leading-5" style={{ color }}>{arrow}</span>
            <span className="font-medium text-slate-100 shrink-0">{other}</span>
            <span className="px-1.5 py-0.5 rounded shrink-0" style={{ backgroundColor: color + "22", color }}>{typeLabel}</span>
            {r.time_context && <span className="text-slate-400 shrink-0">{r.time_context}</span>}
            {r.description && <span className="text-slate-400 flex-1 truncate">{r.description}</span>}
          </div>
        );
      })}
    </div>
  );
}

function IndirectRelations({ relations }: { relations: PersonGraph["indirect_relations"] }) {
  if (!relations || relations.length === 0) return null;
  return (
    <div className="mt-2 pt-2 border-t border-slate-800/40">
      <div className="text-[10px] text-slate-500 mb-1">关系圈（{relations.length} 条二跳）</div>
      <div className="space-y-0.5 max-h-28 overflow-auto">
        {relations.slice(0, 12).map((r, i) => (
          <div key={i} className="text-xs text-slate-400">{r.path || (r.source_name + " → " + r.bridge_name + " → " + r.target_name)}</div>
        ))}
      </div>
    </div>
  );
}

function EvidenceList({ evidence }: { evidence: Array<{ video_id: string; atom_id: string; text: string }> }) {
  const [page, setPage] = useState(0);
  const pageSize = 10;
  const totalPages = Math.ceil(evidence.length / pageSize);
  const paged = evidence.slice(page * pageSize, (page + 1) * pageSize);

  if (evidence.length === 0) return <p className="text-xs text-slate-400">暂无关联原子</p>;

  return (
    <div className="space-y-2">
      {paged.map((e, i) => (
        <div key={i} className="border-b border-slate-800 pb-2 text-xs space-y-1">
          <div className="text-slate-400">{e.video_id || ""}</div>
          <p className="text-slate-100 leading-relaxed">{e.text || ""}</p>
          {e.video_id && e.atom_id && (
            <Link
              to={"/analysis?projectId=" + e.video_id + "#atom-" + e.atom_id}
              className="text-emerald-400 hover:text-emerald-300"
            >
              在分析页查看 →
            </Link>
          )}
        </div>
      ))}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 pt-1">
          <Button variant="ghost" size="sm" className="h-6 text-[10px]" onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0}>上一页</Button>
          <span className="text-[10px] text-slate-400">{page + 1} / {totalPages}</span>
          <Button variant="ghost" size="sm" className="h-6 text-[10px]" onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))} disabled={page >= totalPages - 1}>下一页</Button>
        </div>
      )}
    </div>
  );
}

export function PersonDetailPanel({ personId, personName, onClose, layout = "side", allVideos = [] }: PersonDetailPanelProps) {
  const [question, setQuestion] = useState("");
  const [qaHistory, setQaHistory] = useState<Array<{ q: string; a: string }>>([]);

  const { data, isLoading } = useQuery({
    queryKey: ["personDetail", personId],
    queryFn: () => fetchPersonDetail(personId),
  });

  const { data: relationsData, isLoading: relationsLoading } = useQuery({
    queryKey: ["personRelations", personId],
    queryFn: () => fetchPersonRelations(personId),
  });

  const askMutation = useMutation({
    mutationFn: (q: string) => askPerson(personId, q),
    onSuccess: (res, q) => {
      setQaHistory((prev) => [...prev, { q, a: res.answer }]);
      setQuestion("");
    },
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!data) {
    return <p className="text-xs text-muted-foreground p-4">加载失败</p>;
  }

  // ── Left column content (always visible) ────────────────────────
  const leftCol = (
    <div className="space-y-3">
      {/* Name card */}
      <div className="space-y-1.5">
        <div className="flex items-center gap-2">
          <Badge className="text-[10px]" style={{ backgroundColor: CATEGORY_COLORS[data.person.category], color: "#fff" }}>
            {CATEGORY_ZH[data.person.category] || data.person.category}
          </Badge>
          <span className="text-xs text-muted-foreground">提及 {data.person.mention_count} 次 · {data.person.video_count} 个视频</span>
        </div>
        {/* Video sources */}
        {(() => {
          const videoIds = [...new Set((data.person.evidence || []).map((e) => e.video_id).filter(Boolean))];
          if (videoIds.length === 0) return null;
          return (
            <div className="flex flex-col gap-0.5">
              {videoIds.map((vid) => {
                const found = allVideos.find((v) => v.id === vid);
                const label = found?.title || vid;
                return (
                  <Link
                    key={vid}
                    to={"/analysis?projectId=" + vid}
                    className="text-[10px] text-blue-400 hover:text-blue-300 truncate"
                    title={label}
                  >
                    ▶ {label}
                  </Link>
                );
              })}
            </div>
          );
        })()}
      </div>

      {/* AI Summary */}
      {(data.ai_summary || (data as unknown as Record<string, string>).summary) && (
        <div className="rounded-lg border border-slate-700/50 bg-slate-900/40 p-3">
          <div className="text-[10px] uppercase tracking-wide text-slate-400 mb-2">人物简介</div>
          <div className="text-xs text-slate-200 leading-relaxed prose prose-sm max-w-none prose-invert">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{data.ai_summary || (data as unknown as Record<string, string>).summary || ""}</ReactMarkdown>
          </div>
        </div>
      )}

      {/* 关系类型统计 */}
      <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
        <div className="text-[10px] uppercase tracking-wide text-slate-400 mb-2">
          关系类型 {relationsLoading && <span className="text-slate-600">加载中...</span>}
        </div>
        {relationsData && <RelationTypeStats relations={relationsData.direct_relations} />}
      </div>

      {/* 履历事件 (from detail API) */}
      <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
        <div className="text-[10px] uppercase tracking-wide text-slate-400 mb-2">履历事件</div>
        <CareerTimeline events={data.career_events || []} />
      </div>
    </div>
  );

  // ── Right column content ─────────────────────────────────────────
  const rightCol = (
    <div className="space-y-3">
      {/* 权力关系脉络 */}
      {(relationsLoading || (relationsData && relationsData.direct_relations.length > 0)) && (
        <div className="rounded-lg border border-amber-800/40 bg-amber-950/10 p-3">
          <div className="text-[10px] uppercase tracking-wide text-amber-400 mb-2">
            权力关系脉络
            {relationsLoading && <span className="text-slate-500 ml-2">加载中...</span>}
            {relationsData && <span className="text-slate-500 ml-2">({relationsData.direct_relations.length} 条)</span>}
          </div>
          {relationsData && <PowerRelations relations={relationsData.direct_relations} personId={personId} />}
          {relationsData && <IndirectRelations relations={relationsData.indirect_relations} />}
        </div>
      )}

      {/* 职业履历（知识图谱） */}
      {relationsData && relationsData.career_events.length > 0 && (
        <div className="rounded-lg border border-sky-800/40 bg-sky-950/10 p-3">
          <div className="text-[10px] uppercase tracking-wide text-sky-400 mb-2">
            职业履历（知识图谱）<span className="text-slate-500 ml-1">({relationsData.career_events.length} 条)</span>
          </div>
          <KGCareerTimeline events={relationsData.career_events} />
        </div>
      )}

      {/* Q&A */}
      <div className="rounded-lg border border-indigo-800/50 bg-indigo-950/20 p-3">
        <div className="text-[10px] uppercase tracking-wide text-indigo-300 mb-2">向 AI 提问</div>
        {qaHistory.map((qa, i) => (
          <div key={i} className="mb-3 space-y-1">
            <p className="text-xs text-indigo-300 font-medium">Q: {qa.q}</p>
            <div className="text-xs text-slate-200 leading-relaxed">{qa.a}</div>
          </div>
        ))}
        <div className="flex gap-1.5">
          <Input
            placeholder={"关于 " + personName + " 的问题..."}
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !askMutation.isPending && askMutation.mutate(question.trim())}
            className="h-7 text-xs bg-slate-950 border-slate-700"
          />
          <Button size="icon" className="h-7 w-7 shrink-0" onClick={() => askMutation.mutate(question.trim())} disabled={askMutation.isPending || !question.trim()}>
            {askMutation.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Send className="h-3 w-3" />}
          </Button>
        </div>
      </div>

      {/* 关联原子 */}
      <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
        <div className="text-[10px] uppercase tracking-wide text-slate-400 mb-2">
          相关原子（{(data.person.evidence || []).length} 条）
        </div>
        <EvidenceList evidence={data.person.evidence || []} />
      </div>
    </div>
  );

  // ── bottom layout: side-by-side two columns ──────────────────────
  if (layout === "bottom") {
    return (
      <div className="h-full flex gap-0 overflow-hidden">
        <ScrollArea className="w-[280px] shrink-0 border-r border-border/30 h-full">
          <div className="p-3">{leftCol}</div>
        </ScrollArea>
        <ScrollArea className="flex-1 h-full">
          <div className="p-3">{rightCol}</div>
        </ScrollArea>
      </div>
    );
  }

  // ── side layout: single column ───────────────────────────────────
  return (
    <div className="w-[380px] h-full border-l border-border/50 bg-card flex flex-col">
      <div className="flex items-center justify-between p-4 border-b border-border/50">
        <h2 className="text-sm font-semibold">{personName}</h2>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </div>
      <ScrollArea className="flex-1">
        <div className="p-4 space-y-4">
          {leftCol}
          {rightCol}
        </div>
      </ScrollArea>
    </div>
  );
}
