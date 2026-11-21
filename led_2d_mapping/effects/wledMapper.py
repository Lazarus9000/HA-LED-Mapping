"""Helper library for the effects: maps a picture onto the LEDs and sends it to WLED.

An effect normally only needs two lines:

    from wledMapper import Mapper
    wled = Mapper.fromSettings()
    ...
    wled.image(picture, brightness)

Mapper.fromSettings() takes the WLED address and the calibration from the add-on
(environment variables WLED_IP, WLED_PORT and CALIBRATION_PATH), and the number of
LEDs from the calibration. Outside the add-on it falls back to 192.168.1.172 and a
calibration.txt next to the script, so the effects can still be run on a laptop.

When the effect stops - stopped from the add-on, Ctrl+C or a crash - the LEDs are
handed back to WLED, so the chain does not stay frozen on the last picture.
"""
import atexit
import os
import signal
import socket
import sys
import time
import numpy as np
import requests

#Preview window
#Start an effect with --preview to see the picture that is sent to the LEDs in a window
#on the computer. Without it no window is opened, which is what you want on a server
previewEnabled = "--preview" in sys.argv and "--no-preview" not in sys.argv

#Show img in a window called name, but only when the script was started with --preview
def preview(name, img):
    if previewEnabled:
        import cv2
        cv2.imshow(name, img)
        cv2.waitKey(1)

#The command line arguments of the script, without the preview switches
def arguments():
    return [a for a in sys.argv[1:] if a not in ("--preview", "--no-preview")]

#Settings used when an effect is not started by the add-on
DEFAULT_IP = "192.168.1.172"
DEFAULT_PORT = 21324
DEFAULT_CALIBRATION = "calibration.txt"


#Number of points in a calibration file
def countLeds(filename):
    with open(filename, 'r') as f:
        return sum(1 for line in f if line.strip())


#Overall brightness set from Home Assistant (0-255), on top of the effect's own brightness.
#The add-on writes it to the file named in LED_BRIGHTNESS_FILE; it is read again at most
#twice a second, so a change shows without restarting the effect
class _MasterBrightness:
    def __init__(self, path):
        self.path = path
        self.value = 255
        self.checked = 0.0
        self.mtime = None

    def get(self):
        if not self.path:
            return 255
        now = time.monotonic()
        if now - self.checked >= 0.5:
            self.checked = now
            try:
                mtime = os.path.getmtime(self.path)
                if mtime != self.mtime:
                    self.mtime = mtime
                    with open(self.path) as f:
                        self.value = max(0, min(255, int(float(f.read().strip()))))
            except (OSError, ValueError):
                pass
        return self.value

masterBrightness = _MasterBrightness(os.environ.get("LED_BRIGHTNESS_FILE"))


#Turn SIGTERM (what the add-on sends to stop an effect) into a normal exit, so the
#LEDs are handed back to WLED on the way out
def _exitOnTerminate(signum, frame):
    sys.exit(0)


