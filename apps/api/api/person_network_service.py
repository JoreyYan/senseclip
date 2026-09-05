from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

from config import CLAUDE_API_KEY, TAVILY_API_KEY
from utils import ClaudeClient
from api.person_relation_service import PersonRelationService, RELATION_TYPE_ZH


class PersonNetworkService:
    """Build a cross-video person network from existing Supabase assets."""

    CACHE_KEY = "person_network_v1"
    TAXONOMY_KEY = "person_network_taxonomy_v1"
    ALIASES_KEY = "person_network_aliases_v1"
    DEFAULT_TAXONOMY = {
        "person_categories": {},
        "relation_keywords": {
            "family": ["妻子", "丈夫", "父亲", "母亲", "儿子", "女儿", "家族", "兄弟", "姐妹"],
            "superior_subordinate": ["上级", "下属", "提拔", "任命", "领导", "汇报", "直接对"],
            "alliance": ["合作", "支持", "结盟", "同盟", "关系密切", "配合"],
            "conflict": ["斗争", "冲突", "矛盾", "举报", "翻脸", "打压", "清洗", "批评"],
            "business_tie": ["合伙", "投资", "控股", "融资", "借款", "交易"],
            "mentor_successor": ["接任", "继任", "提携", "老领导", "推荐"],
        },
        "career_event_keywords": {
            "removal_or_investigation": ["被查", "落马", "调查", "双规", "免职", "撤职"],
            "promotion": ["升任", "晋升", "提拔", "升为"],
            "transfer": ["调任", "空降", "转任", "转到"],
            "appointment": ["担任", "出任", "任", "就任", "接任"],
        },
        "framework_dimensions": {
            "power_base": ["背景", "系统", "靠山", "提拔", "资源"],
            "career_path": ["升任", "调任", "出任", "担任", "晋升"],
            "faction_relation": ["派系", "关系", "阵营", "同盟", "斗争"],
            "turning_point": ["转折", "后来", "之后", "关键", "拐点"],
            "risk_signal": ["被查", "落马", "风险", "失败", "争议"],
        },
    }
    PERSON_STOPWORDS = {
        "老聂", "老爷子", "局长", "部长", "书记", "主任", "秘书", "专家", "官员",
        "家族", "人物", "领导", "朋友", "同事", "记者", "主持人", "博主", "老师",
        "父亲", "母亲", "妻子", "丈夫", "儿子", "女儿", "对方", "我们", "他们", "你们",
    }
    PERSON_BAD_SUFFIX = ("书记", "主任", "部长", "局长", "老师", "同志", "书记员")

    def __init__(self, supabase_client: Any):
        self.supabase = supabase_client
        self._progress_callback = None  # set before AI rebuild to receive progress updates

    def get_cached(self) -> Optional[Dict[str, Any]]:
        if not self.supabase:
            return None
        res = (
            self.supabase.table("app_settings")
            .select("value,updated_at")
            .eq("key", self.CACHE_KEY)
            .limit(1)
            .execute()
        )
        if not res.data:
            return None
        raw = res.data[0].get("value")
        if not raw:
            return None
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return None
        if isinstance(payload, dict):
            payload["_cached_updated_at"] = res.data[0].get("updated_at")
            return payload
        return None

    def rebuild(self, limit_videos: int = 200, use_ai: bool = False, ai_model: str = "claude-haiku-4-5") -> Dict[str, Any]:
        if not self.supabase:
            raise RuntimeError("Supabase not configured")
        self.taxonomy = self._load_taxonomy()
        self.person_alias_map = self._load_aliases()
        self.dynamic_lexicon = {
            "political_actor": [],
            "business_actor": [],
            "family_member": [],
            "media_actor": [],
            "military_actor": [],
        }

        videos, video_title_map = self._list_processed_videos(limit_videos=limit_videos)
        logger.info(f"[rebuild] _list_processed_videos returned {len(videos)} videos: {videos}")
        contributed_video_ids: Set[str] = set()
        person_stats: Dict[str, Dict[str, Any]] = {}
        relation_stats: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        person_events: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        org_counter: Counter = Counter()
        concept_counter: Counter = Counter()
        event_counter: Counter = Counter()

        for video_id in videos:
            atoms = self._load_atoms(video_id)
            logger.info(f"[rebuild] video={video_id} atoms={len(atoms)}")
            if not atoms:
                continue
            contributed_video_ids.add(video_id)
            atom_ids = [row.get("id") for row in atoms if row.get("id")]
            atom_entities = self._load_atom_entities_map(atom_ids)

            for atom in atoms:
                atom_id = atom.get("id")
                if not atom_id:
                    continue
                text = str(atom.get("merged_text") or "")
                entities = atom_entities.get(atom_id, [])
                for ent in entities:
                    ename = self._normalize_name(ent.get("entity_name"))
                    etype = str(ent.get("entity_type") or "").lower()
                    if not ename:
                        continue
                    if etype in {"organization", "机构", "org"}:
                        org_counter[ename] += 1
                    elif etype in {"concept", "概念"}:
                        concept_counter[ename] += 1
                    elif etype in {"event", "事件"}:
                        event_counter[ename] += 1

                persons = sorted({
                    self._normalize_person_name(e.get("entity_name"))
                    for e in entities
                    if self._is_person(e) and self._is_valid_person_name(e.get("entity_name"))
                })
                persons = [p for p in persons if p]
                orgs = sorted({self._normalize_name(e.get("entity_name")) for e in entities if self._is_org(e)})
                times = sorted({self._normalize_name(e.get("entity_name")) for e in entities if self._is_time(e)})

                for person in persons:
                    stat = person_stats.setdefault(person, {
                        "name": person,
                        "mentions": 0,
                        "video_ids": set(),
                        "atom_ids": set(),
                        "evidence": [],
                        "contexts": [],
                    })
                    stat["mentions"] += 1
                    stat["video_ids"].add(video_id)
                    stat["atom_ids"].add(atom_id)
                    if len(stat["evidence"]) < 8:
                        stat["evidence"].append({
                            "video_id": video_id,
                            "atom_id": atom_id,
                            "text": text[:260],
                        })
                    if len(stat["contexts"]) < 80:
                        stat["contexts"].append(text[:260])

                if len(persons) >= 2:
                    rel_type = self._detect_relation_type(text)
                    for i in range(len(persons)):
                        for j in range(i + 1, len(persons)):
                            src, dst = sorted([persons[i], persons[j]])
                            key = (src, dst, rel_type)
                            rel = relation_stats.setdefault(key, {
                                "source": src,
                                "target": dst,
                                "relation_type": rel_type,
                                "count": 0,
                                "evidence": [],
                                "video_ids": set(),
                            })
                            rel["count"] += 1
                            rel["video_ids"].add(video_id)
                            if len(rel["evidence"]) < 5:
                                rel["evidence"].append({
                                    "video_id": video_id,
                                    "atom_id": atom_id,
                                    "text": text[:220],
                                })

                evt_type = self._detect_career_event_type(text)
                if evt_type:
                    time_expr = times[0] if times else self._extract_time_expr(text)
                    org = orgs[0] if orgs else self._extract_org_expr(text)
                    for person in persons:
                        person_events[person].append({
                            "event_type": evt_type,
                            "video_id": video_id,
                            "atom_id": atom_id,
                            "time_expr": time_expr,
                            "organization": org,
                            "text": text[:220],
                            "confidence": 0.72 if time_expr or org else 0.62,
                        })

        self.dynamic_lexicon = self._build_dynamic_lexicon(org_counter, concept_counter, event_counter)

        persons_out: List[Dict[str, Any]] = []
        for name, stat in person_stats.items():
            category, confidence = self._classify_person(stat["contexts"])
            framework_tags = self._framework_tags(stat["contexts"])
            persons_out.append({
                "person_id": self._person_id(name),
                "name": name,
                "category": category,
                "category_confidence": confidence,
                "mention_count": int(stat["mentions"]),
                "video_count": len(stat["video_ids"]),
                "framework_tags": framework_tags,
                "evidence": stat["evidence"],
            })
        persons_out.sort(key=lambda x: (x["video_count"], x["mention_count"]), reverse=True)

        relations_out = [{
            "source_person_id": self._person_id(rel["source"]),
            "source_name": rel["source"],
            "target_person_id": self._person_id(rel["target"]),
            "target_name": rel["target"],
            "relation_type": rel["relation_type"],
            "strength": int(rel["count"]),
            "video_count": len(rel["video_ids"]),
            "evidence": rel["evidence"],
        } for rel in relation_stats.values() if rel["count"] >= 1]
        relations_out.sort(key=lambda x: x["strength"], reverse=True)

        routes_out: List[Dict[str, Any]] = []
        for name, events in person_events.items():
            if not events:
                continue
            routes_out.append({
                "person_id": self._person_id(name),
                "name": name,
                "event_count": len(events),
                "events": events[:60],
            })
        routes_out.sort(key=lambda x: x["event_count"], reverse=True)

        if use_ai:
            self._ai_enrich(
                persons_out,
                relations_out,
                ai_model=ai_model,
                person_stats=person_stats,
                person_events=person_events,
                career_routes=routes_out,
            )

        payload = {
            "version": "v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "taxonomy": {
                "person_categories": list(self.dynamic_lexicon.keys()) + ["other"],
                "relation_types": list(self.taxonomy.get("relation_keywords", {}).keys()) + ["co_mentioned"],
                "career_event_types": list(self.taxonomy.get("career_event_keywords", {}).keys()),
                "framework_dimensions": list(self.taxonomy.get("framework_dimensions", {}).keys()),
                "dynamic_person_lexicon": self.dynamic_lexicon,
            },
            "stats": {
                "video_count": len(contributed_video_ids),
                "person_count": len(persons_out),
                "relation_count": len(relations_out),
                "career_event_count": sum(item["event_count"] for item in routes_out),
                "ai_enriched": bool(use_ai and CLAUDE_API_KEY),
                "alias_count": len(self.person_alias_map or {}),
                "videos": [
                    {"id": vid, "title": video_title_map.get(vid, vid)}
                    for vid in contributed_video_ids
                ],
            },
            "persons": persons_out,
            "relations": relations_out,
            "career_routes": routes_out,
        }

        self._save_cache(payload)
        return payload

    def _get_analyzed_atom_ids(self, person_id: str) -> set:
        try:
            row = self.supabase.table("person_profiles").select("source_atom_ids").eq("person_id", person_id).maybe_single().execute()
            if row.data and row.data.get("source_atom_ids"):
                return set(row.data["source_atom_ids"])
        except Exception:
            pass
        return set()

    def _save_person_profile(self, person_id: str, name: str, ai_result: dict, source_atom_ids: list, model_used: str) -> None:
        try:
            self.supabase.table("person_profiles").upsert({
                "person_id": person_id,
                "name": name,
                "ai_summary": ai_result.get("summary", ""),
                "ai_career_events": ai_result.get("career_events", []),
                "ai_relations": ai_result.get("relations", []),
                "source_atom_ids": source_atom_ids,
                "atom_count": len(source_atom_ids),
                "model_used": model_used,
                "last_analyzed_at": datetime.utcnow().isoformat(),
            }).execute()
        except Exception as e:
            logger.error(f"_save_person_profile failed for {name}: {e}")

    def _analyze_person_with_ai(
        self,
        person: Dict[str, Any],
        evidence: List[Dict[str, Any]],
        rule_career_events: List[Dict[str, Any]],
        person_relations: List[Dict[str, Any]],
        ai_model: str,
    ) -> Optional[Dict[str, Any]]:
        if not CLAUDE_API_KEY:
            return None
        try:
            client = ClaudeClient(CLAUDE_API_KEY)
        except Exception:
            return None

        name = person.get("name", "")
        category = person.get("category", "other")

        # All atoms, full text, no truncation
        atom_lines = []
        for ev in evidence:
            atom_id = ev.get("atom_id", "")
            text = (ev.get("text") or "").strip()
            if text:
                atom_lines.append(f"[{atom_id}] {text}")

        # All rule-based career events, full fields including position
        evt_lines = []
        for ev in rule_career_events:
            position = ev.get("position") or ev.get("event_type") or ""
            org = ev.get("organization") or "机构未知"
            t_start = ev.get("time_start") or ev.get("time_expr") or ""
            t_end = ev.get("time_end") or ""
            time_str = f"{t_start}~{t_end}".strip("~") or "时间未知"
            lvl = ev.get("power_level") or ""
            etype = ev.get("event_type") or ""
            conf = ev.get("confidence", 0)
            evt_lines.append(
                f"- 职位:{position} | 机构:{org} | 级别:{lvl} | 时间:{time_str} | 类型:{etype} | 置信度:{conf}"
            )

        # All co-occurrence relations, no limit
        rel_by_other: Dict[str, int] = {}
        for rel in person_relations:
            other = rel.get("target_name") if rel.get("source_name") == name else rel.get("source_name")
            if other:
                rel_by_other[other] = rel_by_other.get(other, 0) + rel.get("strength", 1)
        rel_lines = [f"- {other}：共现{cnt}次" for other, cnt in sorted(rel_by_other.items(), key=lambda x: -x[1])]

        prompt = (
            f"人物：{name}\n"
            f"分类（规则判断，仅参考）：{category}\n\n"
            "【原始原子证据（以此为准）】\n"
            + ("\n".join(atom_lines) if atom_lines else "（无）") + "\n\n"
            "【规则提取的职业事件候选（可能有错误，请逐条判断）】\n"
            + ("\n".join(evt_lines) if evt_lines else "- 无") + "\n\n"
            "【共现人物统计（全部）】\n"
            + ("\n".join(rel_lines) if rel_lines else "- 无") + "\n\n"
            "任务：\n"
            "1. 人物画像：基于原始原子写200-350字，客观可追溯\n"
            "2. 职业事件：对规则提取的候选逐条判断——\n"
            "   - 保留：原子证据支持的条目（可合并重复）\n"
            "   - 删除：明显不属于此人、或无任何原子支持的条目\n"
            "   - 不要凭空新增原子里没有的职位\n"
            "3. 关系：基于原子证据整理关键关系\n\n"
            "输出 JSON：\n"
            "{\n"
            '  "summary": "...",\n'
            '  "career_events": [\n'
            '    {"position": "职位名", "organization": "机构", "time_expr": "时间", "event_type": "appointment|transfer|promotion|investigation", "atom_id": "依据原子ID"}\n'
            "  ],\n"
            '  "relations": [\n'
            '    {"other_name": "...", "relation_type": "family|superior_subordinate|alliance|conflict|business_tie|mentor_successor|co_mentioned", "strength": 整数, "evidence_text": "..."}\n'
            "  ]\n"
            "}\n"
            "只返回JSON，无其他内容。"
        )

        try:
            result = client.call(prompt=prompt, model=ai_model, max_tokens=4000)
            parsed = self._extract_json_object(result)
            return parsed if isinstance(parsed, dict) else None
        except Exception as e:
            logger.error(f"_analyze_person_with_ai call failed for {name}: {e}")
            return None

    def _extract_json_object(self, text: str) -> Optional[Dict[str, Any]]:
        raw = text.strip()
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except Exception:
            pass
        m = re.search(r"(\{[\s\S]*\})", raw)
        if not m:
            return None
        try:
            data = json.loads(m.group(1))
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _ai_enrich(
        self,
        persons: List[Dict[str, Any]],
        relations: List[Dict[str, Any]],
        ai_model: str,
        person_stats: Dict[str, Any] = None,
        person_events: Dict[str, Any] = None,
        career_routes: List[Dict[str, Any]] = None,
    ) -> None:
        if not CLAUDE_API_KEY:
            return

        # Build relation lookup by person name (deduplicated by source+target pair)
        relations_by_person: Dict[str, List[Dict[str, Any]]] = {}
        seen_rel_pairs: set = set()
        deduped_relations: List[Dict[str, Any]] = []
        for rel in relations:
            src = rel.get("source_name", "")
            tgt = rel.get("target_name", "")
            pair = tuple(sorted([src, tgt]))
            rel_type = rel.get("relation_type", "co_mentioned")
            pair_key = (pair[0], pair[1], rel_type)
            if pair_key not in seen_rel_pairs:
                seen_rel_pairs.add(pair_key)
                deduped_relations.append(rel)
            for name in [src, tgt]:
                if name:
                    relations_by_person.setdefault(name, []).append(rel)

        # Replace original relations list in-place with deduped version
        relations[:] = deduped_relations

        career_routes = career_routes or []

        persons_to_process = []
        for person in persons:
            person_id = person["person_id"]
            current_atom_ids = [ev["atom_id"] for ev in person.get("evidence", []) if ev.get("atom_id")]
            try:
                analyzed_ids = self._get_analyzed_atom_ids(person_id)
            except Exception:
                analyzed_ids = set()
            new_atom_ids = set(current_atom_ids) - analyzed_ids
            if new_atom_ids or not analyzed_ids:
                persons_to_process.append((person, current_atom_ids))

        total = len(persons_to_process)
        if self._progress_callback:
            self._progress_callback({"status": "running", "total": total, "done": 0, "current": ""})

        for idx, (person, current_atom_ids) in enumerate(persons_to_process):
            person_id = person["person_id"]
            name = person["name"]

            if self._progress_callback:
                self._progress_callback({"status": "running", "total": total, "done": idx, "current": name})

            route = next((r for r in career_routes if r.get("person_id") == person_id), {"events": []})
            rule_career_events = route.get("events", [])
            person_rels = relations_by_person.get(name, [])

            try:
                ai_result = self._analyze_person_with_ai(
                    person, person.get("evidence", []), rule_career_events, person_rels, ai_model
                )
                if ai_result:
                    self._save_person_profile(person_id, name, ai_result, current_atom_ids, ai_model)
                    person["ai_summary"] = ai_result.get("summary", "")
            except Exception as e:
                logger.error(f"AI analysis failed for {name}: {e}")
                continue

        if self._progress_callback:
            self._progress_callback({"status": "done", "total": total, "done": total, "current": ""})

    def _extract_json_array(self, text: str) -> List[Any]:
        raw = text.strip()
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except Exception:
            pass
        m = re.search(r"(\[[\s\S]*\])", raw)
        if not m:
            return []
        try:
            data = json.loads(m.group(1))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def get_taxonomy(self) -> Dict[str, Any]:
        return self._load_taxonomy()

    def update_taxonomy(self, taxonomy: Dict[str, Any]) -> Dict[str, Any]:
        merged = self._merge_taxonomy(taxonomy or {})
        self.supabase.table("app_settings").upsert({
            "key": self.TAXONOMY_KEY,
            "value": json.dumps(merged, ensure_ascii=False),
        }).execute()
        return merged

    def get_aliases(self) -> Dict[str, str]:
        return self._load_aliases()

    def merge_persons(
        self,
        source_name: str,
        target_name: str,
        limit_videos: int = 300,
        use_ai: bool = True,
        ai_model: str = "claude-haiku-4-5",
        rebuild: bool = True,
    ) -> Dict[str, Any]:
        source = self._normalize_name(source_name)
        target = self._normalize_name(target_name)
        if not source or not target:
            raise ValueError("source_name and target_name are required")
        if source == target:
            raise ValueError("source_name and target_name cannot be the same")

        alias_map = self._load_aliases()
        # flatten both ends to current canonical names
        source = self._canonical_person_name(source, alias_map)
        target = self._canonical_person_name(target, alias_map)
        if source == target:
            return {
                "merged": False,
                "message": "already merged",
                "source_name": source_name,
                "target_name": target_name,
                "canonical_name": target,
                "alias_count": len(alias_map),
            }

        alias_map[source] = target
        # rewire existing aliases that pointed to source
        for k, v in list(alias_map.items()):
            if v == source:
                alias_map[k] = target
        # collapse chains
        for k in list(alias_map.keys()):
            alias_map[k] = self._canonical_person_name(alias_map[k], alias_map)
            if k == alias_map[k]:
                alias_map.pop(k, None)

        self._save_aliases(alias_map)
        # Sync person aliases to entity_aliases_v1 so analysis page also shows the mapping
        self._sync_to_entity_aliases(alias_map)
        if rebuild:
            payload = self.rebuild(
                limit_videos=max(1, min(int(limit_videos or 300), 1000)),
                use_ai=use_ai,
                ai_model=ai_model,
            )
            return {
                "merged": True,
                "source_name": source,
                "target_name": target,
                "alias_count": len(alias_map),
                "network_stats": payload.get("stats", {}),
            }
        # rebuild deferred to caller (async background job keeps /api/chat responsive)
        return {
            "merged": True,
            "source_name": source,
            "target_name": target,
            "alias_count": len(alias_map),
            "rebuild": "deferred",
        }

    def get_person_detail(self, person_id: str, use_ai: bool = True, ai_model: str = "claude-haiku-4-5") -> Dict[str, Any]:
        network = self.get_cached()
        if not network:
            network = self.rebuild(limit_videos=200, use_ai=False)

        persons = network.get("persons", [])
        person = next((p for p in persons if p.get("person_id") == person_id), None)
        if not person:
            raise ValueError(f"person not found: {person_id}")

        relations = [
            r for r in (network.get("relations") or [])
            if r.get("source_person_id") == person_id or r.get("target_person_id") == person_id
        ]
        relations.sort(key=lambda x: x.get("strength", 0), reverse=True)
        relations = relations[:50]

        route = next(
            (item for item in (network.get("career_routes") or []) if item.get("person_id") == person_id),
            {"events": []},
        )
        career_events = route.get("events", [])[:80]

        # Build set of name variants to validate atoms actually mention this person
        _alias_map = self._load_aliases()
        _person_name = person.get("name", "")
        _name_variants: set[str] = {_person_name}
        # add all aliases that point to this canonical name
        for variant, canonical in _alias_map.items():
            if canonical == _person_name:
                _name_variants.add(variant)

        def _atom_mentions_person(text: str) -> bool:
            if not text:
                return False
            return any(v in text for v in _name_variants)

        atoms: List[Dict[str, Any]] = []
        seen = set()
        for ev in person.get("evidence", [])[:60]:
            key = (ev.get("video_id"), ev.get("atom_id"))
            if key in seen:
                continue
            # Skip atoms where none of the person's name variants appear in text
            if not _atom_mentions_person(ev.get("text", "")):
                continue
            seen.add(key)
            atoms.append({
                "video_id": ev.get("video_id"),
                "atom_id": ev.get("atom_id"),
                "text": ev.get("text"),
                "source": "person_evidence",
                "analysis_url": f"/analysis?projectId={ev.get('video_id')}&atomId={ev.get('atom_id')}",
            })
        for rel in relations[:25]:
            for ev in rel.get("evidence", [])[:2]:
                key = (ev.get("video_id"), ev.get("atom_id"))
                if key in seen:
                    continue
                seen.add(key)
                atoms.append({
                    "video_id": ev.get("video_id"),
                    "atom_id": ev.get("atom_id"),
                    "text": ev.get("text"),
                    "source": "relation_evidence",
                    "analysis_url": f"/analysis?projectId={ev.get('video_id')}&atomId={ev.get('atom_id')}",
                })
        for ev in career_events[:25]:
            key = (ev.get("video_id"), ev.get("atom_id"))
            if key in seen:
                continue
            seen.add(key)
            atoms.append({
                "video_id": ev.get("video_id"),
                "atom_id": ev.get("atom_id"),
                "text": ev.get("text"),
                "source": "career_event",
                "analysis_url": f"/analysis?projectId={ev.get('video_id')}&atomId={ev.get('atom_id')}",
            })

        summary = None
        ai_career_events = None
        ai_relations_from_profile = None
        profile_row_exists = False  # track whether AI rebuild has ever run

        # Try reading from person_profiles table first
        try:
            profile = self.supabase.table("person_profiles").select("*").eq("person_id", person_id).maybe_single().execute()
            if profile.data:
                profile_row_exists = True
                summary = profile.data.get("ai_summary") or None
                ai_career_events_raw = profile.data.get("ai_career_events")
                if isinstance(ai_career_events_raw, list) and ai_career_events_raw:
                    ai_career_events = ai_career_events_raw
                ai_relations_raw = profile.data.get("ai_relations")
                if isinstance(ai_relations_raw, list) and ai_relations_raw:
                    ai_relations_from_profile = ai_relations_raw
        except Exception:
            pass

        # Fallback: generate summary on-the-fly only if AI rebuild has never run for this person
        # If person_profiles row exists (even with empty summary), do NOT fall back to old Haiku cache
        if not summary and not profile_row_exists and use_ai and CLAUDE_API_KEY:
            summary = self._get_or_generate_person_summary(person, relations, career_events, atoms, ai_model=ai_model)

        # Merge AI-corrected career events with rule-based ones
        if ai_career_events:
            career_events = ai_career_events

        # Merge AI relations from profile into network relations for richer output
        merged_ai_relations = []
        if ai_relations_from_profile:
            existing_others = {
                r.get("target_name") if r.get("source_person_id") == person_id else r.get("source_name")
                for r in relations
            }
            for ai_rel in ai_relations_from_profile:
                other = ai_rel.get("other_name", "")
                if other and other not in existing_others:
                    merged_ai_relations.append({
                        "source_person_id": person_id,
                        "source_name": person.get("name", ""),
                        "target_person_id": self._person_id(other),
                        "target_name": other,
                        "relation_type": ai_rel.get("relation_type", "co_mentioned"),
                        "strength": int(ai_rel.get("strength", 1)),
                        "evidence": [{"text": ai_rel.get("evidence_text", "")}] if ai_rel.get("evidence_text") else [],
                        "ai_inferred": True,
                    })

        return {
            "person": person,
            "summary": summary,
            "relations": relations + merged_ai_relations,
            "career_events": career_events,
            "related_atoms": atoms[:120],
        }

    def _save_cache(self, payload: Dict[str, Any]) -> None:
        self.supabase.table("app_settings").upsert({
            "key": self.CACHE_KEY,
            "value": json.dumps(payload, ensure_ascii=False),
        }).execute()

    def _get_or_generate_person_summary(
        self,
        person: Dict[str, Any],
        relations: List[Dict[str, Any]],
        career_events: List[Dict[str, Any]],
        atoms: List[Dict[str, Any]],
        ai_model: str,
    ) -> Optional[str]:
        cache_key = f"person_profile_v1_{person.get('person_id')}"
        try:
            cached = (
                self.supabase.table("app_settings")
                .select("value")
                .eq("key", cache_key)
                .limit(1)
                .execute()
            )
            if cached.data and cached.data[0].get("value"):
                value = cached.data[0]["value"]
                if isinstance(value, str) and value.strip():
                    return value
        except Exception:
            pass

        try:
            client = ClaudeClient(CLAUDE_API_KEY)
            rel_lines = []
            for rel in relations[:10]:
                other = rel.get("target_name") if rel.get("source_person_id") == person.get("person_id") else rel.get("source_name")
                rel_lines.append(f"- {other} ({rel.get('relation_type')}, 强度{rel.get('strength')})")
            evt_lines = [f"- {e.get('event_type')} | {e.get('time_expr') or '时间未知'} | {e.get('organization') or '机构未知'}" for e in career_events[:10]]
            atom_lines = [f"- {a.get('text','')[:120]}" for a in atoms[:10]]
            prompt = (
                "请基于以下证据，写一段中文人物画像（200~350字），要求客观、可追溯，不编造。\n"
                f"人物: {person.get('name')}\n"
                f"分类: {person.get('category')} (置信度 {person.get('category_confidence')})\n"
                "关系索引:\n" + ("\n".join(rel_lines) if rel_lines else "- 无") + "\n"
                "职业/事件路线:\n" + ("\n".join(evt_lines) if evt_lines else "- 无") + "\n"
                "原子证据摘录:\n" + ("\n".join(atom_lines) if atom_lines else "- 无")
            )
            text = client.call(prompt=prompt, model=ai_model, max_tokens=520).strip()
            if text:
                try:
                    self.supabase.table("app_settings").upsert({"key": cache_key, "value": text}).execute()
                except Exception:
                    pass
                return text
        except Exception:
            return None
        return None

    def _load_taxonomy(self) -> Dict[str, Any]:
        res = (
            self.supabase.table("app_settings")
            .select("value")
            .eq("key", self.TAXONOMY_KEY)
            .limit(1)
            .execute()
        )
        if not res.data:
            return self.DEFAULT_TAXONOMY
        raw = res.data[0].get("value")
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(data, dict):
                return self.DEFAULT_TAXONOMY
            return self._merge_taxonomy(data)
        except Exception:
            return self.DEFAULT_TAXONOMY

    def _load_aliases(self) -> Dict[str, str]:
        out: Dict[str, str] = {}

        # Load from both alias stores and merge; entity_aliases_v1 is the primary source
        keys_to_load = [self.ALIASES_KEY, "entity_aliases_v1"]
        rows = (
            self.supabase.table("app_settings")
            .select("key,value")
            .in_("key", keys_to_load)
            .execute()
        )
        for row in (rows.data or []):
            raw = row.get("value")
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(data, dict):
                    continue
                for k, v in data.items():
                    # entity_aliases_v1 stores {alias: {canonical, type}}, person_network_aliases_v1 stores {alias: canonical}
                    canonical = v.get("canonical") if isinstance(v, dict) else v
                    if not canonical:
                        continue
                    sk = self._normalize_name(k)
                    sv = self._normalize_name(canonical)
                    if sk and sv and sk != sv:
                        out[sk] = sv
            except Exception:
                continue
        return out

    def _save_aliases(self, aliases: Dict[str, str]) -> None:
        self.supabase.table("app_settings").upsert({
            "key": self.ALIASES_KEY,
            "value": json.dumps(aliases, ensure_ascii=False),
        }).execute()

    def _sync_to_entity_aliases(self, person_aliases: Dict[str, str]) -> None:
        """Sync person aliases into entity_aliases_v1 so analysis page shows the mapping."""
        try:
            row = self.supabase.table("app_settings").select("value").eq("key", "entity_aliases_v1").execute()
            existing: dict = {}
            if row.data:
                existing = json.loads(row.data[0]["value"]) if row.data[0].get("value") else {}
            for alias, canonical in person_aliases.items():
                if alias not in existing:
                    existing[alias] = {"canonical": canonical, "type": "person"}
                elif isinstance(existing[alias], dict):
                    existing[alias]["canonical"] = canonical
            self.supabase.table("app_settings").upsert({
                "key": "entity_aliases_v1",
                "value": json.dumps(existing, ensure_ascii=False),
            }).execute()
        except Exception:
            pass

    def _merge_taxonomy(self, incoming: Dict[str, Any]) -> Dict[str, Any]:
        merged: Dict[str, Any] = json.loads(json.dumps(self.DEFAULT_TAXONOMY, ensure_ascii=False))
        for section in ["person_categories", "relation_keywords", "career_event_keywords", "framework_dimensions"]:
            block = incoming.get(section)
            if not isinstance(block, dict):
                continue
            clean_block: Dict[str, List[str]] = {}
            for k, v in block.items():
                if not isinstance(k, str):
                    continue
                if isinstance(v, list):
                    clean_words = [str(w).strip() for w in v if str(w).strip()]
                    if clean_words:
                        clean_block[k] = clean_words
            if clean_block:
                merged[section] = clean_block
        return merged

    def _list_processed_videos(self, limit_videos: int) -> tuple:
        # Don't filter by status — library endpoint dynamically enriches status
        # based on real data (atoms/embeddings), so video_assets.status may still
        # be "transcribed" even when atoms and embeddings exist.
        # Instead, fetch all videos with a db_video_id and let _load_atoms filter.
        res = (
            self.supabase.table("video_assets")
            .select("db_video_id,status,title")
            .not_.is_("db_video_id", "null")
            .order("updated_at", desc=True)
            .limit(limit_videos)
            .execute()
        )
        ids = []
        title_map: Dict[str, str] = {}
        for row in (res.data or []):
            vid = row.get("db_video_id")
            if vid:
                ids.append(vid)
                if row.get("title"):
                    title_map[vid] = row["title"]
        # de-dup while preserving order
        return list(dict.fromkeys(ids)), title_map

    def _load_atoms(self, video_id: str) -> List[Dict[str, Any]]:
        res = (
            self.supabase.table("atoms")
            .select("id,video_id,segment_id,start_ms,end_ms,merged_text")
            .eq("video_id", video_id)
            .order("start_ms")
            .limit(3000)
            .execute()
        )
        return res.data or []

    def _load_atom_entities_map(self, atom_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        if not atom_ids:
            return out
        batch_size = 500
        for i in range(0, len(atom_ids), batch_size):
            batch = atom_ids[i:i + batch_size]
            res = (
                self.supabase.table("atom_entities")
                .select("atom_id,entity_name,entity_type,confidence")
                .in_("atom_id", batch)
                .execute()
            )
            for row in (res.data or []):
                out[str(row.get("atom_id"))].append(row)
        return out

    def _person_id(self, name: str) -> str:
        return "P_" + hashlib.md5(name.encode("utf-8")).hexdigest()[:12]

    def _normalize_name(self, name: Any) -> str:
        if name is None:
            return ""
        text = str(name).strip()
        return re.sub(r"\s+", "", text)

    def _canonical_person_name(self, name: str, alias_map: Optional[Dict[str, str]] = None) -> str:
        aliases = alias_map if alias_map is not None else getattr(self, "person_alias_map", {}) or {}
        current = self._normalize_name(name)
        seen: Set[str] = set()
        while current and current not in seen and aliases.get(current):
            seen.add(current)
            current = self._normalize_name(aliases.get(current))
        return current

    def _normalize_person_name(self, name: Any) -> str:
        normalized = self._normalize_name(name)
        if not normalized:
            return ""
        return self._canonical_person_name(normalized)

    def _is_valid_person_name(self, name: Any) -> bool:
        text = self._normalize_name(name)
        if not text:
            return False
        if len(text) < 2 or len(text) > 8:
            return False
        if text in self.PERSON_STOPWORDS:
            return False
        if any(text.endswith(s) for s in self.PERSON_BAD_SUFFIX):
            return False
        if re.search(r"(系统|关系|家族|组织|机构|公司|集团|政府|委员会|部|局|省|市|县)$", text):
            return False
        # mostly Chinese names; allow middle dot names
        if not re.fullmatch(r"[\u4e00-\u9fa5·]{2,8}", text):
            return False
        return True

    def _is_person(self, row: Dict[str, Any]) -> bool:
        t = str(row.get("entity_type") or "").lower()
        return t in {"person", "人物", "people"}

    def _is_org(self, row: Dict[str, Any]) -> bool:
        t = str(row.get("entity_type") or "").lower()
        return t in {"organization", "机构", "org"}

    def _is_time(self, row: Dict[str, Any]) -> bool:
        t = str(row.get("entity_type") or "").lower()
        return t in {"time_point", "time", "时间"}

    def _detect_relation_type(self, text: str) -> str:
        checks = self.taxonomy.get("relation_keywords", {})
        for rel, keywords in checks.items():
            if any(k in text for k in keywords):
                return rel
        return "co_mentioned"

    def _detect_career_event_type(self, text: str) -> Optional[str]:
        checks = self.taxonomy.get("career_event_keywords", {})
        for evt_type, words in checks.items():
            if any(k in text for k in words):
                return evt_type
        return None

    def _extract_time_expr(self, text: str) -> Optional[str]:
        m = re.search(r"((19|20)\d{2}年(\d{1,2}月)?)", text)
        if m:
            return m.group(1)
        m2 = re.search(r"(\d{1,2}月)", text)
        return m2.group(1) if m2 else None

    def _extract_org_expr(self, text: str) -> Optional[str]:
        m = re.search(r"([\u4e00-\u9fa5]{2,20}(部|厅|局|委|办|省|市|院|会|集团|公司))", text)
        return m.group(1) if m else None

    def _classify_person(self, contexts: List[str]) -> Tuple[str, float]:
        joined = "\n".join(contexts[:80])
        rules = self.dynamic_lexicon or {}
        scores = {k: sum(joined.count(w) for w in words) for k, words in rules.items()}
        if not scores:
            return "other", 0.4
        best = max(scores.items(), key=lambda x: x[1])
        total = sum(scores.values())
        if best[1] <= 0:
            return "other", 0.4
        confidence = round(min(0.95, 0.55 + (best[1] / max(1, total)) * 0.4), 2)
        return best[0], confidence

    def _build_dynamic_lexicon(
        self,
        org_counter: Counter,
        concept_counter: Counter,
        event_counter: Counter,
    ) -> Dict[str, List[str]]:
        def _top(counter: Counter, pattern: str, n: int = 40) -> List[str]:
            reg = re.compile(pattern)
            picked = [k for k, _ in counter.most_common(200) if reg.search(k)]
            return picked[:n]

        political = _top(org_counter, r"(部|委|厅|局|省|市|中央|党委|纪委|人大|政协|政府|外交|中联)")
        business = _top(org_counter, r"(公司|集团|资本|投资|银行|企业|控股|基金|保险|证券)")
        media = _top(org_counter, r"(媒体|电视|报|新闻|记者|频道|出版)")
        military = _top(org_counter, r"(军|武警|战区|部队|总参|总政)")

        family_terms = []
        family_pat = re.compile(r"(父|母|妻|夫|儿|女|家族|兄|姐|妹)")
        for src in (concept_counter, event_counter):
            for k, _ in src.most_common(200):
                if family_pat.search(k):
                    family_terms.append(k)
                if len(family_terms) >= 30:
                    break
            if len(family_terms) >= 30:
                break

        return {
            "political_actor": political,
            "business_actor": business,
            "family_member": family_terms,
            "media_actor": media,
            "military_actor": military,
        }

    def _framework_tags(self, contexts: List[str]) -> List[Dict[str, Any]]:
        text = "\n".join(contexts[:120])
        dims = self.taxonomy.get("framework_dimensions", {})
        scored = []
        for dim, keys in dims.items():
            c = sum(text.count(k) for k in keys)
            if c > 0:
                scored.append({"dimension": dim, "score": c})
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:4]

    # ──────────────────────────────────────────────────────────────────────────
    # Q&A and Web Enrichment
    # ──────────────────────────────────────────────────────────────────────────

    def ask_person(
        self,
        person_id: str,
        question: str,
        ai_model: str = "claude-haiku-4-5",
        include_web: bool = True,
    ) -> Dict[str, Any]:
        """Answer a natural-language question about a person using corpus evidence."""
        if not CLAUDE_API_KEY:
            raise RuntimeError("CLAUDE_API_KEY not set")

        # Load person detail (no AI summary needed)
        detail = self.get_person_detail(person_id=person_id, use_ai=False)
        person = detail.get("person") or {}
        name = str(person.get("name") or "")
        if not name:
            raise ValueError(f"person not found: {person_id}")

        relations = detail.get("relations") or []
        career_events = detail.get("career_events") or []

        # Load structured relation_events from app_settings (stored during pipeline)
        stored_relation_events = self._load_relation_events_for_person(name, person_id)

        # Fetch extended atom list directly from DB
        db_atoms = self._fetch_all_person_atoms(name)

        # Merge: db_atoms first (direct hits), then cached evidence
        seen_ids: Set[str] = set()
        all_atoms: List[Dict[str, Any]] = []
        for a in db_atoms + (detail.get("related_atoms") or []):
            aid = str(a.get("atom_id") or a.get("id") or "")
            if aid and aid in seen_ids:
                continue
            if aid:
                seen_ids.add(aid)
            all_atoms.append(a)
            if len(all_atoms) >= 60:
                break

        # Build context sections — use PersonRelationService for multi-hop graph
        parts: List[str] = []
        source_refs: List[Dict[str, Any]] = []

        # ── Graph context from knowledge-graph tables ──────────────────
        try:
            _prs = PersonRelationService(
                supabase_client=self.supabase,
                claude_api_key=CLAUDE_API_KEY,
                tavily_api_key=TAVILY_API_KEY,
            )
            web_profile = self._get_cached_web_profile(person_id) if include_web else None
            graph_context, source_refs = _prs.build_context_for_qa(
                person_id=person_id,
                person_name=name,
                atom_texts=all_atoms,
                web_profile=web_profile,
            )
            if graph_context:
                parts.append(graph_context)
        except Exception:
            # Fallback to legacy context building
            web_profile = self._get_cached_web_profile(person_id) if include_web else None

            # Structured relation events (from pipeline's enhanced extraction)
            if stored_relation_events:
                lines = []
                for ev in stored_relation_events[:20]:
                    actor = ev.get("actor") or ""
                    action = ev.get("action") or ""
                    target = ev.get("target") or ""
                    t = ev.get("time") or ""
                    ctx = (ev.get("context") or "")[:80]
                    lines.append(f"• {actor} {action} {target} {t}  {ctx}")
                parts.append("=== 人物关系事件（结构化）===\n" + "\n".join(lines))

            # Career events
            if career_events:
                lines = []
                for ev in career_events[:40]:
                    t = ev.get("time_expr") or "时间不明"
                    o = ev.get("organization") or ""
                    txt = (ev.get("text") or "")[:120]
                    lines.append(f"• [{ev.get('event_type')}] {t} {o}  {txt}")
                parts.append("=== 职业/事件路线 ===\n" + "\n".join(lines))

            # Relations
            if relations:
                lines = []
                for rel in relations[:25]:
                    if rel.get("source_person_id") == person_id:
                        other = rel.get("target_name", "")
                    else:
                        other = rel.get("source_name", "")
                    ev_txt = " | ".join(
                        (e.get("text") or "")[:80]
                        for e in (rel.get("evidence") or [])[:1]
                    )
                    lines.append(
                        f"• {other}（关系:{rel.get('relation_type')} 强度:{rel.get('strength')}）  {ev_txt}"
                    )
                parts.append("=== 人物关系 ===\n" + "\n".join(lines))

            if web_profile:
                parts.append("=== 公开资料 ===\n" + web_profile[:800])

            # Atom evidence
            atom_lines: List[str] = []
            for idx, a in enumerate(all_atoms[:50], start=1):
                txt = (a.get("text") or a.get("merged_text") or "")[:220]
                if not txt:
                    continue
                vid = a.get("video_id") or ""
                aid = a.get("atom_id") or a.get("id") or ""
                atom_lines.append(f"[{idx}] {txt}")
                source_refs.append({
                    "index": idx,
                    "video_id": vid,
                    "atom_id": aid,
                    "text": txt[:100],
                    "analysis_url": f"/analysis?projectId={vid}#atom-{aid}" if vid and aid else None,
                })
            if atom_lines:
                parts.append("=== 原文摘录 ===\n" + "\n".join(atom_lines))

        context = "\n\n".join(parts)

        prompt = (
            "你是一位专业的政治人物研究助手。我将提供来自视频字幕和公开资料的证据，请根据这些证据回答问题。\n\n"
            "严格要求：\n"
            "1. 只根据下方证据作答，不要凭空编造\n"
            "2. 在回答中用 [编号] 引用原文摘录（如 [1][3]），让读者可溯源\n"
            "3. 如果证据不足以回答，明确说明哪里不确定\n"
            "4. 回答用中文，300字以内，条理清晰\n\n"
            f"【人物】{name}  分类: {person.get('category','未知')}\n\n"
            f"{context}\n\n"
            f"【问题】{question}\n\n"
            "【回答】"
        )

        try:
            client = ClaudeClient(CLAUDE_API_KEY)
            answer = client.call(prompt=prompt, model=ai_model, max_tokens=700).strip()
        except Exception as e:
            answer = f"（生成回答失败：{e}）"

        return {
            "person_id": person_id,
            "person_name": name,
            "question": question,
            "answer": answer,
            "sources": source_refs[:20],
            "model": ai_model,
        }

    def _load_relation_events_for_person(self, person_name: str, person_id: str) -> List[Dict[str, Any]]:
        """Load structured relation_events that involve this person from app_settings."""
        # Find which videos this person appears in via the cached network
        network = self.get_cached()
        if not network:
            return []
        person = next((p for p in (network.get("persons") or []) if p.get("person_id") == person_id), None)
        if not person:
            return []
        video_ids: List[str] = []
        for ev in (person.get("evidence") or []):
            vid = ev.get("video_id")
            if vid and vid not in video_ids:
                video_ids.append(vid)

        out: List[Dict[str, Any]] = []
        for vid in video_ids[:5]:
            key = f"video_relation_events_v1_{vid}"
            try:
                res = (
                    self.supabase.table("app_settings")
                    .select("value")
                    .eq("key", key)
                    .limit(1)
                    .execute()
                )
                if not res.data or not res.data[0].get("value"):
                    continue
                events = json.loads(res.data[0]["value"])
                for ev in (events if isinstance(events, list) else []):
                    if not isinstance(ev, dict):
                        continue
                    actor = str(ev.get("actor") or "")
                    target = str(ev.get("target") or "")
                    if person_name in actor or person_name in target:
                        out.append(ev)
            except Exception:
                continue
        return out[:30]

    def _fetch_all_person_atoms(self, person_name: str, limit: int = 60) -> List[Dict[str, Any]]:
        """Query atom_entities to find all atoms mentioning this person, then fetch their text."""
        try:
            # Try both singular and plural entity_type values
            res = (
                self.supabase.table("atom_entities")
                .select("atom_id,video_id")
                .in_("entity_type", ["person", "persons", "people", "人物"])
                .eq("entity_name", person_name)
                .limit(limit)
                .execute()
            )
            rows = res.data or []
            if not rows:
                return []

            atom_ids = list({r.get("atom_id") for r in rows if r.get("atom_id")})
            vid_map = {r.get("atom_id"): r.get("video_id") for r in rows}

            # Batch fetch atom texts
            texts_res = (
                self.supabase.table("atoms")
                .select("id,merged_text,video_id")
                .in_("id", atom_ids[:limit])
                .execute()
            )
            out = []
            for row in (texts_res.data or []):
                aid = row.get("id")
                out.append({
                    "atom_id": aid,
                    "video_id": row.get("video_id") or vid_map.get(aid),
                    "text": row.get("merged_text") or "",
                    "source": "db_lookup",
                })
            return out
        except Exception:
            return []

    def enrich_person_from_web(self, person_id: str, person_name: str) -> Dict[str, Any]:
        """Fetch public profile for a person via Tavily and cache in app_settings."""
        if not TAVILY_API_KEY:
            raise RuntimeError("TAVILY_API_KEY not set")
        try:
            from tavily import TavilyClient
        except ImportError:
            raise RuntimeError("tavily-python not installed: pip install tavily-python")

        client = TavilyClient(api_key=TAVILY_API_KEY)
        query = f"{person_name} 中国官员 履历 关系"
        result = client.search(query=query, search_depth="advanced", max_results=5)
        snippets = []
        for item in (result.get("results") or []):
            content = item.get("content") or ""
            url = item.get("url") or ""
            if content:
                snippets.append(f"[{url}]\n{content[:400]}")

        profile_text = "\n\n".join(snippets[:5])
        cache_key = f"person_web_profile_v1_{person_id}"
        try:
            self.supabase.table("app_settings").upsert({
                "key": cache_key,
                "value": profile_text,
            }).execute()
        except Exception:
            pass
        return {"person_id": person_id, "person_name": person_name, "profile": profile_text, "cached": True}

    def _get_cached_web_profile(self, person_id: str) -> Optional[str]:
        cache_key = f"person_web_profile_v1_{person_id}"
        try:
            res = (
                self.supabase.table("app_settings")
                .select("value")
                .eq("key", cache_key)
                .limit(1)
                .execute()
            )
            if res.data and res.data[0].get("value"):
                return str(res.data[0]["value"])
        except Exception:
            pass
        return None
