"""
全パビリオン空き状況一覧確認ツール
大まかな空き状況（date_status）を確認
"""

import requests
import json
from datetime import datetime, timedelta

# =========================================================
# 設定
# =========================================================

COOKIE_FILE = "cookie.txt"

# date_status の意味
DATE_STATUS = {
    0: "🟢 空きあり",
    1: "🟡 残りわずか", 
    2: "🔴 満席/予約不可"
}

# チャンネル
CHANNELS = {
    0: "来場日時予約",
    1: "超早割特別抽選",
    2: "2か月前抽選",
    3: "7日前抽選",
    4: "空き枠先着予約",
    5: "当日登録"
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
# 全イベント取得
# =========================================================

def fetch_all_events(cookie, ticket_ids, entrance_date, channel=4, event_name=""):
    """
    全イベントの大まかな空き状況を取得
    
    Args:
        cookie: 認証Cookie
        ticket_ids: チケットIDのリスト
        entrance_date: 入場日（YYYYMMDD形式）
        channel: 予約チャンネル
        event_name: イベント名検索クエリ
    
    Returns:
        list: イベント情報のリスト
    """
    base_url = "https://ticket.expo2025.or.jp/api/d/events"
    
    # entrance_dateをYYYYMMDD形式に変換
    if isinstance(entrance_date, datetime):
        entrance_date_str = entrance_date.strftime("%Y%m%d")
    else:
        entrance_date_str = entrance_date
    
    params = {
        "ticket_ids[]": ",".join(ticket_ids),
        "entrance_date": entrance_date_str,
        "count": "1",
        "limit": "100",  # 一度に多く取得
        "event_type": "0",
        "channel": str(channel),
        "event_name": event_name
    }
    
    headers = {
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0",
        "x-api-lang": "ja"
    }
    
    all_events = []
    next_token = ""
    page = 1
    
    print(f"\n📡 全イベント情報取得中...")
    print(f"   入場日: {entrance_date_str}")
    print(f"   チャンネル: {channel} ({CHANNELS.get(channel, '不明')})")
    
    while True:
        if next_token:
            params["next_token"] = next_token
        
        try:
            response = requests.get(base_url, params=params, headers=headers, timeout=10)
            
            if response.status_code != 200:
                print(f"❌ エラー: Status {response.status_code}")
                print(f"Response: {response.text[:200]}")
                break
            
            data = response.json()
            event_list = data.get("list", [])
            
            if event_list:
                all_events.extend(event_list)
                print(f"   ページ {page}: {len(event_list)}件取得（累計: {len(all_events)}件）")
            
            if data.get("exists_next") and data.get("next_token"):
                next_token = data["next_token"]
                page += 1
            else:
                break
                
        except Exception as e:
            print(f"❌ 取得エラー: {e}")
            break
    
    print(f"\n✅ 合計 {len(all_events)}件のイベントを取得")
    
    return all_events


# =========================================================
# 表示関数
# =========================================================

def display_events_overview(events, filter_status=None):
    """
    イベント一覧を表形式で表示
    
    Args:
        events: イベント情報のリスト
        filter_status: フィルタするdate_status（None=全て、0=空きあり、1=残りわずか、2=満席）
    """
    if not events:
        print("\n⚠️ イベントが見つかりませんでした")
        return
    
    # フィルタリング
    if filter_status is not None:
        events = [e for e in events if e.get("date_status") == filter_status]
    
    print("\n" + "=" * 100)
    print("📊 全パビリオン空き状況一覧")
    print("=" * 100)
    
    # ヘッダー
    print(f"{'ステータス':<12} {'コード':<8} {'イベント名':<60}")
    print("-" * 100)
    
    # イベントをdate_statusでソート（空きあり優先）
    sorted_events = sorted(events, key=lambda x: (x.get("date_status", 2), x.get("event_code", "")))
    
    for event in sorted_events:
        event_code = event.get("event_code", "不明")
        event_name = event.get("event_name", "不明")
        date_status = event.get("date_status", 2)
        
        # イベント名を短縮
        if len(event_name) > 58:
            event_name = event_name[:55] + "..."
        
        status_text = DATE_STATUS.get(date_status, "⚪ 不明")
        
        print(f"{status_text:<15} {event_code:<8} {event_name:<60}")
    
    print("=" * 100)
    
    # 統計情報
    print(f"\n📈 統計情報:")
    status_counts = {}
    for event in events:
        status = event.get("date_status", 2)
        status_counts[status] = status_counts.get(status, 0) + 1
    
    for status, count in sorted(status_counts.items()):
        status_text = DATE_STATUS.get(status, "不明")
        print(f"   {status_text}: {count}件")
    
    print(f"   合計: {len(events)}件")


def display_available_only(events):
    """空きありのイベントのみ表示"""
    available_events = [e for e in events if e.get("date_status") == 0]
    
    if not available_events:
        print("\n⚠️ 空きありのイベントが見つかりませんでした")
        return
    
    print("\n" + "=" * 100)
    print("🟢 空きありイベント一覧")
    print("=" * 100)
    
    for event in sorted(available_events, key=lambda x: x.get("event_code", "")):
        event_code = event.get("event_code", "")
        event_name = event.get("event_name", "")
        print(f"\n【{event_code}】{event_name}")
        
        # サマリーがあれば表示
        summary = event.get("event_summary", "")
        if summary and len(summary) > 0:
            summary_short = summary[:100] + "..." if len(summary) > 100 else summary
            print(f"   {summary_short}")
    
    print("\n" + "=" * 100)
    print(f"🟢 空きあり: {len(available_events)}件")
    print("=" * 100)


def export_to_csv(events, filename="events_overview.csv"):
    """CSV形式でエクスポート"""
    import csv
    
    with open(filename, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ステータス", "イベントコード", "イベント名", "概要"])
        
        for event in sorted(events, key=lambda x: (x.get("date_status", 2), x.get("event_code", ""))):
            status = DATE_STATUS.get(event.get("date_status", 2), "不明")
            code = event.get("event_code", "")
            name = event.get("event_name", "")
            summary = event.get("event_summary", "")
            
            writer.writerow([status, code, name, summary])
    
    print(f"\n📁 {filename} に保存しました")


# =========================================================
# メイン実行
# =========================================================

def main():
    """メイン処理"""
    print("=" * 100)
    print("📊 EXPO2025 全パビリオン空き状況一覧確認ツール")
    print("=" * 100)
    
    # Cookie読み込み
    cookie = load_cookie()
    if not cookie:
        return
    
    # 設定入力
    print("\n📝 設定入力")
    print("-" * 100)
    
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
    
    # チャンネル選択
    print("\nチャンネル選択:")
    for ch, name in CHANNELS.items():
        print(f"  {ch}: {name}")
    channel_input = input("チャンネル（0-5、Enter=4:空き枠先着）: ").strip()
    channel = int(channel_input) if channel_input else 4
    
    # イベント名検索（オプション）
    event_name = input("イベント名検索（オプション、Enter=全て）: ").strip()
    
    # 表示モード選択
    print("\n表示モード:")
    print("  1: 全イベント表示")
    print("  2: 空きありのみ表示")
    print("  3: 残りわずかのみ表示")
    mode_input = input("モード（1-3、Enter=1）: ").strip()
    mode = int(mode_input) if mode_input else 1
    
    # イベント取得
    events = fetch_all_events(
        cookie=cookie,
        ticket_ids=ticket_ids,
        entrance_date=entrance_date,
        channel=channel,
        event_name=event_name
    )
    
    if not events:
        print("\n⚠️ 取得できたイベントがありません")
        return
    
    # JSONに保存
    json_filename = f"all_events_{entrance_date}_ch{channel}.json"
    with open(json_filename, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)
    print(f"\n📁 {json_filename} に詳細データを保存")
    
    # 表示
    if mode == 1:
        display_events_overview(events)
    elif mode == 2:
        display_available_only(events)
    elif mode == 3:
        display_events_overview(events, filter_status=1)
    
    # CSV出力オプション
    csv_output = input("\nCSV形式で出力しますか？ (y/n, Enter=n): ").strip().lower()
    if csv_output == 'y':
        csv_filename = f"events_{entrance_date}_ch{channel}.csv"
        export_to_csv(events, csv_filename)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️ 中断されました")
    except Exception as e:
        print(f"\n❌ エラー: {e}")
        import traceback
        traceback.print_exc()