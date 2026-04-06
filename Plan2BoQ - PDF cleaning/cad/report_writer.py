"""
cad.report_writer
=================
Formats and saves the door/window detection results as:
  - <name>_cad_report.json  — full structured output (machine-readable)
  - <name>_cad_report.txt   — human-readable summary
  - <name>_cad_scores.csv   — pandas scoring table (one row per layer)

No PDF imports. No fitz.
Depends on: json, pathlib, stdlib, pandas (optional — CSV skipped if absent).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from cad.door_window_detector import LayerClassification


# ── Public API ─────────────────────────────────────────────────────────────────

def write_reports(
    results: Dict,
    source_file: str,
    output_dir: str,
    base_name: str,
) -> Dict[str, str]:
    """
    Write JSON and plain-text reports for one processed CAD file.

    Parameters
    ----------
    results     : dict returned by DoorWindowDetector.classify_all()
    source_file : original file path (for metadata)
    output_dir  : directory to write reports into
    base_name   : filename stem (e.g. "A-604_Ground_Floor")

    Returns
    -------
    {
      "json_path": "/path/to/<base_name>_cad_report.json",
      "txt_path":  "/path/to/<base_name>_cad_report.txt",
    }
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / f"{base_name}_cad_report.json"
    txt_path  = output_dir / f"{base_name}_cad_report.txt"
    csv_path  = output_dir / f"{base_name}_cad_scores.csv"

    _write_json(results, source_file, json_path)
    _write_txt(results, source_file, txt_path)
    _write_csv(results, csv_path)

    return {
        "json_path": str(json_path),
        "txt_path":  str(txt_path),
        "csv_path":  str(csv_path),
    }


# ── JSON writer ────────────────────────────────────────────────────────────────

