"""Local Tk desktop workflow for batch segmentation review."""
from __future__ import annotations

import copy
import math
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import cv2
from video_reader import ExactVideoCapture

import batch_core as core
from app import HSVVideoTester

STATUS = dict(pending="待分析", queued="等待中", running="執行中", completed="完成", failed="失敗", cancelled="已取消")
OUTCOME = dict(stable="已達穩定", not_stable="未達穩定", no_detection="未檢出")
VIDEO_TYPES = [("Video", "*.mp4 *.avi *.mov *.mkv *.m4v"), ("All", "*.*")]
AUTO_EVENTS = (("First", "first_detection"), ("Stable 起點", "first_sustained_stable_start"),
               ("Stable 確認", "first_stable_confirmation"))


def error(parent, exc):
    messagebox.showerror("無法完成操作", str(exc), parent=parent)


def scenario_text(item):
    values = [str(item.get(key)) if item.get(key) not in (None, "") else "—"
              for key in ("scenario_id", "phase", "note")]
    return f"scenarioID: {values[0]} / phase: {values[1]} / note: {values[2]}"


def segment_text(item):
    # Older pairing imports used the opaque run ID as the visible segment label.
    return "" if item.get("pairing_match") and not item.get("pairing_run_id") else item.get("segment", "")


def bbox_text(summary, prefix):
    if not summary.get(prefix + "_bbox_valid"):
        return "無 bbox"
    return f'{summary[prefix + "_bbox_width"]} × {summary[prefix + "_bbox_height"]} px'


def frame_details(row):
    if not row:
        return "此幀不在分析範圍內"
    return (f'Frame {row["frame"]} · {row["timestamp"]:.3f}s · Raw {row["raw_detected"]} · '
            f'Rolling {row["rolling_hit_count"]}/{row["rolling_window_length"]} = {row["rolling_rate"]:.3f}'
            f'{" (warm-up)" if not row["window_ready"] else ""} · '
            f'ON streak {row["on_qualifying_streak"]} · Stable {row["stable_detected"]} · '
            f'BBox {str(row["bbox_width"]) + " × " + str(row["bbox_height"]) if row["bbox_valid"] else "無"}')


