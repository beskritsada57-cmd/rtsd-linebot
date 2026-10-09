import os
import sys
import io
import time
import re
import json
import requests
import urllib3

# ตั้งค่า stdout รองรับภาษาไทย UTF-8 บน Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =========================================================================
# การตั้งค่าการเชื่อมต่อ Geoportal RTSD และ Google Sheets
# =========================================================================
PORTAL_URL = "https://geoportal.rtsd.mi.th/portal"
PORTAL_USER = "goc01@2024"
PORTAL_PASS = "GEOINT@rtsd_09_69"
TARGET_LAYER_URL = "https://geoportal.rtsd.mi.th/arcgis/rest/services/Hosted/incident_reports_template/FeatureServer/0"
TRACKER_LAYER_URL = "https://geoportal.rtsd.mi.th/arcgis/rest/services/Hosted/rtsd_live_trackers/FeatureServer/0"

# Web App URL ของ Google Sheets และ API สำหรับ Trackers
GOOGLE_SHEET_URL = "https://script.google.com/macros/s/AKfycbw5Gepu7a5s9j1vXtgE55403L0K3sKtOcpUNArNCm6RJO1ulNp735XyZgAbTlMBwxI/exec"
TRACKER_UNITS_URL = "https://rtsd-linebot.onrender.com/api/tracker/units"

# ไฟล์บันทึกประวัติแถวที่ Sync ไปแล้ว เพื่อไม่ให้ปักหมุดซ้ำ
SYNC_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "synced_reports.json")


