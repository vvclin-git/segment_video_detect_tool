# ECU Segmentation Video Validator

本機桌面工具，用 HSV 從 ECU 影片中的 segmentation 顏色擷取 mask，支援多影片設定、批次分析及事件複核。影片不會上傳網路。

## 啟動批次版

```powershell
uv sync
uv run python app.py
```

原本的單影片介面仍可使用：`uv run python app.py --single`。

單影片模式的 HSV／ROI、播放與逐幀操作、Raw／Rolling／Stable、Stable 回推起點、CSV 與事件 raw／mask PNG 匯出保持獨立；`--single` 啟動不載入批次模組。可執行 `uv run python -m unittest test_single_video -v` 驗證這些流程（需 Tk 桌面環境）。

## 批次操作流程

1. **匯入影片**：多選 MP4／AVI／MOV／MKV／M4V。第一欄勾選決定批次分析與匯出範圍；點選列決定設定來源。
2. **逐支設定**：雙擊影片或按「設定／預覽」。調整 HSV、最小 blob 面積、N／ON／OFF／K、開始／結束百分比；拖曳畫面框 ROI。可填場次、相機及區段標籤。按「儲存設定」或關閉預覽會套用；新專案還需按「儲存專案」。
3. **套用其他影片**：「複製上一支設定」會複製到目前選取的影片；「套用設定」將目前選取列當來源，可選 HSV／穩定／範圍／ROI 群組套用至勾選影片。不同或未知解析度不複製 ROI，畫面會提示。相同影片需要另一段分析時用「複製為新區段」。
4. **儲存專案**：產生專案 JSON。分析結果放在同位置的 `<專案名稱>.assets/runs/<run_id>/`，每次分析獨立保存。已有專案的設定及完成結果會自動保存。請保留 JSON 與 assets 資料夾；目前結果路徑採絕對路徑，移動分析資料夾後需回原路徑開啟。
5. **批次分析**：選「分析勾選影片」或「分析全部影片」。背景逐片執行，顯示單片及總進度；缺檔／解碼失敗會記錄原因並繼續。取消會停止當前及尚未開始的工作，已完成結果保留。
6. **結果總覽**：每片一列，左側 First／Stable confirmation frame、時間、各自 bbox 與摘要，右側 Raw／Rolling／Stable 圖。可篩選未檢出、未達穩定、失敗或過期結果。
7. **事件複核**：點 First／Stable，圖表預設放大事件前後各 2 秒並開啟對應幀；秒數可調。也可點圖表跳幀、輸入 frame 範圍，或恢復全分析區段。預覽支援播放、前後逐幀、左右方向鍵及直接跳幀。複核固定使用分析當次設定，與游標／當幀數據同步。
8. **批次匯出**：選全部或勾選項目，輸出獨立資料夾。先看 `batch_summary.csv` 的 `execution_status`、`stale`、`export_status` 與 `export_error`，再查看事件圖。

### 當幀複核、固定視窗與 PNG

批次版的設定／預覽及結果複核視窗中，右側 bbox 清單可用 Ctrl／Shift 多選；「全選 bbox」與「清除選取」可快速操作。選取框會在主預覽中以較醒目的顏色顯示。換幀或修改 HSV、ROI、最小 blob 面積會清除選取，切換 Original／Mask／Overlay 或調整視窗大小則保留。

按「獨立檢視當幀」會以來源路徑及開啟時的偵測參數建立自己的解碼器；分析複核視窗使用分析 run 的設定快照。每個視窗都有自己的 frame 導覽、Original／Mask／Overlay、縮放／平移、bbox 多選清單及樣式，不會改變主預覽、結果游標或其他視窗。影像取得焦點後才用 `←`／`→` 逐幀；輸入欄位、下拉選單及 bbox 清單保留方向鍵原本的操作。切幀成功會重新計算 Mask／bbox 並清除選取，失敗則保留前一張有效畫面與 frame 編號。關閉所屬設定／複核視窗時，獨立解碼器也會釋放。

