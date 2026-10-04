# 本地备份与恢复

阶段 4 提供 `BackupService`，使用 Python 标准库 `sqlite3` 和 ZIP 格式。测试只读取临时目录内的合成账目。归档不加密，包含完整账目、审计与幂等命令回执；请由用户选择受保护的保存位置。

## 接口

```python
from pathlib import Path

from openledger.infrastructure.backup import BackupService
from openledger.infrastructure.database.database import Database

database = Database(Path(r"D:\OpenLedgerData\database\openledger.sqlite3"))
database.initialize()
service = BackupService(database)

service.backup(
    Path(r"D:\MyBackups\2026-10-02.olbackup"),
    attachments_directory=Path(r"D:\OpenLedgerData\attachments"),
)

# 恢复到独立新目录。父目录必须已存在。
restored = service.restore(
    Path(r"D:\MyBackups\2026-10-02.olbackup"),
    Path(r"D:\OpenLedgerRestored"),
)
```

传入绝对路径。目标备份文件必须不存在，恢复目标必须为新目录或空目录。路径不能包含符号链接、Windows 目录联接或其他重解析点。

恢复 API 不覆盖正在运行的数据库，也不自动切换当前应用的数据目录。恢复成功后先关闭应用，再以 `--data-dir D:\OpenLedgerRestored` 启动；检查账本与账户后自行保留或移除旧目录。旧数据库始终保留。

## 一致性与附件

备份使用 SQLite 在线备份 API 创建独立快照，再从该快照读取附件元数据。不会直接复制活动数据库及 journal/WAL 文件。生成归档前检查 SQLite 完整性、外键、OpenLedger `application_id`、数据库版本、迁移摘要，以及流水方向/数量、转账守恒、期初切点、退款额度、分类层级、默认偏好和聚合范围等跨行资金规则。

归档只包含 `attachments` 表引用的文件，包含软删除但仍保留元数据的附件，不扫描目录中其他文件。每个附件按数据库记录验证相对路径、字节数和 SHA256。任何引用文件缺失、路径不安全或摘要不匹配都会取消整份备份；有附件记录时不能省略附件目录。

附件录入页面尚未实现，此处为后续附件存储提供完整性边界。未来实现垃圾回收时，必须同步处理已删除附件的保留策略，不能仅删除文件而保留需要恢复的元数据。

备份在目标文件同卷的临时目录写入，关闭并同步文件后发布。Windows 重命名拒绝已有目标；其他平台使用原子硬链接发布后移除临时文件，防止并发创建目标时被覆盖。失败会清理临时文件。

## 归档格式 v1

```text
2026-10-02.olbackup
├── manifest.json
├── database.sqlite3
└── attachments/<数据库记录的 relative_path>
```

`manifest.json` 为 UTF-8 JSON，包含：

| 字段 | 含义 |
| --- | --- |
| `format_version` | 当前固定为整数 `1` |
| `app_version` | 创建备份时的应用版本 |
| `schema_version` | 当前支持的数据库版本，阶段 4 为 `1` |
| `created_at_utc` | 以 `Z` 结尾的 UTC 时间 |
| `database_sha256` | 归档中数据库文件的 SHA256 |
| `database_size_bytes` | 数据库文件字节数 |
| `attachments` | `relative_path`、`size_bytes`、`sha256` 的列表 |

恢复同时核对清单、ZIP 实际内容和数据库中的附件记录。额外文件、遗漏文件、重复字段或重复路径均拒绝。清单摘要用于发现损坏，不提供对恶意重新制作归档的身份认证；本版没有签名或加密。

## 恢复边界

恢复先在目标目录同卷暂存并验证，全部通过后才发布完整数据目录：

```text
OpenLedgerRestored/
├── database/openledger.sqlite3
├── attachments/
└── restore-receipt.json
```

`restore-receipt.json` 记录归档 SHA256、数据库 SHA256、支持的 schema 版本和恢复时间。原有 `command_receipts`、`audit_events`、`change_log` 随数据库完整保留；恢复收据不伪装成一笔资金操作。

校验拒绝 ZIP 路径穿越、Windows 保留名、尾部空格/句点、目录条目、大小写冲突、符号链接条目、加密条目和不支持的压缩方式。仅接收普通文件与 Store/Deflate。读取过程中再次计算大小与摘要，CRC 错误也会导致失败。对未来的归档格式或数据库版本直接拒绝，不在恢复阶段猜测迁移方式。

默认资源限额由 `BackupLimits` 管理：数据库 512 MiB、单附件 20 MiB、清单 2 MiB、解压总量 1 GiB、最多 10,002 个文件。检查同时使用 ZIP 声明大小和实际流式读取字节数，防止压缩内容突破限制。开发者可显式提供更低的限额用于受限环境或测试。

## 验证

```powershell
uv run --locked pytest tests/integration/test_backup.py
```

测试覆盖资金及审计往返、未提交写入隔离、引用附件与软删除附件、源数据库保留、既有目标拒绝、磁盘同步故障、中文及空格路径、ZIP 路径穿越、重复路径/字段、目录联接、摘要错误、数据库识别与版本、迁移摘要、外键破坏和解压限额。
