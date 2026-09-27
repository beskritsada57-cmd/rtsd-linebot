import os
import requests
from flask import Flask, request, abort, jsonify
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    LocationMessageContent
)

app = Flask(__name__)

# =========================================================================
# 🔴 1. ข้อมูล LINE Bot ของคุณ (ใส่ค่าจริงถูกต้องแล้ว)
# =========================================================================

CHANNEL_SECRET = os.getenv('CHANNEL_SECRET', '95fadcaa0b4890bf137239eb0122230c')
CHANNEL_ACCESS_TOKEN = os.getenv('CHANNEL_ACCESS_TOKEN', 'tZwEj7/Os0MEb2g5oQMsZc6/8Uvt0AID8SVj/O5dyRkph1hgP8H3JSdduIh+SIXjQI1rPILjCx3ZVuG+WszETDZXOZZ2oXki4wCIF/kz26gjfE+iz8GCQtYCj4cbFLv3EQrOv/YrsWJ/VwMDns4f7gdB04t89/1O/w1cDnyilFU=')



configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# =========================================================================
# 🔴 2. ข้อมูล ArcGIS Geoportal RTSD
# =========================================================================
PORTAL_URL = "https://geoportal.rtsd.mi.th/portal"
PORTAL_USER = "goc01@2024"
PORTAL_PASS = "GEOINT@rtsd_09_69"
TARGET_LAYER_URL = "https://geoportal.rtsd.mi.th/arcgis/rest/services/Hosted/incident_reports_template/FeatureServer/0"


def get_arcgis_token():
    """ขอ Token จาก Portal"""
    try:
        url = f"{PORTAL_URL.rstrip('/')}/sharing/rest/generateToken"
        res = requests.post(url, data={
            'username': PORTAL_USER,
            'password': PORTAL_PASS,
            'client': 'referer',
            'referer': PORTAL_URL,
            'expiration': 15,
            'f': 'json'
        }, verify=False, timeout=10)
        return res.json().get('token')
    except Exception as e:
        print(f"Error get token: {e}")
        return None


def add_to_geoportal(lat, lon, title, address):
    """ปักหมุดเหตุการณ์ลงแผนที่ ArcGIS Geoportal RTSD ในเลเยอร์แจ้งเหตุใหม่"""
    token = get_arcgis_token()
    if not token:
        return False
    
    feature = [{
        "geometry": {
            "x": float(lon),
            "y": float(lat),
            "spatialReference": {"wkid": 4326}
        },
        "attributes": {
            "title": str(title)[:250],
            "address": str(address)[:250],
            "incident_type": "แจ้งเหตุผ่าน LINE",
            "reporter": "ผู้ใช้งาน LINE",
            "status": "รอดำเนินการ",
            "report_time": "ปัจจุบัน"
        }
    }]
    
    try:
        url = f"{TARGET_LAYER_URL}/addFeatures"
        res = requests.post(url, data={
            'features': requests.compat.json.dumps(feature),
            'token': token,
            'f': 'json'
        }, verify=False, timeout=15)
        result = res.json()
        return bool(result.get('addResults', [{}])[0].get('success'))
    except Exception as e:
        print(f"Error adding feature: {e}")
        return False



# =========================================================================
# 3. จุดรับ Webhook จาก LINE
# =========================================================================
@app.route("/", methods=['GET'])
def index():
    return "✅ LINE Bot Webhook for RTSD GIS is Running Online!"


@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)

    # ดักกรณี LINE กดปุ่ม Verify (จะไม่มี signature หรือไม่มี events)
    if not signature or body == '{"events":[],"destination":""}' or '"events":[]' in body:
        return 'OK', 200

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        return 'Invalid signature', 400
    except Exception as e:
        print(f"Error handling event: {e}")
        return 'OK', 200

    return 'OK', 200



# =========================================================================
# 🔴 3. Google Sheets Webhook URL
# =========================================================================
GOOGLE_SHEET_URL = "https://script.google.com/macros/s/AKfycbw5Gepu7a5s9j1vXtgE55403L0K3sKtOcpUNArNCm6RJO1ulNp735XyZgAbTlMBwxI/exec"


