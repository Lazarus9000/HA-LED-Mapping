import os
import json
import signal
import subprocess
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates

app = FastAPI()
templates = Jinja2Templates(directory="templates")

DATA_DIR = Path("/data")
EFFECTS_DIR = Path("/app/effects")
CALIB_JSON = DATA_DIR / "calibration.json"
CALIB_IMG = DATA_DIR / "calibration_reference.jpg"

active_process: subprocess.Popen | None = None
active_effect_name: str | None = None


def get_available_effects() -> list[str]:
    if not EFFECTS_DIR.exists():
        return []
    return sorted([f.name for f in EFFECTS_DIR.glob("*.py") if f.name != "__init__.py"])


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "effects": get_available_effects(),
            "active_effect": active_effect_name,
            "has_calibration": CALIB_JSON.exists(),
            "has_reference_img": CALIB_IMG.exists(),
        },
    )


@app.post("/upload-calibration")
async def upload_calibration(
    calibration_file: UploadFile = File(...),
    reference_image: UploadFile | None = File(None),
):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CALIB_JSON.write_bytes(await calibration_file.read())
    if reference_image and reference_image.filename:
        CALIB_IMG.write_bytes(await reference_image.read())
    return JSONResponse({"status": "ok"})


@app.get("/api/calibration")
async def get_calibration():
    if not CALIB_JSON.exists():
        return JSONResponse({"error": "No calibration file uploaded"}, status_code=404)
    return JSONResponse(json.loads(CALIB_JSON.read_text()))


@app.get("/api/reference-image")
async def get_reference_image():
    if not CALIB_IMG.exists():
        return JSONResponse({"error": "No reference image"}, status_code=404)
    return FileResponse(CALIB_IMG)


@app.post("/api/effect/start")
async def start_effect(effect: str = Form(...)):
    global active_process, active_effect_name
    await stop_effect()

    script_path = EFFECTS_DIR / effect
    if not script_path.exists():
        return JSONResponse({"error": "Effect not found"}, status_code=404)

    env = os.environ.copy()
    env["CALIBRATION_PATH"] = str(CALIB_JSON)

    active_process = subprocess.Popen(["python3", str(script_path)], env=env)
    active_effect_name = effect
    return JSONResponse({"status": "started", "effect": effect})


@app.post("/api/effect/stop")
async def stop_effect():
    global active_process, active_effect_name
    if active_process and active_process.poll() is None:
        active_process.send_signal(signal.SIGTERM)
        try:
            active_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            active_process.kill()
    active_process = None
    active_effect_name = None
    return JSONResponse({"status": "stopped"})
