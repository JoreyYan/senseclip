export const API_BASE = (import.meta.env.VITE_API_URL as string) || "http://localhost:8000";

export const CATEGORY_COLORS: Record<string, string> = {
  political_actor: "#ef4444",  // red
  business_actor: "#eab308",   // gold
  family_member: "#22c55e",    // green
  military_actor: "#3b82f6",   // blue
  media_actor: "#a855f7",      // purple
  other: "#6b7280",            // gray
};

export const CATEGORY_ZH: Record<string, string> = {
  political_actor: "政治人物",
  business_actor: "商界人物",
  family_member: "家族成员",
  media_actor: "媒体人物",
  military_actor: "军事人物",
  other: "其他",
};

export const RELATION_TYPE_ZH: Record<string, string> = {
  family: "家族",
  superior_subordinate: "上下级",
  alliance: "同盟",
  conflict: "冲突",
  business_tie: "商业",
  mentor_successor: "师承/接任",
  co_mentioned: "共现",
  cooperated: "合作",
  patron_client: "庇护",
  faction_ally: "派系同盟",
};

export const RELATION_EDGE_STYLES: Record<string, { color: string; style: string }> = {
  family: { color: "#22c55e", style: "solid" },
  superior_subordinate: { color: "#ef4444", style: "solid" },
  alliance: { color: "#3b82f6", style: "solid" },
  conflict: { color: "#ef4444", style: "dashed" },
  business_tie: { color: "#eab308", style: "solid" },
  mentor_successor: { color: "#3b82f6", style: "solid" },
  co_mentioned: { color: "#4b5563", style: "solid" },
  cooperated: { color: "#06b6d4", style: "solid" },
  patron_client: { color: "#f97316", style: "solid" },
  faction_ally: { color: "#8b5cf6", style: "solid" },
};
