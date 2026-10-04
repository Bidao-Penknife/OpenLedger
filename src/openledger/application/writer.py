"""A cancellable FIFO worker for desktop clients of the synchronous ledger port."""

from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from threading import Lock

from openledger.application.dto.results import MutationResult
from openledger.application.ports.ledger import LedgerPort


class LedgerWriter:
    """Serialize requests without sharing SQLite connections between threads."""

    def __init__(self, ledger: LedgerPort) -> None:
        self._ledger = ledger
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="openledger-writer")
        self._lock = Lock()
        self._closed = False

    def submit(
        self, request_id: str, command_type: str, payload: Mapping[str, object]
    ) -> Future[MutationResult]:
        """Queue an intent; cancelling a pending future prevents all writes."""
        with self._lock:
            if self._closed:
                raise RuntimeError("The ledger writer is closed.")
            return self._executor.submit(
                self._ledger.execute, request_id, command_type, deepcopy(dict(payload))
            )

    def close(self) -> None:
        """Reject new work and wait for already running commits to finish."""
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)
