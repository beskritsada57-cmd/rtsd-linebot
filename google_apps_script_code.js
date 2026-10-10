/**
 * Google Apps Script สำหรับ RTSD Incident & Disaster Command System
 * ความสามารถ:
 * 1. doGet: ส่งข้อมูลเหตุการณ์ หรือผู้ใช้งาน (เมื่อใส่ ?sheet=users)
 * 2. doPost (action: "register_user"): บันทึกข้อมูลผู้ใช้ใหม่ (LINE ID, ชื่อ, เบอร์โทร, บทบาท) ลงแท็บ "Users"
 * 3. doPost (action: "get_users"): ดึงรายชื่อผู้ใช้งานทั้งหมดเพื่อตรวจสอบ OTP และสิทธิ์
 * 4. doPost (action: "update_status"): ค้นหาแถวเหตุการณ์เดิม แล้วแก้ไขช่องสถานะ (Column I)
 * 5. doPost (ใหม่): รับข้อมูลเหตุการณ์ + ภาพถ่าย/คลิป บันทึกลง Google Drive และเพิ่มแถวใหม่ใน Sheets
 */

function doGet(e) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sheetParam = (e && e.parameter && e.parameter.sheet) ? e.parameter.sheet.toLowerCase() : "";
  
  if (sheetParam === "users") {
    var userSheet = ss.getSheetByName("Users");
    if (!userSheet) {
      return ContentService.createTextOutput(JSON.stringify([])).setMimeType(ContentService.MimeType.JSON);
    }
    var uData = userSheet.getDataRange().getValues();
    return ContentService.createTextOutput(JSON.stringify(uData)).setMimeType(ContentService.MimeType.JSON);
  }
  
  // ปกติ ส่งข้อมูลเหตุการณ์ทั้งหมดออกเป็น JSON
  var sheet = ss.getActiveSheet();
  var data = sheet.getDataRange().getValues();
  return ContentService.createTextOutput(JSON.stringify(data)).setMimeType(ContentService.MimeType.JSON);
}

