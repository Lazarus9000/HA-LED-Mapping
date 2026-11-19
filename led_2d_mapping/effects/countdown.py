import math
import random
import time
import cv2
import numpy as np
from wledMapper import Mapper, preview

#Three red flashes (time to get from the keypad to where the LEDs can be seen), then a
#countdown 3-2-1 with big numbers that shrink, then an explosion. The countdown and
#explosion repeat until stopped; the flashes only come at the start
#Add --preview to see the picture in a window

#The picture is small. OpenCV's extra worker threads do not make it faster here,
#they only keep the computer busy
cv2.setNumThreads(1)

#WLED address, calibration and number of LEDs come from the add-on, see wledMapper.py
wled = Mapper.fromSettings()

#Change these to taste
size = 400               #everything is drawn in a size x size picture that is mapped onto the LEDs
#Red flashes at the start
flashes = 3
flashOnSeconds = 0.75
flashOffSeconds = 0.75
lastFlashFadeSeconds = 1.5   #the last flash fades to black over this time
flashColor = (0, 0, 128)     #red at half brightness (blue, green, red)
framesPerSecond = 25
secondsPerNumber = 1.0
startScale = 1.05        #number height at the start, compared to the picture
endScale = 0.5           #number height when the number ends. Much smaller is too few LEDs to read
#Color of each number (blue, green, red, as OpenCV wants it)
numberColors = {3: (60, 60, 255), 2: (0, 190, 255), 1: (80, 255, 80)}
explosionSeconds = 3.0
particles = 70
pauseSeconds = 1.5       #dark pause before the countdown starts again
blur = 6                 #softens the picture so the LEDs do not flicker on edges

font = cv2.FONT_HERSHEY_DUPLEX


def drawNumber(image, number, progress):
    """Draw a number in the middle; progress 0 = start (large), 1 = end (small)."""
    #Ease out: shrinks fast at first, then slows down, so the number stays readable
    shrink = 1 - (1 - progress) ** 2
    height = size * (startScale + (endScale - startScale) * shrink)
    text = str(number)
    #Font scale 1 is about 22 pixels high for this font
    scale = height / 22
    thickness = max(2, int(height / 7))
    (w, h), _ = cv2.getTextSize(text, font, scale, thickness)
    x = int(size / 2 - w / 2)
    y = int(size / 2 + h / 2)
    #Fade in during the first tenth, so a new number does not jump in
    fade = min(1.0, progress / 0.1)
    color = tuple(int(c * fade) for c in numberColors.get(number, (255, 255, 255)))
    cv2.putText(image, text, (x, y), font, scale, color, thickness, cv2.LINE_AA)


class Particle:
    def __init__(self):
        angle = random.uniform(0, 2 * math.pi)
        speed = random.uniform(120, 330)
        self.x = size / 2
        self.y = size / 2
        self.vx = math.cos(angle) * speed
        self.vy = math.sin(angle) * speed - 60     #a little upwards, like a firework
        self.life = random.uniform(1.6, explosionSeconds)
        self.radius = random.randint(9, 16)
        #Warm firework colors: red, orange, yellow, and some white
        hue = random.choice([0, 8, 15, 25, 30])
        saturation = random.choice([255, 255, 255, 60])
        b, g, r = cv2.cvtColor(np.uint8([[[hue, saturation, 255]]]), cv2.COLOR_HSV2BGR)[0][0]
        self.color = (int(b), int(g), int(r))

    def draw(self, image, t):
        if t > self.life:
            return
        x = self.x + self.vx * t
        y = self.y + self.vy * t + 0.5 * 160 * t * t   #gravity
        fade = (1 - t / self.life) ** 1.5
        color = tuple(int(c * fade) for c in self.color)
        cv2.circle(image, (int(x), int(y)), self.radius, color, -1)


def drawExplosion(image, t, sparks):
    #White flash that grows fast from the middle and fades within half a second
    if t < 0.5:
        flash = 1 - t / 0.5
        radius = int(40 + t * 700)
        cv2.circle(image, (size // 2, size // 2), radius, tuple(int(255 * flash) for _ in range(3)), -1)
    for spark in sparks:
        spark.draw(image, t)


def drawFlash(image, t):
    """The red flashes at the start; t = seconds since the effect started."""
    period = flashOnSeconds + flashOffSeconds
    flash = int(t / period)
    inFlash = t - flash * period
    if flash < flashes - 1:
        level = 1.0 if inFlash < flashOnSeconds else 0.0
    else:
        #The last flash: on, then fading to black
        inFlash = t - (flashes - 1) * period
        if inFlash < flashOnSeconds:
            level = 1.0
        else:
            level = max(0.0, 1 - (inFlash - flashOnSeconds) / lastFlashFadeSeconds)
    image[:] = tuple(int(c * level) for c in flashColor)


def show(image):
    img2 = cv2.GaussianBlur(image, (0, 0), blur, cv2.BORDER_DEFAULT)
    preview("countdown", img2)
    wled.image(img2, 100)


frameTime = 1 / framesPerSecond
countdownSeconds = 3 * secondsPerNumber
cycleSeconds = countdownSeconds + explosionSeconds + pauseSeconds

introSeconds = (flashes - 1) * (flashOnSeconds + flashOffSeconds) + flashOnSeconds + lastFlashFadeSeconds

#Red flashes first
introStart = time.perf_counter()
nextFrame = introStart
while time.perf_counter() - introStart < introSeconds:
    image = np.zeros((size, size, 3), dtype='uint8')
    drawFlash(image, time.perf_counter() - introStart)
    #No blur: the whole picture is one color
    preview("countdown", image)
    wled.image(image, 100)

    nextFrame = nextFrame + frameTime
    pause = nextFrame - time.perf_counter()
    if pause > 0:
        time.sleep(pause)
    else:
        nextFrame = time.perf_counter()

#Then countdown and explosion, over and over
cycleStart = time.perf_counter()
nextFrame = cycleStart
sparks = None
while True:
    t = time.perf_counter() - cycleStart
    if t >= cycleSeconds:
        cycleStart += cycleSeconds
        t -= cycleSeconds
        sparks = None

    image = np.zeros((size, size, 3), dtype='uint8')
    if t < countdownSeconds:
        number = 3 - int(t / secondsPerNumber)
        drawNumber(image, number, (t % secondsPerNumber) / secondsPerNumber)
    elif t < countdownSeconds + explosionSeconds:
        if sparks is None:
            sparks = [Particle() for _ in range(particles)]
        drawExplosion(image, t - countdownSeconds, sparks)
    show(image)

    #Wait until it is time for the next picture
    nextFrame = nextFrame + frameTime
    pause = nextFrame - time.perf_counter()
    if pause > 0:
        time.sleep(pause)
    else:
        nextFrame = time.perf_counter()
