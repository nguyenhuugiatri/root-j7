import os
import subprocess
import sys
import adbutils

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
adb = adbutils.AdbClient()

# Chỉ các trạng thái này chạy được chay-root.py
RUNNABLE = ("device", "recovery")


def prop(serial, name):
    try:
        return adb.device(serial=serial).shell(f"getprop {name}").strip()
    except Exception:
        return ""


def list_all():
    rows = []
    for d in adb.list():
        model = prop(d.serial, "ro.product.model") if d.state == "device" else ""
        android = prop(d.serial, "ro.build.version.release") if d.state == "device" else ""
        rows.append((d.serial, d.state, model, android))
    return rows


def show(rows):
    print("\nDanh sách devices:")
    print(f"  {'#':>2}  {'SERIAL':<22} {'TRẠNG THÁI':<14} {'MODEL':<18} ANDROID")
    for i, (serial, state, model, android) in enumerate(rows, 1):
        note = "" if state in RUNNABLE else "  (không chạy được)"
        print(f"  {i:>2}  {serial:<22} {state:<14} {model:<18} {android}{note}")


def parse_choice(text, rows):
    """'1,3' / '1 3' / '2-4' / 'a' (tất cả chạy được) -> list serial; None nếu không hợp lệ."""
    text = text.strip().lower()
    if text in ("a", "all"):
        return [r[0] for r in rows if r[1] in RUNNABLE]
    picked = []
    for part in text.replace(",", " ").split():
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                idx = range(int(a), int(b) + 1)
            else:
                idx = [int(part)]
        except ValueError:
            return None
        for i in idx:
            if not 1 <= i <= len(rows):
                print(f"Số {i} không hợp lệ.")
                return None
            serial, state = rows[i - 1][0], rows[i - 1][1]
            if state not in RUNNABLE:
                print(f"{serial} đang '{state}', không chạy được (cần 'device' hoặc 'recovery').")
                return None
            if serial not in picked:
                picked.append(serial)
    return picked or None


def main():
    while True:
        rows = list_all()
        if not rows:
            print("Không thấy device nào.")
            return
        show(rows)
        text = input("\nChọn số device (vd: 1  |  1,3  |  2-4  |  a = tất cả, r = quét lại, q = thoát): ").strip()
        if text.lower() in ("q", "quit", "exit"):
            return
        if text.lower() in ("r", ""):
            continue
        serials = parse_choice(text, rows)
        if serials:
            break

    print(f"\nChạy cho: {', '.join(serials)}")
    env = dict(os.environ, ADB_SERIALS=",".join(serials))
    result = subprocess.run([sys.executable, os.path.join(BASE_DIR, "chay-root.py")], cwd=BASE_DIR, env=env)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
