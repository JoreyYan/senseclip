-- SenseClip baseline schema (generated from the production database on 2026-09-05)
-- Idempotent: safe to run on an empty Supabase/Postgres project. Requires pgvector.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- sequences
CREATE SEQUENCE IF NOT EXISTS public.atom_annotations_id_seq;
CREATE SEQUENCE IF NOT EXISTS public.atom_embeddings_id_seq;
CREATE SEQUENCE IF NOT EXISTS public.atom_entities_id_seq;
CREATE SEQUENCE IF NOT EXISTS public.atom_topics_id_seq;
CREATE SEQUENCE IF NOT EXISTS public.project_videos_id_seq;
CREATE SEQUENCE IF NOT EXISTS public.topics_id_seq;

CREATE TABLE IF NOT EXISTS public."app_settings" (
  "key" text NOT NULL,
  "value" text,
  "updated_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "app_settings_pkey" PRIMARY KEY (key)
);

CREATE TABLE IF NOT EXISTS public."atom_annotations" (
  "id" bigint DEFAULT nextval('atom_annotations_id_seq'::regclass) NOT NULL,
  "atom_id" character varying(50) NOT NULL,
  "topics" text[] DEFAULT '{}'::text[],
  "emotion_type" character varying(20),
  "emotion_score" numeric(3,2),
  "emotion_confidence" numeric(3,2),
  "emotion_distribution" jsonb,
  "importance_score" numeric(3,2),
  "has_entity" boolean DEFAULT false,
  "has_topic" boolean DEFAULT false,
  "embedding_status" character varying(20) DEFAULT 'pending'::character varying,
  "parent_segment_id" character varying(50),
  "parent_narrative_id" character varying(50),
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "atom_annotations_atom_id_key" UNIQUE (atom_id),
  CONSTRAINT "atom_annotations_embedding_status_check" CHECK (((embedding_status)::text = ANY ((ARRAY['pending'::character varying, 'completed'::character varying, 'failed'::character varying])::text[]))),
  CONSTRAINT "atom_annotations_emotion_type_check" CHECK (((emotion_type)::text = ANY ((ARRAY['positive'::character varying, 'negative'::character varying, 'neutral'::character varying])::text[]))),
  CONSTRAINT "atom_annotations_pkey" PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public."atom_embeddings" (
  "id" bigint DEFAULT nextval('atom_embeddings_id_seq'::regclass) NOT NULL,
  "atom_id" character varying(50) NOT NULL,
  "embedding" vector(1024),
  "embedding_model" character varying(50) DEFAULT 'text-embedding-ada-002'::character varying,
  "embedding_version" character varying(20),
  "status" character varying(20) DEFAULT 'active'::character varying,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now(),
  "video_id" text,
  CONSTRAINT "atom_embeddings_atom_id_key" UNIQUE (atom_id),
  CONSTRAINT "atom_embeddings_pkey" PRIMARY KEY (id),
  CONSTRAINT "atom_embeddings_status_check" CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'outdated'::character varying, 'failed'::character varying])::text[])))
);

