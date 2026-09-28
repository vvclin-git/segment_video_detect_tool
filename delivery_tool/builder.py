from __future__ import annotations

import csv
import copy
import hashlib
import json
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

from .collage import copy_keyframe, render_collage
from .config import comparison_overlay_settings, resolve_path
from .dataset import validate_config
from .exporters import write_csv, write_html


def _safe_name(value: str) -> str:
    value = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(value))
    return value.strip("_")[:90] or "event"


def _atomic_json(path: Path, data: object):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def _video_for_record(record: dict, pairings: list, config: dict, errors: list, warnings: list) -> Path | None:
    source = Path(record.get("video_source_path", "")) if record.get("video_source_path") else None
    if source and source.is_file():
        return source
    run_id = str(record.get("analysis_link", {}).get("pairing_run_id") or record.get("parsed", {}).get("run_id") or "")
    if not run_id:
        return None
    camera = str(record.get("camera", ""))
    paths = []
    for pair in pairings:
        run = pair["run"]
        if str(run.get("id", "")) != run_id:
            continue
        matches = run.get("cameraMatches") or {}
        for camera_id, match in matches.items():
            if not match or not match.get("path"):
                continue
            label = pair.get("cameras", {}).get(str(camera_id), str(camera_id))
            if label == camera or str(label).endswith(camera[-1:]):
                paths.append(str(match["path"]))
    if not paths:
        return None
    if len(set(paths)) > 1:
        errors.append({"code": "video_pairing_ambiguous", "key": record["key"], "message": "航次配對含多個影片來源"})
        return None
    source_text = paths[0]
    basename = Path(source_text.replace("\\", "/")).name
    root = resolve_path(config, config.get("video_root"))
    candidates = []
    if root and root.is_dir():
        candidates = [p for p in root.rglob("*") if p.is_file() and p.name.casefold() == basename.casefold()]
    source_path = Path(source_text)
    if source_path.is_file():
        candidates.append(source_path)
    # Identical source paths can appear in both route lists; count unique canonical files only.
    candidates = list({str(p.resolve()).casefold(): p.resolve() for p in candidates}.values())
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        target = errors if config.get("include_videos") else warnings
        target.append({"code": "video_ambiguous", "key": record["key"], "message": f"影片 {basename} 有 {len(candidates)} 個候選"})
    elif config.get("include_videos"):
        warnings.append({"code": "missing_video", "key": record["key"], "message": f"找不到配對影片 {basename}；影片未打包"})
    return None


def _copy_source(source: Path, output: Path, root: Path, day: str) -> str:
    digest = hashlib.sha256(str(source.resolve()).casefold().encode("utf-8")).hexdigest()[:12]
    target = output / root / (day or "unknown_date") / f"{source.stem}_{digest}{source.suffix.lower()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(source, target)
    return target.relative_to(output).as_posix()


