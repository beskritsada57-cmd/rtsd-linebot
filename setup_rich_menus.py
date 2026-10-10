#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
สคริปต์สร้างและติดตั้ง LINE Rich Menu แบบ 4 ช่องบูรณาการ (Unified 4-Grid Menu - Style 2)
ระบบบูรณาการสำหรับประชาชนและเจ้าหน้าที่ RTSD (กรมแผนที่ทหาร)
- ช่อง 1 (ซ้ายบน): 🚨 แจ้งเหตุด่วนฉุกเฉิน -> ส่งข้อความ 'แจ้งเหตุ'
- ช่อง 2 (ขวาบน): 🗺️ แผนที่สถานการณ์ & กำลังพล -> เปิดลิงก์ Dashboard Live Map
- ช่อง 3 (ซ้ายล่าง): 📞 สายด่วนกู้ภัย & ศูนย์ TOC -> ส่งข้อความ 'เบอร์ฉุกเฉิน'
- ช่อง 4 (ขวาล่าง): 📱 ดาวน์โหลดแอป RTSD -> ส่งข้อความ 'ดาวน์โหลดแอป'
"""

import os
import sys
import json
import requests

if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

CHANNEL_ACCESS_TOKEN = os.environ.get(
    'CHANNEL_ACCESS_TOKEN',
    'tZwEj7/Os0MEb2g5oQMsZc6/8Uvt0AID8SVj/O5dyRkph1hgP8H3JSdduIh+SIXjQI1rPILjCx3ZVuG+WszETDZxOZZ2oXki4wCIF/kz26gjfE+iz8GCQtYCj4cbFLv3EQrOv/YrsWJ/VwMDns4f7gdB04t89/1O/w1cDnyilFU='
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 🎨 ริชเมนูบูรณาการ 4 ช่อง แบบ 2 (Modern Vibrant Cards)
IMAGE_PATH = os.path.join(BASE_DIR, 'richmenu_unified_option2_vibrant.jpg')

HEADERS_JSON = {
    'Authorization': f'Bearer {CHANNEL_ACCESS_TOKEN}',
    'Content-Type': 'application/json'
}

HEADERS_IMG = {
    'Authorization': f'Bearer {CHANNEL_ACCESS_TOKEN}',
    'Content-Type': 'image/jpeg'
}


def delete_existing_menus():
    """ค้นหาและลบริชเมนูเดิมที่ชื่อขึ้นต้นด้วย RTSD เพื่อความสะอาดเรียบร้อย"""
    print("[1/4] ตรวจสอบริชเมนูเดิมในระบบ...")
    try:
        r = requests.get('https://api.line.me/v2/bot/richmenu/list', headers=HEADERS_JSON, timeout=10)
        if r.status_code == 200:
            menus = r.json().get('richmenus', [])
            print(f"   พบเมนูเดิมทั้งหมด: {len(menus)} เมนู")
            for m in menus:
                name = m.get('name', '')
                menu_id = m.get('richMenuId', '')
                if 'RTSD' in name:
                    del_r = requests.delete(f'https://api.line.me/v2/bot/richmenu/{menu_id}', headers=HEADERS_JSON, timeout=10)
                    print(f"   - ลบเมนูเดิม: {name} ({menu_id}) -> Status: {del_r.status_code}")
        else:
            print(f"   [!] ดึงรายการเมนูล้มเหลว: {r.status_code} {r.text}")
    except Exception as e:
        print(f"   [!] เกิดข้อผิดพลาดในการตรวจสอบเมนูเดิม: {e}")


def create_unified_4grid_menu():
    """สร้างริชเมนูบูรณาการ 4 ช่อง (2x2 Grid)"""
    print("\n[2/4] สร้างริชเมนูบูรณาการ 4 ช่อง (Modern Vibrant Cards)...")
    payload = {
        "size": {"width": 2500, "height": 1686},
        "selected": True,
        "name": "RTSD_Unified_Menu_4Grid_V2",
        "chatBarText": "เมนูช่วยเหลือ",
        "areas": [
            # ช่อง 1 (ซ้ายบน): 🚨 แจ้งเหตุด่วนฉุกเฉิน
            {
                "bounds": {"x": 0, "y": 0, "width": 1250, "height": 843},
                "action": {"type": "message", "text": "แจ้งเหตุ"}
            },
            # ช่อง 2 (ขวาบน): 🗺️ แผนที่สถานการณ์ & กำลังพล
            {
                "bounds": {"x": 1250, "y": 0, "width": 1250, "height": 843},
                "action": {"type": "uri", "uri": "https://rtsd-linebot.onrender.com/dashboard"}
            },
            # ช่อง 3 (ซ้ายล่าง): 📞 สายด่วนกู้ภัย & ศูนย์ TOC 24 ชม.
            {
                "bounds": {"x": 0, "y": 843, "width": 1250, "height": 843},
                "action": {"type": "message", "text": "เบอร์ฉุกเฉิน"}
            },
            # ช่อง 4 (ขวาล่าง): 📱 ดาวน์โหลดแอป RTSD (ภาคสนาม)
            {
                "bounds": {"x": 1250, "y": 843, "width": 1250, "height": 843},
                "action": {"type": "message", "text": "ดาวน์โหลดแอป"}
            }
        ]
    }

    r = requests.post('https://api.line.me/v2/bot/richmenu', headers=HEADERS_JSON, json=payload, timeout=10)
    if r.status_code != 200:
        print(f"   [X] สร้างเมนูล้มเหลว: {r.status_code} {r.text}")
        return None

    menu_id = r.json().get('richMenuId')
    print(f"   ✅ สร้างเมนูสำเร็จ! ID: {menu_id}")

    # อัปโหลดรูปภาพ 4-Grid แบบ 2
    if not os.path.exists(IMAGE_PATH):
        print(f"   [X] ไม่พบไฟล์ภาพ: {IMAGE_PATH}")
        return menu_id

    with open(IMAGE_PATH, 'rb') as f:
        img_data = f.read()

    upload_url = f'https://api-data.line.me/v2/bot/richmenu/{menu_id}/content'
    r_up = requests.post(upload_url, headers=HEADERS_IMG, data=img_data, timeout=30)
    if r_up.status_code == 200:
        print(f"   ✅ อัปโหลดรูปภาพ 4 ช่องสำเร็จ ({len(img_data)} bytes)")
    else:
        print(f"   [X] อัปโหลดรูปภาพล้มเหลว: {r_up.status_code} {r_up.text}")

    # ตั้งเป็น Default Rich Menu สำหรับผู้ใช้ทุกคน
    def_url = f'https://api.line.me/v2/bot/user/all/richmenu/{menu_id}'
    r_def = requests.post(def_url, headers=HEADERS_JSON, timeout=10)
    if r_def.status_code == 200:
        print(f"   🌟 ตั้งเป็น Default Rich Menu สำหรับผู้ใช้ทุกคนเรียบร้อย!")
    else:
        print(f"   [!] ตั้ง Default ล้มเหลว: {r_def.status_code} {r_def.text}")

    return menu_id


def save_config(menu_id):
    """บันทึกรหัส Rich Menu ลงในไฟล์ config"""
    print("\n[3/4] บันทึกการกำหนดค่า...")
    config_data = {
        "UNIFIED_RICH_MENU_ID": menu_id,
        "STYLE": "Option 2 - Modern Vibrant Cards (4-Grid)"
    }
    config_path = os.path.join(BASE_DIR, 'richmenu_config.json')
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config_data, f, indent=2, ensure_ascii=False)
    print(f"   ✅ บันทึก config ที่: {config_path}")


def main():
    print("=" * 65)
    print("🚀 ระบบติดตั้ง LINE Rich Menu RTSD บูรณาการ 4 ช่อง (Style 2)")
    print("=" * 65)

    delete_existing_menus()
    menu_id = create_unified_4grid_menu()

    if menu_id:
        save_config(menu_id)
        print("\n" + "=" * 65)
        print("🎉 ติดตั้งริชเมนูบูรณาการ 4 ช่องสำเร็จสมบูรณ์ 100%!")
        print("=" * 65)
        print(f"📌 UNIFIED_RICH_MENU_ID: {menu_id}")
        print("🌟 สถานะ: ใช้งานเป็น Default Menu ให้ทุกคนเรียบร้อยแล้ว!")
        print("=" * 65)
    else:
        print("\n[!] เกิดข้อผิดพลาดในการติดตั้งริชเมนู")


if __name__ == '__main__':
    main()
