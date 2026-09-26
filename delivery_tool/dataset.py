from __future__ import annotations

import csv
import cv2
import json
import math
import re
from collections import defaultdict
from pathlib import Path

from .config import resolve_list, resolve_path
from .parsing import canonical_event, history_run_mapping, normalize_date, normalize_row, parse_filename


def _read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def _date_folder_match(path: Path, iso_date: str) -> bool:
    if not iso_date:
        return True
    return normalize_date(str(path)) == iso_date


def _asset_index(root: Path | None, dates: list[str]) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = defaultdict(list)
    if not root or not root.exists():
        return result
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}:
            result[path.name.casefold()].append(path)
    return result


def _project_files(config: dict, errors: list, warnings: list) -> tuple[list[dict], list[dict]]:
    import batch_core

    items, pairings = [], []
    for project_path in resolve_list(config, "projects"):
        if not project_path.is_file():
            errors.append({"code": "missing_project", "message": f"找不到分析專案：{project_path}"})
            continue
        try:
            project = batch_core.load_project(project_path)
        except Exception as exc:
            errors.append({"code": "invalid_project", "message": f"專案讀取失敗：{project_path}：{exc}"})
            continue
        for item in project.get("items", []):
            items.append({"item": item, "project_path": str(project_path), "project_name": project.get("name", "")})
    for pairing_path in resolve_list(config, "pairings"):
        if not pairing_path.is_file():
            errors.append({"code": "missing_pairing", "message": f"找不到 pairing 檔：{pairing_path}"})
            continue
        try:
            data = json.loads(pairing_path.read_text(encoding="utf-8-sig"))
            if not isinstance(data.get("runs"), list):
                raise ValueError("缺少 runs 清單")
            cameras = {str(c.get("id", "")): str(c.get("name", c.get("id", ""))) for c in data.get("cameras", [])}
            for run in data["runs"]:
                pairings.append({"run": run, "pairing_path": str(pairing_path), "cameras": cameras})
        except Exception as exc:
            errors.append({"code": "invalid_pairing", "message": f"pairing 讀取失敗：{pairing_path}：{exc}"})
    if not items:
        warnings.append({"code": "no_project", "message": "沒有可讀取的分析專案；評估資料仍會保留且標示未連結"})
    if not pairings and config.get("pairings"):
        warnings.append({"code": "no_pairings", "message": "沒有可讀取的航次配對資料"})
    return items, pairings


def _attachment_index(config: dict, errors: list, warnings: list) -> dict:
    path = resolve_path(config, config.get("attachment_index"))
    if not path:
        warnings.append({"code": "no_attachment_index", "message": "未提供附件索引；GT／Mask 會以缺漏狀態呈現"})
        return {}
    if not path.is_file():
        errors.append({"code": "missing_attachment_index", "message": f"找不到附件索引：{path}"})
        return {}
    records = defaultdict(list)
    try:
        for raw in _read_csv(path):
            row = normalize_row(raw)
            file_value = row.get("filename") or row.get("labelme") or row.get("mask_asset") or ""
            if not file_value:
                continue
            filename = Path(str(file_value).replace("\\", "/")).name.casefold()
            key = Path(filename).stem.casefold()
            records[key].append({"row": row, "source_path": str(path), "filename": filename,
                                 "parsed": parse_filename(filename, row, test_dates=[])} )
    except Exception as exc:
        errors.append({"code": "invalid_attachment_index", "message": f"附件索引無法讀取：{exc}"})
    return records


def _parse_metric(record: dict, field: str, errors: list):
    raw = record.get("_raw", {}).get(field)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        number = float(str(raw).strip().replace(",", ""))
    except (ValueError, TypeError):
        errors.append({"code": "invalid_numeric", "key": record["key"], "message": f"{field} 不是有效數值：{raw}"})
        return None
    if not math.isfinite(number):
        errors.append({"code": "non_finite", "key": record["key"], "message": f"{field} 不是有限數值"})
        return None
    return number


