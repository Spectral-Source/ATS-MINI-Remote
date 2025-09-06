import os
import time
import threading
import argparse
from flask import Flask, jsonify, request, render_template
import serial
import serial.tools.list_ports

# === Config ===
DEFAULT_BAUD = 112500
FALLBACK_BAUD = 9600
SERIAL_TIMEOUT = 0.1

app = Flask(__name__)

# Globals
ser = None
serial_lock = threading.Lock()
data_lock = threading.Lock()

latest_raw_line = ""
monitor_parsed = {}
monitor_active = False
monitor_requested = False

def find_serial_port(preferred=None):
    """Try to auto-detect ATS-MINI USB serial port if port not given."""
    if preferred:
        return preferred
    ports = list(serial.tools.list_ports.comports())
    if not ports:
        return None
    # prefer ports with common USB/USB-serial identifiers
    keywords = ["USB", "CP210", "CH340", "FTDI", "UART", "CDC", "Silicon", "SiLabs"]
    for p in ports:
        desc = (p.description or "") + " " + (p.manufacturer or "")
        if any(k.upper() in desc.upper() for k in keywords):
            return p.device
    # fallback to first port
    return ports[0].device

def open_serial(port=None, baud=DEFAULT_BAUD):
    global ser
    if ser:
        try:
            ser.close()
        except Exception:
            pass
    if port is None:
        port = find_serial_port()
    if port is None:
        raise RuntimeError("No serial ports found. Set ATS_PORT environment or pass --port.")
    # try primary baud and fallback
    try:
        s = serial.Serial(port, baud, timeout=SERIAL_TIMEOUT)
        print(f"[+] Opened serial {port} @ {baud}")
        ser = s
        return ser
    except Exception as e:
        print(f"[!] Failed open {port}@{baud}: {e}")
        if baud == DEFAULT_BAUD:
            try:
                s = serial.Serial(port, FALLBACK_BAUD, timeout=SERIAL_TIMEOUT)
                print(f"[+] Opened serial {port} @ {FALLBACK_BAUD}")
                ser = s
                return ser
            except Exception as e2:
                raise RuntimeError(f"Failed to open serial on {port}: {e2}")
        raise

def send_serial_raw(cmd, wait_lines=1, read_timeout=0.1):
    """
    Send raw cmd string to serial (no extra processing). Returns list of response lines.
    cmd: string (we append newline)
    """
    global ser
    if ser is None:
        raise RuntimeError("Serial port not opened")
    with serial_lock:
        ser.reset_input_buffer()
        if not cmd.endswith("\n"):
            cmd_out = cmd + "\n"
        else:
            cmd_out = cmd
        ser.write(cmd_out.encode())
        ser.flush()
        t0 = time.time()
        lines = []
        while time.time() - t0 < read_timeout:
            try:
                raw = ser.readline()
                if not raw:
                    time.sleep(0.01)
                    continue
                try:
                    line = raw.decode(errors="ignore").strip()
                except:
                    line = repr(raw)
                if line:
                    lines.append(line)
                    if len(lines) >= wait_lines:
                        break
            except Exception:
                break
        return lines

def format_frequency(freq_khz, bfo_hz, mode):
    try:
        if mode is None:
            mode = ""
        mm = mode.upper()
        if mm in ("USB", "LSB", "SSB"):
            freq_hz = int(freq_khz) * 1000 + int(bfo_hz)
            mhz = freq_hz / 1_000_000.0
            return f"{mhz:.6f} MHz"
        else:
            mhz = int(freq_khz) / 1000.0
            if mm == "FM":
                return f"{mhz:.2f} MHz"
            elif mm == "AM":
                if int(freq_khz) < 1000:
                    return f"{int(freq_khz)} kHz"
                else:
                    return f"{mhz:.3f} MHz"
            else:
                return f"{mhz:.3f} MHz"
    except Exception:
        return f"{freq_khz} (raw)"

