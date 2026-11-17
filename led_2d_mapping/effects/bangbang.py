import time
import cv2
import numpy as np
import random
import colorsys
from wledMapper import Mapper, preview

#The first fireworks: one ring after another, expanding from the middle
#bangbangv3.py has several rockets at once

#WLED address, calibration and number of LEDs come from the add-on, see wledMapper.py
wled = Mapper.fromSettings()

while True:

    hue = random.randint(0, 180)
    (h, s, v) = (hue / 255, 1, 1)
    #convert to RGB
    (r, g, b) = colorsys.hsv_to_rgb(h, s, v)
    #expand RGB range
    (r, g, b) = (int(r * 255), int(g * 255), int(b * 255))

    for radius in range(100):

        image = np.zeros((400, 400, 3), dtype='uint8')

        #Colored circle (OpenCV pictures are blue, green, red)
        if radius > 0:
            cv2.circle(image, (200, 200), radius*5, (b, g, r), 50)

        img2 = cv2.GaussianBlur(image,(0,0),10,cv2.BORDER_DEFAULT)
        preview("blur", img2)
        wled.image(img2, 100)

        time.sleep(0.0105)
