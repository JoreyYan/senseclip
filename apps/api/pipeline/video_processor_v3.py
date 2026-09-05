"""
视频处理Pipeline v3 - 集成向量化和语义搜索

新增功能：
- 自动生成原子和片段的向量嵌入
- 存储到 Qdrant 向量数据库
- 支持语义搜索
"""

import sys
from pathlib import Path
from typing import List, Optional
import time
import os

sys.path.insert(0, str(Path(__file__).parent.parent))

from models import Utterance, Atom, NarrativeSegment, SegmentMeta
from parsers import SRTParser, Cleaner
from atomizers import Atomizer, AtomValidator, OverlapFixer
from structurers import SegmentIdentifier
from analyzers import DeepAnalyzer
from analyzers.entity_extractor import EntityExtractor
from analyzers.topic_network_builder import TopicNetworkBuilder
from analyzers.knowledge_graph_builder import KnowledgeGraphBuilder
from analyzers.structure_report_generator import StructureReportGenerator
from analyzers.creative_angle_analyzer import CreativeAngleAnalyzer
from analyzers.atom_annotator import AtomAnnotator
from embedders.embedding_generator import EmbeddingGenerator
from vectorstores.qdrant_store import QdrantVectorStore
from searchers.semantic_search import SemanticSearchEngine
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
        enable_semantic_analysis: bool = True,
        identify_narrative_segments: bool = True,
        deep_analyze_segments: bool = True,
        # 标注配置（新增）
        enable_atom_annotation: bool = True,
        annotation_batch_size: int = 10,
        # 向量化配置（新增）
        enable_vectorization: bool = True,
        embedding_model: str = 'text-embedding-3-small',
        vector_store_path: Optional[str] = None,  # None表示使用内存模式
        vectorize_atoms: bool = True,
        vectorize_segments: bool = True,
        # 输出配置
        output_dir: str = "data/output",
        save_segments: bool = True,
        save_frontend_data: bool = True,
        save_narrative_segments: bool = True
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
        self.enable_atom_annotation = enable_atom_annotation
        self.annotation_batch_size = annotation_batch_size
        self.enable_vectorization = enable_vectorization
        self.embedding_model = embedding_model
        self.vector_store_path = vector_store_path
        self.vectorize_atoms = vectorize_atoms
        self.vectorize_segments = vectorize_segments
        self.output_dir = output_dir
        self.save_segments = save_segments
        self.save_frontend_data = save_frontend_data
        self.save_narrative_segments = save_narrative_segments


