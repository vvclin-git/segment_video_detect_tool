from __future__ import annotations

import re
import string
from datetime import date


DEFAULT_VIDEO_FILENAME_TEMPLATE = "Camera{camera}_Test{test}_{run}_{date:%y%m%d}"
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".m4v"}
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAME = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.I)


def validate_video_filename_template(template: object) -> set[str]:
    if not isinstance(template, str) or not template.strip():
        raise ValueError("影片檔名範本不可空白")
    fields: set[str] = set()
    try:
        parts = string.Formatter().parse(template)
        for _literal, field, format_spec, conversion in parts:
            if _INVALID_FILENAME_CHARS.search(_literal):
                raise ValueError(f"影片檔名範本含非法字元：{_literal}")
            if field is None:
                continue
            if field not in {"camera", "test", "run", "date"}:
                raise ValueError(f"不支援的欄位 {{{field}}}；可用欄位為 camera、test、run、date")
            if conversion:
                raise ValueError("影片檔名範本不支援 !r 等轉換格式")
            if field != "date" and format_spec:
                raise ValueError(f"只有 date 欄位可指定格式：{{date:日期格式}}（目前為 {{{field}:{format_spec}}}）")
            if "{" in format_spec or "}" in format_spec:
                raise ValueError("日期格式不可再包含範本欄位")
            if field == "date":
                try:
                    date_sample = date(2026, 9, 22).__format__(format_spec)
                except ValueError as exc:
                    raise ValueError(f"date 欄位的日期格式無效：{format_spec}") from exc
                if _INVALID_FILENAME_CHARS.search(date_sample) or date_sample.endswith((" ", ".")):
                    raise ValueError("date 欄位的日期格式會產生非法檔名字元")
            fields.add(field)
        if template.endswith((" ", ".")):
            raise ValueError("影片檔名範本不可用空白或句點結尾")
        if any(template.casefold().endswith(extension) for extension in VIDEO_EXTENSIONS):
            raise ValueError("影片檔名範本不可指定副檔名；副檔名會沿用來源影片")
        if not fields and _WINDOWS_RESERVED_NAME.fullmatch(template):
            raise ValueError(f"影片檔名範本是 Windows 保留名稱：{template}")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"影片檔名範本格式錯誤：{exc}") from exc
    return fields


def render_video_filename_stem(template: str, values: dict) -> str:
    validate_video_filename_template(template)
    try:
        stem = template.format_map(values)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"影片檔名範本無法套用：{exc}") from exc
    if not stem or stem in {".", ".."}:
        raise ValueError("影片檔名範本產生了空檔名")
    if _INVALID_FILENAME_CHARS.search(stem):
        raise ValueError(f"產生的影片檔名含非法字元：{stem}")
    if stem.endswith((" ", ".")):
        raise ValueError(f"影片檔名不可用空白或句點結尾：{stem}")
    if _WINDOWS_RESERVED_NAME.fullmatch(stem):
        raise ValueError(f"影片檔名是 Windows 保留名稱：{stem}")
    if any(stem.casefold().endswith(extension) for extension in VIDEO_EXTENSIONS):
        raise ValueError("影片檔名範本不可指定副檔名；副檔名會沿用來源影片")
    return stem


def paired_video_filename_values(record: dict, pairings: list, source=None) -> dict:
    """Extract filename values only from the paired voyage and camera metadata."""
    from .parsing import history_run_mapping, normalize_date

    analysis_link = record.get("analysis_link") or {}
    run_id = str(analysis_link.get("pairing_run_id") or record.get("parsed", {}).get("run_id") or "")
    camera = str(record.get("camera", ""))
    source_name = str(getattr(source, "name", "") or "").casefold()
    candidates, source_candidates = [], []
    for pairing in pairings:
        run = pairing.get("run") or {}
        if run_id and str(run.get("id", "")) != run_id:
            continue
        matches = run.get("cameraMatches") or {}
        for camera_id, match in matches.items():
            if not match or not match.get("path"):
                continue
            camera_name = str(pairing.get("cameras", {}).get(str(camera_id), camera_id))
            if camera_name != camera and not camera_name.endswith(camera[-1:]):
                continue
            match_name = str(match.get("name") or str(match["path"]).replace("\\", "/").rsplit("/", 1)[-1]).casefold()
            entry = (run, camera_name, str(camera_id))
            candidates.append(entry)
            if source_name and match_name == source_name:
                source_candidates.append(entry)
    if source_candidates:
        candidates = source_candidates
    elif source_name and not run_id:
        candidates = []
    if not candidates:
        # A confirmed analysis item retains its original voyage context even when
        # the external Pairing snapshot is absent or uses different run IDs.
        embedded_run = analysis_link.get("video_pairing_run")
        if analysis_link.get("status") != "linked" or not embedded_run:
            return {}
        candidates = [(embedded_run, camera, camera)]

    def context(candidate):
        run, camera_name, camera_id = candidate
        voyage = run.get("voyage") or {}
        scenario = str(run.get("scenarioId", "")).strip()
        scenario = re.sub(r"^test\s*", "", scenario, flags=re.I)
        if scenario.isdigit():
            scenario = str(int(scenario))
        camera_match = re.search(r"(?:camera|cam|相機)\s*[-_ ]*([a-z0-9]+)", camera_name, re.I)
        if not camera_match:
            camera_match = re.search(r"([a-z0-9]+)$", camera_id, re.I)
        camera_value = camera_match.group(1) if camera_match else camera_name.strip()
        if camera_value.isdigit():
            camera_value = str(int(camera_value))
        run_value = str(run.get("angle") or run.get("runLetter") or run.get("sequence") or "").strip()
        normalized_date = normalize_date(voyage.get("date") or run.get("startedAt"))
        if normalized_date == "2026-09-15":
            # The September 15 logger kept phase P1 for every run. The user
            # confirmed that the note's speed suffix identifies runs A-D.
            note = str(run.get("note") or "").strip()
            speeds = re.findall(r"(?<![a-z0-9.])(\d+)\s*kt(?![a-z0-9])", note, re.I)
            suffix = re.search(r"(?<![a-z0-9.])(\d+)\s*kt\s*$", note, re.I)
            run_value = ({"5": "A", "10": "B", "15": "C", "20": "D"}.get(suffix.group(1), "")
                         if suffix and len(set(speeds)) == 1 else "")
        # Use the same legacy correction as evaluation identities. These voyages
        # stored angle A for P1-P4, which represent delivery runs A-D.
        mapping = history_run_mapping({"date": normalized_date, "run_letter": run_value,
                                       "phase": str(run.get("phase") or "")})
        if mapping:
            run_value = mapping["run_letter"]
        date_value = date.fromisoformat(normalized_date) if normalized_date else None
        return {"camera": camera_value, "test": scenario, "run": run_value, "date": date_value}

    contexts = {tuple(sorted(context(candidate).items())) for candidate in candidates}
    if len(contexts) != 1:
        return {}
    return dict(next(iter(contexts)))
