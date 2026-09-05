-- person_profiles: stores AI-generated analysis per person
-- separate from persons table (which handles network structure)

CREATE TABLE IF NOT EXISTS person_profiles (
  person_id          TEXT PRIMARY KEY,
  name               TEXT NOT NULL,
  ai_summary         TEXT,                          -- AI-generated bio
  ai_career_events   JSONB    DEFAULT '[]'::jsonb,  -- [{event_type, time_expr, organization, text, atom_id}]
  ai_relations       JSONB    DEFAULT '[]'::jsonb,  -- [{other_name, relation_type, strength, evidence_text, atom_ids}]
  source_atom_ids    TEXT[]   DEFAULT '{}'::text[],  -- atom ids used in last analysis
  atom_count         INTEGER  DEFAULT 0,
  model_used         TEXT,
  last_analyzed_at   TIMESTAMPTZ,
  created_at         TIMESTAMPTZ DEFAULT NOW(),
  updated_at         TIMESTAMPTZ DEFAULT NOW()
);

-- index for quick lookup
CREATE INDEX IF NOT EXISTS idx_person_profiles_name ON person_profiles(name);

-- auto-update updated_at
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER person_profiles_updated_at
  BEFORE UPDATE ON person_profiles
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
