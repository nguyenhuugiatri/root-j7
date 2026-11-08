import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import adbutils
from devfilter import list_devices

MAX_THREADS = 5
# Nếu máy vẫn kẹt ở "Rung": đặt True để thử gọi service audio với nhiều mã transaction
# (chỉ dùng khi cần, vì mã sai có thể đổi cài đặt âm thanh khác)
SCAN_SERVICE_CALL = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")  # log đầy đủ từng máy: logs/<serial>-silent.log
os.makedirs(LOG_DIR, exist_ok=True)

adb = adbutils.AdbClient()
print_lock = Lock()

# Các stream âm thanh Android: 0 voice call, 1 system, 2 ring, 3 music,
# 4 alarm, 5 notification, 6 bluetooth sco, 7 system-enforced, 8 dtmf, 9 tts, 10 accessibility
STREAMS = range(0, 11)


class Dev:
    def __init__(self, serial):
        self.serial = serial
        self.d = adb.device(serial=serial)

    def log(self, text):
        line = f"[{time.strftime('%H:%M:%S')}] [{self.serial}] {text}"
        with print_lock:
            print(line, flush=True)
        with open(os.path.join(LOG_DIR, f"{self.serial}-silent.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def sh(self, cmd, show=False):
        """Chạy shell (mặc định im lặng, show=True để log lệnh + output)."""
        if show:
            self.log(f"$ {cmd}")
        try:
            out = self.d.shell(cmd).strip()
        except Exception as e:
            out = f"[exception] {e}"
        if show:
            for l in (out.splitlines() or ["(empty)"])[:20]:
                self.log(f"    {l}")
        return out


def apply(serial):
    dev = Dev(serial)
    try:
        dev.log("=== tắt âm thanh + rung + NFC ===")

        # 1. Tắt rung TRƯỚC (để chế độ chuông không rơi về "Rung")
        for key in (
            "vibrate_when_ringing",
            "vibrate_on",
            "vibrate_in_silent",
            "haptic_feedback_enabled",
            "notification_vibration_intensity",
            "ring_vibration_intensity",
            "haptic_feedback_intensity",
            "alarm_vibration_intensity",
            "media_vibration_intensity",
            "hardware_haptic_feedback_intensity",
            "apply_ramping_ringer",
            "touch_vibration_intensity",
        ):
            dev.sh(f"settings put system {key} 0", show=False)

        # 2. Âm lượng về 0 cho mọi stream
        for s in STREAMS:
            dev.sh(f"cmd media_session volume --stream {s} --set 0", show=False)
            dev.sh(f"media volume --stream {s} --set 0", show=False)

        # 3. Chế độ chuông = TẮT ÂM (SILENT=0, không phải VIBRATE=1)
        sdk = dev.sh("getprop ro.build.version.sdk")  # dùng cho dòng kết quả cuối

        def ringer_mode():
            """Trả về chuỗi mô tả ringer mode hiện tại (từ dumpsys audio)."""
            return dev.sh("dumpsys audio | grep -i -E 'ringer mode|mRingerMode'", show=False)

        def is_silent():
            r = ringer_mode().lower()
            return "silent" in r or "mode: 0" in r or "mode:0" in r or "mringermode=0" in r.replace(" ", "")

        # Cách 1: lệnh cmd audio (Android 13+)
        dev.sh("cmd audio set-ringer-mode SILENT")
        # Cách 2: Không làm phiền chặn tất cả (zen 2 = total silence)
        dev.sh("cmd notification set_dnd on")
        dev.sh("settings put global zen_mode 2")
        dev.sh("settings put global mode_ringer 0", show=False)
        time.sleep(1)

        if SCAN_SERVICE_CALL and not is_silent():
            # Mã transaction setRingerMode thay đổi theo bản Android -> thử lần lượt và kiểm tra
            for code in range(1, 80):
                dev.sh(f"service call audio {code} i32 0 s16 android")
                if is_silent():
                    break
        dev.sh("cmd vibrator_manager cancel")  # dừng rung đang chạy (nếu có)

        # 4. Tắt âm bàn phím / chạm / khoá màn hình / sạc
        dev.sh("settings put system sound_effects_enabled 0")
        dev.sh("settings put system dtmf_tone 0")
        dev.sh("settings put system lockscreen_sounds_enabled 0")
        dev.sh("settings put global charging_sounds_enabled 0")
        dev.sh("settings put global low_battery_sound_timeout 0")

        # 5. Tắt NFC
        dev.sh("svc nfc disable")
        dev.sh("settings put global nfc_on 0")

        # 6. Tắt vị trí (Location / chia sẻ vị trí)
        dev.sh("cmd location set-location-enabled false")  # Android 9+
        dev.sh("settings put secure location_mode 0", show=False)  # 0 = OFF
        dev.sh("settings put secure location_providers_allowed -gps", show=False)  # Android cũ
        dev.sh("settings put secure location_providers_allowed -network", show=False)
        # Tắt quét Wi-Fi/Bluetooth để định vị
        dev.sh("settings put global wifi_scan_always_enabled 0", show=False)
        dev.sh("settings put global ble_scan_always_enabled 0", show=False)

        # Kiểm tra
        silent = is_silent()
        loc = dev.sh("settings get secure location_mode", show=False)
        dev.log(f"sdk={sdk} | tắt âm: {'OK' if silent else 'CHƯA (vẫn rung?)'} | location_mode={loc}")
        return serial, True, "done" if silent else "done (chế độ chuông chưa về Tắt âm)"
    except Exception as e:
        dev.log(f"[exception] {e}")
        return serial, False, str(e)


def main():
    only = set(sys.argv[1:])  # truyền serial để chỉ chạy các máy đó
    serials = [dev.serial for dev in list_devices(adb)
               if dev.state == "device" and (not only or dev.serial in only)]
    if not serials:
        print("No running devices found.")
        return
    print(f"Tắt âm thanh/rung/NFC cho {len(serials)} device(s)...")
    with ThreadPoolExecutor(MAX_THREADS) as executor:
        for serial, ok, msg in executor.map(apply, serials):
            print(f"Device {serial}: {'OK' if ok else 'FAILED'} - {msg}")


if __name__ == "__main__":
    main()
