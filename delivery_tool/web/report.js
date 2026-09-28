(() => {
  'use strict';

  const DATA = JSON.parse(document.getElementById('manifestData').textContent || '{}');
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const shown = value => value === null || value === undefined || value === '' ? '—' : String(value);
  const formatIou = value => {
    if (value === null || value === undefined || value === '') return '—';
    const number = Number(value);
    return Number.isFinite(number) ? number.toFixed(4) : String(value);
  };
  const allSequences = DATA.analysis_sequences || [];
  const sequenceById = new Map(allSequences.map(sequence => [sequence.sequence_id, sequence]));
  const analysisCache = new Map();
  const detail = { group: [], record: null, sequence: null, cameraCleanup: null, selectCleanup: null, renderToken: 0 };
  const eventLabels = {
    FirstDetection: 'FirstDetection', StableStart: 'StableStart', StableConfirmation: 'StableConfirmation'
  };
  const comparisonOverlay = DATA.comparison_overlay || {
    mode: 'three_color_gt_prediction', opacity: 0.65,
    colors: {overlap:'#1EEB5A', gt_only:'#FF282D', prediction_only:'#14D2FF'}
  };

  function unique(values) { return [...new Set(values.filter(value => value !== null && value !== undefined && value !== '').map(String))]; }
  function fillSelect(id, values) {
    unique(values).sort((a, b) => a.localeCompare(b, 'zh-Hant')).forEach(value => {
      const option = document.createElement('option'); option.value = option.textContent = value; $(id).append(option);
    });
  }
  fillSelect('date', (DATA.records || []).map(row => row.date));
  fillSelect('camera', (DATA.records || []).map(row => row.camera));
  fillSelect('test', (DATA.records || []).map(row => row.test));

  const counts = {available:0, missing:0, invalid:0, disabled:0};
  allSequences.forEach(sequence => { if (counts[sequence.status] !== undefined) counts[sequence.status]++; });
  $('chartCounts').textContent = `逐幀資料：可用 ${counts.available}　缺漏 ${counts.missing}　無效 ${counts.invalid}　已關閉 ${counts.disabled}`;

  function filteredRecords() {
    const query = $('search').value.trim().toLocaleLowerCase();
    const date = $('date').value, camera = $('camera').value, test = $('test').value, link = $('link').value;
    return (DATA.records || []).filter(row => {
      const haystack = [row.filename, row.run_letter, row.phase, row.test, row.camera, row.date].map(shown).join(' ').toLocaleLowerCase();
      return (!query || haystack.includes(query)) && (!date || row.date === date) && (!camera || row.camera === camera) &&
        (!test || row.test === test) && (!link || row.analysis_link?.status === link);
    });
  }

  function eventButton(sequenceId, label, record = null) {
    const button = document.createElement('button'); button.type = 'button'; button.className = 'open-detail';
    button.textContent = label; button.addEventListener('click', () => openDetail(sequenceId, record)); return button;
  }

  function makeProjectList() {
    const list = $('projectList'); list.replaceChildren();
    (DATA.project_completeness || []).forEach(item => {
      const li = document.createElement('li'); li.className = 'project-row';
      const title = document.createElement('span');
      title.textContent = `${shown(item.camera)} · Test ${shown(item.scenario_id)} · ${shown(item.phase)} · ${shown(item.status)} · ${item.evaluated ? '已評估' : '未評估'} · 逐幀 ${shown(item.frame_data_status)}`;
      li.append(title);
      if (item.sequence_id && sequenceById.has(item.sequence_id)) li.append(eventButton(item.sequence_id, '查看航次分析'));
      if (item.frame_data_reason) { const reason = document.createElement('span'); reason.className = 'muted'; reason.textContent = item.frame_data_reason; li.append(reason); }
      list.append(li);
    });
    if (!list.children.length) list.innerHTML = '<li>沒有專案項目</li>';
  }
  makeProjectList();

  function makeWarningList() {
    const list = $('warningList'); list.replaceChildren();
    const issues = [...(DATA.validation?.errors || []), ...(DATA.validation?.warnings || [])];
    issues.forEach(issue => { const li = document.createElement('li'); li.textContent = issue.message || issue.code || ''; list.append(li); });
    if (!list.children.length) list.innerHTML = '<li>沒有缺漏</li>';
  }
  makeWarningList();

  function eventTitle(row) {
    const names = {FirstDetection:'FirstDetection', StableStart:'StableStart', StableConfirmation:'StableConfirmation'};
    return names[row.event] || shown(row.event);
  }
  function imagePanels(row) {
    const group = document.createElement('div'); group.className = 'image-panels';
    const panels = [
      ['原始影像', row.image_copy || row.image_path],
      ['分割影像', row.attachment?.seg_status === 'valid' ? row.attachment.seg_path : ''],
      ['GT／預測比較', row.overlay_image]
    ];
    panels.forEach(([label, path]) => {
      const panel = document.createElement('div'); panel.className = 'image-panel';
      const title = document.createElement('div'); title.className = 'image-panel-title'; title.textContent = label; panel.append(title);
      if (label === 'GT／預測比較') {
        const legend = document.createElement('div'); legend.className = 'comparison-legend';
        const entries = [
          ['overlap', 'GT 與預測重疊'], ['gt_only', '僅 GT'], ['prediction_only', '僅預測']
        ];
        entries.forEach(([key, name]) => {
          const item = document.createElement('span'); item.className = 'comparison-legend-item';
          const swatch = document.createElement('i'); swatch.style.backgroundColor = comparisonOverlay.colors?.[key] || '#777777';
          item.append(swatch, document.createTextNode(name)); legend.append(item);
        });
        const opacity = document.createElement('span'); opacity.className = 'comparison-opacity';
        opacity.textContent = `疊圖不透明度：${Math.round(Number(comparisonOverlay.opacity ?? 0.65) * 100)}%`;
        legend.append(opacity); panel.append(legend);
      }
      if (path) {
        const link = document.createElement('a'); link.href = path; link.target = '_blank'; link.rel = 'noopener';
        link.title = `${label}：另開原始解析度圖片`; link.setAttribute('aria-label', link.title);
        const image = document.createElement('img'); image.src = path; image.alt = `${row.camera} ${eventTitle(row)} ${label}`; image.loading = 'lazy';
        link.append(image); panel.append(link);
      } else {
        const missing = document.createElement('div'); missing.className = 'missing-image'; missing.textContent = `未提供${label}`; panel.append(missing);
      }
      group.append(panel);
    });
    return group;
  }
  function makeRecordCard(row) {
    const sequenceId = row.analysis_link?.sequence_id;
    const article = document.createElement('article'); article.className = 'card';
    const image = '<div class="event-image-host"></div>';
    const movie = row.video ? '<div class="muted">影片已打包；航次明細提供相機共用播放器。</div>' : '<div class="muted">此事件沒有打包影片。</div>';
    article.innerHTML = `<h2>${esc(row.date)} · Test ${esc(row.test)} · ${esc(row.run_letter)}／${esc(row.phase)} · ${esc(row.camera)} · ${esc(eventTitle(row))}</h2>
      <div class="identity">${esc(row.filename)} · Frame ${esc(shown(row.frame))} · ${esc(row.nominal_time_s == null ? '—' : Number(row.nominal_time_s).toFixed(3) + ' 秒')}</div>
      <div class="card-grid"><div>${image}</div><div><div class="metrics"><b>mIoU：${esc(formatIou(row.iou))}</b><b>距離：${esc(row.distance_m == null ? '—' : row.distance_m + ' m')}</b><b>關聯：${esc(row.analysis_link?.status || 'unlinked')}</b></div>
      <div class="badges"><span class="badge">GT ${esc(row.attachment?.gt_status || 'missing')}</span><span class="badge">Mask ${esc(row.attachment?.mask_status || 'missing')}</span></div>
      <p>${movie}</p><div class="links"></div></div></div>`;
    article.querySelector('.event-image-host').append(imagePanels(row));
    if (sequenceId && sequenceById.has(sequenceId)) article.querySelector('.metrics').append(eventButton(sequenceId, '查看航次分析', row));
    else if (row.analysis_link?.status !== 'linked') {
      const note = document.createElement('div'); note.className = 'warning'; note.textContent = `沒有可開啟的分析項目：${row.analysis_link?.status || 'unlinked'}`; article.append(note);
    }
    const linkBox = article.querySelector('.links');
    (row.links || []).forEach(link => { const anchor = document.createElement('a'); anchor.href = link.path; anchor.download = ''; anchor.textContent = link.label; anchor.style.marginRight = '10px'; linkBox.append(anchor); });
    article.querySelectorAll('[data-full]').forEach(img => img.addEventListener('click', () => openImage(img.dataset.full)));
    return article;
  }
  function renderList() {
    const rows = filteredRecords(); $('count').textContent = `${rows.length} / ${(DATA.records || []).length} 筆`;
    const cards = $('cards'); cards.replaceChildren();
    rows.forEach(row => cards.append(makeRecordCard(row)));
    if (!rows.length) cards.innerHTML = '<div class="card muted">沒有符合條件的事件</div>';
  }
  ['search','date','camera','test','link'].forEach(id => $(id).addEventListener(id === 'search' ? 'input' : 'change', renderList));

  function tripRecords(sequence, anchor) {
    if (anchor) return (DATA.records || []).filter(row => row.date === anchor.date && row.test === anchor.test && row.run_letter === anchor.run_letter && row.phase === anchor.phase);
    return (DATA.records || []).filter(row => row.analysis_link?.sequence_id === sequence.sequence_id);
  }
  function sameTrip(a, b) {
    if (!a.pairing_run_id || !b.pairing_run_id) return a.sequence_id === b.sequence_id;
    return String(a.pairing_run_id) === String(b.pairing_run_id) && String(a.scenario_id || '') === String(b.scenario_id || '') && String(a.phase || '') === String(b.phase || '');
  }
  function summaryCard(cameraName, sequences, tripRows) {
    const article = document.createElement('article'); article.className = 'camera-summary';
    const seq = sequences.find(item => item.camera === cameraName);
    const rows = tripRows.filter(row => row.camera === cameraName);
    const h3 = document.createElement('h3'); h3.textContent = cameraName; article.append(h3);
    if (!seq) {
      const missing = document.createElement('div'); missing.className = 'warning'; missing.textContent = '沒有此相機的逐幀專案資料'; article.append(missing);
    } else if (seq.status !== 'available') {
      const missing = document.createElement('div'); missing.className = 'warning'; missing.textContent = `逐幀資料${seq.status === 'invalid' ? '無效' : seq.status === 'disabled' ? '已關閉' : '缺漏'}：${seq.reason || '原因未提供'}`; article.append(missing);
    }
    const first = rows.find(row => row.event === 'FirstDetection');
    const stable = rows.find(row => row.event === 'StableStart');
    const figures = document.createElement('div'); figures.className = 'event-collages';
    [[first,'FirstDetection'],[stable,'StableStart']].forEach(([row,label]) => {
      const figure = document.createElement('figure'); const caption = document.createElement('figcaption'); caption.textContent = label; figure.append(caption);
      if (row) figure.append(imagePanels(row));
      else { const placeholder = document.createElement('div'); placeholder.className = 'missing-image'; placeholder.textContent = '沒有評估事件'; figure.append(placeholder); }
      if (row) {
        const metric = document.createElement('div'); metric.className = 'camera-metrics';
        metric.textContent = `Frame ${shown(row.frame)} · mIoU ${formatIou(row.iou)} · 距離 ${row.distance_m == null ? '—' : row.distance_m + ' m'}`;
        figure.append(metric);
      }
      figures.append(figure);
    });
    article.append(figures);
    const metrics = document.createElement('div'); metrics.className = 'camera-metrics';
    if (!rows.length) { const line = document.createElement('div'); line.className = 'muted'; line.textContent = '本相機沒有評估事件；若有逐幀資料仍可在下方查看。'; metrics.append(line); }
    article.append(metrics); return article;
  }

  function cleanupCamera() { detail.renderToken++; if (detail.cameraCleanup) { detail.cameraCleanup(); detail.cameraCleanup = null; } }
  function openDetail(sequenceId, anchorRecord = null) {
    const selected = sequenceById.get(sequenceId); if (!selected) return;
    cleanupCamera(); if(detail.selectCleanup){detail.selectCleanup();detail.selectCleanup=null;} detail.sequence = selected; detail.record = anchorRecord;
    detail.group = allSequences.filter(item => sameTrip(item, selected));
    if (!detail.group.length) detail.group = [selected];
    const records = tripRecords(selected, anchorRecord);
    const firstRecord = anchorRecord || records[0] || null;
    const date = firstRecord?.date || '未評估';
    const test = firstRecord?.test || selected.scenario_id || '—';
    const run = firstRecord?.run_letter || selected.pairing_run_id || '—';
    const phase = firstRecord?.phase || selected.phase || '—';
    $('tripTitle').textContent = `${date} · Test ${test} · 航次 ${run} · ${phase}`;
    $('tripMeta').textContent = `Project ${selected.project_id || '—'} · Item ${selected.item_id || '—'} · Analysis run ${selected.run_id || '—'}`;
    const cameras = unique(['Camera 1','Camera 2', ...detail.group.map(item => item.camera), ...records.map(row => row.camera)]);
    const summaries = $('cameraSummaries'); summaries.replaceChildren();
    cameras.forEach(camera => summaries.append(summaryCard(camera, detail.group, records)));
    $('listView').hidden = true; $('detailView').hidden = false;
    const cameraSelect = $('detailCamera'); cameraSelect.replaceChildren();
    cameras.forEach(camera => {
      const candidate = detail.group.find(item => item.camera === camera);
      const option = document.createElement('option'); option.value = candidate?.sequence_id || `missing:${camera}`;
      option.textContent = candidate ? `${camera} · ${candidate.status === 'available' ? '逐幀可用' : candidate.reason || candidate.status}` : `${camera} · 沒有逐幀資料`;
      cameraSelect.append(option);
    });
    const defaultSequence = detail.group.find(item => item.status === 'available') || detail.group.find(item => item.camera === selected.camera) || detail.group[0];
    cameraSelect.value = defaultSequence?.sequence_id || `missing:${cameras[0]}`;
    const renderCamera = () => {
      cleanupCamera(); const token = detail.renderToken;
      const sequence = sequenceById.get(cameraSelect.value) || null;
      Promise.resolve(renderCameraReview(sequence, cameraSelect.value.replace(/^missing:/, ''))).then(cleanup => {
        if (token === detail.renderToken) detail.cameraCleanup = cleanup || null; else if (cleanup) cleanup();
      });
    };
    cameraSelect.addEventListener('change', renderCamera);
    detail.selectCleanup = () => cameraSelect.removeEventListener('change', renderCamera);
    renderCamera(); window.scrollTo({top:0,behavior:'auto'});
  }
  function closeDetail() {
    cleanupCamera(); if(detail.selectCleanup){detail.selectCleanup();detail.selectCleanup=null;} $('cameraReview').replaceChildren(); $('detailCamera').replaceChildren(); $('detailView').hidden = true; $('listView').hidden = false;
    window.scrollTo({top:0,behavior:'auto'});
  }
  $('backToList').addEventListener('click', closeDetail);

  function renderCameraReview(sequence, cameraName) {
    const host = $('cameraReview'); host.replaceChildren();
    if (!sequence) {
      const message = document.createElement('div'); message.className = 'warning'; message.textContent = `${cameraName} 沒有逐幀分析項目；未借用其他相機資料。`;
      host.append(message); return;
    }
    if (sequence.status !== 'available') {
      const message = document.createElement('div'); message.className = 'warning';
      message.textContent = `此圖表${sequence.status === 'invalid' ? '無效' : sequence.status === 'disabled' ? '已關閉' : '缺漏'}：${sequence.reason || '原因未提供'}。其他報告內容仍可使用。`;
      host.append(message); return renderVideo(sequence, host, () => {});
    }
    loadSequence(sequence).then(payload => {
      if (!host.isConnected || sequence !== sequenceById.get($('detailCamera').value)) return () => {};
      const normalized = Object.assign({}, sequence, {rows: payload.rows.map(unpackRow), events: payload.events || sequence.events,
        settings: payload.settings || sequence.settings, fps: payload.fps ?? sequence.fps,
        analysis_start_frame: payload.start ?? sequence.analysis_start_frame, analysis_end_frame: payload.end ?? sequence.analysis_end_frame});
      return renderVideo(normalized, host, videoApi => createChart(normalized, host, videoApi));
    }).catch(error => {
      if (!host.isConnected) return () => {};
      const message = document.createElement('div'); message.className = 'warning'; message.textContent = `逐幀資料檔載入失敗：${error.message || error}`; host.append(message);
      return renderVideo(sequence, host, () => {});
    });
  }

  function loadSequence(sequence) {
    if (analysisCache.has(sequence.sequence_id)) return analysisCache.get(sequence.sequence_id);
    const promise = new Promise((resolve, reject) => {
      if (!sequence.data_path) { reject(new Error('交付包沒有此序列檔案')); return; }
      const script = document.createElement('script'); script.src = sequence.data_path; script.async = true;
      script.onload = () => {
        const payload = window.__SEA_TRIAL_FRAME_DATA__?.[sequence.sequence_id];
        if (!payload) reject(new Error('資料檔沒有註冊預期的序列識別')); else resolve(payload);
      };
      script.onerror = () => reject(new Error(`無法讀取 ${sequence.data_path}`));
      document.head.append(script);
    });
    analysisCache.set(sequence.sequence_id, promise); return promise;
  }
  function unpackRow(value) {
    return {frame:value[0],timestamp:value[1],raw_detected:value[2],rolling_rate:value[3],stable_detected:value[4],
      selected_pixels:value[5],largest_blob_area:value[6],bbox_valid:value[7],bbox_x:value[8],bbox_y:value[9],
      bbox_width:value[10],bbox_height:value[11],window_ready:value[12]};
  }

  function renderVideo(sequence, host, ready) {
    const block = document.createElement('div'); block.className = 'video-block'; host.append(block);
    let video = null, raf = 0, destroyed = false, lastDraw = 0, pendingFrame = null;
    const listeners = [];
    const listen = (target, name, fn) => { target.addEventListener(name, fn); listeners.push([target,name,fn]); };
    const note = document.createElement('p'); note.className = 'video-note'; block.append(note);
    if (!sequence.video) {
      const reason = sequence.video_reason || '沒有打包影片；圖表仍可用 Frame 操作。'; note.textContent = reason;
      const api={get video(){return null;}, seek(){return '沒有打包影片；僅定位圖表。'}, setReadout(){}};ready(api); return () => { destroyed = true; };
    }
    video = document.createElement('video'); video.controls = true; video.preload = 'metadata'; video.src = sequence.video; block.prepend(video);
    let playbackTick = null;
    const api = {
      video,
      setReadout: (frame, outOfRange) => { if (playbackTick) playbackTick(frame, outOfRange); },
      seek(frame) {
        if (!Number.isFinite(sequence.fps) || sequence.fps <= 0) return 'FPS 無效，停用影片定位。';
        if (!video || video.error) return '瀏覽器無法播放此影片。';
        if (video.readyState < 1) { pendingFrame = frame; return '等待影片 metadata 載入後定位。'; }
        const seconds = (frame - 1) / sequence.fps;
        if (!Number.isFinite(video.duration) || seconds < 0 || seconds > video.duration) return '名目時間超出影片長度，未執行定位。';
        video.currentTime = seconds; return `已定位至名目 ${seconds.toFixed(3)} 秒（瀏覽器時間估算，非逐幀精準解碼）。`;
      }
    };
    const metadata = () => { if (pendingFrame !== null) { const value = pendingFrame; pendingFrame = null; note.textContent = api.seek(value); } };
    const timeUpdate = () => {
      if (destroyed || !sequence.fps) return;
      const frame = Math.round(video.currentTime * sequence.fps) + 1;
      const outside = frame < sequence.analysis_start_frame || frame > sequence.analysis_end_frame;
      api.setReadout(frame, outside);
    };
    const playback = () => {
      if (destroyed || video.paused || video.ended) { raf = 0; return; }
      if (!lastDraw || performance.now() - lastDraw >= 90) { lastDraw = performance.now(); timeUpdate(); }
      raf = requestAnimationFrame(playback);
    };
    const play = () => { if (!raf) raf = requestAnimationFrame(playback); };
    const error = () => { note.textContent = '瀏覽器不支援此影片編碼或影片檔無法讀取；逐幀圖表仍可使用。'; };
    listen(video,'loadedmetadata',metadata); listen(video,'seeked',timeUpdate); listen(video,'play',play); listen(video,'pause',()=>{if(raf){cancelAnimationFrame(raf);raf=0;}}); listen(video,'error',error);
    note.textContent = Number.isFinite(sequence.fps) && sequence.fps > 0
      ? `Frame 定位使用 (frame−1) / FPS 名目時間估算，非逐幀精準解碼。${sequence.timestamp_basis ? ' ' + sequence.timestamp_basis : ''}`
      : 'FPS 無效；影片可播放，但停用秒數換算與影片定位。';
    ready(api);
    return () => {
      destroyed = true; if (raf) cancelAnimationFrame(raf);
      listeners.forEach(([target,name,fn]) => target.removeEventListener(name,fn));
      if (video) { video.pause(); video.removeAttribute('src'); video.load(); }
    };
  }

  function createChart(sequence, host, videoApi) {
    const shell = document.createElement('div'); shell.className = 'chart-shell'; host.append(shell);
    const validStart = sequence.analysis_start_frame, validEnd = sequence.analysis_end_frame;
    const state = {start:validStart,end:validEnd,cursor:null,fixed:null,drag:null,suppressClick:false,series:{raw:true,rolling:true,stable:true},draw:null,read:null};
    const controls = document.createElement('div'); controls.className = 'chart-controls'; shell.append(controls);
    const full = document.createElement('button'); full.textContent = '完整區段'; full.className = 'active'; controls.append(full);
    const rangeLabel = document.createElement('strong'); rangeLabel.textContent = '指定 Frame 範圍'; controls.append(rangeLabel);
    const range = document.createElement('div'); range.className = 'range-fields'; range.innerHTML = `<input class="range-start" type="number" aria-label="Frame 範圍起點"><span>至</span><input class="range-end" type="number" aria-label="Frame 範圍終點"><button class="apply-range">套用</button>`; controls.append(range);
    const around = document.createElement('button'); around.textContent = '移至事件'; controls.append(around);
    const eventSelect = document.createElement('select'); eventSelect.setAttribute('aria-label','選擇事件');
    sequence.events.forEach((event,index) => { const option = document.createElement('option'); option.value = String(index); option.textContent = `${eventTitle(event)} · ${event.source === 'manual' ? '人工' : '自動'} F${event.frame}`; eventSelect.append(option); });
    controls.append(eventSelect);
    const seconds = document.createElement('input'); seconds.type='number'; seconds.min='0'; seconds.step='0.5'; seconds.value='2'; seconds.setAttribute('aria-label','事件前後秒數'); seconds.style.width='82px'; controls.append(seconds);
    const zoomHint = document.createElement('span'); zoomHint.className='muted'; zoomHint.textContent='滾輪縮放 · 拖曳平移 · 單擊固定游標'; controls.append(zoomHint);
    const toggles = document.createElement('div'); toggles.className='series-toggles';
    [['raw','Raw','#ffc16c'],['rolling','Rolling','#61b8ff'],['stable','Stable','#39dfbf']].forEach(([key,label,color])=>{
      const wrapper=document.createElement('label'), input=document.createElement('input'); input.type='checkbox';input.checked=true;
      input.addEventListener('change',()=>{state.series[key]=input.checked;draw();});
      const mark=document.createElement('i');mark.className='series-mark';mark.style.background=color;wrapper.append(input,mark,document.createTextNode(label));toggles.append(wrapper);
    }); controls.append(toggles);
    const scope = document.createElement('p'); scope.className='scope-note'; scope.textContent=`分析範圍 F${validStart}–F${validEnd} · ${thresholdDescription(sequence.settings)} · 圖表只繪製有效分析資料。`; shell.append(scope);
    const canvas=document.createElement('canvas');canvas.className='chart-canvas';canvas.setAttribute('aria-label','Raw、Rolling、Stable 逐幀圖表');shell.append(canvas);
    const readout=document.createElement('div');readout.className='readout';readout.textContent='移入圖表查看原始逐幀值；單擊可固定並定位影片。';shell.append(readout);
    const eventButtons=document.createElement('div');eventButtons.className='event-buttons';shell.append(eventButtons);
    sequence.events.forEach(event=>{
      const button=document.createElement('button'); button.type='button'; button.textContent=`${eventTitle(event)} · ${event.source==='manual'?'人工':'自動'} F${event.frame}`;
      if(event.frame<validStart||event.frame>validEnd){button.classList.add('outside');button.title='分析範圍外；不會顯示推測讀值。'}
      button.addEventListener('click',()=>focusEvent(event));eventButtons.append(button);
    });
    const startInput=range.querySelector('.range-start'),endInput=range.querySelector('.range-end');
    startInput.min=endInput.min=String(validStart);startInput.max=endInput.max=String(validEnd);endInput.max=startInput.max=String(validEnd);
    const aroundEnabled=Number.isFinite(sequence.fps)&&sequence.fps>0;
    around.disabled=eventSelect.disabled=seconds.disabled=!aroundEnabled;
    if(!aroundEnabled){const note=document.createElement('span');note.className='muted';note.textContent='FPS 無效，事件秒數換算已停用；Frame 範圍操作仍可用。';controls.append(note);}
    function setRange(start,end){
      const a=Math.max(validStart,Math.min(validEnd,Math.floor(Number(start)||validStart)));
      const b=Math.max(validStart,Math.min(validEnd,Math.ceil(Number(end)||validEnd)));
      state.start=Math.min(a,b);state.end=Math.max(a,b);state.fixed=null;
      startInput.value=String(state.start);endInput.value=String(state.end);full.classList.toggle('active',state.start===validStart&&state.end===validEnd);draw();
    }
    function frameRow(frame){const index=Math.round(frame)-validStart;return index>=0&&index<sequence.rows.length?sequence.rows[index]:null;}
    function setReadout(frame,outside=false){
      state.cursor=frame;
      if(outside||frame<validStart||frame>validEnd){readout.textContent=`影片位於分析範圍外（F${frame}；分析範圍 F${validStart}–F${validEnd}），沒有此處逐幀讀值。`;draw();return;}
      const row=frameRow(frame); if(!row){readout.textContent='此 Frame 沒有逐幀資料。';draw();return;}
      const eventNames=eventsAt(row.frame).map(e=>`${eventTitle(e)}（${e.source==='manual'?'人工':'自動'}）`);
      const bbox=row.bbox_valid===1?`bbox ${shown(row.bbox_x)},${shown(row.bbox_y)},${shown(row.bbox_width)}×${shown(row.bbox_height)}`:'bbox 無效';
      readout.textContent=`F${row.frame} · ${Number.isFinite(row.timestamp)?row.timestamp.toFixed(3)+' 秒':'時間 —'} · Raw ${row.raw_detected} · Rolling ${row.rolling_rate.toFixed(3)} · Stable ${row.stable_detected}${row.window_ready===0?' · warm-up':''} · selected pixels ${shown(row.selected_pixels)} · 最大 Blob ${shown(row.largest_blob_area)} · ${bbox}${eventNames.length?' · 事件 '+eventNames.join('／'):''}`;
      draw();
    }
    function draw(){
      state.draw=draw;
      const rect=canvas.getBoundingClientRect(), width=Math.max(320,rect.width||900), height=Math.max(260,rect.height||390), dpr=window.devicePixelRatio||1;
      canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,width,height);ctx.fillStyle='#102833';ctx.fillRect(0,0,width,height);
      const left=64,right=width-16,top=36,bottom=height-48,plotWidth=Math.max(1,right-left),plotHeight=bottom-top;
      const xOf=frame=>left+(frame-state.start)/Math.max(1,state.end-state.start)*plotWidth;
      ctx.font='12px Microsoft JhengHei, sans-serif';ctx.strokeStyle='#29414e';ctx.lineWidth=1;
      [0,.5,1].forEach(value=>{const y=bottom-value*plotHeight;ctx.beginPath();ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();ctx.fillStyle='#a9bbc3';ctx.fillText(value.toFixed(1),23,y+4);});
      ctx.fillStyle='#bfd0d6';ctx.fillText(`F${state.start}`,left,bottom+26);const endLabel=`F${state.end}`;ctx.fillText(endLabel,right-ctx.measureText(endLabel).width,bottom+26);
      ctx.fillStyle='#97adb6';ctx.fillText(`Analysis F${validStart}–F${validEnd}`,left,20);
      const first=Math.max(0,state.start-validStart),last=Math.min(sequence.rows.length-1,state.end-validStart);
      const definitions=[['raw','raw_detected','#ffc16c',1.6],['rolling','rolling_rate','#61b8ff',2.2],['stable','stable_detected','#39dfbf',2.2]];
      definitions.forEach(([key,field,color,lineWidth])=>{
        if(!state.series[key])return;
        ctx.strokeStyle=color;ctx.lineWidth=lineWidth;ctx.beginPath();let started=false;
        const indices=globalThis.SeaTrialChartMath.reduceSeriesIndices(sequence.rows,first,last,field,plotWidth);
        indices.forEach(index=>{const row=sequence.rows[index],x=xOf(row.frame),y=bottom-row[field]*plotHeight;if(!started){ctx.moveTo(x,y);started=true;}else ctx.lineTo(x,y);});
        ctx.stroke();
      });
      const groups=new Map();sequence.events.forEach(event=>{const frame=Number(event.frame);if(!Number.isFinite(frame)||frame<state.start||frame>state.end)return;const list=groups.get(frame)||[];list.push(event);groups.set(frame,list);});
      let labelRow=0;groups.forEach((events,frame)=>{const x=xOf(frame);ctx.strokeStyle=events.some(e=>e.source==='manual')?'#ee9bff':'#ff9a67';ctx.setLineDash([5,4]);ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,bottom);ctx.stroke();ctx.setLineDash([]);const title=events.map(event=>`${eventTitle(event)} ${event.source==='manual'?'人工':'自動'}`).join('／');ctx.fillStyle=events.some(e=>e.source==='manual')?'#f3b9ff':'#ffb28b';ctx.fillText(title,Math.min(right-120,Math.max(left,x-32)),top+14+(labelRow%3)*14);labelRow++;});
      const cursor=state.fixed??state.cursor;if(cursor!==null&&cursor>=state.start&&cursor<=state.end){const x=xOf(cursor);ctx.strokeStyle='#fff';ctx.lineWidth=1.5;ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,bottom);ctx.stroke();}
      canvas.title=cursor===null?'':eventsAt(Math.round(cursor)).map(event=>`${eventTitle(event)} ${event.source==='manual'?'人工':'自動'} F${event.frame}`).join(' · ');
    }
    function eventsAt(frame){return sequence.events.filter(event=>Number(event.frame)===Number(frame));}
    function locate(frame){
      const result=videoApi.seek(frame);if(result)readout.textContent += ` · ${result}`;
    }
    function focusEvent(event){
      state.fixed=Number(event.frame);state.cursor=Number(event.frame);
      if(event.frame>=validStart&&event.frame<=validEnd){
        if(aroundEnabled){const radius=Math.round(Number(seconds.value||2)*sequence.fps);setRange(event.frame-radius,event.frame+radius);state.fixed=Number(event.frame);state.cursor=Number(event.frame);setReadout(event.frame);}
        else {setReadout(event.frame);}
      } else {readout.textContent=`${eventTitle(event)} F${event.frame} 位於分析範圍外（F${validStart}–F${validEnd}），沒有逐幀讀值。`;draw();}
      locate(Number(event.frame));
    }
    full.addEventListener('click',()=>setRange(validStart,validEnd));
    range.querySelector('.apply-range').addEventListener('click',()=>setRange(startInput.value,endInput.value));
    around.addEventListener('click',()=>{
      const event=sequence.events[Number(eventSelect.value)];if(!event)return;
      const radius=Math.round(Number(seconds.value||2)*sequence.fps);setRange(event.frame-radius,event.frame+radius);
      state.fixed=Number(event.frame);state.cursor=Number(event.frame);setReadout(event.frame);locate(Number(event.frame));
    });
    canvas.addEventListener('wheel',event=>{
      event.preventDefault();const rect=canvas.getBoundingClientRect(),ratio=Math.max(0,Math.min(1,(event.clientX-rect.left-64)/(rect.width-80)));
      const span=state.end-state.start+1,current=state.start+ratio*span,factor=event.deltaY<0?.78:1.28,newSpan=Math.max(1,Math.min(validEnd-validStart+1,Math.round(span*factor)));
      const start=current-ratio*newSpan;setRange(start,start+newSpan-1);
    },{passive:false});
    canvas.addEventListener('pointerdown',event=>{canvas.setPointerCapture(event.pointerId);state.drag={x:event.clientX,start:state.start,end:state.end,moved:false};});
    canvas.addEventListener('pointermove',event=>{
      const rect=canvas.getBoundingClientRect();
      if(state.drag){const dx=event.clientX-state.drag.x;if(Math.abs(dx)>3)state.drag.moved=true;if(state.drag.moved){const shift=-dx/Math.max(1,rect.width-80)*(state.drag.end-state.drag.start+1);setRange(state.drag.start+shift,state.drag.end+shift);return;}}
      const ratio=Math.max(0,Math.min(1,(event.clientX-rect.left-64)/(rect.width-80)));const frame=Math.round(state.start+ratio*(state.end-state.start));
      if(frame>=validStart&&frame<=validEnd){state.cursor=frame;setReadout(frame);}
    });
    canvas.addEventListener('pointerup',event=>{
      if(!state.drag)return;const moved=state.drag.moved;state.drag=null;
      if(moved){state.suppressClick=true;return;}
      const rect=canvas.getBoundingClientRect(),ratio=Math.max(0,Math.min(1,(event.clientX-rect.left-64)/(rect.width-80)));const frame=Math.round(state.start+ratio*(state.end-state.start));
      state.fixed=frame;state.cursor=frame;setReadout(frame);locate(frame);
    });
    state.read=setReadout;
    videoApi.setReadout((frame,outside)=>setReadout(frame,outside));
    startInput.value=String(state.start);endInput.value=String(state.end);draw();
  }

  function thresholdDescription(settings={}) {
    const n=settings.window_n, on=settings.stable_on_m, off=settings.stable_off_count, k=settings.stable_confirm_frames;
    if(!n||on===undefined||off===undefined||!k)return '分析當次閾值未提供';
    return `分析當次閾值：ON ≥ ${on}/${n}、OFF ≤ ${off}/${n}、K = ${k} 個連續確認幀`;
  }

  function openImage(path) { $('large').src=path; $('modal').classList.add('open'); $('modal').setAttribute('aria-hidden','false'); }
  function closeImage() { $('modal').classList.remove('open'); $('modal').setAttribute('aria-hidden','true'); $('large').removeAttribute('src'); }
  $('closeModal').addEventListener('click',closeImage);$('modal').addEventListener('click',event=>{if(event.target===$('modal'))closeImage();});
  renderList();
})();
