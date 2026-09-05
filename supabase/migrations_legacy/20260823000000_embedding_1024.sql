-- Switch embedding space: OpenAI text-embedding-3-small (1536d)
--   -> SiliconFlow BAAI/bge-m3 (1024d)
-- Old vectors are unusable in the new space; wipe and re-embed via
-- POST /api/admin/reembed-all after deploying with SILICONFLOW_API_KEY.

DROP INDEX IF EXISTS idx_atom_embeddings_hnsw;
DROP INDEX IF EXISTS idx_atom_embeddings_vector;

DELETE FROM atom_embeddings;

ALTER TABLE atom_embeddings ALTER COLUMN embedding TYPE vector(1024) USING NULL;

DROP FUNCTION IF EXISTS match_atoms_v1(vector, int, text[]);
DROP FUNCTION IF EXISTS match_atoms_v1(vector(1536), int, text[]);

CREATE FUNCTION match_atoms_v1(
  query_embedding  vector(1024),
  match_count      int     DEFAULT 20,
  video_ids_filter text[]  DEFAULT NULL
)
RETURNS TABLE (
  atom_id    character varying,
  video_id   character varying,
  similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
  IF video_ids_filter IS NOT NULL AND array_length(video_ids_filter, 1) > 0 THEN
    RETURN QUERY
      SELECT ae.atom_id::character varying, ae.video_id::character varying,
             (1 - (ae.embedding <=> query_embedding))::float AS similarity
      FROM atom_embeddings ae
      WHERE ae.video_id = ANY(video_ids_filter)
      ORDER BY ae.embedding <=> query_embedding
      LIMIT match_count;
  ELSE
    RETURN QUERY
      SELECT ae.atom_id::character varying, ae.video_id::character varying,
             (1 - (ae.embedding <=> query_embedding))::float AS similarity
      FROM atom_embeddings ae
      ORDER BY ae.embedding <=> query_embedding
      LIMIT match_count;
  END IF;
END;
$$;

CREATE INDEX idx_atom_embeddings_hnsw
  ON atom_embeddings USING hnsw (embedding vector_cosine_ops);
