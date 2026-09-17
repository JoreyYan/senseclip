import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Newspaper, Loader2, X, ExternalLink } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { API_BASE } from "@/constants";
import type { ChatMessage, Citation } from "@/types/chat";

interface ReportItem {
  id: string;
  persona: string;
  week_start: string;
  title: string;
  label: string;
  avatar: string;
  updated_at: string;
}

interface SeriesPoint {
  name: string;
  date: string;
  value: number;
  unit?: string;
  chg_1w?: number;
  chg_4w?: number;
  mom_diff?: number;
  mom_pct?: number;
  yoy?: number;
}

interface ReportDetail extends ReportItem {
  content: string;
  citations: Array<{
    ref_num: number; atom_id: string; video_id: string; text: string; start_ms: number; end_ms?: number;
  }>;
  data_snapshot?: Record<string, SeriesPoint | unknown>;
}

// 报告页顶部展示的关键指标(按四层框架)
const KEY_SERIES: Array<{ id: string; layer: string }> = [
  { id: "VIX_CBOE", layer: "仓位与波动" },
  { id: "SKEW_CBOE", layer: "仓位与波动" },
  { id: "COT_ES_LEV_MONEY_NET", layer: "仓位与波动" },
  { id: "DGS10", layer: "利率" },
  { id: "DGS2", layer: "利率" },
  { id: "THREEFYTP10", layer: "利率" },
  { id: "PAYEMS", layer: "硬数据" },
  { id: "PCEPILFE", layer: "硬数据" },
  { id: "ICSA", layer: "硬数据" },
  { id: "BAMLH0A0HYM2", layer: "信用与跨市场" },
  { id: "DCOILWTICO", layer: "信用与跨市场" },
  { id: "DTWEXBGS", layer: "信用与跨市场" },
];

function fmtNum(v?: number, digits = 2) {
  if (v === undefined || v === null || Number.isNaN(v)) return "—";
  if (Math.abs(v) >= 1e5) return v.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
  return v.toFixed(digits);
}

function reading(p: SeriesPoint): { main: string; sub: string; up?: boolean } {
  if (p.mom_diff !== undefined) {
    // 人数类统一换算成「万人」:FRED 的非农单位是千人,ADP 是人
    const persons = p.unit === "千人" ? p.mom_diff * 1000 : p.unit === "人" ? p.mom_diff : null;
    const main = persons !== null
      ? `${persons >= 0 ? "+" : ""}${(persons / 1e4).toFixed(1)}万人`
      : `${p.mom_diff >= 0 ? "+" : ""}${fmtNum(p.mom_diff, 0)}`;
    return { main, sub: `环比 · ${p.date.slice(0, 7)}`, up: p.mom_diff >= 0 };
  }
  if (p.unit === "人" && p.value >= 1e4) return { main: `${(p.value / 1e4).toFixed(1)}万人`, sub: p.date };
  if (p.yoy !== undefined) return { main: `${fmtNum(p.yoy)}%`, sub: `同比 · ${p.date.slice(0, 7)}` };
  if (p.chg_1w !== undefined) return { main: fmtNum(p.value), sub: `周变化 ${p.chg_1w >= 0 ? "+" : ""}${fmtNum(p.chg_1w)}`, up: p.chg_1w >= 0 };
  return { main: fmtNum(p.value), sub: p.date };
}

