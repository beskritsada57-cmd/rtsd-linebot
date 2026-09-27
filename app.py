import os
import base64
import requests
from flask import Flask, request, abort, jsonify
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    TextMessage,
    QuickReply,
    QuickReplyItem,
    MessageAction,
    LocationAction
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    LocationMessageContent,
    ImageMessageContent,
    VideoMessageContent
)

app = Flask(__name__)

# =========================================================================
# 🔴 1. ข้อมูล LINE Bot ของคุณ
# =========================================================================
CHANNEL_SECRET = '95fadcaa0b4890bf137239eb0122230c'
CHANNEL_ACCESS_TOKEN = 'tZwEj7/Os0MEb2g5oQMsZc6/8Uvt0AID8SVj/O5dyRkph1hgP8H3JSdduIh+SIXjQI1rPILjCx3ZVuG+WszETDZxOZZ2oXki4wCIF/kz26gjfE+iz8GCQtYCj4cbFLv3EQrOv/YrsWJ/VwMDns4f7gdB04t89/1O/w1cDnyilFU='

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# =========================================================================
# 🔴 2. ข้อมูล Google Sheets Webhook URL
# =========================================================================
GOOGLE_SHEET_URL = "https://script.google.com/macros/s/AKfycbw5Gepu7a5s9j1vXtgE55403L0K3sKtOcpUNArNCm6RJO1ulNp735XyZgAbTlMBwxI/exec"

# ที่เก็บสถานะการสนทนาชั่วคราวของผู้ใช้ (User State Session)
user_sessions = {}


def save_to_google_sheet(lat, lon, title, address, reporter, urgency, incident_type, file_base64=None, file_name=None, mime_type=None):
    """ส่งข้อมูลครบทั้ง 10 คอลัมน์ (รวมรูปถ่าย/วิดีโอ) ไปบันทึกลง Google Sheets และ Drive"""
    try:
        payload = {
            "title": str(title),
            "address": str(address),
            "latitude": float(lat),
            "longitude": float(lon),
            "reporter": str(reporter),
            "urgency": str(urgency),
            "incident_type": str(incident_type),
            "file_base64": file_base64,
            "file_name": file_name,
            "mime_type": mime_type
        }
        res = requests.post(GOOGLE_SHEET_URL, json=payload, timeout=20)
        return res.status_code == 200
    except Exception as e:
        print(f"Error saving to Google Sheets: {e}")
        return False


@app.route("/", methods=['GET'])
def index():
    return "✅ LINE Bot Webhook for RTSD GIS & Media Storage is Running Online (v2 - Direct Token)!"


@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature', '')
    body = request.get_data(as_text=True)

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
    user_id = event.source.user_id
    lat = event.message.latitude
    lon = event.message.longitude
    address = event.message.address or "ไม่ระบุที่อยู่"
    title = event.message.title or "จุดแจ้งเหตุ"

    session = user_sessions.get(user_id, {})
    session["lat"] = lat
    session["lon"] = lon
    session["address"] = address
    session["title"] = title
    session["waiting_for_media"] = True
    user_sessions[user_id] = session

    # ถามผู้ใช้ต่อว่าต้องการส่งรูปหรือวิดีโอแนบมาด้วยไหม
    quick_reply = QuickReply(items=[
        QuickReplyItem(action=MessageAction(label="⏩ ข้าม (ไม่ส่งรูป)", text="ข้ามการส่งรูป"))
    ])
    
    reply = (f"📍 ได้รับพิกัดเรียบร้อยแล้วครับ!\n\n"
             f"🏠 สถานที่: {address}\n\n"
             f"📸 เพื่อความสมบูรณ์ของข้อมูล กรุณากดถ่ายหรือส่ง [รูปถ่าย] หรือ [คลิปวิดีโอ] หลักฐานเข้ามาได้เลยครับ (หรือกดปุ่ม 'ข้าม' ด้านล่าง)")

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply, quick_reply=quick_reply)]
            )
        )


