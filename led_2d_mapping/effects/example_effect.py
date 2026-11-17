"""Example effect: a slow rainbow moving across the LEDs.

Start here when writing a new effect. Every .py file in this folder (except
wledMapper.py) shows up in the add-on's effect list. An effect:

  1. creates a Mapper with Mapper.fromSettings(). It reads the WLED address and the
     uploaded calibration from the add-on, see wledMapper.py
  2. draws each frame as an OpenCV picture (blue, green, red) of any size. The whole
     picture is stretched over the area the calibration covers
  3. sends it with wled.image(picture, brightness in percent)
  4. loops until the add-on stops it. The LEDs are handed back to WLED automatically

Files next to the effect (pictures) can be opened by name, the effect runs in this folder.
"""
import time
import cv2
import numpy as np
from wledMapper import Mapper, preview

wled = Mapper.fromSettings()

size = 200               #the rainbow is drawn in a size x size picture
secondsPerRound = 10     #time for the colors to travel once across the LEDs
framesPerSecond = 25
brightness = 50

#Hue across the picture from left to right, 0-179 as OpenCV counts it
across = np.tile(np.linspace(0, 180, size, endpoint=False), (size, 1))

start = time.perf_counter()
while True:
    shift = (time.perf_counter() - start) / secondsPerRound * 180
    hue = ((across + shift) % 180).astype(np.uint8)
    full = np.full((size, size), 255, np.uint8)
    picture = cv2.cvtColor(cv2.merge([hue, full, full]), cv2.COLOR_HSV2BGR)

    preview("Example", picture)
    wled.image(picture, brightness)
    time.sleep(1 / framesPerSecond)