def _evaluation_records(config: dict, errors: list, warnings: list) -> list[dict]:
    paths = resolve_list(config, "evaluation_csvs")
    if not paths:
        errors.append({"code": "no_evaluation_csv", "message": "至少需要一份評估 CSV"})
        return []
    date_filter = [str(d) for d in config.get("test_dates", [])]
    records, seen = [], set()
    event_identities = {}
    for path in paths:
        if not path.is_file():
            errors.append({"code": "missing_evaluation_csv", "message": f"找不到評估 CSV：{path}"})
            continue
        try:
            rows = _read_csv(path)
        except Exception as exc:
            errors.append({"code": "invalid_evaluation_csv", "message": f"評估 CSV 讀取失敗：{path}：{exc}"})
            continue
        for row_number, raw in enumerate(rows, 2):
            values = normalize_row(raw)
            filename = str(values.get("filename") or "").strip()
            if not filename:
                errors.append({"code": "missing_filename", "source": str(path), "row": row_number, "message": f"CSV 第 {row_number} 列缺少圖片檔名"})
                continue
            parsed = parse_filename(filename, values, test_dates=date_filter)
            if not parsed.get("date"):
                parsed["date"] = normalize_date(path.name)
            if not parsed.get("date"):
                # Take date from a date-named CSV only when the name is unambiguous.
                parsed["date"] = next((d for d in date_filter if d), "")
            if date_filter and parsed.get("date") and parsed["date"] not in date_filter:
                continue
            mapping = history_run_mapping(parsed)
            if mapping:
                parsed["history_run_mapping"] = mapping
                parsed["run_letter"] = mapping["run_letter"]
            event = canonical_event(values.get("event") or parsed.get("event"))
            frame_raw = values.get("frame") or parsed.get("frame")
            try:
                frame = int(float(frame_raw)) if str(frame_raw).strip() else None
            except (ValueError, TypeError):
                frame = None
            parsed.update(event=event, frame=frame)
            key = "|".join(str(parsed.get(k, "")) for k in ("date", "camera", "test", "run_letter", "phase", "event", "frame"))
            record = {
                "key": key,
                "date": parsed.get("date", ""), "camera": parsed.get("camera", ""),
                "test": parsed.get("test", ""), "run_letter": parsed.get("run_letter", ""),
                "phase": parsed.get("phase", ""), "event": event, "frame": frame,
                "source_csv": str(path),
                "source_row": row_number,
                "filename": parsed["filename"],
                "parsed": parsed,
                "raw": raw,
                "_raw": {"iou": values.get("iou") or values.get("target_iou"), "distance": values.get("distance")},
                "iou_raw": str(values.get("iou") if values.get("iou") not in (None, "") else (values.get("target_iou") or "")),
                "distance_raw": str(values.get("distance", "") or ""),
                "evaluation_fps": values.get("fps"),
                "iou_source": "mIoU" if values.get("iou") not in (None, "") else "target_iou" if values.get("target_iou") not in (None, "") else "",
                "iou": None,
                "distance_m": None,
                "status": values.get("status", ""),
            }
            missing = [label for field, label in (("date", "日期"), ("camera", "Camera"), ("test", "Test"),
                                                   ("run_letter", "Run Letter"), ("phase", "Phase"),
                                                   ("event", "事件"), ("frame", "Frame"))
                       if parsed.get(field) in (None, "")]
            if missing:
                errors.append({"code": "incomplete_identity", "key": key, "source": str(path), "row": row_number,
                               "message": f"評估列無法解析欄位：{'、'.join(missing)}"})
            if frame is not None and frame < 1:
                errors.append({"code": "invalid_frame", "key": key, "message": f"Frame 必須是 1-based 正整數：{frame}"})
            record["iou"] = _parse_metric(record, "iou", errors)
            record["distance_m"] = _parse_metric(record, "distance", errors)
            if record["iou"] is not None and not 0 <= record["iou"] <= 1:
                errors.append({"code": "iou_out_of_range", "key": key, "message": f"mIoU 超出 0–1：{record['iou']}"})
            if record["distance_m"] is not None and record["distance_m"] < 0:
                errors.append({"code": "negative_distance", "key": key, "message": f"檢出距離為負值：{record['distance_m']}"})
            if parsed.get("filename_distance_m") is not None and record["distance_m"] is not None:
                if not math.isclose(parsed["filename_distance_m"], record["distance_m"], abs_tol=0.05):
                    warnings.append({"code": "distance_mismatch", "key": key,
                                     "message": f"檔名距離 {parsed['filename_distance_m']} m 與 CSV 檢出距離 {record['distance_m']} m 不同；以 CSV 值顯示"})
            if key in seen:
                errors.append({"code": "duplicate_key", "key": key, "message": "評估資料具有重複識別鍵"})
            seen.add(key)
            event_identity = "|".join(str(parsed.get(k, "")) for k in ("date", "camera", "test", "run_letter", "phase", "event"))
            previous_frame = event_identities.get(event_identity)
            if previous_frame is not None and previous_frame != frame:
                errors.append({"code": "event_frame_ambiguous", "key": key,
                               "message": f"同一航次／相機／事件有多個 Frame：{previous_frame} 與 {frame}"})
            event_identities[event_identity] = frame
            records.append(record)
    if not records:
        warnings.append({"code": "empty_evaluation", "message": "指定日期範圍內沒有評估資料列"})
    return records


