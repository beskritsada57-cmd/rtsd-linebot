import os
import re
import time
import datetime
import threading
import base64
import math
import requests
import sqlite3
from flask import Flask, request, abort, jsonify, send_file, redirect
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
GOOGLE_SHEET_URL = os.environ.get('GOOGLE_SHEET_URL', "https://script.google.com/macros/s/AKfycbwX4BwiTCEy7gZCymlGDF9_-WArI9RCIsxTpJ2vOOhEp3C2-ut-Pe5S_fN0IpXP-y8/exec")
MASTER_PIN = os.environ.get('MASTER_PIN', 'RTSD2024')

# =========================================================================
# 🎨 3. การกำหนดค่า LINE Rich Menu บูรณาการ (Unified 4-Grid Menu)
# =========================================================================
UNIFIED_RICH_MENU_ID = os.environ.get('UNIFIED_RICH_MENU_ID', 'richmenu-8c2b92141212ba4832157a7e6d9f6d02')
OFFICER_RICH_MENU_ID = UNIFIED_RICH_MENU_ID
CITIZEN_RICH_MENU_ID = UNIFIED_RICH_MENU_ID

def switch_user_rich_menu(user_id, role="citizen"):
    """
    จัดสรรริชเมนูบูรณาการ (Unified Rich Menu)
    """
    if not user_id or str(user_id).startswith("-"):
        return False
    try:
        headers = {"Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}"}
        url = f"https://api.line.me/v2/bot/user/{user_id}/richmenu"
        resp = requests.delete(url, headers=headers, timeout=5)
        return resp.status_code == 200
    except Exception as e:
        print(f"Error resetting rich menu for user {user_id}: {e}")
        return False

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
        "role": "ผู้ดูแลระบบสูงสุด (Super Admin)",
        "unit": "กรมแผนที่ทหาร (RTSD)",
        "position": "ผู้ดูแลระบบหลัก",
        "status": "อนุมัติแล้ว",
        "line_user_id": "U2e495c2521fcf6fda9afee8924b3ba55",
        "area": "เชียงราย (ทุกอำเภอ)",
        "district": "เชียงราย (ทุกอำเภอ)",
        "unit_size": "ชุดปฏิบัติการขนาดเล็ก (3-5 นาย)",
        "vehicle_type": "🚗 รถกระบะตรวจการณ์ 4x4 (Pickup 4WD)"
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


def normalize_drive_image_url(url):
    """แปลงลิงก์ Google Drive ให้เป็น Direct Image URL คมชัดสูง (lh3.googleusercontent.com)"""
    if not url or url == "-" or not str(url).strip():
        return "-"
    u = str(url).strip()
    match = re.search(r'/file/d/([a-zA-Z0-9_-]+)', u)
    if match:
        return f"https://lh3.googleusercontent.com/d/{match.group(1)}=s160-c"
    match2 = re.search(r'[?&]id=([a-zA-Z0-9_-]+)', u)
    if match2:
        return f"https://lh3.googleusercontent.com/d/{match2.group(1)}=s160-c"
    return u


def normalize_phone_number(phone):
    """ปรับรูปแบบเบอร์โทรศัพท์ให้เป็น 10 หลักขึ้นต้นด้วย 0 เสมอ"""
    if not phone:
        return ""
    p = str(phone).strip().replace("-", "").replace(" ", "")
    if len(p) == 9 and p[0] in ['6', '8', '9']:
        return "0" + p
    return p


# =========================================================================
# 🗄️ LOCAL SQLite DATABASE ON QNAP NAS (rtsd_tactical.db)
# จัดเก็บฐานข้อมูลผู้ใช้งาน RTSD ในเครื่อง NAS โดยตรง ป้องกันปัญหาข้อมูลสูญหาย
# และใช้ Google Sheets เป็นตัวสำรองข้อมูลภายนอก (Backup Mirror)
# =========================================================================
DB_FILE = os.path.join(os.path.dirname(__file__), "rtsd_tactical.db")

