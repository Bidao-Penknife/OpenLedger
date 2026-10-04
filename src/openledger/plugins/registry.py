"""Explicit adapter registration, with every optional capability disabled initially."""

from dataclasses import dataclass

from openledger.domain.errors import LedgerError
from openledger.plugins.contracts import PLUGIN_KINDS, PluginKind, PluginManifest


@dataclass(frozen=True, slots=True)
class RegisteredPlugin:
    """A validated manifest and a trusted adapter supplied by application composition."""

    manifest: PluginManifest
    adapter: object


class PluginRegistry:
    """Store adapters without loading modules, scanning directories or executing them."""

    def __init__(self) -> None:
        self._entries: dict[str, RegisteredPlugin] = {}
        self._selected: dict[PluginKind, str] = {}

    def register(self, manifest: PluginManifest, adapter: object) -> None:
        """Register one trusted adapter once; registration alone never enables it."""
        if manifest.id in self._entries:
            raise LedgerError("DUPLICATE_PLUGIN")
        if adapter is None:
            raise LedgerError("INVALID_PLUGIN_ADAPTER")
        self._entries[manifest.id] = RegisteredPlugin(manifest, adapter)

    def entries(self, kind: PluginKind | None = None) -> tuple[RegisteredPlugin, ...]:
        """Return a deterministic immutable listing for optional capability selection."""
        if kind is not None and kind not in PLUGIN_KINDS:
            raise LedgerError("INVALID_PLUGIN_KIND")
        return tuple(
            entry
            for identifier, entry in sorted(self._entries.items())
            if kind is None or entry.manifest.kind == kind
        )

    def select(self, kind: PluginKind, identifier: str | None) -> None:
        """Enable an explicitly selected adapter, or disable its entire capability."""
        if kind not in PLUGIN_KINDS:
            raise LedgerError("INVALID_PLUGIN_KIND")
        if identifier is None:
            self._selected.pop(kind, None)
            return
        entry = self._entries.get(identifier)
        if entry is None or entry.manifest.kind != kind:
            raise LedgerError("PLUGIN_NOT_FOUND")
        self._selected[kind] = identifier

    def selected(self, kind: PluginKind) -> RegisteredPlugin | None:
        """Return the selected adapter without calling it; defaults always return None."""
        if kind not in PLUGIN_KINDS:
            raise LedgerError("INVALID_PLUGIN_KIND")
        identifier = self._selected.get(kind)
        return self._entries.get(identifier) if identifier is not None else None
