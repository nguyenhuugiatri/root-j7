"""Chọn máy adb và phát tiếng bip (+ rung, bật màn hình, notification) để nhận biết máy.

Dùng:
    python bip-may.py              # liệt kê máy, chọn bằng số
    python bip-may.py SERIAL ...   # bip thẳng các máy có serial đó
"""
import sys
import adbutils

adb = adbutils.AdbClient()


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
        note = "" if state == "device" else "  (không bip được)"
        print(f"  {i:>2}  {serial:<22} {state:<14} {model:<18} {android}{note}")


def parse_choice(text, rows):
    """'1,3' / '1 3' / '2-4' / 'a' (tất cả bip được) -> list serial; None nếu không hợp lệ."""
    text = text.strip().lower()
    if text in ("a", "all"):
        return [r[0] for r in rows if r[1] == "device"]
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
            if state != "device":
                print(f"{serial} đang '{state}', không bip được (cần 'device').")
                return None
            if serial not in picked:
                picked.append(serial)
    return picked or None


def beep(serial):
    d = adb.device(serial=serial)

    def sh(cmd):
        try:
            return d.shell(cmd).strip()
        except Exception:
            return ""

    sh("input keyevent KEYCODE_WAKEUP")           # bật màn hình
    sh("media volume --stream 3 --set 15")        # volume media tối đa
    sh("media volume --stream 5 --set 7")         # volume notification
    sh("cmd vibrator vibrate 1500")               # rung
    sh("cmd notification post -S bigtext -t 'MAY NAY' beep 'May nay dang bip'")
    path = sh("ls -d /system/media/audio/alarms/* /system/media/audio/ringtones/* "
              "/system/media/audio/notifications/* 2>/dev/null | head -1")
    if path:
        sh(f"am start -a android.intent.action.VIEW -d file://{path} -t audio/*")
    sh("input keyevent KEYCODE_VOLUME_UP")        # tiếng click phím + hiện thanh volume


def main():
    if len(sys.argv) > 1:
        for s in sys.argv[1:]:
            print(f"Bip {s}")
            beep(s)
        return

    while True:
        rows = list_all()
        if not rows:
            print("Không thấy device nào.")
            return
        show(rows)
        text = input("\nChọn số device để bip (vd: 1  |  1,3  |  2-4  |  a = tất cả, r = quét lại, q = thoát): ").strip()
        if text.lower() in ("q", "quit", "exit"):
            return
        if text.lower() in ("r", ""):
            continue
        serials = parse_choice(text, rows)
        for s in serials or []:
            print(f"Bip {s} ...")
            beep(s)


if __name__ == "__main__":
    main()
