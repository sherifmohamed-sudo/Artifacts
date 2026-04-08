# Plan2BoQ — Technical Design Document

> **Purpose:** This document describes the internal architecture, data flow, algorithms, and design decisions behind the Plan2BoQ floor plan PDF preprocessing system.

---

## 1. Problem Statement

Architectural floor plan PDFs produced by CAD tools (AutoCAD / Revit) contain hundreds of layers of information: walls, doors, windows, furniture, hatching, grid lines, annotations, MEP systems, title blocks, and more. A downstream OCR / computer-vision model that counts doors and windows is confused by this noise.

**Goal:** Produce a clean, minimal PDF that contains *only* the elements needed to count doors and windows, with door/window tags and dimension values visually distinguished in pure red so they can be isolated by color channel during OCR.

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        ENTRY POINT                              │
│                  process_floor_plans.py                         │
│  • Scans unprocessed/ for PDFs                                  │
│  • Orchestrates the pipeline per file                           │
│  • Generates before/after PNG previews                          │
│  • Writes processing_report.txt                                 │
│  • Archives originals to archived/                              │
└────────────────────┬────────────────────────────────────────────┘
                     │  calls
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                     CLEANING ENGINE                             │
│                   clean_floor_plan.py                           │
│                                                                 │
│  Step 1 │ OCG layer visibility flags                            │
│  Step 2 │ BDC/EMC content-stream stripping                      │
│  Step 3 │ Untagged content stripping (hatching, fills)          │
│  Step 4 │ Room number text stripping                            │
│  Step 5 │ Red colour normalisation                              │
│  Step 6 │ Bold + red label enhancement                          │
└────────────────────┬────────────────────────────────────────────┘
                     │  audit (standalone)
                     ▼
┌─────────────────────────────────────────────────────────────────┐
│                   CONFIDENCE SCORER (audit tool)                │
│                   ml_confidence_scorer.py                       │
│  • Scores every layer-removal decision with 0–100% confidence  │
│  • Produces ml_confidence_report.txt for QA review             │
└─────────────────────────────────────────────────────────────────┘
```

### File Roles

| File | Role | Importable? |
|------|------|-------------|
| `process_floor_plans.py` | Workflow orchestrator — run this | No (entry point) |
| `clean_floor_plan.py` | Core PDF mutation engine | Yes (`from clean_floor_plan import clean_floor_plan`) |
| `ml_confidence_scorer.py` | Audit-only confidence scorer | Yes (`from ml_confidence_scorer import ConfidenceScorer`) |

### Folder Structure

```
Plan2BoQ - PDF cleaning/
│
├── unprocessed/    ← Drop raw PDFs here (input)
├── cleaned/        ← Cleaned PDFs + PNG previews (output)
└── archived/       ← Originals archived here after processing
```

---

## 3. PDF Internals — Background

Understanding how the cleaning engine operates requires knowledge of how AutoCAD-exported PDFs are structured.

### 3.1 Optional Content Groups (OCG / Layers)

AutoCAD exports each drawing layer as an **Optional Content Group (OCG)**. Each OCG has:

- A **name** (e.g., `A-DOOR-TAG`, `A_A_STAIR`, `A_L_HAT_WALL`)
- An **xref number** (integer ID within the PDF object table)
- A **visibility flag** (`ON` / `OFF`) that PDF viewers respect

OCGs are declared in the PDF catalog under `/OCProperties`. The system reads all OCGs using `fitz.Document.get_layers()`.

### 3.2 Content Streams and BDC/EMC Blocks

Each page stores its drawing commands in one or more **content streams** (raw byte sequences of PDF operators). When a drawing belongs to a specific OCG, it is wrapped in a **BDC/EMC block**:

```
/oc422 BDC          ← Begin marked content for OCG with tag /oc422
  ... drawing commands (moveto, lineto, stroke, fill, etc.) ...
