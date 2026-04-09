#!/usr/bin/env python3
"""
Floor Plan Preprocessing Script - Line Cleanup
================================================
Cleans unneeded lines from the A-619 Mechanical 2 Floor Plan (Doors & Windows Tags)
so that door/window counting is possible without visual clutter.

Two complementary approaches are used:

1. PDF Layer (OCG) visibility: Turns off unneeded layers so that conforming
   PDF viewers (Adobe Acrobat, browsers) hide them automatically.

2. Content-stream stripping: Physically removes the marked-content sections
   (BDC/EMC blocks) for unwanted layers from the raw PDF content streams,
   so the cleanup is visible even in renderers that ignore OCG state.

Layers / content REMOVED:
  - Grid lines and column grid axes (GRID LINE 2 group)
  - Column grid dimensions and reference numbers
  - Elevation and section tags
  - Grid annotation text
  - Plot limit / setback text
  - Dimension annotations
  - Cloud revision marks
  - Concrete hatching patterns (AD-HAT-CONC)
  - Column tone fill patterns from roof level cross-reference (EC-TONE)
  - Title block content (TBLOCK DM group)
  - GFC (Good For Construction) stamp
  - Room tag outline boxes

Layers / content KEPT:
  - Structural walls (A_A_BLCKWALL Cut)
  - Door geometry (A_A_DOOR)
  - Door tags (A-DOOR-TAG)
  - Window tags (A-WIND-TAG)
  - Slab and room boundaries (A-Slab Limit Line)
  - Floor plan text labels (A_TEXT, text, mTEXT_)
  - Stair geometry, loft elements
  - Glazing / glass elements (14-GLAS)
  - Column and wall outlines (EC-WALL)
  - Vertical fins, stone cladding, expansion joints
  - Track indicators for sliding elements (TRACKS)
"""

import fitz  # PyMuPDF
import os
import re
import sys


# ── Layer removal list ────────────────────────────────────────────────────────
REMOVE_PATTERNS: list[str] = [

    # ── 1. Tables & schedules ─────────────────────────────────────────────────
    "TBLOCK",          # Title block (drawing border, revision table, title text)
    "STAMP",           # GFC / approval stamps
    # NOTE: ROOM-TAG / ROOM-AREA are intentionally NOT listed here — area names
    # (e.g. "GYM area", "MASTER BED") must be kept for BoQ room classification.
    "LTAG",            # Level / FFL / SSL tags
    "X-TAGS",          # External-reference tag groups
    "A_D_LTAG",        # Door-level tags
    "CLOUD",           # Revision clouds
    "DIMENSIONS",      # Standalone dimension layers
    "COTES",           # French: dimensions / cotations
    "A_TEXT",          # General annotation text
    "A-TEXT",
    "A_T_TEXT",        # Room-number text
    "A-T-TEXT",
    "TEXT",            # Bare TEXT / TEXTE / mTEXT_ layers
    "A-TXT",
    "YELLOW",          # Yellow annotation highlights
    "ARROW",           # Leader / direction arrows

    # ── 2. Hatching & grids ───────────────────────────────────────────────────
    "HATCH",           # All hatching patterns
    "HACH",            # Hatching (short form)
    "HACHURES",        # French: hatching
    "-HAT-",           # Mid-name hatch marker (AD-HAT-CONC)
    "_HATCH",          # Hatch suffix
    "AD-HAT",          # Concrete hatch
    "A-VOIDHATCH",     # Void hatch
    "STONE CLADDING",  # Stone cladding pattern
    "CLADDING",
    "GRID LINE",       # Structural grid lines
    "GRID",            # Grid dimensions / grid numbers
    "AXES GRID",
    "_AXS",            # Axis lines
    "_GRID",
    "ELEV. TAG",       # Elevation tags on grid
    "SEC. TAG",        # Section tags on grid
    "PLOT LIMIT",      # Plot limit annotation

    # ── 3. Furniture ──────────────────────────────────────────────────────────
    "FURN",            # Furniture
    "MOBILIER",        # French: furniture (TA - MOBILIER etc.)
    "TA-MOBILIER",
    "TA - MOBILIER",

    # ── 4. Trees / landscape planting ─────────────────────────────────────────
    "L-PL-",           # Planting layers (L-PL-PATT, L-PL-SHRUB, L-PL-TREE)
    "L-PLANT",         # Plants
    "TREE",            # Tree symbols
    "SHRUB",           # Shrub symbols
    "PLANTING",        # Planting areas
    "L-LO-PV",         # Landscape PV pattern
    "L-LO-POOL",       # Landscape pool surround
    "L-WATER",         # Water feature
    "TA - HACH",       # Landscape hatching
    "XR LANDSCAPE",    # Entire landscape XRef group

    # ── 5. Stairs ─────────────────────────────────────────────────────────────
    "STAIR",           # A-Stair Tread, A-Stair Hidden, AR-STAIR
    "HANDRAIL",        # Stair handrails
    "STEPS",           # Exit stair steps (A-F.EXIT STAIR STEPS)
    "A-F.EXIT",        # Fire exit stair layers
    "AR-STAIR",        # External-ref stair geometry
    "A-upper-dot",     # Upper-floor dotted stair projection
    "LOFT",            # Loft / mezzanine stair projections (PLAN_LOFT)

    # ── Remaining grids / axes ────────────────────────────────────────────────
    "AXES",            # Bare AXES layers still kept (grid axis lines)
    "A_AXES",          # Variant with underscore

    # ── Floor tile / finish grid ──────────────────────────────────────────────
    "TA - FLOOR",      # Floor tile finish grid pattern
    "TA-FLOOR",

    # ── Woodwork spec-note text & area labels ─────────────────────────────────
    "TA - WOOD WORK",  # Spec notes: "SLIDING DOOR DESIGN, TECHNICAL DETAIL..."
    "TA - WOODWORK",   # Variant: single word
    "TA-WOODWORK",     # Hyphenated variant
    "WOOD WORK",       # Bare suffix fallback
]


