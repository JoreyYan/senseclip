"""
视频处理Pipeline - 标准化的模块化架构
支持自动切分、缓存、断点续传、时间重叠修复
"""

import sys
from pathlib import Path
from typing import List, Optional
import time

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Utterance, Atom
from parsers import SRTParser, Cleaner
from atomizers import Atomizer, AtomValidator, OverlapFixer
from utils import save_jsonl, save_json, setup_logger

logger = setup_logger(__name__)


class PipelineConfig:
    """Pipeline配置"""

    def __init__(
        self,
        # 输入配置
        input_srt_path: str = "data/raw/test.srt",
        # 切分配置
        auto_segment: bool = True,  # 是否自动切分
        segment_duration_minutes: int = 10,  # 切分粒度（分钟）
        segment_threshold_minutes: int = 15,  # 超过此时长才切分
        # 原子化配置
        batch_size: int = 50,
        prompt_version: str = 'v1',
        use_cache: bool = True,
        use_checkpoint: bool = True,
        # 后处理配置
        fix_overlap: bool = True,
        overlap_strategy: str = 'proportional_split',
        # 输出配置
        output_dir: str = "data/output",
        save_segments: bool = True,
        save_frontend_data: bool = True
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
        self.output_dir = output_dir
        self.save_segments = save_segments
        self.save_frontend_data = save_frontend_data


class VideoProcessor:
    """视频处理器 - 标准化Pipeline"""

    def __init__(self, api_key: str, config: PipelineConfig):
        self.api_key = api_key
        self.config = config
        self.output_path = Path(config.output_dir)
        self.output_path.mkdir(parents=True, exist_ok=True)

    def process(self, time_limit_ms: Optional[int] = None) -> dict:
        """
        执行完整pipeline

        Args:
            time_limit_ms: 可选的时间限制（毫秒），用于处理前N分钟

        Returns:
            处理结果字典
        """
        start_time = time.time()

        print("\n" + "="*60)
        print("视频处理Pipeline")
        print("="*60)

        # Step 1: 解析和清洗
        utterances = self._parse_and_clean()

        # 应用时间限制
        if time_limit_ms:
            utterances = [u for u in utterances if u.start_ms < time_limit_ms]
            print(f"  [时间限制] 处理前{time_limit_ms//60000}分钟，{len(utterances)}条字幕")

        # Step 2: 决定是否切分
        video_duration_minutes = max(u.end_ms for u in utterances) // 60000
        should_segment = (
            self.config.auto_segment and
            video_duration_minutes > self.config.segment_threshold_minutes
        )

        if should_segment:
            print(f"\n[Pipeline模式] 切分处理（视频{video_duration_minutes}分钟 > 阈值{self.config.segment_threshold_minutes}分钟）")
            result = self._process_segmented(utterances)
        else:
            print(f"\n[Pipeline模式] 整体处理（视频{video_duration_minutes}分钟 ≤ 阈值{self.config.segment_threshold_minutes}分钟）")
            result = self._process_whole(utterances)

        # 添加处理时间
        result['elapsed_time'] = time.time() - start_time

        # 打印总结
        self._print_summary(result)

        return result

    def _parse_and_clean(self) -> List[Utterance]:
        """解析和清洗字幕"""
        print("\n[1/N] 解析和清洗...")
        parser = SRTParser()
        utterances = parser.parse(self.config.input_srt_path)
        print(f"  解析完成：{len(utterances)}条字幕")

        cleaner = Cleaner()
        utterances_clean = cleaner.clean(utterances)
        print(f"  清洗完成：{len(utterances_clean)}条")

        return utterances_clean

    def _process_whole(self, utterances: List[Utterance]) -> dict:
        """整体处理（不切分）"""
        print("\n[2/4] 原子化...")

        # 原子化
        checkpoint_id = "whole_video" if self.config.use_checkpoint else None
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

        # 修复重叠
        print("\n[3/4] 后处理...")
        if self.config.fix_overlap:
            fixer = OverlapFixer(strategy=self.config.overlap_strategy)
            atoms_fixed = fixer.fix(atoms)
            overlap_report = fixer.get_overlap_report(atoms, atoms_fixed)
            print(f"  修复重叠：{overlap_report['fixed_count']}处")
            atoms = atoms_fixed
        else:
            overlap_report = {'fixed_count': 0}

        # 质量验证
        print("\n[4/4] 质量验证...")
        validator = AtomValidator()
        report = validator.validate(atoms, utterances)
        print(f"  质量评分：{report['quality_score']}")
        print(f"  时间覆盖：{report['coverage_rate']*100:.1f}%")

        # 保存结果
        self._save_results(atoms, report, stats, None)

        return {
            'atoms': atoms,
            'report': report,
            'stats': stats,
            'segment_count': 1,
            'overlaps_fixed': overlap_report['fixed_count']
        }

    def _process_segmented(self, utterances: List[Utterance]) -> dict:
        """切分处理"""
        max_time_ms = max(u.end_ms for u in utterances)
        segment_duration_ms = self.config.segment_duration_minutes * 60 * 1000
        total_segments = (max_time_ms + segment_duration_ms - 1) // segment_duration_ms

        print(f"  切分粒度：{self.config.segment_duration_minutes}分钟/片段")
        print(f"  片段总数：{total_segments}个")

        if self.config.save_segments:
            segments_dir = self.output_path / "segments"
            segments_dir.mkdir(exist_ok=True)

        all_atoms = []
        all_stats = {
            'segments': [],
            'total_api_calls': 0,
            'total_cost': 0.0,
            'total_overlaps_fixed': 0
        }

        print("\n[2/4] 原子化（分片段）...")
        for seg_num in range(1, total_segments + 1):
            seg_start_ms = (seg_num - 1) * segment_duration_ms
            seg_end_ms = min(seg_num * segment_duration_ms, max_time_ms)

            print(f"\n--- 片段 {seg_num}/{total_segments} ---")
            print(f"  时间范围: {self._ms_to_time(seg_start_ms)} - {self._ms_to_time(seg_end_ms)}")

            # 提取片段字幕
            segment_utterances = [
                u for u in utterances
                if u.start_ms < seg_end_ms and u.end_ms > seg_start_ms
            ]

            if len(segment_utterances) == 0:
                print(f"  [跳过] 该片段无字幕")
                continue

            print(f"  字幕数量: {len(segment_utterances)}条")

            try:
                # 原子化
                checkpoint_id = f"segment_{seg_num:03d}" if self.config.use_checkpoint else None
                atomizer = Atomizer(
                    self.api_key,
                    batch_size=self.config.batch_size,
                    prompt_version=self.config.prompt_version,
                    use_cache=self.config.use_cache,
                    checkpoint_id=checkpoint_id
                )

                segment_atoms = atomizer.atomize(segment_utterances)
                print(f"  生成原子: {len(segment_atoms)}个")

                # 修复重叠
                if self.config.fix_overlap:
                    fixer = OverlapFixer(strategy=self.config.overlap_strategy)
                    segment_atoms_fixed = fixer.fix(segment_atoms)
                    overlap_report = fixer.get_overlap_report(segment_atoms, segment_atoms_fixed)
                    print(f"  修复重叠: {overlap_report['fixed_count']}处")
                    segment_atoms = segment_atoms_fixed
                else:
                    overlap_report = {'fixed_count': 0}

                # 统计
                stats = atomizer.client.get_stats()
                segment_stats = {
                    'segment_num': seg_num,
                    'start_ms': seg_start_ms,
                    'end_ms': seg_end_ms,
                    'atoms_count': len(segment_atoms),
                    'api_calls': stats['total_calls'],
                    'cost': stats['estimated_cost'],
                    'overlaps_fixed': overlap_report['fixed_count']
                }
                all_stats['segments'].append(segment_stats)
                all_stats['total_api_calls'] += stats['total_calls']
                all_stats['total_overlaps_fixed'] += overlap_report['fixed_count']

                # 解析成本
                cost_str = stats['estimated_cost'].replace('$', '')
                all_stats['total_cost'] += float(cost_str)

                # 保存片段
                if self.config.save_segments:
                    segment_file = segments_dir / f"segment_{seg_num:03d}.json"
                    segment_data = {
                        'segment_info': segment_stats,
                        'atoms': [atom.to_dict() for atom in segment_atoms]
                    }
                    save_json(segment_data, str(segment_file))
                    print(f"  已保存: {segment_file.name}")

                all_atoms.extend(segment_atoms)

            except Exception as e:
                logger.error(f"  片段{seg_num}处理失败: {e}")
                print(f"  [失败] 可重新运行继续处理")
                continue

        # 质量验证
        print("\n[3/4] 全局质量验证...")
        validator = AtomValidator()
        report = validator.validate(all_atoms, utterances)
        print(f"  质量评分：{report['quality_score']}")
        print(f"  时间覆盖：{report['coverage_rate']*100:.1f}%")

        # 保存结果
        print("\n[4/4] 保存最终结果...")
        all_stats['total_cost_formatted'] = f"${all_stats['total_cost']:.2f}"
        self._save_results(all_atoms, report, all_stats, utterances)

        return {
            'atoms': all_atoms,
            'report': report,
            'stats': all_stats,
            'segment_count': len(all_stats['segments']),
            'overlaps_fixed': all_stats['total_overlaps_fixed']
        }

    def _save_results(self, atoms: List[Atom], report: dict, stats: dict, utterances: Optional[List[Utterance]]):
        """保存处理结果"""
        # 保存原子列表
        save_jsonl(atoms, str(self.output_path / "atoms.jsonl"))

        # 保存验证报告
        save_json(report, str(self.output_path / "validation.json"))

        # 保存统计信息
        save_json(stats, str(self.output_path / "stats.json"))

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
                "stats": stats,
                "report": report
            }
            save_json(frontend_data, str(self.output_path / "frontend_data.json"))

    def _print_summary(self, result: dict):
        """打印处理总结"""
        print("\n" + "="*60)
        print("处理完成！")
        print("="*60)
        print(f"总耗时: {result['elapsed_time']:.1f}秒")
        print(f"片段数: {result['segment_count']}个")
        print(f"原子数: {len(result['atoms'])}个")

        stats = result['stats']
        if 'total_api_calls' in stats:
            print(f"API调用: {stats['total_api_calls']}次")
            print(f"预估成本: {stats['total_cost_formatted']}")
        else:
            print(f"API调用: {stats['total_calls']}次")
            print(f"预估成本: {stats['estimated_cost']}")

        print(f"质量评分: {result['report']['quality_score']}")
        print(f"时间覆盖: {result['report']['coverage_rate']*100:.1f}%")
        print(f"修复重叠: {result['overlaps_fixed']}处")

        print("\n类型分布:")
        for t, count in result['report']['type_distribution'].items():
            print(f"  {t}: {count}个")
        print("="*60)

    def _ms_to_time(self, ms: int) -> str:
        """毫秒转时间格式"""
        hours = ms // 3600000
        minutes = (ms % 3600000) // 60000
        seconds = (ms % 60000) // 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
