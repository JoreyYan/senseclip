-- Ensure atom_entities has a video_id column so deletes are properly scoped
-- This prevents cross-video data corruption during force_reprocess

ALTER TABLE atom_entities
  ADD COLUMN IF NOT EXISTS video_id varchar(50);

-- Backfill video_id from atoms table where missing
UPDATE atom_entities ae
SET video_id = a.video_id
FROM atoms a
WHERE ae.atom_id = a.id
  AND ae.video_id IS NULL;

-- Add index for faster per-video deletes
CREATE INDEX IF NOT EXISTS idx_atom_entities_video_id ON atom_entities(video_id);
