-- 创建 audio Storage bucket（public 可公开访问）
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
  'audio',
  'audio',
  true,
  524288000,  -- 500MB 上限
  ARRAY['audio/mpeg', 'audio/mp3', 'audio/mp4', 'audio/m4a', 'audio/wav', 'audio/ogg']
)
ON CONFLICT (id) DO NOTHING;

-- 允许任何人读取（public bucket）
CREATE POLICY "Public read audio" ON storage.objects
  FOR SELECT USING (bucket_id = 'audio');

-- 只允许 service_role 上传（后端用）
CREATE POLICY "Service role upload audio" ON storage.objects
  FOR INSERT WITH CHECK (bucket_id = 'audio');

CREATE POLICY "Service role delete audio" ON storage.objects
  FOR DELETE USING (bucket_id = 'audio');
