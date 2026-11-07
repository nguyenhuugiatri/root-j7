import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

STEPS = [
    "buoc-4-connect-wifi.py",
    "buoc-5-tat-am-thanh-rung-nfc.py",
]


def run_script(name):
    print(f"\n===== RUN {name} =====", flush=True)
    result = subprocess.run([sys.executable, os.path.join(BASE_DIR, name)], cwd=BASE_DIR)
    if result.returncode != 0:
        print(f"{name} failed (exit code {result.returncode}). Stop.")
        sys.exit(result.returncode)


def main():
    for name in STEPS:
        run_script(name)
    print("\nWIFI + AM THANH COMPLETED!")


if __name__ == "__main__":
    main()