def _resolve_record_image(record: dict, image_index: dict, image_root: Path | None, warnings: list, errors: list):
    parsed = record["parsed"]
    filename = record["filename"]
    original = normalize_row(record.get("raw", {})).get("filename", "")
    direct = Path(str(original).replace("\\", "/")) if original else Path(filename)
    if direct.is_absolute() and direct.is_file() and direct.name.casefold() == Path(filename).name.casefold():
        return direct
    if image_root and not direct.is_absolute():
        candidate = (image_root / direct).resolve()
        if candidate.is_file() and candidate.name.casefold() == Path(filename).name.casefold():
            return candidate
    candidates = image_index.get(Path(filename).name.casefold(), [])
    date_matches = [p for p in candidates if _date_folder_match(p, parsed.get("date", ""))]
    unknown_date = [p for p in candidates if not normalize_date(str(p))]
    candidates = date_matches or unknown_date
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        errors.append({"code": "image_ambiguous", "key": record["key"], "message": f"同名圖片有 {len(candidates)} 個候選，無法精確配對"})
    else:
        warnings.append({"code": "missing_image", "key": record["key"], "message": f"找不到圖片：{filename}"})
    return None


def _find_attachment(record: dict, attachment_records: dict, config: dict, errors: list, warnings: list):
    parsed = record["parsed"]
    stem = Path(record["filename"]).stem.casefold()
    keyed = [candidate for rows in attachment_records.values() for candidate in rows
             if all(str(candidate["parsed"].get(k, "")) == str(parsed.get(k, ""))
                    for k in ("date", "camera", "test", "run_letter", "phase", "event", "frame"))]
    matches = keyed or attachment_records.get(stem, [])
    if len(matches) > 1:
        errors.append({"code": "attachment_ambiguous", "key": record["key"], "message": f"附件索引有 {len(matches)} 個候選"})
        return {}
    if not matches:
        warnings.append({"code": "missing_attachment", "key": record["key"], "message": "附件索引沒有此事件的標註與 Mask 路徑"})
        return {}
    attachment = matches[0]["row"]
    base_root = resolve_path(config, config.get("attachment_root"))
    result = {}
    for field, root in (("labelme", base_root), ("mask_asset", resolve_path(config, config.get("mask_root")))):
        value = attachment.get(field)
        if not value:
            continue
        candidate = Path(str(value).strip()).expanduser()
        if not candidate.is_absolute():
            candidate = (root or Path(matches[0]["source_path"]).parent) / candidate
        result[field] = candidate.resolve()
    result["attachment_source"] = matches[0]["source_path"]
    result["attachment_row"] = attachment
    return result


def _pairing_id_for(parsed: dict, config: dict) -> str:
    keys = ["|".join(str(parsed.get(k, "")) for k in fields) for fields in (
        ("date", "test", "run_letter", "phase", "camera"), ("test", "run_letter", "phase", "camera"),
        ("date", "run_letter", "phase", "camera"))]
    mapping = config.get("pairing_run_id_map") or {}
    return next((str(mapping[key]) for key in keys if key in mapping and mapping[key]), "")


