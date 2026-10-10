<?php
/**
 * LINE Bot Webhook เชื่อมต่อกับ ArcGIS Portal RTSD 10.61
 * ความสามารถ:
 * 1. รับข้อความ หรือ "การแชร์พิกัดสถานที่ (Location)" จากผู้ใช้ใน LINE
 * 2. บันทึกพิกัดและรายละเอียดลงใน Feature Layer บน ArcGIS Geoportal RTSD ทันที
 * 3. ส่งข้อความตอบกลับผู้ใช้ใน LINE ยืนยันการบันทึกข้อมูล
 */

// ปิดการแคช
header('Content-Type: application/json; charset=utf-8');

// =========================================================================
// 🔴 1. ตั้งค่า LINE Bot (ใส่ค่าจริงของคุณเรียบร้อยแล้ว)
// =========================================================================
define('LINE_CHANNEL_ACCESS_TOKEN', 'tZwEj7/Os0MEb2g5oQMsZc6/8Uvt0AID8SVj/O5dyRkph1hgP8H3JSdduIh+SIXjQI1rPILjCx3ZVuG+WszETDZxOZZ2oXki4wCIF/kz26gjfE+iz8GCQtYCj4cbFLv3EQrOv/YrsWJ/VwMDns4f7gdB04t89/1O/w1cDnyilFU=');
define('LINE_CHANNEL_SECRET', '01c3290fd81f65255b654ab26a5713b4');


// =========================================================================
// 🔴 2. ตั้งค่า ArcGIS Geoportal RTSD (ใส่ค่าของคุณไว้ให้แล้ว)
// =========================================================================
define('PORTAL_URL', 'https://geoportal.rtsd.mi.th/portal');
define('PORTAL_USER', 'goc01@2024');
define('PORTAL_PASS', 'GEOINT@rtsd_09_69');
// Layer ปลายทางสำหรับรับจุดแจ้งเหตุ
define('TARGET_LAYER_URL', 'https://geoportal.rtsd.mi.th/arcgis/rest/services/Hosted/itic_cctv/FeatureServer/0');


// =========================================================================
// 3. ฟังก์ชันขอ Token จาก ArcGIS Portal
// =========================================================================
function get_arcgis_token() {
    $token_url = rtrim(PORTAL_URL, '/') . '/sharing/rest/generateToken';
    $payload = [
        'username'   => PORTAL_USER,
        'password'   => PORTAL_PASS,
        'client'     => 'referer',
        'referer'    => PORTAL_URL,
        'expiration' => 15,
        'f'          => 'json'
    ];

    $ch = curl_init($token_url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_POSTFIELDS, http_build_query($payload));
    curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
    curl_setopt($ch, CURLOPT_TIMEOUT, 10);
    $res = json_decode(curl_exec($ch), true);
    curl_close($ch);

    return $res['token'] ?? null;
}


// =========================================================================
// 4. ฟังก์ชันปักหมุดข้อมูลลง ArcGIS Feature Layer
// =========================================================================
function add_feature_to_gis($lat, $lon, $title, $description) {
    $token = get_arcgis_token();
    if (!$token) return false;

    $feature = [
        [
            'geometry' => [
                'x' => (float)$lon,
                'y' => (float)$lat,
                'spatialReference' => ['wkid' => 4326]
            ],
            'attributes' => [
                'title'        => mb_substr($title, 0, 250, 'utf-8'),
                'organization' => 'แจ้งเหตุผ่าน LINE Bot',
                'status'       => 'แจ้งเตือนภัยใหม่',
                'lastupdate'   => date('Y-m-d H:i:s')
            ]
        ]
    ];

    $add_url = TARGET_LAYER_URL . '/addFeatures';
    $ch = curl_init($add_url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_POSTFIELDS, http_build_query([
        'features' => json_encode($feature),
        'token'    => $token,
        'f'        => 'json'
    ]));
    curl_setopt($ch, CURLOPT_SSL_VERIFYPEER, false);
    curl_setopt($ch, CURLOPT_TIMEOUT, 15);
    $res = json_decode(curl_exec($ch), true);
    curl_close($ch);

    $success = false;
    if (isset($res['addResults'][0]['success'])) {
        $success = $res['addResults'][0]['success'];
    }
    return $success;
}


