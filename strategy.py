"""分批進場策略邏輯:用歷史資料重播出目前策略狀態,並產生人看的訊號文字。"""
import datetime as dt
import pandas as pd
from data_fetch import TAIPEI_OFFSET

RESERVE = 200_000.0
TRANCHES = [(-10.0, 0.30), (-20.0, 0.30), (-30.0, 0.40)]
EXIT_DD = -5.0
EXIT_PROFIT_PCT = 30.0
STOP_LOSS_PCT = -40.0
MAX_HOLD_DAYS = 545
REARM_DD = -2.0


def replay_strategy(df):
    armed = True
    triggered = [False] * len(TRANCHES)
    shares = 0.0
    cost = 0.0
    in_position = False
    cycle_start_date = None
    entries = []

    for i in range(len(df)):
        row = df.iloc[i]
        dd = row["drawdown_pct"]
        if not armed and dd >= REARM_DD:
            armed = True
        if armed:
            for t_i, (thresh, frac) in enumerate(TRANCHES):
                if not triggered[t_i] and dd <= thresh:
                    triggered[t_i] = True
                    amt = RESERVE * frac
                    px = row["p631"]
                    shares += amt / px
                    cost += amt
                    if not in_position:
                        in_position = True
                        cycle_start_date = row["date"]
                    entries.append((row["date"], px, t_i + 1, row["rolling_peak"], dd))
        if in_position and shares > 0:
            current_value = shares * row["p631"]
            unrealized_pct = (current_value / cost - 1) * 100 if cost > 0 else 0
            days_held = (row["date"] - cycle_start_date).days
            exit_now = (dd >= EXIT_DD or unrealized_pct >= EXIT_PROFIT_PCT
                        or unrealized_pct <= STOP_LOSS_PCT or days_held >= MAX_HOLD_DAYS)
            if exit_now:
                shares = 0.0; cost = 0.0
                in_position = False
                triggered = [False] * len(TRANCHES)
                armed = False

    return {
        "armed": armed, "triggered": triggered, "in_position": in_position,
        "shares": shares, "cost": cost, "cycle_start_date": cycle_start_date,
        "entries": entries,
    }


def evaluate_signal(status, dd_now, p631_now):
    """回傳 dict: kind(entry/exit/hold/idle), text(多行), color, notify_key(用來判斷是否為新訊號)。"""
    if status["in_position"]:
        current_value = status["shares"] * p631_now
        unrealized_pct = (current_value / status["cost"] - 1) * 100 if status["cost"] > 0 else 0
        days_held = (dt.datetime.now(TAIPEI_OFFSET).date() - status["cycle_start_date"].date()).days
        reasons = []
        if dd_now >= EXIT_DD:
            reasons.append("回撤已修復")
        if unrealized_pct >= EXIT_PROFIT_PCT:
            reasons.append("已達停利")
        if unrealized_pct <= STOP_LOSS_PCT:
            reasons.append("觸發停損")
        if days_held >= MAX_HOLD_DAYS:
            reasons.append("超過最長持有天數")
        if reasons:
            text = (f"🔴 出場訊號: {','.join(reasons)}\n"
                    f"未實現報酬 {unrealized_pct:+.1f}%   持有 {days_held} 天")
            return {"kind": "exit", "text": text, "color": "red",
                    "notify_key": "exit:" + ",".join(sorted(reasons))}
        else:
            text = (f"🟡 持有中,未實現報酬 {unrealized_pct:+.1f}%\n"
                    f"持有 {days_held} 天,尚未觸發出場條件")
            return {"kind": "hold", "text": text, "color": "orange", "notify_key": "hold"}
    else:
        untried = [i for i, t in enumerate(status["triggered"]) if not t]
        if untried and dd_now <= TRANCHES[untried[0]][0]:
            thresh, frac = TRANCHES[untried[0]]
            text = (f"🟢 進場訊號! 第{untried[0]+1}階觸發(門檻{thresh}%)\n"
                    f"建議投入 NT${RESERVE*frac:,.0f}")
            return {"kind": "entry", "text": text, "color": "green",
                    "notify_key": f"entry_{untried[0]}"}
        elif untried:
            thresh, frac = TRANCHES[untried[0]]
            gap = dd_now - thresh
            text = f"⚪ 空手觀望中,距離第{untried[0]+1}階門檻({thresh}%)還差 {gap:.1f} 個百分點"
            return {"kind": "idle", "text": text, "color": "gray", "notify_key": "idle"}
        else:
            text = f"⚪ 空手,所有階段已用完,等待回撤修復到 {REARM_DD}% 以上重新解鎖"
            return {"kind": "idle", "text": text, "color": "gray", "notify_key": "idle_full"}


