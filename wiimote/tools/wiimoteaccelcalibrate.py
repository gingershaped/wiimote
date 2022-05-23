import wiimote
import os, os.path

def run():
    print("Welcome to the Wii Remote Accelerometer Calibration Utility. Please press 1 and 2 on your Wii Remote, and then press Enter.")
    input()
    print("Connecting to device...")
    w = wiimote.Wiimote(accelCalibration = None)
    w.reportMode = wiimote.ReportMode.BUTTONSACCEL
    while not w.accelerometerRaw:
        pass
    print("Connected successfully.")
    print("Would you like to do the full or simple calibration? Full is more accurate but takes longer.")
    response = None
    while True:
        d = input("[F]ull or [S]imple? ").lower()
        if d == "f":
            response = "Full"
        elif d == "s":
            response = "Simple"
        else:
            print("Please enter F or S.")
            continue
        break
    print("Beginning", response, "calibration...")
    if response == "Full":
        print("If you have not done so already, please remove the remote's jacket.")
        print("Please place the Wii Remote face-up (A button up) on a flat table and press Enter.")
        input()
        x1, y1, z1 = w.accelerometerRaw
        print("Please place the Wii Remote vertically on a flat table so the IR sensor is down and the expansion port is up, and press Enter.")
        input()
        x2, y2, z2 = w.accelerometerRaw
        print("Please place the Wii Remote on a flat table so the left side (from the perspective of a person holding it) is up, and press Enter.")
        input()
        x3, y3, z3 = w.accelerometerRaw
    else:
        print("todo lol")
    path = input("Please enter a path for the configuration data, or press Enter to use the default: ")
    if path == "":
        path = os.path.join(os.path.expanduser("~"), ".wiimoteAccelConfig")
    print("Thank you. Please wait while your calibration data is created...")
    x0 = (x1 + x2) / 2
    y0 = (y1 + y3) / 2
    z0 = (z2 + z3) / 2
    f = open(path, "w")
    f.write("\n".join([" ".join([str(y) for y in x]) for x in [[x0, x3], [y0, y2], [z0, z1]]]))
    f.close()
    print("Calibration complete.")
