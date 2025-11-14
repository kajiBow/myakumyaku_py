"""
EXPO2025 当日予約自動化ツール
- 指定時刻に自動でAPIリクエストを送信
- Discord Webhookで予約成功通知
- 補正値を適用した時間調整
"""

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import uvicorn
import requests
import threading
import time
import random
import json
import os
from datetime import datetime
import subprocess
import sys
from pathlib import Path
import logging

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# ✅ /cookie/status と /status エンドポイントのログを非表示にする
class StatusEndpointFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return "GET /status" not in message and "GET /cookie/status" not in message

# ✅ ログフィルタを適用
logging.getLogger("uvicorn.access").addFilter(StatusEndpointFilter())

# =========================================================
# グローバル変数
# =========================================================

# 【予約処理の状態管理】
running = False                # 予約処理の実行状態フラグ
task_thread = None             # 予約処理を実行するスレッド
last_status_code = None        # 最後に受信したHTTPステータスコード
status_code_count = 0          # 同じステータスコードの連続回数
request_count = 0              # リクエストの試行回数

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
# ベースディレクトリ（このファイルがある場所）
BASE_DIR = Path(__file__).resolve().parent
SAVE_FILE = str(BASE_DIR / "form_data.json")
SCHEDULE_FILE = str(BASE_DIR / "scheduled_tasks.json")
COOKIE_FILE = str(BASE_DIR / "cookie.txt")  # autologin.pyが生成するCookieファイル
# 補正値ファイルはプロジェクト相対に変更（存在しなければ load 時に空辞書）
ADJUST_FILE = str((BASE_DIR / "adjustments" / "adjustments.json"))

# 【スケジュール監視】
scheduler_running = True       # スケジュール監視スレッドの実行フラグ
scheduler_thread = None        # スケジュール監視スレッド


# =========================================================
# 補正値関連関数
# =========================================================

def load_adjustments():
    """
    補正値ファイル（adjustments.json）を読み込む
    
    Returns:
        dict: パビリオンコードをキー、補正分をバリューとした辞書
              例: {"H1H9": -10, "H5H0": 5}
    """
    try:
        with open(ADJUST_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"⚠️ 補正データファイルが見つかりません: {ADJUST_FILE}")
    except Exception as e:
        print(f"⚠️ 補正データ読み込みエラー: {e}")
    return {}


def adjust_time_for_post(pavilion: str, t: str) -> str:
    """
    ebii時間を補正してPOST用の時刻に変換
    
    Args:
        pavilion: パビリオンコード（例: "H1H9"）
        t: ebii表示時刻（HHMM形式、例: "1845"）
    
    Returns:
        str: 補正後の時刻（HHMM形式、例: "1835"）
    
    Example:
        >>> adjust_time_for_post("H1H9", "1845")
        "1835"  # -10分補正の場合
    """
    if not t.isdigit() or len(t) != 4:
        print(f"⚠️ 無効な時間形式: {t}")
        return t

    adjustments = load_adjustments()
    offset = adjustments.get(pavilion, 0)  # 補正値を取得（デフォルト0）

    # HHMMを分単位に変換
    hh, mm = int(t[:2]), int(t[2:])
    total = hh * 60 + mm + offset
    total %= 24 * 60  # 24時間を超えた場合の処理

    # 分単位からHHMMに戻す
    new_hh, new_mm = divmod(total, 60)
    adjusted = f"{new_hh:02d}{new_mm:02d}"

    print(f"🕒 {pavilion}: {t} → {adjusted}（補正 {offset:+}分）")
    return adjusted


# =========================================================
# 設定ファイル操作
# =========================================================

