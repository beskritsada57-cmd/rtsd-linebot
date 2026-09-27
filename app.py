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
TARGET_LAYER_URL = "https://geoportal.rtsd.mi.th/arcgis/rest/services/Hosted/itic_cctv/FeatureServer/0"


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
    """ปักหมุดเหตุการณ์ลงแผนที่ ArcGIS Geoportal RTSD"""
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
            "organization": str(address)[:100],
            "status": "แจ้งเตือนภัยใหม่ (LINE)",
            "lastupdate": "ปัจจุบัน"
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



# กรณีผู้ใช้แชร์พิกัดสถานที่ (Location)
@handler.add(MessageEvent, message=LocationMessageContent)
def handle_location(event):
    lat = event.message.latitude
    lon = event.message.longitude
    address = event.message.address or "ไม่ระบุที่อยู่"
    title = event.message.title or "จุดแจ้งเหตุ"

    # บันทึกลง ArcGIS Portal RTSD
    saved = add_to_geoportal(lat, lon, f"แจ้งเหตุ: {title}", address)

    if saved:
        reply = (f"✅ ได้รับรายงานเหตุการณ์เรียบร้อยแล้ว!\n\n"
                 f"📍 พิกัด: {lat}, {lon}\n"
                 f"🏠 สถานที่: {address}\n\n"
                 f"ระบบได้ปักหมุดข้อมูลลงบนแผนที่ Geoportal RTSD ให้ทันทีแล้วครับ!")
    else:
        reply = "⚠️ ได้รับพิกัดแล้ว แต่ระบบ GIS ขัดข้องชั่วคราว"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply)]
            )
        )


# กรณีผู้ใช้พิมพ์ข้อความธรรมดา
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event):
    user_text = event.message.text.strip()
    
    if user_text in ['ช่วย', 'แจ้งเหตุ', 'menu', 'วิธีใช้']:
        reply = ("🚨 ระบบรับแจ้งเหตุเตือนภัย RTSD 🚨\n\n"
                 "ท่านสามารถส่งพิกัดมาปักหมุดบนแผนที่ได้ง่าย ๆ:\n"
                 "👉 เพียงกดปุ่ม '+' ด้านล่างซ้าย\n"
                 "👉 เลือก 'ตำแหน่งที่ตั้ง' (Location)\n"
                 "👉 ปักหมุดแล้วกดแชร์เข้ามาได้เลยครับ!")
    else:
        reply = (f"รับข้อความ: \"{user_text}\"\n\n"
                 "หากต้องการแจ้งเหตุและปักหมุดแผนที่ GIS กรุณากดปุ่ม '+' แล้วเลือก 'ตำแหน่งที่ตั้ง' (Location) ส่งพิกัดเข้ามาได้เลยครับ")

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply)]
            )
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
