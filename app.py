import os
import re
import time
import base64
import requests
from flask import Flask, request, abort, jsonify
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
import random
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
    QuickReply,
    QuickReplyItem,
    MessageAction,
    LocationAction,
    URIAction
)
from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    LocationMessageContent,
    ImageMessageContent,
    VideoMessageContent,
    FollowEvent
)

app = Flask(__name__)

# เปิดใช้งาน CORS เพื่อให้หน้าเว็บเรียก API ข้ามโดเมนได้ (เช่น file:// หรือ GitHub Pages)
@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
    return response

@app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
@app.route("/<path:path>", methods=["OPTIONS"])
def preflight_cors(path=""):
    resp = app.make_default_options_response()
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
    return resp

# =========================================================================
# 🔴 1. ข้อมูล LINE Bot ของคุณ
# =========================================================================
CHANNEL_SECRET = os.environ.get('CHANNEL_SECRET', '95fadcaa0b4890bf137239eb0122230c')
CHANNEL_ACCESS_TOKEN = os.environ.get('CHANNEL_ACCESS_TOKEN', 'tZwEj7/Os0MEb2g5oQMsZc6/8Uvt0AID8SVj/O5dyRkph1hgP8H3JSdduIh+SIXjQI1rPILjCx3ZVuG+WszETDZxOZZ2oXki4wCIF/kz26gjfE+iz8GCQtYCj4cbFLv3EQrOv/YrsWJ/VwMDns4f7gdB04t89/1O/w1cDnyilFU=')

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# =========================================================================
# 🔴 2. ข้อมูล Google Sheets Webhook URL & Master PIN
# =========================================================================
GOOGLE_SHEET_URL = os.environ.get('GOOGLE_SHEET_URL', "https://script.google.com/macros/s/AKfycbw5Gepu7a5s9j1vXtgE55403L0K3sKtOcpUNArNCm6RJO1ulNp735XyZgAbTlMBwxI/exec")
MASTER_PIN = os.environ.get('MASTER_PIN', 'RTSD2024')

# ที่เก็บสถานะการสนทนาชั่วคราวของผู้ใช้ (User State Session)
user_sessions = {}

# 🛡️ รายชื่อเบอร์โทรศัพท์ผู้ดูแลระบบหลัก (Master Admin Phones)
ADMIN_PHONES = ["0863390614"]

# 🛡️ ฐานข้อมูลผู้ใช้ที่ลงทะเบียนยืนยันตัวตนแล้ว (User Registry & OTP Cache)
registered_users = {}  # { line_user_id: {"name": ..., "phone": ..., "role": ...} }
phone_to_user = {
    "0863390614": {
        "name": "ผู้ดูแลระบบ RTSD",
        "phone": "0863390614",
        "role": "ผู้ดูแลระบบ (Admin)",
        "unit": "กรมแผนที่ทหาร (RTSD)",
        "position": "ผู้ดูแลระบบหลัก",
        "status": "อนุมัติแล้ว",
        "line_user_id": "-"
    }
}
otp_cache = {}         # { phone: {"otp": "123456", "expires_at": timestamp, "user_info": ...} }
ACTIVE_SESSIONS = {}   # token -> {"user": user_info, "phone": phone, "role": role, "expires_at": timestamp}

# 🛡️ ระดับสิทธิ์ยุทธการ RTSD Tactical Roles & Permissions Matrix
TACTICAL_ROLES = {
    "SUPER_ADMIN": "ผู้ดูแลระบบสูงสุด (Super Admin)",
    "TOC_OPERATOR": "ศูนย์ควบคุมยุทธการ (TOC Operator)",
    "FIELD_OFFICER": "หัวหน้าชุดปฏิบัติการ (Field Officer)",
    "OBSERVER": "ผู้ใช้งานทั่วไป (Observer)"
}

def get_user_permissions(role):
    """ส่งคืนรายการสิทธิ์ตามระดับ Role ของผู้ใช้งาน"""
    r = (role or "").lower()
    if any(k in r for k in ["superadmin", "commander", "ผู้บังคับบัญชา", "ผู้ดูแลระบบสูงสุด", "admin", "แอดมิน"]):
        return [
            "manage_users",
            "manage_incidents",
            "view_all_radars",
            "view_telemetry",
            "system_settings",
            "crisis_mode",
            "send_telemetry",
            "record_path",
            "export_data"
        ]
    elif any(k in r for k in ["operator", "ศูนย์ควบคุม", "toc", "โอเปอเรเตอร์"]):
        return [
            "manage_incidents",
            "view_all_radars",
            "view_telemetry",
            "assign_missions",
            "export_data"
        ]
    elif any(k in r for k in ["field", "สนาม", "ชุดปฏิบัติการ", "officer", "เจ้าหน้าที่"]):
        return [
            "view_district_incidents",
            "send_telemetry",
            "record_path",
            "update_mission_status",
            "submit_report"
        ]
    else:
        return [
            "view_public_alerts",
            "report_incident",
            "track_own_reports"
        ]

# 🌐 การตั้งค่าความมั่นคงระบบ (System Security Settings)
# ควบคุมการอนุญาตให้แชร์และแนบลิงก์ Google Maps ใน LINE Bot สำหรับทีมสนาม (สลับเปิด/ปิดได้โดยแอดมิน)
SYSTEM_SETTINGS = {
    "enable_google_maps": True  # ค่าเริ่มต้น: เปิดใช้งาน
}


def fetch_registered_users():
    """ดึงรายชื่อผู้ใช้ที่ลงทะเบียนแล้วจาก Google Sheets เข้ามาเก็บในแคช"""
    global registered_users, phone_to_user
    # คงสถานะผู้ดูแลระบบหลักไว้เสมอ
    phone_to_user["0863390614"] = {
        "name": "ผู้ดูแลระบบ RTSD",
        "phone": "0863390614",
        "role": "ผู้ดูแลระบบสูงสุด (Super Admin)",
        "unit": "กรมแผนที่ทหาร (RTSD)",
        "position": "ผู้ดูแลระบบหลัก",
        "status": "อนุมัติแล้ว",
        "line_user_id": phone_to_user.get("0863390614", {}).get("line_user_id", "-"),
        "permissions": get_user_permissions("ผู้ดูแลระบบสูงสุด (Super Admin)")
    }
    try:
        # ใช้คำสั่ง GET ?sheet=users (Read-Only) ปลอดภัย 100% ไม่สร้างแถวใหม่ใน Google Sheets
        res = requests.get(f"{GOOGLE_SHEET_URL}?sheet=users", timeout=10)
        if res.status_code == 200:
            users = res.json()
            if isinstance(users, list):
                for u in users:
                    picture_profile = "-"
                    unit = "-"
                    position = "-"
                    status = "อนุมัติแล้ว"
                    if isinstance(u, dict):
                        lid = str(u.get("line_user_id", "")).strip()
                        phone = str(u.get("phone_number", "")).strip().replace("-", "").replace(" ", "")
                        name = str(u.get("full_name", "")).strip()
                        role = str(u.get("role", "ผู้ใช้งานทั่วไป")).strip()
                        unit = str(u.get("unit", "-")).strip()
                        position = str(u.get("position", "-")).strip()
                        status = str(u.get("approval_status", u.get("status", "อนุมัติแล้ว"))).strip()
                        picture_profile = str(u.get("picture_profile", u.get("picture_url", "-"))).strip()
                    elif isinstance(u, list) and len(u) >= 4:
                        if str(u[0]).lower() == "registered_at":
                            continue
                        lid = str(u[1]).strip()
                        name = str(u[2]).strip()
                        phone = str(u[3]).strip().replace("-", "").replace(" ", "")
                        role = str(u[4]).strip() if len(u) > 4 else "ผู้ใช้งานทั่วไป"
                        unit = str(u[5]).strip() if len(u) > 5 else "-"
                        position = str(u[6]).strip() if len(u) > 6 else "-"
                        status = str(u[7]).strip() if len(u) > 7 else "อนุมัติแล้ว"
                        picture_profile = str(u[9]).strip() if len(u) > 9 else "-"
                    else:
                        continue

                    if phone in ADMIN_PHONES:
                        role = "ผู้ดูแลระบบสูงสุด (Super Admin)"
                    user_data_item = {
                        "name": name,
                        "phone": phone,
                        "role": role,
                        "unit": unit,
                        "position": position,
                        "status": status,
                        "picture_profile": picture_profile,
                        "permissions": get_user_permissions(role)
                    }
                    if lid and lid != "-":
                        registered_users[lid] = user_data_item
                    if phone:
                        phone_to_user[phone] = {"line_user_id": lid, **user_data_item}
    except Exception as e:
        print(f"Error fetching users: {e}")


def save_registered_user(line_user_id, name, phone, role="ผู้ใช้งาน", unit="-", position="-", status="อนุมัติแล้ว", purpose="-", picture_profile="-", picture_base64=""):
    """บันทึกข้อมูลผู้ใช้ใหม่ลง Google Sheets และแคชในหน่วยความจำ พร้อมรูปโปรไฟล์ (รองรับทั้ง URL และ Base64 จาก Gallery)"""
    global registered_users, phone_to_user
    clean_phone = phone.replace("-", "").replace(" ", "")

    # หากมี LINE User ID และยังไม่มีรูปโปรไฟล์ ให้ดึงรูปจาก LINE อัตโนมัติ
    if line_user_id and str(line_user_id).startswith("U") and (not picture_profile or picture_profile == "-") and not picture_base64:
        try:
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                prof = line_bot_api.get_profile(line_user_id)
                if prof.picture_url:
                    picture_profile = prof.picture_url
        except Exception:
            pass

    user_data = {
        "name": name,
        "phone": clean_phone,
        "role": role,
        "unit": unit,
        "position": position,
        "status": status,
        "purpose": purpose,
        "picture_profile": picture_profile or "-"
    }
    registered_users[line_user_id] = user_data
    phone_to_user[clean_phone] = {"line_user_id": line_user_id, **user_data}
    try:
        payload = {
            "action": "register_user",
            "line_user_id": line_user_id,
            "full_name": name,
            "phone_number": clean_phone,
            "role": role,
            "unit": unit,
            "position": position,
            "approval_status": status,
            "purpose": purpose,
            "picture_profile": picture_profile or "-",
            "picture_base64": picture_base64 or ""
        }
        requests.post(GOOGLE_SHEET_URL, json=payload, timeout=10)
        return True
    except Exception as e:
        print(f"Error registering user: {e}")
        return False


def notify_admins(message_text):
    """ส่งข้อความแจ้งเตือนทาง LINE ไปยังผู้ดูแลระบบ (Admin) ทุกท่าน"""
    admin_lids = set()
    for phone in ADMIN_PHONES:
        u = phone_to_user.get(phone, {})
        lid = u.get("line_user_id")
        if lid and str(lid).startswith("U"):
            admin_lids.add(str(lid))
    for lid, u in registered_users.items():
        if str(lid).startswith("U") and (u.get("phone") in ADMIN_PHONES or "Super Admin" in u.get("role", "") or "แอดมิน" in u.get("role", "")):
            admin_lids.add(str(lid))

    if not admin_lids:
        print(f"[Admin Notify] No Admin LINE ID found to notify. Msg: {message_text[:30]}...")
        return

    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            for target_lid in admin_lids:
                try:
                    line_bot_api.push_message(
                        PushMessageRequest(
                            to=target_lid,
                            messages=[TextMessage(text=message_text)]
                        )
                    )
                except Exception as ex:
                    print(f"Error pushing to admin {target_lid}: {ex}")
    except Exception as e:
        print(f"Error initializing line api for admin push: {e}")


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


