"""0050 / 00631L 即時參考儀表板 — 網頁版 (Streamlit)

跟桌面版 (matplotlib) 的差異:
    - 用瀏覽器打開,手機也能連(部署方式見下方 README)。
    - 訊號從「灰→綠(進場)」或「進場中→紅(出場)」變化時,
      會自動用 LINE 官方帳號推播提醒(broadcast,見 notify.py 說明)。
    - 多了「交易紀錄」頁籤,可以手動記錄你實際的買賣,程式會幫你算均價、
      未實現損益,跟策略理論部位對照參考。

執行:
    export FINMIND_API_TOKEN="..."
    export LINE_CHANNEL_ACCESS_TOKEN="..."   # 不設定的話推播功能會自動停用,程式照常運作
    streamlit run app.py

⚠️ 提醒 (跟桌面版相同的限制,沒有因為換成網頁版而消失):
    - 三大法人資料是收盤後才更新,不是即時的。
    - 成交量/法人金額都是估算/換算值,不是交易所公告的精確數字。
    - 這是決策參考工具,不會自動下單,不構成投資建議,金額與進出場的
      最終判斷權在你自己。
    - 證交所即時行情端點是非官方接口,盤中偶爾被擋是正常現象。
"""
import os
import json
import datetime as dt

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from data_fetch import fetch_history, fetch_realtime_quotes, is_market_hours, TAIPEI_OFFSET
from strategy import replay_strategy, evaluate_signal, build_buy_sell_table, TRANCHES
from notify import send_line_broadcast, notify_enabled
import trades as trades_mod

REFRESH_SECONDS = 30
HISTORY_TTL = 4 * 3600
STATE_PATH = os.path.join(os.path.dirname(__file__), "data", "last_signal.json")

# 圖表可選的顯示區間(天數,依日曆天數往回篩,不是交易日數)。
# 「全部」用 None 代表不篩選,顯示 history 裡的所有資料。
CHART_RANGE_OPTIONS = {
    "1個月": 30,
    "3個月": 90,
    "6個月": 180,
    "1年": 365,
    "全部": None,
}
CHART_RANGE_DEFAULT = "3個月"

st.set_page_config(page_title="0050 / 00631L 即時參考儀表板", layout="wide")

# ------------------------- 手機版排版:螢幕寬度 <= 768px 時,把左右並排的欄位
# (0050 / 00631L 圖表、交易紀錄表單、持倉摘要)自動改成上下堆疊,避免在手機
# 瀏覽器上被壓成太窄看不清楚。純 CSS media query,不需要額外套件或 JS 偵測。
st.markdown("""
<style>
@media (max-width: 768px) {
  div[data-testid="stHorizontalBlock"] {
    flex-direction: column !important;
  }
  div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
    width: 100% !important;
    flex: 1 1 100% !important;
    min-width: 100% !important;
  }
}
</style>
""", unsafe_allow_html=True)

# 部署到 Streamlit Community Cloud 時,token 存在 st.secrets 裡而不是環境變數,
# 這裡統一把 st.secrets 的值灌進 os.environ,本地 run.bat 跟雲端部署就能共用
# 同一套 data_fetch.py / notify.py,不用寫兩份邏輯。
for _key in ["FINMIND_API_TOKEN", "LINE_CHANNEL_ACCESS_TOKEN", "LINE_NOTIFY_ENABLED"]:
    if not os.environ.get(_key) and _key in st.secrets:
        os.environ[_key] = str(st.secrets[_key])


# ------------------------- 快取抓取(避免每30秒重打一堆API) -------------------------
@st.cache_data(ttl=HISTORY_TTL, show_spinner="抓取歷史資料中...")
def cached_history():
    return fetch_history()


def load_last_signal_key():
    if not os.path.exists(STATE_PATH):
        return None
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("notify_key")
    except Exception:
        return None


def save_last_signal_key(key):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"notify_key": key, "updated_at": dt.datetime.now().isoformat()}, f)


def filter_by_days(history, days):
    """依日曆天數篩選最近的資料;days 為 None 時回傳全部。"""
    if days is None:
        return history
    cutoff = history["date"].max() - pd.Timedelta(days=days)
    return history[history["date"] >= cutoff]