EMC                 ← End marked content
```

The tag `/oc422` maps to an OCG xref via the page's `/Resources` → `/Properties` dictionary:

```
/Resources << /Properties << /oc422 422 0 R >> >>
```

### 3.3 Vectorized Text

In AutoCAD PDFs, **text is almost never stored as actual text objects** (`Tj` / `TJ` operators). Instead, it is converted to **geometric paths** — a series of `m` (moveto), `l` (lineto), `c` (curveto), `S` (stroke), `f` (fill) operators that trace the outline of each character. This has two important consequences:

1. Standard PDF text-extraction tools (`get_text()`) return nothing useful for these elements.
2. Making labels "bold" requires widening the **stroke width** (`w` operator), not changing a font weight.

### 3.4 PDF Color Operators

| Operator | Scope | Example |
|----------|-------|---------|
| `rg` | Fill color (RGB) | `1 0 0 rg` → red fill |
| `RG` | Stroke color (RGB) | `1 0 0 RG` → red stroke |
| `w` | Line/stroke width | `10 w` → 10 units wide |
| `q` / `Q` | Save / restore graphics state | Isolates state changes |

---

## 4. Processing Pipeline — Step by Step

### Step 1 — OCG Layer Visibility Flags

**Function:** `_apply_layer_visibility(doc, page)`

**What it does:**
- Iterates over all OCGs in the document via `doc.get_layers()`
- For each OCG, calls `layer_should_remove(name)` to decide keep/remove
- Sets the OCG's visibility flag to `OFF` for removals using `doc.set_layer(xref, on=False)`

**Effect:** PDF viewers (Acrobat, browsers) will not render hidden layers. This alone produces a visually clean output without touching the actual stream bytes.

**Why this is not enough alone:** OCG flags are viewer hints. Some renderers and all rasterizers (including PyMuPDF's own `get_pixmap()`) ignore OFF-flagged content unless the content streams are also physically stripped (Step 2).

---

### Step 2 — BDC/EMC Content-Stream Stripping

**Function:** `_strip_tagged_content(doc, page)`

**What it does:**

1. Builds a set of tag names (`/oc422`, `/oc317`, …) whose OCGs are marked for removal — this is `_build_tag_removal_set()`.
2. Reads each raw content stream via `doc.xref_stream(xref)` (returns compressed bytes).
3. Decodes the bytes with `latin-1` (preserves all byte values 0–255 without loss).
4. Applies a regex to find and delete entire BDC/EMC blocks whose tag matches the removal set:

```python
re.sub(
    r"/oc(\d+)\s+BDC.*?EMC",
    lambda m: "" if f"/oc{m.group(1)}" in removal_tags else m.group(0),
    content,
    flags=re.DOTALL
)
```

5. Re-encodes the cleaned content and writes it back via `doc.update_stream(xref, bytes)`.

**Why DOTALL is critical:** The `.*?` must cross newlines because BDC blocks span many lines of drawing commands.

---

### Step 3 — Untagged Content Stripping

**Function:** `_strip_untagged_patterns(doc, page)`

**What it does:**

Some hatching and fill geometry is written **directly into the page stream without any BDC/EMC wrapper** (untagged content). This step targets those regions by matching known-harmful drawing sequences using regex patterns:

| Target | Regex approach |
|--------|---------------|
| Hatch fill blocks | Sequences ending in `f` or `f*` (fill operators) with no stroke |
| Solid colour fills | `rg` / `RG` followed immediately by `re f` (rectangle fill) |
| FFL/SSL level text geometry | Specific vectorized path sequences embedded without tags |

The regex operates on the same decoded content stream from Step 2.

---

### Step 4 — Room Number Text Stripping

**Function:** `_strip_room_number_text(doc, page)`

**What it does:**

Room numbers (01–26), floor indicators (`G`, `UP`, `DOWN`) are sometimes embedded **inside door/window layers** (e.g., `A_A_DOOR_HID`) as actual text `Tj` operators rather than vectorized paths. Because they reside inside a KEEP layer, Step 2 cannot remove them.

This function surgically removes only those specific text-show operations:

```python
re.sub(r'\((0?[0-9]{1,2}|UP|DOWN|G)\)\s*Tj', '', content)
```

**Why safe:** The regex matches only 1-2 digit numbers, `UP`, `DOWN`, and `G`. Door/window dimension values are 3-4+ digit numbers (e.g., `1345`, `2100`) and are not affected.

---

### Step 5 — Red Colour Normalisation

**Function:** `_strip_red_colors(doc, page)`

**What it does:**

Some AutoCAD layers use red (`1 0 0 rg` / `1 0 0 RG`) for revision markup, fire exits, and other annotations. Since Step 6 will apply pure red to door/window tags (to make them OCR-identifiable), any pre-existing red must be converted to black first to avoid false positives.

```python
re.sub(r'1\.?0*\s+0\.?0*\s+0\.?0*\s+rg\b', '0 0 0 rg', content)
re.sub(r'1\.?0*\s+0\.?0*\s+0\.?0*\s+RG\b', '0 0 0 RG', content)
```

**Key implementation note:** The replacement string is the same length as the original. The update guard must compare content equality (`if content != original_content`), not byte length, or the change is silently discarded.

**Order dependency:** This step MUST run before Step 6. Step 5 clears all existing red; Step 6 then injects clean red only on target layers. If run in reverse order, Step 5 would erase the labels we just coloured.

---

### Step 6 — Bold + Red Label Enhancement

**Function:** `_make_labels_bold(doc, page)`

**What it does:**

Identifies BDC blocks by OCG layer name and applies **door vs window colour coding**:
- **Door-related** layers (door tags, `A_A_DOOR`, `A-DOOR`, `d-tag`, `dr-`, etc.) and **dimension** layers → pure red `1 0 0` RG/rg
- **Window-related** layers (wind tags, `window`, `glaz`, `a-win`, `w-tag`, `win-`, and `wind` when not a door layer) → orange `1 0.55 0` RG/rg

For each matching BDC block, wraps the inner content with a graphics state push/pop that sets:
- **Stroke and fill** to the tier colour (red or orange as above)
- **Stroke width** to 10 units (≈ 1.7 pt at AutoCAD PDF scale): `10 w`

```
/oc422 BDC
q
1 0 0 RG          ← red stroke (for vectorized path outlines)
1 0 0 rg          ← red fill   (for any filled elements)
10 w              ← bold stroke width
  ... original drawing commands ...
