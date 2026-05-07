import os
import time
import threading
import argparse
from flask import Flask, jsonify, request, render_template
from flask_socketio import SocketIO, emit, join_room, leave_room
import serial
import serial.tools.list_ports

# === Config ===
DEFAULT_BAUD = 115200
FALLBACK_BAUD = 9600
SERIAL_TIMEOUT = 0.1

# Band definitions with frequency ranges in kHz
BANDS = {
    "VHF": {"min_khz": 64000, "max_khz": 108000},
    "ALL": {"min_khz": 150, "max_khz": 30000},
    "11M": {"min_khz": 25600, "max_khz": 26100},
    "13M": {"min_khz": 21500, "max_khz": 21900},
    "15M": {"min_khz": 18900, "max_khz": 19100},
    "16M": {"min_khz": 17400, "max_khz": 18100},
    "19M": {"min_khz": 15100, "max_khz": 15900},
    "22M": {"min_khz": 13500, "max_khz": 13900},
    "25M": {"min_khz": 11000, "max_khz": 13000},
    "31M": {"min_khz": 9000, "max_khz": 11000},
    "41M": {"min_khz": 7000, "max_khz": 9000},
    "49M": {"min_khz": 5000, "max_khz": 7000},
    "60M": {"min_khz": 4000, "max_khz": 5100},
    "75M": {"min_khz": 3500, "max_khz": 4000},
    "90M": {"min_khz": 3000, "max_khz": 3500},
    "MW3": {"min_khz": 1700, "max_khz": 3500},
    "MW2": {"min_khz": 495, "max_khz": 1701},
    "MW1": {"min_khz": 150, "max_khz": 1800},
    "160M": {"min_khz": 1800, "max_khz": 2000},
    "80M": {"min_khz": 3500, "max_khz": 4000},
    "40M": {"min_khz": 7000, "max_khz": 7300},
    "30M": {"min_khz": 10000, "max_khz": 10200},
    "20M": {"min_khz": 14000, "max_khz": 14400},
    "17M": {"min_khz": 18000, "max_khz": 18200},
    "15M_SSB": {"min_khz": 21000, "max_khz": 21500},
    "12M": {"min_khz": 24800, "max_khz": 25000},
    "10M": {"min_khz": 28000, "max_khz": 29700},
    "CB": {"min_khz": 25000, "max_khz": 28000},
}

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

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
    cmd: string (appended with \r\n for proper termination)
    """
    global ser
    if ser is None:
        raise RuntimeError("Serial port not opened")
    with serial_lock:
        ser.reset_input_buffer()
        if not cmd.endswith("\r\n"):
            cmd_out = cmd + "\r\n"
        else:
            cmd_out = cmd
        print(f"[SERIAL_TX] Sending: {repr(cmd_out)}")
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
                    print(f"[SERIAL_RX] Received: {repr(line)}")
                    if len(lines) >= wait_lines:
                        break
            except Exception:
                break
        return lines

def format_frequency(freq_khz, bfo_hz, mode, band_name=""):
    try:
        if mode is None:
            mode = ""
        if band_name is None:
            band_name = ""
        mm = mode.upper()
        bb = band_name.upper()
        freq_val = int(freq_khz)
        
        # Check for SSB modes first (require 6 decimals in MHz with MHz.kHz.Hz format)
        if mm in ("USB", "LSB", "SSB"):
            freq_hz = freq_val * 1000 + int(bfo_hz)
            mhz_int = freq_hz // 1_000_000
            khz_part = (freq_hz % 1_000_000) // 1_000
            hz_part = freq_hz % 1_000
            return f"{mhz_int}.{khz_part:03d}.{hz_part:03d} MHz"
        
        # VHF/FM broadcast: device sends at 1/10 scale, so multiply by 10
        if "VHF" in bb or (mm == "FM" and "VHF" in bb):
            mhz = (freq_val * 10) / 1000.0
            return f"{mhz:.2f} MHz"
        
        # Dynamic formatting for bands that can span kHz to MHz range
        # Bands: ALL, MW1, MW2, MW3, FM
        dynamic_bands = ("ALL", "MW", "FM")
        is_dynamic_band = any(band in bb for band in dynamic_bands)
        
        if is_dynamic_band:
            # Below 1000 kHz: show as kHz with 3 decimals (Hz precision)
            if freq_val < 1000:
                return f"{freq_val}.000 kHz"
            else:
                # 1000 kHz and above: show as MHz with MHz.kHz.Hz format
                mhz_int = freq_val // 1000
                khz_part = freq_val % 1000
                hz_part = 0
                return f"{mhz_int}.{khz_part:03d}.{hz_part:03d} MHz"
        
        # Other AM/FM bands in kHz range (11M, 13M, 15M, 16M, 19M, 22M, 25M, 31M, 41M, 49M, 60M, 75M, 90M, CB)
        # Show with MHz.kHz.Hz format if >= 1000, otherwise show as kHz
        if freq_val >= 1000:
            mhz_int = freq_val // 1000
            khz_part = freq_val % 1000
            hz_part = 0
            return f"{mhz_int}.{khz_part:03d}.{hz_part:03d} MHz"
        else:
            return f"{freq_val}.000 kHz"
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
        out["frequency"] = format_frequency(freq_khz, bfo_hz, out["mode"], out["bandName"])
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
                        # Emit data to all connected WebSocket clients in real-time
                        socketio.emit('monitor_update', {
                            "parsed": parsed,
                            "raw": line,
                            "monitor_active": True,
                            "monitor_requested": monitor_requested
                        })
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

@socketio.on('connect')
def handle_connect():
    """Send current state to newly connected client"""
    with data_lock:
        emit('monitor_update', {
            "parsed": monitor_parsed,
            "raw": latest_raw_line,
            "monitor_active": monitor_active,
            "monitor_requested": monitor_requested
        })
    print(f"[WebSocket] Client connected. Total active connections")

@socketio.on('disconnect')
def handle_disconnect():
    print("[WebSocket] Client disconnected")

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

@app.route("/api/set_freq", methods=["POST"])
def api_set_freq():
    global monitor_requested
    data = request.get_json(force=True)
    freq = data.get("frequency")
    
    print(f"[api_set_freq] Received frequency value: {freq} (type: {type(freq).__name__})")
    
    if not freq:
        print("[api_set_freq] Error: No frequency provided")
        return jsonify({"ok": False, "error": "No frequency provided"}), 400
    
    try:
        # Convert to float to handle both MHz and Hz input
        freq_val = float(freq)
        
        # If less than 100000, assume it's MHz and convert to Hz
        if freq_val < 100000:
            freq_hz = int(freq_val * 1_000_000)
            freq_khz = freq_hz / 1000
            print(f"[api_set_freq] Converted {freq_val} MHz to {freq_hz} Hz")
        else:
            # Assume it's already in Hz
            freq_hz = int(freq_val)
            freq_khz = freq_hz / 1000
            print(f"[api_set_freq] Using {freq_hz} Hz (already in Hz)")
        
        # Get current band from monitor data
        current_band = None
        with data_lock:
            current_band = monitor_parsed.get("bandName", "").upper()
        
        print(f"[api_set_freq] Current band: {current_band}")
        
        # Validate frequency against band limits
        if current_band and current_band in BANDS:
            band_info = BANDS[current_band]
            min_khz = band_info["min_khz"]
            max_khz = band_info["max_khz"]
            
            if freq_khz < min_khz or freq_khz > max_khz:
                error_msg = f"Your frequency is outside {current_band} band limits ({min_khz:.0f}-{max_khz:.0f} kHz)"
                print(f"[api_set_freq] {error_msg}")
                return jsonify({"ok": False, "error": error_msg}), 400
            
            print(f"[api_set_freq] Frequency {freq_khz:.1f} kHz is within {current_band} band limits")
        elif current_band:
            print(f"[api_set_freq] Warning: Band '{current_band}' not found in band table, proceeding without validation")
        
        cmd = f"F{freq_hz}"
        print(f"[api_set_freq] Sending command to radio: {cmd}")
        
        # Temporarily pause monitor to avoid interference
        was_monitoring = monitor_requested
        if monitor_requested:
            print("[api_set_freq] Pausing monitor to send frequency command...")
            time.sleep(0.1)  # Brief pause for monitor thread to settle
        
        # Send the command to the radio
        lines = send_serial_raw(cmd, wait_lines=0, read_timeout=0.5)
        print(f"[api_set_freq] Radio response: {lines}")
        
        return jsonify({"ok": True, "sent": cmd, "response_lines": lines})
    except ValueError as e:
        print(f"[api_set_freq] ValueError: {e}")
        return jsonify({"ok": False, "error": "Invalid frequency format. Must be a number in MHz or Hz."}), 400
    except Exception as e:
        print(f"[api_set_freq] Exception: {e}")
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
    socketio.run(app, host=args.host, port=args.http_port, debug=False, allow_unsafe_werkzeug=True)

if __name__ == "__main__":

    main()
