"""Run real-video regression checks: python verify_debug_video.py DEBUG_DIR OUTPUT_DIR."""
import copy
import csv
import hashlib
import json
from pathlib import Path
import sys

import cv2
import numpy as np

import batch_core as core
from frame_render import segment_frame
from video_reader import ExactVideoCapture


def digest(frame):
    return hashlib.sha256(frame.tobytes()).hexdigest()


def main():
    source, output = map(Path, sys.argv[1:3])
    output.mkdir(parents=True, exist_ok=True)
    video = next(source.glob("*.mp4"))
    saved = next(source.rglob("settings.json"))
    settings = core.read_json(saved)["settings"]
    baseline = []
    cap = cv2.VideoCapture(str(video))
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            _, boxes, pixels = segment_frame(frame, settings)
            baseline.append(dict(hash=digest(frame), boxes=boxes, pixels=pixels))
    finally:
        cap.release()
    print(f"Independent sequential baseline: {len(baseline)} frames", flush=True)

    def check_image(frame, number):
        assert digest(frame) == baseline[number - 1]["hash"], number

    def check_row(row):
        expected = baseline[row["frame"] - 1]
        boxes = expected["boxes"]
        assert row["selected_pixels"] == expected["pixels"], row
        assert row["bbox_valid"] == row["raw_detected"] == int(bool(boxes)), row
        actual = tuple(row[k] for k in ("bbox_x", "bbox_y", "bbox_width", "bbox_height", "largest_blob_area"))
        assert actual == (boxes[0] if boxes else (None, None, 0, 0, 0)), row

    with saved.with_name("frame_analysis.csv").open(encoding="utf-8-sig") as stream:
        old_rows = list(csv.DictReader(stream))
    for row in old_rows:
        expected = baseline[int(row["frame"]) - 1]
        assert int(row["selected_pixels"]) == expected["pixels"]
        assert int(row["bbox_valid"]) == int(bool(expected["boxes"]))
    print(f"Original CSV: {len(old_rows)} rows agree with baseline", flush=True)

    item = core.new_item(video)
    assert item["metadata"]["frame_count"] == len(baseline)
    item["settings"] = dict(settings, end_percent=100.0)
    run = core.analyze_item(item, output / "runs")
    item.update(latest_run=run, status="completed")
    rows = core.load_rows(run)
    assert len(rows) == len(baseline)
    for row in rows:
        check_row(row)
    print("Full-range analysis matches every baseline frame", flush=True)

    reader = ExactVideoCapture(video)
    probes = [1, 1556, 1560, 1559, 1561, 1555, 2040, 2189, 3332, 1560, 1]
    try:
        for number in probes:
            check_image(core.read_exact_frame(video, number, reader), number)
    finally:
        reader.release()
    print("Forward/backward navigation is pixel-exact", flush=True)

    partial = copy.deepcopy(item)
    partial["settings"].update(start_percent=46.5, end_percent=47.0)
    partial_run = core.analyze_item(partial, output / "runs")
    for row in core.load_rows(partial_run):
        check_row(row)
    assert "recomputed_from" not in partial_run
    core.set_manual_event(item, "manual_first_detection", 1560, "Regression verification only")
    export, summaries = core.export_batch([item], output)
    assert summaries[0]["export_status"] == "completed", summaries
    exported = []
    for path in export.rglob("*_raw.png"):
        number = int(path.stem.split("_frame_")[1].split("_")[0])
        image = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
        check_image(image, number)
        exported.append(str(path))
    assert len(exported) == 4, exported
    print("Partial analysis and all four exported event images match", flush=True)

    # Exercise actual review and independent viewer paths, including their labels.
    import tkinter as tk
    from batch_ui import BatchApp
    root = tk.Tk()
    root.withdraw()
    errors = []
    root.report_callback_exception = lambda *args: errors.append(str(args))
    app = BatchApp(root)
    app.project["items"] = [item]
    try:
        app.open_editor(item, review=True, number=1560)
        root.update()
        review = app.editor
        check_image(review.frame, 1560)
        assert "BBox 無" in review.detail_text.get(), review.detail_text.get()
        assert review.analyze_frame(review.frame)[1] == []
        review.read_frame(1555)
        root.update()
        check_image(review.frame, 1556)
        assert "BBox 12 × 4" in review.detail_text.get(), review.detail_text.get()
        review.open_frame_viewer()
        root.update()
        viewer = review.frame_viewers[-1]
        viewer.read_frame(1559)
        check_image(viewer.frame, 1560)
        assert viewer.boxes == []
        assert not errors, errors
    finally:
        if app.editor:
            app.editor.close()
        root.destroy()
    report = dict(status="passed", decoded_frames=len(baseline), baseline_csv_rows=len(old_rows),
                  full_analysis_rows=len(rows), navigation_probes=probes,
                  partial_range=[partial_run["analysis_start_frame"], partial_run["analysis_end_frame"]],
                  review_and_independent_viewer="passed", exported_images=exported,
                  frame_1556=rows[1555], frame_1560=rows[1559])
    core.atomic_json(output / "verification.json", report)
    print(json.dumps(report, ensure_ascii=True, indent=2), flush=True)


if __name__ == "__main__":
    main()
