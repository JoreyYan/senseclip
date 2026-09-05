# -*- coding: utf-8 -*-
"""Analysis Service - Background task for full video analysis"""

import json
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
import threading
import logging

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from analyzers.entity_extractor import EntityExtractor
from analyzers.topic_network_builder import TopicNetworkBuilder
from analyzers.knowledge_graph_builder import KnowledgeGraphBuilder

logger = logging.getLogger(__name__)

@dataclass
class AnalysisProgress:
    """Progress tracking for analysis"""
    status: str  # "idle", "running", "completed", "failed"
    current_step: str
    progress_percent: int
    total_atoms: int
    processed_atoms: int
    total_chunks: int
    processed_chunks: int
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    error_message: Optional[str] = None
    results_summary: Optional[Dict] = None

class AnalysisService:
    """Service to handle background analysis tasks"""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.progress = AnalysisProgress(
            status="idle",
            current_step="",
            progress_percent=0,
            total_atoms=0,
            processed_atoms=0,
            total_chunks=0,
            processed_chunks=0
        )
        self.analysis_thread: Optional[threading.Thread] = None
        self.stop_flag = False

    def get_progress(self) -> Dict:
        """Get current progress"""
        return asdict(self.progress)

    def start_full_analysis(self, project_id: str, chunk_size: int = 50):
        """Start full video analysis in background"""
        if self.progress.status == "running":
            raise ValueError("Analysis already running")

        # Start analysis in background thread
        self.stop_flag = False
        self.analysis_thread = threading.Thread(
            target=self._run_full_analysis,
            args=(project_id, chunk_size),
            daemon=True
        )
        self.analysis_thread.start()

    def cancel_analysis(self):
        """Cancel running analysis"""
        if self.progress.status == "running":
            self.stop_flag = True
            self.progress.status = "cancelled"
            self.progress.current_step = "Analysis cancelled by user"

    def _run_full_analysis(self, project_id: str, chunk_size: int):
        """Run the full analysis (runs in background thread)"""
        try:
            # Initialize progress
            self.progress.status = "running"
            self.progress.start_time = datetime.now().isoformat()
            self.progress.current_step = "Loading atoms"
            self.progress.progress_percent = 0

            # Load all atoms
            atoms_file = self.data_dir.parent / "output" / "atoms_full.jsonl"
            if not atoms_file.exists():
                raise FileNotFoundError(f"atoms_full.jsonl not found at {atoms_file}")

            atoms = []
            with open(atoms_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if self.stop_flag:
                        return
                    atoms.append(json.loads(line))

            self.progress.total_atoms = len(atoms)
            self.progress.total_chunks = (len(atoms) + chunk_size - 1) // chunk_size
            logger.info(f"Loaded {len(atoms)} atoms, will process in {self.progress.total_chunks} chunks")

            # Create mock segments for each chunk
            self.progress.current_step = "Creating analysis segments"
            self.progress.progress_percent = 5

            segments = []
            for i in range(0, len(atoms), chunk_size):
                if self.stop_flag:
                    return
                chunk = atoms[i:i+chunk_size]
                seg_id = f"CHUNK_{i//chunk_size + 1:03d}"
                segments.append(self._create_mock_segment(chunk, seg_id))

            logger.info(f"Created {len(segments)} segments")

            # Extract entities
            self.progress.current_step = "Extracting entities"
            self.progress.progress_percent = 10

            extractor = EntityExtractor()
            all_entities = {
                'persons': {},
                'countries': {},
                'organizations': {},
                'time_points': {},
                'events': {},
                'concepts': {}
            }

            for idx, seg in enumerate(segments):
                if self.stop_flag:
                    return

                self.progress.processed_chunks = idx + 1
                self.progress.current_step = f"Processing chunk {idx + 1}/{len(segments)}"
                self.progress.progress_percent = 10 + int((idx / len(segments)) * 50)

                logger.info(f"Processing segment {seg.segment_id}")
                result = extractor.extract([seg])

                # Merge results
                for entity_type in all_entities.keys():
                    for entity in result.get(entity_type, []):
                        name = entity['name']
                        if name not in all_entities[entity_type]:
                            all_entities[entity_type][name] = entity
                        else:
                            # Merge atoms and segments
                            existing = all_entities[entity_type][name]
                            existing['mentions'] = existing.get('mentions', 1) + entity.get('mentions', 1)
                            existing['atoms'] = list(set(existing.get('atoms', []) + entity.get('atoms', [])))
                            existing['segments'] = list(set(existing.get('segments', []) + entity.get('segments', [])))

            if self.stop_flag:
                return

            # Convert back to lists
            self.progress.current_step = "Finalizing entities"
            self.progress.progress_percent = 65

            final_entities = {}
            for entity_type, entities_dict in all_entities.items():
                final_entities[entity_type] = list(entities_dict.values())

            # Calculate statistics
            final_entities['statistics'] = {
                'total_entities': sum(len(entities) for entities in final_entities.values() if isinstance(entities, list)),
                'by_type': {k: len(v) for k, v in final_entities.items() if isinstance(v, list)}
            }

            # Save entities
            self.progress.current_step = "Saving entities"
            self.progress.progress_percent = 70

            output_dir = self.data_dir
            output_dir.mkdir(parents=True, exist_ok=True)

            entities_file = output_dir / "entities.json"
            with open(entities_file, 'w', encoding='utf-8') as f:
                json.dump(final_entities, f, ensure_ascii=False, indent=2)

            logger.info(f"Saved {final_entities['statistics']['total_entities']} entities")

            # Build topic network
            self.progress.current_step = "Building topic network"
            self.progress.progress_percent = 75

            topic_builder = TopicNetworkBuilder()
            topics_result = topic_builder.build(segments)

            topics_file = output_dir / "topics.json"
            with open(topics_file, 'w', encoding='utf-8') as f:
                json.dump(topics_result, f, ensure_ascii=False, indent=2)

            logger.info("Saved topic network")

            # Build knowledge graph
            self.progress.current_step = "Building knowledge graph"
            self.progress.progress_percent = 85

            graph_builder = KnowledgeGraphBuilder()
            graph_result = graph_builder.build(segments, final_entities)

            graph_file = output_dir / "knowledge_graph.json"
            with open(graph_file, 'w', encoding='utf-8') as f:
                json.dump(graph_result, f, ensure_ascii=False, indent=2)

            logger.info(f"Saved knowledge graph with {len(graph_result.get('nodes', []))} nodes")

            # Complete
            self.progress.status = "completed"
            self.progress.current_step = "Analysis complete"
            self.progress.progress_percent = 100
            self.progress.end_time = datetime.now().isoformat()
            self.progress.processed_atoms = len(atoms)
            self.progress.results_summary = {
                "total_entities": final_entities['statistics']['total_entities'],
                "entities_by_type": final_entities['statistics']['by_type'],
                "total_atoms": len(atoms),
                "total_segments": len(segments),
                "graph_nodes": len(graph_result.get('nodes', [])),
                "graph_edges": len(graph_result.get('edges', []))
            }

            logger.info("Full analysis completed successfully")

        except Exception as e:
            logger.error(f"Analysis failed: {str(e)}", exc_info=True)
            self.progress.status = "failed"
            self.progress.current_step = "Analysis failed"
            self.progress.error_message = str(e)
            self.progress.end_time = datetime.now().isoformat()

    def _create_mock_segment(self, atoms: List[Dict], segment_id: str):
        """Create mock segment object for analysis"""
        class MockAtom:
            def __init__(self, data):
                self.atom_id = data['atom_id']
                self.merged_text = data['merged_text']
                self.start_ms = data.get('start_ms', 0)
                self.end_ms = data.get('end_ms', 0)

        class MockEntities:
            def __init__(self):
                self.persons = []
                self.countries = []
                self.organizations = []
                self.time_points = []
                self.events = []
                self.concepts = []

        class MockNarrative:
            def __init__(self):
                self.primary_topic = "金三角历史与缅北双雄时代"
                self.secondary_topics = []
                self.tags = []

        class MockSegment:
            def __init__(self, atoms_data, seg_id):
                self.segment_id = seg_id
                self.atoms = [MockAtom(a) for a in atoms_data]
                self.entities = MockEntities()
                self.narrative_arc = MockNarrative()
                self.full_text = " ".join([a['merged_text'] for a in atoms_data])

        return MockSegment(atoms, segment_id)
