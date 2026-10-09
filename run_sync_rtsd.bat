@echo off
chcp 65001 > nul
title RTSD LINE-GIS Real-Time Sync Service
echo =========================================================================
echo    RTSD Real-Time Incident Sync Service (Google Sheets to Geoportal RTSD)
echo =========================================================================
echo.
echo กำลังเริ่มต้นบริการดึงข้อมูลเหตุการณ์และภาพถ่ายจาก LINE ขึ้นสู่ ArcGIS Geoportal RTSD...
set PYTHONUNBUFFERED=1
python -u sync_sheets_to_rtsd.py
pause
