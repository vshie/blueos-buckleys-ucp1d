"""CSV logging of probe samples, one file per logging session."""

import csv
import threading
from datetime import datetime, timezone
from pathlib import Path

HEADER = ["timestamp_utc", "epoch_s", "potential_mV"]


class CsvLogger:
    def __init__(self, directory):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._fh = None
        self._writer = None
        self.current = None

    @property
    def active(self):
        return self._fh is not None

    def start(self):
        with self._lock:
            self._close_locked()
            name = datetime.now(timezone.utc).strftime("ucp1d_%Y%m%d_%H%M%S.csv")
            self._fh = open(self.dir / name, "a", newline="")
            self._writer = csv.writer(self._fh)
            self._writer.writerow(HEADER)
            self._fh.flush()
            self.current = name

    def stop(self):
        with self._lock:
            self._close_locked()

    def _close_locked(self):
        if self._fh:
            self._fh.close()
        self._fh = self._writer = self.current = None

    def write(self, sample):
        with self._lock:
            if self._writer:
                self._writer.writerow([sample["ts"], f"{sample['epoch']:.3f}", sample["mv"]])
                self._fh.flush()

    def files(self):
        out = []
        for p in sorted(self.dir.glob("ucp1d_*.csv"), reverse=True):
            st = p.stat()
            out.append({"name": p.name, "size": st.st_size, "modified": st.st_mtime})
        return out

    def path_for(self, name):
        p = (self.dir / name).resolve()
        if p.parent != self.dir.resolve() or not p.name.startswith("ucp1d_") or p.suffix != ".csv":
            return None
        return p if p.exists() else None