主預覽或獨立視窗按「匯出 PNG」後，每次選擇一種模式：Original（預設，完整原始彩色 frame）、Mask（二值遮罩）或 Overlay（沿用預覽的遮罩疊色），再獨立決定是否包含已選 bbox。三種模式都保持原始解析度；Mask 加框時會轉成彩色。單幀出圖的預設標示為綠色 `#00FF00`、2 px 框線、36 px Pillow 字體；標籤為 `寬 x 高 px`，使用 `#101010` 背景、6 px 內距、框下方置中及 8 px 間隔。可在獨立視窗或匯出對話框調整框／字色、線寬、字體大小及「尺寸／編號＋座標＋尺寸」，對話框會預覽目前設定。標籤會限制在圖片內，必要時改放上方或框內；若字體大到標籤無法放入圖片，會提示調小字體，不截斷。沒有選取 bbox 時不允許匯出帶框圖片，但 Original／Mask／Overlay 原圖仍可匯出。設定以原始影像像素為準，最近一次成功確認的樣式只在目前預覽工作階段記住，不寫入專案。批次事件圖片仍維持既有樣式與流程。

「修改設定」不會改寫舊結果；結果會標示過期。僅修改 N／ON／OFF／K 時，下一次分析重用已保存 Raw 數據；HSV、ROI、面積或分析範圍改變則重新讀取影片。重跑失敗或取消時會明示正在展示前次完成結果。

### 人工事件 frame 標註

每支影片／分析區段可獨立標註「人工首次檢出」與「人工穩定確認」，**不需要先執行自動分析**。

1. 開啟「設定／預覽」、結果頁的「人工標註」，或已分析影片的複核視窗。
2. 在上方「人工事件標註」選擇事件類型，播放／逐幀找到目標後按「標記目前幀」；也可輸入原始 **1-based frame**，填寫備註，再按「儲存標註」（或在 frame 欄按 Enter）。
3. 「跳到標註」回到已儲存的事件幀；「清除標註」刪除目前選擇的人工事件。切換事件類型或關閉視窗前，請先按「儲存標註」提交手動輸入內容。

標註包含 frame、由 FPS 換算的時間、備註與更新時間，保存在專案的 `manual_events`；已有專案會自動存檔，新專案需按「儲存專案」。結果摘要與圖表以紫色標示人工事件，自動 First／Stable 仍獨立保留。修改偵測參數、套用設定及重跑分析均不會改寫人工標註；「複製為新區段」建立空白標註，避免把舊區段事件誤帶入。

可以標註分析區段外、但仍在原影片有效範圍內的幀。這類事件在結果摘要顯示「分析範圍外」，只有落在圖表目前範圍內的事件才畫線；跳幀預覽仍可使用。來源影片被更換時保留原標註並提示重新確認，不用新影片匯出舊標註截圖。

儲存「人工穩定確認」時，自動回推「人工穩定起點」：`確認 frame − Confirm K + 1`。例如確認 F2189、K=150，起點為 F2040。起點早於影片開頭時截至 F1，並標示截斷。上方「回推穩定起點」按鈕可直接跳到該幀；結果摘要、時間軸與離線匯出索引也會列出起點。修改確認幀會更新起點，清除確認則一併移除起點。

新標註保存當時的 K、HSV、ROI 與最小面積；複核視窗使用該次分析的設定，設定／預覽視窗使用目前設定，日後修改參數不會改變已保存標註的回推結果與截圖設定。舊標註沒有設定快照時，使用已保存分析設定，無分析時使用項目設定；如需固定為新的設定，請重新儲存標註。

批次總表包含 `manual_first_detection_*`、`manual_stable_confirmation_*`、`manual_sustained_stable_start_*` 的 frame／timestamp／note／updated_at／status 欄位，另記錄回推 K 與是否截至開頭。在 `<item_id>/manual_events/` 匯出 `manual_events.json`，以及每個人工事件的完整原始 `*_raw.png`、帶 ROI／bbox 的 `*_overlay.png` 和 HSV／ROI 二值 `*_mask.png`；三種事件都有時共九張圖。manifest 的 `event_source=manual` 識別人工事件，`derived_from` 識別回推起點，並提供三種圖片的相對路徑。只有人工標註而尚無自動分析的項目，匯出狀態為 `manual_only`。人工標註不改寫自動 bbox／rolling 紀錄。

### pairing.json 匯入

「匯入 pairing.json」支援 `runs[].cameraMatches` 中的場次／相機對應，使用 `runStartInVideoSec`／`runEndInVideoSec` 與 duration 換算分析起訖百分比。每個場次 × 相機建立獨立工作項目。

同時匯入每個 run 的 `scenarioId`（亦接受 `scenarioID`）、`phase`、`note`。影片清單提供 **scenarioID / phase / note** 三欄，點選列可在下方閱讀完整備註；結果頁與設定／複核視窗也顯示相同情境資訊，批次總表一併匯出。原始 run ID 保留於專案供配對識別，不再當作顯示區段。

