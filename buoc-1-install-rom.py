import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from threading import Lock, Thread
from colorama import init, Cursor
import adbutils
from devfilter import list_devices

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
    # TWRP: "You may need to reboot recovery to be able to use /data again" -> bắt buộc reboot recovery
    ("reboot recovery", ["reboot", "recovery"], True, 60),
    ("mount data", ["shell", "twrp", "mount", "data"], True, 60),
    ("clear sdcard", ["shell", "rm", "-rf", "/sdcard/*"], True, 300),
    # push thẳng; nếu máy lỗi (adbd hỏng với file lớn) thì tự chuyển sang cắt nhỏ + ghép bằng cat
    ("push rom.zip", ["push-smart"], False, 1800),
    ("install rom.zip", ["shell", "twrp", "install", "/sdcard/rom.zip"], False, 3600),
    ("reboot", ["reboot"], False, 60),
]

PUSH_RETRIES = 3
CHUNK_SIZE = 300 * 1024 * 1024  # 300MB/phần (nếu hay lỗi thì hạ xuống 100MB)
CHUNK_RETRIES = 4
PARTS_DIR = os.path.join(BASE_DIR, "rom_parts")
_split_lock = Lock()


def prepare_parts():
    """Cắt rom.zip thành các phần nhỏ (1 lần, dùng chung mọi máy). Trả về list đường dẫn."""
    with _split_lock:
        size = os.path.getsize(ROM_FILE)
        stamp = f"{size}:{int(os.path.getmtime(ROM_FILE))}:{CHUNK_SIZE}"
        marker = os.path.join(PARTS_DIR, "stamp.txt")
        n = (size + CHUNK_SIZE - 1) // CHUNK_SIZE
        names = [os.path.join(PARTS_DIR, f"rom.part{i:03d}") for i in range(n)]
        try:
            with open(marker) as f:
                if f.read() == stamp and all(os.path.exists(p) for p in names):
                    return names
        except OSError:
            pass
        shutil.rmtree(PARTS_DIR, ignore_errors=True)
        os.makedirs(PARTS_DIR)
        with open(ROM_FILE, "rb") as src:
            for p in names:
                with open(p, "wb") as dst:
                    dst.write(src.read(CHUNK_SIZE))
        with open(marker, "w") as f:
            f.write(stamp)
        return names

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

    def note(self, text):
        """Dòng log có thời gian."""
        self.log(f"[{time.strftime('%H:%M:%S')}] {text}\n")

    def run_cmd(self, label, cmd, timeout):
        """Chạy lệnh, cập nhật status liên tục (thời gian + dòng output mới nhất).
        Trả về (returncode, output)."""
        start = time.time()
        last_line = ""
        output = b""
        self.log(f"\n[{time.strftime('%H:%M:%S')}] $ {' '.join(cmd)}\n")
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
                self.note(f"TIMEOUT sau {timeout}s")
                return -1, output.decode("utf-8", errors="replace") + "\nTIMEOUT"
        proc.wait()
        self.note(f"-> exit {proc.returncode} ({int(time.time() - start)}s)")
        return proc.returncode, output.decode("utf-8", errors="replace")

    def remote_size(self, path):
        r = subprocess.run(["adb", "-s", self.serial, "shell", f"wc -c < {path}"],
                           capture_output=True, text=True, timeout=60)
        try:
            size = int(r.stdout.strip())
        except ValueError:
            size = -1
        self.note(f"kiểm tra kích thước {path} = {size}")
        return size

    def push_smart(self, label, timeout):
        """Push thẳng rom.zip; chỉ khi lỗi (máy có adbd hỏng) mới chuyển sang push cắt nhỏ."""
        size = os.path.getsize(ROM_FILE)
        for attempt in range(1, PUSH_RETRIES + 1):
            code, out = self.run_cmd(label, ["adb", "-s", self.serial, "push", ROM_FILE, "/sdcard/rom.zip"], timeout)
            if code == 0 or self.remote_size("/sdcard/rom.zip") == size:
                return 0, out
            self.log(f"[push thẳng lỗi {attempt}/{PUSH_RETRIES}]\n")
            if attempt < PUSH_RETRIES:
                self.update_status(f"{label} lỗi, thử lại {attempt}/{PUSH_RETRIES - 1}...")
                self.wait_recovery(90)
                time.sleep(3)
        self.log("[chuyển sang push cắt nhỏ]\n")
        self.note("xóa /sdcard/rom.zip dở dang")
        subprocess.run(["adb", "-s", self.serial, "shell", "rm -f /sdcard/rom.zip"], timeout=60)
        return self.push_chunked(f"{label} (cắt nhỏ)", timeout)

    def push_chunked(self, label, timeout):
        """Push rom.zip theo từng phần nhỏ rồi ghép trên máy. Trả về (code, output)."""
        self.update_status(f"{label} đang cắt file...")
        parts = prepare_parts()
        total = len(parts)
        for i, part in enumerate(parts, 1):
            remote = f"/sdcard/{os.path.basename(part)}"
            expected = os.path.getsize(part)
            for attempt in range(1, CHUNK_RETRIES + 1):
                code, out = self.run_cmd(f"{label} phần {i}/{total}",
                                         ["adb", "-s", self.serial, "push", part, remote], timeout)
                if code == 0 or self.remote_size(remote) == expected:
                    break
                self.log(f"[phần {i} lỗi, thử lại {attempt}/{CHUNK_RETRIES}]\n")
                self.wait_recovery(90)
                time.sleep(2)
            else:
                return 1, f"push phần {i}/{total} lỗi"
            if self.remote_size(remote) != expected:
                return 1, f"phần {i}/{total} sai kích thước"
        self.update_status(f"{label} đang ghép file...")
        code, out = self.run_cmd(label, ["adb", "-s", self.serial, "shell",
                                         "cat /sdcard/rom.part* > /sdcard/rom.zip && rm -f /sdcard/rom.part*"], 600)
        if self.remote_size("/sdcard/rom.zip") != os.path.getsize(ROM_FILE):
            return 1, "rom.zip trên máy sai kích thước sau khi ghép"
        return 0, ""

    def wait_recovery(self, timeout):
        """Poll trạng thái adb tới khi 'recovery' hoặc hết timeout (không ném lỗi)."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                r = subprocess.run(["adb", "-s", self.serial, "get-state"],
                                   capture_output=True, text=True, timeout=10)
                if r.stdout.strip() == "recovery":
                    self.note(f"máy ở recovery (chờ {int(time.time() - start)}s)")
                    return True
            except Exception:
                pass
            time.sleep(2)
        self.note(f"không thấy recovery sau {timeout}s, vẫn thử lại")
        return False

    def run(self):
        self.log(f"\n{'=' * 60}\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] BẮT ĐẦU {self.serial}\n{'=' * 60}\n")
        try:
            self._run()
        except Exception as e:
            import traceback
            self.log(traceback.format_exc())
            self.update_status(f"{StatusEnum.FAILED.value}: exception: {e}")

    def _run(self):
        total = len(STEPS)
        for i, (desc, args, ignore_error, timeout) in enumerate(STEPS, 1):
            label = f"[{i}/{total}] {desc}"
            self.note(f"== {label}")
            self.update_status(f"{label} (0s)")
            cmd = ["adb", "-s", self.serial] + args
            # push hay lỗi do USB rớt khi nhiều máy cùng push -> thử lại tối đa 3 lần
            attempts = PUSH_RETRIES if args[0] == "push" else 1
            if args[0] == "reboot":
                # sau install adbd có thể restart -> chờ máy hiện lại, thử reboot vài lần
                self.update_status(f"{label} chờ máy sẵn sàng...")
                self.wait_recovery(120)
                time.sleep(3)
                attempts = 4
            for attempt in range(1, attempts + 1):
                try:
                    if args[0] == "push-smart":
                        code, out = self.push_smart(label, timeout)
                    else:
                        code, out = self.run_cmd(label, cmd, timeout)
                except Exception as e:
                    code, out = -1, str(e)
                if code == 0 or attempt == attempts:
                    break
                self.log(f"[retry {attempt}/{attempts - 1}] exit {code}\n")
                self.update_status(f"{label} lỗi, thử lại {attempt}/{attempts - 1}...")
                self.wait_recovery(90)
                time.sleep(3)
            if args[:2] == ["reboot", "recovery"]:
                time.sleep(10)  # chờ máy thật sự tắt rồi mới poll
                self.update_status(f"{label} chờ recovery...")
                if not self.wait_recovery(180):
                    self.update_status(f"{StatusEnum.FAILED.value}: {desc}: không vào lại recovery")
                    return
                time.sleep(5)
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
        if is_finished(status):
            self.note(f"KẾT QUẢ: {status}")


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
    only = set(sys.argv[1:])  # truyền serial để chỉ chạy các máy đó
    for device in list_devices(adb):
        if only and device.serial not in only:
            continue
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