def update_google_sheet_status(title=None, timestamp=None, new_status="🟢 แก้ไขแล้วเสร็จ"):
    """ส่งคำสั่งไปค้นหาแถวเดิมและอัปเดตสถานะใน Google Sheets"""
    try:
        payload = {
            "action": "update_status",
            "title": str(title) if title else "",
            "timestamp": str(timestamp) if timestamp else "",
            "status": str(new_status)
        }
        res = requests.post(GOOGLE_SHEET_URL, json=payload, timeout=20)
        if res.status_code == 200:
            return res.json()
        return {"status": "error", "message": f"HTTP {res.status_code}"}
    except Exception as e:
        print(f"Error updating incident status: {e}")
        return {"status": "error", "message": str(e)}


def get_user_incidents(query_text=None, user_name=None):
    """ดึงข้อมูลสถานะการแจ้งเหตุจาก Google Sheets (ผ่าน doGet) เพื่อระบบ Tracking"""
    try:
        res = requests.get(GOOGLE_SHEET_URL, timeout=20)
        if res.status_code != 200:
            return []
        rows = res.json()
        if not rows or len(rows) <= 1:
            return []
        
        data_rows = rows[1:]  # ข้ามแถวหัวตาราง
        matched = []
        
        # ค้นหาจากแถวใหม่ล่าสุดย้อนกลับไปแถวแรก
        for r in reversed(data_rows):
            if len(r) < 9:
                continue
            timestamp = str(r[0])
            title = str(r[1])
            address = str(r[2])
            reporter = str(r[5])
            urgency = str(r[6])
            incident_type = str(r[7])
            status = str(r[8]).strip() or "⏳ รอดำเนินการ"
            media_url = str(r[9]) if len(r) > 9 else "-"
            
            # ถ้าผู้ใช้ระบุรหัสเหตุ เช่น RTSD-1234
            if query_text and (query_text.lower() in title.lower() or query_text.lower() in timestamp.lower()):
                matched.append({
                    "timestamp": timestamp,
                    "title": title,
                    "address": address,
                    "reporter": reporter,
                    "urgency": urgency,
                    "incident_type": incident_type,
                    "status": status,
                    "media_url": media_url
                })
            # ถ้าไม่ระบุรหัส ให้ค้นจากชื่อผู้แจ้ง
            elif user_name and user_name.lower() in reporter.lower():
                matched.append({
                    "timestamp": timestamp,
                    "title": title,
                    "address": address,
                    "reporter": reporter,
                    "urgency": urgency,
                    "incident_type": incident_type,
                    "status": status,
                    "media_url": media_url
                })
        return matched
    except Exception as e:
        print(f"Error fetching incidents for tracking: {e}")
        return []


# ที่เก็บข้อมูลพิกัดสดของหน่วยกำลังพล / ยานพาหนะ (In-memory Active Units)
active_trackers = {}


@app.route("/", methods=['GET'])
def index():
    return ("✅ LINE Bot Webhook with Incident Tracking & Command Dashboard is Running Online!<br><br>"
            "👉 เข้าชมหน้า Dashboard ติดตามสถานการณ์ได้ที่: <a href='/dashboard'><b>/dashboard</b></a><br>"
            "👉 หน้าลงทะเบียนขอใช้งานระบบ (Access Portal): <a href='/register'><b>/register</b></a><br>"
            "👉 เปิดหน้าจอส่งพิกัดสดสำหรับเจ้าหน้าที่ (Live Tracker): <a href='/tracker'><b>/tracker</b></a>")


@app.route("/dashboard", methods=['GET'])
def dashboard():
    """หน้าเว็บ Command Center Dashboard สำหรับแสดงผลสถิติและแผนที่ GIS"""
    html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            headers = {
                'Content-Type': 'text/html; charset=utf-8',
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0'
            }
            return f.read(), 200, headers
    return "Dashboard HTML template not found on server", 404