def load_form_data():
    """
    保存されたフォーム入力内容を読み込む
    
    Returns:
        dict: フォームデータ（ticket_ids, entrance_date, cookie等）
    """
    if os.path.exists(SAVE_FILE):
        with open(SAVE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_form_data(data: dict):
    """
    フォーム入力内容を保存
    
    Args:
        data: 保存するフォームデータ
    """
    with open(SAVE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_schedules():
    """
    時間指定予約のスケジュール設定を読み込む
    
    Returns:
        list: スケジュールのリスト
    """
    if os.path.exists(SCHEDULE_FILE):
        with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_schedules(schedules: list):
    """
    スケジュール設定を保存
    
    Args:
        schedules: 保存するスケジュールのリスト
    """
    with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
        json.dump(schedules, f, ensure_ascii=False, indent=2)


# =========================================================
# Cookie監視機能
# =========================================================

def check_cookie_validity():
    """
    現在のフォームのCookieをテスト（autologin.pyを使わず直接テスト）
    
    Returns:
        bool: Cookie有効ならTrue
    """
    global cookie_status
    
    cookie_status["checking"] = True
    cookie_status["message"] = "チェック中..."
    
    try:
        # form_data.json からCookieを取得
        form_data = load_form_data()
        cookie = form_data.get("cookie", "")
        
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
            "reason": cookie_status["message"]})
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
        # Python実行ファイルと作業ディレクトリを明示して、相対パス問題を防止
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
        
        # cookie.txt から新しいCookieを読み込み（絶対パス解決）
        cookie_path = os.path.join(str(BASE_DIR), COOKIE_FILE) if not os.path.isabs(COOKIE_FILE) else COOKIE_FILE
        if os.path.exists(cookie_path):
            with open(cookie_path, "r", encoding="utf-8") as f:
                new_cookie = f.read().strip()
            
            if new_cookie:
                # form_data.json を更新
                form_data = load_form_data()
                form_data["cookie"] = new_cookie
                save_form_data(form_data)
                
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


# =========================================================
# Discord通知機能
# =========================================================

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


# =========================================================
# 予約リクエスト処理
# =========================================================

def send_single_request(url, payload, headers, start_timestamp, webhook_url=None, attempt_num=0):
    """
    1回のAPIリクエストを送信（別スレッドで非同期実行）
    
    Args:
        url: APIエンドポイント
        payload: POSTリクエストのボディ
        headers: HTTPヘッダー
        start_timestamp: 処理開始時刻（time.time()）
        webhook_url: Discord Webhook URL（任意）
        attempt_num: 試行回数
    """
    global running, last_status_code, status_code_count
    
    try:
        # APIリクエストを送信
        r = requests.post(url, json=payload, headers=headers, timeout=10)
        
        # ステータスコードの連続回数をカウント
        if r.status_code == last_status_code:
            status_code_count += 1
        else:
            last_status_code = r.status_code
            status_code_count = 1
        
        # 経過時間を計算
        elapsed = int(time.time() - start_timestamp)
        print(f"[試行 #{attempt_num}] Status: {r.status_code} | 経過: {elapsed}秒 | Response: {r.text[:150]}")

        # レスポンスをJSON解析
        try:
            data = r.json()
        except Exception:
            data = {}

        # 予約成功判定（Status 200 かつ必要なキーが含まれる）
        if r.status_code == 200 and (
            "user_visiting_reservation_ids" in data or data == {}
        ):
            print(f"✅ 予約成功！（試行回数: {attempt_num}回、経過時間: {elapsed}秒）")
            running = False  # 予約処理を停止
            
            # Discord通知を送信
            if webhook_url:
                send_discord_notification(
                    webhook_url, 
                    payload.get("event_code", ""), 
                    payload.get("start_time", ""),
                    elapsed
                )
            
    except Exception as e:
        print(f"❌ リクエストエラー: {e}")


def reservation_task(ticket_ids, entrance_date, start_time, event_code, cookie, min_interval, max_interval, time_limit=None, webhook_url=None):
    """
    予約リクエストを連続送信するメイン処理
    
    Args:
        ticket_ids: チケットIDのリスト
        entrance_date: 入場日（yyyymmdd形式）
        start_time: 予約希望時刻（HHMM形式）
        event_code: イベントコード
        cookie: 認証Cookie
        min_interval: 最小送信間隔（秒）
        max_interval: 最大送信間隔（秒）※現在未使用
        time_limit: 制限時間（秒）※Noneの場合は無制限
        webhook_url: Discord Webhook URL（任意）
    """
    global running, last_status_code, status_code_count, request_count
    
    url = "https://ticket.expo2025.or.jp/api/d/user_event_reservations"

    # 補正値を適用して時刻を調整
    adjusted_time = adjust_time_for_post(event_code, start_time)

    # APIリクエストのペイロード
    payload = {
        "ticket_ids": ticket_ids,
        "entrance_date": entrance_date,
        "start_time": adjusted_time,
        "event_code": event_code,
        "registered_channel": "5"
    }

    # HTTPヘッダー
    headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Cookie": cookie,
        "x-api-lang": "ja",
        "User-Agent": "Mozilla/5.0"
    }

    print(f"\n=== POST開始 ===\n対象: {event_code} {adjusted_time}（補正済）\n間隔: {min_interval}秒（レスポンス非同期）\n=================\n")
    
    # 開始時刻を記録
    start_timestamp = time.time()
    request_count = 0  # 試行回数をリセット

    # 予約処理のメインループ
    while running:
        # 制限時間チェック（スケジュール実行の場合のみ）
        if time_limit and (time.time() - start_timestamp) >= time_limit:
            print(f"⏱️ 制限時間（{time_limit}秒）に達しました。プログラム停止（総試行回数: {request_count}回）")
            running = False
            break
        
        # 試行回数をインクリメント
        request_count += 1
        
        # 別スレッドでリクエストを送信（レスポンスを待たない）
        threading.Thread(
            target=send_single_request, 
            args=(url, payload, headers, start_timestamp, webhook_url, request_count),
            daemon=True
        ).start()
        
        # 固定間隔で待機（レスポンスを待たずに次のリクエストを送信）
        time.sleep(min_interval)


