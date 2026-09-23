"""Tk-side progress feedback. Called only by readers owned by the UI thread."""


def show_video_progress(root, variable, phase, completed, target, elapsed):
    if target is None:
        detail = f"已讀取 {completed:,} 幀（總幀數確認中）"
    else:
        detail = f"{completed:,} / {target:,} 幀 · {min(100, completed / target * 100):.0f}%"
    variable.set(f"處理中：{phase} · {detail} · 已耗時 {elapsed:.1f} 秒，請稍候")
    # Paint status changes without dispatching user commands that could re-enter
    # the decoder or close its window halfway through a synchronous read.
    root.update_idletasks()
