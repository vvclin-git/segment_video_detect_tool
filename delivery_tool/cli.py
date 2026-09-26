from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .builder import build
from .config import load_config
from .dataset import validate_config


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m delivery_tool", description="Sea Trial Result Builder")
    parser.add_argument("--config", help="設定檔 JSON")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--validate-only", action="store_true", help="只檢查輸入與關聯，不產生交付包")
    action.add_argument("--build", action="store_true", help="產生 HTML、PDF、CSV 與 Collage")
    args = parser.parse_args(argv)
    if not args.config:
        if args.validate_only or args.build:
            parser.error("--validate-only／--build 需要 --config")
        from .gui import launch
        launch()
        return 0
    try:
        config = load_config(args.config)
        if args.validate_only:
            result = validate_config(config)
            payload = {"ok": result["ok"], "record_count": result["record_count"],
                       "counts": result["counts"], "errors": result["errors"], "warnings": result["warnings"]}
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 2
        if args.build:
            result = build(config, progress=lambda done, total, message: print(f"[{done}/{total}] {message}", file=sys.stderr))
            print(json.dumps({"delivery_dir": str(result["delivery_dir"]),
                              "index_html": str(result["index_html"]),
                              "pdfs": [str(p) for p in result["pdfs"]],
                              "csv": str(result["csv"]), "manifest": str(result["manifest"]),
                              "validation": str(result["validation"]), "counts": result["counts"],
                              "record_count": result["record_count"], "warnings": result["warnings"]}, ensure_ascii=False, indent=2))
            return 0
        from .gui import launch
        launch(args.config)
        return 0
    except (OSError, ValueError, json.JSONDecodeError, InterruptedError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
