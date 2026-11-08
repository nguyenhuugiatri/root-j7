import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import Enum
from threading import Thread
import subprocess


def _ensure(module, package=None):
    try:
        __import__(module)
    except ModuleNotFoundError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package or module])


_ensure("colorama")
_ensure("adbutils")

from colorama import init, Cursor
import adbutils
from devfilter import list_devices

init()

adb = adbutils.AdbClient()

STATUS = {}


class StatusEnum(str, Enum):
    DONE = "Done, in recovery"
    PENDING = "Pending"
    FAILED = "FAILED"


class RecoveryThread:
    def __init__(self, serial):
        self.serial = serial
        self.d = adb.device(serial=serial)

    def run(self):
        try:
            self.update_status("sending reboot recovery")
            self.d.shell('reboot recovery')
        except Exception as e:
            # adb connection may drop as the device reboots
            self.update_status(f"reboot command: {e}")
        if self.wait_for_recovery():
            self.update_status(StatusEnum.FAILED)
        else:
            self.update_status(StatusEnum.DONE)

    def wait_for_recovery(self, timeout=200):
        """Return True on timeout, False once the device is in recovery."""
        # Give the device time to actually go offline first
        time.sleep(3)
        for x in range(timeout):
            try:
                for info in list_devices(adb):
                    if info.serial == self.serial and info.state == 'recovery':
                        return False
            except Exception:
                pass
            self.update_status(f"waiting for recovery, {x}s")
            time.sleep(1)
        return True

    def update_status(self, status: str):
        STATUS[self.serial] = status


def clear_stdout():
    os.system('cls' if os.name == 'nt' else 'clear')


def clear_to_end():
    sys.stdout.write("\033[J")  # Clear from cursor to end of screen
    sys.stdout.flush()


def move_cursor_and_clear():
    sys.stdout.write(Cursor.POS(1, 1))  # Move cursor to top-left
    clear_to_end()


def display_statuses():
    clear_stdout()
    while True:
        move_cursor_and_clear()
        complete_count = 0
        for serial, status in STATUS.items():
            sys.stdout.write(f"Device {serial}: {status}\n")
            if status in {StatusEnum.DONE, StatusEnum.FAILED}:
                complete_count += 1
        sys.stdout.flush()
        if complete_count == len(STATUS):
            return
        time.sleep(0.5)


def main():
    max_threads = 5
    threads = []
    for device in list_devices(adb):
        if device.state == 'device':  # running normally (booted into Android)
            STATUS[device.serial] = StatusEnum.PENDING
            threads.append(RecoveryThread(serial=device.serial))

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
