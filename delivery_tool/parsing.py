from __future__ import annotations

import re
from datetime import date
from pathlib import Path


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value).casefold())


ALIASES = {
    "filename": ("filename", "file", "image", "imagefilename", "keyframefilename", "keyframe檔名", "keyframe", "labeledimage", "圖片", "圖片檔名", "檔名", "影像", "影像檔名", "imagepath", "path"),
    "date": ("date", "testdate", "日期", "測試日期"),
    "camera": ("camera", "cam", "相機", "攝影機"),
    "test": ("test", "testid", "測試", "測試編號"),
    "run_letter": ("runletter", "run", "航次", "航次代號"),
    "phase": ("phase", "階段"),
    "event": ("event", "eventtype", "事件", "事件種類"),
    "frame": ("frame", "framenumber", "影格", "幀"),
    "iou": ("miou", "iou", "miouvalue", "交並比"),
    "distance": ("檢出距離m", "detectiondistancem", "distance", "distancem", "距離m", "檢出距離"),
    "run_id": ("pairingrunid", "runid", "pairingid", "航次id", "配對航次id"),
    "fps": ("fps", "framerate", "影格率"),
    "status": ("status", "結果", "狀態"),
    "target_iou": ("targetiou",),
    "labelme": ("labelmejson", "labelme", "annotationjson", "jsonpath", "標註json", "標註檔"),
    "mask_asset": ("maskasset", "maskpath", "alignedmask", "mask", "遮罩路徑", "遮罩"),
}
ALIAS_LOOKUP = {_norm(alias): key for key, aliases in ALIASES.items() for alias in aliases}


def normalize_row(row: dict) -> dict:
    normalized = {}
    for key, value in row.items():
        canonical = ALIAS_LOOKUP.get(_norm(key))
        if canonical:
            normalized[canonical] = value
    return normalized


def normalize_date(value: object) -> str:
    text = str(value or "").strip()
    m = re.search(r"(?<!\d)(20\d{2})[-_/年.]?(\d{1,2})[-_/月.]?(\d{1,2})", text)
    if not m:
        return ""
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return ""


def _metric_float(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(str(value).strip().replace(",", ""))
    except (ValueError, TypeError):
        return None


def parse_filename(filename: str, explicit: dict | None = None, *, test_dates=()) -> dict:
    """Parse identity tokens from a file name while preserving the original spelling."""
    explicit = explicit or {}
    name = Path(str(filename).replace("\\", "/")).name
    stem = Path(name).stem
    result = {k: (str(v).strip() if v is not None else "") for k, v in explicit.items()}
    date_text = normalize_date(result.get("date")) or normalize_date(stem)
    if not date_text:
        for value in test_dates:
            candidate = normalize_date(value)
            if candidate and candidate.replace("-", "") in re.sub(r"\D", "", stem):
                date_text = candidate
                break
    result["date"] = date_text

    m = re.search(r"(?:camera|cam|相機|c)[ _-]*([12])(?![A-Za-z0-9])", stem, re.I)
    if m and not result.get("camera"):
        result["camera"] = f"Camera {m.group(1)}"
    elif result.get("camera"):
        cm = re.search(r"([12])", result["camera"])
        if cm:
            result["camera"] = f"Camera {cm.group(1)}"

    m = re.search(r"(?:test|測試)[ _-]*([a-z0-9]+)", stem, re.I)
    if m and not result.get("test"):
        result["test"] = m.group(1)

    m = re.search(r"(?:^|[_ -])P\s*([1-9]\d*)(?:[_ -]|$)", stem, re.I)
    if m and not result.get("phase"):
        result["phase"] = f"P{int(m.group(1))}"
    elif result.get("phase"):
        pm = re.search(r"P\s*([1-9]\d*)", result["phase"], re.I)
        if pm:
            result["phase"] = f"P{int(pm.group(1))}"

    m = re.search(r"(?:^|[_ -])([A-Z])(?:[_ -]|$)", stem)
    if m and not result.get("run_letter"):
        result["run_letter"] = m.group(1).upper()
    elif result.get("run_letter"):
        lm = re.search(r"[A-Z]", result["run_letter"].upper())
        if lm:
            result["run_letter"] = lm.group(0)

    if not result.get("event"):
        if re.search(r"first[ _-]*detection|firstdetect|首次檢出|初次檢出", stem, re.I):
            result["event"] = "FirstDetection"
        elif re.search(r"stable[ _-]*start|穩定起點", stem, re.I):
            result["event"] = "StableStart"

    if not result.get("frame"):
        m = re.search(r"(?:frame|frm|f)[ _-]*(\d{1,9})(?!\d)", stem, re.I)
        if not m:
            # The current evaluation export places its 1-based frame token directly after the event.
            m = re.search(r"(?:first[ _-]*detection|stable[ _-]*start)[_-](\d{4,9})(?:[_-]raw)?(?:[_-]|$)", stem, re.I)
        if m:
            result["frame"] = m.group(1)
    if result.get("frame"):
        try:
            result["frame"] = int(float(result["frame"]))
        except (TypeError, ValueError):
            pass

    # These suffixes are retained as a comparison signal; they never override CSV distance.
    distance = re.search(r"(?:^|[_-])(?:distance|dist|d)[ _-]*(\d+)(?:[_.](\d+))?m?(?:[_-]|$)", stem, re.I)
    result["filename_distance_m"] = float(distance.group(1) + ("." + distance.group(2) if distance.group(2) else "")) if distance else None
    speed = re.search(r"(?:^|[_ -])(?:speed|spd|v)[ _-]*(\d+(?:\.\d+)?)\s*(kt|kts|kn|kmh|kph)?(?:[_ -]|$)", stem, re.I)
    if not speed:
        speed = re.search(r"(?:^|[_ -])(\d+(?:\.\d+)?)\s*(kt|kts|kn|kmh|kph)(?:[_ -]|$)", stem, re.I)
    result["speed"] = f"{speed.group(1)} {speed.group(2) or ''}".strip() if speed else ""
    result["filename"] = name
    result["stem"] = stem
    result["event"] = canonical_event(result.get("event"))
    return result


def canonical_event(value: object) -> str:
    text = _norm(str(value or ""))
    if text in {"firstdetection", "first", "manualfirstdetection", "首次檢出", "人工首次檢出"}:
        return "FirstDetection"
    if text in {"stablestart", "firstsustainedstablestart", "manualsustainedstablestart", "stable", "穩定起點", "人工穩定起點"}:
        return "StableStart"
    return str(value or "").strip()


def history_run_mapping(parsed: dict) -> dict | None:
    """Apply the documented 9/18 and 9/22 legacy A/P1-P4 naming correction."""
    day = str(parsed.get("date", ""))
    letter, phase = str(parsed.get("run_letter", "")), str(parsed.get("phase", ""))
    if day not in {"2026-09-18", "2026-09-22"} or phase not in {"P1", "P2", "P3", "P4"}:
        return None
    mapped = {"P1": "A", "P2": "B", "P3": "C", "P4": "D"}[phase]
    # Filenames may already be renamed while the pairing record still stores angle A.
    if letter not in {"A", mapped}:
        return None
    return {"date": day, "original_run_letter": "A", "run_letter": mapped,
            "phase": phase, "rule": "legacy_A_P1-P4_to_A-D_by_phase"}
