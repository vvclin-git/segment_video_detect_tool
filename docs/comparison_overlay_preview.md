# GT／預測比較圖預覽操作

## 預覽事件

1. 載入設定並按「檢查配對與問題」。
2. 在「檢查結果與缺漏」清單選擇一筆評估事件，再按「預覽 GT／預測比較」。預覽會提示原圖、GT 或 Mask 缺漏／無效原因。
3. 每列色塊即時顯示該分類顏色。輸入 `#RRGGBB`，或按「選擇顏色…」開啟系統色彩選擇器。欄位錯誤會顯示設定路徑；相同顏色可以套用，但畫面會提示分類不易區分。
4. 拖曳 0–100% 滑桿或用數字欄位調整疊圖不透明度。顏色只混入 GT／預測區域，背景仍是原圖。不透明度 0% 會顯示原圖，介面會提示分類因此無法辨識。
5. 上方左右檢視原圖與目前設定比較圖。下方 30%、65%、100% 三張圖使用目前選定色彩；點選縮圖會把對應比例帶入數字欄位。
6. 「適合視窗」顯示完整影像；「100% 原尺寸」、放大／縮小、滑鼠滾輪與拖曳可檢視小目標。顯示縮放不會改變渲染尺寸。
7. 按「套用」將草稿帶回主畫面；按「取消」放棄這次編輯。按主畫面「儲存設定」才會寫入目前 JSON。主畫面顯示不透明度與三個色塊，未儲存時會標記。

預覽只載入所選事件的原圖、LabelMe JSON 與已對齊 Mask，並快取三個分類區域。調色／比例更新在約 100 ms 後重新混色，不執行整批驗證、重新計算 mIoU 或產生交付包。

## JSON 設定

設定沿用既有 schema version 1，在 `report` 中加入：

```json
{
  "schema_version": 1,
  "report": {
    "comparison_overlay": {
      "opacity": 0.65,
      "colors": {
        "overlap": "#1EEB5A",
        "gt_only": "#FF282D",
        "prediction_only": "#14D2FF"
      }
    }
  }
}
```

`opacity` 是有限數值，範圍 0–1；GUI 顯示百分比。顏色必須是六位 HEX，儲存時轉為大寫。省略任何欄位會只為該欄補上預設值；非法值不會自動修正，載入或檢查時會指出欄位。更改顏色或不透明度不改變 LabelMe／Mask 二值化、範圍或評估數值。

## 像素與輸出規則

GT 以 LabelMe polygon 填滿，座標取 `int(x), int(y)`；GT 與已對齊預測 Mask 使用 `>127`。分類依序是 `gt & pred`、`gt & ~pred`、`~gt & pred`，彼此互斥。每個區域使用相同公式 `original * (1-opacity) + color * opacity`，裁切至 0–255 並轉成 `uint8`；未分類背景維持原圖。

GUI 預覽、獨立比較 PNG、三圖 Collage、HTML 與 overview／run PDF 使用同一渲染函式。網頁只呈現交付時生成的固定圖片。Manifest 的 `comparison_overlay` 記錄 `mode`、`opacity` 與 `colors`；`internal/build_config.json` 保留可重現設定。

## 2026-09-22 資料驗證

獨立設定為 `delivery_config.20260922.comparison_validation.json`。預覽對照圖與正式交付包會放在 `deliveries/2026-09-22-comparison-validation/` 下，不覆寫既有的 9/22 驗收資料夾。指定的 Camera 2 Test 1 FirstDetection Frame 2321 影格使用 30%、65%、100% 對照圖。