function doPost(e) {
  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var sheet = ss.getActiveSheet();
    var data = JSON.parse(e.postData.contents);
    
    // =========================================================================
    // 1. ลงทะเบียนผู้ใช้งาน (action: "register_user")
    // =========================================================================
    if (data.action === "register_user") {
      var userSheet = ss.getSheetByName("Users");
      if (!userSheet) {
        userSheet = ss.insertSheet("Users");
        userSheet.appendRow([
          "registered_at", "line_user_id", "full_name", "phone_number", "role", 
          "unit", "position", "approval_status", "purpose", "picture_profile"
        ]);
      } else {
        var lastCol = userSheet.getLastColumn();
        if (lastCol < 9) {
          userSheet.getRange(1, 6).setValue("unit");
          userSheet.getRange(1, 7).setValue("position");
          userSheet.getRange(1, 8).setValue("approval_status");
          userSheet.getRange(1, 9).setValue("purpose");
        }
        if (lastCol < 10) {
          userSheet.getRange(1, 10).setValue("picture_profile");
        }
      }
      
      var lineId = (data.line_user_id || "").toString().trim();
      var name = (data.full_name || data.name || "").toString().trim();
      var phone = (data.phone_number || data.phone || "").toString().trim();
      var role = (data.role || "ผู้ใช้งานทั่วไป").toString().trim();
      var unit = (data.unit || "-").toString().trim();
      var position = (data.position || "-").toString().trim();
      var status = (data.approval_status || "อนุมัติแล้ว").toString().trim();
      var purpose = (data.purpose || "-").toString().trim();
      var pic = (data.picture_profile || data.picture_url || "-").toString().trim();

      // หากมีการอัปโหลดไฟล์ภาพ Base64 จาก Gallery
      if (data.picture_base64) {
        try {
          var FOLDER_ID = "1Hm76KnhLEE7CecGTDkXtEUJBasMbAqMn";
          var folder = DriveApp.getFolderById(FOLDER_ID);
          var decoded = Utilities.base64Decode(data.picture_base64);
          var fileName = "PROFILE_" + (phone || "USER") + "_" + (new Date().getTime()) + ".jpg";
          var blob = Utilities.newBlob(decoded, data.mime_type || "image/jpeg", fileName);
          var file = folder.createFile(blob);
          file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
          pic = file.getUrl();
        } catch(driveErr) {
          // หากบันทึกลง Drive ไม่สำเร็จ ให้คงค่าเดิม
        }
      }
      var now = new Date();
      
      var uValues = userSheet.getDataRange().getValues();
      var updated = false;
      for (var i = 1; i < uValues.length; i++) {
        var rowLineId = (uValues[i][1] || "").toString().trim();
        var rowPhone = (uValues[i][3] || "").toString().trim();
        
        if ((lineId && rowLineId === lineId) || (phone && rowPhone === phone)) {
          if (name) userSheet.getRange(i + 1, 3).setValue(name);
          if (phone) userSheet.getRange(i + 1, 4).setValue(phone);
          if (role) userSheet.getRange(i + 1, 5).setValue(role);
          if (unit) userSheet.getRange(i + 1, 6).setValue(unit);
          if (position) userSheet.getRange(i + 1, 7).setValue(position);
          if (status) userSheet.getRange(i + 1, 8).setValue(status);
          if (purpose) userSheet.getRange(i + 1, 9).setValue(purpose);
          if (pic && pic !== "-") userSheet.getRange(i + 1, 10).setValue(pic);
          updated = true;
          break;
        }
      }
      if (!updated) {
        userSheet.appendRow([now, lineId, name, phone, role, unit, position, status, purpose, pic]);
      }
      
      return ContentService.createTextOutput(JSON.stringify({
        status: "success",
        line_user_id: lineId,
        full_name: name,
        phone_number: phone,
        role: role,
        unit: unit,
        position: position,
        approval_status: status,
        picture_profile: pic
      })).setMimeType(ContentService.MimeType.JSON);
    }
    
    // =========================================================================
    // 2. ดึงรายชื่อผู้ใช้ที่ลงทะเบียนแล้ว (action: "get_users")
    // =========================================================================
    if (data.action === "get_users") {
      var userSheet = ss.getSheetByName("Users");
      if (!userSheet) {
        return ContentService.createTextOutput(JSON.stringify([])).setMimeType(ContentService.MimeType.JSON);
      }
      var rows = userSheet.getDataRange().getValues();
      var users = [];
      for (var u = 1; u < rows.length; u++) {
        if (rows[u].length >= 4) {
          users.push({
            registered_at: rows[u][0],
            line_user_id: rows[u][1],
            full_name: rows[u][2],
            phone_number: rows[u][3],
            role: rows[u][4] || "ผู้ใช้งานทั่วไป",
            unit: rows[u][5] || "-",
            position: rows[u][6] || "-",
            approval_status: rows[u][7] || "อนุมัติแล้ว",
            purpose: rows[u][8] || "-",
            picture_profile: rows[u][9] || "-"
          });
        }
      }
      return ContentService.createTextOutput(JSON.stringify(users)).setMimeType(ContentService.MimeType.JSON);
    }

    // =========================================================================
    // 3. กรณีสั่งอัปเดตสถานะของเหตุการณ์เดิม (action: "update_status")
    // =========================================================================
    if (data.action === "update_status") {
      var searchTitle = (data.title || "").toString().toLowerCase().trim();
      var searchTime = (data.timestamp || "").toString().toLowerCase().trim();
      var newStatus = data.status || "🟢 แก้ไขแล้วเสร็จ";
      
      var values = sheet.getDataRange().getValues();
      var foundRow = -1;
      
      for (var r = values.length - 1; r >= 1; r--) {
        var rowTime = (values[r][0] || "").toString().toLowerCase();
        var rowTitle = (values[r][1] || "").toString().toLowerCase();
        
        var match = false;
        if (searchTitle && searchTitle !== "-" && rowTitle.indexOf(searchTitle) !== -1) {
          match = true;
        } else if (searchTime && searchTime !== "-" && rowTime.indexOf(searchTime) !== -1) {
          match = true;
        }
        
        if (match) {
          foundRow = r + 1;
          break;
        }
      }
      
      if (foundRow !== -1) {
        sheet.getRange(foundRow, 9).setValue(newStatus);
        return ContentService.createTextOutput(JSON.stringify({
          status: "success",
          updated_row: foundRow,
          new_status: newStatus
        })).setMimeType(ContentService.MimeType.JSON);
      } else {
        return ContentService.createTextOutput(JSON.stringify({
          status: "not_found",
          message: "ไม่พบเหตุการณ์ที่ตรงกับเงื่อนไข"
        })).setMimeType(ContentService.MimeType.JSON);
      }
    }
    
    // =========================================================================
    // 4. กรณีบันทึกการแจ้งเหตุใหม่ (เพิ่มแถวใหม่ต่อท้าย)
    // =========================================================================
    // ป้องกัน Ghost Record: ต้องมีข้อมูลพิกัดจริง หรือหัวข้อเหตุการณ์ หรือไฟล์แนบ หรือ action: "add_incident" เท่านั้น
    var hasIncidentData = data.action === "add_incident" || 
                          (data.latitude && data.latitude !== 0) || 
                          (data.title && data.title !== "จุดแจ้งเหตุ") ||
                          (data.file_base64 && data.file_name);

    if (!hasIncidentData) {
      return ContentService.createTextOutput(JSON.stringify({ 
        "status": "ignored", 
        "message": "ไม่มีข้อมูลเหตุการณ์ที่ถูกต้อง ข้ามการบันทึกแถวใหม่" 
      })).setMimeType(ContentService.MimeType.JSON);
    }

    var timestamp = new Date();
    var title     = data.title || "จุดแจ้งเหตุ";
    var address   = data.address || "ไม่ระบุที่อยู่";
    var lat       = data.latitude || 0;
    var lon       = data.longitude || 0;
    var reporter  = data.reporter || "ผู้ใช้ LINE";
    var urgency   = data.urgency || "🟡 ปานกลาง";
    var incident  = data.incident_type || "🌊 น้ำท่วมขัง";
    var status    = "⏳ รอดำเนินการ";
    var fileUrl   = "-";

    if (data.file_base64 && data.file_name) {
      try {
        var FOLDER_ID = "1Hm76KnhLEE7CecGTDkXtEUJBasMbAqMn";
        var folder = DriveApp.getFolderById(FOLDER_ID);
        var decoded = Utilities.base64Decode(data.file_base64);
        var blob = Utilities.newBlob(decoded, data.mime_type || "image/jpeg", data.file_name);
        var file = folder.createFile(blob);
        file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
        fileUrl = file.getUrl();
      } catch (driveErr) {
        fileUrl = "Error: " + driveErr.toString();
      }
    }

    sheet.appendRow([timestamp, title, address, lat, lon, reporter, urgency, incident, status, fileUrl]);
    
    return ContentService.createTextOutput(JSON.stringify({ 
      "status": "success", 
      "file_url": fileUrl 
    })).setMimeType(ContentService.MimeType.JSON);

  } catch (err) {
    return ContentService.createTextOutput(JSON.stringify({ 
      "status": "error", 
      "message": err.toString() 
    })).setMimeType(ContentService.MimeType.JSON);
  }
}
