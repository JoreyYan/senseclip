"""
视频处理Pipeline v2 - 支持叙事片段识别和深度语义分析
"""

import sys
from pathlib import Path
from typing import List, Optional
import time

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Utterance, Atom, NarrativeSegment, SegmentMeta
from parsers import SRTParser, Cleaner
from atomizers import Atomizer, AtomValidator, OverlapFixer
from structurers import SegmentIdentifier
from analyzers import DeepAnalyzer
from utils import save_jsonl, save_json, setup_logger

logger = setup_logger(__name__)


class PipelineConfig:
    """Pipeline配置"""

    def __init__(
        self,
        # 输入配置
        input_srt_path: str = "data/raw/test.srt",
        # 切分配置
        auto_segment: bool = True,
        segment_duration_minutes: int = 10,
        segment_threshold_minutes: int = 15,
        # 原子化配置
        batch_size: int = 50,
        prompt_version: str = 'v2',
        use_cache: bool = True,
        use_checkpoint: bool = True,
        # 后处理配置
        fix_overlap: bool = True,
        overlap_strategy: str = 'proportional_split',
        # 语义分析配置
        enable_semantic_analysis: bool = True,  # 新增
        identify_narrative_segments: bool = True,  # 新增
        deep_analyze_segments: bool = True,  # 新增
        # 输出配置
        output_dir: str = "data/output",
        save_segments: bool = True,
        save_frontend_data: bool = True,
        save_narrative_segments: bool = True  # 新增
    ):
        self.input_srt_path = input_srt_path
        self.auto_segment = auto_segment
        self.segment_duration_minutes = segment_duration_minutes
        self.segment_threshold_minutes = segment_threshold_minutes
        self.batch_size = batch_size
        self.prompt_version = prompt_version
        self.use_cache = use_cache
        self.use_checkpoint = use_checkpoint
        self.fix_overlap = fix_overlap
        self.overlap_strategy = overlap_strategy
        self.enable_semantic_analysis = enable_semantic_analysis
        self.identify_narrative_segments = identify_narrative_segments
        self.deep_analyze_segments = deep_analyze_segments
        self.output_dir = output_dir
        self.save_segments = save_segments
        self.save_frontend_data = save_frontend_data
        self.save_narrative_segments = save_narrative_segments


