import os
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from threading import Thread
from colorama import init, Cursor
import adbutils
from devfilter import list_devices

init()

# ---- Chỉnh danh sách APK ở đây (thứ tự = thứ tự cài, tuần tự) ----
APK_FILES = [
    "app-magisk23.apk",
    "Root_Checker.apk",
]
# APK không bắt buộc: thiếu file hoặc cài lỗi thì bỏ qua, không báo FAILED.
OPTIONAL_APKS = {"Root_Checker.apk"}
# Package của app Magisk, được mở ở cuối bước.
MAGISK_PACKAGE = "com.topjohnwu.magisk"

# Lệnh chạy sau khi cài xong + mở app cuối cùng. {serial} sẽ được thay bằng serial máy.
# Chạy trên từng máy. Đổi lệnh ở đây.
POST_COMMAND = "adb -s {serial} shell su"
# --------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")  # log đầy đủ từng máy: logs/<serial>-magisk.log
os.makedirs(LOG_DIR, exist_ok=True)

adb = adbutils.AdbClient()

STATUS = {}


class StatusEnum(str, Enum):
    DONE = "Done"
    PENDING = "Pending"
    FAILED = "FAILED"


class InstallThread:
    def __init__(self, serial):
        self.serial = serial
        self.d = adb.device(serial=serial)

    def log(self, text):
        with open(os.path.join(LOG_DIR, f"{self.serial}-magisk.log"), "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")

    def run(self):
        self.log(f"{'=' * 50}\nBẮT ĐẦU {self.serial} {time.strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            self._run()
        except Exception as e:
            import traceback
            self.log(traceback.format_exc())
            self.update_status(f"{StatusEnum.FAILED.value}: exception: {e}")

    def _run(self):
        total = len(APK_FILES)
        for i, name in enumerate(APK_FILES, 1):
            path = os.path.join(BASE_DIR, name)
            self.update_status(f"[{i}/{total}] installing {name}")
            if name in OPTIONAL_APKS and not os.path.isfile(path):
                self.log(f"bỏ qua {name} (không có file, không bắt buộc)")
                continue
            try:
                # Không tự mở app khi cài; Magisk được mở ở cuối bước
                self.d.install(path, nolaunch=True)
                self.log(f"cài xong {name}")
            except Exception as e:
                if name in OPTIONAL_APKS:
                    self.log(f"cài {name} lỗi (không bắt buộc, bỏ qua): {e}")
                    continue
                self.update_status(f"{StatusEnum.FAILED.value}: {name}: {e}")
                return
            time.sleep(1)
        time.sleep(2)  # chờ app mở lên
        self.update_status(f"running: {POST_COMMAND.format(serial=self.serial)}")
        # Lệnh này không bắt buộc: lỗi (vd user không kịp bấm cho phép su)
        # chỉ ghi log, vẫn coi là Done để qua bước tiếp theo.
        try:
            result = subprocess.run(
                shlex.split(POST_COMMAND.format(serial=self.serial)),
                capture_output=True, text=True, timeout=120,
                stdin=subprocess.DEVNULL,
            )
            self.log(f"exit {result.returncode}\n  stdout: {result.stdout.strip()}\n  stderr: {result.stderr.strip()}")
        except Exception as e:
            self.log(f"command lỗi (bỏ qua): {e}")
        # Cuối bước: mở sẵn Magisk trên máy
        try:
            out = self.d.shell(
                f"monkey -p {MAGISK_PACKAGE} -c android.intent.category.LAUNCHER 1"
            )
            self.log(f"mở Magisk: {str(out).strip()}")
        except Exception as e:
            self.log(f"mở Magisk lỗi (bỏ qua): {e}")
        self.update_status(StatusEnum.DONE)

    def update_status(self, status: str):
        STATUS[self.serial] = status
        self.log(f"== {status}")


def is_finished(status):
    return status == StatusEnum.DONE or str(status).startswith(StatusEnum.FAILED.value)


def clear_stdout():
    os.system('cls' if os.name == 'nt' else 'clear')


def move_cursor_and_clear():
    sys.stdout.write(Cursor.POS(1, 1))
    sys.stdout.write("\033[J")


def display_statuses():
    clear_stdout()
    while True:
        move_cursor_and_clear()
        complete_count = 0
        for serial, status in list(STATUS.items()):
            sys.stdout.write(f"Device {serial}: {status}\n")
            if is_finished(status):
                complete_count += 1
        sys.stdout.flush()
        if complete_count == len(STATUS):
            return
        time.sleep(0.5)


def main():
    missing = [n for n in APK_FILES if n not in OPTIONAL_APKS and not os.path.isfile(os.path.join(BASE_DIR, n))]
    if missing:
        print(f"Missing APK files in {BASE_DIR}: {', '.join(missing)}")
        return

    max_threads = 5
    threads = []
    only = set(sys.argv[1:])  # truyền serial để chỉ chạy các máy đó
    for device in list_devices(adb):
        if only and device.serial not in only:
            continue
        if device.state == 'device':
            STATUS[device.serial] = StatusEnum.PENDING
            threads.append(InstallThread(serial=device.serial))

    if not threads:
        print("No running devices found.")
        return

    monitor_thread = Thread(target=display_statuses)
    monitor_thread.start()
    with ThreadPoolExecutor(max_threads) as executor:
        futures = [executor.submit(t.run) for t in threads]
        for future in as_completed(futures):
            pass
    monitor_thread.join()
    print("All tasks completed!")


if __name__ == '__main__':
    main()
