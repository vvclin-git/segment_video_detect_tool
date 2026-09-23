"""Original-resolution frame composition helpers shared by the UI/exporters.

The GUI deliberately keeps the preview image resized, but every function in
this module works on the decoded, original-resolution frame.  Keeping the
composition here avoids small differences between a screenshot-like preview
and an exported PNG.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable, Mapping

import cv2
import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFont

IMAGE_MODES = ("Original", "Mask", "Overlay")


@dataclass(frozen=True)
class BboxStyle:
    """Style used only when composing a single annotated frame.

    Detection settings (HSV, ROI and minimum area) intentionally live in a
    separate dictionary.  This object is never persisted in a project.
    """

    color: str = "#00FF00"
    line_width: int = 2
    font_size: int = 36
    label_mode: str = "size"


DEFAULT_BBOX_STYLE = BboxStyle()

# Batch event images predate the configurable single-frame renderer.  Keeping
# this marker lets those images use their existing OpenCV appearance without
# making the new single-frame default depend on batch-export details.
LEGACY_BBOX_STYLE = BboxStyle(color="#00FF00", line_width=2, font_size=0,
                              label_mode="legacy")

LABEL_MODES = ("size", "detailed")
LABEL_MODE_NAMES = {"size": "尺寸", "detailed": "編號＋座標＋尺寸"}
_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")


def validate_bbox_style(style: BboxStyle | Mapping[str, object]) -> BboxStyle:
    """Validate and normalize a UI/API-provided bbox style."""
    if isinstance(style, BboxStyle):
        value = style
    else:
        value = BboxStyle(
            color=str(style.get("color", DEFAULT_BBOX_STYLE.color)),
            line_width=style.get("line_width", DEFAULT_BBOX_STYLE.line_width),
            font_size=style.get("font_size", DEFAULT_BBOX_STYLE.font_size),
            label_mode=str(style.get("label_mode", DEFAULT_BBOX_STYLE.label_mode)),
        )
    if not isinstance(value.color, str) or not _HEX_COLOR.fullmatch(value.color):
        raise ValueError("框線／文字顏色必須是 #RRGGBB 格式")
    try:
        line_width = int(value.line_width)
        font_size = int(value.font_size)
    except (TypeError, ValueError) as exc:
        raise ValueError("線寬與字體大小須為正整數") from exc
    # bool is an int subclass but is not a meaningful UI value here.
    if isinstance(value.line_width, bool) or isinstance(value.font_size, bool):
        raise ValueError("線寬與字體大小須為正整數")
    if line_width <= 0 or font_size <= 0:
        raise ValueError("線寬與字體大小須為正整數")
    if value.label_mode not in LABEL_MODES:
        raise ValueError("不支援的 bbox 標籤內容")
    return BboxStyle(value.color.upper(), line_width, font_size, value.label_mode)


def bbox_style_dict(style: BboxStyle | Mapping[str, object]) -> dict[str, object]:
    value = validate_bbox_style(style)
    return {"color": value.color, "line_width": value.line_width,
            "font_size": value.font_size, "label_mode": value.label_mode}


def load_bbox_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load the same stable UI font on Windows and common Linux installs."""
    candidates = (
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeui.ttf"),
        Path("/usr/share/fonts/truetype/msttcorefonts/Arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    )
    for candidate in candidates:
        try:
            if candidate.is_file():
                return ImageFont.truetype(str(candidate), size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10 fallback, kept for portable test runners.
        return ImageFont.load_default()


def _label_text(index: int, box: tuple[int, int, int, int, int], mode: str) -> str:
    x, y, width, height, _area = box
    if mode == "size":
        return f"{width} x {height} px"
    return f"#{index + 1} ({x}, {y}) {width} x {height} px"


def _draw_bboxes_pillow(
    image: np.ndarray,
    boxes: Iterable[tuple[int, int, int, int, int]],
    style: BboxStyle,
    selected_indices: Iterable[int] | None,
    only_selected: bool,
) -> np.ndarray:
    """Draw the configurable annotation in original image pixels."""
    value = validate_bbox_style(style)
    if value.label_mode == "legacy":
        raise ValueError("legacy bbox style must use the legacy renderer")
    height, width = image.shape[:2]
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(rgb, mode="RGB")
    draw = ImageDraw.Draw(pil_image)
    color = ImageColor.getrgb(value.color)
    font = load_bbox_font(value.font_size)
    selected = None if selected_indices is None else {int(i) for i in selected_indices}
    padding, gap = 6, 8
    for index, box in enumerate(boxes):
        if only_selected and (selected is None or index not in selected):
            continue
        x, y, box_width, box_height, _area = map(int, box)
        draw.rectangle((x, y, x + box_width - 1, y + box_height - 1),
                       outline=color, width=value.line_width)
        text = _label_text(index, (x, y, box_width, box_height, int(box[4])), value.label_mode)
        text_bbox = draw.textbbox((0, 0), text, font=font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        label_width = text_width + padding * 2
        label_height = text_height + padding * 2
        if label_width > width or label_height > height:
            raise ValueError(
                f"bbox 標籤大於影像（需要 {label_width}×{label_height} px，"
                f"影像為 {width}×{height} px），請調小字體。"
            )
        label_x = max(0, min(width - label_width,
                             round(x + box_width / 2 - label_width / 2)))
        below_y = y + box_height + gap
        if below_y + label_height <= height:
            label_y = below_y
        else:
            above_y = y - gap - label_height
            if above_y >= 0:
                label_y = above_y
            else:
                # Both preferred placements are unavailable; keep the whole
                # label inside the frame, even if it overlaps the bbox.
                label_y = max(0, min(height - label_height, below_y))
        draw.rectangle((label_x, label_y, label_x + label_width - 1,
                        label_y + label_height - 1), fill=(16, 16, 16))
        draw.text((label_x + padding - text_bbox[0],
                   label_y + padding - text_bbox[1]), text, font=font, fill=color)
    image[:, :, :] = cv2.cvtColor(np.asarray(pil_image), cv2.COLOR_RGB2BGR)
    return image


def _draw_bboxes_legacy(
    image: np.ndarray,
    boxes: Iterable[tuple[int, int, int, int, int]],
    *,
    selected_indices: Iterable[int] | None = None,
    only_selected: bool = False,
    detailed: bool = True,
) -> np.ndarray:
    selected = None if selected_indices is None else {int(i) for i in selected_indices}
    for index, (x, y, box_width, box_height, _area) in enumerate(boxes):
        if only_selected and (selected is None or index not in selected):
            continue
        is_selected = selected is not None and index in selected
        color = (0, 0, 255) if is_selected else (0, 255, 0)
        thickness = 3 if is_selected else 2
        cv2.rectangle(image, (x, y), (x + box_width - 1, y + box_height - 1), color, thickness)
        if detailed:
            label = f"#{index + 1} x={x} y={y} w={box_width} h={box_height}"
        else:
            label = f"{index + 1}: {box_width}x{box_height}"
        cv2.putText(image, label, (x, max(16, y - 5)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, color, 1, cv2.LINE_AA)
    return image


def segment_frame(frame: np.ndarray, settings: dict[str, object]):
    """Return ``mask, boxes, selected_pixel_count`` for an original frame."""
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = map(int, settings.get("roi") or (0, 0, width, height))
    x1, x2, _ = slice(x1, x2).indices(width)
    y1, y2, _ = slice(y1, y2).indices(height)
    mask = np.zeros((height, width), dtype=np.uint8)
    if x2 <= x1 or y2 <= y1:
        return mask, [], 0
    selected = cv2.inRange(
        cv2.cvtColor(frame[y1:y2, x1:x2], cv2.COLOR_BGR2HSV),
        np.array(settings["lower"], dtype=np.uint8),
        np.array(settings["upper"], dtype=np.uint8),
    )
    mask[y1:y2, x1:x2] = selected
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(selected, connectivity=8)
    stats[:, 0] += x1
    stats[:, 1] += y1
    boxes = [tuple(map(int, stats[i])) for i in range(1, count)
             if int(stats[i, 4]) >= int(settings["min_area"])]
    # Backend component labels can change after cropping. Preserve the original
    # tie ordering (including the chosen largest bbox) when equal areas occur.
    if len({box[4] for box in boxes}) != len(boxes):
        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        boxes = [tuple(map(int, stats[i])) for i in range(1, count)
                 if int(stats[i, 4]) >= int(settings["min_area"])]
    boxes.sort(key=lambda box: box[4], reverse=True)
    return mask, boxes, int(cv2.countNonZero(selected))


def draw_bboxes(
    image: np.ndarray,
    boxes: Iterable[tuple[int, int, int, int, int]],
    *,
    selected_indices: Iterable[int] | None = None,
    only_selected: bool = False,
    detailed: bool = True,
    style: BboxStyle | Mapping[str, object] = DEFAULT_BBOX_STYLE,
) -> np.ndarray:
    """Draw configurable boxes in-place and return the image.

    ``style`` defaults to the configurable single-frame appearance.  The
    live batch preview and batch event exporter pass ``LEGACY_BBOX_STYLE``
    explicitly so their established appearance is not changed.
    """
    if isinstance(style, BboxStyle) and style.label_mode == "legacy":
        return _draw_bboxes_legacy(image, boxes, selected_indices=selected_indices,
                                   only_selected=only_selected, detailed=detailed)
    value = validate_bbox_style(style)
    return _draw_bboxes_pillow(image, boxes, value, selected_indices, only_selected)


def compose_frame(
    frame: np.ndarray,
    settings: dict[str, object],
    mode: str = "Original",
    *,
    boxes: Iterable[tuple[int, int, int, int, int]] | None = None,
    selected_indices: Iterable[int] | None = None,
    highlight_indices: Iterable[int] | None = None,
    include_bbox: bool = False,
    include_roi: bool = False,
    bbox_style: BboxStyle | Mapping[str, object] = DEFAULT_BBOX_STYLE,
    analysis=None,
) -> np.ndarray:
    """Compose one original-resolution BGR image for preview or export.

    ``Original`` without annotations returns a byte-for-byte copy of the
    decoded frame.  ``selected_indices`` controls which boxes are exported;
    ``highlight_indices`` is for the live preview, where all boxes remain
    visible but selected boxes are emphasized.
    """
    if mode not in IMAGE_MODES:
        raise ValueError(f"不支援的圖片模式：{mode}")
    selected = None if selected_indices is None else tuple(int(i) for i in selected_indices)
    needs_analysis = mode != "Original" or include_bbox or highlight_indices is not None
    if not needs_analysis and not include_roi:
        return frame.copy()

    if mode == "Mask":
        mask, detected_boxes, _ = analysis if analysis is not None else segment_frame(frame, settings)
        output = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    elif mode == "Overlay":
        mask, detected_boxes, _ = analysis if analysis is not None else segment_frame(frame, settings)
        output = frame.copy()
        selected_pixels = mask > 0
        tint = np.zeros_like(output)
        tint[:, :, 2] = 255
        output[selected_pixels] = cv2.addWeighted(output, 0.35, tint, 0.65, 0)[selected_pixels]
    else:
        output = frame.copy()
        detected_boxes = []

    actual_boxes = list(boxes) if boxes is not None else detected_boxes
    if include_bbox:
        draw_bboxes(output, actual_boxes, selected_indices=selected,
                    only_selected=selected is not None, detailed=True,
                    style=bbox_style)
    elif highlight_indices is not None:
        draw_bboxes(output, actual_boxes, selected_indices=highlight_indices,
                    only_selected=False, detailed=False, style=LEGACY_BBOX_STYLE)

    if include_roi and settings.get("roi"):
        x1, y1, x2, y2 = map(int, settings["roi"])
        cv2.rectangle(output, (x1, y1), (x2 - 1, y2 - 1), (0, 255, 255), 2)
    return output


def frame_filename(video_name: str, frame_number: int, mode: str,
                   include_bbox: bool) -> str:
    """Return the stable user-facing PNG name required by the export spec."""
    stem = Path(video_name).stem
    mode_name = mode.casefold()
    annotation = "annotated" if include_bbox else "plain"
    return f"{stem}_frame_{int(frame_number):06d}_{mode_name}_{annotation}.png"


def write_png(path: Path, image: np.ndarray) -> None:
    """Write an image with Unicode-safe Windows paths."""
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise ValueError("PNG 編碼失敗")
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        buffer.tofile(str(path))
    except OSError as exc:
        raise OSError(f"PNG 寫入失敗：{path}") from exc
