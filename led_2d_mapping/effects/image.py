import time
import cv2
import numpy as np
from wledMapper import Mapper, preview, arguments

#Shows a picture on the LEDs until the effect is stopped
#Run it by hand with a picture of your own, e.g. "python image.py spiral.png"

imagepath = "image2.jpg"

#Read image path from args
#(the --preview switch is handled by wledMapper and is not counted here)
args = arguments()
if len(args) == 1:
    imagepath = args[0]

#WLED address, calibration and number of LEDs come from the add-on, see wledMapper.py
wled = Mapper.fromSettings()

#Load image
inputimg = cv2.imread(imagepath)
if inputimg is None:
    raise SystemExit("Could not read the picture %s" % imagepath)
#Blur to avoid aliasing when projecting onto the lower resolution LEDs
img2 = cv2.GaussianBlur(inputimg,(0,0),10,cv2.BORDER_DEFAULT)

preview("Background", img2)

#Send the picture again every second. WLED keeps showing it in between, and a lost
#packet is repaired within a second
while True:
    wled.image(img2,25)
    time.sleep(1)
