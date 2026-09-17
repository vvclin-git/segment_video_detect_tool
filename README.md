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

「修改設定」不會改寫舊結果；結果會標示過期。僅修改 N／ON／OFF／K 時，下一次分析重用已保存 Raw 數據；HSV、ROI、面積或分析範圍改變則重新讀取影片。重跑失敗或取消時會明示正在展示前次完成結果。

### 人工事件 frame 標註

每支影片／分析區段可獨立標註「人工首次檢出」與「人工穩定確認」，**不需要先執行自動分析**。

1. 開啟「設定／預覽」、結果頁的「人工標註」，或已分析影片的複核視窗。
2. 在上方「人工事件標註」選擇事件類型，播放／逐幀找到目標後按「標記目前幀」；也可輸入原始 **1-based frame**，填寫備註，再按「儲存標註」（或在 frame 欄按 Enter）。
3. 「跳到標註」回到已儲存的事件幀；「清除標註」刪除目前選擇的人工事件。切換事件類型或關閉視窗前，請先按「儲存標註」提交手動輸入內容。

標註包含 frame、由 FPS 換算的時間、備註與更新時間，保存在專案的 `manual_events`；已有專案會自動存檔，新專案需按「儲存專案」。結果摘要與圖表以紫色標示人工事件，自動 First／Stable 仍獨立保留。修改偵測參數、套用設定及重跑分析均不會改寫人工標註；「複製為新區段」建立空白標註，避免把舊區段事件誤帶入。

可以標註分析區段外、但仍在原影片有效範圍內的幀。這類事件在結果摘要顯示「分析範圍外」，只有落在圖表目前範圍內的事件才畫線；跳幀預覽仍可使用。來源影片被更換時保留原標註並提示重新確認，不用新影片匯出舊標註截圖。

批次總表新增 `manual_first_detection_*`、`manual_stable_confirmation_*` 的 frame／timestamp／note／updated_at／status 欄位。另在 `<item_id>/manual_events/` 匯出 `manual_events.json` 與人工事件的完整原始 PNG；manifest 的 `event_source=manual` 識別人工事件。只有人工標註而尚無自動分析的項目，匯出狀態為 `manual_only`。人工標註不推算或捏造自動 bbox／rolling 數據。

### pairing.json 匯入

「匯入 pairing.json」支援 `runs[].cameraMatches` 中的場次／相機對應，使用 `runStartInVideoSec`／`runEndInVideoSec` 與 duration 換算分析起訖百分比。每個場次 × 相機建立獨立工作項目。

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
