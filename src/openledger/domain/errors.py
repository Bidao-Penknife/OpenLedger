"""Safe, stable failures shared by the finance domain and application layer."""


class LedgerError(Exception):
    """Expose an error code without attaching private records or database details."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        super().__init__(message if message is not None else code)