CREATE TABLE IF NOT EXISTS public."atom_entities" (
  "id" bigint DEFAULT nextval('atom_entities_id_seq'::regclass) NOT NULL,
  "atom_id" character varying(50) NOT NULL,
  "entity_name" character varying(200) NOT NULL,
  "entity_type" character varying(50) NOT NULL,
  "confidence" numeric(3,2) NOT NULL,
  "global_entity_id" character varying(100),
  "created_at" timestamp with time zone DEFAULT now(),
  "video_id" text,
  CONSTRAINT "atom_entities_pkey" PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public."atom_topics" (
  "id" bigint DEFAULT nextval('atom_topics_id_seq'::regclass) NOT NULL,
  "atom_id" character varying(50) NOT NULL,
  "topic_id" bigint NOT NULL,
  "relevance_score" numeric(3,2) DEFAULT 1.0,
  "created_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "atom_topics_atom_id_topic_id_key" UNIQUE (atom_id, topic_id),
  CONSTRAINT "atom_topics_pkey" PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public."atoms" (
  "id" character varying(50) NOT NULL,
  "video_id" character varying(50) NOT NULL,
  "segment_id" character varying(50),
  "start_ms" integer NOT NULL,
  "end_ms" integer NOT NULL,
  "duration_ms" integer NOT NULL,
  "start_time" character varying(20),
  "end_time" character varying(20),
  "duration_seconds" numeric(10,2),
  "merged_text" text NOT NULL,
  "type" character varying(50),
  "completeness" character varying(20),
  "source_utterance_ids" integer[] DEFAULT '{}'::integer[],
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "atoms_pkey" PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public."chat_logs" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "user_id" uuid,
  "guest_ip" text,
  "question" text NOT NULL,
  "answer" text,
  "atoms_count" integer DEFAULT 0,
  "model" text,
  "created_at" timestamp with time zone DEFAULT now(),
  "rating" smallint,
  CONSTRAINT "chat_logs_pkey" PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public."chat_messages" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "conversation_id" uuid NOT NULL,
  "role" text NOT NULL,
  "content" text NOT NULL,
  "citations" jsonb,
  "created_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "chat_messages_pkey" PRIMARY KEY (id),
  CONSTRAINT "chat_messages_role_check" CHECK ((role = ANY (ARRAY['user'::text, 'assistant'::text])))
);

CREATE TABLE IF NOT EXISTS public."consult_jobs" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "status" text DEFAULT 'running'::text NOT NULL,
  "result" jsonb,
  "created_at" timestamp with time zone DEFAULT now(),
  "progress" text,
  CONSTRAINT "consult_jobs_pkey" PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public."conversations" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "user_id" uuid NOT NULL,
  "title" text DEFAULT '新对话'::text NOT NULL,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "conversations_pkey" PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public."credit_transactions" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "user_id" uuid NOT NULL,
  "amount" integer NOT NULL,
  "type" text NOT NULL,
  "description" text,
  "stripe_session_id" text,
  "job_id" text,
  "created_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "credit_transactions_pkey" PRIMARY KEY (id),
  CONSTRAINT "credit_transactions_type_check" CHECK ((type = ANY (ARRAY['signup_bonus'::text, 'purchase'::text, 'admin_adjustment'::text, 'ingest'::text, 'chat'::text, 'consult'::text, 'subscription'::text])))
);

CREATE TABLE IF NOT EXISTS public."entities" (
  "id" character varying(100) NOT NULL,
  "name" character varying(200) NOT NULL,
  "entity_type" character varying(50) NOT NULL,
  "description" text,
  "aliases" text[] DEFAULT '{}'::text[],
  "mention_count" integer DEFAULT 0,
  "importance_score" numeric(3,2),
  "first_mention_ms" integer,
  "last_mention_ms" integer,
  "mentioned_in_atoms" text[] DEFAULT '{}'::text[],
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "entities_entity_type_check" CHECK (((entity_type)::text = ANY ((ARRAY['PERSON'::character varying, 'LOCATION'::character varying, 'ORGANIZATION'::character varying, 'EVENT'::character varying, 'CONCEPT'::character varying])::text[]))),
  CONSTRAINT "entities_pkey" PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public."error_reports" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "created_at" timestamp with time zone DEFAULT now(),
  "user_id" uuid,
  "guest_ip" text,
  "question" text,
  "error" text,
  "mode" text,
  "diagnostics" jsonb,
  CONSTRAINT "error_reports_pkey" PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public."narrative_segments" (
  "id" character varying(50) NOT NULL,
  "video_id" character varying(50) NOT NULL,
  "start_ms" integer NOT NULL,
  "end_ms" integer NOT NULL,
  "duration_ms" integer NOT NULL,
  "title" character varying(500) NOT NULL,
  "summary" text NOT NULL,
  "atom_count" integer DEFAULT 0,
  "topics" text[] DEFAULT '{}'::text[],
  "key_entities" text[] DEFAULT '{}'::text[],
  "narrative_type" character varying(100),
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "narrative_segments_pkey" PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public."ng_location" (
  "code" text NOT NULL,
  "name" text,
  "active" boolean DEFAULT true NOT NULL,
  "note" text,
  "created_at" timestamp with time zone DEFAULT now() NOT NULL,
  CONSTRAINT "ng_location_pkey" PRIMARY KEY (code)
);

CREATE TABLE IF NOT EXISTS public."ng_stock_txn" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "txn_no" text NOT NULL,
  "direction" text NOT NULL,
  "txn_date" date DEFAULT CURRENT_DATE NOT NULL,
  "material_key" text NOT NULL,
  "material_name" text NOT NULL,
  "zb_code" text,
  "qty" numeric NOT NULL,
  "location" text NOT NULL,
  "source" text,
  "disposal" text,
  "ref_type" text,
  "ref_no" text,
  "good_batch_record_id" text,
  "supplier" text,
  "customer" text,
  "defect_code" text,
  "defect_desc" text,
  "unit_price" numeric,
  "operator" text,
  "note" text,
  "revoked" boolean DEFAULT false NOT NULL,
  "revoke_note" text,
  "created_at" timestamp with time zone DEFAULT now() NOT NULL,
  CONSTRAINT "ng_stock_txn_direction_check" CHECK ((direction = ANY (ARRAY['in'::text, 'out'::text]))),
  CONSTRAINT "ng_stock_txn_pkey" PRIMARY KEY (id),
  CONSTRAINT "ng_stock_txn_qty_check" CHECK ((qty > (0)::numeric)),
  CONSTRAINT "ng_stock_txn_txn_no_key" UNIQUE (txn_no)
);

CREATE TABLE IF NOT EXISTS public."person_career" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "person_id" text NOT NULL,
  "person_name" text NOT NULL,
  "position" text,
  "organization" text,
  "power_level" text,
  "time_start" text,
  "time_end" text,
  "event_type" text,
  "source" text DEFAULT 'corpus'::text,
  "source_atom_id" text,
  "source_url" text,
  "confidence" double precision DEFAULT 0.7,
  "created_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "person_career_pkey" PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public."person_profiles" (
  "person_id" text NOT NULL,
  "name" text NOT NULL,
  "ai_summary" text,
  "ai_career_events" jsonb DEFAULT '[]'::jsonb,
  "ai_relations" jsonb DEFAULT '[]'::jsonb,
  "source_atom_ids" text[] DEFAULT '{}'::text[],
  "atom_count" integer DEFAULT 0,
  "model_used" text,
  "last_analyzed_at" timestamp with time zone,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "person_profiles_pkey" PRIMARY KEY (person_id)
);

CREATE TABLE IF NOT EXISTS public."person_relations" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "person_a_id" text NOT NULL,
  "person_a_name" text NOT NULL,
  "person_b_id" text NOT NULL,
  "person_b_name" text NOT NULL,
  "relation_type" text NOT NULL,
  "direction" text DEFAULT 'a_to_b'::text,
  "time_context" text,
  "organization" text,
  "description" text,
  "confidence" double precision DEFAULT 0.7,
  "source" text DEFAULT 'corpus'::text,
  "source_atom_id" text,
  "source_url" text,
  "video_id" text,
  "created_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "person_relations_pkey" PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public."persona_quotes" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "persona" text NOT NULL,
  "quote" text NOT NULL,
  "context" text,
  "atom_id" text,
  "embedding" vector(1024),
  "created_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "persona_quotes_pkey" PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public."persona_viewpoints" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "persona" text NOT NULL,
  "topic" text NOT NULL,
  "stance" text NOT NULL,
  "reasoning" text,
  "confidence" text,
  "quote" text,
  "atom_ids" text[],
  "embedding" vector(1024),
  "created_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "persona_viewpoints_pkey" PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public."persons" (
  "id" text NOT NULL,
  "name" text NOT NULL,
  "aliases" text[] DEFAULT '{}'::text[],
  "category" text DEFAULT 'other'::text,
  "category_confidence" double precision DEFAULT 0.4,
  "mention_count" integer DEFAULT 0,
  "video_ids" text[] DEFAULT '{}'::text[],
  "framework_tags" jsonb DEFAULT '[]'::jsonb,
  "web_profile" text,
  "web_enriched_at" timestamp with time zone,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "persons_pkey" PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public."pipeline_jobs" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "youtube_url" text NOT NULL,
  "video_id" text,
  "status" text DEFAULT 'pending'::text,
  "current_step" text,
  "error_message" text,
  "audio_url" text,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "pipeline_jobs_pkey" PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public."project_videos" (
  "id" bigint DEFAULT nextval('project_videos_id_seq'::regclass) NOT NULL,
  "project_id" uuid NOT NULL,
  "video_id" character varying(50) NOT NULL,
  "added_at" timestamp with time zone DEFAULT now(),
  "added_by" character varying(100),
  CONSTRAINT "project_videos_pkey" PRIMARY KEY (id),
  CONSTRAINT "project_videos_project_id_video_id_key" UNIQUE (project_id, video_id)
);

CREATE TABLE IF NOT EXISTS public."projects" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "title" character varying(255) NOT NULL,
  "description" text,
  "status" character varying(20) DEFAULT 'active'::character varying,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "projects_pkey" PRIMARY KEY (id),
  CONSTRAINT "projects_status_check" CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'archived'::character varying])::text[])))
);

