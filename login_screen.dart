import 'dart:convert';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import '../../../../data/models/unit_profile_model.dart';
import '../../../../data/models/user_model.dart';
import '../../../../data/services/api_service.dart';
import '../../../../data/services/storage_service.dart';
import '../../../core/app_colors.dart';
import '../../home/views/home_screen.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  // 0 = เข้าสู่ระบบ (Login), 1 = สมัครสมาชิก (Register)
  int _mainMode = 0;

  // สำหรับโหมดเข้าสู่ระบบ: 0 = PIN/Password, 1 = LINE OTP
  int _loginSubTab = 0;

  // Controllers for Password/PIN Login
  final _phoneController = TextEditingController();
  final _passwordController = TextEditingController();
  bool _obscurePassword = true;

  // Controllers for LINE OTP Login
  final _otpPhoneController = TextEditingController();
  final _otpCodeController = TextEditingController();
  bool _isOtpSent = false;
  bool _isRequestingOtp = false;

  // Controllers & States for Registration
  final _regUsernameController = TextEditingController();
  final _regPasswordController = TextEditingController();
  final _regConfirmPasswordController = TextEditingController();
  bool _obscureRegPassword = true;
  bool _obscureRegConfirmPassword = true;
  String? _regPictureBase64;
  Uint8List? _regImageBytes;

  final List<String> _ranks = [
    'นาย',
    'นาง',
    'นางสาว',
    'ร.ต.',
    'ร.ท.',
    'ร.อ.',
    'พ.ต.',
    'พ.ท.',
    'พ.อ.',
    'พล.ต.',
    'พล.ท.',
    'พล.อ.',
    'ส.ต.',
    'ส.ท.',
    'ส.อ.',
    'จ.ส.อ.',
    'ร.ต.อ.',
    'พ.ต.อ.',
    'ด.ต.',
    'อื่นๆ (ระบุเอง)',
  ];
  String _selectedRank = 'ร.อ.';
  final _regCustomRankController = TextEditingController();
  final _regNameController = TextEditingController();
  final _regPhoneController = TextEditingController();

  final List<String> _units = [
    'กรมแผนที่ทหาร (ผท.บก.ทท.)',
    'กองบริการแผนที่ (กบค.ผท.)',
    'กองเทคโนโลยีภูมิสารสนเทศ (กทภ.ผท.)',
    'ศูนย์ปฏิบัติการร่วมแผนที่ (ศปก.ผท.)',
    'กองบัญชาการกองทัพไทย (บก.ทท.)',
    'กองทัพบก (ทบ.)',
    'กองทัพเรือ (ทร.)',
    'กองทัพอากาศ (ทอ.)',
    'กรมป้องกันและบรรเทาสาธารณภัย (ปภ.)',
    'สำนักงานตำรวจแห่งชาติ (ตร.)',
    'GISTDA (จิสด้า)',
    'อื่นๆ (ระบุหน่วยงานเอง)',
  ];
  String _selectedUnit = 'กรมแผนที่ทหาร (ผท.บก.ทท.)';
  final _regCustomUnitController = TextEditingController();
  final _regPositionController = TextEditingController(text: 'หัวหน้าชุดปฏิบัติการ');
  final _regAreaController = TextEditingController(text: 'เชียงราย (ทุกอำเภอ)');

  // ขนาดยูนิต และ ยานพาหนะ (ตามที่ผู้ใช้ร้องขอ)
  final List<String> _unitSizes = [
    'ชุดปฏิบัติการขนาดเล็ก (3-5 นาย)',
    'หมู่ / หมวดปฏิบัติการ (6-15 นาย)',
    'กองร้อย / ชุดเคลื่อนที่เร็ว (16-50 นาย)',
    'กองพัน / บก.ควบคุม (50+ นาย)',
    'เจ้าหน้าที่เดี่ยว / ลาดตระเวนคู่ (1-2 นาย)',
  ];
  String _selectedUnitSize = 'ชุดปฏิบัติการขนาดเล็ก (3-5 นาย)';

  final List<String> _vehicleTypes = [
    '🚗 รถกระบะตรวจการณ์ 4x4 (Pickup 4WD)',
    '🚚 รถบรรทุกทหาร 6 ล้อ / ขนส่งพล',
    '🚑 รถพยาบาล / กู้ภัยฉุกเฉิน (Ambulance)',
    '🏍️ รถจักรยานยนต์ลาดตระเวน (Motorcycle)',
    '🚤 เรือท้องแบนกู้ภัย (Flat-bottom Boat)',
    '🛶 เรือยางกู้ภัยติดเครื่องยนต์ (Inflatable Boat)',
    '🛥️ เรือเร็วตรวจการณ์ (Patrol Speedboat)',
    '🚁 โดรนสำรวจ / อากาศยานไร้คนขับ (UAV Drone)',
    '🚶 ชุดเดินเท้า / ลาดตระเวนเดินเท้า (Foot Patrol)',
  ];
  String _selectedVehicleType = '🚗 รถกระบะตรวจการณ์ 4x4 (Pickup 4WD)';

  bool _isLoading = false;
  String? _errorMessage;

  final ApiService _apiService = ApiService();

  @override
  void dispose() {
    _phoneController.dispose();
    _passwordController.dispose();
    _otpPhoneController.dispose();
    _otpCodeController.dispose();
    _regUsernameController.dispose();
    _regPasswordController.dispose();
    _regConfirmPasswordController.dispose();
    _regCustomRankController.dispose();
    _regNameController.dispose();
    _regPhoneController.dispose();
    _regCustomUnitController.dispose();
    _regPositionController.dispose();
    _regAreaController.dispose();
    super.dispose();
  }

  Future<void> _pickRegisterImage() async {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surfaceCard,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
      ),
      builder: (ctx) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.symmetric(vertical: 16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Container(
                width: 40,
                height: 4,
                margin: const EdgeInsets.only(bottom: 16),
                decoration: BoxDecoration(
                  color: Colors.white24,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
              const Text(
                'เลือกรูปภาพโปรไฟล์',
                style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold),
              ),
              const SizedBox(height: 14),
              ListTile(
                leading: Container(
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: Colors.cyan.withOpacity(0.15),
                    shape: BoxShape.circle,
                  ),
                  child: const Icon(Icons.camera_alt, color: Colors.cyan),
                ),
                title: const Text('ถ่ายรูปด้วยกล้อง (Camera)', style: TextStyle(color: Colors.white, fontSize: 14)),
                onTap: () {
                  Navigator.pop(ctx);
                  _processPickedImage(ImageSource.camera);
                },
              ),
              ListTile(
                leading: Container(
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: Colors.amber.withOpacity(0.15),
                    shape: BoxShape.circle,
                  ),
                  child: const Icon(Icons.photo_library, color: Colors.amber),
                ),
                title: const Text('เลือกจากคลังภาพ (Gallery)', style: TextStyle(color: Colors.white, fontSize: 14)),
                onTap: () {
                  Navigator.pop(ctx);
                  _processPickedImage(ImageSource.gallery);
                },
              ),
              if (_regImageBytes != null) ...[
                const Divider(color: AppColors.border),
                ListTile(
                  leading: Container(
                    padding: const EdgeInsets.all(8),
                    decoration: BoxDecoration(
                      color: Colors.red.withOpacity(0.15),
                      shape: BoxShape.circle,
                    ),
                    child: const Icon(Icons.delete_outline, color: Colors.redAccent),
                  ),
                  title: const Text('ลบรูปภาพที่เลือก', style: TextStyle(color: Colors.redAccent, fontSize: 14)),
                  onTap: () {
                    Navigator.pop(ctx);
                    setState(() {
                      _regImageBytes = null;
                      _regPictureBase64 = null;
                    });
                  },
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _processPickedImage(ImageSource source) async {
    try {
      final picker = ImagePicker();
      final picked = await picker.pickImage(
        source: source,
        maxWidth: 400,
        maxHeight: 400,
        imageQuality: 85,
      );
      if (picked == null) return;
      final bytes = await picked.readAsBytes();
      final b64 = base64Encode(bytes);
      setState(() {
        _regImageBytes = bytes;
        _regPictureBase64 = b64;
      });
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text('ไม่สามารถเลือกรูปได้: $e'), backgroundColor: AppColors.dangerRed),
      );
    }
  }

  Future<void> _handleLoginSuccess(UserModel user) async {
    await StorageService.saveUser(user);

    // ซิงค์ UnitProfile ให้ตรงกับข้อมูลผู้ใช้ที่ล็อกอิน รวมถึงพื้นที่รับผิดชอบ ขนาดหน่วย และยานพาหนะ
    final existingProfile = await StorageService.getUnitProfile();
    final userArea = user.area.isNotEmpty ? user.area : (existingProfile?.district ?? 'เชียงราย (ทุกอำเภอ)');
    final newProfile = UnitProfileModel(
      unitId: existingProfile?.unitId ?? 'RTSD-${user.phone.length >= 4 ? user.phone.substring(user.phone.length - 4) : "01"}',
      district: userArea,
      unitName: user.unit.isNotEmpty ? user.unit : (existingProfile?.unitName ?? 'ชุดเคลื่อนที่เร็ว บรรเทาสาธารณภัย'),
      commander: user.name,
      phoneNumber: user.phone,
      unitSize: user.unitSize.isNotEmpty ? user.unitSize : (existingProfile?.unitSize ?? _selectedUnitSize),
      vehicleType: user.vehicleType.isNotEmpty ? user.vehicleType : (existingProfile?.vehicleType ?? _selectedVehicleType),
    );
    await StorageService.saveUnitProfile(newProfile);

    if (!mounted) return;

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text('✅ เข้าสู่ระบบสำเร็จ: ${user.name} (${user.role})'),
        backgroundColor: AppColors.accentGreen,
        duration: const Duration(seconds: 2),
      ),
    );

    Navigator.pushReplacement(
      context,
      MaterialPageRoute(builder: (_) => const HomeScreen()),
    );
  }

  // 1. Password / PIN Login
  Future<void> _handlePasswordLogin() async {
    final phone = _phoneController.text.trim();
    final password = _passwordController.text.trim();

    if (phone.isEmpty) {
      setState(() => _errorMessage = 'กรุณาระบุหมายเลขโทรศัพท์ หรือ Username');
      return;
    }
    if (password.isEmpty) {
      setState(() => _errorMessage = 'กรุณาระบุรหัสผ่าน หรือ Tactical PIN');
      return;
    }

    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    final result = await _apiService.login(
      usernameOrPhone: phone,
      passwordOrPin: password,
    );

    if (!mounted) return;

    if (result['success'] == true) {
      await _handleLoginSuccess(result['user'] as UserModel);
    } else {
      final msg = result['message']?.toString() ?? 'เข้าสู่ระบบไม่สำเร็จ';
      setState(() {
        _isLoading = false;
        _errorMessage = msg;
      });

      if (msg.contains('รออนุมัติ') || msg.toLowerCase().contains('pending')) {
        _showPendingApprovalDialog();
      }
    }
  }

  // 2. Request OTP via LINE
  Future<void> _handleRequestOtp() async {
    final phone = _otpPhoneController.text.trim().replaceAll(RegExp(r'[-\s]'), '');

    if (phone.isEmpty || phone.length < 9) {
      setState(() => _errorMessage = 'กรุณาระบุหมายเลขโทรศัพท์ 9-10 หลัก');
      return;
    }

    setState(() {
      _isRequestingOtp = true;
      _errorMessage = null;
    });

    final result = await _apiService.requestOtp(phone);

    if (!mounted) return;

    setState(() {
      _isRequestingOtp = false;
    });

    if (result['success'] == true) {
      setState(() {
        _isOtpSent = true;
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('📲 ${result['message']}'),
          backgroundColor: Colors.teal,
          duration: const Duration(seconds: 3),
        ),
      );
    } else {
      final msg = result['message']?.toString() ?? 'ไม่สามารถส่ง OTP ได้';
      setState(() => _errorMessage = msg);
      if (msg.contains('รออนุมัติ') || msg.toLowerCase().contains('pending')) {
        _showPendingApprovalDialog();
      }
    }
  }

  // 3. Verify OTP
  Future<void> _handleVerifyOtp() async {
    final phone = _otpPhoneController.text.trim().replaceAll(RegExp(r'[-\s]'), '');
    final otp = _otpCodeController.text.trim();

    if (otp.length < 4) {
      setState(() => _errorMessage = 'กรุณาระบุรหัส OTP ให้ครบถ้วน');
      return;
    }

    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    final result = await _apiService.verifyOtp(phone: phone, otp: otp);

    if (!mounted) return;

    if (result['success'] == true) {
      await _handleLoginSuccess(result['user'] as UserModel);
    } else {
      final msg = result['message']?.toString() ?? 'รหัส OTP ไม่ถูกต้อง';
      setState(() {
        _isLoading = false;
        _errorMessage = msg;
      });
      if (msg.contains('รออนุมัติ') || msg.toLowerCase().contains('pending')) {
        _showPendingApprovalDialog();
      }
    }
  }

  // 4. Registration Submission
  Future<void> _handleRegister() async {
    final username = _regUsernameController.text.trim().toLowerCase().replaceAll(RegExp(r'\s+'), '');
    final password = _regPasswordController.text.trim();
    final confirmPassword = _regConfirmPasswordController.text.trim();
    final rawName = _regNameController.text.trim();
    final phone = _regPhoneController.text.trim().replaceAll(RegExp(r'[-\s]'), '');
    final rank = _selectedRank == 'อื่นๆ (ระบุเอง)'
        ? _regCustomRankController.text.trim()
        : _selectedRank;
    final unit = _selectedUnit == 'อื่นๆ (ระบุหน่วยงานเอง)'
        ? _regCustomUnitController.text.trim()
        : _selectedUnit;
    final position = _regPositionController.text.trim();

    if (username.isEmpty || username.length < 3) {
      setState(() => _errorMessage = 'กรุณาระบุ Username อย่างน้อย 3 ตัวอักษร (เช่น somchai99)');
      return;
    }
    if (password.isEmpty || password.length < 4) {
      setState(() => _errorMessage = 'กรุณาระบุรหัสผ่าน (Password / PIN) อย่างน้อย 4 ตัวอักษร');
      return;
    }
    if (password != confirmPassword) {
      setState(() => _errorMessage = 'รหัสผ่านและยืนยันรหัสผ่านไม่ตรงกัน');
      return;
    }
    if (rawName.isEmpty) {
      setState(() => _errorMessage = 'กรุณาระบุชื่อ - นามสกุล');
      return;
    }
    if (phone.isEmpty || phone.length < 9 || phone.length > 10) {
      setState(() => _errorMessage = 'กรุณาระบุหมายเลขโทรศัพท์ 10 หลักที่ถูกต้อง');
      return;
    }
    if (unit.isEmpty) {
      setState(() => _errorMessage = 'กรุณาระบุหน่วยงาน / สังกัด');
      return;
    }

    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    final fullName = rank.isNotEmpty ? '$rank $rawName'.trim() : rawName;
    final area = _regAreaController.text.trim().isNotEmpty
        ? _regAreaController.text.trim()
        : 'เชียงราย (ทุกอำเภอ)';

    final result = await _apiService.registerUser(
      fullName: fullName,
      phone: phone,
      username: username,
      password: password,
      unit: unit,
      position: position.isNotEmpty ? position : 'เจ้าหน้าที่สนาม',
      area: area,
      unitSize: _selectedUnitSize,
      vehicleType: _selectedVehicleType,
      pictureBase64: _regPictureBase64,
    );

    // บันทึก UnitProfile เบื้องต้นที่มีพื้นที่รับผิดชอบ ขนาดหน่วย และยานพาหนะ
    final profile = UnitProfileModel(
      unitId: 'RTSD-${phone.length >= 4 ? phone.substring(phone.length - 4) : "01"}',
      district: area,
      unitName: unit,
      commander: fullName,
      phoneNumber: phone,
      unitSize: _selectedUnitSize,
      vehicleType: _selectedVehicleType,
    );
    await StorageService.saveUnitProfile(profile);

    if (!mounted) return;

    setState(() => _isLoading = false);

    if (result['success'] == true) {
      showDialog(
        context: context,
        builder: (ctx) => AlertDialog(
          backgroundColor: AppColors.surfaceCard,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(16),
            side: const BorderSide(color: AppColors.accentGreen, width: 1.5),
          ),
          title: const Row(
            children: [
              Icon(Icons.check_circle_outline, color: AppColors.accentGreen, size: 28),
              SizedBox(width: 8),
              Text(
                'ยื่นคำขอสำเร็จ',
                style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold),
              ),
            ],
          ),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'ยื่นคำขอลงทะเบียนของ คุณ$fullName (${phone})\nUsername: $username\nสังกัด: $unit\nขนาดหน่วย: $_selectedUnitSize\nยานพาหนะ: $_selectedVehicleType\nเรียบร้อยแล้วครับ',
                style: const TextStyle(color: Colors.white70, fontSize: 13, height: 1.4),
              ),
              const SizedBox(height: 12),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: Colors.amber.withOpacity(0.12),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: Colors.amber.withOpacity(0.4)),
                ),
                child: const Row(
                  children: [
                    Icon(Icons.info_outline, color: Colors.amber, size: 18),
                    SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        'ระบบได้บันทึก Username & รหัสผ่านของท่านเรียบร้อยแล้ว เมื่อแอดมินอนุมัติสิทธิ์จะสามารถใช้ล็อกอินได้ทันทีครับ',
                        style: TextStyle(color: Colors.amber, fontSize: 12),
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
          actions: [
            ElevatedButton(
              style: ElevatedButton.styleFrom(backgroundColor: AppColors.primary),
              onPressed: () {
                Navigator.pop(ctx);
                setState(() {
                  _mainMode = 0; // สลับกลับมาหน้า Login
                  _phoneController.text = username;
                  _passwordController.text = password;
                  _errorMessage = null;
                });
              },
              child: const Text('ตกลง (ไปหน้าเข้าสู่ระบบ)', style: TextStyle(color: Colors.white)),
            ),
          ],
        ),
      );
    } else {
      setState(() => _errorMessage = result['message']?.toString() ?? 'เกิดข้อผิดพลาดในการลงทะเบียน');
    }
  }

  void _showPendingApprovalDialog() {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppColors.surfaceCard,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: const BorderSide(color: Colors.amber, width: 1.5),
        ),
        title: const Row(
          children: [
            Icon(Icons.hourglass_top, color: Colors.amber, size: 26),
            SizedBox(width: 10),
            Expanded(
              child: Text(
                'อยู่ระหว่างรอการอนุมัติ',
                style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold),
              ),
            ),
          ],
        ),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text(
              'บัญชีผู้ใช้งานของท่านยื่นคำขอลงทะเบียนเรียบร้อยแล้ว แต่อยู่ระหว่างรอผู้ดูแลระบบ (Admin) ตรวจสอบและอนุมัติสิทธิ์การเข้าใช้งาน',
              style: TextStyle(color: Colors.white70, fontSize: 13, height: 1.5),
            ),
            const SizedBox(height: 12),
            Container(
              padding: const EdgeInsets.all(10),
              decoration: BoxDecoration(
                color: Colors.amber.withOpacity(0.1),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: Colors.amber.withOpacity(0.3)),
              ),
              child: const Row(
                children: [
                  Icon(Icons.info_outline, color: Colors.amber, size: 18),
                  SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'เมื่อแอดมินอนุมัติสิทธิ์เรียบร้อย ท่านจะได้รับการแจ้งเตือนทาง LINE Bot และสามารถเข้าใช้งานได้ทันทีครับ',
                      style: TextStyle(color: Colors.amber, fontSize: 12),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            const Text(
              '📞 สอบถามแอดมิน: 086-339-0614 (ผบ.ชา RTSD)',
              style: TextStyle(color: Colors.white60, fontSize: 12),
            ),
          ],
        ),
        actions: [
          ElevatedButton(
            style: ElevatedButton.styleFrom(
              backgroundColor: AppColors.primary,
              foregroundColor: Colors.white,
            ),
            onPressed: () => Navigator.pop(ctx),
            child: const Text('รับทราบ'),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 20),
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                // 🛡️ RTSD Header Icon & Title
                Center(
                  child: Container(
                    padding: const EdgeInsets.all(16),
                    decoration: BoxDecoration(
                      color: AppColors.primary.withOpacity(0.12),
                      shape: BoxShape.circle,
                      border: Border.all(color: AppColors.primaryLight.withOpacity(0.4), width: 1.5),
                      boxShadow: [
                        BoxShadow(
                          color: AppColors.primary.withOpacity(0.2),
                          blurRadius: 20,
                          spreadRadius: 2,
                        )
                      ],
                    ),
                    child: const Icon(
                      Icons.shield,
                      size: 48,
                      color: AppColors.primaryLight,
                    ),
                  ),
                ),
                const SizedBox(height: 14),
                const Center(
                  child: Text(
                    'RTSD COMMAND & CONTROL',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 19,
                      fontWeight: FontWeight.bold,
                      letterSpacing: 1.1,
                    ),
                  ),
                ),
                const SizedBox(height: 4),
                const Center(
                  child: Text(
                    'ระบบสารสนเทศภูมิศาสตร์ & ปฏิบัติการยุทธการภาคสนาม',
                    style: TextStyle(
                      color: AppColors.textMuted,
                      fontSize: 12.5,
                    ),
                  ),
                ),
                const SizedBox(height: 20),

                // 🔄 MAIN SEGMENT SWITCHER: เข้าสู่ระบบ vs สมัครสมาชิก
                Container(
                  padding: const EdgeInsets.all(4),
                  decoration: BoxDecoration(
                    color: AppColors.surfaceCard,
                    borderRadius: BorderRadius.circular(14),
                    border: Border.all(color: AppColors.border),
                  ),
                  child: Row(
                    children: [
                      Expanded(
                        child: GestureDetector(
                          onTap: () => setState(() {
                            _mainMode = 0;
                            _errorMessage = null;
                          }),
                          child: Container(
                            padding: const EdgeInsets.symmetric(vertical: 11),
                            decoration: BoxDecoration(
                              color: _mainMode == 0 ? AppColors.primary : Colors.transparent,
                              borderRadius: BorderRadius.circular(10),
                            ),
                            child: Row(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                Icon(
                                  Icons.login,
                                  size: 17,
                                  color: _mainMode == 0 ? Colors.white : AppColors.textMuted,
                                ),
                                const SizedBox(width: 6),
                                Text(
                                  'เข้าสู่ระบบ (Login)',
                                  style: TextStyle(
                                    color: _mainMode == 0 ? Colors.white : AppColors.textMuted,
                                    fontSize: 13,
                                    fontWeight: FontWeight.bold,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ),
                      Expanded(
                        child: GestureDetector(
                          onTap: () => setState(() {
                            _mainMode = 1;
                            _errorMessage = null;
                          }),
                          child: Container(
                            padding: const EdgeInsets.symmetric(vertical: 11),
                            decoration: BoxDecoration(
                              color: _mainMode == 1 ? Colors.cyan.shade700 : Colors.transparent,
                              borderRadius: BorderRadius.circular(10),
                            ),
                            child: Row(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                Icon(
                                  Icons.person_add_alt_1,
                                  size: 17,
                                  color: _mainMode == 1 ? Colors.white : AppColors.textMuted,
                                ),
                                const SizedBox(width: 6),
                                Text(
                                  'สมัครสมาชิก (Register)',
                                  style: TextStyle(
                                    color: _mainMode == 1 ? Colors.white : AppColors.textMuted,
                                    fontSize: 13,
                                    fontWeight: FontWeight.bold,
                                  ),
                                ),
                              ],
                            ),
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 18),

                // ⚠️ Error Message Box
                if (_errorMessage != null) ...[
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: AppColors.dangerRed.withOpacity(0.15),
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(color: AppColors.dangerRed.withOpacity(0.5)),
                    ),
                    child: Row(
                      children: [
                        const Icon(Icons.error_outline, color: AppColors.dangerRed, size: 20),
                        const SizedBox(width: 10),
                        Expanded(
                          child: Text(
                            _errorMessage!,
                            style: const TextStyle(color: AppColors.dangerRed, fontSize: 13),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 16),
                ],

                // ─────────────────────────────────────────────
                // 1. SECTION: เข้าสู่ระบบ (LOGIN)
                // ─────────────────────────────────────────────
                if (_mainMode == 0) ...[
                  // ตัวเลือกย่อย: รหัสผ่าน/PIN vs LINE OTP
                  Container(
                    padding: const EdgeInsets.all(3),
                    decoration: BoxDecoration(
                      color: AppColors.surface,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Row(
                      children: [
                        Expanded(
                          child: GestureDetector(
                            onTap: () => setState(() {
                              _loginSubTab = 0;
                              _errorMessage = null;
                            }),
                            child: Container(
                              padding: const EdgeInsets.symmetric(vertical: 8),
                              decoration: BoxDecoration(
                                color: _loginSubTab == 0 ? AppColors.surfaceCard : Colors.transparent,
                                borderRadius: BorderRadius.circular(8),
                                border: _loginSubTab == 0 ? Border.all(color: AppColors.primaryLight.withOpacity(0.6)) : null,
                              ),
                              child: Center(
                                child: Text(
                                  '🔑 รหัสผ่าน / PIN',
                                  style: TextStyle(
                                    color: _loginSubTab == 0 ? Colors.white : AppColors.textMuted,
                                    fontSize: 12,
                                    fontWeight: FontWeight.w600,
                                  ),
                                ),
                              ),
                            ),
                          ),
                        ),
                        Expanded(
                          child: GestureDetector(
                            onTap: () => setState(() {
                              _loginSubTab = 1;
                              _errorMessage = null;
                            }),
                            child: Container(
                              padding: const EdgeInsets.symmetric(vertical: 8),
                              decoration: BoxDecoration(
                                color: _loginSubTab == 1 ? AppColors.surfaceCard : Colors.transparent,
                                borderRadius: BorderRadius.circular(8),
                                border: _loginSubTab == 1 ? Border.all(color: Colors.teal.withOpacity(0.6)) : null,
                              ),
                              child: Center(
                                child: Text(
                                  '💬 รับ OTP ผ่าน LINE',
                                  style: TextStyle(
                                    color: _loginSubTab == 1 ? Colors.tealAccent : AppColors.textMuted,
                                    fontSize: 12,
                                    fontWeight: FontWeight.w600,
                                  ),
                                ),
                              ),
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 18),

                  // SUBTAB 0: Password / PIN
                  if (_loginSubTab == 0) ...[
                    const Text(
                      'เบอร์โทรศัพท์ หรือ รหัสผู้ใช้งาน',
                      style: TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600),
                    ),
                    const SizedBox(height: 8),
                    TextField(
                      controller: _phoneController,
                      keyboardType: TextInputType.text,
                      style: const TextStyle(color: Colors.white),
                      decoration: InputDecoration(
                        prefixIcon: const Icon(Icons.person_outline, color: AppColors.textMuted),
                        hintText: 'กรอกเบอร์โทร 10 หลัก หรือ Username',
                        hintStyle: const TextStyle(color: AppColors.textMuted),
                        filled: true,
                        fillColor: AppColors.surface,
                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: AppColors.border)),
                        enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: AppColors.border)),
                        focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: AppColors.primaryLight, width: 1.5)),
                      ),
                    ),
                    const SizedBox(height: 16),

                    const Text(
                      'รหัสผ่าน หรือ Tactical PIN',
                      style: TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600),
                    ),
                    const SizedBox(height: 8),
                    TextField(
                      controller: _passwordController,
                      obscureText: _obscurePassword,
                      style: const TextStyle(color: Colors.white),
                      decoration: InputDecoration(
                        prefixIcon: const Icon(Icons.lock_outline, color: AppColors.textMuted),
                        suffixIcon: IconButton(
                          icon: Icon(
                            _obscurePassword ? Icons.visibility_off : Icons.visibility,
                            color: AppColors.textMuted,
                          ),
                          onPressed: () => setState(() => _obscurePassword = !_obscurePassword),
                        ),
                        hintText: 'กรอกรหัสผ่าน หรือ Tactical PIN',
                        hintStyle: const TextStyle(color: AppColors.textMuted),
                        filled: true,
                        fillColor: AppColors.surface,
                        border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: AppColors.border)),
                        enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: AppColors.border)),
                        focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: AppColors.primaryLight, width: 1.5)),
                      ),
                    ),
                    const SizedBox(height: 22),

                    ElevatedButton(
                      onPressed: _isLoading ? null : _handlePasswordLogin,
                      style: ElevatedButton.styleFrom(
                        backgroundColor: AppColors.primary,
                        disabledBackgroundColor: AppColors.primary.withOpacity(0.5),
                        padding: const EdgeInsets.symmetric(vertical: 14),
                        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                        elevation: 4,
                      ),
                      child: _isLoading
                          ? const SizedBox(height: 22, width: 22, child: CircularProgressIndicator(strokeWidth: 2.5, color: Colors.white))
                          : const Row(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                Icon(Icons.login, size: 20, color: Colors.white),
                                SizedBox(width: 8),
                                Text(
                                  'เข้าสู่ระบบยุทธการ',
                                  style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.white),
                                ),
                              ],
                            ),
                    ),
                  ],

                  // SUBTAB 1: LINE OTP
                  if (_loginSubTab == 1) ...[
                    const Text(
                      'เบอร์โทรศัพท์ (ที่ผูกกับ LINE Bot)',
                      style: TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600),
                    ),
                    const SizedBox(height: 8),
                    Row(
                      children: [
                        Expanded(
                          child: TextField(
                            controller: _otpPhoneController,
                            keyboardType: TextInputType.phone,
                            style: const TextStyle(color: Colors.white),
                            decoration: InputDecoration(
                              prefixIcon: const Icon(Icons.phone_outlined, color: AppColors.textMuted),
                              hintText: '08xxxxxxxx',
                              hintStyle: const TextStyle(color: AppColors.textMuted),
                              filled: true,
                              fillColor: AppColors.surface,
                              border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: AppColors.border)),
                              enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: AppColors.border)),
                              focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: Colors.teal, width: 1.5)),
                            ),
                          ),
                        ),
                        const SizedBox(width: 10),
                        ElevatedButton(
                          onPressed: _isRequestingOtp ? null : _handleRequestOtp,
                          style: ElevatedButton.styleFrom(
                            backgroundColor: Colors.teal,
                            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 16),
                            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                          ),
                          child: _isRequestingOtp
                              ? const SizedBox(height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                              : const Text('ขอ OTP', style: TextStyle(fontWeight: FontWeight.bold, color: Colors.white)),
                        ),
                      ],
                    ),
                    const SizedBox(height: 10),
                    const Text(
                      '💡 รหัส OTP จะส่งเข้าห้องแชต LINE ของท่านโดยตรง (ฟรี ไม่มีค่าบริการ SMS)',
                      style: TextStyle(color: Colors.tealAccent, fontSize: 11),
                    ),
                    const SizedBox(height: 16),

                    if (_isOtpSent) ...[
                      const Text(
                        'รหัส OTP 6 หลักที่ได้รับจาก LINE',
                        style: TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600),
                      ),
                      const SizedBox(height: 8),
                      TextField(
                        controller: _otpCodeController,
                        keyboardType: TextInputType.number,
                        textAlign: TextAlign.center,
                        maxLength: 6,
                        style: const TextStyle(
                          color: Colors.greenAccent,
                          fontSize: 22,
                          letterSpacing: 8,
                          fontWeight: FontWeight.bold,
                          fontFamily: 'monospace',
                        ),
                        decoration: InputDecoration(
                          counterText: '',
                          hintText: '••••••',
                          hintStyle: const TextStyle(color: AppColors.textMuted, letterSpacing: 8),
                          filled: true,
                          fillColor: AppColors.surface,
                          border: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: AppColors.border)),
                          enabledBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: Colors.teal)),
                          focusedBorder: OutlineInputBorder(borderRadius: BorderRadius.circular(12), borderSide: const BorderSide(color: Colors.greenAccent, width: 2)),
                        ),
                      ),
                      const SizedBox(height: 16),

                      ElevatedButton(
                        onPressed: _isLoading ? null : _handleVerifyOtp,
                        style: ElevatedButton.styleFrom(
                          backgroundColor: Colors.teal,
                          disabledBackgroundColor: Colors.teal.withOpacity(0.5),
                          padding: const EdgeInsets.symmetric(vertical: 14),
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                          elevation: 4,
                        ),
                        child: _isLoading
                            ? const SizedBox(height: 22, width: 22, child: CircularProgressIndicator(strokeWidth: 2.5, color: Colors.white))
                            : const Row(
                                mainAxisAlignment: MainAxisAlignment.center,
                                children: [
                                  Icon(Icons.lock_open, size: 20, color: Colors.white),
                                  SizedBox(width: 8),
                                  Text(
                                    'ยืนยัน OTP และเข้าสู่ระบบ',
                                    style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.white),
                                  ),
                                ],
                              ),
                      ),
                    ],
                  ],

                  const SizedBox(height: 24),
                  Center(
                    child: TextButton.icon(
                      style: TextButton.styleFrom(foregroundColor: AppColors.primaryLight),
                      icon: const Icon(Icons.person_add_alt_1_outlined, size: 16),
                      label: const Text(
                        'ยังไม่มีบัญชีผู้ใช้? กดสมัครสมาชิกใหม่ที่นี่',
                        style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600, decoration: TextDecoration.underline),
                      ),
                      onPressed: () => setState(() {
                        _mainMode = 1;
                        _errorMessage = null;
                      }),
                    ),
                  ),
                ],

                // ─────────────────────────────────────────────
                // 2. SECTION: สมัครสมาชิก (REGISTER)
                // ─────────────────────────────────────────────
                if (_mainMode == 1) ...[
                  Container(
                    padding: const EdgeInsets.all(16),
                    decoration: BoxDecoration(
                      color: AppColors.surfaceCard,
                      borderRadius: BorderRadius.circular(16),
                      border: Border.all(color: Colors.cyan.withOpacity(0.35)),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Row(
                          children: [
                            Icon(Icons.badge_outlined, color: Colors.cyan, size: 20),
                            SizedBox(width: 8),
                            Text(
                              'ข้อมูลกำลังพล & บัญชีผู้ใช้',
                              style: TextStyle(color: Colors.white, fontSize: 14, fontWeight: FontWeight.bold),
                            ),
                          ],
                        ),
                        const Divider(color: AppColors.border, height: 18),

                        // 📷 รูปภาพโปรไฟล์ (เลือกจากกล้อง หรือ แกลเลอรี)
                        Center(
                          child: Column(
                            children: [
                              Stack(
                                alignment: Alignment.bottomRight,
                                children: [
                                  CircleAvatar(
                                    radius: 42,
                                    backgroundColor: Colors.cyan.withOpacity(0.15),
                                    backgroundImage: _regImageBytes != null
                                        ? MemoryImage(_regImageBytes!)
                                        : null,
                                    child: _regImageBytes == null
                                        ? const Icon(Icons.person, size: 48, color: Colors.cyan)
                                        : null,
                                  ),
                                  GestureDetector(
                                    onTap: _pickRegisterImage,
                                    child: Container(
                                      padding: const EdgeInsets.all(7),
                                      decoration: BoxDecoration(
                                        color: Colors.cyan.shade600,
                                        shape: BoxShape.circle,
                                        border: Border.all(color: AppColors.surfaceCard, width: 2.5),
                                        boxShadow: [
                                          BoxShadow(
                                            color: Colors.black.withOpacity(0.4),
                                            blurRadius: 4,
                                          ),
                                        ],
                                      ),
                                      child: const Icon(Icons.camera_alt, size: 16, color: Colors.white),
                                    ),
                                  ),
                                ],
                              ),
                              const SizedBox(height: 8),
                              OutlinedButton.icon(
                                style: OutlinedButton.styleFrom(
                                  foregroundColor: Colors.cyanAccent,
                                  side: BorderSide(color: Colors.cyan.withOpacity(0.5)),
                                  padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
                                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                                ),
                                icon: Icon(
                                  _regImageBytes == null ? Icons.add_a_photo_outlined : Icons.edit,
                                  size: 15,
                                ),
                                label: Text(
                                  _regImageBytes == null ? '📷 เลือกรูปถ่ายโปรไฟล์ (กล้อง / คลังภาพ)' : 'เปลี่ยนรูปโปรไฟล์',
                                  style: const TextStyle(fontSize: 12),
                                ),
                                onPressed: _pickRegisterImage,
                              ),
                            ],
                          ),
                        ),
                        const SizedBox(height: 16),

                        // 🔐 ข้อมูลบัญชีผู้ใช้ (สำหรับล็อกอิน)
                        const Row(
                          children: [
                            Icon(Icons.lock_person_outlined, color: Colors.amberAccent, size: 17),
                            SizedBox(width: 6),
                            Text(
                              'บัญชีผู้ใช้งานสำหรับล็อกอิน (Login Credentials)',
                              style: TextStyle(color: Colors.amberAccent, fontSize: 13, fontWeight: FontWeight.bold),
                            ),
                          ],
                        ),
                        const SizedBox(height: 8),

                        // Username
                        const Text('ชื่อผู้ใช้งาน (Username) *', style: TextStyle(color: Colors.white70, fontSize: 12)),
                        const SizedBox(height: 6),
                        TextField(
                          controller: _regUsernameController,
                          keyboardType: TextInputType.text,
                          style: const TextStyle(color: Colors.white, fontSize: 13),
                          decoration: InputDecoration(
                            prefixIcon: const Icon(Icons.alternate_email, size: 18, color: Colors.amberAccent),
                            hintText: 'เช่น somchai99 (ใช้สำหรับล็อกอินเข้าแอป)',
                            hintStyle: const TextStyle(color: AppColors.textMuted),
                            filled: true,
                            fillColor: AppColors.surface,
                            border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: AppColors.border)),
                          ),
                        ),
                        const SizedBox(height: 12),

                        // Password
                        const Text('รหัสผ่าน (Password / PIN) *', style: TextStyle(color: Colors.white70, fontSize: 12)),
                        const SizedBox(height: 6),
                        TextField(
                          controller: _regPasswordController,
                          obscureText: _obscureRegPassword,
                          style: const TextStyle(color: Colors.white, fontSize: 13),
                          decoration: InputDecoration(
                            prefixIcon: const Icon(Icons.lock_outline, size: 18, color: Colors.amberAccent),
                            suffixIcon: IconButton(
                              icon: Icon(
                                _obscureRegPassword ? Icons.visibility_off : Icons.visibility,
                                color: AppColors.textMuted,
                                size: 18,
                              ),
                              onPressed: () => setState(() => _obscureRegPassword = !_obscureRegPassword),
                            ),
                            hintText: 'ตั้งรหัสผ่านอย่างน้อย 4 ตัวอักษร หรือ PIN 6 หลัก',
                            hintStyle: const TextStyle(color: AppColors.textMuted),
                            filled: true,
                            fillColor: AppColors.surface,
                            border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: AppColors.border)),
                          ),
                        ),
                        const SizedBox(height: 12),

                        // Confirm Password
                        const Text('ยืนยันรหัสผ่าน (Confirm Password) *', style: TextStyle(color: Colors.white70, fontSize: 12)),
                        const SizedBox(height: 6),
                        TextField(
                          controller: _regConfirmPasswordController,
                          obscureText: _obscureRegConfirmPassword,
                          style: const TextStyle(color: Colors.white, fontSize: 13),
                          decoration: InputDecoration(
                            prefixIcon: const Icon(Icons.lock_reset, size: 18, color: Colors.amberAccent),
                            suffixIcon: IconButton(
                              icon: Icon(
                                _obscureRegConfirmPassword ? Icons.visibility_off : Icons.visibility,
                                color: AppColors.textMuted,
                                size: 18,
                              ),
                              onPressed: () => setState(() => _obscureRegConfirmPassword = !_obscureRegConfirmPassword),
                            ),
                            hintText: 'กรอกรหัสผ่านซ้ำอีกครั้งเพื่อยืนยัน',
                            hintStyle: const TextStyle(color: AppColors.textMuted),
                            filled: true,
                            fillColor: AppColors.surface,
                            border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: AppColors.border)),
                          ),
                        ),
                        const SizedBox(height: 18),
                        const Divider(color: AppColors.border, height: 1),
                        const SizedBox(height: 16),

                        const Row(
                          children: [
                            Icon(Icons.badge, color: Colors.cyan, size: 17),
                            SizedBox(width: 6),
                            Text(
                              'ข้อมูลประจำตัวกำลังพล & สังกัด',
                              style: TextStyle(color: Colors.cyan, fontSize: 13, fontWeight: FontWeight.bold),
                            ),
                          ],
                        ),
                        const SizedBox(height: 10),

                        // ยศ / คำนำหน้า
                        const Text('ยศ / คำนำหน้า', style: TextStyle(color: Colors.white70, fontSize: 12)),
                        const SizedBox(height: 6),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 14),
                          decoration: BoxDecoration(
                            color: AppColors.surface,
                            borderRadius: BorderRadius.circular(10),
                            border: Border.all(color: AppColors.border),
                          ),
                          child: DropdownButtonHideUnderline(
                            child: DropdownButton<String>(
                              value: _selectedRank,
                              dropdownColor: AppColors.surfaceCard,
                              isExpanded: true,
                              style: const TextStyle(color: Colors.white, fontSize: 13),
                              items: _ranks.map((r) => DropdownMenuItem(value: r, child: Text(r))).toList(),
                              onChanged: (val) {
                                if (val != null) setState(() => _selectedRank = val);
                              },
                            ),
                          ),
                        ),
                        if (_selectedRank == 'อื่นๆ (ระบุเอง)') ...[
                          const SizedBox(height: 8),
                          TextField(
                            controller: _regCustomRankController,
                            style: const TextStyle(color: Colors.white, fontSize: 13),
                            decoration: InputDecoration(
                              hintText: 'พิมพ์ยศ หรือ คำนำหน้าเอง...',
                              hintStyle: const TextStyle(color: AppColors.textMuted),
                              filled: true,
                              fillColor: AppColors.surface,
                              border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: AppColors.border)),
                            ),
                          ),
                        ],
                        const SizedBox(height: 12),

                        // ชื่อ - นามสกุล
                        const Text('ชื่อ - นามสกุล *', style: TextStyle(color: Colors.white70, fontSize: 12)),
                        const SizedBox(height: 6),
                        TextField(
                          controller: _regNameController,
                          style: const TextStyle(color: Colors.white, fontSize: 13),
                          decoration: InputDecoration(
                            hintText: 'เช่น สมชาย ใจภักดี',
                            hintStyle: const TextStyle(color: AppColors.textMuted),
                            filled: true,
                            fillColor: AppColors.surface,
                            border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: AppColors.border)),
                          ),
                        ),
                        const SizedBox(height: 12),

                        // เบอร์โทรศัพท์ 10 หลัก
                        const Text('หมายเลขโทรศัพท์มือถือ (10 หลัก) *', style: TextStyle(color: Colors.white70, fontSize: 12)),
                        const SizedBox(height: 6),
                        TextField(
                          controller: _regPhoneController,
                          keyboardType: TextInputType.phone,
                          maxLength: 10,
                          style: const TextStyle(color: Colors.white, fontSize: 13),
                          decoration: InputDecoration(
                            counterText: '',
                            prefixIcon: const Icon(Icons.phone_android, size: 18, color: Colors.cyan),
                            hintText: '08xxxxxxxx',
                            hintStyle: const TextStyle(color: AppColors.textMuted),
                            filled: true,
                            fillColor: AppColors.surface,
                            border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: AppColors.border)),
                          ),
                        ),
                        const SizedBox(height: 12),

                        // หน่วยงาน / สังกัด
                        const Text('หน่วยงาน / ต้นสังกัด *', style: TextStyle(color: Colors.white70, fontSize: 12)),
                        const SizedBox(height: 6),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 14),
                          decoration: BoxDecoration(
                            color: AppColors.surface,
                            borderRadius: BorderRadius.circular(10),
                            border: Border.all(color: AppColors.border),
                          ),
                          child: DropdownButtonHideUnderline(
                            child: DropdownButton<String>(
                              value: _selectedUnit,
                              dropdownColor: AppColors.surfaceCard,
                              isExpanded: true,
                              style: const TextStyle(color: Colors.white, fontSize: 13),
                              items: _units.map((u) => DropdownMenuItem(value: u, child: Text(u))).toList(),
                              onChanged: (val) {
                                if (val != null) setState(() => _selectedUnit = val);
                              },
                            ),
                          ),
                        ),
                        if (_selectedUnit == 'อื่นๆ (ระบุหน่วยงานเอง)') ...[
                          const SizedBox(height: 8),
                          TextField(
                            controller: _regCustomUnitController,
                            style: const TextStyle(color: Colors.white, fontSize: 13),
                            decoration: InputDecoration(
                              hintText: 'พิมพ์ชื่อหน่วยงาน / สังกัด...',
                              hintStyle: const TextStyle(color: AppColors.textMuted),
                              filled: true,
                              fillColor: AppColors.surface,
                              border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: AppColors.border)),
                            ),
                          ),
                        ],
                        const SizedBox(height: 12),

                        // ตำแหน่งหน้าที่
                        const Text('ตำแหน่งหน้าที่', style: TextStyle(color: Colors.white70, fontSize: 12)),
                        const SizedBox(height: 6),
                        TextField(
                          controller: _regPositionController,
                          style: const TextStyle(color: Colors.white, fontSize: 13),
                          decoration: InputDecoration(
                            hintText: 'เช่น หัวหน้าชุด, พลขับ, ฝ่ายยุทธการ',
                            hintStyle: const TextStyle(color: AppColors.textMuted),
                            filled: true,
                            fillColor: AppColors.surface,
                            border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: AppColors.border)),
                          ),
                        ),
                        const SizedBox(height: 12),

                        // 🗺️ พื้นที่รับผิดชอบ / เขตปฏิบัติการ
                        const Row(
                          children: [
                            Icon(Icons.map_outlined, color: AppColors.primaryLight, size: 16),
                            SizedBox(width: 6),
                            Text('พื้นที่รับผิดชอบ / เขตปฏิบัติการ *', style: TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.bold)),
                          ],
                        ),
                        const SizedBox(height: 6),
                        TextField(
                          controller: _regAreaController,
                          style: const TextStyle(color: Colors.white, fontSize: 13),
                          decoration: InputDecoration(
                            hintText: 'เช่น เชียงราย (ทุกอำเภอ), อ.แม่สาย, ภาคเหนือ, ส่วนกลาง',
                            hintStyle: const TextStyle(color: AppColors.textMuted),
                            filled: true,
                            fillColor: AppColors.surface,
                            border: OutlineInputBorder(borderRadius: BorderRadius.circular(10), borderSide: const BorderSide(color: AppColors.border)),
                          ),
                        ),
                        const SizedBox(height: 16),

                        // 👥 ขนาดของหน่วย / กำลังพล
                        const Row(
                          children: [
                            Icon(Icons.groups, color: Colors.amberAccent, size: 18),
                            SizedBox(width: 6),
                            Text('ขนาดของหน่วย / กำลังพล *', style: TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.bold)),
                          ],
                        ),
                        const SizedBox(height: 6),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 14),
                          decoration: BoxDecoration(
                            color: AppColors.surface,
                            borderRadius: BorderRadius.circular(10),
                            border: Border.all(color: AppColors.border),
                          ),
                          child: DropdownButtonHideUnderline(
                            child: DropdownButton<String>(
                              value: _selectedUnitSize,
                              dropdownColor: AppColors.surfaceCard,
                              isExpanded: true,
                              style: const TextStyle(color: Colors.white, fontSize: 13),
                              items: _unitSizes.map((s) => DropdownMenuItem(value: s, child: Text(s))).toList(),
                              onChanged: (val) {
                                if (val != null) setState(() => _selectedUnitSize = val);
                              },
                            ),
                          ),
                        ),
                        const SizedBox(height: 14),

                        // 🚜 ยานพาหนะหลักประจำหน่วย
                        const Row(
                          children: [
                            Icon(Icons.directions_car, color: Colors.lightGreenAccent, size: 18),
                            SizedBox(width: 6),
                            Text('ยานพาหนะหลักประจำหน่วย *', style: TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.bold)),
                          ],
                        ),
                        const SizedBox(height: 6),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 14),
                          decoration: BoxDecoration(
                            color: AppColors.surface,
                            borderRadius: BorderRadius.circular(10),
                            border: Border.all(color: AppColors.border),
                          ),
                          child: DropdownButtonHideUnderline(
                            child: DropdownButton<String>(
                              value: _selectedVehicleType,
                              dropdownColor: AppColors.surfaceCard,
                              isExpanded: true,
                              style: const TextStyle(color: Colors.white, fontSize: 13),
                              items: _vehicleTypes.map((v) => DropdownMenuItem(value: v, child: Text(v))).toList(),
                              onChanged: (val) {
                                if (val != null) setState(() => _selectedVehicleType = val);
                              },
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 20),

                  ElevatedButton(
                    onPressed: _isLoading ? null : _handleRegister,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: Colors.cyan.shade700,
                      disabledBackgroundColor: Colors.cyan.shade900,
                      padding: const EdgeInsets.symmetric(vertical: 14),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                      elevation: 4,
                    ),
                    child: _isLoading
                        ? const SizedBox(height: 22, width: 22, child: CircularProgressIndicator(strokeWidth: 2.5, color: Colors.white))
                        : const Row(
                            mainAxisAlignment: MainAxisAlignment.center,
                            children: [
                              Icon(Icons.how_to_reg, size: 20, color: Colors.white),
                              SizedBox(width: 8),
                              Text(
                                'ยื่นขอลงทะเบียนใช้งาน',
                                style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.white),
                              ),
                            ],
                          ),
                  ),

                  const SizedBox(height: 18),
                  Center(
                    child: TextButton.icon(
                      style: TextButton.styleFrom(foregroundColor: AppColors.primaryLight),
                      icon: const Icon(Icons.arrow_back, size: 16),
                      label: const Text(
                        'มีบัญชีอยู่แล้ว? กลับไปหน้าเข้าสู่ระบบ',
                        style: TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
                      ),
                      onPressed: () => setState(() {
                        _mainMode = 0;
                        _errorMessage = null;
                      }),
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}
