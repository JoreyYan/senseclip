-- Persons main table (persistent, replaces ephemeral app_settings cache)
CREATE TABLE IF NOT EXISTS persons (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    aliases TEXT[] DEFAULT '{}',
    category TEXT DEFAULT 'other',
    category_confidence FLOAT DEFAULT 0.4,
    mention_count INT DEFAULT 0,
    video_ids TEXT[] DEFAULT '{}',
    framework_tags JSONB DEFAULT '[]',
    web_profile TEXT,
    web_enriched_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS persons_name_idx ON persons(name);

-- Person career trajectory
CREATE TABLE IF NOT EXISTS person_career (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    person_id TEXT NOT NULL,
    person_name TEXT NOT NULL,
    position TEXT,
    organization TEXT,
    power_level TEXT,  -- 正国/副国/正省部/副省部/正厅局/副厅局/其他
    time_start TEXT,
    time_end TEXT,
    event_type TEXT,   -- appointment/promotion/transfer/removal/investigation
    source TEXT DEFAULT 'corpus',  -- corpus/web
    source_atom_id TEXT,
    source_url TEXT,
    confidence FLOAT DEFAULT 0.7,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS person_career_person_idx ON person_career(person_id);
CREATE INDEX IF NOT EXISTS person_career_name_idx ON person_career(person_name);

-- Directed person relations (the core knowledge graph)
CREATE TABLE IF NOT EXISTS person_relations (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    person_a_id TEXT NOT NULL,
    person_a_name TEXT NOT NULL,
    person_b_id TEXT NOT NULL,
    person_b_name TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    -- promoted/appointed/investigated/cooperated/succeeded/
    -- opposed/mentored/allied/protected/betrayed/competed/family/co_mentioned
    direction TEXT DEFAULT 'a_to_b',  -- a_to_b/b_to_a/bidirectional
    time_context TEXT,
    organization TEXT,
    description TEXT,
    confidence FLOAT DEFAULT 0.7,
    source TEXT DEFAULT 'corpus',  -- corpus/web/inferred
    source_atom_id TEXT,
    source_url TEXT,
    video_id TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS person_relations_a_idx ON person_relations(person_a_id);
CREATE INDEX IF NOT EXISTS person_relations_b_idx ON person_relations(person_b_id);
CREATE INDEX IF NOT EXISTS person_relations_name_a_idx ON person_relations(person_a_name);
CREATE INDEX IF NOT EXISTS person_relations_name_b_idx ON person_relations(person_b_name);
CREATE INDEX IF NOT EXISTS person_relations_type_idx ON person_relations(relation_type);
