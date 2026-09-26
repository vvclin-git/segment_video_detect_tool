from __future__ import annotations

import json
from pathlib import Path


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
    },
}


def load_config(path: str | Path) -> dict:
    path = Path(path).expanduser().resolve()
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("設定檔必須是 schema_version 1 的 JSON 物件")
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update(data)
    report = dict(DEFAULT_CONFIG["report"])
    report.update(data.get("report") or {})
    merged["report"] = report
    merged["_config_path"] = str(path)
    merged["_config_dir"] = str(path.parent)
    return merged


def save_config(path: str | Path, data: dict) -> Path:
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in data.items() if not k.startswith("_")}
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
