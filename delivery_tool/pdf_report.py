from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from .config import resolve_path
from PIL import Image


PAGE_W, PAGE_H = landscape(A4)
FONT_NAME = "SeaTrialZH"
GRID_COLOR = colors.HexColor("#b7c7cd")
GRID_LINE_WIDTH = 0.7


def _set_grid(pdf):
    pdf.setStrokeColor(GRID_COLOR)
    pdf.setLineWidth(GRID_LINE_WIDTH)


def resolve_chinese_font(config: dict) -> str | None:
    report = config.get("report", {})
    configured = str(report.get("font_path") or "").strip()
    configured_path = resolve_path(config, configured) if configured else None
    candidates = [configured_path] if configured_path else []
    candidates += [Path("C:/Windows/Fonts/msjh.ttc"), Path("C:/Windows/Fonts/msjhbd.ttc"),
                   Path("C:/Windows/Fonts/mingliu.ttc"), Path("C:/Windows/Fonts/simsun.ttc")]
    for path in candidates:
        if path.is_file():
            try:
                font = TTFont(FONT_NAME, str(path), subfontIndex=0)
            except Exception:
                try:
                    font = TTFont(FONT_NAME, str(path))
                except Exception:
                    continue
            cmap = getattr(getattr(font, "face", None), "charToGlyph", {})
            required = "海試結果報告測試航次相機事件未提供人工標註預測距離Frame"
            if all(ord(char) in cmap and cmap[ord(char)] != 0 for char in required):
                return str(path)
    return None


def _register_font(config: dict) -> str:
    path = resolve_chinese_font(config)
    if not path:
        raise ValueError("找不到可用中文字型；請在 report.font_path 設定 TTF/TTC 字型")
    if FONT_NAME not in pdfmetrics.getRegisteredFontNames():
        try:
            pdfmetrics.registerFont(TTFont(FONT_NAME, path, subfontIndex=0))
        except Exception:
            pdfmetrics.registerFont(TTFont(FONT_NAME, path))
    return path


def _fmt(value, suffix=""):
    if value is None or value == "":
        return "未提供"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".") + suffix
    return f"{value}{suffix}"


def _short(text: str, max_chars: int = 42) -> str:
    text = str(text or "")
    return text if len(text) <= max_chars else text[:max_chars - 1] + "…"


def _footer(pdf, page_num):
    pdf.setStrokeColor(colors.HexColor("#d6e0e4"))
    pdf.line(24, 22, PAGE_W - 24, 22)
    pdf.setFont(FONT_NAME, 8)
    pdf.setFillColor(colors.HexColor("#667983"))
    pdf.drawRightString(PAGE_W - 26, 10, str(page_num))


def _draw_header(pdf, title, subtitle=""):
    pdf.setFillColor(colors.HexColor("#153e4d"))
    pdf.rect(0, PAGE_H - 64, PAGE_W, 64, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont(FONT_NAME, 18)
    pdf.drawString(28, PAGE_H - 32, title)
    pdf.setFont(FONT_NAME, 9)
    pdf.drawString(29, PAGE_H - 50, subtitle)


def _draw_image(pdf, path: str, x, y, width, height):
    if path:
        candidate = Path(path)
        if candidate.is_file():
            try:
                pdf_image = candidate
                if candidate.suffix.lower() != ".jpg":
                    pdf_image = candidate.parents[2] / "internal" / "pdf_images" / f"{candidate.stem}.jpg"
                    pdf_image.parent.mkdir(parents=True, exist_ok=True)
                    if not pdf_image.exists():
                        with Image.open(candidate) as source:
                            image_rgb = source.convert("RGB")
                            if image_rgb.width > 1200:
                                height_px = round(image_rgb.height * 1200 / image_rgb.width)
                                image_rgb = image_rgb.resize((1200, height_px), Image.Resampling.LANCZOS)
                            image_rgb.save(pdf_image, format="JPEG", quality=88, subsampling=0, optimize=True)
                image = ImageReader(str(pdf_image))
                iw, ih = image.getSize()
                scale = min(width / iw, height / ih)
                dw, dh = iw * scale, ih * scale
                pdf.drawImage(image, x + (width - dw) / 2, y + (height - dh) / 2,
                              dw, dh, preserveAspectRatio=True, mask="auto")
                return
            except Exception:
                pass
    pdf.setFillColor(colors.HexColor("#f5f7f8"))
    _set_grid(pdf)
    pdf.roundRect(x, y, width, height, 3, fill=1, stroke=1)
    pdf.setFillColor(colors.HexColor("#825b4b"))
    pdf.setFont(FONT_NAME, 8)
    pdf.drawCentredString(x + width / 2, y + height / 2 - 3, "缺少事件圖片／附件")


def _draw_metric(pdf, record, x, y, width):
    pdf.setFont(FONT_NAME, 8)
    pdf.setFillColor(colors.HexColor("#20333c"))
    pdf.drawString(x, y + 7, f"Frame {_fmt(record.get('frame'))}　mIoU {_fmt(record.get('iou'))}")
    pdf.drawString(x, y - 2, f"距離 {_fmt(record.get('distance_m'), ' m')}")


def _draw_cell(pdf, record, x, y, width, height, delivery_dir):
    _set_grid(pdf)
    pdf.rect(x, y, width, height, fill=0, stroke=1)
    if not record:
        pdf.setFont(FONT_NAME, 9)
        pdf.setFillColor(colors.HexColor("#7d8d94"))
        pdf.drawCentredString(x + width / 2, y + height / 2, "缺少此事件，未挪用其他事件")
        return
    collage = str(record.get("collage", ""))
    collage_path = str((delivery_dir / collage).resolve()) if collage else ""
    metric_height = 19
    _draw_image(pdf, collage_path, x + 4, y + metric_height + 2, width - 8, height - metric_height - 6)
    _draw_metric(pdf, record, x + 5, y + 5, width - 10)


def _record_groups(manifest: dict):
    return manifest.get("records", [])


def _overview_groups(records):
    groups = defaultdict(list)
    for r in records:
        p = r.get("parsed", {})
        groups[(r.get("date", ""), r.get("test", ""), str(r.get("batch_id", "")))].append(r)
    return sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0]))


