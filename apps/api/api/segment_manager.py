# -*- coding: utf-8 -*-
"""Segment Manager - Manages video time segments for incremental analysis"""

import json
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
import logging

logger = logging.getLogger(__name__)

@dataclass
class TimeSegment:
    """Represents a time-based video segment"""
    segment_id: str
    start_ms: int
    end_ms: int
    duration_ms: int
    start_time_str: str  # "00:00:00"
    end_time_str: str    # "00:20:00"
    atom_ids: List[str]
    status: str  # "pending", "atomized", "analyzed", "failed"
    atomization_complete: bool
    analysis_complete: bool
    entity_count: int = 0
    error_message: Optional[str] = None

class SegmentManager:
    """Manages video segmentation and tracking"""

    def __init__(self, data_dir: Path, segment_duration_minutes: int = 20):
        self.data_dir = data_dir
        self.segment_duration_ms = segment_duration_minutes * 60 * 1000
        self.segments_file = data_dir / "segments_state.json"
        self.atoms_file = data_dir / "atoms.jsonl"

    def ms_to_time_str(self, ms: int) -> str:
        """Convert milliseconds to HH:MM:SS format"""
        seconds = ms // 1000
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def load_atoms(self) -> List[Dict]:
        """Load all atoms from file"""
        if not self.atoms_file.exists():
            return []

        atoms = []
        with open(self.atoms_file, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    atoms.append(json.loads(line))
        return atoms

    def get_video_duration(self, atoms: List[Dict]) -> int:
        """Get total video duration from atoms"""
        if not atoms:
            return 0
        return max(atom.get('end_ms', 0) for atom in atoms)

    def create_time_segments(self, atoms: List[Dict]) -> List[TimeSegment]:
        """Create time-based segments from atoms"""
        if not atoms:
            return []

        total_duration = self.get_video_duration(atoms)
        segments = []

        # Create segments every 20 minutes
        current_start = 0
        segment_num = 1

        while current_start < total_duration:
            segment_end = min(current_start + self.segment_duration_ms, total_duration)

            # Find atoms in this time range
            segment_atoms = [
                atom for atom in atoms
                if atom.get('start_ms', 0) >= current_start and atom.get('start_ms', 0) < segment_end
            ]

            # Store atom indices instead of atom_ids to avoid cyclical ID conflicts
            segment_atom_indices = []
            for i, atom in enumerate(atoms):
                if atom.get('start_ms', 0) >= current_start and atom.get('start_ms', 0) < segment_end:
                    segment_atom_indices.append(i)

            segment = TimeSegment(
                segment_id=f"TSEG_{segment_num:03d}",
                start_ms=current_start,
                end_ms=segment_end,
                duration_ms=segment_end - current_start,
                start_time_str=self.ms_to_time_str(current_start),
                end_time_str=self.ms_to_time_str(segment_end),
                atom_ids=segment_atom_indices,  # Store actual atom indices, not cyclical IDs
                status="atomized" if segment_atoms else "pending",
                atomization_complete=bool(segment_atoms),
                analysis_complete=False
            )

            segments.append(segment)
            current_start = segment_end
            segment_num += 1

        return segments

    def load_segments_state(self) -> List[TimeSegment]:
        """Load segments state from file, or create new if not exists"""
        atoms = self.load_atoms()

        if self.segments_file.exists():
            with open(self.segments_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                existing_segments = [TimeSegment(**seg) for seg in data]

            # Check if atoms data has changed - validate if existing segment atom_ids still exist
            if atoms and existing_segments:
                # segment.atom_ids contains array indices, not atom_id strings
                # So we need to check if the indices are still valid for the current atoms array
                max_atom_index = len(atoms) - 1

                # Check if any existing segment references non-existent atom indices
                segments_valid = True
                for segment in existing_segments:
                    for atom_index in segment.atom_ids:
                        if not isinstance(atom_index, int) or atom_index < 0 or atom_index > max_atom_index:
                            logger.info(f"Segment {segment.segment_id} references invalid atom index {atom_index}, regenerating segments")
                            segments_valid = False
                            break
                    if not segments_valid:
                        break

                # If segments are still valid, return them
                if segments_valid:
                    logger.info(f"Loaded existing segments state: {len(existing_segments)} segments")
                    return existing_segments
                else:
                    logger.info("Atoms data changed, regenerating segments...")
            else:
                # Return existing segments if no atoms or no existing segments
                return existing_segments

        # Create new segments from atoms
        if atoms:
            logger.info(f"Creating new segments from {len(atoms)} atoms")
            segments = self.create_time_segments(atoms)
            self.save_segments_state(segments)
            return segments

        return []

    def save_segments_state(self, segments: List[TimeSegment]):
        """Save segments state to file"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with open(self.segments_file, 'w', encoding='utf-8') as f:
            json.dump([asdict(seg) for seg in segments], f, indent=2, ensure_ascii=False)

    def get_segment_by_id(self, segment_id: str, segments: List[TimeSegment]) -> Optional[TimeSegment]:
        """Get segment by ID"""
        for seg in segments:
            if seg.segment_id == segment_id:
                return seg
        return None

    def update_segment_status(self, segment_id: str, status: str, **kwargs):
        """Update segment status and save"""
        segments = self.load_segments_state()
        segment = self.get_segment_by_id(segment_id, segments)

        if segment:
            segment.status = status

            # Clear error message if we're moving to a successful state
            if status in ["analyzed", "atomized"]:
                segment.error_message = None

            for key, value in kwargs.items():
                if hasattr(segment, key):
                    setattr(segment, key, value)
            self.save_segments_state(segments)
            logger.info(f"Updated segment {segment_id} status to {status}")

    def get_next_pending_segment(self) -> Optional[TimeSegment]:
        """Get next segment that needs analysis"""
        segments = self.load_segments_state()

        # First, find atomized but not analyzed segments
        for seg in segments:
            if seg.atomization_complete and not seg.analysis_complete and seg.status != "failed":
                return seg

        # Then, find pending segments that need atomization
        for seg in segments:
            if not seg.atomization_complete and seg.status == "pending":
                return seg

        return None

    def get_analysis_progress(self) -> Dict:
        """Get overall analysis progress"""
        segments = self.load_segments_state()

        if not segments:
            return {
                "total_segments": 0,
                "analyzed_segments": 0,
                "pending_segments": 0,
                "failed_segments": 0,
                "total_entities": 0,
                "progress_percent": 0
            }

        analyzed = sum(1 for s in segments if s.analysis_complete)
        pending = sum(1 for s in segments if not s.analysis_complete and s.status != "failed")
        failed = sum(1 for s in segments if s.status == "failed")

        # Read actual deduplicated entity count from entities.json
        total_entities = 0
        entities_file = self.data_dir / "entities.json"
        if entities_file.exists():
            try:
                with open(entities_file, 'r', encoding='utf-8') as f:
                    entities_data = json.load(f)
                    total_entities = entities_data.get('statistics', {}).get('total_entities', 0)
            except Exception as e:
                logger.warning(f"Could not read entities count: {e}")
                # Fallback to segment sum (will be inaccurate due to duplicates)
                total_entities = sum(s.entity_count for s in segments)

        return {
            "total_segments": len(segments),
            "analyzed_segments": analyzed,
            "pending_segments": pending,
            "failed_segments": failed,
            "total_entities": total_entities,
            "progress_percent": int((analyzed / len(segments)) * 100) if segments else 0,
            "segments": [asdict(s) for s in segments]
        }

    def reset_analysis(self):
        """Reset all analysis status (keep atomization)"""
        segments = self.load_segments_state()
        for seg in segments:
            if seg.atomization_complete:
                seg.analysis_complete = False
                seg.status = "atomized"
                seg.entity_count = 0
        self.save_segments_state(segments)

        # Clear analysis result files
        analysis_files = [
            "entities.json",
            "topics.json",
            "knowledge_graph.json",
            "segments.pkl"
        ]

        for filename in analysis_files:
            file_path = self.data_dir / filename
            if file_path.exists():
                try:
                    file_path.unlink()
                    logger.info(f"Removed analysis file: {filename}")
                except Exception as e:
                    logger.warning(f"Failed to remove {filename}: {e}")

        logger.info("Reset all segment analysis status and cleared analysis files")

    def recreate_segments(self, segment_duration_minutes: int):
        """Recreate segments with new duration"""
        self.segment_duration_ms = segment_duration_minutes * 60 * 1000
        atoms = self.load_atoms()
        if atoms:
            segments = self.create_time_segments(atoms)
            self.save_segments_state(segments)
            logger.info(f"Recreated segments with {segment_duration_minutes} minute duration")
            return segments
        return []

    def reset_segment_analysis(self, segment_id: str):
        """Reset analysis status for a specific segment"""
        segments = self.load_segments_state()
        segment = self.get_segment_by_id(segment_id, segments)
        if segment and segment.atomization_complete:
            segment.analysis_complete = False
            segment.status = "atomized"
            segment.entity_count = 0
            segment.error_message = None
            self.save_segments_state(segments)
            logger.info(f"Reset analysis status for segment {segment_id}")
