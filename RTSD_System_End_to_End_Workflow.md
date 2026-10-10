# 🗺️ แผนผังกระบวนการทำงานแบบครบวงจร (RTSD End-to-End System Workflow)
### ศูนย์ติดตามสถานการณ์ แจ้งเตือนภัย และแทร็กกิ้งพิกัดสด กรมแผนที่ทหาร

---

## 1. แผนภาพสถาปัตยกรรมระบบภาพรวม (High-Level Architecture)

ระบบประกอบด้วย **3 กลุ่มผู้ใช้งานหลัก** เชื่อมโยงผ่าน **Cloud Webhook API**, **คลาวด์จัดเก็บข้อมูล**, และ **ศูนย์บัญชาการแผนที่ทหาร**:

```mermaid
flowchart TD
    subgraph Users["👥 ผู้ใช้งานภาคสนาม & ประชาชน"]
        U1["📱 ผู้แจ้งเหตุ / ประชาชน<br>(LINE Official Account)"]
        U2["🚙 พลขับ / ชุดลาดตระเวน<br>(Tactical GPS Tracker Web)"]
        U3["👨‍💼 ผู้บังคับบัญชา / ศูนย์ควบคุม<br>(Command Dashboard)"]
    end

    subgraph CloudServer["☁️ Cloud Application (Render Webhook)"]
        App["⚙️ Flask Backend Server<br>(rtsd-linebot.onrender.com)"]
        Auth["🛡️ Identity & OTP Engine<br>(In-Memory Cache & Push API)"]
        Track["🛰️ Real-time GPS Telemetry Hub<br>(/api/tracker/update)"]
        StatAPI["📊 Incident & Status API<br>(/api/incident/update-status)"]
    end

    subgraph DataStorage["🗄️ คลังข้อมูลและสื่อบันทึก (Google Cloud)"]
        GS["📋 Google Sheets Database<br>(ตารางเหตุการณ์ 10 คอลัมน์ + ตาราง Users)"]
        GD["📁 Google Drive Folder<br>(RTSD_LINE_Media: รูปถ่าย & คลิป MP4)"]
    end

    subgraph RTSD_GIS["🏛️ เครือข่ายระบบภูมิสารสนเทศทหาร (ArcGIS Geoportal)"]
        SyncScript["🔄 RTSD Dual Sync Service<br>(sync_sheets_to_rtsd.py)"]
        L1["📍 Layer: incident_reports_template<br>(จุดแจ้งเหตุ + รูป Pop-up)"]
        L2["🚙 Layer: rtsd_live_trackers<br>(พิกัดรถยนต์ / กำลังพลสด)"]
        PortalMap["🗺️ RTSD Portal Web Map<br>(Auto-refresh 6s)"]
    end

    subgraph ExecutiveUI["📊 ศูนย์รายงานข้อมูลและสถิติ"]
        Dash["📈 Command Analytics Dashboard<br>(KPI Cards, 4 กราฟสถิติ, ตารางปรับสถานะ)"]
    end

    %% Flow connections
    U1 -->|1. ลงทะเบียนเบอร์โทร / แจ้งเหตุ + รูป| App
    U2 -->|2. ส่ง Telemetry พิกัด GPS ทุก 10s| App
    App -->|บันทึกพิกัด + อัปโหลดรูป/คลิป| GS
    App -->|บันทึกไฟล์ภาพ/คลิป| GD
    App -->|ส่ง OTP เข้าแชท LINE| U1
    
    SyncScript -->|ดึงข้อมูลเหตุการณ์ & สถานะ| GS
    SyncScript -->|ดึงพิกัดสดกำลังพล| App
    SyncScript -->|addFeatures / updateFeatures| L1
    SyncScript -->|addFeatures / updateFeatures| L2
    L1 & L2 --> PortalMap

    U3 -->|ล็อกอินด้วย OTP LINE หรือ Master PIN| Dash
    Dash -->|ดึงสถิติ / อัปเดตสถานะงาน| App
    App -->|อัปเดตแถวเดิม| GS
    Dash -->|คลิกดูรูปหลักฐานจาก Google Drive| GD
```

