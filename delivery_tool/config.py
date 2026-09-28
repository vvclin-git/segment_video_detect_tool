from __future__ import annotations

import json
import math
import re
from pathlib import Path

from .video_naming import DEFAULT_VIDEO_FILENAME_TEMPLATE


DEFAULT_CONFIG = {
    "schema_version": 1,
    "test_dates": [],
    "batch_id": "",
    "projects": [],
    "pairings": [],
    "evaluation_csvs": [],
    "attachment_index": "",
    "attachment_root": "",
    "image_root": "",
    "mask_root": "",
    "video_root": "",
    "output_dir": "",
    "include_videos": False,
    "video_filename_template": DEFAULT_VIDEO_FILENAME_TEMPLATE,
    "include_frame_charts": True,
    "project_assets_roots": {},
    "pairing_run_id_map": {},
    "event_links": {},
    "report": {
        "pdf_layout": "overview",
        "report_name": "海試結果報告",
        "customer_project": "",
        "version": "1.0",
        "logo": "",
        "scope": "",
        "notes": "",
        "cover": True,
        "summary_table": True,
        "missing_appendix": False,
        "font_path": "",
        "comparison_overlay": {
            "opacity": 0.65,
            "colors": {
                "overlap": "#1EEB5A",
                "gt_only": "#FF282D",
                "prediction_only": "#14D2FF",
            },
        },
    },
}

DEFAULT_COMPARISON_OVERLAY = DEFAULT_CONFIG["report"]["comparison_overlay"]
_COMPARISON_COLOR_FIELDS = ("overlap", "gt_only", "prediction_only")


def comparison_overlay_settings(config_or_settings: dict | None = None) -> dict:
    """Return validated, field-by-field comparison overlay settings.

    Accepts either a full config, a report object, or the comparison_overlay
    object itself. Missing fields inherit their defaults independently.
    """
    source = {} if config_or_settings is None else config_or_settings
    if not isinstance(source, dict):
        raise ValueError("report.comparison_overlay 必須是 JSON 物件")
    report = source.get("report", source)
    if not isinstance(report, dict):
        raise ValueError("report 必須是 JSON 物件")
    settings = report.get("comparison_overlay", report)
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise ValueError("report.comparison_overlay 必須是 JSON 物件")
    opacity = settings.get("opacity", DEFAULT_COMPARISON_OVERLAY["opacity"])
    try:
        valid_opacity = (not isinstance(opacity, bool) and isinstance(opacity, (int, float))
                         and math.isfinite(float(opacity)) and 0 <= opacity <= 1)
    except (OverflowError, TypeError, ValueError):
        valid_opacity = False
    if not valid_opacity:
        raise ValueError("report.comparison_overlay.opacity 必須是 0–1 的有限數值")
    incoming_colors = settings.get("colors", {})
    if incoming_colors is None:
        incoming_colors = {}
    if not isinstance(incoming_colors, dict):
        raise ValueError("report.comparison_overlay.colors 必須是 JSON 物件")
    colors = {}
    for key in _COMPARISON_COLOR_FIELDS:
        value = incoming_colors.get(key, DEFAULT_COMPARISON_OVERLAY["colors"][key])
        if not isinstance(value, str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            raise ValueError(f"report.comparison_overlay.colors.{key} 必須是 #RRGGBB")
        colors[key] = value.upper()
    return {"opacity": float(opacity), "colors": colors}


def load_config(path: str | Path) -> dict:
    path = Path(path).expanduser().resolve()
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("設定檔必須是 schema_version 1 的 JSON 物件")
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update(data)
    report = dict(DEFAULT_CONFIG["report"])
    report.update(data.get("report") or {})
    report["comparison_overlay"] = comparison_overlay_settings({"report": report})
    merged["report"] = report
    merged["_config_path"] = str(path)
    merged["_config_dir"] = str(path.parent)
    return merged


def save_config(path: str | Path, data: dict) -> Path:
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in data.items() if not k.startswith("_")}
    report = clean.get("report", {})
    if report is None:
        report = {}
    if not isinstance(report, dict):
        raise ValueError("report 必須是 JSON 物件")
    report = dict(report)
    report["comparison_overlay"] = comparison_overlay_settings({"report": report})
    clean["report"] = report
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return path


def resolve_path(config: dict, value: str | Path | None, *, root_key: str | None = None) -> Path | None:
    if value is None or str(value).strip() == "":
        return None
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    base = config.get(root_key) if root_key else None
    if base:
        base_path = Path(base).expanduser()
        if not base_path.is_absolute():
            base_path = Path(config.get("_config_dir", ".")) / base_path
        return (base_path / path).resolve()
    return (Path(config.get("_config_dir", ".")) / path).resolve()


def resolve_list(config: dict, key: str) -> list[Path]:
    values = config.get(key) or []
    if isinstance(values, (str, Path)):
        values = [values]
    return [p for value in values if (p := resolve_path(config, value)) is not None]
