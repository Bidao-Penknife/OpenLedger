"""Read-only management views and validated, explicitly confirmed command forms."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, localcontext
from typing import cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from PySide6.QtCore import QDate, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from openledger.domain.currencies import CURRENCIES, currency, format_minor, rate_fraction
from openledger.domain.errors import LedgerError
from openledger.domain.money import checked_aggregate, parse_amount, validate_minor
from openledger.domain.values import normalize_text, validate_occurrence
from openledger.infrastructure.ledger import LedgerService


def _money_text(minor: int, code: str = "CNY") -> str:
    return format_minor(minor, code)


def _signed_amount(text: str, *, aggregate: bool = False, code: str = "CNY") -> int:
    """Parse a signed CNY value exactly, allowing an explicit zero balance."""
    stripped = text.strip()
    match = re.fullmatch(r"[+-]?([0-9]+)(?:\.([0-9]+))?", stripped)
    if match is None:
        raise LedgerError("INVALID_AMOUNT")
    whole = match.group(1).lstrip("0") or "0"
    if len(match.group(2) or "") > currency(code).digits:
        raise LedgerError("AMOUNT_PRECISION")
    if len(whole) > 17:
        raise LedgerError("AMOUNT_OUT_OF_RANGE")
    with localcontext() as context:
        context.prec = 32
        minor = int(Decimal(stripped) * 10 ** currency(code).digits)
    return (
        checked_aggregate(minor)
        if aggregate
        else validate_minor(minor, signed=True, allow_zero=True)
    )


def _date_editor(today: date, parent: QWidget, name: str) -> QDateEdit:
    editor = QDateEdit(parent)
    editor.setObjectName(name)
    editor.setDisplayFormat("yyyy-MM-dd")
    editor.setCalendarPopup(True)
    editor.setDate(QDate(today.year, today.month, today.day))
    return editor


def _selection(combo: QComboBox) -> str:
    value = combo.currentData()
    if not isinstance(value, str) or not value:
        raise LedgerError("MISSING_REQUIRED_FIELD")
    return value


def _select(combo: QComboBox, value: object) -> None:
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)


def _accounts(combo: QComboBox, ledger: LedgerService, *, retained: set[str] | None = None) -> None:
    for row in ledger.entities("account", include_archived=True):
        if not row["is_archived"] or str(row["id"]) in (retained or set()):
            label = str(row["name"])
            if row["is_archived"]:
                label += combo.tr("（已归档）")
            combo.addItem(label, row["id"])


class _ValidatedDialog(QDialog):
    """Keep invalid input visible; acceptance never writes to the ledger."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumWidth(440)
        self.body = QVBoxLayout(self)
        self.error = QLabel(self)
        self.error.setObjectName("dialogError")
        self.error.setWordWrap(True)
        self.error.setProperty("warning", True)

    def _finish(self) -> None:
        self.body.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel, self
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText(self.tr("保存"))
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName("confirmDialog")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(self.tr("取消"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.body.addWidget(buttons)

    def payload(self) -> dict[str, object]:
        """Return a validated command payload without side effects."""
        raise NotImplementedError

    def accept(self) -> None:
        """Accept only when required fields pass the local form validation."""
        try:
            self.payload()
        except LedgerError as error:
            self.error.setText(
                self.tr("请检查必填项、金额、日期与账户选择。错误代码：") + error.code
            )
            return
        self.error.clear()
        super().accept()


class EntityDialog(_ValidatedDialog):
    """Create or replace editable metadata, retaining optimistic entity versions."""

    def __init__(
        self,
        ledger: LedgerService,
        entity: str,
        row: dict[str, object] | None = None,
        *,
        time_zone: str = "Asia/Shanghai",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if entity not in {"account", "book", "category", "tag", "payment_method"}:
            raise ValueError("Unsupported management entity")
        self.ledger, self.entity, self.row, self.time_zone = ledger, entity, row, time_zone
        self._id = str(row["id"]) if row else str(uuid4())
        self.command_type = f"{entity}.{'update' if row else 'create'}.v1"
        self.setObjectName("entityDialog")
        self.setWindowTitle(self.tr("编辑资料") if row else self.tr("新建资料"))
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.name = QLineEdit(self)
        self.name.setObjectName("entityName")
        self.name.setMaxLength(80)
        self.name.setText(str(row["name"]) if row else "")
        form.addRow(self.tr("名称 *"), self.name)
        self.description = QPlainTextEdit(self)
        self.description.setObjectName("entityDescription")
        self.description.setMaximumHeight(85)
        self.description.setPlainText(str(row.get("description", "")) if row else "")
        if entity in {"account", "book"}:
            form.addRow(self.tr("说明"), self.description)
        self.kind = QComboBox(self)
        self.kind.setObjectName("entityKind")
        self.account_type = QComboBox(self)
        self.account_type.setObjectName("accountType")
        for label, value in [
            (self.tr("现金"), "cash"),
            (self.tr("银行卡"), "bank"),
            (self.tr("微信"), "wechat"),
            (self.tr("支付宝"), "alipay"),
            (self.tr("自定义"), "custom"),
        ]:
            self.account_type.addItem(label, value)
        today = ledger.clock().astimezone(ZoneInfo(time_zone)).date()
        self.start = _date_editor(today, self, "balanceStart")
        self.opening = QLineEdit(self)
        self.currency = QComboBox(self)
        self.currency.addItems(list(CURRENCIES))
        self.currency.setCurrentText(
            str(row["currency_code"]) if row and entity == "account" else "CNY"
        )
        self.currency.setEnabled(row is None)
        self.opening.setObjectName("openingBalance")
        self.opening.setPlaceholderText(self.tr("请输入原币金额；余额为零时填写 0"))
        if entity == "account":
            form.addRow(self.tr("账户类型"), self.account_type)
            form.addRow(self.tr("账户币种（创建后固定）"), self.currency)
            if row:
                _select(self.account_type, row["account_type"])
                hint = QLabel(
                    self.tr("期初余额请使用「修改期初」；实际余额请使用「余额校准」。"), self
                )
                hint.setWordWrap(True)
                form.addRow(hint)
            else:
                form.addRow(self.tr("开始记账日期 *"), self.start)
                form.addRow(self.tr("该日期的期初余额（账户币种） *"), self.opening)
        self.parent_category = QComboBox(self)
        self.parent_category.setObjectName("parentCategory")
        self.parent_category.addItem(self.tr("无（一级分类）"), None)
        self.color = QLineEdit(self)
        self.color.setObjectName("entityColor")
        self.color.setPlaceholderText(self.tr("可选，如 #4f8cff"))
        self.color.setText(str(row.get("color") or "") if row else "")
        if entity == "category":
            self.kind.addItem(self.tr("支出"), "expense")
            self.kind.addItem(self.tr("收入"), "income")
            if row:
                _select(self.kind, row["transaction_kind"])
            self.kind.currentIndexChanged.connect(self._load_parents)
            self._load_parents()
            if row:
                _select(self.parent_category, row["parent_id"])
            form.addRow(self.tr("分类类型"), self.kind)
            form.addRow(self.tr("上级分类"), self.parent_category)
        if entity in {"category", "tag"}:
            form.addRow(self.tr("颜色"), self.color)
        self.mapping = QComboBox(self)
        self.mapping.setObjectName("channelAccount")
        self.mapping.addItem(self.tr("不指定（记账时选择）"), None)
        if entity == "payment_method":
            retained_mapping = (
                {str(row["default_account_id"])} if row and row["default_account_id"] else set()
            )
            _accounts(self.mapping, ledger, retained=retained_mapping)
            if row:
                _select(self.mapping, row["default_account_id"])
            form.addRow(self.tr("建议付款账户"), self.mapping)
        if entity not in {"account", "book"}:
            self.description.hide()
        if entity != "account":
            self.account_type.hide()
        if entity != "account" or row:
            self.start.hide()
            self.opening.hide()
        if entity != "category":
            self.kind.hide()
            self.parent_category.hide()
        if entity not in {"category", "tag"}:
            self.color.hide()
        if entity != "payment_method":
            self.mapping.hide()
        self.body.addLayout(form)
        self._finish()

    def _load_parents(self) -> None:
        previous = self.parent_category.currentData()
        self.parent_category.clear()
        self.parent_category.addItem(self.tr("无（一级分类）"), None)
        for row in self.ledger.entities("category", include_archived=True):
            if (
                row["parent_id"] is None
                and row["transaction_kind"] == self.kind.currentData()
                and row["id"] != self._id
                and (not row["is_archived"] or self.row and row["id"] == self.row["parent_id"])
            ):
                label = str(row["name"]) + (self.tr("（已归档）") if row["is_archived"] else "")
                self.parent_category.addItem(label, row["id"])
        _select(self.parent_category, previous)

    def payload(self) -> dict[str, object]:
        """Validate metadata and preserve fields that the form does not change."""
        result: dict[str, object] = {
            "id": self._id,
            "name": normalize_text(self.name.text(), max_length=80, required=True),
            "sort_order": self.row["sort_order"] if self.row else 0,
        }
        if self.row:
            result["expected_version"] = self.row["version"]
        if self.entity in {"book", "account"}:
            result["description"] = (
                normalize_text(self.description.toPlainText(), max_length=1000) or ""
            )
        if self.entity == "account":
            result["currency_code"] = self.currency.currentText()
            result["account_type"] = _selection(self.account_type)
            if not self.row:
                start = cast(date, self.start.date().toPython())
                validate_occurrence(start, self.time_zone, now=self.ledger.clock())
                result.update(
                    balance_start_on=start,
                    opening_balance_minor=_signed_amount(
                        self.opening.text(), code=self.currency.currentText()
                    ),
                )
        if self.entity == "category":
            result.update(kind=_selection(self.kind), parent_id=self.parent_category.currentData())
        if self.entity in {"category", "tag"}:
            color = self.color.text().strip() or None
            if color is not None and re.fullmatch(r"#[0-9a-fA-F]{6}", color) is None:
                raise LedgerError("INVALID_ENVELOPE")
            result["color"] = color
        if self.entity == "payment_method":
            if not self.row:
                result["code"] = "custom_" + self._id.replace("-", "")
            result["default_account_id"] = self.mapping.currentData()
        return result


class ArchiveDialog(_ValidatedDialog):
    """Require an explicit replacement when archiving a default book or account."""

    def __init__(
        self,
        ledger: LedgerService,
        entity: str,
        row: dict[str, object],
        *,
        is_default: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.entity, self.row = entity, row
        self.command_type = f"{entity}.archive.v1"
        self.archived = not bool(row["is_archived"])
        self.needs_replacement = self.archived and is_default
        self.setWindowTitle(self.tr("归档资料") if self.archived else self.tr("恢复资料"))
        notice = QLabel(self.tr("操作对象：") + str(row["name"]), self)
        notice.setWordWrap(True)
        self.body.addWidget(notice)
        explanation = QLabel(
            self.tr("归档后将保留历史记录；账户余额仍计入总资产。")
            if self.archived
            else self.tr("恢复后可再次用于记账。"),
            self,
        )
        explanation.setWordWrap(True)
        self.body.addWidget(explanation)
        self.replacement = QComboBox(self)
        self.replacement.setObjectName("replacementDefault")
        self.replacement.addItem(self.tr("请选择新的默认项"), None)
        if self.needs_replacement:
            for item in ledger.entities(entity):
                if item["id"] != row["id"]:
                    self.replacement.addItem(str(item["name"]), item["id"])
            self.body.addWidget(QLabel(self.tr("当前项为默认项，请先选择新的默认项。"), self))
            self.body.addWidget(self.replacement)
            if self.replacement.count() == 1:
                self.body.addWidget(
                    QLabel(self.tr("请先创建另一个可用项，再归档当前默认项。"), self)
                )
        else:
            self.replacement.hide()
        self._finish()

    def payload(self) -> dict[str, object]:
        """Return an optimistic archive command with an explicit replacement."""
        result = {
            "id": self.row["id"],
            "expected_version": self.row["version"],
            "archived": self.archived,
        }
        if self.needs_replacement:
            result["replacement_default_id"] = _selection(self.replacement)
        return result


class OperationDialog(_ValidatedDialog):
    """Confirm a transfer, refund, balance adjustment or dedicated opening change."""

    def __init__(
        self,
        ledger: LedgerService,
        operation: str,
        transaction_id: str | None = None,
        time_zone: str = "Asia/Shanghai",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        commands = {
            "transfer": "transfer.record.v1",
            "transfer_edit": "transfer.update.v1",
            "refund": "refund.record.v1",
            "refund_edit": "refund.update.v1",
            "adjust": "account.adjust.v1",
            "opening": "account.opening.set.v1",
        }
        if operation not in commands:
            raise ValueError("Unsupported financial operation")
        self.ledger, self.operation, self.time_zone = ledger, operation, time_zone
        self._account_snapshots = ledger.entities("account", include_archived=True)
        self.command_type = commands[operation]
        self._id = str(uuid4())
        self._previous: dict[str, object] | None = None
        self._original: dict[str, object] | None = None
        if operation.endswith("_edit"):
            if transaction_id is None:
                raise LedgerError("MISSING_REQUIRED_FIELD")
            self._previous = ledger.transaction(transaction_id)
            expected_kind = "transfer" if operation == "transfer_edit" else "expense_refund"
            if self._previous["kind"] != expected_kind:
                raise LedgerError("FIELD_CONFLICT")
            self._id = transaction_id
            self.time_zone = str(self._previous["time_zone"])
        if operation in {"refund", "refund_edit"}:
            original_id = (
                self._previous["original_transaction_id"] if self._previous else transaction_id
            )
            if not isinstance(original_id, str):
                raise LedgerError("MISSING_REQUIRED_FIELD")
            self._original = ledger.transaction(original_id)
            if self._original["kind"] != "expense":
                raise LedgerError("ORIGINAL_EXPENSE_REQUIRED")
        today = ledger.clock().astimezone(ZoneInfo(self.time_zone)).date()
        self.setObjectName("operationDialog")
        titles = {
            "transfer": self.tr("账户转账"),
            "transfer_edit": self.tr("编辑转账"),
            "refund": self.tr("记录退款"),
            "refund_edit": self.tr("编辑退款"),
            "adjust": self.tr("余额校准"),
            "opening": self.tr("修改期初余额"),
        }
        self.setWindowTitle(titles[operation])
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.account = QComboBox(self)
        self.account.setObjectName("operationAccount")
        self.destination = QComboBox(self)
        self.destination.setObjectName("destinationAccount")
        self.account.addItem(self.tr("请选择账户"), None)
        self.destination.addItem(self.tr("请选择账户"), None)
        entries = cast(list[dict[str, object]], self._previous["entries"]) if self._previous else []
        retained = {str(item["account_id"]) for item in entries}
        _accounts(self.account, ledger, retained=retained)
        _accounts(self.destination, ledger, retained=retained)
        self.amount = QLineEdit(self)
        self.amount.setObjectName("operationAmount")
        self.amount.setPlaceholderText(self.tr("请输入原币金额"))
        self.day = _date_editor(today, self, "operationDate")
        self.note = QPlainTextEdit(self)
        self.note.setObjectName("operationNote")
        self.note.setMaximumHeight(85)
        self.reason = QLineEdit(self)
        self.reason.setObjectName("adjustmentReason")
        self.reason.setMaxLength(1000)
        if operation.startswith("transfer"):
            form.addRow(self.tr("转出账户 *"), self.account)
            form.addRow(self.tr("转入账户 *"), self.destination)
        else:
            form.addRow(
                self.tr("到账账户 *") if operation.startswith("refund") else self.tr("账户 *"),
                self.account,
            )
        amount_label = self.tr("金额（账户币种） *")
        if operation == "adjust":
            amount_label = self.tr("当前实际余额（账户币种） *")
            self.day.setEnabled(False)
        if operation == "opening":
            amount_label = self.tr("期初余额（账户币种） *")
        form.addRow(amount_label, self.amount)
        self.incoming = QLineEdit(self)
        self.incoming.setObjectName("transferIncomingAmount")
        if operation.startswith("transfer"):
            form.addRow(self.tr("实际到账金额（转入币种）"), self.incoming)
            if (
                self._previous
                and self._previous["currency_code"] != self._previous["to_currency_code"]
            ):
                self.incoming.setText(
                    _money_text(
                        cast(int, self._previous["to_amount_minor"]),
                        str(self._previous["to_currency_code"]),
                    )
                )
        form.addRow(
            self.tr("开始记账日期 *") if operation == "opening" else self.tr("日期 *"), self.day
        )
        if operation == "adjust":
            form.addRow(self.tr("校准原因 *"), self.reason)
        elif operation != "opening":
            form.addRow(self.tr("备注"), self.note)
        if not operation.startswith("transfer"):
            self.destination.hide()
        if operation != "adjust":
            self.reason.hide()
        if operation in {"adjust", "opening"}:
            self.note.hide()
        self.details = QLabel(self)
        self.details.setObjectName("operationDetails")
        self.details.setWordWrap(True)
        self.body.addWidget(self.details)
        self.body.addLayout(form)
        if self._original:
            remaining = cast(int, self._original["remaining_refundable_minor"])
            if self._previous:
                remaining += cast(int, self._previous["amount_minor"])
            self.details.setText(
                self.tr("原支出日期：")
                + str(self._original["occurred_on"])
                + self.tr("；本次可退上限：")
                + _money_text(remaining, str(self._original["currency_code"]))
                + " "
                + str(self._original["currency_code"])
            )
        elif operation == "adjust":
            self.details.setText(self.tr("按当前实际余额记录差额；原有收支记录会完整保留。"))
        elif operation == "opening":
            self.details.setText(
                self.tr("修改期初会影响余额。开始日期不得晚于该账户已有的有效交易。")
            )
            self.account.currentIndexChanged.connect(self._opening_date)
        if self._previous:
            self.amount.setText(
                _money_text(
                    cast(int, self._previous["amount_minor"]), str(self._previous["currency_code"])
                )
            )
            self.note.setPlainText(str(self._previous["note"] or ""))
            original_day = date.fromisoformat(str(self._previous["occurred_on"]))
            self.day.setDate(QDate(original_day.year, original_day.month, original_day.day))
            for entry in entries:
                if operation == "transfer_edit" and cast(int, entry["delta_minor"]) > 0:
                    _select(self.destination, entry["account_id"])
                else:
                    _select(self.account, entry["account_id"])
            if self._previous["occurrence_precision"] != "date":
                self.day.setEnabled(False)
                self.body.addWidget(
                    QLabel(self.tr("原记录的具体时间将保留；此处仅修改金额、账户与备注。"), self)
                )
        self._finish()

    def _opening_date(self) -> None:
        account_id = self.account.currentData()
        for row in self._account_snapshots:
            if row["id"] == account_id:
                start = date.fromisoformat(str(row["balance_start_on"]))
                self.day.setDate(QDate(start.year, start.month, start.day))
                self.amount.clear()
                break

    def payload(self) -> dict[str, object]:
        """Build the service envelope, retaining immutable source and exact timing."""
        account_id = _selection(self.account)
        occurred_on = cast(date, self.day.date().toPython())
        validate_occurrence(occurred_on, self.time_zone, now=self.ledger.clock())
        code = str(
            next(row["currency_code"] for row in self._account_snapshots if row["id"] == account_id)
        )
        if self.operation == "opening":
            row = next(item for item in self._account_snapshots if item["id"] == account_id)
            return {
                "account_id": account_id,
                "expected_account_version": row["version"],
                "balance_start_on": occurred_on,
                "opening_balance_minor": _signed_amount(self.amount.text(), code=code),
            }
        if self.operation == "adjust":
            if occurred_on != self.ledger.clock().astimezone(ZoneInfo(self.time_zone)).date():
                raise LedgerError("ADJUSTMENT_DATE_MUST_BE_TODAY")
            return {
                "account_id": account_id,
                "target_balance_minor": _signed_amount(
                    self.amount.text(), aggregate=True, code=code
                ),
                "occurred_on": occurred_on,
                "time_zone": self.time_zone,
                "reason": normalize_text(self.reason.text(), max_length=1000, required=True),
            }
        fields: dict[str, object] = {
            "amount_minor": parse_amount(self.amount.text(), code),
            "currency_code": code,
            "occurred_on": occurred_on,
            "time_zone": self.time_zone,
            "note": normalize_text(self.note.toPlainText(), max_length=4000) or "",
            "source": self._previous["source"] if self._previous else "manual",
            "source_text": self._previous["source_text"] if self._previous else None,
            "occurrence_precision": self._previous["occurrence_precision"]
            if self._previous
            else "date",
            "time_period": self._previous["time_period"] if self._previous else None,
            "occurred_at_utc": self._previous["occurred_at_utc"] if self._previous else None,
            "tag_ids": tuple(cast(list[str], self._previous["tag_ids"])) if self._previous else (),
        }
        if self.operation.startswith("transfer"):
            destination_id = _selection(self.destination)
            if account_id == destination_id:
                raise LedgerError("INVALID_TRANSFER")
            fields.update(from_account_id=account_id, to_account_id=destination_id)
            if self.incoming.text().strip():
                destination_code = str(
                    next(
                        row["currency_code"]
                        for row in self._account_snapshots
                        if row["id"] == destination_id
                    )
                )
                fields["to_amount_minor"] = parse_amount(self.incoming.text(), destination_code)
        else:
            assert self._original is not None
            if self._original["deleted_at_utc"] is not None:
                raise LedgerError("ORIGINAL_EXPENSE_UNAVAILABLE")
            if occurred_on < date.fromisoformat(str(self._original["occurred_on"])):
                raise LedgerError("REFUND_DATE_BEFORE_EXPENSE")
            limit = cast(int, self._original["remaining_refundable_minor"])
            if self._previous:
                limit += cast(int, self._previous["amount_minor"])
            if cast(int, fields["amount_minor"]) > limit:
                raise LedgerError("REFUND_LIMIT_EXCEEDED")
            fields.update(
                account_id=account_id,
                original_transaction_id=self._original["id"],
                payment_method_id=self._previous["payment_method_id"] if self._previous else None,
            )
        result: dict[str, object] = {"id": self._id, "fields": fields}
        if self._previous:
            result["expected_version"] = self._previous["version"]
        return result


class CurrencyDialog(_ValidatedDialog):
    """Review a display currency or a dated manual quotation before an audited write."""

    def __init__(
        self, ledger: LedgerService, *, rates: bool, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.ledger, self.rates = ledger, rates
        self.command_type = "rate.set.v1" if rates else "currency.set.v1"
        self.setWindowTitle(self.tr("手动汇率") if rates else self.tr("统计显示币种"))
        with ledger.database.read() as connection:
            self.settings = dict(connection.execute("SELECT * FROM currency_settings").fetchone())
            self.quotations = tuple(
                dict(row) for row in connection.execute("SELECT * FROM exchange_rates")
            )
        form = QFormLayout()
        self.code = QComboBox(self)
        self.code.addItems([code for code in CURRENCIES if not rates or code != "CNY"])
        self.code.setCurrentText("USD" if rates else str(self.settings["display_currency"]))
        form.addRow(self.tr("币种"), self.code)
        self.day = _date_editor(ledger.clock().date(), self, "rateDate")
        self.rate = QLineEdit(self)
        self.note = QLineEdit(self)
        if rates:
            policy = QLabel(
                self.tr(
                    "1 单位外币 = 填写数值 CNY。统计按交易日取此前最近汇率；修改汇率仅影响估值。"
                ),
                self,
            )
            policy.setWordWrap(True)
            form.addRow(policy)
            form.addRow(self.tr("生效日期"), self.day)
            form.addRow(self.tr("人民币汇率"), self.rate)
            form.addRow(self.tr("备注"), self.note)
            self.previous = QComboBox(self)
            self.previous.addItem(self.tr("新建汇率"), None)
            for index, row in enumerate(self.quotations):
                self.previous.addItem(
                    f"{row['currency_code']} · {row['effective_on']} · {row['rate_text']}", index
                )
            self.previous.currentIndexChanged.connect(self._select_rate)
            form.addRow(self.tr("已有汇率"), self.previous)
        self.body.addLayout(form)
        self._finish()

    def _select_rate(self) -> None:
        index = self.previous.currentData()
        if index is None:
            self.code.setEnabled(True)
            self.day.setEnabled(True)
            return
        row = self.quotations[int(index)]
        self.code.setCurrentText(str(row["currency_code"]))
        self.day.setDate(QDate.fromString(str(row["effective_on"]), "yyyy-MM-dd"))
        self.rate.setText(str(row["rate_text"]))
        self.note.setText(str(row["note"]))
        self.code.setEnabled(False)
        self.day.setEnabled(False)

    def payload(self) -> dict[str, object]:
        """Use the originally displayed version rather than refreshing away a conflict."""
        if not self.rates:
            return {
                "display_currency": self.code.currentText(),
                "expected_version": self.settings["version"],
            }
        rate_fraction(self.rate.text())
        day = cast(date, self.day.date().toPython())
        validate_occurrence(day, self.ledger.time_zone, now=self.ledger.clock())
        result: dict[str, object] = {
            "currency_code": self.code.currentText(),
            "effective_on": day.isoformat(),
            "rate_text": self.rate.text(),
            "note": self.note.text(),
        }
        row = next(
            (
                row
                for row in self.quotations
                if row["currency_code"] == result["currency_code"]
                and row["effective_on"] == result["effective_on"]
            ),
            None,
        )
        if row is not None:
            if self.previous.currentData() is None:
                raise LedgerError("VERSION_CONFLICT")
            result["expected_version"] = row["version"]
        return result


class ManagementPage(QWidget):
    """Read entity snapshots and emit confirmed commands to the root writer bridge."""

    commandRequested = Signal(str, object)

    def __init__(
        self,
        ledger: LedgerService,
        parent: QWidget | None = None,
        *,
        time_zone: str = "Asia/Shanghai",
    ) -> None:
        super().__init__(parent)
        self.ledger, self.time_zone = ledger, time_zone
        self._rows: tuple[dict[str, object], ...] = ()
        self._preferences: dict[str, object] = {}
        self.setObjectName("managementPage")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        title = QLabel(self.tr("账户与资料管理"), self)
        title.setProperty("heading", True)
        layout.addWidget(title)
        controls = QHBoxLayout()
        self.entity = QComboBox(self)
        self.entity.setObjectName("managementEntity")
        for label, key in [
            (self.tr("账户"), "account"),
            (self.tr("账本"), "book"),
            (self.tr("分类"), "category"),
            (self.tr("标签"), "tag"),
            (self.tr("支付渠道"), "payment_method"),
        ]:
            self.entity.addItem(label, key)
        self.entity.currentIndexChanged.connect(self.refresh)
        controls.addWidget(self.entity)
        self.include_archived = QCheckBox(self.tr("显示已归档"), self)
        self.include_archived.setObjectName("managementArchived")
        self.include_archived.toggled.connect(self.refresh)
        controls.addWidget(self.include_archived)
        for currency_title, rates in ((self.tr("显示币种"), False), (self.tr("手动汇率"), True)):
            button = QPushButton(currency_title, self)
            button.clicked.connect(
                lambda checked=False, edit_rates=rates: self._currency(edit_rates)
            )
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)
        self.status = QLabel(self)
        self.status.setObjectName("managementStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.table = QTableWidget(0, 4, self)
        self.table.setObjectName("managementTable")
        self.table.setHorizontalHeaderLabels(
            [self.tr("名称"), self.tr("类型 / 说明"), self.tr("余额（账户币种）"), self.tr("状态")]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.itemDoubleClicked.connect(lambda: self._edit())
        layout.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.create_button = QPushButton(self.tr("新建"), self)
        self.edit = QPushButton(self.tr("编辑"), self)
        self.archive = QPushButton(self.tr("归档"), self)
        self.default = QPushButton(self.tr("设为默认"), self)
        for key, button, callback in [
            ("createEntity", self.create_button, self._create),
            ("editEntity", self.edit, self._edit),
            ("archiveEntity", self.archive, self._archive),
            ("defaultEntity", self.default, self._default),
        ]:
            button.setObjectName(key)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        operations = QHBoxLayout()
        self.operation_buttons: list[QPushButton] = []
        for key, label in [
            ("transfer", self.tr("账户转账")),
            ("adjust", self.tr("余额校准")),
            ("opening", self.tr("修改期初")),
        ]:
            button = QPushButton(label, self)
            button.setObjectName(key + "Operation")
            button.clicked.connect(lambda checked=False, op=key: self._operation(op))
            operations.addWidget(button)
            self.operation_buttons.append(button)
        operations.addStretch()
        layout.addLayout(operations)
        self._pending_dialog: _ValidatedDialog | None = None
        self.refresh()

    def refresh(self) -> None:
        """Reload committed read snapshots and retain selection by stable entity ID."""
        selected = self.selected()
        identifier = selected["id"] if selected else None
        entity = _selection(self.entity)
        self._rows = self.ledger.entities(
            entity, include_archived=self.include_archived.isChecked()
        )
        self._preferences = self.ledger.preferences()
        balances = self.ledger.balances() if entity == "account" else {}
        self.table.clearContents()
        self.table.setRowCount(len(self._rows))
        labels = {
            "cash": self.tr("现金"),
            "bank": self.tr("银行卡"),
            "wechat": self.tr("微信"),
            "alipay": self.tr("支付宝"),
            "custom": self.tr("自定义"),
            "expense": self.tr("支出"),
            "income": self.tr("收入"),
        }
        default_id = self._preferences.get("default_" + entity + "_id")
        for index, row in enumerate(self._rows):
            detail = labels.get(str(row.get("account_type", row.get("transaction_kind"))), "")
            if not detail:
                detail = str(row.get("description", "") or "")
            status = self.tr("已归档") if row["is_archived"] else self.tr("可用")
            if row["id"] == default_id:
                status += self.tr(" · 默认")
            values = [
                str(row["name"]),
                detail,
                str(row["currency_code"])
                + " "
                + _money_text(balances.get(str(row["id"]), 0), str(row["currency_code"]))
                if entity == "account"
                else "",
                status,
            ]
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(value))
            if row["id"] == identifier:
                self.table.selectRow(index)
        if entity == "account":
            from openledger.infrastructure.queries import LedgerQueries

            overview = LedgerQueries(self.ledger.database).overview(
                self.ledger.clock().astimezone(ZoneInfo(self.time_zone)).date()
            )
            total = (
                overview.currency_code
                + " "
                + _money_text(overview.total_assets_minor, overview.currency_code)
                if overview.assets_complete
                else self.tr("未完整估值，请补充手动汇率。")
            )
            self.status.setText(self.tr("总资产（含已归档账户）：") + total)
        elif not self._rows:
            self.status.setText(self.tr("还没有资料，点击「新建」开始。"))
        else:
            self.status.setText(self.tr("归档会保留历史交易，资料变更不会改写历史金额。"))
        self.default.setVisible(entity in {"book", "account"})
        for button in self.operation_buttons:
            button.setVisible(entity == "account")
            button.setEnabled(bool(self.ledger.entities("account")))
        self._selection_changed()

    def selected(self) -> dict[str, object] | None:
        """Return the snapshot selected in the table, or no selection."""
        row = self.table.currentRow()
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def _selection_changed(self) -> None:
        row = self.selected()
        self.edit.setEnabled(row is not None)
        self.archive.setEnabled(row is not None)
        self.archive.setText(self.tr("恢复") if row and row["is_archived"] else self.tr("归档"))
        self.default.setEnabled(row is not None and not row["is_archived"])

    def _confirm(self, dialog: _ValidatedDialog, command_type: str) -> None:
        if self._pending_dialog is not None:
            dialog.deleteLater()
            self._pending_dialog.show()
            self._pending_dialog.raise_()
            self._pending_dialog.activateWindow()
            return
        self._pending_dialog = dialog
        dialog.accepted.connect(lambda: self._emit_dialog(dialog, command_type))
        dialog.rejected.connect(lambda: self._dismissed(dialog))
        dialog.exec()

    def _emit_dialog(self, dialog: _ValidatedDialog, command_type: str) -> None:
        try:
            self.commandRequested.emit(command_type, dialog.payload())
        except LedgerError as error:
            dialog.error.setText(self.tr("资料已保留，请检查输入后重试。错误代码：") + error.code)
            dialog.show()

    def _dismissed(self, dialog: _ValidatedDialog) -> None:
        if self._pending_dialog is dialog:
            self._pending_dialog = None
        dialog.deleteLater()

    def command_finished(self, success: bool) -> None:
        """Retain confirmed fields after a failed commit so the user can retry."""
        if self._pending_dialog is None:
            return
        if success:
            self._pending_dialog.deleteLater()
            self._pending_dialog = None
        else:
            self._pending_dialog.error.setText(
                self.tr("保存未完成，资料已保留。请检查提示后重试。")
            )
            self._pending_dialog.show()
            self._pending_dialog.raise_()
            self._pending_dialog.activateWindow()

    def _create(self) -> None:
        dialog = EntityDialog(
            self.ledger, _selection(self.entity), time_zone=self.time_zone, parent=self
        )
        self._confirm(dialog, dialog.command_type)

    def _currency(self, rates: bool) -> None:
        dialog = CurrencyDialog(self.ledger, rates=rates, parent=self)
        self._confirm(dialog, dialog.command_type)

    def _edit(self) -> None:
        row = self.selected()
        if row:
            dialog = EntityDialog(
                self.ledger, _selection(self.entity), row, time_zone=self.time_zone, parent=self
            )
            self._confirm(dialog, dialog.command_type)

    def _archive(self) -> None:
        row = self.selected()
        if row:
            entity = _selection(self.entity)
            dialog = ArchiveDialog(
                self.ledger,
                entity,
                row,
                is_default=self._preferences.get("default_" + entity + "_id") == row["id"],
                parent=self,
            )
            self._confirm(dialog, dialog.command_type)

    def _default(self) -> None:
        row = self.selected()
        if row and not row["is_archived"]:
            self.commandRequested.emit(
                _selection(self.entity) + ".default.set.v1",
                {"id": row["id"], "expected_version": row["version"]},
            )

    def _operation(self, operation: str) -> None:
        dialog = OperationDialog(self.ledger, operation, time_zone=self.time_zone, parent=self)
        row = self.selected()
        if row and not row["is_archived"]:
            _select(dialog.account, row["id"])
        self._confirm(dialog, dialog.command_type)
