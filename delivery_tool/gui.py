from __future__ import annotations

import copy
import json
import queue
import threading
import webbrowser
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, W, X, Y, BooleanVar, StringVar, Tk, filedialog, messagebox, ttk

from .builder import build
from .config import DEFAULT_CONFIG, load_config, save_config
from .dataset import validate_config


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
        self._entry_row(form, "test_dates", "測試日期（逗號分隔，如 2026-09-18）")
        self._entry_row(form, "batch_id", "批次識別")
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
        self.values["include_videos"] = BooleanVar(value=False)
        ttk.Checkbutton(report, text="將唯一配對影片複製到交付包", variable=self.values["include_videos"]).pack(anchor=W)
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
        columns = ("level", "code", "key", "message")
        self.issue_tree = ttk.Treeview(issue_frame, columns=columns, show="headings", height=10)
        for col, title, width in (("level", "級別", 70), ("code", "類型", 180), ("key", "事件鍵", 240), ("message", "說明", 520)):
            self.issue_tree.heading(col, text=title); self.issue_tree.column(col, width=width, anchor="w")
        self.issue_tree.pack(side=LEFT, fill=BOTH, expand=True)
        sb = ttk.Scrollbar(issue_frame, orient="vertical", command=self.issue_tree.yview); sb.pack(side=RIGHT, fill=Y)
        self.issue_tree.configure(yscrollcommand=sb.set)

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
            return
        else:
            chosen = filedialog.askopenfilename(title="選擇檔案", filetypes=(("CSV／字型／圖片", "*.csv *.ttf *.ttc *.png *.jpg"), ("所有檔案", "*.*")))
        if chosen:
            variable.set(chosen)

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
            for key in ("attachment_index", "image_root", "mask_root", "attachment_root", "video_root", "output_dir"):
                self.values[key].set(str(config.get(key) or ""))
            self.values["test_dates"].set(",".join(config.get("test_dates", [])))
            self.values["batch_id"].set(str(config.get("batch_id", "")))
            report = config.get("report", {})
            for key in ("pdf_layout", "report_name", "customer_project", "version", "scope", "logo", "font_path"):
                self.values[key].set(str(report.get(key, "overview" if key == "pdf_layout" else "")))
            for key in ("cover", "summary_table", "missing_appendix"):
                self.values[key].set(bool(report.get(key, DEFAULT_CONFIG["report"][key])))
            self.values["notes"].delete("1.0", END); self.values["notes"].insert("1.0", report.get("notes", ""))
            self.values["include_videos"].set(bool(config.get("include_videos", False)))
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
        config["report"].update({key: self.values[key].get().strip() for key in
                                 ("pdf_layout", "report_name", "customer_project", "version", "scope", "logo", "font_path")})
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
        self.progress_text.set("")

    def _open_preview(self):
        if self.last_result:
            webbrowser.open(Path(self.last_result["index_html"]).resolve().as_uri())