# =========================================================
# スケジュール監視機能
# =========================================================

def schedule_monitor():
    """
    時間指定予約のスケジュールを監視し、指定時刻に予約処理を自動開始
    0.5秒ごとに現在時刻をチェックし、スケジュールと一致したら実行
    """
    global scheduler_running, running, task_thread, last_status_code, status_code_count, request_count
    print("📅 スケジュール監視スレッド開始")
    
    while scheduler_running:
        schedules = load_schedules()
        now = datetime.now()
        current_time = now.strftime("%H:%M")
        current_second = now.second
        
        for schedule in schedules:
            # 無効化されたスケジュールはスキップ
            if not schedule.get("enabled", True):
                continue
                
            schedule_time = schedule.get("trigger_time", "")
            
            # 指定時刻の00秒ちょうどに実行
            if schedule_time == current_time and current_second == 0 and not running:
                print(f"🎯 スケジュール実行: {schedule['event_code']} at {current_time}:00")
                
                # 予約タスクを開始（60秒制限付き）
                running = True
                last_status_code = None
                status_code_count = 0
                request_count = 0
                
                # 保存された設定を読み込み
                saved = load_form_data()
                ids = [tid.strip() for tid in saved.get("ticket_ids", "").split(",")]
                
                # 予約処理スレッドを起動
                task_thread = threading.Thread(
                    target=reservation_task,
                    args=(
                        ids,
                        saved.get("entrance_date", ""),
                        schedule.get("start_time", ""),
                        schedule.get("event_code", ""),
                        saved.get("cookie", ""),
                        float(saved.get("min_interval", 0.5)),
                        float(saved.get("max_interval", 3.5)),
                        60,  # 60秒で自動停止
                        saved.get("webhook_url", "")
                    )
                )
                task_thread.start()
                
                # 一度実行したら無効化（再実行を防止）
                schedule["enabled"] = False
                save_schedules(schedules)
        
        time.sleep(0.5)  # 0.5秒ごとにチェック


# =========================================================
# Web UI エンドポイント
# =========================================================

@app.get("/", response_class=HTMLResponse)
async def form_page(request: Request):
    """
    メイン画面を表示
    保存されたフォーム入力内容を読み込んでテンプレートに渡す
    """
    saved = load_form_data()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "saved": saved
    })


@app.get("/schedule", response_class=HTMLResponse)
async def schedule_page(request: Request):
    """
    時間指定予約の管理画面を表示
    登録済みスケジュールの一覧を表示
    """
    schedules = load_schedules()
    return templates.TemplateResponse("schedule.html", {
        "request": request,
        "schedules": schedules
    })


@app.post("/save")
async def save(request: Request):
    """
    フォーム入力内容を保存
    """
    data = await request.json()
    save_form_data(data)
    return JSONResponse({"message": "✅ 入力内容を保存しました"})


@app.get("/status")
async def get_status():
    """
    現在の予約処理のステータスを返す
    フロントエンドがポーリングしてリアルタイム表示に使用
    """
    global running, last_status_code, status_code_count
    return JSONResponse({
        "running": running,
        "status_code": last_status_code,
        "count": status_code_count
    })


