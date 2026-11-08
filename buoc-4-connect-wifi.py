import os
import shlex
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Thread
from colorama import init, Cursor
import adbutils
from devfilter import list_devices

init()

SSID = "Thanh Duy"
PASSWORD = "123456Asd"
PASSWORD_TYPE = "WPA"  # WPA (WPA/WPA2) | WEP | NONE (mạng không mật khẩu)
MAX_THREADS = 5
CONNECT_TIMEOUT = 40  # giây chờ máy kết nối

# App https://github.com/steinwurf/adb-join-wifi (release 1.0.1), để cùng thư mục với script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APK = os.path.join(BASE_DIR, "adb-join-wifi.apk")
PACKAGE = "com.steinwurf.adbjoinwifi"

LOG_DIR = os.path.join(BASE_DIR, "logs")  # log đầy đủ từng máy: logs/<serial>-wifi.log
os.makedirs(LOG_DIR, exist_ok=True)

adb = adbutils.AdbClient()

STATUS = {}
DONE = "Done"
FAILED = "FAILED"


class Dev:
    def __init__(self, serial):
        self.serial = serial
        self.d = adb.device(serial=serial)

    def log(self, text):
        """Chỉ ghi vào file log, không in ra terminal."""
        line = f"[{time.strftime('%H:%M:%S')}] {text}"
        with open(os.path.join(LOG_DIR, f"{self.serial}-wifi.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def status(self, text):
        """Cập nhật dòng trạng thái trên terminal (và ghi log)."""
        STATUS[self.serial] = text
        self.log(f"== {text}")

    def sh(self, cmd):
        """Chạy shell, ghi lệnh + output vào file log."""
        self.log(f"$ {cmd}")
        try:
            out = self.d.shell(cmd).strip()
        except Exception as e:
            out = f"[exception] {e}"
        for l in (out.splitlines() or ["(empty)"])[:40]:
            self.log(f"    {l}")
        return out

    def wifi_info(self):
        try:
            out = self.d.shell("dumpsys wifi | grep mWifiInfo").strip()
        except Exception:
            return ""
        return out.splitlines()[0].strip() if out else ""


def connect(serial):
    dev = Dev(serial)
    try:
        dev.log(f"=== connect '{SSID}' ===")

        # 1. Bật wifi
        for n in range(1, 11):
            if dev.sh("settings get global wifi_on") == "1":
                break
            dev.status(f"bật wifi ({n}/10)")
            dev.sh("svc wifi enable")
            time.sleep(2)
        else:
            dev.status(f"{FAILED}: không bật được wifi")
            return

        # 2. Cài app adb-join-wifi (nếu chưa có)
        if PACKAGE not in dev.sh(f"pm list packages {PACKAGE}"):
            dev.status("cài adb-join-wifi.apk")
            try:
                dev.d.install(APK, nolaunch=True)
            except Exception as e:
                dev.log(f"install error: {e}")
                dev.status(f"{FAILED}: cài adb-join-wifi.apk lỗi: {e}")
                return

        # 3. Bật màn hình + mở khoá (app cần Activity chạy được)
        dev.sh("input keyevent KEYCODE_WAKEUP")
        dev.sh("wm dismiss-keyguard")

        # 4. Gửi lệnh kết nối
        dev.status(f"kết nối '{SSID}'")
        cmd = f"am start -n {PACKAGE}/.MainActivity -e ssid {shlex.quote(SSID)}"
        if PASSWORD_TYPE.upper() != "NONE":
            cmd += f" -e password_type {PASSWORD_TYPE} -e password {shlex.quote(PASSWORD)}"
        dev.sh(cmd)

        # 5. Chờ kết nối xong
        last = None
        for i in range(1, CONNECT_TIMEOUT + 1):
            time.sleep(1)
            state = dev.wifi_info()
            if state != last:
                dev.log(f"wifi info: {state}")
                last = state
            dev.status(f"chờ kết nối ({i}s)")
            # Android 9 in SSID không có dấu ngoặc kép (SSID: Thanh Duy), bản mới thì có
            if (f"SSID: {SSID}," in state or f'SSID: "{SSID}"' in state) and "COMPLETED" in state:
                dev.sh("input keyevent KEYCODE_HOME")
                dev.status(DONE)
                return

        # Thất bại: ghi thêm log của app vào file
        dev.sh(f"logcat -d -v time | grep -i -E '{PACKAGE}|adbjoinwifi' | tail -n 20")
        dev.status(f"{FAILED}: timeout, xem logs/{serial}-wifi.log")
    except Exception as e:
        dev.log(f"[exception] {e}")
        dev.status(f"{FAILED}: {e}")


def is_finished(status):
    return status == DONE or str(status).startswith(FAILED)


def display_statuses():
    os.system("cls" if os.name == "nt" else "clear")
    while True:
        sys.stdout.write(Cursor.POS(1, 1))
        sys.stdout.write("\033[J")
        width = shutil.get_terminal_size((120, 20)).columns - 1
        complete_count = 0
        for serial, status in list(STATUS.items()):
            sys.stdout.write(f"Device {serial}: {status}"[:width].ljust(width) + "\n")
            if is_finished(status):
                complete_count += 1
        sys.stdout.flush()
        if complete_count == len(STATUS):
            return
        time.sleep(0.5)


def main():
    if not os.path.isfile(APK):
        print(f"Missing adb-join-wifi.apk in {BASE_DIR}")
        return
    only = set(sys.argv[1:])  # truyền serial để chỉ chạy các máy đó
    serials = [dev.serial for dev in list_devices(adb)
               if dev.state == "device" and (not only or dev.serial in only)]
    if not serials:
        print("No running devices found.")
        return
    for s in serials:
        STATUS[s] = "Pending"

    monitor_thread = Thread(target=display_statuses)
    monitor_thread.start()
    with ThreadPoolExecutor(MAX_THREADS) as executor:
        futures = [executor.submit(connect, s) for s in serials]
        for future in as_completed(futures):
            pass
    monitor_thread.join()
    print("All tasks completed!")


if __name__ == "__main__":
    main()