def get_db_connection():
    """เปิดการเชื่อมต่อ SQLite โดยเปิด timeout 15 วิ และใช้ Row factory"""
    conn = sqlite3.connect(DB_FILE, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_sqlite_db():
    """สร้างตาราง users และ index บน NAS SQLite DB หากยังไม่มี พร้อมรองรับ schema migration"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT UNIQUE NOT NULL,
                name TEXT DEFAULT '',
                role TEXT DEFAULT 'ผู้ใช้งานทั่วไป',
                unit TEXT DEFAULT '-',
                position TEXT DEFAULT '-',
                status TEXT DEFAULT 'อนุมัติแล้ว',
                purpose TEXT DEFAULT '-',
                line_user_id TEXT DEFAULT '',
                username TEXT DEFAULT '',
                password TEXT DEFAULT '',
                picture_profile TEXT DEFAULT '-',
                area TEXT DEFAULT 'เชียงราย (ทุกอำเภอ)',
                district TEXT DEFAULT 'เชียงราย (ทุกอำเภอ)',
                unit_size TEXT DEFAULT 'ชุดปฏิบัติการขนาดเล็ก (3-5 นาย)',
                vehicle_type TEXT DEFAULT '🚗 รถกระบะตรวจการณ์ 4x4 (Pickup 4WD)',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_line_user_id ON users(line_user_id);")

            # ตรวจสอบและเพิ่มคอลัมน์อัตโนมัติหากมีคอลัมน์ใหม่ในอนาคต (Schema Auto-Migration)
            cursor.execute("PRAGMA table_info(users);")
            existing_cols = {col["name"] for col in cursor.fetchall()}
            for col_name, col_def in [
                ("area", "TEXT DEFAULT 'เชียงราย (ทุกอำเภอ)'"),
                ("district", "TEXT DEFAULT 'เชียงราย (ทุกอำเภอ)'"),
                ("unit_size", "TEXT DEFAULT 'ชุดปฏิบัติการขนาดเล็ก (3-5 นาย)'"),
                ("vehicle_type", "TEXT DEFAULT '🚗 รถกระบะตรวจการณ์ 4x4 (Pickup 4WD)'"),
                ("username", "TEXT DEFAULT ''"),
                ("password", "TEXT DEFAULT ''"),
                ("picture_profile", "TEXT DEFAULT '-'")
            ]:
                if col_name not in existing_cols:
                    try:
                        cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def};")
                    except Exception:
                        pass

            # สร้างบัญชี Master Admin เริ่มต้นเสมอ
            cursor.execute("""
            INSERT INTO users (phone, name, role, unit, position, status, line_user_id, username, password)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(phone) DO UPDATE SET
                role='ผู้ดูแลระบบสูงสุด (Super Admin)',
                status='อนุมัติแล้ว',
                updated_at=CURRENT_TIMESTAMP
            """, (
                "0863390614",
                "ผู้ดูแลระบบสูงสุด (Master Admin)",
                "ผู้ดูแลระบบสูงสุด (Super Admin)",
                "กรมแผนที่ทหาร (RTSD)",
                "ผู้ดูแลระบบหลัก",
                "อนุมัติแล้ว",
                "U2e495c2521fcf6fda9afee8924b3ba55",
                "admin",
                MASTER_PIN
            ))
            conn.commit()
            print("[SQLite] Database initialized and master admin verified.")
    except Exception as e:
        print(f"[SQLite] Error initializing database: {e}")

def db_upsert_user(phone, name="", role="ผู้ใช้งานทั่วไป", unit="-", position="-", status="อนุมัติแล้ว",
                   purpose="-", line_user_id="", username="", password="", picture_profile="-",
                   area="เชียงราย (ทุกอำเภอ)", unit_size="ชุดปฏิบัติการขนาดเล็ก (3-5 นาย)",
                   vehicle_type="🚗 รถกระบะตรวจการณ์ 4x4 (Pickup 4WD)"):
    """บันทึกหรืออัปเดตข้อมูลผู้ใช้งานลง SQLite ในเครื่อง NAS โดยตรง"""
    clean_phone = normalize_phone_number(phone) or str(phone).strip().replace("-", "").replace(" ", "")
    if not clean_phone:
        return False

    if clean_phone in ADMIN_PHONES or clean_phone == "0863390614":
        role = "ผู้ดูแลระบบสูงสุด (Super Admin)"

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE phone = ?", (clean_phone,))
            existing = cursor.fetchone()

            final_name = name or (existing["name"] if existing and existing["name"] else "ผู้ใช้งาน")
            final_role = role or (existing["role"] if existing and existing["role"] else "ผู้ใช้งานทั่วไป")
            final_unit = unit if unit and unit != "-" else (existing["unit"] if existing and existing["unit"] else "-")
            final_pos = position if position and position != "-" else (existing["position"] if existing and existing["position"] else "-")
            final_status = status or (existing["status"] if existing and existing["status"] else "อนุมัติแล้ว")
            final_purpose = purpose if purpose and purpose != "-" else (existing["purpose"] if existing and existing["purpose"] else "-")

            old_lid = existing["line_user_id"] if existing else ""
            final_lid = line_user_id if str(line_user_id).startswith("U") else (old_lid if str(old_lid).startswith("U") else (line_user_id or old_lid or ""))

            final_uname = username or (existing["username"] if existing and existing["username"] else "")
            final_pwd = password or (existing["password"] if existing and existing["password"] else "")

            old_pic = existing["picture_profile"] if existing else "-"
            final_pic = picture_profile if picture_profile and picture_profile != "-" else old_pic

            final_area = area if area and area != "-" else (existing["area"] if existing and existing["area"] else "เชียงราย (ทุกอำเภอ)")
            final_unit_size = unit_size if unit_size and unit_size != "-" else (existing["unit_size"] if existing and existing["unit_size"] else "ชุดปฏิบัติการขนาดเล็ก (3-5 นาย)")
            final_vehicle = vehicle_type if vehicle_type and vehicle_type != "-" else (existing["vehicle_type"] if existing and existing["vehicle_type"] else "🚗 รถกระบะตรวจการณ์ 4x4 (Pickup 4WD)")

            cursor.execute("""
            INSERT INTO users (
                phone, name, role, unit, position, status, purpose,
                line_user_id, username, password, picture_profile,
                area, district, unit_size, vehicle_type, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(phone) DO UPDATE SET
                name = excluded.name,
                role = excluded.role,
                unit = excluded.unit,
                position = excluded.position,
                status = excluded.status,
                purpose = excluded.purpose,
                line_user_id = excluded.line_user_id,
                username = CASE WHEN excluded.username != '' THEN excluded.username ELSE users.username END,
                password = CASE WHEN excluded.password != '' THEN excluded.password ELSE users.password END,
                picture_profile = CASE WHEN excluded.picture_profile != '-' AND excluded.picture_profile != '' THEN excluded.picture_profile ELSE users.picture_profile END,
                area = excluded.area,
                district = excluded.district,
                unit_size = excluded.unit_size,
                vehicle_type = excluded.vehicle_type,
                updated_at = CURRENT_TIMESTAMP;
            """, (
                clean_phone, final_name, final_role, final_unit, final_pos, final_status, final_purpose,
                final_lid, final_uname, final_pwd, final_pic,
                final_area, final_area, final_unit_size, final_vehicle
            ))
            conn.commit()
            return True
    except Exception as e:
        print(f"[SQLite] Error upserting user {clean_phone}: {e}")
        return False

def db_get_user_by_phone(phone):
    """ค้นหาผู้ใช้จากเบอร์โทรศัพท์ใน SQLite"""
    clean_phone = normalize_phone_number(phone) or str(phone).strip().replace("-", "").replace(" ", "")
    if not clean_phone:
        return None
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE phone = ?", (clean_phone,))
            row = cursor.fetchone()
            if row:
                return dict(row)
    except Exception as e:
        print(f"[SQLite] Error getting user by phone: {e}")
    return None

def db_get_user_by_username(username):
    """ค้นหาผู้ใช้จาก username ใน SQLite"""
    if not username:
        return None
    uname = str(username).strip().lower()
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE LOWER(username) = ? OR phone = ?", (uname, uname))
            row = cursor.fetchone()
            if row:
                return dict(row)
    except Exception as e:
        print(f"[SQLite] Error getting user by username: {e}")
    return None

def db_get_all_users():
    """ดึงรายชื่อผู้ใช้ทั้งหมดจาก SQLite"""
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users ORDER BY id ASC")
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[SQLite] Error getting all users: {e}")
        return []

def db_delete_user(phone):
    """ลบผู้ใช้ออกจาก SQLite ในเครื่อง NAS อย่างถาวร"""
    clean_phone = normalize_phone_number(phone) or str(phone).strip().replace("-", "").replace(" ", "")
    if not clean_phone:
        return False
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE phone = ?", (clean_phone,))
            conn.commit()
            print(f"[SQLite] Deleted user {clean_phone} from database.")
            return True
    except Exception as e:
        print(f"[SQLite] Error deleting user: {e}")
        return False

def sync_db_to_memory_cache():
    """ซิงค์ข้อมูลจาก SQLite บน NAS เข้าสู่ Memory cache (phone_to_user, registered_users, USER_CREDENTIALS)"""
    global phone_to_user, registered_users, USER_CREDENTIALS, DELETED_USERS
    try:
        users = db_get_all_users()
        for u in users:
            ph = normalize_phone_number(u.get("phone", ""))
            if not ph or ph in DELETED_USERS:
                continue

            role = u.get("role", "ผู้ใช้งานทั่วไป")
            if ph in ADMIN_PHONES or ph == "0863390614":
                role = "ผู้ดูแลระบบสูงสุด (Super Admin)"

            user_entry = {
                "name": u.get("name", "ผู้ใช้งาน"),
                "phone": ph,
                "username": u.get("username", ""),
                "role": role,
                "unit": u.get("unit", "-"),
                "position": u.get("position", "-"),
                "status": u.get("status", "อนุมัติแล้ว"),
                "purpose": u.get("purpose", "-"),
                "area": u.get("area", "เชียงราย (ทุกอำเภอ)"),
                "district": u.get("district", "เชียงราย (ทุกอำเภอ)"),
                "unit_size": u.get("unit_size", "ชุดปฏิบัติการขนาดเล็ก (3-5 นาย)"),
                "vehicle_type": u.get("vehicle_type", "🚗 รถกระบะตรวจการณ์ 4x4 (Pickup 4WD)"),
                "picture_profile": u.get("picture_profile", "-"),
                "permissions": get_user_permissions(role)
            }
            lid = u.get("line_user_id", "")
            phone_to_user[ph] = {"line_user_id": lid, **user_entry}
            if lid and str(lid).startswith("U"):
                registered_users[lid] = user_entry

            pwd = u.get("password", "")
            uname = u.get("username", "")
            if pwd:
                cred_item = {
                    "username": uname or ph,
                    "password": pwd,
                    "phone": ph,
                    "name": user_entry["name"]
                }
                USER_CREDENTIALS[ph] = cred_item
                if uname:
                    USER_CREDENTIALS[uname] = cred_item
        print(f"[SQLite] Synced {len(users)} users to memory cache successfully.")
    except Exception as e:
        print(f"[SQLite] Error syncing db to memory cache: {e}")

def migrate_existing_data_to_sqlite():
    """ไมเกรตข้อมูลจาก registered_users.json และ user_credentials.json เข้าสู่ SQLite ครั้งแรก"""
    # 1. จาก registered_users.json
    if os.path.exists(REGISTERED_USERS_FILE):
        try:
            with open(REGISTERED_USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                cached = data.get("phone_to_user", {})
                for ph, u in cached.items():
                    norm_ph = normalize_phone_number(ph)
                    if norm_ph and norm_ph not in DELETED_USERS:
                        db_upsert_user(
                            phone=norm_ph,
                            name=u.get("name", ""),
                            role=u.get("role", "ผู้ใช้งานทั่วไป"),
                            unit=u.get("unit", "-"),
                            position=u.get("position", "-"),
                            status=u.get("status", "อนุมัติแล้ว"),
                            purpose=u.get("purpose", "-"),
                            line_user_id=u.get("line_user_id", ""),
                            username=u.get("username", ""),
                            picture_profile=u.get("picture_profile", "-"),
                            area=u.get("area", "เชียงราย (ทุกอำเภอ)"),
                            unit_size=u.get("unit_size", "ชุดปฏิบัติการขนาดเล็ก (3-5 นาย)"),
                            vehicle_type=u.get("vehicle_type", "🚗 รถกระบะตรวจการณ์ 4x4 (Pickup 4WD)")
                        )
        except Exception as e:
            print(f"[SQLite] Error migrating registered_users.json: {e}")

    # 2. จาก user_credentials.json
    if os.path.exists(USER_CREDENTIALS_FILE):
        try:
            with open(USER_CREDENTIALS_FILE, "r", encoding="utf-8") as f:
                creds = json.load(f)
                for k, v in creds.items():
                    ph = normalize_phone_number(v.get("phone", "")) or (normalize_phone_number(k) if k.isdigit() else "")
                    if ph and v.get("password"):
                        db_upsert_user(
                            phone=ph,
                            name=v.get("name", ""),
                            username=v.get("username", ""),
                            password=v.get("password", "")
                        )
        except Exception as e:
            print(f"[SQLite] Error migrating user_credentials.json: {e}")


# 🔑 Tactical User Credentials Store (Phone/Username -> Password/PIN)
USER_CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "user_credentials.json")
USER_CREDENTIALS = {}

# 🗑️ Persistent Deleted Users Store (ป้องกันการดึงรายชื่อที่แอดมินลบแล้วกลับมาแสดง)
DELETED_USERS_FILE = os.path.join(os.path.dirname(__file__), "deleted_users.json")
DELETED_USERS = set()

def load_deleted_users():
    global DELETED_USERS
    if os.path.exists(DELETED_USERS_FILE):
        try:
            with open(DELETED_USERS_FILE, "r", encoding="utf-8") as f:
                DELETED_USERS = set(json.load(f))
        except Exception as e:
            print(f"Error loading deleted_users.json: {e}")
            DELETED_USERS = set()
    else:
        DELETED_USERS = set()

def save_deleted_users():
    try:
        with open(DELETED_USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(DELETED_USERS), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving deleted_users.json: {e}")

load_deleted_users()

# 🗑️ Persistent Deleted Incidents Store (เก็บประวัติเหตุการณ์ที่แอดมินลบถาวร)
DELETED_INCIDENTS_FILE = os.path.join(os.path.dirname(__file__), "deleted_incidents.json")
DELETED_INCIDENTS = set()

def load_deleted_incidents():
    global DELETED_INCIDENTS
    if os.path.exists(DELETED_INCIDENTS_FILE):
        try:
            with open(DELETED_INCIDENTS_FILE, "r", encoding="utf-8") as f:
                DELETED_INCIDENTS = set(json.load(f))
        except Exception as e:
            print(f"Error loading deleted_incidents.json: {e}")
            DELETED_INCIDENTS = set()
    else:
        DELETED_INCIDENTS = set()

def save_deleted_incidents():
    try:
        with open(DELETED_INCIDENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(DELETED_INCIDENTS), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving deleted_incidents.json: {e}")

load_deleted_incidents()

def load_user_credentials():
    global USER_CREDENTIALS
    if os.path.exists(USER_CREDENTIALS_FILE):
        try:
            with open(USER_CREDENTIALS_FILE, "r", encoding="utf-8") as f:
                USER_CREDENTIALS = json.load(f)
        except Exception as e:
            print(f"Error loading user_credentials.json: {e}")
            USER_CREDENTIALS = {}
    else:
        USER_CREDENTIALS = {}

    # Pre-populate default admin credentials
    default_creds = {
        "0863390614": {"username": "admin", "password": MASTER_PIN, "phone": "0863390614"},
        "admin": {"username": "admin", "password": "admin1234", "phone": "0863390614"},
        "commander": {"username": "commander", "password": MASTER_PIN, "phone": "0863390614"}
    }
    for k, v in default_creds.items():
        if k not in USER_CREDENTIALS:
            USER_CREDENTIALS[k] = v

def save_user_credentials():
    try:
        with open(USER_CREDENTIALS_FILE, "w", encoding="utf-8") as f:
            json.dump(USER_CREDENTIALS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving user_credentials.json: {e}")

load_user_credentials()

# 👥 Persistent Registered Users Store (บันทึกรายชื่อผู้ใช้ลงดิสก์ ป้องกันข้อมูลโปรไฟล์หายเมื่อรีสตาร์ทคอนเทนเนอร์)
REGISTERED_USERS_FILE = os.path.join(os.path.dirname(__file__), "registered_users.json")

def load_registered_users_cache():
    global registered_users, phone_to_user
    if os.path.exists(REGISTERED_USERS_FILE):
        try:
            with open(REGISTERED_USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                cached = data.get("phone_to_user", {})
                for ph, u in cached.items():
                    norm_ph = normalize_phone_number(ph) or ph
                    phone_to_user[norm_ph] = u
                    lid = u.get("line_user_id")
                    if lid and lid != "-" and str(lid).startswith("U"):
                        registered_users[lid] = u
        except Exception as e:
            print(f"Error loading registered_users.json: {e}")

def save_registered_users_cache():
    global phone_to_user
    try:
        with open(REGISTERED_USERS_FILE, "w", encoding="utf-8") as f:
            json.dump({"phone_to_user": phone_to_user}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving registered_users.json: {e}")

load_registered_users_cache()

# 🚀 เริ่มต้นการทำงานของฐานข้อมูล SQLite บน NAS ทันที
init_sqlite_db()
migrate_existing_data_to_sqlite()
sync_db_to_memory_cache()

# 🔐 Persistent OTP Cache Store (รองรับ Multi-worker และป้องกัน OTP หายเวลารีสตาร์ทคอนเทนเนอร์)
OTP_CACHE_FILE = os.path.join(os.path.dirname(__file__), "otp_cache.json")

def get_cached_otp(phone):
    global otp_cache
    if os.path.exists(OTP_CACHE_FILE):
        try:
            with open(OTP_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if phone in data:
                    otp_cache[phone] = data[phone]
                    return data[phone]
        except Exception:
            pass
    return otp_cache.get(phone)

def set_cached_otp(phone, entry):
    global otp_cache
    otp_cache[phone] = entry
    try:
        data = {}
        if os.path.exists(OTP_CACHE_FILE):
            with open(OTP_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        data[phone] = entry
        with open(OTP_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving otp_cache: {e}")

def delete_cached_otp(phone):
    global otp_cache
    otp_cache.pop(phone, None)
    if os.path.exists(OTP_CACHE_FILE):
        try:
            with open(OTP_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if phone in data:
                del data[phone]
                with open(OTP_CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

# 🗺️ Patrol Route Tracklines Store (บันทึกเส้นทางเดินของแต่ละชุดปฏิบัติการแบบ Polyline)
UNIT_TRACKS_FILE = os.path.join(os.path.dirname(__file__), "unit_tracks.json")
unit_tracks = {} # { unit_id: [ {"lon": 100.x, "lat": 13.y, "time": "...", "speed": 10, "heading": 90}, ... ] }

def load_unit_tracks():
    global unit_tracks
    if os.path.exists(UNIT_TRACKS_FILE):
        try:
            with open(UNIT_TRACKS_FILE, "r", encoding="utf-8") as f:
                unit_tracks = json.load(f)
        except Exception as e:
            print(f"Error loading unit_tracks.json: {e}")
            unit_tracks = {}
    else:
        unit_tracks = {}

def save_unit_tracks():
    try:
        with open(UNIT_TRACKS_FILE, "w", encoding="utf-8") as f:
            json.dump(unit_tracks, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving unit_tracks.json: {e}")

def calculate_track_distance_km(pts):
    if not pts or len(pts) < 2:
        return 0.0
    total_m = 0.0
    for i in range(len(pts) - 1):
        p1 = pts[i]
        p2 = pts[i + 1]
        dlat = (p2["lat"] - p1["lat"]) * 111320
        dlon = (p2["lon"] - p1["lon"]) * 111320 * math.cos(math.radians((p1["lat"] + p2["lat"]) / 2.0))
        total_m += math.sqrt(dlat * dlat + dlon * dlon)
    return round(total_m / 1000.0, 2)

load_unit_tracks()




def fetch_registered_users():
    """ดึงรายชื่อผู้ใช้จาก SQLite บน NAS เป็นหลัก และนำเข้าข้อมูลใหม่จาก Google Sheets มาอัปเดตลง SQLite"""
    global registered_users, phone_to_user, DELETED_USERS
    master_phone = "0863390614"

    # 1. ซิงค์ข้อมูลทั้งหมดจาก SQLite บน NAS เข้าสู่ Memory ทันที
    sync_db_to_memory_cache()

    try:
        # 2. ซิงค์จาก Google Sheets เพื่อดึงข้อมูลกำลังพลใหม่ๆ ที่อาจถูกเพิ่มผ่าน Sheet
        res = requests.get(f"{GOOGLE_SHEET_URL}?sheet=users", timeout=10)
        if res.status_code == 200:
            users = res.json()
            if isinstance(users, list):
                for u in users:
                    picture_profile = "-"
                    unit = "-"
                    position = "-"
                    status = "อนุมัติแล้ว"
                    unit_size = "ชุดปฏิบัติการขนาดเล็ก (3-5 นาย)"
                    vehicle_type = "🚗 รถกระบะตรวจการณ์ 4x4 (Pickup 4WD)"
                    area = "เชียงราย (ทุกอำเภอ)"
                    user_uname = ""
                    purpose = "-"

                    if isinstance(u, dict):
                        lid = str(u.get("line_user_id", "")).strip()
                        raw_phone = str(u.get("phone_number", "")).strip()
                        name = str(u.get("full_name", "")).strip()
                        role = str(u.get("role", "ผู้ใช้งานทั่วไป")).strip()
                        unit = str(u.get("unit", "-")).strip()
                        position = str(u.get("position", "-")).strip()
                        status = str(u.get("approval_status", u.get("status", "อนุมัติแล้ว"))).strip()
                        purpose = str(u.get("purpose", "-")).strip()
                        picture_profile = str(u.get("picture_profile", u.get("picture_url", "-"))).strip()
                        unit_size = str(u.get("unit_size", unit_size)).strip()
                        vehicle_type = str(u.get("vehicle_type", vehicle_type)).strip()
                        area = str(u.get("area", u.get("district", area))).strip()
                        user_uname = str(u.get("username", "")).strip()
                    elif isinstance(u, list) and len(u) >= 4:
                        if str(u[0]).lower() == "registered_at":
                            continue
                        lid = str(u[1]).strip()
                        name = str(u[2]).strip()
                        raw_phone = str(u[3]).strip()
                        role = str(u[4]).strip() if len(u) > 4 else "ผู้ใช้งานทั่วไป"
                        unit = str(u[5]).strip() if len(u) > 5 else "-"
                        position = str(u[6]).strip() if len(u) > 6 else "-"
                        status = str(u[7]).strip() if len(u) > 7 else "อนุมัติแล้ว"
                        purpose = str(u[8]).strip() if len(u) > 8 else "-"
                        picture_profile = str(u[9]).strip() if len(u) > 9 else "-"
                        if len(u) > 10:
                            user_uname = str(u[10]).strip()
                        if len(u) > 11:
                            area = str(u[11]).strip()
                        if len(u) > 12:
                            unit_size = str(u[12]).strip()
                        if len(u) > 13:
                            vehicle_type = str(u[13]).strip()
                    else:
                        continue

                    phone = normalize_phone_number(raw_phone)
                    if not phone or phone in DELETED_USERS:
                        continue  # ข้ามเบอร์ที่ถูกลบแล้ว ไม่นำกลับเข้ามาแสดง

                    direct_pic = normalize_drive_image_url(picture_profile)

                    if phone in ADMIN_PHONES or phone == master_phone:
                        role = "ผู้ดูแลระบบสูงสุด (Super Admin)"

                    # บันทึกแถวใหม่หรืออัปเดตลง SQLite DB บน NAS
                    db_upsert_user(
                        phone=phone,
                        name=name,
                        role=role,
                        unit=unit,
                        position=position,
                        status=status,
                        purpose=purpose,
                        line_user_id=lid,
                        username=user_uname,
                        picture_profile=direct_pic or "-",
                        area=area,
                        unit_size=unit_size,
                        vehicle_type=vehicle_type
                    )

        # 3. อัปเดต Memory Cache จาก SQLite ล่าสุด
        sync_db_to_memory_cache()
        save_registered_users_cache()
    except Exception as e:
        print(f"Error fetching users: {e}")
        # แม้ Sheets มีปัญหา ก็ยังคงใช้ข้อมูลจาก SQLite บน NAS ได้ 100%
        sync_db_to_memory_cache()


def save_registered_user(line_user_id, name, phone, role="ผู้ใช้งาน", unit="-", position="-", status="อนุมัติแล้ว", purpose="-", picture_profile="-", picture_base64="", username="", unit_size="ชุดปฏิบัติการขนาดเล็ก (3-5 นาย)", vehicle_type="🚗 รถกระบะตรวจการณ์ 4x4 (Pickup 4WD)", area="เชียงราย (ทุกอำเภอ)", password=""):
    """บันทึกข้อมูลผู้ใช้ใหม่ลงฐานข้อมูล SQLite บน NAS ทันที พร้อมสำรองลง Google Sheets และแคชหน่วยความจำ"""
    global registered_users, phone_to_user, DELETED_USERS, USER_CREDENTIALS
    clean_phone = normalize_phone_number(phone) or str(phone).replace("-", "").replace(" ", "")

    # หากเคยถูกลบ ให้นำออกจาก Blacklist ทันทีเมื่อมีการลงทะเบียนใหม่
    if clean_phone in DELETED_USERS:
        DELETED_USERS.remove(clean_phone)
        save_deleted_users()

    # หากมีรูป Base64 ให้ตั้งค่ารูปโปรไฟล์ทันที เพื่อให้แสดงผลได้ทันที
    if picture_base64:
        picture_profile = f"data:image/jpeg;base64,{picture_base64}"
    elif picture_profile and picture_profile != "-":
        picture_profile = normalize_drive_image_url(picture_profile)

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

    user_username = username or USER_CREDENTIALS.get(clean_phone, {}).get("username", "")
    existing_pwd = password or USER_CREDENTIALS.get(clean_phone, {}).get("password", "")

    if clean_phone in ADMIN_PHONES or clean_phone == "0863390614":
        role = "ผู้ดูแลระบบสูงสุด (Super Admin)"

    # 1. 🗄️ บันทึกลง SQLite DB บน NAS ทันที (Single Source of Truth)
    db_upsert_user(
        phone=clean_phone,
        name=name,
        role=role,
        unit=unit,
        position=position,
        status=status,
        purpose=purpose,
        line_user_id=line_user_id,
        username=user_username,
        password=existing_pwd,
        picture_profile=picture_profile or "-",
        area=area,
        unit_size=unit_size,
        vehicle_type=vehicle_type
    )

    # 2. ⚡ อัปเดต Memory Cache และไฟล์ JSON สำรอง
    user_data = {
        "name": name,
        "phone": clean_phone,
        "username": user_username,
        "role": role,
        "unit": unit,
        "position": position,
        "area": area,
        "district": area,
        "status": status,
        "purpose": purpose,
        "picture_profile": picture_profile or "-",
        "unit_size": unit_size,
        "vehicle_type": vehicle_type,
        "permissions": get_user_permissions(role)
    }
    if line_user_id and line_user_id != "-":
        registered_users[line_user_id] = user_data
    phone_to_user[clean_phone] = {"line_user_id": line_user_id, **user_data}
    save_registered_users_cache()

    # 3. 🌐 ซิงค์ส่งต่อเป็น Backup Mirror ไปยัง Google Sheets
    try:
        payload = {
            "action": "register_user",
            "line_user_id": line_user_id,
            "full_name": name,
            "phone_number": clean_phone,
            "username": user_username,
            "role": role,
            "unit": unit,
            "position": position,
            "approval_status": status,
            "purpose": purpose,
            "picture_profile": picture_profile or "-",
            "picture_base64": picture_base64 or "",
            "area": area,
            "district": area,
            "unit_size": unit_size,
            "vehicle_type": vehicle_type
        }
        requests.post(GOOGLE_SHEET_URL, json=payload, timeout=10)
        return True
    except Exception as e:
        print(f"Error mirroring user to Google Sheets: {e}")
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


def push_line_message(target_user_id, text_message):
    """ส่ง Push Message หาผู้ใช้ LINE หรือ Admin ตาม User ID"""
    if not target_user_id or not str(target_user_id).startswith("U"):
        return False
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.push_message(
                PushMessageRequest(
                    to=str(target_user_id),
                    messages=[TextMessage(text=text_message)]
                )
            )
            return True
    except Exception as e:
        print(f"Error in push_line_message to {target_user_id}: {e}")
        return False


# แคชข้อมูลเหตุการณ์จาก Google Sheets เพื่อความเร็วและลด API quota
INCIDENTS_CACHE = {
    "data": [],
    "last_fetched": 0
}


def calculate_distance_km(lat1, lon1, lat2, lon2):
    """คำนวณระยะทางทางภูมิศาสตร์เป็นกิโลเมตรด้วยสูตร Haversine"""
    try:
        lat1, lon1, lat2, lon2 = float(lat1), float(lon1), float(lat2), float(lon2)
        if (lat1 == 0 and lon1 == 0) or (lat2 == 0 and lon2 == 0):
            return 999999.0
        R = 6371.0 # รัศมีโลกเฉลี่ย (กิโลเมตร)
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        return round(R * c, 2)
    except Exception:
        return 999999.0


def save_to_google_sheet(lat, lon, title, address, reporter, urgency, incident_type, file_base64=None, file_name=None, mime_type=None):
    """ส่งข้อมูลครบทั้ง 10 คอลัมน์ (รวมรูปถ่าย/วิดีโอ) ไปบันทึกลง Google Sheets และ Drive"""
    global INCIDENTS_CACHE
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
        INCIDENTS_CACHE["last_fetched"] = 0 # ล้างแคชเพื่อให้ดึงข้อมูลใหม่ทันที
        return res.status_code == 200
    except Exception as e:
        print(f"Error saving to Google Sheets: {e}")
        return False


def update_google_sheet_status(title=None, timestamp=None, new_status="🟢 แก้ไขแล้วเสร็จ"):
    """ส่งคำสั่งไปค้นหาแถวเดิมและอัปเดตสถานะใน Google Sheets"""
    global INCIDENTS_CACHE
    try:
        payload = {
            "action": "update_status",
            "title": str(title) if title else "",
            "timestamp": str(timestamp) if timestamp else "",
            "status": str(new_status)
        }
        res = requests.post(GOOGLE_SHEET_URL, json=payload, timeout=20)
        INCIDENTS_CACHE["last_fetched"] = 0 # ล้างแคชเพื่อให้ดึงข้อมูลใหม่ทันที
        if res.status_code == 200:
            return res.json()
        return {"status": "error", "message": f"HTTP {res.status_code}"}
    except Exception as e:
        print(f"Error updating incident status: {e}")
        return {"status": "error", "message": str(e)}


def get_user_incidents(query_text=None, user_name=None, limit=50):
    """ดึงข้อมูลสถานะการแจ้งเหตุจาก Google Sheets (ผ่าน doGet) เพื่อระบบ Tracking และ SitRep รอบตัว"""
    global INCIDENTS_CACHE
    now = time.time()
    rows = None

    # ตรวจสอบแคชก่อน (อายุแคช 30 วินาที)
    if INCIDENTS_CACHE.get("data") and (now - INCIDENTS_CACHE.get("last_fetched", 0) < 30):
        rows = INCIDENTS_CACHE["data"]
    else:
        try:
            res = requests.get(GOOGLE_SHEET_URL, timeout=20)
            if res.status_code == 200:
                rows = res.json()
                INCIDENTS_CACHE["data"] = rows
                INCIDENTS_CACHE["last_fetched"] = now
        except Exception as e:
            print(f"Error fetching incidents from Google Sheets: {e}")
            rows = INCIDENTS_CACHE.get("data")

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
        try:
            lat_val = float(r[3])
            lon_val = float(r[4])
        except (ValueError, TypeError, IndexError):
            lat_val = 0.0
            lon_val = 0.0

        reporter = str(r[5])
        urgency = str(r[6])
        incident_type = str(r[7])
        status = str(r[8]).strip() or "⏳ รอดำเนินการ"
        media_url = str(r[9]) if len(r) > 9 else "-"

        item = {
            "timestamp": timestamp,
            "title": title,
            "address": address,
            "latitude": lat_val,
            "longitude": lon_val,
            "reporter": reporter,
            "urgency": urgency,
            "incident_type": incident_type,
            "status": status,
            "media_url": media_url
        }

        # ถ้าผู้ใช้ระบุรหัสเหตุ เช่น RTSD-1234
        if query_text and (query_text.lower() in title.lower() or query_text.lower() in timestamp.lower()):
            matched.append(item)
        # ถ้าไม่ระบุรหัส ให้ค้นจากชื่อผู้แจ้ง
        elif user_name and user_name.lower() in reporter.lower():
            matched.append(item)
        # ถ้าไม่ระบุทั้งสอง ให้ดึงทั้งหมดตามจำนวน limit
        elif not query_text and not user_name:
            matched.append(item)
            if len(matched) >= limit:
                break

    return matched


# ที่เก็บข้อมูลพิกัดสดของหน่วยกำลังพล / ยานพาหนะ (In-memory Active Units)
active_trackers = {}
# แผนที่จับคู่รหัสเหตุการณ์กับ LINE User ID ของประชาชนผู้แจ้งเหตุ
INCIDENT_REPORTERS = {}


@app.route("/", methods=['GET'])
def index():
    return ("✅ LINE Bot Webhook with Incident Tracking & Command Dashboard is Running Online!<br><br>"
            "👉 เข้าชมหน้า Dashboard ติดตามสถานการณ์ได้ที่: <a href='/dashboard'><b>/dashboard</b></a><br>"
            "👉 หน้าลงทะเบียนขอใช้งานระบบ (Access Portal): <a href='/register'><b>/register</b></a><br>"
            "👉 เปิดหน้าจอส่งพิกัดสดสำหรับเจ้าหน้าที่ (Live Tracker): <a href='/tracker'><b>/tracker</b></a>")



@app.route("/map", methods=['GET'])
@app.route("/situational-map", methods=['GET'])
def situational_map_page():
    """หน้าเว็บแผนที่สถานการณ์สดสำหรับมือถือ (Mobile-First Situational Map)"""
    html_path = os.path.join(os.path.dirname(__file__), "map.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            headers = {
                'Content-Type': 'text/html; charset=utf-8',
                'Cache-Control': 'no-cache, no-store, must-revalidate',
                'Pragma': 'no-cache',
                'Expires': '0'
            }
            return f.read(), 200, headers
    return "Map HTML template not found on server", 404


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


# 📦 ลิงก์ดาวน์โหลด APK ผ่าน Google Drive (RTSD_Tactical_Tracker_v1.6_ProfileSync.apk)
APK_DOWNLOAD_URL = "https://drive.google.com/file/d/1hJO9oDcaq6glCIscPkxqEoxqyWfjTCkR/view?usp=sharing"

@app.route("/download", methods=['GET'])
@app.route("/download/app", methods=['GET'])
def download_app_file():
    """Redirect ไปดาวน์โหลด APK จาก Google Drive โดยตรง"""
    return redirect(APK_DOWNLOAD_URL)


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

    speed = float(data.get("speed", 0))
    raw_status = str(data.get("status", "🟢 สแตนด์บายพร้อมปฏิบัติการ")).strip()
    
    lat = float(data.get("latitude", 0))
    lon = float(data.get("longitude", 0))

    # Real-time state deduction: ถ้า speed > 2 กม./ชม. หรือมีคีย์เวิร์ดภารกิจถือว่า ACTIVE เคลื่อนที่
    active_trackers[unit_id] = {
        "unit_id": unit_id,
        "unit_name": str(data.get("unit_name", "ชุดปฏิบัติการ")),
        "commander": commander,
        "latitude": lat,
        "longitude": lon,
        "speed": speed,
        "heading": float(data.get("heading", 0)),
        "battery": int(data.get("battery", 100)),
        "status": raw_status,
        "picture_profile": pic or "-",
        "unit_size": str(data.get("unit_size", "-")),
        "vehicle_type": str(data.get("vehicle_type", "-")),
        "last_update": data.get("timestamp", time.strftime("%Y-%m-%d %H:%M:%S")),
        "last_seen_epoch": time.time(),
        "is_line_tracking": str(data.get("record_track", "")).lower() in ["true", "1", "yes"]
    }

    # 🗺️ บันทึกประวัติพิกัดลง Tracklines (Polyline) ของชุดปฏิบัติการ
    # เฉพาะเมื่อเจ้าหน้าที่กดเปิดแทร็กกิ่งแบบเส้นในแอป (record_track = True) เท่านั้น
    record_track = str(data.get("record_track", "")).lower() in ["true", "1", "yes"]
    if record_track and lat != 0 and lon != 0 and -90 <= lat <= 90 and -180 <= lon <= 180:
        if unit_id not in unit_tracks:
            unit_tracks[unit_id] = []
        pts = unit_tracks[unit_id]
        should_add = True
        if pts:
            last_pt = pts[-1]
            dlat = (lat - last_pt["lat"]) * 111320
            dlon = (lon - last_pt["lon"]) * 111320 * math.cos(math.radians(lat))
            if math.sqrt(dlat * dlat + dlon * dlon) < 4.0:
                should_add = False
        if should_add:
            pts.append({
                "lon": round(lon, 6),
                "lat": round(lat, 6),
                "time": data.get("timestamp", time.strftime("%Y-%m-%d %H:%M:%S")),
                "speed": speed,
                "heading": float(data.get("heading", 0))
            })
            if len(pts) > 3000:
                unit_tracks[unit_id] = pts[-3000:]
            save_unit_tracks()

    return jsonify({"status": "success", "unit_id": unit_id}), 200


@app.route("/api/tracker/offline", methods=['POST'])
def api_tracker_offline():
    """รับสัญญาณแจ้งเตือนเมื่อเจ้าหน้าที่กดปิดการส่งพิกัดหรือออกจากระบบ (เปลี่ยนสถานะเป็น OFFLINE ทันที)"""
    data = request.get_json(silent=True) or {}
    unit_id = str(data.get("unit_id") or "").strip()
    if unit_id and unit_id in active_trackers:
        active_trackers[unit_id]["status"] = "⚫ OFFLINE (พักเวร/ปิดระบบ)"
        active_trackers[unit_id]["speed"] = 0
        active_trackers[unit_id]["last_seen_epoch"] = time.time() - 3600 # mark as offline immediately
    return jsonify({"status": "success", "message": "Unit marked as OFFLINE"}), 200


def get_computed_active_units():
    """คำนวณสถานะ ACTIVE/STANDBY/OFFLINE ของหน่วยกำลังพลทั้งหมดตามเวลาจริง"""
    now = time.time()
    result = []
    for uid, unit in list(active_trackers.items()):
        u = dict(unit)
        last_epoch = u.get("last_seen_epoch", now)
        elapsed_sec = now - last_epoch
        speed = float(u.get("speed", 0))
        status_text = (u.get("status") or "").upper()

        if "OFFLINE" in status_text or elapsed_sec > 180: # เกิน 3 นาที
            u["calculated_state"] = "OFFLINE"
            u["state_label"] = "OFFLINE"
            u["state_color"] = "gray"
        elif speed >= 2 or "EN ROUTE" in status_text or "เดินทาง" in status_text or "ระงับเหตุ" in status_text:
            u["calculated_state"] = "ACTIVE"
            u["state_label"] = "ACTIVE"
            u["state_color"] = "emerald"
        else:
            u["calculated_state"] = "STANDBY"
            u["state_label"] = "STANDBY"
            u["state_color"] = "sky"
            
        u["elapsed_seconds"] = int(elapsed_sec)
        result.append(u)
    return result


@app.route("/api/tracker/units", methods=['GET'])
def api_tracker_units():
    """ส่งรายการพิกัดสดของทุกหน่วยให้ Dashboard และ Geoportal RTSD Sync โดยคำนวณสถานะ ACTIVE/STANDBY/OFFLINE ตามเวลาจริง"""
    return jsonify(get_computed_active_units()), 200


@app.route("/api/tracker/tracks", methods=['GET'])
def api_tracker_tracks():
    """ส่งข้อมูลประวัติเส้นทางเดินลาดตระเวน (Patrol Tracks) ของทุกหน่วยในรูปแบบ GeoJSON LineString"""
    target_unit = request.args.get("unit_id")
    features = []
    
    for uid, pts in unit_tracks.items():
        if target_unit and uid != target_unit:
            continue
        if len(pts) < 1:
            continue
        
        info = active_trackers.get(uid, {})
        unit_name = info.get("unit_name", f"ชุดปฏิบัติการ {uid}")
        commander = info.get("commander", "หัวหน้าชุด")
        status = info.get("status", "🟢 ปฏิบัติการ")
        
        line_coords = [[p["lon"], p["lat"]] for p in pts]
        dist_km = calculate_track_distance_km(pts)
        speeds = [p.get("speed", 0) for p in pts if p.get("speed", 0) > 0]
        avg_speed = round(sum(speeds) / len(speeds), 1) if speeds else 0.0
        
        feat = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": line_coords
            },
            "properties": {
                "unit_id": uid,
                "unit_name": unit_name,
                "commander": commander,
                "status": status,
                "point_count": len(pts),
                "total_distance_km": dist_km,
                "avg_speed": avg_speed,
                "start_time": pts[0].get("time", "-"),
                "last_update": pts[-1].get("time", "-"),
                "points": pts[-50:] # 50 จุดล่าสุดสำหรับ tooltip
            }
        }
        features.append(feat)
        
    return jsonify({
        "type": "FeatureCollection",
        "features": features
    }), 200


@app.route("/api/tracker/tracks/clear", methods=['POST'])
def api_tracker_tracks_clear():
    """ล้างประวัติเส้นทางเดิน (เฉพาะ Admin)"""
    target_unit = request.args.get("unit_id")
    if target_unit:
        unit_tracks.pop(target_unit, None)
    else:
        unit_tracks.clear()
    save_unit_tracks()
    return jsonify({"status": "success", "message": "Cleared track logs"}), 200


@app.route("/api/tracker/export/kml", methods=['GET'])
def api_tracker_export_kml():
    """ดาวน์โหลดเส้นทางลาดตระเวนเป็นไฟล์ KML สำหรับ Google Earth / ArcGIS"""
    target_unit = request.args.get("unit_id", "ALL")
    kml = ['<?xml version="1.0" encoding="UTF-8"?>']
    kml.append('<kml xmlns="http://www.opengis.net/kml/2.2">')
    kml.append('<Document>')
    kml.append('<name>RTSD Tactical Patrol Routes</name>')
    kml.append('<description>เส้นทางเดินลาดตระเวนชุดปฏิบัติการ กรมแผนที่ทหาร</description>')
    kml.append('<Style id="patrolLine"><LineStyle><color>ff00ff00</color><width>4</width></LineStyle></Style>')
    
    for uid, pts in unit_tracks.items():
        if target_unit != "ALL" and uid != target_unit:
            continue
        if len(pts) < 2:
            continue
        info = active_trackers.get(uid, {})
        u_name = info.get("unit_name", uid)
        dist = calculate_track_distance_km(pts)
        
        kml.append('<Placemark>')
        kml.append(f'<name>{u_name} ({dist} กม.)</name>')
        kml.append(f'<description>หน่วย: {uid}&#10;ระยะทางรวม: {dist} กม.&#10;จุดตรวจ: {len(pts)} จุด</description>')
        kml.append('<styleUrl>#patrolLine</styleUrl>')
        kml.append('<LineString><extrude>1</extrude><tessellate>1</tessellate><coordinates>')
        coords_str = " ".join([f"{p['lon']},{p['lat']},0" for p in pts])
        kml.append(coords_str)
        kml.append('</coordinates></LineString>')
        kml.append('</Placemark>')
        
    kml.append('</Document></kml>')
    response = make_response("\n".join(kml))
    response.headers["Content-Type"] = "application/vnd.google-earth.kml+xml; charset=utf-8"
    response.headers["Content-Disposition"] = f"attachment; filename=rtsd_patrol_tracks_{int(time.time())}.kml"
    return response


@app.route("/api/tracker/export/gpx", methods=['GET'])
def api_tracker_export_gpx():
    """ดาวน์โหลดเส้นทางลาดตระเวนเป็นไฟล์ GPX สำหรับอุปกรณ์ GPS Garmin / GIS"""
    target_unit = request.args.get("unit_id", "ALL")
    gpx = ['<?xml version="1.0" encoding="UTF-8"?>']
    gpx.append('<gpx version="1.1" creator="RTSD Tactical Tracker" xmlns="http://www.topografix.com/GPX/1/1">')
    
    for uid, pts in unit_tracks.items():
        if target_unit != "ALL" and uid != target_unit:
            continue
        if len(pts) < 2:
            continue
        info = active_trackers.get(uid, {})
        u_name = info.get("unit_name", uid)
        dist = calculate_track_distance_km(pts)
        
        gpx.append(f'<trk><name>{u_name}</name><desc>ระยะทาง: {dist} km</desc><trkseg>')
        for p in pts:
            gpx.append(f'<trkpt lat="{p["lat"]}" lon="{p["lon"]}"><time>{p["time"].replace(" ", "T")}Z</time><speed>{p.get("speed", 0)}</speed></trkpt>')
        gpx.append('</trkseg></trk>')
        
    gpx.append('</gpx>')
    response = make_response("\n".join(gpx))
    response.headers["Content-Type"] = "application/gpx+xml; charset=utf-8"
    response.headers["Content-Disposition"] = f"attachment; filename=rtsd_patrol_tracks_{int(time.time())}.gpx"
    return response



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


@app.route("/api/system/broadcast-app-update", methods=['POST'])
def api_broadcast_app_update():
    """
    ส่งข้อความ Broadcast ทาง LINE แจ้งเตือนเพื่อน/ผู้ติดตามทุกคนว่ามีการอัปเดตแอปพลิเคชันเวอร์ชันใหม่
    """
    try:
        data = request.get_json(silent=True) or {}
        version = str(data.get("version") or "v1.3").strip()
        changelog = str(data.get("changelog") or "ปรับปรุงระบบถ่ายภาพหน้างาน และส่งสัญญาณขอกำลังเสริมด่วน (SOS) พร้อมพิกัด GPS สด").strip()
        download_url = str(data.get("download_url") or "https://linebot.goc-rtsd.com/download/app").strip()
        map_url = "https://linebot.goc-rtsd.com/map"
        dashboard_url = "https://linebot.goc-rtsd.com/dashboard"

        msg_text = (
            f"📲 [แจ้งอัปเดตแอปพลิเคชัน RTSD Tactical Tracker]\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🌟 เวอร์ชันใหม่: {version}\n"
            f"🛡️ ระบบติดตามยุทธการและสนับสนุนภาคสนาม กรมแผนที่ทหาร\n\n"
            f"📋 ไฮไลต์การปรับปรุง:\n"
            f"• {changelog}\n"
            f"• รองรับการถ่ายภาพความคืบหน้าหน้างานส่งตรงเข้าศูนย์ TOC\n"
            f"• เพิ่มระบบส่งสัญญาณขอกำลังเสริมด่วน (SOS) พร้อมพิกัดสด\n"
            f"• เพิ่มความแม่นยำและการซูมตำแหน่งเหตุการณ์บนแผนที่\n\n"
            f"📥 ดาวน์โหลดและติดตั้งใหม่ได้ทันทีที่:\n"
            f"👉 {download_url}\n\n"
            f"💡 ข้อแนะนำ: เจ้าหน้าที่สามารถกดดาวน์โหลดไฟล์ APK และกดติดตั้งทับ (Update) เพื่อเริ่มใช้งานต่อได้ทันทีครับ\n"
            f"━━━━━━━━━━━━━━━━━━"
        )

        quick_reply = {
            "items": [
                {
                    "type": "action",
                    "action": {
                        "type": "uri",
                        "label": "📥 ดาวน์โหลด APK",
                        "uri": download_url
                    }
                },
                {
                    "type": "action",
                    "action": {
                        "type": "uri",
                        "label": "🗺️ แผนที่สถานการณ์",
                        "uri": map_url
                    }
                },
                {
                    "type": "action",
                    "action": {
                        "type": "uri",
                        "label": "🖥️ แดชบอร์ดศูนย์ TOC",
                        "uri": dashboard_url
                    }
                }
            ]
        }

        headers = {
            "Authorization": f"Bearer {CHANNEL_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        broadcast_payload = {
            "messages": [
                {
                    "type": "text",
                    "text": msg_text,
                    "quickReply": quick_reply
                }
            ]
        }

        # ส่งผ่าน LINE Official Broadcast API (ไปยังเพื่อนทุกคน)
        resp = requests.post("https://api.line.me/v2/bot/message/broadcast", headers=headers, json=broadcast_payload, timeout=10)
        is_broadcast_ok = resp.status_code == 200

        # ส่ง Push Message สำรองไปยังผู้ใช้ที่ลงทะเบียนในระบบ
        pushed_count = 0
        for lid in list(registered_users.keys()):
            if str(lid).startswith("U"):
                try:
                    push_payload = {
                        "to": str(lid),
                        "messages": [
                            {
                                "type": "text",
                                "text": msg_text,
                                "quickReply": quick_reply
                            }
                        ]
                    }
                    p_resp = requests.post("https://api.line.me/v2/bot/message/push", headers=headers, json=push_payload, timeout=5)
                    if p_resp.status_code == 200:
                        pushed_count += 1
                except Exception as push_err:
                    print(f"[Broadcast fallback push] Error to {lid}: {push_err}")

        return jsonify({
            "status": "success",
            "message": "ส่งข้อความแจ้งเตือนอัปเดตแอปพลิเคชันทาง LINE เรียบร้อยแล้ว",
            "broadcast_status": resp.status_code,
            "broadcast_ok": is_broadcast_ok,
            "pushed_count": pushed_count,
            "version": version
        }), 200
    except Exception as e:
        print(f"[api_broadcast_app_update] Error: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/incidents", methods=['GET'])
def api_incidents():
    """API ดึงข้อมูลเหตุการณ์ทั้งหมดจาก Google Sheets สำหรับ Dashboard (กรองรายการที่ถูกลบออก)"""
    try:
        res = requests.get(GOOGLE_SHEET_URL, timeout=20)
        rows = res.json()
        if isinstance(rows, list) and len(rows) > 1 and DELETED_INCIDENTS:
            header = rows[0]
            filtered = [header]
            for r in rows[1:]:
                t = str(r[0]).strip() if len(r) > 0 else ""
                ttl = str(r[1]).strip() if len(r) > 1 else ""
                k = f"{t}_{ttl}"
                if k in DELETED_INCIDENTS or ttl in DELETED_INCIDENTS or t in DELETED_INCIDENTS:
                    continue
                id_m = re.search(r'\[(RTSD-\d+)\]', ttl)
                if id_m and id_m.group(1) in DELETED_INCIDENTS:
                    continue
                filtered.append(r)
            return jsonify(filtered), 200
        return jsonify(rows), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/incident/create", methods=['POST'])
def api_create_incident():
    """API สำหรับให้ศูนย์ควบคุม TOC หรือระบบเว็บเปิดใบแจ้งเหตุการณ์ใหม่ลง Google Sheets และแจ้งเตือนกำลังพล"""
    data = request.get_json(silent=True) or {}
    report_id = f"RTSD-{random.randint(1000, 9999)}"
    title = f"[{report_id}] {data.get('title', 'แจ้งเหตุฉุกเฉิน')}"
    address = data.get("address", "ศูนย์ควบคุม TOC (พิกัดแผนที่)")
    lat = float(data.get("latitude", 0))
    lon = float(data.get("longitude", 0))
    reporter = data.get("reporter", "ศูนย์ควบคุม TOC")
    urgency = data.get("urgency", "🔴 ด่วนที่สุด")
    incident_type = data.get("incident_type", "อื่นๆ")
    file_base64 = data.get("file_base64")
    file_name = data.get("file_name")
    mime_type = data.get("mime_type", "image/jpeg")

    # บันทึกลง Google Sheets
    success = save_to_google_sheet(
        lat=lat,
        lon=lon,
        title=title,
        address=address,
        reporter=reporter,
        urgency=urgency,
        incident_type=incident_type,
        file_base64=file_base64,
        file_name=file_name,
        mime_type=mime_type
    )

    # ส่ง Push Notification ไปยัง Admin/Line
    if success:
        push_text = (
            f"🚨 มีการเปิดใบแจ้งเหตุใหม่จากศูนย์ TOC!\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🆔 รหัสเหตุ: #{report_id}\n"
            f"📌 เรื่อง: {title}\n"
            f"⚠️ ความเร่งด่วน: {urgency}\n"
            f"📍 สถานที่: {address}\n"
            f"👤 ผู้เปิดเรื่อง: {reporter}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🧭 แผนที่: https://www.google.com/maps/dir/?api=1&destination={lat},{lon}"
        )
        for admin_phone in ADMIN_PHONES:
            for phone, u in phone_to_user.items():
                if phone == admin_phone and u.get("user_id"):
                    push_line_message(u["user_id"], push_text)

    return jsonify({
        "status": "success" if success else "error",
        "report_id": report_id,
        "title": title
    }), (200 if success else 500)


@app.route("/api/incident/update-status", methods=['POST'])
def api_update_incident_status():
    """API สำหรับให้ Dashboard หรือแอปมือถือสั่งเปลี่ยนสถานะของเหตุการณ์เดิม และบันทึกชุดปฏิบัติการที่เข้าพื้นที่"""
    global DISPATCHED_INCIDENTS, active_trackers, FIELD_REPORTS, INCIDENT_REPORTERS
    data = request.get_json(silent=True) or {}
    report_id = str(data.get("report_id") or data.get("incident_id") or "").strip()
    title = str(data.get("title") or "").strip()
    timestamp = str(data.get("timestamp") or "").strip()
    new_status = str(data.get("status") or "🟢 แก้ไขแล้วเสร็จ").strip()
    unit_id = str(data.get("unit_id") or "").strip()
    commander = str(data.get("commander") or "").strip()
    note = str(data.get("note") or "").strip()
    image_base64 = data.get("image_base64") or data.get("imageBase64") or ""
    photo_url = data.get("photo_url") or "-"

    if image_base64:
        if not str(image_base64).startswith("data:"):
            photo_url = f"data:image/jpeg;base64,{image_base64}"
        else:
            photo_url = str(image_base64)

    # หากไม่มี title หรือ timestamp ให้ค้นหาจากแคชเหตุการณ์โดยใช้ report_id
    if (not title or not timestamp) and report_id:
        try:
            cached_rows = get_user_incidents(limit=200)
            for r in cached_rows:
                if len(r) > 1 and (report_id in str(r[1]) or (len(r) > 0 and report_id == str(r[0]))):
                    if not timestamp and len(r) > 0:
                        timestamp = str(r[0])
                    if not title and len(r) > 1:
                        title = str(r[1])
                    break
        except Exception as e:
            print(f"Error finding incident by report_id: {e}")

    # ปรับแต่งสถานะให้ระบุชุดปฏิบัติการชัดเจน หากมีการรับภารกิจหรือกำลังเข้าพื้นที่
    unit_label = f"{unit_id} / {commander}".strip(" /") if (unit_id or commander) else ""
    is_completed = any(kw in new_status for kw in ["เสร็จ", "เรียบร้อย", "แล้วเสร็จ"]) or "เสร็จสิ้น" in note

    if unit_label:
        if is_completed:
            new_status = f"🟢 แก้ไขแล้วเสร็จ (ชุด: {unit_label})"
        elif any(kw in new_status for kw in ["เข้าพื้นที่", "ดำเนินการ", "ช่วยเหลือ", "ปฏิบัติ"]) or "รับภารกิจ" in note:
            if "ชุด:" not in new_status:
                new_status = f"{new_status} (ชุด: {unit_label})"

    # อัปเดต DISPATCHED_INCIDENTS ในหน่วยความจำเพื่อให้ Dashboard แสดงผลทันที (เวลาประเทศไทย UTC+7)
    now_th = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 7 * 3600))
    assigned_info = {
        "incident_id": report_id,
        "title": title,
        "timestamp": timestamp,
        "status": new_status,
        "dispatched_at": now_th,
        "directive_note": note or ("ภารกิจเสร็จสิ้นเรียบร้อย" if is_completed else "กดยืนยันรับภารกิจผ่านแอปพลิเคชัน RTSD Mobile"),
        "completion_note": note if is_completed else "",
        "photo_url": photo_url,
        "dispatch_all": False,
        "assigned_units": [{
            "unit_id": unit_id,
            "commander": commander,
            "status": new_status,
            "timestamp": now_th
        }] if unit_label else [],
        "dispatched_units": [unit_label] if unit_label else [],
        "accepted_by_unit": {
            "unit_id": unit_id,
            "commander": commander,
            "status": new_status,
            "timestamp": now_th
        } if unit_label else None
    }

    if report_id:
        DISPATCHED_INCIDENTS[report_id] = assigned_info
    if timestamp and title:
        DISPATCHED_INCIDENTS[f"{timestamp}_{title}"] = assigned_info
    if title:
        DISPATCHED_INCIDENTS[title] = assigned_info

    # อัปเดตสถานะของ Tracker หากเป็นชุดปฏิบัติการในระบบ
    if unit_id and unit_id in active_trackers:
        if is_completed:
            active_trackers[unit_id]["status"] = "🟢 พร้อมปฏิบัติการ (STANDBY)"
        elif any(k in new_status for k in ["ขอสนับสนุน", "ขอกำลัง", "SOS"]):
            active_trackers[unit_id]["status"] = f"⚠️ ขอสนับสนุน: {note[:25]}"
        else:
            active_trackers[unit_id]["status"] = f"🚗 กำลังไประงับเหตุ: {title[:20]}"
        active_trackers[unit_id]["last_seen"] = time.time()

    # ถ้ามีภาพถ่าย, ปิดภารกิจ, หรือเป็นการขอสนับสนุนด่วน (SOS) ให้บันทึกลง FIELD_REPORTS เพื่อโชว์ใน Dashboard ทันที
    is_support_req = any(k in new_status for k in ["ขอสนับสนุน", "ขอกำลัง", "SOS"]) or any(k in note for k in ["ขอสนับสนุน", "ขอกำลัง", "SOS"])
    if is_completed or photo_url != "-" or is_support_req:
        rep_lat = data.get("latitude")
        rep_lon = data.get("longitude")
        if (rep_lat is None or rep_lon is None or rep_lat == 0 or rep_lon == 0) and unit_id in active_trackers:
            rep_lat = active_trackers[unit_id].get("latitude", 0.0)
            rep_lon = active_trackers[unit_id].get("longitude", 0.0)

        FIELD_REPORTS.append({
            "id": f"REP-{int(time.time())}",
            "incident_id": report_id or title,
            "unit_id": unit_id or "RTSD-UNIT",
            "commander": commander or "เจ้าหน้าที่หน้างาน",
            "phone": "",
            "report_type": "support_request" if is_support_req else ("completion_report" if is_completed else "photo_report"),
            "message": note or ("ภารกิจเสร็จสิ้นเรียบร้อย ปลอดภัยทุกฝ่าย" if is_completed else ("ขอรับการสนับสนุนด่วน" if is_support_req else "ภาพถ่ายรายงานผลหน้างาน")),
            "image_url": photo_url,
            "latitude": float(rep_lat) if rep_lat is not None else 0.0,
            "longitude": float(rep_lon) if rep_lon is not None else 0.0,
            "timestamp": now_th
        })
        if len(FIELD_REPORTS) > 100:
            FIELD_REPORTS = FIELD_REPORTS[-100:]

    # ดึง LINE User ID ของประชาชนผู้แจ้งเหตุ (ถ้ามี)
    reporter_line_id = INCIDENT_REPORTERS.get(report_id) or INCIDENT_REPORTERS.get(title)

    # 1. กรณี: ชุดปฏิบัติการกดรับภารกิจ (En route)
    if unit_label and ("เข้าพื้นที่" in new_status or "รับภารกิจ" in note) and not is_completed:
        try:
            push_admin_msg = (
                f"🚗 ชุดปฏิบัติการยืนยันรับภารกิจแล้ว!\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🆔 เหตุการณ์: #{report_id or title}\n"
                f"🛡️ ชุดปฏิบัติการ: {unit_label}\n"
                f"📌 สถานะ: {new_status}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"ศูนย์ TOC สามารถติดตามพิกัดการเคลื่อนที่ได้สดบน Dashboard\n"
                f"👉 https://linebot.goc-rtsd.com/map"
            )
            for admin_phone in ADMIN_PHONES:
                for phone, u in phone_to_user.items():
                    if phone == admin_phone and u.get("user_id"):
                        push_line_message(u["user_id"], push_admin_msg)
            
            if reporter_line_id:
                push_citizen_msg = (
                    f"🚒 แจ้งความคืบหน้าการช่วยเหลือ!\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🆔 รหัสเหตุ: #{report_id or title}\n"
                    f"🛡️ ชุดปฏิบัติการ: {unit_label}\n"
                    f"📍 สถานะ: กำลังมุ่งหน้าเข้าสู่จุดเกิดเหตุของท่าน\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🗺️ ตรวจสอบแผนที่สถานการณ์สด:\n"
                    f"👉 https://linebot.goc-rtsd.com/map"
                )
                push_line_message(reporter_line_id, push_citizen_msg)
        except Exception as e:
            print(f"Error sending mission accepted line push: {e}")

    # 2. กรณี: ภารกิจเสร็จสิ้น (Mission Completed)
    elif is_completed:
        try:
            push_admin_msg = (
                f"✅ รายงานปิดภารกิจสำเร็จ!\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🆔 เหตุการณ์: #{report_id or title}\n"
                f"🛡️ ชุดปฏิบัติการ: {unit_label}\n"
                f"📝 ผลการปฏิบัติ: {note or 'ระงับเหตุและช่วยเหลือประชาชนเรียบร้อยแล้ว ปลอดภัยทุกฝ่าย'}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🌐 ตรวจสอบภาพและสรุปงาน:\n"
                f"👉 https://linebot.goc-rtsd.com/map"
            )
            for admin_phone in ADMIN_PHONES:
                for phone, u in phone_to_user.items():
                    if phone == admin_phone and u.get("user_id"):
                        push_line_message(u["user_id"], push_admin_msg)
            
            if reporter_line_id:
                push_citizen_msg = (
                    f"🎉 ภารกิจระงับเหตุเสร็จสิ้นเรียบร้อย!\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🆔 รหัสเหตุ: #{report_id or title}\n"
                    f"🛡️ ชุดปฏิบัติการ: {unit_label}\n"
                    f"📝 ผลการปฏิบัติ: {note or 'ให้การช่วยเหลือและดูแลความปลอดภัยเรียบร้อยแล้ว'}\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🙏 ศูนย์ปฏิบัติการ RTSD ขอขอบคุณที่ท่านได้ร่วมแจ้งเหตุเตือนภัยครับ"
                )
                push_line_message(reporter_line_id, push_citizen_msg)
        except Exception as e:
            print(f"Error sending mission completed line push: {e}")

    # ส่งอัปเดตลง Google Sheets
    result = update_google_sheet_status(title=title, timestamp=timestamp, new_status=new_status)
    return jsonify({
        "status": "success",
        "sheet_result": result,
        "new_status": new_status,
        "report_id": report_id,
        "title": title,
        "dispatched_info": assigned_info
    }), 200


@app.route("/api/incident/delete", methods=['POST'])
def api_delete_incident():
    """API สำหรับให้ Dashboard สั่งลบข้อมูลเหตุการณ์ออกจากระบบอย่างถาวร"""
    global DELETED_INCIDENTS, DISPATCHED_INCIDENTS, INCIDENTS_CACHE
    data = request.get_json(silent=True) or {}
    title = str(data.get("title") or "").strip()
    timestamp = str(data.get("timestamp") or "").strip()
    report_id = str(data.get("report_id") or "").strip()

    if not title and not timestamp and not report_id:
        return jsonify({"status": "error", "message": "กรุณาระบุข้อมูลเหตุการณ์ที่ต้องการลบ"}), 400

    # บันทึกเข้า DELETED_INCIDENTS
    if timestamp and title:
        DELETED_INCIDENTS.add(f"{timestamp}_{title}")
    if title:
        DELETED_INCIDENTS.add(title)
    if timestamp:
        DELETED_INCIDENTS.add(timestamp)
    if report_id:
        DELETED_INCIDENTS.add(report_id)

    id_match = re.search(r'\[(RTSD-\d+)\]', title)
    if id_match:
        DELETED_INCIDENTS.add(id_match.group(1))

    save_deleted_incidents()
    INCIDENTS_CACHE["last_fetched"] = 0

    # ลบออกจาก DISPATCHED_INCIDENTS ด้วย
    keys_to_del = [k for k in DISPATCHED_INCIDENTS if k in DELETED_INCIDENTS or (title and title in k) or (timestamp and timestamp in k)]
    for k in keys_to_del:
        del DISPATCHED_INCIDENTS[k]

    # ส่งคำสั่งไปลบใน Google Sheets
    sheet_res = None
    try:
        payload = {
            "action": "delete_incident",
            "title": title,
            "timestamp": timestamp,
            "report_id": report_id or (id_match.group(1) if id_match else "")
        }
        res = requests.post(GOOGLE_SHEET_URL, json=payload, timeout=15)
        if res.status_code == 200:
            sheet_res = res.json()
    except Exception as e:
        print(f"Error calling Google Sheets delete_incident: {e}")

    return jsonify({
        "status": "success",
        "deleted": True,
        "sheet_result": sheet_res
    }), 200


# =========================================================================
# 📍 TACTICAL PINS & FIELD DISPATCH MANAGEMENT ENGINE
# =========================================================================
TACTICAL_PINS_FILE = os.path.join(os.path.dirname(__file__), "tactical_pins.json")
TACTICAL_PINS = []

PIN_CATEGORY_NAMES = {
    "incident": "🚨 จุดเกิดเหตุ / พื้นที่ประสบภัย",
    "target": "🎯 พิกัดเป้าหมาย / จุดนัดหมาย (RV)",
    "command": "⛺ กองอำนวยการ / ศูนย์ประสานงาน",
    "helipad": "🚁 ลานจอด ฮ. / จุดส่งกำลังบำรุง",
    "hazard": "🚧 สิ่งกีดขวาง / ดินถล่ม / น้ำท่วม",
    "checkpoint": "📍 จุดตรวจ / จุดสังเกตการณ์"
}

def load_tactical_pins():
    global TACTICAL_PINS
    if os.path.exists(TACTICAL_PINS_FILE):
        try:
            with open(TACTICAL_PINS_FILE, "r", encoding="utf-8") as f:
                TACTICAL_PINS = json.load(f)
        except Exception as e:
            print(f"Error loading tactical_pins.json: {e}")
            TACTICAL_PINS = []
    else:
        TACTICAL_PINS = []

def save_tactical_pins():
    try:
        with open(TACTICAL_PINS_FILE, "w", encoding="utf-8") as f:
            json.dump(TACTICAL_PINS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error saving tactical_pins.json: {e}")

load_tactical_pins()

def notify_tactical_pin(pin):
    """ส่ง Push Message ทาง LINE ให้เจ้าหน้าที่ที่ถูกแท็กในหมุดยุทธวิธี"""
    try:
        tagged_units = pin.get("tagged_units", [])
        if not tagged_units:
            return 0
        
        cat_name = PIN_CATEGORY_NAMES.get(pin.get("category"), "📍 จุดยุทธวิธี")
        title = pin.get("title", "ภารกิจยุทธวิธี")
        creator = pin.get("creator", "ศูนย์ยุทธวิธี RTSD")
        notes = pin.get("notes", "-")
        lat = float(pin.get("latitude", 0))
        lon = float(pin.get("longitude", 0))
        nav_url = f"https://www.google.com/maps/dir/?api=1&destination={lat:.6f},{lon:.6f}"

        msg = (
            f"🚨 [คำสั่งยุทธวิธีด่วนจาก RTSD Command]\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📌 จุดหมาย: {title}\n"
            f"🏷️ ประเภท: {cat_name}\n"
            f"👨‍✈️ สั่งการโดย: {creator}\n"
            f"📍 พิกัด GPS: {lat:.6f}, {lon:.6f}\n"
            f"📝 รายละเอียด: {notes}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🧭 กดนำทางทันที:\n{nav_url}"
        )

        target_lids = set()
        is_broadcast = ("ALL" in tagged_units) or ("all" in tagged_units)

        if is_broadcast:
            for p, u in phone_to_user.items():
                lid = u.get("line_user_id")
                if lid and str(lid).startswith("U"):
                    target_lids.add(lid)
        else:
            for tag in tagged_units:
                tag_str = str(tag).strip()
                clean_p = tag_str.replace("-", "").replace(" ", "")
                if clean_p in phone_to_user:
                    lid = phone_to_user[clean_p].get("line_user_id")
                    if lid and str(lid).startswith("U"):
                        target_lids.add(lid)
                for uid, tr in active_trackers.items():
                    if uid == tag_str or tr.get("commander") == tag_str:
                        for p, u in phone_to_user.items():
                            if u.get("name") == tr.get("commander"):
                                lid = u.get("line_user_id")
                                if lid and str(lid).startswith("U"):
                                    target_lids.add(lid)

        sent_count = 0
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            for lid in target_lids:
                try:
                    line_bot_api.push_message(
                        PushMessageRequest(to=lid, messages=[TextMessage(text=msg)])
                    )
                    sent_count += 1
                except Exception as push_err:
                    print(f"Error pushing pin alert to {lid}: {push_err}")
        return sent_count
    except Exception as e:
        print(f"Error in notify_tactical_pin: {e}")
        return 0


@app.route("/api/tactical-pins", methods=['GET', 'POST'])
def api_tactical_pins():
    """ดึงหรือบันทึกหมุดยุทธการ (Tactical Pins) พร้อมส่งแจ้งเตือนกำลังพลที่ถูกแท็ก"""
    global TACTICAL_PINS
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        lat = data.get("latitude")
        lon = data.get("longitude")
        if lat is None or lon is None:
            return jsonify({"status": "error", "message": "พิกัดไม่ถูกต้อง"}), 400

        pin_id = str(data.get("id") or f"PIN-{int(time.time() * 1000) % 1000000:06d}")
        tagged_units = data.get("tagged_units", [])
        if isinstance(tagged_units, str):
            tagged_units = [tagged_units]

        new_pin = {
            "id": pin_id,
            "latitude": float(lat),
            "longitude": float(lon),
            "category": str(data.get("category", "checkpoint")),
            "title": str(data.get("title", "จุดยุทธวิธี")),
            "creator": str(data.get("creator", "ศูนย์ยุทธวิธี RTSD")),
            "notes": str(data.get("notes", "")),
            "tagged_units": tagged_units,
            "created_at": str(data.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        }

        # อัปเดตรายการหมุด (ถ้ามี id เดิมให้แทนที่ ถ้าไม่มีให้เพิ่ม)
        existing_idx = next((i for i, p in enumerate(TACTICAL_PINS) if p.get("id") == pin_id), None)
        if existing_idx is not None:
            TACTICAL_PINS[existing_idx] = new_pin
        else:
            TACTICAL_PINS.append(new_pin)

        save_tactical_pins()

        # ส่งข้อความ Push แจ้งเตือนไปยัง LINE ของกำลังพลที่ถูกแท็ก
        sent_line = notify_tactical_pin(new_pin)

        return jsonify({
            "status": "success",
            "pin": new_pin,
            "line_notified_count": sent_line
        }), 200

    return jsonify(TACTICAL_PINS), 200


@app.route("/api/tactical-pins/my-alerts", methods=['GET'])
def api_tactical_pin_alerts():
    """สำหรับ Mobile App เพื่อตรวจสอบหมุดใหม่ที่ตนเองถูกแท็ก"""
    phone = str(request.args.get("phone", "")).strip().replace("-", "").replace(" ", "")
    unit_id = str(request.args.get("unit_id", "")).strip()

    my_alerts = []
    # ดึงหมุดที่สร้างขึ้นและมีการแท็กตนเอง หรือ Broadcast ALL
    for p in reversed(TACTICAL_PINS[-30:]): # ดู 30 หมุดล่าสุด
        tags = p.get("tagged_units", [])
        if "ALL" in tags or "all" in tags:
            my_alerts.append(p)
        elif phone and phone in tags:
            my_alerts.append(p)
        elif unit_id and unit_id in tags:
            my_alerts.append(p)

    return jsonify(my_alerts), 200


@app.route("/api/tactical-pins/delete", methods=['POST'])
def api_delete_tactical_pin():
    """ลบหมุดยุทธการ"""
    global TACTICAL_PINS
    data = request.get_json(silent=True) or {}
    pin_id = str(data.get("id", "")).strip()
    if not pin_id:
        return jsonify({"status": "error", "message": "กรุณาระบุ ID ของหมุด"}), 400

    TACTICAL_PINS = [p for p in TACTICAL_PINS if p.get("id") != pin_id]
    save_tactical_pins()
    return jsonify({"status": "success", "id": pin_id}), 200


@app.route("/api/tactical-pins/clear-all", methods=['POST'])
def api_clear_all_tactical_pins():
    """ลบหมุดยุทธการทั้งหมด"""
    global TACTICAL_PINS
    TACTICAL_PINS = []
    save_tactical_pins()
    return jsonify({"status": "success", "message": "ลบหมุดยุทธการทั้งหมดเรียบร้อยแล้ว"}), 200


# =========================================================================
# 📹 5.2.5 ระบบกล้องวงจรปิดโทรมาตร กรมทรัพยากรน้ำ (DWR CCTV Telemetry)
# =========================================================================
@app.route("/api/cctv/dwr", methods=['GET'])
def api_get_dwr_cctv_stations():
    """ดึงข้อมูลกล้องวงจรปิดโทรมาตร กรมทรัพยากรน้ำ 118 สถานี"""
    import json
    json_path = os.path.join(os.path.dirname(__file__), 'dwr_cctv_stations.json')
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                return jsonify(json.load(f)), 200
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify([]), 200


@app.route("/api/cctv/rtsd", methods=['GET'])
def api_get_rtsd_hls_cctv():
    """ดึงข้อมูลกล้องวงจรปิด RTSD GeoPortal HLS 314 สถานี (item: b8b872b1146e410b82791dcb08ad66cb)"""
    import json
    json_path = os.path.join(os.path.dirname(__file__), 'rtsd_hls_cctv.json')
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                return jsonify(json.load(f)), 200
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify([]), 200


@app.route("/api/cctv/all", methods=['GET'])
def api_get_all_cctv_stations():
    """ดึงข้อมูลกล้องวงจรปิดทั้งหมด 432 สถานี (DWR 118 + RTSD GeoPortal HLS 314)"""
    import json
    json_path = os.path.join(os.path.dirname(__file__), 'all_cctv_stations.json')
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                return jsonify(json.load(f)), 200
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify([]), 200


@app.route("/api/cctv/proxy/<station_code>", methods=['GET'])
def api_cctv_proxy_mjpeg(station_code):
    """Proxy live MJPEG stream from DWR Telemetry with cross-origin headers"""
    import urllib.request
    import base64
    from flask import Response
    clean_code = re.sub(r'[^A-Za-z0-9]', '', station_code)
    target_url = f"https://telemetry.dwr.go.th/cctv/mjpeg/{clean_code}"
    auth_header = 'Basic ' + base64.b64encode(b'live:Live2025!').decode('ascii')

    def generate():
        req = urllib.request.Request(target_url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Authorization': auth_header
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as stream:
                while True:
                    chunk = stream.read(4096)
                    if not chunk:
                        break
                    yield chunk
        except Exception:
            return

    resp = Response(generate(), mimetype='multipart/x-mixed-replace; boundary=frame')
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


@app.route("/api/cctv/snapshot/<station_code>", methods=['GET'])
def api_cctv_snapshot_frame(station_code):
    """Grab a crisp single JPEG frame from DWR stream for instant display & fallback"""
    import urllib.request
    import base64
    from flask import Response
    clean_code = re.sub(r'[^A-Za-z0-9]', '', station_code)
    target_url = f"https://telemetry.dwr.go.th/cctv/mjpeg/{clean_code}"
    auth_header = 'Basic ' + base64.b64encode(b'live:Live2025!').decode('ascii')
    try:
        req = urllib.request.Request(target_url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Authorization': auth_header
        })
        with urllib.request.urlopen(req, timeout=8) as stream:
            buf = bytearray()
            max_read = 1024 * 1024  # 1MB max
            while len(buf) < max_read:
                chunk = stream.read(8192)
                if not chunk:
                    break
                buf.extend(chunk)
                start = buf.find(b'\xff\xd8')
                if start != -1:
                    end = buf.find(b'\xff\xd9', start + 2)
                    if end != -1:
                        jpeg_bytes = bytes(buf[start:end + 2])
                        resp = Response(jpeg_bytes, mimetype='image/jpeg')
                        resp.headers["Access-Control-Allow-Origin"] = "*"
                        resp.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
                        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                        resp.headers["Pragma"] = "no-cache"
                        return resp
    except Exception as e:
        logger.warning(f"CCTV snapshot error for {clean_code}: {e}")

    return jsonify({"error": "snapshot_unavailable"}), 502





# =========================================================================
# 🚨 5.3 ศูนย์สั่งการกำลังพล & แจ้งเตือนไประงับเหตุ (Incident Dispatch Center)
# =========================================================================
DISPATCHED_INCIDENTS = {}  # { incident_id: { "dispatched_units": [...], "timestamp": ..., "directive": ... } }

@app.route("/api/incident/dispatch", methods=['POST'])
def api_incident_dispatch():
    """
    รับคำสั่งมอบหมายกำลังพลจากหน้า Dashboard เพื่อส่งเจ้าหน้าที่ไประงับเหตุ
    - รองรับการเลือกเป็นรายบุคคล / รายชุดปฏิบัติการ
    - รองรับการเลือกทั้งหมด (Dispatch All Units / Broadcast)
    - ส่ง Push Notification ทาง LINE ไปยังเจ้าหน้าที่เป้าหมายทันที
    """
    global DISPATCHED_INCIDENTS, TACTICAL_PINS
    data = request.get_json(silent=True) or {}

    incident_id = str(data.get("incident_id") or data.get("report_id") or "").strip()
    title = str(data.get("title") or data.get("incident_title") or "เหตุการณ์ฉุกเฉิน").strip()
    incident_type = str(data.get("incident_type") or "ทั่วไป").strip()
    urgency = str(data.get("urgency") or "🟡 ปานกลาง").strip()
    address = str(data.get("address") or data.get("location") or "ไม่ระบุสถานที่").strip()
    lat = data.get("latitude") if data.get("latitude") is not None else data.get("lat")
    lon = data.get("longitude") if data.get("longitude") is not None else data.get("lon")
    raw_units = data.get("target_units") or data.get("units") or []
    if isinstance(raw_units, str):
        raw_units = [raw_units]

    target_phones = set()
    target_unit_ids = set()
    target_names = set()

    for item in raw_units:
        if isinstance(item, dict):
            p = normalize_phone_number(str(item.get("phone", "")).strip())
            if p:
                target_phones.add(p)
            uid = str(item.get("unit_id", "")).strip()
            if uid:
                target_unit_ids.add(uid)
            cmd = str(item.get("commander", "")).strip()
            if cmd:
                target_names.add(cmd)
        else:
            s = str(item).strip()
            if not s:
                continue
            p = normalize_phone_number(s)
            if p:
                target_phones.add(p)
            target_unit_ids.add(s)
            target_names.add(s)

    dispatch_all = bool(data.get("dispatch_all", False) or "ALL" in target_unit_ids or "all" in target_unit_ids)
    directive_note = str(data.get("directive_note") or data.get("directive") or "ให้ชุดปฏิบัติการเร่งรัดเข้าตรวจสอบและช่วยเหลือประชาชน ณ จุดเกิดเหตุโดยด่วน").strip()
    commander_name = str(data.get("commander_name") or "ศูนย์บัญชาการ TOC กรมแผนที่ทหาร").strip()

    if not incident_id:
        match = re.search(r'\[(RTSD-\d+)\]', title)
        incident_id = match.group(1) if match else f"INC-{int(time.time()) % 100000}"

    # 1. สร้างหมุดยุทธการ TACTICAL_PINS เพื่อให้ RTSD Mobile App เด้งแจ้งเตือน Modal บนจอมือถือทันที!
    pin_id = f"DISP-{int(time.time() * 1000) % 1000000:06d}"
    try:
        p_lat = float(lat) if (lat is not None and float(lat) != 0) else 19.907
        p_lon = float(lon) if (lon is not None and float(lon) != 0) else 99.832
    except:
        p_lat, p_lon = 19.907, 99.832

    pin_tagged = ["ALL"] if dispatch_all else list(target_phones | target_unit_ids | target_names)
    new_pin = {
        "id": pin_id,
        "latitude": p_lat,
        "longitude": p_lon,
        "category": "emergency",
        "title": f"🚨 [ภารกิจสั่งการด่วน] {title}",
        "creator": commander_name,
        "notes": f"⚡ ข้อสั่งการ: {directive_note}\n📍 จุดเกิดเหตุ: {address}",
        "tagged_units": pin_tagged,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }
    TACTICAL_PINS.append(new_pin)
    save_tactical_pins()

    # 2. ค้นหาผู้รับข้อความแจ้งเตือนทาง LINE (LINE Messaging API)
    fetch_registered_users()

    target_lids = set()
    target_unit_names = []

    if dispatch_all:
        target_unit_names.append("กำลังพลทุกนาย (All Units)")
        for lid, u in registered_users.items():
            if str(lid).startswith("U"):
                target_lids.add(str(lid))
        for p, u in phone_to_user.items():
            lid = u.get("line_user_id")
            if lid and str(lid).startswith("U"):
                target_lids.add(str(lid))
    else:
        # A. จับคู่จากหมายเลขโทรศัพท์
        for p in target_phones:
            if p in phone_to_user:
                u = phone_to_user[p]
                name = u.get("name", p)
                if name not in target_unit_names:
                    target_unit_names.append(name)
                lid = u.get("line_user_id")
                if lid and str(lid).startswith("U"):
                    target_lids.add(str(lid))

        # B. จับคู่จาก unit_id ใน active_trackers
        for uid in target_unit_ids:
            if uid in active_trackers:
                tr = active_trackers[uid]
                cmd_name = tr.get("commander") or tr.get("unit_name") or uid
                if cmd_name not in target_unit_names:
                    target_unit_names.append(cmd_name)
                for p, u in phone_to_user.items():
                    if u.get("name") == tr.get("commander") or (p and p[-4:] in uid):
                        lid = u.get("line_user_id")
                        if lid and str(lid).startswith("U"):
                            target_lids.add(str(lid))

        # C. จับคู่จากชื่อเจ้าหน้าที่ / ชุดปฏิบัติการ
        for name in target_names:
            if name not in target_unit_names:
                target_unit_names.append(name)
            for p, u in phone_to_user.items():
                if u.get("name") == name or name in u.get("name", ""):
                    lid = u.get("line_user_id")
                    if lid and str(lid).startswith("U"):
                        target_lids.add(str(lid))

        # D. Fallback: หากยังไม่พบ LINE ID จากชุดที่เลือก ให้ส่งเข้า LINE ของผู้ดูแลระบบ/ผู้ใช้ที่มี LINE ID เสมอ
        if not target_lids:
            for p, u in phone_to_user.items():
                lid = u.get("line_user_id")
                if lid and str(lid).startswith("U"):
                    target_lids.add(str(lid))

    nav_link = None
    try:
        f_lat = float(lat)
        f_lon = float(lon)
        if f_lat != 0 and f_lon != 0:
            nav_link = f"https://www.google.com/maps/dir/?api=1&destination={f_lat},{f_lon}"
    except:
        nav_link = None

    msg_lines = [
        "🚨 [คำสั่งด่วนจากศูนย์บัญชาการ TOC กรมแผนที่ทหาร]",
        "━━━━━━━━━━━━━━━━━━",
        "📢 มอบหมายกำลังพลไประงับเหตุฉุกเฉิน!",
        f"📍 รหัส/เหตุการณ์: {title}",
        f"⚠️ ระดับความเร่งด่วน: {urgency}",
        f"🏢 สถานที่เกิดเหตุ: {address}",
        f"📝 ข้อสั่งการ: {directive_note}",
        f"👤 ผู้สั่งการ: {commander_name}",
        "━━━━━━━━━━━━━━━━━━"
    ]
    if nav_link:
        msg_lines.append(f"🧭 นำทาง Google Maps ทันที:\n👉 {nav_link}\n")
    msg_lines.append("📱 เจ้าหน้าที่กรุณาเปิดแอป RTSD Tactical Tracker เพื่อรับภารกิจและเริ่มแทร็กพิกัดช่วยเหลือ")
    push_msg_text = "\n".join(msg_lines)

    quick_items = [
        QuickReplyItem(action=URIAction(label="🌐 แดชบอร์ดสถานการณ์", uri="https://linebot.goc-rtsd.com/dashboard")),
        QuickReplyItem(action=MessageAction(label="🚨 รับทราบคำสั่ง", text=f"รับทราบคำสั่ง {incident_id}"))
    ]
    if nav_link:
        quick_items.insert(0, QuickReplyItem(action=URIAction(label="🧭 นำทาง GPS", uri=nav_link)))

    quick_reply = QuickReply(items=quick_items)

    sent_count = 0
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        for target_lid in target_lids:
            try:
                line_bot_api.push_message(
                    PushMessageRequest(
                        to=target_lid,
                        messages=[TextMessage(text=push_msg_text, quick_reply=quick_reply)]
                    )
                )
                sent_count += 1
            except Exception as push_err:
                print(f"[Dispatch] Error pushing to {target_lid}: {push_err}")

    now_th = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    assigned_info = {
        "incident_id": incident_id,
        "title": title,
        "dispatched_units": target_unit_names,
        "dispatch_all": dispatch_all,
        "directive_note": directive_note,
        "commander_name": commander_name,
        "dispatched_at": now_th,
        "sent_count": sent_count,
        "assigned_units": [{"unit_id": name, "commander": name} for name in target_unit_names]
    }
    if incident_id:
        DISPATCHED_INCIDENTS[incident_id] = assigned_info
    ts = str(data.get("timestamp") or "").strip()
    if ts and title:
        DISPATCHED_INCIDENTS[f"{ts}_{title}"] = assigned_info
    if title:
        DISPATCHED_INCIDENTS[title] = assigned_info

    try:
        sheet_payload = {
            "action": "update_status",
            "title": title,
            "timestamp": str(data.get("timestamp", "")),
            "status": f"🟡 กำลังช่วยเหลือ (มอบหมาย: {', '.join(target_unit_names[:2])})"
        }
        requests.post(GOOGLE_SHEET_URL, json=sheet_payload, timeout=4)
    except Exception as e:
        print(f"[Dispatch] Error updating Google Sheets: {e}")

    return jsonify({
        "status": "success",
        "message": f"สั่งการมอบหมายกำลังพลสำเร็จ ส่งแจ้งเตือนผ่าน LINE เรียบร้อย ({sent_count} นาย)",
        "dispatched_info": assigned_info,
        "sent_count": sent_count
    }), 200


@app.route("/api/incident/dispatched-list", methods=['GET'])
def api_incident_dispatched_list():
    """ส่งคืนรายการเหตุการณ์ที่ถูกสั่งการมอบหมายกำลังพลแล้วทั้งหมด"""
    return jsonify({
        "status": "success",
        "dispatched": DISPATCHED_INCIDENTS,
        "items": list(DISPATCHED_INCIDENTS.values())
    }), 200


# =========================================================================
# 📡 5.4 รายงานสดจากหน้างาน: คำขอสนับสนุน & ภาพถ่ายภาคสนาม (Field Reports)
# =========================================================================
FIELD_REPORTS = []  # เก็บประวัติรายงานจากชุดปฏิบัติการหน้างาน (Support Requests & Photo Reports)

@app.route("/api/incident/field-report", methods=['POST'])
def api_incident_field_report():
    """
    รับรายงานความคืบหน้า / คำขอสนับสนุน / รูปถ่ายหลักฐาน จากชุดปฏิบัติการหน้างาน
    - รองรับทั้งจาก RTSD Mobile App, เว็บไซต์ Tracker และแชต LINE
    - บันทึกลง FIELD_REPORTS เพื่อส่งให้ Dashboard แสดงแจ้งเตือนทันที
    - อัปเดตสถานะของชุดปฏิบัติการใน active_trackers
    - นำภาพเข้าเก็บในระบบและส่งต่อไป Google Sheets/Drive
    """
    global FIELD_REPORTS, active_trackers
    try:
        import json as _py_json
        data = {}
        try:
            if request.is_json:
                data = request.get_json(silent=True) or {}
            elif request.data:
                data = _py_json.loads(request.data.decode('utf-8', errors='ignore'))
            elif request.form:
                data = request.form.to_dict()
        except Exception:
            data = {}

        unit_id = str(data.get("unit_id") or "FIELD-UNIT").strip()
        commander = str(data.get("commander") or "").strip()
        phone = str(data.get("phone") or "").strip()
        incident_id = str(data.get("incident_id") or "").strip()
        report_type = str(data.get("report_type") or "support_request").strip()
        message = str(data.get("message") or data.get("note") or "").strip()
        lat = data.get("latitude")
        lon = data.get("longitude")
        file_base64 = data.get("image_base64") or data.get("picture_base64")
        image_url = data.get("image_url") or data.get("picture_url") or "-"

        # หากไม่ได้ระบุพิกัดมา ให้ค้นหาจากพิกัดล่าสุดของ tracker
        if (lat is None or lon is None or lat == 0 or lon == 0) and unit_id in active_trackers:
            lat = active_trackers[unit_id].get("latitude", 0)
            lon = active_trackers[unit_id].get("longitude", 0)

        if not commander and unit_id in active_trackers:
            commander = active_trackers[unit_id].get("commander", unit_id)

        # หากมีรูปภาพ Base64 แนบมา ให้บันทึกเข้า Google Sheets/Drive ใน Background Thread เพื่อให้มือถือตอบสนองทันที
        if file_base64:
            image_url = f"data:image/jpeg;base64,{file_base64}"
            try:
                file_name = f"FIELD_{unit_id}_{int(time.time())}.jpg"
                title = f"[{incident_id or 'หน้างาน'}] ภาพรายงานจาก {commander or unit_id}"
                
                def _async_save():
                    try:
                        save_to_google_sheet(
                            lat=lat or 0,
                            lon=lon or 0,
                            title=title,
                            address=f"จุดปฏิบัติการ {unit_id}",
                            reporter=commander or unit_id,
                            urgency="🔴 วิกฤต" if report_type == "support_request" else "🟡 ปานกลาง",
                            incident_type="📸 ภาพรายงานหน้างาน",
                            file_base64=file_base64,
                            file_name=file_name,
                            mime_type="image/jpeg"
                        )
                    except Exception as _e:
                        print(f"[FieldReport] Async save error: {_e}")

                threading.Thread(target=_async_save, daemon=True).start()
            except Exception as err:
                print(f"[FieldReport] Error queuing photo: {err}")

        rep_id = f"REP-{int(time.time() * 1000) % 1000000:06d}"
        now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 7 * 3600))
        report_entry = {
            "id": rep_id,
            "incident_id": incident_id,
            "unit_id": unit_id,
            "commander": commander or unit_id,
            "phone": phone,
            "report_type": report_type,
            "message": message,
            "image_url": image_url,
            "latitude": float(lat) if lat is not None else 0.0,
            "longitude": float(lon) if lon is not None else 0.0,
            "timestamp": now_str
        }
        FIELD_REPORTS.append(report_entry)
        if len(FIELD_REPORTS) > 100:
            FIELD_REPORTS = FIELD_REPORTS[-100:]

        # อัปเดตสถานะของ unit ใน active_trackers
        if unit_id in active_trackers:
            if report_type == "support_request":
                active_trackers[unit_id]["status"] = f"⚠️ ขอสนับสนุน: {message[:30]}"
            elif report_type == "photo_report":
                active_trackers[unit_id]["status"] = "📸 ส่งภาพรายงานหน้างานแล้ว"

        return jsonify({
            "status": "success",
            "message": "บันทึกรายงานหน้างานเรียบร้อย ส่งสัญญาณเข้าศูนย์บัญชาการแล้ว",
            "report": report_entry
        }), 200
    except Exception as e:
        print(f"[api_incident_field_report] Error: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@app.route("/api/incident/field-reports", methods=['GET'])
def api_incident_field_reports():
    """ส่งรายการรายงานหน้างานทั้งหมดให้ Dashboard ตรวจสอบ (รองรับทั้งขอสนับสนุนและภาพถ่าย)"""
    return jsonify({
        "status": "success",
        "count": len(FIELD_REPORTS),
        "reports": list(reversed(FIELD_REPORTS[-50:]))
    }), 200


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

    # บันทึก OTP ลงแคช (RAM + File)
    set_cached_otp(phone, {
        "otp": otp_code,
        "expires_at": time.time() + 300,  # 5 นาที
        "user_info": user_info
    })

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
    if master_pin and (master_pin == MASTER_PIN or master_pin.upper() == MASTER_PIN.upper()):
        user_info = {
            "name": "ผู้ดูแลระบบสูงสุด (Master Admin)",
            "phone": "0863390614",
            "username": "commander",
            "role": "ผู้ดูแลระบบสูงสุด (Super Admin)",
            "unit": "กรมแผนที่ทหาร (RTSD)",
            "position": "Commander In Chief",
            "status": "อนุมัติแล้ว",
            "picture_profile": phone_to_user.get("0863390614", {}).get("picture_profile", "-"),
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

    clean_login = login_id.lower().replace("-", "").replace(" ", "")
    norm_phone = normalize_phone_number(clean_login)

    # ตรวจสอบว่ามีบันทึกใน USER_CREDENTIALS หรือไม่
    cred = USER_CREDENTIALS.get(clean_login) or (USER_CREDENTIALS.get(norm_phone) if norm_phone else None)
    if not cred:
        # ค้นหาโดยตรงจาก SQLite บน NAS
        db_u = db_get_user_by_username(clean_login) or (db_get_user_by_phone(norm_phone) if norm_phone else None)
        if db_u and db_u.get("password"):
            cred = {
                "username": db_u.get("username", "") or db_u.get("phone", ""),
                "password": db_u.get("password", ""),
                "phone": db_u.get("phone", "")
            }

    is_admin_user = (clean_login in [p.lower() for p in ADMIN_PHONES]) or \
                    (norm_phone in [p.lower() for p in ADMIN_PHONES]) or \
                    (clean_login in ["admin", "rtsd_admin", "commander", "superadmin", "administrator"])

    # ตรวจสอบรหัสผ่าน: MASTER_PIN, Admin default, หรือรหัสผ่านที่ผู้ใช้ตั้งไว้ตอนสมัคร
    valid_password = False
    if password == MASTER_PIN or password.upper() == MASTER_PIN.upper():
        valid_password = True
    elif is_admin_user and password.lower() in ["admin1234", "rtsd2024", MASTER_PIN.lower()]:
        valid_password = True
    elif cred and cred.get("password") and cred.get("password") == password:
        valid_password = True

    if not valid_password:
        return jsonify({"status": "error", "message": "รหัสผ่านหรือ PIN ไม่ถูกต้อง"}), 401

    # ดึงข้อมูลผู้ใช้ล่าสุด
    if not phone_to_user or len(phone_to_user) <= 1:
        fetch_registered_users()

    matched_phone = None
    if cred and cred.get("phone"):
        matched_phone = normalize_phone_number(cred["phone"])
    elif norm_phone and len(norm_phone) >= 9:
        matched_phone = norm_phone

    existing_user = None
    if matched_phone and matched_phone in phone_to_user:
        existing_user = phone_to_user[matched_phone]
    elif clean_login in phone_to_user:
        existing_user = phone_to_user[clean_login]
    else:
        # ค้นหาเพิ่มเติมจาก username หรือชื่อ
        for ph, u in phone_to_user.items():
            if u.get("username", "").lower() == clean_login or (u.get("name") and clean_login in u.get("name", "").lower()):
                existing_user = u
                matched_phone = ph
                break

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
        "username": "admin",
        "role": "ผู้ดูแลระบบสูงสุด (Super Admin)",
        "unit": "กรมแผนที่ทหาร (RTSD)",
        "position": "Commander",
        "status": "อนุมัติแล้ว",
        "picture_profile": phone_to_user.get("0863390614", {}).get("picture_profile", "-"),
        "permissions": get_user_permissions("ผู้ดูแลระบบสูงสุด (Super Admin)")
    }
    if is_admin_user:
        user_info["role"] = "ผู้ดูแลระบบสูงสุด (Super Admin)"
        user_info["permissions"] = get_user_permissions(user_info["role"])

    if not user_info.get("unit_size"):
        user_info["unit_size"] = "ชุดปฏิบัติการขนาดเล็ก (3-5 นาย)"
    if not user_info.get("vehicle_type"):
        user_info["vehicle_type"] = "🚗 รถกระบะตรวจการณ์ 4x4 (Pickup 4WD)"

    if cred and cred.get("username"):
        user_info["username"] = cred["username"]

    token = f"rtsd-token-{login_id}-{int(time.time())}-{random.randint(1000, 9999)}"
    ACTIVE_SESSIONS[token] = {
        "user": user_info,
        "phone": user_info.get("phone", matched_phone or login_id),
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
        clean_login = login_id.lower().replace("-", "").replace(" ", "")
        norm_phone = normalize_phone_number(clean_login)

        cred = USER_CREDENTIALS.get(clean_login) or (USER_CREDENTIALS.get(norm_phone) if norm_phone else None)
        is_admin_user = (clean_login in [p.lower() for p in ADMIN_PHONES]) or \
                        (norm_phone in [p.lower() for p in ADMIN_PHONES]) or \
                        (clean_login in ["admin", "rtsd_admin", "commander", "superadmin"])
        
        valid_password = False
        if password == MASTER_PIN or password.upper() == MASTER_PIN.upper():
            valid_password = True
        elif is_admin_user and password.lower() in ["admin1234", "rtsd2024", MASTER_PIN.lower()]:
            valid_password = True
        elif cred and cred.get("password") and cred.get("password") == password:
            valid_password = True
        
        if valid_password:
            if not phone_to_user or len(phone_to_user) <= 1:
                fetch_registered_users()

            matched_phone = None
            if cred and cred.get("phone"):
                matched_phone = normalize_phone_number(cred["phone"])
            elif norm_phone and len(norm_phone) >= 9:
                matched_phone = norm_phone

            existing_user = None
            if matched_phone and matched_phone in phone_to_user:
                existing_user = phone_to_user[matched_phone]
            elif clean_login in phone_to_user:
                existing_user = phone_to_user[clean_login]
            else:
                for ph, u in phone_to_user.items():
                    if u.get("username", "").lower() == clean_login or (u.get("name") and clean_login in u.get("name", "").lower()):
                        existing_user = u
                        matched_phone = ph
                        break

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
                "username": "admin",
                "role": "ผู้ดูแลระบบสูงสุด (Super Admin)",
                "unit": "กรมแผนที่ทหาร (RTSD)",
                "position": "Super Admin",
                "status": "อนุมัติแล้ว",
                "picture_profile": phone_to_user.get("0863390614", {}).get("picture_profile", "-")
            }
            if is_admin_user:
                user_info["role"] = "ผู้ดูแลระบบสูงสุด (Super Admin)"
            user_info["permissions"] = get_user_permissions(user_info["role"])
            if cred and cred.get("username"):
                user_info["username"] = cred["username"]

            token = f"token-{login_id}-{int(time.time())}"
            ACTIVE_SESSIONS[token] = {
                "user": user_info,
                "phone": user_info.get("phone", matched_phone or login_id),
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
    cached = get_cached_otp(phone)
    if not cached:
        return jsonify({"status": "error", "message": "ไม่พบคำขอ OTP หรือรหัสหมดอายุแล้ว กรุณากดขอใหม่"}), 400

    if time.time() > cached.get("expires_at", 0):
        delete_cached_otp(phone)
        return jsonify({"status": "error", "message": "รหัส OTP หมดอายุแล้ว (เกิน 5 นาที) กรุณากดขอใหม่"}), 400

    if cached.get("otp") != otp:
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
    delete_cached_otp(phone)

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
    username = str(data.get("username", "")).strip().replace(" ", "").lower()
    password = str(data.get("password", "") or data.get("pin", "")).strip()
    unit_size = str(data.get("unit_size", "ชุดปฏิบัติการขนาดเล็ก (3-5 นาย)")).strip()
    vehicle_type = str(data.get("vehicle_type", "🚗 รถกระบะตรวจการณ์ 4x4 (Pickup 4WD)")).strip()
    area = str(data.get("area", "") or data.get("district", "")).strip() or "เชียงราย (ทุกอำเภอ)"

    if not phone or len(phone) < 9 or len(phone) > 10:
        return jsonify({"status": "error", "message": "หมายเลขโทรศัพท์ไม่ถูกต้อง (ต้องเป็น 10 หลัก)"}), 400

    if not full_name:
        return jsonify({"status": "error", "message": "กรุณาระบุชื่อ-นามสกุล"}), 400

    req_id = f"RTSD-REQ-{int(time.time()) % 10000:04d}"
    initial_status = "อนุมัติแล้ว" if phone in ADMIN_PHONES else "รออนุมัติ"

    # บันทึกรหัสผ่านและชื่อผู้ใช้ลงใน USER_CREDENTIALS ทันที
    if password:
        cred_entry = {
            "username": username or phone,
            "password": password,
            "phone": phone,
            "name": full_name,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        USER_CREDENTIALS[phone] = cred_entry
        norm_phone = normalize_phone_number(phone)
        if norm_phone:
            USER_CREDENTIALS[norm_phone] = cred_entry
        if username:
            USER_CREDENTIALS[username] = cred_entry
        save_user_credentials()

    # บันทึกเข้า SQLite บน NAS, Memory Cache และส่งสำรองไป Google Sheets
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
        picture_base64=picture_base64,
        username=username,
        password=password,
        unit_size=unit_size,
        vehicle_type=vehicle_type,
        area=area
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
            f"🔗 https://linebot.goc-rtsd.com/dashboard\n"
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
    """ส่งรายชื่อผู้ลงทะเบียนทั้งหมดให้แอดมินดูและจัดการสิทธิ์ (ตัดความซ้ำซ้อนและแปลงลิงก์รูปให้แสดงผลได้ทันที)"""
    fetch_registered_users()
    users_list = []
    seen_phones = set()
    for raw_phone, u in phone_to_user.items():
        phone = normalize_phone_number(raw_phone)
        if phone in seen_phones:
            continue
        seen_phones.add(phone)
        role = u.get("role", "ผู้ใช้งานทั่วไป (Observer)")
        pic = normalize_drive_image_url(u.get("picture_profile", "-"))
        users_list.append({
            "name": u.get("name", "ไม่ระบุชื่อ"),
            "phone": phone,
            "username": u.get("username", ""),
            "unit": u.get("unit", "-"),
            "position": u.get("position", "-"),
            "role": role,
            "status": u.get("status", "อนุมัติแล้ว"),
            "area": u.get("area", "เชียงราย (ทุกอำเภอ)"),
            "district": u.get("district", "เชียงราย (ทุกอำเภอ)"),
            "unit_size": u.get("unit_size", "ชุดปฏิบัติการขนาดเล็ก (3-5 นาย)"),
            "vehicle_type": u.get("vehicle_type", "🚗 รถกระบะตรวจการณ์ 4x4 (Pickup 4WD)"),
            "line_user_id": u.get("line_user_id", "-"),
            "picture_profile": pic,
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

    # บันทึกลง SQLite บน NAS และซิงค์ไป Google Sheets
    save_registered_user(
        line_user_id=lid,
        name=user_info.get("name", "ผู้ใช้งาน"),
        phone=phone,
        role=new_role,
        unit=user_info.get("unit", "-"),
        position=user_info.get("position", "-"),
        status=new_status,
        purpose=user_info.get("purpose", "-"),
        picture_profile=user_info.get("picture_profile", "-"),
        username=user_info.get("username", ""),
        password=USER_CREDENTIALS.get(phone, {}).get("password", ""),
        unit_size=user_info.get("unit_size", "ชุดปฏิบัติการขนาดเล็ก (3-5 นาย)"),
        vehicle_type=user_info.get("vehicle_type", "🚗 รถกระบะตรวจการณ์ 4x4 (Pickup 4WD)"),
        area=user_info.get("area", "เชียงราย (ทุกอำเภอ)")
    )

    # Push แจ้งเตือนทาง LINE หากมี LINE ID
    if lid and lid.startswith("U"):
        try:
            # สลับริชเมนูตามสิทธิ์ใหม่แบบอัตโนมัติ
            if new_status == "อนุมัติแล้ว":
                switch_user_rich_menu(lid, new_role)
            else:
                switch_user_rich_menu(lid, "citizen")

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
                    f"⚡ ระบบได้เปิดใช้งาน 'ริชเมนูยุทธการ (Tactical Command)' ให้ท่านแล้ว!\n"
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
    """แอดมินลบบัญชีผู้ใช้งานออกจากระบบ และลบออกจากตาราง Google Sheets ถาวร"""
    global phone_to_user, registered_users, DELETED_USERS
    data = request.get_json(silent=True) or {}
    phone = str(data.get("phone", "")).strip().replace("-", "").replace(" ", "")

    if not phone:
        return jsonify({"status": "error", "message": "กรุณาระบุหมายเลขโทรศัพท์ที่ต้องการลบ"}), 400

    user_info = phone_to_user.get(phone, {})
    lid = user_info.get("line_user_id", "")

    # บันทึกเข้า Blacklist DELETED_USERS ถาวรเพื่อป้องกันการฟื้นคืนชีพเมื่อรีเฟรชหน้าเว็บ
    DELETED_USERS.add(phone)
    save_deleted_users()

    # ลบออกจาก SQLite บน NAS ทันที
    db_delete_user(phone)

    if phone in phone_to_user:
        del phone_to_user[phone]
    if lid and lid in registered_users:
        del registered_users[lid]

    # ลบข้อมูลรหัสผ่านออกจาก USER_CREDENTIALS ด้วย
    if phone in USER_CREDENTIALS:
        del USER_CREDENTIALS[phone]
        save_user_credentials()

    save_registered_users_cache()

    tokens_to_remove = [t for t, s in ACTIVE_SESSIONS.items() if s.get("phone") == phone]
    for t in tokens_to_remove:
        del ACTIVE_SESSIONS[t]

    # ส่งคำสั่งไปลบแถวในแท็บ Users ของ Google Sheets ถาวร
    sheet_res = {}
    try:
        res = requests.post(
            GOOGLE_SHEET_URL,
            json={
                "action": "delete_user",
                "phone": phone,
                "line_user_id": lid
            },
            timeout=10
        )
        if res.status_code == 200:
            sheet_res = res.json()
    except Exception as e:
        print(f"Error deleting user from Google Sheets: {e}")

    return jsonify({
        "status": "success", 
        "message": f"ลบบัญชีกำลังพล {phone} ออกจากระบบและ Google Sheets เรียบร้อยแล้ว",
        "sheet_sync": sheet_res
    }), 200


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
    norm_phone = normalize_phone_number(phone)
    if norm_phone:
        phone = norm_phone

    # ตรวจสอบสิทธิ์ผ่าน Token
    session = ACTIVE_SESSIONS.get(token) if token else None
    if session:
        session_phone = normalize_phone_number(session.get("phone", "")) or session.get("phone", "")
        if not phone:
            phone = session_phone
        is_admin = session_phone in ADMIN_PHONES or "Super Admin" in session.get("role", "")
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
    if "unit_size" in data and str(data["unit_size"]).strip():
        user_info["unit_size"] = str(data["unit_size"]).strip()
    if "vehicle_type" in data and str(data["vehicle_type"]).strip():
        user_info["vehicle_type"] = str(data["vehicle_type"]).strip()
    if "area" in data and str(data["area"]).strip():
        user_info["area"] = str(data["area"]).strip()
        user_info["district"] = str(data["area"]).strip()
    elif "district" in data and str(data["district"]).strip():
        user_info["area"] = str(data["district"]).strip()
        user_info["district"] = str(data["district"]).strip()

    if "password" in data and str(data["password"]).strip():
        new_pwd = str(data["password"]).strip()
        cred = USER_CREDENTIALS.get(phone, {})
        cred["password"] = new_pwd
        cred["phone"] = phone
        USER_CREDENTIALS[phone] = cred
        norm_ph = normalize_phone_number(phone)
        if norm_ph:
            USER_CREDENTIALS[norm_ph] = cred
        if cred.get("username"):
            USER_CREDENTIALS[cred["username"]] = cred
        save_user_credentials()

    if "username" in data and str(data["username"]).strip():
        new_un = str(data["username"]).strip().lower()
        cred = USER_CREDENTIALS.get(phone, {})
        cred["username"] = new_un
        cred["phone"] = phone
        USER_CREDENTIALS[phone] = cred
        USER_CREDENTIALS[new_un] = cred
        user_info["username"] = new_un
        save_user_credentials()

    pic_url = str(data.get("picture_profile", "")).strip()
    pic_b64 = str(data.get("picture_base64", "")).strip()

    if pic_b64:
        user_info["picture_profile"] = f"data:image/jpeg;base64,{pic_b64}"
    elif pic_url and pic_url != "-":
        user_info["picture_profile"] = pic_url

    if phone in ADMIN_PHONES or phone == "0863390614":
        user_info["role"] = "ผู้ดูแลระบบสูงสุด (Super Admin)"
        user_info["permissions"] = get_user_permissions(user_info["role"])

    lid = user_info.get("line_user_id", "")
    if lid and lid != "-" and lid in registered_users:
        registered_users[lid].update({
            "name": user_info["name"],
            "unit": user_info["unit"],
            "position": user_info["position"],
            "area": user_info.get("area", "เชียงราย (ทุกอำเภอ)"),
            "district": user_info.get("district", "เชียงราย (ทุกอำเภอ)"),
            "picture_profile": user_info["picture_profile"],
            "unit_size": user_info.get("unit_size", "ชุดปฏิบัติการขนาดเล็ก (3-5 นาย)"),
            "vehicle_type": user_info.get("vehicle_type", "🚗 รถกระบะตรวจการณ์ 4x4 (Pickup 4WD)")
        })

    # ซิงค์ Session ปัจจุบัน
    for s_tok, s in ACTIVE_SESSIONS.items():
        if normalize_phone_number(s.get("phone", "")) == phone:
            s["user"] = user_info

    # ซิงค์ลง Google Sheets และแคชลงไฟล์ดิสก์
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
        picture_base64=pic_b64,
        username=user_info.get("username", ""),
        password=USER_CREDENTIALS.get(phone, {}).get("password", ""),
        unit_size=user_info.get("unit_size", "ชุดปฏิบัติการขนาดเล็ก (3-5 นาย)"),
        vehicle_type=user_info.get("vehicle_type", "🚗 รถกระบะตรวจการณ์ 4x4 (Pickup 4WD)"),
        area=user_info.get("area", "เชียงราย (ทุกอำเภอ)")
    )
    save_registered_users_cache()

    return jsonify({
        "status": "success",
        "message": "อัปเดตข้อมูลโปรไฟล์เรียบร้อยแล้ว",
        "user": user_info
    }), 200


@app.route("/api/user/profile", methods=['GET'])
def api_user_profile():
    """API ดึงข้อมูลโปรไฟล์ผู้ใช้ล่าสุด สำหรับ Mobile App ซิงค์ข้อมูล"""
    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()

    phone = str(request.args.get("phone", "")).strip().replace("-", "").replace(" ", "")
    norm_phone = normalize_phone_number(phone)
    if norm_phone:
        phone = norm_phone

    if not phone and token:
        session = ACTIVE_SESSIONS.get(token)
        if session:
            phone = normalize_phone_number(session.get("phone", "")) or session.get("phone", "")

    if not phone:
        return jsonify({"status": "error", "message": "กรุณาระบุหมายเลขโทรศัพท์หรือแนบ Token"}), 400

    user_info = phone_to_user.get(phone)
    if not user_info:
        # ตรวจสอบจาก SQLite DB บน NAS ทันที
        db_u = db_get_user_by_phone(phone)
        if db_u:
            sync_db_to_memory_cache()
            user_info = phone_to_user.get(phone)
        if not user_info:
            fetch_registered_users()
            user_info = phone_to_user.get(phone)

    if not user_info:
        return jsonify({"status": "error", "message": "ไม่พบข้อมูลผู้ใช้"}), 404

    return jsonify({
        "status": "success",
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
        # สลับริชเมนูให้ตรงกับบทบาทของผู้ใช้
        if user_info.get("status") == "อนุมัติแล้ว":
            switch_user_rich_menu(user_id, user_info.get("role", "citizen"))
        else:
            switch_user_rich_menu(user_id, "citizen")

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
            f"https://linebot.goc-rtsd.com/register\n\n"
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
    lat = float(event.message.latitude)
    lon = float(event.message.longitude)
    address = event.message.address or "ไม่ระบุที่อยู่"
    title = event.message.title or "จุดแจ้งเหตุ"

    user_info = registered_users.get(user_id) or {}
    user_name = user_info.get("name") or user_sessions.get(user_id, {}).get("user_name", "ผู้ใช้งาน LINE")

    session = user_sessions.get(user_id, {})
    is_reporting = bool(session.get("incident_type"))

    # บันทึกพิกัดล่าสุดลง Session เสมอ
    session["lat"] = lat
    session["lon"] = lon
    session["address"] = address
    session["title"] = title
    user_sessions[user_id] = session

    # -------------------------------------------------------------
    # กรณีที่ 1: ผู้ใช้อยู่ในขั้นตอน "แจ้งเหตุ" (เลือกประเภทเหตุไว้แล้ว)
    # -------------------------------------------------------------
    if is_reporting:
        session["waiting_for_media"] = True
        user_sessions[user_id] = session

        # สแกนหากำลังพลใกล้เคียงเพื่อแจ้งให้ผู้แจ้งเหตุอุ่นใจ
        active_units = get_computed_active_units()
        nearby_units = []
        for u in active_units:
            if u.get("calculated_state") in ["ACTIVE", "STANDBY"]:
                u_lat = u.get("latitude", 0)
                u_lon = u.get("longitude", 0)
                dist = calculate_distance_km(lat, lon, u_lat, u_lon)
                if dist <= 15.0:
                    nearby_units.append((dist, u))

        nearby_support_text = ""
        if nearby_units:
            nearby_units.sort(key=lambda x: x[0])
            closest_dist, closest_unit = nearby_units[0]
            nearby_support_text = f"\n\n🛡️ มีชุดปฏิบัติการใกล้เคียง: {closest_unit.get('unit_name')} (ห่าง ~{closest_dist:.1f} กม. พร้อมเข้าช่วยเหลือ)"

        quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label="⏩ ข้าม (ไม่ส่งรูป)", text="ข้ามการส่งรูป"))
        ])

        reply = (f"📍 ได้รับพิกัดจุดเกิดเหตุเรียบร้อยแล้วครับ!\n"
                 f"──────────────────────\n"
                 f"🚨 ประเภท: {session.get('incident_type')}\n"
                 f"⚠️ ความเร่งด่วน: {session.get('urgency', '🟡 ปานกลาง')}\n"
                 f"🏠 สถานที่: {address}{nearby_support_text}\n"
                 f"──────────────────────\n"
                 f"📸 กรุณากดถ่ายหรือส่ง [รูปถ่าย] หรือ [คลิปวิดีโอ] หลักฐานเข้ามาได้เลยครับ (หรือกดปุ่ม '⏩ ข้าม (ไม่ส่งรูป)' ด้านล่าง)")

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply, quick_reply=quick_reply)]
                )
            )
        return

    # -------------------------------------------------------------
    # กรณีที่ 2: ผู้ใช้แชร์พิกัดมาโดยตรง -> แสดง SitRep สถานการณ์รอบตัว (รัศมี 15 กม.)
    # -------------------------------------------------------------
    all_incidents = get_user_incidents(limit=50)
    nearby_incidents = []
    for inc in all_incidents:
        inc_lat = inc.get("latitude", 0)
        inc_lon = inc.get("longitude", 0)
        if inc_lat and inc_lon:
            dist = calculate_distance_km(lat, lon, inc_lat, inc_lon)
            if dist <= 15.0:
                inc_copy = dict(inc)
                inc_copy["dist_km"] = dist
                nearby_incidents.append(inc_copy)
    nearby_incidents.sort(key=lambda x: x["dist_km"])

    active_units = get_computed_active_units()
    nearby_units = []
    for u in active_units:
        u_lat = u.get("latitude", 0)
        u_lon = u.get("longitude", 0)
        if u_lat and u_lon:
            dist = calculate_distance_km(lat, lon, u_lat, u_lon)
            if dist <= 15.0:
                u_copy = dict(u)
                u_copy["dist_km"] = dist
                nearby_units.append(u_copy)
    nearby_units.sort(key=lambda x: x["dist_km"])

    sitrep_msg = [
        "📡 รายงานสถานการณ์รอบตัว (SitRep)",
        "━━━━━━━━━━━━━━━━━━",
        f"📍 พิกัด: {lat:.4f}, {lon:.4f}",
        f"🏠 ตำแหน่ง: {address[:40]}",
        "━━━━━━━━━━━━━━━━━━"
    ]

    # สรุปเหตุการณ์ใกล้เคียง
    if nearby_incidents:
        sitrep_msg.append(f"🚨 เหตุการณ์ใกล้เคียง (พบ {len(nearby_incidents)} จุดในระยะ 15 กม.):")
        for idx, inc in enumerate(nearby_incidents[:3], 1):
            st = inc.get("status", "⏳ รอดำเนินการ")
            st_icon = "🟢" if ("เสร็จ" in st or "เรียบร้อย" in st) else ("🟡" if "ดำเนิน" in st else "⏳")
            sitrep_msg.append(
                f"{idx}. {inc.get('incident_type', 'เหตุ')} (ห่าง {inc['dist_km']:.1f} กม.)\n"
                f"   {st_icon} {st} | {inc.get('urgency', 'ปานกลาง')}\n"
                f"   🏠 {inc.get('address', '-')[:28]}"
            )
    else:
        sitrep_msg.append("🚨 เหตุการณ์ใกล้เคียง: ✅ ไม่พบเหตุการณ์ฉุกเฉินในระยะ 15 กม.")

    sitrep_msg.append("──────────────────")

    # สรุปกำลังพลใกล้เคียง
    if nearby_units:
        sitrep_msg.append(f"👥 ชุดปฏิบัติการใกล้เคียง (พบ {len(nearby_units)} หน่วย):")
        for u in nearby_units[:3]:
            u_state = u.get("calculated_state", "STANDBY")
            u_icon = "🟢" if u_state == "ACTIVE" else ("🔵" if u_state == "STANDBY" else "⚫")
            sitrep_msg.append(
                f"• {u_icon} {u.get('unit_name')} ({u.get('commander')})\n"
                f"  🚘 {u.get('vehicle_type', '-')} | ห่าง {u['dist_km']:.1f} กม. [{u_state}]"
            )
    else:
        sitrep_msg.append("👥 ชุดปฏิบัติการใกล้เคียง: ℹ️ ไม่พบหน่วยส่ง GPS สดในระยะ 15 กม.")

    sitrep_msg.append("━━━━━━━━━━━━━━━━━━")
    sitrep_msg.append("💡 หากพบเหตุฉุกเฉิน กดปุ่ม '🚨 แจ้งเหตุ ณ จุดนี้' ได้ทันทีครับ")

    quick_items = [
        QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุ ณ จุดนี้", text="แจ้งเหตุ ณ จุดนี้")),
        QuickReplyItem(action=MessageAction(label="👥 เช็กกำลังพล", text="เช็กกำลังพล")),
        QuickReplyItem(action=MessageAction(label="🔍 ติดตามสถานะ", text="ติดตามสถานะ")),
        QuickReplyItem(action=MessageAction(label="🆘 เบอร์ฉุกเฉิน", text="เบอร์ฉุกเฉิน")),
        QuickReplyItem(action=MessageAction(label="🛰️ ส่งพิกัดสด GPS", text="แทร็กกิ้ง"))
    ]

    # ถ้ามีเหตุการณ์ใกล้เคียงและเปิดแชร์ Google Maps ให้เพิ่มปุ่มนำทาง
    if SYSTEM_SETTINGS.get("enable_google_maps") and nearby_incidents:
        top_inc = nearby_incidents[0]
        if top_inc.get("latitude") and top_inc.get("longitude"):
            quick_items.insert(1, QuickReplyItem(action=URIAction(
                label=f"🧭 นำทาง ({top_inc['dist_km']:.1f} กม.)",
                uri=f"https://www.google.com/maps/dir/?api=1&destination={top_inc['latitude']},{top_inc['longitude']}"
            )))

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text="\n".join(sitrep_msg), quick_reply=QuickReply(items=quick_items))]
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

    # ตรวจสอบว่าผู้ส่งภาพเป็นเจ้าหน้าที่ชุดปฏิบัติการหรือไม่
    officer_unit = None
    officer_info = registered_users.get(user_id) or {}
    officer_phone = officer_info.get("phone")
    for uid, tr in active_trackers.items():
        if tr.get("commander") == officer_info.get("name") or (officer_phone and officer_phone in tr.get("unit_name", "")):
            officer_unit = tr
            break
        if uid.startswith("RTSD") and officer_phone and officer_phone[-4:] in uid:
            officer_unit = tr
            break

    # หากเป็นเจ้าหน้าที่ที่กำลังส่งพิกัดสด ให้ดึงพิกัด GPS ล่าสุดมาผูกกับภาพทันที
    if officer_unit and (not lat or lat == 0):
        lat = officer_unit.get("latitude", 0)
        lon = officer_unit.get("longitude", 0)
        reporter = f"{officer_unit.get('commander', reporter)} ({officer_unit.get('unit_id', '')})"

    # ออกรหัสติดตามเหตุการณ์เฉพาะ (Report Tracking ID)
    report_id = f"RTSD-{int(time.time()) % 100000:04d}"
    title = f"[{report_id}] แจ้งเหตุ: {incident_type}"
    INCIDENT_REPORTERS[report_id] = user_id
    INCIDENT_REPORTERS[title] = user_id

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

    # ส่งเข้า FIELD_REPORTS เพื่อให้ขึ้นบน Dashboard ทันทีโดยไม่ต้องรอ Google Drive sync
    global FIELD_REPORTS
    rep_img_url = f"data:image/jpeg;base64,{file_base64}" if file_base64 else "-"
    FIELD_REPORTS.append({
        "id": f"LINE-IMG-{message_id}",
        "incident_id": report_id,
        "unit_id": officer_unit.get("unit_id", "LINE-OFFICER") if officer_unit else "LINE-REPORTER",
        "commander": reporter,
        "phone": officer_phone or "",
        "report_type": "photo_report",
        "message": f"ภาพถ่ายรายงานหน้างาน จากคุณ {reporter}",
        "image_url": rep_img_url,
        "latitude": float(lat) if lat else 0.0,
        "longitude": float(lon) if lon else 0.0,
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    if len(FIELD_REPORTS) > 100:
        FIELD_REPORTS = FIELD_REPORTS[-100:]

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
    INCIDENT_REPORTERS[report_id] = user_id
    INCIDENT_REPORTERS[title] = user_id

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
                         f"👉 https://linebot.goc-rtsd.com/register\n"
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

    # 0.4. คำสั่งเชื่อมต่อบัญชี LINE เข้ากับแอปมือถือ (Account Linking)
    clean_cmd_text = re.sub(r'[\s\-]', '', user_text)
    if any(clean_cmd_text.startswith(k) for k in ["ผูกบัญชี", "ผูกแอป", "เชื่อมแอป", "เชื่อมต่อแอป", "linkapp"]):
        phone_match = re.search(r'(0[689]\d{8}|0[2-9]\d{7})', clean_cmd_text)
        if not phone_match:
            reply_msg = ("📱 คำสั่งผูกบัญชี LINE เข้ากับแอปมือถือ RTSD\n"
                         "━━━━━━━━━━━━━━━━━━\n"
                         "กรุณาระบุเบอร์โทรศัพท์ที่ท่านใช้สมัครในแอป เช่น:\n"
                         "👉 'ผูกบัญชี 0812345678'\n"
                         "👉 'ผูกแอป 0891234567'")
        else:
            target_phone = phone_match.group(1)
            if target_phone not in phone_to_user:
                fetch_registered_users()

            if target_phone in phone_to_user:
                user_record = phone_to_user[target_phone]
                user_record["line_user_id"] = user_id
                save_registered_user(
                    line_user_id=user_id,
                    name=user_record.get("name", user_name),
                    phone=target_phone,
                    role=user_record.get("role", "ผู้ใช้งานทั่วไป"),
                    unit=user_record.get("unit", "-"),
                    position=user_record.get("position", "-"),
                    status="อนุมัติแล้ว",
                    purpose=user_record.get("purpose", "-"),
                    unit_size=user_record.get("unit_size", "-"),
                    vehicle_type=user_record.get("vehicle_type", "-")
                )
                registered_users[user_id] = user_record
                user_sessions[user_id]["user_name"] = f"{user_record.get('name')} ({target_phone})"
                
                # สลับริชเมนูตามบทบาทเจ้าหน้าที่
                switch_user_rich_menu(user_id, user_record.get('role', 'officer'))

                reply_msg = (f"🎉 ผูกบัญชี LINE กับแอปมือถือสำเร็จแล้ว!\n"
                             f"━━━━━━━━━━━━━━━━━━\n"
                             f"👤 เจ้าหน้าที่: {user_record.get('name')}\n"
                             f"📱 เบอร์โทรศัพท์: {target_phone}\n"
                             f"🔰 สังกัดหน่วย: {user_record.get('unit', '-')}\n"
                             f"🎖️ ตำแหน่ง: {user_record.get('position', '-')}\n"
                             f"🛡️ สิทธิ์: {user_record.get('role', 'ผู้ใช้งานทั่วไป')}\n"
                             f"━━━━━━━━━━━━━━━━━━\n"
                             f"⚡ ระบบได้สลับเป็น 'ริชเมนูยุทธการ (Tactical Command)' ให้ท่านเรียบร้อยแล้ว!\n"
                             f"✅ ทุกการแจ้งเตือนและการสั่งการจากศูนย์ TOC จะส่งตรงเข้า LINE และแอปของท่านพร้อมกันทันทีครับ!")
            else:
                # บันทึกใหม่พร้อมผูก LINE ทันที
                save_registered_user(
                    line_user_id=user_id,
                    name=user_name,
                    phone=target_phone,
                    role="ผู้ใช้งานทั่วไป",
                    status="อนุมัติแล้ว"
                )
                reply_msg = (f"✅ ผูกบัญชีและลงทะเบียนเรียบร้อยแล้ว!\n"
                             f"━━━━━━━━━━━━━━━━━━\n"
                             f"👤 ชื่อ: {user_name}\n"
                             f"📱 เบอร์โทร: {target_phone}\n"
                             f"👉 บัญชี LINE นี้เชื่อมต่อกับแอปมือถือเรียบร้อยแล้วครับ")

        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_msg)]))
        return

    # 0.45. รายงานสถานการณ์ / ขอรับการสนับสนุน / ตอบรับคำสั่งจากเจ้าหน้าที่ภาคสนาม
    if any(k in user_text for k in ["ขอสนับสนุน", "ขอกำลัง", "ขอเรือ", "ขอรถ", "ต้องการกำลัง", "รับทราบคำสั่ง", "ถึงที่เกิดเหตุ", "รายงานหน้างาน"]):
        officer_unit = None
        officer_info = registered_users.get(user_id) or {}
        officer_phone = officer_info.get("phone")
        for uid, tr in active_trackers.items():
            if tr.get("commander") == officer_info.get("name") or (officer_phone and officer_phone in tr.get("unit_name", "")):
                officer_unit = tr
                break
            if uid.startswith("RTSD") and officer_phone and officer_phone[-4:] in uid:
                officer_unit = tr
                break

        unit_label = officer_unit.get("unit_id") if officer_unit else (user_info.get("name") if user_info else user_name)
        lat = officer_unit.get("latitude", 0) if officer_unit else 0
        lon = officer_unit.get("longitude", 0) if officer_unit else 0

        # Update tracker status
        if officer_unit and officer_unit.get("unit_id") in active_trackers:
            active_trackers[officer_unit["unit_id"]]["status"] = f"⚠️ {user_text[:35]}"

        # Record in FIELD_REPORTS
        rep_id = f"LINE-REQ-{int(time.time() * 1000) % 1000000:06d}"
        field_rep = {
            "id": rep_id,
            "incident_id": "FIELD-LINE",
            "unit_id": unit_label,
            "commander": officer_info.get("name", user_name),
            "phone": officer_phone or "",
            "report_type": "support_request" if ("ขอ" in user_text or "ต้องการ" in user_text) else "status_update",
            "message": user_text,
            "image_url": "-",
            "latitude": float(lat) if lat else 0.0,
            "longitude": float(lon) if lon else 0.0,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        global FIELD_REPORTS
        FIELD_REPORTS.append(field_rep)
        if len(FIELD_REPORTS) > 100:
            FIELD_REPORTS = FIELD_REPORTS[-100:]

        reply_msg = (f"🚨 [ศูนย์บัญชาการ TOC รับทราบรายงานแล้ว]\n"
                     f"━━━━━━━━━━━━━━━━━━\n"
                     f"📡 รายงาน: {user_text}\n"
                     f"👤 ผู้รายงาน: {officer_info.get('name', user_name)}\n"
                     f"⏱️ เวลา: {datetime.datetime.now().strftime('%H:%M:%S น.')}\n"
                     f"━━━━━━━━━━━━━━━━━━\n"
                     f"ข้อมูลและพิกัดถูกส่งขึ้นหน้าจอ Dashboard เรียบร้อยแล้ว ศูนย์ฯ กำลังเร่งประสานสนับสนุนครับ")
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=URIAction(label="🌐 ตรวจสอบบน Dashboard", uri="https://linebot.goc-rtsd.com/dashboard")),
            QuickReplyItem(action=MessageAction(label="📸 ส่งภาพถ่ายรายงาน", text="ส่งรูปรายงาน"))
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
                f"👉 ตรวจสอบและอนุมัติที่ https://linebot.goc-rtsd.com/dashboard"
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
        INCIDENT_REPORTERS[report_id] = user_id
        INCIDENT_REPORTERS[title] = user_id

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

    # =========================================================================
    # 👥 ระบบเช็กกำลังพล & สถานะเวรปฏิบัติการ (Personnel & Unit Roster)
    # =========================================================================
    elif user_text in ["เช็กกำลังพล", "กำลังพล", "สถานะเวร", "เช็คกำลังพล", "ยอดกำลังพล", "เวร", "หน่วย", "roster", "units", "กำลังพลเวร"]:
        units = get_computed_active_units()
        
        active_list = [u for u in units if u.get("calculated_state") == "ACTIVE"]
        standby_list = [u for u in units if u.get("calculated_state") == "STANDBY"]
        offline_list = [u for u in units if u.get("calculated_state") == "OFFLINE"]
        total_registered = len(phone_to_user)
        
        msg_lines = [
            "🎖️ รายงานยอดกำลังพล & สถานะเวรปฏิบัติการ",
            "━━━━━━━━━━━━━━━━━━",
            "📊 สรุปความพร้อมรบ/สนับสนุน:",
            f"🟢 ปฏิบัติการในพื้นที่ (ACTIVE): {len(active_list)} หน่วย",
            f"🔵 พร้อมออกเหตุ (STANDBY): {len(standby_list)} หน่วย",
            f"⚫ ปิดระบบ/พักเวร (OFFLINE): {len(offline_list)} หน่วย",
            f"👥 ทะเบียนกำลังพลในระบบ: {total_registered} นาย",
            "━━━━━━━━━━━━━━━━━━"
        ]
        
        deployed_units = active_list + standby_list
        if deployed_units:
            msg_lines.append("📋 รายชื่อชุดปฏิบัติการที่ออนไลน์:")
            for u in deployed_units[:6]:
                st = u.get("calculated_state", "STANDBY")
                icon = "🟢" if st == "ACTIVE" else "🔵"
                spd = float(u.get("speed", 0))
                spd_text = f"ความเร็ว {spd:.0f} กม./ชม." if spd > 1 else "จอดประจำจุด"
                msg_lines.append(
                    f"• {icon} {u.get('unit_name')} ({u.get('commander')})\n"
                    f"  🚘 {u.get('vehicle_type', '-')} | 👥 {u.get('unit_size', '-')}\n"
                    f"  📍 {u.get('status', '-')} ({spd_text})"
                )
            if len(deployed_units) > 6:
                msg_lines.append(f"...และอีก {len(deployed_units) - 6} หน่วย")
        else:
            msg_lines.append("ℹ️ ขณะนี้ยังไม่มีชุดปฏิบัติการส่งสัญญาณ GPS สด")
            msg_lines.append("💡 เจ้าหน้าที่สามารถกด '🛰️ ส่งพิกัดสด GPS' หรือเปิดหน้าเว็บ https://linebot.goc-rtsd.com/tracker เพื่อเริ่มเข้าเวรได้ทันทีครับ")
            
        msg_lines.append("━━━━━━━━━━━━━━━━━━")
        msg_lines.append("🌐 ดูแผนที่สถานการณ์สด (มือถือ):\n👉 https://linebot.goc-rtsd.com/map\n(หรือเปิด Command Dashboard PC: https://linebot.goc-rtsd.com/dashboard)")
        
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=URIAction(label="🗺️ แผนที่สถานการณ์", uri="https://linebot.goc-rtsd.com/map")),
            QuickReplyItem(action=MessageAction(label="🔄 รีเฟรชยอด", text="เช็กกำลังพล")),
            QuickReplyItem(action=LocationAction(label="📍 เช็กสถานการณ์รอบตัว")),
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุเตือนภัย", text="แจ้งเหตุ"))
        ])
        reply_message = TextMessage(text="\n".join(msg_lines), quick_reply=quick_reply)

    # =========================================================================
    # 📍 แจ้งเหตุ ณ จุดนี้ (เชื่อมต่อจากพิกัดเดิมที่เพิ่งแชร์มา)
    # =========================================================================
    elif user_text in ["แจ้งเหตุ ณ จุดนี้", "แจ้งเหตุที่นี่", "แจ้งเหตุพิกัดนี้"]:
        session = user_sessions.get(user_id, {})
        lat = session.get("lat")
        lon = session.get("lon")
        addr = session.get("address", "พิกัดที่ระบุ")
        if not lat or not lon:
            quick_reply = QuickReply(items=[
                QuickReplyItem(action=LocationAction(label="📍 ส่งตำแหน่งพิกัด"))
            ])
            reply_message = TextMessage(
                text="👉 กรุณากดปุ่ม '📍 ส่งตำแหน่งพิกัด' ด้านล่างก่อน เพื่อระบุตำแหน่งจุดเกิดเหตุครับ",
                quick_reply=quick_reply
            )
        else:
            quick_reply = QuickReply(items=[
                QuickReplyItem(action=MessageAction(label="🌊 น้ำท่วมขัง", text="เลือกเหตุ: 🌊 น้ำท่วมขัง")),
                QuickReplyItem(action=MessageAction(label="🚧 ถนนชำรุด", text="เลือกเหตุ: 🚧 ดินถล่ม / ผิวทางชำรุด")),
                QuickReplyItem(action=MessageAction(label="💥 อุบัติเหตุ", text="เลือกเหตุ: 💥 อุบัติเหตุจราจร")),
                QuickReplyItem(action=MessageAction(label="🔥 ไฟไหม้", text="เลือกเหตุ: 🔥 ไฟไหม้ / หมอกควัน")),
                QuickReplyItem(action=MessageAction(label="📌 อื่นๆ", text="เลือกเหตุ: 📌 อื่นๆ"))
            ])
            reply_message = TextMessage(
                text=(f"📍 ตำแหน่งจุดเกิดเหตุ: {addr}\n"
                      f"📌 พิกัด: {lat:.4f}, {lon:.4f}\n\n"
                      f"กรุณาเลือก [ประเภทเหตุการณ์] ด้านล่างนี้ครับ 👇"),
                quick_reply=quick_reply
            )

    # =========================================================================
    # 📡 ผู้ใช้พิมพ์ขอเช็กสถานการณ์รอบตัว / เรดาร์ตรวจการณ์ (Radar SitRep)
    # =========================================================================
    elif user_text in ["เรดาร์ตรวจการณ์", "เรดาร์", "เรดาห์", "radar", "sitrep", "เช็กสถานการณ์รอบตัว", "สถานการณ์รอบตัว", "รอบตัว", "เหตุการณ์ใกล้เคียง", "เหตุใกล้เคียง", "สถานการณ์"]:
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=LocationAction(label="📍 ส่งตำแหน่งเพื่อเช็ก")),
            QuickReplyItem(action=MessageAction(label="👥 เช็กกำลังพล", text="เช็กกำลังพล")),
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุศูนย์ TOC", text="แจ้งเหตุ"))
        ])
        reply_message = TextMessage(
            text=("📡 ระบบเรดาร์ตรวจการณ์ & ประเมินสถานการณ์รอบตัว (Radar SitRep)\n"
                  "━━━━━━━━━━━━━━━━━━\n"
                  "👉 กรุณากดปุ่ม '📍 ส่งตำแหน่งเพื่อเช็ก' ด้านล่างนี้\n"
                  "เพื่อให้ระบบเรดาร์สแกนเหตุการณ์ฉุกเฉินและชุดปฏิบัติการในรัศมี 15 กม. รอบจุดตรวจการณ์ทันทีครับ"),
            quick_reply=quick_reply
        )

    # =========================================================================
    # 📱 ดาวน์โหลดแอปพลิเคชันมือถือ RTSD Tactical Tracker
    # =========================================================================
    elif user_text in ["ดาวน์โหลดแอป", "โหลดแอป", "แอปมือถือ", "ดาวน์โหลด", "app", "apk", "แอป", "ดาวน์โหลดแอป rtsd", "ดาวน์โหลดแอปมือถือ"]:
        reply_msg = (
            "📱 แอปพลิเคชัน RTSD Tactical Tracker\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "ระบบติดตามพิกัด GPS สด & เรดาร์ยุทธการ สำหรับเจ้าหน้าที่ภาคสนาม\n\n"
            "🎖️ สังกัด: กรมแผนที่ทหาร (RTSD)\n"
            "📦 เวอร์ชัน: v1.5 (Opt-in Line Tracking & Patrol Route Log)\n"
            "⚙️ ขนาดไฟล์: ~56 MB (Android 8.0+)\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📥 ลิงก์ดาวน์โหลดตรง (.APK):\n"
            "👉 https://linebot.goc-rtsd.com/download/app\n\n"
            "🛡️ คำแนะนำความปลอดภัย:\n"
            "แอปพลิเคชันนี้พัฒนาเพื่อภารกิจภายในกรมแผนที่ทหาร ปลอดภัย 100% ไม่มีมัลแวร์\n"
            "หากระบบ Android ขึ้นเตือน ให้กด 'ติดตั้งต่อไป (Install anyway)' ได้เลยครับ"
        )
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=URIAction(label="📥 ดาวน์โหลดไฟล์ APK", uri="https://linebot.goc-rtsd.com/download/app")),
            QuickReplyItem(action=URIAction(label="🗺️ แผนที่สถานการณ์", uri="https://linebot.goc-rtsd.com/map")),
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุฉุกเฉิน", text="แจ้งเหตุ"))
        ])
        reply_message = TextMessage(text=reply_msg, quick_reply=quick_reply)

    # =========================================================================
    # 🗺️ แผนที่สถานการณ์สดสำหรับมือถือ (Mobile Situational Map)
    # =========================================================================
    elif user_text in ["แผนที่", "แผนที่สถานการณ์", "แมพ", "map", "live map", "ดูแผนที่"]:
        reply_msg = (
            "🗺️ แผนที่สถานการณ์สด RTSD (Mobile Situational Map)\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "ระบบแสดงพิกัดเหตุการณ์ฉุกเฉินและจุดชุดปฏิบัติการที่กำลัง Active แบบเรียลไทม์\n"
            "ออกแบบสำหรับเปิดใช้งานบนสมาร์ตโฟนโดยเฉพาะ รวดเร็ว ไม่รกตา ใช้งานง่าย\n\n"
            "👉 แตะปุ่ม '🗺️ เปิดแผนที่สถานการณ์' ด้านล่างเพื่อเข้าดูได้ทันทีครับ:"
        )
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=URIAction(label="🗺️ เปิดแผนที่สถานการณ์", uri="https://linebot.goc-rtsd.com/map")),
            QuickReplyItem(action=URIAction(label="🖥️ แดชบอร์ดศูนย์ TOC (PC)", uri="https://linebot.goc-rtsd.com/dashboard")),
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุฉุกเฉิน", text="แจ้งเหตุ"))
        ])
        reply_message = TextMessage(text=reply_msg, quick_reply=quick_reply)

    # =========================================================================
    # ❓ คู่มือการใช้งานระบบฉบับย่อ (Quick User Guide)
    # =========================================================================
    elif user_text in ["คู่มือ", "คู่มือการใช้งาน", "วิธีใช้งาน", "วิธีใช้", "guide", "manual", "user guide"]:
        guide_text = (
            "📖 คู่มือการใช้งานระบบศูนย์บัญชาการสถานการณ์ RTSD\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "👥 สำหรับประชาชนผู้แจ้งเหตุ:\n"
            "1. แจ้งเหตุฉุกเฉิน: พิมพ์ 'แจ้งเหตุ' แล้วเลือกระดับความเร่งด่วนและประเภทเหตุ\n"
            "2. ปักหมุดพิกัด: กดแชร์ตำแหน่งที่ตั้งใน LINE เพื่อให้ทีมกู้ภัยเข้าช่วยเหลือตรงจุด\n"
            "3. ส่งภาพถ่ายหลักฐาน: ส่งรูปภาพหน้างานในแชตได้ทันที มี AI วิเคราะห์ประเภทอัตโนมัติ\n"
            "4. ติดตามสถานะ: ระบบจะส่งรหัสเหตุการณ์ เช่น #RTSD-1234 และแจ้งเตือนเมื่อมีเจ้าหน้าที่เข้าพื้นที่\n\n"
            "🪖 สำหรับเจ้าหน้าที่ภาคสนาม:\n"
            "• พิมพ์ 'กำลังเข้าพื้นที่ RTSD-xxxx' เพื่อรับงาน\n"
            "• พิมพ์ 'ถึงที่เกิดเหตุ RTSD-xxxx' เพื่อยืนยันการปฏิบัติการ\n"
            "• พิมพ์ 'เสร็จสิ้น RTSD-xxxx' เพื่อปิดงาน\n"
            "• ส่งภาพรายงานจากหน้างานเข้าระบบ Dashboard ได้ทันที\n\n"
            "🛰️ ศูนย์ควบคุม TOC & แอปพลิเคชัน:\n"
            "• แดชบอร์ดควบคุม (PC): https://linebot.goc-rtsd.com/dashboard\n"
            "• แผนที่มือถือสด: https://linebot.goc-rtsd.com/map\n"
            "• ดาวน์โหลดแอป Android: https://linebot.goc-rtsd.com/download/app"
        )
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=URIAction(label="🗺️ แผนที่สถานการณ์", uri="https://linebot.goc-rtsd.com/map")),
            QuickReplyItem(action=URIAction(label="🖥️ แดชบอร์ด TOC", uri="https://linebot.goc-rtsd.com/dashboard")),
            QuickReplyItem(action=URIAction(label="📲 ติดตั้งแอป APK", uri="https://linebot.goc-rtsd.com/download/app")),
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุเตือนภัย", text="แจ้งเหตุ")),
            QuickReplyItem(action=MessageAction(label="🆘 เบอร์ฉุกเฉิน", text="เบอร์ฉุกเฉิน"))
        ])
        reply_message = TextMessage(text=guide_text, quick_reply=quick_reply)

    # =========================================================================
    # 🆘 ระบบเบอร์ฉุกเฉิน & ข้อแนะนำรับมือภัยพิบัติ
    # =========================================================================
    elif user_text in ["ฉุกเฉิน", "เบอร์ฉุกเฉิน", "สายด่วน", "ช่วยเหลือ", "sos", "emergency", "จุดอพยพ", "เบอร์ติดต่อ", "ติดต่อฉุกเฉิน", "ขอความช่วยเหลือ"]:
        reply_msg = (
            "🆘 หมายเลขโทรศัพท์ฉุกเฉิน & ช่องทางช่วยเหลือ 24 ชม.\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📞 สายด่วนบรรเทาสาธารณภัย & กู้ชีพ:\n"
            "• 1784 : กรมป้องกันและบรรเทาสาธารณภัย (ปภ.)\n"
            "• 1669 : ศูนย์การแพทย์ฉุกเฉิน / กู้ชีพ\n"
            "• 191 : แจ้งเหตุด่วนเหตุร้าย (ตำรวจ)\n"
            "• 199 : ดับเพลิงและกู้ภัย\n"
            "• 1196 : อุบัติเหตุทางน้ำ\n"
            "• 1193 : ตำรวจทางหลวง\n"
            "• 02-221-2871 : ศูนย์ประสานงาน กรมแผนที่ทหาร (TOC)\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🌊 ข้อแนะนำการรับมือภัยพิบัติฉุกเฉิน:\n"
            "1. ตัดสะพานไฟทันทีเมื่อระดับน้ำท่วมถึงปลั๊กไฟ\n"
            "2. เคลื่อนย้ายเด็ก ผู้สูงอายุ และสัตว์เลี้ยงขึ้นที่ปลอดภัย\n"
            "3. หลีกเลี่ยงการเดินลุยน้ำเชี่ยวและเสาไฟฟ้าส่องสว่าง\n"
            "4. เตรียมกระเป๋าฉุกเฉิน (น้ำดื่ม ยาประจำตัว ไฟฉาย เอกสารสำคัญ)\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "👉 ท่านสามารถกดปุ่ม '📍 เช็กสถานการณ์รอบตัว' หรือ '🚨 แจ้งเหตุเตือนภัย' ด้านล่างได้ทันทีครับ"
        )
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=LocationAction(label="📍 เช็กสถานการณ์รอบตัว")),
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุเตือนภัย", text="แจ้งเหตุ")),
            QuickReplyItem(action=MessageAction(label="👥 เช็กกำลังพล", text="เช็กกำลังพล")),
            QuickReplyItem(action=MessageAction(label="🔍 ติดตามสถานะ", text="ติดตามสถานะ"))
        ])
        reply_message = TextMessage(text=reply_msg, quick_reply=quick_reply)

    # =========================================================================
    # 📦 จุดปลอดภัย & ศูนย์พักพิงชั่วคราว (Safe Zones & Shelters)
    # =========================================================================
    elif user_text in ["จุดปลอดภัย", "ศูนย์อพยพ", "จุดอพยพ", "ศูนย์พักพิง", "safe zones", "safe zone", "จุดปลอดภัย/ศูนย์อพยพ"]:
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=LocationAction(label="📍 ส่งตำแหน่งเพื่อหาจุดปลอดภัย")),
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุเตือนภัย", text="แจ้งเหตุ")),
            QuickReplyItem(action=MessageAction(label="🆘 สายด่วนกู้ภัย", text="เบอร์ฉุกเฉิน"))
        ])
        reply_message = TextMessage(
            text=("📦 ข้อมูลศูนย์พักพิงชั่วคราวและจุดปลอดภัย (Evacuation Safe Zones)\n"
                  "━━━━━━━━━━━━━━━━━━\n"
                  "👉 กรุณากดปุ่ม '📍 ส่งตำแหน่งเพื่อหาจุดปลอดภัย' ด้านล่าง\n"
                  "เพื่อให้ระบบแนะนำศูนย์พักพิงและจุดแจกจ่ายถุงยังชีพที่ใกล้ท่านที่สุดครับ\n\n"
                  "💡 ศูนย์ประสานงาน กรมแผนที่ทหาร (RTSD TOC) โทร: 02-221-2871"),
            quick_reply=quick_reply
        )

    # =========================================================================
    # 🛡️ เข้าสู่ระบบและยืนยันตัวตนเจ้าหน้าที่ (Staff Login / Link Account)
    # =========================================================================
    elif user_text in ["เข้าสู่ระบบเจ้าหน้าที่", "ยืนยันตัวตนเจ้าหน้าที่", "staff login", "ระบบเจ้าหน้าที่", "สำหรับเจ้าหน้าที่", "เมนูเจ้าหน้าที่", "เมนูยุทธการ"]:
        if user_info and user_info.get("status") == "อนุมัติแล้ว":
            # สลับเป็นริชเมนูเจ้าหน้าที่ 2A
            switch_user_rich_menu(user_id, user_info.get("role", "officer"))

            reply_msg = (f"🛡️ บัญชีเจ้าหน้าที่ของท่านได้รับการยืนยันแล้ว!\n"
                         f"━━━━━━━━━━━━━━━━━━\n"
                         f"👤 เจ้าหน้าที่: คุณ{user_info.get('name')}\n"
                         f"📱 เบอร์โทรศัพท์: {user_info.get('phone')}\n"
                         f"🔰 สิทธิ์การใช้งาน: {user_info.get('role')}\n"
                         f"🏢 สังกัดหน่วย: {user_info.get('unit', '-')}\n"
                         f"━━━━━━━━━━━━━━━━━━\n"
                         f"⚡ ระบบเปิดใช้งาน 'ริชเมนูยุทธการ (Tactical Command)' ให้ท่านแล้ว!\n"
                         f"👉 ท่านสามารถเข้าสู่ระบบ Dashboard War Room หรือสั่งการผ่านปุ่มด้านล่างได้ทันทีครับ")
            quick_reply = QuickReply(items=[
                QuickReplyItem(action=URIAction(label="🌐 แดชบอร์ด War Room", uri="https://linebot.goc-rtsd.com/dashboard")),
                QuickReplyItem(action=MessageAction(label="👥 เช็กกำลังพล", text="เช็กกำลังพล")),
                QuickReplyItem(action=MessageAction(label="⚡ อัปเดตงานสนาม", text="อัปเดตงานสนาม")),
                QuickReplyItem(action=MessageAction(label="🔄 สลับเมนูประชาชน", text="เมนูประชาชน"))
            ])
        else:
            reply_msg = ("🛡️ เข้าสู่ระบบและยืนยันตัวตนเจ้าหน้าที่ RTSD\n"
                         "━━━━━━━━━━━━━━━━━━\n"
                         "สำหรับเจ้าหน้าที่ออกปฏิบัติการ / ผู้บังคับบัญชา:\n\n"
                         "1️⃣ พิมพ์เบอร์โทรศัพท์ 10 หลัก เพื่อขอรับรหัส OTP หรือผูกบัญชี เช่น:\n"
                         "👉 0812345678\n\n"
                         "2️⃣ หรือลงทะเบียนข้อมูลยศ/สังกัด/ยานพาหนะ ได้ที่:\n"
                         "👉 https://linebot.goc-rtsd.com/register\n\n"
                         "💡 เมื่อผ่านการอนุมัติ ระบบจะปลดล็อกริชเมนูยุทธการให้อัตโนมัติครับ")
            quick_reply = QuickReply(items=[
                QuickReplyItem(action=URIAction(label="📝 ลงทะเบียนเจ้าหน้าที่", uri="https://linebot.goc-rtsd.com/register")),
                QuickReplyItem(action=MessageAction(label="👤 โปรไฟล์ของฉัน", text="โปรไฟล์")),
                QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุเตือนภัย", text="แจ้งเหตุ"))
            ])
        reply_message = TextMessage(text=reply_msg, quick_reply=quick_reply)

    # 🔄 สลับกลับไปใช้เมนูประชาชน (Switch to Citizen Menu)
    elif user_text in ["เมนูประชาชน", "สลับเมนูประชาชน", "เมนูทั่วไป", "ริชเมนูประชาชน"]:
        switch_user_rich_menu(user_id, "citizen")
        reply_message = TextMessage(
            text=("🔄 สลับกลับสู่ 'เมนูช่วยเหลือประชาชน (Citizen Mode)' เรียบร้อยแล้วครับ!\n"
                  "━━━━━━━━━━━━━━━━━━\n"
                  "หากต้องการกลับมาใช้เมนูยุทธการ ให้กดปุ่ม '🛡️ เข้าสู่ระบบเจ้าหน้าที่' ได้ทุกเมื่อครับ"),
            quick_reply=QuickReply(items=[
                QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุเตือนภัย", text="แจ้งเหตุ")),
                QuickReplyItem(action=MessageAction(label="🛡️ เมนูเจ้าหน้าที่", text="เข้าสู่ระบบเจ้าหน้าที่"))
            ])
        )

    # =========================================================================
    # ⚡ เมนูช่วยเหลืออัปเดตงานสนาม (Field Task Quick Helper)
    # =========================================================================
    elif user_text in ["อัปเดตงานสนาม", "รายงานภาคสนาม", "รายงานสนาม", "field update", "อัปเดตงาน"]:
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label="🚗 กำลังเข้าพื้นที่", text="กำลังเข้าพื้นที่")),
            QuickReplyItem(action=MessageAction(label="📍 ถึงที่เกิดเหตุ", text="ถึงที่เกิดเหตุ")),
            QuickReplyItem(action=MessageAction(label="🟢 เสร็จสิ้นภารกิจ", text="เสร็จสิ้น")),
            QuickReplyItem(action=MessageAction(label="🔍 ติดตามสถานะ", text="ติดตามสถานะ"))
        ])
        reply_message = TextMessage(
            text=("⚡ รายงานสถานะภารกิจภาคสนาม (Field Task Update)\n\n"
                  "เจ้าหน้าที่สามารถพิมพ์คำสั่งพร้อมรหัสเหตุ เช่น:\n"
                  "• 'กำลังเข้าพื้นที่ RTSD-1234'\n"
                  "• 'ถึงที่เกิดเหตุ RTSD-1234'\n"
                  "• 'เสร็จสิ้น RTSD-1234'\n\n"
                  "👉 หรือเลือกกดปุ่มสถานะด่วนด้านล่างนี้ได้เลยครับ 👇"),
            quick_reply=quick_reply
        )

    # =========================================================================
    # 🚨 แจ้งเหตุศูนย์ TOC (TOC Dispatch Help)
    # =========================================================================
    elif user_text in ["แจ้งเหตุศูนย์ toc", "แจ้งเหตุ toc", "toc dispatch", "แจ้งเหตุศูนย์"]:
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=URIAction(label="🚨 เปิดระบบแจ้งเหตุ TOC", uri="https://linebot.goc-rtsd.com/dashboard")),
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุผ่านแชต", text="แจ้งเหตุ")),
            QuickReplyItem(action=MessageAction(label="👥 เช็กกำลังพล", text="เช็กกำลังพล"))
        ])
        reply_message = TextMessage(
            text=("🚨 ระบบแจ้งเหตุและสั่งการศูนย์ควบคุม TOC (TOC Dispatch)\n"
                  "━━━━━━━━━━━━━━━━━━\n"
                  "ศูนย์ควบคุมสามารถเปิดใบสั่งการและปักหมุดเรดาร์ Tactical Pins ได้ที่:\n"
                  "👉 https://linebot.goc-rtsd.com/dashboard\n\n"
                  "หรือแจ้งเหตุฉุกเฉินด่วนผ่านแชต LINE ได้ทันทีครับ"),
            quick_reply=quick_reply
        )

    # =========================================================================
    # ⚡ ปรับปรุง/แก้ไขสถานะเหตุการณ์ภาคสนาม (สำหรับเจ้าหน้าที่สั่งผ่าน LINE Bot)
    # =========================================================================
    elif any(user_text.startswith(prefix) for prefix in [
        "ปรับสถานะ", "อัปเดตสถานะ", "อัปเดต", "แก้ไขสถานะ", 
        "เสร็จสิ้น", "เสร็จ", "เรียบร้อย", "ปิดเหตุ", 
        "กำลังเข้าพื้นที่", "เข้าพื้นที่", "กำลังเดินทาง", "เดินทาง", 
        "ถึงที่เกิดเหตุ", "ถึงจุดเกิดเหตุ", "ถึงแล้ว", "ยกเลิก"
    ]):
        id_match = re.search(r'rtsd-\d+', user_text, re.IGNORECASE)
        target_id = id_match.group(0).upper() if id_match else ""

        if target_id:
            raw_lower = user_text.lower()
            if any(w in raw_lower for w in ["เสร็จ", "เรียบร้อย", "ปิดเหตุ", "done", "close", "สำเร็จ"]):
                new_status = "🟢 แก้ไขแล้วเสร็จ"
            elif any(w in raw_lower for w in ["ถึงที่เกิดเหตุ", "ถึงจุดเกิดเหตุ", "ถึงแล้ว", "ถึงหน้างาน"]):
                new_status = "🟡 ถึงจุดเกิดเหตุ / กำลังปฏิบัติการ"
            elif any(w in raw_lower for w in ["เข้าพื้นที่", "เดินทาง", "en route", "enroute"]):
                new_status = "🟡 กำลังเดินทางเข้าพื้นที่"
            elif any(w in raw_lower for w in ["ยกเลิก", "ระงับ", "cancel"]):
                new_status = "❌ ยกเลิก/ระงับเหตุ"
            elif any(w in raw_lower for w in ["รอ", "wait", "pending"]):
                new_status = "⏳ รอดำเนินการ"
            else:
                new_status = "🟡 กำลังดำเนินการ"

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
                
                # ส่งแจ้งเตือนด่วนไปยังกลุ่มแอดมิน
                admin_notice = (
                    f"📢 [LINE Bot Update] มีการปรับสถานะเหตุการณ์ภาคสนาม!\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📌 รหัส: #{target_id}\n"
                    f"🔄 สถานะใหม่: {new_status}\n"
                    f"👤 ผู้ปรับ: {user_name}\n"
                    f"⏰ เวลา: {time.strftime('%H:%M:%S น.')}"
                )
                notify_admins(admin_notice)
            else:
                reply_msg = (f"⚠️ ไม่สามารถอัปเดตสถานะได้\n\n"
                             f"ไม่พบเหตุการณ์ที่มีรหัส '{target_id}' ในระบบ หรือ Google Sheets ยังไม่ตอบกลับ\n"
                             f"💡 กรุณาตรวจสอบรหัสเหตุการณ์อีกครั้งครับ")
        else:
            reply_msg = ("ℹ️ รูปแบบคำสั่งปรับสถานะเหตุการณ์ภาคสนาม:\n\n"
                         "👉 'กำลังเข้าพื้นที่ RTSD-1234'\n"
                         "👉 'ถึงที่เกิดเหตุ RTSD-1234'\n"
                         "👉 'เสร็จสิ้น RTSD-1234'\n"
                         "👉 'ปรับสถานะ RTSD-1234 กำลังดำเนินการ'\n\n"
                         "ตัวอย่าง: เสร็จสิ้น RTSD-0927")

        quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label=f"🔍 ติดตาม {target_id}" if target_id else "🔍 ติดตามสถานะ", text=f"ติดตาม {target_id}" if target_id else "ติดตามสถานะ")),
            QuickReplyItem(action=MessageAction(label="👥 เช็กกำลังพล", text="เช็กกำลังพล")),
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุใหม่", text="แจ้งเหตุ"))
        ])
        reply_message = TextMessage(text=reply_msg, quick_reply=quick_reply)
        
    # ด่านเปิดระบบ Live GPS Tracker สำหรับเจ้าหน้าที่
    elif user_text in ['แทร็กกิ้ง', 'แทร็ก', 'gps', 'พิกัดสด', 'tracking', 'แชร์พิกัดสด', 'ภารกิจ', 'แชร์ตำแหน่ง', 'ระบบแทร็กกิ่ง']:
        tracker_url = "https://linebot.goc-rtsd.com/tracker"
        portal_map_url = "https://geoportal.rtsd.mi.th/portal/home/webmap/viewer.html?url=https://geoportal.rtsd.mi.th/arcgis/rest/services/Hosted/rtsd_live_trackers/FeatureServer/0"
        reply_msg = (f"🛰️ ระบบติดตามกำลังพลภาคสนาม RTSD (Live GPS Tracker)\n"
                     f"━━━━━━━━━━━━━━━━━━\n"
                     f"สำหรับเจ้าหน้าที่ออกปฏิบัติการ กรมแผนที่ทหาร:\n\n"
                     f"1️⃣ เปิดระบบส่งพิกัดสดต่อเนื่อง (มีโหมดพรางหน้าจอ/ประหยัดแบตเตอรี่):\n"
                     f"👉 {tracker_url}\n\n"
                     f"2️⃣ เปิดดูแผนที่ติดตามกำลังพลบน Portal RTSD:\n"
                     f"👉 {portal_map_url}\n\n"
                     f"3️⃣ เปิดดู Tactical Dashboard ศูนย์ควบคุม:\n"
                     f"👉 https://linebot.goc-rtsd.com/dashboard\n"
                     f"━━━━━━━━━━━━━━━━━━\n"
                     f"💡 หรือกดปุ่ม '📍 ส่งตำแหน่งปัจจุบัน' ด้านล่างเพื่อเช็กอินจุดพิกัดทันทีได้เลยครับ")
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=LocationAction(label="📍 ส่งตำแหน่งปัจจุบัน")),
            QuickReplyItem(action=MessageAction(label="👥 เช็กกำลังพล", text="เช็กกำลังพล")),
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุเตือนภัย", text="แจ้งเหตุ")),
            QuickReplyItem(action=MessageAction(label="🔍 ติดตามสถานะ", text="ติดตามสถานะ"))
        ])
        reply_message = TextMessage(text=reply_msg, quick_reply=quick_reply)

    # เมนูแนะนำคำสั่งและการใช้งาน (Menu / Help)
    elif user_text in ['ช่วย', 'เมนู', 'menu', 'วิธีใช้', 'คำสั่ง', 'สวัสดี', 'hi', 'hello', 'เริ่มต้น', 'start']:
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label="🚨 แจ้งเหตุเตือนภัย", text="แจ้งเหตุ")),
            QuickReplyItem(action=LocationAction(label="📍 เช็กสถานการณ์รอบตัว")),
            QuickReplyItem(action=MessageAction(label="👥 เช็กกำลังพล", text="เช็กกำลังพล")),
            QuickReplyItem(action=MessageAction(label="🔍 ติดตามสถานะ", text="ติดตามสถานะ")),
            QuickReplyItem(action=MessageAction(label="🆘 เบอร์ฉุกเฉิน", text="เบอร์ฉุกเฉิน")),
            QuickReplyItem(action=MessageAction(label="🛰️ ส่งพิกัดสด GPS", text="แทร็กกิ้ง"))
        ])
        reply_message = TextMessage(
            text=(f"สวัสดีครับคุณ {user_name} 🛡️\n"
                  f"ยินดีต้อนรับสู่ศูนย์บัญชาการสถานการณ์ RTSD\n"
                  f"━━━━━━━━━━━━━━━━━━\n"
                  f"📱 คำสั่งด่วนที่สามารถพิมพ์สั่งงานได้:\n"
                  f"• 'แจ้งเหตุ' : แจ้งเหตุฉุกเฉินและภัยพิบัติ\n"
                  f"• แชร์พิกัด / กดปุ่มพิกัด : ตรวจสถานการณ์รอบตัว (SitRep)\n"
                  f"• 'เช็กกำลังพล' : ดูยอดหน่วยปฏิบัติการและเวร\n"
                  f"• 'ติดตาม' : ตรวจสอบสถานะการแก้ไขเหตุ\n"
                  f"• 'กำลังเข้าพื้นที่ RTSD-xxxx' : อัปเดตงานสนาม\n"
                  f"• 'เสร็จสิ้น RTSD-xxxx' : ปิดงานเหตุการณ์\n"
                  f"• 'เบอร์ฉุกเฉิน' : เบอร์ติดต่อกู้ภัย 24 ชม.\n"
                  f"━━━━━━━━━━━━━━━━━━\n"
                  f"👉 หรือเลือกกดปุ่มลัดด้านล่างนี้ได้เลยครับ 👇"),
            quick_reply=quick_reply
        )

    # ด่านที่ 1: ผู้ใช้เริ่มแจ้งเหตุ -> เด้ง Dropdown ให้เลือก "ประเภทเหตุการณ์"
    elif user_text in ['แจ้งเหตุ', 'แจ้งเตือน']:
        quick_reply = QuickReply(items=[
            QuickReplyItem(action=MessageAction(label="🌊 น้ำท่วมขัง", text="เลือกเหตุ: 🌊 น้ำท่วมขัง")),
            QuickReplyItem(action=MessageAction(label="🚧 ถนนชำรุด", text="เลือกเหตุ: 🚧 ดินถล่ม / ผิวทางชำรุด")),
            QuickReplyItem(action=MessageAction(label="💥 อุบัติเหตุ", text="เลือกเหตุ: 💥 อุบัติเหตุจราจร")),
            QuickReplyItem(action=MessageAction(label="🔥 ไฟไหม้", text="เลือกเหตุ: 🔥 ไฟไหม้ / หมอกควัน")),
            QuickReplyItem(action=MessageAction(label="📌 อื่นๆ", text="เลือกเหตุ: 📌 อื่นๆ")),
            QuickReplyItem(action=MessageAction(label="🔍 ติดตามสถานะ", text="ติดตามสถานะ"))
        ])
        reply_message = TextMessage(
            text=f"🚨 ขั้นตอนการแจ้งเหตุเตือนภัย\n\nกรุณาเลือก [ประเภทเหตุการณ์] ที่ท่านพบเห็นด้านล่างนี้ครับ 👇",
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
            QuickReplyItem(action=LocationAction(label="📍 เช็กสถานการณ์รอบตัว")),
            QuickReplyItem(action=MessageAction(label="👥 เช็กกำลังพล", text="เช็กกำลังพล")),
            QuickReplyItem(action=MessageAction(label="🔍 ติดตามสถานะ", text="ติดตามสถานะ")),
            QuickReplyItem(action=MessageAction(label="🆘 เบอร์ฉุกเฉิน", text="เบอร์ฉุกเฉิน"))
        ])
        reply_message = TextMessage(
            text=f"หากต้องการแจ้งเหตุ เช็กกำลังพล หรือสั่งการระบบ กรุณาเลือกปุ่มด้านล่างนี้ได้เลยครับ 👇",
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
