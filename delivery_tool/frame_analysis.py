from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


def sequence_id(project_id: str, item_id: str, run_id: str) -> str:
    """Return a stable, filesystem-safe key for one project analysis run."""
    identity = "\0".join((str(project_id), str(item_id), str(run_id)))
    return "seq_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _finite_number(value, label: str, *, minimum: float | None = None,
                   maximum: float | None = None) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} 必須是有限數值")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} 必須是有限數值") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} 必須是有限數值")
    if minimum is not None and number < minimum:
        raise ValueError(f"{label} 不可小於 {minimum}")
    if maximum is not None and number > maximum:
        raise ValueError(f"{label} 不可大於 {maximum}")
    return number


def _state(value, label: str) -> int:
    if isinstance(value, bool) or value not in (0, 1):
        raise ValueError(f"{label} 必須是 0 或 1")
    return int(value)


def validate_rows(rows: object, start: object, end: object, fps: object = None) -> list[dict]:
    """Validate and retain only the per-frame fields used by the report chart."""
    if type(start) is not int or type(end) is not int or start < 1 or end < start:
        raise ValueError("分析範圍必須是有效的 1-based Frame 整數")
    expected_count = end - start + 1
    if not isinstance(rows, list) or len(rows) != expected_count:
        raise ValueError(f"逐幀筆數不符：預期 {expected_count}，取得 {len(rows) if isinstance(rows, list) else '非陣列'}")
    try:
        fps_value = _finite_number(fps, "FPS", minimum=0)
        if fps_value == 0:
            fps_value = None
    except ValueError:
        fps_value = None

    output = []
    for index, source in enumerate(rows):
        expected_frame = start + index
        if not isinstance(source, dict) or type(source.get("frame")) is not int or source["frame"] != expected_frame:
            raise ValueError(f"Frame 順序錯誤：第 {index + 1} 筆預期 F{expected_frame}")
        row = {"frame": expected_frame}
        timestamp = source.get("timestamp")
        if timestamp is None and fps_value:
            timestamp = (expected_frame - 1) / fps_value
        row["timestamp"] = _finite_number(timestamp, f"F{expected_frame} timestamp", minimum=0)
        row["raw_detected"] = _state(source.get("raw_detected"), f"F{expected_frame} raw_detected")
        row["rolling_rate"] = _finite_number(source.get("rolling_rate"), f"F{expected_frame} rolling_rate", minimum=0, maximum=1)
        row["stable_detected"] = _state(source.get("stable_detected"), f"F{expected_frame} stable_detected")
        if "window_ready" in source and source["window_ready"] is not None:
            row["window_ready"] = _state(source["window_ready"], f"F{expected_frame} window_ready")
        else:
            row["window_ready"] = None
        for field in ("selected_pixels", "largest_blob_area"):
            value = source.get(field)
            row[field] = None if value is None else _finite_number(value, f"F{expected_frame} {field}", minimum=0)
        bbox_valid = source.get("bbox_valid")
        row["bbox_valid"] = None if bbox_valid is None else _state(bbox_valid, f"F{expected_frame} bbox_valid")
        for field in ("bbox_x", "bbox_y", "bbox_width", "bbox_height"):
            value = source.get(field)
            row[field] = None if value is None else _finite_number(value, f"F{expected_frame} {field}")
        output.append(row)
    return output


def _assets_root(config: dict, project_id: str) -> Path | None:
    roots = config.get("project_assets_roots") or {}
    if not isinstance(roots, dict):
        return None
    value = roots.get(str(project_id))
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(config.get("_config_dir", ".")) / path
    return path.resolve()


def frames_source(project_row: dict, config: dict) -> tuple[Path | None, list[Path]]:
    """Apply the documented source priority; return the first existing file only."""
    project_path = Path(project_row["project_path"])
    project_id = str(project_row.get("project_id", ""))
    item = project_row["item"]
    latest = item.get("latest_run") or {}
    run_id = str(latest.get("run_id") or "")
    candidates = []
    root = _assets_root(config, project_id)
    if root and run_id:
        candidates.append(root / "runs" / run_id / "frames.json")
    if run_id:
        candidates.append(project_path.with_suffix(".assets") / "runs" / run_id / "frames.json")
    directory = latest.get("directory")
    if directory:
        candidates.append(Path(directory).expanduser() / "frames.json")
    # Keep deterministic precedence while avoiding trying the exact same file twice.
    unique = []
    seen = set()
    for path in candidates:
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return next((path for path in unique if path.is_file()), None), unique


