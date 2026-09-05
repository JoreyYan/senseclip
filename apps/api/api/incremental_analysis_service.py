# -*- coding: utf-8 -*-
"""Incremental Analysis Service - Process video segments one at a time"""

import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
import threading
import logging

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzers.entity_extractor import EntityExtractor
from analyzers.topic_network_builder import TopicNetworkBuilder
from analyzers.knowledge_graph_builder import KnowledgeGraphBuilder
from analyzers.deep_analyzer import DeepAnalyzer
from analyzers.atom_annotator import AtomAnnotator
from api.segment_manager import SegmentManager, TimeSegment
from models import Atom, SegmentMeta
from models.entity_index import AtomAnnotation

logger = logging.getLogger(__name__)

class IncrementalAnalysisService:
    """Service for incremental segment-by-segment analysis"""

    def __init__(self, data_dir: Path, api_key: str = None):
        self.data_dir = data_dir
        self.api_key = api_key
        self.segment_manager = SegmentManager(data_dir, segment_duration_minutes=20)
        self.is_running = False
        self.stop_flag = False
        self.current_segment_id: Optional[str] = None
        self.analysis_thread: Optional[threading.Thread] = None

        # Accumulators for merged results
        self.entities_file = data_dir / "entities.json"
        self.topics_file = data_dir / "topics.json"
        self.graph_file = data_dir / "knowledge_graph.json"
        self.annotations_file = data_dir / "atom_annotations.json"

    def start_incremental_analysis(self, project_id: str):
        """Start incremental analysis"""
        if self.is_running:
            raise ValueError("Analysis already running")

        self.stop_flag = False
        self.is_running = True
        self.analysis_thread = threading.Thread(
            target=self._run_incremental_analysis,
            args=(project_id,),
            daemon=True
        )
        self.analysis_thread.start()

    def stop_analysis(self):
        """Stop incremental analysis"""
        self.stop_flag = True
        self.is_running = False

    def analyze_single_segment(self, project_id: str, segment_id: str):
        """Analyze a single specific segment"""
        if self.is_running:
            raise ValueError("Analysis already running")

        self.stop_flag = False
        self.is_running = True
        self.current_segment_id = segment_id

        # Update segment status to analyzing immediately
        self.segment_manager.update_segment_status(segment_id, "analyzing")

        analysis_thread = threading.Thread(
            target=self._run_single_segment_analysis,
            args=(project_id, segment_id),
            daemon=True
        )
        analysis_thread.start()

    def get_progress(self) -> Dict:
        """Get current analysis progress"""
        progress = self.segment_manager.get_analysis_progress()
        progress['is_running'] = self.is_running
        progress['current_segment'] = self.current_segment_id
        return progress

    def _run_incremental_analysis(self, project_id: str):
        """Main incremental analysis loop"""
        try:
            logger.info("Starting incremental analysis")

            # Load atoms
            atoms = self.segment_manager.load_atoms()
            if not atoms:
                logger.error("No atoms found")
                self.is_running = False
                return

            # Use list index for atom lookup instead of cyclical atom_id
            atoms_list = atoms

            # Initialize analyzers
            if not self.api_key:
                raise ValueError("API key required for deep analysis")

            deep_analyzer = DeepAnalyzer(self.api_key)
            entity_extractor = EntityExtractor()
            topic_builder = TopicNetworkBuilder()
            graph_builder = KnowledgeGraphBuilder()
            atom_annotator = AtomAnnotator(self.api_key)

            # Load existing results or initialize
            all_entities = self._load_or_init_entities()
            all_topics = self._load_or_init_topics()
            all_graph = self._load_or_init_graph()
            all_annotations = self._load_or_init_annotations()

            # Process segments one by one
            while not self.stop_flag:
                next_segment = self.segment_manager.get_next_pending_segment()

                if not next_segment:
                    logger.info("No more segments to process")
                    break

                self.current_segment_id = next_segment.segment_id
                logger.info(f"Processing segment {next_segment.segment_id} ({next_segment.start_time_str} - {next_segment.end_time_str})")

                # Check if atomization is complete
                if not next_segment.atomization_complete:
                    logger.warning(f"Segment {next_segment.segment_id} not atomized yet, skipping")
                    self.segment_manager.update_segment_status(
                        next_segment.segment_id,
                        "pending",
                        error_message="Atomization not complete"
                    )
                    break

                # Update status to analyzing
                self.segment_manager.update_segment_status(next_segment.segment_id, "analyzing")

                try:
                    # FIXED VERSION 2024-10-04: Use atom indices instead of cyclical IDs
                    logger.info(f"Processing segment {next_segment.segment_id} with {len(next_segment.atom_ids)} atom indices")
                    segment_atoms = []
                    for atom_index in next_segment.atom_ids:
                        if isinstance(atom_index, int) and 0 <= atom_index < len(atoms_list):
                            segment_atoms.append(atoms_list[atom_index])
                        else:
                            logger.warning(f"Invalid atom index: {atom_index}")

                    logger.info(f"Successfully resolved {len(segment_atoms)} atoms for {next_segment.segment_id}")

                    if not segment_atoms:
                        logger.warning(f"No atoms found for segment {next_segment.segment_id}")
                        self.segment_manager.update_segment_status(
                            next_segment.segment_id,
                            "failed",
                            error_message="No atoms in segment"
                        )
                        continue

                    # Create SegmentMeta for DeepAnalyzer
                    segment_meta = SegmentMeta(
                        segment_num=int(next_segment.segment_id.replace("SEG_", "")),
                        atoms=next_segment.atom_ids,
                        start_ms=next_segment.start_ms,
                        end_ms=next_segment.end_ms,
                        duration_ms=next_segment.duration_ms,
                        reason="Incremental analysis",
                        confidence=1.0
                    )

                    # Convert atom dicts to Atom objects
                    atom_objects = []
                    for atom_dict in segment_atoms:
                        start_ms = atom_dict.get('start_ms', 0)
                        end_ms = atom_dict.get('end_ms', 0)
                        atom_obj = Atom(
                            atom_id=atom_dict['atom_id'],
                            merged_text=atom_dict['merged_text'],
                            start_ms=start_ms,
                            end_ms=end_ms,
                            duration_ms=end_ms - start_ms,
                            type=atom_dict.get('type', 'fragment'),
                            completeness=atom_dict.get('completeness', '完整')
                        )
                        atom_objects.append(atom_obj)

                    # Run deep analysis to extract entities, topics etc.
                    logger.info(f"Running deep analysis on {len(segment_atoms)} atoms")
                    narrative_segment = deep_analyzer.analyze_segment(segment_meta, atom_objects)

                    # Annotate atoms with topics, entities, emotions
                    logger.info(f"Annotating {len(atom_objects)} atoms with semantic tags")
                    segment_annotations = atom_annotator.annotate_atoms_batch(
                        atom_objects,
                        segment_id=next_segment.segment_id,
                        narrative_id=None
                    )

                    # Merge annotations into accumulator
                    self._merge_annotations(all_annotations, segment_annotations)

                    # Extract entities using the real entities from deep analysis
                    logger.info(f"Extracting entities from analyzed segment")
                    # Filter atoms to only include current segment atoms to avoid duplicate counting
                    segment_atoms = [atom for atom in atoms_list if atom.get('atom_id') in next_segment.atom_ids]
                    logger.info(f"Processing {len(segment_atoms)} atoms from current segment (out of {len(atoms_list)} total)")
                    logger.info(f"Segment {next_segment.segment_id} atom_ids: {next_segment.atom_ids[:5]}...")
                    logger.info(f"Filtered segment atoms: {[atom['atom_id'] for atom in segment_atoms[:5]]}...")
                    segment_entities = entity_extractor.extract([narrative_segment], segment_atoms)

                    # Merge entities into accumulator
                    entity_count = self._merge_entities(all_entities, segment_entities, next_segment.segment_id)

                    # Build topics
                    logger.info("Building topic network")
                    segment_topics = topic_builder.build([narrative_segment])
                    self._merge_topics(all_topics, segment_topics, next_segment.segment_id)

                    # Build knowledge graph
                    logger.info("Building knowledge graph")
                    segment_graph = graph_builder.build([narrative_segment], segment_entities, segment_topics)
                    self._merge_graph(all_graph, segment_graph, next_segment.segment_id)

                    # Save updated results
                    self._save_results(all_entities, all_topics, all_graph, all_annotations)

                    # Update segment status
                    self.segment_manager.update_segment_status(
                        next_segment.segment_id,
                        "analyzed",
                        analysis_complete=True,
                        entity_count=entity_count
                    )

                    logger.info(f"Completed segment {next_segment.segment_id}, extracted {entity_count} entities")

                except Exception as e:
                    logger.error(f"Error processing segment {next_segment.segment_id}: {e}", exc_info=True)
                    self.segment_manager.update_segment_status(
                        next_segment.segment_id,
                        "failed",
                        error_message=str(e)
                    )

            logger.info("Incremental analysis completed")

        except Exception as e:
            logger.error(f"Incremental analysis failed: {e}", exc_info=True)

        finally:
            self.is_running = False
            self.current_segment_id = None

    def _run_single_segment_analysis(self, project_id: str, segment_id: str):
        """Run analysis on a single segment"""
        try:
            logger.info(f"Starting single segment analysis for {segment_id}")

            # Load atoms
            atoms = self.segment_manager.load_atoms()
            if not atoms:
                logger.error("No atoms found")
                return

            # Use list index for atom lookup instead of cyclical atom_id
            atoms_list = atoms

            # Get specific segment
            segments = self.segment_manager.load_segments_state()
            target_segment = self.segment_manager.get_segment_by_id(segment_id, segments)

            if not target_segment:
                logger.error(f"Segment {segment_id} not found")
                return

            if not target_segment.atomization_complete:
                logger.warning(f"Segment {segment_id} not atomized yet")
                self.segment_manager.update_segment_status(
                    segment_id, "pending", error_message="Atomization not complete"
                )
                return

            # Status already set to analyzing in caller

            # Initialize analyzers
            if not self.api_key:
                raise ValueError("API key required for deep analysis")

            deep_analyzer = DeepAnalyzer(self.api_key)
            entity_extractor = EntityExtractor()
            topic_builder = TopicNetworkBuilder()
            graph_builder = KnowledgeGraphBuilder()
            atom_annotator = AtomAnnotator(self.api_key)

            # Load existing results
            all_entities = self._load_or_init_entities()
            all_topics = self._load_or_init_topics()
            all_graph = self._load_or_init_graph()
            all_annotations = self._load_or_init_annotations()

            # Get atoms for this segment - FIXED 2024-10-04: Use atom indices instead of cyclical IDs
            segment_atoms = []
            for atom_index in target_segment.atom_ids:
                if isinstance(atom_index, int) and 0 <= atom_index < len(atoms_list):
                    segment_atoms.append(atoms_list[atom_index])
                else:
                    logger.warning(f"Invalid atom index: {atom_index}")

            if not segment_atoms:
                logger.warning(f"No atoms found for segment {segment_id}")
                self.segment_manager.update_segment_status(
                    segment_id, "failed", error_message="No atoms in segment"
                )
                return

            # Create SegmentMeta for DeepAnalyzer
            segment_meta = SegmentMeta(
                segment_num=int(segment_id.replace("SEG_", "")),
                atoms=target_segment.atom_ids,
                start_ms=target_segment.start_ms,
                end_ms=target_segment.end_ms,
                duration_ms=target_segment.duration_ms,
                reason="Single segment analysis",
                confidence=1.0
            )

            # Convert atom dicts to Atom objects
            atom_objects = []
            for atom_dict in segment_atoms:
                start_ms = atom_dict.get('start_ms', 0)
                end_ms = atom_dict.get('end_ms', 0)
                atom_obj = Atom(
                    atom_id=atom_dict['atom_id'],
                    merged_text=atom_dict['merged_text'],
                    start_ms=start_ms,
                    end_ms=end_ms,
                    duration_ms=end_ms - start_ms,
                    type=atom_dict.get('type', 'fragment'),
                    completeness=atom_dict.get('completeness', '完整')
                )
                atom_objects.append(atom_obj)

            # Run deep analysis to extract entities, topics etc.
            logger.info(f"Running deep analysis on {len(segment_atoms)} atoms")
            narrative_segment = deep_analyzer.analyze_segment(segment_meta, atom_objects)

            # Annotate atoms with topics, entities, emotions
            logger.info(f"Annotating {len(atom_objects)} atoms with semantic tags")
            segment_annotations = atom_annotator.annotate_atoms_batch(
                atom_objects,
                segment_id=segment_id,
                narrative_id=None
            )

            # Merge annotations into accumulator
            self._merge_annotations(all_annotations, segment_annotations)

            # Extract entities using the real entities from deep analysis
            logger.info(f"Extracting entities from analyzed segment")
            # Filter atoms to only include current segment atoms to avoid duplicate counting
            segment_atoms_filtered = [atom for atom in atoms_list if atom.get('atom_id') in target_segment.atom_ids]
            logger.info(f"Processing {len(segment_atoms_filtered)} atoms from current segment (out of {len(atoms_list)} total)")
            segment_entities = entity_extractor.extract([narrative_segment], segment_atoms_filtered)

            # Merge entities into accumulator
            entity_count = self._merge_entities(all_entities, segment_entities, segment_id)

            # Build topics
            logger.info("Building topic network")
            segment_topics = topic_builder.build([narrative_segment])
            self._merge_topics(all_topics, segment_topics, segment_id)

            # Build knowledge graph
            logger.info("Building knowledge graph")
            segment_graph = graph_builder.build([narrative_segment], segment_entities, segment_topics)
            self._merge_graph(all_graph, segment_graph, segment_id)

            # Save updated results
            self._save_results(all_entities, all_topics, all_graph, all_annotations)

            # Update segment status
            self.segment_manager.update_segment_status(
                segment_id, "analyzed", analysis_complete=True, entity_count=entity_count
            )

            logger.info(f"Completed segment {segment_id}, extracted {entity_count} entities")

        except Exception as e:
            logger.error(f"Error processing segment {segment_id}: {e}", exc_info=True)
            self.segment_manager.update_segment_status(
                segment_id, "failed", error_message=str(e)
            )

        finally:
            self.is_running = False
            self.current_segment_id = None

    def _load_or_init_entities(self) -> Dict:
        """Load existing entities or initialize"""
        if self.entities_file.exists():
            with open(self.entities_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {
            'persons': [],
            'countries': [],
            'organizations': [],
            'time_points': [],
            'events': [],
            'concepts': [],
            'statistics': {'total_entities': 0, 'by_type': {}}
        }

    def _load_or_init_topics(self) -> Dict:
        """Load existing topics or initialize"""
        if self.topics_file.exists():
            with open(self.topics_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {
            'primary_topics': [],
            'secondary_topics': [],
            'tags': []
        }

    def _load_or_init_graph(self) -> Dict:
        """Load existing graph or initialize"""
        if self.graph_file.exists():
            with open(self.graph_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {
            'nodes': [],
            'edges': []
        }

    def _load_or_init_annotations(self) -> List[Dict]:
        """Load existing annotations or initialize"""
        if self.annotations_file.exists():
            with open(self.annotations_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        return []

    def _merge_entities(self, all_entities: Dict, new_entities: Dict, segment_id: str) -> int:
        """Merge new entities into accumulator"""
        entity_count = 0

        for entity_type in ['persons', 'countries', 'organizations', 'time_points', 'events', 'concepts']:
            existing = {e['name']: e for e in all_entities.get(entity_type, [])}

            for entity in new_entities.get(entity_type, []):
                name = entity['name']
                entity_count += 1

                if name in existing:
                    # Merge atoms and segments, but don't accumulate mentions
                    # Instead, merge atoms and recalculate mentions based on unique atoms
                    existing_atoms = set(existing[name].get('atoms', []))
                    new_atoms = set(entity.get('atoms', []))
                    combined_atoms = existing_atoms | new_atoms

                    existing[name]['atoms'] = list(combined_atoms)
                    existing[name]['segments'] = list(set(existing[name].get('segments', []) + [segment_id]))

                    # Recalculate mentions based on the number of atoms (simple approximation)
                    # This is a temporary fix - ideally we should recalculate from all atoms
                    existing[name]['mentions'] = len(combined_atoms)
                else:
                    # Add new
                    entity['segments'] = [segment_id]
                    existing[name] = entity

            all_entities[entity_type] = list(existing.values())

        # Update statistics
        all_entities['statistics'] = {
            'total_entities': sum(len(all_entities[t]) for t in ['persons', 'countries', 'organizations', 'time_points', 'events', 'concepts']),
            'by_type': {t: len(all_entities[t]) for t in ['persons', 'countries', 'organizations', 'time_points', 'events', 'concepts']}
        }

        return entity_count

    def _merge_topics(self, all_topics: Dict, new_topics: Dict, segment_id: str):
        """Merge new topics into accumulator"""
        # Simple append for now (can be improved with deduplication)
        for topic in new_topics.get('primary_topics', []):
            topic['segment_id'] = segment_id
            all_topics.setdefault('primary_topics', []).append(topic)

        for topic in new_topics.get('secondary_topics', []):
            topic['segment_id'] = segment_id
            all_topics.setdefault('secondary_topics', []).append(topic)

        for tag in new_topics.get('tags', []):
            tag['segment_id'] = segment_id
            all_topics.setdefault('tags', []).append(tag)

    def _merge_graph(self, all_graph: Dict, new_graph: Dict, segment_id: str):
        """Merge new graph nodes/edges into accumulator"""
        existing_nodes = {n['id']: n for n in all_graph.get('nodes', [])}
        existing_edges = {(e['source'], e['target']): e for e in all_graph.get('edges', [])}

        # Merge nodes
        for node in new_graph.get('nodes', []):
            node_id = node['id']
            if node_id in existing_nodes:
                # Update mentions
                existing_nodes[node_id]['mentions'] = existing_nodes[node_id].get('mentions', 1) + node.get('mentions', 1)
            else:
                node['segment_id'] = segment_id
                existing_nodes[node_id] = node

        # Merge edges
        for edge in new_graph.get('edges', []):
            edge_key = (edge['source'], edge['target'])
            if edge_key in existing_edges:
                existing_edges[edge_key]['weight'] = existing_edges[edge_key].get('weight', 1) + edge.get('weight', 1)
            else:
                edge['segment_id'] = segment_id
                existing_edges[edge_key] = edge

        all_graph['nodes'] = list(existing_nodes.values())
        all_graph['edges'] = list(existing_edges.values())

    def _merge_annotations(self, all_annotations: List[Dict], new_annotations: List):
        """Merge new annotations into accumulator"""
        # Create a dict keyed by atom_id for efficient lookup
        annotations_dict = {ann['atom_id']: ann for ann in all_annotations}

        # Add or update annotations
        for annotation in new_annotations:
            if isinstance(annotation, AtomAnnotation):
                ann_dict = annotation.model_dump()
            else:
                ann_dict = annotation

            atom_id = ann_dict['atom_id']
            annotations_dict[atom_id] = ann_dict

        # Convert back to list
        all_annotations.clear()
        all_annotations.extend(annotations_dict.values())

    def _save_results(self, entities: Dict, topics: Dict, graph: Dict, annotations: List[Dict] = None):
        """Save current accumulated results"""
        def clean_for_json(obj):
            """Remove non-serializable objects recursively"""
            if isinstance(obj, dict):
                return {k: clean_for_json(v) for k, v in obj.items()
                       if not k.startswith('_') and not hasattr(v, '__call__')}
            elif isinstance(obj, list):
                return [clean_for_json(item) for item in obj
                       if item is not None and not hasattr(item, 'atom_id')]  # Skip Atom objects
            elif hasattr(obj, '__dict__') and hasattr(obj, 'atom_id'):
                # This is an Atom object, skip it
                return None
            elif hasattr(obj, 'model_dump'):
                # This is a Pydantic model, convert to dict
                return clean_for_json(obj.model_dump())
            elif isinstance(obj, (str, int, float, bool)) or obj is None:
                return obj
            else:
                # Try to convert to string as fallback
                return str(obj)

        try:
            clean_entities = clean_for_json(entities)
            clean_topics = clean_for_json(topics)
            clean_graph = clean_for_json(graph)

            with open(self.entities_file, 'w', encoding='utf-8') as f:
                json.dump(clean_entities, f, ensure_ascii=False, indent=2)

            with open(self.topics_file, 'w', encoding='utf-8') as f:
                json.dump(clean_topics, f, ensure_ascii=False, indent=2)

            with open(self.graph_file, 'w', encoding='utf-8') as f:
                json.dump(clean_graph, f, ensure_ascii=False, indent=2)

            if annotations is not None:
                clean_annotations = clean_for_json(annotations)
                with open(self.annotations_file, 'w', encoding='utf-8') as f:
                    json.dump(clean_annotations, f, ensure_ascii=False, indent=2)

            logger.info("Saved incremental results")
        except Exception as e:
            logger.error(f"Error saving results: {e}")
            # Don't fail the whole analysis just because of save errors
            pass

