# 资金核心接口

资金核心自阶段 4 建立，不依赖 GUI。当前入口是 `LedgerPort`，SQLite 实现为 `LedgerService`；阶段 5 接入桌面确认，阶段 6 增加批次导入，阶段 7 的快速窗口复用同一个资金 writer。服务不会从“微信支付”等渠道猜测实际扣款账户，AI 也只提供待确认草稿。

## 创建隔离演示账本

以下演示只写临时目录中的合成账目，不读取默认用户数据：

```python
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from zoneinfo import ZoneInfo

from openledger.application.dto.ledger import TransactionFields
from openledger.infrastructure.database.database import Database
from openledger.infrastructure.ledger import LedgerService

with TemporaryDirectory() as directory:
    database = Database(Path(directory).resolve() / "demo.sqlite3")
    database.initialize()
    ledger = LedgerService(database, time_zone="Asia/Shanghai")
    ledger.ensure_defaults()
    account_id = str(uuid4())
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    ledger.execute(
        str(uuid4()),
        "account.create.v1",
        {
            "id": account_id,
            "name": "演示现金",
            "account_type": "cash",
            "balance_start_on": today,
            "opening_balance_minor": 10000,
        },
    )
    book_id = str(ledger.entities("book")[0]["id"])
    category_id = str(
        next(
            item["id"]
            for item in ledger.entities("category")
            if item["transaction_kind"] == "expense"
        )
    )
    fields = TransactionFields(
        "expense",
        2500,
        account_id,
        book_id,
        category_id,
        today,
        time_zone="Asia/Shanghai",
        note="演示咖啡",
    )
    request_id, transaction_id = str(uuid4()), str(uuid4())
    first = ledger.record(fields, request_id=request_id, transaction_id=transaction_id)
    replay = ledger.record(fields, request_id=request_id, transaction_id=transaction_id)
    assert replay.replayed
    assert ledger.balances()[account_id] == 7500
```

## 命令与查询

`execute(request_id, command_type, payload)` 是唯一持久化业务边界。request_id 是一次用户保存意图的 UUID，同一意图重试沿用；修改内容需新 request_id。payload 规范化后参与 hash，expected_version 也是意图的一部分。修改使用完整替换字段，不能省略希望保留的可选值。

| 命令 | payload / 行为 |
| --- | --- |
| book/account/category/tag/payment_method.create/update/archive.v1 | 见阶段 2 契约；update/archive 必填 expected_version；account.create 必填切点并显式设定期初 |
| book.default.set.v1 / account.default.set.v1 | id、expected_version；选择活跃默认项 |
| transaction.record/update.v1 | id、TransactionFields；update 含 expected_version，只允许 income/expense |
| refund.record/update.v1 | id、RefundFields；退款继承原支出账本/分类，不能切换原支出 |
| transfer.record/update.v1 | id、TransferFields；两个不同账户，两侧金额相等，手续费另记支出 |
| transaction.delete/restore.v1 | id、expected_version；期初走专用命令，恢复重新验证依赖与切点 |
| account.opening.set.v1 | account_id、expected_account_version、balance_start_on、opening_balance_minor |
| import.commit.v1 | 批次 id、来源指纹、映射和已确认候选行；整批记录及元数据在同一资金事务内提交，失败完整回滚 |
| import.revert.v1 | 批次 id、expected_version；要求有效成员未改动且无批次外有效退款，满足条件时整批撤销 |
| account.adjust.v1 | account_id、target_balance_minor、occurred_on、reason，可提供 time_zone；仅今天新增校准 |
| adjustment.metadata.update.v1 | id、expected_version、note、reason、tag_ids；不改资金字段 |

创建/更新分类使用 kind；支付方式的 code 创建后固定。description 0–1000、note/source_text 0–4000、对象/地点 0–200、名称 1–80 字符。首版仅 CNY；金额用正整数分，流水 delta 可为负；输入框转换使用 domain.money.parse_amount。

`balances()` 返回包含归档账户的当前余额，`total_assets()` 返回精确资产总额。`entities(entity, include_archived=False)` 查询管理项，`transaction(id)` 返回独立完整快照和动态退款归属。查询在一致读事务内完成，不向调用者泄露连接或持久化可变对象；列表分页和统计在后续阶段增加。

成功结果包含提交时的 balance_changes、changed_entities、change_seq 与 data；replayed 为本次调用的传输属性，回执仍保留原提交时的余额与时刻。收到重放结果后应再次查询当前状态。失败抛 LedgerError，code 可映射为本地化消息；不给 UI 返回底层 SQLite 异常或完整审计 JSON。

## 日期、归档与依赖

业务日期不可晚于字段 time_zone 中的今天，也不可早于涉及账户的 balance_start_on；账户切点验证使用服务构造时的业务时区。exact 必须带 aware datetime 且转换后落在业务日期内；period 不保存伪造的精确时刻。所有精确时间与注入时钟先规范到 UTC 毫秒。

已有交易编辑可保留其原有归档引用，改换的引用须活跃；退款继承的原分类/账本归档不阻止退款。原支出有活跃退款时不能删原记录、迁移账本或把金额降至已退总额以下。删除退款释放额度，恢复退款重新验证；恢复原支出不自动恢复退款。

校准差额在写锁内按最新余额求得并固定保存；零差额仍保存 no_change 回执。原校准只允许修改说明/标签；修正资金差额应显式删除旧校准，再创建新校准。审计只提供本地可追溯性，不声称防恶意篡改。

`LedgerWriter` 提供单线程 FIFO worker，排队意图深复制；取消尚未开始的任务不会写入，close 等待执行中的提交完成。Qt 接入由 `presentation.commands.CommandBridge` 完成，经 queued signal 将已提交结果或安全错误码交付主线程；主窗口和快速窗口共用这一命令桥。解析和文件任务可取消，已经确认并开始的资金事务完整完成，显式退出等待提交回执后再释放资源。
