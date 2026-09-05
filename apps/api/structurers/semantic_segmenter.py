"""
SemanticSegmenter - AI 语义分片器
两轮 Haiku 处理：1) 全局大纲切分  2) 每个片段独立精炼
"""

import json
import re
from typing import List, Dict, Any, Optional
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils import ClaudeClient, GrokClient, setup_logger
from utils.api_client import DeepSeekClient

logger = setup_logger(__name__)

# ── 第1轮 prompt：全局大纲切分 ──────────────────────────────────
SPLIT_PROMPT = """你是一个视频内容分析专家。以下是一个视频的全部原子摘要（按时间顺序），包含原子ID、正文摘要和核心实体。

这个视频共有 {total_atoms} 个原子，请把它们分成 **{min_segs}~{max_segs} 个大段落**。

每个段落应该是一个**完整的主题单元**，比如：
- 一个人物的完整故事线（从创业到成功到暴雷，不要拆开）
- 一个事件的完整始末
- 一段独立的评论或分析

切分原则：
- 宁粗勿碎：同一个人物的连续叙述不要拆开，即使有子话题切换
- 注意实体连续性：如果连续多个原子的核心人物相同（即使用"他/她/其"指代），它们属于同一段落
- 只在**大话题真正切换**时才切：换了一个完全不同的人物/事件/时间线
- 每个段落目标 {target_low}~{target_high} 个原子

原子列表：
{atoms_text}

请输出切分方案，严格JSON格式：
[
  {{"after_atom": "最后一个原子ID", "reason": "这里结束了XX话题，下面开始YY话题"}}
]

注意：
- after_atom 是该段落**最后一个原子**的ID，下一个原子开始新段落
- 总共输出 {min_segs}~{max_segs} 个切分点（最后一段不需要标记）
- 如果整个视频就是一个话题，输出空数组 []
- 只输出JSON数组，不要其他内容"""

# ── 第2轮 prompt：片段精炼 ──────────────────────────────────
REFINE_PROMPT = """以下是一个视频片段的原子内容和实体标注。请分析这个片段。

原子：
{atoms_with_entities}

请输出严格JSON格式：
{{
  "title": "简洁概括片段主题（15字以内）",
  "summary": "2-3句话概括内容",
  "core_persons": ["核心人物1", "核心人物2"],
  "pronoun_resolution": {{
    "原子ID": {{"他": "实际人名", "她": "实际人名"}}
  }},
  "keywords": ["关键词1", "关键词2", "关键词3"]
}}

要求：
- title：简洁概括，不超过15字
- summary：概括这段内容在讲什么
- core_persons：只列核心角色（主角），不是所有提及的人
- pronoun_resolution：标注原子中代词"他/她/其/这个人"实际指代的人物。只标注能明确判断的。如果某个原子没有代词则不用列出。
- keywords：3-6个关键词
- 只输出JSON，不要其他内容"""


