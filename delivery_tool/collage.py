from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .config import comparison_overlay_settings


REGION_NAMES = {
    "overlap": "GT 與預測重疊",
    "gt_only": "僅 GT",
    "prediction_only": "僅預測",
}


def _font(size: int):
    for name in ("msjh.ttc", "msjhbd.ttc", "mingliu.ttc", "arial.ttf"):
        candidate = Path("C:/Windows/Fonts") / name
        if candidate.is_file():
            try:
                return ImageFont.truetype(str(candidate), size=size, index=0)
            except (OSError, TypeError):
                pass
    return ImageFont.load_default()


def load_comparison_layers(image_path: str | Path, gt_path: str | Path, mask_path: str | Path):
    """Load one event and rasterize its two masks once for preview/render reuse."""
    with Image.open(image_path) as source:
        base = np.asarray(source.convert("RGB")).copy()
    return base, comparison_regions(base, gt_path, mask_path)


def comparison_regions(base: Image.Image | np.ndarray, gt_path: str | Path, mask_path: str | Path):
    """Rasterize LabelMe GT and aligned prediction masks against a given RGB image."""
    import json

    base = np.asarray(base.convert("RGB") if isinstance(base, Image.Image) else base)
    height, width = base.shape[:2]
    data = json.loads(Path(gt_path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not isinstance(data.get("shapes"), list):
        raise ValueError("LabelMe JSON 缺少 shapes")
    try:
        label_size = (int(data.get("imageWidth")), int(data.get("imageHeight")))
    except (TypeError, ValueError):
        label_size = (0, 0)
    if label_size != (width, height):
        raise ValueError(f"LabelMe 座標尺寸 {label_size[0]}x{label_size[1]} 與原圖 {width}x{height} 不符")
    gt_image = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(gt_image)
    for shape in data["shapes"]:
        if shape.get("shape_type", "polygon") != "polygon" or shape.get("label") not in {"ship", "buoy"}:
            raise ValueError(f"不支援的 LabelMe shape：{shape.get('label')} / {shape.get('shape_type')}")
        points = shape.get("points")
        if not isinstance(points, list) or len(points) < 3:
            raise ValueError("LabelMe polygon 座標無效")
        converted = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError("LabelMe polygon 座標無效")
            x, y = float(point[0]), float(point[1])
            if not np.isfinite(x) or not np.isfinite(y) or not (0 <= x < width and 0 <= y < height):
                raise ValueError("LabelMe polygon 座標超出標註影像範圍")
            converted.append((int(x), int(y)))
        draw.polygon(converted, fill=255)
    gt = np.asarray(gt_image) > 127
    with Image.open(mask_path) as mask_image:
        mask_image.load()
        if mask_image.size != (width, height):
            raise ValueError(f"Mask 座標尺寸 {mask_image.width}x{mask_image.height} 與原圖 {width}x{height} 不符")
        channels = mask_image.convert("RGB").split()
        if channels[0].tobytes() != channels[1].tobytes() or channels[0].tobytes() != channels[2].tobytes():
            raise ValueError("Mask 附件是彩色影像；拒絕將 Seg／彩圖當作二值 Mask")
        pred = np.asarray(mask_image.convert("L")) > 127
    return {"overlap": gt & pred, "gt_only": gt & ~pred, "prediction_only": ~gt & pred}


def render_comparison(base: np.ndarray, regions: dict, settings: dict | None = None) -> Image.Image:
    """Blend mutually exclusive GT/prediction classes over an unmodified RGB base."""
    overlay = comparison_overlay_settings(settings)
    pixels = np.asarray(base, dtype=np.float32).copy()
    opacity = overlay["opacity"]
    for key in ("overlap", "gt_only", "prediction_only"):
        color = tuple(int(overlay["colors"][key][i:i + 2], 16) for i in (1, 3, 5))
        region = regions[key]
        # Keep NumPy's reference arithmetic: float32 source term + int64 color
        # term (promoted during addition), assigned back into the float32 image.
        pixels[region] = pixels[region] * (1.0 - opacity) + np.asarray(color) * opacity
    return Image.fromarray(pixels.clip(0, 255).astype(np.uint8), mode="RGB")


def render_comparison_paths(image_path: str | Path, gt_path: str | Path, mask_path: str | Path,
                            settings: dict | None = None) -> Image.Image:
    base, regions = load_comparison_layers(image_path, gt_path, mask_path)
    return render_comparison(base, regions, settings)


def _overlay(base: Image.Image, gt_path: Path, mask_path: Path, settings: dict | None = None,
             pixel_counts: dict | None = None) -> Image.Image:
    base_array = np.asarray(base.convert("RGB")).copy()
    regions = comparison_regions(base_array, gt_path, mask_path)
    if pixel_counts is not None:
        pixel_counts.update({
            "tp": int(np.count_nonzero(regions["overlap"])),
            "fn": int(np.count_nonzero(regions["gt_only"])),
            "fp": int(np.count_nonzero(regions["prediction_only"])),
        })
    return render_comparison(base_array, regions, settings)


def render_collage(record: dict, output: Path, *, overlay_output: Path | None = None,
                   comparison_overlay: dict | None = None,
                   pixel_counts: dict | None = None) -> Path | None:
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
    settings = comparison_overlay_settings(comparison_overlay)
    right = _overlay(base, gt, mask, settings, pixel_counts=pixel_counts) if complete and gt and mask else None
    if right is not None and overlay_output is not None:
        overlay_output.parent.mkdir(parents=True, exist_ok=True)
        right.save(overlay_output, format="PNG")
    title_height, footer_height, gutter = 48, 44, 28
    canvas = Image.new("RGB", (base.width * 3 + gutter * 2, base.height + title_height + footer_height), "white")
    draw = ImageDraw.Draw(canvas)
    title_font = _font(max(16, round(base.width / 45)))
    small_font = _font(max(10, round(base.width / 96)))
    draw.text((base.width // 2, 9), "原始影像", fill=(32, 48, 60), font=title_font, anchor="mt")
    draw.text((2 * (base.width + gutter) + base.width // 2, 9), "GT／預測比較", fill=(32, 48, 60), font=title_font, anchor="mt")
    canvas.paste(base, (0, title_height))
    middle_x = base.width + gutter
    draw.text((middle_x + base.width // 2, 9), "分割影像", fill=(32, 48, 60), font=title_font, anchor="mt")
    if attachment.get("seg_status") == "valid" and attachment.get("seg_path"):
        with Image.open(attachment["seg_path"]) as segmented:
            if segmented.size != base.size:
                raise ValueError("分割影像與原圖尺寸不一致")
            canvas.paste(segmented.convert("RGB"), (middle_x, title_height))
    else:
        draw.text((middle_x + base.width // 2, title_height + base.height // 2),
                  "未提供分割影像", fill=(140, 48, 48), font=title_font, anchor="mm")
    if right:
        canvas.paste(right, (2 * (base.width + gutter), title_height))
        y = title_height + base.height + 12
        right_x = 2 * (base.width + gutter)
        legend_items = [("overlap", REGION_NAMES["overlap"]), ("gt_only", REGION_NAMES["gt_only"]),
                        ("prediction_only", REGION_NAMES["prediction_only"])]
        positions = (0.01, 0.25, 0.43)
        for (key, label), position in zip(legend_items, positions):
            color = tuple(int(settings["colors"][key][i:i + 2], 16) for i in (1, 3, 5))
            x = right_x + round(base.width * position)
            draw.rectangle((x, y + 1, x + 15, y + 14), fill=color)
            draw.text((x + 20, y), label, fill=(32, 48, 60), font=small_font)
        draw.text((right_x + round(base.width * 0.65), y),
                  f"疊圖不透明度：{round(settings['opacity'] * 100)}%", fill=(32, 48, 60), font=small_font)
    else:
        messages = []
        if attachment.get("gt_status") != "valid":
            messages.append("缺少有效人工標註 GT")
        if attachment.get("mask_status") != "valid":
            messages.append("缺少有效對齊預測 Mask")
        text = "\n".join(messages) or "疊圖附件未提供"
        draw.multiline_text((2 * (base.width + gutter) + base.width // 2, title_height + base.height // 2),
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