def build_stock_panel(code, price_col, recent, live_points, rolling_peak, entries,
                       vol_col, today_vol, amt_col, last_row, foreign_col, trust_col,
                       dealer_col, last_date):
    """把單一檔股票的價格/成交量/三大法人 3 張圖合併成一個直向 subplot,
    並用 shared_xaxes 讓 X 軸(日期範圍)彼此同步縮放/平移。"""
    avg_vol = recent[vol_col].mean()
    inst_title = f"三大法人買賣超(約,萬元)"
    if last_row is not None:
        f_v = last_row[foreign_col] / 1000
        t_v = last_row[trust_col] / 1000
        d_v = last_row[dealer_col] / 1000
        inst_title += f" — 最新({last_date}) 外資{f_v:+.0f}張 投信{t_v:+.0f}張 自營{d_v:+.0f}張"
    else:
        inst_title += " — 尚無法人資料"

    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.05,
        row_heights=[0.5, 0.25, 0.25],
        subplot_titles=(code, f"成交量(張)" + (f"　今日累積 {today_vol:,.0f}張" if today_vol is not None else ""),
                         inst_title),
    )

    # ---- row 1: 價格 ----
    fig.add_trace(go.Scatter(x=recent["date"], y=recent[price_col], mode="lines",
                              name="收盤(歷史)", line=dict(color="steelblue", width=1)),
                  row=1, col=1)
    if live_points:
        xs, ys = zip(*live_points)
        fig.add_trace(go.Scatter(x=list(xs), y=list(ys), mode="lines+markers",
                                  name="今日即時", line=dict(color="red", width=2), marker=dict(size=4)),
                      row=1, col=1)
    if rolling_peak is not None:
        fig.add_hline(y=rolling_peak, line_dash="dot", line_color="black", opacity=0.6,
                       annotation_text="歷史高點", row=1, col=1)
        for thresh, frac in TRANCHES:
            level = rolling_peak * (1 + thresh / 100)
            fig.add_hline(y=level, line_dash="dash", line_color="gray", opacity=0.5,
                           annotation_text=f"{thresh}%", row=1, col=1)
    if entries:
        ex = [e[0] for e in entries if e[0] >= recent["date"].iloc[0]]
        ey = [e[1] for e in entries if e[0] >= recent["date"].iloc[0]]
        if ex:
            fig.add_trace(go.Scatter(x=ex, y=ey, mode="markers", name="進場點",
                                      marker=dict(color="green", size=10, symbol="triangle-up")),
                          row=1, col=1)

    # ---- row 2: 成交量 ----
    fig.add_trace(go.Bar(x=recent["date"], y=recent[vol_col], name="成交量(張)",
                          marker_color="slategray", showlegend=False),
                  row=2, col=1)
    fig.add_hline(y=avg_vol, line_dash="dash", line_color="orange",
                   annotation_text=f"均量 {avg_vol:,.0f}張", row=2, col=1)

    # ---- row 3: 三大法人買賣超 ----
    colors = ["seagreen" if v >= 0 else "crimson" for v in recent[amt_col]]
    fig.add_trace(go.Bar(x=recent["date"], y=recent[amt_col], marker_color=colors,
                          name="買賣超(萬元,估)", showlegend=False),
                  row=3, col=1)
    fig.add_hline(y=0, line_color="black", opacity=0.6, row=3, col=1)

    fig.update_layout(height=680, margin=dict(l=30, r=10, t=40, b=20),
                       legend=dict(orientation="h", y=1.06, x=0),
                       hovermode="x unified")
    return fig