class VideoEditor(HSVVideoTester):
    """Reuse proven video navigation and histogram, with isolated item/run settings."""
    def __init__(self, owner, item, review=False, number=None):
        self.owner, self.item, self.review = owner, item, review
        self.batch_features = True
        self.run = copy.deepcopy(item.get("latest_run")) if review else None
        self.initial = copy.deepcopy(self.run["settings"] if self.run else item["settings"])
        self.rows = core.load_rows(self.run) if self.run else []
        self.rows_start = self.rows[0]["frame"] if self.rows else 0
        self.ready = False
        top = tk.Toplevel(owner.root)
        super().__init__(top)
        self.ready = True
        top.title(("結果複核 · " if review else "設定與預覽 · ") + item["name"])
        top.geometry("1360x880")
        top.minsize(1050, 700)
        top.protocol("WM_DELETE_WINDOW", self.close)
        try:
            if review and core.source_identity(item["path"]) != self.run["source_identity"]:
                raise ValueError("來源影片已更動，不能用新影片複核舊結果。請重新指定相同來源或重跑。")
            meta = core.probe_video(item["path"])
            self.preview_identity = core.source_identity(item["path"])
            core.validate_settings(self.initial, meta)
            self.capture = ExactVideoCapture(item["path"])
            self.video_path = Path(item["path"])
            self.frame_count, self.fps = meta["frame_count"], meta["fps"]
            self.timeline.configure(to=self.frame_count - 1)
            self.read_frame((number or 1) - 1)
            if review and number:
                self.zoom_review(number)
            self.update_range()
            self.load_manual_fields()
        except Exception:
            if self.capture:
                self.capture.release()
            top.destroy()
            raise

    def _build_ui(self):
        s = self.initial
        for var, value in zip((self.h_min, self.s_min, self.v_min), s["lower"]):
            var.set(value)
        for var, value in zip((self.h_max, self.s_max, self.v_max), s["upper"]):
            var.set(value)
        for var, key in ((self.min_area, "min_area"), (self.window_size, "window_n"),
                         (self.stable_on, "stable_on_m"), (self.stable_off, "stable_off_count"),
                         (self.stable_confirm_frames, "stable_confirm_frames")):
            var.set(s[key])
        self.roi = tuple(s["roi"]) if s["roi"] else None
        self.start_percent = tk.StringVar(value=str(s["start_percent"]))
        self.end_percent = tk.StringVar(value=str(s["end_percent"]))
        self.session = tk.StringVar(value=self.item["session"])
        self.camera_name = tk.StringVar(value=self.item["camera"])
        self.segment_name = tk.StringVar(value=self.item.get("segment", ""))
        self.range_text = tk.StringVar()
        self.detail_text = tk.StringVar(value="拖曳預覽設定 ROI；使用原始影片像素座標。")
        self.settings_state = tk.StringVar(value="使用已儲存設定")
        bar = ttk.Frame(self.root, padding=8)
        bar.pack(fill="x")
        ttk.Label(self.root, text=scenario_text(self.item), wraplength=1250, padding=(10, 3)).pack(fill="x")
        for title, callback in (("播放／暫停", self.toggle_play), ("◀ 上一幀", lambda: self.step(-1)),
                                ("下一幀 ▶", lambda: self.step(1))):
            ttk.Button(bar, text=title, command=callback).pack(side="left", padx=3)
        ttk.Combobox(bar, textvariable=self.view_mode, values=("Original", "Mask", "Overlay"),
                     state="readonly", width=10).pack(side="left", padx=8)
        self.view_mode.trace_add("write", lambda *_: self.render())
        ttk.Button(bar, text="獨立檢視當幀", command=self.open_frame_viewer).pack(side="left", padx=3)
        ttk.Button(bar, text="匯出當幀 PNG…", command=self.export_current_png).pack(side="left", padx=3)
        if not self.review:
            ttk.Button(bar, text="清除 ROI", command=self.clear_roi).pack(side="left")
            ttk.Button(bar, text="儲存設定", command=self.save).pack(side="right")
        else:
            ttk.Label(bar, text="使用分析當次參數快照 · " + self.run["run_id"][:8]).pack(side="right")
            for label, prefix in AUTO_EVENTS:
                number = self.run["summary"].get(prefix + "_frame")
                ttk.Button(bar, text=label, state="normal" if number else "disabled",
                           command=lambda n=number: self.jump_review(n)).pack(side="left", padx=4)
        annotations = ttk.LabelFrame(self.root, text="人工事件標註 · 與自動判定分開保存（原始 1-based frame）", padding=5)
        annotations.pack(fill="x", padx=8, pady=(0, 4))
        self.manual_kind = tk.StringVar(value="人工首次檢出")
        self.manual_frame = tk.StringVar()
        self.manual_note = tk.StringVar()
        self.manual_status = tk.StringVar(value="尚未標註")
        line = ttk.Frame(annotations)
        line.pack(fill="x")
        selector = ttk.Combobox(line, textvariable=self.manual_kind, values=tuple(core.MANUAL_EVENTS.values()), state="readonly", width=16)
        selector.pack(side="left", padx=3)
        selector.bind("<<ComboboxSelected>>", lambda _: self.load_manual_fields())
        ttk.Label(line, text="Frame").pack(side="left", padx=3)
        manual_entry = ttk.Entry(line, textvariable=self.manual_frame, width=9)
        manual_entry.pack(side="left")
        manual_entry.bind("<Return>", lambda _: self.save_manual())
        for label, callback in (("標記目前幀", self.mark_current), ("儲存標註", self.save_manual),
                                ("跳到標註", self.jump_manual), ("回推穩定起點", self.jump_manual_start), ("清除標註", self.clear_manual)):
            ttk.Button(line, text=label, command=callback).pack(side="left", padx=3)
        ttk.Label(line, text="備註").pack(side="left", padx=3)
        ttk.Entry(line, textvariable=self.manual_note).pack(side="left", fill="x", expand=True)
        ttk.Label(annotations, textvariable=self.manual_status).pack(anchor="w")
        footer = ttk.Frame(self.root, padding=8)
        footer.pack(side="bottom", fill="x")
        ttk.Label(footer, textvariable=self.detail_text, wraplength=1250).pack(anchor="w")
        ttk.Label(footer, text="時間依 reported FPS 換算；VFR 時間為名目估算，事件以 frame 為準。").pack(anchor="w")
        body = ttk.Panedwindow(self.root, orient="horizontal")
        body.pack(fill="both", expand=True, padx=8)
        left, controls_outer = ttk.Frame(body), ttk.Frame(body, width=350)
        body.add(left, weight=4)
        body.add(controls_outer, weight=1)
        controls_canvas = tk.Canvas(controls_outer, width=355, highlightthickness=0)
        controls_scroll = ttk.Scrollbar(controls_outer, command=controls_canvas.yview)
        controls_canvas.configure(yscrollcommand=controls_scroll.set)
        controls_scroll.pack(side="right", fill="y")
        controls_canvas.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(controls_canvas)
        control_window = controls_canvas.create_window(0, 0, window=right, anchor="nw")
        controls_canvas.bind("<Configure>", lambda e: controls_canvas.itemconfigure(control_window, width=e.width))
        right.bind("<Configure>", lambda _: controls_canvas.configure(scrollregion=controls_canvas.bbox("all")))
        self.canvas = tk.Canvas(left, bg="#142029", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _: self.render())
        if not self.review:
            self.canvas.bind("<ButtonPress-1>", self.roi_press)
            self.canvas.bind("<B1-Motion>", self.roi_drag)
            self.canvas.bind("<ButtonRelease-1>", self.roi_release)
        nav = ttk.Frame(left, padding=4)
        nav.pack(fill="x")
        ttk.Label(nav, text="Frame").pack(side="left")
        entry = ttk.Entry(nav, textvariable=self.target_frame, width=10)
        entry.pack(side="left", padx=5)
        entry.bind("<Return>", lambda _: self.go_to_frame())
        ttk.Button(nav, text="跳轉", command=self.go_to_frame).pack(side="left")
        ttk.Label(nav, text="← / → 逐幀").pack(side="right")
        self.timeline = ttk.Scale(left, from_=0, to=1, command=self.seek)
        self.timeline.pack(fill="x")
        ttk.Label(left, textvariable=self.status).pack(anchor="w", pady=5)
        if self.review:
            plot_item = copy.deepcopy(self.item)
            plot_item["latest_run"] = self.run
            self.review_plot = DetectionPlot(left, plot_item, lambda n: self.read_frame(n - 1))
            self.review_plot.rows = self.rows
            self.review_plot.active = True
            self.review_plot.low, self.review_plot.high = self.rows[0]["frame"], self.rows[-1]["frame"]
            self.review_plot.pack(side="bottom", fill="x")
            zoom = ttk.Frame(left)
            zoom.pack(side="bottom", fill="x")
            ttk.Button(zoom, text="全分析區段", command=self.review_plot.window).pack(side="left")
            ttk.Button(zoom, text="目前幀附近", command=lambda: self.zoom_review(self.frame_index + 1)).pack(side="left", padx=4)
            ttk.Button(zoom, text="自訂範圍…", command=lambda: self.owner.custom_range(self.review_plot)).pack(side="left")
        settings = ttk.LabelFrame(right, text="HSV / Raw detection", padding=6)
        settings.pack(fill="x")
        for args in (("H min", self.h_min, 0, 179), ("H max", self.h_max, 0, 179),
                     ("S min", self.s_min, 0, 255), ("S max", self.s_max, 0, 255),
                     ("V min", self.v_min, 0, 255), ("V max", self.v_max, 0, 255),
                     ("Min blob area", self.min_area, 1, 2000)):
            self._add_slider(settings, *args)
        stable = ttk.LabelFrame(right, text="Stable · 完整 N 幀 + 連續 K 次符合 ON", padding=6)
        stable.pack(fill="x", pady=5)
        for args in (("Window N", self.window_size, 1, 10000), ("ON ≥", self.stable_on, 1, 10000),
                     ("OFF ≤", self.stable_off, 0, 10000), ("Confirm K", self.stable_confirm_frames, 1, 10000)):
            self._add_spinbox(stable, *args)
        interval = ttk.LabelFrame(right, text="分析範圍與標籤", padding=6)
        interval.pack(fill="x")
        for label, variable in (("開始 %", self.start_percent), ("結束 %", self.end_percent),
                                ("場次", self.session), ("相機", self.camera_name), ("區段", self.segment_name)):
            row = ttk.Frame(interval)
            row.pack(fill="x")
            ttk.Label(row, text=label, width=10).pack(side="left")
            ttk.Entry(row, textvariable=variable, width=23).pack(side="left", fill="x", expand=True)
        ttk.Label(interval, textvariable=self.range_text, wraplength=330).pack(anchor="w")
        ttk.Label(interval, textvariable=self.settings_state).pack(anchor="w")
        self.start_percent.trace_add("write", lambda *_: self.update_range())
        self.end_percent.trace_add("write", lambda *_: self.update_range())
        histogram = ttk.LabelFrame(right, text="Hue histogram · ROI + S/V", padding=4)
        histogram.pack(fill="x", pady=5)
        self.hist_canvas = tk.Canvas(histogram, width=330, height=85, bg="white", highlightthickness=0)
        self.hist_canvas.pack(fill="x")
        self.hist_canvas.bind("<Configure>", lambda _: self.draw_histogram())
        ttk.Label(right, textvariable=self.roi_text).pack(anchor="w")
        ttk.Label(right, textvariable=self.stats_text, wraplength=340).pack(anchor="w")
        self.tree = ttk.Treeview(right, columns=("id", "x", "y", "w", "h", "area", "cx", "cy"),
                                 show="headings", height=3, selectmode="extended")
        for key in self.tree["columns"]:
            self.tree.heading(key, text=key)
            self.tree.column(key, width=40)
        self.tree.pack(fill="both", expand=True, pady=4)
        bbox_tools = ttk.Frame(right)
        bbox_tools.pack(fill="x")
        ttk.Button(bbox_tools, text="全選 bbox", command=self.select_all_bboxes).pack(side="left")
        ttk.Button(bbox_tools, text="清除選取", command=self.clear_bbox_selection).pack(side="left", padx=4)
        self.tree.bind("<<TreeviewSelect>>", self._bbox_tree_selection)
        def wheel_bind(widget):
            widget.bind("<MouseWheel>", lambda e: controls_canvas.yview_scroll(-int(e.delta / 120), "units"))
            for child in widget.winfo_children():
                wheel_bind(child)
        wheel_bind(right)
        if self.review:
            def disable(widget):
                if isinstance(widget, (ttk.Entry, ttk.Scale, ttk.Spinbox)):
                    widget.configure(state="disabled")
                for child in widget.winfo_children():
                    disable(child)
            for group in (settings, stable, interval):
                disable(group)
        else:
            for variable in (self.h_min, self.h_max, self.s_min, self.s_max, self.v_min, self.v_max,
                             self.min_area, self.window_size, self.stable_on, self.stable_off,
                             self.stable_confirm_frames, self.start_percent, self.end_percent,
                             self.session, self.camera_name, self.segment_name):
                variable.trace_add("write", lambda *_: self.settings_state.set("尚未儲存變更"))

    def _add_slider(self, parent, label, variable, low, high):
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=1)
        ttk.Label(row, text=label, width=13).pack(side="left")
        scale = ttk.Scale(row, from_=low, to=high, value=variable.get())
        scale.pack(side="left", fill="x", expand=True)
        ttk.Entry(row, textvariable=variable, width=7).pack(side="right")
        def slide(value):
            variable.set(round(float(value)))
        def typed(*_):
            try:
                scale.configure(value=max(low, min(high, variable.get())))
                if label.startswith(("H ", "S ", "V ")) or label == "Min blob area":
                    self.clear_bbox_selection()
                self.render()
            except tk.TclError:
                pass
        scale.configure(command=slide)
        variable.trace_add("write", typed)

    def _frame_settings(self):
        """Freeze the analysis snapshot for review viewers and exports."""
        if self.review:
            settings = self.initial
            return dict(lower=list(settings["lower"]), upper=list(settings["upper"]),
                        min_area=settings["min_area"],
                        roi=tuple(settings["roi"]) if settings.get("roi") else None)
        return super()._frame_settings()

    def manual_key(self):
        return next(key for key, label in core.MANUAL_EVENTS.items() if label == self.manual_kind.get())

    def load_manual_fields(self):
        annotation = self.item.get("manual_events", {}).get(self.manual_key())
        self.manual_frame.set(str(annotation["frame"]) if annotation else "")
        self.manual_note.set(annotation.get("note", "") if annotation else "")
        if annotation:
            reason = core.manual_event_reason(self.item, annotation)
            self.manual_status.set(f'已標註 F{annotation["frame"]} · {annotation["timestamp"]:.3f}s' +
                                   (" · " + reason if reason else " · 可修改 frame 後儲存"))
        else:
            self.manual_status.set("尚未標註；可輸入 frame 後儲存，或直接標記目前幀。不需先執行自動分析。")

        start = core.resolved_manual_events(self.item).get(core.MANUAL_START)
        if start:
            suffix = "（已截至影片開頭）" if start["clamped_to_first_frame"] else ""
            self.manual_status.set(self.manual_status.get() +
                                   f' · 人工穩定起點 F{start["frame"]}（K={start["stable_confirm_frames"]}）{suffix}')

    def jump_manual_start(self):
        annotation = core.resolved_manual_events(self.item).get(core.MANUAL_START)
        if annotation is None:
            self.manual_status.set("請先儲存人工穩定確認，即可自動回推起點。")
            return
        reason = core.manual_event_reason(self.item, annotation)
        if reason:
            error(self.root, reason)
            return
        self.playing = False
        self.read_frame(annotation["frame"] - 1)
        if self.review:
            self.zoom_review(annotation["frame"])

    def save_manual(self):
        try:
            number = int(self.manual_frame.get().strip())
            core.set_manual_event(self.item, self.manual_key(), number, self.manual_note.get(), self.preview_identity,
                                  settings=self.initial if self.review else self.config())
            self.owner.changed()
            self.load_manual_fields()
            if self.owner.dirty:
                self.manual_status.set(self.manual_status.get() + " · 尚未存檔，請儲存專案")
            if self.review:
                self.review_plot.item["manual_events"] = copy.deepcopy(self.item["manual_events"])
                self.review_plot.manual_valid = None
                self.review_plot.draw()
            return True
        except (ValueError, OSError, cv2.error) as exc:
            error(self.root, exc)
            return False

    def mark_current(self):
        self.playing = False
        self.manual_frame.set(str(self.frame_index + 1))
        return self.save_manual()

    def jump_manual(self):
        annotation = self.item.get("manual_events", {}).get(self.manual_key())
        if annotation:
            reason = core.manual_event_reason(self.item, annotation)
            if reason:
                error(self.root, reason)
                return
            self.playing = False
            self.read_frame(annotation["frame"] - 1)
            if self.review:
                self.zoom_review(annotation["frame"])

    def clear_manual(self):
        self.item.setdefault("manual_events", {}).pop(self.manual_key(), None)
        self.owner.changed()
        self.load_manual_fields()
        if self.review:
            self.review_plot.item["manual_events"] = copy.deepcopy(self.item["manual_events"])
            self.review_plot.manual_valid = None
            self.review_plot.draw()

    def config(self):
        result = dict(lower=[self.h_min.get(), self.s_min.get(), self.v_min.get()],
                      upper=[self.h_max.get(), self.s_max.get(), self.v_max.get()],
                      min_area=self.min_area.get(), roi=list(self.roi) if self.roi else None,
                      start_percent=float(self.start_percent.get()), end_percent=float(self.end_percent.get()),
                      window_n=self.window_size.get(), stable_on_m=self.stable_on.get(),
                      stable_off_count=self.stable_off.get(), stable_confirm_frames=self.stable_confirm_frames.get())
        core.validate_settings(result, self.item.get("metadata") or None)
        return result

    def update_range(self):
        if not self.ready or not self.frame_count:
            return
        try:
            start, end = core.analysis_bounds(self.config(), self.frame_count)
            self.range_text.set(f"Frame {start + 1}–{end} · {start / self.fps:.3f}–{(end - 1) / self.fps:.3f} s")
        except (ValueError, tk.TclError) as exc:
            self.range_text.set(str(exc))

    def read_frame(self, index):
        if self.capture is None:
            return
        index = max(0, min(index, self.frame_count - 1))
        if index != self.frame_index:
            self.clear_bbox_selection()
        try:
            self.frame = core.read_exact_frame(self.video_path, index + 1, self.capture)
        except ValueError as exc:
            self.playing = False
            self.status.set(str(exc))
            return
        self.frame_index = index
        self.target_frame.set(index + 1)
        self.timeline.set(index)
        self.render()
        if self.review:
            i = index + 1 - self.rows_start
            row = self.rows[i] if 0 <= i < len(self.rows) else None
            self.detail_text.set(frame_details(row))
            self.owner.sync_cursor(self.item["id"], index + 1)
            self.review_plot.set_cursor(index + 1)

    def zoom_review(self, number):
        try:
            seconds = float(self.owner.event_seconds.get())
            if not math.isfinite(seconds) or seconds <= 0:
                raise ValueError("事件前後秒數必須大於 0")
            radius = max(1, round(seconds * self.fps))
            self.review_plot.window(number - radius, number + radius)
        except ValueError as exc:
            error(self.root, exc)

    def jump_review(self, number):
        self.playing = False
        self.read_frame(number - 1)
        self.zoom_review(number)

    def toggle_play(self):
        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        super().toggle_play()

    def render(self):
        try:
            super().render()
        except (ValueError, tk.TclError):
            pass  # Intermediate text in a numeric entry is not an applied setting.

    def export_current_png(self):
        self.playing = False
        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        snapshot = self.snapshot_current_frame()
        if snapshot is not None:
            self.export_frame_snapshot(snapshot, self.root,
                                       self.video_path.parent if self.video_path else None)

    def roi_release(self, event):
        super().roi_release(event)
        self.settings_state.set("尚未儲存變更")

    def clear_roi(self):
        super().clear_roi()
        self.settings_state.set("尚未儲存變更")

    def save(self):
        try:
            self.item.update(settings=self.config(), session=self.session.get(), camera=self.camera_name.get(),
                             segment=self.segment_name.get())
            self.owner.changed()
            persisted = self.owner.project_path is not None and not self.owner.dirty
            self.settings_state.set("已套用至專案" + ("並儲存" if persisted else "；尚未寫入檔案，請儲存專案"))
            return True
        except (ValueError, OSError, tk.TclError) as exc:
            error(self.root, exc)
            return False

    def close(self):
        if not self.review and not self.save():
            return
        self.owner.editor = None
        super().close()