def _package_assets(result: dict, output: Path, pairings: list, config: dict, progress=None, cancel=None):
    manifest = result["manifest"]
    errors, warnings = result["errors"], result["warnings"]
    copied_videos: dict[str, str] = {}
    records = manifest["records"]
    for index, record in enumerate(records):
        if cancel and cancel.is_set():
            raise InterruptedError("已取消")
        if record.get("image_path"):
            source = Path(record["image_path"])
            record["source_image_name"] = source.name
            record["image_copy"] = copy_keyframe(record, output)
        collage_key = "_".join(_safe_name(str(record.get(k, ""))) for k in
                               ("date", "test", "run_letter", "phase", "camera", "event", "frame"))
        collage_path = output / "collages" / (record.get("date") or "unknown_date") / f"{collage_key}.png"
        overlay_path = output / "overlays" / (record.get("date") or "unknown_date") / f"{collage_key}.png"
        generated = render_collage(record, collage_path, overlay_output=overlay_path,
                                   comparison_overlay=config.get("report", {}).get("comparison_overlay"))
        record["overlay_image"] = overlay_path.relative_to(output).as_posix() if overlay_path.is_file() else ""
        record["collage"] = generated.relative_to(output).as_posix() if generated else ""

        # Copy source annotation and aligned mask into the delivery so manifest links remain portable.
        attachment = record.get("attachment", {})
        for field, subdir in (("labelme_path", "labelme"), ("mask_path", "masks"), ("seg_path", "seg")):
            source_text = attachment.get(field, "")
            if source_text and Path(source_text).is_file():
                attachment[field] = _copy_source(Path(source_text), output, Path("internal/attachments") / subdir,
                                                 record.get("date", ""))
            else:
                attachment[field] = ""

        if config.get("include_videos"):
            video = _video_for_record(record, pairings, config, errors, warnings)
            if video:
                identity = str(video.resolve()).casefold()
                if identity not in copied_videos:
                    copied_videos[identity] = _copy_source(video, output, Path("videos"), record.get("date", ""))
                record["video"] = copied_videos[identity]
            else:
                record["video"] = ""
        else:
            record["video"] = ""

        # Package paths are relative. Keep provenance as source basenames, not machine-specific paths.
        record["image_path"] = record.get("image_copy", "")
        record["video_source_name"] = Path(record.get("video_source_path", "")).name if record.get("video_source_path") else ""
        record.pop("video_source_path", None)
        record["source_csv"] = Path(record.get("source_csv", "")).name
        record.pop("source_row", None)
        record.pop("raw", None)
        record.pop("_raw", None)
        record.pop("pairing", None)
        record["analysis_link"]["project_path"] = Path(record.get("analysis_link", {}).get("project_path", "")).name
        if progress:
            progress(index + 1, len(records), f"整理交付附件：{record.get('filename', '')}")
    sequence_records = {record.get("analysis_link", {}).get("sequence_id"): record for record in records
                        if record.get("analysis_link", {}).get("sequence_id")}
    for sequence in manifest.get("analysis_sequences", []):
        if cancel and cancel.is_set():
            raise InterruptedError("已取消")
        sid = sequence["sequence_id"]
        rows = result.get("analysis_frames", {}).get(sid)
        if sequence.get("status") == "available" and rows is not None:
            compact_rows = [[row.get("frame"), row.get("timestamp"), row.get("raw_detected"), row.get("rolling_rate"),
                             row.get("stable_detected"), row.get("selected_pixels"), row.get("largest_blob_area"),
                             row.get("bbox_valid"), row.get("bbox_x"), row.get("bbox_y"), row.get("bbox_width"),
                             row.get("bbox_height"), row.get("window_ready")] for row in rows]
            payload = {"sequence_id": sid, "start": sequence.get("analysis_start_frame"),
                       "end": sequence.get("analysis_end_frame"), "fps": sequence.get("fps"),
                       "timestamp_basis": sequence.get("timestamp_basis", ""),
                       "settings": sequence.get("settings", {}), "events": sequence.get("events", []),
                       "rows": compact_rows}
            serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            serialized = (serialized.replace("<", "\\u003c").replace(">", "\\u003e")
                          .replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))
            relative = Path("analysis") / f"{sid}.js"
            target = output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("window.__SEA_TRIAL_FRAME_DATA__=window.__SEA_TRIAL_FRAME_DATA__||{};"
                              f"window.__SEA_TRIAL_FRAME_DATA__[{json.dumps(sid)}]={serialized};", encoding="utf-8")
            sequence["data_path"] = relative.as_posix()
        if config.get("include_videos"):
            linked_record = sequence_records.get(sid)
            surrogate = {"key": sid, "camera": sequence.get("camera", ""),
                         "analysis_link": {"pairing_run_id": sequence.get("pairing_run_id", "")},
                         "parsed": {"run_id": sequence.get("pairing_run_id", "")}}
            video = _video_for_record(linked_record or surrogate, pairings, config, errors, warnings)
            if video:
                identity = str(video.resolve()).casefold()
                if identity not in copied_videos:
                    date = (linked_record or {}).get("date", "")
                    copied_videos[identity] = _copy_source(video, output, Path("videos"), date)
                sequence["video"] = copied_videos[identity]
            else:
                sequence["video_reason"] = "沒有唯一且可讀取的配對影片；圖表仍可使用。"
        else:
            sequence["video_reason"] = "設定未啟用影片打包；圖表仍可使用。"
        # Machine-specific source paths are retained in the internal validation report only.
        sequence.pop("_source_path", None)
        sequence.pop("_source_candidates", None)
        sequence.pop("source_name", None)
        if progress:
            progress(len(records), len(records), f"整理逐幀序列：{sequence.get('camera', '')} · {sid}")
    for item in manifest.get("project_completeness", []):
        item["project_path"] = Path(item.get("project_path", "")).name
    return manifest