def _video_metadata(record: dict, pairings: list, config: dict, errors: list, warnings: list) -> dict:
    """Resolve an exact paired video and inspect its reported frame range without decoding it."""
    root = resolve_path(config, config.get("video_root"))
    include = bool(config.get("include_videos"))
    run_id = (record.get("analysis_link") or {}).get("pairing_run_id") or record.get("parsed", {}).get("run_id") or _pairing_id_for(record["parsed"], config)
    raw_paths = []
    for pair in pairings:
        run = pair["run"]
        if run_id and str(run.get("id", "")) == str(run_id):
            for camera_id, match in (run.get("cameraMatches") or {}).items():
                camera_name = pair.get("cameras", {}).get(str(camera_id), str(camera_id))
                if match and match.get("path") and (camera_name == record.get("camera") or camera_name.endswith(str(record.get("camera", ""))[-1:])):
                    raw_paths.append(str(match["path"]))
    raw_paths = list(dict.fromkeys(raw_paths))
    if len(raw_paths) > 1:
        target = errors if include else warnings
        target.append({"code": "video_pairing_ambiguous", "key": record["key"], "message": "pairing 中此航次／相機有多個影片來源"})
        return {"status": "ambiguous", "path": "", "fps": None, "frame_count": None}
    if not root and not include:
        # Pairing snapshots still carry reported FPS/duration when the source videos are not supplied.
        run_row = next((row for row in pairings if str(row["run"].get("id", "")) == str(run_id)), None)
        match = None
        if run_row:
            camera_id = next((cid for cid, name in run_row.get("cameras", {}).items() if name == record.get("camera")), "")
            match = (run_row["run"].get("cameraMatches") or {}).get(camera_id) if camera_id else None
        fps = match.get("fps") if match else None
        duration = match.get("duration") if match else None
        try:
            fps = float(fps) if fps not in (None, "") else None
            duration = float(duration) if duration not in (None, "") else None
        except (TypeError, ValueError):
            fps, duration = None, None
        frame_count = int(round(fps * duration)) if fps and duration and fps > 0 and duration > 0 else None
        if frame_count and record.get("frame") and record["frame"] > frame_count:
            errors.append({"code": "frame_outside_video", "key": record["key"],
                           "message": f"Frame {record['frame']} 超過 pairing 記錄影片長度推算的幀數 {frame_count}"})
        return {"status": "source_unverified", "path": "", "fps": fps, "frame_count": frame_count}
    if not raw_paths:
        return {"status": "source_unverified", "path": "", "fps": None, "frame_count": None}
    basename = Path(raw_paths[0].replace("\\", "/")).name
    candidates = []
    if root and root.is_dir():
        candidates.extend(p for p in root.rglob("*") if p.is_file() and p.name.casefold() == basename.casefold())
    direct = Path(raw_paths[0])
    if direct.is_file():
        candidates.append(direct)
    candidates = list({str(p.resolve()).casefold(): p.resolve() for p in candidates}.values())
    if len(candidates) != 1:
        if len(candidates) > 1:
            target = errors if include else warnings
            target.append({"code": "video_ambiguous", "key": record["key"], "message": f"影片 {basename} 有 {len(candidates)} 個候選"})
            return {"status": "ambiguous", "path": "", "fps": None, "frame_count": None}
        return {"status": "source_unverified", "path": "", "fps": None, "frame_count": None}
    path = candidates[0]
    cap = cv2.VideoCapture(str(path))
    opened = cap.isOpened()
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS))
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    finally:
        cap.release()
    if not opened or not math.isfinite(fps) or fps <= 0 or frame_count <= 0:
        warnings.append({"code": "video_metadata_unavailable", "key": record["key"], "message": f"影片無法提供有效 FPS／總幀數：{basename}"})
        return {"status": "metadata_unavailable", "path": str(path), "fps": None, "frame_count": None}
    if record.get("frame") and record["frame"] > frame_count:
        errors.append({"code": "frame_outside_video", "key": record["key"], "message": f"Frame {record['frame']} 超過配對影片總幀數 {frame_count}"})
    return {"status": "verified", "path": str(path), "fps": fps, "frame_count": frame_count}


def _project_events(item: dict) -> list[dict]:
    events = []
    latest = item.get("latest_run") or {}
    summary = latest.get("summary") or {}
    auto_map = {"FirstDetection": "first_detection_frame", "StableStart": "first_sustained_stable_start_frame"}
    for name, field in auto_map.items():
        frame = summary.get(field)
        if frame is not None:
            events.append({"event": name, "frame": int(frame), "source": "automatic"})
    try:
        import batch_core
        manual = batch_core.resolved_manual_events(item)
    except Exception:
        manual = item.get("manual_events", {})
    manual_map = {"manual_first_detection": "FirstDetection", "manual_sustained_stable_start": "StableStart"}
    for key, name in manual_map.items():
        if manual.get(key, {}).get("frame") is not None:
            events.append({"event": name, "frame": int(manual[key]["frame"]), "source": "manual"})
    return events