class Mapper:

    numberOfLeds = 200
    ip = DEFAULT_IP

    #https://kno.wled.ge/interfaces/udp-realtime/
    protocol = "JSON"

    #Valid values: "JSON", "UDP"
    #Not sure JSON is able to handle more than 250 individual leds

    #UDP implementation based on https://github.com/RolandDaum/WLED-UDP-Realtime-Controll-Python-JavaScript/blob/master/WLEDUDP.py
    WLED_PORT = DEFAULT_PORT
    sock = None
    udpProtocol = 2
    #2 = DRGB - max leds 490, longer chains are sent as several DNRGB packets
    udpTimeout = 1

    white = [255,255,255]
    black = [0,0,0]

    def __init__(self, newip, Leds, newprotocol, calibrationfile):
        #Per Mapper, so two Mappers do not share (and double) each other's points
        self.x = []
        self.y = []
        self.normx = []
        self.normy = []
        #Which pixel of the picture each LED looks at, worked out on the first call to image()
        self.pixelKey = None
        self.pixelRows = None
        self.pixelCols = None
        self.released = False

        if calibrationfile != "":
            tx,ty = self.read_arrays_from_file(calibrationfile)
            self.x.extend(tx)
            self.y.extend(ty)
            self.normx.extend(self.normalize(self.x, 0, 1))
            self.normy.extend(self.normalize(self.y, 0, 1))

        self.ip = newip
        self.numberOfLeds = Leds
        self.protocol = newprotocol

        if self.protocol in ("UDP", "DDP"):
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        #Hand the LEDs back to WLED whenever the effect ends
        atexit.register(self.release)
        try:
            if signal.getsignal(signal.SIGTERM) == signal.SIG_DFL:
                signal.signal(signal.SIGTERM, _exitOnTerminate)
        except (ValueError, AttributeError):
            #Not the main thread, or no SIGTERM on this system
            pass

    #Create a Mapper from the add-on settings. Each value can be overridden
    @classmethod
    def fromSettings(cls, ip=None, port=None, calibration=None, leds=None):
        ip = ip or os.environ.get("WLED_IP") or DEFAULT_IP
        port = int(port or os.environ.get("WLED_PORT") or DEFAULT_PORT)
        calibration = calibration or os.environ.get("CALIBRATION_PATH") or DEFAULT_CALIBRATION
        if not os.path.exists(calibration):
            sys.exit("No calibration found at %s - upload a calibration.txt first" % calibration)
        available = countLeds(calibration)
        if available == 0:
            sys.exit("The calibration %s is empty" % calibration)
        leds = int(leds or available)
        if leds > available:
            sys.exit("The calibration %s has %d points, fewer than the %d LEDs asked for"
                     % (calibration, available, leds))
        #DDP: WLED draws once per picture, after all its data has arrived. With the two
        #packets of the UDP protocol it draws after each one, which gave flicker
        mapper = cls(ip, leds, "DDP", calibration)
        mapper.WLED_PORT = port
        print("Sending %d LEDs to WLED at %s (DDP)" % (leds, ip), flush=True)
        return mapper

    #Utility function to normalize array
    def normalize(self, arr, t_min, t_max):
        norm_arr = []
        diff = t_max - t_min
        low = min(arr)
        diff_arr = max(arr) - low
        for i in arr:
            #All points on one line (e.g. a single LED): put them in the middle
            temp = (((i - low)*diff)/diff_arr) + t_min if diff_arr else (t_min + t_max) / 2
            norm_arr.append(temp)
        return norm_arr

    #Utility function to write calibration data
    def write_arrays_to_file(self, list1, list2, filename):
      with open(filename, 'w') as f:
        for i in range(len(list1)):
          f.write(str(list1[i]) + ',' + str(list2[i]) + '\n')

    #Utility function to read calibration data
    def read_arrays_from_file(self, filename):
      list1 = []
      list2 = []

      with open(filename, 'r') as f:
        for line in f:
          elements = line.strip().split(',')
          if len(elements) < 2:
            #empty line, e.g. at the end of the file
            continue
          #round, so a calibration with decimals works too
          list1.append(int(round(float(elements[0]))))
          list2.append(int(round(float(elements[1]))))

      return list1, list2

    def allColor(self, color, count = 0):
        self.colors([color] * count)
        return

    def oneLed(self, color, index = 0,  count = 0):
        radiances = [self.black] * count
        if 0 <= index < count:
            radiances[index] = color
        self.colors(radiances)
        return

    #Hand control back to WLED. The realtime packets sent by colors() never time out,
    #so without this the chain stays frozen until WLED is restarted or gets new data
    def release(self):
        if self.released:
            return
        self.released = True
        if self.protocol in ("UDP", "DDP"):
            try:
                if self.protocol == "DDP":
                    self.sendDdp([self.black] * self.numberOfLeds)
                else:
                    self.sendUdp([self.black] * self.numberOfLeds, 1)
            except OSError:
                pass
            #WLED ignores a shorter timeout once "no timeout" has been set, so ask it
            #to leave realtime mode through the JSON API as well
            try:
                requests.post('http://' + self.ip + '/json/state', json={"live": False}, timeout=5)
            except requests.RequestException:
                print("Could not reach WLED to end realtime mode - restart it or send {\"live\":false} to /json/state", flush=True)

    def colors(self, colors):
        master = masterBrightness.get()
        if master < 255:
            colors = (np.asarray(colors, dtype=np.float32).reshape(-1, 3) * (master / 255)).astype(np.uint8)

        if self.protocol == "JSON":
            r = requests.post('http://' + self.ip + '/json/state', json={"seg":{"i":np.asarray(colors).tolist()}})

        if self.protocol == "UDP":
            self.sendUdp(colors, 255)

        if self.protocol == "DDP":
            self.sendDdp(colors)

    #Send colors (BGR order, as in OpenCV images) as WLED realtime UDP
    #Up to 490 LEDs fit in one DRGB packet. Longer chains are split over several DNRGB
    #packets, which carry the index of their first LED and hold 489 LEDs each
    #Send colors (BGR order, as in OpenCV images) with DDP, http://www.3waylabs.com/ddp/
    #Up to 480 LEDs per packet. Only the last packet of a picture has the "push" flag, and
    #WLED draws the LEDs when that one arrives, so once per picture
    DDP_PORT = 4048
    ddpSequence = 0

    def sendDdp(self, colors):
        rgb = np.asarray(colors, dtype=np.uint8).reshape(-1, 3)[:, ::-1].tobytes()
        self.ddpSequence = self.ddpSequence % 15 + 1
        for offset in range(0, len(rgb), 1440):
            chunk = rgb[offset:offset + 1440]
            last = offset + 1440 >= len(rgb)
            #flags (version 1, push on the last packet), sequence, data type (8 bit RGB),
            #destination (the display), byte offset, data length
            header = bytes([0x40 | (0x01 if last else 0x00), self.ddpSequence, 0x0B, 0x01])                 + offset.to_bytes(4, "big") + len(chunk).to_bytes(2, "big")
            self.sock.sendto(header + chunk, (self.ip, self.DDP_PORT))

    def sendUdp(self, colors, timeout):
        #One row per LED, turned from blue-green-red into the red-green-blue WLED expects
        rgb = np.asarray(colors, dtype=np.uint8).reshape(-1, 3)[:, ::-1]
        if len(rgb) <= 490:
            self.sock.sendto(bytes([self.udpProtocol, timeout]) + rgb.tobytes(), (self.ip, self.WLED_PORT))
            return
        for start in range(0, len(rgb), 489):
            data = bytes([4, timeout, start >> 8, start & 255]) + rgb[start:start + 489].tobytes()
            self.sock.sendto(data, (self.ip, self.WLED_PORT))

    def image(self, img, brightness):
        #Which pixel each LED looks at only depends on the size of the picture, so it is
        #worked out once and reused. All LEDs are then read from the picture in one step,
        #which is much lighter on the computer than handling them one at a time
        key = (img.shape[0], img.shape[1], self.numberOfLeds)
        if self.pixelKey != key:
            count = self.numberOfLeds
            if len(self.normx) < count:
                raise ValueError("the calibration has %d points, but there are %d LEDs" % (len(self.normx), count))
            self.pixelRows = np.clip((np.array(self.normy[:count]) * img.shape[0]).astype(int), 0, img.shape[0] - 1)
            self.pixelCols = np.clip((np.array(self.normx[:count]) * img.shape[1]).astype(int), 0, img.shape[1] - 1)
            self.pixelKey = key

        #map from image to leds
        pixels = img[self.pixelRows, self.pixelCols]
        if brightness != 100:
            pixels = np.clip(pixels * (brightness/100), 0, 255).astype(np.uint8)

        self.colors(pixels)