class VideoProcessorV3:
    """视频处理器 v3 - 集成向量化和语义搜索"""

    def __init__(self, api_key: str, openai_api_key: str, config: PipelineConfig):
        self.api_key = api_key  # Claude API Key
        self.openai_api_key = openai_api_key
        self.config = config
        self.output_path = Path(config.output_dir)
        self.output_path.mkdir(parents=True, exist_ok=True)

        # 初始化向量化组件
        self.embedder = None
        self.vector_store = None
        self.search_engine = None

        if config.enable_vectorization:
            self._init_vector_components()

    def _init_vector_components(self):
        """初始化向量化组件"""
        print("\n初始化向量化组件...")

        # 初始化 Embedding Generator
        self.embedder = EmbeddingGenerator(
            api_key=self.openai_api_key,
            model=self.config.embedding_model
        )
        print(f"  [OK] EmbeddingGenerator ({self.config.embedding_model})")

        # 初始化 Vector Store
        location = self.config.vector_store_path if self.config.vector_store_path else ":memory:"
        self.vector_store = QdrantVectorStore(
            location=location,
            collection_name="video_atoms"
        )

        # 创建 collection
        self.vector_store.create_collection(
            dimension=self.embedder.model_config['dimension'],
            distance="Cosine",
            recreate=True
        )
        print(f"  [OK] QdrantVectorStore ({'内存模式' if location == ':memory:' else '持久化模式'})")

        # 初始化 Search Engine
        self.search_engine = SemanticSearchEngine(
            vector_store=self.vector_store,
            embedder=self.embedder
        )
        print(f"  [OK] SemanticSearchEngine")

    def process(self, time_limit_ms: Optional[int] = None) -> dict:
        """
        执行完整pipeline（包含语义分析和向量化）

        Args:
            time_limit_ms: 可选的时间限制（毫秒）

        Returns:
            处理结果字典
        """
        start_time = time.time()

        print("\n" + "="*70)
        print("视频处理Pipeline v3 - 向量化版")
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

        # Step 5: 语义分析
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
                segment_metas = [self._create_whole_segment(atoms)]

            # Step 5.2: 深度分析片段
            if self.config.deep_analyze_segments and len(segment_metas) > 0:
                narrative_segments = self._deep_analyze(segment_metas, atoms)

                semantic_stats = {
                    'segment_count': len(segment_metas),
                    'analyzed_count': len(narrative_segments),
                    'avg_segment_duration_min': sum(s.duration_minutes for s in narrative_segments) / len(narrative_segments) if narrative_segments else 0,
                    'total_api_calls': len(segment_metas) * 2
                }

        # Step 6: 原子级别语义标注
        annotation_stats = {}
        atom_annotations = []
        if self.config.enable_atom_annotation:
            print("\n" + "="*70)
            print("原子语义标注阶段")
            print("="*70)
            annotation_stats, atom_annotations = self._annotate_atoms(atoms, narrative_segments)

        # Step 7: 向量化
        vector_stats = {}
        if self.config.enable_vectorization:
            vector_stats = self._vectorize(atoms, narrative_segments)

        # Step 8: 构建知识索引（实体、主题、图谱）
        index_stats = {}
        entities_data = {}
        topics_data = {}
        graph_data = {}
        if len(narrative_segments) > 0:
            index_stats, entities_data, topics_data, graph_data = self._build_knowledge_indexes(narrative_segments, atoms)

        # Step 9: 生成理解展示层报告
        report_stats = {}
        if len(narrative_segments) > 0 and entities_data:
            report_stats = self._generate_reports(atoms, narrative_segments, entities_data, topics_data, graph_data)

        # 保存结果
        stats = self._save_results(atoms, report, narrative_segments, utterances)

        # 合并统计信息
        if semantic_stats:
            stats['semantic'] = semantic_stats
        if annotation_stats:
            stats['annotation'] = annotation_stats
        if vector_stats:
            stats['vector'] = vector_stats
        if index_stats:
            stats['index'] = index_stats
        if report_stats:
            stats['reports'] = report_stats

        # 添加处理时间
        elapsed_time = time.time() - start_time

        # 打印总结
        self._print_summary(atoms, report, narrative_segments, vector_stats, elapsed_time)

        return {
            'atoms': atoms,
            'report': report,
            'narrative_segments': narrative_segments,
            'atom_annotations': atom_annotations,  # 新增：原子标注结果
            'stats': stats,
            'elapsed_time': elapsed_time,
            'overlaps_fixed': overlaps_fixed,
            'search_engine': self.search_engine  # 返回搜索引擎供后续使用
        }

    def _parse_and_clean(self) -> List[Utterance]:
        """解析和清洗字幕"""
        print("\n[1/9] 解析和清洗...")
        parser = SRTParser()
        utterances = parser.parse(self.config.input_srt_path)
        print(f"  解析完成：{len(utterances)}条字幕")

        cleaner = Cleaner()
        utterances_clean = cleaner.clean(utterances)
        print(f"  清洗完成：{len(utterances_clean)}条")

        return utterances_clean

    def _atomize(self, utterances: List[Utterance]) -> List[Atom]:
        """原子化"""
        print("\n[2/9] 原子化...")

        checkpoint_id = "whole_video_v3" if self.config.use_checkpoint else None
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
        print("\n[3/9] 修复时间重叠...")

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
        print("\n[4/7] 质量验证...")
        validator = AtomValidator()
        report = validator.validate(atoms, utterances)
        print(f"  质量评分：{report['quality_score']}")
        print(f"  时间覆盖：{report['coverage_rate']*100:.1f}%")
        return report

    def _identify_segments(self, atoms: List[Atom]) -> List[SegmentMeta]:
        """识别叙事片段"""
        print("\n[5/7] 识别叙事片段...")

        identifier = SegmentIdentifier(self.api_key)
        segment_metas = identifier.identify_segments(atoms)

        print(f"  识别完成：{len(segment_metas)}个叙事片段")

        for seg in segment_metas:
            print(f"    - SEG_{seg.segment_num:03d}: {seg.start_time} - {seg.end_time} ({seg.duration_minutes:.1f}分钟, {len(seg.atoms)}个原子)")

        return segment_metas

    def _deep_analyze(self, segment_metas: List[SegmentMeta], atoms: List[Atom]) -> List[NarrativeSegment]:
        """深度分析片段"""
        print("\n[6/7] 深度语义分析...")

        analyzer = DeepAnalyzer(self.api_key)
        narrative_segments = analyzer.analyze_batch(segment_metas, atoms, show_progress=True)

        print(f"  分析完成：{len(narrative_segments)}/{len(segment_metas)}个片段")

        return narrative_segments

    def _annotate_atoms(self, atoms: List[Atom], narrative_segments: List[NarrativeSegment]) -> tuple[dict, list]:
        """原子级别语义标注"""
        from models.entity_index import AtomAnnotation

        print("\n[7/9] 原子语义标注...")

        # 初始化标注器
        annotator = AtomAnnotator(self.api_key)

        # 按叙事段落组织原子进行标注
        all_annotations = []
        annotation_stats = {
            'total_atoms': len(atoms),
            'annotated_atoms': 0,
            'entities_found': 0,
            'topics_found': 0,
            'avg_importance': 0.0,
            'failed_atoms': 0
        }

        if narrative_segments:
            # 按段落标注
            for segment in narrative_segments:
                print(f"  标注段落 {segment.segment_id}...")

                # 获取段落内的原子
                segment_atom_ids = set(segment.atoms)
                segment_atoms = [atom for atom in atoms if atom.atom_id in segment_atom_ids]

                if segment_atoms:
                    try:
                        # 批量标注段落内的原子
                        segment_annotations = annotator.annotate_atoms_batch(
                            segment_atoms,
                            segment_id=segment.segment_id,
                            batch_size=self.config.annotation_batch_size
                        )
                        all_annotations.extend(segment_annotations)

                        # 更新统计
                        annotation_stats['annotated_atoms'] += len(segment_annotations)

                        # 统计实体和主题
                        for annotation in segment_annotations:
                            if annotation.entities:
                                annotation_stats['entities_found'] += len(annotation.entities)
                            if annotation.topics:
                                annotation_stats['topics_found'] += len(annotation.topics)

                        print(f"    完成 {len(segment_annotations)} 个原子")

                    except Exception as e:
                        logger.error(f"段落 {segment.segment_id} 标注失败: {e}")
                        annotation_stats['failed_atoms'] += len(segment_atoms)
        else:
            # 无段落时，直接标注所有原子
            print("  标注全部原子...")
            try:
                all_annotations = annotator.annotate_atoms_batch(
                    atoms,
                    batch_size=self.config.annotation_batch_size
                )
                annotation_stats['annotated_atoms'] = len(all_annotations)
            except Exception as e:
                logger.error(f"原子标注失败: {e}")
                annotation_stats['failed_atoms'] = len(atoms)

        # 计算平均重要性
        if all_annotations:
            importance_scores = [ann.importance_score for ann in all_annotations]
            annotation_stats['avg_importance'] = sum(importance_scores) / len(importance_scores)

        # 保存标注结果
        if all_annotations:
            annotations_file = self.output_path / "atom_annotations.json"
            annotator.save_annotations(all_annotations, annotations_file)
            print(f"  [OK] 标注数据已保存: atom_annotations.json")

        print(f"  标注完成: {annotation_stats['annotated_atoms']}/{annotation_stats['total_atoms']} 个原子")
        print(f"  发现实体: {annotation_stats['entities_found']} 个")
        print(f"  发现主题: {annotation_stats['topics_found']} 个")
        print(f"  平均重要性: {annotation_stats['avg_importance']:.3f}")

        return annotation_stats, all_annotations

    def _vectorize(self, atoms: List[Atom], narrative_segments: List[NarrativeSegment]) -> dict:
        """向量化原子和片段"""
        print("\n[8/9] 向量化...")

        vector_stats = {
            'atoms_vectorized': 0,
            'segments_vectorized': 0,
            'embedding_tokens': 0,
            'embedding_cost': 0.0
        }

        # 向量化原子
        if self.config.vectorize_atoms and atoms:
            print(f"  向量化原子...")
            atom_texts = [atom.merged_text for atom in atoms]
            atom_embeddings = self.embedder.generate_batch(atom_texts)

            # 准备数据插入向量数据库
            atom_dicts = []
            for atom in atoms:
                atom_dict = {
                    "atom_id": atom.atom_id,
                    "merged_text": atom.merged_text,
                    "type": atom.type,
                    "completeness": atom.completeness,
                    "start_ms": atom.start_ms,
                    "end_ms": atom.end_ms,
                    "duration_seconds": atom.duration_seconds,
                    "source_utterance_ids": atom.source_utterance_ids
                }
                atom_dicts.append(atom_dict)

            # 批量插入
            inserted_count = self.vector_store.insert_atoms(
                atoms=atom_dicts,
                embeddings=atom_embeddings
            )

            vector_stats['atoms_vectorized'] = inserted_count
            print(f"    [OK] {inserted_count}个原子")

        # 向量化片段
        if self.config.vectorize_segments and narrative_segments:
            print(f"  向量化叙事片段...")
            # 过滤掉 summary 为空的片段
            valid_segments = [seg for seg in narrative_segments if seg.summary and seg.summary.strip()]
            if not valid_segments:
                print(f"    [跳过] 无有效片段")
                vector_stats['segments_vectorized'] = 0
            else:
                segment_texts = [seg.summary for seg in valid_segments]
                segment_embeddings = self.embedder.generate_batch(segment_texts)

                # 准备数据插入向量数据库
                segment_dicts = []
                for seg in valid_segments:
                    segment_dict = {
                        "segment_id": seg.segment_id,
                        "title": seg.title,
                        "summary": seg.summary,
                        "full_text": seg.full_text,
                        "start_ms": seg.start_ms,
                        "end_ms": seg.end_ms,
                        "duration_minutes": seg.duration_minutes,
                        "topics": seg.topics.model_dump(),
                        "entities": seg.entities.model_dump(),
                        "importance_score": seg.importance_score,
                        "quality_score": seg.quality_score
                    }
                    segment_dicts.append(segment_dict)

                # 批量插入
                inserted_count = self.vector_store.insert_segments(
                    segments=segment_dicts,
                    embeddings=segment_embeddings
                )

                vector_stats['segments_vectorized'] = inserted_count
                print(f"    [OK] {inserted_count}个片段")

        # 获取 embedding 统计
        embedding_stats = self.embedder.get_stats()
        vector_stats['embedding_tokens'] = embedding_stats['total_tokens']
        vector_stats['embedding_cost'] = self.embedder.stats['estimated_cost']  # 获取原始数值

        print(f"  向量化完成：{vector_stats['atoms_vectorized']}个原子 + {vector_stats['segments_vectorized']}个片段")
        print(f"  预估成本：${vector_stats['embedding_cost']:.6f}")

        return vector_stats

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

    def _build_knowledge_indexes(self, narrative_segments: List[NarrativeSegment], atoms: List[Atom]):
        """构建知识索引（实体、主题、图谱）"""
        print("\n[9/9] 构建知识索引...")

        # 实体提取
        entity_extractor = EntityExtractor()
        entities = entity_extractor.extract(narrative_segments, atoms)
        entity_path = self.output_path / "entities.json"
        entity_extractor.save(entities, entity_path)
        print(f"  [OK] 实体聚合完成: {entities['statistics']['total_entities']}个实体")

        # 主题网络
        topic_builder = TopicNetworkBuilder()
        topics = topic_builder.build(narrative_segments)
        topic_path = self.output_path / "topics.json"
        topic_builder.save(topics, topic_path)
        print(f"  [OK] 主题网络完成: {topics['statistics']['total_primary_topics']}个主题")

        # 知识图谱
        graph_builder = KnowledgeGraphBuilder()
        graph = graph_builder.build(narrative_segments, entities, topics)
        graph_path = self.output_path / "knowledge_graph.json"  # 修正路径
        graph_builder.save(graph, graph_path)
        print(f"  [OK] 知识图谱完成: {graph['statistics']['total_nodes']}个节点, {graph['statistics']['total_edges']}条边")

        stats = {
            'entity_count': entities['statistics']['total_entities'],
            'topic_count': topics['statistics']['total_primary_topics'],
            'graph_nodes': graph['statistics']['total_nodes'],
            'graph_edges': graph['statistics']['total_edges']
        }

        return stats, entities, topics, graph

    def _generate_reports(
        self,
        atoms: List[Atom],
        narrative_segments: List[NarrativeSegment],
        entities: dict,
        topics: dict,
        graph: dict
    ) -> dict:
        """生成理解展示层报告"""
        print("\n[后处理] 生成理解展示层报告...")

        # 读取验证报告
        validation_path = self.output_path / "validation.json"
        validation = {}
        if validation_path.exists():
            import json
            with open(validation_path, 'r', encoding='utf-8') as f:
                validation = json.load(f)

        # 1. 生成视频结构报告
        report_generator = StructureReportGenerator()
        structure_report = report_generator.generate(
            atoms=atoms,
            segments=narrative_segments,
            entities=entities,
            topics=topics,
            validation=validation
        )
        report_path = self.output_path / "video_structure.md"
        report_generator.save(structure_report, report_path)
        print(f"  [OK] 视频结构报告: video_structure.md")

        # 2. 生成创作角度分析
        angle_analyzer = CreativeAngleAnalyzer()
        creative_angles = angle_analyzer.analyze(
            atoms=atoms,
            segments=narrative_segments,
            entities=entities,
            topics=topics,
            graph=graph
        )
        angle_path = self.output_path / "creative_angles.json"
        angle_analyzer.save(creative_angles, angle_path)
        print(f"  [OK] 创作角度分析: creative_angles.json")

        # 统计信息
        clip_count = len(creative_angles.get('clip_recommendations', []))
        angle_count = len(creative_angles.get('content_angles', []))

        return {
            'structure_report_generated': True,
            'creative_angles_generated': True,
            'clip_recommendations': clip_count,
            'content_angles': angle_count
        }

    def _print_summary(
        self,
        atoms: List[Atom],
        report: dict,
        narrative_segments: List[NarrativeSegment],
        vector_stats: dict,
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
            for seg in narrative_segments[:5]:
                print(f"  {seg.segment_id}: {seg.title}")
                print(f"    时长: {seg.duration_minutes:.1f}分钟 | 重要性: {seg.importance_score:.2f} | 质量: {seg.quality_score:.2f}")
                print(f"    主题: {seg.topics.primary_topic}")
                tags = ', '.join(seg.topics.free_tags[:5])
                print(f"    标签: {tags}")
                print()

            if len(narrative_segments) > 5:
                print(f"  ... 还有{len(narrative_segments) - 5}个片段")

        if vector_stats:
            print(f"\n【向量化结果】")
            print(f"  原子向量: {vector_stats.get('atoms_vectorized', 0)}个")
            print(f"  片段向量: {vector_stats.get('segments_vectorized', 0)}个")
            print(f"  Embedding tokens: {vector_stats.get('embedding_tokens', 0)}")
            print(f"  Embedding 成本: ${vector_stats.get('embedding_cost', 0):.6f}")

        print("="*70)