def _test_identity(value: object) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"^test\s*", "", text)
    return text.lstrip("0") or "0"


def _pairing_run_for(item: dict, pairing_rows: list) -> dict:
    run = item.get("pairing_run") or {}
    if run:
        return run
    run_id = str(item.get("pairing_run_id", ""))
    return next((row["run"] for row in pairing_rows if str(row["run"].get("id", "")) == run_id), {})


def _identity_project_candidates(record: dict, project_rows: list, pairing_rows: list) -> list[dict]:
    parsed = record["parsed"]
    mapping = parsed.get("history_run_mapping") or {}
    accepted_letters = {str(parsed.get("run_letter", "")).upper()}
    if mapping.get("original_run_letter"):
        accepted_letters.add(str(mapping["original_run_letter"]).upper())
    candidates = []
    for row in project_rows:
        item = row["item"]
        if item.get("camera") and str(item.get("camera")) != str(parsed.get("camera")):
            continue
        if item.get("phase") and str(item.get("phase")) != str(parsed.get("phase")):
            continue
        pairing = _pairing_run_for(item, pairing_rows)
        scenario = pairing.get("scenarioId", item.get("scenario_id", ""))
        if scenario not in (None, "") and _test_identity(scenario) != _test_identity(parsed.get("test")):
            continue
        phase = pairing.get("phase") or item.get("phase", "")
        if phase and str(phase) != str(parsed.get("phase")):
            continue
        angle = str(pairing.get("angle", pairing.get("runLetter", ""))).upper()
        if angle and angle not in accepted_letters:
            continue
        voyage = pairing.get("voyage") or {}
        pairing_date = normalize_date(voyage.get("date") or pairing.get("startedAt"))
        if pairing_date and pairing_date != parsed.get("date"):
            continue
        candidates.append(row)
    return candidates


def _identity_pairing_candidates(record: dict, pairing_rows: list) -> list[dict]:
    parsed = record["parsed"]
    mapping = parsed.get("history_run_mapping") or {}
    letters = {str(parsed.get("run_letter", "")).upper()}
    if mapping.get("original_run_letter"):
        letters.add(str(mapping["original_run_letter"]).upper())
    camera = str(parsed.get("camera", ""))
    matches = []
    for row in pairing_rows:
        run = row["run"]
        if _test_identity(run.get("scenarioId")) != _test_identity(parsed.get("test")):
            continue
        if str(run.get("phase", "")) != str(parsed.get("phase", "")):
            continue
        voyage = run.get("voyage") or {}
        run_date = normalize_date(voyage.get("date") or run.get("startedAt"))
        if run_date and run_date != parsed.get("date"):
            continue
        angle = str(run.get("angle", "")).upper()
        if angle and angle not in letters:
            continue
        camera_id = next((cid for cid, name in row.get("cameras", {}).items() if name == camera), "")
        camera_match = (run.get("cameraMatches") or {}).get(camera_id) if camera_id else None
        if camera_match and camera_match.get("path"):
            matches.append(row)
    return matches