// =========================================================================
// 5. ฟังก์ชันส่งข้อความตอบกลับใน LINE (Reply Message)
// =========================================================================
function reply_to_line($reply_token, $text_message) {
    $url = 'https://api.line.me/v2/bot/message/reply';
    $payload = [
        'replyToken' => $reply_token,
        'messages'   => [
            [
                'type' => 'text',
                'text' => $text_message
            ]
        ]
    ];

    $ch = curl_init($url);
    curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
    curl_setopt($ch, CURLOPT_POST, true);
    curl_setopt($ch, CURLOPT_HTTPHEADER, [
        'Content-Type: application/json',
        'Authorization: Bearer ' . LINE_CHANNEL_ACCESS_TOKEN
    ]);
    curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($payload));
    curl_setopt($ch, CURLOPT_TIMEOUT, 10);
    curl_exec($ch);
    curl_close($ch);
}


// =========================================================================
// 6. ส่วนประมวลผลหลัก (Webhook Event Handler)
// =========================================================================
$content = file_get_contents('php://input');
$events = json_decode($content, true);

if (!empty($events['events'])) {
    foreach ($events['events'] as $event) {
        $reply_token = $event['replyToken'] ?? '';
        $type        = $event['type'] ?? '';

        if ($type === 'message') {
            $msg_type = $event['message']['type'] ?? '';

            // กรณีที่ 1: ผู้ใช้ "แชร์พิกัดสถานที่ (Location)" ใน LINE
            if ($msg_type === 'location') {
                $lat     = $event['message']['latitude'];
                $lon     = $event['message']['longitude'];
                $address = $event['message']['address'] ?? 'ไม่ระบุชื่อที่อยู่';
                $title   = $event['message']['title'] ?? 'จุดเกิดเหตุ';

                // ปักหมุดลง ArcGIS Portal
                $is_saved = add_feature_to_gis($lat, $lon, "แจ้งเหตุ: $title ($address)", $address);

                if ($is_saved) {
                    $reply_text = "✅ ได้รับรายงานเหตุการณ์เรียบร้อยแล้ว!\n\n"
                                . "📍 พิกัด: $lat, $lon\n"
                                . "🏠 สถานที่: $address\n\n"
                                . "ระบบได้นำข้อมูลปักหมุดลงบนระบบแผนที่ Geoportal RTSD เรียบร้อยแล้วครับ";
                } else {
                    $reply_text = "⚠️ ได้รับพิกัดแล้ว แต่ไม่สามารถเชื่อมต่อฐานข้อมูล GIS ได้ในขณะนี้";
                }

                reply_to_line($reply_token, $reply_text);
            }

            // กรณีที่ 2: ผู้ใช้พิมพ์ข้อความธรรมดา (Text)
            else if ($msg_type === 'text') {
                $user_text = trim($event['message']['text']);

                if ($user_text === 'ช่วย' || $user_text === 'แจ้งเหตุ' || $user_text === 'menu') {
                    $reply_text = "🚨 ระบบรับแจ้งเหตุเตือนภัย RTSD 🚨\n\n"
                                . "ท่านสามารถแจ้งเหตุพร้อมปักหมุดบนแผนที่ได้ง่าย ๆ:\n"
                                . "👉 เพียงกดปุ่ม '+' ด้านล่างซ้าย แล้วเลือก 'ตำแหน่งที่ตั้ง' (Location) เพื่อส่งพิกัดเข้ามาได้ทันทีครับ!";
                    reply_to_line($reply_token, $reply_text);
                } else {
                    $reply_text = "ได้รับข้อความ: \"$user_text\"\n\nหากต้องการรายงานเหตุการณ์และปักหมุดแผนที่ กรุณากดปุ่ม '+' แล้วเลือก 'ตำแหน่งที่ตั้ง' (Location) ส่งพิกัดเข้ามาได้เลยครับ";
                    reply_to_line($reply_token, $reply_text);
                }
            }
        }
    }
}

// ตอบกลับ Status 200 ให้ LINE ทราบว่าได้รับ Request แล้ว
http_response_code(200);
echo json_encode(['status' => 'ok']);
?>
