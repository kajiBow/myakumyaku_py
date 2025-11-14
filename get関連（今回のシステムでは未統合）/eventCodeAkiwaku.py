"""
時間帯別詳細空き情報取得ツール
"""

import requests
import json
from datetime import datetime, timedelta

# =========================================================
# 設定
# =========================================================

COOKIE_FILE = "cookie.txt"

# time_status の意味
TIME_STATUS = {
    0: "🟢 空きあり",
    1: "🟡 残りわずか",
    2: "🔴 予約不可"
}


# =========================================================
# Cookie読み込み
# =========================================================

def load_cookie():
    """保存されたCookieを読み込み"""
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        print(f"❌ {COOKIE_FILE} が見つかりません")
        return None


# =========================================================
# イベント詳細取得
# =========================================================

def fetch_event_detail(cookie, event_code, ticket_ids, entrance_date, channel=4):
    """
    特定イベントの詳細情報（時間帯別空き状況）を取得
    
    Args:
        cookie: 認証Cookie
        event_code: イベントコード（例: "IC0C", "H1H9"）
        ticket_ids: チケットIDのリスト
        entrance_date: 入場日（YYYYMMDD形式）
        channel: 予約チャンネル（デフォルト: 4=空き枠先着予約）
    
    Returns:
        dict: イベント詳細情報
    """
    url = f"https://ticket.expo2025.or.jp/api/d/events/{event_code}"
    
    # entrance_dateをYYYYMMDD形式に変換
    if isinstance(entrance_date, datetime):
        entrance_date_str = entrance_date.strftime("%Y%m%d")
    else:
        entrance_date_str = entrance_date
    
    params = {
        "ticket_ids[]": ",".join(ticket_ids),
        "entrance_date": entrance_date_str,
        "channel": str(channel)
    }
    
    headers = {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0",
        "x-api-lang": "ja"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        
        if response.status_code != 200:
            print(f"❌ エラー: Status {response.status_code}")
            print(f"Response: {response.text[:200]}")
            return None
        
        return response.json()
        
    except Exception as e:
        print(f"❌ 取得エラー: {e}")
        return None


# =========================================================
# 時間帯別表示
# =========================================================

def display_event_schedule(event_data):
    """
    イベントの時間帯別空き状況を表示
    
    Args:
        event_data: イベント詳細データ
    """
    if not event_data:
        print("⚠️ データがありません")
        return
    
    event_code = event_data.get("event_code", "不明")
    event_name = event_data.get("event_name", "不明")
    event_schedules = event_data.get("event_schedules", {})
    
    print("\n" + "=" * 80)
    print(f"【{event_code}】{event_name}")
    print("=" * 80)
    
    if not event_schedules:
        print("⚠️ スケジュール情報がありません")
        return
    
    # 時間帯でソート
    sorted_times = sorted(event_schedules.keys())
    
    print("\n時間帯別空き状況:")
    print("-" * 80)
    
    available_count = 0
    almost_full_count = 0
    full_count = 0
    
    for time_key in sorted_times:
        schedule = event_schedules[time_key]
        
        schedule_name = schedule.get("schedule_name", "不明")
        start_time = schedule.get("start_time", "")
        end_time = schedule.get("end_time", "")
        time_status = schedule.get("time_status", 2)
        unavailable_reason = schedule.get("unavailable_reason", "")
        
        # 時刻フォーマット
        if len(start_time) == 4 and len(end_time) == 4:
            formatted_time = f"{start_time[:2]}:{start_time[2:]} - {end_time[:2]}:{end_time[2:]}"
        else:
            formatted_time = schedule_name
        
        # ステータス
        status_text = TIME_STATUS.get(time_status, "⚪ 不明")
        
        # カウント
        if time_status == 0:
            available_count += 1
        elif time_status == 1:
            almost_full_count += 1
        elif time_status == 2:
            full_count += 1
        
        # 表示
        reason_text = f"  (理由: {unavailable_reason})" if unavailable_reason and time_status == 2 else ""
        print(f"  {status_text}  {formatted_time}{reason_text}")
    
    # サマリー
    print("\n" + "-" * 80)
    print(f"📊 サマリー:")
    print(f"   🟢 空きあり: {available_count}件")
    print(f"   🟡 残りわずか: {almost_full_count}件")
    print(f"   🔴 予約不可: {full_count}件")
    print(f"   合計: {len(event_schedules)}件")
    print("=" * 80)


def display_multiple_events(event_data_list):
    """
    複数イベントの時間帯別空き状況を一覧表示
    
    Args:
        event_data_list: イベントデータのリスト
    """
    print("\n" + "=" * 80)
    print("📊 複数イベント時間帯別空き状況")
    print("=" * 80)
    
    for event_data in event_data_list:
        if not event_data:
            continue
        
        event_code = event_data.get("event_code", "不明")
        event_name = event_data.get("event_name", "不明")
        event_schedules = event_data.get("event_schedules", {})
        
        # 空き状況をカウント
        available_times = []
        for time_key in sorted(event_schedules.keys()):
            schedule = event_schedules[time_key]
            if schedule.get("time_status") == 0:  # 空きあり
                start_time = schedule.get("start_time", "")
                if len(start_time) == 4:
                    formatted_time = f"{start_time[:2]}:{start_time[2:]}"
                    available_times.append(formatted_time)
        
        # 表示
        print(f"\n【{event_code}】{event_name[:50]}...")
        if available_times:
            print(f"  🟢 空きあり時間帯 ({len(available_times)}件): {', '.join(available_times[:10])}")
            if len(available_times) > 10:
                print(f"     ...他 {len(available_times) - 10}件")
        else:
            print(f"  🔴 空き時間帯なし")
    
    print("\n" + "=" * 80)


# =========================================================
# メイン実行
# =========================================================

def main():
    """メイン処理"""
    print("=" * 80)
    print("📊 EXPO2025 時間帯別詳細空き情報確認ツール")
    print("=" * 80)
    
    # Cookie読み込み
    cookie = load_cookie()
    if not cookie:
        return
    
    # 設定入力
    print("\n📝 設定入力")
    print("-" * 80)
    
    # チケットID
    ticket_input = input("チケットID（カンマ区切り、Enter=form_data.jsonから）: ").strip()
    
    if not ticket_input:
        try:
            with open("form_data.json", "r", encoding="utf-8") as f:
                form_data = json.load(f)
                ticket_ids_str = form_data.get("ticket_ids", "")
                ticket_ids = [tid.strip() for tid in ticket_ids_str.split(",")]
                print(f"  → {ticket_ids}")
        except FileNotFoundError:
            print("❌ form_data.json が見つかりません")
            return
    else:
        ticket_ids = [tid.strip() for tid in ticket_input.split(",")]
    
    # 入場日
    date_input = input("入場日（YYYYMMDD、Enter=明日）: ").strip()
    
    if not date_input:
        entrance_date = (datetime.now() + timedelta(days=1)).strftime("%Y%m%d")
        print(f"  → {entrance_date}")
    else:
        entrance_date = date_input
    
    # イベントコード入力
    print("\nイベントコード入力:")
    print("  単一: H1H9")
    print("  複数: H1H9,IC0C,H5H9（カンマ区切り）")
    event_codes_input = input("イベントコード: ").strip()
    
    if not event_codes_input:
        print("❌ イベントコードを入力してください")
        return
    
    event_codes = [code.strip() for code in event_codes_input.split(",")]
    
    # チャンネル選択
    channel_input = input("チャンネル（0-5、Enter=4:空き枠先着）: ").strip()
    channel = int(channel_input) if channel_input else 4
    
    # 取得実行
    event_data_list = []
    
    for event_code in event_codes:
        print(f"\n📡 {event_code} の詳細情報を取得中...")
        
        event_data = fetch_event_detail(
            cookie=cookie,
            event_code=event_code,
            ticket_ids=ticket_ids,
            entrance_date=entrance_date,
            channel=channel
        )
        
        if event_data:
            event_data_list.append(event_data)
            print(f"✅ 取得成功")
            
            # JSONに保存
            filename = f"event_{event_code}_{entrance_date}.json"
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(event_data, f, ensure_ascii=False, indent=2)
            print(f"📁 {filename} に保存")
        else:
            print(f"❌ 取得失敗")
    
    # 表示
    if len(event_data_list) == 1:
        # 単一イベント：詳細表示
        display_event_schedule(event_data_list[0])
    elif len(event_data_list) > 1:
        # 複数イベント：サマリー表示
        display_multiple_events(event_data_list)
    else:
        print("\n⚠️ 取得できたイベントがありません")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ 中断されました")
    except Exception as e:
        print(f"\n❌ エラー: {e}")
        import traceback
        traceback.print_exc()