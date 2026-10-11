import 'dart:async';
import 'dart:math' as math;
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_map/flutter_map.dart';
import 'package:geolocator/geolocator.dart';
import 'package:latlong2/latlong.dart';
import 'package:url_launcher/url_launcher.dart';
import '../../../../data/models/incident_model.dart';
import '../../../../data/models/tactical_pin_model.dart';
import '../../../../data/models/track_point_model.dart';
import '../../../../data/models/unit_profile_model.dart';
import '../../../../data/services/storage_service.dart';
import '../../../core/app_colors.dart';
import '../../mission/views/active_mission_screen.dart';
import '../widgets/radar_sweep_painter.dart';
import '../widgets/tactical_incident_marker.dart';
import '../widgets/tactical_pin_marker.dart';
import '../widgets/tactical_unit_marker.dart';

enum BasemapStyle { osm, satellite, dark }

class TacticalRadarMapScreen extends StatefulWidget {
  final UnitProfileModel? profile;
  final List<IncidentModel> incidents;
  final List<TacticalPinModel> tacticalPins;
  final IncidentModel? targetIncident; // Optional: auto-focus on a specific incident
  final TacticalPinModel? targetPin; // Optional: auto-focus on a specific pin

  const TacticalRadarMapScreen({
    super.key,
    required this.profile,
    required this.incidents,
    this.tacticalPins = const [],
    this.targetIncident,
    this.targetPin,
  });

  @override
  State<TacticalRadarMapScreen> createState() => _TacticalRadarMapScreenState();
}

