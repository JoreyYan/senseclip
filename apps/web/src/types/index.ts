export interface Person {
  person_id: string;
  name: string;
  category: string;
  category_confidence: number;
  mention_count: number;
  video_count: number;
  framework_tags: Array<{ dimension: string; score: number }>;
  evidence: Array<{ video_id: string; atom_id: string; text: string }>;
}

export interface CoRelation {
  source_person_id: string;
  source_name: string;
  target_person_id: string;
  target_name: string;
  relation_type: string;
  strength: number;
  video_count: number;
  evidence: Array<{ video_id: string; atom_id: string; text: string }>;
}

export interface DirectedEdge {
  id: string;
  source: string;
  source_name: string;
  target: string;
  target_name: string;
  relation_type: string;
  direction: string;
  time_context: string;
  description: string;
  confidence: number;
}

export interface DirectedNode {
  id: string;
  name: string;
  category: string;
  mention_count: number;
}

export interface CareerEvent {
  person_id: string;
  person_name: string;
  position: string;
  organization: string;
  power_level: string;
  time_start: string;
  time_end: string | null;
  event_type: string;
  confidence: number;
}

export interface PersonDetail {
  person: Person;
  ai_summary: string;
  career_events: CareerEvent[];
  relations: CoRelation[];
}

export interface PersonGraph {
  person_id: string;
  name: string;
  direct_relations: Array<{
    id: string;
    person_a_id: string;
    person_a_name: string;
    person_b_id: string;
    person_b_name: string;
    relation_type: string;
    direction: string;
    time_context: string;
    organization: string;
    description: string;
    confidence: number;
  }>;
  indirect_relations: Array<{
    source_name: string;
    bridge_name: string;
    target_name: string;
    path: string;
  }>;
  career_events: CareerEvent[];
}

export interface NetworkStats {
  video_count: number;
  person_count: number;
  relation_count: number;
  career_event_count: number;
  ai_enriched: boolean;
  alias_count: number;
  videos: Array<{ id: string; title: string }>;
}

export interface NetworkResponse {
  stats: NetworkStats;
  persons: Person[];
  relations: CoRelation[];
  career_routes: Array<{
    person_id: string;
    name: string;
    event_count: number;
    events: any[];
  }>;
}

export interface DirectedGraphResponse {
  nodes: DirectedNode[];
  edges: DirectedEdge[];
}

export interface AskResponse {
  answer: string;
  sources: Array<{ video_id: string; atom_id: string; text: string }>;
}
