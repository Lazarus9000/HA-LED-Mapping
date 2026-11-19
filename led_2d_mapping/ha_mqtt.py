"""Home Assistant light entity for the add-on, over MQTT.

The add-on shows up in Home Assistant as a device with one light entity:
on/off starts and stops an effect, the effect list picks which one, and the
brightness slider dims whatever runs. It is announced with MQTT discovery,
so no YAML is needed; it only needs the Mosquitto broker add-on and the MQTT
integration.
"""
import json
import os
import queue
import random
import threading
import urllib.request

import paho.mqtt.client as mqtt

DISCOVERY_PREFIX = "homeassistant"
DEVICE_ID = "led_2d_mapping"
DISCOVERY_TOPIC = f"{DISCOVERY_PREFIX}/light/{DEVICE_ID}/config"
COMMAND_TOPIC = f"{DEVICE_ID}/light/set"
STATE_TOPIC = f"{DEVICE_ID}/light/state"
AVAILABILITY_TOPIC = f"{DEVICE_ID}/availability"
# Home Assistant says "online" here when it (re)starts; discovery is sent again then
BIRTH_TOPIC = f"{DISCOVERY_PREFIX}/status"
REPOSITORY_URL = "https://github.com/Lazarus9000/HA-LED-Mapping"


def effect_name(filename: str) -> str:
    """How an effect is called in Home Assistant: the file name without .py"""
    return filename[:-3] if filename.endswith(".py") else filename


def broker_settings() -> dict | None:
    """MQTT broker address and login, from the Supervisor (or MQTT_* variables for testing)."""
    if os.environ.get("MQTT_HOST"):
        return {
            "host": os.environ["MQTT_HOST"],
            "port": int(os.environ.get("MQTT_PORT", "1883")),
            "username": os.environ.get("MQTT_USERNAME"),
            "password": os.environ.get("MQTT_PASSWORD"),
            "ssl": False,
        }
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        print("MQTT: not running under Home Assistant, no light entity", flush=True)
        return None
    request = urllib.request.Request("http://supervisor/services/mqtt",
                                     headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read()).get("data") or {}
    except (OSError, ValueError) as error:
        print(f"MQTT: no broker found ({error}). Install the Mosquitto broker add-on and "
              "restart this add-on to get the Home Assistant light entity", flush=True)
        return None
    if not data.get("host"):
        print("MQTT: the Supervisor returned no broker address", flush=True)
        return None
    return {
        "host": data["host"],
        "port": int(data.get("port", 1883)),
        "username": data.get("username"),
        "password": data.get("password"),
        "ssl": bool(data.get("ssl")),
    }