# กรณีผู้ใช้ส่งรูปถ่าย (Image)
@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image(event):
    user_id = event.source.user_id
    message_id = event.message.id
    
    session = user_sessions.get(user_id, {})
    lat = session.get("lat", 0)
    lon = session.get("lon", 0)
    address = session.get("address", "ไม่ระบุที่อยู่")
    reporter = session.get("user_name", "ผู้ใช้งาน LINE")
    urgency = session.get("urgency", "🟡 ปานกลาง")
    incident_type = session.get("incident_type", "🌊 น้ำท่วมขัง")

    # ดาวน์โหลดรูปจาก LINE API
    file_base64 = None
    try:
        with ApiClient(configuration) as api_client:
            line_blob_api = MessagingApiBlob(api_client)
            image_bytes = line_blob_api.get_message_content(message_id)
            file_base64 = base64.b64encode(image_bytes).decode('utf-8')
    except Exception as e:
        print(f"Error fetching image: {e}")

    file_name = f"LINE_IMG_{message_id}.jpg"
    mime_type = "image/jpeg"

    # บันทึกข้อมูลและรูปเข้า Google Sheets + Drive
    save_to_google_sheet(lat, lon, f"แจ้งเหตุ: {incident_type}", address, reporter, urgency, incident_type, file_base64, file_name, mime_type)

    reply = (f"✅ บันทึกข้อมูลและรูปถ่ายหลักฐานสำเร็จ!\n\n"
             f"👤 ผู้แจ้ง: {reporter}\n"
             f"🚨 เหตุการณ์: {incident_type}\n"
             f"⚠️ ความเร่งด่วน: {urgency}\n"
             f"📸 ไฟล์ภาพ: บันทึกลง Drive RTSD เรียบร้อยแล้วครับ")

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply)]
            )
        )

    if user_id in user_sessions:
        del user_sessions[user_id]


# กรณีผู้ใช้ส่งวิดีโอ (Video)
@handler.add(MessageEvent, message=VideoMessageContent)
def handle_video(event):
    user_id = event.source.user_id
    message_id = event.message.id
    
    session = user_sessions.get(user_id, {})
    lat = session.get("lat", 0)
    lon = session.get("lon", 0)
    address = session.get("address", "ไม่ระบุที่อยู่")
    reporter = session.get("user_name", "ผู้ใช้งาน LINE")
    urgency = session.get("urgency", "🟡 ปานกลาง")
    incident_type = session.get("incident_type", "🌊 น้ำท่วมขัง")

    # ดาวน์โหลดวิดีโอจาก LINE API
    file_base64 = None
    try:
        with ApiClient(configuration) as api_client:
            line_blob_api = MessagingApiBlob(api_client)
            video_bytes = line_blob_api.get_message_content(message_id)
            file_base64 = base64.b64encode(video_bytes).decode('utf-8')
    except Exception as e:
        print(f"Error fetching video: {e}")

    file_name = f"LINE_VDO_{message_id}.mp4"
    mime_type = "video/mp4"

    # บันทึกข้อมูลและคลิปเข้า Google Sheets + Drive
    save_to_google_sheet(lat, lon, f"แจ้งเหตุ: {incident_type}", address, reporter, urgency, incident_type, file_base64, file_name, mime_type)

    reply = (f"✅ บันทึกข้อมูลและคลิปวิดีโอหลักฐานสำเร็จ!\n\n"
             f"👤 ผู้แจ้ง: {reporter}\n"
             f"🚨 เหตุการณ์: {incident_type}\n"
             f"⚠️ ความเร่งด่วน: {urgency}\n"
             f"🎥 คลิปวิดีโอ: บันทึกลง Drive RTSD เรียบร้อยแล้วครับ")

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply)]
            )
        )

    if user_id in user_sessions:
        del user_sessions[user_id]


