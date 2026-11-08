import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from threading import Thread
from colorama import init, Cursor
import adbutils
from devfilter import list_devices

init()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")  # log đầy đủ từng máy: logs/<serial>-root.log
os.makedirs(LOG_DIR, exist_ok=True)

adb = adbutils.AdbClient()

INSTALL_STATUS = {}


class StatusEnum(str, Enum):
    DONE = "Done"
    PENDING = "Recovery mode, pending"
    FAILED = "FAILED"


class InstallThread:
    def __init__(self, serial):
        self.serial = serial
        self.d = adb.device(serial=serial)

    def log(self, text):
        with open(os.path.join(LOG_DIR, f"{self.serial}-root.log"), "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {text}\n")

    def sh(self, cmd, **kwargs):
        """Chạy shell, ghi lệnh + output vào log. Lỗi vẫn ném ra để caller xử lý."""
        self.log(f"$ {cmd}")
        try:
            out = self.d.shell(cmd, **kwargs)
        except Exception as e:
            self.log(f"    [exception] {e}")
            raise
        for l in (str(out).strip().splitlines() or ["(empty)"])[:40]:
            self.log(f"    {l}")
        return out

    def run(self):
        self.log(f"{'=' * 50}\nBẮT ĐẦU {self.serial} {time.strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            self._run()
        except Exception as e:
            import traceback
            self.log(traceback.format_exc())
            self.update_status(f"{StatusEnum.FAILED.value}: exception: {e}")

    def _run(self):
        print("reboot recovery")
        if self.reboot_to_recovery("00"):
            self.update_status(StatusEnum.FAILED)
            return
        self.update_status("set tw_mtp_enabled 0")
        self.sh('set tw_mtp_enabled 0')
        time.sleep(1)
        try:
            self.update_status("twrp unmount data")
            self.sh('twrp unmount data', timeout=10)
            time.sleep(1)
        except Exception as e:
            self.update_status(f"error unmount data {e}")
            time.sleep(10)
        try:
            self.update_status("twrp format data")
            self.sh('twrp format data', timeout=10)
            time.sleep(1)
        except Exception as e:
            self.update_status(f"error format data {e}")
            time.sleep(10)
        self.update_status("install root.zip")
        time.sleep(1)
        self.log("push root.zip -> /sdcard/root.zip")
        self.d.sync.push(os.path.abspath('root.zip'), '/sdcard/root.zip')
        self.log("push xong")
        time.sleep(1)
        self.sh('twrp install /sdcard/root.zip')
        time.sleep(1)
        self.sh('twrp wipe cache')
        time.sleep(1)
        # self.update_status("install 2.zip")
        # time.sleep(1)
        # self.d.sync.push(os.path.abspath('2.zip'), '/sdcard/2.zip')
        # time.sleep(1)
        # self.d.shell('twrp install /sdcard/2.zip')
        # time.sleep(1)
        self.sh('twrp wipe cache')
        time.sleep(1)
        self.sh('reboot')
        self.update_status(StatusEnum.DONE)

    def reboot_to_recovery(self, step=None):
        self.sh('reboot recovery')
        for x in range(200):
            for info in list_devices(adb):
                if info.state == 'recovery' and info.serial == self.serial:
                    self.update_status(f"reboot recovery completed {step}")
                    return False
            self.update_status(f"waiting for reboot recovery complete {step}, {x}s")
            time.sleep(1)
        else:
            self.update_status(f"need reboot recovery {step}")
            return True

    def update_status(self, status: str):
        INSTALL_STATUS[self.serial] = status
        # không ghi các dòng "waiting ... Ns" mỗi giây, chỉ ghi lần đầu của mỗi trạng thái
        key = status.split(",")[0] if str(status).startswith("waiting") else status
        if key != getattr(self, "_last_logged", None):
            self._last_logged = key
            self.log(f"== {status}")


def clear_stdout():
    os.system('cls' if os.name == 'nt' else 'clear')


def clear_to_end():
    sys.stdout.write("\033[J")  # Clear from cursor to end of screen
    sys.stdout.flush()


def move_cursor_and_clear():
    sys.stdout.write(Cursor.POS(1, 1))  # Move cursor to top-left
    clear_to_end()  # Clear to the end of the screen


def display_statuses():
    clear_stdout()
    while True:  # While any process is still running
        move_cursor_and_clear()
        complete_count = 0
        for serial, status in INSTALL_STATUS.items():
            sys.stdout.write(f"Device {serial}: {status}\n")
            if status == StatusEnum.DONE or str(status).startswith(StatusEnum.FAILED.value):
                complete_count += 1
        if complete_count == len(INSTALL_STATUS):
            return
        sys.stdout.flush()
        time.sleep(0.5)  # Refresh interval


def main():
    if not os.path.isfile(os.path.abspath('root.zip')):
        print(f"Missing root.zip in {os.getcwd()}")
        return
    max_threads = 5
    install_threads = []
    only = set(sys.argv[1:])  # truyền serial để chỉ chạy các máy đó
    for device in list_devices(adb):
        if only and device.serial not in only:
            continue
        if device.state == 'recovery':
            INSTALL_STATUS[device.serial] = StatusEnum.PENDING
            install_thread = InstallThread(serial=device.serial)
            install_threads.append(install_thread)
    if not install_threads:
        print("Không có máy nào ở chế độ recovery" + (f" khớp {sorted(only)}" if only else ""))
        return
    monitor_thread = Thread(target=display_statuses)
    monitor_thread.start()
    # Use ThreadPoolExecutor to limit concurrent tasks
    with ThreadPoolExecutor(max_threads) as executor:
        futures = [
            executor.submit(install_thread.run) for install_thread in install_threads
        ]
        # Wait for all tasks to complete
        for future in as_completed(futures):
            pass
    monitor_thread.join()  # Wait for monitor to finish
    print("All tasks completed!")


if __name__ == '__main__':
    main()