def _run_rows(records):
    groups = defaultdict(list)
    for r in records:
        # Confirmed pairing runs are the primary key; missing links use all identity tokens to avoid accidental merging.
        link = r.get("analysis_link", {})
        run_id = link.get("pairing_run_id", "")
        fallback = (r.get("batch_id", ""), r.get("date", ""), r.get("test", ""), r.get("run_letter", ""), r.get("phase", ""))
        key = (r.get("date", ""), r.get("test", ""), r.get("run_letter", ""), r.get("phase", ""), run_id or fallback)
        groups[key].append(r)
    return sorted(groups.items(), key=lambda kv: tuple(str(x) for x in kv[0]))


def _matching(records, camera, event):
    return next((r for r in records if str(r.get("camera", "")).endswith(str(camera)) and r.get("event") == event), None)


def _draw_cover(pdf, manifest, config, layout):
    report = config.get("report", {})
    pdf.setFillColor(colors.HexColor("#123b4a"))
    pdf.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    logo = report.get("logo")
    logo_path = Path(logo) if logo else None
    if logo_path and not logo_path.is_absolute():
        logo_path = Path(config.get("_config_dir", ".")) / logo_path
    if logo_path and logo_path.is_file():
        try:
            pdf.drawImage(str(logo_path), PAGE_W - 150, PAGE_H - 115, 105, 55, preserveAspectRatio=True, mask="auto")
        except Exception:
            pass
    pdf.setFont(FONT_NAME, 25)
    pdf.drawString(54, PAGE_H - 160, str(report.get("report_name") or "海試結果報告"))
    pdf.setFont(FONT_NAME, 13)
    pdf.drawString(56, PAGE_H - 194, str(report.get("customer_project") or ""))
    pdf.setFont(FONT_NAME, 11)
    dates = "、".join(str(x) for x in manifest.get("test_dates", [])) or "未指定日期"
    pdf.drawString(56, PAGE_H - 235, f"測試日期：{dates}　批次：{manifest.get('batch_id') or '未命名'}　版本：{report.get('version') or '1.0'}")
    pdf.drawString(56, PAGE_H - 260, f"版型：{'測試總覽' if layout == 'overview' else '航次明細'}")
    scope = str(report.get("scope") or "")
    note = str(report.get("notes") or "")
    pdf.setFont(FONT_NAME, 10)
    if scope:
        pdf.drawString(56, PAGE_H - 310, "納入範圍：" + _short(scope, 90))
    if note:
        pdf.drawString(56, PAGE_H - 340, "備註：" + _short(note, 90))
    pdf.setFont(FONT_NAME, 9)
    pdf.drawString(56, 42, f"評估事件 {len(manifest.get('records', []))} 筆 · 報告由離線 Result Builder 產生")
    pdf.showPage()