Q
EMC
```

**Scale calculation:**

AutoCAD PDFs use an internal coordinate unit where 1 unit ≈ 0.1685 pt (verified by measuring a known 1-pt line in a reference drawing).

| Stroke width | Points | Visual result |
|-------------|--------|---------------|
| 1 unit (original) | 0.17 pt | Hairline — barely visible |
| 1.8 units | 0.30 pt | Slightly thicker |
| **10 units** | **1.69 pt** | **Clear bold — OCR-readable** |

**OCR benefit:** After this step, door/window tags (e.g., `GD07`, `WD06`) and dimension values (e.g., `1400 × 2100`) are rendered in bold pure red. An OCR pipeline can:
1. Isolate the red channel → extract only tags and dimensions
2. Use the black/grey channel → extract spatial/geometry context

---

## 5. Layer Decision Engine

### `layer_should_remove(name: str) → bool`

The core decision function uses a **3-tier priority system**:

```
Priority 1 — EXPLICIT KEEP (always wins)
  ├─ Door:    'door', 'dr-', 'a-door', 'a_a_door', 'door-tag', 'd-tag'
  ├─ Window:  'window', 'wind', 'win-', 'glaz', 'a-win', 'wind-tag', 'w-tag'
  ├─ Wall:    'wall' (unless 'landscape' or 'l-lo-' also present)
  ├─ Boundary:'room' + ('bound' or 'line' or 'outline')
  └─ Symbol:  'symb' (unless also 'anno' or 'tag')
       ↓ no match
Priority 2 — EXPLICIT REMOVE (pattern list)
  └─ Matches any entry in REMOVE_PATTERNS → remove
       ↓ no match
Priority 3 — DEFAULT KEEP
  └─ Return False (keep the layer)