def load_synced_history():
    """โหลดรายการ Timestamp ที่เคยซิงค์ขึ้น RTSD แล้ว"""
    if os.path.exists(SYNC_HISTORY_FILE):
        try:
            with open(SYNC_HISTORY_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_synced_history(history_set):
    """บันทึกรายการ Timestamp ที่ซิงค์แล้วลงไฟล์"""
    try:
        with open(SYNC_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(list(history_set), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ ไม่สามารถบันทึกประวัติการ Sync: {e}")


def format_direct_image_url(url):
    """แปลงลิงก์ Google Drive ให้เป็น Direct Image URL เพื่อให้ Pop-up ของ ArcGIS โชว์รูปได้ทันที"""
    if not url or url == "-" or url.strip() == "":
        return "-"
    
    url = url.strip()
    
    # หากเป็นลิงก์โฟลเดอร์ ให้ข้ามไปเพราะไม่ใช่ไฟล์รูป
    if "/drive/folders/" in url:
        return url
        
    # หากเป็นลิงก์ไฟล์ Google Drive ทั่วไป -> แปลงเป็น lh3 direct image
    match = re.search(r'/file/d/([a-zA-Z0-9_-]+)', url)
    if match:
        file_id = match.group(1)
        return f"https://lh3.googleusercontent.com/d/{file_id}"
        
    return url


def get_arcgis_token():
    """ขอ Token เข้าใช้งาน Geoportal RTSD"""
    token_url = f"{PORTAL_URL.rstrip('/')}/sharing/rest/generateToken"
    try:
        res = requests.post(token_url, data={
            'username': PORTAL_USER,
            'password': PORTAL_PASS,
            'client': 'referer',
            'referer': PORTAL_URL,
            'expiration': 60,
            'f': 'json'
        }, verify=False, timeout=15)
        return res.json().get('token')
    except Exception as e:
        print(f"❌ ขอ Token RTSD ไม่สำเร็จ: {e}")
        return None


def fetch_sheets_data():
    """ดึงข้อมูลแถวทั้งหมดจาก Google Sheets"""
    try:
        res = requests.get(GOOGLE_SHEET_URL, timeout=20)
        if res.status_code == 200:
            return res.json()
        else:
            print(f"⚠️ Google Sheets ตอบกลับด้วยรหัส: {res.status_code}")
            return []
    except Exception as e:
        print(f"❌ ดึงข้อมูลจาก Google Sheets ไม่สำเร็จ: {e}")
        return []


def push_features_to_rtsd(features_list, token):
    """ส่งข้อมูลเหตุการณ์ใหม่ขึ้น Geoportal RTSD Feature Layer"""
    if not features_list:
        return True
        
    url = f"{TARGET_LAYER_URL}/addFeatures"
    headers = {'referer': PORTAL_URL}
    try:
        res = requests.post(url, data={
            'features': json.dumps(features_list),
            'token': token,
            'f': 'json'
        }, headers=headers, verify=False, timeout=20)
        
        result = res.json()
        adds = result.get('addResults', [])
        success_count = sum(1 for a in adds if a.get('success'))
        print(f"✅ ปักหมุดลง Geoportal RTSD สำเร็จ {success_count}/{len(features_list)} รายการ!")
        return success_count > 0
    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาดในการปักหมุดขึ้น RTSD: {e}")
        return False


def update_features_on_rtsd(features_list, token):
    """ส่งคำสั่ง updateFeatures ไปยัง Geoportal RTSD เพื่ออัปเดตสถานะของเหตุการณ์เดิม"""
    if not features_list:
        return True
        
    url = f"{TARGET_LAYER_URL}/updateFeatures"
    headers = {'referer': PORTAL_URL}
    try:
        res = requests.post(url, data={
            'features': json.dumps(features_list),
            'token': token,
            'f': 'json'
        }, headers=headers, verify=False, timeout=20)
        
        result = res.json()
        updates = result.get('updateResults', [])
        success_count = sum(1 for u in updates if u.get('success'))
        if success_count > 0:
            print(f"🔄 อัปเดตสถานะบน Geoportal RTSD สำเร็จ {success_count}/{len(features_list)} รายการ!")
        return success_count > 0
    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาดในการอัปเดตสถานะบน RTSD: {e}")
        return False


def sync_once(synced_history):
    """ทำการตรวจสอบ 1 รอบ: ปักหมุดแถวใหม่ และอัปเดตสถานะแถวเดิมที่เปลี่ยนไป"""
    rows = fetch_sheets_data()
    if not rows or len(rows) <= 1:
        return synced_history

    token = get_arcgis_token()
    if not token:
        return synced_history

    headers = {'referer': PORTAL_URL}
    existing_map = {}
    try:
        q_res = requests.get(f"{TARGET_LAYER_URL}/query", params={
            'where': '1=1',
            'outFields': 'objectid,title,status,report_time',
            'f': 'json',
            'token': token
        }, headers=headers, verify=False, timeout=15)
        for feat in q_res.json().get('features', []):
            attrs = feat.get('attributes', {})
            oid = attrs.get('objectid')
            r_time = (attrs.get('report_time') or '').strip()
            r_title = (attrs.get('title') or '').strip()
            r_status = (attrs.get('status') or '').strip()
            if r_time:
                existing_map[r_time] = (oid, r_status)
            if r_title:
                existing_map[r_title] = (oid, r_status)
    except Exception as e:
        pass

    data_rows = rows[1:]
    new_features = []
    update_features = []

    for row in data_rows:
        if len(row) < 10:
            continue
            
        timestamp = str(row[0]).strip()
        title = str(row[1]).strip()
        address = str(row[2]).strip()
        
        try:
            lat = float(row[3])
            lon = float(row[4])
        except (ValueError, TypeError):
            continue

        reporter = str(row[5]).strip()
        urgency = str(row[6]).strip()
        incident_type = str(row[7]).strip()
        status = str(row[8]).strip() or "⏳ รอดำเนินการ"
        raw_media_url = str(row[9]).strip()

        # ตรวจสอบพิกัด
        if lat == 0 or lon == 0 or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue

        # ตรวจสอบว่ามีอยู่แล้วบน ArcGIS หรือไม่
        match_info = existing_map.get(timestamp) or existing_map.get(title)
        
        if match_info:
            oid, curr_gis_status = match_info
            # ถ้าสถานะใน Google Sheets ไม่ตรงกับบน ArcGIS -> อัปเดตทันที
            if status != curr_gis_status:
                update_features.append({
                    "attributes": {
                        "objectid": oid,
                        "status": status[:50]
                    }
                })
        else:
            # รายการใหม่ ปักหมุดเพิ่ม
            media_url = format_direct_image_url(raw_media_url)
            id_match = re.search(r'\[(RTSD-\d+)\]', title)
            report_id = id_match.group(1) if id_match else "-"

            feature = {
                "geometry": {
                    "x": float(lon),
                    "y": float(lat),
                    "spatialReference": {"wkid": 4326}
                },
                "attributes": {
                    "title": title[:250],
                    "address": address[:250],
                    "reporter": reporter[:100],
                    "urgency": urgency[:100],
                    "incident_type": incident_type[:100],
                    "media_url": media_url[:500],
                    "status": status[:50],
                    "report_time": timestamp[:50],
                    "report_id": report_id[:50]
                }
            }
            new_features.append(feature)

    if new_features:
        print(f"\n🔔 พบข้อมูลเหตุการณ์ใหม่ {len(new_features)} รายการ กำลังปักหมุดขึ้น ArcGIS RTSD...")
        push_features_to_rtsd(new_features, token)

    if update_features:
        print(f"\n🔄 พบสถานะเหตุการณ์เปลี่ยนแปลง {len(update_features)} รายการ กำลังอัปเดตบน ArcGIS RTSD...")
        update_features_on_rtsd(update_features, token)

    return synced_history


# บันทึกประวัติ Timestamp ล่าสุดของแต่ละหน่วย เพื่อไม่ให้ส่งจุดซ้ำซ้อน
last_synced_tracker_pings = {}

try:
    import mgrs
    _mgrs_converter = mgrs.MGRS()
except Exception:
    _mgrs_converter = None

def get_mgrs_formatted(lat, lon):
    if not _mgrs_converter or not lat or not lon:
        return ""
    try:
        raw = _mgrs_converter.toMGRS(lat, lon)
        if len(raw) >= 13:
            return f"{raw[:3]} {raw[3:5]} {raw[5:10]} {raw[10:]}"
        return raw
    except Exception:
        return ""

def sync_live_trackers_to_rtsd(token):
    """ดึงพิกัดสดของหน่วยกำลังพลจากเซิร์ฟเวอร์ แล้วบันทึกลง Geoportal RTSD แบบ Log Track เส้นทางประวัติการเคลื่อนที่ พร้อมอัปเดตจุดล่าสุด (is_latest) และ MGRS"""
    global last_synced_tracker_pings
    try:
        res = requests.get(TRACKER_UNITS_URL, timeout=10)
        if res.status_code != 200:
            return
        units = res.json()
        if not units:
            return

        headers = {'referer': PORTAL_URL}
        adds = []
        units_to_demote = []

        for u in units:
            unit_id = str(u.get('unit_id', 'UNIT-01')).strip()
            lat = float(u.get('latitude', 0))
            lon = float(u.get('longitude', 0))
            last_update = str(u.get('last_update', ''))[:50]

            if lat == 0 or lon == 0:
                continue

            # ตรวจสอบว่าพิกัดนี้เป็นสัญญาณใหม่หรือไม่ (ป้องกันการปักหมุดซ้ำซ้อนขณะยังไม่มีสัญญาณใหม่)
            prev_update = last_synced_tracker_pings.get(unit_id)
            if prev_update == last_update:
                continue

            feature_geom = {
                "x": lon,
                "y": lat,
                "spatialReference": {"wkid": 4326}
            }
            pic_url = format_direct_image_url(str(u.get('picture_profile', '')))
            feature_attrs = {
                "unit_id": unit_id[:50],
                "unit_name": str(u.get('unit_name', ''))[:100],
                "commander": str(u.get('commander', ''))[:100],
                "speed": float(u.get('speed', 0)),
                "heading": float(u.get('heading', 0)),
                "battery": int(u.get('battery', 100)),
                "status": str(u.get('status', '🟢 กำลังปฏิบัติภารกิจ'))[:50],
                "picture_profile": pic_url[:500] if pic_url and pic_url != '-' else '',
                "last_update": last_update,
                "is_latest": 1,
                "mgrs": get_mgrs_formatted(lat, lon)
            }

            adds.append({"geometry": feature_geom, "attributes": feature_attrs})
            units_to_demote.append(unit_id)
            last_synced_tracker_pings[unit_id] = last_update

        # ถ้ามีจุดใหม่เข้ามา ให้ปรับจุดเดิมของหน่วยนั้นเป็น is_latest = 0 ก่อน
        if units_to_demote:
            for uid in set(units_to_demote):
                try:
                    q_res = requests.get(f"{TRACKER_LAYER_URL}/query", params={
                        "where": f"unit_id = '{uid}' AND is_latest = 1",
                        "outFields": "objectid",
                        "f": "json",
                        "token": token
                    }, headers=headers, verify=False, timeout=10).json()
                    old_feats = q_res.get("features", [])
                    if old_feats:
                        demotes = [{"attributes": {"objectid": f["attributes"]["objectid"], "is_latest": 0}} for f in old_feats]
                        requests.post(f"{TRACKER_LAYER_URL}/updateFeatures", data={
                            'features': json.dumps(demotes), 'token': token, 'f': 'json'
                        }, headers=headers, verify=False, timeout=10)
                except Exception:
                    pass

        # บันทึกจุดพิกัดใหม่เพิ่มลงในเลเยอร์เสมอ เพื่อสร้าง Log Track เส้นทางการเคลื่อนที่
        if adds:
            r = requests.post(f"{TRACKER_LAYER_URL}/addFeatures", data={
                'features': json.dumps(adds), 'token': token, 'f': 'json'
            }, headers=headers, verify=False, timeout=15)
            print(f"\n🛰️ บันทึกพิกัดใหม่บน RTSD สำเร็จ {len(adds)} จุด (ปรับสถานะจุดล่าสุดอัตโนมัติ)")

    except Exception as e:
        # ไม่แสดง error ถ้าเซิร์ฟเวอร์ยังไม่มี tracker เชื่อมต่อ
        pass


def main():
    print("=" * 65)
    print("🚀 ระบบอัตโนมัติ: เชื่อมโยงข้อมูล LINE Bot ➔ Google Sheets ➔ Geoportal RTSD")
    print("=" * 65)
    print(f"📡 ติดตามตาราง: {GOOGLE_SHEET_URL[:55]}...")
    print(f"🗺️ ปลายทางเหตุการณ์: {TARGET_LAYER_URL}")
    print(f"🛰️ ปลายทางพิกัดสด: {TRACKER_LAYER_URL}")
    print("🔄 ตรวจสอบข้อมูลใหม่ทุกๆ 30 วินาที (กด Ctrl + C เพื่อหยุดการทำงาน)\n")

    synced_history = load_synced_history()
    print(f"ℹ️ ประวัติแถวที่เคยซิงค์แล้วในระบบ: {len(synced_history)} รายการ")

    while True:
        try:
            curr_time = time.strftime("%H:%M:%S")
            print(f"[{curr_time}] 🔍 กำลังตรวจสอบข้อมูลใหม่จาก Sheets & GPS สด...", end="\r", flush=True)
            
            token = get_arcgis_token()
            if token:
                # 1. ซิงค์เหตุการณ์จาก Google Sheets
                synced_history = sync_once(synced_history)
                # 2. ซิงค์พิกัดสดของหน่วยกำลังพล / รถยนต์
                sync_live_trackers_to_rtsd(token)
                
            time.sleep(30)
        except KeyboardInterrupt:
            print("\n👋 หยุดการทำงานระบบ Sync เรียบร้อยแล้วครับ")
            break
        except Exception as e:
            print(f"\n⚠️ เกิดข้อผิดพลาดในรอบการทำงาน: {e}")
            time.sleep(10)


if __name__ == "__main__":
    main()
