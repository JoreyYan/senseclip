-- Scale optimizations: vector index, stats RPC, job retention
-- Batch 1 of the large-scale data readiness work.

-- 1. HNSW vector index: match_atoms_v1 currently does a sequential scan
--    over all embeddings on every chat query. HNSW makes it logarithmic.
CREATE INDEX IF NOT EXISTS idx_atom_embeddings_hnsw
  ON atom_embeddings USING hnsw (embedding vector_cosine_ops);

-- 2. Missing FK-ish indexes used by hot queries
--    NOTE: live topics/entities tables are GLOBAL (no video_id column),
--    so no per-video indexes there.
CREATE INDEX IF NOT EXISTS idx_atoms_video_id ON atoms(video_id);
CREATE INDEX IF NOT EXISTS idx_narrative_segments_video_id ON narrative_segments(video_id);
CREATE INDEX IF NOT EXISTS idx_atom_entities_entity_name ON atom_entities(entity_name);
CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_url_created
  ON pipeline_jobs(youtube_url, created_at DESC);

-- 3. Per-video stats in ONE round trip (replaces pulling every row to count in Python)
CREATE OR REPLACE FUNCTION get_video_stats(p_video_ids text[])
RETURNS TABLE (
  video_id        text,
  atom_count      bigint,
  segment_count   bigint,
  topic_count     bigint,
  entity_count    bigint,
  embedding_count bigint
)
LANGUAGE sql STABLE
AS $$
  -- topics/entities are global tables in the live schema; per-video entity
  -- signal comes from atom_entities (distinct entity per video).
  SELECT
    v.vid AS video_id,
    COALESCE(a.cnt, 0)  AS atom_count,
    COALESCE(s.cnt, 0)  AS segment_count,
    0::bigint           AS topic_count,
    COALESCE(e.cnt, 0)  AS entity_count,
    COALESCE(em.cnt, 0) AS embedding_count
  FROM unnest(p_video_ids) AS v(vid)
  LEFT JOIN (SELECT video_id, count(*) cnt FROM atoms              WHERE video_id = ANY(p_video_ids) GROUP BY video_id) a  ON a.video_id = v.vid
  LEFT JOIN (SELECT video_id, count(*) cnt FROM narrative_segments WHERE video_id = ANY(p_video_ids) GROUP BY video_id) s  ON s.video_id = v.vid
  LEFT JOIN (SELECT video_id, count(DISTINCT entity_name) cnt FROM atom_entities WHERE video_id = ANY(p_video_ids) GROUP BY video_id) e ON e.video_id = v.vid
  LEFT JOIN (SELECT video_id, count(*) cnt FROM atom_embeddings    WHERE video_id = ANY(p_video_ids) GROUP BY video_id) em ON em.video_id = v.vid;
$$;

-- 4. One-time cleanup: keep only the newest 3 pipeline_jobs per youtube_url
DELETE FROM pipeline_jobs
WHERE id IN (
  SELECT id FROM (
    SELECT id,
           row_number() OVER (PARTITION BY youtube_url ORDER BY created_at DESC) AS rn
    FROM pipeline_jobs
  ) ranked
  WHERE ranked.rn > 3
);
