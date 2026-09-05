import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { API_BASE } from "@/constants";
import { getLibrary, retryAsset, getJobStatus } from "@/services/api";
import type { VideoAsset } from "@/services/api";

const PIPELINE_STEPS = [
  { key: "audio_ready",         label: "音频就绪" },
  { key: "transcribed",         label: "字幕完成" },
  { key: "atomized",            label: "原子化" },
  { key: "segmented",           label: "片段化" },
  { key: "entities_extracted",  label: "实体提取" },
  { key: "topics_extracted",    label: "话题提取" },
  { key: "vectorized",          label: "向量化" },
  { key: "processed",           label: "分析完成" },
];

function normalizeStatus(status: unknown): VideoAsset["status"] {
  if (status === "done" || status === "completed") return "processed";
  return status as VideoAsset["status"];
}

function resolveAssetStatus(asset: VideoAsset): VideoAsset["status"] {
  const rawStatus = (asset as unknown as { status?: unknown }).status;
  const normalized = normalizeStatus(rawStatus);
  const atomCount = Number(asset.atom_count || 0);
  const segmentCount = Number(asset.segment_count || 0);
  const entityCount = Number(asset.entity_count || 0);
  const embeddingCount = Number(asset.embedding_count || 0);

  // 从最完整的状态开始检查
  if (embeddingCount > 0 || asset.analysis_ready) return "processed";
  if (entityCount > 0) return "entities_extracted";
  if (segmentCount > 0) return "segmented";
  if (atomCount > 0) return "atomized";

  if (normalized === "processed" && !asset.db_video_id) return "transcribed";
  return normalized;
}

function stepIndex(status: VideoAsset["status"]) {
  if (status === "processed") return 8;
  if (status === "vectorized") return 7;
  if (status === "topics_extracted") return 6;
  if (status === "entities_extracted") return 5;
  if (status === "segmented") return 4;
  if (status === "atomized") return 3;
  if (status === "transcribed") return 2;
  if (status === "audio_ready") return 1;
  return 0;
}

function stepIndexFromStep(step?: string | null): number {
  if (!step) return 0;
  const i = PIPELINE_STEPS.findIndex((s) => s.key === step);
  return i >= 0 ? i + 1 : 0;
}

function inferFailedStep(lastSuccessfulStep?: string | null): string | null {
  const doneIdx = stepIndexFromStep(lastSuccessfulStep);
  if (doneIdx <= 0 || doneIdx >= PIPELINE_STEPS.length) return null;
  return PIPELINE_STEPS[doneIdx].key;
}

function inferCompletedIdxFromCurrentStep(currentStep?: string | null): number {
  const text = (currentStep || "").trim();
  if (!text) return 0;
  if (text.includes("向量化完成")) return 7;
  if (text.includes("话题提取完成")) return 6;
  if (text.includes("实体提取完成")) return 5;
  if (text.includes("片段化完成")) return 4;
  if (text.includes("原子化完成")) return 3;
  if (text.includes("字幕完成")) return 2;
  if (text.includes("音频就绪")) return 1;
  return 0;
}

function getStepState(asset: VideoAsset) {
  const status = resolveAssetStatus(asset);
  const latestJob = asset.recent_jobs?.[0];
  const isRunning = !!latestJob && ["pending","downloading","uploading","transcribing","processing"].includes(latestJob.status);
  const statusIdx = stepIndex(status);
  const inferredFromCurrentStepIdx = inferCompletedIdxFromCurrentStep(latestJob?.current_step);
  const fallbackLastSuccessfulIdx = Math.max(
    status !== "error" ? statusIdx : 0,
    inferredFromCurrentStepIdx
  );
  const lastSuccessfulIdx = stepIndexFromStep(latestJob?.last_successful_step) || fallbackLastSuccessfulIdx;
  const inferredFailedStep = latestJob?.failed_step || (latestJob?.status === "error" ? inferFailedStep(latestJob?.last_successful_step || (status !== "error" ? status : null)) : null);
  const failedIdx = stepIndexFromStep(inferredFailedStep);

  if (latestJob?.status === "error" && failedIdx > 0) {
    return { doneIdx: lastSuccessfulIdx, activeIdx: 0, failedIdx };
  }

  if (isRunning && lastSuccessfulIdx > 0) {
    return {
      doneIdx: lastSuccessfulIdx,
      activeIdx: Math.min(lastSuccessfulIdx + 1, PIPELINE_STEPS.length),
      failedIdx: 0,
    };
  }

  const idx = statusIdx;
  if (status === "error") {
    return { doneIdx: Math.max(idx - 1, 0), activeIdx: 0, failedIdx: idx > 0 ? idx : 0 };
  }

  return { doneIdx: Math.max(idx - 1, 0), activeIdx: idx, failedIdx: 0 };
}

