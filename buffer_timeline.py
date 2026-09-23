"""Small cache-range strip aligned below a video's navigation slider."""
import tkinter as tk
from tkinter import ttk


class BufferTimeline(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.ranges = ()
        self.total = 1
        self.current = 1
        self.canvas = tk.Canvas(self, height=14, highlightthickness=0)
        self.canvas.pack(fill="x")
        self.caption = tk.StringVar()
        ttk.Label(self, textvariable=self.caption, font=("TkDefaultFont", 8)).pack(anchor="w")
        self.canvas.bind("<Configure>", lambda _event: self._draw())
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Leave>", lambda _event: self._caption())
        self._caption()

    def set_state(self, total, current, ranges):
        state = (max(1, int(total)), int(current), tuple(ranges))
        if state == (self.total, self.current, self.ranges):
            return
        self.total, self.current, self.ranges = state
        self._draw()
        self._caption()

    def _caption(self):
        count = sum(end - start + 1 for start, end in self.ranges)
        spans = "、".join(f"F{a}" if a == b else f"F{a}–F{b}" for a, b in self.ranges[:3])
        if len(self.ranges) > 3:
            spans += f" … 共 {len(self.ranges)} 段"
        self.caption.set(f"綠色：已快取 {count} 幀{('（' + spans + '）') if spans else ''}　藍線：目前位置　灰色：未快取")

    def _bounds(self):
        return 8, max(9, self.canvas.winfo_width() - 8)

    def _draw(self):
        self.canvas.delete("all")
        left, right = self._bounds()
        width = right - left
        self.canvas.create_rectangle(left, 4, right, 11, fill="#d7dce2", outline="", tags="track")
        for start, end in self.ranges:
            x1 = left + (start - 1) / self.total * width
            x2 = min(right, max(x1 + 1, left + end / self.total * width))
            self.canvas.create_rectangle(x1, 4, x2, 11, fill="#329960", outline="", tags="cached")
        x = left + (min(self.total, max(1, self.current)) - 1) / max(1, self.total - 1) * width
        # Keep the cursor above the strip so one-frame cache spans remain visible.
        self.canvas.create_line(x, 0, x, 4, fill="#087cce", width=2, tags="cursor")

    def _hover(self, event):
        left, right = self._bounds()
        number = min(self.total, max(1, int((event.x - left) / (right - left) * self.total) + 1))
        span = next(((a, b) for a, b in self.ranges if a <= number <= b), None)
        detail = f"已快取（F{span[0]}–F{span[1]}）" if span else "未快取，需解碼讀取"
        self.caption.set(f"F{number} · {detail}　|　目前 F{self.current}")