def layer_should_remove(name: str) -> bool:
    """
    Return True if this layer should be removed.
    
    GOAL: Keep everything needed for door/window counting.
    PRINCIPLE: When in doubt, KEEP IT.
    """
    nl = name.lower()
    
    # ============================================================================
    # STEP 0: REMOVE wind / compass layers before the generic "wind" KEEP rule
    # (e.g. A-WIND-DIRECTION matches "wind" and would otherwise be kept)
    # ============================================================================
    _wind_or_compass_remove = (
        "wind dir",
        "wind-dir",
        "wind-direction",
        "winddir",
        "wind direction",
        "winddirection",
        "wind rose",
        "windrose",
        "w.dir",
        "wind-arrow",
        "wind arrow",
        "compass",
        "compass rose",
        "true north",
        "north arrow",
        "north arr",
    )
    if any(p in nl for p in _wind_or_compass_remove):
        _glazing_or_door_keep = (
            "window",
            "glaz",
            "win-",
            "wind-tag",
            "wind tag",
            "windtag",
            "w-tag",
            "door",
            "dr-",
            "a-door",
            "door-tag",
            "door tag",
            "doortag",
            "d-tag",
        )
        _kept_glazing = any(k in nl for k in _glazing_or_door_keep)
        # "a-win" must not match inside "a-wind-*" / "a-window-*" (e.g. A-WIND-DIRECTION)
        if not _kept_glazing and "a-win" in nl and "a-wind" not in nl:
            _kept_glazing = True
        if not _kept_glazing:
            return True
    
    # ============================================================================
    # STEP 1: EXPLICIT KEEP LIST (check first, highest priority)
    # ============================================================================
    
    # ALWAYS keep: Doors (including tags and dimensions)
    if any(pattern in nl for pattern in ['door', 'dr-', 'a-door', 'a_a_door', 'door-tag', 'd-tag']):
        return False
    
    # ALWAYS keep: Windows (including tags and dimensions)
    if any(pattern in nl for pattern in ['window', 'wind', 'win-', 'glaz', 'a-win', 'wind-tag', 'w-tag']):
        return False
    
    # ALWAYS keep: Walls
    # Check landscape exceptions against LEAF layer name only so building walls
    # nested in landscape XRefs (e.g. XR Landscape...$0$A_A_BLCKWALL Cut) are kept.
    if 'wall' in nl:
        leaf = nl.split('$0$')[-1] if '$0$' in nl else nl
        if not any(exc in leaf for exc in ['landscape', 'l-lo-']):
            return False
    
    # ALWAYS keep: Room boundaries
    if 'room' in nl and any(pattern in nl for pattern in ['bound', 'line', 'outline']):
        return False
    
    # ALWAYS keep: Symbols (may contain door/window blocks)
    if 'symb' in nl and not any(bad in nl for bad in ['anno', 'tag']):
        return False
    
    # ALWAYS keep: Context layers (00_CONTEXT) — "context" contains "text" substring
    if 'context' in nl:
        return False
    
    # ============================================================================
    # STEP 2: EXPLICIT REMOVE LIST (check if it matches removal criteria)
    # ============================================================================
    
    # Only remove if it matches one of the explicit removal patterns
    should_remove = any(pat.lower() in nl for pat in REMOVE_PATTERNS)
    
    # ============================================================================
    # STEP 3: DEFAULT = KEEP (when in doubt)
    # ============================================================================
    
    return should_remove


