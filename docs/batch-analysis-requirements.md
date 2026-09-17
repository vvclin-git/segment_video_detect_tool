# 批次影片 Segmentation 分析工具 — 實作需求單

版本：v1.0 草案｜日期：2026-09-17｜用途：開發拆單與驗收依據

來源：[Batch segment analysis](chatgpt-conversation://6aab445e-e7c0-83e8-922d-4c4c942aa897) 的批次分析討論、使用者提供的結果列表參考圖與 pairing.json；另比對工作區的 app.py、README.md 與 detection_visualizer.html 檔案現況。

本文件以對話中較新的使用者決定為準。「已明確需求」指使用者明確提出的內容；「承接方案」指前次助手提出、尚未逐項確認的具體做法；「實作建議」是為可開發與可驗收而補充的規格，不代表已獲使用者確認。本次只產出需求文件。

## 1. 目標與範圍

讓使用者先針對多支影片個別設定偵測參數、穩定條件、分析範圍及 ROI，保存設定後批次分析；分析完成可在同一結果頁快速比較每支影片的事件與偵測狀態，進入單片複核，最後批次匯出數據與截圖。

主要流程：**匯入影片 → 逐支設定與預覽 → 保存專案 → 批次分析 → 結果總覽與事件複核 → 批次匯出**。

### 1.1 已明確需求

- 每支影片可獨立輸入 segmentation 偵測參數、穩定條件、分析開始百分比及 ROI。
- 可直接開啟影片預覽，並保存每支影片設定後批次分析。
- 需要知道首次檢出及首次穩定事件發生的 frame，以及該 frame 附近的 rolling state。
- 批次結果頁採每支影片一列，左侧為影片資訊／分析數據，右側為 Detection state 圖；可往下瀏覽多支影片。
- 保留簡單操作 UI，並可批次匯出全部影片結果及供人工判讀的事件截圖。
- 本期不需要計算距離，使用者自行查看事件截圖上的距離並人工記錄。

### 1.2 承接方案

- 三個主要畫面：逐支設定與預覽、批次分析、結果總覽。
- 分析範圍提供開始及結束百分比，連動顯示原始 frame 與秒數。
- 圖表呈現 Raw、Rolling rate、Stable；標記 First 與 Stable confirmation。
- 首次穩定採實際 confirmation frame，不回推；First bbox 與 Stable bbox 分開列出。
- 複製設定／套用至勾選影片、事件前後各 2 秒複核、圖表點擊跳幀。
- 批次背景依序執行、取消、失敗後繼續、重跑，以及重開專案檢視既有結果。
- 匯出總表、逐幀 CSV、設定 JSON、原始完整事件截圖及 ROI／bbox 標示圖。

### 1.3 本期不做

- 最大檢出距離、穩定最大檢出距離、自動 OCR 讀距離、GPS／logger 距離推算。
- Johnson criteria、FOV／實際目標尺寸估算、自動 GT overlap 判定。
- 模型訓練或模型推論；仍由 ECU 影片中的 segmentation 顏色擷取 mask。
- 多相機同步播放、雲端分析、多使用者協作未列入本期。

## 2. 現有基礎與新增工作

| 項目 | 工作區現況 | 本期工作 |
|---|---|---|
| 單片預覽 | Python／Tkinter／OpenCV；播放、逐幀、跳幀 | 納入多影片選取流程 |
| 色彩與 ROI | HSV、最小 blob 面積、拖曳 ROI、Hue histogram、三種預覽 | 各影片獨立保存、批次套用 |
| 穩定判定 | M-of-N、ON/OFF、連續 K 幀確認 | 抽離 UI，沿用語意並補上複核欄位 |
| 分析範圍 | 可指定 1-based 開始 frame | 增加起訖百分比與結束 frame |
| 事件 | First、sustained-stable start、confirmation | 主要 UI 聚焦 First 與 confirmation，保留既有欄位相容性 |
| 匯出 | 單片逐幀／摘要 CSV、事件 raw／mask PNG 與 manifest | 專案批次匯出、標示圖、狀態與設定追溯 |
| 視覺化 | 工作區已有獨立 HTML 視覺化檔案；對話提供圖表參考 | 整合每支影片一列的互動結果總覽 |
| 批次專案 | 尚缺完整多影片管理流程 | 新增專案、工作項目、持久化結果與執行佇列 |

技術框架尚未定案；本文件不將「網頁 UI」視為已確認需求。建議分析核心維持 Python，先決定沿用桌面介面或採本機 Web UI，再實作介面層。

## 3. 功能需求

以下 P0 為建議首版交付範圍；P1 為可後續交付項目，優先序屬實作建議。

| ID | 優先 | 功能與完成條件 |
|---|---|---|
| FR-01 | P0 | 建立、儲存、重新開啟專案；還原影片清單、各片設定、結果關聯及狀態 |
| FR-02 | P0 | 多選匯入本機影片；至少保留現有 MP4、AVI、MOV、MKV、M4V 支援，實際可解碼性由後端檢查 |
| FR-03 | P0 | 每個分析工作項目有唯一 ID、影片路徑、顯示名稱；場次／相機可選填。相同檔名不得造成設定或輸出互相覆蓋 |
| FR-04 | P0 | 個別編輯 HSV、最小 blob 面積、N／ON／OFF／K、ROI、分析起訖範圍；切換影片保留設定，顯示未保存狀態 |
| FR-05 | P0 | 提供複製上一支設定、套用至勾選影片；可選擇套用欄位。不同解析度的 ROI 不得直接默默套用 |
| FR-06 | P0 | 播放／暫停、前後逐幀、時間軸及直接輸入 frame 跳轉；Original／Mask／Overlay；原始像素座標 ROI 與 bbox |
| FR-07 | P0 | Hue histogram 使用 ROI 內通過 S/V 篩選的像素，顯示 H=0–179 與 H 門檻；隨預覽幀及設定更新 |
| FR-08 | P0 | 執行全部或勾選工作項目；顯示總體及單片進度、目前處理影片、已處理／預期幀數、成功／失敗數 |
| FR-09 | P0 | 批次執行期間 UI 可操作；可取消，單片失敗保留原因並繼續其他項目；取消／部分結果不可標成完成 |
| FR-10 | P0 | 結果頁每支影片一列，摘要及圖表並列；可篩選未檢出、未達穩定、失敗、結果過期，並勾選重跑 |
| FR-11 | P0 | 點 First／Stable 或圖表位置開啟預覽並精確跳幀；圖表游標、預覽幀與當幀數值同步 |
| FR-12 | P0 | 保存分析使用的參數快照；修改影響結果的設定後標記過期，舊結果仍以舊參數顯示且不可冒充新結果 |
| FR-13 | P0 | 匯出全部／勾選項目的摘要、逐幀 CSV、設定及事件圖，提供成功／缺項／失敗清單 |
| FR-14 | P1 | pairing.json 匯入：以場次與 cameraMatches 建立工作項目、解析影片對應並處理缺檔 |
| FR-15 | P1 | 只改穩定條件時由既有 raw 資料重算；HSV、面積、ROI 或分析範圍改變時重新分析。快取優化不可改變結果 |

## 4. 畫面與互動

### 4.1 逐支設定與預覽

- 左側：影片／工作項目清單、場次、相機、設定及分析狀態。
- 中央：影片預覽、播放與跳幀、ROI 框選、Original／Mask／Overlay。
- 設定區：HSV、Min blob area、N／ON／OFF／K、起訖百分比與換算的 frame／時間、Hue histogram。
- 操作區：保存設定、複製設定、套用至勾選影片、啟動批次分析。
- 已分析影片可開啟相應 Detection state 複核；調參預覽與既有結果必須標示各自使用的設定版本。

### 4.2 批次分析

- 每個項目顯示等待、執行中、完成、失敗或取消及進度。
- 提供取消批次；建議語意為停止目前工作並停止啟動待執行工作，已完成結果保留。
- 畫面能返回結果總覽查看已完成項目；執行中的設定以啟動時快照為準。

### 4.3 結果總覽（主要驗收畫面）

| 左側摘要 | 右側 Detection state |
|---|---|
| 影片名稱、場次／相機、分析起訖 | Raw、Rolling rate、Stable 三條序列 |
| First frame／時間／bbox W×H | First 與 Stable 垂直事件線 |
| Stable confirmation frame／時間／bbox W×H | X 軸為該影片原始 1-based frame；Y 軸固定 0–1 |
| 執行狀態、分析結果、過期提醒 | 預設全分析區段，可切換事件附近及調整範圍 |
| 預覽、First、Stable、修改設定／重跑 | Hover 顯示該幀詳情；點擊跳幀 |

- 每列為獨立影片結果，整頁垂直捲動；不同影片的 X 軸範圍清楚標示，不暗示已做時間同步。
- Hover／詳情顯示 frame、timestamp、Raw、rolling 命中數／有效視窗長度／比例、連續 ON 符合幀數、Stable、bbox W×H。
- 事件附近預設前後各 2 秒，超過分析邊界時截斷；可調整或恢復全分析區段。
- 無事件不畫事件線，事件按鈕停用並顯示原因；事件 frame 相同時仍可辨識兩種標記。
- 展開／關閉預覽後保留列表捲動位置；多列圖表建議依可視範圍載入，以避免大量影片拖慢 UI。

## 5. 判定與數值契約

### 5.1 幀與分析範圍

- UI、CSV、事件檔名統一使用原始影片 1-based frame；內部解碼索引為 frame−1。
- 分析區間兩端均包含，完整逐幀分析，不跳幀；開始時清空 rolling history、Stable 與連續計數。
- 所有首次事件均指「指定分析範圍內首次」，不得宣稱為全影片首次。
- 若分析第一幀 Raw=1，顯示「分析起點已檢出」。
- 固定 FPS 影片沿用 timestamp=(frame−1)/FPS；變動幀率及無效 FPS 的時間支援政策列為待確認，不得將估算時間偽裝成精確時間。
- 百分比換算建議：共 T 幀，start_index=floor(T×start_percent/100)，end_index_exclusive=ceil(T×end_percent/100)，範圍為兩者形成的半開區間；對外顯示 start_index+1 到 end_index_exclusive。要求 0≤start<end≤100，並檢查換算後至少一幀。0–100% 必須涵蓋全部影片。
- 百分比及換算後 frame 一起保存；執行快照中的 frame 邊界是重現分析的依據。

### 5.2 Raw detection 與 bbox

- 以 ROI 內的 HSV mask 做 connected components；至少一個 blob 面積 ≥ Min blob area 時 Raw=1。
- 以符合面積條件的最大 blob 作為本幀記錄對象，延續現行程式；selected_pixels 是 ROI 內通過 HSV 的像素數。
- bbox 的 x/y/w/h 及中心均使用原始影片座標，不能使用縮放後預覽座標。
- 無合格 blob 時 Raw=0。沿用逐幀 CSV 的面積與寬高 0、座標空值語意；UI 顯示「無 bbox」。新匯出建議增加 bbox_valid，避免把 0×0 當成實測尺寸。
- Stable=1 的當幀可能 Raw=0；事件 bbox 只能來自事件當幀，不得借用前後幀 bbox。

### 5.3 Stable detection

參數：window N、ON 命中門檻 M、OFF 命中門檻 O、連續確認幀數 K。沿用現行預設 N=5、M=4、O=1、K=3。驗證 N≥1、1≤M≤N、0≤O<M、K≥1。

1. 每幀將 Raw 加入最多 N 幀的視窗；rolling_hit_count 為命中數。
2. Rolling rate = hit_count / 實際已累積視窗長度；未滿 N 幀時顯示 warm-up，Stable 維持 0。
3. 尚未 Stable 且視窗已滿，若 hit_count≥M，ON streak 加一；否則歸零。
4. ON streak 達 K 的當幀才轉 Stable=1；K 是連續滿足 rolling ON，不是連續 K 幀 Raw=1。
5. 已 Stable 時，hit_count≤O 則在當幀退出，並重置 ON streak；介於 OFF 與 ON 之間仍維持 Stable。
6. 初次轉 Stable=1 的當幀為 First Stable confirmation，不回推。

既有 first_sustained_stable_start_frame 可保留在進階摘要；first_stable_detection_frame 繼續作為 confirmation 的相容別名，不得拿 sustained start 取代。

### 5.4 狀態與摘要

- 執行狀態與分析結論分欄：例如 execution_status=completed，detection_outcome=no_detection；過期另以 stale 標記。
- 結論至少包含「已達穩定」「有檢出但未達穩定」「未檢出」；失敗／取消不產生完整分析結論。
- 摘要保留 First、Stable confirmation、各自 timestamp 與 bbox；不存在的事件填空值，不填 frame=0。
- 延續既有 Raw detection rate、Stable coverage、longest consecutive miss、stabilization latency；分母為成功完成分析的有效幀數，latency 僅在兩事件皆存在時有效。
- 指定範圍內提早解碼失敗應標失敗或不完整，不得當成自然結束後標示完整成功。

## 6. 保存與匯出

### 6.1 最小保存資料（實作建議）

| 實體 | 必要資料 |
|---|---|
| Project | schema_version、project_id、名稱、建立／更新時間、工作項目集合 |
| AnalysisItem | item_id、影片路徑與識別資訊、場次／相機／區段標籤、FPS／解析度／總幀數、目前設定 |
| Settings | HSV 上下限、Min area、ROI 或全畫面、起訖百分比與 frame、N／M／O／K |
| AnalysisRun | run_id、item_id、程式／演算法版本、設定快照、影片識別快照、執行狀態與錯誤、摘要及逐幀結果位置 |
| FrameResult | 現有逐幀欄位，加 rolling_hit_count、rolling_window_length、window_ready、on_qualifying_streak（未 Stable 時的候選計數）、bbox_valid |

- 設定與分析快照分開保存；重跑產生新的 run_id，避免覆蓋後無法辨識結果來源。
- 預覽／事件圖匯出必須使用該次 run 的參數；來源影片已更動或找不到時明確提示，支援重新指定路徑並驗證來源。
- 單支影片多區段建議以獨立 item_id 表達；是否在首版開放 UI 編輯多區段列為待確認。

### 6.2 匯出內容

| 檔案 | 內容 |
|---|---|
| batch_summary.csv | 每個工作項目一列；影片／場次／相機、分析範圍、狀態／錯誤／stale、First 與 confirmation 的 frame／時間／bbox、統計、run_id |
| 各項目 frame_analysis.csv | 每個分析幀一列，含偵測、rolling、stable、bbox 及複核欄位 |
| 各項目 settings.json | 當次完整設定及來源影片／分析版本資訊 |
| first_detection_*_raw.png | 首次檢出事件的完整原始畫面，不能裁切掉畫面距離資訊 |
| first_stable_confirmation_*_raw.png | 首次穩定確認事件的完整原始畫面 |
| 對應 annotated.png | 另存 ROI 與當幀 bbox 標示圖，不修改 raw PNG |
| event_images_manifest.csv | item_id／run_id、事件類型、frame／時間、bbox、輸出路徑及匯出狀態／原因 |

- 可保留既有 mask PNG 與 sustained-start 事件作額外相容輸出，主要交付仍以兩個事件為準。
- 同一 frame 同時發生 First 與 Stable 時，manifest 保留兩筆事件關係。
- 無事件不產生假截圖；摘要及 manifest 說明未檢出／未達穩定。
- 過期、未分析、取消或失敗項目不得靜默省略；摘要列出狀態。過期結果若匯出須保留 stale 與原 run_id。
- 輸出資料夾建議包含批次匯出 ID、item_id、run_id，避免同名影片或重跑互相覆蓋；CSV 使用可正常顯示繁體中文的 UTF-8 編碼。

## 7. 驗收案例

| ID | 情境 | 預期結果 |
|---|---|---|
| AC-01 | 匯入三支影片，分別設定 ROI／HSV／穩定條件，保存後重開 | 每支設定及分析範圍正確還原，互不串用 |
| AC-02 | 同一影片分析 0–100% 與指定部分範圍 | 全段包含首尾；部分區段保留原始 frame 編號，起點重新累積 rolling |
| AC-03 | N=5、M=4、O=1、K=3；Raw=[1,1,1,1,0,1,1] | First=1；frame 5/6/7 皆符合 ON，confirmation=7，sustained start=5；frame 5 Raw=0 不影響 ON 合格 |
| AC-04 | 接 AC-03，再輸入 [0,0,0,0] | frame 10 命中 2/5 仍 Stable；frame 11 命中 1/5 當幀退出 |
| AC-05 | ON streak 未滿 K 即低於 M，或整段不足 N 幀 | 不產生 Stable 事件；前者候選计數重置，後者標示 warm-up |
| AC-06 | 全部 Raw=0／有 Raw 但未達穩定 | 分別顯示未檢出／未達穩定；不存在事件留空，按鈕停用 |
| AC-07 | N=5、M=4、O=1、K=1；Raw=[1,1,1,1,0] | confirmation=5 且當幀無 bbox；不得沿用 frame 4 的 bbox |
| AC-08 | 點 First、Stable 或圖表某個 frame | 預覽、游標、數值與原始影片同一幀；事件放大範圍依邊界截斷 |
| AC-09 | 多片結果頁 | 每片一列、左右摘要與圖表並列、三條序列與事件線清楚，First／Stable bbox 分列 |
| AC-10 | 執行中取消；其中一片缺檔或解碼失敗 | UI 有回應，取消不標完成；單片失敗保留原因且其他影片可繼續 |
| AC-11 | 已分析後修改 HSV／ROI／穩定條件 | 標示結果過期；既有結果与截圖仍對應原參數快照，重跑後才更新 |
| AC-12 | 匯出含同名影片、無事件及失敗項目的批次 | 無檔案覆蓋；總表不漏項；事件原圖、標示圖與 manifest 可互相核對 |
| AC-13 | 單片工具與新核心輸入同影片、同範圍、同參數 | Raw／rolling／Stable／事件 frame 一致，確認核心抽離未改變既有語意 |

## 8. 建議開發拆單

| 工作單 | 內容 | 依賴 | 驗收 |
|---|---|---|---|
| T1 | 抽離無 UI 的分析核心、範圍與穩定狀態契約 | 無 | AC-02～07、13 |
| T2 | 專案／工作項目／設定／run 保存、版本與過期判斷 | T1 資料契約 | AC-01、11 |
| T3 | 多影片設定與預覽、ROI、起訖百分比、設定套用 | T2 | AC-01、02 |
| T4 | 背景批次佇列、進度、取消與失敗隔離 | T1、T2 | AC-10 |
| T5 | 結果列表、互動 Detection state、事件跳轉複核 | T3、T4 | AC-08、09 |
| T6 | 批次總表、逐幀 CSV、設定與事件圖匯出 | T2、T4 | AC-11、12 |
| T7 | 整體回歸、操作說明、以實際影片驗收 | T1～T6 | AC-01～13 |
| T8 | pairing.json 匯入、stable-only 快取重算 | 首版後或另排 | 另訂匯入及快取一致性案例 |

不先承諾人日；UI 技術方案、批次規模與實際影片資料確認後再估工。

## 9. 待確認決策（不阻擋本需求單交付）

| 決策 | 現有證據與建議 |
|---|---|
| UI 技術 | 使用者要 viewer 與簡單 UI，未明確指定新工具框架；需選沿用桌面或本機 Web UI |
| pairing.json 是否首版必要 | 附件有 26 筆 runs，含 cameras、videoCatalog、cameraMatches；其完整匯入規則未定，不能把 26 場直接視為 26 支影片。建議先支援手動多選，再納入配對匯入 |
| 一片多場次／多區段 | 前次助手建議「場次 × 相機 × 區段」，使用者未逐項確認；資料層可預留，首版 UI 是否開放需決定 |
| 百分比邊界規則 | 本文件提供可重現換算建議；需在實作前固定，避免相鄰 frame 的認知差異 |
| 不同解析度 ROI 複製 | 建議保留其他設定並要求重新框 ROI；若要比例縮放，需另訂規則並明示結果 |
| 時間與 FPS | 固定 FPS 沿用現行公式；VFR 是否支援及是否以解碼 timestamp 為準待定 |
| 大批次規模 | 典型片數、單片長度／解析度、可接受分析時間與記憶體尚未提供；效能數字應以實際資料訂定 |
| 人工距離紀錄 | 已確認由使用者人工查看截圖；是否另加手填距離／複核註記欄位未明確要求，不列必做 |

首版完成標準：使用者可完成「逐支設定保存 → 批次分析 → 每片一列比較 → 點事件複核 → 全部匯出」流程；事件 frame、rolling 判定及截圖來源一致且可追溯。

## 10. 實作交付紀錄（2026-09-17）

- 已實作 FR-01～FR-15；包含原列 P1 的 pairing.json 匯入與 stable-only 快取重算。
- UI 採本機 Tkinter 桌面；`uv run python app.py` 預設批次版，`--single` 保留既有單片版。
- 開放同一影片複製為多區段，使用獨立 item_id；ROI 僅自動複製至同解析度影片。
- 採本文件百分比半開索引換算規則；时间使用 reported FPS 名目換算，未實作 VFR PTS 精確時間。
- 結果頁每片一列；另在複核視窗的影片下方放置互動 Detection state 圖，游標／影片／幀詳情同步。
- 設定、結果與事件圖皆保留 run_id 及當次參數。專案 JSON 與 assets 需保留；目前結果檔案路徑為絕對路徑。
- 已通過 17 項自動測試：包含既有穩定判定回歸、真實編碼測試影片、匯出原圖逐像素比對、專案重開、缺檔／提早解碼失敗／取消處理，以及 Tk 桌面完整操作流程。
- 已用 `uv run --offline` 檢查批次與舊單片兩個入口可建立視窗。環境無法擷取桌面截圖，介面以 Tk widget 尺寸、互動與事件同步測試檢查；未宣稱完成螢幕截圖視覺驗收。
- 尚未取得正式 ECU 影片；實際編碼相容性、長片效能及人工確認事件的驗收需使用正式資料。距離自動計算與人工距離欄位仍不列入本期。

操作方式與輸出結構見 [README](../README.md)。

## 11. 人工事件標註增補（2026-09-17）

- 每個影片工作項目可獨立保存人工首次檢出與人工穩定確認 frame，採原始 1-based 幀號；支援目前幀標記、手動輸入、備註、修改、跳轉及清除。
- 人工事件與自動判定分開保存，不改寫自動 First／Stable，不影響偵測設定的過期判斷；分析重跑不刪除人工事件。
- 尚未分析也可標註、保存及匯出；允許標記分析區段之外的有效影片 frame，超出影片範圍的輸入需拒絕。
- 結果摘要顯示人工事件與時間；圖表以紫色事件線表示目前顯示範圍內的人工事件。
- 批次總表、事件 manifest、標註 JSON 與原始完整事件截圖均納入人工事件；不捏造人工事件的自動偵測數據。
- 來源影片識別不符時保留標註但提示失效，拒絕匯出該事件的新影片截圖；重開舊專案時補上空白標註欄位。
- 增補驗收：首次／末幀及非法 frame、無自動分析的標註保存與 PNG 匯出、人工／自動不同事件及分析範圍外 frame、重跑保留、來源更換／缺檔、舊專案相容，以及 GUI 標記／修改／跳轉／清除與圖表同步。
