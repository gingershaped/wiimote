import bluetooth
import logging
import threading
import atexit
import warnings
import os, os.path
from sys import stdout
from enum import Enum
from time import sleep, monotonic
from collections import namedtuple
from copy import deepcopy
from . import errors

logging.basicConfig(format="[%(name)s] %(levelname)s: %(message)s", stream=stdout, level=logging.INFO)

Buttons = namedtuple(
  "Buttons",
  [
    "dLeft",
    "dRight",
    "dDown",
    "dUp",
    "plus",
    "two",
    "one",
    "b",
    "a",
    "minus",
    "home"
  ]
)
FunctionResult = namedtuple(
    "FunctionResult",
    [
        "report",
        "result"
    ]
)
class SpeakerConfig:
    def __init__(self, sampleRate, fmt, volume):
        self.sampleRate = sampleRate
        self.fmt = fmt
        self.volume = volume
    def dump(self):
        return (
              b"\x00"
            + self.fmt.value.to_bytes(1, "little")
            + int(12000000 / self.sampleRate).to_bytes(2, "little")
            + int(self.volume * (255 if self.fmt == SpeakerFormat.PCM else 64)).to_bytes(1, "little")
            + b"\x00"
        )

class ReportMode(Enum):
  BUTTONS = 0x30
  BUTTONSACCEL = 0x31
  BUTTONSEXTENSION8 = 0x32
  BUTTONSACCELIR = 0x33
  BUTTONSEXTENSION19 = 0x34
  BUTTONSACCELEXTENSION16 = 0x35
  BUTTONSACCELIR10EXTENSION9 = 0x36
  BUTTONSACCELIR10EXTENSION6 = 0x37
  EXTENSION21 = 0x3d
  # We're just not going to use the interleaved mode for now :p
class RWTarget(Enum):
    EEPROM = 0x00
    CTRLREG = 0x04
class SpeakerFormat(Enum):
    PCM = 0x40
    ADPCM = 0x00
class Result(Enum):
    STATUS = 0
    

