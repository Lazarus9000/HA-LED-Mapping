# LED 2D Mapping Home Assistant Add-on Repository

This repository contains the **LED 2D Mapping** Home Assistant add-on with an integrated web UI for uploading calibration data, viewing mapped LEDs, and starting/stopping Python LED effects.

## Setup
1. Update the `url` field in `repository.yaml` with your GitHub repository URL.
2. Copy your existing effect scripts (`ball.py`, `bangbang.py`, `doom.py`, etc.) into `led_2d_mapping/effects/`.
3. Push this repository to GitHub.
4. In Home Assistant, go to **Settings > Add-ons > Add-on Store > Menu (top right) > Repositories** and add your GitHub repository URL.