class SemanticSegmenter:
    """两轮 AI 语义分片"""

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5", xai_api_key: str = "", deepseek_api_key: str = ""):
        if deepseek_api_key:
            self.client = DeepSeekClient(deepseek_api_key)
            self.model = "deepseek-v4-flash"
            logger.info("SemanticSegmenter: using DeepSeek v4-flash")
        elif xai_api_key:
            self.client = GrokClient(xai_api_key)
            self.model = "grok-4.3"
            logger.info("SemanticSegmenter: using Grok")
        else:
            self.client = ClaudeClient(api_key)
            self.model = model
            logger.info("SemanticSegmenter: using Claude Haiku")

    def segment(self, atoms: List[Dict], entities: List[Dict]) -> List[Dict]:
        """
        主入口：两轮处理

        Args:
            atoms: 按时间排序的原子列表，每个含 id, merged_text, start_ms, end_ms
            entities: atom_entities 列表，每个含 atom_id, entity_name, entity_type

        Returns:
            片段列表
        """
        if not atoms:
            return []

        # 构建实体索引
        entity_by_atom: Dict[str, List[Dict]] = {}
        for e in entities:
            entity_by_atom.setdefault(e["atom_id"], []).append(e)

        # 如果原子很少（<=8），不需要切分
        if len(atoms) <= 8:
            logger.info(f"原子数量 {len(atoms)} <= 8，整体作为一个片段")
            return [self._refine_segment(atoms, [e for elist in entity_by_atom.values() for e in elist], segment_num=1)]

        # 第1轮：全局大纲切分
        logger.info(f"第1轮：对 {len(atoms)} 个原子做全局大纲切分")
        split_points = self._find_split_points(atoms, entity_by_atom)
        logger.info(f"找到 {len(split_points)} 个切分点: {split_points}")

        # 按切分点把原子分组
        groups = self._split_atoms_by_points(atoms, split_points)
        logger.info(f"分成 {len(groups)} 个组，各含 {[len(g) for g in groups]} 个原子")

        # 第2轮：每个组独立精炼
        segments = []
        for i, group in enumerate(groups):
            logger.info(f"第2轮：精炼片段 {i+1}/{len(groups)}（{len(group)} 个原子）")
            group_entities = []
            for a in group:
                group_entities.extend(entity_by_atom.get(a["id"], []))
            seg = self._refine_segment(group, group_entities, segment_num=i + 1)
            segments.append(seg)

        return segments

    def _find_split_points(self, atoms: List[Dict], entity_by_atom: Dict[str, List[Dict]]) -> List[str]:
        """第1轮：全局大纲切分，一次性看全部原子摘要"""
        total = len(atoms)

        # 计算目标片段数
        target_segs = max(3, total // 20)  # 大约每20个原子一段
        min_segs = max(2, target_segs - 2)
        max_segs = target_segs + 3
        target_low = max(8, total // max_segs)
        target_high = min(40, total // min_segs)

        # 构建全局原子摘要（含实体）
        # 如果原子太多，分批处理
        if total <= 120:
            return self._find_splits_single_pass(atoms, entity_by_atom, total, min_segs, max_segs, target_low, target_high)
        else:
            return self._find_splits_multi_pass(atoms, entity_by_atom, total, min_segs, max_segs, target_low, target_high)

    def _find_splits_single_pass(self, atoms: List[Dict], entity_by_atom: Dict[str, List[Dict]],
                                  total: int, min_segs: int, max_segs: int,
                                  target_low: int, target_high: int) -> List[str]:
        """原子 <= 120 时，一次性全部给 AI 看"""
        lines = []
        for a in atoms:
            aid = self._short_id(a["id"])
            # 正文截取前100字（全局视角不需要全文，关键是看主题和人物）
            text = (a.get("merged_text") or "")[:100]
            # 加实体标注
            ents = entity_by_atom.get(a["id"], [])
            ent_str = self._format_entities_inline(ents)
            line = f"[{aid}] {text}"
            if ent_str:
                line += f"  [{ent_str}]"
            lines.append(line)

        atoms_text = "\n".join(lines)
        prompt = SPLIT_PROMPT.format(
            total_atoms=total,
            min_segs=min_segs,
            max_segs=max_segs,
            target_low=target_low,
            target_high=target_high,
            atoms_text=atoms_text,
        )

        try:
            response = self.client.call(prompt, model=self.model, max_tokens=2000)
            splits = self._parse_json_array(response)
            result = []
            for s in splits:
                after_atom = s.get("after_atom", "")
                full_id = self._resolve_atom_id(after_atom, atoms)
                if full_id:
                    result.append(full_id)
            return result
        except Exception as ex:
            logger.warning(f"全局切分失败: {ex}")
            return []

    def _find_splits_multi_pass(self, atoms: List[Dict], entity_by_atom: Dict[str, List[Dict]],
                                 total: int, min_segs: int, max_segs: int,
                                 target_low: int, target_high: int) -> List[str]:
        """原子 > 120 时，滑动窗口但带实体信息和更严格的粒度控制"""
        window_size = 60
        overlap = 10
        all_splits = []

        # 计算每个窗口期望的切分数
        splits_per_window = max(1, (window_size // 20))

        for start in range(0, len(atoms), window_size - overlap):
            window = atoms[start:start + window_size]
            if len(window) < 8:
                break

            lines = []
            for a in window:
                aid = self._short_id(a["id"])
                text = (a.get("merged_text") or "")[:100]
                ents = entity_by_atom.get(a["id"], [])
                ent_str = self._format_entities_inline(ents)
                line = f"[{aid}] {text}"
                if ent_str:
                    line += f"  [{ent_str}]"
                lines.append(line)

            atoms_text = "\n".join(lines)
            w_min = max(1, splits_per_window - 1)
            w_max = splits_per_window + 1
            w_target_low = max(8, len(window) // (w_max + 1))
            w_target_high = min(40, len(window) // max(1, w_min))

            prompt = SPLIT_PROMPT.format(
                total_atoms=len(window),
                min_segs=w_min,
                max_segs=w_max,
                target_low=w_target_low,
                target_high=w_target_high,
                atoms_text=atoms_text,
            )

            try:
                response = self.client.call(prompt, model=self.model, max_tokens=1500)
                splits = self._parse_json_array(response)
                for s in splits:
                    after_atom = s.get("after_atom", "")
                    full_id = self._resolve_atom_id(after_atom, window)
                    if full_id:
                        all_splits.append(full_id)
            except Exception as ex:
                logger.warning(f"窗口 {start} 处理失败: {ex}")

        # 去重
        seen = set()
        unique = []
        for s in all_splits:
            if s not in seen:
                seen.add(s)
                unique.append(s)

        # 如果切分点太多，做后处理合并
        if len(unique) > max_segs:
            unique = self._merge_close_splits(atoms, unique, min_segs, max_segs)

        return unique

    def _merge_close_splits(self, atoms: List[Dict], splits: List[str],
                             min_segs: int, max_segs: int) -> List[str]:
        """如果切分点太多，合并距离太近的切分点"""
        if not splits:
            return splits

        # 建立 atom_id → index 映射
        id_to_idx = {a["id"]: i for i, a in enumerate(atoms)}
        split_indices = [(s, id_to_idx.get(s, -1)) for s in splits]
        split_indices = [(s, idx) for s, idx in split_indices if idx >= 0]
        split_indices.sort(key=lambda x: x[1])

        target = (min_segs + max_segs) // 2
        min_gap = len(atoms) // (target + 1)

        # 贪心选取：保证相邻切分点之间至少 min_gap 个原子
        result = []
        last_idx = -min_gap
        for s, idx in split_indices:
            if idx - last_idx >= min_gap:
                result.append(s)
                last_idx = idx

        # 如果还是太多，均匀采样
        if len(result) > max_segs:
            step = len(result) / max_segs
            sampled = []
            for i in range(max_segs):
                sampled.append(result[int(i * step)])
            result = sampled

        return result

    def _format_entities_inline(self, ents: List[Dict]) -> str:
        """格式化实体为内联字符串"""
        if not ents:
            return ""
        parts = []
        for e in ents:
            etype = e.get("entity_type", "")
            name = e.get("entity_name", "")
            if not name or name in ("他", "她", "其", "这个人"):
                continue  # 跳过代词实体，避免干扰
            icon = {"person": "👤", "organization": "🏛", "location": "📍"}.get(etype, "")
            parts.append(f"{icon}{name}")
        return " ".join(parts)

    def _short_id(self, atom_id: str) -> str:
        """提取短ID"""
        return atom_id.split("_")[-1] if "_" in atom_id else atom_id

    def _resolve_atom_id(self, short_id: str, atoms: List[Dict]) -> Optional[str]:
        """将 AI 输出的短 ID（如 A029）还原为完整 ID"""
        short_id = short_id.strip()
        for a in atoms:
            if a["id"] == short_id:
                return a["id"]
            if a["id"].endswith(f"_{short_id}"):
                return a["id"]
            if a["id"].endswith(short_id):
                return a["id"]
        return None

    def _split_atoms_by_points(self, atoms: List[Dict], split_points: List[str]) -> List[List[Dict]]:
        """按切分点分组"""
        if not split_points:
            return [atoms]

        split_set = set(split_points)
        groups = []
        current_group = []

        for atom in atoms:
            current_group.append(atom)
            if atom["id"] in split_set:
                groups.append(current_group)
                current_group = []

        if current_group:
            if len(current_group) < 5 and groups:
                groups[-1].extend(current_group)
            else:
                groups.append(current_group)

        return groups if groups else [atoms]

    def _refine_segment(self, atoms: List[Dict], entities: List[Dict], segment_num: int) -> Dict:
        """第2轮：精炼单个片段"""
        entity_by_atom: Dict[str, List[Dict]] = {}
        for e in entities:
            entity_by_atom.setdefault(e["atom_id"], []).append(e)

        lines = []
        for a in atoms:
            aid = self._short_id(a["id"])
            text = (a.get("merged_text") or "")[:300]
            ents = entity_by_atom.get(a["id"], [])
            ent_str = ""
            if ents:
                ent_parts = []
                for e in ents:
                    etype = e.get("entity_type", "")
                    icon = {"person": "👤", "organization": "🏛", "location": "📍"}.get(etype, "🔖")
                    ent_parts.append(f"{icon}{e['entity_name']}")
                ent_str = "\n  实体：" + " ".join(ent_parts)
            lines.append(f"[{aid}] {text}{ent_str}")

        atoms_with_entities = "\n".join(lines)
        prompt = REFINE_PROMPT.format(atoms_with_entities=atoms_with_entities)

        # 从实体中提取兜底信息
        all_person_names = []
        for ents in entity_by_atom.values():
            for e in ents:
                name = e.get("entity_name", "")
                if e.get("entity_type") == "person" and name and name not in ("他", "她", "其", "这个人"):
                    if name not in all_person_names:
                        all_person_names.append(name)

        result = {
            "title": f"片段 {segment_num}",
            "summary": "",
            "core_persons": all_person_names[:3],  # 兜底：从实体中取前3个人物
            "pronoun_resolution": {},
            "keywords": [],
        }

        # 重试最多2次
        for attempt in range(2):
            try:
                response = self.client.call(prompt, model=self.model, max_tokens=1500)
                parsed = self._parse_json_object(response)
                if parsed:
                    result["title"] = parsed.get("title", result["title"])
                    result["summary"] = parsed.get("summary", "")
                    result["core_persons"] = parsed.get("core_persons", []) or all_person_names[:3]
                    result["keywords"] = parsed.get("keywords", [])
                    raw_pr = parsed.get("pronoun_resolution", {})
                    resolved_pr = {}
                    for atom_short_id, mappings in raw_pr.items():
                        full_id = self._resolve_atom_id(atom_short_id, atoms)
                        if full_id and isinstance(mappings, dict):
                            resolved_pr[full_id] = mappings
                    result["pronoun_resolution"] = resolved_pr
                    break  # 成功，退出重试
                else:
                    logger.warning(f"片段 {segment_num} 精炼返回空结果，重试 {attempt+1}/2")
            except Exception as ex:
                logger.warning(f"片段 {segment_num} 精炼失败 (attempt {attempt+1}): {ex}")
                if attempt == 0:
                    import time
                    time.sleep(1)  # 等1秒再重试

        atom_ids = [a["id"] for a in atoms]
        start_ms = min(a.get("start_ms", 0) for a in atoms)
        end_ms = max(a.get("end_ms", 0) for a in atoms)

        return {
            "segment_num": segment_num,
            "atom_ids": atom_ids,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "atom_count": len(atoms),
            **result,
        }

    def _parse_json_array(self, text: str) -> List[Dict]:
        """从 AI 响应中提取 JSON 数组"""
        text = text.strip()
        m = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', text, re.DOTALL)
        if m:
            text = m.group(1)
        else:
            m = re.search(r'\[.*\]', text, re.DOTALL)
            if m:
                text = m.group(0)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"JSON 数组解析失败: {text[:200]}")
            return []

    def _parse_json_object(self, text: str) -> Optional[Dict]:
        """从 AI 响应中提取 JSON 对象"""
        text = text.strip()
        m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if m:
            text = m.group(1)
        else:
            m = re.search(r'\{.*\}', text, re.DOTALL)
            if m:
                text = m.group(0)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"JSON 对象解析失败: {text[:200]}")
            return None
