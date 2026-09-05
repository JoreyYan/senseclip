-- Drop all overloads of match_atoms_v1
DROP FUNCTION IF EXISTS match_atoms_v1(vector, int, text[]);
DROP FUNCTION IF EXISTS match_atoms_v1(vector(1536), int, text[]);

-- Recreate with explicit casts to character varying to match atom_embeddings columns
CREATE FUNCTION match_atoms_v1(
  query_embedding  vector(1536),
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