已用舊版匯入的專案，請再匯入一次原 pairing.json：相同 run／相機會補上或更新情境資訊，不重複新增影片，保留原本 ROI、threshold、分析結果與人工標註。舊版專案未保存這三個欄位，因此需從 pairing.json 補回；沒有提供的資訊顯示「—」。

可選本機影片根目錄重新尋找檔案：只有檔名唯一吻合時才自動重連；同名多筆不任選，保留缺檔狀態。可選中工作項目後「重新指定影片」。配對資料即使尚未 confirmed 仍可匯入，使用前請檢查影片、場次、ROI 及分析範圍。工具不讀取距離或速度作為判定依據。

### 判定規則

- UI／CSV／檔名皆採原始影片 **1-based frame**；分析開始時清空 rolling history。百分比換算為 `floor(T × start% / 100)` 到 `ceil(T × end% / 100)` 的內部半開索引區間，0–100% 涵蓋全片。
- Raw = ROI 內至少一個通過 HSV 的 blob 面積 ≥ Min blob area；bbox 記錄最大合格 blob，以原始解析度表示。
- Rolling rate = 命中數／已累積視窗長度。未滿 N 幀屬 warm-up，Stable=0。
- 完整 N 幀視窗後，連續 K 幀滿足 hit count ≥ ON 才進入 Stable。K 計算的是 ON 條件，不要求當幀 Raw=1。
- Stable 後，hit count ≤ OFF 當幀退出；中間區域維持 Stable。預設 N=5、ON=4、OFF=1、K=3。
- 同時回傳 **Stable 起點**（`first_sustained_stable_start_frame`）與 **Stable 確認**（`first_stable_confirmation_frame`）；`first_stable_detection_frame` 仍是 confirmation 的別名。結果摘要、圖表與跳幀按鈕皆提供這兩個事件，CSV 另含各自時間／bbox，並匯出各自原始事件圖。
- Stable 起點是「最後成功完成 K 次連續 ON 確認」那一段的第一幀：`起點 = confirmation frame − K + 1`。例如 K=3、frame 102 確認時，起點為 frame 100。未確認成功的短暫候選不列入；未達 Stable 則起點留空。這不是回推到 N 幀視窗最前端，也不保證起點當幀 Raw=1。
- Stable 當幀可能沒有 Raw／bbox；顯示「無 bbox」，不借用鄰幀。CSV 使用 `bbox_valid=0`，寬高 0、位置空白。
- 「首次」只指分析範圍內；分析第一幀已 Raw=1 時會標示「分析起點已檢出」。沒有事件以空值表示，不填 frame=0。
- 時間採 `(frame−1)/reported FPS`。**VFR 的時間為名目估算，不是逐幀 PTS**；frame 為主要判定依據。無效 FPS／幀數／解析度會拒絕分析，不偷偷套用 30 FPS。

### 匯出內容

```text
batch_export_<時間>_<唯一ID>/
  index.html                 # 可雙擊開啟的離線結果索引
  batch_summary.csv
  event_images_manifest.csv
  export_report.json
  <item_id>/<run_id>/
    frame_analysis.csv
    run_summary.csv
    settings.json
    first_detection_frame_XXXXXX_raw.png
    first_detection_frame_XXXXXX_annotated.png
    first_detection_frame_XXXXXX_mask.png
    first_stable_confirmation_frame_XXXXXX_*.png
    first_sustained_stable_start_frame_XXXXXX_*.png
```

Raw PNG 是未加標記、未裁切的完整原始幀，可人工讀取距離。另存 ROI／最大 bbox 標示圖及 mask。沒有事件就不產生該事件圖，manifest 記錄原因；失敗、取消、未分析項目仍留在總表。同名影片及多次匯出不互相覆蓋。

來源識別使用檔案大小及三段內容的 SHA-256 抽樣；影片不符分析快照時仍可匯出既有數據，但不會用新影片產生舊事件圖。這是快速變更檢查，不是全檔雜湊驗證。

### 離線 HTML 結果索引

每次批次匯出都會在根目錄產生 `index.html`。直接雙擊即可檢視，不需要重新選資料夾，也不會啟動本機服務；CSS、JavaScript、摘要、manifest 及圖表逐幀資料均內嵌，圖片與 CSV／JSON 使用匯出包內的相對連結。索引依 pairing 的 `run.sequence` 顯示 run 流水號，沒有時使用場次，缺值顯示「—」。

