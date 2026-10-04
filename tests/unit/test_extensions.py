"""Optional adapters stay disabled, and synchronization contracts preserve exact values."""

from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest

from openledger.application.ports.sync import SyncChange, SyncEnvelope
from openledger.domain.errors import LedgerError
from openledger.plugins.contracts import PluginContext, PluginKind, PluginManifest
from openledger.plugins.registry import PluginRegistry


def manifest(identifier: str = "test.ai") -> PluginManifest:
    return PluginManifest(identifier, "测试解析器", "ai", "1.0.0")


def test_registration_does_not_enable_or_call_adapter() -> None:
    class Adapter:
        def __getattribute__(self, name: str) -> object:
            raise AssertionError("Registry must not inspect or execute an adapter")

    adapter = Adapter()
    registry = PluginRegistry()
    registry.register(manifest(), adapter)
    assert registry.selected("ai") is None
    registry.select("ai", "test.ai")
    selected = registry.selected("ai")
    assert selected is not None and selected.adapter is adapter
    registry.select("ai", None)
    assert registry.selected("ai") is None


def test_registry_rejects_duplicates_unknown_and_wrong_kind() -> None:
    registry = PluginRegistry()
    registry.register(manifest(), object())
    with pytest.raises(LedgerError, match="DUPLICATE_PLUGIN"):
        registry.register(manifest(), object())
    with pytest.raises(LedgerError, match="PLUGIN_NOT_FOUND"):
        registry.select("theme", "test.ai")
    with pytest.raises(LedgerError, match="PLUGIN_NOT_FOUND"):
        registry.select("ai", "missing")
    with pytest.raises(LedgerError, match="INVALID_PLUGIN_ADAPTER"):
        registry.register(manifest("empty.ai"), None)
    for kind in ("file", "network", "database"):
        with pytest.raises(LedgerError, match="INVALID_PLUGIN_KIND"):
            registry.select(cast(PluginKind, kind), None)


def test_registry_listing_is_sorted_filtered_and_immutable() -> None:
    registry = PluginRegistry()
    registry.register(manifest("z.ai"), object())
    registry.register(manifest("a.ai"), object())
    registry.register(PluginManifest("a.theme", "主题", "theme", "1"), object())
    assert tuple(entry.manifest.id for entry in registry.entries("ai")) == ("a.ai", "z.ai")
    with pytest.raises(FrozenInstanceError):
        registry.entries()[0].manifest.name = "Changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    "field,value",
    [
        ("api_version", 2),
        ("api_version", True),
        ("id", "../outside"),
        ("id", "module:Class"),
        ("id", "MixedCase"),
        ("name", "  "),
        ("kind", "database"),
        ("version", "not-a-version"),
    ],
)
def test_manifest_rejects_unsupported_contract(field: str, value: object) -> None:
    with pytest.raises(LedgerError, match="INVALID_PLUGIN_MANIFEST"):
        replace(manifest(), **{field: value})  # type: ignore[arg-type]


def test_context_has_no_financial_or_persistence_handles() -> None:
    context = PluginContext("0.3.0.dev0")
    assert context.locale == "zh_CN"
    assert not hasattr(context, "ledger") and not hasattr(context, "database")
    with pytest.raises(FrozenInstanceError):
        context.time_zone = "UTC"  # type: ignore[misc]


def change() -> SyncChange:
    return SyncChange(
        "transaction", "transaction-id", 2, 11, "update", (("amount_minor", "99999999999999"),), 1
    )


def test_sync_keeps_large_integer_money_as_text() -> None:
    event = change()
    envelope = SyncEnvelope("device", 10, 11, (event,))
    assert envelope.changes[0].fields[0][1] == "99999999999999"
    assert int(envelope.changes[0].fields[0][1]) == 99999999999999


@pytest.mark.parametrize("value", ["1.0", "1e3", "+25", " 25", "NaN", "01", ""])
def test_sync_rejects_noncanonical_integer_money(value: str) -> None:
    with pytest.raises(LedgerError, match="INVALID_SYNC_CHANGE"):
        replace(change(), fields=(("amount_minor", value),))


def test_sync_requires_expected_version_and_strict_sequence() -> None:
    with pytest.raises(LedgerError, match="INVALID_SYNC_CHANGE"):
        replace(change(), expected_version=None)
    with pytest.raises(LedgerError, match="INVALID_SYNC_CHANGE"):
        replace(change(), fields=(("amount_minor", "1"), ("amount_minor", "2")))
    for events in ((change(), change()), (replace(change(), change_seq=10),)):
        with pytest.raises(LedgerError, match="INVALID_SYNC_ENVELOPE"):
            SyncEnvelope("device", 10, 11, events)
    with pytest.raises(LedgerError, match="INVALID_SYNC_ENVELOPE"):
        SyncEnvelope("device", 10, 11, (), schema_version=2)
