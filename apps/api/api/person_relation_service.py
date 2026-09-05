# -*- coding: utf-8 -*-
"""
PersonRelationService
- Batch extracts directed person-to-person relations from atom text
- Stores in person_relations + person_career tables
- Provides graph traversal for multi-hop Q&A
- Syncs persons table from network cache
- Tavily web enrichment → career timeline
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

RELATION_TYPES = [
    "promoted",       # A提拔B
    "appointed",      # A任命B
    "investigated",   # A查处B
    "cooperated",     # 共事
    "succeeded",      # A接替B职位
    "opposed",        # 对立/斗争
    "mentored",       # A提携/教导B
    "allied",         # 结盟
    "protected",      # A庇护B
    "betrayed",       # A出卖/举报B
    "competed",       # 争同一职位
    "family",         # 亲属
    "co_mentioned",   # 共同出现，无明确关系
]

RELATION_TYPE_ZH = {
    "promoted": "提拔",
    "appointed": "任命",
    "investigated": "查处",
    "cooperated": "共事",
    "succeeded": "接替",
    "opposed": "对立",
    "mentored": "提携",
    "allied": "结盟",
    "protected": "庇护",
    "betrayed": "出卖",
    "competed": "竞争",
    "family": "亲属",
    "co_mentioned": "共同提及",
}


def _person_id(name: str) -> str:
    return "P_" + hashlib.md5(name.encode("utf-8")).hexdigest()[:12]


class PersonRelationService:
    def __init__(self, supabase_client: Any, claude_api_key: str = "", tavily_api_key: str = ""):
        self.supabase = supabase_client
        self.claude_api_key = claude_api_key
        self.tavily_api_key = tavily_api_key

    # ─────────────────────────────────────────────────────────────
    # 1. Batch relation extraction from atoms
    # ─────────────────────────────────────────────────────────────

    def extract_relations_for_videos(
        self,
        video_ids: List[str],
        batch_size: int = 12,
        model: str = "claude-haiku-4-5",
    ) -> Dict[str, Any]:
        """Extract directed relations from atoms of given videos and persist."""
        if not self.claude_api_key:
            raise RuntimeError("CLAUDE_API_KEY not set")

        from utils import ClaudeClient
        client = ClaudeClient(self.claude_api_key)

        total_relations = 0
        total_career = 0
        stats_by_video: Dict[str, Any] = {}

        for video_id in video_ids:
            atoms = self._load_atoms(video_id)
            if not atoms:
                stats_by_video[video_id] = {"skipped": True, "reason": "no atoms"}
                continue

            video_relations: List[Dict] = []
            video_career: List[Dict] = []

            # Process in batches
            for i in range(0, len(atoms), batch_size):
                batch = atoms[i: i + batch_size]
                texts = [
                    f"[{j+1}] {a.get('merged_text','')[:220]}"
                    for j, a in enumerate(batch)
                ]
                atom_ids = [a.get("id") or a.get("atom_id") for a in batch]

                rels, careers = self._extract_batch(
                    texts=texts,
                    atom_ids=atom_ids,
                    video_id=video_id,
                    client=client,
                    model=model,
                )
                video_relations.extend(rels)
                video_career.extend(careers)
                time.sleep(0.3)  # rate limit buffer

            # Persist
            if video_relations:
                self._save_relations(video_relations)
            if video_career:
                self._save_career(video_career)

            stats_by_video[video_id] = {
                "atom_count": len(atoms),
                "relations_extracted": len(video_relations),
                "career_events_extracted": len(video_career),
            }
            total_relations += len(video_relations)
            total_career += len(video_career)

        return {
            "total_relations": total_relations,
            "total_career_events": total_career,
            "by_video": stats_by_video,
        }

    def _extract_batch(
        self,
        texts: List[str],
        atom_ids: List[Optional[str]],
        video_id: str,
        client: Any,
        model: str,
    ) -> Tuple[List[Dict], List[Dict]]:
        combined = "\n".join(texts)
        prompt = f"""你是中国政治人物关系分析专家。从以下视频字幕原文中提取两类结构化信息。

【原文】
{combined}

【任务A】提取人物之间的有向关系：
只提取文本中有明确描述的关系，不推测。两端必须都是具体人名。

