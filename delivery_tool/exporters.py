from __future__ import annotations

import csv
import html
import json
from collections import defaultdict
from pathlib import Path


def _safe_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def write_csv(path: Path, manifest: dict) -> Path:
    fields = ["date", "batch_id", "camera", "test", "run_letter", "phase", "event", "frame", "mIoU",
              "mIoU_source", "distance_m", "filename_distance_m", "nominal_time_s", "analysis_item_id",
              "analysis_link_status", "event_sources", "collage", "image", "labelme", "mask", "evaluation_status"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for event in manifest.get("records", []):
            row = {k: event.get(k, "") for k in fields}
            row.update({
                "mIoU": event.get("iou_raw", event.get("iou")), "mIoU_source": event.get("iou_source", ""),
                "distance_m": event.get("distance_raw", event.get("distance_m")),
                "filename_distance_m": event.get("parsed", {}).get("filename_distance_m"),
                "nominal_time_s": event.get("nominal_time_s"),
                "analysis_item_id": event.get("analysis_link", {}).get("item_id", ""),
                "analysis_link_status": event.get("analysis_link", {}).get("status", "unlinked"),
                "event_sources": "+".join(event.get("analysis_link", {}).get("event_sources", [])),
                "image": event.get("image_copy", ""), "labelme": event.get("attachment", {}).get("labelme_path", ""),
                "mask": event.get("attachment", {}).get("mask_path", ""), "evaluation_status": event.get("status", ""),
            })
            writer.writerow(row)
    return path


HTML = r'''<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title><style>
:root{font-family:"Microsoft JhengHei","Noto Sans CJK TC",sans-serif;color:#18313b;background:#edf2f4}*{box-sizing:border-box}body{margin:0}header{padding:26px max(20px,calc((100vw - 1440px)/2));background:#123b4a;color:white}header h1{margin:0 0 6px}header p{margin:4px 0;color:#d6e7ec}main{max-width:1440px;margin:auto;padding:18px}.filters,.card,.integrity{background:white;border:1px solid #d8e2e6;border-radius:10px;padding:14px;margin-bottom:14px}.filters{display:flex;gap:8px;flex-wrap:wrap;position:sticky;top:5px;z-index:2;box-shadow:0 2px 8px #19374612}.filters input,.filters select{font:inherit;padding:7px;border:1px solid #bfccd2;border-radius:6px}.filters input{min-width:230px;flex:1}.card h2{margin:0 0 4px;color:#17536a}.identity{color:#536872;font-size:13px}.metrics{margin:5px 0 10px}.metrics b{margin-right:14px}.collage{width:min(100%,960px);display:block;border:1px solid #d1dade;cursor:zoom-in}.warnings{color:#8a4c18;background:#fff4e7;padding:8px;border-radius:6px;margin-top:8px}.integrity li{padding:3px}.modal{position:fixed;inset:0;background:#101c20e8;display:none;align-items:center;justify-content:center;padding:18px;z-index:5}.modal img{max-width:98vw;max-height:95vh;background:white}.modal button{position:absolute;right:22px;top:18px;padding:8px}.empty{padding:20px;text-align:center;color:#667}a{color:#135b72}.count{margin-left:auto;color:#577}
</style></head><body><header><h1>__TITLE__</h1><p>離線海試結果 · 批次：__BATCH__</p><p>每日 CSV 的 mIoU 原值照錄；來源欄位為 target_iou，未重新計算，非跨類別平均。</p><p>距離採 CSV 檢出距離_m；檔名距離僅供核對。來源為 OCR，人工覆核狀態未知。</p></header><main>
<section class="filters"><input id="q" placeholder="搜尋檔名、航次或批次"><select id="date"><option value="">全部日期</option></select><select id="camera"><option value="">全部 Camera</option></select><select id="test"><option value="">全部 Test</option></select><select id="link"><option value="">全部分析關聯</option><option>linked</option><option>unlinked</option><option>mismatch</option><option>cleared</option></select><span class="count" id="count"></span></section>
<section class="integrity"><h2>完整性</h2><p>評估事件：__COUNT__　圖片：__IMAGES__　GT：__GT__　Mask：__MASK__　分析關聯：__LINKED__</p><details><summary>Project 完整性清單（包含未評估／失敗項目）</summary><ul id="projectList"></ul></details><details><summary>缺漏與品質檢查</summary><ul id="warningList"></ul></details></section>
<section id="cards"></section></main><div class="modal" id="modal"><button id="close">關閉</button><img id="large" alt="Collage 放大"></div>
<script>const DATA=__DATA__;const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fill=(id,vals)=>[...new Set(vals.filter(Boolean))].sort().forEach(x=>{let o=document.createElement('option');o.value=o.textContent=x;document.getElementById(id).append(o)});
fill('date',DATA.records.map(x=>x.date));fill('camera',DATA.records.map(x=>x.camera));fill('test',DATA.records.map(x=>x.test));
document.getElementById('projectList').innerHTML=(DATA.project_completeness||[]).map(x=>'<li>'+esc(x.camera)+' · '+esc(x.scenario_id)+' · '+esc(x.phase)+' · '+esc(x.status)+' · '+(x.evaluated?'已評估':'未評估')+'</li>').join('')||'<li>沒有專案項目</li>';
const issues=[...(DATA.validation?.errors||[]),...(DATA.validation?.warnings||[])];document.getElementById('warningList').innerHTML=issues.map(x=>'<li>'+esc(x.message)+'</li>').join('')||'<li>沒有缺漏</li>';
function render(){let q=document.getElementById('q').value.toLocaleLowerCase(),d=document.getElementById('date').value,c=document.getElementById('camera').value,t=document.getElementById('test').value,l=document.getElementById('link').value;let rows=DATA.records.filter(x=>(!q||[x.filename,x.run_letter,x.phase,x.test].join(' ').toLocaleLowerCase().includes(q))&&(!d||x.date===d)&&(!c||x.camera===c)&&(!t||x.test===t)&&(!l||x.analysis_link?.status===l));document.getElementById('count').textContent=rows.length+' / '+DATA.records.length+' 筆';document.getElementById('cards').innerHTML=rows.map(x=>{let metric=(v,suffix='')=>v===null||v===undefined||v===''?'未提供':esc(v)+suffix;let img=x.collage?'<img class="collage" loading="lazy" src="'+esc(x.collage)+'" data-full="'+esc(x.collage)+'" alt="雙圖 Collage">':'<div class="warnings">原始圖片缺漏，無法產生 Collage</div>';let miss=[];if(x.attachment?.gt_status!=='valid')miss.push('人工 GT 缺漏');if(x.attachment?.mask_status!=='valid')miss.push('預測 Mask 缺漏');if(x.analysis_link?.status!=='linked')miss.push('分析關聯：'+(x.analysis_link?.status||'unlinked'));if(x.video)miss.push('影片：可離線播放');return '<article class="card"><h2>'+esc(x.date)+' · '+esc(x.test)+' · '+esc(x.run_letter)+'／'+esc(x.phase)+' · '+esc(x.camera)+' · '+esc(x.event)+'</h2><div class="identity">'+esc(x.filename)+' · Frame '+metric(x.frame)+' · '+metric(x.nominal_time_s,' 秒')</div><div class="metrics"><b>mIoU：'+metric(x.iou)+'</b><b>距離：'+metric(x.distance_m,' m')+'</b><b>關聯：'+esc(x.analysis_link?.status||'unlinked')+'</b></div>'+img+(x.video?'<p><video controls preload="none" src="'+esc(x.video)+'#t='+esc(x.nominal_time_s||0)+'"></video></p>':'<p>未提供影片，事件定位停用。</p>')+(miss.length?'<div class="warnings">'+miss.map(esc).join(' · ')+'</div>':'')+'</article>'}).join('')||'<div class="empty">沒有符合條件的事件</div>';document.querySelectorAll('[data-full]').forEach(img=>img.onclick=()=>{document.getElementById('large').src=img.dataset.full;document.getElementById('modal').style.display='flex'})}
['q','date','camera','test','link'].forEach(id=>document.getElementById(id).addEventListener(id==='q'?'input':'change',render));document.getElementById('close').onclick=()=>document.getElementById('modal').style.display='none';document.getElementById('modal').onclick=e=>{if(e.target.id==='modal')e.currentTarget.style.display='none'};render();</script></body></html>'''


def write_html(path: Path, manifest: dict, config: dict, counts: dict) -> Path:
    report = config.get("report", {})
    title = html.escape(str(report.get("report_name") or "海試結果報告"))
    payload = _safe_json(manifest)
    page = HTML.replace("__TITLE__", title).replace("__BATCH__", html.escape(str(manifest.get("batch_id") or "未命名")))
    page = page.replace("__COUNT__", str(len(manifest.get("records", []))))
    page = page.replace("__IMAGES__", str(counts.get("images_found", 0))).replace("__GT__", str(counts.get("gt_valid", 0)))
    page = page.replace("__MASK__", str(counts.get("mask_valid", 0))).replace("__LINKED__", str(counts.get("analysis_linked", 0)))
    page = page.replace("__DATA__", payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path
