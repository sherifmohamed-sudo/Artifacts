# Plan2BoQ Preprocessing

Workspace for **floor plan PDF cleaning**, **DWG/DXF analysis**, optional **YOLO training data**, and unified batch processing.

## Where the code lives

All runnable pipelines are under:

**[`Plan2BoQ - PDF cleaning/`](Plan2BoQ%20-%20PDF%20cleaning/)**

## Quick start

```bash
cd "Plan2BoQ - PDF cleaning"
pip3 install -r requirements.txt
```

Put **PDF**, **DWG**, or **DXF** files in `unprocessed/`, then run **one** of:

| Command | Purpose |
|---------|---------|
| `python3 dispatch.py` | Route each file to PDF or CAD pipeline |
| `python3 process_all.py` | Same as `dispatch.py` |
| `python3 process_floor_plans.py` | PDFs only |
| `python3 process_cad.py` | DWG/DXF only |

Outputs go to `cleaned/`; originals move to `archived/`.

## Documentation

| Doc | Location |
|-----|----------|
| PDF + CAD overview, CLI details | [`Plan2BoQ - PDF cleaning/README.md`](Plan2BoQ%20-%20PDF%20cleaning/README.md) |
| Architecture / internals | [`Plan2BoQ - PDF cleaning/TECHNICAL_DESIGN.md`](Plan2BoQ%20-%20PDF%20cleaning/TECHNICAL_DESIGN.md) |
| Colab YOLO training | [`Plan2BoQ - PDF cleaning/ml/COLAB.md`](Plan2BoQ%20-%20PDF%20cleaning/ml/COLAB.md) |
| Step-by-step usage | [`USER_GUIDE.md`](USER_GUIDE.md) |

## Tests

```bash
cd "Plan2BoQ - PDF cleaning"
python3 -m pytest tests/ -q
```
