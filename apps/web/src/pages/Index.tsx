import { useState, useCallback, useMemo, useRef } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { Loader2, Merge, Pencil } from "lucide-react";
import { Link } from "react-router-dom";
import { NetworkGraph } from "@/components/graph/NetworkGraph";
import { GraphToolbar } from "@/components/toolbar/GraphToolbar";
import { StatsBar } from "@/components/panels/StatsBar";
import { useAuth } from "@/hooks/useAuth";
import {
  fetchNetwork,
  fetchDirectedGraph,
  fetchPersonRelations,
  fetchPersonDetail,
  askPerson,
  rebuildNetwork,
  mergePersons,
  renamePerson,
  waitForRebuild,
} from "@/services/api";
import {
  CATEGORY_COLORS,
  CATEGORY_ZH,
  RELATION_TYPE_ZH,
  RELATION_EDGE_STYLES,
} from "@/constants";
import { toast } from "sonner";

const ATOMS_PER_PAGE = 10;

const EVENT_TYPE_ZH: Record<string, string> = {
  appointment: "任命",
  promotion: "晋升",
  transfer: "调动",
  removal_or_investigation: "免职/调查",
};

const Index = () => {
  const [searchQuery, setSearchQuery] = useState("");
  const [searchTarget, setSearchTarget] = useState<string | null>(null);
  const [categoryFilter, setCategoryFilter] = useState("all");
  const [relationFilter, setRelationFilter] = useState("all");
  const [showVideos, setShowVideos] = useState(false);
  const [minMention, setMinMention] = useState(2);

  const [egoPersonId, setEgoPersonId] = useState<string | null>(null);
  const [egoPersonName, setEgoPersonName] = useState<string>("");
  const [selectedPersonId, setSelectedPersonId] = useState<string | null>(null);
  const [selectedPersonName, setSelectedPersonName] = useState("");

  const [askQuestion, setAskQuestion] = useState("");
  const [askResult, setAskResult] = useState<{ answer: string; sources: Array<{ video_id: string; atom_id: string; text: string }> } | null>(null);
  const [atomPage, setAtomPage] = useState(0);

  const detailRef = useRef<HTMLDivElement>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [isRebuilding, setIsRebuilding] = useState(false);
  const { role } = useAuth();

  // Merge / Rename state
  const [mergeSource, setMergeSource] = useState<{ id: string; name: string } | null>(null);
  const [mergeTarget, setMergeTarget] = useState("");
  const [mergeFilter, setMergeFilter] = useState("");
  const [merging, setMerging] = useState(false);
  const [renameTarget, setRenameTarget] = useState<{ id: string; name: string } | null>(null);
  const [newName, setNewName] = useState("");

  const { data: networkData, isLoading: networkLoading, refetch: refetchNetwork } = useQuery({
    queryKey: ["network"],
    queryFn: fetchNetwork,
  });

  const { data: directedData } = useQuery({
    queryKey: ["directedGraph"],
    queryFn: () => fetchDirectedGraph(),
  });

  const { data: egoData } = useQuery({
    queryKey: ["egoRelations", egoPersonId],
    queryFn: () => fetchPersonRelations(egoPersonId!),
    enabled: !!egoPersonId,
  });

  const { data: personDetail, isLoading: personDetailLoading } = useQuery({
    queryKey: ["personDetail", selectedPersonId],
    queryFn: () => fetchPersonDetail(selectedPersonId!),
    enabled: !!selectedPersonId,
  });

  const { data: personGraph, isLoading: personGraphLoading } = useQuery({
    queryKey: ["personRelations", selectedPersonId],
    queryFn: () => fetchPersonRelations(selectedPersonId!),
    enabled: !!selectedPersonId,
  });

  const askMutation = useMutation({
    mutationFn: (q: string) => askPerson(selectedPersonId!, q),
    onSuccess: (res) => {
      setAskResult(res as { answer: string; sources: Array<{ video_id: string; atom_id: string; text: string }> });
      setAskQuestion("");
    },
  });

  const handleNodeClick = useCallback((personId: string, personName: string) => {
    setEgoPersonId(personId);
    setEgoPersonName(personName);
    setSelectedPersonId(personId);
    setSelectedPersonName(personName);
    setAskResult(null);
    setAtomPage(0);
    setTimeout(() => detailRef.current?.scrollIntoView({ behavior: "smooth" }), 100);
  }, []);

  const handleBackToGlobal = useCallback(() => {
    setEgoPersonId(null);
    setEgoPersonName("");
  }, []);

  const handleSearch = useCallback(() => {
    if (searchQuery.trim()) {
      setSearchTarget(searchQuery.trim());
      setTimeout(() => setSearchTarget(null), 100);
    }
  }, [searchQuery]);

  const handleRefresh = useCallback(() => {
    refetchNetwork();
  }, [refetchNetwork]);

  const handleQuickRebuild = useCallback(async () => {
    setIsRebuilding(true);
    setRebuilding(true);
    try {
      await rebuildNetwork(false);
      await refetchNetwork();
      toast.success("快速重建完成");
    } catch {
      toast.error("重建失败");
    }
    setIsRebuilding(false);
    setRebuilding(false);
  }, [refetchNetwork]);

  const handleAiRebuild = useCallback(async () => {
    setIsRebuilding(true);
    setRebuilding(true);
    try {
      await rebuildNetwork(true);
      toast.success("AI 重建已启动");
    } catch {
      toast.error("重建失败");
    }
    setIsRebuilding(false);
    setRebuilding(false);
  }, []);

  const handlePersonListClick = useCallback((personId: string, name: string) => {
    setSelectedPersonId(personId);
    setSelectedPersonName(name);
    setEgoPersonId(personId);
    setEgoPersonName(name);
    setAskResult(null);
    setAtomPage(0);
    setTimeout(() => detailRef.current?.scrollIntoView({ behavior: "smooth" }), 100);
  }, []);

  const handleMerge = useCallback(async () => {
    if (!mergeSource || !mergeTarget) return;
    setMerging(true);
    try {
      // 合并只写别名（秒回），群英图在服务端后台重建，不阻塞页面/问答
      const result = await mergePersons(mergeSource.name, mergeTarget);
      toast.success(`已合并：${mergeSource.name} → ${mergeTarget}，群英图后台重建中…`);
      setMergeSource(null);
      setMergeTarget("");
      setMergeFilter("");
      if (result.rebuild_job_id) {
        waitForRebuild(result.rebuild_job_id).then((ok) => {
          if (ok) toast.success("群英图已更新");
          refetchNetwork();
        });
      } else {
        refetchNetwork();
      }
    } catch (ex: any) {
      toast.error("合并失败：" + (ex?.message || "未知错误"));
    }
    setMerging(false);
  }, [mergeSource, mergeTarget, refetchNetwork]);

  const handleRename = useCallback(async () => {
    if (!renameTarget || !newName.trim()) return;
    try {
      const result = await renamePerson(renameTarget.id, newName.trim());
      toast.success(`已改名：${renameTarget.name} → ${newName.trim()}，群英图后台重建中…`);
      setRenameTarget(null);
      setNewName("");
      if (result.rebuild_job_id) {
        waitForRebuild(result.rebuild_job_id).then((ok) => {
          if (ok) toast.success("群英图已更新");
          refetchNetwork();
        });
      } else {
        refetchNetwork();
      }
    } catch (ex: any) {
      toast.error("改名失败：" + (ex?.message || "未知错误"));
    }
  }, [renameTarget, newName, refetchNetwork]);

  const isEgoMode = !!egoPersonId;

  const { egoDirectedNodes, egoDirectedEdges } = useMemo(() => {
    if (!egoPersonId) return { egoDirectedNodes: [], egoDirectedEdges: [] };

    const nodesMap = new Map<string, { id: string; name: string; category: string; mention_count: number }>();
    const edges: Array<{
      id: string; source: string; source_name: string; target: string; target_name: string;
      relation_type: string; direction: string; time_context: string; description: string; confidence: number;
    }> = [];

    const egoPersonInfo = networkData?.persons.find((p) => p.person_id === egoPersonId);
    nodesMap.set(egoPersonId, {
      id: egoPersonId,
      name: egoPersonName,
      category: egoPersonInfo?.category || "political_actor",
      mention_count: egoPersonInfo?.mention_count || 50,
    });

    if (egoData && egoData.direct_relations.length > 0) {
      egoData.direct_relations.forEach((r) => {
        const otherId = r.person_a_id === egoPersonId ? r.person_b_id : r.person_a_id;
        const otherName = r.person_a_id === egoPersonId ? r.person_b_name : r.person_a_name;
        const otherPerson = networkData?.persons.find((p) => p.person_id === otherId);
        if (!nodesMap.has(otherId)) {
          nodesMap.set(otherId, {
            id: otherId, name: otherName,
            category: otherPerson?.category || "political_actor",
            mention_count: otherPerson?.mention_count || 10,
          });
        }
        edges.push({
          id: r.id, source: r.person_a_id, source_name: r.person_a_name,
          target: r.person_b_id, target_name: r.person_b_name,
          relation_type: r.relation_type, direction: r.direction,
          time_context: r.time_context, description: r.description, confidence: r.confidence,
        });
      });
    }

    if (networkData?.relations) {
      networkData.relations.forEach((r) => {
        if (r.source_person_id !== egoPersonId && r.target_person_id !== egoPersonId) return;
        const hasDirected = edges.some(
          (e) =>
            (e.source === r.source_person_id && e.target === r.target_person_id) ||
            (e.source === r.target_person_id && e.target === r.source_person_id)
        );
        if (hasDirected) return;
        const otherId = r.source_person_id === egoPersonId ? r.target_person_id : r.source_person_id;
        const otherName = r.source_person_id === egoPersonId ? r.target_name : r.source_name;
        const otherPerson = networkData.persons?.find((p) => p.person_id === otherId);
        if (!nodesMap.has(otherId)) {
          nodesMap.set(otherId, {
            id: otherId, name: otherName,
            category: otherPerson?.category || "other",
            mention_count: otherPerson?.mention_count || r.strength,
          });
        }
        edges.push({
          id: "co_" + r.source_person_id + "_" + r.target_person_id,
          source: r.source_person_id, source_name: r.source_name,
          target: r.target_person_id, target_name: r.target_name,
          relation_type: r.relation_type, direction: "bidirectional",
          time_context: "", description: "共现 " + r.strength + " 次",
          confidence: Math.min(1, r.strength / 10),
        });
      });
    }

    return { egoDirectedNodes: Array.from(nodesMap.values()), egoDirectedEdges: edges };
  }, [egoPersonId, egoPersonName, egoData, networkData]);

  const relationIndex = useMemo(() => {
    if (!personGraph) return [];
    const map = new Map<string, number>();
    personGraph.direct_relations.forEach((r) => {
      const t = RELATION_TYPE_ZH[r.relation_type] ?? r.relation_type;
      map.set(t, (map.get(t) ?? 0) + 1);
    });
    return Array.from(map.entries()).map(([relationType, count]) => ({ relationType, count })).sort((a, b) => b.count - a.count);
  }, [personGraph]);

  const relatedAtoms = useMemo(() => {
    return personDetail?.person?.evidence || [];
  }, [personDetail]);

  const pagedAtoms = useMemo(() => {
    const start = atomPage * ATOMS_PER_PAGE;
    return { total: relatedAtoms.length, page: relatedAtoms.slice(start, start + ATOMS_PER_PAGE) };
  }, [relatedAtoms, atomPage]);

  return (
    <div className="flex flex-col flex-1 overflow-auto bg-background text-foreground">
      {/* First screen: toolbar + full-height graph */}
      <div className="flex flex-col shrink-0" style={{ height: "calc(100vh - 44px)" }}>
      {/* Top bar */}
      <div className="border-b border-border px-4 py-2 space-y-2 shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <h1 className="text-sm font-bold tracking-tight text-foreground">群英图</h1>
            {networkData && (
              <div className="relative">
                <button
                  onClick={() => setShowVideos((v) => !v)}
                  className="text-sm text-muted-foreground hover:text-foreground flex items-center gap-1"
                >
                  基于 {networkData.stats.video_count} 个视频构建
                  <span className="text-xs">{showVideos ? "▴" : "▾"}</span>
                </button>
                {showVideos && networkData.stats.videos?.length > 0 && (
                  <div className="absolute top-full left-0 mt-1 z-30 bg-card border border-border rounded-lg shadow-xl w-80">
                    {networkData.stats.videos.map((v) => (
                      <Link
                        key={v.id}
                        to={"/analysis?projectId=" + v.id}
                        className="flex items-center justify-between px-3 py-2 hover:bg-accent text-sm border-b border-border last:border-0"
                        onClick={() => setShowVideos(false)}
                      >
                        <span className="text-foreground truncate flex-1 mr-2">{v.title || v.id}</span>
                        <span className="text-xs text-primary shrink-0">分析 →</span>
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            <StatsBar stats={networkData?.stats || null} directedEdgeCount={directedData?.edges?.length || 0} />
            {role === "owner" && (
              <>
                <button
                  onClick={handleQuickRebuild}
                  disabled={rebuilding}
                  className="text-xs px-3 py-1.5 rounded bg-secondary hover:bg-secondary/80 disabled:opacity-50 text-secondary-foreground shrink-0"
                >
                  {rebuilding ? "重建中..." : "快速重建"}
                </button>
                <button
                  onClick={handleAiRebuild}
                  disabled={rebuilding}
                  className="text-xs px-3 py-1.5 rounded bg-primary hover:bg-primary/90 disabled:opacity-50 text-primary-foreground shrink-0"
                >
                  AI 重建
                </button>
              </>
            )}
          </div>
        </div>
        <div className="flex items-center gap-4">
          <GraphToolbar
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            onSearch={handleSearch}
            categoryFilter={categoryFilter}
            onCategoryFilterChange={setCategoryFilter}
            relationFilter={relationFilter}
            onRelationFilterChange={setRelationFilter}
            onRefresh={handleRefresh}
            isLoading={networkLoading}
            isRebuilding={isRebuilding}
            isEgoMode={isEgoMode}
            egoPersonName={egoPersonName}
            onBackToGlobal={handleBackToGlobal}
          />
          {!isEgoMode && (
            <div className="flex items-center gap-2 shrink-0 text-xs text-muted-foreground">
              <span>最少提及</span>
              <input
                type="range"
                min={1}
                max={20}
                value={minMention}
                onChange={(e) => setMinMention(Number(e.target.value))}
                className="w-20 h-1 accent-primary"
              />
              <span className="font-mono w-5 text-foreground">{minMention}</span>
            </div>
          )}
        </div>
      </div>

      {/* Graph area — fills rest of first screen */}
      <div className="flex-1 relative min-h-0">
        {networkLoading ? (
          <div className="flex items-center justify-center h-full">
            <Loader2 className="h-8 w-8 animate-spin text-slate-500" />
          </div>
        ) : networkData ? (
          <NetworkGraph
            persons={networkData.persons}
            relations={networkData.relations}
            directedNodes={isEgoMode ? egoDirectedNodes : (directedData?.nodes || [])}
            directedEdges={isEgoMode ? egoDirectedEdges : (directedData?.edges || [])}
            isEgoMode={isEgoMode}
            egoPersonId={egoPersonId || undefined}
            onNodeClick={handleNodeClick}
            searchTarget={searchTarget}
            categoryFilter={categoryFilter}
            relationFilter={relationFilter}
            minMention={minMention}
          />
        ) : null}
      </div>
      </div>{/* end first screen */}

      {/* Second screen: person list + detail */}
      <div ref={detailRef} className="border-t border-border">
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4 p-4">

          {/* Person list */}
          <div className="rounded-xl border border-border bg-card p-3">
            <h2 className="font-semibold mb-2 text-sm">人物列表</h2>
            <div className="space-y-0.5 text-sm max-h-[540px] overflow-auto">
              {(networkData?.persons ?? []).map((p) => (
                <div
                  key={p.person_id}
                  className={"flex items-center justify-between border-b border-border/50 py-1 px-1 rounded cursor-pointer group " + (selectedPersonId === p.person_id ? "bg-accent" : "hover:bg-accent/50")}
                  onClick={() => handlePersonListClick(p.person_id, p.name)}
                >
                  <span className="flex items-center gap-1.5 flex-1 min-w-0">
                    <span className="h-2 w-2 rounded-full shrink-0" style={{ backgroundColor: CATEGORY_COLORS[p.category] || "#6b7280" }} />
                    <span className="truncate">{p.name}</span>
                    <span className="text-xs text-muted-foreground shrink-0">({p.mention_count})</span>
                  </span>
                  {role === "owner" && (
                    <span className="hidden group-hover:flex items-center gap-0.5 shrink-0 ml-1">
                      <button
                        title="改名"
                        className="p-0.5 rounded hover:bg-secondary text-muted-foreground hover:text-foreground"
                        onClick={(e) => { e.stopPropagation(); setRenameTarget({ id: p.person_id, name: p.name }); setNewName(p.name); }}
                      >
                        <Pencil className="h-3 w-3" />
                      </button>
                      <button
                        title="合并到..."
                        className="p-0.5 rounded hover:bg-secondary text-muted-foreground hover:text-foreground"
                        onClick={(e) => { e.stopPropagation(); setMergeSource({ id: p.person_id, name: p.name }); setMergeTarget(""); setMergeFilter(""); }}
                      >
                        <Merge className="h-3 w-3" />
                      </button>
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Person detail */}
          <div className="rounded-xl border border-border bg-card p-3 xl:col-span-2 space-y-4">
            {!selectedPersonId && <div className="text-sm text-muted-foreground">点击人物查看详情</div>}
            {selectedPersonId && personDetailLoading && <div className="text-sm text-muted-foreground">加载中...</div>}

            {selectedPersonId && personDetail && (
              <>
                {/* Header */}
                <div>
                  <div className="flex items-center gap-2">
                    <h2 className="text-xl font-semibold">{personDetail.person.name}</h2>
                    {role === "owner" && (
                      <span className="flex items-center gap-1">
                        <button
                          title="改名"
                          className="p-1 rounded hover:bg-secondary text-muted-foreground hover:text-foreground"
                          onClick={() => { setRenameTarget({ id: selectedPersonId!, name: personDetail.person.name }); setNewName(personDetail.person.name); }}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          title="合并到..."
                          className="p-1 rounded hover:bg-secondary text-muted-foreground hover:text-foreground"
                          onClick={() => { setMergeSource({ id: selectedPersonId!, name: personDetail.person.name }); setMergeTarget(""); setMergeFilter(""); }}
                        >
                          <Merge className="h-3.5 w-3.5" />
                        </button>
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-muted-foreground mt-1 flex items-center gap-2 flex-wrap">
                    <span className="px-1.5 py-0.5 rounded text-[10px] text-white" style={{ backgroundColor: CATEGORY_COLORS[personDetail.person.category] }}>
                      {CATEGORY_ZH[personDetail.person.category] || personDetail.person.category}
                    </span>
                    提及 {personDetail.person.mention_count} 次 · {personDetail.person.video_count} 个视频
                    {/* Video source links */}
                    {networkData?.stats?.videos && (() => {
                      const vidIds = [...new Set((personDetail.person.evidence || []).map((e) => e.video_id).filter(Boolean))];
                      return vidIds.map((vid) => {
                        const found = networkData.stats.videos?.find((v) => v.id === vid);
                        return (
                          <Link key={vid} to={"/analysis?projectId=" + vid} className="text-primary hover:text-primary/80 underline text-[10px]">
                            {found?.title || vid}
                          </Link>
                        );
                      });
                    })()}
                  </div>
                </div>

                {/* Summary */}
                <div className="rounded-lg border border-border bg-secondary/30 p-3">
                  <div className="text-xs uppercase tracking-wide text-muted-foreground mb-1">人物简介</div>
                  <div className="text-sm text-foreground whitespace-pre-wrap">
                    {(personDetail as unknown as Record<string, string>).summary || personDetail.ai_summary || "暂无简介"}
                  </div>
                </div>

                {/* 关系类型 + 履历事件 */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div className="rounded-lg border border-border bg-secondary/30 p-3">
                    <div className="text-xs uppercase tracking-wide text-muted-foreground mb-2">关系类型</div>
                    <div className="space-y-1 text-sm">
                      {relationIndex.length === 0 && <div className="text-muted-foreground">暂无关系</div>}
                      {relationIndex.map((row) => {
                        const relType = Object.entries(RELATION_TYPE_ZH).find(([, v]) => v === row.relationType)?.[0];
                        const color = relType ? (RELATION_EDGE_STYLES[relType]?.color || "#6b7280") : "#6b7280";
                        return (
                          <div key={row.relationType} className="flex items-center justify-between border-b border-border/50 py-1">
                            <span className="font-medium" style={{ color }}>{row.relationType}</span>
                            <span className="text-muted-foreground">{row.count}</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  <div className="rounded-lg border border-border bg-secondary/30 p-3">
                    <div className="text-xs uppercase tracking-wide text-muted-foreground mb-2">履历事件</div>
                    <div className="space-y-1 text-sm max-h-[220px] overflow-auto">
                      {(personDetail.career_events || []).length === 0 && <div className="text-muted-foreground">暂无履历</div>}
                      {(personDetail.career_events || []).slice(0, 20).map((e, idx) => (
                        <div key={idx} className="border-b border-border/50 py-1">
                          <div className="text-foreground">{e.organization || e.event_type} · {e.time_start || "时间不详"}</div>
                          <div className="text-xs text-muted-foreground">{e.position}{e.power_level ? " (" + e.power_level + ")" : ""}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                {/* 权力关系脉络 + 职业履历（知识图谱） 并列 */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {(personGraphLoading || (personGraph && personGraph.direct_relations.length > 0)) && (
                  <div className="rounded-lg border border-amber-300/50 bg-amber-50/50 p-3">
                    <div className="text-xs uppercase tracking-wide text-amber-700 mb-2">
                      权力关系脉络
                      {personGraphLoading && <span className="text-muted-foreground ml-2">加载中...</span>}
                      {personGraph && <span className="text-muted-foreground ml-2">({personGraph.direct_relations.length} 条)</span>}
                    </div>
                    {personGraph && personGraph.direct_relations.length > 0 && (
                      <div className="space-y-1 max-h-[240px] overflow-auto">
                        {personGraph.direct_relations.map((r, idx) => {
                          const isA = r.person_a_id === selectedPersonId;
                          const other = isA ? r.person_b_name : r.person_a_name;
                          const arrow = r.direction === "bidirectional" ? "↔" : isA ? "→" : "←";
                          const typeLabel = RELATION_TYPE_ZH[r.relation_type] ?? r.relation_type;
                          const color = RELATION_EDGE_STYLES[r.relation_type]?.color ?? "#94a3b8";
                          return (
                            <div key={r.id ?? idx} className="flex items-start gap-2 border-b border-border/40 py-1 text-sm">
                              <span className="shrink-0 font-bold text-base leading-5" style={{ color }}>{arrow}</span>
                              <span className="font-medium text-foreground shrink-0">{other}</span>
                              <span className="text-xs px-1.5 py-0.5 rounded shrink-0" style={{ backgroundColor: color + "18", color }}>{typeLabel}</span>
                              {r.time_context && <span className="text-xs text-muted-foreground shrink-0">{r.time_context}</span>}
                              {r.description && <span className="text-xs text-muted-foreground flex-1 truncate">{r.description}</span>}
                            </div>
                          );
                        })}
                      </div>
                    )}
                    {personGraph && personGraph.indirect_relations.length > 0 && (
                      <div className="mt-2 pt-2 border-t border-border/40">
                        <div className="text-xs text-muted-foreground mb-1">关系圈（{personGraph.indirect_relations.length} 条二跳）</div>
                        <div className="space-y-0.5 max-h-[100px] overflow-auto">
                          {personGraph.indirect_relations.slice(0, 12).map((r: any, idx) => {
                            const a = r.person_a_name || r.source_name || "?";
                            const b = r.person_b_name || r.target_name || "?";
                            const rel = RELATION_TYPE_ZH[r.relation_type] || r.relation_type || "";
                            return (
                              <div key={idx} className="text-xs text-muted-foreground">
                                {r.path || `${a} ↔ ${b} [${rel}]`}
                              </div>
                            );
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* 职业履历（知识图谱） */}
                {personGraph && personGraph.career_events.length > 0 && (
                  <div className="rounded-lg border border-sky-300/50 bg-sky-50/50 p-3">
                    <div className="text-xs uppercase tracking-wide text-sky-700 mb-2">
                      职业履历（知识图谱）<span className="text-muted-foreground ml-1">({personGraph.career_events.length} 条)</span>
                    </div>
                    <div className="space-y-1 max-h-[220px] overflow-auto">
                      {personGraph.career_events.map((c, idx) => {
                        const t = [c.time_start, c.time_end].filter(Boolean).join(" ~ ") || "时间不明";
                        return (
                          <div key={idx} className="border-b border-border/40 py-1 text-sm">
                            <div className="flex items-center gap-2">
                              <span className="text-foreground font-medium">{c.position}</span>
                              {c.organization && <span className="text-xs text-muted-foreground">@ {c.organization}</span>}
                              {c.power_level && <span className="text-xs px-1 rounded bg-sky-100 text-sky-700">{c.power_level}</span>}
                            </div>
                            <div className="text-xs text-muted-foreground mt-0.5">{t} · {EVENT_TYPE_ZH[c.event_type] || c.event_type || "任职"}</div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
                </div>{/* end grid: 权力脉络 + 职业履历 */}

                {/* Q&A */}
                <div className="rounded-lg border border-indigo-200/60 bg-indigo-50/40 p-3">
                  <div className="text-xs uppercase tracking-wide text-indigo-600 mb-2">向 AI 提问</div>
                  <div className="flex gap-2">
                    <input
                      value={askQuestion}
                      onChange={(e) => setAskQuestion(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && !askMutation.isPending && askMutation.mutate(askQuestion.trim())}
                      placeholder={"关于 " + (personDetail.person.name) + " 的问题，例如：他是谁提拔的？"}
                      className="flex-1 bg-background border border-border rounded px-3 py-2 text-sm text-foreground placeholder-muted-foreground focus:outline-none focus:border-indigo-400"
                    />
                    <button
                      onClick={() => askMutation.mutate(askQuestion.trim())}
                      disabled={askMutation.isPending || !askQuestion.trim()}
                      className="px-4 py-2 rounded bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-sm font-medium text-white shrink-0"
                    >
                      {askMutation.isPending ? "思考中..." : "提问"}
                    </button>
                  </div>
                  {askResult && (
                    <div className="mt-3 space-y-2">
                      <div className="text-xs text-indigo-600 font-medium">AI 回答：</div>
                      <div className="text-sm text-foreground leading-relaxed whitespace-pre-wrap bg-card rounded p-3 border border-border">
                        {askResult.answer}
                      </div>
                      {askResult.sources?.length > 0 && (
                        <div>
                          <div className="text-xs text-muted-foreground mb-1">引用来源：</div>
                          <div className="space-y-1 max-h-40 overflow-auto">
                            {askResult.sources.map((src, i) => (
                              <div key={i} className="flex items-start gap-2 text-xs border-b border-border/50 pb-1">
                                <span className="text-indigo-500 shrink-0 font-mono">[{i + 1}]</span>
                                <span className="text-foreground/80 flex-1">{src.text || ""}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* 相关原子 */}
                <div className="rounded-lg border border-border bg-secondary/30 p-3">
                  <div className="flex items-center justify-between mb-2">
                    <div className="text-xs uppercase tracking-wide text-muted-foreground">相关原子（{pagedAtoms.total} 条）</div>
                    {pagedAtoms.total > ATOMS_PER_PAGE && (
                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <button disabled={atomPage === 0} onClick={() => setAtomPage((p) => p - 1)} className="px-2 py-0.5 rounded bg-secondary disabled:opacity-30 hover:bg-accent">←</button>
                        <span>{atomPage + 1} / {Math.ceil(pagedAtoms.total / ATOMS_PER_PAGE)}</span>
                        <button disabled={(atomPage + 1) * ATOMS_PER_PAGE >= pagedAtoms.total} onClick={() => setAtomPage((p) => p + 1)} className="px-2 py-0.5 rounded bg-secondary disabled:opacity-30 hover:bg-accent">→</button>
                      </div>
                    )}
                  </div>
                  <div className="space-y-2 text-sm">
                    {pagedAtoms.total === 0 && <div className="text-muted-foreground">暂无相关原子</div>}
                    {pagedAtoms.page.map((atom, idx) => (
                      <div key={atom.video_id + "-" + atom.atom_id + "-" + idx} className="border-b border-border/50 pb-2">
                        <div className="text-xs text-muted-foreground mb-1">{atom.video_id || ""}</div>
                        <div className="text-foreground">{atom.text || ""}</div>
                        {atom.video_id && atom.atom_id && (
                          <Link
                            to={"/analysis?projectId=" + atom.video_id + "#atom-" + atom.atom_id}
                            className="text-xs text-primary hover:text-primary/80"
                          >
                            在分析页查看 →
                          </Link>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
      {/* ── Merge Dialog ── */}
      {mergeSource && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setMergeSource(null)}>
          <div className="bg-card border border-border rounded-xl shadow-xl w-[420px] max-h-[80vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
            <div className="p-4 border-b border-border">
              <h3 className="font-semibold text-sm">合并人物</h3>
              <p className="text-xs text-muted-foreground mt-1">
                将 <span className="font-medium text-foreground">{mergeSource.name}</span> 合并到目标人物，合并后所有提及将归入目标人物。
              </p>
            </div>
            <div className="p-4 space-y-3 flex-1 overflow-auto">
              <input
                placeholder="搜索目标人物..."
                value={mergeFilter}
                onChange={(e) => setMergeFilter(e.target.value)}
                className="w-full bg-background border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-primary"
                autoFocus
              />
              <div className="space-y-0.5 max-h-[300px] overflow-auto">
                {(networkData?.persons ?? [])
                  .filter((p) => p.person_id !== mergeSource.id && (!mergeFilter || p.name.includes(mergeFilter)))
                  .map((p) => (
                    <div
                      key={p.person_id}
                      className={"flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer text-sm " + (mergeTarget === p.name ? "bg-primary/10 border border-primary/30" : "hover:bg-accent")}
                      onClick={() => setMergeTarget(p.name)}
                    >
                      <span className="h-2 w-2 rounded-full shrink-0" style={{ backgroundColor: CATEGORY_COLORS[p.category] || "#6b7280" }} />
                      <span>{p.name}</span>
                      <span className="text-xs text-muted-foreground">({p.mention_count})</span>
                    </div>
                  ))}
              </div>
              {mergeTarget && (
                <div className="text-xs text-muted-foreground bg-secondary/50 rounded p-2">
                  合并方向：<span className="text-foreground font-medium">{mergeSource.name}</span>
                  <span className="mx-1">→</span>
                  <span className="text-foreground font-medium">{mergeTarget}</span>
                  <br />
                  <span className="text-orange-600">合并后将触发群英图重建，{mergeSource.name} 将作为 {mergeTarget} 的别名。</span>
                </div>
              )}
            </div>
            <div className="p-4 border-t border-border flex justify-end gap-2">
              <button
                onClick={() => setMergeSource(null)}
                className="px-3 py-1.5 text-xs rounded bg-secondary hover:bg-secondary/80 text-secondary-foreground"
              >
                取消
              </button>
              <button
                onClick={handleMerge}
                disabled={!mergeTarget || merging}
                className="px-3 py-1.5 text-xs rounded bg-primary hover:bg-primary/90 text-primary-foreground disabled:opacity-40"
              >
                {merging ? "合并中..." : "确认合并"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ── Rename Dialog ── */}
      {renameTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setRenameTarget(null)}>
          <div className="bg-card border border-border rounded-xl shadow-xl w-[360px]" onClick={(e) => e.stopPropagation()}>
            <div className="p-4 border-b border-border">
              <h3 className="font-semibold text-sm">改名</h3>
              <p className="text-xs text-muted-foreground mt-1">
                将 <span className="font-medium text-foreground">{renameTarget.name}</span> 改为新名称
              </p>
            </div>
            <div className="p-4">
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleRename()}
                className="w-full bg-background border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-primary"
                autoFocus
              />
            </div>
            <div className="p-4 border-t border-border flex justify-end gap-2">
              <button
                onClick={() => setRenameTarget(null)}
                className="px-3 py-1.5 text-xs rounded bg-secondary hover:bg-secondary/80 text-secondary-foreground"
              >
                取消
              </button>
              <button
                onClick={handleRename}
                disabled={!newName.trim() || newName.trim() === renameTarget.name}
                className="px-3 py-1.5 text-xs rounded bg-primary hover:bg-primary/90 text-primary-foreground disabled:opacity-40"
              >
                确认改名
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Index;