class Wiimote:
    def __init__(self, doReady = True, receiveTimeout = 10, accelCalibration = "default"):
        self.logger = logging.getLogger("Wiimote")
        self.receiveTimeout = receiveTimeout
        self.alive = False
        self.buttons = None
        self.accelerometer = None
        self.accelerometerRaw = None
        self.accelerometerCalibration = [[0, 1], [0, 1], [0, 1]]
        self._rumble = False
        self._continuous = False
        self._reportMode = None
        self._waitingFor = None
        self._result = None
        self._readData = b""
        self._targetReadSize = 0
        self._speakerConfig = None
        self._accelData = None

        self.status = {
            "lowBattery": False,
            "extension": False,
            "speaker": False,
            "ir": False
        }
        self._buttons = None
        
        devices = bluetooth.discover_devices(duration=3)
        deviceID = None
        for device in devices:
            for match in bluetooth.find_service(address = device):
                if match["provider"] == "Nintendo":
                    deviceID = device
        if not deviceID:
            raise ConnectionError("No device")
        
        self.logger.info("Found device: " + deviceID)
        self.device = bluetooth.BluetoothSocket(bluetooth.L2CAP)
        self.device.connect((deviceID, 0x13))
        self.logger.debug("Connected, inititalizing...")
        self.send(b"\x11", b"\x00")
        self.reportMode = ReportMode.BUTTONS
        self.loadAccelCalibration(accelCalibration)
        self.device.setblocking(False)
        self.logger.debug("Starting thread...")
        thread = threading.Thread(target = self._run)
        thread.start()
        if doReady:
          self.leds = 0b1111
          self.rumble = True
          sleep(1)
          self.rumble = False
        atexit.register(self.stop)

    def __del__(self):
        self.stop()
        
    def stop(self):
        if self.alive:
            self.alive = False
            self.logger.debug("Stopping thread...")
    def _run(self):
        if self.alive:
            self.logger.error("Thread already running!")
            return
        self.alive = True
        self.logger.debug("Thread started!")
        while self.alive:
            try:
                data = self.device.recv(1024)
            except:
                pass
            else:
                if not (data.startswith(b"\xa1") or data.startswith(b"\x00")):
                    raise Exception("Bad packet")
                self.process(data)
        self.logger.debug("Thread stopped")
        self.device.close()

    def process(self, data):
        report = data[1]
        if report != 0x3d:
            self.processButtons(data[2], data[3])
        if report == 0x20:
            self.logger.debug("Received status update")
            if self._waitingFor == Result.STATUS:
                self._result = True
            self.updateStatus(data)
        elif report == 0x22:
            self.logger.debug("Received confirmation")
            # TODO
        elif report == 0x21:
            self.logger.debug("Recived memory data")
            self.processMemoryData(data)
        elif report >= 0x30 and report <= 0x3f:
            self.logger.debug("Recieved data report")
            self.processReport(report, data)
    def processButtons(self, a, b):
        self._buttons = Buttons(
            dLeft  = bool(a & 0x01),
            dRight = bool(a & 0x02),
            dDown  = bool(a & 0x04),
            dUp    = bool(a & 0x08),
            plus   = bool(a & 0x10),
            two    = bool(b & 0x01),
            one    = bool(b & 0x02),
            b      = bool(b & 0x04),
            a      = bool(b & 0x08),
            minus  = bool(b & 0x10),
            home   = bool(b & 0x80)
          )
    def processReport(self, report, data):
        report = ReportMode(report)
        if report != ReportMode.EXTENSION21:
            buttons = data[:2]
            self.buttons = Buttons(
                dLeft = buttons[0] & 0x01,
                dRight = buttons[0] & 0x02,
                dDown = buttons[0] & 0x04,
                dUp = buttons[0] & 0x08,
                plus = buttons[0] & 0x10,
                two = buttons[1] & 0x01,
                one = buttons[1] & 0x02,
                b = buttons[1] & 0x04,
                a = buttons[1] & 0x08,
                minus = buttons[1] & 0x10,
                home = buttons[1] & 0x80
            )
        else:
            self.buttons = None
        if report not in (ReportMode.BUTTONS, ReportMode.BUTTONSEXTENSION8, ReportMode.BUTTONSEXTENSION19, ReportMode.EXTENSION21):
            self.processAccel(data[2:5])
        else:
            self.accelerometer = None
            self.accelerometerRaw = None

    def loadAccelCalibration(self, path):
        if not path:
            return
        if path == "default":
            p = os.path.join(os.path.expanduser("~"), ".wiimoteAccelConfig")
        else:
            p = path
        try:
            f = open(p, "r")
        except FileNotFoundError:
            if path == "default":
                warnings.warn("No accelerometer configuration data found! Try running wiimote-accel-config at a command line.", errors.AccelerometerConfigurationWarning)
            else:
                warnings.warn("Invalid accelerometer configuration data path: " + p, errors.AccelerometerConfigurationWarning)
        except:
            warnings.warn("Unable to load accelerometer configuration data!", errors.AccelerometerConfigurationWarning)
        else:
            data = f.read()
            f.close()
            self.accelerometerCalibration = [[float(y) for y in x.split(" ")] for x in data.splitlines()]
    def processAccel(self, data):
        ##xraw = int.from_bytes(data[0], "little")
        ##yraw = int.from_bytes(data[1], "little")
        ##zraw = int.from_bytes(data[2], "little")
        xraw = data[0]
        yraw = data[1]
        zraw = data[2]
        x0, x3 = self.accelerometerCalibration[0]
        y0, y2 = self.accelerometerCalibration[1]
        z0, z1 = self.accelerometerCalibration[2]
        x = (xraw - x0) / (x3 - x0)
        y = (yraw - y0) / (y2 - y0)
        z = (zraw - z0) / (z1 - z0)
        self.accelerometer = (x, y, z)
        self.accelerometerRaw = (xraw, yraw, zraw)
    def updateStatus(self, data):
        if bool(data[4] & 0x02) != self.status["extension"]:
            # Extension connected, gotta resend report mode!
            self.reportMode = self.reportMode 
        self.status["lowBattery"] = bool(data[4] & 0x01)
        self.status["extension"] = bool(data[4] & 0x02)
        self.status["speaker"] = bool(data[4] & 0x04)
        self.status["ir"] = bool(data[4] & 0x08)
        self.status["leds"] = data[4] >> 4
    def processMemoryData(self, data):
        size = data[4] >> 4
        error = data[4] & 0xF
        if error == 7:
            self._result = errors.MemoryRWError("Tried to read from a write-only address")
            return
        elif error == 8:
            self._result = errors.MemoryRWError("Tried to read from a nonexistent address")
            return
        elif error != 0:
            self._result = errors.MemoryRWError("Unknown error: " + str(error))
            return
        assert size <= 16, "Size too big: " + str(size)
        d = data[7:size]
        self._readData += d
        print(size, len(self._readData), self._targetReadSize)
        if len(self._readData) == self._targetReadSize:
            self._result = deepcopy(self._readData)
            self._readData = b""
      
    def send(self, report, data):
        self.device.send(b"\xa2" + report + bytes((data[0] | self._rumble,)) + data[1:])
    def awaitResult(self):
        s = monotonic()
        while not self._result:
            if monotonic() - s > self.receiveTimeout:
                raise TimeoutError()
            if issubclass(type(self._result), Exception):
              raise self._result
        res = deepcopy(self._result)
        self._result = None
        self._waitingFor = None
        return res

    def requestStatusUpdate(self, wait=True):
        self.send(b"\x15", b"\x00")
        self._waitingFor = Result.STATUS
        if wait:
            self.awaitResult()
    def read(self, offset, size, target=RWTarget.EEPROM):
        self._targetReadSize = size
        self.send(b"\x17", (0x00 | target.value).to_bytes(1, "big") + offset.to_bytes(3, "big") + (size*2).to_bytes(2, "big"))
        return self.awaitResult()
    def write(self, data, offset, target=RWTarget.EEPROM):
        for c, x in enumerate([(len(data[i:i+16]), data[i:i+16].ljust(16, b"\x00")) for i in range(0, len(data), 16)]):
            self.send(b"\x16", (0x00 | target.value).to_bytes(1, "big") + (offset).to_bytes(3, "big") + x[0].to_bytes(1, "big") + x[1])
            
    def initSpeaker(self, config=SpeakerConfig(sampleRate=2000, fmt=SpeakerFormat.PCM, volume=0.2)):
        self._speakerConfig = config
        self.send(b"\x14", b"\x04")
        self.send(b"\x19", b"\x04")
        self.write(b"\x01", 0xa20009, RWTarget.CTRLREG)
        sleep(0.2)
        self.write(b"\x08", 0xa20001, RWTarget.CTRLREG)
        sleep(0.2)
        self.write(config.dump(), 0xa20001, RWTarget.CTRLREG)
        sleep(0.2)
        self.write(b"\x01", 0xa20008, RWTarget.CTRLREG)
        sleep(0.2)
        self.send(b"\x19", b"\x00")
    def _play(self, data):
        for c, x in enumerate([(len(data[i:i+20]), data[i:i+20].ljust(20, b"\x00")) for i in range(0, len(data), 20)]):
            try:
                self.send(b"\x18", (x[0] << 3).to_bytes(1, "big") + x[1])
            except:
                pass
            sleep((self._speakerConfig.sampleRate * 5e-06))
    def play(self, data, wait=True):
        if not self._speakerConfig:
            raise AttributeError("Speaker not inititalized")
        t = threading.Thread(target = self._play, args=(data,))
        t.start()
        if wait:
            t.join()

    def initExtension(self):
        if not self.status["extension"]:
            raise AttributeError("No extension!")
        self.write(b"\x55", 0xa400f0, RWTarget.CTRLREG)
        self.write(b"\x00", 0xa400fb, RWTarget.CTRLREG)
        sleep(0.2)
        data = self.read(0xa400fa, 16, RWTarget.CTRLREG)
        
        
    @property
    def continuous(self):
        return self._continuous
    @continuous.setter
    def continuous(self, value):
        self._continuous = value
        self.reportMode = self.reportMode
    @property
    def reportMode(self):
        return self._reportMode
    @reportMode.setter
    def reportMode(self, value):
        self._reportMode = value
        self.send(b"\x12", (b"\x04" if self._continuous else b"\x00") + bytes((value.value,)))

    @property
    def leds(self):
        return self.status["leds"]
    @leds.setter
    def leds(self, value):
        self.send(b"\x11", bytes((value << 4,)))
        self.requestStatusUpdate()
    @property
    def rumble(self):
        return self._rumble
    @rumble.setter
    def rumble(self, value):
        self._rumble = bool(value)
        self.send(b"\x10", b"\x00")