@app.post("/start")
async def start(
    ticket_ids: str = Form(...),
    entrance_date: str = Form(...),
    start_time: str = Form(...),
    event_code: str = Form(...),
    cookie: str = Form(...),
    min_interval: float = Form(...),
    max_interval: float = Form(...),
    webhook_url: str = Form(default="")
):
    """
    予約処理を手動で開始（メイン画面の「予約開始」ボタン）
    制限時間なし、成功するまで無限実行
    """
    global running, task_thread, last_status_code, status_code_count, request_count
    
    if running:
        return {"status": "すでに実行中です"}

    # 入力内容を保存
    save_form_data({
        "ticket_ids": ticket_ids,
        "entrance_date": entrance_date,
        "start_time": start_time,
        "event_code": event_code,
        "cookie": cookie,
        "min_interval": min_interval,
        "max_interval": max_interval,
        "webhook_url": webhook_url
    })

    # 状態をリセット
    running = True
    last_status_code = None
    status_code_count = 0
    request_count = 0
    
    # チケットIDをリストに分割
    ids = [tid.strip() for tid in ticket_ids.split(",")]
    
    # 予約処理スレッドを起動（time_limit=Noneで無制限）
    task_thread = threading.Thread(
        target=reservation_task,
        args=(ids, entrance_date, start_time, event_code, cookie, min_interval, max_interval, None, webhook_url)
    )
    task_thread.start()
    
    return {"status": f"予約開始しました（{min_interval}〜{max_interval} 秒間隔）"}


@app.post("/stop")
async def stop():
    """
    実行中の予約処理を停止
    """
    global running
    running = False
    return {"status": "停止しました"}


@app.post("/test_notification")
async def test_notification(request: Request):
    """
    Discord Webhookのテスト通知を送信
    設定が正しいか確認するためのエンドポイント
    """
    data = await request.json()
    webhook_url = data.get("webhook_url", "")
    
    # バリデーション
    if not webhook_url:
        return JSONResponse({"success": False, "message": "Webhook URLが空です"})
    
    if not webhook_url.startswith("https://discord.com/api/webhooks/"):
        return JSONResponse({"success": False, "message": "無効なWebhook URLです"})
    
    try:
        message = {
            "content": "🔔 **テスト通知**\n\nEXPO予約ツールからのテスト通知です。\n設定が正しく動作しています！",
            "username": "EXPO予約Bot"
        }
        response = requests.post(webhook_url, json=message, timeout=5)
        
        if response.status_code == 204:
            return JSONResponse({"success": True, "message": "送信成功"})
        else:
            return JSONResponse({"success": False, "message": f"Discord API エラー: {response.status_code}"})
            
    except Exception as e:
        return JSONResponse({"success": False, "message": str(e)})


@app.post("/schedule/add")
async def add_schedule(request: Request):
    """
    時間指定予約のスケジュールを追加
    """
    data = await request.json()
    schedules = load_schedules()
    schedules.append({
        "trigger_time": data["trigger_time"],
        "event_code": data["event_code"],
        "start_time": data["start_time"],
        "enabled": True
    })
    save_schedules(schedules)
    return JSONResponse({"message": "✅ スケジュールを追加しました"})


@app.post("/schedule/delete")
async def delete_schedule(request: Request):
    """
    スケジュールを削除
    """
    data = await request.json()
    schedules = load_schedules()
    index = data["index"]
    if 0 <= index < len(schedules):
        schedules.pop(index)
        save_schedules(schedules)
        return JSONResponse({"message": "🗑️ スケジュールを削除しました"})
    return JSONResponse({"message": "エラー: 無効なインデックス"})

@app.get("/cookie/log", response_class=HTMLResponse)
async def cookie_log_page(request: Request):
    """Cookie無効履歴ページ"""
    global cookie_invalid_log
    return templates.TemplateResponse("cookie_log.html", {
        "request": request,
        "logs": cookie_invalid_log
    })


@app.post("/cookie/log/clear")
async def cookie_log_clear():
    """履歴をクリア"""
    global cookie_invalid_log
    cookie_invalid_log = []
    return JSONResponse({"message": "履歴をクリアしました"})


# =========================================================
# Cookie監視エンドポイント
# =========================================================

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
    interval = data.get("interval", 10)  # デフォルト5分
    
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
    form_data = load_form_data()
    if "cookie" in form_data:
        current_cookie = form_data["cookie"][:100] + "..."  # 先頭100文字のみ
    
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


# =========================================================
# アプリケーション起動設定
# =========================================================

@app.on_event("startup")
async def startup_event():
    """
    アプリ起動時にスケジュール監視スレッドを開始
    """
    global scheduler_thread
    scheduler_thread = threading.Thread(target=schedule_monitor, daemon=True)
    scheduler_thread.start()


if __name__ == "__main__":
    print(f"""
    起動URL: http://localhost:8090
    """)
    uvicorn.run(app, host="0.0.0.0", port=8090)
    