def _build_tag_removal_set(doc: fitz.Document, page: fitz.Page) -> set:
    """
    Build the set of content-stream OCG tag names (e.g. '/oc422') that
    correspond to layers we want to remove.
    """
    ocgs = doc.get_ocgs()
    props_raw = doc.xref_get_key(page.xref, "Resources/Properties")
    if not props_raw or props_raw[0] not in ("dict", "<<"):
        return set()

    _, props_str = props_raw
    tag_to_xref: dict[str, int] = {}
    for m in re.finditer(r"/(\w+)\s+(\d+)\s+0\s+R", props_str):
        tag_to_xref[m.group(1)] = int(m.group(2))

    remove_tags: set[str] = set()
    for tag, xref in tag_to_xref.items():
        name = ocgs.get(xref, {}).get("name", "")
        if layer_should_remove(name):
            remove_tags.add(tag)

    return remove_tags


def _strip_content_streams(doc: fitz.Document, page: fitz.Page,
                            remove_tags: set) -> tuple[int, int]:
    """
    Physically remove BDC/EMC marked-content sections for the given OCG tags
    from all content streams on the page.

    Returns (total_sections_removed, total_bytes_removed).
    """
    total_sections = 0
    total_bytes = 0

    for stream_xref in page.get_contents():
        raw_bytes = doc.xref_stream(stream_xref)
        content = raw_bytes.decode("latin-1")
        original_len = len(content)

        for tag in remove_tags:
            pattern = r"/" + re.escape(tag) + r"\s+BDC.*?EMC\n?"
            sections = len(re.findall(pattern, content, flags=re.DOTALL))
            total_sections += sections
            content = re.sub(pattern, "", content, flags=re.DOTALL)

        bytes_removed = original_len - len(content)
        if bytes_removed > 0:
            total_bytes += bytes_removed
            doc.update_stream(stream_xref, content.encode("latin-1"))

    return total_sections, total_bytes


def _strip_untagged_patterns(doc: fitz.Document, page: fitz.Page) -> int:
    """
    Remove ONLY untagged (outside BDC/EMC) hatching and color fills.
    
    CRITICAL: Must NOT touch content inside BDC/EMC blocks (layers).
    Door/window labels are IN layers (oc171, oc172) - must preserve them!
    
    Returns bytes removed.
    """
    total_bytes_removed = 0
    
    for stream_xref in page.get_contents():
        raw_bytes = doc.xref_stream(stream_xref)
        content = raw_bytes.decode("latin-1", errors='ignore')
        original_len = len(content)
        
        # STEP 1: Extract all BDC/EMC blocks (these are layer content - DON'T TOUCH)
        layer_blocks = []
        for match in re.finditer(r'/oc\d+\s+BDC(.*?)EMC', content, re.DOTALL):
            layer_blocks.append((match.start(), match.end(), match.group(0)))
        
        # STEP 2: Identify untagged regions (between layer blocks)
        untagged_regions = []
        last_end = 0
        
        for start, end, block_content in layer_blocks:
            if start > last_end:
                # Content between last block and this block = untagged
                untagged_regions.append((last_end, start))
            last_end = end
        
        # Add final region after last block
        if last_end < len(content):
            untagged_regions.append((last_end, len(content)))
        
        # STEP 3: Remove hatching/fills ONLY from untagged regions
        cleaned_content = ""
        pos = 0
        
        for region_start, region_end in untagged_regions:
            # Add any layer content before this region
            if pos < region_start:
                cleaned_content += content[pos:region_start]
            
            # Process untagged region
            untagged_chunk = content[region_start:region_end]
            
            # Remove pattern fills (hatching)
            untagged_chunk = re.sub(
                r'/Pattern\s+cs\s+/P\d+\s+scn.*?[fFbB]\*?(?=\s)',
                '',
                untagged_chunk,
                flags=re.DOTALL
            )
            
            # Remove solid color fills (but NOT if followed by stroke)
            # Only remove if it's: color + simple path + fill (no stroke)
            untagged_chunk = re.sub(
                r'(\d+\.?\d*\s+){1,4}[rRgGkK]\s+[\d\s\.mlrec-]+?\s+[fF](?!\s*S)',
                '',
                untagged_chunk
            )
            
            # Remove red color markup sequences
            # Pattern: 1 0 0 rg/RG ... (geometry) ... S/f/F
            untagged_chunk = re.sub(
                r'1\.?0*\s+0\.?0*\s+0\.?0*\s+[rR][gG]\s+[\d\s\.mlrec-]+?\s+[SfF]\b',
                '',
                untagged_chunk
            )
            
            # Remove FFL/SSL level marker text and geometry
            # Pattern: FFL = +X.XXm or SSL = +X.XXm with associated circle/crosshair
            untagged_chunk = re.sub(
                r'\((?:FFL|SSL)\s*=\s*[+-]?\d+\.\d+m\).*?Tj',
                '',
                untagged_chunk,
                flags=re.DOTALL
            )
            
            # Remove circle symbols (level markers) - typically 0 0 r re f* pattern
            untagged_chunk = re.sub(
                r'\d+\.?\d*\s+\d+\.?\d*\s+\d+\.?\d*\s+0\s+360\s+arc\s+[fFS]\*?',
                '',
                untagged_chunk
            )
            
            cleaned_content += untagged_chunk
            pos = region_end
        
        # Add any remaining content
        if pos < len(content):
            cleaned_content += content[pos:]
        
        # Update stream if changed
        new_len = len(cleaned_content)
        if new_len < original_len:
            doc.update_stream(stream_xref, cleaned_content.encode("latin-1"))
            total_bytes_removed += (original_len - new_len)
    
    return total_bytes_removed


