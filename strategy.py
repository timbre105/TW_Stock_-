"""分批進場策略邏輯:用歷史資料重播出目前策略狀態,並產生人看的訊號文字。"""
import datetime as dt
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
                    entries.append((row["date"], px, t_i + 1))
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