```

**Design principle:** "When in doubt, keep it." An over-cleaned drawing is worse than a slightly cluttered one for a downstream vision model.

### REMOVE_PATTERNS categories

| Category | Representative patterns |
|----------|------------------------|
| Hatching | `HATCH`, `HACH`, `-HAT-`, `_HATCH` |
| Furniture | `FURN`, `MOBILIER`, `EQPM` |
| Grid lines | `GRID`, `AXES GRID`, `_AXS`, `_GRID` |
| Annotations | `CLOUD`, `TBLOCK`, `STAMP`, `LTAG`, `ROOM-TAG` |
| Area text | `A_TEXT`, `A-TEXT`, `45-TEXTS`, `MTEXT` |
| Level markers | `FFL`, `SSL`, `ANNO-LEVL`, `ELEV-MARKER` |
| Stairs / circulation | `STAIR`, `HANDRAIL`, `STEPS`, `LIFT`, `RAMP` |
| Site / landscape | `LANDSCAPE`, `TREE`, `SITE`, `AREA`, `L-PL-AREA` |
| Parking / roads | `PARKING`, `ROAD`, `ARROW` |
| MEP systems | `MEP`, `DUCT`, `PLUMBING` |
| Dimensions (grid only) | `GRID DIM`, `COLUMN-DIM`, `LIFT-DIM` |

---

## 6. ML-Style Confidence Scorer

`ml_confidence_scorer.py` is an **audit companion** — it does not modify PDFs. It replicates the same keep/remove logic as the cleaning engine but outputs a human-readable report with confidence percentages.

### Architecture of `ConfidenceScorer`

```
ConfidenceScorer
├── __init__()
│   ├── self.removal_patterns  — dict of {category: {patterns, confidence}}
│   └── self.keep_patterns     — dict of {category: {patterns, confidence=1.0}}
│
├── analyze_layer_name(name) → (should_remove, reason, confidence, details)
│   ├── 1. Check keep_patterns first (Tier 1, confidence=1.0)
│   │      Note: 'window' is checked before 'door' to prevent substring
│   │      collision ('wind-tag' contains 'd-tag', a door substring)
│   ├── 2. Check removal_patterns (Tier 2, confidence 0.80–0.95)
│   └── 3. Default KEEP at confidence=0.30 (Tier 3, uncertain)
│
└── analyze_pdf(pdf_path) → {remove: [...], keep: [...], uncertain: int}
    └── Calls analyze_layer_name() for each OCG in the document
```

### Confidence weight design

Confidence weights are calibrated to the **certainty** of the pattern match, not the importance of the element:

- **1.00 (100%)** — Essential elements: a layer named `A-DOOR-TAG` can only ever be a door tag
- **0.95 (95%)** — Very safe removals: `HATCH`, `STAIR`, `FURN` — these never contain door/window data
- **0.90 (90%)** — Safe removals: `GRID`, `LANDSCAPE` — small risk of false positives
- **0.85 (85%)** — Moderate confidence: `PARKING`, `ANNO` — occasional edge cases
- **0.80 (80%)** — Lower confidence: `MEP` — some MEP layers touch door/window surrounds
- **0.30 (30%)** — Uncertain (default KEEP) — pattern not recognised; safer to keep

---

## 7. Data Flow Diagram

```
Raw PDF (unprocessed/)
        │
        ▼
┌───────────────────┐
│  fitz.open(pdf)   │  Load document into memory
└────────┬──────────┘
         │
         ▼
┌───────────────────┐
│  get_layers()     │  Read all OCG names and xrefs
│  layer_should_    │  Decision: keep or remove per layer
│  remove(name)     │
└────────┬──────────┘
         │
         ├─── Step 1: set_layer(xref, on=False)   → visibility flags
         │
         ├─── Step 2: BDC/EMC regex strip          → remove unwanted stream blocks
         │
         ├─── Step 3: Untagged regex strip          → remove untagged hatching/fills
         │
         ├─── Step 4: Room number Tj strip          → remove embedded room numbers
         │
         ├─── Step 5: Red colour → black            → neutralise existing red markup
         │
         └─── Step 6: Red + bold on tag/dim layers  → highlight for OCR
                  │
                  ▼
         ┌────────────────┐
         │  doc.save()    │  Write cleaned PDF to cleaned/
         └────────┬───────┘
                  │
                  ├── get_pixmap() → PNG preview (before + after)
                  └── shutil.move() → archive original
