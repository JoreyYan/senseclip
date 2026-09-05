import { useState, useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { submitVideo, uploadAudio, getJobStatus } from "@/services/api";
import type { JobStatus } from "@/services/api";

const STEPS = [
  { key: "downloading", label: "下载音频" },
  { key: "uploading", label: "上传云端" },
  { key: "transcribing", label: "转录字幕" },
  { key: "processing", label: "分析内容" },
  { key: "completed", label: "完成" },
];

function StepIndicator({ status }: { status: JobStatus["status"] }) {
  const currentIndex = STEPS.findIndex((s) => s.key === status);
  return (
    <div className="flex items-center gap-2 mt-4">
      {STEPS.map((step, i) => {
        const done = currentIndex > i || status === "completed";
        const active = STEPS[currentIndex]?.key === step.key;
        return (
          <div key={step.key} className="flex items-center gap-1">
            <div
              className={"w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold " +
                (done ? "bg-green-500 text-white" : active ? "bg-blue-500 text-white animate-pulse" : "bg-gray-700 text-gray-400")}
            >
              {done ? "✓" : i + 1}
            </div>
            <span className={"text-xs " + (active ? "text-blue-400" : done ? "text-green-400" : "text-gray-500")}>
              {step.label}
            </span>
            {i < STEPS.length - 1 && <div className={"w-6 h-0.5 " + (done ? "bg-green-500" : "bg-gray-700")} />}
          </div>
        );
      })}
    </div>
  );
}

export default function IngestPage() {
  const [url, setUrl] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<JobStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    setError(null);
    setLoading(true);
    try {
      const res = await submitVideo(url.trim());
      setJobId(res.job_id);
      setUrl("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "提交失败");
    } finally {
      setLoading(false);
    }
  }

  async function handleAudioFile(file: File) {
    setError(null);
    setLoading(true);
    try {
      const res = await uploadAudio(file);
      setJobId(res.job_id);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "上传失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!jobId) return;
    const poll = async () => {
      try {
        const status = await getJobStatus(jobId);
        setJob(status);
        if (status.status === "completed" || status.status === "error") {
          if (pollRef.current) clearInterval(pollRef.current);
        }
      } catch {
        // ignore poll errors
      }
    };
    poll();
    pollRef.current = setInterval(poll, 3000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [jobId]);

  return (
    <div className="flex-1 overflow-auto bg-gray-950 text-white p-8">
      <div className="max-w-xl mx-auto">
        <h1 className="text-2xl font-bold mb-2">提交 YouTube 视频</h1>
        <p className="text-gray-400 text-sm mb-6">
          提交后自动下载音频 → 转录字幕 → 分析内容，全程云端处理。
        </p>

        <form onSubmit={handleSubmit} className="flex gap-2">
          <input
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://www.youtube.com/watch?v=..."
            className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:border-blue-500"
            required
          />
          <button
            type="submit"
            disabled={loading}
            className="px-5 py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 rounded-lg text-sm font-medium transition-colors"
          >
            {loading ? "提交中..." : "提交"}
          </button>
        </form>

        <div className="mt-4">
          <p className="text-xs text-gray-500 mb-2 text-center">— 或者直接上传音频文件 —</p>
          <input
            ref={fileInputRef}
            type="file"
            accept="audio/*,.mp3,.m4a,.wav,.ogg,.flac"
            className="hidden"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) handleAudioFile(f); e.target.value = ""; }}
          />
          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => { e.preventDefault(); setDragging(false); const f = e.dataTransfer.files[0]; if (f) handleAudioFile(f); }}
            onClick={() => !loading && fileInputRef.current?.click()}
            className={"border-2 border-dashed rounded-lg p-5 text-center cursor-pointer transition-colors " +
              (dragging ? "border-blue-500 bg-blue-900/10" : "border-gray-700 hover:border-gray-600")}
          >
            {loading ? (
              <p className="text-sm text-blue-400">处理中...</p>
            ) : (
              <>
                <p className="text-sm text-gray-400">拖拽或点击上传 mp3 / m4a / wav</p>
                <p className="text-xs text-gray-600 mt-1">支持本地音频，直接跳过 YouTube 下载</p>
              </>
            )}
          </div>
        </div>

        {error && (
          <div className="mt-4 p-3 bg-red-900/40 border border-red-700 rounded-lg text-red-300 text-sm">
            {error}
          </div>
        )}

        {job && (
          <div className="mt-6 p-5 bg-gray-900 border border-gray-800 rounded-xl">
            <div className="flex items-center justify-between mb-1">
              <span className="text-sm font-medium text-gray-300 truncate max-w-xs">{job.youtube_url}</span>
              <span className={"text-xs px-2 py-0.5 rounded-full font-medium " +
                (job.status === "completed" ? "bg-green-900 text-green-400" :
                  job.status === "error" ? "bg-red-900 text-red-400" :
                  "bg-blue-900 text-blue-400")}>
                {job.status === "completed" ? "完成" :
                 job.status === "error" ? "失败" : "处理中"}
              </span>
            </div>

            {job.current_step && (
              <p className="text-xs text-gray-500 mt-1">{job.current_step}</p>
            )}

            <StepIndicator status={job.status} />

            {job.status === "error" && job.error_message && (
              <div className="mt-4 p-3 bg-red-900/30 border border-red-800 rounded-lg text-red-300 text-xs">
                {job.error_message}
              </div>
            )}

            {job.status === "completed" && job.video_id && (
              <div className="mt-4 p-3 bg-green-900/20 border border-green-800 rounded-lg">
                <p className="text-green-400 text-sm font-medium">处理完成！</p>
                <p className="text-gray-400 text-xs mt-1">视频 ID: {job.video_id}</p>
                <Link
                  to={"/analysis?projectId=" + encodeURIComponent(job.video_id)}
                  className="inline-block mt-2 text-xs text-blue-400 hover:text-blue-300 underline"
                >
                  查看分析结果 →
                </Link>
              </div>
            )}
          </div>
        )}

        <div className="mt-8 text-center">
          <Link to="/settings" className="text-xs text-gray-500 hover:text-gray-400">
            管理 YouTube Cookies →
          </Link>
        </div>
      </div>
    </div>
  );
}
