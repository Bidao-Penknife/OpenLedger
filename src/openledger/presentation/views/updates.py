"""Manual release checks with responsive cancellation and stale-result protection."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from PySide6.QtCore import QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from openledger.domain.errors import LedgerError
from openledger.infrastructure.updates import (
    UpdateResult,
    UpdateService,
    UpdateSettings,
    UpdateSettingsStore,
    validate_repository,
)
from openledger.presentation.tasks import TaskBridge


@dataclass(frozen=True, slots=True)
class _Checked:
    generation: int
    result: UpdateResult


class UpdatesWidget(QWidget):
    """An embedded settings section; construction and configuration never contact GitHub."""

    def __init__(
        self,
        store: UpdateSettingsStore,
        current_version: str,
        parent: QWidget | None = None,
        *,
        service: UpdateService | None = None,
        open_release: Callable[[str], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.service = service if service is not None else UpdateService(current_version)
        self._open_release = (
            open_release
            if open_release is not None
            else lambda url: QDesktopServices.openUrl(QUrl(url))
        )
        self.tasks = TaskBridge(self)
        self._generation = 0
        self._active_generation = -1
        self.last_result: UpdateResult | None = None
        settings = self.store.load()
        layout = QVBoxLayout(self)
        heading = QLabel(self.tr("软件更新"), self)
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        description = QLabel(
            self.tr("当前版本：%1。手动检查配置的公开 GitHub 仓库，仅查询稳定版 Release。").replace(
                "%1", current_version
            ),
            self,
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        form = QFormLayout()
        self.owner = QLineEdit(settings.owner, self)
        self.owner.setObjectName("updateRepositoryOwner")
        self.owner.setMaxLength(39)
        self.owner.setPlaceholderText(self.tr("GitHub 用户或组织名称"))
        self.repo = QLineEdit(settings.repo, self)
        self.repo.setObjectName("updateRepositoryName")
        self.repo.setMaxLength(100)
        self.repo.setPlaceholderText(self.tr("仓库名称"))
        form.addRow(self.tr("仓库所有者"), self.owner)
        form.addRow(self.tr("仓库名称"), self.repo)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.check_button = QPushButton(self.tr("检查更新"), self)
        self.check_button.setObjectName("updateCheckButton")
        self.cancel_button = QPushButton(self.tr("取消检查"), self)
        self.cancel_button.setObjectName("updateCancelButton")
        self.cancel_button.setEnabled(False)
        self.release_button = QPushButton(self.tr("打开 Release 页面"), self)
        self.release_button.setObjectName("updateReleaseButton")
        self.release_button.setEnabled(False)
        actions.addWidget(self.check_button)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.release_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.status = QLabel(self.tr("未配置发布仓库。不会自动联网检查或下载安装。"), self)
        self.status.setObjectName("updateStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        if settings.owner:
            self.status.setText(self.tr("仓库已配置。点击检查更新时才会访问 GitHub。"))
        self.owner.textChanged.connect(self._configuration_changed)
        self.repo.textChanged.connect(self._configuration_changed)
        self.check_button.clicked.connect(self.check)
        self.cancel_button.clicked.connect(self.cancel)
        self.release_button.clicked.connect(self.open_release)
        self.tasks.completed.connect(self._completed)
        self.tasks.failed.connect(self._failed)
        self.tasks.busyChanged.connect(self._busy_changed)

    @Slot()
    def _configuration_changed(self) -> None:
        self._generation += 1
        self.last_result = None
        self.release_button.setEnabled(False)
        self.tasks.cancel()
        self.status.setText(self.tr("仓库设置已更改。点击检查更新时才会保存并访问 GitHub。"))

    @Slot()
    def check(self) -> None:
        """Save the explicit repository choice and start one bounded background request."""
        if self.tasks.busy:
            return
        owner, repo = self.owner.text().strip(), self.repo.text().strip()
        try:
            if owner or repo:
                validate_repository(owner, repo)
            self.store.save(UpdateSettings(owner, repo))
        except LedgerError as error:
            self._show_error(error.code)
            return
        self._generation += 1
        generation = self._generation
        self._active_generation = generation
        self.last_result = None
        self.release_button.setEnabled(False)
        self.status.setText(self.tr("正在检查 GitHub 稳定版 Release…"))
        self.tasks.start(
            lambda cancelled: _Checked(generation, self.service.check(owner, repo, cancelled))
        )

    @Slot()
    def cancel(self) -> None:
        """Invalidate a result immediately, while cooperative cancellation closes its socket."""
        self._generation += 1
        self.tasks.cancel()
        self.status.setText(self.tr("已取消检查。"))
        self.last_result = None
        self.release_button.setEnabled(False)

    @Slot(object)
    def _completed(self, value: object) -> None:
        checked = cast(_Checked, value)
        if checked.generation != self._generation:
            return
        result = checked.result
        self.last_result = result
        if result.status == "available":
            self.status.setText(
                self.tr("发现新稳定版：%1。可打开 Release 页面查看更新。").replace(
                    "%1", result.latest_version or ""
                )
            )
        elif result.status == "up_to_date":
            self.status.setText(
                self.tr("当前版本无需更新。最新稳定版：%1。").replace(
                    "%1", result.latest_version or ""
                )
            )
        elif result.status == "no_release":
            self.status.setText(self.tr("未找到公开的稳定版 Release，请核对仓库配置。"))
        elif result.status == "ignored":
            self.status.setText(self.tr("本次返回预发布版本，已忽略。"))
        else:
            self.status.setText(self.tr("未配置发布仓库。不会自动联网检查或下载安装。"))
        self.release_button.setEnabled(result.release_url is not None)

    @Slot(str)
    def _failed(self, code: str) -> None:
        if self._active_generation == self._generation:
            self._show_error(code)

    def _show_error(self, code: str) -> None:
        messages = {
            "INVALID_UPDATE_REPOSITORY": self.tr("请填写有效的 GitHub 所有者和仓库名称。"),
            "UPDATE_TIMEOUT": self.tr("更新检查超时，请稍后重试。"),
            "UPDATE_NETWORK_FAILED": self.tr("无法连接 GitHub，请检查网络后重试。"),
            "UPDATE_RATE_LIMITED": self.tr("GitHub 暂时限制了请求，请稍后重试。"),
            "UPDATE_CANCELLED": self.tr("已取消检查。"),
            "UPDATE_SETTINGS_WRITE_FAILED": self.tr("无法保存仓库设置，请检查数据目录权限。"),
        }
        self.status.setText(messages.get(code, self.tr("更新检查返回无效响应，请稍后重试。")))
        self.last_result = None
        self.release_button.setEnabled(False)

    @Slot(bool)
    def _busy_changed(self, busy: bool) -> None:
        self.check_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    @Slot()
    def open_release(self) -> None:
        """Open only the release URL validated by the current user-initiated check."""
        result = self.last_result
        if (
            result is not None
            and result.release_url is not None
            and not self._open_release(result.release_url)
        ):
            self.status.setText(self.tr("无法打开浏览器，请稍后重试。"))

    def close_workers(self) -> None:
        """Cancel pending checks and close sockets before the main window exits."""
        self._generation += 1
        self.tasks.close()
