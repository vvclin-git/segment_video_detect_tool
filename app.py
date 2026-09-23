from __future__ import annotations

import csv
import copy
import tkinter as tk
from time import perf_counter
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2
from video_reader import ExactVideoCapture
from buffer_timeline import BufferTimeline
from video_progress import show_video_progress
import numpy as np
from PIL import Image, ImageTk

from frame_render import (DEFAULT_BBOX_STYLE, IMAGE_MODES, LABEL_MODE_NAMES,
                          BboxStyle, compose_frame,
                          frame_filename, segment_frame, validate_bbox_style,
                          write_png)


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


@dataclass
class FrameSnapshot:
    """State needed to recreate a single-frame export or independent view."""

    video_name: str
    frame_number: int
    frame: np.ndarray
    settings: dict[str, object]
    boxes: tuple[tuple[int, int, int, int, int], ...]
    selected_indices: tuple[int, ...]
    source_path: Path | None = None
    frame_count: int = 1
    fps: float = 30.0
    view_mode: str = "Original"
    include_bbox: bool = False
    bbox_style: BboxStyle = DEFAULT_BBOX_STYLE


class FrameViewer:
    """Independent, zoomable and navigable view of a video frame."""

    def __init__(self, owner: "HSVVideoTester", snapshot: FrameSnapshot):
        self.owner = owner
        self.source_path = Path(snapshot.source_path) if snapshot.source_path else None
        self.video_name = snapshot.video_name
        self.settings = copy.deepcopy(snapshot.settings)
        self.frame_count = max(1, int(snapshot.frame_count))
        self.fps = float(snapshot.fps or 30.0)
        self.capture: ExactVideoCapture | None = None
        self.frame_index = int(snapshot.frame_number) - 1
        self.frame = snapshot.frame.copy()
        self.boxes = list(snapshot.boxes)
        self.selected_bboxes = set(int(i) for i in snapshot.selected_indices)
        self.view_mode = tk.StringVar(value=snapshot.view_mode)
        self.include_bbox = tk.BooleanVar(value=bool(snapshot.include_bbox and self.selected_bboxes))
        self.style = validate_bbox_style(snapshot.bbox_style)
        self.zoom = 1.0
        self.fit_mode = True
        self.pan = [0.0, 0.0]
        self.drag_start = None
        self.photo = None
        self.style_error = tk.StringVar()
        self.frame_status = tk.StringVar()
        self._closed = False
        self.root = tk.Toplevel(owner.root)
        self.root.title(f"{self.video_name} · Frame {snapshot.frame_number}")
        self.root.geometry("1200x780")
        self.root.minsize(760, 520)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._build_ui()
        if self.source_path is not None:
            self.capture = ExactVideoCapture(str(self.source_path), progress=self._video_progress)
            if not self.capture.isOpened():
                self.capture.release()
                self.capture = None
                self.style_error.set("無法開啟獨立視窗的影片解碼器；保留開啟時的有效畫面。")
            else:
                # Metadata is captured at open time and never follows the
                # owner's decoder or its later source/settings changes.
                self.frame_count = max(1, int(self.capture.get(cv2.CAP_PROP_FRAME_COUNT)) or self.frame_count)
                self.fps = float(self.capture.get(cv2.CAP_PROP_FPS) or self.fps)
        owner.frame_viewers.append(self)
        self._update_frame_controls()
        self.root.after_idle(self.fit)

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self.root, padding=7)
        toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text="模式").pack(side=tk.LEFT)
        mode = ttk.Combobox(toolbar, textvariable=self.view_mode, values=IMAGE_MODES,
                             state="readonly", width=10)
        mode.pack(side=tk.LEFT, padx=(4, 10))
        self.view_mode.trace_add("write", lambda *_: self.render())
        self.bbox_check = ttk.Checkbutton(toolbar, text="包含選取 bbox",
                                          variable=self.include_bbox, command=self.render)
        self.bbox_check.pack(side=tk.LEFT)
        ttk.Button(toolbar, text="符合視窗", command=self.fit).pack(side=tk.LEFT, padx=(14, 3))
        ttk.Button(toolbar, text="100%", command=self.original_size).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="匯出 PNG…", command=self.export).pack(side=tk.RIGHT, padx=3)

        body = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True)
        left, right = ttk.Frame(body), ttk.Frame(body, width=330)
        body.add(left, weight=4)
        body.add(right, weight=1)
        self.canvas = tk.Canvas(left, bg="#142029", highlightthickness=0, cursor="fleur")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda _event: self.render())
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.drag)
        self.canvas.bind("<ButtonRelease-1>", lambda _event: setattr(self, "drag_start", None))
        self.canvas.bind("<MouseWheel>", self.wheel)
        self.canvas.bind("<Button-4>", lambda event: self.zoom_by(1.15, event.x, event.y))
        self.canvas.bind("<Button-5>", lambda event: self.zoom_by(1 / 1.15, event.x, event.y))
        # Navigation bindings belong to the image only.  Entries and the
        # read-only mode selector therefore retain their native arrow keys.
        self.canvas.bind("<KeyPress-Left>", lambda _event: self.step(-1))
        self.canvas.bind("<KeyPress-Right>", lambda _event: self.step(1))
        self.root.bind("<Escape>", lambda _event: self.close())
        self._build_style_controls(right)
        self._build_bbox_controls(right)
        self._update_bbox_tree()
        self.status = ttk.Label(right, textvariable=self.frame_status, wraplength=300)
        self.status.pack(fill=tk.X, padx=8, pady=(5, 8))
        navigation = ttk.Frame(left, padding=5)
        navigation.pack(fill=tk.X)
        ttk.Button(navigation, text="◀ 上一幀", command=lambda: self.step(-1)).pack(side=tk.LEFT)
        ttk.Button(navigation, text="下一幀 ▶", command=lambda: self.step(1)).pack(side=tk.LEFT, padx=4)
        ttk.Label(navigation, textvariable=self.frame_status).pack(side=tk.LEFT, padx=8)
        self.buffer_timeline = BufferTimeline(left)
        self.buffer_timeline.pack(fill=tk.X)

    def _build_style_controls(self, parent: ttk.Widget) -> None:
        group = ttk.LabelFrame(parent, text="bbox 出圖樣式", padding=7)
        group.pack(fill=tk.X, padx=8, pady=(8, 4))
        self.style_vars = {
            "color": tk.StringVar(value=self.style.color),
            "line_width": tk.StringVar(value=str(self.style.line_width)),
            "font_size": tk.StringVar(value=str(self.style.font_size)),
            "label_mode": tk.StringVar(value=LABEL_MODE_NAMES[self.style.label_mode]),
        }
        fields = (("框線／文字色", "color"), ("線寬 px", "line_width"), ("字體 px", "font_size"))
        for label, key in fields:
            row = ttk.Frame(group)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=label, width=11).pack(side=tk.LEFT)
            entry = ttk.Entry(row, textvariable=self.style_vars[key], width=14)
            entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
            entry.bind("<Return>", lambda _event: self.apply_style())
            entry.bind("<FocusOut>", lambda _event: self.apply_style())
        row = ttk.Frame(group)
        row.pack(fill=tk.X, pady=2)
        ttk.Label(row, text="標籤內容", width=11).pack(side=tk.LEFT)
        mode = ttk.Combobox(row, textvariable=self.style_vars["label_mode"],
                            values=tuple(LABEL_MODE_NAMES.values()), state="readonly", width=16)
        mode.pack(side=tk.LEFT, fill=tk.X, expand=True)
        mode.bind("<<ComboboxSelected>>", lambda _event: self.apply_style())
        ttk.Button(group, text="恢復預設", command=self.reset_style).pack(anchor=tk.W, pady=(5, 0))
        ttk.Label(group, textvariable=self.style_error, foreground="#b3261e",
                  wraplength=285).pack(fill=tk.X, pady=(4, 0))

    def _build_bbox_controls(self, parent: ttk.Widget) -> None:
        group = ttk.LabelFrame(parent, text="目前幀 bbox（可多選）", padding=7)
        group.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        self.bbox_tree = ttk.Treeview(group, columns=("id", "x", "y", "w", "h"),
                                      show="headings", selectmode="extended", height=7)
        for key, width in (("id", 35), ("x", 52), ("y", 52), ("w", 52), ("h", 52)):
            self.bbox_tree.heading(key, text=key.upper())
            self.bbox_tree.column(key, width=width, anchor=tk.CENTER)
        self.bbox_tree.pack(fill=tk.BOTH, expand=True)
        self.bbox_tree.bind("<<TreeviewSelect>>", self._bbox_tree_selection)
        tools = ttk.Frame(group)
        tools.pack(fill=tk.X, pady=(5, 0))
        ttk.Button(tools, text="全選", command=self.select_all_bboxes).pack(side=tk.LEFT)
        ttk.Button(tools, text="清除", command=self.clear_bbox_selection).pack(side=tk.LEFT, padx=4)

    def _style_from_controls(self) -> BboxStyle:
        label_name = self.style_vars["label_mode"].get()
        label_mode = next((key for key, name in LABEL_MODE_NAMES.items() if name == label_name), label_name)
        return validate_bbox_style({"color": self.style_vars["color"].get(),
                                    "line_width": self.style_vars["line_width"].get(),
                                    "font_size": self.style_vars["font_size"].get(),
                                    "label_mode": label_mode})

    def apply_style(self) -> bool:
        try:
            self.style = self._style_from_controls()
        except ValueError as exc:
            self.style_error.set(str(exc))
            return False
        self.style_error.set("")
        self.render()
        return True

    def reset_style(self) -> None:
        self.style = DEFAULT_BBOX_STYLE
        self.style_vars["color"].set(self.style.color)
        self.style_vars["line_width"].set(str(self.style.line_width))
        self.style_vars["font_size"].set(str(self.style.font_size))
        self.style_vars["label_mode"].set(LABEL_MODE_NAMES[self.style.label_mode])
        self.style_error.set("")
        self.render()

    def _update_frame_controls(self) -> None:
        self.frame_status.set(f"Frame {self.frame_index + 1} / {self.frame_count}")
        if not self.selected_bboxes:
            self.include_bbox.set(False)
            self.bbox_check.configure(state="disabled")
        else:
            self.bbox_check.configure(state="normal")
        self._sync_bbox_tree_selection()

    def _update_bbox_tree(self) -> None:
        for item in self.bbox_tree.get_children():
            self.bbox_tree.delete(item)
        for index, (x, y, width, height, _area) in enumerate(self.boxes):
            self.bbox_tree.insert("", tk.END, iid=f"bbox-{index}",
                                  values=(index + 1, x, y, width, height))
        self.selected_bboxes.intersection_update(range(len(self.boxes)))
        self._sync_bbox_tree_selection()

    def _sync_bbox_tree_selection(self) -> None:
        if not hasattr(self, "bbox_tree"):
            return
        ids = [f"bbox-{index}" for index in sorted(self.selected_bboxes)
               if self.bbox_tree.exists(f"bbox-{index}")]
        current = self.bbox_tree.selection()
        if set(current) == set(ids):
            return
        if current:
            self.bbox_tree.selection_remove(current)
        self.bbox_tree.selection_set(*ids)

    def _bbox_tree_selection(self, _event=None) -> None:
        self.selected_bboxes = {
            int(str(iid).split("-", 1)[1]) for iid in self.bbox_tree.selection()
            if str(iid).startswith("bbox-")
        }
        self.include_bbox.set(bool(self.selected_bboxes))
        self._update_frame_controls()
        self.render()

    def select_all_bboxes(self) -> None:
        self.selected_bboxes = set(range(len(self.boxes)))
        self._update_frame_controls()
        self.render()

    def clear_bbox_selection(self, render=True) -> None:
        self.selected_bboxes.clear()
        self.include_bbox.set(False)
        self._update_frame_controls()
        self.render()

    def current_snapshot(self) -> FrameSnapshot:
        return FrameSnapshot(
            video_name=self.video_name, frame_number=self.frame_index + 1,
            frame=self.frame.copy(), settings=copy.deepcopy(self.settings),
            boxes=tuple(tuple(box) for box in self.boxes),
            selected_indices=tuple(sorted(self.selected_bboxes)),
            source_path=self.source_path, frame_count=self.frame_count, fps=self.fps,
            view_mode=self.view_mode.get(), include_bbox=bool(self.include_bbox.get()),
            bbox_style=self.style,
        )

    def current_image(self) -> np.ndarray:
        return compose_frame(
            self.frame, self.settings, self.view_mode.get(), boxes=self.boxes,
            selected_indices=sorted(self.selected_bboxes) if self.include_bbox.get() else None,
            include_bbox=bool(self.include_bbox.get()), bbox_style=self.style,
        )

    def fit(self) -> None:
        self.fit_mode = True
        self.pan = [0.0, 0.0]
        self.render()

    def original_size(self) -> None:
        self.fit_mode = False
        self.zoom = 1.0
        self.pan = [0.0, 0.0]
        self.render()

    def zoom_by(self, factor: float, x: int | None = None, y: int | None = None) -> None:
        old = self.zoom if not self.fit_mode else self._fit_scale()
        new = max(0.05, min(20.0, old * factor))
        if x is not None and y is not None:
            # Keep the pixel under the pointer fixed while zooming.
            self.pan[0] = x - (x - self._offset()[0]) * new / old - (self.canvas.winfo_width() - self.frame.shape[1] * new) / 2
            self.pan[1] = y - (y - self._offset()[1]) * new / old - (self.canvas.winfo_height() - self.frame.shape[0] * new) / 2
        self.fit_mode = False
        self.zoom = new
        self.render()

    def wheel(self, event) -> None:
        self.zoom_by(1.15 if event.delta > 0 else 1 / 1.15, event.x, event.y)

    def _fit_scale(self) -> float:
        height, width = self.frame.shape[:2]
        return min(max(1, self.canvas.winfo_width()) / width,
                   max(1, self.canvas.winfo_height()) / height)

    def _offset(self) -> tuple[float, float]:
        height, width = self.frame.shape[:2]
        scale = self._fit_scale() if self.fit_mode else self.zoom
        return ((self.canvas.winfo_width() - width * scale) / 2 + self.pan[0],
                (self.canvas.winfo_height() - height * scale) / 2 + self.pan[1])

    def press(self, event) -> None:
        self.canvas.focus_set()
        self.drag_start = (event.x, event.y, self.pan[0], self.pan[1])

    def drag(self, event) -> None:
        if self.drag_start is None:
            return
        x, y, start_x, start_y = self.drag_start
        if self.fit_mode:
            self.zoom = self._fit_scale()
        self.fit_mode = False
        self.pan = [start_x + event.x - x, start_y + event.y - y]
        self.render()

    def render(self) -> None:
        if hasattr(self, "buffer_timeline"):
            self.buffer_timeline.set_state(self.frame_count, self.frame_index + 1,
                                           self.capture.cached_ranges() if self.capture else ())
        if self._closed or not self.root.winfo_exists():
            return
        try:
            image = self.current_image()
        except ValueError as exc:
            self.style_error.set(str(exc))
            self.canvas.delete("all")
            return
        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        height, width = rgb.shape[:2]
        scale = self._fit_scale() if self.fit_mode else self.zoom
        size = (max(1, round(width * scale)), max(1, round(height * scale)))
        display = Image.fromarray(rgb).resize(size, Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(display)
        self.canvas.delete("all")
        ox, oy = self._offset()
        self.canvas.create_image(round(ox), round(oy), anchor=tk.NW, image=self.photo)

    def export(self) -> None:
        self.owner.export_frame_snapshot(self.current_snapshot(), self.root,
                                         self.source_path.parent if self.source_path else None)

    def step(self, amount: int) -> str:
        self.read_frame(self.frame_index + amount)
        return "break"

    def _video_progress(self, phase, completed, target, elapsed):
        variable = self.frame_status if hasattr(self, "frame_status") else self.status
        show_video_progress(self.root, variable, phase, completed, target, elapsed)

    def read_frame(self, index: int) -> None:
        if self.capture is None or self._closed:
            return
        index = max(0, min(index, self.frame_count - 1))
        if index == self.frame_index:
            return
        try:
            self.capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = self.capture.read()
            position = self.capture.get(cv2.CAP_PROP_POS_FRAMES)
            if not ok or abs(position - (index + 1)) > 0.5:
                raise ValueError(f"無法精確讀取 frame {index + 1}")
            _mask, boxes, _pixels = self.owner.analyze_frame_with_settings(frame, self.settings)
        except (ValueError, cv2.error) as exc:
            self.frame_status.set(f"Frame {self.frame_index + 1} / {self.frame_count} · 讀取失敗：{exc}")
            return
        # State is committed only after decode and analysis both succeed.
        self.frame_index, self.frame = index, frame
        self.boxes = list(boxes)
        self.selected_bboxes.clear()
        self._update_bbox_tree()
        self._update_frame_controls()
        self.root.title(f"{self.video_name} · Frame {self.frame_index + 1}")
        self.render()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self in self.owner.frame_viewers:
            self.owner.frame_viewers.remove(self)
        if self.capture is not None:
            self.capture.release()
            self.capture = None
        if self.root.winfo_exists():
            self.root.destroy()


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
        self.capture: ExactVideoCapture | None = None
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
        self.batch_features = bool(getattr(self, "batch_features", False))
        self.selected_bboxes: set[int] = set()
        self._updating_tree_selection = False
        self._ignore_bbox_event = False
        self.frame_viewers: list[FrameViewer] = []
        self.last_bbox_style = DEFAULT_BBOX_STYLE
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
        self.buffer_timeline = BufferTimeline(left)
        self.buffer_timeline.pack(fill=tk.X)
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
        self.tree = ttk.Treeview(blobs, columns=columns, show="headings", height=7,
                                 selectmode="extended" if self.batch_features else "browse")
        for name, width in zip(columns, (30, 42, 42, 38, 38, 52, 48, 48)):
            self.tree.heading(name, text=name.upper())
            self.tree.column(name, width=width, anchor=tk.CENTER)
        self.tree.pack(fill=tk.BOTH, expand=True)
        if self.batch_features:
            bbox_tools = ttk.Frame(blobs)
            bbox_tools.pack(fill=tk.X, pady=(4, 0))
            ttk.Button(bbox_tools, text="全選 bbox", command=self.select_all_bboxes).pack(side=tk.LEFT)
            ttk.Button(bbox_tools, text="清除選取", command=self.clear_bbox_selection).pack(side=tk.LEFT, padx=4)
            self.tree.bind("<<TreeviewSelect>>", self._bbox_tree_selection)
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
            if self.batch_features and (label.startswith(("H ", "S ", "V ")) or label == "Min blob area"):
                self.clear_bbox_selection()
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
            if self.batch_features and (label.startswith(("H ", "S ", "V ")) or label == "Min blob area"):
                self.clear_bbox_selection()
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
        capture = ExactVideoCapture(path, progress=self._video_progress)
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

    def _video_progress(self, phase, completed, target, elapsed):
        variable = self.frame_status if hasattr(self, "frame_status") else self.status
        show_video_progress(self.root, variable, phase, completed, target, elapsed)

    def read_frame(self, index: int) -> None:
        if self.capture is None:
            return
        index = max(0, min(index, self.frame_count - 1))
        if self.batch_features and index != self.frame_index:
            self.clear_bbox_selection(render=False)
        self.capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = self.capture.read()
        if ok:
            self.frame_index, self.frame = index, frame
            self.target_frame.set(index + 1)
            self.timeline.set(index)
            self.render()
        else:
            self.playing = False
            self.status.set(f"讀取第 {index + 1} 幀失敗，已停止播放。")

    def toggle_play(self) -> None:
        if self.capture is not None:
            self.playing = not self.playing
            if self.playing:
                self._play_tick()
            else:
                self.render()

    def _play_tick(self) -> None:
        if not self.playing:
            return
        if self.frame_index + 1 >= self.frame_count:
            self.playing = False
            return
        started = perf_counter()
        self.read_frame(self.frame_index + 1)
        if self.playing:
            delay = max(1, round(1000 * (1 / self.fps - (perf_counter() - started))))
            self.after_id = self.root.after(delay, self._play_tick)

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

    def clear_bbox_selection(self, render=True) -> None:
        """Clear selected boxes without changing the current frame or view."""
        self.selected_bboxes.clear()
        tree = getattr(self, "tree", None)
        if tree is not None and tree.winfo_exists():
            tree.selection_remove(tree.selection())
        if render and getattr(self, "frame", None) is not None and getattr(self, "batch_features", False):
            self.render()

    def select_all_bboxes(self) -> None:
        if not self.batch_features:
            return
        boxes = self.analyze_frame(self.frame)[1] if self.frame is not None else []
        self.selected_bboxes = set(range(len(boxes)))
        self._sync_tree_selection()
        self.render()

    def _bbox_tree_selection(self, _event=None) -> None:
        if not self.batch_features or self._updating_tree_selection or self._ignore_bbox_event:
            return
        selected = set()
        for iid in self.tree.selection():
            if str(iid).startswith("bbox-"):
                try:
                    selected.add(int(str(iid).split("-", 1)[1]))
                except ValueError:
                    continue
        self.selected_bboxes = selected
        self._ignore_bbox_event = True
        try:
            self.render()
        finally:
            self._ignore_bbox_event = False

    def _sync_tree_selection(self) -> None:
        if not self.batch_features or not hasattr(self, "tree"):
            return
        available = [f"bbox-{index}" for index in sorted(self.selected_bboxes)
                     if self.tree.exists(f"bbox-{index}")]
        current = set(self.tree.selection())
        if current == set(available):
            return
        self._updating_tree_selection = True
        self.tree.selection_set(*available)
        # Tk delivers <<TreeviewSelect>> after the Tcl command returns.  Keep
        # the guard alive until idle so restoring selection during a redraw is
        # not mistaken for a user click (which otherwise redraws forever).
        self.root.after_idle(self._release_tree_selection_guard)

    def _release_tree_selection_guard(self) -> None:
        self._updating_tree_selection = False

    def _frame_settings(self) -> dict[str, object]:
        """Return only the saved detection inputs needed to recreate a frame."""
        return dict(lower=[self.h_min.get(), self.s_min.get(), self.v_min.get()],
                    upper=[self.h_max.get(), self.s_max.get(), self.v_max.get()],
                    min_area=self.min_area.get(),
                    roi=tuple(self.roi) if self.roi else None)

    @staticmethod
    def analyze_frame_with_settings(frame: np.ndarray, settings: dict[str, object]):
        """Analyze a frame with a frozen detection snapshot.

        Independent viewers use this instead of reading the owner's Tk
        variables, so changing another window cannot change their result.
        """
        return segment_frame(frame, settings)

    def snapshot_current_frame(self) -> FrameSnapshot | None:
        if self.frame is None or self.video_path is None:
            return None
        _mask, boxes, _pixels = self.analyze_frame(self.frame)
        return FrameSnapshot(
            video_name=self.video_path.name,
            frame_number=self.frame_index + 1,
            frame=self.frame.copy(),
            settings=self._frame_settings(),
            boxes=tuple(tuple(box) for box in boxes),
            selected_indices=tuple(sorted(self.selected_bboxes)),
            source_path=self.video_path,
            frame_count=self.frame_count,
            fps=self.fps,
            view_mode=self.view_mode.get(),
            include_bbox=bool(self.selected_bboxes),
            bbox_style=getattr(self, "last_bbox_style", DEFAULT_BBOX_STYLE),
        )

    def open_frame_viewer(self) -> None:
        """Open a fixed snapshot; later playback or edits cannot mutate it."""
        if not self.batch_features:
            return
        snapshot = self.snapshot_current_frame()
        if snapshot is not None:
            FrameViewer(self, snapshot)

    def export_frame_snapshot(self, snapshot: FrameSnapshot, parent: tk.Misc,
                              initialdir: Path | None = None) -> None:
        """Preview and save one original-resolution PNG."""
        dialog = tk.Toplevel(parent)
        dialog.title(f"匯出 Frame {snapshot.frame_number} PNG")
        dialog.geometry("900x760")
        dialog.minsize(680, 580)
        dialog.transient(parent)
        dialog.grab_set()
        mode = tk.StringVar(value=snapshot.view_mode if snapshot.view_mode in IMAGE_MODES else "Original")
        include = tk.BooleanVar(value=bool(snapshot.include_bbox and snapshot.selected_indices))
        initial_style = validate_bbox_style(snapshot.bbox_style)
        style = initial_style
        style_vars = {
            "color": tk.StringVar(value=style.color),
            "line_width": tk.StringVar(value=str(style.line_width)),
            "font_size": tk.StringVar(value=str(style.font_size)),
            "label_mode": tk.StringVar(value=LABEL_MODE_NAMES[style.label_mode]),
        }
        preview_error = tk.StringVar()
        ttk.Label(dialog, text=f"{snapshot.video_name} · Frame {snapshot.frame_number}",
                  padding=(12, 10)).pack(anchor=tk.W)
        row = ttk.Frame(dialog, padding=(12, 3))
        row.pack(fill=tk.X)
        ttk.Label(row, text="圖片模式").pack(side=tk.LEFT)
        mode_box = ttk.Combobox(row, textvariable=mode, values=IMAGE_MODES, state="readonly",
                                width=12)
        mode_box.pack(side=tk.LEFT, padx=(8, 0))
        check = ttk.Checkbutton(dialog, text="包含選取 bbox", variable=include)
        check.pack(anchor=tk.W, padx=12, pady=6)
        if not snapshot.selected_indices:
            check.configure(state="disabled")

        style_group = ttk.LabelFrame(dialog, text="bbox 出圖樣式（原始影像像素）", padding=8)
        style_group.pack(fill=tk.X, padx=12, pady=(0, 6))
        for label, key in (("框線／文字色", "color"), ("線寬 px", "line_width"),
                           ("字體 px", "font_size")):
            line = ttk.Frame(style_group)
            line.pack(side=tk.LEFT, padx=(0, 12))
            ttk.Label(line, text=label).pack(side=tk.LEFT, padx=(0, 4))
            entry = ttk.Entry(line, textvariable=style_vars[key], width=10)
            entry.pack(side=tk.LEFT)
            entry.bind("<Return>", lambda _event: update_preview())
            entry.bind("<FocusOut>", lambda _event: update_preview())
        ttk.Label(style_group, text="標籤內容").pack(side=tk.LEFT, padx=(0, 4))
        label_box = ttk.Combobox(style_group, textvariable=style_vars["label_mode"],
                                 values=tuple(LABEL_MODE_NAMES.values()), state="readonly", width=18)
        label_box.pack(side=tk.LEFT)
        ttk.Button(style_group, text="恢復預設", command=lambda: reset_style()).pack(side=tk.LEFT, padx=10)
        ttk.Label(style_group, textvariable=preview_error, foreground="#b3261e",
                  wraplength=800).pack(side=tk.LEFT, padx=4)

        preview_group = ttk.LabelFrame(dialog, text="預覽", padding=6)
        preview_group.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 6))
        preview_canvas = tk.Canvas(preview_group, background="#142029", highlightthickness=0)
        preview_canvas.pack(fill=tk.BOTH, expand=True)
        preview_photo = {"image": None}

        def read_style() -> BboxStyle:
            label_mode = next((key for key, name in LABEL_MODE_NAMES.items()
                               if name == style_vars["label_mode"].get()), style_vars["label_mode"].get())
            return validate_bbox_style({"color": style_vars["color"].get(),
                                        "line_width": style_vars["line_width"].get(),
                                        "font_size": style_vars["font_size"].get(),
                                        "label_mode": label_mode})

        def update_preview(*_args) -> None:
            nonlocal style
            try:
                style = read_style()
                image = compose_frame(snapshot.frame, snapshot.settings, mode.get(),
                                      boxes=snapshot.boxes,
                                      selected_indices=snapshot.selected_indices if include.get() else None,
                                      include_bbox=bool(include.get() and snapshot.selected_indices),
                                      bbox_style=style)
            except (ValueError, cv2.error) as exc:
                preview_error.set(str(exc))
                preview_canvas.delete("all")
                return
            preview_error.set("")
            preview_canvas.delete("all")
            rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            preview_canvas.update_idletasks()
            cw, ch = max(1, preview_canvas.winfo_width()), max(1, preview_canvas.winfo_height())
            height, width = rgb.shape[:2]
            scale = min(cw / width, ch / height)
            size = (max(1, round(width * scale)), max(1, round(height * scale)))
            display = Image.fromarray(rgb).resize(size, Image.Resampling.LANCZOS)
            preview_photo["image"] = ImageTk.PhotoImage(display)
            preview_canvas.create_image((cw - size[0]) // 2, (ch - size[1]) // 2,
                                         anchor=tk.NW, image=preview_photo["image"])

        def reset_style() -> None:
            style_vars["color"].set(DEFAULT_BBOX_STYLE.color)
            style_vars["line_width"].set(str(DEFAULT_BBOX_STYLE.line_width))
            style_vars["font_size"].set(str(DEFAULT_BBOX_STYLE.font_size))
            style_vars["label_mode"].set(LABEL_MODE_NAMES[DEFAULT_BBOX_STYLE.label_mode])
            update_preview()

        def close_dialog() -> None:
            try:
                dialog.grab_release()
            finally:
                dialog.destroy()

        def save():
            try:
                confirmed_style = read_style()
            except ValueError as exc:
                preview_error.set(str(exc))
                return
            annotated = bool(include.get())
            if annotated and not snapshot.selected_indices:
                preview_error.set("目前幀沒有選取 bbox，不能匯出帶框圖片。")
                return
            initial = frame_filename(snapshot.video_name, snapshot.frame_number,
                                     mode.get(), annotated)
            filename = filedialog.asksaveasfilename(
                parent=dialog, title="儲存 PNG", initialdir=str(initialdir) if initialdir else None,
                initialfile=initial, defaultextension=".png",
                filetypes=[("PNG", "*.png")])
            if not filename:
                return
            path = Path(filename)
            if path.exists() and not messagebox.askyesno("覆寫檔案", f"檔案已存在，是否覆寫？\n{path}", parent=dialog):
                return
            try:
                image = compose_frame(snapshot.frame, snapshot.settings, mode.get(),
                                      boxes=snapshot.boxes,
                                      selected_indices=snapshot.selected_indices if annotated else None,
                                      include_bbox=annotated, bbox_style=confirmed_style)
                write_png(path, image)
            except (OSError, ValueError, cv2.error) as exc:
                messagebox.showerror("PNG 匯出失敗", str(exc), parent=dialog)
                return
            self.last_bbox_style = confirmed_style
            close_dialog()
            messagebox.showinfo("PNG 匯出完成", f"已儲存：\n{path}", parent=parent)

        mode_box.bind("<<ComboboxSelected>>", update_preview)
        label_box.bind("<<ComboboxSelected>>", update_preview)
        check.configure(command=update_preview)
        preview_canvas.bind("<Configure>", update_preview)
        buttons = ttk.Frame(dialog, padding=12)
        buttons.pack(fill=tk.X)
        ttk.Button(buttons, text="取消", command=close_dialog).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="儲存", command=save).pack(side=tk.RIGHT, padx=5)
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        update_preview()

    def _close_frame_viewers(self) -> None:
        for viewer in list(self.frame_viewers):
            viewer.close()

    def analyze_frame(self, frame: np.ndarray) -> tuple[np.ndarray, list[tuple[int, int, int, int, int]], int]:
        return segment_frame(frame, self._frame_settings())

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
        if hasattr(self, "buffer_timeline"):
            self.buffer_timeline.set_state(self.frame_count, self.frame_index + 1,
                                           self.capture.cached_ranges() if self.capture else ())
        if self.frame is None or self.canvas.winfo_width() < 2:
            return
        mask, boxes, pixels = self.analyze_frame(self.frame)
        if self.batch_features:
            output = compose_frame(self.frame, self._frame_settings(), self.view_mode.get(),
                                   boxes=boxes, highlight_indices=self.selected_bboxes,
                                   analysis=(mask, boxes, pixels))
        else:
            if self.view_mode.get() == "Mask":
                output = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
            else:
                output = self.frame.copy()
                if self.view_mode.get() == "Overlay":
                    selected, tint = mask > 0, np.zeros_like(output)
                    tint[:, :, 2] = 255
                    output[selected] = cv2.addWeighted(output, 0.35, tint, 0.65, 0)[selected]
            for index, (x, y, width, height, _area) in enumerate(boxes, start=1):
                cv2.rectangle(output, (x, y), (x + width - 1, y + height - 1), (0, 255, 0), 2)
                cv2.putText(output, f"{index}: {width}x{height}", (x, max(16, y - 5)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1, cv2.LINE_AA)
        if self.roi:
            x1, y1, x2, y2 = self.roi
            cv2.rectangle(output, (x1, y1), (x2 - 1, y2 - 1), (0, 255, 255), 2)
        self._show_on_canvas(output)
        self._update_stats(boxes, pixels)
        now = perf_counter()
        if not self.playing or now - getattr(self, "_histogram_updated", 0) >= 0.2:
            self.draw_histogram()
            self._histogram_updated = now

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
            self.tree.insert("", tk.END, iid=f"bbox-{index - 1}", values=(index, x, y, width, height, area,
                f"{x + width / 2:.1f}", f"{y + height / 2:.1f}"))
        if self.batch_features:
            self.selected_bboxes.intersection_update(range(len(boxes)))
            self._sync_tree_selection()
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
        scan = ExactVideoCapture(str(self.video_path))
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
        capture = ExactVideoCapture(str(self.video_path))
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
            if self.batch_features:
                self.clear_bbox_selection()
            self.last_results = []
            self.analysis_config = None
            self.render()

    def roi_release(self, event: tk.Event) -> None:
        self.roi_drag(event)
        self.drag_start = None

    def clear_roi(self) -> None:
        self.roi, self.last_results, self.analysis_config = None, [], None
        if self.batch_features:
            self.clear_bbox_selection()
        self.render()

    def close(self) -> None:
        self.playing = False
        if self.after_id:
            self.root.after_cancel(self.after_id)
        if self.capture:
            self.capture.release()
        self._close_frame_viewers()
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