def _write_validation(path: Path, result: dict):
    report = {"ok": result["ok"], "record_count": result["record_count"], "counts": result["counts"],
              "frame_chart_counts": result.get("frame_chart_counts", {}),
              "frame_chart_sources": result.get("frame_chart_sources", []),
              "errors": result["errors"], "warnings": result["warnings"]}
    _atomic_json(path, report)


def build(config: dict, *, progress=None, cancel: threading.Event | None = None) -> dict:
    cancel = cancel or threading.Event()
    config = copy.deepcopy(config)
    config.setdefault("report", {})["comparison_overlay"] = comparison_overlay_settings(config)
    result = validate_config(config, progress=progress, cancel=cancel)
    if not result["ok"]:
        raise ValueError(json.dumps({"errors": result["errors"], "warnings": result["warnings"]}, ensure_ascii=False, indent=2))
    if cancel.is_set():
        raise InterruptedError("已取消")
    parent = resolve_path(config, config.get("output_dir"))
    if parent is None:
        raise ValueError("請設定 output_dir")
    parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    delivery = parent / f"delivery_{timestamp}_{uuid.uuid4().hex[:8]}"
    delivery.mkdir()
    try:
        for record in result["manifest"]["records"]:
            record["batch_id"] = config.get("batch_id", "")
        result["manifest"]["generated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        result["frame_chart_sources"] = [
            {"sequence_id": sequence.get("sequence_id"), "project_id": sequence.get("project_id"),
             "item_id": sequence.get("item_id"), "run_id": sequence.get("run_id"),
             "status": sequence.get("status"), "source_path": sequence.get("_source_path", ""),
             "source_candidates": sequence.get("_source_candidates", [])}
            for sequence in result["manifest"].get("analysis_sequences", [])]
        manifest = _package_assets(result, delivery, result["pairings"], config, progress, cancel)
        # Optional video pairing can add validation findings after the initial pass.
        manifest["validation"] = {"errors": result["errors"], "warnings": result["warnings"]}
        if result["errors"]:
            raise ValueError(json.dumps({"errors": result["errors"], "warnings": result["warnings"]}, ensure_ascii=False, indent=2))
        _write_validation(delivery / "internal" / "validation_report.json", result)
        clean_config = {k: v for k, v in config.items() if not k.startswith("_")}
        _atomic_json(delivery / "internal" / "build_config.json", clean_config)
        _atomic_json(delivery / "internal" / "result_manifest.json", manifest)
        write_csv(delivery / "result_summary.csv", manifest)
        write_html(delivery / "index.html", manifest, config, result["counts"])

        from .pdf_report import build_pdf
        layout = config.get("report", {}).get("pdf_layout", "overview")
        outputs = []
        if layout in {"overview", "both"}:
            path = build_pdf(delivery / "report_by_test.pdf", manifest, config, delivery, "overview")
            outputs.append(path)
        if layout in {"run", "both"}:
            path = build_pdf(delivery / "report_by_run.pdf", manifest, config, delivery, "run")
            outputs.append(path)
        pdf_cache = delivery / "internal" / "pdf_images"
        if pdf_cache.exists():
            shutil.rmtree(pdf_cache)
        return {"delivery_dir": delivery, "index_html": delivery / "index.html", "pdfs": outputs,
                "csv": delivery / "result_summary.csv", "manifest": delivery / "internal" / "result_manifest.json",
                "validation": delivery / "internal" / "validation_report.json", "counts": result["counts"],
                "frame_chart_counts": result.get("frame_chart_counts", {}),
                "record_count": result["record_count"], "warnings": result["warnings"]}
    except BaseException:
        shutil.rmtree(delivery, ignore_errors=True)
        raise
