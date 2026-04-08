"""
cad.xref_resolver
==================
Detects whether a DWG/DXF file is an xref "sheet wrapper" and extracts the
names of externally referenced source files from xref-style layer names.

When source DWGs are found alongside the input file (or in the unprocessed
folder), they are returned so the caller can auto-queue them for processing.

No PDF imports. No fitz.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, NamedTuple, Set

from cad.layer_analyzer import LayerProfile


# ── Public result type ────────────────────────────────────────────────────────

class XrefAnalysis(NamedTuple):
    is_xref_sheet: bool
    source_files_found: List[Path]
    source_files_missing: List[str]
    xref_base_names: List[str]          # deduplicated source stems extracted
    total_entities: int
    empty_layer_pct: float              # 0.0–1.0


# ── Thresholds for xref sheet detection ───────────────────────────────────────

_MAX_ENTITY_COUNT   = 200     # sheets typically have < 200 own entities
_MIN_LAYER_COUNT    = 20      # sheets define many layers via xrefs
_MIN_EMPTY_PCT      = 0.50    # > 50 % of layers have 0 entities

_XREF_SEP = "$0$"             # AutoCAD xref layer name separator
_GUID_RE  = re.compile(r'^\{[0-9A-Fa-f-]{36}\}$')


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_xrefs(
    profiles: Dict[str, LayerProfile],
    input_path: Path,
    search_dirs: List[Path] | None = None,
) -> XrefAnalysis:
    """
    Determine whether the analyzed file is an xref sheet and locate source
    DWGs/DXFs on disk.

    Parameters
    ----------
    profiles    : layer profiles returned by any LayerAnalyzer
    input_path  : path to the original DWG/DXF (used for sibling search)
    search_dirs : additional directories to search for source files
    """
    total_entities = sum(p.entity_count for p in profiles.values())
    layer_count    = len(profiles)
    empty_count    = sum(1 for p in profiles.values() if p.entity_count == 0)
    empty_pct      = empty_count / max(layer_count, 1)

    xref_base_names = _extract_xref_bases(profiles)

    has_guid_blocks = _has_guid_insert_blocks(profiles)

    is_xref = (
        total_entities < _MAX_ENTITY_COUNT
        and layer_count >= _MIN_LAYER_COUNT
        and empty_pct >= _MIN_EMPTY_PCT
        and (len(xref_base_names) > 0 or has_guid_blocks)
    )

    found: List[Path] = []
    missing: List[str] = []

    if is_xref and xref_base_names:
        dirs = _build_search_dirs(input_path, search_dirs)
        found, missing = _locate_source_files(xref_base_names, dirs)

    return XrefAnalysis(
        is_xref_sheet=is_xref,
        source_files_found=found,
        source_files_missing=missing,
        xref_base_names=xref_base_names,
        total_entities=total_entities,
        empty_layer_pct=round(empty_pct, 3),
    )


# ── Internals ─────────────────────────────────────────────────────────────────

def _extract_xref_bases(profiles: Dict[str, LayerProfile]) -> List[str]:
    """
    Collect unique source file base names from xref layer name patterns.

    AutoCAD xref layers follow the pattern:
        SourceFileName$0$OriginalLayerName
    We split on the first ``$0$`` and take the left-hand side.
    """
    bases: Set[str] = set()
    for name in profiles:
        if _XREF_SEP in name:
            stem = name.split(_XREF_SEP, 1)[0]
            stem = stem.strip()
            if stem and _is_plausible_filename(stem):
                bases.add(stem)
    return sorted(bases)


def _is_plausible_filename(stem: str) -> bool:
    """Reject garbled/mojibake names that aren't real filenames."""
    if len(stem) < 2:
        return False
    printable = sum(1 for c in stem if 32 <= ord(c) < 127)
    return printable / max(len(stem), 1) > 0.7


def _has_guid_insert_blocks(profiles: Dict[str, LayerProfile]) -> bool:
    """Check if any layer references INSERT blocks with GUID names."""
    for p in profiles.values():
        for bn in p.block_names:
            if _GUID_RE.match(bn):
                return True
    return False


def _build_search_dirs(input_path: Path, extra: List[Path] | None) -> List[Path]:
    """Build a list of directories to search for source files."""
    dirs: List[Path] = []
    if input_path.parent.is_dir():
        dirs.append(input_path.parent)
    if extra:
        for d in extra:
            if d.is_dir() and d not in dirs:
                dirs.append(d)
    return dirs


def _locate_source_files(
    base_names: List[str],
    search_dirs: List[Path],
) -> tuple[List[Path], List[str]]:
    """
    Search for DWG/DXF files matching the xref base names.

    Returns (found_paths, missing_names).
    """
    found: List[Path] = []
    missing: List[str] = []
    seen: Set[str] = set()

    for stem in base_names:
        located = False
        for d in search_dirs:
            for ext in (".dwg", ".dxf", ".DWG", ".DXF"):
                candidate = d / f"{stem}{ext}"
                if candidate.is_file() and str(candidate) not in seen:
                    found.append(candidate)
                    seen.add(str(candidate))
                    located = True
                    break
            if located:
                break
        if not located:
            missing.append(stem)

    return found, missing
