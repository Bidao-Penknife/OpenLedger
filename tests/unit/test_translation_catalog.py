"""Reviewed English Qt resources cover literal and dynamic labels without altering data."""

import importlib.util
import sys
from collections import Counter
from pathlib import Path
from typing import Protocol, cast

import pytest
from PySide6.QtCore import QCoreApplication, QTranslator
from PySide6.QtWidgets import QApplication, QComboBox
from pytestqt.qtbot import QtBot

from openledger.application.dto.runtime import RuntimeInfo
from openledger.infrastructure.ledger import LedgerService
from openledger.presentation.languages import install_language
from openledger.presentation.views.main_window import MainWindow


class _ExtractedMessage(Protocol):
    context: str
    source: str


class _CatalogTool(Protocol):
    CATALOG: Path

    def check_catalog(self, path: Path = ..., root: Path = ...) -> None: ...

    def collect_messages(self, root: Path = ...) -> tuple[_ExtractedMessage, ...]: ...

    def compile_catalog(self, path: Path = ...) -> None: ...

    def merge_catalog(self, path: Path = ..., root: Path = ...) -> int: ...

    def placeholders(self, text: str) -> Counter[str]: ...

    def read_catalog(self, path: Path) -> dict[tuple[str, str], str]: ...


def _load_tool() -> _CatalogTool:
    path = Path(__file__).resolve().parents[2] / "scripts" / "update_translations.py"
    specification = importlib.util.spec_from_file_location("openledger_catalog_test_tool", path)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return cast(_CatalogTool, module)


_tool = _load_tool()
CATALOG = _tool.CATALOG
check_catalog = _tool.check_catalog
collect_messages = _tool.collect_messages
compile_catalog = _tool.compile_catalog
merge_catalog = _tool.merge_catalog
placeholders = _tool.placeholders
read_catalog = _tool.read_catalog


def test_reviewed_catalog_covers_all_sources_and_preserves_placeholders() -> None:
    check_catalog()
    translations = read_catalog(CATALOG)
    assert len(translations) >= 498
    for context in (
        "MainWindow",
        "QuickEntryWindow",
        "AISettingsWidget",
        "UpdatesWidget",
        "ManagementPage",
        "TransactionsPage",
        "AnalysisPage",
        "ExchangePage",
    ):
        assert any(key[0] == context for key in translations)


def test_dynamic_mapping_and_table_headers_are_explicitly_covered() -> None:
    translations = read_catalog(CATALOG)
    assert translations[("ExchangePage", "转出账户名称")] == "Source Account Name"
    assert translations[("ExchangePage", "默认收入分类")] == "Default Income Category"
    assert translations[("ExchangePage", "校验结果")] == "Validation Result"
    assert translations[("ExchangePage", "创建时间（UTC）")] == "Created At (UTC)"


def test_source_collection_never_executes_modules_and_resolves_tuple_labels(tmp_path: Path) -> None:
    (tmp_path / "example.py").write_text(
        'raise RuntimeError("Must never execute")\n'
        'LABELS = (("a", "账户"), ("b", "金额"))\n'
        "class Example:\n"
        "    def build(self):\n"
        "        for key, label in LABELS:\n"
        "            self.tr(label)\n"
        '        headings = [self.tr(v) for v in ("日期", "备注")]\n'
        'def labels(combo: QComboBox):\n    combo.tr("（已归档）")\n',
        encoding="utf-8",
    )
    assert {(message.context, message.source) for message in collect_messages(tmp_path)} == {
        ("Example", "账户"),
        ("Example", "金额"),
        ("Example", "日期"),
        ("Example", "备注"),
        ("QComboBox", "（已归档）"),
    }


def test_unknown_dynamic_translation_fails_instead_of_silently_losing_coverage(
    tmp_path: Path,
) -> None:
    (tmp_path / "example.py").write_text(
        "class Example:\n    def build(self, text):\n        self.tr(text)\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Unresolved translation source"):
        collect_messages(tmp_path)