class HomeAssistantLight:
    """Publishes the light and passes commands from Home Assistant to the controller.

    The controller is the add-on app. It provides:
      effects()          -> list of effect file names
      state()            -> {"running": bool, "effect": file name or None}
      start(file_name)   -> None, or an error text
      stop()
      last_effect()      -> file name to start when turned on without an effect
      brightness() / set_brightness(0-255)
    """

    def __init__(self, controller, version: str):
        self.controller = controller
        self.version = version
        self.client: mqtt.Client | None = None
        self.connected = False
        self.status = "not started"
        # Commands are handled one by one in their own thread: stopping an effect can
        # take a few seconds, and the MQTT connection must keep running meanwhile
        self.commands: queue.Queue = queue.Queue()

    # ------------------------------------------------------------------ connection

    def start(self) -> None:
        settings = broker_settings()
        if settings is None:
            self.status = "no MQTT broker"
            return
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{DEVICE_ID}_addon")
        if settings["username"]:
            client.username_pw_set(settings["username"], settings["password"])
        if settings["ssl"]:
            client.tls_set()
        client.will_set(AVAILABILITY_TOPIC, "offline", retain=True)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client = client
        threading.Thread(target=self._handle_commands, daemon=True).start()
        self.status = f"connecting to {settings['host']}:{settings['port']}"
        client.connect_async(settings["host"], settings["port"], keepalive=60)
        client.loop_start()

    def stop(self) -> None:
        if self.client is None:
            return
        if self.connected:
            self.client.publish(AVAILABILITY_TOPIC, "offline", retain=True).wait_for_publish(3)
        self.client.disconnect()
        self.client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self.status = f"MQTT login refused: {reason_code}"
            print(f"MQTT: {self.status}", flush=True)
            return
        self.connected = True
        self.status = "connected"
        print("MQTT: connected, publishing the Home Assistant light", flush=True)
        client.subscribe([(COMMAND_TOPIC, 0), (BIRTH_TOPIC, 0)])
        self.publish_discovery()

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected = False
        if self.status == "connected":
            self.status = "disconnected, reconnecting"
            print("MQTT: connection lost, reconnecting", flush=True)

    def _on_message(self, client, userdata, message):
        payload = message.payload.decode("utf-8", errors="ignore")
        if message.topic == BIRTH_TOPIC:
            if payload == "online":
                # Home Assistant restarted. A short random wait, as Home Assistant advises,
                # so it is ready and not every device answers at the same moment
                threading.Timer(random.uniform(1, 5), self.publish_discovery).start()
            return
        try:
            command = json.loads(payload)
        except ValueError:
            print(f"MQTT: ignoring a command that is not JSON: {payload[:100]}", flush=True)
            return
        if isinstance(command, dict):
            self.commands.put(command)

    # ------------------------------------------------------------------ publishing

    def publish_discovery(self) -> None:
        if not self.connected:
            return
        config = {
            "name": None,  # the entity takes the device's name
            "unique_id": f"{DEVICE_ID}_light",
            "schema": "json",
            "command_topic": COMMAND_TOPIC,
            "state_topic": STATE_TOPIC,
            "availability_topic": AVAILABILITY_TOPIC,
            "brightness": True,
            "brightness_scale": 255,
            "supported_color_modes": ["brightness"],
            "effect": True,
            "effect_list": [effect_name(e) for e in self.controller.effects()],
            "icon": "mdi:led-strip-variant",
            "device": {
                "identifiers": [DEVICE_ID],
                "name": "LED 2D Mapping",
                "manufacturer": "LED 2D Mapping",
                "model": "2D effects for WLED",
                "sw_version": self.version,
            },
            "origin": {
                "name": "LED 2D Mapping add-on",
                "sw_version": self.version,
                "support_url": REPOSITORY_URL,
            },
        }
        # Retained, so the entity is there right away after Home Assistant restarts
        self.client.publish(DISCOVERY_TOPIC, json.dumps(config), retain=True)
        self.client.publish(AVAILABILITY_TOPIC, "online", retain=True)
        self.publish_state()

    def publish_state(self) -> None:
        if not self.connected:
            return
        state = self.controller.state()
        effect = state["effect"] if state["running"] else self.controller.last_effect()
        payload = {
            "state": "ON" if state["running"] else "OFF",
            "brightness": self.controller.brightness(),
            "color_mode": "brightness",
        }
        if effect:
            payload["effect"] = effect_name(effect)
        self.client.publish(STATE_TOPIC, json.dumps(payload), retain=True)

    # ------------------------------------------------------------------ commands

    def _handle_commands(self) -> None:
        while True:
            command = self.commands.get()
            try:
                self._handle(command)
            except Exception as error:  # a bad command must not end the command thread
                print(f"MQTT: could not carry out {command}: {error}", flush=True)
            self.publish_state()

    def _handle(self, command: dict) -> None:
        if "brightness" in command:
            self.controller.set_brightness(int(command["brightness"]))

        if command.get("state") == "OFF":
            self.controller.stop()
            return

        wanted = None
        if command.get("effect") is not None:
            wanted = f"{command['effect']}.py"
            if wanted not in self.controller.effects():
                print(f"MQTT: unknown effect {command['effect']}", flush=True)
                return

        state = self.controller.state()
        if command.get("state") == "ON" or wanted:
            target = wanted or (state["effect"] if state["running"] else None) or self.controller.last_effect()
            if target is None:
                print("MQTT: no effect to start", flush=True)
                return
            if not state["running"] or state["effect"] != target:
                error = self.controller.start(target)
                if error:
                    print(f"MQTT: could not start {target}: {error}", flush=True)
