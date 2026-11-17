import os
import json
import time
from pathlib import Path

calib_path = Path(os.environ.get("CALIBRATION_PATH", "/data/calibration.json"))

if calib_path.exists():
    data = json.loads(calib_path.read_text())
    print(f"Loaded calibration data from {calib_path}")
else:
    print(f"Warning: {calib_path} not found")

while True:
    time.sleep(1)
