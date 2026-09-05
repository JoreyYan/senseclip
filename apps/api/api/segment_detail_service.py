# -*- coding: utf-8 -*-
"""Segment Detail Service - Builds three-level analysis for segments"""

import json
import sys
from pathlib import Path
from typing import List, Dict, Optional, Any
import logging

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from models.segment_detail import (
    AtomDetailView,
    SegmentLevelAnalysis,
    NarrativeSegmentAnalysis,
    SegmentDetailAnalysis,
    SegmentDetailService as ModelService
)
from models.entity_index import AtomAnnotation

logger = logging.getLogger(__name__)


class SegmentDetailService:
    """Service to build and retrieve segment detail analysis"""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.model_service = ModelService()

    def load_atoms(self) -> List[Dict]:
        """Load all atoms from atoms.jsonl"""
        atoms_file = self.data_dir / "atoms.jsonl"
        if not atoms_file.exists():
            logger.warning(f"Atoms file not found: {atoms_file}")
            return []

        atoms = []
        with open(atoms_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    atoms.append(json.loads(line))
        return atoms

    def load_annotations(self) -> Dict[str, AtomAnnotation]:
        """Load atom annotations from annotations.json"""
        annotations_file = self.data_dir / "atom_annotations.json"
        if not annotations_file.exists():
            logger.warning(f"Annotations file not found: {annotations_file}")
            return {}

        try:
            with open(annotations_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Convert to dict keyed by atom_id
            annotations_dict = {}
            for item in data:
                annotation = AtomAnnotation(**item)
                annotations_dict[annotation.atom_id] = annotation

            logger.info(f"Loaded {len(annotations_dict)} atom annotations")
            return annotations_dict

        except Exception as e:
            logger.error(f"Failed to load annotations: {e}")
            return {}

    def load_segments(self) -> List[Dict]:
        """Load segment state from segments_state.json"""
        segments_file = self.data_dir / "segments_state.json"
        if not segments_file.exists():
            logger.warning(f"Segments file not found: {segments_file}")
            return []

        with open(segments_file, 'r', encoding='utf-8') as f:
            return json.load(f)

    def load_narrative_segments(self) -> List[Dict]:
        """Load narrative segments from narrative_segments.json"""
        narrative_file = self.data_dir / "narrative_segments.json"
        if not narrative_file.exists():
            return []

        with open(narrative_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Handle both list and dict formats
            if isinstance(data, list):
                return data
            return data.get('segments', [])

    def find_narrative_for_segment(
        self,
        segment_id: str,
        narrative_segments: List[Dict]
    ) -> Optional[Dict]:
        """Find narrative segment that contains this time segment"""
        # This is a simplified mapping - you may need to adjust based on your actual data structure
        # In a real implementation, you'd check if the time segment overlaps with narrative segment
        return None

    def get_segment_detail(self, segment_id: str) -> Optional[Dict]:
        """
        Build complete segment detail analysis for a given segment

        Returns JSON-serializable dict with:
        - atom_level: List of atom details with topics, entities, emotions, etc.
        - segment_level: Aggregate statistics for the segment
        - narrative_level: Narrative context if applicable
        """
        try:
            # Load all required data
            all_atoms = self.load_atoms()
            annotations = self.load_annotations()
            segments = self.load_segments()
            narrative_segments = self.load_narrative_segments()

            # Find the target segment
            target_segment = None
            for seg in segments:
                if seg['segment_id'] == segment_id:
                    target_segment = seg
                    break

            if not target_segment:
                logger.error(f"Segment not found: {segment_id}")
                return None

            # Get atoms for this segment
            atom_ids = target_segment.get('atom_ids', [])
            segment_atoms = []

            # FIXED VERSION 2024-10-04: Handle integer indices, compound IDs, and simple IDs
            for atom_ref in atom_ids:
                if isinstance(atom_ref, int):
                    # Handle integer indices directly (new format)
                    if 0 <= atom_ref < len(all_atoms):
                        segment_atoms.append(all_atoms[atom_ref])
                    else:
                        logger.warning(f"Invalid atom index: {atom_ref}")
                elif isinstance(atom_ref, str) and '_' in atom_ref:
                    # Handle compound atom IDs (e.g., "A001_0" -> get atom at index 0)
                    try:
                        atom_index = int(atom_ref.split('_')[1])
                        if 0 <= atom_index < len(all_atoms):
                            segment_atoms.append(all_atoms[atom_index])
                    except (ValueError, IndexError):
                        logger.warning(f"Invalid compound atom ID: {atom_ref}")
                elif isinstance(atom_ref, str):
                    # Handle simple atom IDs for backward compatibility
                    atom = next((a for a in all_atoms if a['atom_id'] == atom_ref), None)
                    if atom:
                        segment_atoms.append(atom)
                    else:
                        logger.warning(f"Atom not found for ID: {atom_ref}")
                else:
                    logger.warning(f"Unknown atom reference format: {atom_ref}")

            logger.info(f"Building detail for segment {segment_id} with {len(segment_atoms)} atoms")

            # Build atom-level details
            atom_details = []
            for atom in segment_atoms:
                atom_id = atom['atom_id']
                annotation = annotations.get(atom_id)

                if annotation:
                    # Use the model service to build atom detail view
                    atom_detail = AtomDetailView(
                        atom_id=atom_id,
                        text_snippet=atom.get('merged_text', '')[:200] + "..." if len(atom.get('merged_text', '')) > 200 else atom.get('merged_text', ''),
                        start_ms=atom.get('start_ms', 0),
                        end_ms=atom.get('end_ms', 0),
                        duration_ms=atom.get('end_ms', 0) - atom.get('start_ms', 0),
                        topics=annotation.topics,
                        entities=annotation.entities,
                        emotion=annotation.emotion,
                        importance_score=annotation.importance_score,
                        quality_score=getattr(annotation, 'quality_score', 0.5),
                        has_entity=annotation.has_entity,
                        has_topic=annotation.has_topic,
                        embedding_status=annotation.embedding_status,
                        parent_segment_id=segment_id,
                        parent_narrative_id=annotation.parent_narrative_id
                    )
                    atom_details.append(atom_detail)
                else:
                    # Create basic atom detail without annotation
                    atom_detail = AtomDetailView(
                        atom_id=atom_id,
                        text_snippet=atom.get('merged_text', '')[:200] + "..." if len(atom.get('merged_text', '')) > 200 else atom.get('merged_text', ''),
                        start_ms=atom.get('start_ms', 0),
                        end_ms=atom.get('end_ms', 0),
                        duration_ms=atom.get('end_ms', 0) - atom.get('start_ms', 0),
                        embedding_status="not_annotated",
                        parent_segment_id=segment_id
                    )
                    atom_details.append(atom_detail)

            # Build segment-level analysis
            all_entities = []
            all_topics = []
            importance_scores = []

            for atom_detail in atom_details:
                all_entities.extend(atom_detail.entities)
                all_topics.extend(atom_detail.topics)
                importance_scores.append(atom_detail.importance_score)

            # Calculate entity type distribution
            entity_distribution = {}
            for entity in all_entities:
                entity_type = entity.get('type', 'unknown')
                entity_distribution[entity_type] = entity_distribution.get(entity_type, 0) + 1

            # Calculate topic distribution
            topic_distribution = {}
            for topic in all_topics:
                topic_distribution[topic] = topic_distribution.get(topic, 0) + 1

            # Calculate emotion summary
            emotion_summary = self._calculate_emotion_summary([ad for ad in atom_details if ad.emotion])

            segment_analysis = SegmentLevelAnalysis(
                segment_id=segment_id,
                start_ms=target_segment['start_ms'],
                end_ms=target_segment['end_ms'],
                duration_ms=target_segment['duration_ms'],
                start_time_str=target_segment['start_time_str'],
                end_time_str=target_segment['end_time_str'],
                total_atoms=len(atom_details),
                analyzed_atoms=len([a for a in atom_details if a.has_entity or a.has_topic]),
                total_entities=len(set(e['name'] for e in all_entities)) if all_entities else 0,
                total_topics=len(set(all_topics)) if all_topics else 0,
                avg_importance=sum(importance_scores) / len(importance_scores) if importance_scores else 0.5,
                entity_distribution=entity_distribution,
                topic_distribution=topic_distribution,
                emotion_summary=emotion_summary
            )

            # Find narrative segment (if exists)
            narrative_analysis = None
            narrative_segment = self.find_narrative_for_segment(segment_id, narrative_segments)
            if narrative_segment:
                narrative_analysis = NarrativeSegmentAnalysis(
                    narrative_id=narrative_segment.get('id', 'unknown'),
                    title=narrative_segment.get('title', ''),
                    summary=narrative_segment.get('summary', ''),
                    start_ms=target_segment['start_ms'],
                    end_ms=target_segment['end_ms'],
                    duration_ms=target_segment['duration_ms'],
                    time_segments=[segment_id],
                    narrative_importance=narrative_segment.get('importance', 0.5)
                )

            # Build complete analysis
            complete_analysis = SegmentDetailAnalysis(
                segment_id=segment_id,
                atom_level=[ad.model_dump() for ad in atom_details],
                segment_level=segment_analysis.model_dump(),
                narrative_level=narrative_analysis.model_dump() if narrative_analysis else None,
                analysis_status={
                    "atom_analysis": "completed" if annotations else "pending",
                    "segment_analysis": "completed",
                    "narrative_analysis": "completed" if narrative_analysis else "not_applicable"
                },
                analysis_stats={
                    "total_atoms_analyzed": len(atom_details),
                    "entities_found": segment_analysis.total_entities,
                    "topics_found": segment_analysis.total_topics,
                    "avg_importance": segment_analysis.avg_importance
                }
            )

            return complete_analysis.model_dump()

        except Exception as e:
            logger.error(f"Failed to build segment detail for {segment_id}: {e}", exc_info=True)
            return None

    def _calculate_emotion_summary(self, atom_details_with_emotion: List[AtomDetailView]) -> Optional[Dict[str, Any]]:
        """Calculate overall emotion summary for a segment"""
        if not atom_details_with_emotion:
            return None

        # Aggregate emotion counts
        emotion_counts = {}
        total_confidence = 0

        for atom_detail in atom_details_with_emotion:
            if atom_detail.emotion:
                emotion_type = atom_detail.emotion.get('type', 'neutral')
                confidence = atom_detail.emotion.get('confidence', 0.5)

                if emotion_type not in emotion_counts:
                    emotion_counts[emotion_type] = {'count': 0, 'total_confidence': 0}

                emotion_counts[emotion_type]['count'] += 1
                emotion_counts[emotion_type]['total_confidence'] += confidence
                total_confidence += confidence

        if not emotion_counts:
            return None

        # Find dominant emotion
        dominant_emotion = max(emotion_counts.items(), key=lambda x: x[1]['count'])

        # Calculate distribution
        total_count = sum(e['count'] for e in emotion_counts.values())
        distribution = {
            emotion: counts['count'] / total_count
            for emotion, counts in emotion_counts.items()
        }

        return {
            "dominant_emotion": dominant_emotion[0],
            "confidence": dominant_emotion[1]['total_confidence'] / dominant_emotion[1]['count'],
            "distribution": distribution
        }

    def get_all_segments_summary(self) -> List[Dict]:
        """Get summary of all segments with their analysis status"""
        segments = self.load_segments()
        annotations = self.load_annotations()
        all_atoms = self.load_atoms()

        summary = []
        for seg in segments:
            atom_ids = seg.get('atom_ids', [])
            annotated_count = 0

            # FIXED VERSION 2024-10-04: Handle integer indices for annotation counting
            for atom_ref in atom_ids:
                if isinstance(atom_ref, int):
                    # For integer indices, get actual atom_id from atoms list
                    if 0 <= atom_ref < len(all_atoms):
                        actual_atom_id = all_atoms[atom_ref]['atom_id']
                        if actual_atom_id in annotations:
                            annotated_count += 1
                elif isinstance(atom_ref, str):
                    # For string IDs, check directly in annotations
                    if atom_ref in annotations:
                        annotated_count += 1

            summary.append({
                "segment_id": seg['segment_id'],
                "start_time": seg['start_time_str'],
                "end_time": seg['end_time_str'],
                "total_atoms": len(atom_ids),
                "annotated_atoms": annotated_count,
                "analysis_complete": seg.get('analysis_complete', False),
                "has_detail": annotated_count > 0
            })

        return summary
