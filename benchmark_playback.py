import json,time,tkinter as tk
import sys
from pathlib import Path
import batch_core as core
from batch_ui import BatchApp
base=Path(r'C:\Workplace\segmet_video_detect_tool_data\debug_data')
item=core.new_item(next(base.glob('*.mp4')))
item['settings']=core.read_json(next(base.rglob('settings.json')))['settings']
root=tk.Tk(); app=BatchApp(root); app.project['items']=[item]
app.open_editor(item,number=1550); root.update(); e=app.editor
samples=[]; original=e.read_frame

def timed(index):
 start=time.perf_counter(); original(index); samples.append(time.perf_counter()-start)
 if index>=1609: e.playing=False

e.read_frame=timed
start=time.perf_counter(); e.toggle_play()
while e.playing:
 root.update(); time.sleep(.001)
elapsed=time.perf_counter()-start
result=dict(frames=len(samples),elapsed_s=elapsed,fps=len(samples)/elapsed,mean_read_render_ms=sum(samples)/len(samples)*1000)
out=Path(r'C:\Workplace\segmet_video_detect_tool_data\playback_verification');out.mkdir(exist_ok=True)
(out/(sys.argv[1] if len(sys.argv)>1 else 'after.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
print(result,flush=True)
e.close();root.destroy()
