-- Add video_id column to atom_entities and atom_embeddings
-- This enables batch queries by video_id without going through atom_ids (N+1 fix)

ALTER TABLE atom_entities ADD COLUMN IF NOT EXISTS video_id TEXT;
ALTER TABLE atom_embeddings ADD COLUMN IF NOT EXISTS video_id TEXT;

-- Backfill existing rows via atoms table
UPDATE atom_entities
SET video_id = atoms.video_id
FROM atoms
WHERE atom_entities.atom_id = atoms.id
  AND atom_entities.video_id IS NULL;

UPDATE atom_embeddings
SET video_id = atoms.video_id
FROM atoms
WHERE atom_embeddings.atom_id = atoms.id
  AND atom_embeddings.video_id IS NULL;

-- Indexes for efficient IN queries in /api/library
CREATE INDEX IF NOT EXISTS idx_atom_entities_video_id ON atom_entities(video_id);
CREATE INDEX IF NOT EXISTS idx_atom_embeddings_video_id ON atom_embeddings(video_id);
