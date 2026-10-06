"""Daily bookkeeping desktop using pure drafts and one committed write boundary."""

from collections.abc import Mapping
from datetime import date
from importlib.resources import files
from typing import cast
from uuid import uuid4
from zoneinfo import ZoneInfo, available_timezones

from PySide6.QtCore import QDate, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from openledger.application.dto.parsing import (
    ChannelAccountMapping,
    ParseChoice,
    ParsedDraft,
    ParseRequest,
    ParseResult,
)
from openledger.application.dto.results import MutationResult
from openledger.application.dto.runtime import RuntimeInfo
from openledger.application.parsing import LocalParser
from openledger.application.ports.ai import AIProvider, CredentialStore
from openledger.domain.errors import LedgerError
from openledger.infrastructure.ai import AIParser, AISettingsStore, credential_target
from openledger.infrastructure.credentials import WindowsCredentialStore
from openledger.infrastructure.ledger import LedgerService
from openledger.infrastructure.queries import LedgerQueries
from openledger.infrastructure.resources import read_text_resource
from openledger.infrastructure.settings import DesktopSettings, SettingsStore
from openledger.infrastructure.updates import UpdateSettingsStore
from openledger.plugins.contracts import PluginManifest
from openledger.plugins.registry import PluginRegistry
from openledger.presentation.appearance import ThemeController
from openledger.presentation.commands import CommandBridge
from openledger.presentation.desktop import DesktopController
from openledger.presentation.tasks import TaskBridge
from openledger.presentation.views.ai_settings import AISettingsWidget
from openledger.presentation.views.analysis import AnalysisPage
from openledger.presentation.views.exchange import ExchangePage
from openledger.presentation.views.management import ManagementPage, OperationDialog
from openledger.presentation.views.quick_entry import QuickEntryWindow
from openledger.presentation.views.transaction_edit import AdjustmentDialog, EditTransactionDialog
from openledger.presentation.views.transaction_form import QuickInput, TransactionForm, money_text
from openledger.presentation.views.transactions import TransactionsPage
from openledger.presentation.views.updates import UpdatesWidget