def _link_project(record: dict, project_rows: list, pairing_rows: list, config: dict, errors: list, warnings: list) -> dict:
    parsed = record["parsed"]
    override = (config.get("event_links") or {}).get(record["key"], (config.get("event_links") or {}).get(record["filename"]))
    if override == "clear" or override is None and record["key"] in (config.get("event_links") or {}):
        warnings.append({"code": "project_link_cleared", "key": record["key"], "message": "分析關聯已由設定明確清除"})
        return {"status": "cleared", "item_id": "", "project_path": "", "event_sources": []}
    mapped_run_id = str(override.get("pairing_run_id", "")) if isinstance(override, dict) else ""
    chosen_item_id = str(override.get("project_item_id", "")) if isinstance(override, dict) else ""
    run_id = str(parsed.get("run_id", "") or mapped_run_id or _pairing_id_for(parsed, config))
    if not run_id and not chosen_item_id:
        identity_candidates = _identity_project_candidates(record, project_rows, pairing_rows)
        exact = [row for row in identity_candidates if any(e["event"] == parsed.get("event") and e["frame"] == parsed.get("frame")
                                                            for e in _project_events(row["item"]))]
        if len(exact) == 1:
            run_id = str(exact[0]["item"].get("pairing_run_id", exact[0]["item"].get("segment", "")))
        elif len(identity_candidates) == 1:
            run_id = str(identity_candidates[0]["item"].get("pairing_run_id", identity_candidates[0]["item"].get("segment", "")))
        elif len(identity_candidates) > 1:
            errors.append({"code": "project_ambiguous", "key": record["key"],
                           "message": f"日期／Test／Run／Phase／Camera 有 {len(identity_candidates)} 個分析項目候選"})
            return {"status": "ambiguous", "item_id": "", "project_path": "", "event_sources": []}
        if not run_id:
            pairing_candidates = _identity_pairing_candidates(record, pairing_rows)
            if len(pairing_candidates) == 1:
                run_id = str(pairing_candidates[0]["run"].get("id", ""))
            elif len(pairing_candidates) > 1:
                errors.append({"code": "pairing_ambiguous", "key": record["key"],
                               "message": f"日期／Test／Run／Phase／Camera 對應到 {len(pairing_candidates)} 個 pairing 航次"})
                return {"status": "ambiguous", "item_id": "", "project_path": "", "event_sources": []}
    if not run_id and not chosen_item_id:
        warnings.append({"code": "project_unlinked", "key": record["key"], "message": "評估事件沒有 pairing_run_id 對映"})
        return {"status": "unlinked", "item_id": "", "project_path": "", "event_sources": []}
    candidates = ([row for row in project_rows if str(row["item"].get("id", "")) == chosen_item_id]
                  if chosen_item_id else
                  [row for row in project_rows if str(row["item"].get("pairing_run_id", row["item"].get("segment", ""))) == run_id])
    if chosen_item_id and not run_id and len(candidates) == 1:
        run_id = str(candidates[0]["item"].get("pairing_run_id", candidates[0]["item"].get("segment", "")))
    camera = str(parsed.get("camera", ""))
    candidates = [row for row in candidates if not row["item"].get("camera") or str(row["item"].get("camera")) == camera]
    if len(candidates) != 1:
        code = "project_ambiguous" if len(candidates) > 1 else "project_item_missing"
        target = errors if candidates else warnings
        target.append({"code": code, "key": record["key"], "message": f"pairing_run_id={run_id} 對應到 {len(candidates)} 個分析項目"})
        return {"status": "ambiguous" if candidates else "unlinked", "item_id": "", "pairing_run_id": run_id, "project_path": "", "event_sources": []}
    row = candidates[0]
    item = row["item"]
    matches = [e for e in _project_events(item) if e["event"] == parsed.get("event") and e["frame"] == parsed.get("frame")]
    if not matches:
        errors.append({"code": "project_event_mismatch", "key": record["key"], "message": f"分析項目 {item.get('id')} 沒有相同事件種類與 Frame"})
        return {"status": "mismatch", "item_id": str(item.get("id", "")), "pairing_run_id": run_id,
                "project_path": row["project_path"], "event_sources": []}
    sources = sorted({e["source"] for e in matches})
    latest = item.get("latest_run") or {}
    metadata = latest.get("metadata") or item.get("metadata") or {}
    fps, frame_count = metadata.get("fps"), metadata.get("frame_count")
    if frame_count and parsed.get("frame") and parsed["frame"] > int(frame_count):
        errors.append({"code": "frame_outside_video", "key": record["key"], "message": f"Frame {parsed['frame']} 超過分析影片總幀數 {frame_count}"})
    return {"status": "linked", "item_id": str(item.get("id", "")), "pairing_run_id": run_id,
            "project_path": row["project_path"], "event_sources": sources,
            "fps": fps, "frame_count": frame_count}


