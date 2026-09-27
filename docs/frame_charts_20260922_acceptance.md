# 2026-09-22 離線逐幀圖表驗收記錄

交付包：`C:\Workplace\segment_video_detect_tool\deliveries\2026-09-22-frame-chart-validation\delivery_20260927_104749_09c78929`
設定：`delivery_config.20260922.validation.json`。來源分析專案與 `.assets` 只讀；沒有重跑分析，也沒有修改來源專案。`run_data/sea-trial-2026-09-22.csv` 未作為影片或逐幀來源。

## 驗證結果

- CLI 驗證通過；評估事件 110 筆、原圖 110/110、GT JSON 110/110、Mask 110/110。
- Camera 1 分析項目 28 個，latest-run 逐幀資料 28/28 有效；建置 28 份 `analysis/<sequence-id>.js`。56 筆 First／Stable 評估事件引用 28 個唯一序列索引。
- 逐列比對交付包 21,573 個 Frame row 與各自 `frames.json`，Frame、timestamp、Raw、Rolling、Stable、像素／bbox 及 `window_ready` 完全一致。
- Project 完整性清單保留 28 個分析項目。Camera 2 沒有此 Project 的逐幀項目或打包影片；明細顯示缺漏，不借用 Camera 1。
- PDF overview、PDF run、評估 CSV 均有輸出；CSV 欄位與 PDF 產生器未更動。

## 28 組 latest-run 序列

| 相機 | Test | Phase | Item ID | Analysis run ID | 分析範圍 | 筆數 | First | StableStart | StableConfirmation | 狀態 |
|---|---:|---|---|---|---:|---:|---:|---:|---:|---|
| Camera 1 | 1 | P1 | `1be61d6783b647369ff30642a38ab834` | `04cbdaf19008460bae0f84e0223d3c40` | F2026–F3345 | 1320 | 2141 | 2309 | 2318 | available |
| Camera 1 | 1 | P2 | `59ea4e3fb147495aa6e7c82b6502d712` | `b4676f4b16374df699e5112f02cf086f` | F952–F1572 | 621 | 1025 | 1189 | 1198 | available |
| Camera 1 | 1 | P3 | `b788773085544808acd9381fc8a867c8` | `0f50aff3b9c549e091768c820c6c47c2` | F708–F1168 | 461 | 716 | 846 | 855 | available |
| Camera 1 | 1 | P4 | `96499535e43045bcb9dc9047c616a7b6` | `840e537501144995884db251dd6bfbb4` | F529–F873 | 345 | 573 | 623 | 632 | available |
| Camera 1 | 2 | P1 | `d0012e9e39984fa5bacecf4e73546687` | `6f90c39000374c6c809775b11ea414d7` | F5265–F7346 | 2082 | 5441 | 5637 | 5646 | available |
| Camera 1 | 2 | P2 | `c3b94a69fac64109b768d23e2548713f` | `cd0c4136529f4322abda4166f96cd4dd` | F2538–F3540 | 1003 | 2658 | 3283 | 3292 | available |
| Camera 1 | 2 | P3 | `3d9c265ddc924ad2a95f4a0f27d0145c` | `3a132c92c3a242faa2ad89ffacd3badb` | F1918–F2676 | 759 | 2365 | 2591 | 2600 | available |
| Camera 1 | 2 | P4 | `f483fd3fb24e4b7398434d138bcfa649` | `17453cc4e7f84db58c2ef24e758d30b8` | F1464–F2281 | 818 | 1731 | 2079 | 2088 | available |
| Camera 1 | 3 | P1 | `857fccb1512445ff9076b0bb4e1d28bc` | `a2b86969ecce4d46b734f71e93a4736d` | F1499–F2092 | 594 | 1536 | 1761 | 1770 | available |
| Camera 1 | 3 | P2 | `b3896d8afec5447c9e1304a108b1c3ce` | `4e7fb826788b456ba309107a5a8b5003` | F1073–F1497 | 425 | 1203 | 1415 | 1424 | available |
| Camera 1 | 3 | P3 | `ad63ca226cb24f7587d9c837b97d2d02` | `eb5f7fba7dfa4b1087a5aff187eec39c` | F625–F872 | 248 | 641 | 812 | 821 | available |
| Camera 1 | 3 | P4 | `f6c67ade67ff426fba1bee6c1944f7d4` | `0003e2b00c3b458390c05a0e04ddde08` | F550–F767 | 218 | 567 | 613 | 622 | available |
| Camera 1 | 4 | P1 | `3df274c5c0f64a5b8f1485ad9bfa608d` | `38b3881cff0848abafb4c41e62a65336` | F2438–F3220 | 783 | 2439 | 2697 | 2706 | available |
| Camera 1 | 4 | P2 | `5a8539d90a4d4b54b2e2cba562e0be86` | `74e7260153b4487d87fec5d7a60580cb` | F1011–F1769 | 759 | 1069 | 1136 | 1145 | available |
| Camera 1 | 4 | P3 | `615de78f1f5346ba9456ccc3f35cdcf5` | `11105cbb8f7b4a5386b482b5ef6f4e4c` | F916–F1210 | 295 | 955 | 1057 | 1066 | available |
| Camera 1 | 4 | P4 | `45ce287dafd54eaea48bfe3e40785844` | `6763a98060d7402498666de0c8daef2f` | F302–F917 | 616 | 386 | 688 | 697 | available |
| Camera 1 | 5 | P1 | `695b980965f14aaaa80d19acfaafc943` | `7d1100e5862d41bc8e945d80320a2b7c` | F2128–F3990 | 1863 | 2128 | 2166 | 2175 | available |
| Camera 1 | 5 | P2 | `76c70de9da144e84b200b8c572bebf33` | `10fce589094b495788287e14e94aa2ce` | F1268–F2378 | 1111 | 1353 | 1874 | 1883 | available |
| Camera 1 | 5 | P3 | `05e85a74363e4e5298a7a8222036865c` | `c4d95a08891b419fa5cebc8c0e4da6aa` | F800–F1499 | 700 | 1195 | 1336 | 1345 | available |
| Camera 1 | 5 | P4 | `58272d6ac6a14319bb37ae43133c8ee1` | `09fe944bfe6f4d359581a4271f5fd3c4` | F1165–F1588 | 424 | 1179 | 1360 | 1369 | available |
| Camera 1 | 6 | P1 | `9153a0abcfb440ac8b27da63e3a148f1` | `f7da90c7f9c74be38a3c502b76a37f69` | F1177–F1782 | 606 | 1290 | 1587 | 1596 | available |
| Camera 1 | 6 | P2 | `ff701183c293442f861536ca3e7c7cc2` | `4ac43020af4249a99bfc8ae7e326e67b` | F773–F1424 | 652 | 794 | 1131 | 1140 | available |
| Camera 1 | 6 | P3 | `ad1d0c150381422eb9196947487fa6f9` | `a1c63f1acebb4cdda3ace47c7529ee8b` | F504–F1007 | 504 | 531 | 721 | 730 | available |
| Camera 1 | 6 | P4 | `94020fb6d5bc4ea3bc74f3b34e6b782c` | `83b93dc6e6ec4788b960a44ceb8dceb0` | F281–F744 | 464 | 307 | 469 | 478 | available |
| Camera 1 | 7 | P1 | `11993cf2c608455483b802e3fc547156` | `a354bc9ebd184b519dd6049a4b396c7d` | F3674–F5102 | 1429 | 3783 | 3971 | 3980 | available |
| Camera 1 | 7 | P2 | `dbadf767c43141248a7a34b2ac28e23a` | `53c60650bf234de6b12a8c86d51eb5bd` | F1421–F2537 | 1117 | 1493 | 2190 | 2199 | available |
| Camera 1 | 7 | P3 | `cd11408a3273483d811a5a03c4496c47` | `ef1d113e98fe4a509ff959cea6bf1bd5` | F982–F1754 | 773 | 1223 | 1300 | 1309 | available |
| Camera 1 | 7 | P4 | `3512909da7e047abb973e470ad49581b` | `321fed8521ba4042a1dfee93aa38788f` | F742–F1324 | 583 | 1088 | 1218 | 1227 | available |