@app.route("/register", methods=['GET'])
def register_page():
    """หน้าเว็บ Tactical Registration Portal สำหรับยื่นขอใช้งานระบบ RTSD"""
    html_path = os.path.join(os.path.dirname(__file__), "register.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}
    return "Register HTML template not found on server", 404


@app.route("/tracker", methods=['GET'])
def tracker_page():
    """หน้าเว็บ Tactical Live GPS Tracker สำหรับเจ้าหน้าที่เปิดบนมือถือ"""
    html_path = os.path.join(os.path.dirname(__file__), "tracker.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}
    return "Tracker HTML template not found on server", 404


@app.route("/api/tracker/update", methods=['POST'])
def api_tracker_update():
    """รับสัญญาณ Heartbeat พิกัด GPS สดจากมือถือเจ้าหน้าที่ พร้อมรูปโปรไฟล์และทิศทาง Heading"""
    data = request.get_json(silent=True) or {}
    unit_id = str(data.get("unit_id") or data.get("tracker_id") or "UNIT-01").strip()
    commander = str(data.get("commander", "หัวหน้าชุด")).strip()
    pic = str(data.get("picture_profile", "")).strip()

    # หากไม่ได้ส่งรูปมา ให้ค้นหาอัตโนมัติจากฐานข้อมูล Users ตามชื่อหรือเบอร์โทร
    if not pic or pic == "-":
        for phone, u in phone_to_user.items():
            u_name = u.get("name", "")
            if (commander and commander in u_name) or (u_name and u_name in commander):
                pic = u.get("picture_profile", "-")
                break

    active_trackers[unit_id] = {
        "unit_id": unit_id,
        "unit_name": str(data.get("unit_name", "ชุดปฏิบัติการ")),
        "commander": commander,
        "latitude": float(data.get("latitude", 0)),
        "longitude": float(data.get("longitude", 0)),
        "speed": float(data.get("speed", 0)),
        "heading": float(data.get("heading", 0)),
        "battery": int(data.get("battery", 100)),
        "status": str(data.get("status", "🟢 กำลังปฏิบัติภารกิจ")),
        "picture_profile": pic or "-",
        "last_update": data.get("timestamp", time.strftime("%Y-%m-%d %H:%M:%S"))
    }
    return jsonify({"status": "success", "unit_id": unit_id}), 200


@app.route("/api/tracker/units", methods=['GET'])
def api_tracker_units():
    """ส่งรายการพิกัดสดของทุกหน่วยให้ Dashboard และ Geoportal RTSD Sync"""
    return jsonify(list(active_trackers.values())), 200


@app.route("/api/system/settings", methods=['GET', 'POST'])
def api_system_settings():
    """ดึงหรือปรับปรุงการตั้งค่าระบบ (เช่น เปิด/ปิด Google Maps Sharing)"""
    global SYSTEM_SETTINGS
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        if "enable_google_maps" in data:
            SYSTEM_SETTINGS["enable_google_maps"] = bool(data["enable_google_maps"])
        return jsonify({"status": "success", "settings": SYSTEM_SETTINGS}), 200
    return jsonify(SYSTEM_SETTINGS), 200


@app.route("/api/incidents", methods=['GET'])
def api_incidents():
    """API ดึงข้อมูลเหตุการณ์ทั้งหมดจาก Google Sheets สำหรับ Dashboard"""
    try:
        res = requests.get(GOOGLE_SHEET_URL, timeout=20)
        return jsonify(res.json()), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/incident/update-status", methods=['POST'])
def api_update_incident_status():
    """API สำหรับให้ Dashboard หรือระบบภายนอกสั่งเปลี่ยนสถานะของเหตุการณ์เดิม"""
    data = request.get_json(silent=True) or {}
    title = data.get("title", "")
    timestamp = data.get("timestamp", "")
    new_status = data.get("status", "🟢 แก้ไขแล้วเสร็จ")

    result = update_google_sheet_status(title=title, timestamp=timestamp, new_status=new_status)
    return jsonify(result), 200


@app.route("/api/auth/request-otp", methods=['POST'])
def api_request_otp():
    """สร้างรหัส OTP 6 หลัก แล้วส่งเข้าแชท LINE ของเจ้าหน้าที่โดยตรง"""
    data = request.get_json(silent=True) or {}
    phone = str(data.get("phone", "")).strip().replace("-", "").replace(" ", "")

    if not phone or len(phone) < 9:
        return jsonify({"status": "error", "message": "กรุณาระบุเบอร์โทรศัพท์ให้ถูกต้อง (9-10 หลัก)"}), 400

    # ค้นหาข้อมูลผู้ใช้จากเบอร์โทร
    user_info = phone_to_user.get(phone)
    if not user_info:
        fetch_registered_users()
        user_info = phone_to_user.get(phone)

    if phone not in ADMIN_PHONES:
        if not user_info:
            return jsonify({"status": "error", "message": "ไม่พบหมายเลขนี้ในระบบ กรุณายื่นคำขอลงทะเบียนก่อนใช้งาน"}), 404
        user_status = str(user_info.get("status", "")).strip()
        if any(s in user_status for s in ["ระงับสิทธิ์", "ระงับการใช้งาน", "suspended"]):
            return jsonify({"status": "error", "message": "บัญชีผู้ใช้นี้ถูกระงับสิทธิ์การใช้งาน กรุณาติดต่อผู้ดูแลระบบ"}), 403
        if any(s in user_status for s in ["รออนุมัติ", "pending", "รอการอนุมัติ"]):
            return jsonify({"status": "error", "message": "บัญชีของท่านอยู่ระหว่างรอการอนุมัติจากผู้ดูแลระบบ กรุณาติดต่อ Admin"}), 403

    otp_code = f"{random.randint(100000, 999999)}"

    # ตรวจสอบ LINE User ID สำหรับการส่งข้อความ OTP
    target_line_id = user_info.get("line_user_id") if user_info else None
    
    # หากเป็นเบอร์ Admin ให้ค้นหาใน registered_users เพิ่มเติมหากไม่มีใน phone_to_user
    if (not target_line_id or not str(target_line_id).startswith("U")) and phone in ADMIN_PHONES:
        for lid, u in registered_users.items():
            if str(lid).startswith("U") and u.get("phone") in ADMIN_PHONES:
                target_line_id = lid
                break

    if not target_line_id or not str(target_line_id).startswith("U"):
        return jsonify({
            "status": "error",
            "message": "หมายเลขโทรศัพท์นี้ยังไม่ได้เชื่อมต่อกับ LINE Bot กรุณาเพิ่มเพื่อน LINE (@411vtica) แล้วพิมพ์เบอร์โทรส่งเข้าแชตก่อนขอ OTP ครับ"
        }), 400

    # บันทึก OTP ลงแคช
    otp_cache[phone] = {
        "otp": otp_code,
        "expires_at": time.time() + 300,  # 5 นาที
        "user_info": user_info
    }

    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            push_text = (f"🔐 รหัส OTP เข้าสู่ระบบ RTSD Tactical System\n"
                         f"──────────────────────\n"
                         f"👉 รหัส OTP ของคุณคือ: 【 {otp_code} 】\n"
                         f"──────────────────────\n"
                         f"⏰ รหัสนี้มีอายุ 5 นาที\n"
                         f"⚠️ ห้ามแจ้งรหัสนี้แก่บุคคลอื่น เพื่อความปลอดภัยสูงสุด")
            line_bot_api.push_message(
                PushMessageRequest(to=target_line_id, messages=[TextMessage(text=push_text)])
            )
    except Exception as push_err:
        print(f"Error pushing OTP via LINE: {push_err}")
        return jsonify({
            "status": "error",
            "message": f"ไม่สามารถส่งข้อความ OTP ไปยัง LINE ได้ กรุณาตรวจสอบว่าท่านได้บล็อก LINE Bot (@411vtica) หรือไม่ ({str(push_err)})"
        }), 500

    resp = {
        "status": "success",
        "message": "ส่งรหัส OTP เข้า LINE ของท่านเรียบร้อยแล้ว กรุณาเปิดแอป LINE เพื่อดูรหัส",
        "sent_via_line": True,
        "phone": phone
    }
    return jsonify(resp), 200


@app.route("/api/auth/login", methods=['POST'])
def api_auth_login():
    """Unified Authentication API for Web Dashboard & Mobile App
    Supports Phone/Username + Password/PIN, or Commander PIN
    """
    data = request.get_json(silent=True) or {}
    phone = str(data.get("phone", "")).strip().replace("-", "").replace(" ", "")
    username = str(data.get("username", "")).strip().replace("-", "").replace(" ", "").lower()
    password = str(data.get("password", "") or data.get("pin", "")).strip()
    master_pin = str(data.get("master_pin", "")).strip()

    # 1. Master PIN Direct Authentication (Commander War Room)
    if master_pin and master_pin == MASTER_PIN:
        user_info = {
            "name": "ผู้ดูแลระบบสูงสุด (Master Admin)",
            "phone": "0863390614",
            "role": "ผู้ดูแลระบบสูงสุด (Super Admin)",
            "unit": "กรมแผนที่ทหาร (RTSD)",
            "position": "Commander In Chief",
            "status": "อนุมัติแล้ว",
            "permissions": get_user_permissions("ผู้ดูแลระบบสูงสุด (Super Admin)")
        }
        token = f"rtsd-cmd-{int(time.time())}-{random.randint(1000, 9999)}"
        ACTIVE_SESSIONS[token] = {
            "user": user_info,
            "phone": "0863390614",
            "role": user_info["role"],
            "expires_at": time.time() + 86400 * 7
        }
        return jsonify({
            "status": "success",
            "message": "เข้าสู่ระบบด้วยรหัส Commander PIN สำเร็จ",
            "token": token,
            "user": user_info
        }), 200

    # 2. Login with Username / Phone + Password / PIN
    login_id = phone or username
    if not login_id:
        return jsonify({"status": "error", "message": "กรุณาระบุหมายเลขโทรศัพท์หรือชื่อผู้ใช้"}), 400
    if not password:
        return jsonify({"status": "error", "message": "กรุณาระบุรหัสผ่าน หรือ PIN"}), 400

    is_admin_user = (login_id.lower() in [p.lower() for p in ADMIN_PHONES]) or (login_id.lower() in ["admin", "rtsd_admin", "commander", "superadmin", "administrator"])

    # ตรวจสอบรหัสผ่าน: MASTER_PIN หรือ Admin credentials (ไม่อนุญาตให้ใช้รหัส demo ทั่วไป)
    valid_password = (password == MASTER_PIN) or \
                     (password.upper() == MASTER_PIN.upper()) or \
                     (is_admin_user and password.lower() in ["admin1234", "rtsd2024", MASTER_PIN.lower()])

    if not valid_password:
        return jsonify({"status": "error", "message": "รหัสผ่านหรือ PIN ไม่ถูกต้อง"}), 401

    # ค้นหาข้อมูลผู้ใช้ในระบบ
    if login_id not in phone_to_user:
        fetch_registered_users()

    existing_user = phone_to_user.get(login_id)
    if not is_admin_user:
        if not existing_user:
            return jsonify({
                "status": "error",
                "message": "ไม่พบบัญชีผู้ใช้งานนี้ในระบบ กรุณายื่นคำขอลงทะเบียนก่อนใช้งาน หรือติดต่อ Admin"
            }), 404

        u_status = str(existing_user.get("status", "")).strip()
        if any(s in u_status for s in ["ระงับสิทธิ์", "ระงับการใช้งาน", "suspended"]):
            return jsonify({
                "status": "error",
                "message": "บัญชีผู้ใช้นี้ถูกระงับสิทธิ์การเข้าใช้งาน กรุณาติดต่อผู้ดูแลระบบ"
            }), 403

        if any(s in u_status for s in ["รออนุมัติ", "pending", "รอการอนุมัติ"]):
            return jsonify({
                "status": "error",
                "message": "บัญชีของท่านอยู่ระหว่างรอการอนุมัติจากผู้ดูแลระบบ กรุณาติดต่อ Admin หรือรอการอนุมัติผ่านระบบ"
            }), 403

    user_info = existing_user or {
        "name": "ผู้ดูแลระบบสูงสุด (Master Admin)",
        "phone": "0863390614",
        "role": "ผู้ดูแลระบบสูงสุด (Super Admin)",
        "unit": "กรมแผนที่ทหาร (RTSD)",
        "position": "Commander",
        "status": "อนุมัติแล้ว",
        "permissions": get_user_permissions("ผู้ดูแลระบบสูงสุด (Super Admin)")
    }
    if is_admin_user:
        user_info["role"] = "ผู้ดูแลระบบสูงสุด (Super Admin)"
        user_info["permissions"] = get_user_permissions(user_info["role"])

    token = f"rtsd-token-{login_id}-{int(time.time())}-{random.randint(1000, 9999)}"
    ACTIVE_SESSIONS[token] = {
        "user": user_info,
        "phone": user_info.get("phone", login_id),
        "role": user_info.get("role", "หัวหน้าชุดปฏิบัติการ (Field Officer)"),
        "expires_at": time.time() + 86400 * 7
    }

    return jsonify({
        "status": "success",
        "message": f"เข้าสู่ระบบสำเร็จ ({user_info.get('role')})",
        "token": token,
        "user": user_info
    }), 200


@app.route("/api/auth/me", methods=['GET', 'POST'])
def api_auth_me():
    """ตรวจสอบความถูกต้องของ Token และส่งคืนข้อมูลผู้ใช้งานและสิทธิ์ปัจจุบัน"""
    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    if not token:
        data = request.get_json(silent=True) or {}
        token = data.get("token") or request.args.get("token", "")

    if not token:
        return jsonify({"status": "error", "message": "ไม่พบ Access Token"}), 401

    session = ACTIVE_SESSIONS.get(token)
    if not session or time.time() > session.get("expires_at", 0):
        # ตรวจสอบรูปแบบ legacy token
        if token.startswith("token-") or token.startswith("rtsd-"):
            parts = token.split("-")
            phone = parts[1] if len(parts) > 1 and parts[1] != "commander" else "0863390614"
            user_info = phone_to_user.get(phone) or {
                "name": "ผู้ดูแลระบบ RTSD" if phone == "0863390614" else f"เจ้าหน้าที่ ({phone[-4:] if len(phone)>=4 else phone})",
                "phone": phone,
                "role": "ผู้ดูแลระบบสูงสุด (Super Admin)" if phone == "0863390614" else "หัวหน้าชุดปฏิบัติการ (Field Officer)",
                "unit": "กรมแผนที่ทหาร (RTSD)",
                "position": "Officer",
                "status": "อนุมัติแล้ว",
                "permissions": get_user_permissions("ผู้ดูแลระบบสูงสุด (Super Admin)" if phone == "0863390614" else "หัวหน้าชุดปฏิบัติการ (Field Officer)")
            }
            return jsonify({"status": "success", "user": user_info}), 200
        return jsonify({"status": "error", "message": "Session หมดอายุหรือ Token ไม่ถูกต้อง กรุณาเข้าสู่ระบบใหม่"}), 401

    return jsonify({
        "status": "success",
        "user": session["user"]
    }), 200


@app.route("/api/auth/logout", methods=['POST'])
def api_auth_logout():
    """ออกจากระบบและทำลาย Session Token"""
    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    if not token:
        data = request.get_json(silent=True) or {}
        token = data.get("token", "")

    if token and token in ACTIVE_SESSIONS:
        del ACTIVE_SESSIONS[token]

    return jsonify({"status": "success", "message": "ออกจากระบบเรียบร้อยแล้ว"}), 200


@app.route("/api/auth/verify-otp", methods=['POST'])
def api_verify_otp():
    """ตรวจสอบ Password / PIN หรือ OTP หรือ Commander PIN เพื่อออก Token และยืนยันตัวตน"""
    data = request.get_json(silent=True) or {}
    phone = str(data.get("phone", "")).strip().replace("-", "").replace(" ", "")
    username = str(data.get("username", "")).strip().replace("-", "").replace(" ", "").lower()
    password = str(data.get("password", "") or data.get("pin", "")).strip()
    otp = str(data.get("otp", "")).strip()
    master_pin = str(data.get("master_pin", "")).strip()

    # 1. ล็อกอินด้วย Username / เบอร์โทร + Password / PIN
    login_id = phone or username
    if login_id and password:
        is_admin_user = (login_id in [p.lower() for p in ADMIN_PHONES]) or (login_id in ["admin", "rtsd_admin", "commander", "superadmin"])
        
        # ตรวจสอบรหัสผ่าน: MASTER_PIN ("RTSD2024") หรือ Admin credentials (ไม่อนุญาตให้ใช้รหัส demo ทั่วไป)
        valid_password = (password == MASTER_PIN) or (is_admin_user and password.lower() in ["admin1234", "rtsd2024", MASTER_PIN.lower()])
        
        if valid_password:
            existing_user = phone_to_user.get(login_id)
            if not is_admin_user:
                if not existing_user:
                    return jsonify({"status": "error", "message": "ไม่พบบัญชีผู้ใช้งานนี้ในระบบ กรุณายื่นคำขอลงทะเบียนก่อนใช้งาน"}), 404
                u_status = str(existing_user.get("status", "")).strip()
                if any(s in u_status for s in ["ระงับสิทธิ์", "ระงับการใช้งาน", "suspended"]):
                    return jsonify({"status": "error", "message": "บัญชีผู้ใช้นี้ถูกระงับสิทธิ์การใช้งาน กรุณาติดต่อผู้ดูแลระบบ"}), 403
                if any(s in u_status for s in ["รออนุมัติ", "pending", "รอการอนุมัติ"]):
                    return jsonify({"status": "error", "message": "บัญชีของท่านอยู่ระหว่างรอการอนุมัติจากผู้ดูแลระบบ กรุณาติดต่อ Admin หรือรอการอนุมัติ"}), 403

            user_info = existing_user or {
                "name": "ผู้ดูแลระบบสูงสุด (Master Admin)",
                "phone": "0863390614",
                "role": "ผู้ดูแลระบบสูงสุด (Super Admin)",
                "unit": "กรมแผนที่ทหาร (RTSD)",
                "position": "Super Admin",
                "status": "อนุมัติแล้ว"
            }
            if is_admin_user:
                user_info["role"] = "ผู้ดูแลระบบสูงสุด (Super Admin)"
            user_info["permissions"] = get_user_permissions(user_info["role"])
            token = f"token-{login_id}-{int(time.time())}"
            ACTIVE_SESSIONS[token] = {
                "user": user_info,
                "phone": user_info.get("phone", login_id),
                "role": user_info["role"],
                "expires_at": time.time() + 86400 * 7
            }
            return jsonify({
                "status": "success",
                "token": token,
                "user": user_info
            }), 200
        else:
            return jsonify({"status": "error", "message": "เบอร์โทร/Username หรือ Password/PIN ไม่ถูกต้อง"}), 401

    # 2. ตรวจสอบ Master PIN เดี่ยวๆ สำหรับศูนย์บัญชาการ / ผู้บังคับบัญชา
    if master_pin and master_pin == MASTER_PIN:
        user_info = {
            "name": "ผู้ดูแลระบบสูงสุด (Master Admin)",
            "phone": "0863390614",
            "role": "ผู้ดูแลระบบสูงสุด (Super Admin)",
            "unit": "กรมแผนที่ทหาร (RTSD)",
            "position": "Super Admin",
            "permissions": get_user_permissions("ผู้ดูแลระบบสูงสุด (Super Admin)")
        }
        token = f"token-commander-{int(time.time())}"
        ACTIVE_SESSIONS[token] = {
            "user": user_info,
            "phone": "0863390614",
            "role": user_info["role"],
            "expires_at": time.time() + 86400 * 7
        }
        return jsonify({
            "status": "success",
            "token": token,
            "user": user_info
        }), 200

    # 3. ตรวจสอบ OTP ผ่าน LINE
    cached = otp_cache.get(phone)
    if not cached:
        return jsonify({"status": "error", "message": "ไม่พบคำขอ OTP หรือรหัสหมดอายุแล้ว กรุณากดขอใหม่"}), 400

    if time.time() > cached["expires_at"]:
        del otp_cache[phone]
        return jsonify({"status": "error", "message": "รหัส OTP หมดอายุแล้ว (เกิน 5 นาที) กรุณากดขอใหม่"}), 400

    if cached["otp"] != otp:
        return jsonify({"status": "error", "message": "รหัส OTP ไม่ถูกต้อง กรุณาตรวจสอบอีกครั้ง"}), 400

    user_info = cached.get("user_info") or phone_to_user.get(phone)
    if phone not in ADMIN_PHONES:
        if not user_info:
            return jsonify({"status": "error", "message": "ไม่พบบัญชีผู้ใช้งานในระบบ กรุณาลงทะเบียนก่อนใช้งาน"}), 404
        u_status = str(user_info.get("status", "")).strip()
        if any(s in u_status for s in ["ระงับสิทธิ์", "ระงับการใช้งาน", "suspended"]):
            return jsonify({"status": "error", "message": "บัญชีผู้ใช้นี้ถูกระงับสิทธิ์การใช้งาน กรุณาติดต่อผู้ดูแลระบบ"}), 403
        if any(s in u_status for s in ["รออนุมัติ", "pending", "รอการอนุมัติ"]):
            return jsonify({"status": "error", "message": "บัญชีของท่านอยู่ระหว่างรอการอนุมัติจากผู้ดูแลระบบ กรุณาติดต่อ Admin"}), 403

    if not user_info:
        user_info = {
            "name": "ผู้ดูแลระบบ RTSD",
            "phone": phone,
            "role": "ผู้ดูแลระบบสูงสุด (Super Admin)",
            "unit": "กรมแผนที่ทหาร (RTSD)",
            "status": "อนุมัติแล้ว"
        }
    if phone in ADMIN_PHONES:
        user_info["role"] = "ผู้ดูแลระบบสูงสุด (Super Admin)"
    user_info["permissions"] = get_user_permissions(user_info.get("role", ""))
    del otp_cache[phone]

    token = f"token-{phone}-{int(time.time())}"
    ACTIVE_SESSIONS[token] = {
        "user": user_info,
        "phone": phone,
        "role": user_info["role"],
        "expires_at": time.time() + 86400 * 7
    }

    return jsonify({
        "status": "success",
        "token": token,
        "user": user_info
    }), 200


@app.route("/api/auth/register-request", methods=['POST'])
def api_register_request():
    """รับข้อมูลการลงทะเบียนจากหน้าเว็บ /register แล้วบันทึกลง Google Sheets พร้อมส่งข้อความแจ้งเตือนทาง LINE (ถ้ามี)"""
    data = request.get_json(silent=True) or {}
    full_name = str(data.get("full_name", "")).strip()
    phone = str(data.get("phone_number", "")).strip().replace("-", "").replace(" ", "")
    unit = str(data.get("unit", "")).strip()
    position = str(data.get("position", "-")).strip()
    role = str(data.get("role", "ผู้ใช้งานทั่วไป")).strip()
    purpose = str(data.get("purpose", "-")).strip()
    line_user_id = str(data.get("line_user_id", "")).strip()
    picture_profile = str(data.get("picture_profile", "-")).strip()
    picture_base64 = str(data.get("picture_base64", "")).strip()

    if not phone or len(phone) < 9 or len(phone) > 10:
        return jsonify({"status": "error", "message": "หมายเลขโทรศัพท์ไม่ถูกต้อง (ต้องเป็น 10 หลัก)"}), 400

    if not full_name:
        return jsonify({"status": "error", "message": "กรุณาระบุชื่อ-นามสกุล"}), 400

    req_id = f"RTSD-REQ-{int(time.time()) % 10000:04d}"
    initial_status = "อนุมัติแล้ว" if phone in ADMIN_PHONES else "รออนุมัติ"

    # บันทึกเข้า Memory Cache และส่งไป Google Sheets
    save_registered_user(
        line_user_id=line_user_id if line_user_id and line_user_id != "-" else f"WEB-{phone}",
        name=full_name,
        phone=phone,
        role=role,
        unit=unit,
        position=position,
        status=initial_status,
        purpose=purpose,
        picture_profile=picture_profile,
        picture_base64=picture_base64
    )

    # หากมี LINE User ID หรือเบอร์ตรงกับผู้ใช้ LINE ให้ Push แจ้งเตือนทาง LINE ทันที
    target_lid = line_user_id if (line_user_id and line_user_id.startswith("U")) else None
    if not target_lid and phone in phone_to_user:
        target_lid = phone_to_user[phone].get("line_user_id")

    if target_lid and str(target_lid).startswith("U"):
        try:
            if initial_status == "อนุมัติแล้ว":
                line_push_msg = (
                    f"🎉 การลงทะเบียนสมาชิก RTSD ได้รับการอนุมัติแล้ว!\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📋 รหัสคำขอ: {req_id}\n"
                    f"👤 ผู้ใช้งาน: {full_name}\n"
                    f"🏢 หน่วยงาน: {unit}\n"
                    f"📱 เบอร์โทรศัพท์: {phone}\n"
                    f"🔰 ระดับสิทธิ์: {role}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"✨ บัญชีของท่านเปิดใช้งานสมบูรณ์แล้ว สามารถใช้เบอร์นี้ขอรหัส OTP หรือเข้าสู่ระบบได้ทันทีครับ"
                )
            else:
                line_push_msg = (
                    f"📋 ยื่นคำขอลงทะเบียนสมาชิก RTSD เรียบร้อยแล้ว\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"รหัสคำขอ: {req_id}\n"
                    f"👤 ผู้ยื่นคำขอ: {full_name}\n"
                    f"🏢 หน่วยงาน: {unit} ({position})\n"
                    f"📱 เบอร์โทรศัพท์: {phone}\n"
                    f"🔰 สิทธิ์ที่ขอ: {role}\n"
                    f"⏳ สถานะ: ⏳ รอการอนุมัติจากผู้ดูแลระบบ (Pending)\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🔔 ระบบได้ส่งคำขอของท่านไปยัง Admin เรียบร้อยแล้ว\n"
                    f"เมื่อได้รับการอนุมัติ ท่านจะได้รับการแจ้งเตือนทาง LINE นี้ทันทีครับ"
                )
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.push_message(
                    PushMessageRequest(
                        to=target_lid,
                        messages=[TextMessage(text=line_push_msg)]
                    )
                )
        except Exception as e:
            print(f"Error pushing LINE registration confirmation: {e}")

    # ส่งแจ้งเตือนด่วนไปยัง Admin ทาง LINE ทันที
    if initial_status == "รออนุมัติ":
        admin_alert_msg = (
            f"🔔 [แจ้งเตือน] มีคำขอลงทะเบียนสมาชิกใหม่!\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📋 รหัสคำขอ: {req_id}\n"
            f"👤 ผู้ยื่นคำขอ: {full_name}\n"
            f"🏢 สังกัด: {unit} ({position})\n"
            f"📱 เบอร์โทรศัพท์: {phone}\n"
            f"🔰 สิทธิ์ที่ขอ: {role}\n"
            f"🎯 วัตถุประสงค์: {purpose}\n"
            f"⏳ สถานะ: รออนุมัติ (Pending)\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👉 แอดมินสามารถตรวจสอบและกดอนุมัติสิทธิ์ได้ที่:\n"
            f"🔗 https://rtsd-linebot.onrender.com/dashboard\n"
            f"(เมนู 'จัดการสิทธิ์' บน Tactical Dashboard)"
        )
        notify_admins(admin_alert_msg)

    return jsonify({
        "status": "success",
        "req_id": req_id,
        "full_name": full_name,
        "phone_number": phone,
        "role": role,
        "unit": unit,
        "approval_status": initial_status
    }), 200


@app.route("/api/admin/users", methods=['GET'])
def api_admin_get_users():
    """ส่งรายชื่อผู้ลงทะเบียนทั้งหมดให้แอดมินดูและจัดการสิทธิ์"""
    fetch_registered_users()
    users_list = []
    seen_phones = set()
    for phone, u in phone_to_user.items():
        if phone in seen_phones:
            continue
        seen_phones.add(phone)
        role = u.get("role", "ผู้ใช้งานทั่วไป (Observer)")
        users_list.append({
            "name": u.get("name", "ไม่ระบุชื่อ"),
            "phone": phone,
            "unit": u.get("unit", "-"),
            "position": u.get("position", "-"),
            "role": role,
            "status": u.get("status", "อนุมัติแล้ว"),
            "line_user_id": u.get("line_user_id", "-"),
            "picture_profile": u.get("picture_profile", "-"),
            "permissions": get_user_permissions(role)
        })
    return jsonify(users_list), 200


@app.route("/api/admin/update-role", methods=['POST'])
def api_admin_update_role():
    """แอดมินปรับเปลี่ยนสิทธิ์และสถานะของผู้ใช้งาน (Role & Status Management)"""
    data = request.get_json(silent=True) or {}
    phone = str(data.get("phone", "")).strip().replace("-", "").replace(" ", "")

    if not phone or phone not in phone_to_user:
        return jsonify({"status": "error", "message": "ไม่พบหมายเลขโทรศัพท์นี้ในระบบ"}), 404

    user_info = phone_to_user[phone]
    new_role = str(data.get("role", user_info.get("role", "ผู้ใช้งานทั่วไป"))).strip()
    new_status = str(data.get("status", user_info.get("status", "อนุมัติแล้ว"))).strip()

    user_info["role"] = new_role
    user_info["status"] = new_status
    user_info["permissions"] = get_user_permissions(new_role)

    lid = user_info.get("line_user_id", "")
    if lid in registered_users:
        registered_users[lid]["role"] = new_role
        registered_users[lid]["status"] = new_status
        registered_users[lid]["permissions"] = user_info["permissions"]

    # ถ้าระงับสิทธิ์ ให้ตัด Session ทันที
    if new_status in ["ระงับสิทธิ์", "ระงับการใช้งาน", "suspended"]:
        tokens_to_remove = [t for t, s in ACTIVE_SESSIONS.items() if s.get("phone") == phone]
        for t in tokens_to_remove:
            del ACTIVE_SESSIONS[t]

    # บันทึกลง Google Sheets
    save_registered_user(
        line_user_id=lid,
        name=user_info.get("name", "ผู้ใช้งาน"),
        phone=phone,
        role=new_role,
        unit=user_info.get("unit", "-"),
        position=user_info.get("position", "-"),
        status=new_status,
        purpose=user_info.get("purpose", "-")
    )

    # Push แจ้งเตือนทาง LINE หากมี LINE ID
    if lid and lid.startswith("U"):
        try:
            role_emoji = "⭐" if "แอดมิน" in new_role or "ผู้ดูแล" in new_role or "บัญชา" in new_role else ("🎯" if "ศูนย์" in new_role or "TOC" in new_role else "🔺")
            if new_status == "อนุมัติแล้ว":
                push_msg = (
                    f"🎉 บัญชี RTSD ของท่านได้รับการอนุมัติแล้ว!\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"👤 ผู้ใช้งาน: คุณ{user_info.get('name')}\n"
                    f"🏢 หน่วยงาน: {user_info.get('unit', '-')}\n"
                    f"🔰 ระดับสิทธิ์: {role_emoji} {new_role}\n"
                    f"🟢 สถานะ: อนุมัติเรียบร้อย (Active)\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"✨ ท่านสามารถเข้าสู่ระบบผ่านแอปพลิเคชัน RTSD Mobile หรือ Tactical Dashboard ได้ทันทีครับ"
                )
            elif new_status in ["ระงับสิทธิ์", "ระงับการใช้งาน", "suspended"]:
                push_msg = (
                    f"⛔ แจ้งเตือน: บัญชี RTSD ของท่านถูกระงับสิทธิ์\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"👤 ผู้ใช้งาน: คุณ{user_info.get('name')}\n"
                    f"หากมีข้อสงสัย กรุณาติดต่อผู้ดูแลระบบ (Admin) ครับ"
                )
            else:
                push_msg = (
                    f"🔔 แจ้งเตือนการปรับเปลี่ยนระดับสิทธิ์\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"👤 คุณ{user_info.get('name')}\n"
                    f"🔰 ระดับสิทธิ์ใหม่: {role_emoji} {new_role}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"ระบบความปลอดภัย RTSD ได้อัปเดตสิทธิ์ของท่านเรียบร้อยแล้วครับ"
                )
            with ApiClient(configuration) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.push_message(
                    PushMessageRequest(to=lid, messages=[TextMessage(text=push_msg)])
                )
        except Exception as e:
            print(f"Error pushing role change notice: {e}")

    return jsonify({
        "status": "success",
        "message": f"ปรับสิทธิ์เป็น '{new_role}' สำเร็จแล้ว",
        "phone": phone,
        "new_role": new_role
    }), 200


@app.route("/api/admin/delete-user", methods=['POST'])
def api_admin_delete_user():
    """แอดมินลบบัญชีผู้ใช้งานออกจากระบบ"""
    data = request.get_json(silent=True) or {}
    phone = str(data.get("phone", "")).strip().replace("-", "").replace(" ", "")

    if not phone or phone not in phone_to_user:
        return jsonify({"status": "error", "message": "ไม่พบหมายเลขโทรศัพท์นี้ในระบบ"}), 404

    user_info = phone_to_user[phone]
    lid = user_info.get("line_user_id", "")

    del phone_to_user[phone]
    if lid and lid in registered_users:
        del registered_users[lid]

    tokens_to_remove = [t for t, s in ACTIVE_SESSIONS.items() if s.get("phone") == phone]
    for t in tokens_to_remove:
        del ACTIVE_SESSIONS[t]

    return jsonify({"status": "success", "message": f"ลบบัญชีกำลังพล {phone} เรียบร้อยแล้ว"}), 200


@app.route("/api/user/update-profile", methods=['POST'])
def api_user_update_profile():
    """API ให้เจ้าของบัญชีอัปเดตข้อมูลโปรไฟล์ส่วนตัว (รูปโปรไฟล์, ชื่อ-สกุล/ยศ, สังกัด, ตำแหน่ง)"""
    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()

    data = request.get_json(silent=True) or {}
    if not token:
        token = data.get("token", "")

    phone = str(data.get("phone", "")).strip().replace("-", "").replace(" ", "")

    # ตรวจสอบสิทธิ์ผ่าน Token
    session = ACTIVE_SESSIONS.get(token) if token else None
    if session:
        session_phone = session.get("phone", "")
        if not phone:
            phone = session_phone
        is_admin = session.get("phone") in ADMIN_PHONES or "Super Admin" in session.get("role", "")
        if phone != session_phone and not is_admin:
            return jsonify({"status": "error", "message": "ท่านไม่มีสิทธิ์แก้ไขข้อมูลของบัญชีอื่น"}), 403

    if not phone:
        return jsonify({"status": "error", "message": "กรุณาระบุหมายเลขโทรศัพท์หรือแนบ Token"}), 400

    if phone not in phone_to_user:
        fetch_registered_users()

    if phone not in phone_to_user:
        return jsonify({"status": "error", "message": "ไม่พบบัญชีผู้ใช้งานนี้ในระบบ"}), 404

    user_info = phone_to_user[phone]

    # อัปเดตเฉพาะฟิลด์ที่อนุญาตให้แก้ไข
    if "name" in data and str(data["name"]).strip():
        user_info["name"] = str(data["name"]).strip()
    if "unit" in data and str(data["unit"]).strip():
        user_info["unit"] = str(data["unit"]).strip()
    if "position" in data and str(data["position"]).strip():
        user_info["position"] = str(data["position"]).strip()

    pic_url = str(data.get("picture_profile", "")).strip()
    pic_b64 = str(data.get("picture_base64", "")).strip()

    if pic_b64:
        user_info["picture_profile"] = f"data:image/jpeg;base64,{pic_b64}"
    elif pic_url and pic_url != "-":
        user_info["picture_profile"] = pic_url

    lid = user_info.get("line_user_id", "")
    if lid in registered_users:
        registered_users[lid].update({
            "name": user_info["name"],
            "unit": user_info["unit"],
            "position": user_info["position"],
            "picture_profile": user_info["picture_profile"]
        })

    # ซิงค์ Session ปัจจุบัน
    for s_tok, s in ACTIVE_SESSIONS.items():
        if s.get("phone") == phone:
            s["user"] = user_info

    # ซิงค์ลง Google Sheets
    save_registered_user(
        line_user_id=lid,
        name=user_info.get("name", "ผู้ใช้งาน"),
        phone=phone,
        role=user_info.get("role", "ผู้ใช้งานทั่วไป"),
        unit=user_info.get("unit", "-"),
        position=user_info.get("position", "-"),
        status=user_info.get("status", "อนุมัติแล้ว"),
        purpose=user_info.get("purpose", "-"),
        picture_profile=user_info.get("picture_profile", "-"),
        picture_base64=pic_b64
    )

    return jsonify({
        "status": "success",
        "message": "อัปเดตข้อมูลโปรไฟล์เรียบร้อยแล้ว",
        "user": user_info
    }), 200


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


# กรณีผู้ใช้แอดเพื่อนใหม่ หรือ ปลดบล็อกบอท (Follow Event)
@handler.add(FollowEvent)
def handle_follow(event):
    user_id = event.source.user_id
    user_name = "ท่าน"
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            profile = line_bot_api.get_profile(user_id)
            user_name = profile.display_name
    except Exception as e:
        print(f"Error fetching profile on follow: {e}")

    # ตรวจสอบประวัติการลงทะเบียนในระบบ
    user_info = registered_users.get(user_id)
    if not user_info and len(registered_users) == 0:
        fetch_registered_users()
        user_info = registered_users.get(user_id)

    if user_info:
        welcome_msg = (
            f"👋 ยินดีต้อนรับกลับครับ คุณ{user_info.get('name', user_name)}!\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🛡️ ศูนย์บัญชาการแผนที่และเตือนภัย RTSD\n"
            f"📱 บัญชีของท่าน: {user_info.get('phone')}\n"
            f"🔰 สิทธิ์: {user_info.get('role', 'ผู้ใช้งานทั่วไป')}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"ท่านสามารถเริ่มใช้งานได้ทันที เลือกเมนูด้านล่างนี้ได้เลยครับ 👇"
        )
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุเตือนภัย", text="แจ้งเหตุ")),
            QuickReplyItem(action=MessageAction(label="🔍 ติดตามสถานะ", text="ติดตามสถานะ")),
            QuickReplyItem(action=MessageAction(label="🛰️ ส่งพิกัดสด GPS", text="แทร็กกิ้ง"))
        ])
    else:
        welcome_msg = (
            f"🎉 ยินดีต้อนรับคุณ {user_name} สู่ระบบ RTSD!\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🛡️ ศูนย์เตือนภัยและบัญชาการสถานการณ์\n"
            f"กรมแผนที่ทหาร (RTSD Command)\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📝 เพื่อความปลอดภัยและการยืนยันตัวตน (OTP)\n"
            f"กรุณาลงทะเบียนสมาชิก 1 ครั้ง โดยพิมพ์:\n\n"
            f"👉 [เบอร์โทรศัพท์ 10 หลัก] ส่งเข้ามาได้เลยครับ\n"
            f"ตัวอย่างเช่น: 0812345678 หรือ สมชาย 0812345678\n\n"
            f"🌐 หรือกรอกข้อมูลแบบละเอียดพร้อมระบุยศ/สังกัดได้ที่:\n"
            f"https://rtsd-linebot.onrender.com/register\n\n"
            f"✨ เมื่อลงทะเบียนแล้ว ระบบจะจำบัญชีของท่านถาวรเพื่อขอรับรหัส OTP ล็อกอิน Dashboard ครับ!"
        )
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label="📝 ลงทะเบียนสมาชิก", text="ลงทะเบียน")),
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุเตือนภัย", text="แจ้งเหตุ")),
            QuickReplyItem(action=MessageAction(label="🔍 ติดตามสถานะ", text="ติดตามสถานะ"))
        ])

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=welcome_msg, quick_reply=quick_reply)]
            )
        )


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

    # ออกรหัสติดตามเหตุการณ์เฉพาะ (Report Tracking ID)
    report_id = f"RTSD-{int(time.time()) % 100000:04d}"
    title = f"[{report_id}] แจ้งเหตุ: {incident_type}"

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
    save_to_google_sheet(lat, lon, title, address, reporter, urgency, incident_type, file_base64, file_name, mime_type)

    gmap_text = f"\n🧭 นำทาง Google Maps: https://www.google.com/maps/dir/?api=1&destination={lat},{lon}\n" if (SYSTEM_SETTINGS.get("enable_google_maps") and lat and lon) else ""

    reply = (f"✅ บันทึกข้อมูลและรูปถ่ายหลักฐานสำเร็จ!\n\n"
             f"🆔 รหัสติดตามเหตุ: #{report_id}\n"
             f"👤 ผู้แจ้ง: {reporter}\n"
             f"🚨 เหตุการณ์: {incident_type}\n"
             f"⚠️ ความเร่งด่วน: {urgency}\n"
             f"⏳ สถานะ: ⏳ รอดำเนินการ\n"
             f"📸 รูปถ่าย: บันทึกลง Drive RTSD เรียบร้อยแล้วครับ\n"
             f"{gmap_text}\n"
             f"💡 ท่านสามารถกดปุ่ม '🔍 ติดตาม #{report_id}' ด้านล่าง เพื่อเช็กความคืบหน้าได้ตลอดเวลาครับ")

    quick_items = [
        QuickReplyItem(action=MessageAction(label=f"🔍 ติดตาม #{report_id}", text=f"ติดตาม {report_id}")),
        QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุใหม่", text="แจ้งเหตุ"))
    ]
    if SYSTEM_SETTINGS.get("enable_google_maps") and lat and lon:
        quick_items.insert(1, QuickReplyItem(action=URIAction(label="🧭 นำทาง Google Maps", uri=f"https://www.google.com/maps/dir/?api=1&destination={lat},{lon}")))

    quick_reply = QuickReply(items=quick_items)

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply, quick_reply=quick_reply)]
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

    report_id = f"RTSD-{int(time.time()) % 100000:04d}"
    title = f"[{report_id}] แจ้งเหตุ: {incident_type}"

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
    save_to_google_sheet(lat, lon, title, address, reporter, urgency, incident_type, file_base64, file_name, mime_type)

    gmap_text = f"\n🧭 นำทาง Google Maps: https://www.google.com/maps/dir/?api=1&destination={lat},{lon}\n" if (SYSTEM_SETTINGS.get("enable_google_maps") and lat and lon) else ""

    reply = (f"✅ บันทึกข้อมูลและคลิปวิดีโอหลักฐานสำเร็จ!\n\n"
             f"🆔 รหัสติดตามเหตุ: #{report_id}\n"
             f"👤 ผู้แจ้ง: {reporter}\n"
             f"🚨 เหตุการณ์: {incident_type}\n"
             f"⚠️ ความเร่งด่วน: {urgency}\n"
             f"⏳ สถานะ: ⏳ รอดำเนินการ\n"
             f"🎥 คลิปวิดีโอ: บันทึกลง Drive RTSD เรียบร้อยแล้วครับ\n"
             f"{gmap_text}\n"
             f"💡 ท่านสามารถกดปุ่ม '🔍 ติดตาม #{report_id}' ด้านล่าง เพื่อเช็กความคืบหน้าได้ตลอดเวลาครับ")

    quick_items = [
        QuickReplyItem(action=MessageAction(label=f"🔍 ติดตาม #{report_id}", text=f"ติดตาม {report_id}")),
        QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุใหม่", text="แจ้งเหตุ"))
    ]
    if SYSTEM_SETTINGS.get("enable_google_maps") and lat and lon:
        quick_items.insert(1, QuickReplyItem(action=URIAction(label="🧭 นำทาง Google Maps", uri=f"https://www.google.com/maps/dir/?api=1&destination={lat},{lon}")))

    quick_reply = QuickReply(items=quick_items)

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply, quick_reply=quick_reply)]
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

    # ตรวจสอบการลงทะเบียนยืนยันตัวตนของผู้ใช้ (User Identity Check)
    user_info = registered_users.get(user_id)
    if not user_info and len(registered_users) == 0:
        fetch_registered_users()
        user_info = registered_users.get(user_id)

    # 0. ผู้ใช้กดปุ่มหรือพิมพ์คำว่า "ลงทะเบียน" / "สมัครสมาชิก" (แบบยังไม่ระบุเบอร์โทร)
    if user_text in ["ลงทะเบียน", "สมัคร", "สมัครสมาชิก", "register", "ยืนยันตัวตน"]:
        if user_info:
            reply_msg = (f"✅ บัญชีของท่านลงทะเบียนเรียบร้อยแล้วครับ!\n"
                         f"━━━━━━━━━━━━━━━━━━\n"
                         f"👤 ชื่อผู้ใช้งาน: คุณ{user_info.get('name')}\n"
                         f"📱 เบอร์โทรศัพท์: {user_info.get('phone')}\n"
                         f"🔰 บทบาท: {user_info.get('role')}\n"
                         f"━━━━━━━━━━━━━━━━━━\n"
                         f"💡 หากต้องการเปลี่ยนเบอร์ ให้พิมพ์ส่งเข้ามาใหม่ได้เลยครับ เช่น:\n"
                         f"0891234567 หรือ เบอร์ใหม่ 0891234567")
        else:
            reply_msg = (f"📝 ลงทะเบียนสมาชิกระบบ RTSD\n"
                         f"━━━━━━━━━━━━━━━━━━\n"
                         f"ท่านสามารถลงทะเบียนได้ 2 วิธี:\n\n"
                         f"1️⃣ พิมพ์ [เบอร์โทรศัพท์ 10 หลัก] ส่งในแชตนี้ได้ทันที\n"
                         f"เช่น: 0812345678 หรือ สมชาย 0812345678\n\n"
                         f"2️⃣ หรือกรอกฟอร์มพร้อมระบุ ยศ/สังกัด/สิทธิ์ ผ่านหน้าเว็บ:\n"
                         f"👉 https://rtsd-linebot.onrender.com/register\n"
                         f"━━━━━━━━━━━━━━━━━━\n"
                         f"🔒 ข้อมูลจะถูกจัดเก็บสำหรับรับรหัส OTP เข้าหน้าแดชบอร์ดครับ")
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุเตือนภัย", text="แจ้งเหตุ")),
            QuickReplyItem(action=MessageAction(label="🔍 ติดตามสถานะ", text="ติดตามสถานะ"))
        ])
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_msg, quick_reply=quick_reply)]
                )
            )
        return

    # 0.5. คำสั่งพิเศษสำหรับแอดมิน: ตั้งแอดมิน / ปลดแอดมิน
    clean_cmd_text = re.sub(r'[\s\-]', '', user_text)
    if user_text.startswith("ตั้งแอดมิน") or user_text.startswith("ปลดแอดมิน") or clean_cmd_text.startswith("ตั้งแอดมิน") or clean_cmd_text.startswith("ปลดแอดมิน"):
        is_sender_admin = (user_info and any(k in user_info.get("role", "") for k in ["แอดมิน", "ผู้ดูแล", "บัญชา"])) or (user_info and user_info.get("phone") in ADMIN_PHONES)
        if not is_sender_admin:
            reply_msg = "⛔ ขออภัยครับ เฉพาะผู้ดูแลระบบ (Admin) เท่านั้นที่สามารถปรับเปลี่ยนสิทธิ์ได้ครับ"
        else:
            target_match = re.search(r'(0[689]\d{8}|0[2-9]\d{7})', clean_cmd_text)
            if not target_match:
                reply_msg = "⚠️ กรุณาระบุเบอร์โทรศัพท์ของบุคคลที่ต้องการปรับสิทธิ์ด้วยครับ เช่น:\n👉 'ตั้งแอดมิน 0812345678'\n👉 'ปลดแอดมิน 0812345678'"
            else:
                target_phone = target_match.group(1)
                new_r = "ผู้ดูแลระบบ (Admin)" if "ตั้งแอดมิน" in clean_cmd_text else "ผู้ใช้งานทั่วไป"
                if target_phone not in phone_to_user:
                    fetch_registered_users()
                if target_phone not in phone_to_user:
                    reply_msg = f"⚠️ ไม่พบบัญชีเบอร์โทร {target_phone} ในระบบ กรุณาให้บุคคลดังกล่าวลงทะเบียนก่อนครับ"
                else:
                    target_u = phone_to_user[target_phone]
                    target_u["role"] = new_r
                    target_lid = target_u.get("line_user_id", "")
                    if target_lid in registered_users:
                        registered_users[target_lid]["role"] = new_r
                    save_registered_user(
                        line_user_id=target_lid,
                        name=target_u.get("name", "ผู้ใช้งาน"),
                        phone=target_phone,
                        role=new_r,
                        unit=target_u.get("unit", "-"),
                        position=target_u.get("position", "-"),
                        status="อนุมัติแล้ว",
                        purpose=target_u.get("purpose", "-")
                    )
                    reply_msg = f"✅ ดำเนินการปรับสิทธิ์ คุณ{target_u.get('name')} ({target_phone})\nเป็น: ⭐ {new_r} เรียบร้อยแล้วครับ!"
                    if target_lid and target_lid.startswith("U"):
                        try:
                            with ApiClient(configuration) as api_client:
                                line_bot_api = MessagingApi(api_client)
                                line_bot_api.push_message(
                                    PushMessageRequest(to=target_lid, messages=[TextMessage(text=f"🔔 บัญชีของท่านได้รับการปรับสิทธิ์เป็น: ⭐ {new_r} โดยผู้ดูแลระบบ RTSD")])
                                )
                        except Exception:
                            pass

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_msg)])
            )
        return

    # 0.6. คำสั่งพิเศษสำหรับแอดมิน: เปิด/ปิด การแชร์ Google Maps (Security OPSEC Toggle)
    clean_map_cmd = clean_cmd_text.lower()
    if any(k in clean_map_cmd for k in ["เปิดแชร์แผนที่", "เปิดgooglemap", "เปิดgooglemaps", "เปิดแมพ", "เปิดแผนที่", "เปิดgoogle", "enablemaps"]):
        is_sender_admin = (user_info and any(k in user_info.get("role", "") for k in ["แอดมิน", "ผู้ดูแล", "บัญชา"])) or (user_info and user_info.get("phone") in ADMIN_PHONES)
        if not is_sender_admin:
            reply_msg = "⛔ ขออภัยครับ เฉพาะผู้ดูแลระบบ (Admin) เท่านั้นที่สามารถปรับการตั้งค่านี้ได้ครับ"
        else:
            SYSTEM_SETTINGS["enable_google_maps"] = True
            reply_msg = ("✅ เปิดระบบแชร์ Google Maps เรียบร้อยแล้ว!\n"
                         "━━━━━━━━━━━━━━━━━━\n"
                         "🗺️ สถานะ: 🟢 เปิดใช้งาน (Active)\n"
                         "📍 ทุกการ์ดแจ้งเหตุและติดตามสถานะจะแสดงลิงก์นำทาง Google Maps ให้ทีมสนามใช้งานได้ทันทีครับ")
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_msg)]))
        return

    if any(k in clean_map_cmd for k in ["ปิดแชร์แผนที่", "ปิดgooglemap", "ปิดgooglemaps", "ปิดแมพ", "ปิดแผนที่", "ปิดgoogle", "disablemaps"]):
        is_sender_admin = (user_info and any(k in user_info.get("role", "") for k in ["แอดมิน", "ผู้ดูแล", "บัญชา"])) or (user_info and user_info.get("phone") in ADMIN_PHONES)
        if not is_sender_admin:
            reply_msg = "⛔ ขออภัยครับ เฉพาะผู้ดูแลระบบ (Admin) เท่านั้นที่สามารถปรับการตั้งค่านี้ได้ครับ"
        else:
            SYSTEM_SETTINGS["enable_google_maps"] = False
            reply_msg = ("🔒 ปิดระบบแชร์ Google Maps เรียบร้อยแล้ว!\n"
                         "━━━━━━━━━━━━━━━━━━\n"
                         "🛡️ สถานะ: 🔴 ปิดการใช้งาน (โหมดความมั่นคงทางยุทธวิธี)\n"
                         "🚫 ระบบจะซ่อนลิงก์ Google Maps ภายนอกทั้งหมด พิกัดจะถูกเก็บไว้เฉพาะใน Portal RTSD เท่านั้นครับ")
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_msg)]))
        return

    if any(k in clean_map_cmd for k in ["สถานะระบบ", "เช็กระบบ", "systemstatus"]):
        is_sender_admin = (user_info and any(k in user_info.get("role", "") for k in ["แอดมิน", "ผู้ดูแล", "บัญชา"])) or (user_info and user_info.get("phone") in ADMIN_PHONES)
        map_status = "🟢 เปิดใช้งาน (พร้อมลิงก์นำทาง)" if SYSTEM_SETTINGS.get("enable_google_maps") else "🔴 ปิดใช้งาน (โหมดความมั่นคง)"
        reply_msg = (f"⚙️ ข้อมูลสถานะระบบศูนย์บัญชาการ RTSD\n"
                     f"━━━━━━━━━━━━━━━━━━\n"
                     f"🗺️ ระบบแชร์ Google Maps: {map_status}\n"
                     f"🛰️ หน่วยติดตามสด (Active Units): {len(active_trackers)} หน่วย\n"
                     f"👥 กำลังพลที่ลงทะเบียน: {len(registered_users)} นาย\n"
                     f"━━━━━━━━━━━━━━━━━━\n"
                     f"💡 แอดมินสามารถพิมพ์ 'เปิดแชร์แผนที่' หรือ 'ปิดแชร์แผนที่' เพื่อควบคุมระบบได้ตลอดเวลาครับ")
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_msg)]))
        return

    # 1. ผู้ใช้พิมพ์เบอร์โทรศัพท์เข้ามา เพื่อลงทะเบียนยืนยันตัวตน
    clean_text_digits = re.sub(r'[\s\-]', '', user_text)
    phone_match = re.search(r'(0[689]\d{8}|0[2-9]\d{7})', clean_text_digits)
    if phone_match and (any(w in user_text for w in ["ลงทะเบียน", "สมัคร", "เบอร์", "โทร"]) or not user_info):
        clean_phone = phone_match.group(1)
        name_part = re.sub(r'0[689][\d\s\-]{8,12}|0[2-9][\d\s\-]{7,11}', '', user_text)
        name_part = re.sub(r'(ลงทะเบียน|สมัคร|เบอร์|โทร|ชื่อ)', '', name_part).strip()
        reg_name = name_part if len(name_part) >= 2 else user_name
        role = "ผู้ดูแลระบบ (Admin)" if clean_phone in ADMIN_PHONES else "ผู้ใช้งานทั่วไป"
        reg_status = "อนุมัติแล้ว" if clean_phone in ADMIN_PHONES else "รออนุมัติ"
        
        save_registered_user(user_id, reg_name, clean_phone, role=role, status=reg_status)
        user_sessions[user_id]["user_name"] = f"{reg_name} ({clean_phone})"
        
        if reg_status == "อนุมัติแล้ว":
            reply_msg = (f"🎉 ลงทะเบียนยืนยันตัวตนสำเร็จแล้วครับ!\n"
                         f"──────────────────────\n"
                         f"👤 ชื่อผู้ใช้งาน: คุณ{reg_name}\n"
                         f"📱 เบอร์โทรศัพท์: {clean_phone}\n"
                         f"🔰 สิทธิ์การใช้งาน: {role}\n"
                         f"──────────────────────\n"
                         f"🛡️ ระบบความปลอดภัย RTSD จดจำบัญชีของท่านเรียบร้อยแล้ว\n"
                         f"👉 ท่านสามารถเข้าสู่ระบบ Dashboard หรือส่งพิกัดแจ้งเหตุได้ทันทีครับ 🎉")
        else:
            reply_msg = (f"📋 ยื่นคำขอลงทะเบียนเรียบร้อยแล้วครับ!\n"
                         f"──────────────────────\n"
                         f"👤 ชื่อผู้ใช้งาน: คุณ{reg_name}\n"
                         f"📱 เบอร์โทรศัพท์: {clean_phone}\n"
                         f"⏳ สถานะ: ⏳ รอการอนุมัติจากผู้ดูแลระบบ (Pending)\n"
                         f"──────────────────────\n"
                         f"🔔 ระบบได้ส่งคำขอของท่านไปยัง Admin เรียบร้อยแล้ว\n"
                         f"เมื่อ Admin อนุมัติสิทธิ์แล้ว ท่านจะได้รับการแจ้งเตือนทาง LINE นี้ทันทีครับ")
            admin_alert_msg = (
                f"🔔 [LINE Bot] มีคำขอลงทะเบียนใหม่ผ่านแชต LINE!\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👤 ผู้ยื่นคำขอ: คุณ{reg_name}\n"
                f"📱 เบอร์โทรศัพท์: {clean_phone}\n"
                f"🆔 LINE User ID: {user_id}\n"
                f"⏳ สถานะ: รออนุมัติ (Pending)\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👉 ตรวจสอบและอนุมัติที่ https://rtsd-linebot.onrender.com/dashboard"
            )
            notify_admins(admin_alert_msg)

        quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุเตือนภัย", text="แจ้งเหตุ")),
            QuickReplyItem(action=MessageAction(label="🔍 ติดตามสถานะ", text="ติดตามสถานะ")),
            QuickReplyItem(action=MessageAction(label="🛰️ ส่งพิกัดสด GPS", text="แทร็กกิ้ง"))
        ])
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_msg, quick_reply=quick_reply)]
                )
            )
        return

    # 2. ผู้ใช้พิมพ์เช็คข้อมูลตัวเอง
    if user_text in ["โปรไฟล์", "ข้อมูลของฉัน", "profile", "ข้อมูลส่วนตัว", "เบอร์ของฉัน"]:
        if user_info:
            reply_msg = (f"👤 ข้อมูลบัญชีผู้ใช้งานของคุณ\n"
                         f"──────────────────────\n"
                         f"📌 ชื่อ-นามสกุล: คุณ{user_info.get('name')}\n"
                         f"📱 เบอร์โทรศัพท์: {user_info.get('phone')}\n"
                         f"🔰 บทบาท: {user_info.get('role')}\n"
                         f"✅ สถานะยืนยันตัวตน: สมบูรณ์ (Verified)\n"
                         f"──────────────────────\n"
                         f"💡 สามารถใช้เบอร์โทรนี้เพื่อขอรับรหัส OTP เข้าใช้งาน Dashboard ได้ทันทีครับ")
        else:
            reply_msg = (f"⚠️ ท่านยังไม่ได้ลงทะเบียนยืนยันตัวตนในระบบ\n\n"
                         f"👉 กรุณาพิมพ์ [เบอร์โทรศัพท์ 10 หลัก] ส่งเข้ามาได้เลยครับ เช่น:\n"
                         f"0812345678 (หรือระบุชื่อด้วย เช่น 'สมชาย 0812345678')")
        
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุเตือนภัย", text="แจ้งเหตุ")),
            QuickReplyItem(action=MessageAction(label="🔍 ติดตามสถานะ", text="ติดตามสถานะ"))
        ])
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_msg, quick_reply=quick_reply)]
                )
            )
        return

    # ถ้าผู้ใช้ลงทะเบียนแล้ว ให้อัปเดตชื่อใน Session ให้มีชื่อและเบอร์โทรเสมอ
    if user_info:
        user_name = f"{user_info.get('name')} ({user_info.get('phone')})"
        user_sessions[user_id]["user_name"] = user_name

    # กรณีผู้ใช้เลือกข้ามการส่งรูป
    if user_text == "ข้ามการส่งรูป":
        session = user_sessions.get(user_id, {})
        lat = session.get("lat", 0)
        lon = session.get("lon", 0)
        address = session.get("address", "ไม่ระบุที่อยู่")
        urgency = session.get("urgency", "🟡 ปานกลาง")
        incident_type = session.get("incident_type", "🌊 น้ำท่วมขัง")

        report_id = f"RTSD-{int(time.time()) % 100000:04d}"
        title = f"[{report_id}] แจ้งเหตุ: {incident_type}"

        save_to_google_sheet(lat, lon, title, address, user_name, urgency, incident_type)

        gmap_text = f"\n🧭 นำทาง Google Maps: https://www.google.com/maps/dir/?api=1&destination={lat},{lon}\n" if (SYSTEM_SETTINGS.get("enable_google_maps") and lat and lon) else ""

        reply = (f"✅ บันทึกข้อมูลเรียบร้อยแล้ว!\n\n"
                 f"🆔 รหัสติดตามเหตุ: #{report_id}\n"
                 f"👤 ผู้แจ้ง: {user_name}\n"
                 f"🚨 เหตุการณ์: {incident_type}\n"
                 f"⚠️ ความเร่งด่วน: {urgency}\n"
                 f"⏳ สถานะ: ⏳ รอดำเนินการ\n"
                 f"📍 พิกัด: {lat}, {lon}\n"
                 f"🏠 สถานที่: {address}\n"
                 f"{gmap_text}\n"
                 f"💡 ท่านสามารถกดปุ่ม '🔍 ติดตาม #{report_id}' ด้านล่าง เพื่อเช็กความคืบหน้าได้ตลอดเวลาครับ")

        quick_items = [
            QuickReplyItem(action=MessageAction(label=f"🔍 ติดตาม #{report_id}", text=f"ติดตาม {report_id}")),
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุใหม่", text="แจ้งเหตุ"))
        ]
        if SYSTEM_SETTINGS.get("enable_google_maps") and lat and lon:
            quick_items.insert(1, QuickReplyItem(action=URIAction(label="🧭 นำทาง Google Maps", uri=f"https://www.google.com/maps/dir/?api=1&destination={lat},{lon}")))

        quick_reply = QuickReply(items=quick_items)

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply, quick_reply=quick_reply)]
                )
            )

        if user_id in user_sessions:
            del user_sessions[user_id]
        return

    # =========================================================================
    # 🔍 ระบบติดตามสถานะงาน (Incident Status Tracking)
    # =========================================================================
    if user_text in ['ติดตาม', 'ติดตามสถานะ', 'เช็กสถานะ', 'ตรวจสถานะ', 'สถานะ', 'status'] or user_text.startswith("ติดตาม") or "rtsd-" in user_text.lower():
        # ตรวจสอบว่าผู้ใช้พิมพ์รหัสมาด้วยหรือไม่ เช่น "ติดตาม RTSD-1234" หรือ "RTSD-1234"
        id_match = re.search(r'rtsd-\d+', user_text, re.IGNORECASE)
        search_id = id_match.group(0).upper() if id_match else None

        incidents = get_user_incidents(query_text=search_id, user_name=user_name if not search_id else None)

        if incidents:
            top = incidents[0]  # รายการล่าสุด
            time_display = top['timestamp'].replace('T', ' ').replace('.000Z', ' น.')
            
            # สัญลักษณ์สถานะ
            status_text = top['status']
            if 'เสร็จ' in status_text or 'เรียบร้อย' in status_text:
                status_icon = "🟢"
            elif 'ดำเนิน' in status_text:
                status_icon = "🟡"
            else:
                status_icon = "⏳"

            reply_msg = (f"📋 ผลการตรวจสอบสถานะการแจ้งเหตุ\n"
                         f"──────────────────────\n"
                         f"📌 เรื่อง: {top['title']}\n"
                         f"🚨 ประเภท: {top['incident_type']}\n"
                         f"⚠️ ความเร่งด่วน: {top['urgency']}\n"
                         f"🏠 สถานที่: {top['address']}\n"
                         f"{status_icon} สถานะปัจจุบัน: 【 {top['status']} 】\n"
                         f"⏰ เวลาที่แจ้ง: {time_display}\n"
                         f"──────────────────────\n"
                         f"ℹ️ เจ้าหน้าที่ศูนย์ RTSD ได้รับข้อมูลและอยู่ระหว่างดำเนินการครับ")
            
            ref_id = search_id
            if not ref_id:
                id_m = re.search(r'\[(RTSD-[^\]]+)\]', top['title'])
                if id_m:
                    ref_id = id_m.group(1)
            
            refresh_cmd = f"ติดตาม {ref_id}" if ref_id else "ติดตามสถานะ"
            qr_items = [
                QuickReplyItem(action=MessageAction(label="🔄 รีเฟรช", text=refresh_cmd)),
                QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุใหม่", text="แจ้งเหตุ"))
            ]
            if ref_id:
                qr_items.insert(1, QuickReplyItem(action=MessageAction(label="🟡 ปรับ: กำลังทำ", text=f"ปรับสถานะ {ref_id} กำลังดำเนินการ")))
                qr_items.insert(2, QuickReplyItem(action=MessageAction(label="🟢 ปรับ: เสร็จสิ้น", text=f"เสร็จสิ้น {ref_id}")))

            quick_reply = QuickReply(items=qr_items)
        else:
            reply_msg = (f"🔍 ไม่พบข้อมูลประวัติการแจ้งเหตุของคุณ {user_name}\n\n"
                         f"หากท่านมีรหัสติดตาม สามารถพิมพ์ เช่น:\n"
                         f"'ติดตาม RTSD-1234' เพื่อค้นหาได้เลยครับ")
            quick_reply = QuickReply(items=[
                QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุเตือนภัย", text="แจ้งเหตุ"))
            ])

    # ด่านปรับปรุง/แก้ไขสถานะเหตุการณ์เดิม (สำหรับเจ้าหน้าที่สั่งผ่าน LINE Bot)
    elif any(user_text.startswith(prefix) for prefix in ["ปรับสถานะ", "อัปเดตสถานะ", "อัปเดต", "แก้ไขสถานะ", "เสร็จสิ้น"]):
        parts = user_text.split()
        target_id = ""
        status_raw = ""
        
        if user_text.startswith("เสร็จสิ้น"):
            target_id = parts[1].replace("#", "").strip() if len(parts) > 1 else ""
            status_raw = "เสร็จสิ้น"
        elif len(parts) >= 3:
            target_id = parts[1].replace("#", "").strip()
            status_raw = " ".join(parts[2:]).strip()
        elif len(parts) == 2:
            target_id = parts[1].replace("#", "").strip()
            status_raw = "กำลังดำเนินการ"

        if target_id:
            new_status = "🟡 กำลังดำเนินการ"
            if any(w in status_raw for w in ["เสร็จ", "เรียบร้อย", "สำเร็จ", "done", "close"]):
                new_status = "🟢 แก้ไขแล้วเสร็จ"
            elif any(w in status_raw for w in ["รอ", "wait", "pending"]):
                new_status = "⏳ รอดำเนินการ"
            elif any(w in status_raw for w in ["ยกเลิก", "ระงับ", "cancel"]):
                new_status = "❌ ยกเลิก/ระงับเหตุ"

            res = update_google_sheet_status(title=target_id, new_status=new_status)
            if res.get("status") == "success":
                reply_msg = (f"✅ อัปเดตสถานะเหตุการณ์เรียบร้อยแล้ว!\n"
                             f"──────────────────────\n"
                             f"📌 รหัสเหตุการณ์: #{target_id}\n"
                             f"🔄 สถานะใหม่: {new_status}\n"
                             f"👤 เจ้าหน้าที่ผู้ปรับ: {user_name}\n"
                             f"⏰ เวลา: {time.strftime('%H:%M:%S น.')}\n"
                             f"──────────────────────\n"
                             f"📡 ระบบได้บันทึกลง Google Sheets และ Dashboard เรียบร้อยแล้วครับ")
            else:
                reply_msg = (f"⚠️ ไม่สามารถอัปเดตสถานะได้\n\n"
                             f"ไม่พบเหตุการณ์ที่มีรหัส '{target_id}' ในระบบ หรือ Google Sheets ยังไม่ตอบกลับ\n"
                             f"💡 กรุณาตรวจสอบรหัสเหตุการณ์อีกครั้งครับ")
        else:
            reply_msg = ("ℹ️ คำสั่งปรับสถานะเหตุการณ์สำหรับเจ้าหน้าที่:\n\n"
                         "👉 'ปรับสถานะ [รหัสเหตุการณ์] กำลังดำเนินการ'\n"
                         "👉 'ปรับสถานะ [รหัสเหตุการณ์] เสร็จสิ้น'\n"
                         "👉 'เสร็จสิ้น [รหัสเหตุการณ์]'\n\n"
                         "ตัวอย่าง: เสร็จสิ้น RTSD-0927-1420")

        quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label="🔍 ติดตามสถานะ", text="ติดตามสถานะ")),
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุเตือนภัย", text="แจ้งเหตุ"))
        ])
        reply_message = TextMessage(text=reply_msg, quick_reply=quick_reply)
        
    # ด่านเปิดระบบ Live GPS Tracker สำหรับเจ้าหน้าที่
    elif user_text in ['แทร็กกิ้ง', 'แทร็ก', 'gps', 'พิกัดสด', 'tracking', 'แชร์พิกัดสด', 'ภารกิจ', 'แชร์ตำแหน่ง', 'ระบบแทร็กกิ่ง']:
        tracker_url = "https://rtsd-linebot.onrender.com/tracker"
        portal_map_url = "https://geoportal.rtsd.mi.th/portal/home/webmap/viewer.html?url=https://geoportal.rtsd.mi.th/arcgis/rest/services/Hosted/rtsd_live_trackers/FeatureServer/0"
        reply_msg = (f"🛰️ ระบบติดตามกำลังพลภาคสนาม RTSD (Live GPS Tracker)\n"
                     f"━━━━━━━━━━━━━━━━━━\n"
                     f"สำหรับเจ้าหน้าที่ออกปฏิบัติการ กรมแผนที่ทหาร:\n\n"
                     f"1️⃣ เปิดระบบส่งพิกัดสดต่อเนื่อง (มีโหมดพรางหน้าจอ/ประหยัดแบตเตอรี่):\n"
                     f"👉 {tracker_url}\n\n"
                     f"2️⃣ เปิดดูแผนที่ติดตามกำลังพลบน Portal RTSD:\n"
                     f"👉 {portal_map_url}\n"
                     f"━━━━━━━━━━━━━━━━━━\n"
                     f"💡 หรือกดปุ่ม '📍 ส่งตำแหน่งปัจจุบัน' ด้านล่างเพื่อเช็กอินจุดพิกัดทันทีได้เลยครับ")
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=LocationAction(label="📍 ส่งตำแหน่งปัจจุบัน")),
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุเตือนภัย", text="แจ้งเหตุ")),
            QuickReplyItem(action=MessageAction(label="🔍 ติดตามสถานะ", text="ติดตามสถานะ"))
        ])
        reply_message = TextMessage(text=reply_msg, quick_reply=quick_reply)

    # ด่านที่ 1: ผู้ใช้เริ่มแจ้งเหตุ -> เด้ง Dropdown ให้เลือก "ประเภทเหตุการณ์"
    elif user_text in ['ช่วย', 'แจ้งเหตุ', 'แจ้งเตือน', 'menu', 'วิธีใช้', 'สวัสดี', 'hi', 'hello']:
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label="🌊 น้ำท่วมขัง", text="เลือกเหตุ: 🌊 น้ำท่วมขัง")),
            QuickReplyItem(action=MessageAction(label="🚧 ถนนชำรุด", text="เลือกเหตุ: 🚧 ดินถล่ม / ผิวทางชำรุด")),
            QuickReplyItem(action=MessageAction(label="💥 อุบัติเหตุ", text="เลือกเหตุ: 💥 อุบัติเหตุจราจร")),
            QuickReplyItem(action=MessageAction(label="🔥 ไฟไหม้", text="เลือกเหตุ: 🔥 ไฟไหม้ / หมอกควัน")),
            QuickReplyItem(action=MessageAction(label="🔍 ติดตามสถานะ", text="ติดตามสถานะ")),
            QuickReplyItem(action=MessageAction(label="🛰️ ส่งพิกัดสด GPS", text="แทร็กกิ้ง")),
            QuickReplyItem(action=MessageAction(label="📌 อื่นๆ", text="เลือกเหตุ: 📌 อื่นๆ"))
        ])
        reply_message = TextMessage(
            text=f"สวัสดีครับคุณ {user_name} 🚨\nกรุณาเลือก [ประเภทเหตุการณ์], [ติดตามสถานะ] หรือ [ส่งพิกัดสด] ด้านล่างนี้ครับ 👇",
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

    # ด่านที่ 3: เลือกระดับความเร่งด่วนแล้ว -> เด้งปุ่มให้ "กดแชร์พิกัดสถานที่" (หรือพิมพ์ตัวเลขพิกัดจากคอม)
    elif user_text.startswith("ระดับ:"):
        urgency = user_text.replace("ระดับ:", "").strip()
        user_sessions[user_id]["urgency"] = urgency

        quick_reply = QuickReply(items=[
            QuickReplyItem(action=LocationAction(label="📍 กดแชร์พิกัดจุดเกิดเหตุ"))
        ])
        reply_message = TextMessage(
            text=(f"ระดับความเร่งด่วน: [{urgency}]\n\n"
                  f"👉 ขั้นตอนถัดไป: กรุณาระบุพิกัดสถานที่เกิดเหตุ\n"
                  f"📱 สำหรับมือถือ: กดปุ่ม '📍 กดแชร์พิกัดจุดเกิดเหตุ' ด้านล่างได้เลยครับ\n"
                  f"💻 สำหรับคอมพิวเตอร์ (LINE PC): สามารถพิมพ์ตัวเลขพิกัดส่งมาได้เลยครับ เช่น:\n"
                  f"13.7857, 100.5912"),
            quick_reply=quick_reply
        )

    # ด่านเสริมสำหรับ LINE บนคอมพิวเตอร์: ผู้ใช้พิมพ์ตัวเลขพิกัด Lat, Lon ส่งเข้ามา
    elif re.search(r'([-+]?\d{1,2}\.\d+)[,\s]+([-+]?\d{1,3}\.\d+)', user_text):
        coord_match = re.search(r'([-+]?\d{1,2}\.\d+)[,\s]+([-+]?\d{1,3}\.\d+)', user_text)
        lat = float(coord_match.group(1))
        lon = float(coord_match.group(2))

        session = user_sessions.get(user_id, {})
        session["lat"] = lat
        session["lon"] = lon
        session["address"] = f"พิกัดระบุเอง: {lat}, {lon}"
        session["title"] = f"จุดแจ้งเหตุ ({lat:.4f}, {lon:.4f})"
        session["waiting_for_media"] = True
        user_sessions[user_id] = session

        quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label="⏩ ข้าม (ไม่ส่งรูป)", text="ข้ามการส่งรูป"))
        ])
        reply_message = TextMessage(
            text=(f"📍 ได้รับพิกัดจากคอมพิวเตอร์เรียบร้อยแล้วครับ!\n\n"
                  f"📌 ละติจูด: {lat}\n"
                  f"📌 ลองจิจูด: {lon}\n\n"
                  f"📸 เพื่อความสมบูรณ์ของข้อมูล กรุณาส่ง [รูปถ่าย] หรือ [คลิปวิดีโอ] หลักฐานเข้ามาได้เลยครับ (หรือกดปุ่ม '⏩ ข้าม (ไม่ส่งรูป)' ด้านล่าง)"),
            quick_reply=quick_reply
        )

    else:
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุเตือนภัย", text="แจ้งเหตุ")),
            QuickReplyItem(action=MessageAction(label="🔍 ติดตามสถานะ", text="ติดตามสถานะ"))
        ])
        reply_message = TextMessage(
            text=f"หากต้องการแจ้งเหตุ หรือติดตามสถานะงาน กรุณาเลือกปุ่มด้านล่างนี้ได้เลยครับ 👇",
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
