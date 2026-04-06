# Plan2BoQ — Floor Plan PDF Preprocessor

Automated preprocessing pipeline that cleans architectural floor plan PDFs,
retaining only the elements required for door and window counting (BoQ generation).

---

## What It Does

Takes a raw architectural PDF floor plan and produces a clean version that:

- **Keeps** door symbols, window symbols, wall outlines, room boundaries, door/window ID tags, and dimension values
- **Removes** hatching, furniture, grids, section tags, FFL/SSL level markers, room names, area labels, annotations, title blocks, roads, ramps, and all colour fills
- **Enhances** door/window tag labels and dimension numbers with bold stroke weight for easier OCR/vision detection
- **Converts** any red markup colours to black
- **Scores** every layer-removal decision with a confidence percentage via the built-in ML-style classifier

---

## Project Structure

```
Plan2BoQ - PDF cleaning/
│
├── process_floor_plans.py   # Main entry point — run this
├── clean_floor_plan.py      # Core PDF cleaning engine (importable module)
├── ml_confidence_scorer.py  # ML-style rule-based confidence scoring module
│
├── requirements.txt         # Python dependencies
├── README.md                # This file
│
├── unprocessed/             # Drop input PDFs here
├── cleaned/                 # Cleaned PDFs are written here
└── archived/                # Original PDFs are moved here after processing
```

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Add floor plan PDFs

Copy your PDF files into the `unprocessed/` folder:

```
unprocessed/
└── A-604_Ground Floor Plan.pdf
```

### 3. Run the processor

```bash
python3 process_floor_plans.py
```

### 4. Collect results

| Location | Contents |
|----------|----------|
| `cleaned/` | Cleaned PDFs + before/after preview PNGs |
| `archived/` | Original unmodified PDFs |
| `processing_report.txt` | Per-file stats (layers removed, size reduction, etc.) |

---

## Processing Pipeline

Each PDF passes through **6 sequential steps**:

| Step | Module | Action |
|------|--------|--------|
| **1** | `clean_floor_plan.py` | **OCG layer visibility** — sets layer ON/OFF flags for PDF-viewer compliance |
| **2** | `clean_floor_plan.py` | **BDC/EMC content-stream stripping** — physically removes tagged content blocks for unwanted layers from raw streams |
| **3** | `clean_floor_plan.py` | **Untagged content stripping** — removes hatching, colour fills, and FFL/SSL geometry from untagged stream regions |
| **4** | `clean_floor_plan.py` | **Room number text stripping** — removes standalone 1-2 digit room numbers embedded inside door/window layer content |
| **5** | `clean_floor_plan.py` | **Red colour normalisation** — converts all red `rg`/`RG` colour operators to black |
| **6** | `clean_floor_plan.py` | **Bold label enhancement** — wraps door/window tag and dimension BDC blocks with increased stroke weight (10 units ≈ 1.7 pt) |

> **Layer-removal decisions in Steps 1 & 2** are made by `layer_should_remove()` in `clean_floor_plan.py`,
> which applies the same keep/remove logic as the ML confidence scorer. Run the scorer standalone
> (see below) to audit and review those decisions with confidence percentages before or after processing.

---

## ML-Style Confidence Scoring

`ml_confidence_scorer.py` is a **rule-based classifier with confidence weights**
that makes and justifies every layer-removal decision.

### How it works

The scorer applies a **3-tier priority system** to every layer name:

```
Tier 1 (highest priority) — KEEP patterns
   └─ Matches: door, window, wall, symbol
   └─ Confidence: 100%  →  Decision: KEEP unconditionally

Tier 2 — REMOVE patterns
   └─ Matches: furniture, stairs, grid, hatching, annotations, MEP, etc.
   └─ Confidence: 80 – 95%  →  Decision: REMOVE

Tier 3 (default) — No pattern matched
   └─ Confidence: 30%  →  Decision: KEEP  (when in doubt, keep it)
```

### Confidence weight table

| Category | Example patterns | Confidence |
|----------|-----------------|------------|
| Furniture & equipment | `furn`, `mobilier`, `chair` | 95% |
| Stairs & circulation | `stair`, `steps`, `handrail` | 95% |
| Hatching & fills | `hatch`, `hachures`, `_hatch` | 95% |
| Roads & ramps | `road`, `ramp`, `arrow` | 95% |
| Grid lines | `grid line`, `_grid`, `axes grid` | 90% |
| Dimensions (grid/column) | `dimension`, `dim`, `cotes` | 90% |
| Landscape & site | `landscape`, `tree`, `l-pl-` | 90% |
| Parking | `parking`, `arrow` | 85% |
| Annotations & tags | `cloud`, `stamp`, `-tag-box` | 85% |
| MEP systems | `mep`, `duct`, `mechanical` | 80% |
| **Door** | `door`, `dr-`, `a-door`, `d-tag` | **100% KEEP** |
| **Window** | `window`, `wind`, `glaz`, `a-win` | **100% KEEP** |
| **Wall** | `wall` (non-landscape) | **100% KEEP** |

### Run the scorer standalone (audit any PDF)

```bash
python3 ml_confidence_scorer.py archived/A-604_Ground\ Floor\ Plan.pdf
```

**Console output example:**

```
Total layers: 210
Recommend REMOVE: 98 layers
Recommend KEEP: 112 layers
Removal rate: 46.7%

Confidence breakdown:
  High confidence remove (≥80%): 91
  High confidence keep (≥80%): 89
  Uncertain (<50%): 23

Example removals (high confidence):
  • [95%] A-104_Ground Floor Plan$0$A_L_HAT_WALL
    └─ Unwanted: hatching
  • [95%] A-104_Ground Floor Plan$0$A_A_STAIR
    └─ Unwanted: stairs

Example keeps (essential elements):
  • [100%] A-DOOR-TAG
    └─ Essential: door
  • [100%] A-WIND-TAG
    └─ Essential: window
```

