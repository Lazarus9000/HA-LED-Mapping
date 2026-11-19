import collections
import contextlib
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

from ha_mqtt import HomeAssistantLight

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

# LED_DATA_DIR only exists to run the app outside Home Assistant
DATA_DIR = Path(os.environ.get("LED_DATA_DIR", "/data"))
EFFECTS_DIR = APP_DIR / "effects"
CALIB_TXT = DATA_DIR / "calibration.txt"
SESSION_JSON = DATA_DIR / "session.json"
CALIB_IMG = DATA_DIR / "calibration_reference.jpg"
OPTIONS_JSON = DATA_DIR / "options.json"
# Home Assistant light: last effect and brightness, kept over restarts
LIGHT_JSON = DATA_DIR / "ha_light.json"
# Brightness the running effect reads (see wledMapper.py), so it changes without a restart
BRIGHTNESS_FILE = DATA_DIR / "brightness"

# Python files in the effects folder that are helpers, not effects
NOT_EFFECTS = {"__init__.py", "wledMapper.py"}

active_process: subprocess.Popen | None = None
active_effect_name: str | None = None
# Last lines the running (or last) effect printed, shown in the web UI
effect_output: collections.deque[str] = collections.deque(maxlen=40)
# Starting and stopping happen from the web page and from Home Assistant, one at a time
effect_lock = threading.RLock()
light: HomeAssistantLight | None = None


def addon_version() -> str:
    for line in (APP_DIR / "config.yaml").read_text(encoding="utf-8").splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip().strip('"')
    return "unknown"


def load_light_settings() -> dict:
    try:
        return json.loads(LIGHT_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_light_settings(**changes) -> None:
    settings = {**load_light_settings(), **changes}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LIGHT_JSON.write_text(json.dumps(settings), encoding="utf-8")


def notify_home_assistant() -> None:
    if light is not None:
        light.publish_state()


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
    # The effect ended by itself (crashed): show the light as off in Home Assistant
    if process is active_process:
        notify_home_assistant()


def start_effect_now(effect: str) -> str | None:
    """Start an effect, stopping the one that runs. Returns an error text, or None."""
    global active_process, active_effect_name
    # Only names from the effect list, so no other file can be started
    if effect not in get_available_effects():
        return f"Effect {effect} not found"
    if not CALIB_TXT.exists():
        return "Upload a calibration first"

    with effect_lock:
        stop_effect_now(notify=False)

        ip, port = wled_settings()
        env = os.environ.copy()
        env["CALIBRATION_PATH"] = str(CALIB_TXT)
        env["WLED_IP"] = ip
        env["WLED_PORT"] = str(port)
        env["LED_BRIGHTNESS_FILE"] = str(BRIGHTNESS_FILE)
        env["PYTHONUNBUFFERED"] = "1"
        if not BRIGHTNESS_FILE.exists():
            write_brightness(get_brightness())

        effect_output.clear()
        # Run in the effects folder, so effects find their pictures and wledMapper.py
        active_process = subprocess.Popen(
            [sys.executable, str(EFFECTS_DIR / effect)], cwd=str(EFFECTS_DIR), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        active_effect_name = effect
        threading.Thread(target=_collect_output, args=(active_process, effect), daemon=True).start()
        save_light_settings(effect=effect)
    notify_home_assistant()
    return None


def stop_effect_now(notify: bool = True) -> None:
    """Stop the running effect and hand the LEDs back to WLED."""
    global active_process, active_effect_name
    with effect_lock:
        process = active_process
        active_process = None
        active_effect_name = None
        if process is not None:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(5)
                except subprocess.TimeoutExpired:
                    process.kill()
            release_leds()
    if notify:
        notify_home_assistant()


def get_brightness() -> int:
    return int(load_light_settings().get("brightness", 255))


def write_brightness(value: int) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BRIGHTNESS_FILE.write_text(str(value), encoding="utf-8")


def set_brightness(value: int) -> None:
    value = max(0, min(255, int(value)))
    save_light_settings(brightness=value)
    write_brightness(value)


def last_effect() -> str | None:
    effects = get_available_effects()
    remembered = load_light_settings().get("effect")
    if remembered in effects:
        return remembered
    return "snev2.py" if "snev2.py" in effects else (effects[0] if effects else None)


class Controller:
    """What the Home Assistant light can do with the add-on."""
    effects = staticmethod(get_available_effects)
    start = staticmethod(start_effect_now)
    stop = staticmethod(stop_effect_now)
    last_effect = staticmethod(last_effect)
    brightness = staticmethod(get_brightness)
    set_brightness = staticmethod(set_brightness)

    @staticmethod
    def state() -> dict:
        state = effect_state()
        return {"running": state["running"], "effect": state["effect"]}


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global light
    light = HomeAssistantLight(Controller, addon_version())
    light.start()
    yield
    # The add-on is stopping: stop the effect so the LEDs go back to WLED
    await run_in_threadpool(stop_effect_now)
    light.stop()


app = FastAPI(lifespan=lifespan)


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
            "mqtt_status": light.status if light is not None else "not started",
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
    error = await run_in_threadpool(start_effect_now, effect)
    if error:
        return JSONResponse({"error": error}, status_code=404 if "not found" in error else 400)
    return JSONResponse({"status": "started", "effect": effect})


@app.post("/api/effect/stop")
async def stop_effect():
    await run_in_threadpool(stop_effect_now)
    return JSONResponse({"status": "stopped"})