def _strip_red_colors(doc, page) -> int:
    """
    Strip all red color operators (1 0 0 rg/RG) from the content stream.
    
    This removes red markup/highlights that may appear in any layer,
    including layers we're keeping like door tags and dimensions.
    
    Returns: Number of bytes removed.
    """
    total_bytes_removed = 0
    
    for stream_xref in page.get_contents():
        stream = doc.xref_stream(stream_xref)
        if not stream:
            continue
        
        try:
            content = stream.decode("latin-1", errors="ignore")
        except:
            continue
        
        original_len = len(content)
        original_content = content
        
        # Replace red color operators with black (neutral)
        # This preserves geometry but removes the red coloring
        content = re.sub(
            r'1\.?0*\s+0\.?0*\s+0\.?0*\s+rg\b',
            '0 0 0 rg',
            content
        )
        
        content = re.sub(
            r'1\.?0*\s+0\.?0*\s+0\.?0*\s+RG\b',
            '0 0 0 RG',
            content
        )
        
        # Update stream if content changed (even if length is same)
        if content != original_content:
            doc.update_stream(stream_xref, content.encode("latin-1"))
    
    return total_bytes_removed


def _color_bold_circles(inner: str, color: str) -> str:
    """
    Color AND bold the Bézier-circle sub-segments inside a BDC block.
    Wrapped in q…Q so line-width changes cannot leak to surrounding content.
    """
    BOLD_LW = "6"
    circle_pat = re.compile(
        r'(q\s+'
        r'(?:[-\d.]+\s+){5,8}cm\s+'
        r'(?:[-\d.]+\s+){1,3}m\s+'
        r'(?:(?:[-\d.]+\s+){5,7}c\s+){3,}'
        r'(?:[-\d.]+\s+){5,7}c\s+[SfFB]\*?\s*'
        r'Q)',
        re.DOTALL,
    )
    def _recolor(m):
        return (f'q\n{color} RG {color} rg\n{BOLD_LW} w\n'
                f'{m.group(0)}\n'
                f'Q\n')
    return circle_pat.sub(_recolor, inner)


_TEXT_OP_PAT = re.compile(r'(\([^)]+\)\s*Tj|\[[^\]]+\]\s*TJ)', re.DOTALL)


def _bold_text(inner: str, color: str) -> str:
    """
    Wrap every text-show operator (Tj / TJ) with bold fill+stroke rendering
    (Tr=2) so glyphs appear heavier.  Wrapped in q…Q to contain state changes.
    """
    TEXT_STROKE_LW = "1"
    def _embolden(m):
        return (f'q\n{color} RG {color} rg\n{TEXT_STROKE_LW} w\n2 Tr\n'
                f'{m.group(0)}\n'
                f'0 Tr\nQ\n')
    return _TEXT_OP_PAT.sub(_embolden, inner)