def _validate_attachments(record: dict, attachment: dict, image_path: Path | None, errors: list, warnings: list):
    result = {"labelme_path": "", "mask_path": "", "gt_status": "missing", "mask_status": "missing"}
    for field, output, status in (("labelme", "labelme_path", "gt_status"), ("mask_asset", "mask_path", "mask_status")):
        path = attachment.get(field)
        if not path:
            warnings.append({"code": f"missing_{field}", "key": record["key"], "message": f"缺少附件：{'LabelMe GT' if field == 'labelme' else 'aligned prediction Mask'}"})
            continue
        result[output] = str(path)
        if not path.is_file():
            warnings.append({"code": f"missing_{field}", "key": record["key"], "message": f"附件檔案不存在：{path}"})
            continue
        try:
            from PIL import Image
            if field == "labelme":
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                if not isinstance(data.get("shapes"), list):
                    raise ValueError("LabelMe JSON 缺少 shapes")
                try:
                    size = (int(data.get("imageWidth")), int(data.get("imageHeight")))
                except (TypeError, ValueError):
                    size = (0, 0)
                if min(size) <= 0:
                    raise ValueError("LabelMe imageWidth／imageHeight 無效")
                if image_path:
                    if Path(str(data.get("imagePath", "")).replace("\\", "/")).name.casefold() != image_path.name.casefold():
                        raise ValueError("LabelMe imagePath 與 CSV 配對圖片不一致")
                    with Image.open(image_path) as base:
                        actual_size = base.size
                    if size != actual_size:
                        raise ValueError(f"LabelMe 座標尺寸 {size[0]}x{size[1]} 與影像 {actual_size[0]}x{actual_size[1]} 不符")
                for shape in data["shapes"]:
                    if shape.get("shape_type", "polygon") != "polygon" or shape.get("label") not in {"ship", "buoy"}:
                        raise ValueError(f"不支援的 LabelMe shape：{shape.get('label')} / {shape.get('shape_type')}")
                    points = shape.get("points")
                    if not isinstance(points, list) or len(points) < 3 or any(len(p) != 2 or not all(math.isfinite(float(n)) for n in p) for p in points):
                        raise ValueError("LabelMe polygon 座標無效")
                    if any(not (0 <= float(x) < size[0] and 0 <= float(y) < size[1]) for x, y in points):
                        raise ValueError("LabelMe polygon 座標超出標註影像範圍")
                result[status] = "valid"
            else:
                with Image.open(path) as mask_image:
                    mask_image.load()
                    if image_path:
                        with Image.open(image_path) as base:
                            base_size = base.size
                        if mask_image.size != base_size:
                            raise ValueError(f"Mask 尺寸 {mask_image.width}x{mask_image.height} 與標註影像 {base_size[0]}x{base_size[1]} 不符；不自動縮放")
                    channels = mask_image.convert("RGB").split()
                    if channels[0].tobytes() != channels[1].tobytes() or channels[0].tobytes() != channels[2].tobytes():
                        raise ValueError("Mask 附件是彩色影像；拒絕將 Seg／彩圖當作二值 Mask")
                result[status] = "valid"
        except Exception as exc:
            errors.append({"code": "invalid_attachment_coordinates", "key": record["key"], "message": f"附件驗證失敗：{exc}"})
            result[status] = "invalid"
    return result


