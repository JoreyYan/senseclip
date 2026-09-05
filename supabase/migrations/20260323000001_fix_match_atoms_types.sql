-- Drop old function first (return type changed)
DROP FUNCTION IF EXISTS match_atoms_v1(vector, int, text[]);

-- Recreate with correct varchar(50) return types matching atom_embeddings columns
CREATE OR REPLACE FUNCTION match_atoms_v1(
  query_embedding  vector(1536),
  match_count      int     DEFAULT 20,
  video_ids_filter text[]  DEFAULT NULL
)
RETURNS TABLE (
  atom_id    varchar(50),
  video_id   varchar(50),
  similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
  IF video_ids_filter IS NOT NULL AND array_length(video_ids_filter, 1) > 0 THEN
    RETURN QUERY
      SELECT ae.atom_id, ae.video_id,
             1 - (ae.embedding <=> query_embedding) AS similarity
      FROM atom_embeddings ae
      WHERE ae.video_id = ANY(video_ids_filter)
      ORDER BY ae.embedding <=> query_embedding
      LIMIT match_count;
  ELSE
    RETURN QUERY
      SELECT ae.atom_id, ae.video_id,
             1 - (ae.embedding <=> query_embedding) AS similarity
      FROM atom_embeddings ae
      ORDER BY ae.embedding <=> query_embedding
      LIMIT match_count;
  END IF;
END;
$$;