def _color_geometry_bold(inner: str, color: str) -> str:
    """
    Color AND bold ALL strokes in a geometry BDC block (door swings, window
    frames, opening lines).  Wrapped in q…Q so changes are contained.

    The leading newline ensures the 'q' is never glued directly to 'BDC'
    (which MuPDF would parse as the unknown keyword 'BDCq').
    """
    BOLD_LW = "6"
    return f'\nq\n{color} RG {color} rg\n{BOLD_LW} w\n{inner}\nQ\n'


def _minimize_wall_hatch(doc, page) -> int:
    """
    Make wall-hatch diagonal lines thinner and lighter so they read as a
    subtle texture rather than heavy fill.

    Targets layers whose leaf name contains wall-hatch keywords
    (A_L_HAT_WALL, TA - WALL HATCH, A-WALL FILL …).  Each matching BDC
    block is wrapped in:

        q  0.55 0.55 0.55 RG  0.55 0.55 0.55 rg  0.2 w  <content>  Q

    Returns: number of streams modified.
    """
    HATCH_GREY  = "0.55 0.55 0.55"
    HATCH_LW    = "0.2"
    HATCH_KW    = ("_hat_wall", "hat_wall", "wall hatch", "wall-hatch",
                   "a-wall fill", "wall fill", "wall_fill")

    ocgs = doc.get_ocgs()
    props_raw = doc.xref_get_key(page.xref, "Resources/Properties")
    xref_to_oc: dict = {}
    if props_raw and props_raw[0] in ("dict", "<<"):
        for m in re.finditer(r"/oc(\d+)\s+(\d+)\s+0\s+R", props_raw[1]):
            xref_to_oc[int(m.group(2))] = m.group(1)

    hatch_ocs: set = set()
    for xref, info in ocgs.items():
        nl   = info.get("name", "").lower()
        leaf = nl.split("$0$")[-1] if "$0$" in nl else nl
        oc   = xref_to_oc.get(xref)
        if oc and any(kw in leaf for kw in HATCH_KW):
            hatch_ocs.add(oc)

    if not hatch_ocs:
        return 0

    modified = 0
    for stream_xref in page.get_contents():
        raw = doc.xref_stream(stream_xref)
        if not raw:
            continue
        try:
            content = raw.decode("latin-1", errors="ignore")
        except Exception:
            continue
        original = content

        def _thin_hatch(m):
            oc_num = m.group(1)
            inner  = m.group(2)
            if oc_num not in hatch_ocs:
                return m.group(0)
            new_inner = (f'\nq\n{HATCH_GREY} RG {HATCH_GREY} rg\n'
                         f'{HATCH_LW} w\n{inner}\nQ\n')
            return f"/oc{oc_num} BDC{new_inner}EMC"

        content = re.sub(r"/oc(\d+)\s+BDC(.*?)EMC", _thin_hatch,
                         content, flags=re.DOTALL)
        if content != original:
            doc.update_stream(stream_xref, content.encode("latin-1"))
            modified += 1

    return modified