def _draw_summary(pdf, manifest, records, page_num):
    _draw_header(pdf, "測試總表", f"評估事件 {len(records)} 筆 · mIoU 與距離均保留每日 CSV 原值")
    groups = defaultdict(list)
    for r in records:
        groups[(r.get("date", ""), r.get("test", ""), r.get("batch_id", ""))].append(r)
    y = PAGE_H - 96
    pdf.setFont(FONT_NAME, 9)
    pdf.setFillColor(colors.HexColor("#173d4b"))
    heads = ["日期／Test／批次", "事件數", "圖片", "GT", "Mask", "已連結", "未連結"]
    widths = [270, 66, 66, 66, 66, 85, 85]
    x = 28
    _set_grid(pdf)
    for head, width in zip(heads, widths):
        pdf.setFillColor(colors.HexColor("#e6eef1")); pdf.rect(x, y - 18, width, 22, fill=1, stroke=1)
        pdf.setFillColor(colors.HexColor("#173d4b")); pdf.drawString(x + 5, y - 10, head); x += width
    y -= 22
    grouped_rows = sorted(groups.items())
    for (day, test, batch), rows in grouped_rows:
        # Start a new page only when another data row still needs a slot.
        # Checking after the final row creates a blank continuation page when
        # the table happens to end at the bottom boundary.
        if y < 55:
            _footer(pdf, page_num)
            pdf.showPage()
            page_num += 1
            _draw_header(pdf, "測試總表（續）")
            y = PAGE_H - 96
            _set_grid(pdf)
        vals = [f"{day}／{test}／{batch}", str(len(rows)), str(sum(bool(x.get("image_path")) for x in rows)),
                str(sum(x.get("attachment", {}).get("gt_status") == "valid" for x in rows)),
                str(sum(x.get("attachment", {}).get("mask_status") == "valid" for x in rows)),
                str(sum(x.get("analysis_link", {}).get("status") == "linked" for x in rows)),
                str(sum(x.get("analysis_link", {}).get("status") != "linked" for x in rows))]
        x = 28
        for val, width in zip(vals, widths):
            pdf.setFillColor(colors.white); pdf.rect(x, y - 18, width, 22, fill=1, stroke=1)
            pdf.setFillColor(colors.HexColor("#253942")); pdf.drawString(x + 5, y - 10, _short(val, max(12, int(width / 5))))
            x += width
        y -= 22
    _footer(pdf, page_num); pdf.showPage()
    return page_num + 1


def _draw_overview(pdf, records, delivery_dir, page_num):
    for (day, test, batch), rows in _overview_groups(records):
        runs = defaultdict(list)
        for record in rows:
            runs[(record.get("run_letter", ""), record.get("phase", ""))].append(record)
        ordered = sorted(runs.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))
        pages = [ordered[i:i + 4] for i in range(0, len(ordered), 4)] or [[]]
        for page_index, page_rows in enumerate(pages):
            _draw_header(pdf, "海試結果測試總覽", f"日期 {day}　Test {test}　批次 {batch or '未命名'}" + (f"　續頁 {page_index + 1}" if len(pages) > 1 else ""))
            left = 28; label_w = 94; gap = 4; col_w = (PAGE_W - 56 - label_w - 4 * gap) / 4
            top = PAGE_H - 82; head_h = 24; row_h = 111
            pdf.setFont(FONT_NAME, 9)
            headers = ["航次／Phase", "Camera 1 First", "Camera 1 Stable", "Camera 2 First", "Camera 2 Stable"]
            widths = [label_w] + [col_w] * 4
            x = left
            _set_grid(pdf)
            for title, width in zip(headers, widths):
                pdf.setFillColor(colors.HexColor("#dce9ed")); pdf.rect(x, top - head_h, width, head_h, fill=1, stroke=1)
                pdf.setFillColor(colors.HexColor("#173d4b")); pdf.drawCentredString(x + width / 2, top - 16, title)
                x += width + gap
            ytop = top - head_h
            for (letter, phase), run_records in page_rows:
                y = ytop - row_h
                _set_grid(pdf)
                pdf.setFillColor(colors.HexColor("#f3f6f7")); pdf.rect(left, y, label_w, row_h, fill=1, stroke=1)
                pdf.setFillColor(colors.HexColor("#203b45")); pdf.setFont(FONT_NAME, 10)
                pdf.drawCentredString(left + label_w / 2, y + row_h / 2 + 6, f"{letter or '—'}／{phase or '—'}")
                conditions = next((r.get("parsed", {}).get("speed") for r in run_records if r.get("parsed", {}).get("speed")), "")
                if conditions:
                    pdf.setFont(FONT_NAME, 8); pdf.drawCentredString(left + label_w / 2, y + row_h / 2 - 9, str(conditions))
                x = left + label_w + gap
                for camera, event in (("1", "FirstDetection"), ("1", "StableStart"), ("2", "FirstDetection"), ("2", "StableStart")):
                    _draw_cell(pdf, _matching(run_records, camera, event), x, y, col_w, row_h, delivery_dir)
                    x += col_w + gap
                ytop = y
            _footer(pdf, page_num); page_num += 1; pdf.showPage()
    return page_num