function StepBar({ asset }: { asset: VideoAsset }) {
  const { doneIdx, activeIdx, failedIdx } = getStepState(asset);
  return (
    <div className="flex items-center gap-1 mt-2">
      {PIPELINE_STEPS.map((step, i) => {
        const current = i + 1;
        const done = doneIdx >= current;
        const active = activeIdx === current;
        const failed = failedIdx === current;
        return (
          <div key={step.key} className="flex items-center gap-1">
            <div className={"w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold " +
              (done ? "bg-green-500 text-white" :
                failed ? "bg-red-500 text-white" :
                active ? "bg-blue-500 text-white animate-pulse" :
                "bg-gray-700 text-gray-500")}>
              {done ? "✓" : failed ? "✗" : i + 1}
            </div>
            <span className={"text-xs " + (done ? "text-green-400" : failed ? "text-red-400" : active ? "text-blue-400" : "text-gray-600")}>
              {step.label}
            </span>
            {i < PIPELINE_STEPS.length - 1 && (
              <div className={"w-5 h-px " + (done ? "bg-green-500" : "bg-gray-700")} />
            )}
          </div>
        );
      })}
    </div>
  );
}

function AssetCard({ asset, onRetry, onReprocess, forceProcessing }: { asset: VideoAsset; onRetry: (url: string) => void; onReprocess: (videoId: string) => void; forceProcessing?: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const latestJob = asset.recent_jobs?.[0];
  const status = resolveAssetStatus(asset);
  const isRunning = status !== "completed" && (forceProcessing || (latestJob && ["pending","downloading","uploading","transcribing","processing"].includes(latestJob.status)));
  const canOpenAnalysis = !!asset.db_video_id;
  const inferredFailedStep = latestJob?.failed_step || (latestJob?.status === "error" ? inferFailedStep(latestJob?.last_successful_step || (status !== "error" ? status : null)) : null);
  const failedStepLabel = inferredFailedStep
    ? (PIPELINE_STEPS.find((s) => s.key === inferredFailedStep)?.label || inferredFailedStep)
    : null;

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-mono text-gray-300 truncate">
            {asset.video_id || "未知 ID"}
          </p>
          <p className="text-sm font-semibold text-gray-100 truncate mt-1">
            {asset.title || "（无标题）"}
          </p>
          <p className="text-xs text-gray-600 truncate mt-0.5">{asset.youtube_url}</p>
        </div>
        <span className={"shrink-0 text-xs px-2 py-0.5 rounded-full font-medium " +
          (status === "processed" ? "bg-green-900 text-green-400" :
            status === "error" ? "bg-red-900 text-red-400" :
            status === "pending" ? "bg-gray-800 text-gray-400" :
            "bg-blue-900 text-blue-400")}>
          {status === "processed" ? "完成" :
           status === "error" ? "失败" :
           status === "audio_ready" ? "有音频" :
           status === "transcribed" ? "有字幕" :
           status === "atomized" ? "原子化完成" :
           status === "segmented" ? "片段化完成" :
           status === "entities_extracted" ? "实体提取完成" :
           status === "topics_extracted" ? "话题提取完成" :
           status === "vectorized" ? "向量化完成" :
           "待处理"}
        </span>
      </div>

      <StepBar asset={asset} />

      {latestJob && (status !== "completed" || latestJob.status === "error") && (
        <div className={"mt-2 text-xs px-2 py-1 rounded " +
          (isRunning ? "bg-blue-900/30 text-blue-400" :
            latestJob.status === "error" ? "bg-red-900/30 text-red-400" :
            "bg-gray-800 text-gray-400")}>
          {isRunning && <span className="mr-1 animate-pulse">●</span>}
          {latestJob.status === "error" && failedStepLabel
            ? "失败于：" + failedStepLabel
            : (latestJob.current_step || latestJob.status)}
        </div>
      )}

      {status === "error" && asset.error_message && (
        <div className="mt-2">
          <button onClick={() => setExpanded(!expanded)} className="text-xs text-red-500 hover:text-red-400">
            {expanded ? "▲ 收起" : "▼ 查看错误"}
          </button>
          {expanded && (
            <pre className="mt-1 text-xs text-red-300 bg-red-900/20 border border-red-900 rounded p-2 overflow-auto max-h-32 whitespace-pre-wrap">
              {asset.error_message}
            </pre>
          )}
        </div>
      )}

      {expanded && asset.recent_jobs?.length > 1 && (
        <div className="mt-2 space-y-1">
          <p className="text-xs text-gray-500">历史记录：</p>
          {asset.recent_jobs.map(job => (
            <div key={job.id} className="text-xs text-gray-600 flex gap-2">
              <span className={job.status === "error" ? "text-red-500" : job.status === "completed" ? "text-green-500" : "text-blue-400"}>
                {job.status}
              </span>
              <span>{job.current_step}</span>
              <span>{new Date(job.created_at).toLocaleString("zh-CN")}</span>
            </div>
          ))}
        </div>
      )}

      <div className="flex gap-2 mt-3">
        {canOpenAnalysis && (
          <Link
            to={"/analysis?projectId=" + encodeURIComponent(asset.db_video_id || asset.video_id || "default")}
            className="text-xs px-3 py-1 bg-green-800 hover:bg-green-700 rounded text-green-300 transition-colors"
          >
            查看分析 →
          </Link>
        )}
        {!isRunning && (
          <button
            onClick={() => onRetry(asset.youtube_url)}
            className="text-xs px-3 py-1 bg-gray-800 hover:bg-gray-700 rounded text-gray-300 transition-colors"
          >
            {status === "error" ? "重试" :
             status === "processed" ? "重新分析" :
             "继续处理"}
          </button>
        )}
        {!isRunning && asset.db_video_id && (
          <button
            onClick={() => {
              if (confirm("强制重新原子化「" + (asset.title || asset.video_id) + "」？\n将清除已有原子/片段/实体并重新处理。"))
                onReprocess(asset.db_video_id!);
            }}
            className="text-xs px-3 py-1 bg-orange-900/50 hover:bg-orange-800/60 rounded text-orange-400 transition-colors"
            title="清除已有原子，从 SRT 重新原子化"
          >
            重新处理
          </button>
        )}
        {isRunning && (
          <span className="text-xs px-3 py-1 bg-blue-900/30 rounded text-blue-400">
            处理中...
          </span>
        )}
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-xs px-3 py-1 bg-gray-800 hover:bg-gray-700 rounded text-gray-500 transition-colors"
        >
          {expanded ? "收起" : "详情"}
        </button>
      </div>
    </div>
  );
}

