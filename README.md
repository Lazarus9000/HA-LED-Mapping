# LED 2D Mapping Home Assistant Add-on Repository

This repository contains the **LED 2D Mapping** Home Assistant add-on with an integrated web UI for uploading calibration data, viewing mapped LEDs, and starting/stopping Python LED effects.

## Setup
1. Update the `url` field in `repository.yaml` with your GitHub repository URL.
2. Copy your existing effect scripts (`ball.py`, `bangbang.py`, `doom.py`, etc.) into `led_2d_mapping/effects/`.
3. Push this repository to GitHub.
4. In Home Assistant, go to **Settings > Add-ons > Add-on Store > Menu (top right) > Repositories** and add your GitHub repository URL.

## Home Assistant light entity
When the **Mosquitto broker** add-on and the **MQTT** integration are installed, the add-on shows up in Home Assistant as a device called **LED 2D Mapping** with one light entity (`light.led_2d_mapping`). No YAML is needed: the add-on gets the broker login from Home Assistant and announces itself with MQTT discovery. If Mosquitto is installed after this add-on, restart this add-on.

- **On / off** starts and stops an effect. Turning on without choosing an effect starts the last one used.
- **Effect** lists the effects (file names without `.py`). Choosing one switches to it.
- **Brightness** dims whatever effect runs, on top of the effect's own brightness, without restarting it.

Starting and stopping from the add-on page and from Home Assistant stay in step. If an effect crashes, the light shows as off.

Example automation:

```yaml
alias: Snow at sunset
triggers:
  - trigger: sun
    event: sunset
actions:
  - action: light.turn_on
    target:
      entity_id: light.led_2d_mapping
    data:
      effect: snev2
      brightness: 180
```

While an effect runs it overrides WLED; when it stops, WLED goes back to its own effect. The WLED integration's light for the same controller does not stop the effect.