def _write_json(results: Dict, source_file: str, path: Path) -> None:
    payload = {
        "meta": {
            "source_file":  source_file,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "pipeline":     "Plan2BoQ CAD Pipeline",
        },
        "summary": results["summary"],
        "door_layers":   [r.to_dict() for r in results["door_layers"]],
        "window_layers": [r.to_dict() for r in results["window_layers"]],
        "uncertain":     [r.to_dict() for r in results["uncertain"]],
        "all_layers":    [r.to_dict() for r in results["all_layers"]],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


# ── Plain-text writer ──────────────────────────────────────────────────────────

def _write_txt(results: Dict, source_file: str, path: Path) -> None:
    lines: List[str] = []
    w = lines.append  # shorthand

    _hdr = lambda title: (w(""), w("=" * 75), w(f"  {title}"), w("=" * 75))

    w("Plan2BoQ — CAD Layer Detection Report")
    w(f"Source : {source_file}")
    w(f"Created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    w(f"Signals: name | block_names | entity_types | count_ratio | coordinate_geometry")
    w("-" * 75)

    summary = results["summary"]
    w(f"Total layers scanned : {summary['total_layers']}")
    w(f"Door layers found    : {summary['door_count']}")
    w(f"Window layers found  : {summary['window_count']}")
    w(f"Uncertain / other    : {summary['uncertain_count']}")
    w(f"Confidence threshold : {summary['threshold']:.0%}")

    # ── Door layers ───────────────────────────────────────────────────────────
    _hdr("DOOR LAYERS")
    door_layers: List[LayerClassification] = results["door_layers"]
    if door_layers:
        for r in door_layers:
            w(f"\n  Layer  : {r.layer}")
            w(f"  Type   : {r.type.upper()}")
            w(f"  Score  : {r.score} / 130  ({r.confidence:.0%} confidence)")
            w(f"  Count  : {r.entity_count} entities")
            w(f"  Reason : {r.reason}")
            w(_signal_line(r))
    else:
        w("\n  No door layers detected above confidence threshold.")

    # ── Window layers ─────────────────────────────────────────────────────────
    _hdr("WINDOW LAYERS")
    window_layers: List[LayerClassification] = results["window_layers"]
    if window_layers:
        for r in window_layers:
            w(f"\n  Layer  : {r.layer}")
            w(f"  Type   : {r.type.upper()}")
            w(f"  Score  : {r.score} / 130  ({r.confidence:.0%} confidence)")
            w(f"  Count  : {r.entity_count} entities")
            w(f"  Reason : {r.reason}")
            w(_signal_line(r))
    else:
        w("\n  No window layers detected above confidence threshold.")

    # ── Uncertain (scored but below threshold) ────────────────────────────────
    _hdr("UNCERTAIN / UNCLASSIFIED LAYERS  (top 10 by score)")
    uncertain: List[LayerClassification] = sorted(
        results["uncertain"], key=lambda r: r.score, reverse=True
    )[:10]
    if uncertain:
        for r in uncertain:
            w(f"\n  Layer  : {r.layer}  (score {r.score}/130, {r.confidence:.0%})")
            w(f"  Count  : {r.entity_count} entities")
            if r.score > 0:
                w(f"  Reason : {r.reason}")
    else:
        w("\n  No uncertain layers.")

    # ── All layers quick table ─────────────────────────────────────────────────
    _hdr("ALL LAYERS — QUICK REFERENCE  (sorted by score)")
    all_layers: List[LayerClassification] = results["all_layers"]
    w("")
    w(f"  {'Layer':<40} {'Type':<10} {'Score':>6}  {'Conf':>6}  {'Entities':>8}")
    w(f"  {'-'*40} {'-'*10} {'-'*6}  {'-'*6}  {'-'*8}")
    for r in all_layers:
        w(
            f"  {r.layer:<40} {r.type:<10} {r.score:>6}  "
            f"{r.confidence:>5.0%}  {r.entity_count:>8}"
        )

    w("")
    w("=" * 75)
    w("End of report")
    w("=" * 75)

    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


# ── CSV writer (pandas) ────────────────────────────────────────────────────────

def _write_csv(results: Dict, path: Path) -> None:
    """
    Write a pandas scoring table (one row per layer) to a CSV file.
    Silently skipped if pandas is not installed.
    """
    try:
        import pandas as pd
    except ImportError:
        return

    rows = []
    for r in results["all_layers"]:
        sigs = r.signals
        nm   = sigs.get("name_match",          {})
        bn   = sigs.get("block_names",         {})
        et   = sigs.get("entity_types",        {})
        gr   = sigs.get("geometry_ratio",      {})
        cg   = sigs.get("coordinate_geometry", {})

        rows.append({
            "layer":               r.layer,
            "type":                r.type,
            "score":               r.score,
            "confidence_pct":      round(r.confidence * 100, 1),
            "entity_count":        r.entity_count,
            "sig_name_door":       nm.get("door_score",   0),
            "sig_name_window":     nm.get("window_score", 0),
            "sig_name_match":      nm.get("matched", ("", ""))[0] if nm.get("matched") else "",
            "sig_block_door":      bn.get("door_score",   0),
            "sig_block_window":    bn.get("window_score", 0),
            "sig_block_samples":   "|".join(bn.get("samples", [])[:3]),
            "sig_entity_door":     et.get("door_score",   0),
            "sig_entity_window":   et.get("window_score", 0),
            "sig_arc_count":       et.get("arc",    0),
            "sig_insert_count":    et.get("insert", 0),
            "sig_line_count":      et.get("line",   0),
            "sig_ratio_door":      gr.get("door_score",   0),
            "sig_ratio_window":    gr.get("window_score", 0),
            "arc_ratio":           gr.get("arc_ratio",    0),
            "insert_ratio":        gr.get("insert_ratio", 0),
            "sig_geo_door":        cg.get("door_score",   0),
            "sig_geo_window":      cg.get("window_score", 0),
            "geo_arc_analyzed":    cg.get("arc_count",      0),
            "geo_poly_analyzed":   cg.get("polyline_count", 0),
        })

    df = pd.DataFrame(rows)
    df = df.sort_values("score", ascending=False)
    df.to_csv(path, index=False, encoding="utf-8")


# ── Helper ─────────────────────────────────────────────────────────────────────

def _signal_line(r: LayerClassification) -> str:
    """Format a one-line signal breakdown for text output."""
    sigs = r.signals
    parts = []

    nm = sigs.get("name_match", {})
    if nm.get("door_score", 0) + nm.get("window_score", 0) > 0:
        parts.append(f"name={max(nm.get('door_score',0), nm.get('window_score',0))}")

    bn = sigs.get("block_names", {})
    if bn.get("door_score", 0) + bn.get("window_score", 0) > 0:
        samples = bn.get("samples", [])
        parts.append(f"blocks={max(bn.get('door_score',0), bn.get('window_score',0))} {samples[:3]}")

    et = sigs.get("entity_types", {})
    if et.get("door_score", 0) + et.get("window_score", 0) > 0:
        parts.append(f"entities={max(et.get('door_score',0), et.get('window_score',0))}")

    gr = sigs.get("geometry_ratio", {})
    if gr.get("door_score", 0) + gr.get("window_score", 0) > 0:
        parts.append(
            f"ratio={max(gr.get('door_score',0), gr.get('window_score',0))} "
            f"[arc={gr.get('arc_ratio', 0):.0%}, ins={gr.get('insert_ratio', 0):.0%}]"
        )

    cg = sigs.get("coordinate_geometry", {})
    if cg.get("door_score", 0) + cg.get("window_score", 0) > 0:
        parts.append(
            f"geo={max(cg.get('door_score',0), cg.get('window_score',0))} "
            f"[arcs={cg.get('arc_count',0)}, polys={cg.get('polyline_count',0)}]"
        )

    return f"  Signals: {' | '.join(parts)}" if parts else ""
