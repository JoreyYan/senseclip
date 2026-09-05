"""
CreativeAngleAnalyzer - 创作角度分析器
基于内容分析生成短视频切片和内容复用建议
"""

import json
from typing import List, Dict, Any
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Atom, NarrativeSegment
from utils import setup_logger

logger = setup_logger(__name__)


class CreativeAngleAnalyzer:
    """创作角度分析器"""

    def analyze(
        self,
        atoms: List[Atom],
        segments: List[NarrativeSegment],
        entities: Dict[str, Any],
        topics: Dict[str, Any],
        graph: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        分析内容并生成创作建议

        Args:
            atoms: 原子列表
            segments: 叙事片段列表
            entities: 实体聚合数据
            topics: 主题网络数据
            graph: 知识图谱

        Returns:
            创作角度分析结果
        """
        logger.info("开始分析创作角度")

        result = {
            "video_metadata": self._extract_metadata(atoms, segments),
            "clip_recommendations": self._generate_clip_recommendations(segments, atoms),
            "content_angles": self._analyze_content_angles(segments, topics, entities),
            "title_suggestions": self._generate_title_suggestions(segments, topics),
            "target_audience": self._analyze_target_audience(segments, entities),
            "seo_keywords": self._extract_seo_keywords(topics, entities),
            "content_series": self._suggest_content_series(topics, entities),
            "engagement_points": self._identify_engagement_points(segments, atoms)
        }

        logger.info("创作角度分析完成")

        return result

    def _extract_metadata(
        self,
        atoms: List[Atom],
        segments: List[NarrativeSegment]
    ) -> Dict[str, Any]:
        """提取视频元数据"""
        total_duration_ms = atoms[-1].end_ms if atoms else 0

        return {
            "total_duration_ms": total_duration_ms,
            "total_duration_readable": self._format_duration(total_duration_ms),
            "atom_count": len(atoms),
            "segment_count": len(segments),
            "avg_segment_duration_ms": sum(s.duration_ms for s in segments) / len(segments) if segments else 0,
            "content_density": len(atoms) / (total_duration_ms / 60000) if total_duration_ms > 0 else 0  # atoms per minute
        }

    def _generate_clip_recommendations(
        self,
        segments: List[NarrativeSegment],
        atoms: List[Atom]
    ) -> List[Dict[str, Any]]:
        """生成短视频切片建议"""
        clips = []

        for seg in segments:
            # 评估片段是否适合做短视频
            suitability_score = self._calculate_clip_suitability(seg, atoms)

            if suitability_score >= 0.6:  # 适合度阈值
                clip = {
                    "segment_id": seg.segment_id,
                    "title": seg.title,
                    "start_ms": seg.start_ms,
                    "end_ms": seg.end_ms,
                    "duration_ms": seg.duration_ms,
                    "duration_readable": self._format_duration(seg.duration_ms),
                    "suitability_score": round(suitability_score, 2),
                    "reason": self._explain_clip_suitability(seg, suitability_score),
                    "suggested_platforms": self._suggest_platforms(seg.duration_ms),
                    "hook_points": self._identify_hook_points(seg, atoms),
                    "editing_suggestions": self._generate_editing_suggestions(seg),
                    "thumbnail_moments": self._suggest_thumbnail_moments(seg, atoms)
                }
                clips.append(clip)

        # 按适合度排序
        clips.sort(key=lambda x: x['suitability_score'], reverse=True)

        return clips

    def _calculate_clip_suitability(
        self,
        segment: NarrativeSegment,
        atoms: List[Atom]
    ) -> float:
        """计算片段的短视频适合度"""
        score = 0.0

        # 1. 时长适合度 (30%)
        duration_min = segment.duration_ms / 60000
        if 0.5 <= duration_min <= 3:
            score += 0.3
        elif 3 < duration_min <= 5:
            score += 0.2
        elif duration_min < 0.5:
            score += 0.1

        # 2. 内容完整性 (25%)
        score += segment.quality_score * 0.25

        # 3. 重要性 (20%)
        score += segment.importance_score * 0.2

        # 4. 是否适合复用 (15%)
        if segment.ai_analysis.suitable_for_reuse:
            score += 0.15

        # 5. 有明确观点或故事 (10%)
        if segment.ai_analysis.core_argument:
            score += 0.1

        return min(score, 1.0)

    def _explain_clip_suitability(self, segment: NarrativeSegment, score: float) -> str:
        """解释为什么适合做短视频"""
        reasons = []

        duration_min = segment.duration_ms / 60000

        if 0.5 <= duration_min <= 3:
            reasons.append("时长理想(0.5-3分钟)")
        elif 3 < duration_min <= 5:
            reasons.append("时长适中(3-5分钟)")

        if segment.quality_score >= 0.7:
            reasons.append("内容质量高")

        if segment.importance_score >= 0.7:
            reasons.append("信息价值大")

        if segment.ai_analysis.core_argument:
            reasons.append("有明确观点")

        if segment.narrative_structure.type in ["观点论述", "案例分析", "历史叙事"]:
            reasons.append(f"叙事类型适合({segment.narrative_structure.type})")

        return "、".join(reasons) if reasons else "综合评估适合"

    def _suggest_platforms(self, duration_ms: int) -> List[str]:
        """根据时长建议发布平台"""
        duration_sec = duration_ms / 1000
        platforms = []

        if duration_sec <= 60:
            platforms.extend(["抖音", "快手", "视频号", "Instagram Reels", "YouTube Shorts"])
        elif duration_sec <= 180:
            platforms.extend(["抖音", "快手", "B站", "视频号", "小红书"])
        elif duration_sec <= 600:
            platforms.extend(["B站", "YouTube", "西瓜视频"])
        else:
            platforms.extend(["B站", "YouTube", "爱奇艺"])

        return platforms

    def _identify_hook_points(
        self,
        segment: NarrativeSegment,
        atoms: List[Atom]
    ) -> List[Dict[str, Any]]:
        """识别开头吸引点"""
        hooks = []

        # 检查片段的前3个原子，寻找吸引点
        segment_atoms = [atom for atom in atoms if atom.atom_id in segment.atoms[:3]]

        for atom in segment_atoms:
            # 观点类、问题类原子适合做开头
            if atom.type in ["发表观点", "提出问题"]:
                hooks.append({
                    "atom_id": atom.atom_id,
                    "text": atom.merged_text[:100] + "...",
                    "type": "观点/问题引入"
                })

        # 如果有核心论点，也是好的开头
        if segment.ai_analysis.core_argument:
            hooks.append({
                "type": "核心论点",
                "text": segment.ai_analysis.core_argument[:100]
            })

        return hooks[:3]  # 最多返回3个

    def _generate_editing_suggestions(self, segment: NarrativeSegment) -> List[str]:
        """生成剪辑建议"""
        suggestions = []

        duration_min = segment.duration_ms / 60000

        if duration_min > 3:
            suggestions.append("建议加快语速或删减冗余部分，控制在3分钟内")

        if segment.narrative_structure.type == "历史叙事":
            suggestions.append("可添加历史图片或视频素材增强代入感")

        if segment.entities.persons:
            suggestions.append(f"可添加人物照片：{', '.join(segment.entities.persons[:3])}")

        if segment.topics.free_tags:
            suggestions.append(f"可配合关键词字幕：{', '.join(segment.topics.free_tags[:5])}")

        suggestions.append("开头3秒内点明核心观点")
        suggestions.append("结尾可添加引导关注/点赞的提示")

        return suggestions

    def _suggest_thumbnail_moments(
        self,
        segment: NarrativeSegment,
        atoms: List[Atom]
    ) -> List[Dict[str, Any]]:
        """建议封面截图时刻"""
        moments = []

        # 找高价值原子作为封面候选
        segment_atoms = [atom for atom in atoms if atom.atom_id in segment.atoms]
        high_value_atoms = [
            atom for atom in segment_atoms
            if atom.type in ["发表观点", "叙述历史", "讲述故事"]
        ]

        for atom in high_value_atoms[:3]:
            moments.append({
                "atom_id": atom.atom_id,
                "timestamp_ms": atom.start_ms,
                "timestamp_readable": self._ms_to_time(atom.start_ms),
                "text_overlay_suggestion": atom.merged_text[:30] + "..."
            })

        return moments

    def _analyze_content_angles(
        self,
        segments: List[NarrativeSegment],
        topics: Dict[str, Any],
        entities: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """分析内容切入角度"""
        angles = []

        # 基于主题的角度
        primary_topics = topics.get('primary_topics', [])
        for topic_data in primary_topics[:3]:
            topic = topic_data.get('topic', '')
            angles.append({
                "angle_type": "主题切入",
                "title": topic,
                "description": f"从'{topic}'角度展开，适合关注该领域的观众",
                "target_audience": self._infer_audience_from_topic(topic),
                "content_focus": "深度分析"
            })

        # 基于人物的角度
        persons = entities.get('persons', [])
        if persons:
            for person in persons[:2]:
                name = person.get('name', '')
                angles.append({
                    "angle_type": "人物视角",
                    "title": f"{name}的故事",
                    "description": f"以{name}为主线，讲述相关事件",
                    "target_audience": ["历史爱好者", "人物传记粉丝"],
                    "content_focus": "人物经历"
                })

        # 基于事件的角度
        events = entities.get('events', [])
        if events:
            for event in events[:2]:
                name = event.get('name', '')
                angles.append({
                    "angle_type": "事件分析",
                    "title": f"深度解析：{name}",
                    "description": f"解析{name}的来龙去脉和影响",
                    "target_audience": ["历史爱好者", "时事关注者"],
                    "content_focus": "事件分析"
                })

        # 基于观点的角度
        for seg in segments:
            if seg.ai_analysis.core_argument:
                angles.append({
                    "angle_type": "观点论述",
                    "title": seg.title,
                    "description": seg.ai_analysis.core_argument,
                    "target_audience": ["思考型观众", "观点寻找者"],
                    "content_focus": "观点提炼"
                })

        return angles[:8]  # 返回最多8个角度

    def _generate_title_suggestions(
        self,
        segments: List[NarrativeSegment],
        topics: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """生成标题建议"""
        titles = []

        for seg in segments:
            # 类型1: 直接用片段标题
            titles.append({
                "title": seg.title,
                "type": "原始标题",
                "hook_level": "中",
                "seo_friendly": True
            })

            # 类型2: 疑问式标题
            if seg.ai_analysis.core_argument:
                question_title = self._create_question_title(seg)
                if question_title:
                    titles.append({
                        "title": question_title,
                        "type": "疑问式",
                        "hook_level": "高",
                        "seo_friendly": True
                    })

            # 类型3: 数字式标题
            if seg.ai_analysis.key_insights:
                insight_count = len(seg.ai_analysis.key_insights)
                titles.append({
                    "title": f"{insight_count}个关于{seg.title}的重要认知",
                    "type": "数字式",
                    "hook_level": "高",
                    "seo_friendly": True
                })

            # 类型4: 冲突式标题
            if "分析" in seg.narrative_structure.type or "论述" in seg.narrative_structure.type:
                titles.append({
                    "title": f"关于{seg.title}，你可能不知道的真相",
                    "type": "冲突式",
                    "hook_level": "高",
                    "seo_friendly": False
                })

        return titles[:10]

    def _create_question_title(self, segment: NarrativeSegment) -> str:
        """创建疑问式标题"""
        # 简单的规则：如果标题不是疑问句，尝试转换
        if "什么" in segment.title or "如何" in segment.title or "为什么" in segment.title:
            return None

        # 尝试转换为疑问
        if "历史" in segment.title:
            return f"你了解{segment.title.replace('历史', '')}的真实历史吗？"
        elif segment.topics.primary_topic:
            return f"关于{segment.topics.primary_topic}，你想知道什么？"

        return None

    def _analyze_target_audience(
        self,
        segments: List[NarrativeSegment],
        entities: Dict[str, Any]
    ) -> Dict[str, Any]:
        """分析目标受众"""
        audience_tags = set()
        content_categories = set()

        for seg in segments:
            # 根据叙事类型推断受众
            if seg.narrative_structure.type == "历史叙事":
                audience_tags.update(["历史爱好者", "文化学习者"])
                content_categories.add("历史教育")

            elif seg.narrative_structure.type == "观点论述":
                audience_tags.update(["思考型观众", "意见领袖关注者"])
                content_categories.add("观点评论")

            elif seg.narrative_structure.type == "案例分析":
                audience_tags.update(["专业人士", "行业研究者"])
                content_categories.add("案例研究")

            # 根据实体推断受众
            if seg.entities.persons:
                audience_tags.add("人物传记粉丝")

        # 年龄推断
        age_group = "25-45岁"  # 默认值
        education_level = "本科及以上"  # 默认值

        return {
            "primary_audience": list(audience_tags)[:5],
            "content_categories": list(content_categories),
            "estimated_age_group": age_group,
            "estimated_education": education_level,
            "interest_keywords": self._extract_interest_keywords(entities)
        }

    def _extract_interest_keywords(self, entities: Dict[str, Any]) -> List[str]:
        """提取兴趣关键词"""
        keywords = []

        # 从概念中提取
        concepts = entities.get('concepts', [])
        for concept in concepts[:10]:
            keywords.append(concept.get('name', ''))

        return keywords

    def _infer_audience_from_topic(self, topic: str) -> List[str]:
        """从主题推断受众"""
        audiences = []

        if "历史" in topic or "战争" in topic or "政治" in topic:
            audiences.extend(["历史爱好者", "政治观察者"])
        if "经济" in topic or "金融" in topic or "市场" in topic:
            audiences.extend(["经济学爱好者", "投资者"])
        if "文化" in topic or "艺术" in topic:
            audiences.extend(["文化学习者", "艺术爱好者"])

        return audiences if audiences else ["泛知识受众"]

    def _extract_seo_keywords(
        self,
        topics: Dict[str, Any],
        entities: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """提取SEO关键词"""
        keywords = []

        # 从主题提取
        primary_topics = topics.get('primary_topics', [])
        for topic_data in primary_topics:
            topic = topic_data.get('topic', '')
            keywords.append({
                "keyword": topic,
                "type": "主题",
                "search_volume": "高",
                "competition": "中"
            })

        # 从标签提取
        tags = topics.get('tags', {})
        if isinstance(tags, list):
            # 列表格式
            for tag_data in tags[:10]:
                tag = tag_data.get('tag', '')
                keywords.append({
                    "keyword": tag,
                    "type": "标签",
                    "search_volume": "中",
                    "competition": "低"
                })
        else:
            # 字典格式（兼容旧版）
            for tag, data in list(tags.items())[:10]:
                keywords.append({
                    "keyword": tag,
                    "type": "标签",
                    "search_volume": "中",
                    "competition": "低"
                })

        # 从实体提取
        persons = entities.get('persons', [])
        for person in persons[:5]:
            name = person.get('name', '')
            keywords.append({
                "keyword": name,
                "type": "人物",
                "search_volume": "中",
                "competition": "中"
            })

        return keywords[:20]

    def _suggest_content_series(
        self,
        topics: Dict[str, Any],
        entities: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """建议系列内容"""
        series = []

        # 基于主题的系列
        primary_topics = topics.get('primary_topics', [])
        for topic_data in primary_topics:
            topic = topic_data.get('topic', '')
            series.append({
                "series_name": f"{topic}系列",
                "description": f"围绕{topic}展开的系列内容",
                "estimated_episodes": 5,
                "content_angle": "深度挖掘",
                "update_frequency": "每周1-2期"
            })

        # 基于人物的系列
        persons = entities.get('persons', [])
        if len(persons) >= 3:
            series.append({
                "series_name": "历史人物志",
                "description": f"讲述{', '.join([p.get('name', '') for p in persons[:3]])}等人物的故事",
                "estimated_episodes": len(persons),
                "content_angle": "人物传记",
                "update_frequency": "每周1期"
            })

        return series[:5]

    def _identify_engagement_points(
        self,
        segments: List[NarrativeSegment],
        atoms: List[Atom]
    ) -> List[Dict[str, Any]]:
        """识别互动点"""
        points = []

        for seg in segments:
            # 在高价值片段后添加互动引导
            if seg.importance_score >= 0.7:
                points.append({
                    "timestamp_ms": seg.end_ms,
                    "timestamp_readable": self._ms_to_time(seg.end_ms),
                    "engagement_type": "提问引导",
                    "suggestion": f"针对'{seg.title}'，可以问观众：你怎么看？",
                    "expected_action": "评论互动"
                })

            # 在有争议观点处添加投票
            if seg.ai_analysis.core_argument and "分析" in seg.content_facet.type:
                points.append({
                    "timestamp_ms": seg.start_ms + seg.duration_ms // 2,
                    "timestamp_readable": self._ms_to_time(seg.start_ms + seg.duration_ms // 2),
                    "engagement_type": "观点投票",
                    "suggestion": f"你认为'{seg.ai_analysis.core_argument[:50]}'对吗？",
                    "expected_action": "点赞/投票"
                })

        return points[:5]

    # ========== 辅助方法 ==========

    def _format_duration(self, ms: int) -> str:
        """格式化时长"""
        seconds = ms / 1000
        if seconds < 60:
            return f"{seconds:.0f}秒"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f}分钟"
        else:
            hours = seconds / 3600
            return f"{hours:.1f}小时"

    def _ms_to_time(self, ms: int) -> str:
        """毫秒转时间字符串"""
        from datetime import timedelta
        td = timedelta(milliseconds=ms)
        hours, remainder = divmod(td.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def save(self, analysis: Dict[str, Any], output_path: Path):
        """保存分析结果到文件"""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(analysis, f, ensure_ascii=False, indent=2)
        logger.info(f"创作角度分析已保存到: {output_path}")
