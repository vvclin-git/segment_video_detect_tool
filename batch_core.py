"""UI-independent batch analysis, project storage and reproducible exports."""
from __future__ import annotations

import copy
import csv
import hashlib
import json
import math
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from frame_render import (IMAGE_MODES, LEGACY_BBOX_STYLE, compose_frame,
                           frame_filename, segment_frame, write_png)
from result_index import write_index

VERSION = "batch-1.0"
DEFAULTS = dict(lower=[160, 140, 80], upper=[179, 255, 255], min_area=20,
                roi=None, start_percent=0.0, end_percent=100.0, window_n=5,
                stable_on_m=4, stable_off_count=1, stable_confirm_frames=3)
EVENTS = ("first_detection", "first_sustained_stable_start", "first_stable_confirmation")
MANUAL_EVENTS = {"manual_first_detection": "人工首次檢出", "manual_stable_confirmation": "人工穩定確認"}


def uid():
    return uuid.uuid4().hex


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def new_project():
    return dict(schema_version=1, project_id=uid(), name="新分析專案", created_at=now(), items=[])


def save_project(path, project):
    project["updated_at"] = now()
    atomic_json(path, project)


def load_project(path):
    project = read_json(path)
    if project.get("schema_version") != 1 or not isinstance(project.get("items"), list):
        raise ValueError("不支援的專案格式")
    seen = set()
    for item in project["items"]:
        if item["id"] in seen:
            raise ValueError("專案含重複工作項目 ID")
        seen.add(item["id"])
        validate_settings(item["settings"])
        item.setdefault("manual_events", {})
        for key in ("scenario_id", "phase", "note"):
            item.setdefault(key, "")
        if item.get("status") in ("running", "queued"):
            item.update(status="cancelled", error="上次執行中斷；請重新分析")
    return project