# กรณีผู้ใช้พิมพ์ข้อความ / กดเลือกปุ่ม Dropdown
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event):
    user_id = event.source.user_id
    user_text = event.message.text.strip()
    
    # ดึงชื่อ Profile ผู้ใช้จาก LINE
    user_name = "ผู้ใช้งาน LINE"
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            profile = line_bot_api.get_profile(user_id)
            user_name = profile.display_name
    except Exception:
        pass

    if user_id not in user_sessions:
        user_sessions[user_id] = {"user_name": user_name}
    else:
        user_sessions[user_id]["user_name"] = user_name

    # กรณีผู้ใช้เลือกข้ามการส่งรูป
    if user_text == "ข้ามการส่งรูป":
        session = user_sessions.get(user_id, {})
        lat = session.get("lat", 0)
        lon = session.get("lon", 0)
        address = session.get("address", "ไม่ระบุที่อยู่")
        urgency = session.get("urgency", "🟡 ปานกลาง")
        incident_type = session.get("incident_type", "🌊 น้ำท่วมขัง")

        save_to_google_sheet(lat, lon, f"แจ้งเหตุ: {incident_type}", address, user_name, urgency, incident_type)

        reply = (f"✅ บันทึกข้อมูลเรียบร้อยแล้ว!\n\n"
                 f"👤 ผู้แจ้ง: {user_name}\n"
                 f"🚨 เหตุการณ์: {incident_type}\n"
                 f"⚠️ ความเร่งด่วน: {urgency}\n"
                 f"📍 พิกัด: {lat}, {lon}\n"
                 f"🏠 สถานที่: {address}\n\n"
                 f"ระบบได้บันทึกข้อมูลเข้าสู่ฐานข้อมูลตาราง RTSD เรียบร้อยแล้วครับ")

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply)]
                )
            )

        if user_id in user_sessions:
            del user_sessions[user_id]
        return

    # ด่านที่ 1: ผู้ใช้เริ่มแจ้งเหตุ -> เด้ง Dropdown ให้เลือก "ประเภทเหตุการณ์"
    if user_text in ['ช่วย', 'แจ้งเหตุ', 'แจ้งเตือน', 'menu', 'วิธีใช้', 'สวัสดี', 'hi', 'hello']:
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label="🌊 น้ำท่วมขัง", text="เลือกเหตุ: 🌊 น้ำท่วมขัง")),
            QuickReplyItem(action=MessageAction(label="🚧 ถนนชำรุด", text="เลือกเหตุ: 🚧 ดินถล่ม / ผิวทางชำรุด")),
            QuickReplyItem(action=MessageAction(label="💥 อุบัติเหตุ", text="เลือกเหตุ: 💥 อุบัติเหตุจราจร")),
            QuickReplyItem(action=MessageAction(label="🔥 ไฟไหม้", text="เลือกเหตุ: 🔥 ไฟไหม้ / หมอกควัน")),
            QuickReplyItem(action=MessageAction(label="📌 อื่นๆ", text="เลือกเหตุ: 📌 อื่นๆ"))
        ])
        reply_message = TextMessage(
            text=f"สวัสดีครับคุณ {user_name} 🚨\nกรุณาเลือก [ประเภทเหตุการณ์] ที่พบด้านล่างนี้ครับ 👇",
            quick_reply=quick_reply
        )


    # ด่านที่ 2: เลือกประเภทเหตุการณ์แล้ว -> เด้ง Dropdown ให้เลือก "ระดับความเร่งด่วน"
    elif user_text.startswith("เลือกเหตุ:"):
        incident = user_text.replace("เลือกเหตุ:", "").strip()
        user_sessions[user_id]["incident_type"] = incident

        quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label="🔴 วิกฤต/ด่วนมาก", text="ระดับ: 🔴 วิกฤต / เร่งด่วนมาก")),
            QuickReplyItem(action=MessageAction(label="🟡 ปานกลาง", text="ระดับ: 🟡 ปานกลาง")),
            QuickReplyItem(action=MessageAction(label="🟢 ปกติ/เฝ้าระวัง", text="ระดับ: 🟢 ปกติ / เฝ้าระวัง"))
        ])
        reply_message = TextMessage(
            text=f"ประเภท: [{incident}]\n\nกรุณาเลือก [ระดับความเร่งด่วน] ครับ 👇",
            quick_reply=quick_reply
        )

    # ด่านที่ 3: เลือกระดับความเร่งด่วนแล้ว -> เด้งปุ่มให้ "กดแชร์พิกัดสถานที่"
    elif user_text.startswith("ระดับ:"):
        urgency = user_text.replace("ระดับ:", "").strip()
        user_sessions[user_id]["urgency"] = urgency

        quick_reply = QuickReply(items=[
            QuickReplyItem(action=LocationAction(label="📍 กดแชร์พิกัดจุดเกิดเหตุ"))
        ])
        reply_message = TextMessage(
            text=f"ระดับความเร่งด่วน: [{urgency}]\n\n👉 ขั้นตอนถัดไป: กรุณากดปุ่ม '📍 กดแชร์พิกัดจุดเกิดเหตุ' ด้านล่างนี้ครับ 👇",
            quick_reply=quick_reply
        )

    else:
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุเตือนภัย", text="แจ้งเหตุ"))
        ])
        reply_message = TextMessage(
            text=f"หากต้องการแจ้งเหตุเตือนภัย กรุณากดปุ่ม '🚨 แจ้งเหตุเตือนภัย' ด้านล่างนี้ได้เลยครับ 👇",
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