def _run_events(item: dict) -> list[dict]:
    latest = item.get("latest_run") or {}
    summary = latest.get("summary") or {}
    events = []
    automatic = (
        ("FirstDetection", "first_detection_frame"),
        ("StableStart", "first_sustained_stable_start_frame"),
        ("StableConfirmation", "first_stable_confirmation_frame"),
    )
    for name, field in automatic:
        frame = summary.get(field)
        if frame is not None:
            events.append({"event": name, "frame": int(frame), "source": "automatic"})
    try:
        import batch_core
        manual = batch_core.resolved_manual_events(item)
    except Exception:
        manual = item.get("manual_events", {})
    names = (
        ("manual_first_detection", "FirstDetection"),
        ("manual_sustained_stable_start", "StableStart"),
        ("manual_stable_confirmation", "StableConfirmation"),
    )
    for key, name in names:
        annotation = manual.get(key) or {}
        frame = annotation.get("frame")
        if frame is not None:
            events.append({"event": name, "frame": int(frame), "source": "manual"})
    return events


def build_analysis_sequence(project_row: dict, config: dict) -> tuple[dict, list[dict] | None]:
    """Resolve and validate a single latest analysis run without touching source data."""
    item = project_row["item"]
    latest = item.get("latest_run") or {}
    project_id = str(project_row.get("project_id", ""))
    item_id = str(item.get("id", ""))
    run_id = str(latest.get("run_id") or "")
    identity = sequence_id(project_id, item_id, run_id)
    metadata = latest.get("metadata") or item.get("metadata") or {}
    try:
        fps = _finite_number(metadata.get("fps"), "FPS", minimum=0)
        if fps <= 0:
            fps = None
    except ValueError:
        fps = None
    sequence = {
        "sequence_id": identity,
        "project_id": project_id,
        "item_id": item_id,
        "run_id": run_id,
        "scenario_id": item.get("scenario_id", ""),
        "pairing_run_id": str(item.get("pairing_run_id", item.get("segment", "")) or ""),
        "camera": item.get("camera", ""),
        "phase": item.get("phase", ""),
        "video_name": item.get("name", ""),
        "status": "missing",
        "reason": "",
        "analysis_start_frame": latest.get("analysis_start_frame"),
        "analysis_end_frame": latest.get("analysis_end_frame"),
        "fps": fps,
        "timestamp_basis": metadata.get("timestamp_basis", ""),
        "settings": {
            "window_n": (latest.get("settings") or {}).get("window_n"),
            "stable_on_m": (latest.get("settings") or {}).get("stable_on_m"),
            "stable_off_count": (latest.get("settings") or {}).get("stable_off_count"),
            "stable_confirm_frames": (latest.get("settings") or {}).get("stable_confirm_frames"),
        },
        "events": _run_events(item),
        "data_path": "",
        "video": "",
    }
    if not config.get("include_frame_charts", True):
        sequence.update(status="disabled", reason="設定已關閉逐幀圖表")
        return sequence, None
    if not run_id:
        sequence["reason"] = "分析項目沒有 latest_run.run_id"
        return sequence, None
    source, candidates = frames_source(project_row, config)
    if source is None:
        sequence["reason"] = "找不到此分析 run 的 frames.json"
        sequence["_source_candidates"] = [str(path) for path in candidates]
        return sequence, None
    sequence["_source_path"] = str(source.resolve())
    sequence["source_name"] = source.name
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
        rows = validate_rows(payload, sequence["analysis_start_frame"], sequence["analysis_end_frame"], fps)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        sequence.update(status="invalid", reason=f"{source}：{exc}", source_name=source.name)
        return sequence, None
    sequence.update(status="available", reason="", source_name=source.name)
    return sequence, rows
