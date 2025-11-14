import base64
import re
from PIL import Image
import cv2
import numpy as np
import io

def extract_secret_from_base64_qr(base64_data):
    """
    Base64形式のQRコード画像からTOTPシークレットキーを抽出
    OpenCV版（Windows対応）
    """
    try:
        # Base64部分を抽出
        if ',' in base64_data:
            base64_string = base64_data.split(',')[1]
        else:
            base64_string = base64_data
        
        # Base64デコード
        image_data = base64.b64decode(base64_string)
        
        # NumPy配列に変換
        nparr = np.frombuffer(image_data, np.uint8)
        
        # OpenCVで画像を読み込み
        img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)
        
        if img is None:
            print("❌ 画像の読み込みに失敗しました")
            return None
        
        # QRコードデコーダーを作成
        qr_detector = cv2.QRCodeDetector()
        
        # QRコードをデコード
        qr_data, points, _ = qr_detector.detectAndDecode(img)
        
        if not qr_data:
            print("❌ QRコードが検出されませんでした")
            return None
        
        print(f"📱 QRコードの内容:\n{qr_data}\n")
        
        # otpauth://totp/EXPO:user@email.com?secret=XXXXX&issuer=EXPO
        # ↑ このフォーマットからsecretを抽出
        secret_match = re.search(r'secret=([A-Z2-7]+)', qr_data)
        
        if secret_match:
            secret_key = secret_match.group(1)
            print(f"✅ シークレットキー抽出成功: {secret_key}\n")
            return secret_key
        else:
            print("❌ シークレットキーが見つかりませんでした")
            print(f"QRデータ: {qr_data}")
            return None
            
    except Exception as e:
        print(f"❌ エラーが発生しました: {e}")
        import traceback
        traceback.print_exc()
        return None


# =========================================================
# 使用例
# =========================================================

if __name__ == "__main__":
    import pyotp
    import json
    import os
    from dotenv import load_dotenv

    # 環境変数を読み込み
    load_dotenv()

    # Base64データを環境変数から取得
    qr_base64 = os.getenv("QR_BASE64")

    if not qr_base64:
        print("❌ エラー: QR_BASE64 環境変数が設定されていません")
        print("📝 .env ファイルに QR_BASE64 を設定してください")
        exit(1)
    
    print("=" * 60)
    print("📱 QRコードからシークレットキーを抽出")
    print("=" * 60)
    print()
    
    # シークレットキーを抽出
    secret = extract_secret_from_base64_qr(qr_base64)
    
    if secret:
        print("=" * 60)
        print("🎉 抽出完了！")
        print("=" * 60)
        print(f"\nシークレットキー: {secret}")
        
        # 保存
        config = {"totp_secret": secret}
        with open("auth_config.json", "w") as f:
            json.dump(config, f, indent=2)
        print("\n📁 auth_config.json に保存しました")
        
        # テスト生成
        totp = pyotp.TOTP(secret)
        otp = totp.now()
        print(f"\n🔑 現在のOTP: {otp}")
        print("Google Authenticatorアプリと一致するか確認してください")
    else:
        print("\n❌ シークレットキーの抽出に失敗しました")