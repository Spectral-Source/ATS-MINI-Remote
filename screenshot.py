import serial
import binascii

# === Settings ===
PORT = "COM3"          # Windows example, change to your COM port (e.g. COM4)
# On Linux/Mac: "/dev/cu.usbmodem14401"
BAUDRATE = 115200      # default speed for ATS-MINI
OUTPUT_FILE = "screenshot.bmp"

# === Open serial port ===
ser = serial.Serial(PORT, BAUDRATE, timeout=5)

# === Send screenshot command ===
ser.write(b"C")   # 'C' tells the ATS-MINI to dump screenshot

# === Read until no more data ===
data = b""
while True:
    chunk = ser.read(4096)
    if not chunk:
        break
    data += chunk

ser.close()

# === Clean and decode HEX ===
# Some devices send line breaks -> strip whitespace
hex_str = data.replace(b"\r", b"").replace(b"\n", b"")

# Convert HEX -> binary
try:
    bmp_data = binascii.unhexlify(hex_str)
except binascii.Error as e:
    print("Error decoding hex:", e)
    exit(1)

# === Save BMP ===
with open(OUTPUT_FILE, "wb") as f:
    f.write(bmp_data)

print(f"Screenshot saved to {OUTPUT_FILE}")
