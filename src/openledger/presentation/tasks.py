"""Cancelable file/query work with delivery exclusively on the GUI thread."""

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event

from PySide6.QtCore import QObject, Qt, Signal, Slot

from openledger.domain.errors import LedgerError


class TaskBridge(QObject):
    """Run one task; finished signals precede busyChanged(False) on the GUI thread."""

    completed = Signal(object)
    failed = Signal(str)
    busyChanged = Signal(bool)
    _arrived = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="openledger-file")
        self._cancel = Event()
        self.busy = False
        self._closed = False
        self._arrived.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def start(self, work: Callable[[Callable[[], bool]], object]) -> bool:
        """Accept one immutable task; cancellation never interrupts a ledger commit."""
        if self.busy or self._closed:
            return False
        self._cancel = Event()
        self.busy = True
        self.busyChanged.emit(True)
        future = self._executor.submit(work, self._cancel.is_set)
        future.add_done_callback(self._finished)
        return True

    def _finished(self, future: Future[object]) -> None:
        try:
            result: object = future.result()
        except LedgerError as error:
            result = error
        except Exception:
            # Source contents, credentials and raw filesystem errors never enter UI logs.
            result = LedgerError("BACKGROUND_TASK_FAILED")
        if not self._closed:
            self._arrived.emit(result)

    @Slot(object)
    def _deliver(self, result: object) -> None:
        if self._closed:
            return
        if isinstance(result, LedgerError):
            self.failed.emit(result.code)
        else:
            self.completed.emit(result)
        self.busy = False
        self.busyChanged.emit(False)

    def cancel(self) -> None:
        """Request cooperative cancellation; atomic publication checks before replace."""
        self._cancel.set()

    def close(self) -> None:
        """Stop accepting work, cancel queued work and wait for active file handles."""
        self._closed = True
        self._cancel.set()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self.busy = False