def source_identity(path):
    """Stat plus sampled content identity; usable when a video is relocated."""
    path = Path(path)
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for offset in sorted({0, max(0, stat.st_size // 2 - 32768), max(0, stat.st_size - 65536)}):
            source.seek(offset)
            digest.update(source.read(65536))
    return dict(size=stat.st_size, sample_sha256=digest.hexdigest())


def probe_video(path):
    if not Path(path).is_file():
        raise ValueError(f"找不到影片：{path}")
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError("無法開啟影片或不支援的編碼")
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        width, height = (int(cap.get(p)) for p in (cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT))
        if not math.isfinite(fps) or fps <= 0 or min(total, width, height) <= 0:
            raise ValueError("影片 FPS／幀數／解析度無效，不能精確分析")
        return dict(fps=fps, frame_count=total, width=width, height=height,
                    timestamp_basis="frame_index / reported_fps (nominal; not VFR PTS)")
    finally:
        cap.release()


def new_item(path, **labels):
    item = dict(id=uid(), path=str(Path(path).resolve()), name=Path(path).name,
                session="", camera="", segment="", scenario_id="", phase="", note="", settings=copy.deepcopy(DEFAULTS),
                status="pending", error="", runs=[], manual_events={})
    item.update(labels)
    try:
        item["metadata"] = probe_video(path)
    except (ValueError, OSError) as exc:
        item.update(metadata={}, status="failed", error=str(exc))
    return item


def set_manual_event(item, event, frame, note="", expected_identity=None):
    if event not in MANUAL_EVENTS:
        raise ValueError("不支援的人工事件類型")
    meta = probe_video(item["path"])
    if type(frame) is not int or not 1 <= frame <= meta["frame_count"]:
        raise ValueError(f'事件 frame 必須是 1–{meta["frame_count"]} 的整數')
    identity = source_identity(item["path"])
    if expected_identity is not None and identity != expected_identity:
        raise ValueError("預覽期間來源影片已變更，請重新開啟影片後標註")
    read_exact_frame(item["path"], frame)
    if source_identity(item["path"]) != identity:
        raise ValueError("標註期間來源影片已變更，請重新開啟")
    annotation = dict(frame=frame, timestamp=(frame - 1) / meta["fps"], note=note,
                      updated_at=now(), source_identity=identity, timestamp_basis=meta["timestamp_basis"])
    item.setdefault("manual_events", {})[event] = annotation
    return annotation


def manual_event_reason(item, annotation):
    try:
        return "來源影片已變更，請重新標註" if source_identity(item["path"]) != annotation["source_identity"] else ""
    except OSError:
        return "來源影片不存在"


def manual_summary(item):
    result = {}
    for event in MANUAL_EVENTS:
        annotation = item.get("manual_events", {}).get(event)
        for field in ("frame", "timestamp", "note", "updated_at"):
            result[f"{event}_{field}"] = annotation.get(field) if annotation else None
        result[f"{event}_status"] = (manual_event_reason(item, annotation) or "valid") if annotation else "unmarked"
    return result


def validate_settings(settings, metadata=None):
    for key in DEFAULTS:
        if key not in settings:
            raise ValueError(f"缺少設定：{key}")
    for index, limit in enumerate((179, 255, 255)):
        lo, hi = settings["lower"][index], settings["upper"][index]
        if any(not isinstance(v, int) for v in (lo, hi)) or not 0 <= lo <= hi <= limit:
            raise ValueError(f"HSV 範圍錯誤（上限 {limit}，min 必須 ≤ max）")
    for key in ("min_area", "window_n", "stable_on_m", "stable_off_count", "stable_confirm_frames"):
        if not isinstance(settings[key], int):
            raise ValueError(f"{key} 必須為整數")
    n, m, o, k = (settings[x] for x in ("window_n", "stable_on_m", "stable_off_count", "stable_confirm_frames"))
    if settings["min_area"] < 1 or not (n >= 1 and 1 <= m <= n and 0 <= o < m and k >= 1):
        raise ValueError("需符合 Area ≥ 1、1 ≤ ON ≤ N、0 ≤ OFF < ON、K ≥ 1")
    a, b = settings["start_percent"], settings["end_percent"]
    if not (math.isfinite(a) and math.isfinite(b) and 0 <= a < b <= 100):
        raise ValueError("分析百分比需符合 0 ≤ 開始 < 結束 ≤ 100")
    roi = settings["roi"]
    if roi is not None:
        if len(roi) != 4 or any(not isinstance(x, int) for x in roi):
            raise ValueError("ROI 必須包含四個整數 x1,y1,x2,y2")
        x1, y1, x2, y2 = roi
        if not (0 <= x1 < x2 and 0 <= y1 < y2):
            raise ValueError("ROI 必須具有正面積")
        if metadata and (x2 > metadata["width"] or y2 > metadata["height"]):
            raise ValueError("ROI 超出原始影片尺寸，請重新框選")


def analysis_bounds(settings, total):
    validate_settings(settings)
    start = math.floor(total * settings["start_percent"] / 100)
    end = math.ceil(total * settings["end_percent"] / 100)
    if not 0 <= start < end <= total:
        raise ValueError("分析範圍沒有有效幀")
    return start, end


class StableTracker:
    def __init__(self, settings):
        self.n, self.on, self.off, self.k = (settings[key] for key in
            ("window_n", "stable_on_m", "stable_off_count", "stable_confirm_frames"))
        self.history = deque(maxlen=self.n)
        self.hits = self.streak = self.misses = 0
        self.stable = False

    def push(self, detected):
        if len(self.history) == self.n:
            self.hits -= self.history[0]
        self.history.append(detected)
        self.hits += detected
        self.misses = 0 if detected else self.misses + 1
        ready = len(self.history) == self.n
        if ready:
            if self.stable:
                if self.hits <= self.off:
                    self.stable, self.streak = False, 0
            else:
                self.streak = self.streak + 1 if self.hits >= self.on else 0
                if self.streak >= self.k:
                    self.stable = True
        return dict(rolling_rate=self.hits / len(self.history), rolling_hit_count=self.hits,
                    rolling_window_length=len(self.history), window_ready=int(ready),
                    on_qualifying_streak=self.streak, stable_detected=int(self.stable),
                    consecutive_miss=self.misses)


def summarize(rows, settings):
    first = next((row for row in rows if row["raw_detected"]), None)
    stable_index = next((i for i, row in enumerate(rows) if row["stable_detected"]), None)
    stable = rows[stable_index] if stable_index is not None else None
    start = rows[stable_index - settings["stable_confirm_frames"] + 1] if stable else None
    summary = dict(valid_frames=len(rows), detection_outcome="stable" if stable else
                   "not_stable" if first else "no_detection", detected_at_analysis_start=bool(rows and rows[0]["raw_detected"]),
                   raw_detection_rate=sum(r["raw_detected"] for r in rows) / len(rows) if rows else 0,
                   stable_detection_coverage=sum(r["stable_detected"] for r in rows) / len(rows) if rows else 0,
                   longest_consecutive_miss=max((r["consecutive_miss"] for r in rows), default=0),
                   stabilization_latency_frames=stable["frame"] - first["frame"] if stable and first else None,
                   stabilization_latency_s=stable["timestamp"] - first["timestamp"] if stable and first else None)
    for prefix, row in zip(EVENTS, (first, start, stable)):
        for key in ("frame", "timestamp", "bbox_x", "bbox_y", "bbox_width", "bbox_height",
                    "bbox_center_x", "bbox_center_y", "largest_blob_area", "bbox_valid"):
            summary[f"{prefix}_{key}"] = row[key] if row else None
    summary["first_stable_detection_frame"] = summary["first_stable_confirmation_frame"]
    return summary


def stale_reason(item):
    run = item.get("latest_run")
    if not run:
        return ""
    if item["settings"] != run["settings"]:
        return "設定已變更"
    try:
        if source_identity(item["path"]) != run["source_identity"]:
            return "來源影片已變更"
    except OSError:
        return "來源影片不存在"
    return ""


def load_rows(run):
    rows = read_json(Path(run["directory"]) / "frames.json")
    start, end = run["analysis_start_frame"], run["analysis_end_frame"]
    if not isinstance(rows, list) or len(rows) != end - start + 1 or any(
            row.get("frame") != start + i for i, row in enumerate(rows)):
        raise ValueError("逐幀資料不完整或 frame 順序錯誤，請重新分析")
    return rows


class Cancelled(Exception):
    pass


def analyze_item(item, runs_dir, cancel=None, progress=None):
    cancel = cancel or threading.Event()
    config = copy.deepcopy(item["settings"])
    meta = probe_video(item["path"])
    validate_settings(config, meta)
    identity = source_identity(item["path"])
    start, end = analysis_bounds(config, meta["frame_count"])
    run_id = uid()
    run = dict(run_id=run_id, item_id=item["id"], version=VERSION, created_at=now(),
               settings=config, metadata=meta, source_identity=identity,
               analysis_start_frame=start + 1, analysis_end_frame=end,
               directory=str((Path(runs_dir) / run_id).resolve()), source_path=item["path"])
    old = item.get("latest_run")
    stable_keys = {"window_n", "stable_on_m", "stable_off_count", "stable_confirm_frames"}
    reuse = old and old["source_identity"] == identity and old.get("version") == VERSION and all(
        config[key] == old["settings"][key] for key in config if key not in stable_keys)
    rows = None
    if reuse:
        try:
            rows = load_rows(old)
        except (OSError, ValueError):
            rows = None
    tracker = StableTracker(config)
    cap = None
    try:
        if rows is None:
            rows = []
            cap = cv2.VideoCapture(item["path"])
            if not cap.isOpened():
                raise ValueError("無法重新開啟影片")
            if start and not cap.set(cv2.CAP_PROP_POS_FRAMES, start):
                raise ValueError("解碼器不支援跳到分析起點")
            for index in range(start, end):
                if cancel.is_set():
                    raise Cancelled("已取消")
                ok, frame = cap.read()
                if not ok:
                    raise ValueError(f"在 frame {index + 1} 提早解碼失敗，預期結束 {end}")
                if abs(cap.get(cv2.CAP_PROP_POS_FRAMES) - (index + 1)) > 0.5:
                    raise ValueError(f"解碼器無法確認 frame {index + 1} 的位置")
                _, boxes, pixels = segment_frame(frame, config)
                x, y, w, h, area = boxes[0] if boxes else (None, None, 0, 0, 0)
                row = dict(frame=index + 1, timestamp=index / meta["fps"], selected_pixels=pixels,
                           largest_blob_area=area, bbox_x=x, bbox_y=y, bbox_width=w, bbox_height=h,
                           bbox_center_x=x + w / 2 if x is not None else None,
                           bbox_center_y=y + h / 2 if y is not None else None,
                           bbox_valid=int(bool(boxes)), raw_detected=int(bool(boxes)))
                row.update(tracker.push(row["raw_detected"]))
                rows.append(row)
                if progress and ((index - start) % 10 == 0 or index + 1 == end):
                    progress(index - start + 1, end - start)
        else:
            for i, row in enumerate(rows):
                if cancel.is_set():
                    raise Cancelled("已取消")
                row.update(tracker.push(row["raw_detected"]))
                if progress and (i % 100 == 0 or i + 1 == len(rows)):
                    progress(i + 1, len(rows))
            run["recomputed_from"] = old["run_id"]
        if cancel.is_set():
            raise Cancelled("已取消")
        if source_identity(item["path"]) != identity:
            raise ValueError("分析期間來源影片已變更，結果未保存")
        run["summary"] = summarize(rows, config)
        atomic_json(Path(run["directory"]) / "frames.json", rows)
        atomic_json(Path(run["directory"]) / "run.json", run)
        return run
    finally:
        if cap is not None:
            cap.release()


def write_csv(path, rows, fields=None):
    if fields is None:
        fields = list(dict.fromkeys(key for row in rows for key in row))
    with Path(path).open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def read_exact_frame(path, number, capture=None):
    own = capture is None
    cap = capture if capture is not None else cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened() or not cap.set(cv2.CAP_PROP_POS_FRAMES, number - 1):
            raise ValueError(f"無法跳至 frame {number}")
        ok, frame = cap.read()
        position = cap.get(cv2.CAP_PROP_POS_FRAMES)
        if not ok or abs(position - number) > 0.5:
            raise ValueError(f"無法精確讀取 frame {number}")
        return frame
    finally:
        if own:
            cap.release()


def export_batch(items, parent, progress=None):
    directory = Path(parent) / ("batch_export_" + datetime.now().strftime("%Y%m%d_%H%M%S_") + uid()[:8])
    directory.mkdir(parents=True)
    summaries, manifest = [], []
    frames_by_item = {}
    for index, item in enumerate(items):
        run = item.get("latest_run")
        summary = dict(item_id=item["id"], video=item["name"], source_path=item["path"],
                       session=item["session"], camera=item["camera"], segment=item.get("segment", ""),
                       scenarioID=item.get("scenario_id", ""), phase=item.get("phase", ""), note=item.get("note", ""),
                       execution_status=item["status"], error=item.get("error", ""), stale=stale_reason(item),
                       run_id=run["run_id"] if run else "", export_status="no_results", export_error="")
        summaries.append(summary)
        summary.update(manual_summary(item))
        if run:
            summary.update(run["summary"])
            summary.update(analysis_start_frame=run["analysis_start_frame"], analysis_end_frame=run["analysis_end_frame"],
                           fps=run["metadata"]["fps"], timestamp_basis=run["metadata"]["timestamp_basis"])
            output = directory / item["id"] / run["run_id"]
            manifest_start = len(manifest)
            try:
                output.mkdir(parents=True)
                rows = load_rows(run)
                frames_by_item[str(item["id"])] = rows
                write_csv(output / "frame_analysis.csv", rows)
                atomic_json(output / "settings.json", run)
                summary["export_status"] = "completed"
                try:
                    if source_identity(item["path"]) != run["source_identity"]:
                        raise ValueError("來源影片與分析快照不同，未匯出事件圖")
                    source_error = ""
                except (OSError, ValueError) as exc:
                    source_error = str(exc)
                by_frame = {row["frame"]: row for row in rows}
                for event in EVENTS:
                    number = run["summary"][event + "_frame"]
                    record = dict(item_id=item["id"], run_id=run["run_id"], event=event,
                                  event_source="automatic",
                                  frame=number, status="no_event", error="", raw_filename="", annotated_filename="", mask_filename="")
                    manifest.append(record)
                    if number is None:
                        record["error"] = run["summary"]["detection_outcome"]
                        continue
                    record.update(by_frame[number])
                    try:
                        if source_error:
                            raise ValueError(source_error)
                        frame = read_exact_frame(item["path"], number)
                        mask, boxes, _ = segment_frame(frame, run["settings"])
                        annotated = compose_frame(frame, run["settings"], "Original",
                                                  boxes=boxes,
                                                  selected_indices=range(len(boxes)),
                                                  include_bbox=True, include_roi=True,
                                                  bbox_style=LEGACY_BBOX_STYLE)
                        for suffix, image in (("raw", frame), ("annotated", annotated), ("mask", mask)):
                            path = output / f"{event}_frame_{number:06d}_{suffix}.png"
                            write_png(path, image)
                            record[suffix + "_filename"] = path.relative_to(directory).as_posix()
                        record["status"] = "completed"
                    except (OSError, ValueError, cv2.error) as exc:
                        record.update(status="failed", error=str(exc))
                        summary.update(export_status="partial", export_error=str(exc))
            except (OSError, ValueError, KeyError, cv2.error) as exc:
                summary.update(export_status="failed", export_error=str(exc))
                recorded = {record["event"] for record in manifest[manifest_start:]}
                for event in EVENTS:
                    if event not in recorded:
                        manifest.append(dict(item_id=item["id"], run_id=run["run_id"], event=event,
                            frame=run["summary"].get(event + "_frame"), status="failed", error=str(exc)))
        else:
            for event in EVENTS:
                manifest.append(dict(item_id=item["id"], run_id="", event=event, frame=None,
                                     status="no_results", error=item.get("error") or item["status"]))
        annotations = item.get("manual_events", {})
        for event, annotation in annotations.items():
            if event not in MANUAL_EVENTS:
                continue
            record = dict(item_id=item["id"], run_id="", event=event, event_source="manual",
                          frame=annotation["frame"], timestamp=annotation["timestamp"],
                          note=annotation.get("note", ""), updated_at=annotation.get("updated_at", ""),
                          status="failed", error="", raw_filename="")
            manifest.append(record)
            try:
                manual_dir = directory / item["id"] / "manual_events"
                manual_dir.mkdir(parents=True, exist_ok=True)
                atomic_json(manual_dir / "manual_events.json", annotations)
                reason = manual_event_reason(item, annotation)
                if reason:
                    raise ValueError(reason)
                frame = read_exact_frame(item["path"], annotation["frame"])
                path = manual_dir / f'{event}_frame_{annotation["frame"]:06d}_raw.png'
                write_png(path, frame)
                record.update(status="completed", raw_filename=path.relative_to(directory).as_posix())
                if summary["export_status"] == "no_results":
                    summary["export_status"] = "manual_only"
            except (OSError, ValueError, cv2.error) as exc:
                record["error"] = str(exc)
                if summary["export_status"] != "failed":
                    summary["export_status"] = "partial"
                summary["export_error"] = str(exc)
        if run:
            try:
                write_csv(output / "run_summary.csv", [dict(metric=k, value=v) for k, v in summary.items()], ["metric", "value"])
            except OSError as exc:
                summary.update(export_status="failed", export_error=str(exc))
        if progress:
            progress(index + 1, len(items))
    write_csv(directory / "batch_summary.csv", summaries)
    write_csv(directory / "event_images_manifest.csv", manifest)
    atomic_json(directory / "export_report.json", summaries)
    try:
        write_index(directory, items, summaries, manifest, frames_by_item)
    except Exception as exc:
        # The CSV/JSON/PNG export is still useful when an HTML write fails.
        # Keep a human-readable marker so the UI can report the precise issue.
        (directory / "index_generation_error.txt").write_text(str(exc), encoding="utf-8")
    return directory, summaries


def import_pairing(path, video_root=None):
    data = read_json(path)
    if not isinstance(data.get("runs"), list):
        raise ValueError("pairing.json 缺少 runs 清單")
    cameras = {c["id"]: c.get("name", c["id"]) for c in data.get("cameras", [])}
    candidates = {}
    if video_root:
        for file in Path(video_root).rglob("*"):
            if file.is_file() and file.suffix.lower() in {".mp4", ".avi", ".mov", ".mkv", ".m4v"}:
                candidates.setdefault(file.name.casefold(), []).append(file)
    items = []
    for run in data["runs"]:
        for camera, match in (run.get("cameraMatches") or {}).items():
            if not match or not match.get("path"):
                continue
            source = Path(match["path"])
            # Windows paths also work when tests run on a non-Windows host.
            basename = str(source).replace("\\", "/").split("/")[-1]
            if not source.exists():
                matches = candidates.get(basename.casefold(), [])
                if len(matches) == 1:
                    source = matches[0]
            item = new_item(source, session=str(run.get("sequence", "")),
                            camera=cameras.get(camera, camera), pairing_run_id=str(run.get("id", "")),
                            pairing_camera_id=camera, scenario_id=run.get("scenarioId", run.get("scenarioID", "")),
                            phase=run.get("phase", ""), note=run.get("note", ""),
                            pairing_run=copy.deepcopy({k: v for k, v in run.items() if k != "cameraMatches"}),
                            pairing_match=copy.deepcopy(match))
            duration = match.get("duration")
            if isinstance(duration, (int, float)) and duration > 0:
                start = max(0, min(100, float(match.get("runStartInVideoSec", 0)) / duration * 100))
                end = max(0, min(100, float(match.get("runEndInVideoSec", duration)) / duration * 100))
                if start < end:
                    item["settings"].update(start_percent=start, end_percent=end)
            items.append(item)
    return items


def merge_pairing_items(project, imported):
    """Refresh pairing labels without replacing reviewed settings, events or runs."""
    added = updated = 0
    fields = ("scenario_id", "phase", "note", "pairing_run_id", "pairing_camera_id", "pairing_run", "pairing_match")
    for incoming in imported:
        matches = [item for item in project["items"] if item.get("pairing_match") and
                   incoming["pairing_run_id"] and
                   item.get("pairing_run_id", item.get("segment", "")) == incoming["pairing_run_id"] and
                   (item.get("pairing_camera_id") == incoming["pairing_camera_id"] if item.get("pairing_camera_id")
                    else item.get("camera") == incoming["camera"])]
        if matches:
            for item in matches:
                legacy_segment = "pairing_run_id" not in item and item.get("segment") == incoming["pairing_run_id"]
                item.update({key: copy.deepcopy(incoming[key]) for key in fields})
                if legacy_segment:
                    item["segment"] = ""
                updated += 1
        else:
            project["items"].append(incoming)
            added += 1
    return added, updated