頁首工具列支援文字搜尋、scenarioID／phase／相機／結果狀態篩選、筆數統計及下載篩選後總表。每張卡片列出情境、影片／區段、備註、分析範圍、狀態與統計，並提供逐幀 CSV、分析摘要、設定檔及可展開的相對目錄／item ID／run ID／錯誤資訊。

圖表提供 Raw、Rolling、Stable，能檢視完整區段、指定 frame 或事件附近 30／60／150／300 幀（預設 60）。滑鼠移動可讀取原始逐幀數值，點擊可固定游標；大量資料以保留 min/max 尖峰的像素分桶繪製。自動首次檢出、穩定起點、穩定確認與人工事件分開列出；原圖、ROI／bbox 標示圖、Mask 沒有就明確顯示原因，不替代或推測。點圖可開啟符合視窗／原始尺寸預覽並下載來源圖片。未分析、未發生事件、缺圖片、失敗及過期結果均保留狀態。

索引生成失敗不會刪除既有匯出內容；程式會提示並在根目錄留下 `index_generation_error.txt`。本版不支援從 HTML 載入外部資料夾、合併零散檔案，也沒有 CDN、網路請求或 `fetch` 本機 CSV。

### 驗證與限制

```powershell
uv run python -m unittest test_app test_batch -v
uv run python -m unittest test_batch_ui -v
uv run python -m unittest test_manual_events -v
```

後兩行需要可建立 Tk 視窗的桌面環境。測試使用實際產生的 MJPG 影片，涵蓋事件判定、ROI、起訖重置、舊演算法一致性、取消／失敗隔離、專案重開、快取重算、GUI 完整操作、人工標註及截圖逐像素比對。

大量影片的圖表只載入目前可視列；每張圖以像素分桶保留 min/max，避免單幀尖峰消失，事件及 hover 仍讀取原始逐幀數據。單片分析仍在記憶體保留逐幀結果，尚未針對數小時／極大單片的記憶體訂定效能保證。原始影片可解码性取決於 OpenCV 後端；正式 ECU 影片仍應以實際資料做最終驗收。

需求與驗收案例見 [實作需求單](docs/batch-analysis-requirements.md)。

## 原單影片介面（--single）

## Features

- Open MP4, AVI, MOV, MKV, or M4V video
- Play/pause, previous/next frame, and seek timeline
- Left/right arrow keyboard frame navigation
- Direct frame-number jump with Enter or the Go button
- User-defined batch-analysis start frame
- Interactive OpenCV HSV thresholds and an ROI-based Hue histogram
- Editable numeric fields beside every HSV and blob-area slider
- Original, mask, and overlay preview modes
- Drag directly on the image to define an ROI
- Connected-component blob detection inside the ROI
- Per-blob `x`, `y`, `width`, `height`, area, and center output
- Bounding boxes and `width x height` labels in the preview
- Full-video raw detection analysis
- Parameterized M-of-N stable detection with consecutive confirmation and ON/OFF hysteresis
- First detection, sustained-stable start, and stable confirmation frame
- Detection rate, stable coverage, stabilization latency, and longest miss
- Per-frame analysis CSV and separate run-summary CSV
- Event bbox data in the run summary
- Event raw-frame/mask PNG export with an event manifest CSV

All reported coordinates and sizes use the original video resolution, not the resized preview.

## Run with uv

```powershell
uv sync
uv run python app.py --single
```

Start with these thresholds for the magenta segmentation color:

```text
H: 160-179
S: 140-255
V: 80-255
```

Use **Min blob area** to suppress compression noise and tiny false positives.

## Stable detection and export

The defaults use a 5-frame window, Stable ON at 4/5 detections, Stable OFF at 1/5 or fewer detections, and 3 confirmation frames. Stable detection is only confirmed when the full-window ON condition remains satisfied for 3 consecutive frames. The sustained-stable start is the first qualifying frame, while the reported first stable confirmation is the real-time confirmation frame and is not backdated.

For example, with `Window=10`, `Stable ON=9`, and `Confirm frames=3`, rolling rates of `0.9, 1.0, 1.0` confirm stable on the third frame. A single `0.9` peak followed by `0.8` is rejected as a transient candidate.

