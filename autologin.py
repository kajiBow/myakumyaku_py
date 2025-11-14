"""
TOTP自動ログイン + Queue-it対応（session_idのみ取得）
"""

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
import pyotp
import json
import time
from datetime import datetime
import requests
import os
from dotenv import load_dotenv

# 環境変数を読み込み
load_dotenv()

# =========================================================
# 設定
# =========================================================

LOGIN_ID = os.getenv("LOGIN_ID", "your_id")
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD", "your_password")

AUTH_CONFIG = "auth_config.json"
COOKIE_FILE = "cookie.txt"
FORM_DATA_FILE = "form_data.json"

USER_AGENT = "Mozilla/5.0"

# Queue-it関連
QUEUE_WAITING_ROOM_DOMAIN = "tktwaitingroom.expo2025.or.jp"
TARGET_DOMAIN = "ticket.expo2025.or.jp"


# =========================================================
# ユーティリティ
# =========================================================

def load_secret():
    """シークレットキー読み込み"""
    try:
        with open(AUTH_CONFIG, "r") as f:
            return json.load(f).get("totp_secret")
    except FileNotFoundError:
        print(f"❌ {AUTH_CONFIG} が見つかりません")
        return None


def generate_otp(secret_key):
    """OTP生成"""
    return pyotp.TOTP(secret_key).now()


def find_element(driver, strategies, wait_time=10):
    """柔軟な要素検索"""
    for by, value in strategies:
        try:
            element = WebDriverWait(driver, wait_time).until(
                EC.presence_of_element_located((by, value))
            )
            print(f"  ✓ 要素発見: {by} = {value}")
            return element
        except:
            continue
    raise Exception("要素が見つかりません")


def get_all_cookies_for_domain(driver, domain):
    """CDP経由で特定ドメインのすべてのCookieを取得"""
    all_cookies = driver.execute_cdp_cmd('Network.getAllCookies', {})
    
    domain_cookies = []
    for cookie in all_cookies['cookies']:
        if domain in cookie.get('domain', ''):
            domain_cookies.append(cookie)
    
    return domain_cookies


def wait_for_queue_bypass(driver, max_wait_time=300):
    """
    Queue-it待機室をバイパス
    
    Args:
        driver: WebDriverインスタンス
        max_wait_time: 最大待機時間（秒）デフォルト5分
    
    Returns:
        bool: バイパス成功したらTrue
    """
    print("\n" + "=" * 70)
    print("🎫 Queue-it待機室チェック")
    print("=" * 70)
    
    start_time = time.time()
    check_interval = 2
    
    while time.time() - start_time < max_wait_time:
        current_url = driver.current_url
        
        if QUEUE_WAITING_ROOM_DOMAIN in current_url:
            elapsed = int(time.time() - start_time)
            print(f"⏳ 待機室で待機中... ({elapsed}秒経過)")
            time.sleep(check_interval)
            
        elif TARGET_DOMAIN in current_url:
            print(f"\n✅ 待機室をバイパス成功！")
            print(f"📍 現在のURL: {current_url}")
            return True
        else:
            print(f"📍 現在のURL: {current_url}")
            time.sleep(check_interval)
    
    print(f"\n⚠️ タイムアウト（{max_wait_time}秒）")
    return False


# =========================================================
# メイン：自動ログイン
# =========================================================