def build_buy_sell_table(status, rolling_peak, dd_now, p631_now):
    """組出「進出場策略總覽」表格,含分階買點與對應賣點,供畫面下方以 table 呈現。

    entries 內每筆是 (日期, 00631L進場價, 階段, 進場當下的0050高點, 進場當下回撤%)。
    """
    rows = []
    for i, (thresh, frac) in enumerate(TRANCHES):
        target_50 = rolling_peak * (1 + thresh / 100)
        stage_label = f"第{i + 1}階"
        cond = f"回撤 ≤ {thresh:.0f}%"
        triggered = status["triggered"][i]

        entry_match = None
        if triggered and status["in_position"] and status["cycle_start_date"] is not None:
            candidates = [e for e in status["entries"]
                          if e[2] == i + 1 and e[0] >= status["cycle_start_date"]]
            if candidates:
                entry_match = candidates[-1]

        if entry_match:
            e_date, e_price631, _stage, e_peak, e_dd = entry_match
            status_text = "✅ 已觸發"
            note = (f"{e_date.strftime('%Y-%m-%d')} 依高點 {e_peak:.2f} 回估(當時回撤 {e_dd:.1f}%),"
                    f"00631L 進場價 {e_price631:.2f}")
        elif not status["armed"]:
            status_text = "🔒 鎖定中"
            note = f"上一輪已出場,需回撤修復到 {REARM_DD:.0f}% 以上才重新解鎖"
        else:
            gap = dd_now - thresh  # 正值 = 還要再跌幾個百分點才會觸發
            status_text = "⚪ 未觸發"
            note = (f"依目前高點 {rolling_peak:.2f} 回估,目前回撤 {dd_now:.1f}%,"
                    f"還要再跌 {gap:.1f} 個百分點") if gap > 0 else "條件已達成,等待下次刷新確認"

        rows.append({
            "類型": "買點", "階段": stage_label, "條件": cond,
            "目標價位": f"{target_50:.2f}(0050)", "狀態": status_text, "備註": note,
        })

    if status["in_position"] and status["shares"] > 0:
        avg_cost = status["cost"] / status["shares"]
        unrealized_pct = (p631_now / avg_cost - 1) * 100
        recover_price_50 = rolling_peak * (1 + EXIT_DD / 100)
        take_profit_631 = avg_cost * (1 + EXIT_PROFIT_PCT / 100)
        stop_loss_631 = avg_cost * (1 + STOP_LOSS_PCT / 100)
        rows.append({
            "類型": "賣點", "階段": "-", "條件": f"回撤修復至 {EXIT_DD:.0f}%",
            "目標價位": f"{recover_price_50:.2f}(0050)", "狀態": "適用中",
            "備註": f"目前回撤 {dd_now:.1f}%,均價(00631L) {avg_cost:.2f}",
        })
        rows.append({
            "類型": "賣點", "階段": "-", "條件": f"未實現報酬達 +{EXIT_PROFIT_PCT:.0f}%",
            "目標價位": f"{take_profit_631:.2f}(00631L)", "狀態": "適用中",
            "備註": f"目前未實現 {unrealized_pct:+.1f}%,均價 {avg_cost:.2f}",
        })
        rows.append({
            "類型": "賣點", "階段": "-", "條件": f"觸及停損 {STOP_LOSS_PCT:.0f}%",
            "目標價位": f"{stop_loss_631:.2f}(00631L)", "狀態": "適用中",
            "備註": f"均價(00631L) {avg_cost:.2f}",
        })
    else:
        rows.append({
            "類型": "賣點", "階段": "-", "條件": "-", "目標價位": "-",
            "狀態": "目前無持倉", "備註": "尚未進場,暫無賣點目標",
        })

    return pd.DataFrame(rows, columns=["類型", "階段", "條件", "目標價位", "狀態", "備註"])