export default function LibraryPage() {
  const [assets, setAssets] = useState<VideoAsset[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [processingVideoIds, setProcessingVideoIds] = useState<Set<string>>(new Set());

  const load = useCallback(async (options?: { silent?: boolean }) => {
    const silent = options?.silent ?? false;
    if (silent) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    try {
      const { assets } = await getLibrary();
      setAssets(assets);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "获取视频库失败");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  // 自适应轮询：有任务在跑时 8s，空闲时 60s（/api/library 是重端点，别打太勤）
  const hasActiveWork =
    activeJobId !== null ||
    assets.some((a) =>
      a.recent_jobs?.some((j) =>
        ["pending", "downloading", "uploading", "transcribing", "processing"].includes(j.status)
      )
    );

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    const interval = hasActiveWork ? 8000 : 60000;
    const timer = setInterval(() => load({ silent: true }), interval);
    return () => clearInterval(timer);
  }, [load, hasActiveWork]);

  useEffect(() => {
    if (!activeJobId) return;
    const poll = setInterval(async () => {
      const job = await getJobStatus(activeJobId).catch(() => null);
      if (!job) return;
      if (job.status === "completed" || job.status === "error") {
        setActiveJobId(null);
        setProcessingVideoIds(new Set());
        load({ silent: true });
      }
    }, 3000);
    return () => clearInterval(poll);
  }, [activeJobId, load]);

  async function handleRetry(youtubeUrl: string) {
    try {
      const { job_id } = await retryAsset(youtubeUrl);
      setActiveJobId(job_id);
      await load({ silent: true });
    } catch (e) {
      alert(e instanceof Error ? e.message : "操作失败");
    }
  }

  async function handleReprocess(videoId: string) {
    setProcessingVideoIds(prev => new Set(prev).add(videoId));
    try {
      const res = await fetch(API_BASE + "/api/ingest/reprocess/" + videoId, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      const { job_id } = await res.json();
      setActiveJobId(job_id);
      await load({ silent: true });
    } catch (e) {
      setProcessingVideoIds(prev => { const s = new Set(prev); s.delete(videoId); return s; });
      alert(e instanceof Error ? e.message : "重新处理失败");
    }
  }

  const hasRunning = assets.some(a =>
    a.recent_jobs?.[0] && ["pending","downloading","uploading","transcribing","processing"].includes(a.recent_jobs[0].status)
  );

  return (
    <div className="flex-1 overflow-auto bg-gray-950 text-white p-8">
      <div className="max-w-2xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <Link to="/" className="text-xs text-gray-500 hover:text-gray-400">← 返回首页</Link>
          <Link to="/ingest" className="text-xs px-3 py-1.5 bg-blue-600 hover:bg-blue-700 rounded text-white transition-colors">
            + 添加视频
          </Link>
        </div>

        <div className="flex items-center justify-between mb-4">
          <h1 className="text-2xl font-bold">视频库</h1>
          <div className="flex items-center gap-2">
            {refreshing && <span className="text-xs text-gray-500 animate-pulse">刷新中...</span>}
            {hasRunning && <span className="text-xs text-blue-400 animate-pulse">● 处理中</span>}
            <span className="text-xs text-gray-500">{assets.length} 个视频</span>
          </div>
        </div>

        {error && (
          <div className="mb-4 rounded-lg border border-red-900 bg-red-950/40 px-4 py-3 text-sm text-red-300">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="font-medium">视频库加载失败</p>
                <p className="text-xs text-red-400 mt-1">{error}</p>
              </div>
              <button
                onClick={() => load()}
                className="shrink-0 rounded bg-red-900 px-3 py-1 text-xs text-red-100 hover:bg-red-800 transition-colors"
              >
                重试
              </button>
            </div>
          </div>
        )}

        {loading && assets.length === 0 ? (
          <div className="text-center py-12 text-gray-500">正在加载视频库...</div>
        ) : assets.length === 0 ? (
          <div className="text-center py-12 text-gray-600">
            <p className="mb-3">还没有视频</p>
            <Link to="/ingest" className="text-blue-400 hover:text-blue-300 text-sm">去添加第一个视频 →</Link>
          </div>
        ) : (
          <div className="space-y-3">
            {assets.map(asset => (
              <AssetCard key={asset.youtube_url} asset={asset} onRetry={handleRetry} onReprocess={handleReprocess} forceProcessing={processingVideoIds.has(asset.db_video_id || asset.video_id || "")} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