def _draw_run_detail(pdf, records, delivery_dir, page_num):
    for key, rows in _run_rows(records):
        day, test, letter, phase, _run_id = key
        header_record = rows[0] if rows else {}
        title = f"航次明細　{day}　Test {test}　{letter}／{phase}"
        conditions = header_record.get("parsed", {}).get("speed", "")
        _draw_header(pdf, title, f"測試條件：{conditions or '未提供'}")
        left, gap = 28, 8
        col_w = (PAGE_W - 56 - gap) / 2
        top = PAGE_H - 82; head_h = 26; row_h = 213
        _set_grid(pdf)
        for col, title in enumerate(("Camera 1", "Camera 2")):
            x = left + col * (col_w + gap)
            pdf.setFillColor(colors.HexColor("#dce9ed")); pdf.rect(x, top - head_h, col_w, head_h, fill=1, stroke=1)
            pdf.setFillColor(colors.HexColor("#173d4b")); pdf.setFont(FONT_NAME, 10)
            pdf.drawCentredString(x + col_w / 2, top - 18, title)
        ytop = top - head_h
        for event, label in (("FirstDetection", "FirstDetection"), ("StableStart", "StableStart")):
            header_y = ytop - 20
            _set_grid(pdf)
            pdf.setFillColor(colors.HexColor("#eef3f5")); pdf.rect(left, header_y, PAGE_W - 56, 20, fill=1, stroke=1)
            pdf.setFillColor(colors.HexColor("#173d4b")); pdf.setFont(FONT_NAME, 9); pdf.drawString(left + 6, header_y + 6, label)
            y = header_y - 193
            for cam in ("1", "2"):
                x = left + (0 if cam == "1" else col_w + gap)
                record = _matching(rows, cam, event)
                _draw_cell(pdf, record, x, y, col_w, 193, delivery_dir)
            ytop = y
        _footer(pdf, page_num); page_num += 1; pdf.showPage()
    return page_num


def _draw_appendices(pdf, manifest, config, page_num):
    report = config.get("report", {})
    notes = str(report.get("notes") or "")
    warnings = manifest.get("validation", {}).get("warnings", [])
    errors = manifest.get("validation", {}).get("errors", [])
    pages = []
    if len(notes) > 260:
        pages.append(("備註附錄", [notes[i:i + 1600] for i in range(0, len(notes), 1600)]))
    if report.get("missing_appendix") and (warnings or errors):
        texts = ["錯誤：" + x.get("message", "") for x in errors] + ["警告：" + x.get("message", "") for x in warnings]
        pages.append(("缺漏附錄", texts))
    for title, contents in pages:
        _draw_header(pdf, title)
        y = PAGE_H - 94
        pdf.setFont(FONT_NAME, 9); pdf.setFillColor(colors.HexColor("#263b43"))
        for item in contents:
            lines = [item[i:i + 100] for i in range(0, len(item), 100)] or [""]
            for line in lines:
                if y < 40:
                    _footer(pdf, page_num); page_num += 1; pdf.showPage(); _draw_header(pdf, title + "（續）"); y = PAGE_H - 94
                pdf.drawString(30, y, line); y -= 14
            y -= 7
        _footer(pdf, page_num); page_num += 1; pdf.showPage()
    return page_num


def build_pdf(path: Path, manifest: dict, config: dict, delivery_dir: Path, layout: str) -> Path:
    _register_font(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf = canvas.Canvas(str(path), pagesize=landscape(A4), pageCompression=1)
    records = manifest.get("records", [])
    report = config.get("report", {})
    if report.get("cover", True):
        _draw_cover(pdf, manifest, config, layout)
    page_num = 1 + int(bool(report.get("cover", True)))
    if report.get("summary_table", True):
        page_num = _draw_summary(pdf, manifest, records, page_num)
    page_num = _draw_overview(pdf, records, delivery_dir, page_num) if layout == "overview" else _draw_run_detail(pdf, records, delivery_dir, page_num)
    _draw_appendices(pdf, manifest, config, page_num)
    pdf.save()
    return path
