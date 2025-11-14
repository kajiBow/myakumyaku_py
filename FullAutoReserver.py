"""
EXPO2025 空き監視＆自動予約ツール（フルオート版）
- パビリオンの空き枠を自動監視
- 空きが見つかったら自動でPOST送信
- Discord Webhookで予約成功通知
- Cookie自動更新機能
"""

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
import requests
import threading
import time
import random
import json
import os
import logging
import subprocess
import sys
from pathlib import Path
from datetime import datetime

app = FastAPI()

# ✅ /status と /cookie/status エンドポイントのログを非表示にする
class StatusEndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return "GET /status" not in message and "GET /cookie/status" not in message

# ✅ ログフィルタを適用
logging.getLogger("uvicorn.access").addFilter(StatusEndpointFilter())

running = False
monitor_thread = None
last_status_code = None  # ✅ 最新のステータスコードを保持
status_code_count = 0  # ✅ 連続回数をカウント
no_vacancy_count = 0  # ✅ 空きなし連続回数
current_state = "idle"  # ✅ 現在の状態: idle, monitoring, posting
SAVE_FILE = "monitor_settings.json"

# 【Cookie監視】
cookie_monitor_running = False      # Cookie監視スレッドの実行フラグ
cookie_monitor_thread = None        # Cookie監視スレッド
cookie_monitor_interval = 5         # 監視間隔（分）
cookie_invalid_log = []             # Cookie無効履歴 [{time, reason}, ...]
cookie_status = {
    "valid": None,                  # Cookie有効性（True/False/None）
    "last_check": None,             # 最後のチェック時刻
    "message": "未チェック",         # ステータスメッセージ
    "checking": False               # チェック実行中フラグ
}

# 【ファイルパス】
BASE_DIR = Path(__file__).resolve().parent
COOKIE_FILE = str(BASE_DIR / "cookie.txt")  # autologin.pyが生成するCookieファイル

# テストモード設定
TEST_MODE = False  # Trueにするとダミーサーバーを使用

def get_urls():
    """テストモードに応じてURLを切り替え"""
    if TEST_MODE:
        return {
            "GET_URL": "http://localhost:5000/api/add",
            "POST_URL": "http://localhost:5000/api/d/user_event_reservations"
        }
    else:
        return {
            "GET_URL": "https://expo.ebii.net/api/add",
            "POST_URL": "https://ticket.expo2025.or.jp/api/d/user_event_reservations"
        }

# =============================
# Cookie監視機能
# =============================

def check_cookie_validity():
    """
    現在の設定ファイルのCookieをテスト

    Returns:
        bool: Cookie有効ならTrue
    """
    global cookie_status

    cookie_status["checking"] = True
    cookie_status["message"] = "チェック中..."

    try:
        # monitor_settings.json からCookieを取得
        settings = load_settings()
        cookie = settings.get("cookie", "")

        if not cookie:
            cookie_status["valid"] = False
            cookie_status["message"] = "❌ Cookie未設定"
            cookie_status["last_check"] = datetime.now().strftime("%H:%M:%S")
            return False

        # 直接APIテストリクエストを送信
        headers = {
            "Cookie": cookie,
            "User-Agent": "Mozilla/5.0",
            "x-api-lang": "ja"
        }

        r = requests.get(
            "https://ticket.expo2025.or.jp/api/d/account/info",
            headers=headers,
            timeout=10
        )

        if r.status_code == 200:
            cookie_status["valid"] = True
            cookie_status["message"] = "✅ Cookie有効"
            cookie_status["last_check"] = datetime.now().strftime("%H:%M:%S")
            print(f"Cookie有効性チェック: 有効 ({cookie_status['last_check']})")
            return True
        else:
            cookie_status["valid"] = False
            cookie_status["message"] = f"❌ Cookie無効 (Status: {r.status_code})"
            # Cookie無効履歴を記録
            cookie_invalid_log.append({
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "reason": cookie_status["message"]
            })
            # 最新100件まで保持
            if len(cookie_invalid_log) > 100:
                cookie_invalid_log.pop(0)
            cookie_status["last_check"] = datetime.now().strftime("%H:%M:%S")
            print(f"Cookie有効性チェック: 無効 Status {r.status_code}")
            return False

    except Exception as e:
        cookie_status["valid"] = False
        cookie_status["message"] = f"❌ エラー"
        cookie_status["last_check"] = datetime.now().strftime("%H:%M:%S")
        print(f"Cookie有効性チェックエラー: {e}")
        return False
    finally:
        cookie_status["checking"] = False


