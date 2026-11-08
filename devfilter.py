"""Lọc danh sách device theo biến môi trường ADB_SERIALS (các serial cách nhau bằng dấu phẩy).

Không đặt biến -> dùng tất cả device (hành vi mặc định).
chay-root-le.py đặt biến này để chỉ chạy các bước cho serial được chọn.
"""
import os


def selected_serials():
    raw = os.environ.get("ADB_SERIALS", "").strip()
    return {s.strip() for s in raw.split(",") if s.strip()} or None


def list_devices(adb):
    devices = adb.list()
    sel = selected_serials()
    if sel is None:
        return devices
    return [d for d in devices if d.serial in sel]