def _enhance_labels_and_normalize(doc, page) -> int:
    """
    Color-code and bold door and window OCG BDC blocks:

      Tag layers  (A-DOOR-TAG, A-WIND-TAG):
        • Tag bubble circles → bold + colored (red / orange)
        • Label text (GD23, SD07 …) → bold fill+stroke in same color

      Geometry layers (A_A_DOOR, VERRE, RAHMEN, …):
        • All strokes → bold + colored (red for doors, orange for windows)

      All width changes are wrapped in q…Q — walls outside these blocks
      are untouched.

    Returns: number of content streams modified.
    """
    ocgs = doc.get_ocgs()
    props_raw = doc.xref_get_key(page.xref, "Resources/Properties")
    xref_to_oc = {}
    if props_raw and props_raw[0] in ("dict", "<<"):
        _, ps = props_raw
        for m in re.finditer(r"/oc(\d+)\s+(\d+)\s+0\s+R", ps):
            xref_to_oc[int(m.group(2))] = m.group(1)

    red_tag_ocs    = set()
    red_geom_ocs   = set()
    orange_tag_ocs = set()
    orange_geom_ocs = set()

    _win_tag_kw  = ("wind-tag", "wind tag", "windtag", "a-wind-tag", "w-tag")
    _door_tag_kw = ("door-tag", "door tag", "doortag", "a-door-tag")
    _win_kw  = ("window", "glaz", "a-win", "win-", "verre", "vitro",
                "rahmen", "profil alu", "silicone", "loquet", "cache",
                "colle", "isolation")
    _door_kw = ("a_a_door", "a-door", "dr-")

    for xref, info in ocgs.items():
        nl   = info.get("name", "").lower()
        leaf = nl.split("$0$")[-1] if "$0$" in nl else nl
        oc   = xref_to_oc.get(xref)
        if not oc:
            continue
        if any(kw in leaf for kw in _win_tag_kw):
            orange_tag_ocs.add(oc)
        elif any(kw in leaf for kw in _door_tag_kw):
            red_tag_ocs.add(oc)
        elif any(kw in leaf for kw in _win_kw) or ("wind" in leaf and "door" not in leaf):
            orange_geom_ocs.add(oc)
        elif any(kw in leaf for kw in _door_kw):
            red_geom_ocs.add(oc)

    RED    = "1 0 0"
    ORANGE = "1 0.55 0"

    modified = 0

    for stream_xref in page.get_contents():
        raw = doc.xref_stream(stream_xref)
        if not raw:
            continue
        try:
            content = raw.decode("latin-1", errors="ignore")
        except Exception:
            continue

        original = content

        def wrap_block(m):
            oc_num = m.group(1)
            inner  = m.group(2)

            if oc_num in orange_tag_ocs:
                new_inner = _color_geometry_bold(inner, ORANGE)
                return f"/oc{oc_num} BDC{new_inner}EMC"

            if oc_num in red_tag_ocs:
                new_inner = _color_geometry_bold(inner, RED)
                return f"/oc{oc_num} BDC{new_inner}EMC"

            if oc_num in orange_geom_ocs:
                new_inner = _color_geometry_bold(inner, ORANGE)
                return f"/oc{oc_num} BDC{new_inner}EMC"

            if oc_num in red_geom_ocs:
                new_inner = _color_geometry_bold(inner, RED)
                return f"/oc{oc_num} BDC{new_inner}EMC"

            return m.group(0)

        content = re.sub(
            r"/oc(\d+)\s+BDC(.*?)EMC",
            wrap_block,
            content,
            flags=re.DOTALL,
        )

        if content != original:
            doc.update_stream(stream_xref, content.encode("latin-1"))
            modified += 1

    return modified


def _strip_non_window_tags(doc, page) -> int:
    """
    Inside A-WIND-TAG BDC blocks, remove curtain-wall (CW) and storefront (ST)
    tag groups while keeping genuine window-door (WD) tags.

    Detection: each tag is a hexagonal bubble (7-point closed polygon) followed
    by vector-drawn characters.  The FIRST character after the hexagon determines
    the tag type — a "W" is drawn as a distinctive 5-point zigzag (x alternates
    between two values, y monotonically increases).  Blocks whose hexagons all
    start with "W" are WD tags; blocks with non-"W" first letters are CW/ST.

    Returns bytes removed.
    """
    ocgs = doc.get_ocgs()
    props_raw = doc.xref_get_key(page.xref, "Resources/Properties")
    wind_tag_ocs: set[str] = set()
    if props_raw and props_raw[0] in ("dict", "<<"):
        _, ps = props_raw
        for m in re.finditer(r"/oc(\d+)\s+(\d+)\s+0\s+R", ps):
            xref = int(m.group(2))
            if xref in ocgs:
                name = ocgs[xref]["name"].lower()
                if "wind-tag" in name or "wind tag" in name:
                    wind_tag_ocs.add(m.group(1))

    if not wind_tag_ocs:
        return 0

    def _is_w_zigzag(pts):
        if len(pts) != 5:
            return False
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ux = sorted(set(xs))
        if len(ux) != 2:
            return False
        if not all(
            (xs[i] == ux[0] and xs[i + 1] == ux[1])
            or (xs[i] == ux[1] and xs[i + 1] == ux[0])
            for i in range(len(xs) - 1)
        ):
            return False
        return all(ys[i] < ys[i + 1] for i in range(len(ys) - 1)) or all(
            ys[i] > ys[i + 1] for i in range(len(ys) - 1)
        )

    def _block_is_wd(block: str) -> bool | None:
        """True = all tags are WD, False = all non-WD, None = no hexagons found."""
        strokes: list[list[tuple[int, int]]] = []
        for sm in re.finditer(r"((?:\d[\d\s.]*[ml]\s*)+)S", block):
            raw = sm.group(1)
            pts = [
                (int(x), int(y))
                for x, y in re.findall(r"(\d+)\s+(\d+)\s+[ml]", raw)
            ]
            strokes.append(pts)

        first_letters: list[list[tuple[int, int]]] = []
        for i, pts in enumerate(strokes):
            if len(pts) == 7 and pts[0] == pts[6]:
                if i + 1 < len(strokes):
                    first_letters.append(strokes[i + 1])

        if not first_letters:
            return None
        return all(_is_w_zigzag(fl) for fl in first_letters)

    total_removed = 0

    for stream_xref in page.get_contents():
        raw_bytes = doc.xref_stream(stream_xref)
        if not raw_bytes:
            continue
        content = raw_bytes.decode("latin-1", errors="ignore")
        original_len = len(content)

        def _filter_wind_block(m):
            oc_num = m.group(1)
            if oc_num not in wind_tag_ocs:
                return m.group(0)
            block = m.group(2)
            verdict = _block_is_wd(block)
            if verdict is False:
                return f"/oc{oc_num} BDC\nEMC"
            return m.group(0)

        content = re.sub(
            r"/oc(\d+)\s+BDC(.*?)EMC",
            _filter_wind_block,
            content,
            flags=re.DOTALL,
        )
        new_len = len(content)
        if new_len < original_len:
            doc.update_stream(stream_xref, content.encode("latin-1"))
            total_removed += original_len - new_len

    return total_removed


