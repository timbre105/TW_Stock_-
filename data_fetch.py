"""資料抓取:FinMind 歷史資料(含成交量、三大法人)+ 證交所即時報價。"""
import os
import time
import datetime as dt
import pandas as pd
import requests

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
TWSE_REALTIME_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
TAIPEI_OFFSET = dt.timezone(dt.timedelta(hours=8))


def get_finmind_headers():
    token = os.environ.get("FINMIND_API_TOKEN", "")
    if not token:
        raise RuntimeError("找不到 FINMIND_API_TOKEN 環境變數,請先設定再執行。")
    return {"Authorization": f"Bearer {token}"}


def is_market_hours() -> bool:
    now = dt.datetime.now(TAIPEI_OFFSET)
    if now.weekday() >= 5:
        return False
    open_t = now.replace(hour=9, minute=0, second=0, microsecond=0)
    close_t = now.replace(hour=13, minute=30, second=0, microsecond=0)
    return open_t <= now <= close_t


def fetch_dataset(dataset, stock_id=None, start_date=None):
    params = {"dataset": dataset}
    if stock_id:
        params["data_id"] = stock_id
    if start_date:
        params["start_date"] = start_date
    headers = get_finmind_headers()
    for _ in range(3):
        resp = requests.get(FINMIND_URL, headers=headers, params=params, timeout=30)
        if resp.status_code == 200:
            payload = resp.json()
            if payload.get("status") != 200:
                raise RuntimeError(f"{dataset} API 錯誤: {payload}")
            return pd.DataFrame(payload["data"])
        time.sleep(2)
    raise RuntimeError(f"{dataset} 連續失敗")


def compute_split_adjusted(raw, splits):
    adj = raw.copy()
    adj["date"] = pd.to_datetime(adj["date"])
    adj["adj_close"] = adj["close"].astype(float)
    adj["adj_factor"] = 1.0
    if splits.empty:
        return adj
    splits = splits.copy()
    splits["date"] = pd.to_datetime(splits["date"])
    splits["ratio"] = splits["after_price"].astype(float) / splits["before_price"].astype(float)
    for _, s in splits.sort_values("date", ascending=False).iterrows():
        mask = adj["date"] < s["date"]
        adj.loc[mask, "adj_factor"] *= s["ratio"]
    adj["adj_close"] = adj["close"].astype(float) * adj["adj_factor"]
    return adj


def fetch_institutional(stock_id, start_date, suffix):
    """每日三大法人買賣超(股數),欄位加上 suffix 區分股票。"""
    df = fetch_dataset("TaiwanStockInstitutionalInvestorsBuySell", stock_id, start_date)
    cols = ["date", f"foreign_{suffix}", f"trust_{suffix}", f"dealer_{suffix}", f"inst_total_{suffix}"]
    if df.empty:
        return pd.DataFrame(columns=cols)

    df["date"] = pd.to_datetime(df["date"])
    df["net"] = df["buy"].astype(float) - df["sell"].astype(float)

    def categorize(name):
        if "Foreign" in name:
            return "外資"
        if "Investment_Trust" in name or "Trust" in name:
            return "投信"
        if "Dealer" in name:
            return "自營商"
        return "其他"

    df["category"] = df["name"].apply(categorize)
    pivot = df.pivot_table(index="date", columns="category", values="net", aggfunc="sum").reset_index()
    for cat in ["外資", "投信", "自營商"]:
        if cat not in pivot.columns:
            pivot[cat] = 0.0
        pivot[cat] = pivot[cat].fillna(0.0)
    pivot["合計"] = pivot[["外資", "投信", "自營商"]].sum(axis=1)
    pivot = pivot.rename(columns={
        "外資": f"foreign_{suffix}", "投信": f"trust_{suffix}",
        "自營商": f"dealer_{suffix}", "合計": f"inst_total_{suffix}",
    })
    return pivot[cols]


def fetch_history():
    """回傳合併好的歷史資料:價格、成交量、回撤%、三大法人買賣超估算金額。"""
    all_splits = fetch_dataset("TaiwanStockSplitPrice")

    raw50 = fetch_dataset("TaiwanStockPrice", "0050", "2003-01-01").sort_values("date").reset_index(drop=True)
    adj50 = compute_split_adjusted(raw50, all_splits[all_splits["stock_id"] == "0050"] if not all_splits.empty else pd.DataFrame())
    raw631 = fetch_dataset("TaiwanStockPrice", "00631L", "2014-01-01").sort_values("date").reset_index(drop=True)
    adj631 = compute_split_adjusted(raw631, all_splits[all_splits["stock_id"] == "00631L"] if not all_splits.empty else pd.DataFrame())

    merged = pd.merge(
        adj50[["date", "adj_close", "Trading_Volume"]].rename(
            columns={"adj_close": "p50", "Trading_Volume": "vol50_shares"}),
        adj631[["date", "adj_close", "Trading_Volume"]].rename(
            columns={"adj_close": "p631", "Trading_Volume": "vol631_shares"}),
        on="date", how="inner"
    ).reset_index(drop=True)

    merged["vol50"] = merged["vol50_shares"] / 1000.0
    merged["vol631"] = merged["vol631_shares"] / 1000.0

    merged["rolling_peak"] = merged["p50"].cummax()
    merged["drawdown_pct"] = (merged["p50"] / merged["rolling_peak"] - 1) * 100

    inst50 = fetch_institutional("0050", "2018-01-01", "50")
    inst631 = fetch_institutional("00631L", "2018-01-01", "631")
    merged = pd.merge(merged, inst50, on="date", how="left")
    merged = pd.merge(merged, inst631, on="date", how="left")
    inst_cols = [c for c in merged.columns if c.startswith(("foreign_", "trust_", "dealer_", "inst_total_"))]
    merged[inst_cols] = merged[inst_cols].fillna(0.0)

    merged["inst_amt_50"] = merged["inst_total_50"] * merged["p50"] / 10000.0
    merged["inst_amt_631"] = merged["inst_total_631"] * merged["p631"] / 10000.0

    return merged


def fetch_realtime_quotes():
    """回傳 {'0050': {'price','prev_close','change_pct','volume_lots'}, ...},失敗回傳 None。"""
    params = {"ex_ch": "tse_0050.tw|tse_00631L.tw", "json": "1", "delay": "0"}
    try:
        resp = requests.get(TWSE_REALTIME_URL, params=params, timeout=10,
                             headers={"User-Agent": "Mozilla/5.0"})
        data = resp.json()
        out = {}
        for item in data.get("msgArray", []):
            code = item.get("c")
            price_str = item.get("z")
            prev_close_str = item.get("y")
            vol_str = item.get("v")
            if price_str in (None, "-", ""):
                price_str = prev_close_str
            if price_str in (None, "-", ""):
                continue
            price = float(price_str)
            prev_close = float(prev_close_str) if prev_close_str not in (None, "-", "") else None
            change_pct = (price / prev_close - 1) * 100 if prev_close else 0.0
            volume_lots = float(vol_str) if vol_str not in (None, "-", "") else None
            out[code] = {"price": price, "prev_close": prev_close,
                         "change_pct": change_pct, "volume_lots": volume_lots}
        return out if out else None
    except Exception:
        return None
