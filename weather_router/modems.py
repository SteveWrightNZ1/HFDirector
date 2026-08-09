from __future__ import annotations

import logging
import subprocess
import threading
import time
from pathlib import Path
from xmlrpc.client import ServerProxy

from .qsstv import QSSTVClient

LOG = logging.getLogger(__name__)


class ModemSupervisor:
    """Run FLDigi while idle and give QSSTV exclusive ownership for DRM work."""

    def __init__(self, root: Path, qsstv_url: str, post_tx_seconds: float = 120):
        self.root = root
        self.qsstv_url = qsstv_url
        self.post_tx_seconds = post_tx_seconds
        self.qsstv_bin = root / "QSSTV/build/qsstv"
        self.fldigi_config = root / ".fldigi"
        self.runtime = root / "run"
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._name = "stopped"
        self._log = None
        self._idle_since: float | None = None
        self._stop_event = threading.Event()
        self._monitor: threading.Thread | None = None
        self._definitions: dict[str, list[str]] = {}
        self.register_modem("fldigi", [
            "fldigi", "--config-dir", str(self.fldigi_config),
            "--xmlrpc-server-address", "127.0.0.1", "--xmlrpc-server-port", "7363",
        ])
        self.register_modem("qsstv", [str(self.qsstv_bin), "--headless"])

    def register_modem(self, name: str, command: list[str]) -> None:
        """Register a locally trusted modem command for later exclusive activation."""
        if not name or not command:
            raise ValueError("A modem name and command are required")
        self._definitions[name] = list(command)

    @property
    def state(self) -> dict:
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            remaining = 0
            if running and self._name == "qsstv" and self._idle_since is not None:
                remaining = max(0, round(self.post_tx_seconds - (time.monotonic() - self._idle_since)))
            return {
                "name": self._name if running else "stopped", "running": running,
                "pid": self._process.pid if running else None, "receive_window_seconds": remaining,
            }

    def _stop_locked(self) -> None:
        process = self._process
        self._process = None
        self._name = "stopped"
        self._idle_since = None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        if self._log:
            self._log.close()
            self._log = None

    def _start_locked(self, name: str, command: list[str]) -> None:
        self.runtime.mkdir(parents=True, exist_ok=True)
        self._log = (self.runtime / f"{name}.log").open("ab", buffering=0)
        self._process = subprocess.Popen(command, cwd=self.root, stdout=self._log, stderr=self._log)
        self._name = name
        LOG.info("Started %s modem as PID %s", name, self._process.pid)

    def _configure_fldigi(self) -> None:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and self.state["name"] == "fldigi":
            try:
                proxy = ServerProxy("http://127.0.0.1:7363", allow_none=True)
                proxy.main.set_rsid(True)
                proxy.main.rx_only()
                proxy.main.rx()
                LOG.info("FLDigi RSID enabled and TX disabled")
                return
            except OSError:
                time.sleep(0.25)
        LOG.warning("FLDigi started but its XML-RPC service did not become ready")

    def activate(self, name: str) -> None:
        """Hot-load one registered modem after stopping the current modem."""
        with self._lock:
            if self.state["name"] == name:
                return
            try:
                command = self._definitions[name]
            except KeyError as exc:
                raise ValueError(f"Unknown modem: {name}") from exc
            self._stop_locked()
            self._start_locked(name, command)

    def start_idle(self) -> None:
        with self._lock:
            if self.state["name"] == "fldigi":
                return
            self.fldigi_config.mkdir(parents=True, exist_ok=True)
        self.activate("fldigi")
        threading.Thread(
            target=self._configure_fldigi, name="fldigi-configure", daemon=True
        ).start()

    def activate_qsstv(self, timeout: float = 15) -> None:
        with self._lock:
            if self.state["name"] != "qsstv":
                if not self.qsstv_bin.is_file():
                    raise RuntimeError(f"QSSTV is not built: {self.qsstv_bin}")
                self.activate("qsstv")
                self._idle_since = time.monotonic()
        client = QSSTVClient(self.qsstv_url, timeout=1)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if client.status().get("ok"):
                    return
            except OSError:
                pass
            time.sleep(0.25)
        self.start_idle()
        raise RuntimeError("QSSTV did not become ready")

    def _reconcile(self) -> None:
        state = self.state
        if state["name"] == "stopped":
            self.start_idle()
            return
        if state["name"] != "qsstv":
            return
        try:
            status = QSSTVClient(self.qsstv_url, timeout=1).status()
        except OSError:
            return
        active = bool(status.get("busy")) or int(status.get("queued", 0)) > 0
        with self._lock:
            if active:
                self._idle_since = None
                return
            if self._idle_since is None:
                self._idle_since = time.monotonic()
                return
            expired = time.monotonic() - self._idle_since >= self.post_tx_seconds
        if expired:
            self.start_idle()

    def _monitor_loop(self) -> None:
        while not self._stop_event.wait(1):
            try:
                self._reconcile()
            except Exception:
                LOG.exception("Could not reconcile modem lifecycle")

    def start(self) -> None:
        self.start_idle()
        if not self._monitor or not self._monitor.is_alive():
            self._stop_event.clear()
            self._monitor = threading.Thread(target=self._monitor_loop, name="modem-supervisor", daemon=True)
            self._monitor.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._monitor and self._monitor is not threading.current_thread():
            self._monitor.join(timeout=3)
        with self._lock:
            self._stop_locked()


class ManagedQSSTVClient(QSSTVClient):
    def __init__(self, url: str, supervisor: ModemSupervisor):
        super().__init__(url)
        self.supervisor = supervisor

    def send_file(self, request_id: str, path: str, profile: str) -> dict:
        self.supervisor.activate_qsstv()
        return super().send_file(request_id, path, profile)