CREATE TABLE IF NOT EXISTS public."roundtables" (
  "id" uuid DEFAULT gen_random_uuid() NOT NULL,
  "user_id" uuid,
  "guest_ip" text,
  "topic" text NOT NULL,
  "personas" text[] NOT NULL,
  "rounds" integer DEFAULT 2,
  "status" text DEFAULT 'running'::text NOT NULL,
  "progress" text,
  "turns" jsonb DEFAULT '[]'::jsonb NOT NULL,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "roundtables_pkey" PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS public."topics" (
  "id" bigint DEFAULT nextval('topics_id_seq'::regclass) NOT NULL,
  "topic" character varying(500) NOT NULL,
  "weight" numeric(3,2),
  "segments" text[] DEFAULT '{}'::text[],
  "atoms" integer[] DEFAULT '{}'::integer[],
  "topic_type" character varying(50) DEFAULT 'primary'::character varying,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "topics_pkey" PRIMARY KEY (id),
  CONSTRAINT "topics_topic_key" UNIQUE (topic)
);

CREATE TABLE IF NOT EXISTS public."user_credits" (
  "user_id" uuid NOT NULL,
  "balance" integer DEFAULT 50 NOT NULL,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now(),
  "plan" text,
  "sub_period_end" timestamp with time zone,
  CONSTRAINT "user_credits_balance_check" CHECK ((balance >= 0)),
  CONSTRAINT "user_credits_pkey" PRIMARY KEY (user_id)
);

CREATE TABLE IF NOT EXISTS public."user_roles" (
  "user_id" uuid NOT NULL,
  "role" text DEFAULT 'viewer'::text NOT NULL,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "user_roles_pkey" PRIMARY KEY (user_id),
  CONSTRAINT "user_roles_role_check" CHECK ((role = ANY (ARRAY['owner'::text, 'editor'::text, 'viewer'::text])))
);

CREATE TABLE IF NOT EXISTS public."video_assets" (
  "youtube_url" text NOT NULL,
  "video_id" text,
  "audio_url" text,
  "srt_content" text,
  "title" text,
  "duration_sec" integer,
  "status" text DEFAULT 'pending'::text,
  "db_video_id" text,
  "error_message" text,
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "video_assets_pkey" PRIMARY KEY (youtube_url)
);

CREATE TABLE IF NOT EXISTS public."video_stats" (
  "video_id" character varying(50) NOT NULL,
  "person_count" integer DEFAULT 0,
  "location_count" integer DEFAULT 0,
  "organization_count" integer DEFAULT 0,
  "event_count" integer DEFAULT 0,
  "concept_count" integer DEFAULT 0,
  "positive_atoms" integer DEFAULT 0,
  "negative_atoms" integer DEFAULT 0,
  "neutral_atoms" integer DEFAULT 0,
  "unique_topics" integer DEFAULT 0,
  "topics_distribution" jsonb DEFAULT '{}'::jsonb,
  "vectorized_atoms" integer DEFAULT 0,
  "vectorization_progress" numeric(3,2) DEFAULT 0.00,
  "updated_at" timestamp with time zone DEFAULT now(),
  CONSTRAINT "video_stats_pkey" PRIMARY KEY (video_id)
);

CREATE TABLE IF NOT EXISTS public."videos" (
  "id" character varying(50) NOT NULL,
  "title" character varying(500) NOT NULL,
  "video_url" text,
  "duration_ms" integer NOT NULL,
  "duration_seconds" integer NOT NULL,
  "status" character varying(20) DEFAULT 'processing'::character varying,
  "processing_stage" character varying(100),
  "atom_count" integer DEFAULT 0,
  "segment_count" integer DEFAULT 0,
  "entity_count" integer DEFAULT 0,
  "data_dir" character varying(500),
  "srt_file_path" character varying(500),
  "created_at" timestamp with time zone DEFAULT now(),
  "updated_at" timestamp with time zone DEFAULT now(),
  "channel" text,
  CONSTRAINT "videos_pkey" PRIMARY KEY (id),
  CONSTRAINT "videos_status_check" CHECK (((status)::text = ANY ((ARRAY['processing'::character varying, 'completed'::character varying, 'error'::character varying])::text[])))
);

-- foreign keys
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'atom_annotations_atom_id_fkey') THEN ALTER TABLE public."atom_annotations" ADD CONSTRAINT "atom_annotations_atom_id_fkey" FOREIGN KEY (atom_id) REFERENCES atoms(id) ON DELETE CASCADE; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'atom_embeddings_atom_id_fkey') THEN ALTER TABLE public."atom_embeddings" ADD CONSTRAINT "atom_embeddings_atom_id_fkey" FOREIGN KEY (atom_id) REFERENCES atoms(id) ON DELETE CASCADE; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'atom_entities_atom_id_fkey') THEN ALTER TABLE public."atom_entities" ADD CONSTRAINT "atom_entities_atom_id_fkey" FOREIGN KEY (atom_id) REFERENCES atoms(id) ON DELETE CASCADE; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'atom_entities_global_entity_id_fkey') THEN ALTER TABLE public."atom_entities" ADD CONSTRAINT "atom_entities_global_entity_id_fkey" FOREIGN KEY (global_entity_id) REFERENCES entities(id) ON DELETE SET NULL; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'atom_topics_atom_id_fkey') THEN ALTER TABLE public."atom_topics" ADD CONSTRAINT "atom_topics_atom_id_fkey" FOREIGN KEY (atom_id) REFERENCES atoms(id) ON DELETE CASCADE; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'atom_topics_topic_id_fkey') THEN ALTER TABLE public."atom_topics" ADD CONSTRAINT "atom_topics_topic_id_fkey" FOREIGN KEY (topic_id) REFERENCES topics(id) ON DELETE CASCADE; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'atoms_segment_id_fkey') THEN ALTER TABLE public."atoms" ADD CONSTRAINT "atoms_segment_id_fkey" FOREIGN KEY (segment_id) REFERENCES narrative_segments(id) ON DELETE SET NULL; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'atoms_video_id_fkey') THEN ALTER TABLE public."atoms" ADD CONSTRAINT "atoms_video_id_fkey" FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'chat_messages_conversation_id_fkey') THEN ALTER TABLE public."chat_messages" ADD CONSTRAINT "chat_messages_conversation_id_fkey" FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'conversations_user_id_fkey') THEN ALTER TABLE public."conversations" ADD CONSTRAINT "conversations_user_id_fkey" FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'credit_transactions_user_id_fkey') THEN ALTER TABLE public."credit_transactions" ADD CONSTRAINT "credit_transactions_user_id_fkey" FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'narrative_segments_video_id_fkey') THEN ALTER TABLE public."narrative_segments" ADD CONSTRAINT "narrative_segments_video_id_fkey" FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ng_stock_txn_location_fkey') THEN ALTER TABLE public."ng_stock_txn" ADD CONSTRAINT "ng_stock_txn_location_fkey" FOREIGN KEY (location) REFERENCES ng_location(code); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'project_videos_project_id_fkey') THEN ALTER TABLE public."project_videos" ADD CONSTRAINT "project_videos_project_id_fkey" FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'project_videos_video_id_fkey') THEN ALTER TABLE public."project_videos" ADD CONSTRAINT "project_videos_video_id_fkey" FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'user_credits_user_id_fkey') THEN ALTER TABLE public."user_credits" ADD CONSTRAINT "user_credits_user_id_fkey" FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'user_roles_user_id_fkey') THEN ALTER TABLE public."user_roles" ADD CONSTRAINT "user_roles_user_id_fkey" FOREIGN KEY (user_id) REFERENCES auth.users(id) ON DELETE CASCADE; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'video_stats_video_id_fkey') THEN ALTER TABLE public."video_stats" ADD CONSTRAINT "video_stats_video_id_fkey" FOREIGN KEY (video_id) REFERENCES videos(id) ON DELETE CASCADE; END IF; END $$;

