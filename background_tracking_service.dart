import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:flutter_foreground_task/flutter_foreground_task.dart';
import 'package:geolocator/geolocator.dart';
import 'package:battery_plus/battery_plus.dart';
import 'api_service.dart';
import 'storage_service.dart';
import '../models/track_point_model.dart';

// Callback ฟังก์ชันระดับ Top-Level สำหรับ Background Isolate
@pragma('vm:entry-point')
void startCallback() {
  FlutterForegroundTask.setTaskHandler(TacticalTaskHandler());
}

class TacticalTaskHandler extends TaskHandler {
  final ApiService _apiService = ApiService();
  final Battery _battery = Battery();

  @override
  Future<void> onStart(DateTime timestamp, TaskStarter starter) async {
    // เตรียมพร้อมทำงานเบื้องหลัง
  }

  @override
  Future<void> onRepeatEvent(DateTime timestamp) async {
    try {
      // 1. อ่านข้อมูลชุดปฏิบัติการที่เซฟไว้
      final profile = await StorageService.getUnitProfile();
      if (profile == null) return;

      final activeMission = await StorageService.getActiveMissionId();
      final missionStatus = await StorageService.getMissionStatus() ?? '🟢 สแตนด์บาย';

      // 2. ขอพิกัด GPS ล่าสุดด้วยความแม่นยำสูงสุดระดับยุทธการ
      final Position pos = await Geolocator.getCurrentPosition(
        desiredAccuracy: LocationAccuracy.bestForNavigation,
        timeLimit: const Duration(seconds: 8),
      );

      // 3. อ่านระดับแบตเตอรี่
      int batteryLevel = 80;
      try {
        batteryLevel = await _battery.batteryLevel;
      } catch (_) {}

      // 4. ประกอบข้อมูล Telemetry
      final payload = {
        'unit_id': profile.unitId,
        'unit_name': '${profile.unitName} (${profile.district})',
        'commander': profile.commander,
        'latitude': pos.latitude,
        'longitude': pos.longitude,
        'speed': (pos.speed * 3.6).round(), // km/h
        'heading': pos.heading.round(),
        'accuracy': pos.accuracy.round(),
        'battery': batteryLevel,
        'status': missionStatus,
        'active_mission': activeMission ?? '-',
        'unit_size': profile.unitSize,
        'vehicle_type': profile.vehicleType,
        'picture_profile': '-',
        'timestamp': DateTime.now().toIso8601String(),
      };

      // 5. ส่งขึ้นคลาวด์
      await _apiService.sendTelemetry(payload);

      // 6. บันทึก Log ประวัติการเดิน (Movement Track Log) เฉพาะพิกัดที่แม่นยำสูง (Accuracy <= 6-8 ม.)
      if (pos.accuracy <= 8.0) {
        await StorageService.appendTrackPoint(
          TrackPointModel(
            latitude: pos.latitude,
            longitude: pos.longitude,
            speed: pos.speed * 3.6,
            heading: pos.heading >= 0 ? pos.heading : 0.0,
            timestamp: DateTime.now(),
          ),
        );
      }

      // อัปเดตข้อความบน Notification บาร์
      FlutterForegroundTask.updateService(
        notificationTitle: '🛡️ RTSD Tactical Tracker: ${profile.district}',
        notificationText: 'สถานะ: $missionStatus | ความเร็ว: ${(pos.speed * 3.6).round()} กม./ชม.',
      );
    } catch (e) {
      // ดักจับข้อผิดพลาดระหว่างส่งเบื้องหลัง
    }
  }

  @override
  Future<void> onDestroy(DateTime timestamp) async {
    // สิ้นสุดภารกิจเบื้องหลัง
  }
}

class BackgroundTrackingService {
  static void init() {
    if (kIsWeb) return;
    FlutterForegroundTask.init(
      androidNotificationOptions: AndroidNotificationOptions(
        channelId: 'rtsd_tactical_channel',
        channelName: 'RTSD Tactical Background Service',
        channelDescription: 'บริการติดตามพิกัดชุดปฏิบัติการอำเภอแบบต่อเนื่อง',
        channelImportance: NotificationChannelImportance.HIGH,
        priority: NotificationPriority.HIGH,
      ),
      iosNotificationOptions: const IOSNotificationOptions(
        showNotification: true,
        playSound: false,
      ),
      foregroundTaskOptions: ForegroundTaskOptions(
        eventAction: ForegroundTaskEventAction.repeat(15000), // ส่งพิกัดทุกๆ 15 วินาที
        autoRunOnBoot: false,
        allowWakeLock: true,
        allowWifiLock: true,
      ),
    );
  }

  /// เริ่มต้น Background Tracking
  static Future<bool> startTracking({required String district, required String status}) async {
    if (kIsWeb) return true;
    // ตรวจสอบ Permission GPS
    LocationPermission permission = await Geolocator.checkPermission();
    if (permission == LocationPermission.denied) {
      permission = await Geolocator.requestPermission();
      if (permission == LocationPermission.denied) return false;
    }

    if (await FlutterForegroundTask.isRunningService) {
      return true;
    }

    final res = await FlutterForegroundTask.startService(
      serviceId: 256,
      notificationTitle: '🛡️ RTSD Tactical: อ.$district',
      notificationText: 'กำลังเริ่มบริการติดตามพิกัดสด...',
      callback: startCallback,
    );
    return res is ServiceRequestSuccess;
  }

  /// หยุด Background Tracking
  static Future<bool> stopTracking() async {
    if (kIsWeb) return true;
    final res = await FlutterForegroundTask.stopService();
    return res is ServiceRequestSuccess;
  }

  /// ตรวจสอบว่ากำลังรันอยู่หรือไม่
  static Future<bool> isRunning() async {
    if (kIsWeb) return false;
    return await FlutterForegroundTask.isRunningService;
  }
}
