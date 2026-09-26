from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


CYAN = (0, 210, 255)
RED = (255, 50, 50)


def _font(size: int):
    for name in ("msjh.ttc", "msjhbd.ttc", "mingliu.ttc", "arial.ttf"):
        candidate = Path("C:/Windows/Fonts") / name
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size=size, index=0)
            except (OSError, TypeError):
                pass
    return ImageFont.load_default()


def _overlay(base: Image.Image, gt_path: Path | None, mask_path: Path | None) -> Image.Image:
    rgb = base.convert("RGB")
    pixels = np.asarray(rgb).copy()
    with Image.open(mask_path) as mask_image:
        mask = np.asarray(mask_image.convert("L")) > 0
    if mask.shape != (rgb.height, rgb.width):
        raise ValueError(f"Mask 座標尺寸 {mask.shape[1]}x{mask.shape[0]} 與底圖 {rgb.width}x{rgb.height} 不符")
    # Blend only foreground pixels; the source image background stays byte-for-byte unchanged.
    source = pixels[mask].astype(np.uint16)
    pixels[mask] = ((source * 60 + np.asarray(CYAN, dtype=np.uint16) * 40) / 100).astype(np.uint8)
    result = Image.fromarray(pixels, mode="RGB")
    draw = ImageDraw.Draw(result)
    if gt_path:
        data = json.loads(gt_path.read_text(encoding="utf-8-sig"))
        width = max(1, round(3 * rgb.width / 960))
        for shape in data.get("shapes", []):
            points = [(round(float(x)), round(float(y))) for x, y in shape["points"]]
            draw.line(points + [points[0]], fill=RED, width=width, joint="curve")
    return result


def render_collage(record: dict, output: Path) -> Path | None:
    """Write the exact-range, side-by-side event collage. Source files are never changed."""
    if not record.get("image_path"):
        return None
    base_path = Path(record["image_path"])
    with Image.open(base_path) as image:
        base = image.convert("RGB")
    attachment = record.get("attachment", {})
    gt = Path(attachment["labelme_path"]) if attachment.get("labelme_path") else None
    mask = Path(attachment["mask_path"]) if attachment.get("mask_path") else None
    complete = attachment.get("gt_status") == "valid" and attachment.get("mask_status") == "valid"
    # The right panel is deliberately a placeholder unless both aligned sources are valid.
    right = _overlay(base, gt, mask) if complete and gt and mask else None
    title_height, footer_height, gutter = 48, 44, 28
    canvas = Image.new("RGB", (base.width * 2 + gutter, base.height + title_height + footer_height), "white")
    draw = ImageDraw.Draw(canvas)
    title_font = _font(max(16, round(base.width / 45)))
    small_font = _font(max(12, round(base.width / 72)))
    draw.text((base.width // 2, 9), "原始影像", fill=(32, 48, 60), font=title_font, anchor="mt")
    draw.text((base.width + gutter + base.width // 2, 9), "人工標註＋Mask", fill=(32, 48, 60), font=title_font, anchor="mt")
    canvas.paste(base, (0, title_height))
    if right:
        canvas.paste(right, (base.width + gutter, title_height))
        y = title_height + base.height + 12
        draw.line((22, y + 8, 56, y + 8), fill=RED, width=max(2, round(3 * base.width / 960)))
        draw.text((64, y), "紅線：人工標註", fill=(32, 48, 60), font=small_font)
        draw.rounded_rectangle((base.width + gutter + 225, y + 1, base.width + gutter + 255, y + 17), radius=4, fill=CYAN)
        draw.text((base.width + gutter + 264, y), "青色：預測 Mask（40%）", fill=(32, 48, 60), font=small_font)
    else:
        messages = []
        if attachment.get("gt_status") != "valid":
            messages.append("缺少有效人工標註 GT")
        if attachment.get("mask_status") != "valid":
            messages.append("缺少有效對齊預測 Mask")
        text = "\n".join(messages) or "疊圖附件未提供"
        draw.multiline_text((base.width + gutter + base.width // 2, title_height + base.height // 2),
                            text, fill=(140, 48, 48), font=title_font, anchor="mm", align="center", spacing=8)
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="PNG", optimize=True)
    return output


def copy_keyframe(record: dict, delivery_dir: Path) -> str:
    if not record.get("image_path"):
        return ""
    source = Path(record["image_path"])
    day = record.get("date") or "unknown_date"
    destination = delivery_dir / "keyframes" / day / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copy2(source, destination)
    return destination.relative_to(delivery_dir).as_posix()

