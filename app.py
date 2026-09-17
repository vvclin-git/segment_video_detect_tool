from __future__ import annotations

import csv
import tkinter as tk
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2
import numpy as np
from PIL import Image, ImageTk


@dataclass
class FrameResult:
    frame: int
    timestamp: float
    selected_pixels: int
    largest_blob_area: int
    bbox_x: int | None
    bbox_y: int | None
    bbox_width: int
    bbox_height: int
    bbox_center_x: float | None
    bbox_center_y: float | None
    raw_detected: int
    rolling_rate: float = 0.0
    stable_detected: int = 0
    consecutive_miss: int = 0


def calculate_stable_states(detections: list[int], window: int, on_count: int,
                            off_count: int, confirm_frames: int) -> tuple[list[float], list[int], list[int]]:
    history: deque[int] = deque(maxlen=window)
    rates, states, misses = [], [], []
    stable = False
    miss = 0
    qualifying_streak = 0
    for detected in detections:
        history.append(detected)
        miss = 0 if detected else miss + 1
        rate = sum(history) / len(history)
        if len(history) == window:
            hit_count = sum(history)
            if stable:
                if hit_count <= off_count:
                    stable = False
                    qualifying_streak = 0
            else:
                if hit_count >= on_count:
                    qualifying_streak += 1
                    if qualifying_streak >= confirm_frames:
                        stable = True
                else:
                    qualifying_streak = 0
        rates.append(rate)
        states.append(int(stable))
        misses.append(miss)
    return rates, states, misses


def find_first_sustained_stable_start(detections: list[int], window: int,
                                      on_count: int, off_count: int,
                                      confirm_frames: int) -> int | None:
    """Return the first frame index of a stable candidate that gets confirmed.

    The returned index is the first frame whose complete rolling window meets
    the ON threshold for the confirmed run.  It intentionally differs from
    the first ``stable_detected`` frame, which is delayed by ``confirm_frames``.
    """
    history: deque[int] = deque(maxlen=window)
    stable = False
    qualifying_streak = 0
    candidate_start: int | None = None
    for index, detected in enumerate(detections):
        history.append(detected)
        if len(history) < window:
            continue
        hit_count = sum(history)
        if stable:
            if hit_count <= off_count:
                stable = False
                qualifying_streak = 0
                candidate_start = None
            continue
        if hit_count >= on_count:
            if qualifying_streak == 0:
                candidate_start = index
            qualifying_streak += 1
            if qualifying_streak >= confirm_frames:
                return candidate_start
        else:
            qualifying_streak = 0
            candidate_start = None
    return None


