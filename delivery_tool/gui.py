from __future__ import annotations

import copy
import json
import queue
import threading
import webbrowser
from datetime import date
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, W, X, Y, BooleanVar, StringVar, Tk, filedialog, messagebox, ttk

from PIL import Image, ImageTk

from .builder import build
from .config import DEFAULT_CONFIG, comparison_overlay_settings, load_config, save_config
from .dataset import validate_config
from .video_naming import render_video_filename_stem


class ZoomableImage:
    """Small canvas image viewer with fit, 100%, wheel zoom, and drag-to-pan."""
    def __init__(self, parent, image, width=600, height=390):
        self.image = image.convert("RGB") if isinstance(image, Image.Image) else Image.fromarray(image).convert("RGB")
        self.fit_mode = True
        self.scale = 1.0
        self.photo = None
        self.canvas = __import__("tkinter").Canvas(parent, width=width, height=height,
                                                    background="#15242b", highlightthickness=0)
        self.canvas.pack(fill=BOTH, expand=True)
        self.canvas.bind("<Configure>", self._redraw)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<ButtonPress-1>", lambda event: self.canvas.scan_mark(event.x, event.y))
        self.canvas.bind("<B1-Motion>", lambda event: self.canvas.scan_dragto(event.x, event.y, gain=1))
        actions = ttk.Frame(parent); actions.pack(fill=X, pady=(3, 0))
        ttk.Button(actions, text="適合視窗", command=self.fit).pack(side=LEFT)
        ttk.Button(actions, text="100% 原尺寸", command=self.original_size).pack(side=LEFT, padx=4)
        ttk.Button(actions, text="縮小", command=lambda: self.zoom(0.8)).pack(side=LEFT)
        ttk.Button(actions, text="放大", command=lambda: self.zoom(1.25)).pack(side=LEFT, padx=4)

    def set_image(self, image):
        self.image = image.convert("RGB") if isinstance(image, Image.Image) else Image.fromarray(image).convert("RGB")
        self._redraw()

    def fit(self):
        self.fit_mode = True
        self._redraw()

    def original_size(self):
        self.fit_mode = False
        self.scale = 1.0
        self._redraw()

    def zoom(self, factor):
        if self.fit_mode:
            self.scale = self._fit_scale()
            self.fit_mode = False
        self.scale = min(8.0, max(0.1, self.scale * factor))
        self._redraw()

    def _fit_scale(self):
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        return min(width / self.image.width, height / self.image.height)

    def _wheel(self, event):
        self.zoom(1.2 if event.delta > 0 else 1 / 1.2)

    def _redraw(self, _event=None):
        if not self.canvas.winfo_exists():
            return
        if self.fit_mode:
            self.scale = self._fit_scale()
        width = max(1, round(self.image.width * self.scale))
        height = max(1, round(self.image.height * self.scale))
        resized = self.image.resize((width, height), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(resized)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, max(width, self.canvas.winfo_width()),
                                             max(height, self.canvas.winfo_height())))


def launch(config_path: str | None = None):
    root = Tk()
    DeliveryApp(root, config_path)
    root.mainloop()


