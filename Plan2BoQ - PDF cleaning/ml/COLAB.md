# Deploy YOLO training on Google Colab

Use **[train_yolo_colab.ipynb](train_yolo_colab.ipynb)** — it is tuned for Colab (GPU check, Drive, zip upload, download `best.pt`).

## Steps

1. **Prepare data locally** (optional but recommended)

   **From `unprocessed/`** — raster images (`.png`, `.jpg`, `.jpeg`, `.webp`) and/or **PDFs** (first page → PNG). DWG/DXF are not converted by this script.

   ```bash
   cd "Plan2BoQ - PDF cleaning"
   python3 ml/build_dataset_from_unprocessed.py --clean
   ```

   Or call `prepare_dataset()` yourself on any folder of rendered images.

   Annotate `labels/train/*.txt` and `labels/val/*.txt` in YOLO format (`class cx cy w h`, normalized 0–1). Classes: **0 = door**, **1 = window**.

2. **Zip the dataset folder** so the archive contains `data.yaml` at the top level or inside one root folder:

   ```
   floorplans_dataset.zip
     └── floorplans_dataset/   (or flat)
           data.yaml
           images/train/ ...
           images/val/ ...
           labels/train/ ...
           labels/val/ ...
   ```

3. **Open Colab**

   - Go to [colab.research.google.com](https://colab.research.google.com)
   - **File → Upload notebook** and upload `ml/train_yolo_colab.ipynb`  
     *or* upload the notebook from Drive after copying it there.

4. **Enable GPU**

   - **Runtime → Change runtime type → Hardware accelerator: GPU** → Save.

5. **Run cells top to bottom**

   - Cell 1: install `ultralytics`, confirm CUDA.
   - Cell 2: mount Drive if your zip lives on Drive.
   - Cell 3: set `DATASET_MODE`:
     - `upload_zip` — Colab prompts you to upload `floorplans_dataset.zip`.
     - `drive_zip` — set `DRIVE_ZIP_PATH` to your zip on Drive.
     - `drive_folder` — set `DRIVE_FOLDER_PATH` to an unzipped folder that contains `data.yaml`.
   - Training cell: lower **BATCH** to `4` or `2` if you see CUDA out-of-memory.
   - Export cell: downloads `best.pt` and optionally saves to `MyDrive/Plan2BoQ/best.pt`.

6. **Use the model locally**

   Copy `best.pt` to:

   ```
   Plan2BoQ - PDF cleaning/ml/best.pt
   ```

   Then run `process_cad.py` or `process_floor_plans.py`; ML detection runs when this file exists.

## Notes

- Free Colab GPUs vary (T4, L4, etc.). Training time depends on image count and `imgsz`.
- For a large dataset, prefer **Drive zip** instead of browser upload (faster, more reliable).
- The generic notebook `train_yolo.ipynb` is still valid for local Jupyter or AWS; use `train_yolo_colab.ipynb` for Colab.
