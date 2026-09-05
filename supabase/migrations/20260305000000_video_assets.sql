-- 视频资源缓存表
-- 用途：记录每个 YouTube URL 对应的已下载/处理资源，避免重复处理
CREATE TABLE IF NOT EXISTS video_assets (
  youtube_url    TEXT PRIMARY KEY,
  video_id       TEXT,                    -- 从 URL 提取的 video ID（如 ttd-nqzY0r8）
  audio_url      TEXT,                    -- Supabase Storage 公开 URL
  srt_content    TEXT,                    -- 完整 SRT 字幕内容
  title          TEXT,                    -- 视频标题（可选）
  duration_sec   INTEGER,                 -- 时长（秒）
  status         TEXT DEFAULT 'pending',  -- pending / audio_ready / transcribed / processed / error
  db_video_id    TEXT,                    -- supabase_pipeline 处理后写入 videos 表的 ID
  error_message  TEXT,
  created_at     TIMESTAMPTZ DEFAULT now(),
  updated_at     TIMESTAMPTZ DEFAULT now()
);

CREATE OR REPLACE FUNCTION update_video_assets_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER video_assets_updated_at
  BEFORE UPDATE ON video_assets
  FOR EACH ROW EXECUTE FUNCTION update_video_assets_updated_at();

-- video_id 索引方便查询
CREATE INDEX IF NOT EXISTS idx_video_assets_video_id ON video_assets(video_id);
CREATE INDEX IF NOT EXISTS idx_video_assets_status ON video_assets(status);