---

## 2. ลำดับขั้นตอนการทำงานแบบละเอียด (Step-by-Step Lifecycle)

---

### 🔹 ขั้นตอนที่ 1: การยืนยันตัวตนและการลงทะเบียน (Identity & OTP Flow)
> ทำเพียง **1 ครั้ง** ระบบจะจดจำตัวตนถาวร

```mermaid
sequenceDiagram
    autonumber
    actor Officer as เจ้าหน้าที่ / ประชาชน
    participant LineApp as LINE Bot (@411vtica)
    participant Backend as Render Webhook (app.py)
    participant Sheets as Google Sheets (Users Tab)
    actor Commander as ผู้ใช้งาน Dashboard

    Note over Officer,LineApp: การลงทะเบียนครั้งแรกใน LINE
    Officer->>LineApp: พิมพ์เบอร์โทรศัพท์ (เช่น "0812345678")
    LineApp->>Backend: Webhook MessageEvent (ตรวจจับเบอร์ 10 หลัก)
    Backend->>Sheets: บันทึก LINE_ID + ชื่อ + เบอร์โทร ลงแท็บ Users
    Backend-->>LineApp: แจ้ง "🎉 ลงทะเบียนยืนยันตัวตนสำเร็จ!"
    Note over Officer,LineApp: ระบบจดจำตัวตนตลอดไป

    Note over Commander,Backend: การล็อกอินเข้าหน้า Dashboard
    Commander->>Backend: กรอกเบอร์โทร ➔ กด [ขอ OTP]
    Backend->>Backend: สุ่ม OTP 6 หลัก (อายุ 5 นาที)
    Backend->>LineApp: PushMessage ยิงรหัส OTP เข้า LINE ของเบอร์นั้น
    LineApp-->>Officer: เด้งแจ้งเตือน: "🔐 รหัส OTP ของคุณคือ: [ 482915 ]"
    Commander->>Backend: นำรหัส 6 หลักมากรอกยืนยันตัวตน
    Backend-->>Commander: ✅ ออก Token และจดจำสิทธิ์ (Remember Me)
```

---

### 🔹 ขั้นตอนที่ 2: การแจ้งเหตุการณ์เตือนภัย (Incident Reporting & Media Flow)

```mermaid
sequenceDiagram
    autonumber
    actor Reporter as ผู้แจ้งเหตุ
    participant LineApp as LINE Bot
    participant Backend as Render Webhook (app.py)
    participant GAS as Google Apps Script (doPost)
    participant Drive as Google Drive (RTSD_LINE_Media)
    participant Sheet as Google Sheets (Incidents)

    Reporter->>LineApp: กดปุ่ม "🚨 แจ้งเตือนภัย"
    LineApp-->>Reporter: เมนูเลือกประเภท (น้ำท่วม, ถนนชำรุด, อุบัติเหตุ, ไฟไหม้)
    Reporter->>LineApp: เลือกประเภทเหตุการณ์ ➔ เลือกระดับความเร่งด่วน
    LineApp-->>Reporter: ขอพิกัด ("📍 แชร์พิกัด" หรือ พิมพ์ Lat, Lon)
    Reporter->>LineApp: ส่ง Location พิกัดสถานที่เกิดเหตุ
    LineApp-->>Reporter: ขอรูปถ่ายหรือคลิปวิดีโอหลักฐาน
    Reporter->>LineApp: ส่งรูปถ่าย / วิดีโอ (หรือกดข้าม)
    
    LineApp->>Backend: Webhook Image/Video Message
    Backend->>Backend: ดึงไบนารีไฟล์จาก LINE Blob API ➔ แปลงเป็น Base64
    Backend->>GAS: ยิง POST JSON (พิกัด, ชื่อ, เบอร์, รูป Base64)
    GAS->>Drive: สร้างไฟล์รูป/วิดีโอบนโฟลเดอร์ Google Drive
    Drive-->>GAS: ส่งลิงก์ไฟล์กลับมา (file_url)
    GAS->>Sheet: appendRow บันทึกข้อมูลครบ 10 คอลัมน์ (สถานะเริ่มต้น: ⏳ รอดำเนินการ)
    Backend-->>LineApp: ส่งข้อความตอบกลับยืนยัน: "✅ รหัสเหตุการณ์ #RTSD-XXXX"
```

