"""Serial interface to the Buckleys UCP1D digital contact probe (RS-485, ASCII commands)."""

import logging
import math
import os
import queue
import random
import re
import threading
import time
from datetime import datetime, timezone

import serial
import serial.tools.list_ports

log = logging.getLogger(__name__)

BAUD_RATES = [1200, 1800, 2400, 3600, 4800, 7200, 9600, 14400, 19200, 28800, 38400, 57600, 115200]
DEFAULT_BAUD = 9600
DEFAULT_INTERVAL_MS = 500  # 2 Hz
MIN_INTERVAL_MS, MAX_INTERVAL_MS = 100, 60000
EOL = "\r\n"
RECONNECT_DELAY_S = 3.0
QUERY_TIMEOUT_S = 1.5

NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
USB_SERIAL_HINTS = ("ftdi", "ch340", "ch341", "cp210", "pl2303", "rs485", "usb serial", "usb-serial")
SIMULATED_PORT = "simulated"


def list_ports():
    ports = []
    for p in serial.tools.list_ports.comports():
        ports.append({"device": p.device, "description": p.description or "", "hwid": p.hwid or ""})
    by_id = "/dev/serial/by-id"
    if os.path.isdir(by_id):
        for name in sorted(os.listdir(by_id)):
            path = os.path.join(by_id, name)
            ports.append({"device": path, "description": name, "hwid": os.path.realpath(path)})
    return ports


def auto_detect_port():
    ports = list_ports()
    for p in ports:
        if p["device"].startswith("/dev/serial/by-id/"):
            return p["device"]
    for p in ports:
        text = (p["description"] + " " + p["hwid"]).lower()
        if any(h in text for h in USB_SERIAL_HINTS) or "/dev/ttyUSB" in p["device"]:
            return p["device"]
    return None


def parse_number(line):
    m = NUMBER_RE.search(line)
    return float(m.group()) if m else None


class SimulatedProbe:
    """Minimal stand-in for a UCP1D on a serial port, used when UCP_SIMULATE=1."""

    def __init__(self):
        self.mode = 3
        self.interval = DEFAULT_INTERVAL_MS / 1000
        self._next = time.monotonic()
        self._out = b""
        self._rx = b""

    def write(self, data):
        self._rx += data
        while b"\n" in self._rx:
            raw, self._rx = self._rx.split(b"\n", 1)
            self._handle(raw.decode(errors="replace").strip().upper())

    def _handle(self, cmd):
        if cmd == "SW":
            self._reply("UCP1D SIM 1.00")
        elif cmd == "TE":
            self._reply(f"{21.0 + random.uniform(-0.2, 0.2):.1f}")
        elif cmd in ("M0", "M1", "M2", "M3"):
            self.mode = int(cmd[1])
        elif cmd == "ME" and self.mode in (0, 1):
            self._reply(self._value())
        elif cmd.startswith("IN"):
            n = parse_number(cmd)
            if n is not None and MIN_INTERVAL_MS <= n <= MAX_INTERVAL_MS:
                self.interval = n / 1000

    def _value(self):
        mv = -1010 + 6 * math.sin(time.time() / 20) + random.gauss(0, 1.2)
        return f"{mv / 1000:.4f}" if self.mode in (0, 2) else f"{mv:.1f}"

    def _reply(self, text):
        self._out += (text + EOL).encode()

    def read(self, size=1):
        now = time.monotonic()
        if self.mode in (2, 3) and now >= self._next:
            self._next = now + self.interval
            self._reply(self._value())
        if not self._out:
            time.sleep(0.02)
            return b""
        data, self._out = self._out[:size], self._out[size:]
        return data

    @property
    def in_waiting(self):
        return len(self._out)

    def reset_input_buffer(self):
        self._out = b""

    def close(self):
        pass


