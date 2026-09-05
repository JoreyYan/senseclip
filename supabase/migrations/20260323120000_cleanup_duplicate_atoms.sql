-- Remove old-format atoms (id like 'A001') that have been superseded
-- by prefixed versions (id like 'bSQ9yCnLgr0_A001').
-- Root cause: force_reprocess delete failed for rows with NULL video_id,
-- leaving old atoms alive while new prefixed atoms were inserted.

-- 1. Clean up dependent tables first (no video_id filter needed — match by atom_id pattern)
DELETE FROM atom_entities
WHERE atom_id ~ '^A[0-9]+$';

DELETE FROM atom_embeddings
WHERE atom_id ~ '^A[0-9]+$';

DELETE FROM atom_annotations
WHERE atom_id ~ '^A[0-9]+$';

DELETE FROM atom_topics
WHERE atom_id ~ '^A[0-9]+$';

-- 2. Now safe to delete old-format atoms
DELETE FROM atoms
WHERE id ~ '^A[0-9]+$';