指定驗收值符合：Test 1 A／P1 Camera 1 為 First F2141、StableStart F2309；Test 1 B／P2 Camera 1 為 First F1025、StableStart F1189。

## 影片檔與事件影格解碼

以下為來源檔案大小及 project sampled SHA-256 核對；使用既有 `ExactVideoCapture` 成功解碼列出的 1-based Frame。Frame hash 為解碼影像 SHA-256 前 16 碼。

| 影片 | 大小（bytes） | Project sampled SHA-256 | 解碼 Frame（Frame hash） |
|---|---:|---|---|
| `Marine_2026-09-22T05-56-20-706Z.mp4` | 149,085,044 | `3537995885f165f5dda584bb412057a7378345ec7ed513554b6197c2ee1498ec` | F2141 (`179f198aaffc5612`)、F2309 (`c991a3a591801c36`) |
| `Marine_2026-09-22T06-00-43-681Z.mp4` | 69,700,652 | `57bd1136ea55eeb8dbdf10346652535f13afdcd3fc13fdd8eac25821953fb541` | F1025 (`7c18ab5a38649427`)、F1189 (`a6a169cf06d4b78f`) |

**瀏覽器播放檢查尚未完成。** 嘗試在 Edge 以 `file://` 開啟這份交付包時，瀏覽器使用政策拒絕了該導航，並要求不可透過其他瀏覽器介面或間接方式繞過。故兩支影片在瀏覽器中的 metadata、播放游標、名目 seek 與編碼支援結果均未驗證；上表是本機精確影格解碼結果，不代表瀏覽器播放測試。

## Camera 2 待補驗收

目前沒有 Camera 2 對應的分析專案、latest-run `frames.json` 或配對影片。補齊來源後，需重新驗證 Camera 2 序列來源與索引、逐幀值、其影片 Frame 解碼，以及瀏覽器內播放、事件定位和播放游標。

## 自動化回歸

`python -m unittest discover -v`：46 tests passed。新增 `tests/test_frame_analysis.py` 覆蓋舊設定預設、根目錄優先順序、缺檔回退、命中損壞檔不回退、同名專案識別、單點／無 FPS、亂序／缺 Frame／範圍不符／無效數值與安全資料序列化。`node --test tests/chart_math.test.js`：2 tests passed，驗證大量序列的單幀 Raw／Stable 脈衝與範圍極值保留；`node --check` 對報告前端腳本通過。
