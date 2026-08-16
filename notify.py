"""LINE Messaging API 推播。

用 broadcast 端點,把訊息發給「所有把這個官方帳號加為好友的人」。
好處:不用架 webhook、不用去拿 userId,設定最簡單。
缺點:如果之後有別人也加了這個官方帳號好友,他們也會收到——
自用的話只要別把 QR code 分享出去就沒問題。

如果之後想只發給指定的人(1對1 push),需要另外透過 webhook 拿到對方的
userId,再改用 https://api.line.me/v2/bot/message/push,那個複雜一點,
有需要我可以再幫你加。
"""
import os
import requests

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/broadcast"


def notify_enabled() -> bool:
    flag = os.environ.get("LINE_NOTIFY_ENABLED", "true").lower()
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    return flag == "true" and bool(token)


def send_line_broadcast(text: str):
    """回傳 (成功與否: bool, 訊息: str)。不會拋例外,失敗只回傳錯誤訊息。"""
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        return False, "尚未設定 LINE_CHANNEL_ACCESS_TOKEN,略過推播。"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    body = {"messages": [{"type": "text", "text": text[:5000]}]}
    try:
        resp = requests.post(LINE_PUSH_URL, headers=headers, json=body, timeout=10)
        if resp.status_code == 200:
            return True, "推播成功"
        return False, f"推播失敗 ({resp.status_code}): {resp.text[:200]}"
    except Exception as e:
        return False, f"推播例外: {e}"
