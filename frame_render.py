"""Pure OpenCV helpers shared by the preview and frame PNG exporters.

The GUI deliberately keeps the preview image resized, but every function in
this module works on the decoded, original-resolution frame.  Keeping the
composition here avoids small differences between a screenshot-like preview
and an exported PNG.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

import cv2
import numpy as np

IMAGE_MODES = ("Original", "Mask", "Overlay")


def segment_frame(frame: np.ndarray, settings: dict[str, object]):
    """Return ``mask, boxes, selected_pixel_count`` for an original frame."""
    mask = cv2.inRange(
        cv2.cvtColor(frame, cv2.COLOR_BGR2HSV),
        np.array(settings["lower"], dtype=np.uint8),
        np.array(settings["upper"], dtype=np.uint8),
    )
    roi = settings.get("roi")
    if roi is not None:
        x1, y1, x2, y2 = map(int, roi)
        roi_mask = np.zeros_like(mask)
        roi_mask[y1:y2, x1:x2] = mask[y1:y2, x1:x2]
        mask = roi_mask
    count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    boxes = [tuple(map(int, stats[i])) for i in range(1, count)
             if int(stats[i, 4]) >= int(settings["min_area"])]
    boxes.sort(key=lambda box: box[4], reverse=True)
    return mask, boxes, int(cv2.countNonZero(mask))


def draw_bboxes(
    image: np.ndarray,
    boxes: Iterable[tuple[int, int, int, int, int]],
    *,
    selected_indices: Iterable[int] | None = None,
    only_selected: bool = False,
    detailed: bool = True,
) -> np.ndarray:
    """Draw boxes in-place and return the image.

    Box indexes are zero-based internally and labels are one-based for users.
    ``only_selected`` is used by PNG export; preview rendering can draw all
    boxes while making the selected subset visually stronger.
    """
    selected = None if selected_indices is None else {int(i) for i in selected_indices}
    for index, (x, y, width, height, _area) in enumerate(boxes):
        if only_selected and (selected is None or index not in selected):
            continue
        is_selected = selected is not None and index in selected
        color = (0, 0, 255) if is_selected else (0, 255, 0)
        thickness = 3 if is_selected else 2
        cv2.rectangle(image, (x, y), (x + width - 1, y + height - 1), color, thickness)
        if detailed:
            label = f"#{index + 1} x={x} y={y} w={width} h={height}"
        else:
            label = f"{index + 1}: {width}x{height}"
        cv2.putText(image, label, (x, max(16, y - 5)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, color, 1, cv2.LINE_AA)
    return image


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
        mask, detected_boxes, _ = segment_frame(frame, settings)
        output = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    elif mode == "Overlay":
        mask, detected_boxes, _ = segment_frame(frame, settings)
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
                    only_selected=selected is not None, detailed=True)
    elif highlight_indices is not None:
        draw_bboxes(output, actual_boxes, selected_indices=highlight_indices,
                    only_selected=False, detailed=False)

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