class MainWindow(QMainWindow):
    """Coordinate immutable read snapshots and asynchronous, idempotent writes."""

    def __init__(
        self,
        runtime: RuntimeInfo,
        ledger: LedgerService,
        settings_store: SettingsStore | None = None,
        *,
        credentials: CredentialStore | None = None,
        ai_parser: AIProvider | None = None,
    ) -> None:
        super().__init__()
        self.ledger = ledger
        self.queries = LedgerQueries(ledger.database)
        self.settings_store = settings_store
        self.settings = settings_store.load() if settings_store else DesktopSettings()
        data_dir = settings_store.path.parent if settings_store else ledger.database.path.parent
        self.ai_store = AISettingsStore(data_dir)
        self.credentials = credentials if credentials is not None else WindowsCredentialStore()
        self.ai_parser = ai_parser if ai_parser is not None else AIParser(clock=ledger.clock)
        self.plugins = PluginRegistry()
        self.plugins.register(
            PluginManifest("openai-compatible", "OpenAI compatible", "ai", "1.0.0"), self.ai_parser
        )
        self.ai_tasks = TaskBridge(self)
        self.ai_tasks.completed.connect(self._ai_completed)
        self.ai_tasks.failed.connect(self._ai_failed)
        self.ai_tasks.busyChanged.connect(self._ai_busy)
        self._ai_request: ParseRequest | None = None
        self._ai_cancelled = False
        self.desktop: DesktopController | None = None
        self._force_quit = False
        self._quit_when_idle = False
        self.appearance = ThemeController(self.settings.theme, self)
        self.appearance.themeChanged.connect(self._apply_theme)
        self.ledger.time_zone = self.settings.time_zone
        self.parser = LocalParser()
        self.bridge = CommandBridge(ledger, self)
        self._draft_id, self._transaction_id = str(uuid4()), str(uuid4())
        self._revision = 1
        self._parsed: ParseResult | None = None
        self._candidate: ParsedDraft | None = None
        self._manual = True
        self._saved_spans: set[tuple[int, int]] = set()
        self._last_command = ""
        self._pending_source: str | None = None
        self._pending_dialog: QDialog | None = None
        self.setObjectName("mainWindow")
        self.setWindowTitle("OpenLedger")
        self.resize(1200, 820)
        self.setMinimumSize(880, 600)
        self.setStyleSheet(read_text_resource(f"themes/{self.appearance.effective}.qss"))
        root = QWidget(self)
        root.setObjectName("windowRoot")
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(root)
        self._pages = QStackedWidget(root)
        self._pages.setObjectName("pages")
        self._pages.addWidget(self._build_home(runtime))
        self.transactions = TransactionsPage(ledger, self.queries, self.today(), self)
        self.transactions.actionRequested.connect(self._transaction_action)
        self._pages.addWidget(self.transactions)
        self.management = ManagementPage(ledger, self, time_zone=self.settings.time_zone)
        self.management.commandRequested.connect(self._submit_management)
        self._pages.addWidget(self.management)
        self._pages.addWidget(self._build_settings(runtime))
        self.analysis = AnalysisPage(ledger, self.today(), self)
        self.analysis.set_theme(self.appearance.effective)
        self.analysis.categoryRequested.connect(self._category_drill)
        self._pages.addWidget(self.analysis)
        self.exchange = ExchangePage(ledger, self)
        self.exchange.commandRequested.connect(self._submit_exchange)
        self._pages.addWidget(self.exchange)
        self.quick = QuickEntryWindow(ledger, self, time_zone=self.settings.time_zone)
        self.quick.setStyleSheet(self.styleSheet())
        self.quick.commandRequested.connect(self._submit_quick)
        root_layout.addWidget(self._build_sidebar(runtime))
        root_layout.addWidget(self._pages, 1)
        self.notice = QLabel(self)
        self.notice.setObjectName("commandStatus")
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.statusBar().addWidget(self.notice, 1)
        self.bridge.completed.connect(self._committed)
        self.bridge.failed.connect(self._failed)
        self.bridge.busyChanged.connect(self._busy)
        exit_action = QAction(self.tr("退出"), self)
        exit_action.setShortcut(QKeySequence("Ctrl+Q"))
        exit_action.triggered.connect(self.request_quit)
        self.addAction(exit_action)
        self.refresh()

    def today(self) -> date:
        return self.ledger.clock().astimezone(ZoneInfo(self.settings.time_zone)).date()

    def _build_sidebar(self, runtime: RuntimeInfo) -> QWidget:
        sidebar = QFrame(self)
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(184)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(18, 28, 18, 22)
        brand = QLabel("OpenLedger", sidebar)
        brand.setObjectName("brand")
        layout.addWidget(brand)
        caption = QLabel(self.tr("个人财务 · 本地优先"), sidebar)
        caption.setObjectName("brandCaption")
        layout.addWidget(caption)
        layout.addSpacing(30)
        self.navigation = QButtonGroup(sidebar)
        self.navigation.setExclusive(True)
        self.navigation.idClicked.connect(self._pages.setCurrentIndex)
        for index, label, name in [
            (0, self.tr("总览与记账"), "homeNavigation"),
            (4, self.tr("统计分析"), "analysisNavigation"),
            (1, self.tr("交易记录"), "transactionsNavigation"),
            (2, self.tr("账户与管理"), "managementNavigation"),
            (5, self.tr("导入与导出"), "exchangeNavigation"),
            (3, self.tr("设置"), "environmentNavigation"),
        ]:
            button = QPushButton(label, sidebar)
            button.setObjectName(name)
            button.setProperty("navigation", True)
            button.setCheckable(True)
            button.setChecked(index == 0)
            self.navigation.addButton(button, index)
            layout.addWidget(button)
        layout.addStretch()
        mode_label = QLabel(self.tr("本地规则解析"), sidebar)
        mode_label.setProperty("secondary", True)
        layout.addWidget(mode_label)
        version = QLabel(f"v{runtime.app_version}", sidebar)
        version.setObjectName("sidebarVersion")
        layout.addWidget(version)
        exit_button = QPushButton(self.tr("退出"), sidebar)
        exit_button.setObjectName("exitButton")
        exit_button.clicked.connect(self.request_quit)
        layout.addWidget(exit_button)
        return sidebar

    def _page(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        scroll = QScrollArea(self)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)
        heading = QLabel(title, content)
        heading.setProperty("heading", True)
        layout.addWidget(heading)
        hint = QLabel(subtitle, content)
        hint.setProperty("secondary", True)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        scroll.setWidget(content)
        return scroll, layout

    def _build_home(self, runtime: RuntimeInfo) -> QWidget:
        page, layout = self._page(
            self.tr("总览与记账"), self.tr("记下每一笔，确认后保存到本地账本。")
        )
        page.setObjectName("homePage")
        metrics = QHBoxLayout()
        self.metrics: dict[str, QLabel] = {}
        for key, label in [
            ("assets", self.tr("总资产")),
            ("income", self.tr("本月收入")),
            ("expense", self.tr("本月净支出")),
        ]:
            card = QFrame(page)
            card.setProperty("card", True)
            card_layout = QVBoxLayout(card)
            card_layout.addWidget(QLabel(label, card))
            number = QLabel("¥ 0.00", card)
            number.setObjectName(key + "Metric")
            number.setProperty("metric", True)
            card_layout.addWidget(number)
            self.metrics[key] = number
            metrics.addWidget(card)
        layout.addLayout(metrics)
        self.first_run = QLabel(page)
        self.first_run.setWordWrap(True)
        self.first_run.setProperty("secondary", True)
        layout.addWidget(self.first_run)
        self.input = QuickInput(page)
        self.input.textChanged.connect(self._text_changed)
        self.input.parseRequested.connect(self.parse_input)
        self.input.saveRequested.connect(self.save_draft)
        layout.addWidget(self.input)
        toolbar = QHBoxLayout()
        for label, name, callback in [
            (self.tr("解析草稿（Enter）"), "parseDraft", self.parse_input),
            (self.tr("手工录入"), "manualDraft", self._manual_draft),
            (self.tr("新建草稿"), "newDraft", self._reset_draft),
        ]:
            button = QPushButton(label, page)
            button.setObjectName(name)
            button.setProperty("primary", name == "parseDraft")
            button.clicked.connect(callback)
            toolbar.addWidget(button)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        ai_toolbar = QHBoxLayout()
        self.ai_button = QPushButton(self.tr("AI 解析（可选）"), page)
        self.ai_button.setObjectName("aiParseButton")
        self.ai_button.clicked.connect(self.parse_with_ai)
        self.ai_cancel = QPushButton(self.tr("取消 AI 解析"), page)
        self.ai_cancel.setObjectName("aiCancelButton")
        self.ai_cancel.clicked.connect(self._cancel_ai)
        self.ai_cancel.setEnabled(False)
        quick_button = QPushButton(self.tr("打开快速记账窗口"), page)
        quick_button.setObjectName("openQuickWindow")
        quick_button.clicked.connect(lambda: self.quick.open())
        ai_toolbar.addWidget(self.ai_button)
        ai_toolbar.addWidget(self.ai_cancel)
        ai_toolbar.addStretch()
        ai_toolbar.addWidget(quick_button)
        layout.addLayout(ai_toolbar)
        self.parse_status = QLabel(self.tr("可直接手工填写；自然语言输入需要先解析。"), page)
        self.parse_status.setObjectName("parseStatus")
        self.parse_status.setWordWrap(True)
        self.parse_status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.parse_status)
        self.candidates = QComboBox(page)
        self.candidates.setObjectName("draftCandidates")
        self.candidates.hide()
        self.candidates.activated.connect(self._select_candidate)
        layout.addWidget(self.candidates)
        columns = QHBoxLayout()
        draft = QFrame(page)
        draft.setProperty("card", True)
        draft_layout = QVBoxLayout(draft)
        title = QLabel(self.tr("确认记账草稿"), draft)
        title.setProperty("cardTitle", True)
        draft_layout.addWidget(title)
        self.form = TransactionForm(self.ledger, self.today(), draft)
        self.form.edited.connect(self._form_edited)
        draft_layout.addWidget(self.form)
        self.zone_hint = QLabel(
            self.tr("记账时区：{zone}").format(zone=self.settings.time_zone), draft
        )
        self.zone_hint.setProperty("secondary", True)
        draft_layout.addWidget(self.zone_hint)
        self.save = QPushButton(self.tr("确认保存（Ctrl+Enter）"), draft)
        self.save.setObjectName("saveDraft")
        self.save.setProperty("primary", True)
        self.save.clicked.connect(self.save_draft)
        draft_layout.addWidget(self.save)
        columns.addWidget(draft, 3)
        evidence_card = QFrame(page)
        evidence_card.setProperty("card", True)
        evidence_layout = QVBoxLayout(evidence_card)
        title = QLabel(self.tr("原输入识别依据"), evidence_card)
        title.setProperty("cardTitle", True)
        evidence_layout.addWidget(title)
        self.evidence = QLabel(
            self.tr(
                "金额、日期和支付方式的识别结果将在这里显示。分类建议和默认账户会标明来源，请在保存前核对。"
            ),
            evidence_card,
        )
        self.evidence.setObjectName("draftEvidence")
        self.evidence.setWordWrap(True)
        self.evidence.setTextFormat(Qt.TextFormat.PlainText)
        self.evidence.setAlignment(Qt.AlignmentFlag.AlignTop)
        evidence_layout.addWidget(self.evidence)
        evidence_layout.addStretch()
        columns.addWidget(evidence_card, 2)
        layout.addLayout(columns)
        version = QLabel(self.tr("当前版本：{version}").format(version=runtime.app_version), page)
        version.setObjectName("homeVersion")
        version.setProperty("secondary", True)
        layout.addWidget(version)
        return page

    def _text_changed(self) -> None:
        self._cancel_ai()
        self._revision += 1
        self._parsed, self._candidate = None, None
        self._manual = not self.input.toPlainText().strip()
        self._saved_spans.clear()
        self.candidates.hide()
        self.save.setEnabled(self._manual)
        self.parse_status.setText(self.tr("输入已变化，请重新解析；也可以选择手工录入。"))

    def _form_edited(self) -> None:
        self._revision += 1
        self._cancel_ai()

    def _manual_draft(self) -> None:
        if not self.input.composing and not self.bridge.busy:
            self._cancel_ai()
            self._manual, self._candidate = True, None
            self.save.setEnabled(True)
            self.parse_status.setText(self.tr("手工录入：以当前表单为准，保存前请核对所有字段。"))

    def _choices(self, entity: str) -> tuple[ParseChoice, ...]:
        return tuple(
            ParseChoice(
                str(row["id"]),
                str(row["name"]),
                kind=cast(str | None, row.get("transaction_kind")),
                currency_code=str(row.get("currency_code", "CNY")),
            )
            for row in self.ledger.entities(entity)
        )

    def parse_input(self) -> None:
        """Parsing is pure; only a current draft result may populate this form."""
        if self.input.composing or self.bridge.busy:
            return
        self._cancel_ai()
        request = self._parse_request()
        try:
            result = self.parser.parse(request)
        except (LedgerError, ValueError):
            self.parse_status.setText(self.tr("无法解析，请检查输入内容或使用手工录入。"))
            return
        self._accept_parse_result(result, request)

    def _parse_request(self) -> ParseRequest:
        preferences = self.queries.preferences()
        return ParseRequest(
            self._draft_id,
            self._revision,
            self.input.toPlainText(),
            self.today(),
            self.settings.time_zone,
            current_book_id=cast(
                str | None, self.form.book.currentData() or preferences["default_book_id"]
            ),
            default_account_id=cast(str | None, preferences["default_account_id"]),
            category_choices=self._choices("category"),
            account_choices=self._choices("account"),
            payment_method_choices=self._choices("payment_method"),
            channel_account_mappings=tuple(
                ChannelAccountMapping(str(row["id"]), str(row["default_account_id"]))
                for row in self.ledger.entities("payment_method")
                if row["default_account_id"]
            ),
            locale=self.settings.language,
            currency_code=self.form.account_currency(),
        )

    def _accept_parse_result(self, result: ParseResult, request: ParseRequest) -> None:
        if result.draft_id != self._draft_id or result.revision != self._revision:
            return
        self._parsed, self._candidate, self._manual = result, None, False
        self.candidates.clear()
        self.candidates.hide()
        if result.status == "single" and result.drafts:
            self._apply_candidate(result.drafts[0])
        elif result.status == "multiple_events" and result.drafts:
            self.candidates.addItem(self.tr("识别到多笔，请选择本次要保存的一笔"), None)
            for index, candidate in enumerate(result.drafts):
                if (candidate.span.start, candidate.span.end) not in self._saved_spans:
                    self.candidates.addItem(
                        request.text[candidate.span.start : candidate.span.end], index
                    )
            self.candidates.show()
            self.save.setEnabled(False)
            self.parse_status.setText(
                self.tr("多笔输入需要逐笔确认。其余候选保留在列表中，不会自动入账。")
            )
        else:
            self.save.setEnabled(False)
            self.parse_status.setText(
                self.tr("输入有歧义或暂不支持，请修改文字后重新解析，或选择手工录入。")
                + "\n"
                + "\n".join(issue.message for issue in result.issues)
            )

    def parse_with_ai(self) -> None:
        """Send only this explicit request; replies can populate a confirmation draft."""
        if self.input.composing or self.bridge.busy or self.ai_tasks.busy:
            return
        config = self.ai_settings.config
        selected = self.plugins.selected("ai")
        if not config.enabled or selected is None:
            self.parse_status.setText(self.tr("请先在设置中启用并配置 AI；本地解析始终可用。"))
            return
        request = self._parse_request()
        if not request.text.strip():
            self.parse_status.setText(self.tr("请输入需要解析的文字。"))
            return
        try:
            key = self.credentials.get(credential_target(self.ai_store.data_dir, config))
            if not key:
                raise LedgerError("AI_KEY_MISSING")
        except LedgerError as error:
            self.parse_status.setText(
                self.tr("无法读取 AI 凭据，请检查设置；当前草稿已保留。") + f" [{error.code}]"
            )
            return
        self._ai_request = request
        self._ai_cancelled = False
        provider = cast(AIProvider, selected.adapter)
        if self.ai_tasks.start(lambda cancel: provider.parse(request, config, key, cancel)):
            self.parse_status.setText(self.tr("正在请求 AI 建议，请等待并核对返回的草稿…"))

    def _cancel_ai(self) -> None:
        self._ai_cancelled = True
        self.ai_tasks.cancel()

    def _ai_busy(self, busy: bool) -> None:
        self.ai_button.setEnabled(not busy)
        self.ai_cancel.setEnabled(busy)

    def _ai_completed(self, value: object) -> None:
        request = self._ai_request
        if request is None or self._ai_cancelled or not isinstance(value, ParseResult):
            return
        self._accept_parse_result(value, request)

    def _ai_failed(self, code: str) -> None:
        request = self._ai_request
        if (
            request is not None
            and not self._ai_cancelled
            and request.draft_id == self._draft_id
            and request.revision == self._revision
        ):
            self.parse_status.setText(
                self.tr("AI 解析未完成；当前输入与草稿已保留，可继续本地解析。") + f" [{code}]"
            )

    def _select_candidate(self, index: int) -> None:
        if self.ai_tasks.busy:
            self._cancel_ai()
        candidate_index = self.candidates.itemData(index)
        if self._parsed and isinstance(candidate_index, int):
            self.form.user_fields.clear()
            self._apply_candidate(self._parsed.drafts[candidate_index])
        else:
            self._candidate = None
            self.save.setEnabled(False)

    def _apply_candidate(self, candidate: ParsedDraft) -> None:
        self._candidate = candidate
        values: dict[str, object] = {"currency_code": candidate.currency_code}
        values["time_zone"] = candidate.time_zone
        explanations: list[str] = []
        origins = {
            "explicit": self.tr("从输入识别"),
            "rule_suggestion": self.tr("规则建议"),
            "ai_suggestion": self.tr("AI 建议，请核对"),
            "default": self.tr("默认值"),
            "user": self.tr("手工指定"),
        }
        labels = {
            "kind": self.tr("类型"),
            "amount_minor": self.tr("金额"),
            "occurred_on": self.tr("日期"),
            "time_period": self.tr("时段"),
            "account_id": self.tr("资金账户"),
            "category_id": self.tr("分类"),
            "payment_method_id": self.tr("支付方式"),
            "counterparty": self.tr("对象"),
            "note": self.tr("备注"),
        }
        references = {
            str(row["id"]): str(row["name"])
            for entity in ("account", "book", "category", "payment_method")
            for row in self.ledger.entities(entity)
        }
        enum_labels = {
            "income": self.tr("收入"),
            "expense": self.tr("支出"),
            "morning": self.tr("早上"),
            "noon": self.tr("中午"),
            "afternoon": self.tr("下午"),
            "evening": self.tr("晚上"),
            "night": self.tr("夜间"),
        }
        for name in (
            "kind",
            "amount_minor",
            "occurred_on",
            "time_period",
            "occurred_at_utc",
            "book_id",
            "account_id",
            "category_id",
            "payment_method_id",
            "counterparty",
            "merchant",
            "location",
            "note",
        ):
            field = getattr(candidate, name)
            if name not in self.form.user_fields:
                values[name] = field.value
            if name in labels and field.value is not None:
                shown = (
                    money_text(field.value)
                    if name == "amount_minor"
                    else references.get(
                        str(field.value), enum_labels.get(str(field.value), str(field.value))
                    )
                )
                explanations.append(
                    f"{labels[name]}：{shown} · {origins.get(field.origin, field.origin)}"
                )
        for name, editor in [
            ("counterparty", self.form.counterparty),
            ("merchant", self.form.merchant),
            ("location", self.form.location),
            ("note", self.form.note),
        ]:
            if name in self.form.user_fields:
                values[name] = editor.text()
        self.form.load(values)
        self.evidence.setText("\n\n".join(explanations) or self.tr("请手工补齐缺少的信息。"))
        issues = (
            [
                issue.message
                for issue in self._parsed.issues
                if issue.candidate_id in {None, candidate.candidate_id}
            ]
            if self._parsed
            else []
        )
        self.parse_status.setText(
            self.tr("草稿已生成。请核对建议、账户与日期，再确认保存。")
            + ("\n" + "\n".join(issues) if issues else "")
        )
        self.save.setEnabled(True)

    def save_draft(self) -> None:
        if self.input.composing or self.bridge.busy:
            return
        try:
            if not self._manual and self._candidate is None:
                raise LedgerError("DRAFT_NOT_CONFIRMED")
            if not self._manual and self._parsed and self._candidate:
                for issue in self._parsed.issues:
                    if (
                        issue.code == "MULTIPLE_AMOUNTS"
                        and self._parsed.status == "multiple_events"
                    ):
                        continue
                    if (
                        issue.blocking
                        and issue.candidate_id in {None, self._candidate.candidate_id}
                        and issue.field not in self.form.user_fields
                    ):
                        raise LedgerError("DRAFT_NOT_CONFIRMED")
            fields = self.form.fields(
                self.settings.time_zone,
                source=(
                    "manual"
                    if self._manual
                    else "ai_assisted"
                    if self._parsed and self._parsed.provider_id == "openai-compatible"
                    else "local_rule"
                ),
                source_text=None if self._manual else self.input.toPlainText(),
            )
            self._submit("transaction.record.v1", {"id": self._transaction_id, "fields": fields})
        except LedgerError as error:
            self._failed(error.code)

    def _reset_draft(self) -> None:
        if self.bridge.busy:
            return
        self._cancel_ai()
        self._draft_id, self._transaction_id = str(uuid4()), str(uuid4())
        self._revision += 1
        self._parsed, self._candidate, self._manual = None, None, True
        self._saved_spans.clear()
        self.form.user_fields.clear()
        self.input.clear()
        preferences = self.queries.preferences()
        self.form.load(
            {
                "kind": "expense",
                "amount_minor": 0,
                "occurred_on": self.today(),
                "time_period": None,
                "account_id": preferences["default_account_id"],
                "book_id": preferences["default_book_id"],
                "category_id": None,
                "payment_method_id": None,
                "occurred_at_utc": None,
                "time_zone": self.settings.time_zone,
                "counterparty": None,
                "merchant": None,
                "location": None,
                "note": None,
                "tag_ids": (),
            }
        )
        self.save.setEnabled(True)
        self.evidence.setText(self.tr("新草稿：可输入自然语言，也可手工填写。"))

    def _submit_management(self, command_type: str, payload: object) -> None:
        self._submit(command_type, payload, source="management")

    def _submit_exchange(self, command_type: str, payload: object) -> None:
        self._submit(command_type, payload, source="exchange")

    def _submit_quick(self, command_type: str, payload: object) -> None:
        self._submit(command_type, payload, source="quick")

    def _submit(self, command_type: str, payload: object, *, source: str = "draft") -> None:
        if not self.bridge.busy and isinstance(payload, Mapping):
            self._last_command = command_type
            self._pending_source = source
            if self.bridge.submit(command_type, cast(Mapping[str, object], payload)):
                return
            if self._pending_source is None:
                # A synchronous rejection has already delivered its specific safe code.
                return
            self._pending_source = None
        if source == "quick":
            self.quick.command_finished(False, "COMMAND_NOT_ACCEPTED")
            self.quick.set_busy(self.bridge.busy)

    def _busy(self, busy: bool) -> None:
        self._pages.setEnabled(not busy)
        self.quick.set_busy(busy)
        if busy:
            self._cancel_ai()
        elif self._quit_when_idle:
            QTimer.singleShot(0, self.request_quit)
        if busy:
            self.notice.setText(self.tr("正在保存，请稍候…"))

    def _committed(self, result: MutationResult) -> None:
        if self._last_command == "transaction.record.v1" and self._pending_source == "draft":
            if self._parsed and self._parsed.status == "multiple_events" and self._candidate:
                self._saved_spans.add((self._candidate.span.start, self._candidate.span.end))
                self.candidates.removeItem(self.candidates.currentIndex())
                self.candidates.setCurrentIndex(0)
                self._candidate = None
                self._transaction_id = str(uuid4())
                self.form.user_fields.clear()
                self.save.setEnabled(False)
            else:
                self._reset_draft()
        if self._pending_source == "transaction":
            if self._pending_dialog:
                self._pending_dialog.deleteLater()
            self._pending_dialog = None
        if self._pending_source == "management":
            self.management.command_finished(True)
        if self._pending_source == "exchange":
            self.exchange.command_finished(True)
        if self._pending_source == "quick":
            self.quick.command_finished(True)
        self.exchange.invalidate()
        self.analysis.invalidate()
        self._pending_source = None
        self.refresh()
        self.notice.setText(
            self.tr("已保存。")
            if result.outcome == "applied"
            else self.tr("余额没有变化，已记录本次确认。")
        )

    def _failed(self, code: str) -> None:
        messages = {
            "INVALID_AMOUNT": self.tr("请输入大于 0 的金额。"),
            "AMOUNT_PRECISION": self.tr("金额最多保留两位小数。"),
            "MISSING_REQUIRED_FIELD": self.tr("请补齐资金账户、账本和分类。"),
            "DRAFT_NOT_CONFIRMED": self.tr("请先解析并处理歧义，或明确选择手工录入。"),
            "VERSION_CONFLICT": self.tr("记录已被修改。请刷新并重新打开编辑，原草稿已保留。"),
            "DATABASE_BUSY": self.tr("数据库正在使用中，请稍后重试；本次输入已保留。"),
        }
        self.notice.setText(
            messages.get(code, self.tr("保存未完成，请检查输入后重试。")) + f" [{code}]"
        )
        if self._pending_source == "transaction" and self._pending_dialog:
            self._pending_dialog.show()
        if self._pending_source == "management":
            self.management.command_finished(False)
        if self._pending_source == "exchange":
            self.exchange.command_finished(False)
        if self._pending_source == "quick":
            self.quick.command_finished(False, code)
        self._pending_source = None

    def refresh(self) -> None:
        overview = self.queries.overview(self.today())
        for key, amount in [
            ("assets", overview.total_assets_minor),
            ("income", overview.income_minor),
            ("expense", overview.expense_minor),
        ]:
            complete = {
                "assets": overview.assets_complete,
                "income": overview.income_complete,
                "expense": overview.expense_complete,
            }[key]
            self.metrics[key].setText(
                ("¥" if overview.currency_code == "CNY" else overview.currency_code)
                + " "
                + money_text(amount, overview.currency_code)
                if complete
                else self.tr("未完整估值")
            )
        self.first_run.setText(
            self.tr("先在「账户与管理」创建账户，明确期初余额与起算日期。")
            if not overview.balances
            else self.tr(
                "总资产包含已归档账户。本月净支出 = 支出 − 本月退款；"
                "转账、期初和余额校准不计入收支。"
            )
        )
        self.form.refresh()
        preferences = self.queries.preferences()
        for combo, key in [
            (self.form.book, "default_book_id"),
            (self.form.account, "default_account_id"),
        ]:
            if combo.currentData() is None:
                self.form.load(
                    {"book_id" if key == "default_book_id" else "account_id": preferences[key]}
                )
        self.transactions.refresh()
        self.management.refresh()
        self.exchange.refresh()
        self.analysis.refresh()
        self.quick.refresh()

    def _category_drill(self, category_id: str) -> None:
        report = self.analysis.report
        if report is None:
            return
        filters = report.filters
        for combo, identifiers in (
            (self.transactions.book, filters.book_ids),
            (self.transactions.account, filters.account_ids),
            (self.transactions.tag, filters.tag_ids),
        ):
            combo.setCurrentIndex(max(0, combo.findData(identifiers[0] if identifiers else None)))
        self.transactions.category.setCurrentIndex(
            max(0, self.transactions.category.findData(category_id))
        )
        self.transactions.kind.setCurrentIndex(0)
        self.transactions.search.clear()
        self.transactions.include_deleted.setChecked(False)
        self.transactions.limit_dates.setChecked(True)
        self.transactions.start.setDate(
            QDate(filters.start_on.year, filters.start_on.month, filters.start_on.day)
        )
        self.transactions.end.setDate(
            QDate(filters.end_on.year, filters.end_on.month, filters.end_on.day)
        )
        self.transactions.reset_page()
        self._pages.setCurrentIndex(1)
        if (button := self.navigation.button(1)) is not None:
            button.setChecked(True)

    def _transaction_action(self, action: str, row: object) -> None:
        if self.bridge.busy or not isinstance(row, dict):
            return
        if self._pending_dialog and self._pending_dialog.isVisible():
            self._pending_dialog.raise_()
            self._pending_dialog.activateWindow()
            return
        identifier = str(row["id"])
        if action in {"delete", "restore"}:
            label = self.tr("删除") if action == "delete" else self.tr("恢复")
            if (
                QMessageBox.question(
                    self,
                    label,
                    self.tr("确认{action}这笔交易？账户余额会随之变化。").format(action=label),
                )
                == QMessageBox.StandardButton.Yes
            ):
                self._submit(
                    f"transaction.{action}.v1",
                    {"id": identifier, "expected_version": row["version"]},
                    source="transaction",
                )
            return
        try:
            snapshot = self.ledger.transaction(identifier)
            if action == "refund":
                dialog: OperationDialog | EditTransactionDialog | AdjustmentDialog = (
                    OperationDialog(
                        self.ledger, "refund", identifier, self.settings.time_zone, self
                    )
                )
            elif snapshot["kind"] in {"transfer", "expense_refund"}:
                operation = "transfer_edit" if snapshot["kind"] == "transfer" else "refund_edit"
                dialog = OperationDialog(
                    self.ledger, operation, identifier, self.settings.time_zone, self
                )
            elif snapshot["kind"] == "adjustment":
                dialog = AdjustmentDialog(snapshot, self)
            else:
                dialog = EditTransactionDialog(self.ledger, snapshot, self.today(), self)

            def accepted() -> None:
                if self.bridge.busy:
                    dialog.show()
                    return
                try:
                    self._pending_dialog = dialog
                    self._submit(dialog.command_type, dialog.payload(), source="transaction")
                except LedgerError as error:
                    self._failed(error.code)
                    dialog.show()

            dialog.accepted.connect(accepted)

            def rejected() -> None:
                if self._pending_dialog is dialog:
                    self._pending_dialog = None

            dialog.rejected.connect(rejected)
            self._pending_dialog = dialog
            dialog.setWindowModality(Qt.WindowModality.WindowModal)
            dialog.show()
        except LedgerError as error:
            self._failed(error.code)

    def _build_settings(self, runtime: RuntimeInfo) -> QWidget:
        page, layout = self._page(self.tr("设置"), self.tr("外观、记账时区与运行环境。"))
        form = QFormLayout()
        self.theme = QComboBox(page)
        self.theme.setObjectName("themeSelection")
        self.theme.addItem(self.tr("浅色"), "light")
        self.theme.addItem(self.tr("深色"), "dark")
        self.theme.addItem(self.tr("跟随系统"), "system")
        self.theme.setCurrentIndex(max(0, self.theme.findData(self.settings.theme)))
        self.time_zone = QComboBox(page)
        self.time_zone.setObjectName("timeZoneSelection")
        self.time_zone.addItems(sorted(available_timezones()))
        self.time_zone.setCurrentText(self.settings.time_zone)
        self.language = QComboBox(page)
        self.language.setObjectName("languageSelection")
        self.language.addItem("简体中文", "zh_CN")
        self.language.addItem("English", "en_US")
        self.language.setCurrentIndex(max(0, self.language.findData(self.settings.language)))
        self.tray_enabled = QCheckBox(self.tr("显示系统托盘图标"), page)
        self.tray_enabled.setObjectName("trayEnabled")
        self.tray_enabled.setChecked(self.settings.tray_enabled)
        self.close_to_tray = QCheckBox(self.tr("关闭主窗口时隐藏到托盘"), page)
        self.close_to_tray.setObjectName("closeToTray")
        self.close_to_tray.setChecked(self.settings.close_to_tray)
        self.hotkey_enabled = QCheckBox(self.tr("启用全局快捷键"), page)
        self.hotkey_enabled.setObjectName("hotkeyEnabled")
        self.hotkey_enabled.setChecked(self.settings.hotkey_enabled)
        self.shortcut = QLineEdit(self.settings.shortcut, page)
        self.shortcut.setObjectName("globalShortcut")
        form.addRow(self.tr("主题"), self.theme)
        form.addRow(self.tr("记账时区（IANA）"), self.time_zone)
        form.addRow(self.tr("界面语言（重启生效）"), self.language)
        form.addRow(self.tr("系统托盘"), self.tray_enabled)
        form.addRow("", self.close_to_tray)
        form.addRow(self.tr("快速记账"), self.hotkey_enabled)
        form.addRow(self.tr("全局快捷键"), self.shortcut)
        layout.addLayout(form)
        apply = QPushButton(self.tr("应用设置"), page)
        apply.setObjectName("applySettings")
        apply.clicked.connect(self._apply_settings)
        layout.addWidget(apply)
        help_button = QPushButton(self.tr("完整使用手册（离线）"), page)
        help_button.clicked.connect(
            lambda: QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(files("openledger.resources").joinpath("help/manual.html")))
            )
        )
        layout.addWidget(help_button)
        hint = QLabel(
            self.tr(
                "初始时区为 Asia/Shanghai，可在这里修改。"
                "时区变化后，未保存的自然语言草稿需要重新解析；已保存记录保留原时区。"
            ),
            page,
        )
        hint.setWordWrap(True)
        hint.setProperty("secondary", True)
        layout.addWidget(hint)
        self.ai_settings = AISettingsWidget(self.ai_store, self.credentials, page)
        self.ai_settings.configChanged.connect(lambda _config: self._ai_configuration_changed())
        self._ai_configuration_changed()
        layout.addWidget(self.ai_settings)
        data_dir = self.ai_store.data_dir
        self.updates = UpdatesWidget(
            UpdateSettingsStore(data_dir / "update-settings.json"), runtime.app_version, page
        )
        layout.addWidget(self.updates)
        layout.addWidget(self._build_environment(runtime))
        return page

    def _apply_settings(self) -> None:
        settings = DesktopSettings(
            theme=str(self.theme.currentData()),
            time_zone=self.time_zone.currentText(),
            language=str(self.language.currentData()),
            tray_enabled=self.tray_enabled.isChecked(),
            close_to_tray=self.close_to_tray.isChecked(),
            hotkey_enabled=self.hotkey_enabled.isChecked(),
            shortcut=self.shortcut.text(),
        )
        try:
            SettingsStore._validate(settings)
            if self.settings_store:
                self.settings_store.save(settings)
            if settings.time_zone != self.settings.time_zone:
                consumed = set(self._saved_spans)
                self._text_changed()
                self._saved_spans = consumed
                self.form.load({"occurred_at_utc": None, "time_zone": settings.time_zone})
                self.management.time_zone = settings.time_zone
            self.settings = settings
            self.ledger.time_zone = settings.time_zone
            self.exchange.time_zone.setText(settings.time_zone)
            self.appearance.set_selection(settings.theme)
            self.quick.set_time_zone(settings.time_zone)
            self.zone_hint.setText(self.tr("记账时区：{zone}").format(zone=settings.time_zone))
            self.refresh()
            self.notice.setText(self.tr("设置已应用。"))
            if self.desktop and not self._configure_desktop():
                self.notice.setText(
                    self.tr("设置已保存，快捷键无法注册，请更换组合后重试。")
                    + f" [{self.desktop.hotkey_error}]"
                )
        except (OSError, ValueError):
            self.notice.setText(self.tr("设置保存失败，请检查数据目录权限。"))

    def _ai_configuration_changed(self) -> None:
        self._cancel_ai()
        self.plugins.select("ai", "openai-compatible" if self.ai_settings.config.enabled else None)

    def closeEvent(self, event: QCloseEvent) -> None:
        if (
            not self._force_quit
            and self.settings.close_to_tray
            and self.desktop is not None
            and self.desktop.tray_active
        ):
            self.hide()
            event.ignore()
            return
        if self.bridge.busy:
            self._quit_when_idle = True
            self.notice.setText(self.tr("正在完成保存，完成后退出。"))
            event.ignore()
            return
        self.ai_tasks.close()
        self.updates.close_workers()
        self.quick.hide()
        if self.desktop:
            self.desktop.close()
        self.bridge.close()
        self.exchange.close_workers()
        self.analysis.close_workers()
        super().closeEvent(event)

    def request_quit(self) -> None:
        """Explicit exit waits for the shared financial command before releasing handles."""
        self._force_quit = True
        self.close()

    def enable_desktop_features(self) -> None:
        """Enable native integration only for ordinary interactive application startup."""
        if self.desktop is None:
            self.desktop = DesktopController(self, self.quick.open, self.request_quit, self)
        if not self._configure_desktop():
            self.notice.setText(
                self.tr("全局快捷键暂不可用，可使用托盘或主窗口快速记账。")
                + f" [{self.desktop.hotkey_error}]"
            )

    def _configure_desktop(self) -> bool:
        return self.desktop is not None and self.desktop.apply(
            tray_enabled=self.settings.tray_enabled,
            hotkey_enabled=self.settings.hotkey_enabled,
            shortcut=self.settings.shortcut,
        )

    def _apply_theme(self, effective: str) -> None:
        style = read_text_resource(f"themes/{effective}.qss")
        self.setStyleSheet(style)
        if hasattr(self, "analysis"):
            self.analysis.set_theme(effective)
        if hasattr(self, "quick"):
            self.quick.setStyleSheet(style)

    def _build_environment(self, runtime: RuntimeInfo) -> QWidget:
        page, layout = self._page(
            self.tr("运行环境"),
            self.tr("以下信息用于检查安装情况和报告启动问题。"),
        )
        page.setObjectName("environmentPage")
        card = QFrame(page)
        card.setObjectName("environmentDetails")
        card.setProperty("card", True)
        form = QFormLayout(card)
        form.setContentsMargins(22, 20, 22, 20)
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(18)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        rows = (
            (self.tr("应用版本"), runtime.app_version, "appVersion"),
            (self.tr("Python"), runtime.python_version, "pythonVersion"),
            (self.tr("PySide6"), runtime.pyside_version, "pysideVersion"),
            (self.tr("Qt"), runtime.qt_version, "qtVersion"),
            (self.tr("SQLite"), runtime.sqlite_version, "sqliteVersion"),
            (self.tr("数据目录"), runtime.data_directory, "dataDirectory"),
            (self.tr("数据库版本"), str(runtime.database_schema_version or "—"), "schemaVersion"),
            (self.tr("日志模式"), runtime.database_journal_mode or "—", "journalMode"),
        )
        for title, value, object_name in rows:
            label = QLabel(value, card)
            label.setObjectName(object_name)
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setTextFormat(Qt.TextFormat.PlainText)
            form.addRow(title, label)
        layout.addWidget(card)

        if runtime.sqlite_wal_supported:
            status_text = self.tr("SQLite 运行库满足项目的 WAL 版本要求。")
        else:
            status_text = self.tr(
                "当前 SQLite 运行库未达到项目的 WAL 版本要求。"
                "资金数据库采用回滚日志模式；"
                "WAL 需在运行库通过验证后启用。"
            )
        status = QLabel(status_text, page)
        status.setObjectName("sqliteStatus")
        status.setProperty("warning", not runtime.sqlite_wal_supported)
        status.setWordWrap(True)
        layout.addWidget(status)
        scope = QLabel(
            self.tr("启动时创建并校验本地数据库。默认账本与分类不包含示例资金记录。"), page
        )
        scope.setProperty("secondary", True)
        scope.setWordWrap(True)
        layout.addWidget(scope)
        layout.addStretch()
        return page