---

### 🔹 ขั้นตอนที่ 3: ระบบแทร็กกิ้งพิกัดสดภาคสนาม (Tactical Live GPS Flow)

```mermaid
sequenceDiagram
    autonumber
    actor Patrol as ชุดลาดตระเวน / พลขับ
    participant TrackerUI as Mobile Web (/tracker)
    participant Backend as Render Webhook (app.py)
    participant Sync as RTSD Sync Script (Local)
    participant ArcGIS as Geoportal RTSD (rtsd_live_trackers)

    Patrol->>TrackerUI: เปิดหน้าเว็บ /tracker บนมือถือ
    Patrol->>TrackerUI: กรอกรหัสหน่วย (เช่น RTSD-01) ➔ กด [ 🟢 เริ่มส่งพิกัด GPS ]
    TrackerUI->>TrackerUI: ขอสิทธิ์เข้าถึง GPS ผ่านเบราว์เซอร์มือถือ
    
    loop ส่งสัญญาณ Telemetry ทุกๆ 10 วินาที
        TrackerUI->>Backend: POST /api/tracker/update (Lat, Lon, Speed, Heading, Battery)
        Backend->>Backend: เก็บพิกัดล่าสุดลง active_trackers Cache
    end

    loop ตรวจสอบและซิงค์ขึ้น Portal ทุก 30 วินาที
        Sync->>Backend: GET /api/tracker/units
        Backend-->>Sync: ส่งรายชื่อพิกัดสดของทุกหน่วย
        Sync->>ArcGIS: updateFeatures อัปเดตพิกัดและข้อมูล Telemetry บนเลเยอร์
        ArcGIS-->>ArcGIS: หมุดรถยนต์ขยับตามสดๆ บน Web Map (Refresh 6s)
    end
```

---

### 🔹 ขั้นตอนที่ 4: การซิงค์และแสดงผลบน ArcGIS Geoportal RTSD

```mermaid
flowchart LR
    subgraph InputData["แหล่งข้อมูลดิบ"]
        A["Google Sheets<br>(เหตุการณ์เตือนภัย)"]
        B["Render Server<br>(พิกัดสด Telemetry)"]
    end

    subgraph SyncEngine["ตัวขับเคลื่อนการเชื่อมต่อ (Sync Engine)"]
        S["run_sync_rtsd.bat<br>sync_sheets_to_rtsd.py<br>(รันอัตโนมัติทุก 30 วินาที)"]
    end

    subgraph RTSD_Layers["Hosted Feature Layers บน Portal"]
        L1["incident_reports_template<br>• พิกัดจุดแจ้งเหตุ<br>• ระดับความเร่งด่วน<br>• Direct Image Pop-up<br>• สถานะการดำเนินงาน"]
        L2["rtsd_live_trackers<br>• พิกัดรถยนต์ / กำลังพล<br>• ความเร็ว (km/h) & ทิศทาง<br>• ปริมาณแบตเตอรี่ (%)"]
    end

    A -->|อ่าน 10 คอลัมน์| S
    B -->|อ่าน JSON พิกัดสด| S
    S -->|แปลงลิงก์ Drive เป็น lh3 Direct Image| S
    S -->|addFeatures / updateFeatures| L1
    S -->|updateFeatures ขยับหมุด| L2
```

---

### 🔹 ขั้นตอนที่ 5: การควบคุมและปรับสถานะเหตุการณ์เดิม (Status Lifecycle Flow)