A detailed report is saved to `ml_confidence_report.txt`.

### Use the scorer in code

```python
from ml_confidence_scorer import ConfidenceScorer

scorer = ConfidenceScorer()

# Score a single layer name — returns (should_remove, reason, confidence, details)
should_remove, reason, confidence, details = scorer.analyze_layer_name("A-ROOM-TAG-BOX")
# should_remove → True
# reason        → "Unwanted: annotations"
# confidence    → 0.85
# details       → {"action": "REMOVE", "matched_pattern": "-tag-box", "reasoning": "..."}

should_remove, reason, confidence, details = scorer.analyze_layer_name("A-DOOR-TAG")
# should_remove → False
# reason        → "Essential: door"
# confidence    → 1.0

should_remove, reason, confidence, details = scorer.analyze_layer_name("A-dimensions")
# should_remove → False
# reason        → "No clear pattern, keeping by default"
# confidence    → 0.3   (uncertain → default KEEP)

# Score all layers in a PDF and get full results
results = scorer.analyze_pdf("path/to/input.pdf")
print(f"Remove: {len(results['remove'])} layers")
print(f"Keep:   {len(results['keep'])} layers")
print(f"Uncertain (<50%): {results['uncertain']}")
```

---

## What Gets Removed

| Category | Layer patterns matched |
|----------|----------------------|
| Hatching & fills | `HATCH`, `HTCH`, `PATT` |
| Furniture & equipment | `FURN`, `EQPM`, `MOBILIER` |
| Grid lines | `GRID`, `AXES GRID`, `_AXS` |
| Dimensions (non-door/window) | `GRID DIM`, `COLUMN-DIM` |
| Annotations | `ANNO`, `LTAG`, `X-TAGS` |
| Level markers | `FFL`, `SSL`, `ANNO-LEVL`, `ELEV-MARKER` |
| Room tags & names | `ROOM-TAG`, `RM TAG`, `SUITE` |
| Room area text | `A_TEXT`, `A-TEXT`, `45-TEXTS`, `MTEXT` |
| Stairs & circulation | `STAIR`, `RAMP`, `ARROW`, `LIFT` |
| Parking & roads | `PARKING`, `ROAD` |
| Landscape & site | `LANDSCAPE`, `TREE`, `SITE`, `AREA` |
| Title blocks | `TITLE` |
| MEP systems | `MEP`, `DUCT`, `PLUMBING` |

**Principle:** When in doubt, the element is **kept** (Tier 3 default = KEEP at 30% confidence).

---

## What Is Always Preserved

| Element | Layer patterns |
|---------|---------------|
| Door symbols & swing arcs | `door`, `DR`, `A-DOOR`, `A_A_DOOR` |
| Door ID tags (GD07, SD01…) | `door-tag`, `D-TAG`, `A-DOOR-TAG` |
| Window symbols & glazing | `window`, `WIN`, `GLAZ`, `A-WIN` |
| Window ID tags (WD06, CW05…) | `wind-tag`, `W-TAG`, `A-WIND-TAG` |
| Wall outlines | `wall` (excluding `landscape`, `l-lo-`) |
| Room boundary lines | `bound`, `outline` |
| Dimension values | `A-dimensions` and non-grid dimension layers |

---

## Performance

| Metric | Typical value |
|--------|--------------|
| Processing time per PDF | ~4–5 seconds |
| Size reduction | 35 – 55% |
| Layers removed | 60 – 100 per file |
| Scorer confidence ≥ 80% | ~90% of all decisions |

---

## Using the Cleaning Engine Directly

`clean_floor_plan.py` can be imported independently of the workflow:

```python
from clean_floor_plan import clean_floor_plan

summary = clean_floor_plan("input.pdf", "output.pdf")
print(f"Layers removed:    {summary['removed_count']}")
print(f"Sections stripped: {summary['content_sections_removed']}")
print(f"Bytes removed:     {summary['content_bytes_removed']:,}")
```

---

## Requirements

### Python version
- Python 3.9+

### Third-party libraries (install via `pip install -r requirements.txt`)

| Library | Version | Used in | Purpose |
|---------|---------|---------|---------|
| `pymupdf` | ≥ 1.27.0 | all `.py` files | PDF reading, layer (OCG) control, content-stream manipulation, and PNG rendering — imported as `fitz` |
| `Pillow` | ≥ 10.0.0 | `process_floor_plans.py` | High-quality PNG preview generation |

### Standard library (built-in, no install needed)

| Module | Used in | Purpose |
|--------|---------|---------|
| `os` | `process_floor_plans.py`, `clean_floor_plan.py` | File path operations and directory management |
| `re` | all `.py` files | Regular expression matching on PDF content streams and layer names |
| `sys` | all `.py` files | Command-line argument handling and exit codes |
| `shutil` | `process_floor_plans.py` | Moving original PDFs to the `archived/` folder |
| `datetime` | `process_floor_plans.py` | Timestamping the processing report |
| `pathlib` | `process_floor_plans.py` | Cross-platform file path construction |
| `typing` | `ml_confidence_scorer.py` | Type hints (`Dict`, `List`, `Tuple`) |

---

## Notes

- Only the **first page** of each PDF is processed (floor plan drawings are single-page)
- The `.dwg` file in `archived/` is kept for reference but is not processed
- Preview PNGs generated in `cleaned/` are for visual QA and are not committed to version control
- `ml_confidence_report.txt` is generated on each scorer run and is not committed to version control