class DetectionPlot(tk.Canvas):
    def __init__(self, parent, item, callback, **kwargs):
        super().__init__(parent, bg="#0e202b", highlightthickness=0, height=210, **kwargs)
        self.item, self.callback = item, callback
        self.rows = None
        self.low = self.high = None
        self.cursor = None
        self.manual_valid = None
        self.active = False
        self.bind("<Configure>", lambda _: self.draw() if self.active else None)
        self.bind("<Motion>", self.hover)
        self.bind("<Button-1>", self.click)

    def activate(self, active):
        if active:
            if self.active:
                return
            self.active = True
            try:
                run = self.item.get("latest_run")
                self.rows = core.load_rows(run) if run else []
                self.manual_valid = None
                if self.rows and self.low is None:
                    self.low, self.high = self.rows[0]["frame"], self.rows[-1]["frame"]
                self.draw()
            except (OSError, ValueError) as exc:
                self.rows = []
                self.delete("all")
                self.create_text(20, 50, anchor="nw", text=f"無法載入結果：{exc}", fill="#ffbc6b")
        elif self.active:
            self.active = False
            self.rows = None
            self.delete("all")

    def window(self, low=None, high=None):
        run = self.item.get("latest_run")
        if not run:
            return
        start, end = run["analysis_start_frame"], run["analysis_end_frame"]
        self.low = max(start, min(end, low if low is not None else start))
        self.high = max(start, min(end, high if high is not None else end))
        if self.low > self.high:
            self.low = self.high
        self.draw()

    def draw(self):
        self.delete("all")
        w = max(self.winfo_width(), 80)
        self.create_text(45, 17, anchor="w", text="Detection state", fill="#e8f0f6", font=("Segoe UI", 11, "bold"))
        for offset, text, color in ((200, "━ Raw", "#ffc16c"), (125, "━ Rolling", "#61b8ff"), (35, "━ Stable", "#39dfbf")):
            self.create_text(w - offset, 17, anchor="e", text=text, fill=color)
        if not self.rows:
            self.create_text(45, 85, anchor="w", text="尚無分析結果", fill="#9eb0bd")
            return
        x1, x2, top, bottom = 45, w - 18, 42, 144
        if x2 <= x1:
            return
        for value in (0, .5, 1):
            y = bottom - value * (bottom - top)
            self.create_line(x1, y, x2, y, fill="#29414e")
            self.create_text(x1 - 8, y, text=str(value), fill="#91a8b8", anchor="e")
        self.create_text(x1, 158, text=str(self.low), fill="#9eb0bd", anchor="w")
        self.create_text(x2, 158, text=f"{self.high}  Frame", fill="#9eb0bd", anchor="e")
        start = self.rows[0]["frame"]
        rows = self.rows[max(0, self.low - start):self.high - start + 1]
        count = len(rows)
        # Each pixel bucket retains min and max, preserving single-frame spikes.
        bucket = max(1, math.ceil(count / max(1, int(x2 - x1))))
        for field, color, stroke, dash in (("stable_detected", "#39dfbf", 4, ()),
                                          ("rolling_rate", "#61b8ff", 2, ()),
                                          ("raw_detected", "#ffc16c", 1, (2, 4))):
            coords = []
            for i in range(0, count, bucket):
                group = rows[i:i + bucket]
                x = x1 + (group[0]["frame"] - self.low) / max(1, self.high - self.low) * (x2 - x1)
                values = [r[field] for r in group]
                y = bottom - values[0] * (bottom - top)
                if coords:
                    coords.extend((x, coords[-1]))
                coords.extend((x, y))
                if bucket > 1:
                    self.create_line(x, bottom - min(values) * (bottom - top), x,
                                     bottom - max(values) * (bottom - top), fill=color)
            if len(coords) >= 4:
                self.create_line(*coords, fill=color, width=stroke, dash=dash)
            elif coords:
                self.create_oval(coords[0] - 2, coords[1] - 2, coords[0] + 2, coords[1] + 2, fill=color)
        summary = self.item["latest_run"]["summary"]
        for label, prefix, color, y in (("First", "first_detection", "#ffc16c", 52),
                                       ("Stable 起點", "first_sustained_stable_start", "#ff9a67", 70),
                                       ("Stable 確認", "first_stable_confirmation", "#39dfbf", 88)):
            number = summary[prefix + "_frame"]
            if number is not None and self.low <= number <= self.high:
                x = x1 + (number - self.low) / max(1, self.high - self.low) * (x2 - x1)
                self.create_line(x, top, x, bottom, fill=color, dash=(4, 3), tags=prefix)
                self.create_text(x - 4 if x > x2 - 100 else x + 4, y,
                                 anchor="e" if x > x2 - 100 else "w", text=label, fill=color, tags=prefix)
        # Human annotations do not change the automatic series or first events.
        for index, (event, annotation) in enumerate(core.resolved_manual_events(self.item).items()):
            number = annotation["frame"]
            if event not in core.ALL_MANUAL_EVENTS or not self.low <= number <= self.high:
                continue
            if self.manual_valid is None:
                self.manual_valid = {key: not core.manual_event_reason(self.item, value)
                                     for key, value in core.resolved_manual_events(self.item).items()}
            if not self.manual_valid.get(event):
                continue
            x = x1 + (number - self.low) / max(1, self.high - self.low) * (x2 - x1)
            self.create_line(x, top, x, bottom, fill="#ee9bff", dash=(2, 2), width=2, tags="manual_event")
            self.create_text(x - 4 if x > x2 - 110 else x + 4, 108 + 17 * index,
                             anchor="e" if x > x2 - 110 else "w", text=core.ALL_MANUAL_EVENTS[event],
                             fill="#ee9bff", tags="manual_event")
        self.set_cursor(self.cursor)

    def number_at(self, x):
        if not self.rows or self.low is None:
            return None
        fraction = max(0, min(1, (x - 45) / max(1, self.winfo_width() - 63)))
        return round(self.low + fraction * (self.high - self.low))

    def set_cursor(self, number):
        self.cursor = number
        self.delete("cursor")
        if not self.rows or number is None:
            return
        i = number - self.rows[0]["frame"]
        if 0 <= i < len(self.rows):
            self.create_text(45, 180, anchor="nw", text=frame_details(self.rows[i]), fill="#d1e6f2",
                             width=max(100, self.winfo_width() - 65), tags="cursor", font=("Segoe UI", 9))
        if self.low <= number <= self.high:
            x = 45 + (number - self.low) / max(1, self.high - self.low) * (self.winfo_width() - 63)
            self.create_line(x, 42, x, 144, fill="white", tags="cursor")

    def hover(self, event):
        self.set_cursor(self.number_at(event.x))

    def click(self, event):
        number = self.number_at(event.x)
        if number is not None:
            self.set_cursor(number)
            self.callback(number)