def auto_login(username, password, headless=False, wait_queue=True):
    """
    完全自動ログイン + Queue-it対応 + session_idのみ取得
    """
    secret_key = load_secret()
    if not secret_key:
        return None
    
    # Chrome設定
    chrome_options = Options()
    chrome_options.add_argument(f'user-agent={USER_AGENT}')
    if headless:
        chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    driver = webdriver.Chrome(options=chrome_options)
    driver.execute_cdp_cmd('Network.setUserAgentOverride', {"userAgent": USER_AGENT})
    
    try:
        print("=" * 70)
        print(f"🚀 自動ログイン開始 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70)
        
        # ============================================
        # STEP 1: ログイン
        # ============================================
        
        print("\n【STEP 1】ログインページで認証")
        driver.get("https://ticket.expo2025.or.jp/api/d/expo_login")
        #time.sleep(3)
        
        # 万博ID入力
        print("✅ 万博ID入力")
        username_input = find_element(driver, [
            (By.NAME, "username"),
            (By.NAME, "email"),
            (By.CSS_SELECTOR, "input[type='text']")
        ])
        username_input.clear()
        username_input.send_keys(username)
        print(f"  → {username}")
        
        # パスワード入力
        print("✅ パスワード入力")
        password_input = find_element(driver, [
            (By.NAME, "password"),
            (By.CSS_SELECTOR, "input[type='password']")
        ])
        password_input.clear()
        password_input.send_keys(password)
        print("  → ********")
        
        # ログインボタン
        print("✅ ログインボタンをクリック")
        login_btn = find_element(driver, [
            (By.CSS_SELECTOR, "button[type='submit']")
        ])
        login_btn.click()
        #time.sleep(4)
        
        # ============================================
        # STEP 2: OTP認証
        # ============================================
        
        print("\n【STEP 2】OTP認証")
        otp = generate_otp(secret_key)
        print(f"🔑 OTP生成: {otp}")
        
        # OTP入力
        print("✅ OTP入力")
        otp_input = find_element(driver, [
            (By.NAME, "otp"),
            (By.CSS_SELECTOR, "input[maxlength='6']")
        ])
        otp_input.clear()
        otp_input.send_keys(otp)
        print("  → 入力完了")
        
        # OTP送信
        print("✅ OTP送信ボタンをクリック")
        otp_btn = find_element(driver, [
            (By.CSS_SELECTOR, "button[type='submit']")
        ])
        otp_btn.click()
        #time.sleep(5)
        
        print(f"📍 認証後URL: {driver.current_url}")
        
        # ============================================
        # STEP 3: Queue-it待機室チェック
        # ============================================
        
        current_url = driver.current_url
        
        if QUEUE_WAITING_ROOM_DOMAIN in current_url:
            print(f"\n⚠️ Queue-it待機室に入りました")
            
            if wait_queue:
                if not wait_for_queue_bypass(driver, max_wait_time=300):
                    print("\n❌ 待機室バイパス失敗（タイムアウト）")
                    return None
            else:
                print("\n⏭️ 待機室スキップモード")
                return None
        else:
            print(f"\n✅ 待機室なし")
        
        # ============================================
        # STEP 4: 追加ページ訪問
        # ============================================
        
        #print("\n【STEP 4】追加ページ訪問")
        #driver.get("https://ticket.expo2025.or.jp/api/d/account/info")
        time.sleep(2)
        
        # ============================================
        # STEP 5: session_idのみ取得
        # ============================================
        
        print("\n【STEP 5】session_id取得")
        
        target_domain = "ticket.expo2025.or.jp"
        domain_cookies = get_all_cookies_for_domain(driver, target_domain)
        
        print(f"\n🍪 {target_domain} のCookie数: {len(domain_cookies)}件")
        
        # session_idを探す
        session_id_cookie = None
        for cookie in domain_cookies:
            if cookie.get('name') == 'session_id':
                session_id_cookie = cookie
                break
        
        if not session_id_cookie:
            print("\n❌ session_id Cookieが見つかりませんでした")
            print("\n取得したすべてのCookie:")
            for cookie in domain_cookies:
                print(f"  - {cookie.get('name')}: {cookie.get('value', '')[:40]}...")
            return None
        
        # session_idのみのCookie文字列
        cookie_str = f"session_id={session_id_cookie['value']}"
        
        print(f"\n✅ session_id取得成功")
        print(f"   値: {session_id_cookie['value'][:50]}...")
        print(f"   完全: {cookie_str}")
        
        # 保存
        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
            f.write(cookie_str)
        print(f"\n📁 {COOKIE_FILE} に保存")
        
        # 詳細情報も保存
        session_info = {
            "session_id": session_id_cookie['value'],
            "domain": session_id_cookie.get('domain', ''),
            "path": session_id_cookie.get('path', ''),
            "expires": session_id_cookie.get('expires', ''),
            "httpOnly": session_id_cookie.get('httpOnly', False),
            "secure": session_id_cookie.get('secure', False)
        }
        with open("session_info.json", "w", encoding="utf-8") as f:
            json.dump(session_info, f, ensure_ascii=False, indent=2)
        print(f"📁 session_info.json に詳細を保存")
        
        # メイン設定も更新
        try:
            with open(FORM_DATA_FILE, "r", encoding="utf-8") as f:
                form_data = json.load(f)
            form_data["cookie"] = cookie_str
            with open(FORM_DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(form_data, f, ensure_ascii=False, indent=2)
            print(f"📁 {FORM_DATA_FILE} も更新")
        except FileNotFoundError:
            pass
        
        print("\n" + "=" * 70)
        print("🎉 自動ログイン完了！")
        print("=" * 70)
        
        return cookie_str
        
    except Exception as e:
        print(f"\n❌ エラー: {e}")
        import traceback
        traceback.print_exc()
        
        try:
            driver.save_screenshot("error_screenshot.png")
            print("📸 error_screenshot.png 保存")
        except:
            pass
        
        return None
        
    finally:
        if not headless:
            input("\nEnterキーを押すとブラウザを閉じます...")
        driver.quit()


# =========================================================
# Cookie有効性テスト
# =========================================================

def test_cookie(cookie_str):
    """Cookie有効性テスト"""
    print("\n" + "=" * 70)
    print("🧪 Cookie有効性テスト")
    print("=" * 70)
    
    headers = {
        "Cookie": cookie_str,
        "User-Agent": USER_AGENT,
        "x-api-lang": "ja"
    }
    
    print("\n📡 テストリクエスト送信中...")
    print("URL: https://ticket.expo2025.or.jp/api/d/account/info")
    
    try:
        r = requests.get(
            "https://ticket.expo2025.or.jp/api/d/account/info",
            headers=headers,
            timeout=10
        )
        
        print(f"\n📊 結果:")
        print(f"Status Code: {r.status_code}")
        print(f"Response: {r.text[:200]}...")
        
        if r.status_code == 200:
            print("\n" + "=" * 70)
            print("✅ Cookie有効！予約ツールで使用できます")
            print("=" * 70)
            return True
        else:
            print("\n" + "=" * 70)
            print(f"⚠️ Cookie無効（Status: {r.status_code}）")
            print("=" * 70)
            return False
            
    except Exception as e:
        print(f"\n❌ テストエラー: {e}")
        return False


# =========================================================
# メイン実行
# =========================================================

if __name__ == "__main__":
    import sys

    # 環境変数チェック
    if not os.getenv("LOGIN_ID") or not os.getenv("LOGIN_PASSWORD"):
        print("❌ エラー: LOGIN_ID または LOGIN_PASSWORD が設定されていません")
        print("📝 .env ファイルに設定してください")
        sys.exit(1)

    if len(sys.argv) > 1 and sys.argv[1] in ["--help", "-h"]:
        print("""
使い方:
  python autologin.py              # 通常モード（待機室で待つ）
  python autologin.py --no-wait    # 待機室スキップモード
  python autologin.py --test       # Cookie有効性テストのみ
  python autologin.py --silent     # ヘッドレスモード
        """)
        sys.exit(0)
    
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        try:
            with open(COOKIE_FILE, "r") as f:
                cookie = f.read().strip()
            test_cookie(cookie)
        except FileNotFoundError:
            print(f"❌ {COOKIE_FILE} が見つかりません")
        sys.exit(0)
    
    headless = len(sys.argv) > 1 and sys.argv[1] == "--silent"
    wait_queue = "--no-wait" not in sys.argv
    
    cookie = auto_login(LOGIN_ID, LOGIN_PASSWORD, headless=headless, wait_queue=wait_queue)
    
    if cookie:
        test_cookie(cookie)
    else:
        print("\n❌ ログイン失敗")