-- indexes
CREATE INDEX IF NOT EXISTS idx_annotations_embedding_status ON public.atom_annotations USING btree (embedding_status);
CREATE INDEX IF NOT EXISTS idx_annotations_importance_score ON public.atom_annotations USING btree (importance_score);
CREATE INDEX IF NOT EXISTS idx_annotations_topics ON public.atom_annotations USING gin (topics);
CREATE INDEX IF NOT EXISTS idx_atom_embeddings_atom_id ON public.atom_embeddings USING btree (atom_id);
CREATE INDEX IF NOT EXISTS idx_atom_embeddings_hnsw ON public.atom_embeddings USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_atom_embeddings_status ON public.atom_embeddings USING btree (status);
CREATE INDEX IF NOT EXISTS idx_atom_embeddings_video_id ON public.atom_embeddings USING btree (video_id);
CREATE INDEX IF NOT EXISTS idx_atom_entities_atom_id ON public.atom_entities USING btree (atom_id);
CREATE INDEX IF NOT EXISTS idx_atom_entities_entity_name ON public.atom_entities USING btree (entity_name);
CREATE INDEX IF NOT EXISTS idx_atom_entities_global_entity_id ON public.atom_entities USING btree (global_entity_id);
CREATE INDEX IF NOT EXISTS idx_atom_entities_video_id ON public.atom_entities USING btree (video_id);
CREATE INDEX IF NOT EXISTS idx_atom_topics_atom_id ON public.atom_topics USING btree (atom_id);
CREATE INDEX IF NOT EXISTS idx_atom_topics_topic_id ON public.atom_topics USING btree (topic_id);
CREATE INDEX IF NOT EXISTS idx_atoms_segment_id ON public.atoms USING btree (segment_id);
CREATE INDEX IF NOT EXISTS idx_atoms_start_ms ON public.atoms USING btree (start_ms);
CREATE INDEX IF NOT EXISTS idx_atoms_type ON public.atoms USING btree (type);
CREATE INDEX IF NOT EXISTS idx_atoms_video_id ON public.atoms USING btree (video_id);
CREATE INDEX IF NOT EXISTS idx_chat_logs_created ON public.chat_logs USING btree (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_logs_guest_ip ON public.chat_logs USING btree (guest_ip, created_at) WHERE (guest_ip IS NOT NULL);
CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation_id ON public.chat_messages USING btree (conversation_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_created_at ON public.chat_messages USING btree (created_at);
CREATE INDEX IF NOT EXISTS idx_conversations_updated_at ON public.conversations USING btree (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON public.conversations USING btree (user_id);
CREATE INDEX IF NOT EXISTS idx_credit_transactions_stripe_session ON public.credit_transactions USING btree (stripe_session_id) WHERE (stripe_session_id IS NOT NULL);
CREATE INDEX IF NOT EXISTS idx_credit_transactions_user_id ON public.credit_transactions USING btree (user_id);
CREATE INDEX IF NOT EXISTS idx_entities_aliases ON public.entities USING gin (aliases);
CREATE INDEX IF NOT EXISTS idx_entities_importance_score ON public.entities USING btree (importance_score);
CREATE INDEX IF NOT EXISTS idx_entities_type ON public.entities USING btree (entity_type);
CREATE INDEX IF NOT EXISTS idx_narrative_segments_video_id ON public.narrative_segments USING btree (video_id);
CREATE INDEX IF NOT EXISTS idx_segments_start_ms ON public.narrative_segments USING btree (start_ms);
CREATE INDEX IF NOT EXISTS idx_segments_topics ON public.narrative_segments USING gin (topics);
CREATE INDEX IF NOT EXISTS idx_segments_video_id ON public.narrative_segments USING btree (video_id);
CREATE INDEX IF NOT EXISTS idx_ng_txn_date ON public.ng_stock_txn USING btree (txn_date DESC);
CREATE INDEX IF NOT EXISTS idx_ng_txn_loc ON public.ng_stock_txn USING btree (location);
CREATE INDEX IF NOT EXISTS idx_ng_txn_mat ON public.ng_stock_txn USING btree (material_key);
CREATE INDEX IF NOT EXISTS idx_ng_txn_ref ON public.ng_stock_txn USING btree (ref_type, ref_no);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ng_in_ref ON public.ng_stock_txn USING btree (ref_type, ref_no) WHERE ((direction = 'in'::text) AND (revoked = false) AND (ref_type IS NOT NULL) AND (ref_no IS NOT NULL));
CREATE INDEX IF NOT EXISTS person_career_name_idx ON public.person_career USING btree (person_name);
CREATE INDEX IF NOT EXISTS person_career_person_idx ON public.person_career USING btree (person_id);
CREATE INDEX IF NOT EXISTS idx_person_profiles_name ON public.person_profiles USING btree (name);
CREATE INDEX IF NOT EXISTS person_relations_a_idx ON public.person_relations USING btree (person_a_id);
CREATE INDEX IF NOT EXISTS person_relations_b_idx ON public.person_relations USING btree (person_b_id);
CREATE INDEX IF NOT EXISTS person_relations_name_a_idx ON public.person_relations USING btree (person_a_name);
CREATE INDEX IF NOT EXISTS person_relations_name_b_idx ON public.person_relations USING btree (person_b_name);
CREATE INDEX IF NOT EXISTS person_relations_type_idx ON public.person_relations USING btree (relation_type);
CREATE INDEX IF NOT EXISTS idx_quotes_persona ON public.persona_quotes USING btree (persona);
CREATE INDEX IF NOT EXISTS idx_viewpoints_persona ON public.persona_viewpoints USING btree (persona);
CREATE INDEX IF NOT EXISTS persons_name_idx ON public.persons USING btree (name);
CREATE INDEX IF NOT EXISTS idx_pipeline_jobs_url_created ON public.pipeline_jobs USING btree (youtube_url, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_roundtables_user ON public.roundtables USING btree (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_video_assets_status ON public.video_assets USING btree (status);
CREATE INDEX IF NOT EXISTS idx_video_assets_video_id ON public.video_assets USING btree (video_id);
CREATE INDEX IF NOT EXISTS idx_videos_channel ON public.videos USING btree (channel);
CREATE INDEX IF NOT EXISTS idx_videos_created_at ON public.videos USING btree (created_at);
CREATE INDEX IF NOT EXISTS idx_videos_status ON public.videos USING btree (status);

-- functions
CREATE OR REPLACE FUNCTION public.get_video_stats(p_video_ids text[])
 RETURNS TABLE(video_id text, atom_count bigint, segment_count bigint, topic_count bigint, entity_count bigint, embedding_count bigint)
 LANGUAGE sql
 STABLE
AS $function$
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
$function$;

CREATE OR REPLACE FUNCTION public.match_atoms_v1(query_embedding vector, match_count integer DEFAULT 20, video_ids_filter text[] DEFAULT NULL::text[])
 RETURNS TABLE(atom_id character varying, video_id character varying, similarity double precision)
 LANGUAGE plpgsql
AS $function$
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
$function$;

CREATE OR REPLACE FUNCTION public.match_quotes(p_persona text, query_embedding vector, match_count integer)
 RETURNS TABLE(id uuid, quote text, context text, atom_id text, similarity double precision)
 LANGUAGE sql
 STABLE
AS $function$
  SELECT s.id, s.quote, s.context, s.atom_id,
         1 - (s.embedding <=> query_embedding) AS similarity
  FROM persona_quotes s
  WHERE s.persona = p_persona AND s.embedding IS NOT NULL
  ORDER BY s.embedding <=> query_embedding
  LIMIT match_count;
$function$;

CREATE OR REPLACE FUNCTION public.match_viewpoints(p_persona text, query_embedding vector, match_count integer)
 RETURNS TABLE(id uuid, topic text, stance text, reasoning text, confidence text, quote text, atom_ids text[], similarity double precision)
 LANGUAGE sql
 STABLE
AS $function$
  SELECT v.id, v.topic, v.stance, v.reasoning, v.confidence, v.quote, v.atom_ids,
         1 - (v.embedding <=> query_embedding) AS similarity
  FROM persona_viewpoints v
  WHERE v.persona = p_persona AND v.embedding IS NOT NULL
  ORDER BY v.embedding <=> query_embedding
  LIMIT match_count;
$function$;

CREATE OR REPLACE FUNCTION public.set_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.update_app_settings_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.update_pipeline_jobs_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.update_user_credits_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$function$;

CREATE OR REPLACE FUNCTION public.update_video_assets_updated_at()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$function$;

-- triggers
DROP TRIGGER IF EXISTS "app_settings_updated_at" ON public."app_settings";
CREATE TRIGGER "app_settings_updated_at" BEFORE UPDATE ON public."app_settings" FOR EACH ROW EXECUTE FUNCTION update_app_settings_updated_at();
DROP TRIGGER IF EXISTS "person_profiles_updated_at" ON public."person_profiles";
CREATE TRIGGER "person_profiles_updated_at" BEFORE UPDATE ON public."person_profiles" FOR EACH ROW EXECUTE FUNCTION set_updated_at();
DROP TRIGGER IF EXISTS "pipeline_jobs_updated_at" ON public."pipeline_jobs";
CREATE TRIGGER "pipeline_jobs_updated_at" BEFORE UPDATE ON public."pipeline_jobs" FOR EACH ROW EXECUTE FUNCTION update_pipeline_jobs_updated_at();
DROP TRIGGER IF EXISTS "user_credits_updated_at" ON public."user_credits";
CREATE TRIGGER "user_credits_updated_at" BEFORE UPDATE ON public."user_credits" FOR EACH ROW EXECUTE FUNCTION update_user_credits_updated_at();
DROP TRIGGER IF EXISTS "video_assets_updated_at" ON public."video_assets";
CREATE TRIGGER "video_assets_updated_at" BEFORE UPDATE ON public."video_assets" FOR EACH ROW EXECUTE FUNCTION update_video_assets_updated_at();

-- row level security
ALTER TABLE public."chat_messages" ENABLE ROW LEVEL SECURITY;
ALTER TABLE public."conversations" ENABLE ROW LEVEL SECURITY;
ALTER TABLE public."credit_transactions" ENABLE ROW LEVEL SECURITY;
ALTER TABLE public."ng_location" ENABLE ROW LEVEL SECURITY;
ALTER TABLE public."ng_stock_txn" ENABLE ROW LEVEL SECURITY;
ALTER TABLE public."user_credits" ENABLE ROW LEVEL SECURITY;
ALTER TABLE public."user_roles" ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Users manage own messages" ON public."chat_messages";
CREATE POLICY "Users manage own messages" ON public."chat_messages" FOR ALL TO public USING ((conversation_id IN ( SELECT conversations.id
   FROM conversations
  WHERE (conversations.user_id = auth.uid()))));
DROP POLICY IF EXISTS "Users manage own conversations" ON public."conversations";
CREATE POLICY "Users manage own conversations" ON public."conversations" FOR ALL TO public USING ((auth.uid() = user_id));
DROP POLICY IF EXISTS "Users can read own transactions" ON public."credit_transactions";
CREATE POLICY "Users can read own transactions" ON public."credit_transactions" FOR SELECT TO public USING ((auth.uid() = user_id));
DROP POLICY IF EXISTS "Users can read own credits" ON public."user_credits";
CREATE POLICY "Users can read own credits" ON public."user_credits" FOR SELECT TO public USING ((auth.uid() = user_id));
DROP POLICY IF EXISTS "Users can read own role" ON public."user_roles";
CREATE POLICY "Users can read own role" ON public."user_roles" FOR SELECT TO public USING ((auth.uid() = user_id));

-- storage buckets
INSERT INTO storage.buckets (id, name, public) VALUES ('audio', 'audio', true) ON CONFLICT (id) DO NOTHING;
INSERT INTO storage.buckets (id, name, public) VALUES ('subtitles', 'subtitles', true) ON CONFLICT (id) DO NOTHING;
