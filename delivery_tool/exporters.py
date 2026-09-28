from __future__ import annotations

import csv
import html
import json
import shutil
from pathlib import Path


def _safe_json(value: object) -> str:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


def write_csv(path: Path, manifest: dict) -> Path:
    fields = ["date", "batch_id", "camera", "test", "run_letter", "phase", "event", "frame", "mIoU",
              "mIoU_source", "distance_m", "filename_distance_m", "nominal_time_s", "analysis_item_id",
              "analysis_link_status", "event_sources", "collage", "image", "video", "labelme", "mask", "evaluation_status"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for event in manifest.get("records", []):
            row = {k: event.get(k, "") for k in fields}
            row.update({
                "mIoU": event.get("iou_raw", event.get("iou")), "mIoU_source": event.get("iou_source", ""),
                "distance_m": event.get("distance_raw", event.get("distance_m")),
                "filename_distance_m": event.get("parsed", {}).get("filename_distance_m"),
                "nominal_time_s": event.get("nominal_time_s"),
                "analysis_item_id": event.get("analysis_link", {}).get("item_id", ""),
                "analysis_link_status": event.get("analysis_link", {}).get("status", "unlinked"),
                "event_sources": "+".join(event.get("analysis_link", {}).get("event_sources", [])),
                "image": event.get("image_copy", ""), "labelme": event.get("attachment", {}).get("labelme_path", ""),
                "mask": event.get("attachment", {}).get("mask_path", ""), "evaluation_status": event.get("status", ""),
            })
            writer.writerow(row)
    return path


def write_html(path: Path, manifest: dict, config: dict, counts: dict) -> Path:
    web_root = Path(__file__).parent / "web"
    shutil.copy2(web_root / "report.css", path.parent / "report.css")
    shutil.copy2(web_root / "chart_math.js", path.parent / "chart_math.js")
    shutil.copy2(web_root / "report.js", path.parent / "report.js")
    title = html.escape(str((config.get("report") or {}).get("report_name") or "海試結果報告"))
    data = _safe_json(manifest)
    template = (web_root / "report.html").read_text(encoding="utf-8")
    values = {
        "__TITLE__": title,
        "__BATCH__": html.escape(str(manifest.get("batch_id") or "未命名")),
        "__COUNT__": str(len(manifest.get("records", []))),
        "__IMAGES__": str(counts.get("images_found", 0)),
        "__GT__": str(counts.get("gt_valid", 0)),
        "__MASK__": str(counts.get("mask_valid", 0)),
        "__LINKED__": str(counts.get("analysis_linked", 0)),
        "__DATA__": data,
    }
    for key, value in values.items():
        template = template.replace(key, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template, encoding="utf-8")
    return path
