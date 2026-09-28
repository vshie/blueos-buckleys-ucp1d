"""Buckleys UCP1D BlueOS extension: web UI, REST API and live WebSocket feed."""

import asyncio
import json
import logging
import os
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import probe
from logger import CsvLogger

VERSION = "0.1.0"
DATA_DIR = Path(os.environ.get("UCP_DATA_DIR", "/app/data"))
STATIC_DIR = Path(__file__).parent / "static"
SETTINGS_FILE = DATA_DIR / "settings.json"
HISTORY_LEN = 40000  # samples kept in memory for newly opened pages
HISTORY_SECONDS = 3600  # longest graph window offered in the UI
CONSOLE_LEN = 200

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
DATA_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS = {
    "port": "auto",
    "baud": probe.DEFAULT_BAUD,
    "interval_ms": probe.DEFAULT_INTERVAL_MS,
    "logging": True,
}


def load_settings():
    settings = dict(DEFAULT_SETTINGS)
    try:
        settings.update(json.loads(SETTINGS_FILE.read_text()))
    except (FileNotFoundError, ValueError):
        pass
    return settings


def save_settings():
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2))


settings = load_settings()
csv_logger = CsvLogger(DATA_DIR)
history = deque(maxlen=HISTORY_LEN)
console = deque(maxlen=CONSOLE_LEN)
clients = set()
loop: Optional[asyncio.AbstractEventLoop] = None
reader: Optional[probe.ProbeReader] = None
_was_connected = False


def status():
    s = dict(reader.state) if reader else {}
    s.update(logging=csv_logger.active, log_file=csv_logger.current, settings=settings, version=VERSION)
    return s


def broadcast(msg):
    """Thread-safe: queue a JSON message to every connected WebSocket."""
    if loop is None:
        return
    text = json.dumps(msg)

    async def send():
        for ws in list(clients):
            try:
                await ws.send_text(text)
            except Exception:
                clients.discard(ws)

    asyncio.run_coroutine_threadsafe(send(), loop)


def on_sample(sample):
    history.append(sample)
    csv_logger.write(sample)
    broadcast({"type": "sample", "data": sample})


def on_status(_state):
    global _was_connected
    connected = _state["connected"]
    if connected and not _was_connected and settings["logging"]:
        csv_logger.start()  # new CSV each time the probe (re)connects
    _was_connected = connected
    broadcast({"type": "status", "data": status()})


def on_console(direction, text):
    entry = {"dir": direction, "text": text}
    console.append(entry)
    broadcast({"type": "console", "data": entry})


@asynccontextmanager
async def lifespan(_app):
    global loop, reader
    loop = asyncio.get_running_loop()
    reader = probe.ProbeReader(settings, on_sample, on_status, on_console)
    reader.start()
    yield
    reader.stop()
    csv_logger.stop()


app = FastAPI(title="Buckleys UCP1D", version=VERSION, lifespan=lifespan)


class SettingsIn(BaseModel):
    port: Optional[str] = None
    baud: Optional[int] = None
    interval_ms: Optional[int] = Field(None, ge=probe.MIN_INTERVAL_MS, le=probe.MAX_INTERVAL_MS)


class CommandIn(BaseModel):
    command: str = Field(..., min_length=1, max_length=64)


class BaudIn(BaseModel):
    baud: int


@app.get("/register_service")
def register_service():
    return {
        "name": "Buckleys UCP1D",
        "description": "Live logging of Buckleys UCP1D underwater corrosion probe potential over RS-485",
        "icon": "mdi-flash",
        "company": "Buckleys",
        "version": VERSION,
        "webpage": "https://www.buckleysinternational.com",
        "api": "/docs",
    }


@app.get("/api/status")
def get_status():
    return status()


@app.get("/api/ports")
def get_ports():
    return {"ports": probe.list_ports(), "baud_rates": probe.BAUD_RATES}


@app.post("/api/settings")
def post_settings(body: SettingsIn):
    reconnect = False
    if body.baud is not None:
        if body.baud not in probe.BAUD_RATES:
            raise HTTPException(400, "Unsupported baud rate")
        reconnect |= body.baud != settings["baud"]
        settings["baud"] = body.baud
    if body.port is not None:
        reconnect |= body.port != settings["port"]
        settings["port"] = body.port
    if body.interval_ms is not None and body.interval_ms != settings["interval_ms"]:
        settings["interval_ms"] = body.interval_ms
        if not reconnect:
            reader.set_interval(body.interval_ms)
    save_settings()
    if reconnect:
        reader.reconnect()
    return status()


@app.post("/api/logging/start")
def logging_start():
    settings["logging"] = True
    save_settings()
    csv_logger.start()
    broadcast({"type": "status", "data": status()})
    return status()


@app.post("/api/logging/stop")
def logging_stop():
    settings["logging"] = False
    save_settings()
    csv_logger.stop()
    broadcast({"type": "status", "data": status()})
    return status()


@app.post("/api/temperature")
def temperature():
    reader.read_temperature()
    return {"ok": True}


@app.post("/api/command")
def command(body: CommandIn):
    text = body.command.strip()
    if not text.isascii() or not text.isprintable():
        raise HTTPException(400, "Command must be printable ASCII")
    reader.send_raw(text)
    return {"ok": True}


@app.post("/api/probe_baud")
def probe_baud(body: BaudIn):
    if body.baud not in probe.BAUD_RATES:
        raise HTTPException(400, "Unsupported baud rate")
    reader.set_probe_baud(body.baud)
    save_settings()
    return {"ok": True}


@app.get("/api/files")
def files():
    return {"files": csv_logger.files()}


@app.get("/api/files/{name}")
def download(name: str):
    path = csv_logger.path_for(name)
    if not path:
        raise HTTPException(404, "File not found")
    return FileResponse(path, media_type="text/csv", filename=name)


@app.delete("/api/files/{name}")
def delete(name: str):
    path = csv_logger.path_for(name)
    if not path:
        raise HTTPException(404, "File not found")
    if name == csv_logger.current:
        raise HTTPException(409, "File is currently being logged")
    path.unlink()
    return {"ok": True}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    await ws.send_text(json.dumps({"type": "status", "data": status()}))
    cutoff = time.time() - HISTORY_SECONDS
    recent = [s for s in list(history) if s["epoch"] >= cutoff]
    await ws.send_text(json.dumps({"type": "history", "data": recent}))
    await ws.send_text(json.dumps({"type": "console_history", "data": list(console)}))
    clients.add(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(ws)


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
