import time
import cv2
import numpy as np
from wledMapper import Mapper, preview, arguments

#Rotates a picture slowly across the LEDs
#Run it by hand with a picture of your own, e.g. "python rotator.py image2.jpg"

#https://stackoverflow.com/a/32929315
def rotate(image, angle, center = None, scale = 1.0):
    (h, w) = image.shape[:2]

    if center is None:
        center = (w / 2, h / 2)

    # Perform the rotation
    M = cv2.getRotationMatrix2D(center, angle, scale)
    rotated = cv2.warpAffine(image, M, (w, h))

    return rotated

imagepath = "gradient.jpg"
degreesPerSecond = 3     #how fast the picture turns
framesPerSecond = 25     #how often the LEDs are updated. More is smoother but costs more computer power

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

start = time.perf_counter()
nextFrame = start
frameTime = 1 / framesPerSecond
while True:
	#The angle follows the clock, so the speed is the same on a fast and a slow computer
	angle = (time.perf_counter() - start) * degreesPerSecond
	img3 = rotate(img2, angle, scale = 2)
	wled.image(img3,25)
	preview("Background", img3)

	#Wait until it is time for the next picture
	nextFrame = nextFrame + frameTime
	pause = nextFrame - time.perf_counter()
	if pause > 0:
		time.sleep(pause)
	else:
		nextFrame = time.perf_counter()