export default function ReportsPage() {
  const [params, setParams] = useSearchParams();
  const [items, setItems] = useState<ReportItem[]>([]);
  const [loadingList, setLoadingList] = useState(true);
  const [detail, setDetail] = useState<ReportDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [panelCitations, setPanelCitations] = useState<Citation[] | null>(null);
  const selectedId = params.get("id");

  useEffect(() => {
    fetch(`${API_BASE}/api/reports`)
      .then((r) => (r.ok ? r.json() : { reports: [] }))
      .then((d) => {
        setItems(d.reports || []);
        if (!selectedId && d.reports?.length) setParams({ id: d.reports[0].id }, { replace: true });
      })
      .catch(() => {})
      .finally(() => setLoadingList(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    setLoadingDetail(true);
    setPanelCitations(null);
    fetch(`${API_BASE}/api/reports/${selectedId}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setDetail(d))
      .catch(() => setDetail(null))
      .finally(() => setLoadingDetail(false));
  }, [selectedId]);

  const message: ChatMessage | null = detail
    ? {
        id: detail.id,
        role: "assistant",
        content: detail.content.replace(/^\s*#\s[^\n]*\n+/, ""),
        avatar: detail.avatar,
        citations: (detail.citations || []).map((c) => ({
          index: c.ref_num, atom_id: c.atom_id, video_id: c.video_id, text: c.text,
          start_ms: c.start_ms, end_ms: c.end_ms,
        })),
        created_at: detail.updated_at,
      }
    : null;

  const snapshot = (detail?.data_snapshot || {}) as Record<string, SeriesPoint>;
  const keyCards = KEY_SERIES.map((k) => ({ ...k, p: snapshot[k.id] })).filter((k) => k.p && k.p.name);

  return (
    <div className="flex h-full min-h-0">
      {/* 期数列表 */}
      <aside className="hidden md:flex w-60 shrink-0 border-r border-border/30 flex-col">
        <div className="px-4 py-3 text-xs font-semibold text-muted-foreground flex items-center gap-1.5">
          <Newspaper className="h-3.5 w-3.5" /> Shanghao Jin 每周市场解读
        </div>
        <ScrollArea className="flex-1">
          <div className="px-2 pb-4 space-y-1">
            {loadingList && <div className="px-2 text-xs text-muted-foreground">加载中…</div>}
            {!loadingList && items.length === 0 && (
              <div className="px-2 text-xs text-muted-foreground">第一期正在生成,稍后再来</div>
            )}
            {items.map((it) => (
              <button
                key={it.id}
                onClick={() => setParams({ id: it.id })}
                className={`w-full text-left rounded-lg px-2.5 py-2 text-xs transition-colors ${
                  it.id === selectedId ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/50"
                }`}
              >
                <div className="flex items-center gap-2">
                  <img src={it.avatar} alt="" className="h-5 w-5 rounded-full object-cover" />
                  <span className="font-medium">{it.label}</span>
                </div>
                <div className="mt-0.5 pl-7">{Number(it.week_start.slice(5, 7))}月{Number(it.week_start.slice(8, 10))}日当周</div>
              </button>
            ))}
          </div>
        </ScrollArea>
      </aside>

      <ScrollArea className="flex-1 min-w-0">
        <div className="max-w-4xl mx-auto px-4 py-6 space-y-5">
          {/* 手机端期数选择 */}
          {items.length > 0 && (
            <select
              className="md:hidden w-full rounded-lg border border-border/60 bg-background px-3 py-2 text-sm"
              value={selectedId || ""}
              onChange={(e) => setParams({ id: e.target.value })}
            >
              {items.map((it) => (
                <option key={it.id} value={it.id}>{it.label} 每周市场解读 · {it.week_start} 当周</option>
              ))}
            </select>
          )}

          {loadingDetail && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" /> 加载解读…
            </div>
          )}

          {!loadingDetail && detail && message && (
            <>
              <header className="space-y-1.5">
                <div className="flex items-center gap-3">
                  <img src={detail.avatar} alt="" className="h-10 w-10 rounded-full object-cover" />
                  <div>
                    <h1 className="text-lg font-bold tracking-tight">{detail.title}</h1>
                    <p className="text-xs text-muted-foreground">
                      每周一更新 · 数据来自美联储 FRED、美国财政部、CFTC、CBOE · 生成于 {detail.updated_at.slice(0, 16).replace("T", " ")}
                    </p>
                  </div>
                </div>
                <p className="text-[11px] text-amber-700 bg-amber-50 border border-amber-200/60 rounded-lg px-3 py-1.5">
                  AI 依据 {detail.label} 公开视频的观点与思维框架模拟生成,不代表本人,不构成投资建议。
                </p>
              </header>

              {keyCards.length > 0 && (
                <section className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2">
                  {keyCards.map(({ id, layer, p }) => {
                    const r = reading(p);
                    return (
                      <div key={id} className="rounded-xl border border-border/50 bg-card px-3 py-2">
                        <div className="text-[10px] text-muted-foreground">{layer}</div>
                        <div className="text-xs font-medium truncate" title={p.name}>{p.name}</div>
                        <div className="text-base font-semibold tabular-nums">{r.main}</div>
                        <div className="text-[10px] text-muted-foreground tabular-nums">{r.sub}</div>
                      </div>
                    );
                  })}
                </section>
              )}

              <MessageBubble message={message} onCitationClick={(cits) => setPanelCitations(cits)} />
            </>
          )}

          {!loadingDetail && !detail && !loadingList && items.length === 0 && (
            <div className="rounded-2xl border border-dashed border-border/60 p-10 text-center text-sm text-muted-foreground">
              第一期周报正在生成,通常几分钟内完成。
            </div>
          )}
        </div>
      </ScrollArea>

      {/* 引用面板 */}
      {panelCitations && panelCitations.length > 0 && (
        <div className="w-[340px] shrink-0 border-l border-border bg-card flex flex-col">
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <span className="text-sm font-semibold">引用的视频片段</span>
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
                    <a
                      href={`https://www.youtube.com/watch?v=${c.video_id}&t=${Math.floor((c.start_ms || 0) / 1000)}s`}
                      target="_blank" rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-primary hover:underline"
                    >
                      看原视频 <ExternalLink className="h-3 w-3" />
                    </a>
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