class ProbeReader(threading.Thread):
    """Owns the serial port: configures the probe, streams samples, handles queued commands."""

    def __init__(self, settings, on_sample, on_status, on_console):
        super().__init__(daemon=True)
        self.settings = settings  # dict: port, baud, interval_ms
        self.on_sample = on_sample
        self.on_status = on_status
        self.on_console = on_console
        self.simulate = os.environ.get("UCP_SIMULATE") == "1"
        self._ser = None
        self._buf = b""
        self._commands = queue.Queue()
        self._reconnect = threading.Event()
        self._stop = threading.Event()
        self.state = {
            "connected": False,
            "port": None,
            "baud": settings["baud"],
            "interval_ms": settings["interval_ms"],
            "firmware": None,
            "temperature_c": None,
            "last_mv": None,
            "last_ts": None,
            "error": None,
            "simulated": self.simulate,
        }

    # ---- public API (called from other threads) ----

    def reconnect(self):
        self._reconnect.set()

    def stop(self):
        self._stop.set()

    def send_raw(self, text):
        self._commands.put(("raw", text))

    def set_interval(self, ms):
        self._commands.put(("interval", int(ms)))

    def read_temperature(self):
        self._commands.put(("temperature", None))

    def set_probe_baud(self, baud):
        self._commands.put(("probe_baud", int(baud)))

    # ---- internals ----

    def _update(self, **kw):
        self.state.update(kw)
        self.on_status(dict(self.state))

    def _open(self):
        if self.simulate:
            self._ser = SimulatedProbe()
            return SIMULATED_PORT
        port = self.settings["port"]
        if not port or port == "auto":
            port = auto_detect_port()
            if not port:
                raise serial.SerialException("No USB serial adapter found")
        self._ser = serial.Serial(port, self.settings["baud"], bytesize=8, parity="N", stopbits=1, timeout=0.2)
        return port

    def _close(self):
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None
        self._buf = b""

    def _write(self, cmd):
        self.on_console("tx", cmd)
        self._ser.write((cmd + EOL).encode("ascii"))

    def _readline(self, timeout):
        """Return the next complete line (split on CR or LF), or None on timeout."""
        deadline = time.monotonic() + timeout
        while True:
            idx = min((i for i in (self._buf.find(b"\r"), self._buf.find(b"\n")) if i >= 0), default=-1)
            if idx >= 0:
                line, self._buf = self._buf[:idx], self._buf[idx + 1:]
                text = line.decode("ascii", errors="replace").strip()
                if text:
                    self.on_console("rx", text)
                    return text
                continue
            if time.monotonic() >= deadline:
                return None
            chunk = self._ser.read(max(1, getattr(self._ser, "in_waiting", 0) or 1))
            if chunk:
                self._buf += chunk

    def _query(self, cmd):
        """Stop streaming (M1 poll mode), send a query, read its reply, resume streaming (M3)."""
        self._write("M1")
        time.sleep(0.1)
        self._ser.reset_input_buffer()
        self._buf = b""
        self._write(cmd)
        reply = self._readline(QUERY_TIMEOUT_S)
        self._write("M3")
        return reply

    def _configure(self):
        firmware = self._query("SW")
        interval = self.settings["interval_ms"]
        self._write(f"IN{interval}")
        self._update(firmware=firmware, interval_ms=interval)

    def _handle_command(self, kind, arg):
        if kind == "raw":
            self._write(arg)
        elif kind == "interval":
            self._write(f"IN{arg}")
            self._update(interval_ms=arg)
        elif kind == "temperature":
            reply = self._query("TE")
            temp = parse_number(reply) if reply else None
            self._update(temperature_c=temp)
        elif kind == "probe_baud":
            self._write(f"BD{arg}")
            time.sleep(0.3)
            self.settings["baud"] = arg
            self._reconnect.set()

    def run(self):
        while not self._stop.is_set():
            try:
                port = self._open()
                self._reconnect.clear()
                self._update(connected=True, port=port, baud=self.settings["baud"], error=None)
                self._configure()
                while not self._stop.is_set() and not self._reconnect.is_set():
                    try:
                        kind, arg = self._commands.get_nowait()
                        self._handle_command(kind, arg)
                    except queue.Empty:
                        pass
                    line = self._readline(0.2)
                    if line is None:
                        continue
                    mv = parse_number(line)
                    if mv is None:
                        continue
                    now = datetime.now(timezone.utc)
                    sample = {"ts": now.isoformat(timespec="milliseconds"), "epoch": now.timestamp(), "mv": mv}
                    self.state.update(last_mv=mv, last_ts=sample["ts"])
                    self.on_sample(sample)
            except Exception as e:
                log.warning("Probe connection error: %s", e)
                self._update(connected=False, error=str(e))
            finally:
                self._close()
            if self._reconnect.is_set():
                self._reconnect.clear()
                continue
            self._update(connected=False)
            self._stop.wait(RECONNECT_DELAY_S)