def test_catalog_merge_preserves_reviewed_text_and_marks_new_sources(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    (sources / "example.py").write_text(
        'class Example:\n    def build(self):\n        self.tr("金额 {amount}")\n',
        encoding="utf-8",
    )
    catalog = tmp_path / "english.ts"
    merge_catalog(catalog, sources)
    with pytest.raises(ValueError, match="empty=1"):
        check_catalog(catalog, sources)
    reviewed = catalog.read_text(encoding="utf-8").replace(
        '<translation type="unfinished" />', "<translation>Amount {amount}</translation>"
    )
    catalog.write_text(reviewed, encoding="utf-8")
    assert merge_catalog(catalog, sources) == 1
    check_catalog(catalog, sources)
    assert read_catalog(catalog)[("Example", "金额 {amount}")] == "Amount {amount}"
    catalog.write_text(
        catalog.read_text(encoding="utf-8").replace("Amount {amount}", "Amount {wrong}"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="placeholders=1"):
        check_catalog(catalog, sources)


def test_duplicate_context_source_is_rejected(tmp_path: Path) -> None:
    catalog = tmp_path / "duplicate.ts"
    catalog.write_text(
        "<TS><context><name>Example</name>"
        "<message><source>账户</source><translation>Account</translation></message>"
        "<message><source>账户</source><translation>Other</translation></message>"
        "</context></TS>",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Duplicate translation"):
        read_catalog(catalog)


def test_placeholder_comparison_keeps_qt_positions_formats_and_repetitions() -> None:
    assert placeholders("%1 / {amount:,.2f} / %1 / %n") == placeholders(
        "{amount:,.2f} / %n / %1 / %1"
    )
    assert placeholders("{path}") != placeholders("{destination}")
    assert placeholders("{{literal}} {value!r}") == placeholders("{value!r} {{literal}}")


def test_qm_compilation_is_deterministic_and_matches_reviewed_ts(tmp_path: Path) -> None:
    copied = tmp_path / CATALOG.name
    copied.write_bytes(CATALOG.read_bytes())
    compile_catalog(copied)
    assert copied.with_suffix(".qm").read_bytes() == CATALOG.with_suffix(".qm").read_bytes()


def test_qt_translator_loads_package_resource_and_real_widgets_use_english(
    qapp: QApplication, qtbot: QtBot, ledger: LedgerService
) -> None:
    translator = install_language(qapp, "en_US")
    assert isinstance(translator, QTranslator)
    before = ledger.balances()
    window: MainWindow | None = None
    try:
        assert QCoreApplication.translate("MainWindow", "总览与记账") == "Overview"
        runtime = RuntimeInfo("0.3.0.dev0", "3.12", "6.11", "6.11", "3.45", "Test", False)
        window = MainWindow(runtime, ledger)
        qtbot.addWidget(window)
        assert window.navigation.button(0).text() == "Overview"
        assert window.navigation.button(2).text() == "Accounts"
        assert window.navigation.button(5).text() == "Import / Export"
        assert window.updates.check_button.text() == "Check for Updates"
        assert window.quick.save.text() == "Confirm and Save (Ctrl+Enter)"
        assert window.ai_settings.save_button.text() == "Save AI Settings"
        assert QComboBox(window).tr("（已归档）") == " (Archived)"
        # User-owned reference names and all financial values retain their original identity.
        assert str(ledger.entities("account")[0]["name"]).startswith("测试")
        assert ledger.balances() == before
    finally:
        if window is not None:
            window.request_quit()
        assert qapp.removeTranslator(translator)
    assert QCoreApplication.translate("MainWindow", "总览与记账") == "总览与记账"


def test_chinese_default_does_not_install_an_english_translator(qapp: QApplication) -> None:
    assert install_language(qapp, "zh_CN") is None
    with pytest.raises(ValueError, match="Unsupported interface language"):
        install_language(qapp, "unknown")