def save_to_google_sheet(lat, lon, title, address):
    """ส่งข้อมูลไปบันทึกลงตาราง Google Sheets อัตโนมัติ"""
    try:
        payload = {
            "title": str(title),
            "address": str(address),
            "latitude": float(lat),
            "longitude": float(lon)
        }
        res = requests.post(GOOGLE_SHEET_URL, json=payload, timeout=10)
        return res.status_code == 200
    except Exception as e:
        print(f"Error saving to Google Sheets: {e}")
        return False


# กรณีผู้ใช้แชร์พิกัดสถานที่ (Location)
@handler.add(MessageEvent, message=LocationMessageContent)
def handle_location(event):
    lat = event.message.latitude
    lon = event.message.longitude
    address = event.message.address or "ไม่ระบุที่อยู่"
    title = event.message.title or "จุดแจ้งเหตุ"

    # 1. บันทึกลง Google Sheets ทันที
    sheet_saved = save_to_google_sheet(lat, lon, title, address)

    # 2. ตอบกลับผู้ใช้ใน LINE
    reply = (f"✅ ได้รับรายงานเหตุการณ์เรียบร้อยแล้ว!\n\n"
             f"📍 พิกัด: {lat}, {lon}\n"
             f"🏠 สถานที่: {address}\n\n"
             f"ระบบได้บันทึกข้อมูลเข้าสู่ฐานข้อมูลเรียบร้อยแล้วครับ")

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply)]
            )
        )

    # 3. พยายามปักหมุดลง ArcGIS Portal RTSD เบื้องหลัง
    try:
        add_to_geoportal(lat, lon, f"แจ้งเหตุ: {title}", address)
    except Exception as e:
        print(f"GIS Background push error: {e}")




from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    QuickReply,
    QuickReplyItem,
    MessageAction,
    LocationAction
)

# กรณีผู้ใช้พิมพ์ข้อความธรรมดา
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event):
    user_text = event.message.text.strip()
    
    # ถ้าพิมพ์แจ้งเหตุ หรือทักทาย ให้เด้งปุ่ม Dropdown (Quick Reply) ขึ้นมาให้เลือก
    if user_text in ['ช่วย', 'แจ้งเหตุ', 'menu', 'วิธีใช้', 'สวัสดี', 'hi', 'hello']:
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label="🌊 น้ำท่วมขัง", text="แจ้งเหตุ: น้ำท่วมขัง")),
            QuickReplyItem(action=MessageAction(label="🚧 ถนนชำรุด", text="แจ้งเหตุ: ถนนชำรุด")),
            QuickReplyItem(action=MessageAction(label="⛰️ ดินถล่ม", text="แจ้งเหตุ: ดินถล่ม")),
            QuickReplyItem(action=MessageAction(label="💥 อุบัติเหตุ", text="แจ้งเหตุ: อุบัติเหตุ")),
            QuickReplyItem(action=LocationAction(label="📍 ส่งพิกัดทันที"))
        ])
        
        reply_message = TextMessage(
            text="🚨 ระบบรับแจ้งเหตุเตือนภัย RTSD 🚨\n\nกรุณาเลือกประเภทเหตุการณ์ หรือกดส่งพิกัดได้เลยครับ 👇",
            quick_reply=quick_reply
        )
        
    elif user_text.startswith("แจ้งเหตุ:"):
        incident_name = user_text.replace("แจ้งเหตุ:", "").strip()
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=LocationAction(label="📍 กดส่งพิกัดจุดเกิดเหตุ"))
        ])
        reply_message = TextMessage(
            text=f"รับทราบเหตุ: [{incident_name}]\n\n👉 กรุณากดปุ่ม '📍 กดส่งพิกัดจุดเกิดเหตุ' ด้านล่างนี้เพื่อปักหมุดแผนที่ครับ",
            quick_reply=quick_reply
        )
        
    else:
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุเตือนภัย", text="แจ้งเหตุ")),
            QuickReplyItem(action=LocationAction(label="📍 ส่งพิกัดสถานที่"))
        ])
        reply_message = TextMessage(
            text=f"ได้รับข้อความ: \"{user_text}\"\n\nหากต้องการรายงานเหตุการณ์ กรุณาเลือกเมนูด้านล่างนี้ครับ 👇",
            quick_reply=quick_reply
        )

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[reply_message]
            )
        )



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