def _strip_room_number_text(doc, page) -> int:
    """
    Strip small clutter text embedded in kept layers: room numbers (1–2 digits),
    floor indicators (UP/DOWN/G), structural/curtain-wall callouts (ST 01, CW04),
    and simple wind-speed labels — not door/window schedule tags (WD/GD/…).
    
    Returns: Number of bytes removed.
    """
    total_bytes_removed = 0
    
    for stream_xref in page.get_contents():
        stream = doc.xref_stream(stream_xref)
        if not stream:
            continue
        
        try:
            content = stream.decode("latin-1", errors="ignore")
        except:
            continue
        
        original_content = content
        
        # Remove text show operators containing ONLY 1-2 digit numbers
        # e.g. (11) Tj  or  (26) Tj  — but NOT (1345) Tj (dimension values)
        # Also removes UP, DOWN, G (floor indicators)
        content = re.sub(
            r'\((0?[0-9]{1,2}|UP|DOWN|G)\)\s*Tj',
            '',
            content
        )
        # Structural grid / curtain-wall panel callouts (not door type WDxx)
        content = re.sub(
            r'\(ST\s*[- ]?\s*0?\d{1,2}\)\s*Tj',
            '',
            content,
            flags=re.IGNORECASE,
        )
        content = re.sub(
            r'\(CW\s*0?\d{1,3}\)\s*Tj',
            '',
            content,
            flags=re.IGNORECASE,
        )
        # Simple wind-speed annotations, e.g. (3.5 m/s) Tj
        content = re.sub(
            r'\([\d.]+\s*m/s\)\s*Tj',
            '',
            content,
            flags=re.IGNORECASE,
        )
        
        if content != original_content:
            doc.update_stream(stream_xref, content.encode("latin-1"))
            total_bytes_removed += abs(len(original_content) - len(content))
    
    return total_bytes_removed


def clean_floor_plan(input_pdf: str, output_pdf: str) -> dict:
    """
    Clean the floor plan PDF:
      1. Set OCG layer visibility (for conforming PDF viewers).
      2. Strip BDC/EMC content blocks from content streams (for all renderers).

    Returns a summary dict.
    """
    doc = fitz.open(input_pdf)
    page = doc[0]
    ocgs = doc.get_ocgs()

    # ── Step 1: OCG layer visibility ─────────────────────────────────────────
    on_xrefs, off_xrefs = [], []
    kept_names, removed_names = [], []

    for xref, info in sorted(ocgs.items()):
        name = info["name"]
        if layer_should_remove(name):
            off_xrefs.append(xref)
            removed_names.append(name)
        else:
            on_xrefs.append(xref)
            kept_names.append(name)

    doc.set_layer(
        -1,
        basestate="ON",
        on=on_xrefs or None,
        off=off_xrefs or None,
    )

    # ── Step 2: Content-stream stripping (tagged content) ────────────────────
    remove_tags = _build_tag_removal_set(doc, page)
    sections_removed, bytes_removed = _strip_content_streams(
        doc, page, remove_tags
    )

    # No fill/pattern/text stripping — only colour-code doors and windows.
    untagged_bytes_removed = 0
    red_bytes_removed = 0

    # ── Colour-code doors (red) and windows (orange) — inject & reset only ────
    bold_streams = _enhance_labels_and_normalize(doc, page)

    # ── Minimize wall hatch (thin + light grey) ───────────────────────────────
    _minimize_wall_hatch(doc, page)

    doc.save(output_pdf, garbage=4, deflate=True)
    doc.close()

    return {
        "kept_count": len(kept_names),
        "removed_count": len(removed_names),
        "kept": kept_names,
        "removed": removed_names,
        "content_sections_removed": sections_removed,
        "content_bytes_removed": bytes_removed,
        "untagged_bytes_removed": untagged_bytes_removed,
        "red_bytes_removed": red_bytes_removed,
    }


