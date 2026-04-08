"""
ml.render
=========
Renders PDF and DWG floor plan files to high-resolution PNG images
suitable for YOLOv8 training and inference.

PDF  -> PyMuPDF (fitz) rasterisation
DWG  -> ezdwg raw entity decoding + matplotlib rendering
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

# Target long-edge size for YOLO images (pixels)
_DEFAULT_LONG_EDGE = 1280


# ── Public API ────────────────────────────────────────────────────────────────

def render_to_image(
    input_path: str | Path,
    output_path: str | Path,
    long_edge: int = _DEFAULT_LONG_EDGE,
    dpi: int = 300,
) -> Path:
    """
    Render a PDF or DWG floor plan to a high-res PNG.

    Parameters
    ----------
    input_path  : path to PDF or DWG file
    output_path : where to save the PNG
    long_edge   : resize so the longest side is this many pixels (0 = no resize)
    dpi         : DPI for PDF rasterisation (ignored for DWG)

    Returns
    -------
    Path to the saved PNG file.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    ext = input_path.suffix.lower()
    if ext == ".pdf":
        _render_pdf(input_path, output_path, dpi=dpi, long_edge=long_edge)
    elif ext == ".dwg":
        _render_dwg(input_path, output_path, long_edge=long_edge)
    elif ext == ".dxf":
        _render_dxf(input_path, output_path, long_edge=long_edge)
    else:
        raise ValueError(f"Unsupported file type: {ext}")

    return output_path


# ── PDF renderer ──────────────────────────────────────────────────────────────

def _render_pdf(
    pdf_path: Path, out_path: Path, dpi: int, long_edge: int,
) -> None:
    import fitz

    doc = fitz.open(str(pdf_path))
    page = doc[0]

    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)

    if long_edge > 0:
        pix = _resize_pixmap(pix, long_edge)

    pix.save(str(out_path))
    doc.close()


def _resize_pixmap(pix, long_edge: int):
    """Resize a fitz.Pixmap so its longest side equals *long_edge*."""
    import fitz
    import io
    from PIL import Image

    w, h = pix.width, pix.height
    if max(w, h) <= long_edge:
        return pix

    scale = long_edge / max(w, h)
    new_w = int(w * scale)
    new_h = int(h * scale)

    img = Image.frombytes("RGB", (w, h), pix.samples)
    img = img.resize((new_w, new_h), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return fitz.Pixmap(buf.getvalue())


# ── DWG renderer ──────────────────────────────────────────────────────────────

def _render_dwg(
    dwg_path: Path, out_path: Path, long_edge: int,
) -> None:
    """Render DWG entities (lines, arcs, circles) via matplotlib."""
    import ezdwg.raw as raw_mod
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches
    import numpy as np

    lines, arcs, circles = raw_mod.decode_line_arc_circle_entities(str(dwg_path))

    fig, ax = plt.subplots(1, 1, figsize=(16, 12), dpi=150)
    ax.set_facecolor("white")
    ax.set_aspect("equal")

    drawn = 0

    # Draw lines: (handle, x1, y1, z1, x2, y2, z2)
    for ln in lines:
        if len(ln) < 7:
            continue
        x1, y1, x2, y2 = float(ln[1]), float(ln[2]), float(ln[4]), float(ln[5])
        if not all(_finite_sane(v) for v in (x1, y1, x2, y2)):
            continue
        ax.plot([x1, x2], [y1, y2], color="black", linewidth=0.3)
        drawn += 1

    # Draw arcs: (handle, cx, cy, cz, radius, start_angle_rad, end_angle_rad)
    for arc in arcs:
        if len(arc) < 7:
            continue
        cx, cy = float(arc[1]), float(arc[2])
        radius = float(arc[4])
        start_a, end_a = float(arc[5]), float(arc[6])
        if not all(_finite_sane(v) for v in (cx, cy, radius, start_a, end_a)):
            continue
        if radius <= 0 or radius > 1e6:
            continue
        # Reject arcs with absurd angle values (xref garbage)
        if abs(start_a) > 1e4 or abs(end_a) > 1e4:
            continue
        theta1 = math.degrees(start_a) if abs(start_a) < 100 else start_a
        theta2 = math.degrees(end_a) if abs(end_a) < 100 else end_a
        if abs(theta2 - theta1) < 0.01:
            continue
        try:
            arc_patch = patches.Arc(
                (cx, cy), 2 * radius, 2 * radius,
                angle=0, theta1=theta1, theta2=theta2,
                color="black", linewidth=0.3,
            )
            ax.add_patch(arc_patch)
            drawn += 1
        except (ValueError, OverflowError):
            continue

    # Draw circles: (handle, cx, cy, cz, radius)
    for circ in circles:
        if len(circ) < 5:
            continue
        cx, cy, radius = float(circ[1]), float(circ[2]), float(circ[4])
        if not all(_finite_sane(v) for v in (cx, cy, radius)):
            continue
        if radius <= 0 or radius > 1e6:
            continue
        circle_patch = patches.Circle(
            (cx, cy), radius, fill=False, color="black", linewidth=0.3,
        )
        ax.add_patch(circle_patch)
        drawn += 1

    if drawn == 0:
        ax.text(0.5, 0.5, "No renderable geometry",
                ha="center", va="center", transform=ax.transAxes,
                fontsize=14, color="gray")

    ax.autoscale_view()
    ax.axis("off")
    plt.tight_layout(pad=0)
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight",
                facecolor="white", pad_inches=0.1)
    plt.close(fig)

    if long_edge > 0:
        _resize_png(out_path, long_edge)


