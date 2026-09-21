"""Self-contained offline HTML index for a batch export directory."""
from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Iterable


def _json_for_script(value) -> str:
    # The payload is placed between <script> tags.  Escaping '<' prevents a
    # filename or note containing </script> from terminating the data block.
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
            .replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def _relative_file(directory: Path, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(directory.resolve()).as_posix()
    except ValueError:
        return ""
    return relative if relative and not relative.startswith("../") else ""


def _link(directory: Path, path: Path, label: str) -> dict[str, str] | None:
    relative = _relative_file(directory, path)
    if not relative or not path.is_file():
        return None
    return {"label": label, "path": relative}


def _run_serial(item: dict) -> str:
    pairing = item.get("pairing_run") or {}
    sequence = pairing.get("sequence")
    if sequence not in (None, ""):
        return str(sequence)
    return str(item.get("session") or "")


def make_index_payload(directory: Path, items: Iterable[dict], summaries: list[dict],
                      manifest: list[dict], frames_by_item: dict[str, list[dict]]) -> dict:
    """Build only JSON-compatible data used by the HTML page."""
    by_item = {str(summary.get("item_id")): summary for summary in summaries}
    events_by_item: dict[str, list[dict]] = {}
    for record in manifest:
        events_by_item.setdefault(str(record.get("item_id", "")), []).append(record)
    payload_items = []
    for item in items:
        item_id = str(item.get("id", ""))
        summary = dict(by_item.get(item_id, {}))
        # The offline page needs the exported summary, not the user's source
        # filesystem path.  Omitting it also guarantees that every actionable
        # link in the page stays inside the moved export package.
        summary.pop("source_path", None)
        run_id = str(summary.get("run_id") or "")
        run_dir = directory / item_id / run_id if run_id else None
        links = []
        if run_dir:
            for name, label in (("frame_analysis.csv", "逐幀 CSV"),
                                ("run_summary.csv", "分析摘要"),
                                ("settings.json", "設定檔")):
                link = _link(directory, run_dir / name, label)
                if link:
                    links.append(link)
        payload_items.append({
            "item_id": item_id,
            "run_id": run_id,
            "run_serial": _run_serial(item) or "",
            "video": item.get("name", ""),
            "scenario_id": item.get("scenario_id", ""),
            "phase": item.get("phase", ""),
            "camera": item.get("camera", ""),
            "segment": item.get("segment", ""),
            "note": item.get("note", ""),
            "summary": summary,
            "frames": frames_by_item.get(item_id, []),
            "events": events_by_item.get(item_id, []),
            "links": links,
            "relative_directory": _relative_file(directory, run_dir) if run_dir else "",
        })
    return {"generated_at": summaries[0].get("created_at", "") if summaries else "",
            "items": payload_items}


HTML = r'''<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Batch result index</title>
<style>
:root{color-scheme:light;--ink:#20303d;--muted:#6c7c88;--line:#dce4e9;--blue:#173847;--accent:#e57d4a;--bg:#edf2f4;--card:#fff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 "Segoe UI","Microsoft JhengHei",sans-serif}
header{background:linear-gradient(120deg,#102d3a,#205365);color:#fff;padding:28px max(24px,calc((100% - 1440px)/2));box-shadow:0 2px 8px #102d3a30}header h1{margin:0 0 4px;font-size:25px}header p{margin:0;color:#c9e0e8}
main{max-width:1440px;margin:18px auto;padding:0 18px}.toolbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px;position:sticky;top:10px;z-index:3;box-shadow:0 2px 8px #18364610}.toolbar input,.toolbar select,.toolbar button,.chartbar input,.chartbar select{font:inherit;border:1px solid #cbd6dc;border-radius:7px;padding:7px 9px;background:#fff;color:var(--ink)}.toolbar input{min-width:230px;flex:1}.toolbar button{cursor:pointer;background:#eaf1f4}.count{margin-left:auto;color:var(--muted);font-weight:600}.cards{display:grid;gap:14px;margin-top:14px}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;display:grid;grid-template-columns:minmax(260px,330px) minmax(0,1fr);gap:18px;box-shadow:0 2px 8px #1836460c}.identity{border-right:1px solid var(--line);padding-right:16px}.identity h2{font-size:18px;margin:0 0 6px;color:var(--blue);word-break:break-word}.badge{display:inline-block;border-radius:999px;padding:2px 8px;background:#e7f1f3;color:#285d6b;font-size:12px;margin:2px 2px 7px 0}.badge.warn{background:#fff0df;color:#96521f}.badge.bad{background:#fbe5e5;color:#9a3232}.meta{margin:4px 0;color:#4c5e69;word-break:break-word}.meta strong{color:#233944}.stats{display:grid;grid-template-columns:1fr 1fr;gap:4px 12px;margin-top:10px;font-size:13px}.stats div{border-bottom:1px solid #eef2f4;padding:3px 0}.links{display:flex;gap:7px;flex-wrap:wrap;margin-top:12px}.links a,.event a{color:#1b667b;text-decoration:none}.links a:hover,.event a:hover{text-decoration:underline}.chartpanel{min-width:0}.chartbar{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-bottom:5px}.chartbar .hint{color:var(--muted);font-size:12px;margin-left:auto}.chart{width:100%;height:220px;background:#102833;border-radius:9px;display:block;cursor:crosshair}.readout{min-height:24px;color:#536a77;font-size:12px;padding:3px 4px}.events{margin-top:9px;border-top:1px solid var(--line);padding-top:8px}.events h3{margin:0 0 5px;font-size:14px}.event{display:grid;grid-template-columns:180px minmax(75px,100px) 1fr;gap:7px;padding:4px 0;border-bottom:1px solid #eef2f4;align-items:center}.event .reason{color:var(--muted);font-size:12px}.event .images{display:flex;gap:7px;flex-wrap:wrap}.event button{border:0;background:none;padding:0;color:#1b667b;cursor:pointer;font:inherit}.event button.missing{color:var(--muted);cursor:default}.details{margin-top:9px;color:#5a6b74}.details pre{white-space:pre-wrap;word-break:break-word;background:#f6f8f9;border:1px solid var(--line);padding:8px;border-radius:7px;max-height:170px;overflow:auto}
.empty{background:#fff;border:1px dashed #bdcbd2;border-radius:10px;padding:32px;text-align:center;color:var(--muted)}dialog{border:0;border-radius:11px;padding:0;box-shadow:0 8px 40px #102d3a55;max-width:92vw;max-height:92vh}dialog::backdrop{background:#102d3a99}.viewer{padding:12px;background:#102833;color:#fff;min-width:min(900px,90vw)}.viewerbar{display:flex;gap:7px;align-items:center;margin-bottom:9px}.viewerbar button{border:1px solid #7d9aa4;border-radius:6px;background:#284a57;color:#fff;padding:6px 10px;cursor:pointer}.viewerbar span{margin-left:auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.viewer img{display:block;max-width:85vw;max-height:78vh;margin:auto;object-fit:contain}.viewer img.full{max-width:none;max-height:none}
@media(max-width:850px){.card{grid-template-columns:1fr}.identity{border-right:0;border-bottom:1px solid var(--line);padding:0 0 12px}.count{margin-left:0}.event{grid-template-columns:1fr}}
</style>
</head>
<body>
<header><h1>批次結果索引</h1><p>離線檢視 · 圖片與檔案連結均指向本匯出包</p></header>
<main>
<section class="toolbar">
  <input id="search" placeholder="搜尋影片、scenarioID、phase、相機、備註…">
  <select id="scenario"><option value="">全部情境</option></select>
  <select id="phase"><option value="">全部 phase</option></select>
  <select id="camera"><option value="">全部相機</option></select>
  <select id="status"><option value="">全部結果狀態</option></select>
  <button id="download">下載篩選後總表</button><span class="count" id="count"></span>
</section>
<section class="cards" id="cards"></section>
</main>
<dialog id="imageDialog"><div class="viewer"><div class="viewerbar"><button id="fit">符合視窗</button><button id="original">原始尺寸</button><button id="closeImage">關閉</button><a id="downloadImage" class="links" download>下載來源圖片</a><span id="imageTitle"></span></div><img id="image" alt="事件圖片"></div></dialog>
<script>
const DATA=__PAYLOAD__;
const $=id=>document.getElementById(id);
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const text=value=>value===null||value===undefined||value===''?'—':String(value);
const labels={first_detection:'自動首次檢出',first_sustained_stable_start:'自動穩定起點',first_stable_confirmation:'自動穩定確認',manual_first_detection:'人工首次檢出',manual_stable_confirmation:'人工穩定確認'};
const fields=['item_id','video','scenarioID','phase','camera','segment','run_serial','execution_status','export_status','stale','detection_outcome','error'];
const statusLabel=s=>({completed:'完成',manual_only:'僅人工標註',partial:'部分完成',failed:'失敗',no_results:'未分析',pending:'未分析',cancelled:'已取消',stable:'已達穩定',not_stable:'未達穩定',no_detection:'未檢出'}[s]||text(s));
function fillSelect(id,values){const el=$(id); [...new Set(values.filter(v=>v!==''&&v!==null&&v!==undefined).map(String))].sort((a,b)=>a.localeCompare(b,'zh-Hant')).forEach(v=>{const o=document.createElement('option');o.value=v;o.textContent=v;el.appendChild(o)})}
fillSelect('scenario',DATA.items.map(x=>x.scenario_id));fillSelect('phase',DATA.items.map(x=>x.phase));fillSelect('camera',DATA.items.map(x=>x.camera));fillSelect('status',DATA.items.flatMap(x=>{const s=x.summary;return [s.export_status,s.execution_status,s.detection_outcome].concat(s.stale?['__stale__']:[])}));
document.querySelectorAll('#status option').forEach(option=>{option.textContent=option.value==='__stale__'?'結果過期':statusLabel(option.value)});
function filtered(){const q=$('search').value.trim().toLocaleLowerCase();const filters={scenario:$('scenario').value,phase:$('phase').value,camera:$('camera').value,status:$('status').value};return DATA.items.filter(x=>{const s=x.summary;const hay=[x.video,x.scenario_id,x.phase,x.camera,x.segment,x.note,x.run_serial,s.execution_status,s.export_status,s.detection_outcome,s.error].map(text).join(' ').toLocaleLowerCase();const statusOk=filters.status==='__stale__'?Boolean(s.stale):!filters.status||[s.export_status,s.execution_status,s.detection_outcome].map(String).includes(filters.status);return(!q||hay.includes(q))&&(!filters.scenario||String(x.scenario_id)===filters.scenario)&&(!filters.phase||String(x.phase)===filters.phase)&&(!filters.camera||String(x.camera)===filters.camera)&&statusOk})}
function stat(label,value){return '<div><strong>'+esc(label)+'</strong><br>'+esc(text(value))+'</div>'}
function eventRecords(item){const by={};(item.events||[]).forEach(e=>by[e.event]=e);return Object.keys(labels).map(key=>{const e=by[key]||{event:key,status:item.run_id?'no_event':'no_results',error:item.run_id?'未發生事件':'未分析'};return e})}
function imageButton(path,label,reason){return path?'<button data-image="'+esc(path)+'">'+esc(label)+'</button>':'<span class="reason">'+esc(reason||'此模式沒有圖片')+'</span>'}
function eventHtml(item){return eventRecords(item).map(e=>{const title=labels[e.event]||e.event;const frame=e.frame??'—';const reason=e.error||statusLabel(e.status);return '<div class="event"><strong>'+esc(title)+'</strong><span>F'+esc(frame)+'</span><span class="images">'+imageButton(e.raw_filename,'原圖',reason)+' '+imageButton(e.annotated_filename,'ROI／bbox標示',reason)+' '+imageButton(e.mask_filename,'Mask',reason)+' <button data-focus="'+esc(e.frame??'')+'" data-card="'+esc(item.item_id)+'"'+(e.frame?'':' class="missing" disabled')+'>定位</button></span></div>'}).join('')}
function makeCard(item){const s=item.summary;const card=document.createElement('article');card.className='card';card.dataset.id=item.item_id;const outcome=s.detection_outcome?({stable:'已達穩定',not_stable:'未達穩定',no_detection:'未檢出'}[s.detection_outcome]||s.detection_outcome):'';const state=statusLabel(s.export_status||s.execution_status);const warn=s.stale?' warn':'';card.innerHTML='<section class="identity"><h2>'+esc(item.scenario_id||'—')+'</h2><span class="badge">phase '+esc(item.phase||'—')+'</span><span class="badge">run '+esc(item.run_serial||'—')+'</span><span class="badge">'+esc(item.camera||'—')+'</span><div class="meta"><strong>影片／區段：</strong>'+esc(item.video)+' / '+esc(item.segment||'—')+'</div><div class="meta"><strong>備註：</strong>'+esc(item.note||'—')+'</div><div class="meta"><strong>狀態：</strong><span class="badge'+warn+'">'+esc(state)+(s.stale?' · 過期':'')+'</span></div><div class="meta"><strong>分析範圍：</strong>'+esc(s.analysis_start_frame?('F'+s.analysis_start_frame+'–F'+s.analysis_end_frame):'—')+'</div><div class="stats">'+stat('Raw rate',s.raw_detection_rate===undefined?'—':Number(s.raw_detection_rate).toLocaleString(undefined,{style:'percent',maximumFractionDigits:1}))+stat('Stable coverage',s.stable_detection_coverage===undefined?'—':Number(s.stable_detection_coverage).toLocaleString(undefined,{style:'percent',maximumFractionDigits:1}))+stat('分析結論',outcome)+stat('項目 ID',item.item_id)+stat('run ID',item.run_id)+stat('錯誤',s.error||s.export_error)+'</div><div class="links">'+(item.links||[]).map(l=>'<a href="'+esc(l.path)+'" download>'+esc(l.label)+'</a>').join('')+'</div><details class="details"><summary>相對目錄與細節</summary><pre>'+esc(JSON.stringify({relative_directory:item.relative_directory,item_id:item.item_id,run_id:item.run_id,error:s.error||s.export_error||''},null,2))+'</pre></details></section><section class="chartpanel"><div class="chartbar"><select class="chartmode"><option value="full">完整區段</option><option value="frame">指定 frame</option><option value="around">事件附近</option></select><input class="frameinput" type="number" min="1" placeholder="frame" disabled><select class="radius" disabled><option>30</option><option selected>60</option><option>150</option><option>300</option></select><button class="redraw">更新曲線</button><span class="hint">Raw／Rolling／Stable · 可移動或點擊游標</span></div><canvas class="chart" width="900" height="220"></canvas><div class="readout">尚未定位 frame</div><div class="events"><h3>事件與圖片</h3>'+eventHtml(item)+'</div></section>';card._item=item;return card}
function rowsFor(item,mode,frame,radius){const rows=item.frames||[];if(!rows.length)return[];if(mode==='frame')return rows;if(mode==='full')return rows;const n=Number(frame)||rows[0].frame;return rows.filter(r=>r.frame>=n-(Number(radius)||60)&&r.frame<=n+(Number(radius)||60))}
function draw(card,focus){const item=card._item;const mode=card.querySelector('.chartmode').value;const inputFrame=Number(card.querySelector('.frameinput').value)||item.frames?.[0]?.frame;const frame=focus??inputFrame;const radius=Number(card.querySelector('.radius').value)||60;if(mode!=='full'&&frame)card._cursor=frame;const rows=rowsFor(item,mode,frame,radius);const canvas=card.querySelector('canvas');const ctx=canvas.getContext('2d');const dpr=window.devicePixelRatio||1;const w=Math.max(320,canvas.clientWidth||900),h=220;canvas.width=w*dpr;canvas.height=h*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);ctx.fillStyle='#102833';ctx.fillRect(0,0,w,h);if(!rows.length){ctx.fillStyle='#b7cbd2';ctx.fillText(item.run_id?'逐幀資料無法載入':'尚無分析結果',16,30);return}const left=42,right=w-12,top=18,bottom=178;const lo=rows[0].frame,hi=rows[rows.length-1].frame;const xOf=n=>left+(n-lo)/Math.max(1,hi-lo)*(right-left);ctx.strokeStyle='#29414e';ctx.lineWidth=1;[0,.5,1].forEach(v=>{const y=bottom-v*(bottom-top);ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();ctx.fillStyle='#91a8b8';ctx.fillText(String(v),8,y+4)});ctx.fillStyle='#b7cbd2';ctx.fillText('F'+lo,left,bottom+25);ctx.fillText('F'+hi,right-42,bottom+25);const series=[['raw_detected','#ffc16c',1],[ 'rolling_rate','#61b8ff',2],['stable_detected','#39dfbf',3]];series.forEach(([field,color,widthPx])=>{ctx.strokeStyle=color;ctx.lineWidth=widthPx;ctx.beginPath();let last=null;const bucket=Math.max(1,Math.ceil(rows.length/Math.max(1,Math.floor(right-left))));for(let i=0;i<rows.length;i+=bucket){const group=rows.slice(i,i+bucket);const values=group.map(r=>Number(r[field])||0);const x=xOf(group[0].frame);const y=bottom-values[0]*(bottom-top);if(last!==null){ctx.lineTo(x,last)}ctx.lineTo(x,y);if(bucket>1){ctx.stroke();ctx.beginPath();ctx.moveTo(x,bottom-Math.min(...values)*(bottom-top));ctx.lineTo(x,bottom-Math.max(...values)*(bottom-top));ctx.stroke();ctx.beginPath();ctx.moveTo(x,y)}last=y}ctx.stroke()});const events=eventRecords(item);events.forEach((e,i)=>{if(e.frame&&e.frame>=lo&&e.frame<=hi){const x=xOf(e.frame);ctx.strokeStyle=i<3?'#ff9a67':'#ee9bff';ctx.setLineDash([4,3]);ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,bottom);ctx.stroke();ctx.setLineDash([])}});const cursor=card._fixed??card._cursor??(mode!=='full'?frame:null);if(cursor&&cursor>=lo&&cursor<=hi){const x=xOf(cursor);ctx.strokeStyle='#fff';ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,bottom);ctx.stroke()}const nearest=cursor?item.frames.reduce((a,b)=>Math.abs(b.frame-cursor)<Math.abs(a.frame-cursor)?b:a,item.frames[0]):null;card.querySelector('.readout').textContent=nearest?'Frame '+nearest.frame+' · Raw '+nearest.raw_detected+' · Rolling '+Number(nearest.rolling_rate).toFixed(3)+' · Stable '+nearest.stable_detected:'範圍 F'+lo+'–F'+hi+' · 移入曲線讀值';}
function bindCard(card){const mode=card.querySelector('.chartmode'),input=card.querySelector('.frameinput'),radius=card.querySelector('.radius');mode.addEventListener('change',()=>{const on=mode.value!=='full';input.disabled=!on;radius.disabled=mode.value!=='around';draw(card)});card.querySelector('.redraw').addEventListener('click',()=>draw(card));const canvas=card.querySelector('canvas');canvas.addEventListener('mousemove',e=>{const rect=canvas.getBoundingClientRect(),rows=card._item.frames||[];if(!rows.length)return;const modeValue=mode.value;const shown=rowsFor(card._item,modeValue,Number(input.value)||rows[0].frame,Number(radius.value)||60);if(!shown.length)return;const n=shown[0].frame+(e.clientX-rect.left)/rect.width*(shown[shown.length-1].frame-shown[0].frame);const closest=card._item.frames.reduce((a,b)=>Math.abs(b.frame-n)<Math.abs(a.frame-n)?b:a,card._item.frames[0]);card._cursor=closest.frame;draw(card,closest.frame)});canvas.addEventListener('click',()=>{card._fixed=card._cursor;draw(card)});card.querySelectorAll('[data-image]').forEach(button=>button.addEventListener('click',()=>openImage(button.dataset.image,button.textContent)));card.querySelectorAll('[data-focus]').forEach(button=>button.addEventListener('click',()=>{const n=Number(button.dataset.focus);if(n){mode.value='around';input.disabled=false;radius.disabled=false;input.value=n;draw(card,n)}}));if(observer)observer.observe(canvas);else draw(card)}
function render(){const list=filtered();$('count').textContent=list.length+' / '+DATA.items.length+' 筆';const container=$('cards');container.innerHTML='';if(!list.length){container.innerHTML='<div class="empty">沒有符合條件的工作項目。</div>';return}list.forEach(item=>{const card=makeCard(item);container.appendChild(card);bindCard(card)})}
['search','scenario','phase','camera','status'].forEach(id=>$(id).addEventListener(id==='search'?'input':'change',render));
const observer='IntersectionObserver' in window?new IntersectionObserver(entries=>entries.forEach(e=>{if(e.isIntersecting){draw(e.target.closest('.card'));observer.unobserve(e.target)}}),{rootMargin:'300px'}):null;
function openImage(path,label){$('image').src=path;$('image').classList.remove('full');$('imageTitle').textContent=label+' · '+path;$('downloadImage').href=path;$('imageDialog').showModal();}
$('fit').onclick=()=>$('image').classList.remove('full');$('original').onclick=()=>$('image').classList.add('full');$('closeImage').onclick=()=>$('imageDialog').close();$('imageDialog').addEventListener('click',e=>{if(e.target===$('imageDialog'))$('imageDialog').close()});
$('download').onclick=()=>{const list=filtered();const all=[...new Set(list.flatMap(x=>fields.concat(Object.keys(x.summary||{}))))];const csv=[all.join(',')].concat(list.map(x=>all.map(k=>{let v=k==='scenarioID'?x.scenario_id:k==='run_serial'?x.run_serial:k==='video'?x.video:k==='camera'?x.camera:k==='segment'?x.segment:k==='phase'?x.phase:k==='item_id'?x.item_id:(x.summary||{})[k];v=v??'';return '"'+String(v).replace(/"/g,'""')+'"'}).join(','))).join('\r\n');const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['\ufeff'+csv],{type:'text/csv;charset=utf-8'}));a.download='filtered_batch_summary.csv';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)};
render();
</script>
</body></html>'''


def write_index(directory: Path, items: Iterable[dict], summaries: list[dict],
                manifest: list[dict], frames_by_item: dict[str, list[dict]]) -> Path:
    directory = Path(directory)
    payload = make_index_payload(directory, items, summaries, manifest, frames_by_item)
    path = directory / "index.html"
    path.write_text(HTML.replace("__PAYLOAD__", _json_for_script(payload)), encoding="utf-8")
    return path
