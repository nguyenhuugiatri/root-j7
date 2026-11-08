import os
import subprocess
import sys
import time
import adbutils
from devfilter import list_devices

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
adb = adbutils.AdbClient()

BOOT_TIMEOUT = 600  # giây chờ máy lên Android / vào recovery


def run_script(name):
    print(f"\n===== RUN {name} =====", flush=True)
    result = subprocess.run([sys.executable, os.path.join(BASE_DIR, name)], cwd=BASE_DIR)
    if result.returncode != 0:
        print(f"{name} failed (exit code {result.returncode}). Stop.")
        sys.exit(result.returncode)


def states():
    return {d.serial: d.state for d in list_devices(adb)}


def boot_completed(serial):
    try:
        return adb.device(serial=serial).shell("getprop sys.boot_completed").strip() == "1"
    except Exception:
        return False


def wait_all(serials, target):
    """Chờ tất cả serial đạt trạng thái target ('recovery' hoặc 'device')."""
    print(f"\nWaiting for {len(serials)} device(s) to reach '{target}'...", flush=True)
    start = time.time()
    while time.time() - start < BOOT_TIMEOUT:
        current = states()
        pending = []
        for s in serials:
            if current.get(s) != target:
                pending.append(s)
            elif target == "device" and not boot_completed(s):
                pending.append(s)
        if not pending:
            print(f"All devices are '{target}'.", flush=True)
            return
        time.sleep(2)
    print(f"Timeout waiting for '{target}': {', '.join(pending)}")
    sys.exit(1)


REQUIRED_FILES = [
    "rom.zip",  # buoc-1
    "root.zip",  # buoc-2
    "app-magisk23.apk",  # buoc-3
    "boot-recovery.py",
    "buoc-1-install-rom.py",
    "buoc-2-root.py",
    "buoc-3-install-magisk.py",
]


def check_files():
    missing = [n for n in REQUIRED_FILES if not os.path.isfile(os.path.join(BASE_DIR, n))]
    if missing:
        print(f"Thiếu file trong {BASE_DIR}:")
        for n in missing:
            print(f"  - {n}")
        sys.exit(1)


def main():
    check_files()
    current = states()
    serials = [s for s, st in current.items() if st in ("device", "recovery")]
    if not serials:
        print("No devices found (device/recovery).")
        return
    print(f"Devices: {', '.join(serials)}")

    # Bước 0: reboot recovery (bỏ qua nếu tất cả máy đã ở recovery)
    if any(current[s] == "device" for s in serials):
        run_script("boot-recovery.py")
    else:
        print("\nAll devices already in recovery, skip boot-recovery.py")
    wait_all(serials, "recovery")

    # Bước 1: cài rom (cuối bước máy tự reboot vào Android)
    run_script("buoc-1-install-rom.py")
    wait_all(serials, "device")

    # Bước 2 cần máy ở recovery -> reboot recovery lại (chỉ máy đang ở Android)
    if any(st == "device" for s, st in states().items() if s in serials):
        run_script("boot-recovery.py")
    wait_all(serials, "recovery")

    # Bước 2: root (cuối bước máy tự reboot vào Android)
    run_script("buoc-2-root.py")
    wait_all(serials, "device")

    # Bước 3: cài magisk (cần máy đang chạy)
    run_script("buoc-3-install-magisk.py")

    print("\nALL STEPS COMPLETED!")


if __name__ == "__main__":
    main()