After selecting the ROI and thresholds, click **Analyze Full Video**. When the scan finishes, click **Export CSV**. The app saves one per-frame CSV and one run-summary CSV.

The run summary records three event frames independently:

- `first_detection_frame`: the first raw blob detection
- `first_sustained_stable_start_frame`: the first frame of the candidate run that remains qualified long enough to be confirmed
- `first_stable_confirmation_frame`: the real-time confirmation frame after the configured number of confirmation frames

Each event also has timestamp, bbox position and size, bbox center, and largest blob area fields. The existing
`first_stable_detection_frame` field remains as an alias of `first_stable_confirmation_frame` for older tools.

Set **Analysis start frame** before analysis when the beginning of the video is not part of the valid test interval. Frame CSV output and the M-of-N rolling window both start from this frame. Use the left/right arrow keys to step through frames, or enter a 1-based frame number in **Go to frame** and press Enter.

HSV and minimum-area values can be changed either with the sliders or by typing in the numeric fields. Press Enter or move focus away from a field to apply the value.

Click **Export Event Images** to select a parent folder. The app creates a `<video-name>_event_frames` folder containing:

- `first_detection_frame_XXXXXX_raw.png`
- `first_detection_frame_XXXXXX_mask.png`
- `first_sustained_stable_start_frame_XXXXXX_raw.png`
- `first_sustained_stable_start_frame_XXXXXX_mask.png`
- `first_stable_confirmation_frame_XXXXXX_raw.png`
- `first_stable_confirmation_frame_XXXXXX_mask.png`
- `event_images_manifest.csv`

The raw PNG is the untouched source frame. The mask PNG uses the HSV thresholds and ROI captured when **Analyze Full Video** was run; pixels outside the ROI are black.
The manifest records each exported event's frame, timestamp, detection state, bbox values, and raw/mask filenames.

## ROI operation

1. Pause at a useful frame.
2. Drag on the preview to define the ROI.
3. Drag again to replace it, or click **Clear ROI** to analyze the full frame.

The yellow rectangle is the ROI. Green rectangles are accepted blobs.

## Exact frame access (including VFR video)

Preview, review, analysis, independent viewers and PNG exports use the same
decoded-frame ordering. Backend frame seeking is not used: some VFR inputs return
the wrong image even when OpenCV reports the requested frame number. The reader
counts frames from the beginning and caches nearby images (up to 64 MiB).
Opening a video first counts its decodable frames; this count is cached for the
unchanged file during the session. Initial opening and uncached backward jumps
can therefore take longer on large videos. Displayed seconds remain nominal
`frame_index / reported_fps`, not the source's VFR presentation timestamps.

開檔計數與預覽跳幀期間，狀態列會更新「處理中」、已讀取幀數及耗時；跳幀另顯示目標幀與百分比。計數尚未完成時不顯示推測的總幀數。訊息約每 0.2 秒更新，完成後恢復正常影格資訊。此提示改善等待回饋，解碼仍同步執行，等待期間尚不能即時取消或操作其他控制項。

播放時每個新影格只分析一次，Overlay／Mask 共用結果；換幀清除 bbox 選取不再重畫上一幀。HSV 與連通區分析優先使用 ROI，bbox 座標仍為原圖座標，面積相同時保留原有排序。直方圖播放時最多每秒更新五次，暫停／逐幀時立即更新；播放排程扣除本幀處理耗時，不略過影格。這些改善不改變原始圖片匯出或精確影格讀取方式。

時間軸下方的緩衝條以綠色顯示實際保留在記憶體的影格、藍線顯示目前位置、灰色顯示未快取區段；下方列出快取幀數與範圍，滑鼠移入可查看對應幀是否已快取。播放、跳幀與快取淘汰會同步更新，可顯示不連續區段。獨立視窗使用自己的快取。這是目前記憶體快取的顯示，不代表整段影片已預讀；僅為計數或跳幀而解碼略過的影格不會標綠。

Rerun older analyses after upgrading, re-export event images, and review existing
manual annotations: their saved frame numbers are preserved and are not shifted
automatically. Percentage bounds now use the actual decoded frame count.

Run the regression suite with `python -m unittest discover -v`. The supplied
debug-video case can additionally be checked with
`python verify_debug_video.py <debug_data_folder> <verification_output_folder>`;
this checks a sequential baseline, full and partial analysis, navigation, event
exports, and the actual Tk review/independent viewer paths. It writes a
`verification.json` report and separate analysis/export artifacts.