เมื่อเจ้าหน้าที่เข้าแก้ไขเหตุการณ์ สามารถสั่งเปลี่ยนสถานะได้จาก **Dashboard** หรือ **LINE Bot**:

```mermaid
sequenceDiagram
    autonumber
    actor Admin as เจ้าหน้าที่ศูนย์ / ภาคสนาม
    participant UI as Dashboard / LINE Bot
    participant Backend as Render Webhook (app.py)
    participant Sheet as Google Sheets
    participant Sync as sync_sheets_to_rtsd.py
    participant ArcGIS as Geoportal RTSD

    alt ปรับผ่านหน้า Dashboard
        Admin->>UI: เลือกเปลี่ยนสถานะในตาราง (เช่น จาก ⏳ เป็น 🟢 แก้ไขแล้วเสร็จ)
        UI->>Backend: POST /api/incident/update-status
    else ปรับผ่านห้องแชท LINE
        Admin->>UI: พิมพ์ "เสร็จสิ้น RTSD-1234" หรือกดปุ่ม Quick Reply
        UI->>Backend: Webhook รับคำสั่งปรับสถานะ
    end

    Backend->>Sheet: ส่ง action: "update_status" ➔ ค้นหาแถวเดิม ➔ เขียนทับ Column 9
    Backend-->>UI: ตอบกลับ "✅ ปรับสถานะสำเร็จ"
    UI->>UI: กราฟสถิติและตัวเลข KPI บน Dashboard ขยับคำนวณใหม่ทันที

    Note over Sync,ArcGIS: รอบการซิงค์ถัดไป (ภายใน 30 วินาที)
    Sync->>Sheet: ตรวจพบสถานะเปลี่ยนไปจากเดิม
    Sync->>ArcGIS: ส่งคำสั่ง updateFeatures ปรับฟิลด์ status ของหมุดบนแผนที่ทหารทันที
```

---

## 3. สรุปความสัมพันธ์ของทั้งระบบ (System Components Matrix)

| ส่วนประกอบ | หน้าที่หลัก | เทคโนโลยีที่ใช้ | ปลายทางการแสดงผล |
| :--- | :--- | :--- | :--- |
| **LINE Official Account** | ติดต่อประชาชน, รับแจ้งเหตุ, ยืนยันตัวตน, ส่ง OTP, รับคำสั่งปรับสถานะ | LINE Messaging API v3, Python Flask | มือถือของประชาชนและเจ้าหน้าที่ |
| **Tactical GPS Tracker** | ส่งพิกัดรถยนต์และกำลังพลสด (ความเร็ว, ทิศทาง, แบตเตอรี่) | HTML5 Geolocation API, Tailwind CSS | เบราว์เซอร์บนมือถือพลขับ |
| **Cloud Application Webhook** | ประมวลผลกลาง, ตรวจสอบ OTP, พักข้อมูล Telemetry | Flask, Gunicorn บน Render Cloud | `https://rtsd-linebot.onrender.com` |
| **Google Sheets & Drive** | ฐานข้อมูลหลัก 10 คอลัมน์ และคลังเก็บไฟล์รูป/วิดีโอ | Google Apps Script Web App | Google Sheets & Drive Folder |
| **RTSD Sync Service** | เชื่อมประสานข้อมูลระหว่าง Google Cloud และ ArcGIS Portal | Python REST API, Referer Token Auth | รันบนเครื่องคอมพิวเตอร์ศูนย์ควบคุม |
| **Geoportal RTSD** | แผนที่ภูมิสารสนเทศทหาร แสดงจุดเหตุการณ์และหมุดรถยนต์ขยับสด | ArcGIS Enterprise 10.61 Hosted Layers | Portal Map Viewer / Web AppBuilder |
| **Command Dashboard** | ศูนย์สถิติ, กราฟวิเคราะห์ KPI, ตารางควบคุมสถานะ, ยืนยันตัวตนด้วย OTP/PIN | Chart.js, Tailwind CSS, LocalStorage | หน้าจอ Executive Command Dashboard |