class VideoProcessorV2:
    """视频处理器 v2 - 集成语义分析"""

    def __init__(self, api_key: str, config: PipelineConfig):
        self.api_key = api_key
        self.config = config
        self.output_path = Path(config.output_dir)
        self.output_path.mkdir(parents=True, exist_ok=True)

    def process(self, time_limit_ms: Optional[int] = None) -> dict:
        """
        执行完整pipeline（包含语义分析）

        Args:
            time_limit_ms: 可选的时间限制（毫秒）

        Returns:
            处理结果字典
        """
        start_time = time.time()

        print("\n" + "="*70)
        print("视频处理Pipeline v2 - 语义理解版")
        print("="*70)

        # Step 1: 解析和清洗
        utterances = self._parse_and_clean()

        # 应用时间限制
        if time_limit_ms:
            utterances = [u for u in utterances if u.start_ms < time_limit_ms]
            print(f"  [时间限制] 处理前{time_limit_ms//60000}分钟，{len(utterances)}条字幕")

        # Step 2: 原子化
        atoms = self._atomize(utterances)

        # Step 3: 修复重叠
        atoms, overlaps_fixed = self._fix_overlaps(atoms)

        # Step 4: 质量验证
        report = self._validate_quality(atoms, utterances)

        # Step 5: 语义分析（新增）
        narrative_segments = []
        semantic_stats = {}

        if self.config.enable_semantic_analysis:
            print("\n" + "="*70)
            print("语义分析阶段")
            print("="*70)

            # Step 5.1: 识别叙事片段
            if self.config.identify_narrative_segments:
                segment_metas = self._identify_segments(atoms)
            else:
                # 跳过识别，将所有原子作为一个片段
                segment_metas = [self._create_whole_segment(atoms)]

            # Step 5.2: 深度分析片段
            if self.config.deep_analyze_segments and len(segment_metas) > 0:
                narrative_segments = self._deep_analyze(segment_metas, atoms)

                semantic_stats = {
                    'segment_count': len(segment_metas),
                    'analyzed_count': len(narrative_segments),
                    'avg_segment_duration_min': sum(s.duration_minutes for s in narrative_segments) / len(narrative_segments) if narrative_segments else 0,
                    'total_api_calls': len(segment_metas) * 2  # 识别 + 分析
                }

        # 保存结果
        stats = self._save_results(atoms, report, narrative_segments, utterances)

        # 合并语义统计
        if semantic_stats:
            stats['semantic'] = semantic_stats

        # 添加处理时间
        elapsed_time = time.time() - start_time

        # 打印总结
        self._print_summary(atoms, report, narrative_segments, elapsed_time)

        return {
            'atoms': atoms,
            'report': report,
            'narrative_segments': narrative_segments,
            'stats': stats,
            'elapsed_time': elapsed_time,
            'overlaps_fixed': overlaps_fixed
        }

    def _parse_and_clean(self) -> List[Utterance]:
        """解析和清洗字幕"""
        print("\n[1/6] 解析和清洗...")
        parser = SRTParser()
        utterances = parser.parse(self.config.input_srt_path)
        print(f"  解析完成：{len(utterances)}条字幕")

        cleaner = Cleaner()
        utterances_clean = cleaner.clean(utterances)
        print(f"  清洗完成：{len(utterances_clean)}条")

        return utterances_clean

    def _atomize(self, utterances: List[Utterance]) -> List[Atom]:
        """原子化"""
        print("\n[2/6] 原子化...")

        checkpoint_id = "whole_video_v2" if self.config.use_checkpoint else None
        atomizer = Atomizer(
            self.api_key,
            batch_size=self.config.batch_size,
            prompt_version=self.config.prompt_version,
            use_cache=self.config.use_cache,
            checkpoint_id=checkpoint_id
        )

        atoms = atomizer.atomize(utterances)
        stats = atomizer.client.get_stats()
        print(f"  生成原子：{len(atoms)}个")
        print(f"  API调用：{stats['total_calls']}次")
        print(f"  预估成本：{stats['estimated_cost']}")

        return atoms

    def _fix_overlaps(self, atoms: List[Atom]) -> tuple[List[Atom], int]:
        """修复时间重叠"""
        print("\n[3/6] 修复时间重叠...")

        if self.config.fix_overlap:
            fixer = OverlapFixer(strategy=self.config.overlap_strategy)
            atoms_fixed = fixer.fix(atoms)
            overlap_report = fixer.get_overlap_report(atoms, atoms_fixed)
            print(f"  修复重叠：{overlap_report['fixed_count']}处")
            return atoms_fixed, overlap_report['fixed_count']
        else:
            print(f"  跳过重叠修复")
            return atoms, 0

    def _validate_quality(self, atoms: List[Atom], utterances: List[Utterance]) -> dict:
        """质量验证"""
        print("\n[4/6] 质量验证...")
        validator = AtomValidator()
        report = validator.validate(atoms, utterances)
        print(f"  质量评分：{report['quality_score']}")
        print(f"  时间覆盖：{report['coverage_rate']*100:.1f}%")
        return report

    def _identify_segments(self, atoms: List[Atom]) -> List[SegmentMeta]:
        """识别叙事片段"""
        print("\n[5/6] 识别叙事片段...")

        identifier = SegmentIdentifier(self.api_key)
        segment_metas = identifier.identify_segments(atoms)

        print(f"  识别完成：{len(segment_metas)}个叙事片段")

        for seg in segment_metas:
            print(f"    - SEG_{seg.segment_num:03d}: {seg.start_time} - {seg.end_time} ({seg.duration_minutes:.1f}分钟, {len(seg.atoms)}个原子)")

        return segment_metas

    def _deep_analyze(self, segment_metas: List[SegmentMeta], atoms: List[Atom]) -> List[NarrativeSegment]:
        """深度分析片段"""
        print("\n[6/6] 深度语义分析...")

        analyzer = DeepAnalyzer(self.api_key)
        narrative_segments = analyzer.analyze_batch(segment_metas, atoms, show_progress=True)

        print(f"  分析完成：{len(narrative_segments)}/{len(segment_metas)}个片段")

        return narrative_segments

    def _create_whole_segment(self, atoms: List[Atom]) -> SegmentMeta:
        """创建整体片段（当不识别片段时）"""
        atom_ids = [atom.atom_id for atom in atoms]
        start_ms = min(atom.start_ms for atom in atoms)
        end_ms = max(atom.end_ms for atom in atoms)

        return SegmentMeta(
            segment_num=1,
            atoms=atom_ids,
            start_ms=start_ms,
            end_ms=end_ms,
            duration_ms=end_ms - start_ms,
            reason="整体片段",
            confidence=1.0
        )

    def _save_results(
        self,
        atoms: List[Atom],
        report: dict,
        narrative_segments: List[NarrativeSegment],
        utterances: Optional[List[Utterance]]
    ) -> dict:
        """保存处理结果"""
        print("\n保存结果...")

        # 保存原子列表
        save_jsonl(atoms, str(self.output_path / "atoms.jsonl"))
        print(f"  [OK] atoms.jsonl")

        # 保存验证报告
        save_json(report, str(self.output_path / "validation.json"))
        print(f"  [OK] validation.json")

        # 保存叙事片段
        if self.config.save_narrative_segments and narrative_segments:
            segments_data = [seg.to_dict() for seg in narrative_segments]
            save_json(segments_data, str(self.output_path / "narrative_segments.json"))
            print(f"  [OK] narrative_segments.json ({len(narrative_segments)}个片段)")

        # 保存前端数据
        if self.config.save_frontend_data:
            atoms_for_frontend = [
                {
                    "atom_id": atom.atom_id,
                    "start_ms": atom.start_ms,
                    "end_ms": atom.end_ms,
                    "duration_ms": atom.duration_ms,
                    "start_time": atom.start_time,
                    "end_time": atom.end_time,
                    "duration_seconds": atom.duration_seconds,
                    "merged_text": atom.merged_text,
                    "type": atom.type,
                    "completeness": atom.completeness,
                    "source_utterance_ids": atom.source_utterance_ids
                }
                for atom in atoms
            ]

            frontend_data = {
                "atoms": atoms_for_frontend,
                "report": report
            }

            # 如果有叙事片段，也添加到前端数据
            if narrative_segments:
                frontend_data["narrative_segments"] = [
                    {
                        "segment_id": seg.segment_id,
                        "title": seg.title,
                        "start_ms": seg.start_ms,
                        "end_ms": seg.end_ms,
                        "duration_minutes": seg.duration_minutes,
                        "summary": seg.summary,
                        "topics": seg.topics.model_dump(),
                        "importance_score": seg.importance_score
                    }
                    for seg in narrative_segments
                ]

            save_json(frontend_data, str(self.output_path / "overview.json"))
            print(f"  [OK] overview.json")

        # 返回统计信息
        stats = {
            'atom_count': len(atoms),
            'narrative_segment_count': len(narrative_segments) if narrative_segments else 0
        }

        return stats

    def _print_summary(
        self,
        atoms: List[Atom],
        report: dict,
        narrative_segments: List[NarrativeSegment],
        elapsed_time: float
    ):
        """打印处理总结"""
        print("\n" + "="*70)
        print("处理完成！")
        print("="*70)
        print(f"总耗时: {elapsed_time:.1f}秒")
        print(f"\n【原子化结果】")
        print(f"  原子数量: {len(atoms)}个")
        print(f"  质量评分: {report['quality_score']}")
        print(f"  时间覆盖: {report['coverage_rate']*100:.1f}%")

        print(f"\n【类型分布】")
        for t, count in report['type_distribution'].items():
            print(f"  {t}: {count}个")

        if narrative_segments:
            print(f"\n【语义分析结果】")
            print(f"  叙事片段: {len(narrative_segments)}个")
            avg_duration = sum(s.duration_minutes for s in narrative_segments) / len(narrative_segments)
            print(f"  平均时长: {avg_duration:.1f}分钟")

            # 显示片段标题
            print(f"\n【片段列表】")
            for seg in narrative_segments[:5]:  # 只显示前5个
                print(f"  {seg.segment_id}: {seg.title}")
                print(f"    时长: {seg.duration_minutes:.1f}分钟 | 重要性: {seg.importance_score:.2f} | 质量: {seg.quality_score:.2f}")
                print(f"    主题: {seg.topics.primary_topic}")
                tags = ', '.join(seg.topics.free_tags[:5])
                print(f"    标签: {tags}")
                print()

            if len(narrative_segments) > 5:
                print(f"  ... 还有{len(narrative_segments) - 5}个片段")

        print("="*70)
