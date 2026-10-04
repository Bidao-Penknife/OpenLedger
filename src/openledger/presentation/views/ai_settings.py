"""Explicit opt-in configuration with secrets stored only by a credential adapter."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from openledger.application.dto.ai import AIConfig
from openledger.application.ports.ai import CredentialStore
from openledger.domain.errors import LedgerError
from openledger.infrastructure.ai import AISettingsStore, validate_config
from openledger.infrastructure.credentials import credential_target


class AISettingsWidget(QWidget):
    """Configure a provider without testing a connection or reading a key on construction."""

    configChanged = Signal(object)

    def __init__(
        self, store: AISettingsStore, credentials: CredentialStore, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.credentials = credentials
        self._config = store.load()
        self.setObjectName("aiSettings")
        layout = QVBoxLayout(self)
        title = QLabel(self.tr("可选 AI 解析"))
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        description = QLabel(
            self.tr(
                "默认使用本地规则。启用后，只有点击「AI 解析」才会发送当前输入、参考日期、"
                "时区，以及可用账户、分类、支付方式的名称和标识到你配置的服务。"
                "账目历史和余额不会发送。AI 结果仍需你检查并确认。"
            )
        )
        description.setWordWrap(True)
        description.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(description)
        self.enabled = QCheckBox(self.tr("启用 AI，并同意将上述内容发送到所配置的服务"))
        self.enabled.setObjectName("aiEnabled")
        self.enabled.setChecked(self._config.enabled)
        layout.addWidget(self.enabled)
        form = QFormLayout()
        self.base_url = QLineEdit(self._config.base_url)
        self.base_url.setObjectName("aiBaseUrl")
        self.base_url.setMaxLength(2048)
        self.base_url.setPlaceholderText("https://api.openai.com/v1")
        self.model = QLineEdit(self._config.model)
        self.model.setObjectName("aiModel")
        self.model.setMaxLength(160)
        self.model.setPlaceholderText(self.tr("填写服务支持的模型名称"))
        self.key = QLineEdit()
        self.key.setObjectName("aiApiKey")
        self.key.setMaxLength(1280)
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText(self.tr("留空保留现有密钥；保存到 Windows 凭据管理器"))
        self.local_http = QCheckBox(self.tr("允许本机 HTTP 服务（仅 localhost／回环地址）"))
        self.local_http.setObjectName("aiAllowLocalHttp")
        self.local_http.setChecked(self._config.allow_local_http)
        form.addRow(self.tr("API 基础地址"), self.base_url)
        form.addRow(self.tr("模型"), self.model)
        form.addRow(self.tr("API 密钥"), self.key)
        form.addRow("", self.local_http)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.save_button = QPushButton(self.tr("保存 AI 配置"))
        self.save_button.setObjectName("saveAiSettings")
        self.delete_button = QPushButton(self.tr("删除此服务的密钥"))
        self.delete_button.setObjectName("deleteAiKey")
        actions.addWidget(self.save_button)
        actions.addWidget(self.delete_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.status = QLabel()
        self.status.setObjectName("aiSettingsStatus")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.save_button.clicked.connect(self.save)
        self.delete_button.clicked.connect(self.delete_key)

    @property
    def config(self) -> AIConfig:
        """Return the last successfully saved settings, never an unfinished form."""
        return self._config

    def _edited(self) -> AIConfig:
        return AIConfig(
            enabled=self.enabled.isChecked(),
            base_url=self.base_url.text().strip().rstrip("/"),
            model=self.model.text().strip(),
            allow_local_http=self.local_http.isChecked(),
        )

    def save(self) -> None:
        """Explicitly save a key and atomically publish non-secret configuration."""
        previous_key: str | None = None
        changed_key = False
        target = ""
        try:
            config = self._edited()
            validate_config(config)
            target = credential_target(self.store.data_dir, config)
            if self.key.text():
                previous_key = self.credentials.get(target)
                self.credentials.set(target, self.key.text())
                changed_key = True
            self.store.save(config)
        except LedgerError as error:
            if changed_key:
                try:
                    if previous_key is None:
                        self.credentials.delete(target)
                    else:
                        self.credentials.set(target, previous_key)
                except LedgerError:
                    self.status.setText(
                        self.tr("配置未保存；密钥恢复失败，请重新保存或删除该服务密钥。")
                    )
                    return
            messages = {
                "AI_INVALID_CONFIG": self.tr(
                    "请填写有效的 API 基础地址和模型；远程服务需要 HTTPS。"
                ),
                "CREDENTIAL_INVALID_KEY": self.tr("密钥格式无效，请去除空格和换行后重试。"),
                "CREDENTIAL_UNAVAILABLE": self.tr("Windows 凭据管理器不可用，配置未保存。"),
                "AI_SETTINGS_IO_ERROR": self.tr("配置文件保存失败，原配置已保留。"),
            }
            self.status.setText(messages.get(error.code, self.tr("AI 配置保存失败，请重试。")))
            return
        self.key.clear()
        self._config = config
        self.status.setText(self.tr("AI 配置已保存。只有点击 AI 解析才会连接服务。"))
        self.configChanged.emit(config)

    def delete_key(self) -> None:
        """Explicitly remove only the selected service's saved credential."""
        try:
            config = self._edited()
            validate_config(config)
            self.credentials.delete(credential_target(self.store.data_dir, config))
        except LedgerError:
            self.status.setText(self.tr("密钥删除失败，请检查服务地址和 Windows 凭据管理器。"))
            return
        self.key.clear()
        self.status.setText(self.tr("此服务的密钥已删除。再次解析前需要重新配置密钥。"))
