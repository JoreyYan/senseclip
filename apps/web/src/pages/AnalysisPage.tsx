import { useState, useEffect, useRef } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { API_BASE } from "@/constants";
import { useAuth } from "@/hooks/useAuth";

interface AtomEntity {
  name: string;
  type: string;
  description?: string;
}

interface Atom {
  id: string;
  atom_id: string;
  video_id: string;
  segment_id: string | null;
  start_ms: number;
  end_ms: number;
  merged_text: string;
  type: string | null;
  completeness: string | null;
}

interface SegmentMeta {
  id: string;
  title: string | null;
  summary: string | null;
  start_ms: number;
  end_ms: number;
  topics: string[] | null;
  key_entities: string[] | null;
}

interface SegmentGroup {
  segment_id: string;
  start_ms: number;
  end_ms: number;
  atom_count: number;
  atoms: Atom[];
  meta?: SegmentMeta;
}

interface VideoAnalysis {
  video: { id: string; title: string; duration_ms: number } | null;
  atoms: Atom[];
  segments: unknown[];
  entities: unknown[];
  atom_entities: Record<string, AtomEntity[]>;
  stats: { atom_count: number; segment_count: number; entity_count: number };
}

type AliasMap = Record<string, { canonical: string; type: string }>;

function msToTime(ms: number) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0") + ":" + String(sec).padStart(2, "0");
}

function entityDisplayName(name: string, aliasMap: AliasMap): string {
  const entry = aliasMap[name];
  if (entry?.canonical) return name + " → " + entry.canonical;
  return name;
}

interface EditModalProps {
  atom: Atom;
  entity: AtomEntity | null;
  mode: "alias" | "text";
  aliasMap: AliasMap;
  onClose: () => void;
  onAliasAdded: (alias: string, canonical: string, type: string) => void;
  onTextUpdated: (atomId: string, newText: string, videoId: string) => void;
}

