"""Deliver committed worker results to the GUI through a queued Qt signal."""

from collections.abc import Mapping
from concurrent.futures import Future
from uuid import uuid4

from PySide6.QtCore import QObject, Qt, Signal, Slot

from openledger.application.dto.results import MutationResult
from openledger.application.ports.ledger import LedgerPort
from openledger.application.writer import LedgerWriter
from openledger.domain.errors import LedgerError
from openledger.domain.values import canonical_hash


class CommandBridge(QObject):
    """Allow one visible intent at a time and reuse its request ID after failure."""

    completed = Signal(object)
    failed = Signal(str)
    busyChanged = Signal(bool)
    _arrived = Signal(object)

    def __init__(self, ledger: LedgerPort, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._writer = LedgerWriter(ledger)
        self._intent: tuple[str, str] | None = None
        self._request_id = ""
        self.busy = False
        self._closed = False
        self._arrived.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def submit(self, command_type: str, payload: Mapping[str, object]) -> bool:
        """Queue a confirmed intent; duplicate key presses cannot queue another save."""
        if self.busy or self._closed:
            return False
        try:
            intent = (command_type, canonical_hash(command_type, payload))
            if intent != self._intent:
                self._intent = intent
                self._request_id = str(uuid4())
            self.busy = True
            self.busyChanged.emit(True)
            future = self._writer.submit(self._request_id, command_type, payload)
            future.add_done_callback(self._receive)
            return True
        except Exception as error:
            self.busy = False
            self.busyChanged.emit(False)
            self.failed.emit(error.code if isinstance(error, LedgerError) else "STORAGE_IO_ERROR")
            return False

    def _receive(self, future: Future[MutationResult]) -> None:
        try:
            value: object = future.result()
        except Exception as error:
            value = error
        self._arrived.emit(value)

    @Slot(object)
    def _deliver(self, value: object) -> None:
        self.busy = False
        self.busyChanged.emit(False)
        if isinstance(value, MutationResult):
            self._intent = None
            self.completed.emit(value)
        else:
            self.failed.emit(value.code if isinstance(value, LedgerError) else "STORAGE_IO_ERROR")

    def close(self) -> None:
        """Finish a running commit before the desktop can exit."""
        self._closed = True
        self._writer.close()