class DeliveryApp:
    def __init__(self, root: Tk, config_path: str | None = None):
        self.root = root
        root.title("Sea Trial Result Builder")
        root.geometry("1080x780")
        self.values = {}
        self.events = queue.Queue()
        self.cancel_event = threading.Event()
        self.busy = False
        self.last_result = None
        self.validation_result = None
        self.event_records = {}
        self.project_assets_roots = {}
        self.comparison_overlay = comparison_overlay_settings()
        self.config_dirty = False
        self.config_file = StringVar(value=config_path or "")
        self.status = StringVar(value="選擇或建立設定檔，載入輸入資料後執行檢查。")
        self.progress_text = StringVar(value="")
        self.progress_value = StringVar(value="")
        self._build_ui()
        if config_path:
            self._load(config_path)
        self.root.after(120, self._poll)

    def _build_ui(self):
        top = ttk.Frame(self.root, padding=10); top.pack(fill=X)
        ttk.Label(top, text="設定檔").pack(side=LEFT)
        ttk.Entry(top, textvariable=self.config_file).pack(side=LEFT, fill=X, expand=True, padx=7)
        ttk.Button(top, text="載入", command=self._choose_config).pack(side=LEFT, padx=3)
        ttk.Button(top, text="儲存設定", command=self._save_config).pack(side=LEFT, padx=3)

        outer = ttk.Frame(self.root, padding=(10, 0, 10, 4)); outer.pack(fill=BOTH, expand=True)
        canvas = __import__("tkinter").Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        form = ttk.Frame(canvas, padding=5)
        form.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=form, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=LEFT, fill=BOTH, expand=True); scrollbar.pack(side=RIGHT, fill=Y)

        files = [
            ("projects", "分析專案 JSON（可多選）", "files"),
            ("pairings", "Pairing JSON（可多選）", "files"),
            ("evaluation_csvs", "每日評估 CSV（可多選）", "files"),
            ("attachment_index", "附件索引 CSV", "file"),
            ("image_root", "標註原圖根目錄", "dir"),
            ("mask_root", "Mask 根目錄", "dir"),
            ("attachment_root", "附件索引相對路徑根目錄", "dir"),
            ("video_root", "影片根目錄（選用）", "dir"),
            ("output_dir", "交付輸出目錄", "dir"),
        ]
        for key, label, kind in files:
            self._path_row(form, key, label, kind)
        assets = ttk.LabelFrame(form, text="逐幀圖表：各 Project ID 的 .assets 根目錄", padding=6)
        assets.pack(fill=X, pady=4)
        self.asset_tree = ttk.Treeview(assets, columns=("project_id", "project", "root"), show="headings", height=4)
        for column, title, width in (("project_id", "Project ID", 250), ("project", "專案", 210), ("root", ".assets 根目錄（空白時依序自動尋找）", 470)):
            self.asset_tree.heading(column, text=title); self.asset_tree.column(column, width=width, anchor="w")
        self.asset_tree.pack(fill=X, expand=True)
        self.values["projects"].trace_add("write", lambda *_: self._refresh_project_assets_rows())
        asset_actions = ttk.Frame(assets); asset_actions.pack(fill=X, pady=(5, 0))
        ttk.Button(asset_actions, text="設定所選 Project 根目錄…", command=self._choose_project_assets_root).pack(side=LEFT)
        ttk.Button(asset_actions, text="清除所選根目錄", command=self._clear_project_assets_root).pack(side=LEFT, padx=7)
        self._entry_row(form, "test_dates", "測試日期（逗號分隔，如 2026-09-18）")
        self._entry_row(form, "batch_id", "批次識別")
        video_settings = ttk.LabelFrame(form, text="影片交付設定", padding=8); video_settings.pack(fill=X, pady=4)
        self.values["include_videos"] = BooleanVar(value=False)
        ttk.Checkbutton(video_settings, text="將唯一配對影片複製到交付包",
                        variable=self.values["include_videos"]).pack(anchor=W)
        template_row = ttk.Frame(video_settings); template_row.pack(fill=X, pady=(5, 2))
        ttk.Label(template_row, text="影片檔名範本", width=26).pack(side=LEFT)
        self.values["video_filename_template"] = StringVar(value=DEFAULT_CONFIG["video_filename_template"])
        ttk.Entry(template_row, textvariable=self.values["video_filename_template"]).pack(side=LEFT, fill=X, expand=True)
        preview_row = ttk.Frame(video_settings); preview_row.pack(fill=X, pady=(3, 0))
        ttk.Label(preview_row, text="即時範例", width=26).pack(side=LEFT)
        self.video_filename_preview = StringVar(value="")
        ttk.Label(preview_row, textvariable=self.video_filename_preview).pack(side=LEFT)
        ttk.Label(video_settings, text="欄位：{camera}、{test}、{run}、{date:日期格式}；副檔名沿用來源影片。",
                  foreground="#52636a").pack(anchor=W, pady=(3, 0))
        self.values["video_filename_template"].trace_add("write", lambda *_: self._refresh_video_filename_preview())
        self._refresh_video_filename_preview()
        ttk.Separator(form).pack(fill=X, pady=9)
        report = ttk.LabelFrame(form, text="報告設定", padding=8); report.pack(fill=X, pady=4)
        row = ttk.Frame(report); row.pack(fill=X, pady=2)
        ttk.Label(row, text="PDF 版型", width=26).pack(side=LEFT)
        self.values["pdf_layout"] = StringVar(value="overview")
        ttk.Combobox(row, textvariable=self.values["pdf_layout"], state="readonly",
                     values=("overview", "run", "both"), width=22).pack(side=LEFT)
        self._report_row(report, "report_name", "報告名稱")
        self._report_row(report, "customer_project", "客戶／專案名稱")
        self._report_row(report, "version", "版本")
        self._report_row(report, "scope", "納入範圍")
        self._path_row(report, "logo", "Logo 圖片（選用）", "file", report=True)
        self._path_row(report, "font_path", "中文字型路徑（選用）", "file", report=True)
        overlay_summary = ttk.Frame(report); overlay_summary.pack(fill=X, pady=(3, 5))
        self.overlay_summary_text = StringVar(value="")
        ttk.Label(overlay_summary, textvariable=self.overlay_summary_text).pack(side=LEFT, padx=(0, 10))
        self.overlay_swatch_labels = []
        for label in ("重疊", "僅 GT", "僅預測"):
            swatch = __import__("tkinter").Label(overlay_summary, text=label, padx=6, pady=2,
                                                  relief="solid", borderwidth=1)
            swatch.pack(side=LEFT, padx=3)
            self.overlay_swatch_labels.append(swatch)
        self._refresh_overlay_summary()
        self.values["include_frame_charts"] = BooleanVar(value=True)
        ttk.Checkbutton(report, text="在 HTML 加入逐幀分析圖表", variable=self.values["include_frame_charts"]).pack(anchor=W)
        self.values["cover"] = BooleanVar(value=True); self.values["summary_table"] = BooleanVar(value=True)
        self.values["missing_appendix"] = BooleanVar(value=False)
        checks = ttk.Frame(report); checks.pack(fill=X, pady=4)
        ttk.Checkbutton(checks, text="封面", variable=self.values["cover"]).pack(side=LEFT)
        ttk.Checkbutton(checks, text="總表", variable=self.values["summary_table"]).pack(side=LEFT, padx=12)
        ttk.Checkbutton(checks, text="缺漏附錄", variable=self.values["missing_appendix"]).pack(side=LEFT, padx=12)
        ttk.Label(report, text="備註").pack(anchor=W)
        self.values["notes"] = __import__("tkinter").Text(report, height=3, wrap="word")
        self.values["notes"].pack(fill=X)

        actions = ttk.Frame(self.root, padding=10); actions.pack(fill=X)
        self.validate_button = ttk.Button(actions, text="檢查配對與問題", command=self._validate); self.validate_button.pack(side=LEFT)
        self.build_button = ttk.Button(actions, text="產生交付包", command=self._build); self.build_button.pack(side=LEFT, padx=8)
        self.repair_button = ttk.Button(actions, text="修正所選事件關聯", command=self._repair_selected_link); self.repair_button.pack(side=LEFT, padx=4)
        self.cancel_button = ttk.Button(actions, text="取消", command=self._cancel, state="disabled"); self.cancel_button.pack(side=LEFT)
        self.preview_button = ttk.Button(actions, text="開啟 HTML 預覽", command=self._open_preview, state="disabled"); self.preview_button.pack(side=LEFT, padx=8)
        ttk.Progressbar(actions, mode="determinate", maximum=100, variable=self.progress_value).pack(side=LEFT, fill=X, expand=True, padx=10)
        ttk.Label(actions, textvariable=self.progress_text).pack(side=LEFT)
        ttk.Label(self.root, textvariable=self.status, padding=(12, 1)).pack(fill=X)

        issue_frame = ttk.LabelFrame(self.root, text="檢查結果與缺漏", padding=6); issue_frame.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))
        result_actions = ttk.Frame(issue_frame); result_actions.pack(fill=X, pady=(0, 5))
        self.comparison_preview_button = ttk.Button(result_actions, text="預覽 GT／預測比較",
                                                    command=self._preview_comparison, state="disabled")
        self.comparison_preview_button.pack(side=LEFT)
        ttk.Label(result_actions, text="請先在下方選取一筆評估事件。", foreground="#60737c").pack(side=LEFT, padx=9)
        columns = ("level", "code", "key", "message")
        self.issue_tree = ttk.Treeview(issue_frame, columns=columns, show="headings", height=10)
        for col, title, width in (("level", "級別", 70), ("code", "類型", 180), ("key", "事件鍵", 240), ("message", "說明", 520)):
            self.issue_tree.heading(col, text=title); self.issue_tree.column(col, width=width, anchor="w")
        self.issue_tree.pack(side=LEFT, fill=BOTH, expand=True)
        sb = ttk.Scrollbar(issue_frame, orient="vertical", command=self.issue_tree.yview); sb.pack(side=RIGHT, fill=Y)
        self.issue_tree.configure(yscrollcommand=sb.set)

    def _refresh_overlay_summary(self):
        settings = comparison_overlay_settings(self.comparison_overlay)
        suffix = "（尚未儲存）" if self.config_dirty else ""
        self.overlay_summary_text.set(f"疊圖不透明度：{round(settings['opacity'] * 100)}%{suffix}")
        for label, key in zip(self.overlay_swatch_labels, ("overlap", "gt_only", "prediction_only")):
            label.configure(background=settings["colors"][key], foreground="#101820")

    def _refresh_video_filename_preview(self):
        template = self.values["video_filename_template"].get()
        try:
            stem = render_video_filename_stem(template, {
                "camera": "1", "test": "1", "run": "A", "date": date(2026, 9, 22),
            })
            self.video_filename_preview.set(f"{stem}.mp4")
        except ValueError as exc:
            self.video_filename_preview.set(f"範本錯誤：{exc}")

    def _path_row(self, parent, key, label, kind, report=False):
        row = ttk.Frame(parent); row.pack(fill=X, pady=2)
        ttk.Label(row, text=label, width=28, anchor="w").pack(side=LEFT)
        variable = self.values.setdefault(key, StringVar(value=""))
        ttk.Entry(row, textvariable=variable).pack(side=LEFT, fill=X, expand=True, padx=6)
        ttk.Button(row, text="瀏覽…", command=lambda: self._browse(key, kind, variable, report)).pack(side=LEFT)

    def _entry_row(self, parent, key, label):
        row = ttk.Frame(parent); row.pack(fill=X, pady=2)
        ttk.Label(row, text=label, width=38, anchor="w").pack(side=LEFT)
        self.values[key] = StringVar(value="")
        ttk.Entry(row, textvariable=self.values[key]).pack(side=LEFT, fill=X, expand=True)

    def _report_row(self, parent, key, label):
        row = ttk.Frame(parent); row.pack(fill=X, pady=2)
        ttk.Label(row, text=label, width=26, anchor="w").pack(side=LEFT)
        self.values[key] = StringVar(value="")
        ttk.Entry(row, textvariable=self.values[key]).pack(side=LEFT, fill=X, expand=True)

    def _browse(self, key, kind, variable, report):
        if kind == "dir":
            chosen = filedialog.askdirectory(title="選擇目錄")
        elif kind == "files":
            chosen = filedialog.askopenfilenames(title="選擇檔案", filetypes=(("資料檔", "*.json *.csv"), ("所有檔案", "*.*")))
            if chosen:
                variable.set(";".join(chosen))
                if key == "projects":
                    self._refresh_project_assets_rows()
            return
        else:
            chosen = filedialog.askopenfilename(title="選擇檔案", filetypes=(("CSV／字型／圖片", "*.csv *.ttf *.ttc *.png *.jpg"), ("所有檔案", "*.*")))
        if chosen:
            variable.set(chosen)
            if key == "projects":
                self._refresh_project_assets_rows()

    def _project_paths_from_form(self):
        values = [value.strip() for value in self.values["projects"].get().split(";") if value.strip()]
        base = Path(self.config_file.get()).resolve().parent if self.config_file.get().strip() else Path.cwd()
        for value in values:
            path = Path(value).expanduser()
            yield path if path.is_absolute() else (base / path).resolve()

    def _refresh_project_assets_rows(self):
        for item in self.asset_tree.get_children():
            self.asset_tree.delete(item)
        seen = set()
        for path in self._project_paths_from_form():
            try:
                project = json.loads(path.read_text(encoding="utf-8-sig"))
                project_id = str(project.get("project_id", ""))
                if not project_id or project_id in seen:
                    continue
                seen.add(project_id)
                self.asset_tree.insert("", END, iid=f"project_{len(seen)}", values=(project_id, project.get("name", path.stem),
                                     self.project_assets_roots.get(project_id, "")))
            except Exception:
                continue

    def _choose_project_assets_root(self):
        selection = self.asset_tree.selection()
        if not selection:
            messagebox.showinfo("選擇 Project", "請先在清單選取一個分析專案。")
            return
        values = self.asset_tree.item(selection[0], "values")
        chosen = filedialog.askdirectory(title=f"選擇 {values[1]} 的 .assets 根目錄")
        if chosen:
            self.project_assets_roots[str(values[0])] = chosen
            self._refresh_project_assets_rows()
            self.asset_tree.selection_set(selection[0])

    def _clear_project_assets_root(self):
        selection = self.asset_tree.selection()
        if not selection:
            messagebox.showinfo("選擇 Project", "請先在清單選取一個分析專案。")
            return
        project_id = str(self.asset_tree.item(selection[0], "values")[0])
        self.project_assets_roots.pop(project_id, None)
        self._refresh_project_assets_rows()

    def _choose_config(self):
        chosen = filedialog.askopenfilename(title="載入設定檔", filetypes=(("JSON", "*.json"),))
        if chosen:
            self._load(chosen)

    def _load(self, path):
        try:
            config = load_config(path)
            self.config_file.set(str(Path(path).resolve()))
            for key in ("projects", "pairings", "evaluation_csvs"):
                self.values[key].set(";".join(str(v) for v in config.get(key, [])))
            self.project_assets_roots = {str(key): str(value) for key, value in (config.get("project_assets_roots") or {}).items()}
            self._refresh_project_assets_rows()
            for key in ("attachment_index", "image_root", "mask_root", "attachment_root", "video_root", "output_dir"):
                self.values[key].set(str(config.get(key) or ""))
            self.values["test_dates"].set(",".join(config.get("test_dates", [])))
            self.values["batch_id"].set(str(config.get("batch_id", "")))
            self.values["video_filename_template"].set(str(config.get("video_filename_template", DEFAULT_CONFIG["video_filename_template"])))
            report = config.get("report", {})
            self.comparison_overlay = comparison_overlay_settings(config)
            self.config_dirty = False
            self._refresh_overlay_summary()
            for key in ("pdf_layout", "report_name", "customer_project", "version", "scope", "logo", "font_path"):
                self.values[key].set(str(report.get(key, "overview" if key == "pdf_layout" else "")))
            for key in ("cover", "summary_table", "missing_appendix"):
                self.values[key].set(bool(report.get(key, DEFAULT_CONFIG["report"][key])))
            self.values["notes"].delete("1.0", END); self.values["notes"].insert("1.0", report.get("notes", ""))
            self.values["include_videos"].set(bool(config.get("include_videos", False)))
            self.values["include_frame_charts"].set(bool(config.get("include_frame_charts", True)))
            self.status.set("設定已載入。")
        except Exception as exc:
            messagebox.showerror("載入失敗", str(exc))

    def _current_config(self):
        config = copy.deepcopy(DEFAULT_CONFIG)
        if self.config_file.get().strip() and Path(self.config_file.get()).is_file():
            loaded = load_config(self.config_file.get())
            config.update({k: v for k, v in loaded.items() if not k.startswith("_")})
        for key in ("projects", "pairings", "evaluation_csvs"):
            config[key] = [x.strip() for x in self.values[key].get().split(";") if x.strip()]
        for key in ("attachment_index", "image_root", "mask_root", "attachment_root", "video_root", "output_dir"):
            config[key] = self.values[key].get().strip()
        config["test_dates"] = [x.strip() for x in self.values["test_dates"].get().split(",") if x.strip()]
        config["batch_id"] = self.values["batch_id"].get().strip()
        config["include_videos"] = bool(self.values["include_videos"].get())
        config["video_filename_template"] = self.values["video_filename_template"].get()
        config["include_frame_charts"] = bool(self.values["include_frame_charts"].get())
        config["project_assets_roots"] = dict(self.project_assets_roots)
        config["report"].update({key: self.values[key].get().strip() for key in
                                 ("pdf_layout", "report_name", "customer_project", "version", "scope", "logo", "font_path")})
        config["report"]["comparison_overlay"] = comparison_overlay_settings(self.comparison_overlay)
        config["report"].update({key: bool(self.values[key].get()) for key in ("cover", "summary_table", "missing_appendix")})
        config["report"]["notes"] = self.values["notes"].get("1.0", "end-1c")
        path = self.config_file.get().strip()
        config["_config_path"] = str(Path(path).resolve()) if path else str(Path.cwd() / "delivery_config.json")
        config["_config_dir"] = str(Path(config["_config_path"]).parent)
        return config

    def _save_config(self):
        path = self.config_file.get().strip()
        if not path:
            path = filedialog.asksaveasfilename(title="儲存設定", defaultextension=".json", filetypes=(("JSON", "*.json"),))
            if not path:
                return
            self.config_file.set(path)
        try:
            save_config(path, self._current_config())
            self.config_dirty = False
            self._refresh_overlay_summary()
            self.status.set(f"設定已儲存：{path}")
        except Exception as exc:
            messagebox.showerror("儲存失敗", str(exc))

    def _persist_config(self, config):
        path = self.config_file.get().strip()
        if not path:
            path = filedialog.asksaveasfilename(title="儲存設定檔", defaultextension=".json", filetypes=(("JSON", "*.json"),))
            if not path:
                return False
            self.config_file.set(path)
        save_config(path, config)
        self.config_dirty = False
        self._refresh_overlay_summary()
        self.status.set(f"關聯修正已寫入設定：{path}")
        return True

    def _repair_selected_link(self):
        selection = self.issue_tree.selection()
        if not selection or selection[0] not in self.event_records:
            messagebox.showinfo("選擇事件", "請先在檢查結果中選取一筆評估事件。")
            return
        record = self.event_records[selection[0]]
        project_rows = (self.validation_result or {}).get("project_items", [])
        parsed = record.get("parsed", {})
        camera, phase = record.get("camera", ""), record.get("phase", "")
        candidates = [row for row in project_rows if
                      (not row["item"].get("camera") or row["item"].get("camera") == camera) and
                      (not row["item"].get("phase") or str(row["item"].get("phase")) == str(phase))]
        dialog = __import__("tkinter").Toplevel(self.root)
        dialog.title("選擇分析項目關聯")
        dialog.geometry("820x430"); dialog.transient(self.root); dialog.grab_set()
        ttk.Label(dialog, text=f"{record.get('key')}\n目前狀態：{record.get('analysis_link', {}).get('status', 'unlinked')}  ·  請選擇 Camera／Phase 候選；確認後仍會核對事件種類與 Frame。", padding=10).pack(fill=X)
        tree = ttk.Treeview(dialog, columns=("id", "camera", "test", "phase", "run", "frames"), show="headings")
        for col, title, width in (("id", "Project Item ID", 160), ("camera", "Camera", 100), ("test", "Test／Scenario", 180),
                                  ("phase", "Phase", 80), ("run", "pairing_run_id", 140), ("frames", "專案事件 Frame", 140)):
            tree.heading(col, text=title); tree.column(col, width=width, anchor="w")
        tree.pack(fill=BOTH, expand=True, padx=10, pady=5)
        for index, row in enumerate(candidates):
            item = row["item"]
            latest = item.get("latest_run") or {}
            summary = latest.get("summary") or {}
            frames = f"First {summary.get('first_detection_frame', '—')} / Stable {summary.get('first_sustained_stable_start_frame', '—')}"
            tree.insert("", END, iid=f"candidate_{index}", values=(item.get("id", ""), item.get("camera", ""),
                        item.get("scenario_id", ""), item.get("phase", ""), item.get("pairing_run_id", item.get("segment", "")), frames))
        footer = ttk.Frame(dialog, padding=10); footer.pack(fill=X)

        def apply(clear=False):
            config = self._current_config()
            config.setdefault("event_links", {})
            if clear:
                config["event_links"][record["key"]] = "clear"
            else:
                picked = tree.selection()
                if not picked:
                    messagebox.showinfo("選擇項目", "請選擇一個分析項目候選。", parent=dialog)
                    return
                item_id = str(tree.item(picked[0], "values")[0])
                row = next(x for x in candidates if str(x["item"].get("id")) == item_id)
                config["event_links"][record["key"]] = {"project_item_id": item_id,
                    "pairing_run_id": str(row["item"].get("pairing_run_id", row["item"].get("segment", "")))}
            try:
                if not self._persist_config(config):
                    return
            except Exception as exc:
                messagebox.showerror("儲存失敗", str(exc), parent=dialog); return
            dialog.destroy()
            self._validate()

        ttk.Button(footer, text="使用所選項目", command=apply).pack(side=LEFT)
        ttk.Button(footer, text="清除關聯", command=lambda: apply(True)).pack(side=LEFT, padx=8)
        ttk.Button(footer, text="取消", command=dialog.destroy).pack(side=RIGHT)

    def _start(self, task):
        if self.busy:
            return
        self.busy = True; self.cancel_event.clear(); self.last_result = None
        self.preview_button.configure(state="disabled"); self.cancel_button.configure(state="normal")
        self.comparison_preview_button.configure(state="disabled")
        self.validate_button.configure(state="disabled"); self.build_button.configure(state="disabled")
        self.progress_value.set("0"); self.progress_text.set("")
        config = self._current_config()
        thread = threading.Thread(target=self._work, args=(task, config), daemon=True)
        thread.start()

    def _work(self, task, config):
        try:
            progress = lambda done, total, message: self.events.put(("progress", done, total, message))
            result = validate_config(config, progress=progress, cancel=self.cancel_event) if task == "validate" else build(config, progress=progress, cancel=self.cancel_event)
            self.events.put(("result", task, result))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _validate(self):
        self._clear_issues(); self.status.set("正在檢查輸入、配對、Frame 與附件…"); self._start("validate")

    def _build(self):
        self._clear_issues(); self.status.set("正在產生交付包…"); self._start("build")

    def _cancel(self):
        self.cancel_event.set(); self.status.set("正在取消；目前工作完成安全中斷後會清除未完成輸出。")

    def _clear_issues(self):
        for child in self.issue_tree.get_children(): self.issue_tree.delete(child)

    def _show_issues(self, result):
        self.event_records = {}
        errors = result.get("errors", [])
        warnings = result.get("warnings", [])
        for level, rows in (("錯誤", errors), ("警告", warnings)):
            for issue in rows:
                self.issue_tree.insert("", END, values=(level, issue.get("code", ""), issue.get("key", ""), issue.get("message", "")))
        for idx, record in enumerate((result.get("manifest", {}) or {}).get("records", [])):
            if record.get("collage") or record.get("image_path"):
                iid = f"event_{idx}"
                self.event_records[iid] = record
                self.issue_tree.insert("", END, iid=iid, values=("事件", "評估事件", record.get("key", ""),
                    f"{record.get('date')} · {record.get('camera')} · {record.get('event')} · F{record.get('frame')} · GT {record.get('attachment',{}).get('gt_status')}／Mask {record.get('attachment',{}).get('mask_status')}"))
        self.comparison_preview_button.configure(state="normal" if self.event_records else "disabled")

    def _preview_comparison(self):
        selection = self.issue_tree.selection()
        if not selection or selection[0] not in self.event_records:
            messagebox.showinfo("選擇事件", "請先在檢查結果中選取一筆評估事件。", parent=self.root)
            return
        record = self.event_records[selection[0]]
        attachment = record.get("attachment", {})
        missing = []
        if not record.get("image_path") or record.get("image_status") != "found":
            missing.append("原圖：" + (record.get("image_status") or "未提供"))
        if attachment.get("gt_status") != "valid" or not attachment.get("labelme_path"):
            missing.append("GT：" + str(attachment.get("gt_status") or "missing"))
        if attachment.get("mask_status") != "valid" or not attachment.get("mask_path"):
            missing.append("預測 Mask：" + str(attachment.get("mask_status") or "missing"))
        if missing:
            messagebox.showerror("比較預覽缺少附件", "此事件無法預覽：\n" + "\n".join(missing), parent=self.root)
            return
        try:
            from .collage import load_comparison_layers
            base, regions = load_comparison_layers(record["image_path"], attachment["labelme_path"], attachment["mask_path"])
        except Exception as exc:
            messagebox.showerror("比較預覽失敗", f"讀取事件附件失敗：{exc}", parent=self.root)
            return
        self._show_comparison_dialog(record, base, regions)

    def _show_comparison_dialog(self, record, base, regions):
        from .collage import render_comparison
        from PIL import ImageOps
        from tkinter.colorchooser import askcolor

        dialog = __import__("tkinter").Toplevel(self.root)
        dialog.title("預覽 GT／預測比較")
        dialog.geometry("1400x940")
        dialog.minsize(1120, 800)
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text=(f"{record.get('date', '—')}　Test {record.get('test', '—')}　"
                               f"航次 {record.get('run_letter', '—')}／{record.get('phase', '—')}　"
                               f"{record.get('camera', '—')}　{record.get('event', '—')}　Frame {record.get('frame', '—')}"),
                  padding=(12, 8), font=("TkDefaultFont", 11, "bold")).pack(fill=X)

        start = comparison_overlay_settings(self.comparison_overlay)
        colors = dict(start["colors"])
        color_vars = {key: StringVar(value=colors[key]) for key in colors}
        opacity_var = StringVar(value=str(round(start["opacity"] * 100)))
        opacity_scale_var = __import__("tkinter").IntVar(value=round(start["opacity"] * 100))
        error_var, note_var = StringVar(value=""), StringVar(value="")
        pending = {"after": None}
        previews = {}

        controls = ttk.LabelFrame(dialog, text="比較圖設定", padding=7)
        controls.pack(fill=X, padx=10, pady=(0, 5))
        categories = (("overlap", "GT 與預測重疊"), ("gt_only", "僅 GT，預測未涵蓋"),
                      ("prediction_only", "僅預測，超出 GT"))
        color_swatches = {}
        for row_index, (key, label) in enumerate(categories):
            ttk.Label(controls, text=label, width=29).grid(row=row_index, column=0, sticky="w", pady=2)
            swatch = __import__("tkinter").Label(controls, text="   ", background=color_vars[key].get(),
                                                   relief="solid", borderwidth=1)
            swatch.grid(row=row_index, column=1, sticky="w", padx=(0, 5))
            color_swatches[key] = swatch
            ttk.Entry(controls, textvariable=color_vars[key], width=12).grid(row=row_index, column=2, sticky="w", padx=4)
            ttk.Button(controls, text="選擇顏色…", command=lambda k=key: choose_color(k)).grid(row=row_index, column=3, sticky="w")
        ttk.Label(controls, text="共用疊圖不透明度", width=29).grid(row=3, column=0, sticky="w", pady=3)
        scale = __import__("tkinter").Scale(controls, from_=0, to=100, resolution=1, orient="horizontal",
                                              showvalue=False, variable=opacity_scale_var, length=390)
        scale.grid(row=3, column=1, columnspan=3, sticky="w")
        ttk.Spinbox(controls, from_=0, to=100, increment=1, textvariable=opacity_var, width=6).grid(row=3, column=4, sticky="w", padx=(8, 2))
        ttk.Label(controls, text="%").grid(row=3, column=5, sticky="w")
        ttk.Label(controls, textvariable=error_var, foreground="#a12828").grid(row=4, column=0, columnspan=6, sticky="w")
        ttk.Label(controls, textvariable=note_var, foreground="#765b20").grid(row=5, column=0, columnspan=6, sticky="w")

        pair = ttk.Frame(dialog, padding=(10, 2)); pair.pack(fill=BOTH, expand=True)
        left = ttk.LabelFrame(pair, text="原圖", padding=5); left.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 5))
        right = ttk.LabelFrame(pair, text="目前設定比較圖", padding=5); right.pack(side=LEFT, fill=BOTH, expand=True, padx=(5, 0))
        original_view = ZoomableImage(left, base, width=650, height=330)
        current_view = ZoomableImage(right, base, width=650, height=330)

        compare = ttk.LabelFrame(dialog, text="比較不同比例（點圖即可選用）", padding=(8, 4))
        compare.pack(fill=X, padx=10, pady=(3, 4))
        for column, percent in enumerate((30, 65, 100)):
            frame = ttk.Frame(compare, padding=4); frame.grid(row=0, column=column, sticky="nsew", padx=5)
            compare.columnconfigure(column, weight=1)
            ttk.Label(frame, text=f"{percent}%").pack()
            button = __import__("tkinter").Button(frame, relief="flat", cursor="hand2",
                                                   command=lambda value=percent: opacity_var.set(str(value)))
            button.pack(fill=X)
            previews[percent] = button

        def choose_color(key):
            candidate = color_vars[key].get()
            initial = candidate if len(candidate) == 7 and candidate.startswith("#") else "#FFFFFF"
            picked = askcolor(color=initial, parent=dialog, title=f"{dict(categories)[key]} 顏色")[1]
            if picked:
                color_vars[key].set(picked.upper())

        def current_draft():
            try:
                raw_percent = opacity_var.get().strip()
                if not raw_percent or not raw_percent.isdecimal():
                    raise ValueError("report.comparison_overlay.opacity：請輸入 0–100 的整數百分比")
                percent = int(raw_percent)
                if not 0 <= percent <= 100:
                    raise ValueError("report.comparison_overlay.opacity：數值必須介於 0–100%")
                return comparison_overlay_settings({"comparison_overlay": {
                    "opacity": percent / 100,
                    "colors": {key: variable.get() for key, variable in color_vars.items()},
                }})
            except ValueError as exc:
                error_var.set(str(exc))
                note_var.set("")
                return None

        def refresh_preview():
            pending["after"] = None
            settings = current_draft()
            if settings is None:
                return
            error_var.set("")
            distinct = len(set(settings["colors"].values())) == 3
            if not distinct:
                note_var.set("有分類使用相同顏色，可能不易區分。")
            elif settings["opacity"] == 0:
                note_var.set("0% 不透明度會顯示原圖，無法從顏色區分分類。")
            else:
                note_var.set("")
            current_view.set_image(render_comparison(base, regions, settings))
            for percent, button in previews.items():
                thumb = render_comparison(base, regions, {"opacity": percent / 100, "colors": settings["colors"]})
                thumb = ImageOps.contain(thumb, (380, 150), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(thumb)
                button.configure(image=photo)
                button.image = photo

        def schedule_preview(*_args):
            if pending["after"] is not None:
                try:
                    dialog.after_cancel(pending["after"])
                except Exception:
                    pass
            pending["after"] = dialog.after(100, refresh_preview)

        def sync_scale_to_entry(*_args):
            value = opacity_scale_var.get()
            if opacity_var.get() != str(value):
                opacity_var.set(str(value))

        def sync_entry_to_scale(*_args):
            value = opacity_var.get().strip()
            if value.isdecimal() and 0 <= int(value) <= 100 and opacity_scale_var.get() != int(value):
                opacity_scale_var.set(int(value))
            schedule_preview()

        def update_color_swatch(key):
            candidate = color_vars[key].get()
            try:
                dialog.winfo_rgb(candidate)
                color_swatches[key].configure(background=candidate)
            except Exception:
                pass

        for key, variable in color_vars.items():
            variable.trace_add("write", schedule_preview)
            variable.trace_add("write", lambda *_args, color_key=key: update_color_swatch(color_key))
        opacity_scale_var.trace_add("write", sync_scale_to_entry)
        opacity_var.trace_add("write", sync_entry_to_scale)

        footer = ttk.Frame(dialog, padding=(10, 5)); footer.pack(fill=X)

        def restore_defaults():
            defaults = comparison_overlay_settings()
            for key, variable in color_vars.items():
                variable.set(defaults["colors"][key])
            opacity_var.set("65")

        def apply_settings():
            settings = current_draft()
            if settings is None:
                return
            self.comparison_overlay = settings
            self.config_dirty = True
            self._refresh_overlay_summary()
            dialog.destroy()

        ttk.Button(footer, text="恢復預設", command=restore_defaults).pack(side=LEFT)
        ttk.Button(footer, text="套用", command=apply_settings).pack(side=RIGHT)
        ttk.Button(footer, text="取消", command=dialog.destroy).pack(side=RIGHT, padx=7)
        schedule_preview()

    def _poll(self):
        try:
            while True:
                event = self.events.get_nowait()
                if event[0] == "progress":
                    _, done, total, message = event
                    self.progress_value.set(str(100 * done / max(1, total))); self.progress_text.set(f"{done}/{total}"); self.status.set(message)
                elif event[0] == "result":
                    _, task, result = event
                    self._finish()
                    if task == "validate":
                        self.validation_result = result
                        self._show_issues(result)
                        self.status.set(f"檢查完成：{result['record_count']} 筆事件、{result['counts']['images_found']} 張原圖；錯誤 {len(result['errors'])}，警告 {len(result['warnings'])}。")
                    else:
                        self.last_result = result
                        self._show_issues({"errors": [], "warnings": result["warnings"]})
                        self.preview_button.configure(state="normal")
                        self.status.set(f"交付包完成：{result['delivery_dir']}（{result['record_count']} 筆，{len(result['warnings'])} 項警告）")
                        messagebox.showinfo("完成", f"交付包已建立：\n{result['delivery_dir']}")
                elif event[0] == "error":
                    self._finish()
                    self.status.set("工作未完成；請查看錯誤與輸入設定。")
                    self.issue_tree.insert("", END, values=("錯誤", "工作失敗", "", event[1]))
        except queue.Empty:
            pass
        self.root.after(120, self._poll)

    def _finish(self):
        self.busy = False; self.cancel_button.configure(state="disabled")
        self.validate_button.configure(state="normal"); self.build_button.configure(state="normal")
        self.comparison_preview_button.configure(state="normal" if self.event_records else "disabled")
        self.progress_text.set("")

    def _open_preview(self):
        if self.last_result:
            webbrowser.open(Path(self.last_result["index_html"]).resolve().as_uri())
