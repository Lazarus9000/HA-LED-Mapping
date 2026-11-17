import os
import time
from pathlib import Path

calib_path = Path(os.environ.get("CALIBRATION_PATH", "/data/calibration.txt"))

if calib_path.exists():
    coords = []
    for line in calib_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        x, y = map(float, line.split(",")[:2])
        coords.append((x, y))
    print(f"Loaded {len(coords)} LED coordinates from {calib_path}")
else:
    print(f"Warning: {calib_path} not found")

while True:
    time.sleep(1)
