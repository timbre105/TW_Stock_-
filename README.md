# 0050 / 00631L 即時參考儀表板(網頁版)

## 安裝
```bash
pip install -r requirements.txt
```

## 設定環境變數
複製 `.env.example` 為 `.env`,填入:
- `FINMIND_API_TOKEN`:FinMind 會員 token
- `LINE_CHANNEL_ACCESS_TOKEN`:LINE Developers Console -> Messaging API 頁籤 -> 「Channel access token (long-lived)」按 Issue 取得
  (**不是** Channel secret)
- `LINE_NOTIFY_ENABLED`:true / false,不想被推播就設 false

Windows PowerShell 範例:
```powershell
$env:FINMIND_API_TOKEN="你的token"
$env:LINE_CHANNEL_ACCESS_TOKEN="你的line token"
```

## 執行
```bash
streamlit run app.py
```
瀏覽器會自動開啟 http://localhost:8501,同一個區域網路內的手機也可以連
(用電腦的區網 IP,例如 http://192.168.1.5:8501,前提是防火牆有開)。
若要在外面用手機看,需要另外做「對外連線」(例如用 Streamlit Community Cloud
部署,或用 Tailscale/ngrok 之類的工具打通),這部分如果你有需要我可以再幫你做。

## LINE 推播怎麼收到
1. 用手機掃這個 LINE Channel 的 QR code,加為好友。
2. 訊號從「非進場/出場」變成「進場」或「出場」時,程式會自動推播一次,
   不會每次刷新都發。

## 交易紀錄
在「交易紀錄」頁籤手動輸入你實際的買賣,程式用移動平均成本法幫你算出
目前均價、未實現損益,存在 `data/trades.csv`,關掉程式也不會不見。

## 檔案結構
```
app.py            # Streamlit 主程式(執行這個)
data_fetch.py     # FinMind + 證交所即時報價
strategy.py       # 分批進場策略邏輯
notify.py         # LINE 推播
trades.py         # 交易紀錄讀寫
data/             # 交易紀錄、上次訊號狀態存在這裡
```

## 限制提醒
- 三大法人資料收盤後才更新,不是即時。
- 成交量/法人金額為估算值,非交易所公告精確數字。
- 不會自動下單,不構成投資建議。
- 證交所即時行情端點是非官方接口,盤中偶爾被擋屬正常現象。
