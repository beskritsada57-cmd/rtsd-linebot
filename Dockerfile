# ============================================================
# 🐳 Dockerfile สำหรับ RTSD LINE Bot
# ใช้รันบน QNAP TS-264 ผ่าน Container Station
# ============================================================
FROM python:3.11-slim

# ตั้งค่า timezone เป็นเวลาไทย
ENV TZ=Asia/Bangkok
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

# ติดตั้ง dependencies ก่อน (ใช้ cache ของ Docker)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# คัดลอกไฟล์โปรเจกต์ทั้งหมด
COPY . .

# เปิด Port 5000
EXPOSE 5000

# รัน Flask ด้วย gunicorn (production-grade)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "120", "app:app"]