关系类型（必须从此列表选）：
promoted（A提拔B）/ appointed（A任命B）/ investigated（A查处B）/
cooperated（共事）/ succeeded（A接替B职位）/ opposed（对立/斗争）/
mentored（A提携/教导B）/ allied（结盟）/ protected（A庇护B）/
betrayed（A出卖/举报B）/ competed（争同一职位）/ family（亲属）

【任务B】提取人物职业事件：
任职、升任、调任、免职、被查、退休等。

输出严格JSON格式：
{{
  "relations": [
    {{
      "person_a": "施动方人名",
      "person_b": "受动方人名",
      "relation_type": "关系类型英文",
      "direction": "a_to_b|b_to_a|bidirectional",
      "time_context": "时间（可空）",
      "organization": "相关机构（可空）",
      "description": "一句话描述20字内",
      "confidence": 0.0到1.0,
      "source_ref": 引用原文编号如1或2
    }}
  ],
  "career_events": [
    {{
      "person_name": "人名",
      "position": "职位",
      "organization": "机构",
      "event_type": "appointment|promotion|transfer|removal|investigation",
      "time_start": "开始时间（可空）",
      "time_end": "结束时间（可空）",
      "power_level": "正国|副国|正省部|副省部|正厅局|副厅局|其他",
      "confidence": 0.0到1.0,
      "source_ref": 引用原文编号
    }}
  ]
}}
无关系则relations返回[]，无职业事件则career_events返回[]。"""

        try:
            raw = client.call(prompt=prompt, model=model, max_tokens=2000)
            data = self._parse_json(raw)
            if not isinstance(data, dict):
                return [], []

            relations = []
            for r in (data.get("relations") or []):
                if not isinstance(r, dict):
                    continue
                pa = self._clean_name(r.get("person_a"))
                pb = self._clean_name(r.get("person_b"))
                rtype = str(r.get("relation_type") or "").strip()
                if not pa or not pb or pa == pb:
                    continue
                if rtype not in RELATION_TYPES:
                    rtype = "co_mentioned"
                ref = r.get("source_ref")
                src_atom = atom_ids[int(ref) - 1] if ref and str(ref).isdigit() and 0 < int(ref) <= len(atom_ids) else None
                relations.append({
                    "person_a_id": _person_id(pa),
                    "person_a_name": pa,
                    "person_b_id": _person_id(pb),
                    "person_b_name": pb,
                    "relation_type": rtype,
                    "direction": str(r.get("direction") or "a_to_b"),
                    "time_context": str(r.get("time_context") or "")[:80] or None,
                    "organization": str(r.get("organization") or "")[:120] or None,
                    "description": str(r.get("description") or "")[:120] or None,
                    "confidence": min(1.0, max(0.0, float(r.get("confidence") or 0.7))),
                    "source": "corpus",
                    "source_atom_id": src_atom,
                    "video_id": video_id,
                })

            careers = []
            for c in (data.get("career_events") or []):
                if not isinstance(c, dict):
                    continue
                pname = self._clean_name(c.get("person_name"))
                if not pname:
                    continue
                ref = c.get("source_ref")
                src_atom = atom_ids[int(ref) - 1] if ref and str(ref).isdigit() and 0 < int(ref) <= len(atom_ids) else None
                careers.append({
                    "person_id": _person_id(pname),
                    "person_name": pname,
                    "position": str(c.get("position") or "")[:120] or None,
                    "organization": str(c.get("organization") or "")[:120] or None,
                    "event_type": str(c.get("event_type") or "appointment")[:40],
                    "time_start": str(c.get("time_start") or "")[:40] or None,
                    "time_end": str(c.get("time_end") or "")[:40] or None,
                    "power_level": str(c.get("power_level") or "")[:20] or None,
                    "confidence": min(1.0, max(0.0, float(c.get("confidence") or 0.7))),
                    "source": "corpus",
                    "source_atom_id": src_atom,
                })
            return relations, careers
        except Exception:
            return [], []

    # ─────────────────────────────────────────────────────────────
    # 2. Tavily web enrichment → person_career
    # ─────────────────────────────────────────────────────────────

    def enrich_top_persons_from_web(
        self,
        person_names: List[str],
        model: str = "claude-haiku-4-5",
    ) -> Dict[str, Any]:
        """Tavily search + Claude extract career timeline for each person."""
        if not self.tavily_api_key:
            raise RuntimeError("TAVILY_API_KEY not set")
        try:
            from tavily import TavilyClient
        except ImportError:
            raise RuntimeError("tavily-python not installed")
        if not self.claude_api_key:
            raise RuntimeError("CLAUDE_API_KEY not set")
        from utils import ClaudeClient

        tavily = TavilyClient(api_key=self.tavily_api_key)
        claude = ClaudeClient(self.claude_api_key)
        results = {}

        for name in person_names:
            try:
                result = tavily.search(
                    query=f"{name} 中国官员 履历 任职",
                    search_depth="advanced",
                    max_results=5,
                )
                snippets = [
                    f"[{item.get('url','')}]\n{item.get('content','')[:500]}"
                    for item in (result.get("results") or [])
                    if item.get("content")
                ]
                if not snippets:
                    results[name] = {"skipped": True, "reason": "no web results"}
                    continue

                web_text = "\n\n".join(snippets[:5])

                # Claude to extract structured career
                prompt = f"""从以下关于"{name}"的公开资料中，提取其职业履历。