def render_web_pdf(cleaned_pdf: str, web_pdf: str, zoom: float = 3.0) -> None:
    """
    Produce a web-compatible flattened PDF by rasterising the cleaned vector
    PDF at high resolution and embedding the result as a single image page.

    WHY THIS IS NEEDED
    ──────────────────
    Web apps (PDF.js and similar) reset the PDF graphics state when entering
    OCG/BDC marked-content blocks, silently ignoring our colour injections
    (red/orange bold labels) in those renderers.

    A rasterised PDF has no layers, no OCG, no BDC — just one image per page.
    It renders identically in every viewer: web browsers, mobile apps, Acrobat,
    Preview, and any OCR / vision pipeline.

    zoom=3.0 → 216 DPI  (default; good balance of quality / file size)
    zoom=4.0 → 288 DPI  (higher quality, larger file)
    """
    src      = fitz.open(cleaned_pdf)
    src_page = src[0]
    pix      = src_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)

    out      = fitz.open()
    out_page = out.new_page(width=src_page.rect.width, height=src_page.rect.height)
    out_page.insert_image(out_page.rect, stream=pix.tobytes("png"))

    out.save(web_pdf, deflate=True)
    out.close()
    src.close()


def render_preview(pdf_path: str, png_path: str, zoom: float = 1.0) -> None:
    """Render the first page of a PDF to a PNG file."""
    doc = fitz.open(pdf_path)
    page = doc[0]
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    pix.save(png_path)
    doc.close()
    print(f"  Saved: {png_path} ({pix.width}x{pix.height} px)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))

    input_pdf = os.path.join(
        "/home/ubuntu/.cursor/projects/workspace/uploads",
        "A-619_Mechanical_2_Floor_Plan-Doors___Windows_Tags-A-619.pdf",
    )
    output_pdf = os.path.join(base_dir, "cleaned_floor_plan.pdf")
    preview_original = os.path.join(base_dir, "preview_original.png")
    preview_cleaned = os.path.join(base_dir, "preview_cleaned.png")

    if not os.path.exists(input_pdf):
        print(f"ERROR: Input PDF not found: {input_pdf}")
        sys.exit(1)

    print("=" * 65)
    print("Floor Plan Line Cleanup  –  Preprocessing for Door/Window Count")
    print("=" * 65)
    print(f"Input:  {input_pdf}")
    print(f"Output: {output_pdf}")
    print()

    print("Rendering original preview ...")
    render_preview(input_pdf, preview_original, zoom=1.0)

    print("\nCleaning floor plan layers ...")
    summary = clean_floor_plan(input_pdf, output_pdf)

    print(f"\nLayers KEPT  ({summary['kept_count']}):")
    for name in summary["kept"]:
        print(f"  + {name}")

    print(f"\nLayers REMOVED ({summary['removed_count']}):")
    for name in summary["removed"]:
        print(f"  - {name}")

    print(f"\nContent stream sections stripped : {summary['content_sections_removed']}")
    print(f"Content bytes removed            : {summary['content_bytes_removed']:,}")

    print("\nRendering cleaned preview ...")
    render_preview(output_pdf, preview_cleaned, zoom=1.0)

    print()
    print("=" * 65)
    print("Done.")
    print(f"  Cleaned PDF : {output_pdf}")
    print(f"  Original PNG: {preview_original}")
    print(f"  Cleaned PNG : {preview_cleaned}")
    print("=" * 65)


if __name__ == "__main__":
    main()