# ── DXF renderer ──────────────────────────────────────────────────────────────

def _render_dxf(
    dxf_path: Path, out_path: Path, long_edge: int,
) -> None:
    """Render DXF entities via ezdxf + matplotlib."""
    import ezdxf
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches

    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()

    fig, ax = plt.subplots(1, 1, figsize=(16, 12), dpi=150)
    ax.set_facecolor("white")
    ax.set_aspect("equal")

    drawn = 0

    for entity in msp:
        dtype = entity.dxftype()

        if dtype == "LINE":
            start = entity.dxf.get("start", (0, 0, 0))
            end = entity.dxf.get("end", (0, 0, 0))
            ax.plot([start[0], end[0]], [start[1], end[1]],
                    color="black", linewidth=0.3)
            drawn += 1

        elif dtype == "ARC":
            cx = entity.dxf.get("center", (0, 0, 0))
            r = entity.dxf.get("radius", 0)
            s = entity.dxf.get("start_angle", 0)
            e = entity.dxf.get("end_angle", 0)
            if r > 0:
                arc_patch = patches.Arc(
                    (cx[0], cx[1]), 2 * r, 2 * r,
                    angle=0, theta1=s, theta2=e,
                    color="black", linewidth=0.3,
                )
                ax.add_patch(arc_patch)
                drawn += 1

        elif dtype == "CIRCLE":
            cx = entity.dxf.get("center", (0, 0, 0))
            r = entity.dxf.get("radius", 0)
            if r > 0:
                circle_patch = patches.Circle(
                    (cx[0], cx[1]), r, fill=False,
                    color="black", linewidth=0.3,
                )
                ax.add_patch(circle_patch)
                drawn += 1

        elif dtype == "LWPOLYLINE":
            try:
                pts = list(entity.get_points(format="xy"))
                if len(pts) >= 2:
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    if entity.closed:
                        xs.append(xs[0])
                        ys.append(ys[0])
                    ax.plot(xs, ys, color="black", linewidth=0.3)
                    drawn += 1
            except Exception:
                pass

    if drawn == 0:
        ax.text(0.5, 0.5, "No renderable geometry",
                ha="center", va="center", transform=ax.transAxes,
                fontsize=14, color="gray")

    ax.autoscale_view()
    ax.axis("off")
    plt.tight_layout(pad=0)
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight",
                facecolor="white", pad_inches=0.1)
    plt.close(fig)

    if long_edge > 0:
        _resize_png(out_path, long_edge)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _finite_sane(v: float, limit: float = 1e8) -> bool:
    return math.isfinite(v) and abs(v) < limit


def _resize_png(path: Path, long_edge: int) -> None:
    """Resize an existing PNG on disk so its longest side = long_edge."""
    from PIL import Image

    img = Image.open(str(path))
    w, h = img.size
    if max(w, h) <= long_edge:
        return

    scale = long_edge / max(w, h)
    new_w, new_h = int(w * scale), int(h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    img.save(str(path))
