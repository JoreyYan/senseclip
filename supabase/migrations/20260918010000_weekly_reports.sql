-- 人格周度市场解读(engine 的 WeeklyReporter 写入,/api/reports 读取)
CREATE TABLE IF NOT EXISTS public.weekly_reports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  persona text NOT NULL,
  week_start date NOT NULL,              -- 所属周的周一
  title text,
  content text,                          -- Markdown,引用为 [N]
  citations jsonb NOT NULL DEFAULT '[]'::jsonb,
  data_snapshot jsonb,                   -- 生成时使用的数据,便于核对
  status text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'error')),
  model text,
  error text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT weekly_reports_persona_week_key UNIQUE (persona, week_start)
);
CREATE INDEX IF NOT EXISTS weekly_reports_persona_idx ON public.weekly_reports (persona, week_start DESC);
ALTER TABLE public.weekly_reports ENABLE ROW LEVEL SECURITY;