def validate_config(config: dict, *, progress=None, cancel=None) -> dict:
    errors, warnings = [], []
    project_rows, pairing_rows = _project_files(config, errors, warnings)
    attachments = _attachment_index(config, errors, warnings)
    records = _evaluation_records(config, errors, warnings)
    image_root = resolve_path(config, config.get("image_root"))
    if not image_root or not image_root.is_dir():
        warnings.append({"code": "missing_image_root", "message": "圖片根目錄未設定或不存在；評估數值仍會保留"})
    image_index = _asset_index(image_root, [str(d) for d in config.get("test_dates", [])])
    enriched = []
    for idx, record in enumerate(records):
        if cancel and cancel.is_set():
            raise InterruptedError("已取消")
        image_path = _resolve_record_image(record, image_index, image_root, warnings, errors)
        attachment = _find_attachment(record, attachments, config, errors, warnings)
        if attachment.get("labelme") and attachment["labelme"].is_file() and image_path:
            attached_name = Path(str(attachment.get("attachment_row", {}).get("filename", ""))).name
            # The attachment index key has to identify the same base image; event fields are checked by the exact stem/key lookup above.
            if attached_name and Path(attached_name).stem.casefold() != Path(record["filename"]).stem.casefold():
                errors.append({"code": "attachment_identity_mismatch", "key": record["key"], "message": "附件檔名與評估圖片不一致"})
        assets = _validate_attachments(record, attachment, image_path, errors, warnings)
        link = _link_project(record, project_rows, pairing_rows, config, errors, warnings)
        parsed = record["parsed"]
        fps = record.get("evaluation_fps")
        try:
            fps = float(fps) if fps not in (None, "") else None
        except (TypeError, ValueError):
            fps = None
        if fps is None:
            fps = link.get("fps")
        video = _video_metadata(record, pairing_rows, config, errors, warnings)
        if fps is None:
            fps = video.get("fps")
        if video.get("status") == "source_unverified" and not any(w.get("code") == "video_source_unverified" for w in warnings):
            warnings.append({"code": "video_source_unverified", "message": "未提供影片檔；來源身份未驗證。Pairing 有 FPS／時長時仍用於名目時間與 Frame 範圍核對"})
        if fps is None and record["frame"]:
            warnings.append({"code": "fps_missing", "key": record["key"], "message": "CSV／分析專案沒有 FPS；名目時間未提供"})
        record.update({
            "image_path": str(image_path) if image_path else "",
            "image_status": "found" if image_path else "missing",
            "attachment": assets,
            "analysis_link": link,
            "fps": fps,
            "nominal_time_s": (record["frame"] - 1) / fps if record["frame"] and fps else None,
            "pairing": {},
            "video_status": video["status"],
            "video_source_path": video["path"],
            "video_frame_count": video["frame_count"],
        })
        record.pop("evaluation_fps", None)
        if image_path:
            from PIL import Image
            with Image.open(image_path) as img:
                record["image_size"] = list(img.size)
        else:
            record["image_size"] = None
        if progress:
            progress(idx + 1, len(records), f"驗證 {record['filename']}")
        enriched.append(record)

    # Preserve unassessed and failed analysis items for an explicit completeness appendix.
    evaluation_item_ids = {r["analysis_link"].get("item_id") for r in enriched if r["analysis_link"].get("item_id")}
    completeness = []
    for row in project_rows:
        item = row["item"]
        latest = item.get("latest_run") or {}
        completeness.append({"item_id": str(item.get("id", "")), "project_path": row["project_path"],
                             "camera": item.get("camera", ""), "pairing_run_id": item.get("pairing_run_id", item.get("segment", "")),
                             "scenario_id": item.get("scenario_id", ""), "phase": item.get("phase", ""),
                             "status": item.get("status", "pending"),
                             "detection_outcome": (latest.get("summary") or {}).get("detection_outcome", ""),
                             "evaluated": str(item.get("id", "")) in evaluation_item_ids})
    # Validate report font and PDF dependency before a formal build.
    pdf_layout = (config.get("report") or {}).get("pdf_layout", "overview")
    if pdf_layout not in {"overview", "run", "both", "none"}:
        errors.append({"code": "invalid_pdf_layout", "message": f"不支援的 PDF 版型：{pdf_layout}"})
    if pdf_layout != "none":
        try:
            from .pdf_report import resolve_chinese_font
            font = resolve_chinese_font(config)
            if not font:
                errors.append({"code": "missing_chinese_font", "message": "找不到可用中文字型；請在 report.font_path 設定微軟正黑體或其他 TTF/TTC 字型"})
        except ImportError:
            errors.append({"code": "missing_reportlab", "message": "產生 PDF 需要 ReportLab；請安裝專案依賴"})
    manifest = {"schema_version": 1, "batch_id": config.get("batch_id", ""),
                "test_dates": config.get("test_dates", []), "records": enriched,
                "project_completeness": completeness,
                "metric_note": "每日 CSV 的 mIoU 原值照錄；既有整理程式來源為 target_iou，未重新計算，非跨類別平均。",
                "distance_note": "距離採每日 CSV 檢出距離_m；檔名距離僅核對。距離來源為 OCR，人工覆核狀態未知。",
                "frame_note": "Frame 為 1-based；名目時間以 (frame - 1) / fps 計算。",
                "validation": {"errors": errors, "warnings": warnings}}
    return {"manifest": manifest, "errors": errors, "warnings": warnings,
            "project_items": project_rows, "pairings": pairing_rows,
            "ok": not errors, "record_count": len(enriched),
            "counts": {"images_found": sum(bool(r["image_path"]) for r in enriched),
                       "gt_valid": sum(r["attachment"]["gt_status"] == "valid" for r in enriched),
                       "mask_valid": sum(r["attachment"]["mask_status"] == "valid" for r in enriched),
                       "analysis_linked": sum(r["analysis_link"]["status"] == "linked" for r in enriched)}}