function EditModal({ atom, entity, mode, aliasMap, onClose, onAliasAdded, onTextUpdated }: EditModalProps) {
  const [aliasInput, setAliasInput] = useState(entity?.name || "");
  const [canonicalInput, setCanonicalInput] = useState(
    entity ? (aliasMap[entity.name]?.canonical || "") : ""
  );
  const [textInput, setTextInput] = useState(atom.merged_text);
  const [entityHint, setEntityHint] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const submitAlias = async () => {
    const alias = aliasInput.trim();
    const canonical = canonicalInput.trim();
    if (!alias || !canonical) { setError("两项都必须填写"); return; }
    if (alias === canonical) { setError("别名和正确名称不能相同"); return; }
    setSaving(true); setError(null);
    try {
      const type = entity?.type || "person";
      const r = await fetch(API_BASE + "/api/aliases", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alias, canonical, type }),
      });
      if (!r.ok) throw new Error(await r.text());
      setSuccess("已保存：" + alias + " → " + canonical);
      onAliasAdded(alias, canonical, type);
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const submitText = async () => {
    const text = textInput.trim();
    if (!text) { setError("文本不能为空"); return; }
    setSaving(true); setError(null);
    try {
      const r = await fetch(API_BASE + "/api/atoms/" + atom.id + "/text", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ merged_text: text, entity_hint: entityHint }),
      });
      if (!r.ok) throw new Error(await r.text());
      setSuccess("已保存，实体已重新提取，群英图需手动 rebuild");
      onTextUpdated(atom.id, text, atom.video_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-slate-900 border border-slate-700 rounded-xl p-5 w-full max-w-md shadow-2xl" onClick={e => e.stopPropagation()}>
        {mode === "alias" ? (
          <>
            <h3 className="text-sm font-semibold text-slate-200 mb-3">加别名</h3>
            <p className="text-xs text-slate-400 mb-4">实体原始名 → 正确名称（写入别名表，不改 DB 数据）</p>
            <label className="text-xs text-slate-400 block mb-1">原始名（别名）</label>
            <input
              className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-slate-100 mb-3 focus:outline-none focus:border-blue-500"
              value={aliasInput}
              onChange={e => setAliasInput(e.target.value)}
              placeholder="远亲网"
            />
            <label className="text-xs text-slate-400 block mb-1">正确名称（canonical）</label>
            <input
              className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-slate-100 mb-4 focus:outline-none focus:border-blue-500"
              value={canonicalInput}
              onChange={e => setCanonicalInput(e.target.value)}
              placeholder="习远平"
              autoFocus
            />
          </>
        ) : (
          <>
            <h3 className="text-sm font-semibold text-slate-200 mb-1">改原子文本</h3>
            <p className="text-xs text-slate-400 mb-3">保存后自动重新向量化 + 重新提取实体。群英图需手动 rebuild。</p>
            <textarea
              className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-slate-100 mb-3 h-32 resize-none focus:outline-none focus:border-blue-500"
              value={textInput}
              onChange={e => setTextInput(e.target.value)}
              autoFocus
            />
            <label className="text-xs text-slate-500 block mb-1">实体提取补充说明（可选）</label>
            <input
              className="w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-slate-400 mb-4 focus:outline-none focus:border-blue-500"
              value={entityHint}
              onChange={e => setEntityHint(e.target.value)}
              placeholder="例如：某某是一个人名"
            />
          </>
        )}

        {error && <p className="text-xs text-red-400 mb-3">{error}</p>}
        {success && <p className="text-xs text-emerald-400 mb-3">{success}</p>}

        <div className="flex gap-2 justify-end">
          <button onClick={onClose} className="text-xs px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-300">取消</button>
          {!success && (
            <button
              onClick={mode === "alias" ? submitAlias : submitText}
              disabled={saving}
              className="text-xs px-3 py-1.5 rounded bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50"
            >
              {saving ? "保存中..." : "确认"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

interface EntityTagProps {
  entity: AtomEntity;
  atom: Atom;
  aliasMap: AliasMap;
  editable: boolean;
  onOpenModal: (atom: Atom, entity: AtomEntity, mode: "alias" | "text") => void;
}

function EntityTag({ entity, atom, aliasMap, editable, onOpenModal }: EntityTagProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const display = entityDisplayName(entity.name, aliasMap);
  const isAliased = display.includes(" → ");

  const colorClass =
    entity.type === "person"
      ? "bg-blue-900/40 text-blue-300 border-blue-800/40"
      : entity.type === "organization"
      ? "bg-purple-900/40 text-purple-300 border-purple-800/40"
      : "bg-slate-700/60 text-slate-400 border-slate-700/40";

  const icon = entity.type === "person" ? "👤" : entity.type === "organization" ? "🏛" : "";

  return (
    <div className="relative inline-block">
      <span
        className={"text-xs px-1.5 py-0.5 rounded border " + colorClass + (isAliased ? " italic" : "") + (editable ? " cursor-pointer hover:opacity-80" : "")}
        onClick={() => editable && setMenuOpen(v => !v)}
        title={isAliased ? "已别名：" + entity.name + " → " + aliasMap[entity.name]?.canonical : editable ? "点击编辑" : entity.name}
      >
        {icon} {display}
      </span>
      {editable && menuOpen && (
        <div
          className="absolute z-30 top-full left-0 mt-1 bg-slate-800 border border-slate-700 rounded-lg shadow-xl py-1 min-w-[140px]"
          onMouseLeave={() => setMenuOpen(false)}
        >
          <button
            className="block w-full text-left text-xs px-3 py-1.5 hover:bg-slate-700 text-slate-300"
            onClick={() => { setMenuOpen(false); onOpenModal(atom, entity, "alias"); }}
          >
            加别名
          </button>
          <button
            className="block w-full text-left text-xs px-3 py-1.5 hover:bg-slate-700 text-slate-300"
            onClick={() => { setMenuOpen(false); onOpenModal(atom, entity, "text"); }}
          >
            改原子文本
          </button>
        </div>
      )}
    </div>
  );
}

export default function AnalysisPage() {
  const [searchParams] = useSearchParams();
  const projectId = searchParams.get("projectId") || searchParams.get("videoId") || null;
  const targetAtomId = searchParams.get("atomId")
    || (window.location.hash.startsWith("#atom-") ? window.location.hash.slice(6) : null);

  const [data, setData] = useState<VideoAnalysis | null>(null);
  const { role } = useAuth();
  const canEdit = role === "owner" || role === "editor";
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expandedSegments, setExpandedSegments] = useState<Set<string>>(new Set());
  const [highlightedAtomId, setHighlightedAtomId] = useState<string | null>(null);
  const atomRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const [aliasMap, setAliasMap] = useState<AliasMap>({});
  const [modal, setModal] = useState<{ atom: Atom; entity: AtomEntity | null; mode: "alias" | "text" } | null>(null);
  const [resegmenting, setResegmenting] = useState(false);
  const [resegmentStatus, setResegmentStatus] = useState("");

  useEffect(() => {
    if (!projectId) return;
    setLoading(true);
    setError(null);
    fetch(API_BASE + "/api/videos/" + projectId + "/analysis")
      .then((r) => { if (!r.ok) throw new Error("请求失败 " + r.status); return r.json(); })
      .then((d: VideoAnalysis) => setData(d))
      .catch((e) => setError(e instanceof Error ? e.message : "加载失败"))
      .finally(() => setLoading(false));
  }, [projectId]);

  useEffect(() => {
    fetch(API_BASE + "/api/aliases")
      .then(r => r.ok ? r.json() : { aliases: {} })
      .then(d => setAliasMap(d.aliases || {}))
      .catch(() => {});
  }, []);

  const segments: SegmentGroup[] = (() => {
    if (!data?.atoms) return [];
    // Build segment metadata index from API segments
    const metaMap = new Map<string, SegmentMeta>();
    if (Array.isArray(data.segments)) {
      for (const s of data.segments as SegmentMeta[]) {
        if (s.id) metaMap.set(s.id, s);
      }
    }
    const map = new Map<string, SegmentGroup>();
    for (const atom of data.atoms) {
      const sid = atom.segment_id || "__unsegmented__";
      if (!map.has(sid)) {
        map.set(sid, { segment_id: sid, start_ms: atom.start_ms, end_ms: atom.end_ms, atom_count: 0, atoms: [], meta: metaMap.get(sid) });
      }
      const g = map.get(sid)!;
      g.atoms.push(atom);
      g.atom_count++;
      g.start_ms = Math.min(g.start_ms, atom.start_ms);
      g.end_ms = Math.max(g.end_ms, atom.end_ms);
    }
    return Array.from(map.values()).sort((a, b) => a.start_ms - b.start_ms);
  })();

  useEffect(() => {
    if (!targetAtomId || !data || segments.length === 0) return;
    for (const seg of segments) {
      if (seg.atoms.some((a) => a.id === targetAtomId)) {
        setExpandedSegments((prev) => new Set([...prev, seg.segment_id]));
        break;
      }
    }
    setHighlightedAtomId(targetAtomId);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetAtomId, data]);

  useEffect(() => {
    if (!highlightedAtomId) return;
    const el = atomRefs.current.get(highlightedAtomId);
    if (el) setTimeout(() => el.scrollIntoView({ behavior: "smooth", block: "center" }), 100);
  }, [highlightedAtomId, expandedSegments]);

  const toggleSegment = (sid: string) => {
    setExpandedSegments((prev) => {
      const next = new Set(prev);
      next.has(sid) ? next.delete(sid) : next.add(sid);
      return next;
    });
  };

  const expandAll = () => setExpandedSegments(new Set(segments.map((s) => s.segment_id)));
  const collapseAll = () => setExpandedSegments(new Set());

  const handleAliasAdded = (alias: string, canonical: string, type: string) => {
    setAliasMap(prev => ({ ...prev, [alias]: { canonical, type } }));
  };

  const handleResegment = async () => {
    if (!projectId || resegmenting) return;
    setResegmenting(true);
    setResegmentStatus("连接中...");
    setError(null);
    try {
      const r = await fetch(API_BASE + "/api/videos/" + projectId + "/resegment", { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      const reader = r.body?.getReader();
      const decoder = new TextDecoder();
      if (reader) {
        let buf = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split("\n");
          buf = lines.pop() || "";
          for (const line of lines) {
            if (!line.trim()) continue;
            try {
              const ev = JSON.parse(line);
              const pct = ev.progress != null ? ` (${ev.progress}%)` : "";
              setResegmentStatus(ev.message + pct);
              if (ev.step === "error") setError(ev.message);
            } catch { /* skip non-json */ }
          }
        }
      }
      // Reload analysis data
      const d = await fetch(API_BASE + "/api/videos/" + projectId + "/analysis").then(r2 => r2.json());
      setData(d);
      setExpandedSegments(new Set());
    } catch (e) {
      setError(e instanceof Error ? e.message : "重新分片失败");
    } finally {
      setResegmenting(false);
      setResegmentStatus("");
    }
  };

  const handleTextUpdated = (atomId: string, newText: string, videoId: string) => {
    setData(prev => {
      if (!prev) return prev;
      return { ...prev, atoms: prev.atoms.map(a => a.id === atomId ? { ...a, merged_text: newText } : a) };
    });
    if (videoId) {
      fetch(API_BASE + "/api/videos/" + videoId + "/analysis")
        .then(r => r.ok ? r.json() : null)
        .then((d: VideoAnalysis | null) => {
          if (!d) return;
          setData(prev => prev ? { ...prev, atom_entities: d.atom_entities } : prev);
        })
        .catch(() => {});
    }
  };

  return (
    <div className="flex-1 overflow-auto bg-slate-950 text-slate-100">
      {modal && (
        <EditModal
          atom={modal.atom}
          entity={modal.entity}
          mode={modal.mode}
          aliasMap={aliasMap}
          onClose={() => setModal(null)}
          onAliasAdded={handleAliasAdded}
          onTextUpdated={handleTextUpdated}
        />
      )}

      <header className="border-b border-slate-800 bg-slate-900/80 backdrop-blur-sm sticky top-0 z-40 px-6 py-3">
        <div className="max-w-5xl mx-auto flex items-center justify-between">
          <div>
            <div className="flex items-center gap-3">
              <Link to="/library" className="text-xs text-slate-400 hover:text-slate-200">← 视频库</Link>
              <h1 className="text-lg font-bold truncate max-w-xl">
                {data?.video?.title || projectId || "分析视图"}
              </h1>
            </div>
            {data && (
              <div className="text-xs text-slate-400 mt-0.5">
                {data.stats.atom_count} 原子 · {segments.length} 片段 · {data.stats.entity_count} 实体
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            {resegmentStatus && (
              <span className="text-xs text-indigo-400 animate-pulse">{resegmentStatus}</span>
            )}
            {segments.length > 0 && (
              <div className="flex gap-2">
                {canEdit && (
                  <button
                    onClick={handleResegment}
                    disabled={resegmenting}
                    className="text-xs px-3 py-1.5 rounded bg-indigo-700 hover:bg-indigo-600 text-white disabled:opacity-50"
                  >
                    {resegmenting ? "分片中..." : "重新分片"}
                  </button>
                )}
                <button onClick={expandAll} className="text-xs px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700">全部展开</button>
                <button onClick={collapseAll} className="text-xs px-3 py-1.5 rounded bg-slate-800 hover:bg-slate-700">全部折叠</button>
              </div>
            )}
          </div>
        </div>
      </header>

      <main className="max-w-5xl mx-auto px-6 py-6 space-y-3">
        {loading && <div className="text-sm text-slate-400">加载中...</div>}
        {error && <div className="text-sm text-red-400 bg-red-950/40 border border-red-900 rounded p-3">{error}</div>}
        {!loading && !error && !projectId && (
          <div className="text-sm text-slate-400">请在 URL 中提供 projectId 参数，例如 <code className="text-slate-300">?projectId=VIDEO_ID</code></div>
        )}

        {segments.map((seg) => {
          const expanded = expandedSegments.has(seg.segment_id);
          const segTitle = seg.meta?.title;
          const corePersons = seg.meta?.key_entities?.filter(Boolean) || [];
          const keywords = seg.meta?.topics?.filter(Boolean) || [];
          return (
            <div key={seg.segment_id} className="rounded-xl border border-slate-800 bg-slate-900/60 overflow-hidden">
              <button
                className="w-full flex items-center justify-between px-4 py-3 hover:bg-slate-800/60 transition-colors text-left"
                onClick={() => toggleSegment(seg.segment_id)}
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-3">
                    <span className="text-slate-400 text-sm w-4 shrink-0">{expanded ? "▾" : "▸"}</span>
                    <span className="text-sm font-medium text-slate-200 truncate">
                      {segTitle
                        ? segTitle
                        : seg.segment_id === "__unsegmented__"
                          ? "未分片"
                          : seg.segment_id.includes("_SEG_")
                            ? "SEG_" + seg.segment_id.split("_SEG_")[1]
                            : seg.segment_id}
                    </span>
                    <span className="text-xs text-slate-500 shrink-0">{msToTime(seg.start_ms)} – {msToTime(seg.end_ms)}</span>
                  </div>
                  {(corePersons.length > 0 || keywords.length > 0) && (
                    <div className="flex items-center gap-2 ml-7 mt-1 flex-wrap">
                      {corePersons.map((p, i) => (
                        <span key={"p" + i} className="text-xs px-1.5 py-0.5 rounded bg-blue-900/40 text-blue-300 border border-blue-800/30">👤 {p}</span>
                      ))}
                      {keywords.map((k, i) => (
                        <span key={"k" + i} className="text-xs px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">{k}</span>
                      ))}
                    </div>
                  )}
                </div>
                <span className="text-xs text-slate-500 shrink-0 ml-3">{seg.atom_count} 原子</span>
              </button>

              {expanded && (
                <div className="border-t border-slate-800 divide-y divide-slate-800/60">
                  {seg.meta?.summary && (
                    <div className="px-4 py-2 bg-slate-900/40 text-xs text-slate-400 italic">{seg.meta.summary}</div>
                  )}
                  {seg.atoms.map((atom) => {
                    const isHighlighted = atom.id === highlightedAtomId;
                    // eslint-disable-next-line @typescript-eslint/no-explicit-any
                    const rawEnts: any[] = (data?.atom_entities?.[atom.id] || []);
                    const atomEnts = rawEnts.map(e => ({
                      name: (e.entity_name || e.name || "") as string,
                      type: ((e.entity_type || e.type || "") as string).toLowerCase(),
                    })).filter(e => e.name);

                    return (
                      <div
                        key={atom.id}
                        id={"atom-" + atom.id}
                        ref={(el) => { if (el) atomRefs.current.set(atom.id, el); else atomRefs.current.delete(atom.id); }}
                        className={"px-4 py-3 transition-colors " + (isHighlighted ? "bg-emerald-950/60 border-l-2 border-emerald-500" : "hover:bg-slate-800/30")}
                      >
                        <div className="flex items-center gap-2 mb-1 flex-wrap">
                          <span className="text-xs font-mono text-slate-600">{atom.id?.includes("_") ? atom.id.split("_").slice(1).join("_") : atom.id}</span>
                          <span className="text-xs font-mono text-slate-500">{msToTime(atom.start_ms)}</span>
                          {atom.type && <span className="text-xs px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">{atom.type}</span>}
                          {atom.completeness && <span className="text-xs px-1.5 py-0.5 rounded bg-slate-800 text-slate-400">{atom.completeness}</span>}
                          {isHighlighted && <span className="text-xs px-1.5 py-0.5 rounded bg-emerald-700 text-emerald-100">定位中</span>}
                          {canEdit && (
                            <button
                              className="text-xs px-1.5 py-0.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-500 hover:text-slate-300 ml-auto"
                              onClick={() => setModal({ atom, entity: null, mode: "text" })}
                              title="改原子文本"
                            >
                              ✎
                            </button>
                          )}
                        </div>
                        <p className="text-sm text-slate-100 leading-relaxed">{atom.merged_text}</p>
                        {atomEnts.length > 0 && (
                          <div className="mt-2 flex flex-wrap gap-1">
                            {atomEnts.map((e, i) => (
                              <EntityTag
                                key={i}
                                entity={e}
                                atom={atom}
                                aliasMap={aliasMap}
                                editable={canEdit}
                                onOpenModal={(a, ent, mode) => setModal({ atom: a, entity: ent, mode })}
                              />
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}

        {!loading && segments.length === 0 && projectId && !error && (
          <div className="text-sm text-slate-400 text-center py-12">该视频暂无分析数据</div>
        )}
      </main>
    </div>
  );
}
