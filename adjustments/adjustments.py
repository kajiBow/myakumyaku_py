from fastapi import FastAPI
from fastapi.responses import HTMLResponse
import requests
import json
import datetime
import time
import os

app = FastAPI()

# ======== 設定 ========
EBII_DATA_URL = "https://expo2.ebii.net/api/data"
OUTPUT_FILE = "adjustments.json"
POST_URL = "https://ticket.expo2025.or.jp/api/d/user_event_reservations"

# 固定設定（必要に応じて差し替え）
COOKIE = "session_id=b2e98fe04b5693e4c4d70bacbe45136ff6faaabf38e4064f37b10e7b54970d6b;"  # ← 実際のCookieに置き換え
TICKET_IDS = ["95D5YW2Y77"]
ENTRANCE_DATE = "20251013"

HEADERS_POST = {
    "Content-Type": "application/json;charset=UTF-8",
    "Cookie": COOKIE,
    "x-api-lang": "ja",
    "User-Agent": "Mozilla/5.0"
}


# ======== 関数 ========

def adjust_time_str(time_str: str, offset_minutes: int):
    """時刻文字列(HHMM)を指定分だけずらす"""
    t = datetime.datetime.strptime(time_str, "%H%M")
    t -= datetime.timedelta(minutes=offset_minutes)
    return t.strftime("%H%M")


def test_pavilion(pavilion_code: str, base_time: str):
    """1パビリオンあたりの時間補正を探索"""
    print(f"\n=== {pavilion_code} 開始 ===")
    for offset in range(0, 35, 5):
        adjusted_time = adjust_time_str(base_time, offset)
        payload = {
            "ticket_ids": TICKET_IDS,
            "entrance_date": ENTRANCE_DATE,
            "start_time": adjusted_time,
            "event_code": pavilion_code,
            "registered_channel": "5"
        }

        try:
            res = requests.post(POST_URL, json=payload, headers=HEADERS_POST, timeout=10)
            print(f"[{pavilion_code}] 試行: {adjusted_time} → Status {res.status_code}")

            if res.status_code == 422:
                print(f"✅ 確定: 補正値 {offset} 分")
                return -offset  # 実際には -5分など
            elif res.status_code == 400:
                print(f"⏳ {offset}分補正では invalid_parameter → 継続")
                time.sleep(0.5)
                continue
            else:
                print(f"⚠️ 予期しないレスポンス: {res.text[:100]}")
                time.sleep(1)
        except Exception as e:
            print(f"❌ 通信エラー: {e}")
            time.sleep(1)

    print(f"❌ 最大補正(-30分)でも422が出ず → 補正値 0")
    return 0


def generate_adjustments_from_ebii():
    """ebiiのAPIからデータ取得→補正値算出"""
    print(f"📡 ebiiからデータ取得中: {EBII_DATA_URL}")
    try:
        res = requests.get(EBII_DATA_URL, timeout=10)
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        print(f"❌ ebiiデータ取得失敗: {e}")
        return {}

    adjustments = {}

    for item in data:
        code = item.get("c")
        schedule = item.get("s", [])
        if not code or not schedule:
            continue

        # 最後の時間スロットを取得
        base_time = schedule[-1].get("t")
        if not base_time:
            continue

        offset = test_pavilion(code, base_time)
        adjustments[code] = offset

    # 保存
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(adjustments, f, ensure_ascii=False, indent=2)

    print("\n=== ✅ 補正値リスト ===")
    print(json.dumps(adjustments, ensure_ascii=False, indent=2))
    return adjustments


# ======== Web UI ========

@app.get("/", response_class=HTMLResponse)
async def show_results():
    if not os.path.exists(OUTPUT_FILE):
        adjustments = generate_adjustments_from_ebii()
    else:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            adjustments = json.load(f)

    html = "<h2>🎯 Expo ebii補正値リスト</h2><table border='1' cellspacing='0' cellpadding='6'>"
    html += "<tr><th>パビリオンID</th><th>補正値（分）</th></tr>"
    for code, offset in adjustments.items():
        color = "#dff0d8" if offset < 0 else "#f2dede"
        html += f"<tr style='background:{color}'><td>{code}</td><td>{offset}</td></tr>"
    html += "</table>"
    html += "<p>出力ファイル: adjustments.json</p>"
    return html


# ======== 起動時に実行 ========

@app.on_event("startup")
def startup_event():
    print("🚀 起動時に ebii API を使用して補正値生成を開始します…")
    generate_adjustments_from_ebii()


if __name__ == "__main__":
    print("""
==========================================
🎪 EXPO2025 ebii連携補正値自動検出ツール 起動
------------------------------------------
ebiiのAPI (https://expo2.ebii.net/api/data) を読み取り、
各パビリオンの時間補正値を算出します。
結果は adjustments.json に保存されます。
==========================================
""")
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8081)