def parse_monitor_line(line):
    parts = [p.strip() for p in line.split(",")]
    out = {}
    try:
        out["raw_parts_count"] = len(parts)
        out["fw_version"] = parts[0] if len(parts) > 0 else ""
        freq_khz = int(parts[1]) if len(parts) > 1 and parts[1] != "" else 0
        bfo_hz = int(parts[2]) if len(parts) > 2 and parts[2] != "" else 0
        out["currentFrequency_raw"] = freq_khz
        out["currentBFO"] = bfo_hz
        out["bandCal"] = parts[3] if len(parts) > 3 else ""
        out["bandName"] = parts[4] if len(parts) > 4 else ""
        out["mode"] = parts[5] if len(parts) > 5 else ""
        out["stepIdx"] = parts[6] if len(parts) > 6 else ""
        out["bandwidthIdx"] = parts[7] if len(parts) > 7 else ""
        out["agcIdx"] = parts[8] if len(parts) > 8 else ""
        out["volume"] = int(parts[9]) if len(parts) > 9 and parts[9] != "" else None
        out["rssi_raw"] = int(parts[10]) if len(parts) > 10 and parts[10] != "" else None
        out["snr_raw"] = int(parts[11]) if len(parts) > 11 and parts[11] != "" else None
        out["tuningCapacitor"] = parts[12] if len(parts) > 12 else ""
        if len(parts) > 13 and parts[13] != "":
            try:
                v_val = float(parts[13])
                out["voltage"] = round(v_val * 1.702 / 1000.0, 3)
            except:
                out["voltage"] = parts[13]
        else:
            out["voltage"] = None
        out["seqnum"] = parts[14] if len(parts) > 14 else ""
        out["frequency"] = format_frequency(freq_khz, bfo_hz, out["mode"])
        out["rssi"] = f"{out['rssi_raw']} dBuV" if out.get("rssi_raw") is not None else ""
        out["snr"] = f"{out['snr_raw']} dB" if out.get("snr_raw") is not None else ""
    except Exception as e:
        out["parse_error"] = str(e)
    return out

def monitor_reader_thread():
    global latest_raw_line, monitor_parsed, monitor_active
    while True:
        if ser is None:
            time.sleep(0.5)
            continue
        try:
            with serial_lock:
                raw = ser.readline()
            if not raw:
                continue
            try:
                line = raw.decode(errors="ignore").strip()
            except:
                line = repr(raw)
            if not line:
                continue
            latest_raw_line = line
            with data_lock:
                if "," in line:
                    parsed = parse_monitor_line(line)
                    if parsed and (parsed.get("currentFrequency_raw") or parsed.get("fw_version")):
                        monitor_parsed = parsed
                        monitor_active = True
                    else:
                        pass
        except Exception as e:
            print("[monitor_reader] error:", e)
            time.sleep(0.01)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/command", methods=["POST"])
def api_command():
    data = request.get_json(force=True)
    cmd = data.get("cmd", "")
    cmd = cmd.replace("\r", "").replace("\n", "")
    try:
        lines = send_serial_raw(cmd, wait_lines=4, read_timeout=0.1)
        return jsonify({"ok": True, "sent": cmd, "response_lines": lines})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/start_monitor", methods=["POST"])
def api_start_monitor():
    global monitor_requested
    try:
        send_serial_raw("t", wait_lines=1, read_timeout=0.1)
        monitor_requested = True
        return jsonify({"ok": True, "msg": "toggle-sent"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/stop_monitor", methods=["POST"])
def api_stop_monitor():
    global monitor_requested
    try:
        send_serial_raw("t", wait_lines=1, read_timeout=0.1)
        monitor_requested = False
        return jsonify({"ok": True, "msg": "toggle-sent"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/monitor", methods=["GET"])
def api_monitor():
    with data_lock:
        data_to_return = {
            "parsed": monitor_parsed,
            "raw": latest_raw_line,
            "monitor_active": monitor_active,
            "monitor_requested": monitor_requested
        }
    return jsonify(data_to_return)

@app.route("/api/memory_slots", methods=["GET"])
def api_memory_slots():
    try:
        lines = send_serial_raw("$", wait_lines=30, read_timeout=0.4)
        return jsonify({"ok": True, "lines": lines})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/screenshot", methods=["GET"])
def api_screenshot():
    try:
        send_serial_raw("C", wait_lines=1, read_timeout=0.05)
        hex_chunks = []
        t0 = time.time()
        while time.time() - t0 < 5.0:
            raw = ser.readline()
            if not raw:
                time.sleep(0.02)
                continue
            try:
                line = raw.decode(errors="ignore").strip()
            except:
                line = repr(raw)
            if not line:
                continue
            hex_chunks.append(line)
            if len("".join(hex_chunks)) > 10_000_000:
                break
        return jsonify({"ok": True, "hex": "\n".join(hex_chunks)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

def start_monitor_thread():
    t = threading.Thread(target=monitor_reader_thread, daemon=True)
    t.start()
    return t

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", help="Serial device path (COMx or /dev/ttyUSBx)", default=os.environ.get("ATS_PORT"))
    parser.add_argument("--baud", type=int, default=int(os.environ.get("ATS_BAUD") or DEFAULT_BAUD))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--http-port", type=int, default=5000)
    args = parser.parse_args()
    try:
        open_serial(args.port, args.baud)
    except Exception as e:
        print("[!] Could not open serial:", e)
        print("[!] Please set ATS_PORT environment variable or pass --port. Exiting.")
        return
    start_monitor_thread()
    print("[*] Monitor reader started. You can open web UI.")
    app.run(host=args.host, port=args.http_port, debug=False)

if __name__ == "__main__":
    main()