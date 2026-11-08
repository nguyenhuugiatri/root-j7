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

# Lệnh chạy sau khi cài xong + mở app cuối cùng. {serial} sẽ được thay bằng serial máy.
# Chạy trên từng máy. Đổi lệnh ở đây.
POST_COMMAND = "adb -s {serial} shell su"
# --------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

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

    def run(self):
        total = len(APK_FILES)
        for i, name in enumerate(APK_FILES, 1):
            path = os.path.join(BASE_DIR, name)
            self.update_status(f"[{i}/{total}] installing {name}")
            is_last = i == total
            try:
                # APK cuối cùng: cài xong tự động mở app
                self.d.install(path, nolaunch=not is_last)
            except Exception as e:
                self.update_status(f"{StatusEnum.FAILED.value}: {name}: {e}")
                return
            time.sleep(1)
        time.sleep(2)  # chờ app mở lên
        self.update_status(f"running: {POST_COMMAND.format(serial=self.serial)}")
        try:
            result = subprocess.run(
                shlex.split(POST_COMMAND.format(serial=self.serial)),
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                err = (result.stderr or result.stdout).strip()
                self.update_status(f"{StatusEnum.FAILED.value}: command: {err}")
                return
        except Exception as e:
            self.update_status(f"{StatusEnum.FAILED.value}: command: {e}")
            return
        self.update_status(StatusEnum.DONE)

    def update_status(self, status: str):
        STATUS[self.serial] = status


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
    missing = [n for n in APK_FILES if not os.path.isfile(os.path.join(BASE_DIR, n))]
    if missing:
        print(f"Missing APK files in {BASE_DIR}: {', '.join(missing)}")
        return

    max_threads = 5
    threads = []
    for device in list_devices(adb):
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
