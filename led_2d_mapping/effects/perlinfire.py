import time
import cv2
import numpy as np
from wledMapper import Mapper, preview

#Fire made from moving Perlin noise
#Uses only numpy and OpenCV, so it runs in the add-on without extra packages

#WLED address, calibration and number of LEDs come from the add-on, see wledMapper.py
wled = Mapper.fromSettings()

framesPerSecond = 20
size = 100           #the fire is drawn in a size x size picture that is mapped onto the LEDs

# Define the minimum and maximum hue values for the gradient (0-1, 0.16 is yellow)
hue_min = 0.0
hue_max = 0.16
gradient_ratio = 0.7


# Perlin noise in 3D, for a whole grid of points at once
# (the "improved noise" of Ken Perlin, https://mrl.cs.nyu.edu/~perlin/noise/)
_perm = np.random.default_rng(1).permutation(256)
_perm = np.concatenate([_perm, _perm])

def _fade(t):
    return t * t * t * (t * (t * 6 - 15) + 10)

def _grad(h, x, y, z):
    h = h & 15
    u = np.where(h < 8, x, y)
    v = np.where(h < 4, y, np.where((h == 12) | (h == 14), x, z))
    return np.where(h & 1, -u, u) + np.where(h & 2, -v, v)

def pnoise3(x, y, z):
    xi = np.floor(x).astype(int) & 255
    yi = np.floor(y).astype(int) & 255
    zi = int(np.floor(z)) & 255
    x = x - np.floor(x)
    y = y - np.floor(y)
    z = z - np.floor(z)
    u, v, w = _fade(x), _fade(y), _fade(z)
    p = _perm
    a = p[xi] + yi
    aa, ab = p[a] + zi, p[a + 1] + zi
    b = p[xi + 1] + yi
    ba, bb = p[b] + zi, p[b + 1] + zi
    lerp = lambda t, a1, a2: a1 + t * (a2 - a1)
    return lerp(w,
                lerp(v, lerp(u, _grad(p[aa], x, y, z), _grad(p[ba], x - 1, y, z)),
                        lerp(u, _grad(p[ab], x, y - 1, z), _grad(p[bb], x - 1, y - 1, z))),
                lerp(v, lerp(u, _grad(p[aa + 1], x, y, z - 1), _grad(p[ba + 1], x - 1, y, z - 1)),
                        lerp(u, _grad(p[ab + 1], x, y - 1, z - 1), _grad(p[bb + 1], x - 1, y - 1, z - 1))))


# Create the x and y coordinate arrays using the NumPy meshgrid function
x, y = np.meshgrid(np.arange(size, dtype=float), np.arange(size, dtype=float))


def update(frame):
    # Generate new noise values; the noise moves upwards and changes over time
    noise_values = pnoise3(x / 30, y / 30 + frame * 2, frame)

    # Adjust the range of the noise values to the range 0-1
    spread = np.max(noise_values) - np.min(noise_values)
    noise_values = (noise_values - np.min(noise_values)) / (spread if spread else 1)

    # Use the y coordinate of each pixel as the hue value, mixed with the noise
    hue = (y / size) * gradient_ratio + noise_values * (1 - gradient_ratio)
    # Scale and shift the hue values using the hue_min and hue_max variables
    hue = hue * (hue_max - hue_min) + hue_min

    # Make the saturation and value relative to the hue value
    saturation_ratio = 0.1
    saturation = 1 - (saturation_ratio + (hue / hue_max) * (1 - saturation_ratio))
    value_ratio = 0.3
    value = value_ratio + hue / hue_max * (1 - value_ratio)

    # Convert to an OpenCV picture (blue, green, red, 0-255). OpenCV wants hue in degrees
    hsv = np.dstack((hue * 360, saturation, value)).astype(np.float32)
    image = (cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR) * 255).astype(np.uint8)

    preview("blur", image)
    wled.image(image, 100)


frame = 0.0
nextFrame = time.perf_counter()
while True:
    update(frame)
    frame += 0.1

    #Wait until it is time for the next picture
    nextFrame = nextFrame + 1 / framesPerSecond
    pause = nextFrame - time.perf_counter()
    if pause > 0:
        time.sleep(pause)
    else:
        nextFrame = time.perf_counter()
