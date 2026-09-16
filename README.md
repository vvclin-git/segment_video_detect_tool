# ECU Segmentation Video Validator

A small desktop app for recovering a segmentation mask from a fixed overlay color in ECU-recorded video.

## Features

- Open MP4, AVI, MOV, MKV, or M4V video
- Play/pause, previous/next frame, and seek timeline
- Left/right arrow keyboard frame navigation
- Direct frame-number jump with Enter or the Go button
- User-defined batch-analysis start frame
- Interactive OpenCV HSV thresholds and an ROI-based Hue histogram
- Editable numeric fields beside every HSV and blob-area slider
- Original, mask, and overlay preview modes
- Drag directly on the image to define an ROI
- Connected-component blob detection inside the ROI
- Per-blob `x`, `y`, `width`, `height`, area, and center output
- Bounding boxes and `width x height` labels in the preview
- Full-video raw detection analysis
- Parameterized M-of-N stable detection with consecutive confirmation and ON/OFF hysteresis
- First detection and first stable confirmation frame
- Detection rate, stable coverage, stabilization latency, and longest miss
- Per-frame analysis CSV and separate run-summary CSV
- First-detection and first-stable raw-frame/mask PNG export

All reported coordinates and sizes use the original video resolution, not the resized preview.

## Run with uv

```powershell
uv sync
uv run python app.py
```

Start with these thresholds for the magenta segmentation color:

```text
H: 160-179
S: 140-255
V: 80-255
```

Use **Min blob area** to suppress compression noise and tiny false positives.

## Stable detection and export

The defaults use a 5-frame window, Stable ON at 4/5 detections, Stable OFF at 1/5 or fewer detections, and 3 confirmation frames. Stable detection is only confirmed when the full-window ON condition remains satisfied for 3 consecutive frames. The reported first stable frame is the real-time confirmation frame and is not backdated.

For example, with `Window=10`, `Stable ON=9`, and `Confirm frames=3`, rolling rates of `0.9, 1.0, 1.0` confirm stable on the third frame. A single `0.9` peak followed by `0.8` is rejected as a transient candidate.

After selecting the ROI and thresholds, click **Analyze Full Video**. When the scan finishes, click **Export CSV**. The app saves one per-frame CSV and one run-summary CSV.

Set **Analysis start frame** before analysis when the beginning of the video is not part of the valid test interval. Frame CSV output and the M-of-N rolling window both start from this frame. Use the left/right arrow keys to step through frames, or enter a 1-based frame number in **Go to frame** and press Enter.

HSV and minimum-area values can be changed either with the sliders or by typing in the numeric fields. Press Enter or move focus away from a field to apply the value.

Click **Export Event Images** to select a parent folder. The app creates a `<video-name>_event_frames` folder containing:

- `first_detection_frame_XXXXXX_raw.png`
- `first_detection_frame_XXXXXX_mask.png`
- `first_stable_frame_XXXXXX_raw.png`
- `first_stable_frame_XXXXXX_mask.png`

The raw PNG is the untouched source frame. The mask PNG uses the HSV thresholds and ROI captured when **Analyze Full Video** was run; pixels outside the ROI are black.

## ROI operation

1. Pause at a useful frame.
2. Drag on the preview to define the ROI.
3. Drag again to replace it, or click **Clear ROI** to analyze the full frame.

The yellow rectangle is the ROI. Green rectangles are accepted blobs.