def relogin_and_update_cookie():
    """
    autologin.py --silent を実行して再ログイン、Cookieを更新

    Returns:
        bool: 成功ならTrue
    """
    print("🔄 Cookie無効のため再ログイン実行...")

    try:
        # autologin.py --silent を実行（ヘッドレスモード）
        env = os.environ.copy()
        # 子プロセス側のPythonにUTF-8を強制（絵文字出力でのUnicodeEncodeError回避）
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        result = subprocess.run(
            [sys.executable, "autologin.py", "--silent"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(BASE_DIR),
            env=env
        )

        if result.returncode != 0:
            print("❌ autologin 実行失敗")
            print(result.stdout)
            print(result.stderr)

        # cookie.txt から新しいCookieを読み込み
        cookie_path = os.path.join(str(BASE_DIR), COOKIE_FILE) if not os.path.isabs(COOKIE_FILE) else COOKIE_FILE
        if os.path.exists(cookie_path):
            with open(cookie_path, "r", encoding="utf-8") as f:
                new_cookie = f.read().strip()

            if new_cookie:
                # monitor_settings.json を更新
                settings = load_settings()
                settings["cookie"] = new_cookie
                save_settings(settings)

                print("✅ Cookie更新完了")
                cookie_status["valid"] = True
                cookie_status["message"] = "✅ 再ログイン成功"
                cookie_status["last_check"] = datetime.now().strftime("%H:%M:%S")
                return True

        print("❌ Cookie更新失敗")
        cookie_status["message"] = "❌ 再ログイン失敗"
        return False

    except Exception as e:
        print(f"❌ 再ログインエラー: {e}")
        cookie_status["message"] = f"❌ 再ログイン失敗"
        return False


def cookie_monitor_loop():
    """
    Cookie監視ループ（バックグラウンドスレッドで実行）
    定期的にCookie有効性をチェックし、無効なら再ログイン
    """
    global cookie_monitor_running, cookie_monitor_interval

    print(f"📡 Cookie監視スレッド開始（間隔: {cookie_monitor_interval}分）")

    while cookie_monitor_running:
        # Cookie有効性チェック
        is_valid = check_cookie_validity()

        # 無効なら再ログイン
        if not is_valid:
            relogin_and_update_cookie()

        # 指定間隔待機（1秒ごとにチェックして、途中で停止できるようにする）
        wait_seconds = cookie_monitor_interval * 60
        for _ in range(wait_seconds):
            if not cookie_monitor_running:
                break
            time.sleep(1)

    print("📡 Cookie監視スレッド停止")


# =============================
# Discord通知機能
# =============================

def send_discord_notification(webhook_url, event_code, start_time, elapsed):
    """
    Discord Webhookで予約成功通知を送信

    Args:
        webhook_url: Discord Webhook URL
        event_code: イベントコード
        start_time: 予約時刻
        elapsed: 経過時間（秒）
    """
    if not webhook_url or not webhook_url.startswith("https://discord.com/api/webhooks/"):
        return

    try:
        message = {
            "content": f"🎉 **予約成功！**\n\n**イベント:** {event_code}\n**時間:** {start_time}\n**経過時間:** {elapsed}秒",
            "username": "EXPO予約Bot"
        }
        response = requests.post(webhook_url, json=message, timeout=5)
        if response.status_code == 204:
            print("✅ Discord通知送信成功")
        else:
            print(f"⚠️ Discord通知失敗: {response.status_code}")
    except Exception as e:
        print(f"⚠️ Discord通知エラー: {e}")


# =============================
# 保存・読み込み
# =============================

def load_settings():
    if os.path.exists(SAVE_FILE):
        with open(SAVE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_settings(data: dict):
    with open(SAVE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# =============================
# メイン監視処理
# =============================
# フォルダパスを指定
ADJUST_DIR = "adjustments"
ADJUST_FILE = os.path.join(ADJUST_DIR, "adjustments.json")

def load_adjustments():
    """adjustments/adjustments.json を読み込む"""
    if os.path.exists(ADJUST_FILE):
        try:
            with open(ADJUST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ 補正データ読み込みエラー: {e}")
    else:
        print(f"⚠️ 補正データファイルが見つかりません: {ADJUST_FILE}")
    return {}

def adjust_time_for_post(pavilion: str, t: str) -> str:
    """
    ebiiで取得した時刻tをパビリオン別補正後に変換
    例: t='1845', pavilion='H1H9', adjustments.json={'H1H9': -10}
    → '1835'
    """
    if not t.isdigit() or len(t) != 4:
        print(f"⚠️ 無効な時間形式: {t}")
        return t

    adjustments = load_adjustments()
    offset = adjustments.get(pavilion, 0)  # 該当がなければ補正なし

    hh, mm = int(t[:2]), int(t[2:])
    total = hh * 60 + mm + offset
    total %= 24 * 60  # 翌日0時超え対策

    new_hh, new_mm = divmod(total, 60)
    adjusted = f"{new_hh:02d}{new_mm:02d}"

    print(f"🕒 {pavilion}: {t} → {adjusted}（補正 {offset:+}分）")
    return adjusted


def monitor_task(pavilion_ids, interval, post_duration, cookie, ticket_ids, entrance_date, post_min, post_max, webhook_url=None):
    global running, last_status_code, status_code_count, no_vacancy_count, current_state

    urls = get_urls()
    GET_URL = urls["GET_URL"]
    POST_URL = urls["POST_URL"]

    headers_get = {"User-Agent": "Mozilla/5.0"}
    headers_post = {
        "Content-Type": "application/json;charset=UTF-8",
        "Cookie": cookie,
        "x-api-lang": "ja",
        "User-Agent": "Mozilla/5.0"
    }

    mode_text = "🧪 テストモード" if TEST_MODE else "🔴 本番モード"
    print(f"\n{'='*60}")
    print(f"{mode_text} - 監視開始")
    print(f"GET: {GET_URL}")
    print(f"POST: {POST_URL}")
    print(f"{'='*60}\n")

    current_state = "monitoring"
    monitoring_start_time = time.time()

    while running:
        try:
            res = requests.get(GET_URL, headers=headers_get, timeout=5)
            if res.status_code != 200:
                print("GET失敗:", res.status_code)
                time.sleep(interval)
                continue

            data = res.json()
            found_target = None

            # 空き枠をチェック
            for pid in pavilion_ids:
                if pid in data:
                    slots = data[pid]
                    for slot in slots:
                        if slot["s"] > 0:  # 空きあり
                            found_target = (pid, slot["t"])
                            break
                if found_target:
                    break

            if found_target:
                pavilion, start_time = found_target
                print(f"🎯 空き発見！ {pavilion} {start_time} → POST開始")
                current_state = "posting"
                no_vacancy_count = 0  # ✅ リセット
                end_time = time.time() + post_duration
                post_start_time = time.time()

                payload = {
                    "ticket_ids": ticket_ids,
                    "entrance_date": entrance_date,
                    "start_time": adjust_time_for_post(pavilion, start_time),
                    "event_code": pavilion,
                    "registered_channel": "5"
                }

                post_count = 0
                while running and time.time() < end_time:
                    post_count += 1
                    r = requests.post(POST_URL, json=payload, headers=headers_post)

                    # ✅ ステータスコードと連続回数を更新
                    if r.status_code == last_status_code:
                        status_code_count += 1
                    else:
                        last_status_code = r.status_code
                        status_code_count = 1

                    print(f"[POST #{post_count}] Status: {r.status_code}, Response: {r.text[:100]}")
                    try:
                        body = r.json()
                    except:
                        body = {}
                    if r.status_code == 200 and body == {}:
                        elapsed = int(time.time() - monitoring_start_time)
                        print("✅ 成功！予約確定・停止します。")

                        # Discord通知を送信
                        if webhook_url:
                            send_discord_notification(
                                webhook_url,
                                pavilion,
                                start_time,
                                elapsed
                            )

                        running = False
                        current_state = "success"
                        return

                    wait_time = random.uniform(post_min, post_max)
                    print(f"⏳ 待機: {wait_time:.2f}秒")
                    time.sleep(wait_time)

                # POST期間終了後、監視に戻る
                current_state = "monitoring"
                last_status_code = None
                status_code_count = 0

            else:
                no_vacancy_count += 1  # ✅ 空きなしカウント
                print("⏳ 空きなし")

            time.sleep(interval)

        except Exception as e:
            print("⚠️ エラー:", e)
            time.sleep(interval)

    print("🛑 監視終了")
    current_state = "idle"

# =============================
# Web UI
# =============================

@app.get("/", response_class=HTMLResponse)
async def index():
    saved = load_settings()
    saved_pavilions = saved.get('pavilion_ids', [])
    
    # パビリオンリスト
    pavilions = [
        ("IC0C", "ナオライ"),
        ("H5H0", "リボーン体験"),
        ("H5H9", "モンハン"),
        ("H5H3", "人生ゲーム"),
        ("H1H9", "日本館"),
        ("HIH0", "三菱未来館"),
        ("HOH0", "ブルーオーシャン"),
        ("HEH0", "住友館"),
        ("HQH0", "GUNDAM NEXT FUTURE PAVILION"),
        ("IC00", "Null"),
        ("IC09", "インスタレーション"),
        ("I300", "betterCoBeing"),
        ("C2N0", "イタリア~1500"),
        ("C2N3", "イタリア1500~"),
        ("EDF0", "ヨルダン"),
        ("I600", "いのちの未来"),
        ("HAH0", "NTT"),
        ("IF00", "いのちの動的平衡"),
        ("II00", "超時空シアター"),
        ("II06", "ANIMA"),
        ("IL00", "EARTH MART"),
        ("HCH0", "電力館"),
        ("C060", "アイルランド ツアー30分"),
        ("C063", "アイルランド ツアー60分"),
        ("C066", "アイルランド ツアーなし"),
        ("HUH6", "ガスパビリオン"),
        ("CCB0", "クウェート"),
        ("HGH0", "ノモの国"),
        ("H3H0", "Womans"),
    ]
    
    # チェックボックスHTML生成
    checkbox_html = ""
    for code, name in pavilions:
        checked = "checked" if code in saved_pavilions else ""
        checkbox_html += f'''
        <label class="pavilion-checkbox">
          <input type="checkbox" name="pavilion_ids" value="{code}" {checked}>
          <span class="checkbox-label">{name}</span>
        </label>
        '''
    
    mode_indicator = "🧪 テストモード" if TEST_MODE else "🔴 本番モード"
    mode_color = "#ff9800" if TEST_MODE else "#f44336"
    
    return f"""
    <!DOCTYPE html>
    <html lang="ja">
      <head>
        <meta charset="UTF-8">
        <title>EXPO 空き監視＆自動予約ツール</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;700&display=swap" rel="stylesheet">
        <style>
          :root {{
            --primary-color: #2196F3;
            --primary-dark: #1976D2;
            --success-color: #4CAF50;
            --success-dark: #388E3C;
            --danger-color: #F44336;
            --danger-dark: #D32F2F;
            --warning-color: #FF9800;
            --warning-dark: #F57C00;
            --info-color: #00BCD4;
            --info-dark: #0097A7;
            --gray-50: #FAFAFA;
            --gray-100: #F5F5F5;
            --gray-200: #EEEEEE;
            --gray-300: #E0E0E0;
            --gray-400: #BDBDBD;
            --gray-600: #757575;
            --gray-700: #616161;
            --gray-800: #424242;
            --gray-900: #212121;
            --shadow-sm: 0 2px 4px rgba(0,0,0,0.08);
            --shadow-md: 0 4px 12px rgba(0,0,0,0.1);
            --shadow-lg: 0 8px 24px rgba(0,0,0,0.15);
            --radius-sm: 6px;
            --radius-md: 10px;
            --radius-lg: 16px;
          }}

          * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
          }}

          body {{
            font-family: 'Noto Sans JP', -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
          }}

          .container {{
            max-width: 700px;
            margin: 0 auto;
          }}

          .header {{
            text-align: center;
            margin-bottom: 24px;
          }}

          .mode-badge {{
            display: inline-block;
            background: {mode_color};
            color: white;
            padding: 8px 20px;
            border-radius: 50px;
            font-weight: 700;
            font-size: 13px;
            letter-spacing: 0.5px;
            box-shadow: var(--shadow-md);
            margin-bottom: 16px;
            animation: pulse 2s ease-in-out infinite;
          }}

          @keyframes pulse {{
            0%, 100% {{ transform: scale(1); }}
            50% {{ transform: scale(1.05); }}
          }}

          .title {{
            font-size: 28px;
            font-weight: 700;
            color: white;
            text-shadow: 0 2px 8px rgba(0,0,0,0.2);
            margin-bottom: 8px;
          }}

          .subtitle {{
            font-size: 14px;
            color: rgba(255,255,255,0.9);
          }}

          .card {{
            background: white;
            border-radius: var(--radius-lg);
            box-shadow: var(--shadow-lg);
            padding: 24px;
            margin-bottom: 20px;
          }}

          .card-header {{
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 20px;
            padding-bottom: 16px;
            border-bottom: 2px solid var(--gray-200);
          }}

          .card-title {{
            font-size: 18px;
            font-weight: 700;
            color: var(--gray-800);
            flex: 1;
          }}

          .form-group {{
            margin-bottom: 20px;
          }}

          .form-label {{
            display: block;
            margin-bottom: 8px;
            font-weight: 600;
            font-size: 14px;
            color: var(--gray-700);
          }}

          input[type="number"],
          input[type="text"] {{
            width: 100%;
            padding: 12px 16px;
            font-size: 15px;
            border: 2px solid var(--gray-300);
            border-radius: var(--radius-md);
            background: white;
            transition: all 0.2s;
            font-family: inherit;
          }}

          input[type="number"]:focus,
          input[type="text"]:focus {{
            outline: none;
            border-color: var(--primary-color);
            box-shadow: 0 0 0 3px rgba(33, 150, 243, 0.1);
          }}

          .btn {{
            width: 100%;
            padding: 14px 20px;
            border: none;
            border-radius: var(--radius-md);
            font-size: 15px;
            font-weight: 700;
            cursor: pointer;
            transition: all 0.2s;
            box-shadow: var(--shadow-sm);
            font-family: inherit;
          }}

          .btn:hover {{
            transform: translateY(-2px);
            box-shadow: var(--shadow-md);
          }}

          .btn:active {{
            transform: translateY(0);
          }}

          .btn-primary {{
            background: linear-gradient(135deg, var(--primary-color), var(--primary-dark));
            color: white;
          }}

          .btn-success {{
            background: linear-gradient(135deg, var(--success-color), var(--success-dark));
            color: white;
          }}

          .btn-danger {{
            background: linear-gradient(135deg, var(--danger-color), var(--danger-dark));
            color: white;
          }}

          .btn-warning {{
            background: linear-gradient(135deg, var(--warning-color), var(--warning-dark));
            color: white;
          }}

          .btn-secondary {{
            background: var(--gray-600);
            color: white;
          }}

          .btn-group {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
            margin-top: 16px;
          }}

          .btn-group-full {{
            display: flex;
            flex-direction: column;
            gap: 10px;
          }}

          .input-group {{
            display: flex;
            gap: 10px;
            align-items: stretch;
          }}

          .input-group input {{
            flex: 1;
          }}

          .input-group .btn {{
            width: auto;
            min-width: 90px;
            padding: 12px 20px;
          }}

          .pavilion-checkboxes {{
            background: var(--gray-50);
            border: 2px solid var(--gray-200);
            border-radius: var(--radius-md);
            padding: 12px;
            max-height: 420px;
            overflow-y: auto;
          }}

          .pavilion-checkboxes::-webkit-scrollbar {{
            width: 8px;
          }}

          .pavilion-checkboxes::-webkit-scrollbar-track {{
            background: var(--gray-200);
            border-radius: 4px;
          }}

          .pavilion-checkboxes::-webkit-scrollbar-thumb {{
            background: var(--gray-400);
            border-radius: 4px;
          }}

          .pavilion-checkbox {{
            display: flex;
            align-items: center;
            padding: 12px 14px;
            margin: 4px 0;
            border-radius: var(--radius-sm);
            cursor: pointer;
            transition: all 0.2s;
            user-select: none;
            background: white;
            border: 2px solid transparent;
          }}

          .pavilion-checkbox:hover {{
            background: var(--gray-100);
            border-color: var(--gray-300);
          }}

          .pavilion-checkbox:has(input:checked) {{
            background: linear-gradient(135deg, #E3F2FD, #BBDEFB);
            border-color: var(--primary-color);
          }}

          .pavilion-checkbox input[type="checkbox"] {{
            width: 20px;
            height: 20px;
            margin: 0;
            cursor: pointer;
            flex-shrink: 0;
            accent-color: var(--primary-color);
          }}

          .checkbox-label {{
            margin-left: 12px;
            font-size: 14px;
            color: var(--gray-800);
            font-weight: 500;
            line-height: 1.4;
          }}

          .status-box {{
            margin-top: 20px;
            padding: 16px;
            border-radius: var(--radius-md);
            text-align: center;
            font-weight: 600;
            font-size: 14px;
            box-shadow: var(--shadow-sm);
            transition: all 0.3s;
          }}

          .alert {{
            background: #FFF3CD;
            border: 2px solid var(--warning-color);
            padding: 16px;
            border-radius: var(--radius-md);
            margin-bottom: 20px;
            font-size: 13px;
            color: #856404;
          }}

          .alert strong {{
            display: block;
            margin-bottom: 6px;
            font-size: 14px;
          }}

          .cookie-status {{
            padding: 14px;
            background: var(--gray-100);
            border-radius: var(--radius-md);
            margin-bottom: 16px;
            font-size: 13px;
            line-height: 1.6;
            transition: all 0.3s;
          }}

          .cookie-status.valid {{
            background: linear-gradient(135deg, #E8F5E9, #C8E6C9);
            border: 2px solid var(--success-color);
          }}

          .cookie-status.invalid {{
            background: linear-gradient(135deg, #FFEBEE, #FFCDD2);
            border: 2px solid var(--danger-color);
          }}

          .cookie-controls {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 10px;
            margin-bottom: 16px;
          }}

          .cookie-controls .btn {{
            font-size: 13px;
            padding: 11px 16px;
          }}

          .interval-input {{
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 13px;
            color: var(--gray-700);
          }}

          .interval-input input {{
            width: 80px;
            padding: 8px 12px;
            border: 2px solid var(--gray-300);
            border-radius: var(--radius-sm);
            font-size: 13px;
          }}

          @media (max-width: 600px) {{
            body {{
              padding: 12px;
            }}

            .card {{
              padding: 18px;
            }}

            .title {{
              font-size: 24px;
            }}

            .btn-group {{
              grid-template-columns: 1fr;
            }}
          }}
        </style>
      </head>
      <body>
        <div class="container">
          <div class="header">
            <div class="mode-badge">{mode_indicator}</div>
            <h1 class="title">EXPO 空き監視＆自動予約ツール</h1>
            <p class="subtitle">大阪・関西万博 2025</p>
          </div>

          {"<div class='alert'><strong>⚠️ テストモード中</strong>ダミーサーバー (localhost:5000) に接続します。<br>本番利用前にコード内の TEST_MODE = False に変更してください。</div>" if TEST_MODE else ""}

          <div class="card">
            <div class="card-header">
              <span class="card-title">📍 監視設定</span>
            </div>

            <form id="monitorForm">
              <div class="form-group">
                <label class="form-label">監視対象パビリオン（複数選択可）</label>
                <div class="pavilion-checkboxes">
                  {checkbox_html}
                </div>
              </div>

              <div class="form-group">
                <label class="form-label">🎫 チケットID（カンマ区切り）</label>
                <input type="text" name="ticket_ids" id="ticket_ids" value="{saved.get('ticket_ids', 'TEST123,TEST456')}" placeholder="例: ABC123,DEF456" required>
              </div>

              <div class="form-group">
                <label class="form-label">📅 入場日（YYYYMMDD形式）</label>
                <input type="text" name="entrance_date" id="entrance_date" value="{saved.get('entrance_date', '20251007')}" placeholder="例: 20251007" pattern="[0-9]{{8}}" required>
              </div>

              <div class="form-group">
                <label class="form-label">⏱️ 監視間隔（秒）</label>
                <input type="number" name="interval" id="interval" value="{saved.get('interval', 2)}" min="0.5" step="0.5">
              </div>

              <div class="form-group">
                <label class="form-label">🔄 POST試行時間（秒）</label>
                <input type="number" name="post_duration" id="post_duration" value="{saved.get('post_duration', 60)}" min="10" step="5">
              </div>

              <div class="btn-group">
                <div>
                  <label class="form-label">⏲️ POST最小間隔（秒）</label>
                  <input type="number" name="post_min" id="post_min" value="{saved.get('post_min', 0.5)}" min="0.1" step="0.1">
                </div>
                <div>
                  <label class="form-label">⏲️ POST最大間隔（秒）</label>
                  <input type="number" name="post_max" id="post_max" value="{saved.get('post_max', 2.0)}" min="0.1" step="0.1">
                </div>
              </div>

              <div class="form-group" style="margin-top: 20px;">
                <label class="form-label">🍪 Cookie</label>
                <div class="input-group">
                  <input type="text" id="cookieInput" name="cookie" value="{saved.get('cookie','test_session_id=dummy_for_testing')}" required>
                  <button type="button" class="btn btn-secondary" onclick="pasteCookie()">📋 Paste</button>
                </div>
              </div>

              <div class="form-group" style="margin-top: 16px;">
                <label class="form-label">🔔 Discord Webhook URL（任意）</label>
                <div class="input-group">
                  <input type="text" id="webhookUrl" name="webhook_url" value="{saved.get('webhook_url', '')}" placeholder="https://discord.com/api/webhooks/...">
                  <button type="button" class="btn btn-secondary" onclick="testWebhook()">🔔 Test</button>
                </div>
              </div>

              <div class="btn-group-full" style="margin-top: 24px;">
                <button type="button" class="btn btn-success" onclick="saveSettings()">💾 設定を保存</button>
                <div class="btn-group">
                  <button type="button" class="btn btn-primary" onclick="startMonitor()">▶️ 監視開始</button>
                  <button type="button" class="btn btn-danger" onclick="stopMonitor()">⏹️ 停止</button>
                </div>
                {"<button type='button' class='btn btn-warning' onclick='openTestServer()'>🧪 テストサーバー確認</button>" if TEST_MODE else ""}
              </div>
            </form>

            <div id="statusMessage" class="status-box" style="display: none;"></div>
          </div>

          <!-- Cookie監視カード -->
          <div class="card">
            <div class="card-header">
              <span class="card-title">🍪 Cookie自動更新</span>
            </div>

            <div id="cookieStatus" class="cookie-status">
              <div><strong>ステータス:</strong> <span id="cookieStatusText">未チェック</span></div>
              <div><strong>最終チェック:</strong> <span id="cookieLastCheck">-</span></div>
            </div>

            <div class="cookie-controls">
              <button type="button" class="btn btn-primary" onclick="checkCookie()">🔍 手動チェック</button>
              <button type="button" class="btn btn-success" onclick="startCookieMonitor()">▶️ 監視開始</button>
              <button type="button" class="btn btn-danger" onclick="stopCookieMonitor()">⏹️ 監視停止</button>
            </div>

            <div class="interval-input">
              <label><strong>監視間隔（分）:</strong></label>
              <input type="number" id="cookieInterval" value="5" min="1" max="60">
            </div>
          </div>
        </div>

        <script>
          let statusCheckInterval = null;

          function showMessage(msg, color="green", clearAfter=0) {{
              const box = document.getElementById("statusMessage");
              box.textContent = msg;
              box.style.display = "block";

              const colorMap = {{
                green: {{ bg: "linear-gradient(135deg, #d4edda, #c3e6cb)", text: "#155724", border: "#4CAF50" }},
                blue: {{ bg: "linear-gradient(135deg, #cce5ff, #b8daff)", text: "#004085", border: "#2196F3" }},
                orange: {{ bg: "linear-gradient(135deg, #fff3cd, #ffeaa7)", text: "#856404", border: "#FF9800" }},
                red: {{ bg: "linear-gradient(135deg, #f8d7da, #f5c6cb)", text: "#721c24", border: "#F44336" }}
              }};

              const style = colorMap[color] || colorMap.green;
              box.style.background = style.bg;
              box.style.color = style.text;
              box.style.border = `2px solid ${{style.border}}`;

              if (clearAfter > 0) {{
                  setTimeout(() => {{
                    box.textContent = "";
                    box.style.display = "none";
                  }}, clearAfter);
              }}
          }}

          // ✅ ステータスコードをリアルタイムで取得して表示
          async function checkStatus() {{
              try {{
                  const res = await fetch('/status');
                  const result = await res.json();
                  
                  if (result.running) {{
                      if (result.state === "posting" && result.status_code !== null) {{
                          // POST中
                          const countText = result.count > 1 ? ` x${{result.count}}` : '';
                          showMessage(`POST処理中... [Status: ${{result.status_code}}]${{countText}}`, "orange");
                      }} else if (result.state === "monitoring") {{
                          // 監視中（空きなし）
                          const countText = result.no_vacancy_count > 1 ? ` x${{result.no_vacancy_count}}` : '';
                          showMessage(`⏳ 監視中（空きなし）${{countText}}`, "blue");
                      }}
                  }} else {{
                      if (statusCheckInterval) {{
                          clearInterval(statusCheckInterval);
                          statusCheckInterval = null;
                      }}
                      if (result.state === "success") {{
                          showMessage("✅ 予約成功！", "green");
                      }}
                  }}
              }} catch (err) {{
                  console.error("Status check error:", err);
              }}
          }}

          function openTestServer() {{
              window.open("http://localhost:5000/status", "_blank");
          }}

          async function pasteCookie() {{
              try {{
                  const text = await navigator.clipboard.readText();
                  if (text.includes("session_id=")) {{
                      document.getElementById("cookieInput").value = text;
                      showMessage("✅ Cookieを貼り付けました", "green", 2000);
                  }} else {{
                      alert("⚠️ Clipboardの内容が session_id= を含んでいません。");
                  }}
              }} catch (err) {{
                  alert("❌ クリップボード読み取り失敗: " + err);
              }}
          }}

          async function testWebhook() {{
              const webhookUrl = document.getElementById("webhookUrl").value;

              if (!webhookUrl) {{
                  alert("⚠️ Webhook URLを入力してください");
                  return;
              }}

              if (!webhookUrl.startsWith("https://discord.com/api/webhooks/")) {{
                  alert("⚠️ 有効なDiscord Webhook URLを入力してください");
                  return;
              }}

              showMessage("📤 テスト通知を送信中...", "blue");

              try {{
                  const res = await fetch("/test_notification", {{
                      method: "POST",
                      headers: {{ "Content-Type": "application/json" }},
                      body: JSON.stringify({{ webhook_url: webhookUrl }})
                  }});

                  if (!res.ok) {{
                      throw new Error(`HTTP error! status: ${{res.status}}`);
                  }}

                  const result = await res.json();

                  if (result.success) {{
                      showMessage("✅ Discord通知テスト成功！Discordをチェックしてください", "green", 5000);
                  }} else {{
                      showMessage(`❌ 通知失敗: ${{result.message}}`, "red", 5000);
                  }}
              }} catch (err) {{
                  console.error("Webhook test error:", err);
                  showMessage(`❌ エラー: ${{err.message || err}}`, "red", 5000);
              }}
          }}

          async function saveSettings() {{
              const selectedPavilions = Array.from(
                document.querySelectorAll('input[name="pavilion_ids"]:checked')
              ).map(cb => cb.value);

              if (selectedPavilions.length === 0) {{
                  alert("⚠️ 少なくとも1つのパビリオンを選択してください");
                  return;
              }}

              const ticketIds = document.getElementById("ticket_ids").value;
              const entranceDate = document.getElementById("entrance_date").value;

              if (!entranceDate.match(/^\d{{8}}$/)) {{
                  alert("⚠️ 入場日はYYYYMMDD形式（8桁の数字）で入力してください");
                  return;
              }}

              const data = {{
                  pavilion_ids: selectedPavilions,
                  ticket_ids: ticketIds,
                  entrance_date: entranceDate,
                  interval: parseFloat(document.getElementById("interval").value),
                  post_duration: parseInt(document.getElementById("post_duration").value),
                  post_min: parseFloat(document.getElementById("post_min").value),
                  post_max: parseFloat(document.getElementById("post_max").value),
                  cookie: document.getElementById("cookieInput").value,
                  webhook_url: document.getElementById("webhookUrl").value
              }};

              const res = await fetch("/save", {{
                  method: "POST",
                  headers: {{ "Content-Type": "application/json" }},
                  body: JSON.stringify(data)
              }});
              const result = await res.json();
              showMessage(result.message, "green", 3000);
          }}

          async function startMonitor() {{
              const selectedPavilions = Array.from(
                document.querySelectorAll('input[name="pavilion_ids"]:checked')
              ).map(cb => cb.value);

              if (selectedPavilions.length === 0) {{
                  alert("⚠️ 少なくとも1つのパビリオンを選択してください");
                  return;
              }}

              const cookie = document.getElementById("cookieInput").value;
              if (!cookie || !cookie.includes("session_id=")) {{
                  alert("⚠️ 有効なCookieを入力してください");
                  return;
              }}

              const entranceDate = document.getElementById("entrance_date").value;
              if (!entranceDate.match(/^\d{{8}}$/)) {{
                  alert("⚠️ 入場日はYYYYMMDD形式（8桁の数字）で入力してください");
                  return;
              }}

              const formData = new FormData();
              formData.append("pavilion_ids", selectedPavilions.join(","));
              formData.append("ticket_ids", document.getElementById("ticket_ids").value);
              formData.append("entrance_date", entranceDate);
              formData.append("interval", document.getElementById("interval").value);
              formData.append("post_duration", document.getElementById("post_duration").value);
              formData.append("post_min", document.getElementById("post_min").value);
              formData.append("post_max", document.getElementById("post_max").value);
              formData.append("cookie", cookie);
              formData.append("webhook_url", document.getElementById("webhookUrl").value);

              const res = await fetch("/start", {{ method: "POST", body: formData }});
              const result = await res.json();
              showMessage(result.status, "blue");

              // ✅ 1秒ごとにステータスをチェック開始
              setTimeout(() => {{
                  if (statusCheckInterval) clearInterval(statusCheckInterval);
                  statusCheckInterval = setInterval(checkStatus, 1000);
              }}, 1000);
          }}

          async function stopMonitor() {{
              const res = await fetch("/stop", {{ method: "POST" }});
              const result = await res.json();
              showMessage(result.status, "red", 3000);
              
              // ✅ ステータスチェックを停止
              if (statusCheckInterval) {{
                  clearInterval(statusCheckInterval);
                  statusCheckInterval = null;
              }}
          }}

          // Cookie監視機能
          let cookieStatusInterval = null;

          async function updateCookieStatus() {{
              try {{
                  const res = await fetch('/cookie/status');
                  const status = await res.json();

                  document.getElementById('cookieStatusText').textContent = status.message;
                  document.getElementById('cookieLastCheck').textContent = status.last_check || '-';

                  // ステータスに応じてスタイルを変更
                  const statusDiv = document.getElementById('cookieStatus');
                  statusDiv.classList.remove('valid', 'invalid');

                  if (status.valid === true) {{
                      statusDiv.classList.add('valid');
                  }} else if (status.valid === false) {{
                      statusDiv.classList.add('invalid');
                  }}
              }} catch (err) {{
                  console.error('Cookie status check error:', err);
              }}
          }}

          async function checkCookie() {{
              try {{
                  const res = await fetch('/cookie/check', {{ method: 'POST' }});
                  const result = await res.json();
                  if (result.success) {{
                      showMessage('🔍 Cookie チェック開始...', 'blue', 3000);
                      // 2秒後にステータス更新
                      setTimeout(updateCookieStatus, 2000);
                  }} else {{
                      showMessage(result.message, 'orange', 3000);
                  }}
              }} catch (err) {{
                  showMessage('❌ エラー: ' + err, 'red', 3000);
              }}
          }}

          async function startCookieMonitor() {{
              try {{
                  const interval = parseInt(document.getElementById('cookieInterval').value);
                  const res = await fetch('/cookie/monitor/start', {{
                      method: 'POST',
                      headers: {{ 'Content-Type': 'application/json' }},
                      body: JSON.stringify({{ interval: interval }})
                  }});
                  const result = await res.json();

                  if (result.success) {{
                      showMessage(result.message, 'green', 3000);
                      // 定期的にステータスを更新
                      if (cookieStatusInterval) clearInterval(cookieStatusInterval);
                      cookieStatusInterval = setInterval(updateCookieStatus, 5000);
                      updateCookieStatus();
                  }} else {{
                      showMessage(result.message, 'orange', 3000);
                  }}
              }} catch (err) {{
                  showMessage('❌ エラー: ' + err, 'red', 3000);
              }}
          }}

          async function stopCookieMonitor() {{
              try {{
                  const res = await fetch('/cookie/monitor/stop', {{ method: 'POST' }});
                  const result = await res.json();

                  if (result.success) {{
                      showMessage(result.message, 'red', 3000);
                      if (cookieStatusInterval) {{
                          clearInterval(cookieStatusInterval);
                          cookieStatusInterval = null;
                      }}
                  }} else {{
                      showMessage(result.message, 'orange', 3000);
                  }}
              }} catch (err) {{
                  showMessage('❌ エラー: ' + err, 'red', 3000);
              }}
          }}

          // ページ読み込み時にCookieステータスを取得
          window.addEventListener('load', () => {{
              updateCookieStatus();
              // 10秒ごとにステータスを更新
              setInterval(updateCookieStatus, 10000);
          }});
        </script>
      </body>
    </html>
    """

# =============================
# APIエンドポイント
# =============================

@app.post("/save")
async def save(request: Request):
    data = await request.json()
    save_settings(data)
    return JSONResponse({"message": "✅ 設定を保存しました"})

# ✅ ステータスコードを返す新しいエンドポイント
@app.get("/status")
async def get_status():
    global running, last_status_code, status_code_count, no_vacancy_count, current_state
    return JSONResponse({
        "running": running,
        "status_code": last_status_code,
        "count": status_code_count,
        "no_vacancy_count": no_vacancy_count,
        "state": current_state
    })

@app.post("/start")
async def start(
    pavilion_ids: str = Form(...),
    ticket_ids: str = Form(...),
    entrance_date: str = Form(...),
    interval: float = Form(...),
    post_duration: int = Form(...),
    post_min: float = Form(...),
    post_max: float = Form(...),
    cookie: str = Form(...),
    webhook_url: str = Form(default="")
):
    global running, monitor_thread, last_status_code, status_code_count, no_vacancy_count, current_state
    if running:
        return {"status": "⚠️ すでに監視中です"}

    pavilions = [p.strip() for p in pavilion_ids.split(",") if p.strip()]
    ticket_list = [t.strip() for t in ticket_ids.split(",") if t.strip()]

    save_settings({
        "pavilion_ids": pavilions,
        "ticket_ids": ticket_ids,
        "entrance_date": entrance_date,
        "interval": interval,
        "post_duration": post_duration,
        "post_min": post_min,
        "post_max": post_max,
        "cookie": cookie,
        "webhook_url": webhook_url
    })

    running = True
    last_status_code = None  # ✅ リセット
    status_code_count = 0  # ✅ リセット
    no_vacancy_count = 0  # ✅ リセット
    current_state = "starting"  # ✅ リセット
    monitor_thread = threading.Thread(
        target=monitor_task,
        args=(pavilions, interval, post_duration, cookie, ticket_list, entrance_date, post_min, post_max, webhook_url)
    )
    monitor_thread.start()
    return {"status": f"✅ 監視開始（{interval}秒間隔、{len(pavilions)}件、日付:{entrance_date}）"}

@app.post("/stop")
async def stop():
    global running
    running = False
    return {"status": "🛑 停止しました"}


@app.post("/test_notification")
async def test_notification(request: Request):
    """
    Discord Webhookのテスト通知を送信
    設定が正しいか確認するためのエンドポイント
    """
    try:
        data = await request.json()
        webhook_url = data.get("webhook_url", "")

        print(f"🔔 テスト通知リクエスト受信: {webhook_url[:50]}...")

        # バリデーション
        if not webhook_url:
            print("⚠️ Webhook URLが空です")
            return JSONResponse({"success": False, "message": "Webhook URLが空です"})

        if not webhook_url.startswith("https://discord.com/api/webhooks/"):
            print("⚠️ 無効なWebhook URL形式")
            return JSONResponse({"success": False, "message": "無効なWebhook URLです"})

        # Discord通知を送信
        message = {
            "content": "🔔 **テスト通知**\n\nEXPO予約ツールからのテスト通知です。\n設定が正しく動作しています！",
            "username": "EXPO予約Bot"
        }

        print(f"📤 Discordへ送信中...")
        response = requests.post(webhook_url, json=message, timeout=10)

        print(f"📨 Discord APIレスポンス: {response.status_code}")

        if response.status_code == 204:
            print("✅ Discord通知送信成功")
            return JSONResponse({"success": True, "message": "送信成功"})
        else:
            error_msg = f"Discord API エラー: {response.status_code}"
            print(f"❌ {error_msg}")
            try:
                error_detail = response.json()
                print(f"   詳細: {error_detail}")
                error_msg += f" - {error_detail}"
            except:
                pass
            return JSONResponse({"success": False, "message": error_msg})

    except Exception as e:
        print(f"❌ テスト通知エラー: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"success": False, "message": f"エラー: {str(e)}"})


# =============================
# Cookie監視エンドポイント
# =============================

@app.post("/cookie/check")
async def cookie_check():
    """
    手動でCookie有効性をチェック
    """
    global cookie_status

    if cookie_status["checking"]:
        return JSONResponse({
            "success": False,
            "message": "既にチェック実行中です"
        })

    # バックグラウンドでチェック実行
    def run_check():
        is_valid = check_cookie_validity()
        if not is_valid:
            relogin_and_update_cookie()

    threading.Thread(target=run_check, daemon=True).start()

    return JSONResponse({
        "success": True,
        "message": "チェック開始しました"
    })


@app.post("/cookie/monitor/start")
async def cookie_monitor_start(request: Request):
    """
    Cookie監視を開始
    """
    global cookie_monitor_running, cookie_monitor_thread, cookie_monitor_interval

    data = await request.json()
    interval = data.get("interval", 5)  # デフォルト5分

    if cookie_monitor_running:
        return JSONResponse({
            "success": False,
            "message": "既に監視中です"
        })

    cookie_monitor_interval = interval
    cookie_monitor_running = True

    cookie_monitor_thread = threading.Thread(
        target=cookie_monitor_loop,
        daemon=True
    )
    cookie_monitor_thread.start()

    return JSONResponse({
        "success": True,
        "message": f"Cookie監視を開始しました（間隔: {interval}分）"
    })


@app.post("/cookie/monitor/stop")
async def cookie_monitor_stop():
    """
    Cookie監視を停止
    """
    global cookie_monitor_running

    if not cookie_monitor_running:
        return JSONResponse({
            "success": False,
            "message": "監視は実行されていません"
        })

    cookie_monitor_running = False

    return JSONResponse({
        "success": True,
        "message": "Cookie監視を停止しました"
    })


@app.get("/cookie/status")
async def cookie_status_get():
    """
    現在のCookieステータスを取得
    """
    global cookie_status, cookie_monitor_running, cookie_monitor_interval

    # 最新のCookieを取得
    current_cookie = ""
    settings = load_settings()
    if "cookie" in settings:
        current_cookie = settings["cookie"][:100] + "..."  # 先頭100文字のみ

    # 更新検知用のバージョン（cookie.txt の mtime）
    try:
        cookie_file_path = os.path.join(str(BASE_DIR), COOKIE_FILE) if not os.path.isabs(COOKIE_FILE) else COOKIE_FILE
        cookie_version = os.path.getmtime(cookie_file_path) if os.path.exists(cookie_file_path) else None
    except Exception:
        cookie_version = None

    return JSONResponse({
        "valid": cookie_status["valid"],
        "last_check": cookie_status["last_check"],
        "message": cookie_status["message"],
        "checking": cookie_status["checking"],
        "monitoring": cookie_monitor_running,
        "interval": cookie_monitor_interval,
        "cookie_preview": current_cookie,
        "cookie_version": cookie_version
    })


if __name__ == "__main__":
    print(f"""
    {'='*60}
    EXPO予約監視ツール - {'🧪 テストモード' if TEST_MODE else '🔴 本番モード'}
    {'='*60}
    
    起動URL: http://localhost:8080
    
    {"⚠️ テストモード有効 - ダミーサーバー (localhost:5000) に接続" if TEST_MODE else "🔴 本番モード - 実際のEXPOサーバーに接続"}
    """)
    uvicorn.run(app, host="0.0.0.0", port=8080)