import collections
import json
import os
import signal
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Request, Form
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, PlainTextResponse
from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent

app = FastAPI()
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

# LED_DATA_DIR only exists to run the app outside Home Assistant
DATA_DIR = Path(os.environ.get("LED_DATA_DIR", "/data"))
EFFECTS_DIR = APP_DIR / "effects"
CALIB_TXT = DATA_DIR / "calibration.txt"
SESSION_JSON = DATA_DIR / "session.json"
CALIB_IMG = DATA_DIR / "calibration_reference.jpg"
OPTIONS_JSON = DATA_DIR / "options.json"

# Python files in the effects folder that are helpers, not effects
NOT_EFFECTS = {"__init__.py", "wledMapper.py"}

active_process: subprocess.Popen | None = None
active_effect_name: str | None = None
# Last lines the running (or last) effect printed, shown in the web UI
effect_output: collections.deque[str] = collections.deque(maxlen=40)


def get_available_effects() -> list[str]:
    if not EFFECTS_DIR.exists():
        return []
    return sorted(f.name for f in EFFECTS_DIR.glob("*.py") if f.name not in NOT_EFFECTS and not f.name.startswith("_"))


def count_leds() -> int:
    if not CALIB_TXT.exists():
        return 0
    lines = [ln.strip() for ln in CALIB_TXT.read_text(encoding="utf-8", errors="ignore").splitlines() if ln.strip()]
    return len(lines)


def wled_settings() -> tuple[str, int]:
    """WLED address and port from the add-on configuration."""
    options = {}
    if OPTIONS_JSON.exists():
        try:
            options = json.loads(OPTIONS_JSON.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return str(options.get("default_udp_ip", "192.168.1.100")).strip(), int(options.get("default_udp_port", 21324))


def effect_state() -> dict:
    """What the effect process is doing right now."""
    if active_process is None:
        return {"running": False, "effect": active_effect_name, "exit_code": None}
    code = active_process.poll()
    return {"running": code is None, "effect": active_effect_name, "exit_code": code}


def release_leds() -> None:
    """Tell WLED to leave realtime mode, so it goes back to its own effects.

    The effects do this themselves when they stop. This is the fallback for an effect
    that had to be killed or crashed before it could."""
    ip, _ = wled_settings()
    request = urllib.request.Request(
        f"http://{ip}/json/state", data=json.dumps({"live": False}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=3).read()
    except OSError as error:
        print(f"Could not reach WLED at {ip}: {error}", flush=True)


def _collect_output(process: subprocess.Popen, name: str) -> None:
    """Copy the effect's output to the add-on log and keep the last lines for the UI."""
    for line in process.stdout:
        line = line.rstrip()
        effect_output.append(line)
        print(f"[{name}] {line}", flush=True)
    code = process.wait()
    effect_output.append(f"--- {name} stopped (exit code {code}) ---")
    print(f"[{name}] stopped with exit code {code}", flush=True)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    state = effect_state()
    ip, port = wled_settings()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "effects": get_available_effects(),
            "active_effect": state["effect"] if state["running"] else None,
            "has_calibration": CALIB_TXT.exists(),
            "led_count": count_leds(),
            "has_reference_img": CALIB_IMG.exists(),
            "wled_address": f"{ip}:{port}",
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


@app.get("/api/effect/status")
async def effect_status():
    return JSONResponse({**effect_state(), "output": list(effect_output)})


@app.post("/api/effect/start")
async def start_effect(effect: str = Form(...)):
    global active_process, active_effect_name
    # Only names from the effect list, so no other file can be started
    if effect not in get_available_effects():
        return JSONResponse({"error": f"Effect {effect} not found"}, status_code=404)
    if not CALIB_TXT.exists():
        return JSONResponse({"error": "Upload a calibration first"}, status_code=400)

    await stop_effect()

    ip, port = wled_settings()
    env = os.environ.copy()
    env["CALIBRATION_PATH"] = str(CALIB_TXT)
    env["WLED_IP"] = ip
    env["WLED_PORT"] = str(port)
    env["PYTHONUNBUFFERED"] = "1"

    effect_output.clear()
    # Run in the effects folder, so effects find their pictures and wledMapper.py
    active_process = subprocess.Popen(
        [sys.executable, str(EFFECTS_DIR / effect)], cwd=str(EFFECTS_DIR), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    active_effect_name = effect
    threading.Thread(target=_collect_output, args=(active_process, effect), daemon=True).start()
    return JSONResponse({"status": "started", "effect": effect})


@app.post("/api/effect/stop")
async def stop_effect():
    global active_process, active_effect_name
    process = active_process
    active_process = None
    active_effect_name = None
    if process is not None:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                await run_in_threadpool(process.wait, 5)
            except subprocess.TimeoutExpired:
                process.kill()
        await run_in_threadpool(release_leds)
    return JSONResponse({"status": "stopped"})