def main():
    st_autorefresh(interval=REFRESH_SECONDS * 1000, key="auto_refresh")

    try:
        history = cached_history()
    except Exception as e:
        st.error(f"歷史資料抓取失敗:{e}\n請確認 FINMIND_API_TOKEN 是否正確設定。")
        st.stop()

    status = replay_strategy(history)

    quotes = fetch_realtime_quotes() if is_market_hours() else None
    if quotes and "0050" in quotes:
        p50_now, change50 = quotes["0050"]["price"], quotes["0050"]["change_pct"]
        vol50_today = quotes["0050"]["volume_lots"]
    else:
        p50_now, change50, vol50_today = history.iloc[-1]["p50"], 0.0, None
    if quotes and "00631L" in quotes:
        p631_now, change631 = quotes["00631L"]["price"], quotes["00631L"]["change_pct"]
        vol631_today = quotes["00631L"]["volume_lots"]
    else:
        p631_now, change631, vol631_today = history.iloc[-1]["p631"], 0.0, None

    if "live_points_50" not in st.session_state:
        st.session_state.live_points_50 = []
        st.session_state.live_points_631 = []
    now = dt.datetime.now(TAIPEI_OFFSET)
    if quotes:
        st.session_state.live_points_50.append((now, p50_now))
        st.session_state.live_points_631.append((now, p631_now))

    hist_peak = history["p50"].max()
    rolling_peak = max(hist_peak, p50_now)
    dd_now = (p50_now / rolling_peak - 1) * 100

    signal = evaluate_signal(status, dd_now, p631_now)

    # ---- 訊號變化才推播,避免每30秒轟炸 LINE ----
    notify_msg = None
    if notify_enabled() and signal["kind"] in ("entry", "exit"):
        last_key = load_last_signal_key()
        if last_key != signal["notify_key"]:
            body = (f"[0050/00631L 策略提醒]\n{signal['text']}\n"
                    f"0050 現價 {p50_now:.2f} ({change50:+.2f}%)  回撤 {dd_now:.2f}%\n"
                    f"00631L 現價 {p631_now:.2f} ({change631:+.2f}%)")
            ok, msg = send_line_broadcast(body)
            notify_msg = msg
            save_last_signal_key(signal["notify_key"])
    else:
        # 狀態回到 hold/idle 時也更新 key,這樣下次再變 entry/exit 才會被視為「新訊號」
        last_key = load_last_signal_key()
        if last_key != signal["notify_key"] and signal["kind"] in ("hold", "idle"):
            save_last_signal_key(signal["notify_key"])

    st.title("0050 / 00631L 即時參考儀表板")
    status_line = "🟢 盤中" if is_market_hours() else "⚪ 非交易時間(顯示最後收盤資料)"
    st.caption(f"{now.strftime('%Y-%m-%d %H:%M:%S')}　{status_line}　每 {REFRESH_SECONDS} 秒自動刷新")

    tab_dash, tab_trades, tab_help = st.tabs(["📊 儀表板", "📒 交易紀錄", "ℹ️ 設定說明"])

    with tab_dash:
        color_map = {"green": "success", "orange": "warning", "red": "error", "gray": "info"}
        sig_col, btn_col = st.columns([5, 1])
        with sig_col:
            getattr(st, color_map.get(signal["color"], "info"))(signal["text"])
        with btn_col:
            st.write("")  # 對齊用
            if st.button("📤 手動推播", use_container_width=True,
                         disabled=not notify_enabled(),
                         help="不管訊號有沒有變化,立刻把目前股價/回撤/訊號推到 LINE"):
                manual_body = (
                    f"[手動查詢] {now.strftime('%Y-%m-%d %H:%M')}\n"
                    f"0050 現價 {p50_now:.2f} ({change50:+.2f}%)  回撤 {dd_now:.2f}%\n"
                    f"00631L 現價 {p631_now:.2f} ({change631:+.2f}%)\n"
                    f"{signal['text']}"
                )
                ok, msg = send_line_broadcast(manual_body)
                if ok:
                    st.toast("已推播到 LINE ✅", icon="✅")
                else:
                    st.toast(f"推播失敗: {msg}", icon="⚠️")
        if not notify_enabled():
            st.caption("⚠️ 尚未設定 LINE_CHANNEL_ACCESS_TOKEN,手動推播按鈕暫時無法使用。")
        if notify_msg:
            st.caption(f"LINE 自動推播狀態: {notify_msg}")

        range_choice = st.radio(
            "圖表顯示區間", list(CHART_RANGE_OPTIONS.keys()),
            index=list(CHART_RANGE_OPTIONS.keys()).index(CHART_RANGE_DEFAULT),
            horizontal=True, key="chart_range",
        )
        chart_days = CHART_RANGE_OPTIONS[range_choice]
        recent = filter_by_days(history, chart_days)

        if recent.empty:
            st.warning(f"「{range_choice}」區間內沒有資料,改用全部歷史資料顯示。")
            recent = history

        c1, c2 = st.columns(2)

        last_row = history.dropna(subset=["inst_total_50"]).iloc[-1] if not history["inst_total_50"].isna().all() else None
        last_date = last_row["date"].strftime("%Y-%m-%d") if last_row is not None else "無資料"

        with c1:
            panel_50 = build_stock_panel(
                "0050", "p50", recent, st.session_state.live_points_50, rolling_peak, None,
                "vol50", vol50_today, "inst_amt_50", last_row, "foreign_50", "trust_50", "dealer_50", last_date,
            )
            st.plotly_chart(panel_50, use_container_width=True)
        with c2:
            panel_631 = build_stock_panel(
                "00631L", "p631", recent, st.session_state.live_points_631, None, status["entries"],
                "vol631", vol631_today, "inst_amt_631", last_row, "foreign_631", "trust_631", "dealer_631", last_date,
            )
            st.plotly_chart(panel_631, use_container_width=True)

        st.subheader("📋 進出場策略總覽")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("目前最高點(0050)", f"{rolling_peak:.2f}")
        m2.metric("目前回撤", f"{dd_now:.2f}%")
        m3.metric("0050 現價", f"{p50_now:.2f}", f"{change50:+.2f}%")
        m4.metric("00631L 現價", f"{p631_now:.2f}", f"{change631:+.2f}%")

        table_df = build_buy_sell_table(status, rolling_peak, dd_now, p631_now)
        st.dataframe(table_df, use_container_width=True, hide_index=True)
        st.caption(
            "「買點」依目前歷史高點回估三個分批進場門檻(-10% / -20% / -30%);"
            "「賣點」只在有持倉時顯示,對應回撤修復、停利、停損三種出場條件,"
            "任一條件先達成就出場。目標價位僅供參考,非即時精確報價。"
        )

    with tab_trades:
        st.subheader("新增一筆交易紀錄")
        with st.form("add_trade_form", clear_on_submit=True):
            fc1, fc2, fc3, fc4 = st.columns(4)
            symbol = fc1.selectbox("標的", ["0050", "00631L"])
            action = fc2.selectbox("動作", ["buy", "sell"], format_func=lambda x: "買入" if x == "buy" else "賣出")
            shares = fc3.number_input("股數", min_value=0.0, step=1000.0)
            price = fc4.number_input("成交價", min_value=0.0, step=0.01, format="%.2f")
            trade_date = st.date_input("成交日期", value=dt.date.today())
            note = st.text_input("備註(選填)")
            submitted = st.form_submit_button("加入紀錄")
            if submitted:
                if shares <= 0 or price <= 0:
                    st.warning("股數與成交價要大於 0。")
                else:
                    trades_mod.add_trade(trade_date, symbol, action, shares, price, note)
                    st.success("已新增,下面表格已更新。")

        df_trades = trades_mod.load_trades()
        st.subheader("持倉摘要(依交易紀錄推算,移動平均成本法)")
        hc1, hc2 = st.columns(2)
        for col, symbol, price_now in [(hc1, "0050", p50_now), (hc2, "00631L", p631_now)]:
            h = trades_mod.compute_holdings(df_trades, symbol, price_now)
            with col:
                st.metric(f"{symbol} 持有股數", f"{h['shares']:,.0f} 股")
                st.metric(f"{symbol} 均價 / 現價", f"{h['avg_cost']:.2f} / {price_now:.2f}")
                st.metric(f"{symbol} 未實現損益", f"{h['unrealized_pct']:+.1f}%",
                          delta=f"{h['market_value'] - h['cost_basis']:+,.0f} 元")

        st.subheader("所有交易紀錄")
        st.dataframe(df_trades.sort_values("date", ascending=False), use_container_width=True)

    with tab_help:
        st.markdown(f"""
### LINE 推播設定
1. 在 LINE Developers Console 的 Messaging API 頁籤,找到「Channel access token (long-lived)」,按 Issue 取得 token。
2. 用手機掃這個 Channel 的 QR code,把它加為好友(不然 broadcast 沒有人收得到)。
3. 設定環境變數 `LINE_CHANNEL_ACCESS_TOKEN`,重啟程式即可。
4. 目前用的是 **broadcast**(廣播給所有好友),沒有用到 Channel secret,也不需要架 webhook。
   如果之後有其他人加了這個帳號好友,他們也會收到訊息——自用的話不要公開分享 QR code 就好。

### 目前推播邏輯
- 只有訊號從「非進場/出場」變成「進場」或「出場」時才會推播一次,不會每 {REFRESH_SECONDS} 秒轟炸你。
- 狀態記錄在 `data/last_signal.json`,重開程式不會遺失(除非你手動刪掉這個檔案)。

### 已知限制
- 三大法人資料是收盤後更新,不是即時。
- 成交量/法人金額為估算值。
- 本工具不會自動下單,不構成投資建議。
        """)


if __name__ == "__main__":
    main()