class BatchApp:
    def __init__(self, root):
        self.root = root
        root.title("ECU Segmentation · 批次分析")
        root.geometry("1380x900")
        root.minsize(1050, 720)
        self.project = core.new_project()
        self.project_path = None
        self.dirty = False
        self.editor = None
        self.busy = False
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.selected = {}
        self.plots = {}
        self.filter = tk.StringVar(value="全部")
        self.message = tk.StringVar(value="匯入影片，逐支設定並儲存專案，即可開始批次分析。")
        self.total_progress = tk.DoubleVar()
        self.event_seconds = tk.StringVar(value="2")
        self.build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_id = root.after(100, self.poll)
        root.bind("<Destroy>", self.on_destroy, add="+")

    def button(self, parent, text, callback):
        b = ttk.Button(parent, text=text, command=callback)
        b.pack(side="left", padx=3, pady=3)
        return b

    def build(self):
        style = ttk.Style()
        style.configure("Treeview", rowheight=28)
        bar = ttk.Frame(self.root, padding=8)
        bar.pack(fill="x")
        for label, callback in (("新專案", self.new), ("開啟專案", self.open), ("儲存專案", self.save),
                                ("匯入影片", self.add_videos), ("匯入 pairing.json", self.add_pairing)):
            self.button(bar, label, callback)
        self.project_label = ttk.Label(bar, text="尚未儲存", foreground="#64758a")
        self.project_label.pack(side="right", padx=8)
        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=10)
        self.setup_tab, self.progress_tab, self.results_tab = (ttk.Frame(self.tabs) for _ in range(3))
        for tab, label in ((self.setup_tab, "① 逐支設定與預覽"), (self.progress_tab, "② 批次分析"), (self.results_tab, "③ 結果總覽")):
            self.tabs.add(tab, text=label)
        tools = ttk.Frame(self.setup_tab, padding=7)
        tools.pack(fill="x")
        for label, callback in (("設定／預覽", lambda: self.edit_focused()), ("勾選全部", lambda: self.select_all(True)),
                                ("取消勾選", lambda: self.select_all(False)), ("套用設定…", self.copy_settings),
                                ("複製為新區段", self.duplicate), ("重新指定影片", self.relink), ("移除勾選", self.remove)):
            self.button(tools, label, callback)
        columns = ("selected", "name", "scenario", "phase", "note", "session", "camera", "range", "status", "outcome")
        table_frame = ttk.Frame(self.setup_tab)
        table_frame.pack(fill="both", expand=True)
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        for key, title, width in zip(columns, ("勾選", "影片／區段", "scenarioID", "phase", "note", "場次", "相機", "分析 %", "執行狀態", "分析結論"),
                                     (48, 250, 90, 70, 220, 60, 100, 95, 100, 150)):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=40, stretch=key in ("name", "note", "outcome"))
        scroll = ttk.Scrollbar(table_frame, command=self.table.yview)
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        horizontal.pack(side="bottom", fill="x")
        self.table.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.table.bind("<Button-1>", self.toggle_selection)
        self.table.bind("<Double-1>", lambda e: self.edit_focused() if self.table.identify_column(e.x) != "#1" else None)
        self.table.bind("<<TreeviewSelect>>", self.show_item_info)
        self.item_info = tk.StringVar(value="點第一欄勾選工作項目；雙擊影片開啟設定。每個區段獨立保存。")
        ttk.Label(self.setup_tab, textvariable=self.item_info, wraplength=1200, padding=10).pack(fill="x")
        actions = ttk.Frame(self.setup_tab, padding=8)
        actions.pack(fill="x")
        self.button(actions, "複製上一支設定", self.copy_previous)
        self.button(actions, "分析勾選影片", lambda: self.start_batch(False))
        self.button(actions, "分析全部影片", lambda: self.start_batch(True))
        progress_tools = ttk.Frame(self.progress_tab, padding=10)
        progress_tools.pack(fill="x")
        self.button(progress_tools, "取消批次", self.cancel_batch)
        self.button(progress_tools, "查看結果", lambda: self.tabs.select(self.results_tab))
        ttk.Progressbar(self.progress_tab, variable=self.total_progress, maximum=100).pack(fill="x", padx=12, pady=8)
        self.jobs = ttk.Treeview(self.progress_tab, columns=("name", "state", "progress", "error"), show="headings")
        for key, text, width in (("name", "影片", 400), ("state", "狀態", 100), ("progress", "已處理／預期幀數", 180), ("error", "訊息", 450)):
            self.jobs.heading(key, text=text)
            self.jobs.column(key, width=width)
        self.jobs.pack(fill="both", expand=True, padx=12, pady=8)
        results_tools = ttk.Frame(self.results_tab, padding=7)
        results_tools.pack(fill="x")
        choice = ttk.Combobox(results_tools, textvariable=self.filter,
                              values=("全部", "未檢出", "未達穩定", "已達穩定", "失敗", "已取消", "結果過期"), state="readonly", width=13)
        choice.pack(side="left", padx=3)
        choice.bind("<<ComboboxSelected>>", lambda _: self.refresh_results())
        self.button(results_tools, "勾選重跑", lambda: self.start_batch(False))
        self.button(results_tools, "匯出勾選", lambda: self.export(False))
        self.button(results_tools, "匯出全部", lambda: self.export(True))
        ttk.Label(results_tools, text="事件前後秒數").pack(side="left", padx=(15, 3))
        ttk.Entry(results_tools, textvariable=self.event_seconds, width=5).pack(side="left")
        container = ttk.Frame(self.results_tab)
        container.pack(fill="both", expand=True)
        self.result_canvas = tk.Canvas(container, highlightthickness=0, bg="#edf1f5")
        scrollbar = ttk.Scrollbar(container, command=self.scroll_results)
        self.result_canvas.configure(yscrollcommand=scrollbar.set)
        self.result_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.result_body = ttk.Frame(self.result_canvas)
        self.result_window = self.result_canvas.create_window(0, 0, window=self.result_body, anchor="nw")
        self.result_body.bind("<Configure>", lambda _: self.result_canvas.configure(scrollregion=self.result_canvas.bbox("all")))
        self.result_canvas.bind("<Configure>", self.resize_results)
        self.root.bind_all("<MouseWheel>", self.wheel, add="+")
        self.tabs.bind("<<NotebookTabChanged>>", lambda _: self.root.after_idle(self.visible_plots))
        ttk.Label(self.root, textvariable=self.message, padding=10, wraplength=1280).pack(fill="x")

    def items(self, all_items=False):
        return [i for i in self.project["items"] if all_items or
                i["id"] in self.selected and self.selected[i["id"]].get()]

    def focused(self):
        selection = self.table.selection()
        return next((i for i in self.project["items"] if selection and i["id"] == selection[0]), None)

    def idle_required(self):
        if self.busy:
            self.message.set("請等目前批次／匯出完成，或先取消批次。")
            return False
        return True

    def close_editor(self):
        if self.editor:
            self.editor.close()
        return self.editor is None

    def changed(self):
        self.dirty = True
        if self.project_path:
            try:
                core.save_project(self.project_path, self.project)
                self.dirty = False
            except OSError as exc:
                self.message.set(f"專案保存失敗，變更仍在記憶體中；請儲存重試：{exc}")
        self.refresh()

    def save(self):
        if self.editor and not self.editor.review and not self.editor.save():
            return False
        if not self.project_path:
            path = filedialog.asksaveasfilename(parent=self.root, title="儲存分析專案", defaultextension=".json",
                                               initialfile="analysis_project.json", filetypes=[("Project JSON", "*.json")])
            if not path:
                return False
            self.project_path = Path(path).resolve()
            self.project["name"] = self.project_path.stem
        try:
            core.save_project(self.project_path, self.project)
            self.dirty = False
            self.project_label.configure(text=self.project_path.name)
            self.message.set(f"已儲存：{self.project_path}")
            return True
        except OSError as exc:
            error(self.root, exc)
            return False

    def can_replace(self):
        if not self.idle_required() or not self.close_editor():
            return False
        if self.dirty:
            answer = messagebox.askyesnocancel("儲存專案", "目前專案尚未儲存，是否先儲存？", parent=self.root)
            return False if answer is None else self.save() if answer else True
        return True

    def new(self):
        if not self.can_replace():
            return
        self.project, self.project_path = core.new_project(), None
        self.selected, self.dirty = {}, False
        self.jobs.delete(*self.jobs.get_children())
        self.refresh()

    def open(self):
        if not self.can_replace():
            return
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("Project JSON", "*.json")])
        if not path:
            return
        try:
            project = core.load_project(path)
            self.project, self.project_path = project, Path(path).resolve()
            self.selected, self.dirty = {}, False
            self.jobs.delete(*self.jobs.get_children())
            self.refresh()
            self.message.set("已還原設定與結果。雙擊影片設定，或到結果總覽複核。")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            error(self.root, exc)

    def add_videos(self):
        if not self.idle_required():
            return
        paths = filedialog.askopenfilenames(parent=self.root, filetypes=VIDEO_TYPES)
        for path in paths:
            self.project["items"].append(core.new_item(path))
        if paths:
            self.changed()

    def add_pairing(self):
        if not self.idle_required():
            return
        path = filedialog.askopenfilename(parent=self.root, title="匯入 pairing.json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        folder = filedialog.askdirectory(parent=self.root, title="影片根目錄（可取消，使用 JSON 原始路徑）")
        try:
            imported = core.import_pairing(path, folder or None)
            added, updated = core.merge_pairing_items(self.project, imported)
            self.changed()
            self.message.set(f"新增 {added} 項、更新 {updated} 項情境資訊（scenarioID / phase / note）；既有設定、分析結果及人工標註保留。")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            error(self.root, exc)

    def select_all(self, value):
        for variable in self.selected.values():
            variable.set(value)
        self.refresh_table()

    def toggle_selection(self, event):
        iid = self.table.identify_row(event.y)
        if iid and self.table.identify_column(event.x) == "#1":
            self.selected[iid].set(not self.selected[iid].get())
            self.refresh_table()

    def show_item_info(self, _=None):
        item = self.focused()
        if item:
            self.item_info.set(scenario_text(item) + "\n" + item["path"] + ("\n" + item["error"] if item.get("error") else ""))

    def edit_focused(self):
        item = self.focused()
        if item:
            self.open_editor(item)
        else:
            self.message.set("請先選取一支影片。")

    def open_editor(self, item, review=False, number=None):
        if self.editor and self.editor.item["id"] == item["id"] and self.editor.review == review:
            self.editor.root.lift()
            if number:
                self.editor.read_frame(number - 1)
                if review:
                    self.editor.zoom_review(number)
            return
        if not self.close_editor():
            return
        if review and not item.get("latest_run"):
            return
        try:
            self.editor = VideoEditor(self, item, review, number)
        except (OSError, ValueError, KeyError, cv2.error) as exc:
            error(self.root, exc)

    def duplicate(self):
        if not self.idle_required() or not self.close_editor():
            return
        item = self.focused()
        if item:
            new = copy.deepcopy(item)
            new.update(id=core.uid(), segment=item.get("segment", "") + " (副本)", status="pending", error="", runs=[], manual_events={})
            new.pop("latest_run", None)
            self.project["items"].append(new)
            self.changed()

    def remove(self):
        if not self.idle_required() or not self.close_editor():
            return
        chosen = {i["id"] for i in self.items()}
        if chosen:
            self.project["items"] = [i for i in self.project["items"] if i["id"] not in chosen]
            self.changed()
            self.message.set("已從專案移除勾選項目，原始影片與已保存分析檔案保留。")

    def relink(self):
        if not self.idle_required() or not self.close_editor():
            return
        item = self.focused()
        if not item:
            return
        path = filedialog.askopenfilename(parent=self.root, filetypes=VIDEO_TYPES)
        if path:
            try:
                meta = core.probe_video(path)
                item.update(path=str(Path(path).resolve()), name=Path(path).name, metadata=meta, error="")
                if item["status"] == "failed" and not item.get("latest_run"):
                    item["status"] = "pending"
                self.changed()
            except (OSError, ValueError) as exc:
                error(self.root, exc)

    def copy_settings(self):
        if not self.idle_required() or not self.close_editor():
            return
        source = self.focused()
        if not source:
            self.message.set("請點選來源影片，再勾選要套用的目標影片。")
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("套用設定至勾選影片")
        ttk.Label(dialog, text="來源：" + source["name"], padding=12).pack(anchor="w")
        groups = {"HSV 與最小面積": ("lower", "upper", "min_area"),
                  "穩定條件": ("window_n", "stable_on_m", "stable_off_count", "stable_confirm_frames"),
                  "分析起訖百分比": ("start_percent", "end_percent"), "ROI（限同解析度）": ("roi",)}
        variables = {label: tk.BooleanVar(value=True) for label in groups}
        for label, variable in variables.items():
            ttk.Checkbutton(dialog, text=label, variable=variable).pack(anchor="w", padx=12, pady=3)
        def apply():
            skipped = applied = 0
            for target in self.items():
                if target["id"] == source["id"]:
                    continue
                for group, keys in groups.items():
                    if not variables[group].get():
                        continue
                    for key in keys:
                        if key == "roi" and source["settings"]["roi"] is not None:
                            a, b = source.get("metadata", {}), target.get("metadata", {})
                            if not a or not b or (a["width"], a["height"]) != (b["width"], b["height"]):
                                skipped += 1
                                continue
                        target["settings"][key] = copy.deepcopy(source["settings"][key])
                applied += 1
            self.changed()
            self.message.set(f"已套用至 {applied} 個項目；{skipped} 個 ROI 因解析度不同／未知而保留原值，請個別確認。")
            dialog.destroy()
        ttk.Button(dialog, text="套用", command=apply).pack(pady=12)

    def copy_previous(self):
        if not self.idle_required() or not self.close_editor():
            return
        target = self.focused()
        if not target:
            self.message.set("請先選取要修改的影片。")
            return
        index = self.project["items"].index(target)
        if index == 0:
            self.message.set("第一支影片沒有上一支設定可複製。")
            return
        source = self.project["items"][index - 1]
        settings = copy.deepcopy(source["settings"])
        a, b = source.get("metadata", {}), target.get("metadata", {})
        skipped = settings["roi"] is not None and (not a or not b or (a["width"], a["height"]) != (b["width"], b["height"]))
        if skipped:
            settings["roi"] = target["settings"]["roi"]
        target["settings"] = settings
        self.changed()
        self.message.set("已複製上一支設定。" + ("解析度不同／未知，ROI 保留原值，請重新確認。" if skipped else ""))

    def start_batch(self, all_items):
        if not self.idle_required() or not self.close_editor():
            return
        chosen = self.items(all_items)
        if not chosen:
            self.message.set("請先匯入並勾選影片。")
            return
        if not self.save():
            return
        self.busy = True
        self.cancel.clear()
        self.total_progress.set(0)
        self.jobs.delete(*self.jobs.get_children())
        for item in chosen:
            item.update(status="queued", error="")
            self.jobs.insert("", "end", iid=item["id"], values=(item["name"], "等待中", "", ""))
        self.changed()
        self.tabs.select(self.progress_tab)
        snapshots = copy.deepcopy(chosen)
        runs_dir = self.project_path.with_suffix(".assets") / "runs"
        def work():
            for index, item in enumerate(snapshots):
                if self.cancel.is_set():
                    self.events.put(("result", item["id"], None, "cancelled", "批次已取消", index + 1, len(snapshots)))
                    continue
                self.events.put(("started", item["id"]))
                try:
                    run = core.analyze_item(item, runs_dir, self.cancel,
                        lambda done, total, ident=item["id"], pos=index: self.events.put(("progress", ident, done, total, pos, len(snapshots))))
                    self.events.put(("result", item["id"], run, "completed", "", index + 1, len(snapshots)))
                except core.Cancelled as exc:
                    self.events.put(("result", item["id"], None, "cancelled", str(exc), index + 1, len(snapshots)))
                except Exception as exc:
                    self.events.put(("result", item["id"], None, "failed", str(exc), index + 1, len(snapshots)))
            self.events.put(("finished",))
        threading.Thread(target=work, daemon=True, name="batch-analysis").start()

    def cancel_batch(self):
        if self.busy:
            self.cancel.set()
            self.message.set("已要求取消分析，將停止目前與待執行項目；匯出作業會完成目前批次。")

    def export(self, all_items):
        if not self.idle_required() or not self.close_editor():
            return
        chosen = self.items(all_items)
        if not chosen:
            self.message.set("沒有選取項目。")
            return
        parent = filedialog.askdirectory(parent=self.root, title="選擇批次匯出目的地")
        if not parent:
            return
        snapshots = copy.deepcopy(chosen)
        self.busy = True
        self.message.set("正在匯出 CSV、設定與事件截圖…")
        def work():
            try:
                result = core.export_batch(snapshots, parent,
                    lambda done, total: self.events.put(("export_progress", done, total)))
                self.events.put(("exported", *result))
            except Exception as exc:
                self.events.put(("export_error", str(exc)))
        threading.Thread(target=work, daemon=True, name="batch-export").start()

    def poll(self):
        try:
            for _ in range(100):
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "started":
                    item = next(i for i in self.project["items"] if i["id"] == event[1])
                    item["status"] = "running"
                    self.jobs.set(item["id"], "state", "執行中")
                elif kind == "progress":
                    _, ident, done, total, index, count = event
                    self.jobs.set(ident, "progress", f"{done} / {total} ({done / total:.0%})")
                    self.total_progress.set(100 * (index + done / total) / count)
                    self.message.set(f"批次 {index + 1}/{count} · frame {done}/{total}")
                elif kind == "result":
                    _, ident, run, status, reason, done, count = event
                    item = next(i for i in self.project["items"] if i["id"] == ident)
                    item.update(status=status, error=reason)
                    if run:
                        item.update(latest_run=run, metadata=run["metadata"])
                        item["runs"].append(run["directory"])
                    self.jobs.set(ident, "state", STATUS[status])
                    self.jobs.set(ident, "error", reason or ("重用 Raw 重算完成" if run and run.get("recomputed_from") else ""))
                    self.total_progress.set(100 * done / count)
                    self.changed()
                elif kind == "finished":
                    self.busy = False
                    self.refresh()
                    counts = {key: sum(i["status"] == key for i in self.project["items"]) for key in ("completed", "failed", "cancelled")}
                    self.message.set(f"批次已結束。專案目前：完成 {counts['completed']}、失敗 {counts['failed']}、取消 {counts['cancelled']}。可到結果總覽複核。")
                elif kind == "export_progress":
                    self.message.set(f"匯出 {event[1]}/{event[2]}…")
                elif kind == "exported":
                    self.busy = False
                    _, path, summaries = event
                    good = sum(s["export_status"] in ("completed", "manual_only") for s in summaries)
                    index_path = Path(path) / "index.html"
                    index_error = Path(path) / "index_generation_error.txt"
                    if index_path.is_file():
                        index_note = f"\n離線結果索引：{index_path}"
                    elif index_error.is_file():
                        index_note = f"\n結果索引產生失敗（既有匯出已保留）：{index_error}\n{index_error.read_text(encoding='utf-8', errors='replace')}"
                    else:
                        index_note = "\n結果索引未產生；請查看匯出資料夾。"
                    self.message.set(f"匯出完成 {good}/{len(summaries)}；詳見 batch_summary.csv：{path}" + index_note)
                    messagebox.showinfo("批次匯出", f"完整匯出：{good}/{len(summaries)}\n\n{path}{index_note}\n\n缺項／失敗請查看 batch_summary.csv 與 manifest。", parent=self.root)
                elif kind == "export_error":
                    self.busy = False
                    error(self.root, event[1])
        except queue.Empty:
            pass
        except Exception as exc:
            self.dirty = True
            self.message.set(f"保存／介面更新失敗，請儲存專案重試：{exc}")
        self.poll_id = self.root.after(100, self.poll)

    def on_destroy(self, event):
        if event.widget is self.root:
            self.root.after_cancel(self.poll_id)

    def refresh_table(self):
        focus = self.table.selection()
        current = set(self.table.get_children())
        desired = {item["id"] for item in self.project["items"]}
        for ident in current - desired:
            self.table.delete(ident)
        for item in self.project["items"]:
            ident = item["id"]
            if ident not in self.selected:
                self.selected[ident] = tk.BooleanVar(value=True)
            run, s = item.get("latest_run"), item["settings"]
            outcome = OUTCOME.get(run["summary"]["detection_outcome"], "") if run else "—"
            if core.stale_reason(item):
                outcome += " · 結果過期"
            segment = segment_text(item)
            values = ("☑" if self.selected[ident].get() else "☐", item["name"] + (" · " + segment if segment else ""),
                      item.get("scenario_id", ""), item.get("phase", ""), item.get("note", ""),
                      item["session"], item["camera"], f'{s["start_percent"]:.1f}–{s["end_percent"]:.1f}', STATUS[item["status"]], outcome)
            if ident in current:
                self.table.item(ident, values=values)
            else:
                self.table.insert("", "end", iid=ident, values=values)
        if focus and focus[0] in desired:
            self.table.selection_set(focus)

    def refresh(self):
        self.project_label.configure(text=(self.project_path.name if self.project_path else "尚未儲存") + (" *" if self.dirty else ""))
        self.refresh_table()
        self.refresh_results()

    def refresh_results(self):
        fraction = self.result_canvas.yview()[0]
        for child in self.result_body.winfo_children():
            child.destroy()
        self.plots = {}
        displayed = 0
        for item in self.project["items"]:
            run = item.get("latest_run")
            stale = core.stale_reason(item)
            outcome = OUTCOME.get(run["summary"]["detection_outcome"], "") if run else ""
            selected_filter = self.filter.get()
            if selected_filter != "全部" and not (selected_filter == outcome or
                selected_filter == STATUS[item["status"]] or selected_filter == "結果過期" and stale):
                continue
            displayed += 1
            row = ttk.Frame(self.result_body, padding=8)
            row.pack(fill="x", pady=(0, 5))
            row.columnconfigure(1, weight=1)
            left = ttk.Frame(row, width=320)
            left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
            title = ttk.Frame(left)
            title.pack(fill="x")
            ttk.Checkbutton(title, variable=self.selected[item["id"]], command=self.refresh_table).pack(side="left", anchor="n")
            ttk.Label(title, text=item["name"], wraplength=285, font=("Segoe UI", 10, "bold")).pack(side="left", anchor="w")
            ttk.Label(left, text=scenario_text(item), wraplength=310).pack(anchor="w")
            ttk.Label(left, text=f'{item["session"] or "—"} / {item["camera"] or "—"}  {segment_text(item)}', wraplength=310).pack(anchor="w")
            ttk.Label(left, text=f'{STATUS[item["status"]]} · {outcome}' + (" · 過期：" + stale if stale else ""),
                      foreground="#9d5814" if stale else "#365966", wraplength=310).pack(anchor="w", pady=3)
            if run:
                s = run["summary"]
                for title, prefix in AUTO_EVENTS:
                    n = s[prefix + "_frame"]
                    t = s[prefix + "_timestamp"]
                    text = f'{title}: F{n} · {t:.3f}s\n{title} bbox: {bbox_text(s, prefix)}' if n else f"{title}: —"
                    ttk.Label(left, text=text).pack(anchor="w")
                ttk.Label(left, text=f'分析 F{run["analysis_start_frame"]}–{run["analysis_end_frame"]}' +
                          (" · 分析起點已檢出" if s["detected_at_analysis_start"] else "")).pack(anchor="w", pady=2)
                ttk.Label(left, text=f'Raw {s["raw_detection_rate"]:.1%} · Stable {s["stable_detection_coverage"]:.1%} · '
                          f'最長漏檢 {s["longest_consecutive_miss"]} 幀', wraplength=310).pack(anchor="w")
                if item["status"] != "completed":
                    ttk.Label(left, text="下方顯示前次完成結果 · " + run["run_id"][:8], foreground="#9d5814").pack(anchor="w")
            if item.get("error"):
                ttk.Label(left, text=item["error"], wraplength=310, foreground="#ae3535").pack(anchor="w")
            for event, label in core.ALL_MANUAL_EVENTS.items():
                annotation = core.resolved_manual_events(item).get(event)
                if annotation:
                    reason = core.manual_event_reason(item, annotation)
                    text = f'{label}: F{annotation["frame"]} · {annotation["timestamp"]:.3f}s'
                    if reason:
                        text += " · " + reason
                    elif run and not run["analysis_start_frame"] <= annotation["frame"] <= run["analysis_end_frame"]:
                        text += " · 分析範圍外"
                    ttk.Label(left, text=text, foreground="#8849a4", wraplength=310).pack(anchor="w")
            buttons = ttk.Frame(left)
            buttons.pack(fill="x")
            self.button(buttons, "設定", lambda i=item: self.open_editor(i))
            manual = item.get("manual_events", {}).get("manual_first_detection")
            self.button(buttons, "人工標註", lambda i=item, a=manual: self.open_editor(i, False,
                         a["frame"] if a and not core.manual_event_reason(i, a) else None))
            if run:
                self.button(buttons, "預覽", lambda i=item: self.open_editor(i, True, i["latest_run"]["analysis_start_frame"]))
                event_buttons = ttk.Frame(left)
                event_buttons.pack(fill="x")
                for label, prefix in AUTO_EVENTS:
                    n = run["summary"][prefix + "_frame"]
                    button = self.button(event_buttons, label, lambda i=item, p=prefix: self.event_jump(i, p))
                    if n is None:
                        button.configure(state="disabled")
            right = ttk.Frame(row)
            right.grid(row=0, column=1, sticky="nsew")
            plot = DetectionPlot(right, item, lambda n, i=item: self.open_editor(i, True, n))
            plot.pack(fill="both", expand=True)
            self.plots[item["id"]] = plot
            if run:
                zoom = ttk.Frame(right)
                zoom.pack(fill="x")
                self.button(zoom, "全分析區段", plot.window)
                self.button(zoom, "自訂範圍…", lambda p=plot: self.custom_range(p))
                ttk.Label(zoom, text="點圖表跳幀 · 移動游標查看判定 · 三條序列 Y=0–1").pack(side="right", padx=5)
        if not displayed:
            ttk.Label(self.result_body, text="尚無符合條件的影片。請先匯入影片並執行分析。", padding=35).pack()
        self.result_canvas.update_idletasks()
        self.result_canvas.yview_moveto(fraction)
        self.root.after_idle(self.visible_plots)

    def event_jump(self, item, prefix):
        try:
            seconds = float(self.event_seconds.get())
            if not math.isfinite(seconds) or seconds <= 0:
                raise ValueError("事件前後秒數必須大於 0")
            run = item["latest_run"]
            n = run["summary"][prefix + "_frame"]
            radius = max(1, round(seconds * run["metadata"]["fps"]))
            self.plots[item["id"]].window(n - radius, n + radius)
            self.open_editor(item, True, n)
        except ValueError as exc:
            error(self.root, exc)

    def custom_range(self, plot):
        dialog = tk.Toplevel(self.root)
        dialog.title("圖表原始 frame 範圍")
        low, high = tk.StringVar(value=str(plot.low)), tk.StringVar(value=str(plot.high))
        for title, variable in (("開始 frame", low), ("結束 frame", high)):
            ttk.Label(dialog, text=title).pack(padx=15, pady=3)
            ttk.Entry(dialog, textvariable=variable).pack(padx=15)
        def apply():
            try:
                a, b = int(low.get()), int(high.get())
                run = plot.item["latest_run"]
                if not run["analysis_start_frame"] <= a <= b <= run["analysis_end_frame"]:
                    raise ValueError("範圍必須位於分析區段內，且開始 ≤ 結束")
                plot.window(a, b)
                dialog.destroy()
            except ValueError as exc:
                error(dialog, exc)
        ttk.Button(dialog, text="套用", command=apply).pack(pady=12)

    def sync_cursor(self, ident, number):
        if ident in self.plots:
            self.plots[ident].set_cursor(number)

    def resize_results(self, event):
        self.result_canvas.itemconfigure(self.result_window, width=event.width)
        self.visible_plots()

    def scroll_results(self, *args):
        self.result_canvas.yview(*args)
        self.visible_plots()

    def wheel(self, event):
        if event.widget.winfo_toplevel() != self.root or self.tabs.select() != str(self.results_tab):
            return
        self.result_canvas.yview_scroll(-int(event.delta / 120), "units")
        self.visible_plots()

    def visible_plots(self):
        visible = self.tabs.select() == str(self.results_tab)
        top = self.result_canvas.winfo_rooty()
        bottom = top + self.result_canvas.winfo_height()
        for plot in self.plots.values():
            y = plot.winfo_rooty()
            plot.activate(visible and y + plot.winfo_height() >= top and y <= bottom)

    def close(self):
        if self.busy:
            self.cancel.set()
            self.message.set("已要求取消分析；請等工作結束後再關閉，以保留已完成結果。")
            return
        if self.can_replace():
            self.root.destroy()


def main():
    root = tk.Tk()
    BatchApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
