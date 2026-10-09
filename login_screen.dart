import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import '../../../../data/models/unit_profile_model.dart';
import '../../../../data/models/user_model.dart';
import '../../../../data/services/api_service.dart';
import '../../../../data/services/storage_service.dart';
import '../../../core/app_colors.dart';
import '../../home/views/home_screen.dart';
import '../../setup/views/unit_setup_screen.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key});

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _phoneController = TextEditingController(text: '0863390614');
  final _passwordController = TextEditingController(text: 'RTSD2024');
  bool _obscurePassword = true;
  bool _isLoading = false;
  String? _errorMessage;

  final ApiService _apiService = ApiService();

  Future<void> _handleLogin() async {
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
      final user = result['user'] as UserModel;
      await StorageService.saveUser(user);

      // ตรวจสอบว่ามี UnitProfile เดิมหรือยัง ถ้ายังไม่มีให้สร้างโปรไฟล์พื้นฐานตามสังกัด
      final existingProfile = await StorageService.getUnitProfile();
      if (existingProfile == null) {
        final newProfile = UnitProfileModel(
          unitId: 'RTSD-${user.phone.length >= 4 ? user.phone.substring(user.phone.length - 4) : "01"}',
          district: 'แม่สาย',
          unitName: user.unit.isNotEmpty ? user.unit : 'ชุดเคลื่อนที่เร็ว บรรเทาสาธารณภัย',
          commander: user.name,
          phoneNumber: user.phone,
        );
        await StorageService.saveUnitProfile(newProfile);
      }

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

  void _showRegistrationDialog() {
    const regUrl = 'https://rtsd-linebot.onrender.com/register';
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppColors.surfaceCard,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(16),
          side: const BorderSide(color: AppColors.primaryLight, width: 1.5),
        ),
        title: const Row(
          children: [
            Icon(Icons.app_registration, color: AppColors.primaryLight, size: 24),
            SizedBox(width: 10),
            Expanded(
              child: Text(
                'ลงทะเบียนใช้งานระบบ RTSD',
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
              'สำหรับผู้ใช้งานหรือกำลังพลใหม่ กรุณายื่นขอลงทะเบียนใช้งานระบบล่วงหน้าตามช่องทางดังนี้:',
              style: TextStyle(color: Colors.white70, fontSize: 13, height: 1.4),
            ),
            const SizedBox(height: 14),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: AppColors.surface,
                borderRadius: BorderRadius.circular(10),
                border: Border.all(color: AppColors.border),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Row(
                    children: [
                      Icon(Icons.language, color: Colors.cyan, size: 16),
                      SizedBox(width: 6),
                      Text(
                        '1. เว็บไซต์ลงทะเบียน (Web Portal):',
                        style: TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w600),
                      ),
                    ],
                  ),
                  const SizedBox(height: 4),
                  const SelectableText(
                    regUrl,
                    style: TextStyle(color: Colors.cyan, fontSize: 12, fontFamily: 'monospace'),
                  ),
                  const SizedBox(height: 10),
                  const Row(
                    children: [
                      Icon(Icons.chat_bubble_outline, color: Colors.greenAccent, size: 16),
                      SizedBox(width: 6),
                      Text(
                        '2. LINE Official Account (@411vtica):',
                        style: TextStyle(color: Colors.white, fontSize: 12, fontWeight: FontWeight.w600),
                      ),
                    ],
                  ),
                  const SizedBox(height: 4),
                  const Text(
                    'พิมพ์เบอร์โทรศัพท์ 10 หลัก ส่งเข้าแชต LINE เพื่อยื่นคำขอ',
                    style: TextStyle(color: AppColors.textMuted, fontSize: 11),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),
            const Text(
              '🛡️ ระบบความปลอดภัย: ทุกคำขอจะต้องได้รับการอนุมัติจาก Admin ก่อนจึงจะสามารถเข้าสู่ระบบได้',
              style: TextStyle(color: Colors.amber, fontSize: 11, fontStyle: FontStyle.italic),
            ),
          ],
        ),
        actions: [
          TextButton.icon(
            icon: const Icon(Icons.copy, size: 16),
            label: const Text('คัดลอกลิงก์'),
            onPressed: () {
              Clipboard.setData(const ClipboardData(text: regUrl));
              Navigator.pop(ctx);
              ScaffoldMessenger.of(context).showSnackBar(
                const SnackBar(
                  content: Text('📋 คัดลอกลิงก์หน้าลงทะเบียนเรียบร้อยแล้ว'),
                  backgroundColor: AppColors.accentGreen,
                ),
              );
            },
          ),
          ElevatedButton(
            style: ElevatedButton.styleFrom(
              backgroundColor: AppColors.primary,
              foregroundColor: Colors.white,
            ),
            onPressed: () => Navigator.pop(ctx),
            child: const Text('ปิด'),
          ),
        ],
      ),
    );
  }

  void _fillPreset(String phone, String password) {
    setState(() {
      _phoneController.text = phone;
      _passwordController.text = password;
      _errorMessage = null;
    });
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
                    padding: const EdgeInsets.all(18),
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
                      size: 54,
                      color: AppColors.primaryLight,
                    ),
                  ),
                ),
                const SizedBox(height: 16),
                const Center(
                  child: Text(
                    'RTSD COMMAND & CONTROL',
                    style: TextStyle(
                      color: Colors.white,
                      fontSize: 20,
                      fontWeight: FontWeight.bold,
                      letterSpacing: 1.2,
                    ),
                  ),
                ),
                const SizedBox(height: 4),
                const Center(
                  child: Text(
                    'ระบบยืนยันตัวตนและจัดการสิทธิ์กำลังพล',
                    style: TextStyle(
                      color: AppColors.textMuted,
                      fontSize: 13,
                    ),
                  ),
                ),
                const SizedBox(height: 32),

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
                  const SizedBox(height: 18),
                ],

                // 📱 Input: Phone / Username
                const Text(
                  'เบอร์โทรศัพท์ หรือ รหัสผู้ใช้งาน',
                  style: TextStyle(color: Colors.white, fontSize: 13, fontWeight: FontWeight.w600),
                ),
                const SizedBox(height: 8),
                TextField(
                  controller: _phoneController,
                  keyboardType: TextInputType.phone,
                  style: const TextStyle(color: Colors.white),
                  decoration: InputDecoration(
                    prefixIcon: const Icon(Icons.person_outline, color: AppColors.textMuted),
                    hintText: '08xxxxxxxx หรือ admin',
                    hintStyle: const TextStyle(color: AppColors.textMuted),
                    filled: true,
                    fillColor: AppColors.surface,
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(12),
                      borderSide: const BorderSide(color: AppColors.border),
                    ),
                    enabledBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(12),
                      borderSide: const BorderSide(color: AppColors.border),
                    ),
                    focusedBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(12),
                      borderSide: const BorderSide(color: AppColors.primaryLight, width: 1.5),
                    ),
                  ),
                ),
                const SizedBox(height: 18),

                // 🔑 Input: Password / PIN
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
                    hintText: 'กรอกรหัสผ่าน หรือ PIN ประจำตัว',
                    hintStyle: const TextStyle(color: AppColors.textMuted),
                    filled: true,
                    fillColor: AppColors.surface,
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(12),
                      borderSide: const BorderSide(color: AppColors.border),
                    ),
                    enabledBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(12),
                      borderSide: const BorderSide(color: AppColors.border),
                    ),
                    focusedBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(12),
                      borderSide: const BorderSide(color: AppColors.primaryLight, width: 1.5),
                    ),
                  ),
                ),
                const SizedBox(height: 24),

                // 🚀 Submit Login Button
                ElevatedButton(
                  onPressed: _isLoading ? null : _handleLogin,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppColors.primary,
                    disabledBackgroundColor: AppColors.primary.withOpacity(0.5),
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
                    elevation: 4,
                  ),
                  child: _isLoading
                      ? const SizedBox(
                          height: 22,
                          width: 22,
                          child: CircularProgressIndicator(strokeWidth: 2.5, color: Colors.white),
                        )
                      : const Row(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            Icon(Icons.login, size: 20),
                            SizedBox(width: 8),
                            Text(
                              'เข้าสู่ระบบยุทธการ',
                              style: TextStyle(fontSize: 15, fontWeight: FontWeight.bold),
                            ),
                          ],
                        ),
                ),
                const SizedBox(height: 12),

                // 📝 Link to register for new personnel
                Center(
                  child: TextButton.icon(
                    style: TextButton.styleFrom(
                      foregroundColor: AppColors.primaryLight,
                      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 6),
                    ),
                    icon: const Icon(Icons.person_add_alt_1_outlined, size: 16),
                    label: const Text(
                      'ยังไม่มีบัญชีผู้ใช้? ยื่นขอลงทะเบียนใช้งาน',
                      style: TextStyle(
                        fontSize: 12.5,
                        fontWeight: FontWeight.w600,
                        decoration: TextDecoration.underline,
                      ),
                    ),
                    onPressed: _showRegistrationDialog,
                  ),
                ),
                const SizedBox(height: 16),

                // ⚡ Quick Demo Preset Chips
                const Center(
                  child: Text(
                    'เข้าสู่ระบบด่วน (Quick Presets):',
                    style: TextStyle(color: AppColors.textMuted, fontSize: 12),
                  ),
                ),
                const SizedBox(height: 10),
                Row(
                  children: [
                    Expanded(
                      child: OutlinedButton.icon(
                        style: OutlinedButton.styleFrom(
                          foregroundColor: Colors.amber,
                          side: BorderSide(color: Colors.amber.withOpacity(0.5)),
                          backgroundColor: Colors.amber.withOpacity(0.08),
                          padding: const EdgeInsets.symmetric(vertical: 10),
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                        ),
                        icon: const Icon(Icons.shield, size: 16),
                        label: const Text('ผบ.ชา (Admin)', style: TextStyle(fontSize: 12)),
                        onPressed: () => _fillPreset('0863390614', 'RTSD2024'),
                      ),
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: OutlinedButton.icon(
                        style: OutlinedButton.styleFrom(
                          foregroundColor: AppColors.primaryLight,
                          side: BorderSide(color: AppColors.primaryLight.withOpacity(0.5)),
                          backgroundColor: AppColors.primaryLight.withOpacity(0.08),
                          padding: const EdgeInsets.symmetric(vertical: 10),
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
                        ),
                        icon: const Icon(Icons.navigation, size: 16),
                        label: const Text('ชุดสนาม (Field)', style: TextStyle(fontSize: 12)),
                        onPressed: () => _fillPreset('0812345678', '1234'),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 28),

                // ⚙️ Offline Unit Setup Option
                Center(
                  child: TextButton.icon(
                    style: TextButton.styleFrom(foregroundColor: AppColors.textMuted),
                    icon: const Icon(Icons.settings_outlined, size: 16),
                    label: const Text(
                      'ตั้งค่าชุดปฏิบัติการประจำอำเภอ (Offline Setup)',
                      style: TextStyle(fontSize: 12),
                    ),
                    onPressed: () {
                      Navigator.push(
                        context,
                        MaterialPageRoute(builder: (_) => const UnitSetupScreen()),
                      );
                    },
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}
