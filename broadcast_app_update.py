"""
📡 RTSD Tactical Tracker - LINE Broadcast App Update Script
สคริปต์สำหรับส่งข้อความแจ้งเตือนเพื่อนและผู้ติดตามทุกคนใน LINE Official Account
ว่ามีการอัปเดตแอปพลิเคชันเวอร์ชันใหม่ พร้อมลิงก์ดาวน์โหลด APK
"""

import sys
import requests

CHANNEL_ACCESS_TOKEN = "tZwEj7/Os0MEb2g5oQMsZc6/8Uvt0AID8SVj/O5dyRkph1hgP8H3JSdduIh+SIXjQI1rPILjCx3ZVuG+WszETDZxOZZ2oXki4wCIF/kz26gjfE+iz8GCQtYCj4cbFLv3EQrOv/YrsWJ/VwMDns4f7gdB04t89/1O/w1cDnyilFU="

def broadcast_app_update(version="v1.4", changelog=None, download_url="https://linebot.goc-rtsd.com/download/app"):
    if not changelog:
        changelog = "อัปเดตโลโก้แอปพลิเคชัน 3D Tactical Emblem ใหม่ พร้อมไอคอนบนหน้าจอมือถือ และเพิ่มความเสถียรระบบ GPS"

    map_url = "https://linebot.goc-rtsd.com/map"
    dashboard_url = "https://linebot.goc-rtsd.com/dashboard"

    msg_text = (
        f"📲 [แจ้งอัปเดตแอปพลิเคชัน RTSD Tactical Tracker]\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🌟 เวอร์ชันใหม่ล่าสุด: {version}\n"
        f"🛡️ ระบบติดตามยุทธการและสนับสนุนภาคสนาม กรมแผนที่ทหาร\n\n"
        f"📋 ไฮไลต์การปรับปรุง:\n"
        f"• {changelog}\n"
        f"• รองรับการถ่ายภาพความคืบหน้าหน้างานส่งตรงเข้าศูนย์ TOC\n"
        f"• เพิ่มระบบส่งสัญญาณขอกำลังเสริมด่วน (SOS) พร้อมพิกัดสด\n"
        f"• เพิ่มความแม่นยำและการซูมตำแหน่งเหตุการณ์บนแผนที่\n\n"
        f"📥 ดาวน์โหลดและติดตั้งใหม่ได้ทันทีที่:\n"
        f"👉 {download_url}\n\n"
        f"💡 ข้อแนะนำ: สำหรับท่านที่ติดตั้งเวอร์ชันเดิมอยู่ สามารถกดดาวน์โหลดไฟล์ APK และกดติดตั้งทับ (Update) เพื่อเริ่มใช้งานต่อได้ทันทีครับ\n"
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

    payload = {
        "messages": [
            {
                "type": "text",
                "text": msg_text,
                "quickReply": quick_reply
            }
        ]
    }

    print(f"[*] Sending LINE Broadcast for version {version}...")
    try:
        res = requests.post("https://api.line.me/v2/bot/message/broadcast", headers=headers, json=payload, timeout=15)
        print(f"[*] Response Status: {res.status_code}")
        if res.status_code == 200:
            print("[+] SUCCESS: LINE Broadcast message sent to ALL followers successfully!")
            return True
        else:
            print(f"[-] FAILED: {res.text}")
            return False
    except Exception as e:
        print(f"[!] Error: {e}")
        return False

if __name__ == "__main__":
    version = sys.argv[1] if len(sys.argv) > 1 else "v1.3"
    broadcast_app_update(version=version)
