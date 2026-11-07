import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from threading import Thread
from colorama import init, Cursor
import adbutils

init()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROM_FILE = os.path.join(BASE_DIR, "rom.zip")
LOG_DIR = os.path.join(BASE_DIR, "logs")  # log đầy đủ từng máy: logs/<serial>.log
os.makedirs(LOG_DIR, exist_ok=True)

# (mô tả, lệnh adb (không gồm "-s serial"), bỏ qua lỗi?, timeout giây)
# Máy đã ở recovery nên không cần "reboot recovery".
STEPS = [
    ("format data", ["shell", "twrp", "format", "data"], True, 300),
    ("wipe data", ["shell", "twrp", "wipe", "data"], True, 300),
    ("clear sdcard", ["shell", "rm", "-rf", "/sdcard/*"], True, 300),
    ("push rom.zip", ["push", ROM_FILE, "/sdcard/"], False, 1800),
    ("install rom.zip", ["shell", "twrp", "install", "/sdcard/rom.zip"], False, 3600),
    ("reboot", ["reboot"], False, 60),
]

adb = adbutils.AdbClient()

STATUS = {}


class StatusEnum(str, Enum):
    DONE = "Done"
    PENDING = "Recovery mode, pending"
    FAILED = "FAILED"


class RomThread:
    def __init__(self, serial):
        self.serial = serial

    def log(self, text):
        with open(os.path.join(LOG_DIR, f"{self.serial}.log"), "a", encoding="utf-8") as f:
            f.write(text)

    def run_cmd(self, label, cmd, timeout):
        """Chạy lệnh, cập nhật status liên tục (thời gian + dòng output mới nhất).
        Trả về (returncode, output)."""
        start = time.time()
        last_line = ""
        output = b""
        self.log(f"\n$ {' '.join(cmd)}\n")
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        fd = proc.stdout.fileno()
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            output += chunk
            text = chunk.decode("utf-8", errors="replace")
            self.log(text)
            lines = [l.strip() for l in text.replace("\r", "\n").split("\n") if l.strip()]
            if lines:
                last_line = lines[-1]
            elapsed = int(time.time() - start)
            self.update_status(f"{label} ({elapsed}s) {last_line}")
            if time.time() - start > timeout:
                proc.kill()
                self.log("\n[TIMEOUT]\n")
                return -1, output.decode("utf-8", errors="replace") + "\nTIMEOUT"
        proc.wait()
        return proc.returncode, output.decode("utf-8", errors="replace")

    def run(self):
        total = len(STEPS)
        for i, (desc, args, ignore_error, timeout) in enumerate(STEPS, 1):
            label = f"[{i}/{total}] {desc}"
            self.update_status(f"{label} (0s)")
            cmd = ["adb", "-s", self.serial] + args
            try:
                code, out = self.run_cmd(label, cmd, timeout)
            except Exception as e:
                code, out = -1, str(e)
            if code != 0:
                self.log(f"[exit {code}]\n")
                if not ignore_error:
                    err = out.strip().splitlines()[-1] if out.strip() else ""
                    self.update_status(f"{StatusEnum.FAILED.value}: {desc}: {err}")
                    return
            time.sleep(1)
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
            line = f"Device {serial}: {status}"
            width = shutil.get_terminal_size((120, 20)).columns - 1
            sys.stdout.write(line[:width].ljust(width) + "\n")
            if is_finished(status):
                complete_count += 1
        sys.stdout.flush()
        if complete_count == len(STATUS):
            return
        time.sleep(0.5)


def main():
    if not os.path.isfile(ROM_FILE):
        print(f"Missing rom.zip in {BASE_DIR}")
        return

    max_threads = 5
    threads = []
    for device in adb.list():
        if device.state == 'recovery':
            STATUS[device.serial] = StatusEnum.PENDING
            threads.append(RomThread(serial=device.serial))

    if not threads:
        print("No devices in recovery found.")
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
