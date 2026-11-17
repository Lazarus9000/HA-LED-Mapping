import os
import signal
import subprocess
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

app = FastAPI()
templates = Jinja2Templates(directory="templates")

DATA_DIR = Path("/data")
EFFECTS_DIR = Path("/app/effects")
CALIB_TXT = DATA_DIR / "calibration.txt"
SESSION_JSON = DATA_DIR / "session.json"
CALIB_IMG = DATA_DIR / "calibration_reference.jpg"

active_process: subprocess.Popen | None = None
active_effect_name: str | None = None


def get_available_effects() -> list[str]:
    if not EFFECTS_DIR.exists():
        return []
    return sorted([f.name for f in EFFECTS_DIR.glob("*.py") if f.name != "__init__.py"])


def count_leds() -> int:
    if not CALIB_TXT.exists():
        return 0
    lines = [ln.strip() for ln in CALIB_TXT.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
    return len(lines)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "effects": get_available_effects(),
            "active_effect": active_effect_name,
            "has_calibration": CALIB_TXT.exists(),
            "led_count": count_leds(),
            "has_reference_img": CALIB_IMG.exists(),
        },
    )


@app.get("/viewer", response_class=HTMLResponse)
async def viewer(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="viewer.html",
        context={},
    )


@app.post("/upload-calibration")
async def upload_calibration(
    calibration_file: UploadFile = File(...),
    session_file: UploadFile | None = File(None),
    reference_image: UploadFile | None = File(None),
):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw_bytes = await calibration_file.read()
    text = raw_bytes.decode("utf-8", errors="ignore").strip()

    # If user uploaded a calibrate_video.py session JSON on the main page, extract x,y lines to calibration.txt
    if text.startswith("{"):
        import json
        data = json.loads(text)
        points = data.get("points", [])
        lines = [f"{round(p['mx'] if p.get('mx') is not None else p['x'])},{round(p['my'] if p.get('my') is not None else p['y'])}" for p in points]
        CALIB_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
        SESSION_JSON.write_text(text, encoding="utf-8")
    else:
        CALIB_TXT.write_bytes(raw_bytes)
        if session_file and session_file.filename:
            SESSION_JSON.write_bytes(await session_file.read())
        elif SESSION_JSON.exists():
            SESSION_JSON.unlink()

    if reference_image and reference_image.filename:
        CALIB_IMG.write_bytes(await reference_image.read())

    return JSONResponse({"status": "ok", "led_count": count_leds()})


@app.get("/api/calibration", response_class=PlainTextResponse)
async def get_calibration():
    if not CALIB_TXT.exists():
        return JSONResponse({"error": "No calibration.txt uploaded"}, status_code=404)
    return PlainTextResponse(CALIB_TXT.read_text(encoding="utf-8"))


@app.get("/api/session", response_class=PlainTextResponse)
async def get_session():
    if not SESSION_JSON.exists():
        return JSONResponse({"error": "No session JSON stored"}, status_code=404)
    return PlainTextResponse(SESSION_JSON.read_text(encoding="utf-8"), media_type="application/json")


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
    env["CALIBRATION_PATH"] = str(CALIB_TXT)

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
