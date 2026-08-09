from __future__ import annotations

import socket
from dataclasses import dataclass
from xmlrpc.client import ServerProxy


@dataclass
class QSSTVClient:
    url: str
    timeout: float = 4.0

    def _proxy(self) -> ServerProxy:
        socket.setdefaulttimeout(self.timeout)
        return ServerProxy(self.url, allow_none=True)

    def status(self) -> dict:
        return self._proxy().system.status()

    def capabilities(self) -> dict:
        return self._proxy().system.capabilities()

    def profiles(self) -> list[dict]:
        return self._proxy().drm.list_profiles()

    def send_file(self, request_id: str, path: str, profile: str) -> dict:
        return self._proxy().drm.send_file(
            {"request_id": request_id, "path": path, "profile": profile}
        )

    def tx_get(self, queue_id: str) -> dict:
        return self._proxy().tx.get(queue_id)

    def bsr(self, state: str = "") -> list[dict]:
        return self._proxy().drm.list_bsr({"state": state})

    def approve_bsr(self, bsr_id: str) -> dict:
        return self._proxy().drm.approve_bsr(bsr_id)

    def reject_bsr(self, bsr_id: str, reason: str) -> dict:
        return self._proxy().drm.reject_bsr(bsr_id, reason)
