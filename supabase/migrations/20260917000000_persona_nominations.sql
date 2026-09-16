-- 提名活动:用户提名想要上线的博主(YouTube 频道 / X 账号),同一链接重复提名计为投票
CREATE TABLE IF NOT EXISTS public.persona_nominations (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  name text NOT NULL,
  platform text NOT NULL CHECK (platform IN ('youtube', 'x')),
  url text NOT NULL,
  handle text NOT NULL,                 -- 规范化后的频道/账号标识,用于去重
  reason text,
  user_id uuid,
  guest_ip text,
  votes integer NOT NULL DEFAULT 1,
  status text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'accepted', 'building', 'live', 'rejected')),
  admin_note text,
  CONSTRAINT persona_nominations_platform_handle_key UNIQUE (platform, handle)
);

CREATE TABLE IF NOT EXISTS public.persona_nomination_votes (
  nomination_id uuid NOT NULL REFERENCES public.persona_nominations(id) ON DELETE CASCADE,
  voter text NOT NULL,                  -- 'u:<user_id>' 或 'ip:<guest_ip>'
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (nomination_id, voter)
);

CREATE INDEX IF NOT EXISTS persona_nominations_votes_idx
  ON public.persona_nominations (status, votes DESC, created_at DESC);

-- 只允许服务端(service_role)读写;前端经 API 访问
ALTER TABLE public.persona_nominations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.persona_nomination_votes ENABLE ROW LEVEL SECURITY;