class HSVVideoTester:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("ECU Segmentation Video Validator")
        root.geometry("1450x900")
        self.capture: cv2.VideoCapture | None = None
        self.video_path: Path | None = None
        self.frame: np.ndarray | None = None
        self.frame_index = self.frame_count = 0
        self.fps = 30.0
        self.playing = False
        self.after_id: str | None = None
        self.roi: tuple[int, int, int, int] | None = None
        self.drag_start: tuple[int, int] | None = None
        self.display_scale = 1.0
        self.display_offset = (0, 0)
        self.photo: ImageTk.PhotoImage | None = None
        self.last_results: list[FrameResult] = []
        self.analysis_config: dict[str, object] | None = None
        self.h_min, self.h_max = tk.IntVar(value=160), tk.IntVar(value=179)
        self.s_min, self.s_max = tk.IntVar(value=140), tk.IntVar(value=255)
        self.v_min, self.v_max = tk.IntVar(value=80), tk.IntVar(value=255)
        self.min_area = tk.IntVar(value=20)
        self.analysis_start_frame = tk.IntVar(value=1)
        self.target_frame = tk.IntVar(value=1)
        self.window_size, self.stable_on, self.stable_off = (
            tk.IntVar(value=5), tk.IntVar(value=4), tk.IntVar(value=1))
        self.stable_confirm_frames = tk.IntVar(value=3)
        self.view_mode = tk.StringVar(value="Overlay")
        self.status = tk.StringVar(value="Open a video to begin")
        self.roi_text = tk.StringVar(value="ROI: full frame")
        self.stats_text = tk.StringVar(value="Pixels: 0 | Blobs: 0 | Largest: 0 px")
        self.analysis_text = tk.StringVar(value="No batch analysis yet")
        self.progress = tk.DoubleVar(value=0)
        self._build_ui()
        self.root.bind("<Left>", lambda event: self.keyboard_step(event, -1))
        self.root.bind("<Right>", lambda event: self.keyboard_step(event, 1))

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self.root, padding=6)
        toolbar.pack(fill=tk.X)
        buttons = (("Open Video", self.open_video), ("Play / Pause", self.toggle_play),
                   ("Previous Frame", lambda: self.step(-1)), ("Next Frame", lambda: self.step(1)),
                   ("Clear ROI", self.clear_roi), ("Analyze Full Video", self.analyze_video),
                   ("Export CSV", self.export_csv),
                   ("Export Event Images", self.export_event_images))
        for text, command in buttons:
            ttk.Button(toolbar, text=text, command=command).pack(side=tk.LEFT, padx=2)
        ttk.Label(toolbar, text="View:").pack(side=tk.LEFT, padx=(12, 2))
        view = ttk.Combobox(toolbar, textvariable=self.view_mode,
                            values=("Original", "Mask", "Overlay"), state="readonly", width=10)
        view.pack(side=tk.LEFT)
        view.bind("<<ComboboxSelected>>", lambda _event: self.render())
        body = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=6)
        left, right = ttk.Frame(body), ttk.Frame(body, width=370)
        body.add(left, weight=4)
        body.add(right, weight=1)
        self.canvas = tk.Canvas(left, bg="#202020", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda _event: self.render())
        self.canvas.bind("<ButtonPress-1>", self.roi_press)
        self.canvas.bind("<B1-Motion>", self.roi_drag)
        self.canvas.bind("<ButtonRelease-1>", self.roi_release)
        navigation = ttk.Frame(left)
        navigation.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(navigation, text="Go to frame:").pack(side=tk.LEFT)
        self.target_entry = ttk.Entry(navigation, textvariable=self.target_frame, width=9)
        self.target_entry.pack(side=tk.LEFT, padx=(4, 3))
        self.target_entry.bind("<Return>", lambda _event: self.go_to_frame())
        ttk.Button(navigation, text="Go", command=self.go_to_frame).pack(side=tk.LEFT)
        ttk.Label(navigation, text="Analysis start frame:").pack(side=tk.LEFT, padx=(18, 4))
        self.start_spinbox = ttk.Spinbox(navigation, from_=1, to=1,
                                         textvariable=self.analysis_start_frame, width=9)
        self.start_spinbox.pack(side=tk.LEFT)
        ttk.Label(navigation, text="Keyboard: ← previous / → next").pack(side=tk.RIGHT)
        self.timeline = ttk.Scale(left, from_=0, to=1, orient=tk.HORIZONTAL, command=self.seek)
        self.timeline.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(left, textvariable=self.status).pack(anchor=tk.W)
        ttk.Progressbar(left, variable=self.progress, maximum=100).pack(fill=tk.X, pady=(3, 0))
        controls = ttk.LabelFrame(right, text="HSV and raw detection", padding=8)
        controls.pack(fill=tk.X, padx=(8, 0))
        for args in (("H min", self.h_min, 0, 179), ("H max", self.h_max, 0, 179),
                     ("S min", self.s_min, 0, 255), ("S max", self.s_max, 0, 255),
                     ("V min", self.v_min, 0, 255), ("V max", self.v_max, 0, 255),
                     ("Min blob area", self.min_area, 1, 2000)):
            self._add_slider(controls, *args)
        stable = ttk.LabelFrame(right, text="Stable detection (M-of-N)", padding=8)
        stable.pack(fill=tk.X, padx=(8, 0), pady=(8, 0))
        for args in (("Window N", self.window_size, 1, 300),
                     ("Stable ON M", self.stable_on, 1, 300),
                     ("Stable OFF <=", self.stable_off, 0, 300),
                     ("Confirm frames", self.stable_confirm_frames, 1, 300)):
            self._add_spinbox(stable, *args)
        ttk.Label(stable, text="Stable requires a full N-frame window and K consecutive ON-qualified frames.",
                  wraplength=340).pack(anchor=tk.W, pady=(4, 0))
        hist_group = ttk.LabelFrame(right, text="Hue histogram (ROI, S/V filtered)", padding=6)
        hist_group.pack(fill=tk.X, padx=(8, 0), pady=(8, 0))
        self.hist_canvas = tk.Canvas(hist_group, width=340, height=130, bg="white", highlightthickness=1)
        self.hist_canvas.pack(fill=tk.X)
        self.hist_canvas.bind("<Configure>", lambda _event: self.draw_histogram())
        info = ttk.LabelFrame(right, text="Current frame", padding=8)
        info.pack(fill=tk.X, padx=(8, 0), pady=(8, 0))
        ttk.Label(info, textvariable=self.roi_text).pack(anchor=tk.W)
        ttk.Label(info, textvariable=self.stats_text, wraplength=340).pack(anchor=tk.W, pady=(4, 0))
        blobs = ttk.LabelFrame(right, text="Blob bounding boxes", padding=8)
        blobs.pack(fill=tk.BOTH, expand=True, padx=(8, 0), pady=(8, 0))
        columns = ("id", "x", "y", "w", "h", "area", "cx", "cy")
        self.tree = ttk.Treeview(blobs, columns=columns, show="headings", height=7)
        for name, width in zip(columns, (30, 42, 42, 38, 38, 52, 48, 48)):
            self.tree.heading(name, text=name.upper())
            self.tree.column(name, width=width, anchor=tk.CENTER)
        self.tree.pack(fill=tk.BOTH, expand=True)
        ttk.Label(right, textvariable=self.analysis_text, wraplength=350).pack(fill=tk.X, padx=(8, 0), pady=8)

    def _add_slider(self, parent: ttk.Widget, label: str, variable: tk.IntVar,
                    low: int, high: int) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=1)
        ttk.Label(row, text=label, width=14).pack(side=tk.LEFT)
        entry_value = tk.StringVar(value=str(variable.get()))

        def apply_slider(value: str) -> None:
            number = max(low, min(high, round(float(value))))
            variable.set(number)
            entry_value.set(str(number))
            self.last_results = []
            self.analysis_config = None
            self.render()

        scale = ttk.Scale(row, from_=low, to=high, value=variable.get(), command=apply_slider)
        scale.pack(side=tk.LEFT, fill=tk.X, expand=True)
        entry = ttk.Entry(row, textvariable=entry_value, width=6, justify=tk.RIGHT)
        entry.pack(side=tk.RIGHT)

        def apply_entry(_event: tk.Event | None = None) -> None:
            try:
                number = round(float(entry_value.get()))
            except ValueError:
                number = variable.get()
            number = max(low, min(high, number))
            variable.set(number)
            entry_value.set(str(number))
            scale.set(number)
            self.last_results = []
            self.analysis_config = None
            self.render()

        entry.bind("<Return>", apply_entry)
        entry.bind("<FocusOut>", apply_entry)

    def _add_spinbox(self, parent: ttk.Widget, label: str, variable: tk.IntVar,
                     low: int, high: int) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text=label, width=18).pack(side=tk.LEFT)
        ttk.Spinbox(row, from_=low, to=high, textvariable=variable, width=8).pack(side=tk.LEFT)

    def open_video(self) -> None:
        path = filedialog.askopenfilename(title="Open video",
            filetypes=[("Video files", "*.mp4 *.avi *.mov *.mkv *.m4v"), ("All files", "*.*")])
        if not path:
            return
        capture = cv2.VideoCapture(path)
        if not capture.isOpened():
            messagebox.showerror("Open failed", "OpenCV could not open this video.")
            return
        if self.capture is not None:
            self.capture.release()
        self.capture, self.video_path = capture, Path(path)
        self.frame_count = max(1, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
        self.fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        self.timeline.configure(to=max(0, self.frame_count - 1))
        self.start_spinbox.configure(to=self.frame_count)
        self.analysis_start_frame.set(1)
        self.target_frame.set(1)
        self.frame_index, self.roi, self.playing = 0, None, False
        self.last_results = []
        self.analysis_config = None
        self.analysis_text.set("No batch analysis yet")
        self.root.title(f"ECU Segmentation Video Validator - {self.video_path.name}")
        self.read_frame(0)

    def read_frame(self, index: int) -> None:
        if self.capture is None:
            return
        index = max(0, min(index, self.frame_count - 1))
        self.capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = self.capture.read()
        if ok:
            self.frame_index, self.frame = index, frame
            self.target_frame.set(index + 1)
            self.timeline.set(index)
            self.render()
        else:
            self.playing = False

    def toggle_play(self) -> None:
        if self.capture is not None:
            self.playing = not self.playing
            if self.playing:
                self._play_tick()

    def _play_tick(self) -> None:
        if not self.playing:
            return
        if self.frame_index + 1 >= self.frame_count:
            self.playing = False
            return
        self.read_frame(self.frame_index + 1)
        self.after_id = self.root.after(max(1, round(1000 / self.fps)), self._play_tick)

    def step(self, amount: int) -> None:
        self.playing = False
        self.read_frame(self.frame_index + amount)

    def keyboard_step(self, event: tk.Event, amount: int) -> str | None:
        if event.widget.winfo_class() in {"TEntry", "Entry", "TSpinbox", "Spinbox"}:
            return None
        if self.capture is not None:
            self.step(amount)
        return "break"

    def go_to_frame(self) -> None:
        if self.capture is None:
            return
        try:
            frame_number = self.target_frame.get()
        except tk.TclError:
            frame_number = self.frame_index + 1
        frame_number = max(1, min(frame_number, self.frame_count))
        self.target_frame.set(frame_number)
        self.read_frame(frame_number - 1)

    def seek(self, value: str) -> None:
        index = int(float(value))
        if self.capture is not None and index != self.frame_index:
            self.playing = False
            self.read_frame(index)

    def analyze_frame(self, frame: np.ndarray) -> tuple[np.ndarray, list[tuple[int, int, int, int, int]], int]:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        lower = np.array([self.h_min.get(), self.s_min.get(), self.v_min.get()], dtype=np.uint8)
        upper = np.array([self.h_max.get(), self.s_max.get(), self.v_max.get()], dtype=np.uint8)
        mask = cv2.inRange(hsv, lower, upper)
        height, width = mask.shape
        x1, y1, x2, y2 = self.roi or (0, 0, width, height)
        roi_mask = np.zeros_like(mask)
        roi_mask[y1:y2, x1:x2] = mask[y1:y2, x1:x2]
        count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(roi_mask, connectivity=8)
        boxes = []
        for index in range(1, count):
            x, y, box_w, box_h, area = map(int, stats[index])
            if area >= self.min_area.get():
                boxes.append((x, y, box_w, box_h, area))
        boxes.sort(key=lambda item: item[4], reverse=True)
        return roi_mask, boxes, cv2.countNonZero(roi_mask)

    @staticmethod
    def create_event_mask(frame: np.ndarray, config: dict[str, object]) -> np.ndarray:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        lower = np.array(config["lower"], dtype=np.uint8)
        upper = np.array(config["upper"], dtype=np.uint8)
        mask = cv2.inRange(hsv, lower, upper)
        roi = config["roi"]
        if roi is None:
            return mask
        x1, y1, x2, y2 = roi
        roi_mask = np.zeros_like(mask)
        roi_mask[y1:y2, x1:x2] = mask[y1:y2, x1:x2]
        return roi_mask

    @staticmethod
    def write_png(path: Path, image: np.ndarray) -> bool:
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            return False
        try:
            encoded.tofile(str(path))
        except OSError:
            return False
        return True

    def render(self) -> None:
        if self.frame is None or self.canvas.winfo_width() < 2:
            return
        mask, boxes, pixels = self.analyze_frame(self.frame)
        if self.view_mode.get() == "Mask":
            output = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        else:
            output = self.frame.copy()
            if self.view_mode.get() == "Overlay":
                selected, tint = mask > 0, np.zeros_like(output)
                tint[:, :, 2] = 255
                output[selected] = cv2.addWeighted(output, 0.35, tint, 0.65, 0)[selected]
        for index, (x, y, width, height, _area) in enumerate(boxes, start=1):
            cv2.rectangle(output, (x, y), (x + width, y + height), (0, 255, 0), 2)
            cv2.putText(output, f"{index}: {width}x{height}", (x, max(16, y - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
        if self.roi:
            x1, y1, x2, y2 = self.roi
            cv2.rectangle(output, (x1, y1), (x2, y2), (0, 255, 255), 2)
        self._show_on_canvas(output)
        self._update_stats(boxes, pixels)
        self.draw_histogram()

    def draw_histogram(self) -> None:
        if self.frame is None:
            return
        hsv = cv2.cvtColor(self.frame, cv2.COLOR_BGR2HSV)
        height, width = hsv.shape[:2]
        x1, y1, x2, y2 = self.roi or (0, 0, width, height)
        sample = hsv[y1:y2, x1:x2]
        valid = ((sample[:, :, 1] >= self.s_min.get()) & (sample[:, :, 1] <= self.s_max.get()) &
                 (sample[:, :, 2] >= self.v_min.get()) & (sample[:, :, 2] <= self.v_max.get()))
        hist = np.bincount(sample[:, :, 0][valid], minlength=180).astype(float)
        canvas = self.hist_canvas
        canvas.delete("all")
        cw, ch = max(2, canvas.winfo_width()), max(2, canvas.winfo_height())
        bottom, top = ch - 17, 6
        maximum = hist.max() if hist.max() > 0 else 1.0
        for hue, value in enumerate(hist):
            x0, x1p = hue * cw / 180, (hue + 1) * cw / 180
            bar_h = (value / maximum) * (bottom - top)
            rgb = cv2.cvtColor(np.uint8([[[hue, 255, 255]]]), cv2.COLOR_HSV2RGB)[0, 0]
            color = f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
            canvas.create_rectangle(x0, bottom - bar_h, x1p + 1, bottom, fill=color, outline="")
        for hue, label in ((self.h_min.get(), "min"), (self.h_max.get(), "max")):
            x = hue * cw / 180
            canvas.create_line(x, top, x, bottom, fill="black", width=2)
            canvas.create_text(x, ch - 7, text=f"{label}:{hue}", font=("TkDefaultFont", 7))

    def _show_on_canvas(self, frame: np.ndarray) -> None:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        height, width = rgb.shape[:2]
        cw, ch = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        self.display_scale = min(cw / width, ch / height)
        nw, nh = max(1, round(width * self.display_scale)), max(1, round(height * self.display_scale))
        ox, oy = (cw - nw) // 2, (ch - nh) // 2
        self.display_offset = (ox, oy)
        image = Image.fromarray(rgb).resize((nw, nh), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(image)
        self.canvas.delete("all")
        self.canvas.create_image(ox, oy, anchor=tk.NW, image=self.photo)

    def _update_stats(self, boxes: list[tuple[int, int, int, int, int]], pixels: int) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, (x, y, width, height, area) in enumerate(boxes, 1):
            self.tree.insert("", tk.END, values=(index, x, y, width, height, area,
                f"{x + width / 2:.1f}", f"{y + height / 2:.1f}"))
        largest = boxes[0][4] if boxes else 0
        self.stats_text.set(f"Pixels: {pixels} | Blobs: {len(boxes)} | Largest: {largest} px | Raw: {int(bool(boxes))}")
        self.status.set(f"Frame {self.frame_index + 1}/{self.frame_count} | "
                        f"{self.frame_index / self.fps:.3f} s | {self.fps:.2f} FPS")
        if self.roi is None:
            self.roi_text.set("ROI: full frame")
        else:
            x1, y1, x2, y2 = self.roi
            self.roi_text.set(f"ROI: x={x1}, y={y1}, width={x2-x1}, height={y2-y1}")

    def _stable_settings(self) -> tuple[int, int, int, int] | None:
        try:
            n, on, off = self.window_size.get(), self.stable_on.get(), self.stable_off.get()
            confirm = self.stable_confirm_frames.get()
        except tk.TclError:
            messagebox.showerror("Invalid settings", "Stable settings must be integers.")
            return None
        if n < 1 or not 1 <= on <= n or not 0 <= off < on or confirm < 1:
            messagebox.showerror(
                "Invalid settings",
                "Require 1 <= ON <= Window, 0 <= OFF < ON, and Confirm frames >= 1.",
            )
            return None
        return n, on, off, confirm

    def analyze_video(self) -> None:
        settings = self._stable_settings()
        if self.video_path is None or settings is None:
            return
        try:
            start_frame = self.analysis_start_frame.get()
        except tk.TclError:
            messagebox.showerror("Invalid start frame", "Analysis start frame must be an integer.")
            return
        start_frame = max(1, min(start_frame, self.frame_count))
        self.analysis_start_frame.set(start_frame)
        start_index = start_frame - 1
        self.playing = False
        scan = cv2.VideoCapture(str(self.video_path))
        if not scan.isOpened():
            messagebox.showerror("Analysis failed", "Could not reopen the video.")
            return
        scan.set(cv2.CAP_PROP_POS_FRAMES, start_index)
        results, detections = [], []
        index = start_index
        frames_to_analyze = max(1, self.frame_count - start_index)
        while True:
            ok, frame = scan.read()
            if not ok:
                break
            _mask, boxes, pixels = self.analyze_frame(frame)
            largest = boxes[0] if boxes else None
            detected = int(largest is not None)
            detections.append(detected)
            if largest:
                x, y, width, height, area = largest
                cx, cy = x + width / 2, y + height / 2
            else:
                x = y = cx = cy = None
                width = height = area = 0
            results.append(FrameResult(index + 1, index / self.fps, pixels, area,
                                       x, y, width, height, cx, cy, detected))
            index += 1
            processed = index - start_index
            if processed % 20 == 0:
                self.progress.set(100 * processed / frames_to_analyze)
                self.status.set(f"Analyzing frame {index}/{self.frame_count}")
                self.root.update_idletasks()
        scan.release()
        rates, states, misses = calculate_stable_states(detections, *settings)
        for result, rate, state, miss in zip(results, rates, states, misses):
            result.rolling_rate, result.stable_detected, result.consecutive_miss = rate, state, miss
        self.last_results = results
        self.analysis_config = {
            "lower": (self.h_min.get(), self.s_min.get(), self.v_min.get()),
            "upper": (self.h_max.get(), self.s_max.get(), self.v_max.get()),
            "roi": self.roi,
            "min_area": self.min_area.get(),
            "analysis_start_frame": start_frame,
            "window_n": settings[0],
            "stable_on_m": settings[1],
            "stable_off_count": settings[2],
            "stable_confirm_frames": settings[3],
        }
        self.progress.set(100)
        summary = self._summary()
        self.analysis_text.set(
            f"First: {summary['first_detection_frame']} | Stable start: "
            f"{summary['first_sustained_stable_start_frame']} | Stable confirm: "
            f"{summary['first_stable_confirmation_frame']} | "
            f"Latency: {summary['stabilization_latency_frames']} frames / {summary['stabilization_latency_s']} s | "
            f"Raw rate: {summary['raw_detection_rate']:.3f} | Stable coverage: "
            f"{summary['stable_detection_coverage']:.3f} | Longest miss: {summary['longest_consecutive_miss']}")
        self.status.set(f"Analysis complete: {len(results)} frames")

    def _event_results(self) -> dict[str, FrameResult | None]:
        """Return the three event frames used by summary and image export."""
        first_detection = next((r for r in self.last_results if r.raw_detected), None)
        first_confirmation = next((r for r in self.last_results if r.stable_detected), None)
        config = self.analysis_config or {}
        if config:
            start_index = find_first_sustained_stable_start(
                [r.raw_detected for r in self.last_results],
                int(config.get("window_n", self.window_size.get())),
                int(config.get("stable_on_m", self.stable_on.get())),
                int(config.get("stable_off_count", self.stable_off.get())),
                int(config.get("stable_confirm_frames", self.stable_confirm_frames.get())),
            )
        else:
            start_index = None
        first_sustained_start = (
            self.last_results[start_index]
            if start_index is not None and start_index < len(self.last_results)
            else None
        )
        return {
            "first_detection": first_detection,
            "first_sustained_stable_start": first_sustained_start,
            "first_stable_confirmation": first_confirmation,
        }

    @staticmethod
    def _event_summary_fields(prefix: str, result: FrameResult | None) -> dict[str, int | float | str]:
        if result is None:
            return {
                f"{prefix}_frame": "",
                f"{prefix}_timestamp": "",
                f"{prefix}_bbox_x": "",
                f"{prefix}_bbox_y": "",
                f"{prefix}_bbox_width": "",
                f"{prefix}_bbox_height": "",
                f"{prefix}_bbox_center_x": "",
                f"{prefix}_bbox_center_y": "",
                f"{prefix}_largest_blob_area": "",
            }
        return {
            f"{prefix}_frame": result.frame,
            f"{prefix}_timestamp": result.timestamp,
            f"{prefix}_bbox_x": result.bbox_x if result.bbox_x is not None else "",
            f"{prefix}_bbox_y": result.bbox_y if result.bbox_y is not None else "",
            f"{prefix}_bbox_width": result.bbox_width,
            f"{prefix}_bbox_height": result.bbox_height,
            f"{prefix}_bbox_center_x": result.bbox_center_x if result.bbox_center_x is not None else "",
            f"{prefix}_bbox_center_y": result.bbox_center_y if result.bbox_center_y is not None else "",
            f"{prefix}_largest_blob_area": result.largest_blob_area,
        }

    def _summary(self) -> dict[str, int | float | str]:
        first = next((r.frame for r in self.last_results if r.raw_detected), "")
        first_stable = next((r.frame for r in self.last_results if r.stable_detected), "")
        if isinstance(first, int) and isinstance(first_stable, int):
            latency_frames: int | str = first_stable - first
            latency_s: float | str = round(latency_frames / self.fps, 6)
        else:
            latency_frames = latency_s = ""
        total = len(self.last_results)
        config = self.analysis_config or {}
        summary: dict[str, int | float | str] = {
            "video": self.video_path.name if self.video_path else "", "fps": self.fps,
            "valid_frames": total, "first_detection_frame": first,
            "first_stable_detection_frame": first_stable,
            "stabilization_latency_frames": latency_frames, "stabilization_latency_s": latency_s,
            "raw_detection_rate": sum(r.raw_detected for r in self.last_results) / total if total else 0.0,
            "stable_detection_coverage": sum(r.stable_detected for r in self.last_results) / total if total else 0.0,
            "longest_consecutive_miss": max((r.consecutive_miss for r in self.last_results), default=0),
            "window_n": int(config.get("window_n", self.window_size.get())),
            "stable_on_m": int(config.get("stable_on_m", self.stable_on.get())),
            "stable_off_count": int(config.get("stable_off_count", self.stable_off.get())),
            "stable_confirm_frames": int(config.get("stable_confirm_frames", self.stable_confirm_frames.get())),
            "min_blob_area": int(config.get("min_area", self.min_area.get())),
            "analysis_start_frame": config.get("analysis_start_frame", 1),
            "h_min": self.h_min.get(), "h_max": self.h_max.get(), "s_min": self.s_min.get(),
            "s_max": self.s_max.get(), "v_min": self.v_min.get(), "v_max": self.v_max.get(),
            "roi": self.roi_text.get().removeprefix("ROI: ")}
        for prefix, result in self._event_results().items():
            summary.update(self._event_summary_fields(prefix, result))
        # Keep the original name as an explicit compatibility alias.
        summary["first_stable_detection_frame"] = summary["first_stable_confirmation_frame"]
        return summary

    def export_csv(self) -> None:
        if not self.last_results:
            messagebox.showinfo("No results", "Run Analyze Full Video first.")
            return
        initial = f"{self.video_path.stem}_frame_analysis.csv" if self.video_path else "frame_analysis.csv"
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=initial,
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        fields = list(FrameResult.__dataclass_fields__)
        with open(path, "w", newline="", encoding="utf-8-sig") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(asdict(result) for result in self.last_results)
        summary_path = Path(path).with_name(Path(path).stem.replace("_frame_analysis", "") + "_run_summary.csv")
        with summary_path.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.writer(output)
            writer.writerow(("metric", "value"))
            writer.writerows(self._summary().items())
        messagebox.showinfo("Export complete", f"Saved:\n{path}\n{summary_path}")

    def export_event_images(self) -> None:
        if not self.last_results or self.video_path is None or self.analysis_config is None:
            messagebox.showinfo("No results", "Run Analyze Full Video first.")
            return
        events = tuple(self._event_results().items())
        available = [(name, result) for name, result in events if result is not None]
        if not available:
            messagebox.showinfo("No event frames", "No raw or stable detection event was found.")
            return
        parent = filedialog.askdirectory(title="Choose event image output folder",
                                         initialdir=str(self.video_path.parent))
        if not parent:
            return
        output_dir = Path(parent) / f"{self.video_path.stem}_event_frames"
        output_dir.mkdir(parents=True, exist_ok=True)
        capture = cv2.VideoCapture(str(self.video_path))
        if not capture.isOpened():
            messagebox.showerror("Export failed", "Could not reopen the video.")
            return
        saved: list[Path] = []
        manifest_rows: list[dict[str, object]] = []
        for event_name, result in available:
            capture.set(cv2.CAP_PROP_POS_FRAMES, result.frame - 1)
            ok, raw_frame = capture.read()
            if not ok:
                continue
            event_prefix = f"{event_name}_frame_{result.frame:06d}"
            raw_path = output_dir / f"{event_prefix}_raw.png"
            mask_path = output_dir / f"{event_prefix}_mask.png"
            mask = self.create_event_mask(raw_frame, self.analysis_config)
            raw_saved = self.write_png(raw_path, raw_frame)
            mask_saved = self.write_png(mask_path, mask)
            if raw_saved:
                saved.append(raw_path)
            if mask_saved:
                saved.append(mask_path)
            manifest_rows.append({
                "event": event_name,
                "frame": result.frame,
                "timestamp": result.timestamp,
                "raw_detected": result.raw_detected,
                "stable_detected": result.stable_detected,
                "rolling_rate": result.rolling_rate,
                "consecutive_miss": result.consecutive_miss,
                "bbox_x": result.bbox_x if result.bbox_x is not None else "",
                "bbox_y": result.bbox_y if result.bbox_y is not None else "",
                "bbox_width": result.bbox_width,
                "bbox_height": result.bbox_height,
                "bbox_center_x": result.bbox_center_x if result.bbox_center_x is not None else "",
                "bbox_center_y": result.bbox_center_y if result.bbox_center_y is not None else "",
                "largest_blob_area": result.largest_blob_area,
                "raw_filename": raw_path.name if raw_saved else "",
                "mask_filename": mask_path.name if mask_saved else "",
            })
        capture.release()
        if not saved:
            messagebox.showerror("Export failed", "No event images could be written.")
            return
        manifest_path = output_dir / "event_images_manifest.csv"
        manifest_fields = (
            "event", "frame", "timestamp", "raw_detected", "stable_detected",
            "rolling_rate", "consecutive_miss", "bbox_x", "bbox_y", "bbox_width",
            "bbox_height", "bbox_center_x", "bbox_center_y", "largest_blob_area",
            "raw_filename", "mask_filename",
        )
        with manifest_path.open("w", newline="", encoding="utf-8-sig") as output:
            writer = csv.DictWriter(output, fieldnames=manifest_fields)
            writer.writeheader()
            writer.writerows(manifest_rows)
        messagebox.showinfo("Event images exported",
                            f"Saved {len(saved)} PNG files to:\n{output_dir}\n{manifest_path}")

    def _canvas_to_frame(self, canvas_x: int, canvas_y: int) -> tuple[int, int]:
        assert self.frame is not None
        ox, oy = self.display_offset
        frame_h, frame_w = self.frame.shape[:2]
        x, y = round((canvas_x - ox) / self.display_scale), round((canvas_y - oy) / self.display_scale)
        return max(0, min(x, frame_w)), max(0, min(y, frame_h))

    def roi_press(self, event: tk.Event) -> None:
        if self.frame is not None:
            self.drag_start = self._canvas_to_frame(event.x, event.y)

    def roi_drag(self, event: tk.Event) -> None:
        if self.drag_start is None or self.frame is None:
            return
        current = self._canvas_to_frame(event.x, event.y)
        x1, x2 = sorted((self.drag_start[0], current[0]))
        y1, y2 = sorted((self.drag_start[1], current[1]))
        if x2 > x1 and y2 > y1:
            self.roi = (x1, y1, x2, y2)
            self.last_results = []
            self.analysis_config = None
            self.render()

    def roi_release(self, event: tk.Event) -> None:
        self.roi_drag(event)
        self.drag_start = None

    def clear_roi(self) -> None:
        self.roi, self.last_results, self.analysis_config = None, [], None
        self.render()

    def close(self) -> None:
        self.playing = False
        if self.after_id:
            self.root.after_cancel(self.after_id)
        if self.capture:
            self.capture.release()
        self.root.destroy()


def main() -> None:
    import sys
    if "--single" not in sys.argv:
        from batch_ui import main as batch_main
        batch_main()
        return
    root = tk.Tk()
    app = HSVVideoTester(root)
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()


if __name__ == "__main__":
    main()
