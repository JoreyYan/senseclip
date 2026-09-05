"""
StructureReportGenerator - 视频结构报告生成器
生成人类可读的 Markdown 格式视频分析报告
"""

import json
from typing import List, Dict, Any
from pathlib import Path
from datetime import timedelta
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Atom, NarrativeSegment
from utils import setup_logger

logger = setup_logger(__name__)


class StructureReportGenerator:
    """视频结构报告生成器"""

    def generate(
        self,
        atoms: List[Atom],
        segments: List[NarrativeSegment],
        entities: Dict[str, Any],
        topics: Dict[str, Any],
        validation: Dict[str, Any]
    ) -> str:
        """
        生成完整的视频结构报告

        Args:
            atoms: 原子列表
            segments: 叙事片段列表
            entities: 实体聚合数据
            topics: 主题网络数据
            validation: 验证报告

        Returns:
            Markdown 格式的报告文本
        """
        logger.info("开始生成视频结构报告")

        sections = []

        # 1. 标题和概览
        sections.append(self._generate_header(atoms, segments))

        # 2. 执行摘要
        sections.append(self._generate_executive_summary(segments, validation))

        # 3. 内容结构时间轴
        sections.append(self._generate_timeline(segments, atoms))

        # 4. 叙事片段详情
        sections.append(self._generate_segments_detail(segments))

        # 5. 主题分析
        sections.append(self._generate_topics_analysis(topics))

        # 6. 实体网络
        sections.append(self._generate_entities_network(entities))

        # 7. 内容统计
        sections.append(self._generate_statistics(atoms, segments, entities))

        # 8. AI 洞察汇总
        sections.append(self._generate_insights_summary(segments))

        # 合并所有部分
        report = "\n\n".join(sections)

        logger.info("视频结构报告生成完成")

        return report

    def _generate_header(self, atoms: List[Atom], segments: List[NarrativeSegment]) -> str:
        """生成报告头部"""
        total_duration = atoms[-1].end_ms if atoms else 0
        duration_str = self._format_duration(total_duration)

        return f"""# 📹 视频内容结构分析报告

---

**视频时长**: {duration_str}
**分析日期**: {self._get_current_date()}
**原子单元数**: {len(atoms)}
**叙事片段数**: {len(segments)}

---
"""

    def _generate_executive_summary(
        self,
        segments: List[NarrativeSegment],
        validation: Dict[str, Any]
    ) -> str:
        """生成执行摘要"""
        if not segments:
            return "## 📊 执行摘要\n\n暂无内容分析。"

        # 计算平均质量和重要性
        avg_importance = sum(s.importance_score for s in segments) / len(segments)
        avg_quality = sum(s.quality_score for s in segments) / len(segments)

        quality_emoji = "🟢" if avg_quality >= 0.7 else "🟡" if avg_quality >= 0.5 else "🔴"
        importance_emoji = "⭐" * int(avg_importance * 5)

        coverage = validation.get('coverage_rate', 0) * 100

        return f"""## 📊 执行摘要

### 内容质量评估

- **整体质量**: {quality_emoji} {avg_quality:.2f}/1.0
- **内容重要性**: {importance_emoji} {avg_importance:.2f}/1.0
- **时间覆盖率**: {coverage:.1f}%

### 核心主题

{self._format_segment_topics(segments)}

### 关键发现

{self._format_key_findings(segments)}
"""

    def _format_segment_topics(self, segments: List[NarrativeSegment]) -> str:
        """格式化片段主题"""
        topics = []
        for seg in segments:
            if seg.topics.primary_topic:
                topics.append(f"- **{seg.title}**: {seg.topics.primary_topic}")

        return "\n".join(topics) if topics else "- 暂无明确主题"

    def _format_key_findings(self, segments: List[NarrativeSegment]) -> str:
        """格式化关键发现"""
        findings = []
        for seg in segments:
            if seg.ai_analysis.key_insights:
                for insight in seg.ai_analysis.key_insights[:2]:  # 每个片段取前2条
                    findings.append(f"- {insight}")

        return "\n".join(findings[:5]) if findings else "- 暂无关键洞察"

    def _generate_timeline(
        self,
        segments: List[NarrativeSegment],
        atoms: List[Atom]
    ) -> str:
        """生成内容时间轴"""
        lines = ["## ⏱️ 内容时间轴", ""]

        if not segments:
            return "\n".join(lines + ["暂无片段数据"])

        for seg in segments:
            start_time = self._ms_to_time(seg.start_ms)
            end_time = self._ms_to_time(seg.end_ms)
            duration_min = seg.duration_ms / 60000

            # 重要性指示器
            importance_bar = "█" * int(seg.importance_score * 10)

            lines.append(f"### {start_time} - {end_time} ({duration_min:.1f}分钟)")
            lines.append(f"**{seg.title}**")
            lines.append(f"")
            lines.append(f"- **重要性**: {importance_bar} {seg.importance_score:.2f}")
            lines.append(f"- **质量**: {'⭐' * int(seg.quality_score * 5)} {seg.quality_score:.2f}")
            lines.append(f"- **类型**: {seg.narrative_structure.type}")

            if seg.topics.primary_topic:
                lines.append(f"- **主题**: {seg.topics.primary_topic}")

            lines.append(f"- **摘要**: {seg.summary[:150]}...")
            lines.append("")

        return "\n".join(lines)

    def _generate_segments_detail(self, segments: List[NarrativeSegment]) -> str:
        """生成片段详细分析"""
        lines = ["## 📝 叙事片段详细分析", ""]

        if not segments:
            return "\n".join(lines + ["暂无片段数据"])

        for i, seg in enumerate(segments, 1):
            lines.append(f"### {i}. {seg.title}")
            lines.append("")
            lines.append(f"**时间**: {self._ms_to_time(seg.start_ms)} - {self._ms_to_time(seg.end_ms)}")
            lines.append(f"**原子数**: {len(seg.atoms)}个")
            lines.append("")

            # 叙事结构
            lines.append("#### 🎬 叙事结构")
            lines.append(f"- **类型**: {seg.narrative_structure.type}")
            lines.append(f"- **结构**: {seg.narrative_structure.structure}")

            if seg.narrative_structure.acts:
                lines.append("- **幕次**:")
                for act in seg.narrative_structure.acts:
                    lines.append(f"  - **{act.get('role', '未知')}**: {act.get('description', '')}")
            lines.append("")

            # 内容摘要
            lines.append("#### 📄 内容摘要")
            lines.append(seg.summary)
            lines.append("")

            # 核心论点
            if seg.ai_analysis.core_argument:
                lines.append("#### 💡 核心论点")
                lines.append(f"> {seg.ai_analysis.core_argument}")
                lines.append("")

            # 关键洞察
            if seg.ai_analysis.key_insights:
                lines.append("#### 🔍 关键洞察")
                for insight in seg.ai_analysis.key_insights:
                    lines.append(f"- {insight}")
                lines.append("")

            # 实体提及
            if any([seg.entities.persons, seg.entities.events, seg.entities.concepts]):
                lines.append("#### 🏷️ 提及的实体")
                if seg.entities.persons:
                    lines.append(f"- **人物**: {', '.join(seg.entities.persons)}")
                if seg.entities.events:
                    lines.append(f"- **事件**: {', '.join(seg.entities.events)}")
                if seg.entities.concepts:
                    lines.append(f"- **概念**: {', '.join(seg.entities.concepts)}")
                lines.append("")

            # 二创建议
            if seg.ai_analysis.reuse_suggestions:
                lines.append("#### ✂️ 内容复用建议")
                for suggestion in seg.ai_analysis.reuse_suggestions:
                    lines.append(f"- {suggestion}")
                lines.append("")

            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def _generate_topics_analysis(self, topics: Dict[str, Any]) -> str:
        """生成主题分析"""
        lines = ["## 🎯 主题分析", ""]

        primary_topics = topics.get('primary_topics', [])
        secondary_topics = topics.get('secondary_topics', {})
        tags = topics.get('tags', {})

        if not primary_topics:
            return "\n".join(lines + ["暂无主题数据"])

        # 主要主题
        lines.append("### 核心主题")
        lines.append("")
        for topic in primary_topics:
            weight = topic.get('weight', 0)
            topic_name = topic.get('topic', '未知')
            segments = topic.get('segments', [])

            lines.append(f"#### {topic_name}")
            lines.append(f"- **权重**: {'█' * int(weight * 10)} {weight:.2f}")
            lines.append(f"- **出现片段**: {len(segments)}个")

            subtopics = topic.get('subtopics', [])
            if subtopics:
                lines.append(f"- **关联次要主题**: {', '.join(list(subtopics)[:5])}")
            lines.append("")

        # 次要主题
        if secondary_topics:
            lines.append("### 次要主题")
            lines.append("")
            # secondary_topics 是列表
            if isinstance(secondary_topics, list):
                for topic_data in secondary_topics[:10]:
                    topic_name = topic_data.get('topic', '未知')
                    weight = topic_data.get('weight', 0)
                    lines.append(f"- **{topic_name}** (权重: {weight:.2f})")
            else:
                # 如果是字典（兼容旧格式）
                for topic_name, data in list(secondary_topics.items())[:10]:
                    count = data.get('count', 0)
                    lines.append(f"- **{topic_name}** (出现{count}次)")
            lines.append("")

        # 标签云
        if tags:
            lines.append("### 📌 内容标签")
            lines.append("")
            # tags 可能是列表或字典
            if isinstance(tags, list):
                # 列表格式：[{tag: '', count: n}, ...]
                sorted_tags = sorted(tags, key=lambda x: x.get('count', 0), reverse=True)
                tag_list = [f"`{item.get('tag', '')}`" for item in sorted_tags[:20]]
            else:
                # 字典格式（兼容旧格式）
                sorted_tags = sorted(tags.items(), key=lambda x: x[1].get('count', 0), reverse=True)
                tag_list = [f"`{tag}`" for tag, _ in sorted_tags[:20]]
            lines.append(" ".join(tag_list))
            lines.append("")

        return "\n".join(lines)

    def _generate_entities_network(self, entities: Dict[str, Any]) -> str:
        """生成实体网络"""
        lines = ["## 👥 实体关系网络", ""]

        stats = entities.get('statistics', {})
        if stats.get('total_entities', 0) == 0:
            return "\n".join(lines + ["暂无实体数据"])

        # 统计概览
        lines.append("### 实体统计")
        lines.append("")
        by_type = stats.get('by_type', {})
        for entity_type, count in by_type.items():
            emoji = self._get_entity_emoji(entity_type)
            lines.append(f"- {emoji} **{entity_type}**: {count}个")
        lines.append("")

        # 重要人物
        persons = entities.get('persons', [])
        if persons:
            lines.append("### 🧑 重要人物")
            lines.append("")
            for person in persons[:10]:
                name = person.get('name', '未知')
                mentions = person.get('mentions', 0)
                atoms = person.get('atoms', [])
                context = person.get('context', [])

                lines.append(f"#### {name}")
                lines.append(f"- **提及次数**: {mentions}")
                lines.append(f"- **出现原子**: {len(atoms)}个 ({', '.join(atoms[:3])}...)")
                if context:
                    lines.append(f"- **上下文**: {', '.join(context[:2])}")
                lines.append("")

        # 关键事件
        events = entities.get('events', [])
        if events:
            lines.append("### 📅 关键事件")
            lines.append("")
            for event in events[:10]:
                name = event.get('name', '未知')
                mentions = event.get('mentions', 0)
                context = event.get('context', [])

                lines.append(f"- **{name}** (提及{mentions}次)")
                if context:
                    lines.append(f"  - 相关: {', '.join(context[:2])}")
            lines.append("")

        # 核心概念
        concepts = entities.get('concepts', [])
        if concepts:
            lines.append("### 💭 核心概念")
            lines.append("")
            for concept in concepts[:10]:
                name = concept.get('name', '未知')
                mentions = concept.get('mentions', 0)
                atoms = concept.get('atoms', [])

                lines.append(f"- **{name}** (出现在{len(atoms)}个原子中)")
            lines.append("")

        return "\n".join(lines)

    def _generate_statistics(
        self,
        atoms: List[Atom],
        segments: List[NarrativeSegment],
        entities: Dict[str, Any]
    ) -> str:
        """生成内容统计"""
        lines = ["## 📈 内容统计", ""]

        # 原子类型分布
        atom_types = {}
        for atom in atoms:
            atom_types[atom.type] = atom_types.get(atom.type, 0) + 1

        lines.append("### 原子类型分布")
        lines.append("")
        for atom_type, count in sorted(atom_types.items(), key=lambda x: x[1], reverse=True):
            percentage = count / len(atoms) * 100
            bar = "█" * int(percentage / 5)
            lines.append(f"- **{atom_type}**: {count}个 ({percentage:.1f}%) {bar}")
        lines.append("")

        # 片段质量分布
        if segments:
            lines.append("### 片段质量分布")
            lines.append("")

            quality_bins = {"优秀(≥0.8)": 0, "良好(0.6-0.8)": 0, "中等(<0.6)": 0}
            for seg in segments:
                if seg.quality_score >= 0.8:
                    quality_bins["优秀(≥0.8)"] += 1
                elif seg.quality_score >= 0.6:
                    quality_bins["良好(0.6-0.8)"] += 1
                else:
                    quality_bins["中等(<0.6)"] += 1

            for quality, count in quality_bins.items():
                lines.append(f"- {quality}: {count}个片段")
            lines.append("")

        return "\n".join(lines)

    def _generate_insights_summary(self, segments: List[NarrativeSegment]) -> str:
        """生成AI洞察汇总"""
        lines = ["## 🤖 AI 深度洞察汇总", ""]

        if not segments:
            return "\n".join(lines + ["暂无洞察数据"])

        all_insights = []
        for seg in segments:
            if seg.ai_analysis.key_insights:
                all_insights.extend(seg.ai_analysis.key_insights)

        if not all_insights:
            return "\n".join(lines + ["暂无洞察数据"])

        lines.append("### 💡 关键洞察集合")
        lines.append("")
        for i, insight in enumerate(all_insights[:15], 1):
            lines.append(f"{i}. {insight}")
        lines.append("")

        # 内容复用建议汇总
        reuse_suggestions = []
        for seg in segments:
            if seg.ai_analysis.reuse_suggestions:
                reuse_suggestions.extend(seg.ai_analysis.reuse_suggestions)

        if reuse_suggestions:
            lines.append("### ✂️ 内容复用建议汇总")
            lines.append("")
            unique_suggestions = list(set(reuse_suggestions))
            for i, suggestion in enumerate(unique_suggestions[:10], 1):
                lines.append(f"{i}. {suggestion}")
            lines.append("")

        return "\n".join(lines)

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
        td = timedelta(milliseconds=ms)
        hours, remainder = divmod(td.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _get_current_date(self) -> str:
        """获取当前日期"""
        from datetime import datetime
        return datetime.now().strftime("%Y年%m月%d日")

    def _get_entity_emoji(self, entity_type: str) -> str:
        """获取实体类型的emoji"""
        emoji_map = {
            "persons": "👤",
            "countries": "🌍",
            "organizations": "🏢",
            "time_points": "📅",
            "events": "📌",
            "concepts": "💭"
        }
        return emoji_map.get(entity_type, "📎")

    def save(self, report: str, output_path: Path):
        """保存报告到文件"""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(report)
        logger.info(f"报告已保存到: {output_path}")