class _TacticalRadarMapScreenState extends State<TacticalRadarMapScreen>
    with SingleTickerProviderStateMixin {
  late final MapController _mapController;
  late final AnimationController _sweepAnimationController;

  Position? _currentPosition;
  StreamSubscription<Position>? _positionStreamSub;
  double _currentHeading = 0.0;
  double _currentSpeed = 0.0;

  BasemapStyle _currentBasemap = BasemapStyle.osm;
  bool _isRadarEnabled = true;
  String _radarRangeMode = 'auto'; // 'auto', '500', '1000', '2500', '5000', '10000', '20000', '50000'
  double _radarRangeMeters = 5000.0;
  double _radarPixelRadius = 160.0;
  String _radarRangeLabel = '5.0 KM';
  String _radarMidRangeLabel = '3.3 KM';
  String _radarInnerRangeLabel = '1.7 KM';
  bool _isRadarCenteredOnUser = false; // true = locked on user GPS coordinate, false = centered on HUD screen

  // Movement Trail Log (Breadcrumb Track)
  List<TrackPointModel> _trackPoints = [];
  bool _showTrail = true;
  double _totalDistanceMeters = 0.0;

  // 🎯 เกณฑ์ความคลาดเคลื่อนสูงสุดที่ยอมรับได้ (ค่าเริ่มต้น: 5.0 เมตร)
  double _maxAllowedAccuracy = 5.0;

  // Default fallback center (Mae Sai / Chiang Rai)
  static const LatLng _defaultCenter = LatLng(20.0210, 99.8760);

  @override
  void initState() {
    super.initState();
    _mapController = MapController();

    // 4s continuous 360° radar sweep animation
    _sweepAnimationController = AnimationController(
      vsync: this,
      duration: const Duration(seconds: 4),
    )..repeat();

    _loadTrackPoints();
    _initLocationTracking();
  }

  /// คำนวณขนาดรัศมีเรดาร์ (พิกเซล) และระยะทางจริงตามระดับการซูมของแผนที่ (Dynamic Meters per Pixel)
  void _updateRadarDimensions(MapCamera camera) {
    try {
      final centerLatLng = (_isRadarCenteredOnUser && _currentPosition != null)
          ? LatLng(_currentPosition!.latitude, _currentPosition!.longitude)
          : camera.center;

      // มาตรฐานระยะเรดาร์ทางยุทธวิธี (เมตร)
      const ranges = [
        100.0,
        250.0,
        500.0,
        1000.0,
        2500.0,
        5000.0,
        10000.0,
        20000.0,
        40000.0,
        80000.0,
      ];

      double getPx(double m) {
        final latOffset = (m / 6378137.0) * (180.0 / math.pi);
        final c = camera.latLngToScreenOffset(centerLatLng);
        final n = camera.latLngToScreenOffset(LatLng(centerLatLng.latitude + latOffset, centerLatLng.longitude));
        return math.max(25.0, (c.dy - n.dy).abs());
      }

      double targetMeters;
      if (_radarRangeMode != 'auto') {
        targetMeters = double.tryParse(_radarRangeMode) ?? 5000.0;
      } else {
        // โหมดอัตโนมัติ: เลือกระยะที่ขนาดวงกลมเรดาร์พอดีกับหน้าจอมือถือ (~120 ถึง 240 พิกเซล)
        double best = 5000.0;
        double minDiff = double.infinity;
        for (final r in ranges) {
          final px = getPx(r);
          if (px >= 120 && px <= 240) {
            best = r;
            break;
          }
          final diff = (px - 170.0).abs();
          if (diff < minDiff) {
            minDiff = diff;
            best = r;
          }
        }
        targetMeters = best;
      }

      final pxRadius = getPx(targetMeters);

      String formatDist(double m) {
        if (m >= 1000) {
          final km = m / 1000.0;
          return km % 1 == 0 ? '${km.toInt()} KM' : '${km.toStringAsFixed(1)} KM';
        }
        return '${m.round()} M';
      }

      if (mounted) {
        setState(() {
          _radarRangeMeters = targetMeters;
          _radarPixelRadius = pxRadius;
          _radarRangeLabel = formatDist(targetMeters);
          _radarMidRangeLabel = formatDist(targetMeters * 0.66);
          _radarInnerRangeLabel = formatDist(targetMeters * 0.33);
        });
      }
    } catch (_) {}
  }

  void _showRadarRangeSelector() {
    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF0F172A),
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setSheetState) => SafeArea(
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Center(
                  child: Container(
                    width: 38,
                    height: 4,
                    margin: const EdgeInsets.only(bottom: 12),
                    decoration: BoxDecoration(
                      color: Colors.white24,
                      borderRadius: BorderRadius.circular(2),
                    ),
                  ),
                ),
                const Row(
                  children: [
                    Icon(Icons.radar, color: Color(0xFFEF4444), size: 20),
                    SizedBox(width: 8),
                    Text(
                      'การตั้งค่าระยะเรดาร์ยุทธวิธี (Radar Range)',
                      style: TextStyle(color: Colors.white, fontSize: 15, fontWeight: FontWeight.bold),
                    ),
                  ],
                ),
                const SizedBox(height: 6),
                const Text(
                  'เลือกระยะรัศมีเรดาร์เพื่อคำนวณสเกลพื้นที่จริงตามระดับการซูมของแผนที่',
                  style: TextStyle(color: AppColors.textMuted, fontSize: 11),
                ),
                const SizedBox(height: 12),

                // 1. Radar Master Switch
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                  margin: const EdgeInsets.only(bottom: 12),
                  decoration: BoxDecoration(
                    color: _isRadarEnabled
                        ? const Color(0xFFEF4444).withValues(alpha: 0.15)
                        : const Color(0xFF1E293B),
                    borderRadius: BorderRadius.circular(12),
                    border: Border.all(
                      color: _isRadarEnabled
                          ? const Color(0xFFEF4444).withValues(alpha: 0.6)
                          : const Color(0xFF334155),
                    ),
                  ),
                  child: Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Row(
                        children: [
                          Icon(
                            Icons.radar,
                            color: _isRadarEnabled ? const Color(0xFFEF4444) : Colors.white38,
                            size: 22,
                          ),
                          const SizedBox(width: 10),
                          Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                _isRadarEnabled ? 'เปิดเรดาร์สแกน (RADAR ON)' : 'ปิดเรดาร์ (RADAR OFF)',
                                style: TextStyle(
                                  color: _isRadarEnabled ? Colors.white : Colors.white70,
                                  fontSize: 13,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                              Text(
                                _isRadarEnabled
                                    ? 'กำลังกวาดสัญญาณและจำลองวงแหวนยุทธวิธี'
                                    : 'ปิดการแสดงวงแหวนและเส้นกวาดเรดาร์',
                                style: const TextStyle(color: AppColors.textMuted, fontSize: 10),
                              ),
                            ],
                          ),
                        ],
                      ),
                      Switch(
                        value: _isRadarEnabled,
                        activeThumbColor: const Color(0xFFEF4444),
                        activeTrackColor: const Color(0xFFEF4444).withValues(alpha: 0.4),
                        onChanged: (val) {
                          setSheetState(() => _isRadarEnabled = val);
                          setState(() => _isRadarEnabled = val);
                        },
                      ),
                    ],
                  ),
                ),

                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    _buildRadarOffChip(setSheetState),
                    _buildRangeChip('⚡ อัตโนมัติ (AUTO)', 'auto', setSheetState),
                    _buildRangeChip('500 M', '500', setSheetState),
                    _buildRangeChip('1.0 KM', '1000', setSheetState),
                    _buildRangeChip('2.5 KM', '2500', setSheetState),
                    _buildRangeChip('5.0 KM', '5000', setSheetState),
                    _buildRangeChip('10 KM', '10000', setSheetState),
                    _buildRangeChip('20 KM', '20000', setSheetState),
                    _buildRangeChip('50 KM', '50000', setSheetState),
                  ],
                ),
                const Divider(color: Color(0xFF334155), height: 24),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  activeColor: const Color(0xFFEF4444),
                  title: const Text('ล็อกเป้าเรดาร์ไว้ที่พิกัด GPS ของฉัน', style: TextStyle(color: Colors.white, fontSize: 13)),
                  subtitle: const Text('หากปิด เรดาร์จะอยู่ที่กึ่งกลางหน้าจอ HUD ตลอดเวลา', style: TextStyle(color: AppColors.textMuted, fontSize: 11)),
                  value: _isRadarCenteredOnUser,
                  onChanged: (val) {
                    setSheetState(() => _isRadarCenteredOnUser = val);
                    setState(() => _isRadarCenteredOnUser = val);
                    _updateRadarDimensions(_mapController.camera);
                  },
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildRadarOffChip(StateSetter setSheetState) {
    final isOff = !_isRadarEnabled;
    return ChoiceChip(
      avatar: Icon(Icons.power_settings_new, size: 14, color: isOff ? Colors.white : Colors.white54),
      label: Text(
        'ปิดเรดาร์ (OFF)',
        style: TextStyle(
          fontSize: 12,
          fontWeight: isOff ? FontWeight.bold : FontWeight.normal,
          color: isOff ? Colors.white : Colors.white70,
        ),
      ),
      selected: isOff,
      selectedColor: const Color(0xFF475569),
      backgroundColor: const Color(0xFF1E293B),
      onSelected: (selected) {
        if (selected) {
          setSheetState(() => _isRadarEnabled = false);
          setState(() => _isRadarEnabled = false);
          Navigator.pop(context);
        }
      },
    );
  }

  Widget _buildRangeChip(String label, String value, StateSetter setSheetState) {
    final isSelected = _isRadarEnabled && _radarRangeMode == value;
    return ChoiceChip(
      label: Text(label, style: TextStyle(fontSize: 12, fontWeight: isSelected ? FontWeight.bold : FontWeight.normal, color: isSelected ? Colors.white : Colors.white70)),
      selected: isSelected,
      selectedColor: const Color(0xFFEF4444),
      backgroundColor: const Color(0xFF1E293B),
      onSelected: (selected) {
        if (selected) {
          setSheetState(() {
            _isRadarEnabled = true;
            _radarRangeMode = value;
          });
          setState(() {
            _isRadarEnabled = true;
            _radarRangeMode = value;
          });
          _updateRadarDimensions(_mapController.camera);
          Navigator.pop(context);
        }
      },
    );
  }

  void _loadTrackPoints() async {
    final list = await StorageService.getTrackPoints();
    double dist = 0.0;
    for (int i = 0; i < list.length - 1; i++) {
      dist += Geolocator.distanceBetween(
        list[i].latitude,
        list[i].longitude,
        list[i + 1].latitude,
        list[i + 1].longitude,
      );
    }
    if (mounted) {
      setState(() {
        _trackPoints = list;
        _totalDistanceMeters = dist;
      });
    }
  }

  void _recordTrackPoint(Position pos) {
    // 🛡️ High-Precision Gate: คัดกรองความคลาดเคลื่อน (ไม่เกิน 5.0 เมตร)
    // หากชิป GPS ยังไม่ล็อกแน่น หรือความคลาดเคลื่อนเกินเกณฑ์ จะไม่นำมาบันทึกเส้นทางเดิน
    if (pos.accuracy > _maxAllowedAccuracy) {
      return;
    }

    final newPt = TrackPointModel(
      latitude: pos.latitude,
      longitude: pos.longitude,
      speed: pos.speed * 3.6,
      heading: pos.heading >= 0 ? pos.heading : 0.0,
      timestamp: DateTime.now(),
    );

    if (_trackPoints.isEmpty) {
      _trackPoints.add(newPt);
      StorageService.appendTrackPoint(newPt);
    } else {
      final last = _trackPoints.last;
      final dist = Geolocator.distanceBetween(
        last.latitude,
        last.longitude,
        newPt.latitude,
        newPt.longitude,
      );
      if (dist >= 4.0) {
        _totalDistanceMeters += dist;
        _trackPoints.add(newPt);
        StorageService.appendTrackPoint(newPt);
      }
    }
  }

  void _confirmClearTrackLog() {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF0F172A),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: const BorderSide(color: Color(0xFF334155)),
        ),
        title: const Row(
          children: [
            Icon(Icons.delete_sweep, color: Color(0xFFEF4444)),
            SizedBox(width: 8),
            Text('ล้างประวัติเส้นทางเดิน?', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
          ],
        ),
        content: const Text(
          'ต้องการลบ Log เส้นทางพิกัดทั้งหมดที่บันทึกไว้ เพื่อเริ่มต้นนับระยะทางและบันทึกเส้นทางใหม่หรือไม่?',
          style: TextStyle(color: AppColors.textMuted, fontSize: 13),
        ),
        actions: [
          TextButton(
            child: const Text('ยกเลิก', style: TextStyle(color: Colors.white70)),
            onPressed: () => Navigator.pop(ctx),
          ),
          ElevatedButton(
            style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFFEF4444),
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
            ),
            child: const Text('ล้างข้อมูล', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
            onPressed: () async {
              Navigator.pop(ctx);
              await StorageService.clearTrackPoints();
              if (mounted) {
                setState(() {
                  _trackPoints.clear();
                  _totalDistanceMeters = 0.0;
                });
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                    backgroundColor: Color(0xFF1E293B),
                    content: Text('🗑️ ล้างประวัติเส้นทางการเดินเรียบร้อยแล้ว'),
                  ),
                );
              }
            },
          ),
        ],
      ),
    );
  }

  @override
  void dispose() {
    _positionStreamSub?.cancel();
    _sweepAnimationController.dispose();
    super.dispose();
  }

  void _initLocationTracking() async {
    try {
      final perm = await Geolocator.checkPermission();
      if (perm == LocationPermission.denied) {
        await Geolocator.requestPermission();
      }

      // Initial fast location fetch
      final pos = await Geolocator.getCurrentPosition(
        desiredAccuracy: LocationAccuracy.bestForNavigation,
      );
      if (mounted) {
        setState(() {
          _currentPosition = pos;
          _currentHeading = pos.heading >= 0 ? pos.heading : 0.0;
          _currentSpeed = pos.speed * 3.6;
        });

        _recordTrackPoint(pos);

        // Center map on target pin or target incident if provided, otherwise on current position
        if (widget.targetPin != null && widget.targetPin!.latitude != 0.0) {
          _mapController.move(
            LatLng(widget.targetPin!.latitude, widget.targetPin!.longitude),
            15.5,
          );
        } else if (widget.targetIncident != null &&
            widget.targetIncident!.latitude != 0.0) {
          _mapController.move(
            LatLng(widget.targetIncident!.latitude, widget.targetIncident!.longitude),
            15.0,
          );
        } else {
          _mapController.move(LatLng(pos.latitude, pos.longitude), 14.5);
        }
      }
    } catch (_) {}

    // Real-time GPS stream listener with bestForNavigation precision
    final LocationSettings locationSettings;
    if (defaultTargetPlatform == TargetPlatform.android) {
      locationSettings = AndroidSettings(
        accuracy: LocationAccuracy.bestForNavigation,
        distanceFilter: 2,
        intervalDuration: const Duration(seconds: 1),
      );
    } else {
      locationSettings = const LocationSettings(
        accuracy: LocationAccuracy.bestForNavigation,
        distanceFilter: 2,
      );
    }

    _positionStreamSub = Geolocator.getPositionStream(
      locationSettings: locationSettings,
    ).listen((pos) {
      if (!mounted) return;
      setState(() {
        _currentPosition = pos;
        if (pos.heading > 0) _currentHeading = pos.heading;
        _currentSpeed = pos.speed * 3.6;
      });
      _recordTrackPoint(pos);
    });
  }

  void _recenterMyPosition() {
    if (_currentPosition != null) {
      _mapController.move(
        LatLng(_currentPosition!.latitude, _currentPosition!.longitude),
        15.0,
      );
    } else {
      _mapController.move(_defaultCenter, 14.0);
    }
  }

  void _fitAllMarkers() {
    final points = <LatLng>[];
    if (_currentPosition != null) {
      points.add(LatLng(_currentPosition!.latitude, _currentPosition!.longitude));
    }
    for (final inc in widget.incidents) {
      if (inc.latitude != 0.0 && inc.longitude != 0.0) {
        points.add(LatLng(inc.latitude, inc.longitude));
      }
    }

    if (points.isEmpty) return;

    if (points.length == 1) {
      _mapController.move(points.first, 15.0);
      return;
    }

    final bounds = LatLngBounds.fromPoints(points);
    _mapController.fitCamera(
      CameraFit.bounds(
        bounds: bounds,
        padding: const EdgeInsets.all(50),
      ),
    );
  }

  void _openGoogleMaps(double lat, double lon) async {
    final uri = Uri.parse('google.navigation:q=$lat,$lon&mode=d');
    final fallbackUri = Uri.parse('https://www.google.com/maps/dir/?api=1&destination=$lat,$lon');
    if (await canLaunchUrl(uri)) {
      await launchUrl(uri);
    } else {
      await launchUrl(fallbackUri, mode: LaunchMode.externalApplication);
    }
  }

  void _showIncidentDetailsModal(IncidentModel inc) {
    double distanceKm = 0.0;
    if (_currentPosition != null && inc.latitude != 0.0 && inc.longitude != 0.0) {
      final meters = Geolocator.distanceBetween(
        _currentPosition!.latitude,
        _currentPosition!.longitude,
        inc.latitude,
        inc.longitude,
      );
      distanceKm = meters / 1000.0;
    }

    showModalBottomSheet(
      context: context,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (ctx) {
        return Container(
          decoration: BoxDecoration(
            color: const Color(0xFF0F172A),
            borderRadius: const BorderRadius.vertical(top: Radius.circular(20)),
            border: Border.all(color: const Color(0xFF334155), width: 1.5),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withOpacity(0.8),
                blurRadius: 20,
                spreadRadius: 5,
              ),
            ],
          ),
          padding: const EdgeInsets.all(18),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Top drag bar
              Center(
                child: Container(
                  width: 40,
                  height: 4,
                  margin: const EdgeInsets.only(bottom: 14),
                  decoration: BoxDecoration(
                    color: Colors.white24,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),

              // Header
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Row(
                    children: [
                      Container(
                        padding: const EdgeInsets.all(6),
                        decoration: BoxDecoration(
                          color: AppColors.dangerRed.withOpacity(0.2),
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(color: AppColors.dangerRed.withOpacity(0.6)),
                        ),
                        child: const Icon(Icons.radar, color: AppColors.dangerRed, size: 18),
                      ),
                      const SizedBox(width: 8),
                      Text(
                        inc.reportId,
                        style: const TextStyle(
                          color: AppColors.primaryLight,
                          fontWeight: FontWeight.bold,
                          fontSize: 16,
                          fontFamily: 'monospace',
                        ),
                      ),
                    ],
                  ),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: AppColors.warningOrange.withOpacity(0.2),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: AppColors.warningOrange.withOpacity(0.5)),
                    ),
                    child: Text(
                      inc.urgency,
                      style: const TextStyle(
                        color: AppColors.warningOrange,
                        fontSize: 11,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 10),

              // Title
              Text(
                inc.title,
                style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 6),

              // Address & Distance
              Row(
                children: [
                  const Icon(Icons.location_on, color: AppColors.primaryLight, size: 16),
                  const SizedBox(width: 4),
                  Expanded(
                    child: Text(
                      inc.address.isNotEmpty ? inc.address : 'พิกัด: ${inc.latitude.toStringAsFixed(4)}, ${inc.longitude.toStringAsFixed(4)}',
                      style: const TextStyle(color: AppColors.textMuted, fontSize: 12),
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),

              if (distanceKm > 0) ...[
                const SizedBox(height: 4),
                Row(
                  children: [
                    const Icon(Icons.straighten, color: AppColors.accentGreen, size: 16),
                    const SizedBox(width: 4),
                    Text(
                      'ระยะห่าง: ${distanceKm.toStringAsFixed(2)} กม. จากตำแหน่งปัจจุบัน',
                      style: const TextStyle(color: AppColors.accentGreen, fontSize: 12, fontWeight: FontWeight.bold),
                    ),
                  ],
                ),
              ],
              const SizedBox(height: 16),

              // Actions
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      style: OutlinedButton.styleFrom(
                        foregroundColor: Colors.white,
                        side: const BorderSide(color: AppColors.border),
                        padding: const EdgeInsets.symmetric(vertical: 12),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                      ),
                      icon: const Icon(Icons.gps_fixed, size: 18, color: AppColors.primaryLight),
                      label: const Text('ล็อคเป้า'),
                      onPressed: () {
                        Navigator.pop(ctx);
                        _mapController.move(LatLng(inc.latitude, inc.longitude), 16.0);
                      },
                    ),
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: ElevatedButton.icon(
                      style: ElevatedButton.styleFrom(
                        backgroundColor: AppColors.primary,
                        padding: const EdgeInsets.symmetric(vertical: 12),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                      ),
                      icon: const Icon(Icons.directions, size: 18),
                      label: const Text('นำทาง GPS'),
                      onPressed: () {
                        Navigator.pop(ctx);
                        _openGoogleMaps(inc.latitude, inc.longitude);
                      },
                    ),
                  ),
                  const SizedBox(width: 8),
                  IconButton.filled(
                    style: IconButton.styleFrom(
                      backgroundColor: AppColors.accentGreen,
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                    ),
                    icon: const Icon(Icons.play_arrow, color: Colors.white),
                    tooltip: 'รับภารกิจ',
                    onPressed: () {
                      Navigator.pop(ctx);
                      Navigator.push(
                        context,
                        MaterialPageRoute(builder: (_) => ActiveMissionScreen(incident: inc)),
                      );
                    },
                  ),
                ],
              ),
            ],
          ),
        );
      },
    );
  }

  void _showTacticalPinDetailsModal(TacticalPinModel pin) {
    double distanceKm = 0.0;
    if (_currentPosition != null && pin.latitude != 0.0 && pin.longitude != 0.0) {
      final meters = Geolocator.distanceBetween(
        _currentPosition!.latitude,
        _currentPosition!.longitude,
        pin.latitude,
        pin.longitude,
      );
      distanceKm = meters / 1000.0;
    }

    showModalBottomSheet(
      context: context,
      backgroundColor: Colors.transparent,
      isScrollControlled: true,
      builder: (ctx) {
        return Container(
          decoration: BoxDecoration(
            color: const Color(0xFF0F172A),
            borderRadius: const BorderRadius.vertical(top: Radius.circular(20)),
            border: Border.all(color: const Color(0xFF334155), width: 1.5),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withOpacity(0.8),
                blurRadius: 20,
                spreadRadius: 5,
              ),
            ],
          ),
          padding: const EdgeInsets.all(18),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              // Top drag bar
              Center(
                child: Container(
                  width: 40,
                  height: 4,
                  margin: const EdgeInsets.only(bottom: 14),
                  decoration: BoxDecoration(
                    color: Colors.white24,
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),

              // Header
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Row(
                    children: [
                      Container(
                        padding: const EdgeInsets.all(6),
                        decoration: BoxDecoration(
                          color: const Color(0xFF3B82F6).withOpacity(0.2),
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(color: const Color(0xFF3B82F6).withOpacity(0.6)),
                        ),
                        child: const Icon(Icons.place, color: Color(0xFF3B82F6), size: 18),
                      ),
                      const SizedBox(width: 8),
                      Text(
                        pin.id,
                        style: const TextStyle(
                          color: AppColors.primaryLight,
                          fontWeight: FontWeight.bold,
                          fontSize: 16,
                          fontFamily: 'monospace',
                        ),
                      ),
                    ],
                  ),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: const Color(0xFF10B981).withOpacity(0.2),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: const Color(0xFF10B981).withOpacity(0.5)),
                    ),
                    child: Text(
                      pin.categoryName,
                      style: const TextStyle(
                        color: Color(0xFF10B981),
                        fontSize: 11,
                        fontWeight: FontWeight.bold,
                      ),
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 10),

              // Title
              Text(
                pin.title,
                style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 6),

              // Creator & Distance
              Row(
                children: [
                  const Icon(Icons.person_pin_circle_outlined, color: AppColors.primaryLight, size: 16),
                  const SizedBox(width: 4),
                  Expanded(
                    child: Text(
                      'ผู้สั่งการ: ${pin.creator}',
                      style: const TextStyle(color: AppColors.textMuted, fontSize: 12),
                    ),
                  ),
                  if (distanceKm > 0)
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                      decoration: BoxDecoration(
                        color: Colors.white10,
                        borderRadius: BorderRadius.circular(4),
                      ),
                      child: Text(
                        'ห่าง ${distanceKm.toStringAsFixed(1)} กม.',
                        style: const TextStyle(color: AppColors.primaryLight, fontSize: 11, fontWeight: FontWeight.bold),
                      ),
                    ),
                ],
              ),

              if (pin.taggedUnits.isNotEmpty) ...[
                const SizedBox(height: 8),
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Icon(Icons.campaign, color: Colors.amber, size: 16),
                    const SizedBox(width: 4),
                    Expanded(
                      child: Text(
                        'แท็กถึง: ${pin.taggedUnits.join(", ")}',
                        style: const TextStyle(color: Colors.amber, fontSize: 12),
                      ),
                    ),
                  ],
                ),
              ],

              if (pin.notes.isNotEmpty) ...[
                const SizedBox(height: 8),
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(10),
                  decoration: BoxDecoration(
                    color: Colors.white.withOpacity(0.04),
                    borderRadius: BorderRadius.circular(8),
                    border: Border.all(color: Colors.white12),
                  ),
                  child: Text(
                    pin.notes,
                    style: const TextStyle(color: Colors.white70, fontSize: 12.5),
                  ),
                ),
              ],

              const SizedBox(height: 14),

              // Action Buttons
              Row(
                children: [
                  Expanded(
                    child: OutlinedButton.icon(
                      style: OutlinedButton.styleFrom(
                        foregroundColor: Colors.white70,
                        side: const BorderSide(color: Color(0xFF334155)),
                        padding: const EdgeInsets.symmetric(vertical: 12),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                      ),
                      icon: const Icon(Icons.gps_fixed, size: 16, color: AppColors.primaryLight),
                      label: const Text('ล็อคเป้า'),
                      onPressed: () {
                        Navigator.pop(ctx);
                        _mapController.move(LatLng(pin.latitude, pin.longitude), 16.0);
                      },
                    ),
                  ),
                  const SizedBox(width: 10),
                  Expanded(
                    flex: 2,
                    child: ElevatedButton.icon(
                      style: ElevatedButton.styleFrom(
                        backgroundColor: const Color(0xFF3B82F6),
                        foregroundColor: Colors.white,
                        padding: const EdgeInsets.symmetric(vertical: 12),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                      ),
                      icon: const Icon(Icons.navigation, size: 16),
                      label: const Text('นำทาง GPS (Google Maps)'),
                      onPressed: () {
                        Navigator.pop(ctx);
                        _openGoogleMaps(pin.latitude, pin.longitude);
                      },
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 10),
            ],
          ),
        );
      },
    );
  }

  String _getTileUrl() {
    switch (_currentBasemap) {
      case BasemapStyle.satellite:
        return 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
      case BasemapStyle.dark:
        return 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}';
      case BasemapStyle.osm:
      default:
        return 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
    }
  }

  int _getMaxNativeZoom() {
    switch (_currentBasemap) {
      case BasemapStyle.dark:
        return 16;
      case BasemapStyle.satellite:
      case BasemapStyle.osm:
      default:
        return 18;
    }
  }

  @override
  Widget build(BuildContext context) {
    final myLatLng = _currentPosition != null
        ? LatLng(_currentPosition!.latitude, _currentPosition!.longitude)
        : _defaultCenter;

    return Scaffold(
      backgroundColor: const Color(0xFF070C19),
      body: Stack(
        children: [
          // 1. Leaflet / OSM Map Engine
          FlutterMap(
            mapController: _mapController,
            options: MapOptions(
              initialCenter: myLatLng,
              initialZoom: 14.5,
              minZoom: 4,
              onMapReady: () {
                _updateRadarDimensions(_mapController.camera);
              },
              onPositionChanged: (pos, hasGesture) {
                _updateRadarDimensions(_mapController.camera);
              },
            ),
            children: [
              TileLayer(
                key: ValueKey('tile_base_${_currentBasemap.name}'),
                urlTemplate: _getTileUrl(),
                fallbackUrl: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
                maxZoom: 20,
                maxNativeZoom: _getMaxNativeZoom(),
                userAgentPackageName: 'com.rtsd.tactical_tracker',
              ),

              // Reference overlay for labels and borders when in dark basemap mode
              if (_currentBasemap == BasemapStyle.dark)
                TileLayer(
                  key: const ValueKey('tile_dark_reference'),
                  urlTemplate:
                      'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}',
                  maxZoom: 20,
                  maxNativeZoom: 16,
                  userAgentPackageName: 'com.rtsd.tactical_tracker',
                ),

              // Movement Trail Polyline Layer (Breadcrumb Path)
              if (_showTrail && _trackPoints.length >= 2)
                PolylineLayer(
                  polylines: [
                    Polyline(
                      points: _trackPoints.map((p) => LatLng(p.latitude, p.longitude)).toList(),
                      color: const Color(0xFF22D3EE),
                      strokeWidth: 4.0,
                      borderColor: const Color(0xFF0284C7).withOpacity(0.8),
                      borderStrokeWidth: 1.5,
                    ),
                  ],
                ),

              // 🎯 GPS Accuracy Circle Layer (วงรัศมีความคลาดเคลื่อนจริง)
              if (_currentPosition != null)
                CircleLayer(
                  circles: [
                    CircleMarker(
                      point: LatLng(_currentPosition!.latitude, _currentPosition!.longitude),
                      radius: _currentPosition!.accuracy > 0 ? _currentPosition!.accuracy : 5.0,
                      useRadiusInMeter: true,
                      color: (_currentPosition!.accuracy <= _maxAllowedAccuracy
                          ? const Color(0xFF10B981) // เขียวมรกต: แม่นยำสูง <= 5 เมตร
                          : const Color(0xFFF59E0B) // ส้มเตือน: กำลังจับสัญญาณ
                      ).withOpacity(0.18),
                      borderColor: (_currentPosition!.accuracy <= _maxAllowedAccuracy
                          ? const Color(0xFF10B981)
                          : const Color(0xFFF59E0B)
                      ).withOpacity(0.65),
                      borderStrokeWidth: 1.5,
                    ),
                  ],
                ),

              // Marker Layer: Incidents & My Unit
              MarkerLayer(
                markers: [
                  // Start Point Waypoint Marker
                  if (_showTrail && _trackPoints.length >= 2)
                    Marker(
                      point: LatLng(_trackPoints.first.latitude, _trackPoints.first.longitude),
                      width: 52,
                      height: 48,
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1.5),
                            decoration: BoxDecoration(
                              color: const Color(0xFF0F172A).withOpacity(0.92),
                              borderRadius: BorderRadius.circular(4),
                              border: Border.all(color: const Color(0xFF10B981), width: 1),
                            ),
                            child: const Text(
                              'START',
                              style: TextStyle(
                                color: Color(0xFF10B981),
                                fontSize: 8.5,
                                fontWeight: FontWeight.bold,
                                fontFamily: 'monospace',
                              ),
                            ),
                          ),
                          const SizedBox(height: 1),
                          const Icon(Icons.trip_origin, color: Color(0xFF10B981), size: 18),
                        ],
                      ),
                    ),

                  // Incidents
                  ...widget.incidents.map((inc) {
                    if (inc.latitude == 0.0 || inc.longitude == 0.0) {
                      return null;
                    }
                    return Marker(
                      point: LatLng(inc.latitude, inc.longitude),
                      width: 50,
                      height: 52,
                      child: TacticalIncidentMarker(
                        incident: inc,
                        onTap: () => _showIncidentDetailsModal(inc),
                      ),
                    );
                  }).whereType<Marker>(),

                  // Tactical Command Pins (หมุดยุทธการจากศูนย์ควบคุม/Admin)
                  ...widget.tacticalPins.map((pin) {
                    if (pin.latitude == 0.0 || pin.longitude == 0.0) {
                      return null;
                    }
                    return Marker(
                      point: LatLng(pin.latitude, pin.longitude),
                      width: 75,
                      height: 52,
                      child: TacticalPinMarker(
                        pin: pin,
                        onTap: () => _showTacticalPinDetailsModal(pin),
                      ),
                    );
                  }).whereType<Marker>(),

                  // My Tactical Unit Beacon
                  Marker(
                    point: myLatLng,
                    width: 80,
                    height: 80,
                    child: TacticalUnitMarker(
                      unitId: widget.profile?.unitId ?? 'TL-ME',
                      commander: widget.profile?.commander,
                      unitName: widget.profile?.unitName,
                      vehicleType: widget.profile?.vehicleType,
                      heading: _currentHeading,
                      speed: _currentSpeed,
                    ),
                  ),
                ],
              ),
            ],
          ),

          // 2. Tactical Radar Overlay (Pulsing Rings & Sweeping Beam)
          if (_isRadarEnabled)
            IgnorePointer(
              child: AnimatedBuilder(
                animation: _sweepAnimationController,
                builder: (context, child) {
                  Offset? centerOffset;
                  if (_isRadarCenteredOnUser && _currentPosition != null) {
                    try {
                      centerOffset = _mapController.camera.latLngToScreenOffset(
                        LatLng(_currentPosition!.latitude, _currentPosition!.longitude),
                      );
                    } catch (_) {}
                  }

                  return CustomPaint(
                    size: Size.infinite,
                    painter: RadarSweepPainter(
                      angle: _sweepAnimationController.value * 2 * math.pi,
                      radarColor: const Color(0xFFEF4444),
                      showRings: true,
                      showSweep: true,
                      outerDistanceText: _radarRangeLabel,
                      midDistanceText: _radarMidRangeLabel,
                      innerDistanceText: _radarInnerRangeLabel,
                      radarRadius: _radarPixelRadius,
                      centerOffset: centerOffset,
                    ),
                  );
                },
              ),
            ),

          // 3. Top Command Bar HUD
          SafeArea(
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  // Status Pill
                  InkWell(
                    onTap: _showRadarRangeSelector,
                    borderRadius: BorderRadius.circular(12),
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                      decoration: BoxDecoration(
                        color: const Color(0xFF0F172A).withValues(alpha: 0.92),
                        borderRadius: BorderRadius.circular(12),
                        border: Border.all(
                          color: _isRadarEnabled
                              ? const Color(0xFFEF4444).withValues(alpha: 0.6)
                              : Colors.white24,
                        ),
                        boxShadow: [
                          BoxShadow(
                            color: Colors.black.withValues(alpha: 0.6),
                            blurRadius: 6,
                          ),
                        ],
                      ),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Container(
                            width: 8,
                            height: 8,
                            decoration: BoxDecoration(
                              color: _isRadarEnabled ? const Color(0xFFEF4444) : Colors.white38,
                              shape: BoxShape.circle,
                            ),
                          ),
                          const SizedBox(width: 6),
                          Text(
                            _isRadarEnabled ? 'RADAR • $_radarRangeLabel' : 'RADAR: ปิด (OFF)',
                            style: TextStyle(
                              color: _isRadarEnabled ? const Color(0xFFF87171) : Colors.white60,
                              fontSize: 11,
                              fontWeight: FontWeight.bold,
                              fontFamily: 'monospace',
                            ),
                          ),
                          if (_isRadarEnabled && _radarRangeMode == 'auto') ...[
                            const SizedBox(width: 5),
                            Container(
                              padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                              decoration: BoxDecoration(
                                color: const Color(0xFFEF4444).withValues(alpha: 0.2),
                                borderRadius: BorderRadius.circular(4),
                              ),
                              child: const Text('AUTO', style: TextStyle(color: Color(0xFFFCA5A5), fontSize: 9, fontWeight: FontWeight.bold)),
                            ),
                          ],
                        ],
                      ),
                    ),
                  ),

                  // Basemap Switcher & Radar Toggle
                  Container(
                    padding: const EdgeInsets.all(3),
                    decoration: BoxDecoration(
                      color: const Color(0xFF0F172A).withValues(alpha: 0.92),
                      borderRadius: BorderRadius.circular(12),
                      border: Border.all(color: const Color(0xFF334155)),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        _buildBasemapButton('OSM', BasemapStyle.osm),
                        _buildBasemapButton('ดาวเทียม', BasemapStyle.satellite),
                        _buildBasemapButton('มืด', BasemapStyle.dark),
                        const SizedBox(width: 4),
                        // Radar Toggle Icon
                        InkWell(
                          onTap: () {
                            setState(() => _isRadarEnabled = !_isRadarEnabled);
                            ScaffoldMessenger.of(context).removeCurrentSnackBar();
                            ScaffoldMessenger.of(context).showSnackBar(
                              SnackBar(
                                backgroundColor: const Color(0xFF1E293B),
                                duration: const Duration(seconds: 1),
                                content: Text(_isRadarEnabled ? '🎯 เปิดเรดาร์แล้ว' : '⏸️ ปิดเรดาร์แล้ว'),
                              ),
                            );
                          },
                          borderRadius: BorderRadius.circular(8),
                          child: Container(
                            padding: const EdgeInsets.all(6),
                            decoration: BoxDecoration(
                              color: _isRadarEnabled
                                  ? const Color(0xFFEF4444).withValues(alpha: 0.25)
                                  : Colors.transparent,
                              borderRadius: BorderRadius.circular(8),
                              border: Border.all(
                                color: _isRadarEnabled
                                    ? const Color(0xFFEF4444)
                                    : Colors.white24,
                              ),
                            ),
                            child: Icon(
                              Icons.radar,
                              size: 16,
                              color: _isRadarEnabled
                                  ? const Color(0xFFEF4444)
                                  : Colors.white60,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ),

          // 4. Floating Action Buttons (Right Side)
          Positioned(
            right: 14,
            bottom: 74,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                // Zoom In
                FloatingActionButton.small(
                  heroTag: 'zoom_in_btn',
                  backgroundColor: const Color(0xFF1E293B),
                  foregroundColor: Colors.white,
                  child: const Icon(Icons.add),
                  onPressed: () {
                    final z = _mapController.camera.zoom;
                    _mapController.move(_mapController.camera.center, z + 1);
                  },
                ),
                const SizedBox(height: 8),

                // Zoom Out
                FloatingActionButton.small(
                  heroTag: 'zoom_out_btn',
                  backgroundColor: const Color(0xFF1E293B),
                  foregroundColor: Colors.white,
                  child: const Icon(Icons.remove),
                  onPressed: () {
                    final z = _mapController.camera.zoom;
                    _mapController.move(_mapController.camera.center, z - 1);
                  },
                ),
                const SizedBox(height: 8),

                // Fit All Markers
                FloatingActionButton.small(
                  heroTag: 'fit_all_btn',
                  backgroundColor: const Color(0xFF1E293B),
                  foregroundColor: AppColors.primaryLight,
                  tooltip: 'ดูภาพรวมทุกจุด',
                  child: const Icon(Icons.fullscreen),
                  onPressed: _fitAllMarkers,
                ),
                const SizedBox(height: 8),

                // Radar Quick Toggle
                FloatingActionButton.small(
                  heroTag: 'radar_quick_toggle_fab',
                  backgroundColor: _isRadarEnabled ? const Color(0xFFEF4444) : const Color(0xFF1E293B),
                  foregroundColor: Colors.white,
                  tooltip: _isRadarEnabled ? 'แตะเพื่อปิดเรดาร์' : 'แตะเพื่อเปิดเรดาร์',
                  child: Icon(Icons.radar, color: _isRadarEnabled ? Colors.white : Colors.white54, size: 18),
                  onPressed: () {
                    setState(() => _isRadarEnabled = !_isRadarEnabled);
                    ScaffoldMessenger.of(context).removeCurrentSnackBar();
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(
                        backgroundColor: const Color(0xFF1E293B),
                        duration: const Duration(seconds: 1),
                        content: Text(
                          _isRadarEnabled ? '🎯 เปิดเรดาร์ตรวจจับยุทธวิธีแล้ว' : '⏸️ ปิดการทำงานเรดาร์แล้ว',
                        ),
                      ),
                    );
                  },
                ),
                const SizedBox(height: 8),

                // Toggle Movement Trail
                FloatingActionButton.small(
                  heroTag: 'trail_toggle_btn',
                  backgroundColor: _showTrail ? const Color(0xFF0284C7) : const Color(0xFF1E293B),
                  foregroundColor: Colors.white,
                  tooltip: _showTrail ? 'ซ่อนเส้นทางเดิน' : 'แสดงเส้นทางเดิน',
                  child: Icon(Icons.alt_route, color: _showTrail ? Colors.white : Colors.white60, size: 18),
                  onPressed: () {
                    setState(() => _showTrail = !_showTrail);
                  },
                ),
                const SizedBox(height: 8),

                // Clear Track Log
                FloatingActionButton.small(
                  heroTag: 'clear_trail_btn',
                  backgroundColor: const Color(0xFF1E293B),
                  foregroundColor: const Color(0xFFEF4444),
                  tooltip: 'ล้างประวัติเส้นทาง',
                  child: const Icon(Icons.delete_sweep, size: 18),
                  onPressed: _confirmClearTrackLog,
                ),
                const SizedBox(height: 8),

                // Toggle Accuracy Threshold (5m / 10m)
                FloatingActionButton.small(
                  heroTag: 'acc_gate_btn',
                  backgroundColor: _maxAllowedAccuracy <= 5.0 ? const Color(0xFF10B981) : const Color(0xFF1E293B),
                  foregroundColor: Colors.white,
                  tooltip: 'เกณฑ์ความคลาดเคลื่อน GPS สูงสุด: ${_maxAllowedAccuracy.round()} ม.',
                  child: Text(
                    '±${_maxAllowedAccuracy.round()}m',
                    style: const TextStyle(fontSize: 10, fontWeight: FontWeight.bold),
                  ),
                  onPressed: () {
                    setState(() {
                      _maxAllowedAccuracy = _maxAllowedAccuracy <= 5.0 ? 10.0 : 5.0;
                    });
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(
                        backgroundColor: const Color(0xFF1E293B),
                        duration: const Duration(seconds: 2),
                        content: Text(
                          _maxAllowedAccuracy <= 5.0
                              ? '🎯 ล็อกเกณฑ์ความแม่นยำสูง: บันทึกเฉพาะจุดที่คลาดเคลื่อน <= 5.0 เมตร'
                              : '📡 โหมดปกติ: บันทึกพิกัดที่คลาดเคลื่อน <= 10.0 เมตร',
                        ),
                      ),
                    );
                  },
                ),
                const SizedBox(height: 8),

                // Recenter My Position
                FloatingActionButton(
                  heroTag: 'my_pos_btn',
                  backgroundColor: AppColors.primary,
                  foregroundColor: Colors.white,
                  tooltip: 'ตำแหน่งฉัน',
                  child: const Icon(Icons.my_location),
                  onPressed: _recenterMyPosition,
                ),
              ],
            ),
          ),

          // 5. Bottom Telemetry Bar
          Positioned(
            left: 0,
            right: 0,
            bottom: 0,
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
              decoration: BoxDecoration(
                color: const Color(0xFF070C19).withOpacity(0.95),
                border: const Border(
                  top: BorderSide(color: Color(0xFF1E293B), width: 1.2),
                ),
              ),
              child: SafeArea(
                top: false,
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    // GPS Coords & Movement Stats
                    Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Row(
                          children: [
                            Text(
                              'LAT: ${myLatLng.latitude.toStringAsFixed(4)}° LON: ${myLatLng.longitude.toStringAsFixed(4)}°',
                              style: const TextStyle(
                                color: Color(0xFF38BDF8),
                                fontSize: 11,
                                fontWeight: FontWeight.bold,
                                fontFamily: 'monospace',
                              ),
                            ),
                            if (_currentPosition != null) ...[
                              const SizedBox(width: 6),
                              Container(
                                padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                                decoration: BoxDecoration(
                                  color: (_currentPosition!.accuracy <= _maxAllowedAccuracy
                                      ? const Color(0xFF10B981)
                                      : const Color(0xFFF59E0B)
                                  ).withOpacity(0.18),
                                  borderRadius: BorderRadius.circular(4),
                                  border: Border.all(
                                    color: (_currentPosition!.accuracy <= _maxAllowedAccuracy
                                        ? const Color(0xFF10B981)
                                        : const Color(0xFFF59E0B)
                                    ).withOpacity(0.5),
                                  ),
                                ),
                                child: Text(
                                  '🎯 ±${_currentPosition!.accuracy.toStringAsFixed(1)} ม.',
                                  style: TextStyle(
                                    color: _currentPosition!.accuracy <= _maxAllowedAccuracy
                                        ? const Color(0xFF10B981)
                                        : const Color(0xFFF59E0B),
                                    fontSize: 9,
                                    fontWeight: FontWeight.bold,
                                    fontFamily: 'monospace',
                                  ),
                                ),
                              ),
                            ],
                          ],
                        ),
                        Row(
                          children: [
                            Text(
                              '${widget.profile?.district.isNotEmpty == true ? widget.profile!.district : "ทุกพื้นที่"} • ${widget.incidents.length} จุดเหตุ',
                              style: const TextStyle(
                                color: AppColors.textMuted,
                                fontSize: 10,
                              ),
                            ),
                            if (_trackPoints.isNotEmpty) ...[
                              const Text(' • ', style: TextStyle(color: Colors.white24, fontSize: 10)),
                              Text(
                                '🏃 เดินสะสม: ${(_totalDistanceMeters / 1000).toStringAsFixed(2)} กม. (${_trackPoints.length} จุด)',
                                style: const TextStyle(
                                  color: Color(0xFF10B981),
                                  fontSize: 10,
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                            ],
                          ],
                        ),
                      ],
                    ),

                    // Speed & Heading
                    Row(
                      children: [
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
                          decoration: BoxDecoration(
                            color: const Color(0xFF1E293B),
                            borderRadius: BorderRadius.circular(6),
                          ),
                          child: Text(
                            '${_currentSpeed.round()} KM/H',
                            style: const TextStyle(
                              color: AppColors.warningOrange,
                              fontSize: 10,
                              fontWeight: FontWeight.bold,
                              fontFamily: 'monospace',
                            ),
                          ),
                        ),
                        const SizedBox(width: 6),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
                          decoration: BoxDecoration(
                            color: const Color(0xFF1E293B),
                            borderRadius: BorderRadius.circular(6),
                          ),
                          child: Text(
                            '${_currentHeading.round()}° DIR',
                            style: const TextStyle(
                              color: Color(0xFF38BDF8),
                              fontSize: 10,
                              fontWeight: FontWeight.bold,
                              fontFamily: 'monospace',
                            ),
                          ),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildBasemapButton(String title, BasemapStyle style) {
    final isSelected = _currentBasemap == style;
    return InkWell(
      onTap: () => setState(() => _currentBasemap = style),
      borderRadius: BorderRadius.circular(8),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(
          color: isSelected ? AppColors.primary : Colors.transparent,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Text(
          title,
          style: TextStyle(
            color: isSelected ? Colors.white : Colors.white60,
            fontSize: 11,
            fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
          ),
        ),
      ),
    );
  }
}
