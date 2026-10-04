"""Per-data-directory instance ownership and a bounded activation-only protocol."""

import hashlib
import os
import time
from pathlib import Path
from typing import Literal

from PySide6.QtCore import QLockFile, QObject, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from openledger.domain.errors import LedgerError

_MAX_REQUEST = 32
_MAX_CLIENTS = 8
_COMMANDS = {b"show\n": "show", b"quick\n": "quick"}


class InstanceCoordinator(QObject):
    """Acquire before opening the ledger; secondary processes may only activate UI."""

    activationRequested = Signal(str)

    def __init__(self, data_directory: Path, parent: QObject | None = None) -> None:
        super().__init__(parent)
        path = data_directory.resolve()
        identity = os.path.normcase(str(path))
        self.server_name = "openledger-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        self.lock = QLockFile(str(path / ".instance.lock"))
        self.lock.setStaleLockTime(0)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self._accept)
        self._clients: dict[QLocalSocket, tuple[bytearray, QTimer]] = {}
        self.is_primary = False

    def acquire(self) -> bool:
        """Only the lock holder may remove stale IPC endpoints and listen."""
        if self.is_primary:
            return True
        if not self.lock.tryLock(0):
            if self.lock.error() == QLockFile.LockError.LockFailedError:
                return False
            raise LedgerError("INSTANCE_LOCK_FAILED")
        QLocalServer.removeServer(self.server_name)
        if not self.server.listen(self.server_name):
            self.lock.unlock()
            raise LedgerError("INSTANCE_LISTEN_FAILED")
        self.is_primary = True
        return True

    def activate(self, command: Literal["show", "quick"] = "show", timeout_ms: int = 1500) -> bool:
        """Send one bounded action and require acknowledgement from the owner."""
        if command not in {"show", "quick"} or not 1 <= timeout_ms <= 5000:
            raise LedgerError("INVALID_ACTIVATION")
        deadline = time.monotonic() + timeout_ms / 1000
        socket = QLocalSocket()
        try:
            # The primary may hold its lock while its local server is still starting.
            while time.monotonic() < deadline:
                socket.connectToServer(self.server_name)
                remaining = max(1, int((deadline - time.monotonic()) * 1000))
                if socket.waitForConnected(min(100, remaining)):
                    break
                socket.abort()
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
            else:
                return False
            if socket.write((command + "\n").encode("ascii")) < 0:
                return False
            socket.flush()
            reply = bytearray()
            while time.monotonic() < deadline:
                remaining = max(1, int((deadline - time.monotonic()) * 1000))
                if not socket.bytesAvailable() and not socket.waitForReadyRead(remaining):
                    return False
                reply.extend(socket.readAll().data())
                if b"\n" in reply:
                    return reply == b"ok\n"
                if len(reply) > 8:
                    return False
            return False
        finally:
            socket.abort()

    def _accept(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is None:
                continue
            if len(self._clients) >= _MAX_CLIENTS:
                socket.abort()
                socket.deleteLater()
                continue
            socket.setReadBufferSize(_MAX_REQUEST + 1)
            timer = QTimer(socket)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda client=socket: client.abort())
            self._clients[socket] = bytearray(), timer
            socket.readyRead.connect(lambda client=socket: self._read(client))
            socket.disconnected.connect(lambda client=socket: self._drop(client))
            timer.start(1500)
            self._read(socket)

    def _read(self, socket: QLocalSocket) -> None:
        client = self._clients.get(socket)
        if client is None:
            return
        buffer, timer = client
        buffer.extend(socket.readAll().data())
        if len(buffer) > _MAX_REQUEST:
            socket.abort()
            return
        if b"\n" not in buffer:
            return
        command = _COMMANDS.get(bytes(buffer))
        timer.stop()
        if command is None:
            socket.abort()
            return
        # Remove authority before emitting: a second packet cannot trigger another action.
        self._clients.pop(socket, None)
        socket.write(b"ok\n")
        socket.flush()
        socket.disconnectFromServer()
        self.activationRequested.emit(command)

    def _drop(self, socket: QLocalSocket) -> None:
        self._clients.pop(socket, None)
        socket.deleteLater()

    def close(self) -> None:
        """Stop accepting commands, abort partial clients, then relinquish ownership."""
        for socket in tuple(self._clients):
            socket.abort()
        self._clients.clear()
        self.server.close()
        if self.is_primary:
            QLocalServer.removeServer(self.server_name)
            self.lock.unlock()
        self.is_primary = False
