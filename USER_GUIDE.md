# User guide

## 1. Install

```bash
cd "Plan2BoQ - PDF cleaning"
pip3 install -r requirements.txt
```

## 2. Add files

Copy floor plans into:

```
Plan2BoQ - PDF cleaning/unprocessed/
```

Supported for batch processing: **`.pdf`**, **`.dwg`**, **`.dxf`**.

(You can also place **`.png` / `.jpg` / `.webp`** here only if you are building a YOLO dataset — see below — they are not processed by `dispatch.py`.)

## 3. Run processing

From `Plan2BoQ - PDF cleaning/`:

```bash
python3 dispatch.py
# or
python3 process_all.py
```

Options:

- `python3 dispatch.py --pdf-only` — only PDF pipeline
- `python3 dispatch.py --cad-only` — only CAD pipeline

## 4. Outputs

| Location | Contents |
|----------|----------|
| `cleaned/` | Cleaned PDFs, previews, CAD/ML reports |
| `archived/` | Original inputs after a successful run |
| `processing_report.txt` | PDF batch summary |
| `cad_processing_report.txt` | CAD batch summary |

## 5. YOLO dataset (optional)

To turn **PDFs or images** in `unprocessed/` into a training dataset (renders PDF first page to PNG):

```bash
python3 ml/build_dataset_from_unprocessed.py --clean
```

Dataset root: `ml/datasets/floorplans/` (see `ml/COLAB.md` for Colab training).

## 6. More detail

See **`Plan2BoQ - PDF cleaning/README.md`** and **`TECHNICAL_DESIGN.md`**.
