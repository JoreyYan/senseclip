-- Pipeline 作业状态追踪表
CREATE TABLE IF NOT EXISTS pipeline_jobs (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  youtube_url TEXT NOT NULL,
  video_id TEXT,
  status TEXT DEFAULT 'pending',
  -- status: pending / downloading / uploading / transcribing / processing / completed / error
  current_step TEXT,
  error_message TEXT,
  audio_url TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- 自动更新 updated_at
CREATE OR REPLACE FUNCTION update_pipeline_jobs_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER pipeline_jobs_updated_at
  BEFORE UPDATE ON pipeline_jobs
  FOR EACH ROW EXECUTE FUNCTION update_pipeline_jobs_updated_at();

-- 全局应用设置表（用于存储 cookies 等）
CREATE TABLE IF NOT EXISTS app_settings (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE OR REPLACE FUNCTION update_app_settings_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER app_settings_updated_at
  BEFORE UPDATE ON app_settings
  FOR EACH ROW EXECUTE FUNCTION update_app_settings_updated_at();