【资料】
{web_text[:3000]}

输出JSON数组，每项格式：
{{
  "position": "职务",
  "organization": "机构",
  "event_type": "appointment|promotion|transfer|removal|investigation",
  "time_start": "开始时间",
  "time_end": "结束时间（在任则空）",
  "power_level": "正国|副国|正省部|副省部|正厅局|副厅局|其他",
  "confidence": 0.0到1.0
}}
无法确认的不要输出。"""

                raw = claude.call(prompt=prompt, model=model, max_tokens=1200)
                items = self._parse_json_array(raw)
                pid = _person_id(name)

                career_rows = []
                for item in (items or []):
                    if not isinstance(item, dict):
                        continue
                    career_rows.append({
                        "person_id": pid,
                        "person_name": name,
                        "position": str(item.get("position") or "")[:120] or None,
                        "organization": str(item.get("organization") or "")[:120] or None,
                        "event_type": str(item.get("event_type") or "appointment")[:40],
                        "time_start": str(item.get("time_start") or "")[:40] or None,
                        "time_end": str(item.get("time_end") or "")[:40] or None,
                        "power_level": str(item.get("power_level") or "")[:20] or None,
                        "confidence": min(1.0, max(0.0, float(item.get("confidence") or 0.7))),
                        "source": "web",
                    })

                if career_rows:
                    self._save_career(career_rows)

                # Cache web profile
                profile_text = "\n\n".join(snippets[:3])
                self.supabase.table("app_settings").upsert({
                    "key": f"person_web_profile_v1_{pid}",
                    "value": profile_text,
                }).execute()

                results[name] = {"career_events": len(career_rows), "web_cached": True}
                time.sleep(1.0)
            except Exception as e:
                results[name] = {"error": str(e)[:100]}

        return {"enriched": results}

    # ─────────────────────────────────────────────────────────────
    # 3. persons table sync
    # ─────────────────────────────────────────────────────────────

    def sync_persons_from_cache(self, network_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Upsert persons table from network cache payload."""
        persons = network_payload.get("persons") or []
        rows = []
        for p in persons:
            rows.append({
                "id": p.get("person_id"),
                "name": p.get("name"),
                "category": p.get("category", "other"),
                "category_confidence": p.get("category_confidence", 0.4),
                "mention_count": p.get("mention_count", 0),
                "video_ids": list(p.get("evidence") and {e.get("video_id") for e in p["evidence"] if e.get("video_id")} or []),
                "framework_tags": json.dumps(p.get("framework_tags") or []),
            })
        if not rows:
            return {"synced": 0}
        # Upsert in batches
        for i in range(0, len(rows), 50):
            batch = rows[i:i + 50]
            self.supabase.table("persons").upsert(batch, on_conflict="id").execute()
        return {"synced": len(rows)}

    # ─────────────────────────────────────────────────────────────
    # 4. Graph traversal for Q&A
    # ─────────────────────────────────────────────────────────────

    def get_person_graph_context(
        self,
        person_id: str,
        person_name: str,
        hops: int = 2,
    ) -> Dict[str, Any]:
        """Gather multi-hop graph context for Q&A: relations + career + neighbors."""
        # Direct relations (1-hop)
        direct_rels = self._load_person_relations(person_id, person_name)

        # Neighbor IDs (2-hop)
        neighbor_ids: Set[str] = set()
        neighbor_names: Dict[str, str] = {}
        for r in direct_rels:
            if r.get("person_a_id") == person_id:
                neighbor_ids.add(r["person_b_id"])
                neighbor_names[r["person_b_id"]] = r["person_b_name"]
            else:
                neighbor_ids.add(r["person_a_id"])
                neighbor_names[r["person_a_id"]] = r["person_a_name"]

        # Career events for this person
        career = self._load_person_career(person_id, person_name)

        # Indirect relations between neighbors (for triangles)
        indirect_rels: List[Dict] = []
        if hops >= 2 and neighbor_ids:
            indirect_rels = self._load_relations_between(list(neighbor_ids)[:15])

        return {
            "direct_relations": direct_rels,
            "career_events": career,
            "neighbors": [
                {"id": nid, "name": nname}
                for nid, nname in neighbor_names.items()
            ],
            "indirect_relations": indirect_rels,
        }

    def _load_person_relations(self, person_id: str, person_name: str) -> List[Dict]:
        try:
            res_a = (
                self.supabase.table("person_relations")
                .select("*")
                .eq("person_a_id", person_id)
                .order("confidence", desc=True)
                .limit(60)
                .execute()
            )
            res_b = (
                self.supabase.table("person_relations")
                .select("*")
                .eq("person_b_id", person_id)
                .order("confidence", desc=True)
                .limit(60)
                .execute()
            )
            # Also search by name for any that used a different ID hash
            res_na = (
                self.supabase.table("person_relations")
                .select("*")
                .eq("person_a_name", person_name)
                .limit(30)
                .execute()
            )
            res_nb = (
                self.supabase.table("person_relations")
                .select("*")
                .eq("person_b_name", person_name)
                .limit(30)
                .execute()
            )
            seen: Set[str] = set()
            out = []
            for row in (res_a.data or []) + (res_b.data or []) + (res_na.data or []) + (res_nb.data or []):
                rid = str(row.get("id", ""))
                if rid in seen:
                    continue
                seen.add(rid)
                out.append(row)
            return out
        except Exception:
            return []

    def _load_person_career(self, person_id: str, person_name: str) -> List[Dict]:
        try:
            res = (
                self.supabase.table("person_career")
                .select("*")
                .or_(f"person_id.eq.{person_id},person_name.eq.{person_name}")
                .order("time_start", desc=False)
                .limit(50)
                .execute()
            )
            return res.data or []
        except Exception:
            return []

    def _load_relations_between(self, person_ids: List[str]) -> List[Dict]:
        try:
            res = (
                self.supabase.table("person_relations")
                .select("person_a_name,person_b_name,relation_type,direction,description")
                .in_("person_a_id", person_ids)
                .in_("person_b_id", person_ids)
                .limit(40)
                .execute()
            )
            return res.data or []
        except Exception:
            return []

    # ─────────────────────────────────────────────────────────────
    # 5. Build Q&A context string
    # ─────────────────────────────────────────────────────────────

    def build_context_for_qa(
        self,
        person_id: str,
        person_name: str,
        atom_texts: List[Dict],
        web_profile: Optional[str] = None,
    ) -> Tuple[str, List[Dict]]:
        """Build rich context string and source refs for ask_person."""
        graph = self.get_person_graph_context(person_id, person_name)
        parts: List[str] = []
        source_refs: List[Dict] = []

        # Career timeline
        career = graph.get("career_events") or []
        if career:
            lines = []
            for ev in career[:30]:
                t_start = ev.get("time_start") or ""
                t_end = ev.get("time_end") or ""
                t = f"{t_start}-{t_end}".strip("-") or "时间不明"
                pos = ev.get("position") or ""
                org = ev.get("organization") or ""
                lvl = ev.get("power_level") or ""
                src = ev.get("source") or "corpus"
                lines.append(f"• {t} | {pos} @ {org} [{lvl}] ({src})")
            parts.append("=== 职业履历 ===\n" + "\n".join(lines))

        # Direct relations
        rels = graph.get("direct_relations") or []
        if rels:
            lines = []
            for r in rels[:30]:
                if r.get("person_a_id") == person_id or r.get("person_a_name") == person_name:
                    other = r.get("person_b_name", "")
                    direction = "→"
                else:
                    other = r.get("person_a_name", "")
                    direction = "←"
                rtype_zh = RELATION_TYPE_ZH.get(r.get("relation_type", ""), r.get("relation_type", ""))
                t = r.get("time_context") or ""
                desc = r.get("description") or ""
                lines.append(f"• {direction} {other} [{rtype_zh}] {t}  {desc}")
            parts.append("=== 人物关系脉络 ===\n" + "\n".join(lines))

        # Indirect (triangles)
        indirect = graph.get("indirect_relations") or []
        if indirect:
            lines = [
                f"• {r.get('person_a_name')} ↔ {r.get('person_b_name')} [{RELATION_TYPE_ZH.get(r.get('relation_type',''), r.get('relation_type',''))}]"
                for r in indirect[:15]
            ]
            parts.append("=== 关系圈 ===\n" + "\n".join(lines))

        # Web profile
        if web_profile:
            parts.append("=== 公开资料 ===\n" + web_profile[:600])

        # Atom evidence with numbered citations
        idx = 1
        for a in atom_texts[:40]:
            txt = (a.get("text") or a.get("merged_text") or "")[:220]
            if not txt:
                continue
            vid = a.get("video_id") or ""
            aid = a.get("atom_id") or a.get("id") or ""
            parts.append(f"[{idx}] {txt}")
            source_refs.append({
                "index": idx,
                "video_id": vid,
                "atom_id": aid,
                "text": txt[:100],
                "analysis_url": f"/analysis?projectId={vid}#atom-{aid}" if vid and aid else None,
            })
            idx += 1

        return "\n\n".join(parts), source_refs

    # ─────────────────────────────────────────────────────────────
    # DB helpers
    # ─────────────────────────────────────────────────────────────

    def _save_relations(self, rows: List[Dict]) -> None:
        if not rows:
            return
        try:
            for i in range(0, len(rows), 100):
                self.supabase.table("person_relations").insert(rows[i:i + 100]).execute()
        except Exception:
            pass

    def _save_career(self, rows: List[Dict]) -> None:
        if not rows:
            return
        try:
            for i in range(0, len(rows), 100):
                self.supabase.table("person_career").insert(rows[i:i + 100]).execute()
        except Exception:
            pass

    def _load_atoms(self, video_id: str) -> List[Dict]:
        try:
            res = (
                self.supabase.table("atoms")
                .select("id,merged_text,video_id")
                .eq("video_id", video_id)
                .order("start_ms")
                .limit(500)
                .execute()
            )
            return res.data or []
        except Exception:
            return []

    # ─────────────────────────────────────────────────────────────
    # Parsing helpers
    # ─────────────────────────────────────────────────────────────

    def _parse_json(self, text: str) -> Any:
        raw = text.strip()
        # Try direct parse
        try:
            return json.loads(raw)
        except Exception:
            pass
        # Extract JSON object
        m = re.search(r"(\{[\s\S]*\})", raw)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
        # Extract JSON array
        m2 = re.search(r"(\[[\s\S]*\])", raw)
        if m2:
            try:
                return json.loads(m2.group(1))
            except Exception:
                pass
        return None

    def _parse_json_array(self, text: str) -> List[Any]:
        data = self._parse_json(text)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    return v
        return []

    def _clean_name(self, name: Any) -> str:
        if not name:
            return ""
        text = re.sub(r"\s+", "", str(name).strip())
        if len(text) < 2 or len(text) > 10:
            return ""
        return text

    def get_relation_stats(self, video_ids: Optional[List[str]] = None) -> Dict[str, Any]:
        """Get stats on person_relations and person_career tables."""
        try:
            q = self.supabase.table("person_relations").select("id", count="exact")
            if video_ids:
                q = q.in_("video_id", video_ids)
            rel_res = q.execute()
            rel_count = rel_res.count or len(rel_res.data or [])
        except Exception:
            rel_count = -1

        try:
            car_res = self.supabase.table("person_career").select("id", count="exact").execute()
            car_count = car_res.count or len(car_res.data or [])
        except Exception:
            car_count = -1

        try:
            per_res = self.supabase.table("persons").select("id", count="exact").execute()
            per_count = per_res.count or len(per_res.data or [])
        except Exception:
            per_count = -1

        return {
            "person_relations_count": rel_count,
            "person_career_count": car_count,
            "persons_count": per_count,
        }