```

---

## 8. Key Implementation Decisions

### 8.1 Why `latin-1` encoding for content streams?

PDF content streams are binary sequences using byte values 0–255. Python's `latin-1` codec maps each byte 1:1 to a Unicode code point, making it the only lossless codec for round-trip decoding/re-encoding of arbitrary byte sequences. Using `utf-8` would fail on bytes > 127.

### 8.2 Why `re.DOTALL` for BDC/EMC matching?

BDC blocks span multiple lines. Without `DOTALL`, `.` does not match `\n`, so `.*?` would stop at the first newline and leave the block partially stripped — creating invalid PDF syntax that may crash viewers.

### 8.3 Why `doc.update_stream` instead of reconstructing the file?

`fitz.Document.update_stream(xref, new_bytes)` replaces the byte payload of a single object in the cross-reference table. PyMuPDF handles compression and cross-reference updates automatically. This is much faster and safer than re-serialising the entire document object graph.

### 8.4 Why not use `.dwg` source files?

The project initially explored processing native AutoCAD `.dwg` files. This was abandoned because:
1. DWG is a proprietary binary format with no reliable open-source parser
2. DWG→PDF export settings vary per version and operator, making geometry unreliable
3. The architectural office delivers PDFs as the canonical deliverable format

### 8.5 Why a rule-based scorer instead of a trained ML model?

Three constraints drove this decision:
1. **Speed:** A trained model (e.g., CLIP or fine-tuned BERT on layer names) would require GPU inference. The rule-based scorer completes in < 1 second.
2. **Data:** No labelled training dataset of AutoCAD layer names exists. Building one would require weeks of annotation.
3. **Interpretability:** The rule-based scorer produces human-readable reasons (`"Unwanted: hatching"`, `"Essential: door"`). A neural model would produce opaque scores.

The rule-based approach achieves ≥ 90% of layer decisions at ≥ 80% confidence, which is comparable to the accuracy a small trained model would achieve on this domain.

### 8.6 Why pure red for OCR highlighting?

The RGB color model decouples red completely from green and blue channels. A downstream OCR pipeline can:

```python
import cv2
img = cv2.imread("cleaned.png")
# Isolate red: high R, low G, low B
red_mask = (img[:,:,2] > 200) & (img[:,:,1] < 50) & (img[:,:,0] < 50)
```

This gives a binary mask of only the door/window tags and dimension values, with zero interference from walls or door swing arcs (which are black/grey).

---

## 9. Performance Characteristics

| Metric | Typical value | Notes |
|--------|--------------|-------|
| Processing time per PDF | 4–5 seconds | Single core, no GPU |
| File size reduction | 35–55% | Varies with density of removed content |
| Layers removed | 60–100 per file | ~46% of all layers |
| Scorer decisions ≥ 80% confidence | ~90% | Remaining 10% default to KEEP |
| Peak memory usage | ~150 MB | Dominated by `fitz` in-memory document |

---

## 10. Extension Points

| Extension | Where to modify |
|-----------|----------------|
| Add a new removal pattern | `REMOVE_PATTERNS` list in `process_floor_plans.py` and `clean_floor_plan.py` |
| Change the bold/red colour | `RED` and `BOLD_WIDTH` constants in `_make_labels_bold()` |
| Process multi-page PDFs | Change `page = doc[0]` to a loop over `doc.pages()` |
| Change output preview zoom | `zoom` parameter in `render_preview()` |
| Adjust confidence thresholds | `self.removal_patterns` confidence values in `ConfidenceScorer.__init__()` |
| Add a new keep pattern | STEP 1 block in `layer_should_remove()` |

---

## 11. Dependencies

| Library | Version | Usage |
|---------|---------|-------|
| `pymupdf` (`fitz`) | ≥ 1.27.0 | All PDF I/O, OCG control, content stream access, PNG rendering |
| `Pillow` | ≥ 10.0.0 | PNG preview post-processing and saving |
| `re` (stdlib) | — | Regex-based content stream manipulation |
| `os`, `shutil`, `pathlib` (stdlib) | — | File system operations |
| `datetime` (stdlib) | — | Report timestamping |
| `typing` (stdlib) | — | Type annotations |

---

*Document version: 1.0 — reflects codebase state as of March 2026*
