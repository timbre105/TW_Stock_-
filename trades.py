"""手動交易紀錄:存成本地 CSV,不會自動下單,只是讓你把「實際成交」記下來,
跟策略理論部位對照。"""
import os
import pandas as pd

TRADES_PATH = os.path.join(os.path.dirname(__file__), "data", "trades.csv")
COLUMNS = ["date", "symbol", "action", "shares", "price", "note"]


def load_trades() -> pd.DataFrame:
    if not os.path.exists(TRADES_PATH):
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(TRADES_PATH)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = None
    df["date"] = pd.to_datetime(df["date"])
    return df[COLUMNS].sort_values("date")


def add_trade(date, symbol, action, shares, price, note=""):
    df = load_trades()
    new_row = pd.DataFrame([{
        "date": pd.to_datetime(date), "symbol": symbol, "action": action,
        "shares": float(shares), "price": float(price), "note": note,
    }])
    df = pd.concat([df, new_row], ignore_index=True)
    os.makedirs(os.path.dirname(TRADES_PATH), exist_ok=True)
    df.to_csv(TRADES_PATH, index=False)
    return df


def compute_holdings(df: pd.DataFrame, symbol: str, current_price: float):
    """簡單移動平均成本法,回傳 dict: shares, avg_cost, cost_basis, market_value, unrealized_pct。"""
    sub = df[df["symbol"] == symbol].sort_values("date")
    shares = 0.0
    cost_basis = 0.0
    for _, r in sub.iterrows():
        if r["action"] == "buy":
            shares += r["shares"]
            cost_basis += r["shares"] * r["price"]
        elif r["action"] == "sell":
            if shares > 0:
                avg = cost_basis / shares
                cost_basis -= min(r["shares"], shares) * avg
            shares -= r["shares"]
        shares = max(shares, 0.0)
        cost_basis = max(cost_basis, 0.0)

    avg_cost = cost_basis / shares if shares > 0 else 0.0
    market_value = shares * current_price
    unrealized_pct = (market_value / cost_basis - 1) * 100 if cost_basis > 0 else 0.0
    return {
        "shares": shares, "avg_cost": avg_cost, "cost_basis": cost_basis,
        "market_value": market_value, "unrealized_pct": unrealized_pct,
    }
