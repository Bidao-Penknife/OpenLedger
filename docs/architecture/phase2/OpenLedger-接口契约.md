# OpenLedger：阶段 2 接口契约

日期：2026-10-02（Asia/Shanghai）  
状态：详细设计，供本阶段交叉评审；下列接口和代码块是待实现的契约，不是已经可调用的 API。  
范围：进程内 Python 应用接口、资金用例、任务与错误、解析/插件/同步预留。首版不启动 HTTP 服务；GUI、快速窗口和未来 CLI 都复用同一应用层。

## 1. 边界与版本

领域和应用 DTO 不依赖 PySide6、SQLAlchemy 或 HTTP SDK。基础设施通过 `Protocol` 实现数据库、时钟、网络、文件和系统服务。表现层负责显式保存操作与字段确认；应用层重新校验输入，不把 GUI 的校验当作数据保证。

契约版本采用独立的 `contract_version=1`，与应用版本、数据库迁移版本、交换文件 `format_version`、插件 `api_version` 分开。新增可选字段需有默认值；改变金额单位、必填性或业务含义是契约破坏性变更。v1.0 前允许经 ADR 调整，不能静默修改已发布交换格式。

`command_type` 是稳定字符串，例如 `transaction.record.v1`。Python 枚举、字段和 ID 不使用中文 UI 文案；显示名称通过翻译机制生成。JSON 用于回执/文件/未来同步时，金额使用十进制整数字符串，避免经过 JavaScript 等环境后丢失精度；进程内金额类型始终是 `int`。

## 2. 公共类型与单位

| 类型 / 字段 | Python 类型 | 单位 / 格式 | 可空与规则 |
| --- | --- | --- | --- |
| `EntityId` / `RequestId` | `UUID` | 客户端生成 UUID；持久化规范化小写文本 | 不可空；不以名称代替 ID |
| `CurrencyCode` | `Literal["CNY"]` | ISO 货币代码 | 不可空；首版只接受 CNY |
| `MoneyMinor` | `int` | CNY 分 | 拒绝 `bool`、浮点数；单事件绝对值上限 `99_999_999_999_999` 分 |
| 正交易金额 | `int` | 分 | `1 <= amount_minor <= 99_999_999_999_999`；方向由 kind 决定 |
| 有符号余额 / 期初 / 校准差额 | `int` | 分 | 可为负；零期初和零差额有专门规则；禁止浮点推算 |
| `BusinessDate` | `date` | `YYYY-MM-DD` | 不可空；资金事件不能晚于应用时钟在用户时区的当天 |
| `UtcInstant` | 带时区 `datetime` | UTC；JSON 固定 24 字符 `YYYY-MM-DDTHH:MM:SS.sssZ` | 明确字段可空；禁止 naive datetime |
| `TimezoneId` | `str` | IANA 标识，如 `Asia/Shanghai` | 不可空；示例不是固定全球时区 |
| `EntityVersion` | `int` | 正整数，自 1 起 | 编辑/删除/恢复传 `expected_version`；每次实体变更加 1 |
| `ChangeSequence` | `int` | 本地单调修改游标 | 不是跨设备全局版本 |
| `OccurrencePrecision` | 枚举 | `date / period / exact` | 精确时间与精度必须匹配 |
| `TimePeriod` | 枚举或 `None` | `morning / noon / afternoon / evening / night` | 仅模糊时段；不虚构具体时刻 |
| `TransactionKind` | 枚举 | `income / expense / expense_refund / transfer / opening / adjustment` | 保存前必须有明确类型 |
| `Source` | 枚举 | `manual / local_rule / ai_assisted / import / system` | 表示创建来源；以后编辑不改原始来源 |
| 文本 | `str` | Unicode，NFC，去首尾空白 | 名称 1–80 字符；备注 0–4000；对象/商家/地点 0–200；空可选字段规范化为 `None` |

金额输入先以字符串构造 `Decimal`，拒绝指数表达式、超两位小数、无穷、NaN、负数收入/支出及超范围值，再转为整数分。规则解析可以识别明确的“1.2万”，但必须先精确归一化；金额输入框本身不接受含糊的量级推测。数据库字段不得保存 float。

UTC 持久化/回执格式固定 24 字符；注入时钟归一化到毫秒，精确输入更细的精度在进入 DTO 前明确规范化。可空 DTO 备注/description 对应 Schema 的 NOT NULL 文本时映射为空串，读取为空时还原 None；数据库没有 NULL 并不意味着 UI 必须显示空串。description 上限 1000、note 上限 4000，与 Schema 一致。

所有资金日期同时校验涉及账户的 `balance_start_on`：切点当日允许入账，早于切点拒绝。零期初账户仍有切点。应用采用注入的时钟和用户配置的 IANA 时区；Windows 构建锁定 `tzdata`，由系统时区映射或用户选择获得 IANA 标识，不能假设 Windows 自带 IANA 数据。

统计和余额采用精确整数聚合；若 SQL `SUM` 发生 integer overflow，使用 Python int 流式聚合再检查最终范围，不使用 `TOTAL` 或浮点降精度。单事件范围与账户余额/聚合范围分开；结果须检查 signed int64 边界，超界返回 `AGGREGATE_OUT_OF_RANGE`，不能截断或显示错误的资产值。进入持久化的任何差额仍受单事件上限约束。

## 3. 通用命令、幂等与回执

### 3.1 MutationEnvelope

| 字段 | 类型 | 可空 | 含义 |
| --- | --- | --- | --- |
| `request_id` | `RequestId` | 否 | 一次用户业务意图的 ID；超时重试沿用，用户改内容后新建 |
| `command_type` | `str` | 否 | 稳定用例名和版本 |
| `contract_version` | `int` | 否 | 固定为 1 |
| `payload` | 对应命令 DTO | 否 | 已规范化字段、完整 required 字段、expected_version |
| `trace_id` | `UUID` | 是 | 脱敏诊断关联；不影响语义和 payload hash |

`payload_hash` 由应用层计算，调用方不能提供可信 hash。规范化字段后，以 UTF-8、稳定键排序、无多余空白的 JSON 编码 `{command_type, contract_version, payload}` 计算 SHA-256。金额编码为整数字符串，UUID、日期和 UTC 时间规范化；集合字段排序去重，无序字典排序，有序行序列保持顺序。`request_id`、trace、当前执行时刻不进入 hash；`expected_version` 是用户意图的一部分，进入 hash。v1 使用同一 canonical encoder，不直接对任意用户 JSON 字符串求 hash。

所有**持久化业务 mutation**共用 `command_receipts`，包括账户/账本/分类管理、记账、编辑、删除、恢复、导入提交/撤销、期初与校准。表中保存全局唯一 `request_id`、`command_type`、`payload_hash`、`result_json`、提交时间及 result_version（回执编码版本，当前为 1）；命令契约版本进入 payload_hash，交易表不保留冗余 request_id。回执和业务变更在同一事务提交。

处理顺序：验证信封与规范化编码 → 取得写事务 → 查询 receipt → 若相同 type/hash 返回原结果 → 若 ID 已用于不同 type/hash 返回 `IDEMPOTENCY_KEY_REUSED` → 对首次执行进行最新状态/版本/资金校验 → 写入业务、审计、change_log 和 receipt → commit → 发出成功事件。

相同请求重试必须先检查 receipt，再检查当前 entity version，保证已提交编辑的重试可以返回原成功结果。返回的余额是**该次提交时**的余额，之后可能已发生其他交易；UI 收到重放结果后重新查询当前状态。失败与回滚不写成功 receipt；成功的无变更结果（例如校准差额为零）也保存 receipt，重试不得在以后余额变化后变成一笔新校准。正常功能不清理 receipt，完整备份和恢复包括它。

配置文件写入、系统热键注册、凭据保存、备份/报告文件任务不属于 SQLite 业务事务；它们使用独立任务 ID 和原子文件/平台适配器，不声称 receipt 可以令数据库与外部系统跨资源原子提交。

### 3.2 MutationResult / Receipt

| 字段 | 类型 | 可空 | 说明 |
| --- | --- | --- | --- |
| `request_id` | UUID | 否 | 原请求 |
| `command_type` | str | 否 | 原命令 |
| `outcome` | `applied / no_change` | 否 | 提交的业务结果 |
| `committed_at_utc` | UtcInstant | 否 | commit 对应的提交时刻 |
| `entity_ids` | tuple[UUID, ...] | 否 | 创建/操作的主实体，可为空 |
| `changed_entities` | tuple[EntityRevision, ...] | 否 | `{entity_type, id, version, operation}` |
| `balance_changes` | tuple[BalanceChange, ...] | 否 | 每账户 `{account_id, before_minor, after_minor, currency_code}`；管理操作为空 |
| `change_seq` | int | 否 | 本事务修改日志高水位；no_change 取当前值 |
| `data` | 对应用例结果 DTO | 否 | 如交易详情、批次行数、无变更原因 |

`replayed: bool` 属于本次调用的外层传输结果，不改写持久化的原始 receipt。资金成功只在 commit 成功后报告；worker 结束、写了交易头或发出了 signal 均不等于成功。

## 4. 核心 DTO

### 4.1 账户与账本

| DTO | 字段与类型 | 可空 / 约束 |
| --- | --- | --- |
| `AccountSummary` | `id: UUID; name: str; account_type: cash/bank/wechat/alipay/custom; currency_code: CNY; balance_start_on: date; balance_minor: int; is_archived: bool; version: int` | 全部非空；余额包含有效历史与归档账户；余额对应 `data_revision` |
| `BookSummary` | `id: UUID; name: str; description: str\|None; currency_code: CNY; is_archived: bool; version: int` | 只有 description 可空 |
| `CategorySummary` | `id: UUID; name: str; kind: income/expense; parent_id: UUID\|None; sort_order: int; is_archived: bool; version: int` | parent 可空；仅一层父子，无循环；种类必须与父相同 |
| `PaymentMethodSummary` | `id: UUID; code: str; name: str; default_account_id: UUID\|None; is_archived: bool; version: int` | default_account_id 可空，属于用户映射；code 稳定；独立于账户 |
| `TagSummary` | `id: UUID; name: str; color: str\|None; is_archived: bool; version: int` | color 可空，仅许可颜色值；标签 ID 不从名称生成 |

余额不作为 `AccountUpdate` 的可编辑字段。期初和校准通过专用命令完成。默认账户/渠道映射是显式用户设置，`wechat` 渠道不自动等于 `wechat` 账户。

### 4.2 交易输入

`TransactionFields` 是收入/支出创建和完整替换编辑的公共字段：

| 字段 | 类型 | 可空 | 约束 |
| --- | --- | --- | --- |
| `kind` | income / expense | 否 | 退款/转账/期初/校准使用专门命令 |
| `amount_minor` | int | 否 | 正金额，分 |
| `currency_code` | CNY | 否 | 与账户、账本一致 |
| `account_id` | UUID | 否 | 实际收款/扣款账户 |
| `book_id` | UUID | 否 | 直接归属账本 |
| `category_id` | UUID | 否 | 分类 kind 与交易 kind 一致 |
| `payment_method_id` | UUID | 是 | 渠道，与账户独立 |
| `occurred_on` | date | 否 | 业务日期、切点和未来日期校验 |
| `occurred_at_utc` | UtcInstant | 是 | 只在确有精确时间时保存 |
| `time_zone` | IANA str | 否 | 精确时间换算应落在 occurred_on |
| `occurrence_precision` | 枚举 | 否 | date/period 时 UTC 时间为空；exact 时非空 |
| `time_period` | 枚举 | 是 | period 时必填，其他精度为空 |
| `counterparty / merchant / location / note` | str | 是 | 不从“火锅”等词虚构店名或地址 |
| `tag_ids` | tuple[UUID, ...] | 否 | 空 tuple 表示无标签，去重 |
| `source` | Source | 否 | 创建来源；编辑沿用原值 |
| `source_text` | str | 是 | 本次原文可保留，最多 4000 字符；不送入普通导出/诊断；编辑保持原始来源文本 |

编辑使用完整替换 DTO，避免把“字段缺省”和“用户清空”混为一谈。快照包含 `transaction_id` 与 `expected_version`；应用层读取原交易后重新验证。已创建交易的 kind 固定，收入、支出、退款、转账之间不转换；类型错误需要删除原记录并新建正确类型。若原支出有活跃退款则先处理依赖，不能通过删除/新建绕过退款约束。

`RefundFields` 包括 `original_transaction_id: UUID`、`amount_minor: int`、`currency_code: CNY`、`account_id: UUID`、退款业务日期/时间字段、可空渠道与备注、标签及创建来源字段。`book_id` 和 `category_id` 不由调用方填写，数据库必须为 NULL，查询时从原 expense 继承；币种必须与原支出一致。退款到账账户可不同于原扣款账户；业务日期必须不早于原支出业务日期。用户草稿可以展示继承名称，但不能将显示值作为独立退款归属存入。

`TransferFields` 包括 `from_account_id / to_account_id: UUID`、`amount_minor: int`、`currency_code: CNY`、业务日期/时间字段、`note: str|None`、`tag_ids`及创建来源字段；两个账户不同，金额两侧相等，账本/分类/原交易字段为空。手续费使用另外的 expense，不隐含在转账中。

### 4.3 查询交易快照

`TransactionDetail` 返回 `id, kind, amount_minor, currency_code, source, version, created_at_utc, updated_at_utc, deleted_at_utc`，所有业务日期/描述/标签字段，以及 `entries: tuple[AccountEntryView, ...]`。流水为 `{id, account_id, delta_minor}`，不单独软删除。

同时区分：

- `stored_book_id / stored_category_id: UUID|None`：数据库直接归属；退款为 None。
- `effective_book / effective_category: Summary|None`：用于显示、筛选与统计的有效归属；退款来自原支出，转账等为 None。
- `original_transaction_id: UUID|None`：仅退款非空。
- `active_refunded_minor: int` 与 `remaining_refundable_minor: int`：仅 expense 有值；在同一查询快照计算。
- `has_active_refunds: bool`：决定原支出迁移账本/删除限制；`has_refund_history: bool` 只提示存在历史关联，已删除退款不阻止原支出删除/迁移。
- `available_actions: tuple[Action, ...]` 与 `action_blockers`：只作为 UI 提示；提交命令仍重新校验。

不返回 API 密钥、完整审计 JSON 或底层 SQLAlchemy 行给 GUI。

## 5. 命令目录与事务行为

所有下列命令除注明外包在 `MutationEnvelope` 中。`expected_version` 是目标实体已加载时的版本；不存在实体和旧版本分别报告，不采用最后写入者自动覆盖。

| command_type | payload 必填字段 | 专有约束 / 结果 |
| --- | --- | --- |
| `book.create.v1` | `id, name, currency_code, description, sort_order` | description 可空；sort_order 默认为 0；名称冲突不覆盖历史实体 |
| `book.update.v1` | `id, expected_version, name, description, sort_order` | 被引用账本不能物理删；货币不可改 |
| `book.archive.v1` | `id, expected_version, archived: bool` | 默认账本归档前需选择另一个活跃默认值 |
| `account.create.v1` | `id, name, account_type, currency_code, description, sort_order, balance_start_on, opening_balance_minor` | description 可空、sort_order 默认 0；新账户与非零期初一起提交；零期初仅保存切点 |
| `account.update.v1` | `id, expected_version, name, account_type, description, sort_order` | 不修改权威余额、币种或切点 |
| `account.archive.v1` | `id, expected_version, archived: bool` | 不影响资产总额；默认账户需先替换 |
| `account.opening.set.v1` | `account_id, expected_account_version, balance_start_on, opening_balance_minor` | 专用原子期初/切点命令，见下文 |
| `account.adjust.v1` | `account_id, target_balance_minor, occurred_on, reason` | 写锁内求最新余额；差额固定；为零返回 no_change |
| `category.create/update/archive.v1` | 创建 `id,name,kind,parent_id,sort_order,color`；完整更新 `id,expected_version,name,kind,parent_id,sort_order,color`；归档 `id,expected_version,archived` | 有历史引用后 kind 不可改；不允许循环或三级分类 |
| `tag.create/update/archive.v1` | 创建 `id,name,color,sort_order`；完整更新 `id,expected_version,name,color,sort_order`；归档 `id,expected_version,archived` | 改名不改交易关联 |
| `payment_method.create/update/archive.v1` | 创建 `id,code,name,default_account_id,sort_order`；完整更新 `id,expected_version,name,default_account_id,sort_order`；归档 `id,expected_version,archived` | code 创建后不可改，归档保留历史；默认账户映射必须指向活跃同币种账户 |
| `transaction.record.v1` | `id, fields: TransactionFields` | 一条收入/支出流水 |
| `transaction.update.v1` | `id, expected_version, fields` | kind 与 source 必须保持；全量重建流水；退款限制优先；只用于income/expense |
| `refund.record.v1` | `id, fields: RefundFields` | 校验原支出及累计有效退款 |
| `refund.update.v1` | `id, expected_version, fields: RefundFields` | 原支出引用不可切换；除当前退款后重新求累计额度 |
| `transfer.record.v1` | `id, fields: TransferFields` | 两侧流水原子提交，合计为零 |
| `transfer.update.v1` | `id, expected_version, fields: TransferFields` | 两侧同时替换，不允许改单侧 |
| `adjustment.metadata.update.v1` | `id, expected_version, note, reason, tag_ids` | 仅校准说明/标签；不改金额、账户、日期、校准快照 |
| `transaction.delete.v1` | `id, expected_version` | 软删除；opening 走专用命令；有活跃退款的原支出禁止单独删除 |
| `transaction.restore.v1` | `id, expected_version` | 完整重校验；恢复原支出不自动恢复其退款 |
| `import.commit.v1` | `batch_id, preview_id, preview_revision, accepted_rows, source_fingerprint` | 所选行全通过才提交；一次批次、一次回执 |
| `import.undo.v1` | `batch_id, expected_batch_version, expected_member_versions` | 组内先退款后原支出；外部活跃关联退款或被后续编辑则冲突，整批不变 |

归档只停用新录入选项；历史快照和总资产仍包含归档实体。历史编辑和恢复可保留原有的归档账户/账本/分类/标签参照，但改换的新参照必须活跃；仍完整重验金额、日期切点、币种、版本和退款等约束。新建退款要求活跃到账账户和未删除的原支出，继承的原分类/账本无需因归档而取消退款。此规则由用例统一执行，不能让快速窗口绕过。

### 5.1 记账、编辑与退款

1. 规范化字段，生成幂等 hash；进入单一 writer 队列，在安全点接受取消。
2. 开始短写事务，先查 receipt。读取目标交易、涉及账户、账本、分类、标签及最新版本。
3. 校验正金额、CNY、精度、业务日期/时区、切点、归档状态、分类 kind 与外键引用。
4. 退款读取未删除原 expense，按本次写入后活跃退款总额校验额度，退款日期不得早于原支出；不得把退款当收入。原支出有活跃退款时禁止迁移账本或单独删除；已创建 kind 始终不可变；活跃退款存在时不得把原金额降至已退总额以下，也不得改原日期导致某个活跃退款早于原支出。
5. 构造规定数量/方向的完整流水。编辑替换原交易流水，软删除改变有效性；不对缓存余额做补丁加减。
6. 校验受影响账户余额与聚合范围。写交易/流水/标签、版本、审计、修改日志和 receipt；任一步失败整体回滚。
7. commit 成功后发送 `MutationCommitted`；GUI 依据 changed_entities 刷新相关查询，展示回执。

删除退款释放额度；恢复退款重新验证原支出仍有效、币种、切点、日期先后和额度。只有已删除退款关联时，原支出可以删除或迁移账本；将来恢复退款时重新从原支出取得有效归属并重验，不能恢复到旧的账本副本。已删除退款不自动随原支出恢复。批次撤销可以在一个事务中显式处理原支出及其组内退款；只允许经过专用组操作验证，不为普通单笔删除开放绕过开关。

### 5.2 期初与校准

账户创建、`SetAccountOpening` 和期初交易修改属于一个聚合用例：账户保存切点，非零期初保存唯一活跃 opening，opening 的业务日等于切点、流水符号按期初余额。零期初不生成零金额交易，但切点仍有效。通用交易编辑/删除/恢复不操作 opening；设零时对旧 opening 做可追溯处理，不能丢掉审计。

改变切点前，检查除 opening 外所有活跃资金事件均不早于新切点。向更早日期移动可能允许补录历史；必须提醒重新核对期初是否已包含这些历史资金。系统不猜测应扣除多少期初，也不自动重算已有校准。期初与切点改变后增加账户版本；提交命令使用 `expected_account_version` 防旧对话框覆盖。

`AdjustBalance` 在取得写锁后重新计算当前全历史余额，`delta = target - current`，目标须在 signed int64 范围，差额绝对值须在单事件范围。首版新增校准日必须是当前业务日，避免对历史快照的含义产生歧义；记录 `balance_before_minor, balance_target_minor` 与固定差额，流水承载有符号 delta，交易金额为其绝对值。差额零时写 no_change receipt，不创建交易。校准后补录/编辑历史会自然改变当前余额，固定差额不重算，UI 提醒复核。余额为负是有效结果，不自动阻止。

已创建 adjustment 的金额、日期、账户与快照不可通用编辑；仅可通过 metadata 命令改备注、原因或标签。修正差额先显式删除旧校准，再按最新当前余额新建今天的校准，UI 展示两步各自资金影响；不在已有交易中悄悄重算。恢复旧 adjustment 保留其原日期/固定差额并重验切点等资金约束，不要求历史校准日等于今天，也不重算旧 target。

### 5.3 导入预览与提交

`ImportPreviewRequest` 包含文件引用、format、列映射、默认账本/账户、时区和编码；响应为 `preview_id, preview_revision, file_digest, mapping_hash, rows`。每行包含 `source_row_number, normalized_draft, issues, exact_duplicate_key, possible_duplicate_ids`。Excel 不执行宏/公式；不读取整个数据库给导入适配器。

严格同源行指纹为 `(import_file_digest, import_mapping_hash, import_source_row)`，mapping hash 必须包含规范化映射、目标账本/账户、时区、format_version 和固定的规范化规则版本；软删除后指纹仍保留，重复导入不能绕开删除创建副本。同一个文件改了目标映射是新的导入上下文，仍显示模糊重复候选。外部稳定流水 ID 的唯一键为 `(external_source, external_transaction_id)`；external_source 是规范化 namespace，至少含 provider 和 external_account_id，不能把不同银行卡的同编号流水当成重复。严格唯一键永久包含软删除记录。

预览令牌绑定源摘要和映射摘要；源文件/映射变更后必须重扫。预览不是资金锁，提交时重新检查引用实体、切点、退款额度、稳定外部流水 ID 和所有金额约束。模糊重复仅提示；用户明确选中时允许两笔相似真实消费。提交可包含已确认行集合，但不能在事务内等待用户决定。校验失败返回对应行的全部可定位问题；资金写入失败则整批回滚，不展示部分成功。

批次撤销只软删除未被后续编辑的批次成员；成员版本必须与导入提交版本一致。批次内退款/原支出按依赖顺序删除；存在批次外活跃退款关联、成员已编辑/删除等情况先显示冲突，不自动删用户后来创建的数据。批次外已删除退款不阻止原支出随批次软删除；以后恢复该退款仍需先恢复原支出并通过完整校验。重试以同 request_id 取得原批次结果。

## 6. 查询、分析与只读接口

查询不使用 mutation receipt，但携带 `query_id` 与 UI generation。一个响应中的数据在同一短只读快照计算，返回 `data_revision`（change_log 高水位）和 `generated_at_utc`。没有固定长读事务跨越用户翻页。

| 查询 | 参数 | 返回与约束 |
| --- | --- | --- |
| `ListBooks/ListAccounts/ListCategories/ListTags` | `include_archived: bool` | summary 集合；资产汇总独立于列表隐藏选项 |
| `GetTransaction` | `id, include_deleted` | detail；不存在和已删除分别报告 |
| `ListTransactions` | `TransactionFilter, page_size=50, cursor=None` | `items, next_cursor, data_revision`；上限 200 |
| `GetAccountLedger` | `account_id, start_on, end_on, cursor` | 按业务日期及稳定次序的流水和期初说明；running balance 口径明确 |
| `GetAssets` | `as_of: date|None` | 全局、包含归档、按币种总计；as_of 前无有效基准的账户标为未知，不显示零 |
| `GetPeriodSummary` | 日期范围、账本/分类/账户/标签筛选 | income、gross expense、refund、net expense、surplus、计数 |
| `GetMonthlyTrend` | 起止月份、同一过滤条件 | 每月全字段，空月补零 |
| `GetCategoryBreakdown` | 同一过滤条件 | 毛支出占比、退款额、净支出；零分母不画占比 |
| `GetExpenseRanking` | 过滤、维度、metric: amount/count、limit<=100 | 排序稳定；退款单列，不误称收入 |
| `BuildReportData` | 过滤、对比期、locale、report options | 只读数字/图表语义/文本规则 DTO；同一 DTO 供 UI/PDF/PNG |

`TransactionFilter` 的类型与缺省：`book_ids/account_ids/category_ids/tag_ids: tuple[UUID,...]=()`，`kinds: tuple[TransactionKind,...]=()`，`start_on/end_on: date|None`，`text: str|None`（上限 200）、`include_deleted=False`。空 ID 集合表示不限；日期区间采用半开范围 [start_on, end_on)，start_on 必须早于 end_on；UI 用户选择的结束日（含）转换为次日 end_on。标签首版按“包含任意所选标签”匹配；将来 all 模式另加显式参数。退款按有效继承的账本/分类过滤，账户按其实际入账流水匹配。

默认交易顺序 `occurred_on DESC, created_at_utc DESC, id DESC`。cursor 包含该排序键、filter hash 与 data_revision，封装为不可解释的应用令牌，不用 SQL offset 模拟稳定游标。数据更新导致旧 cursor 过期时返回 `QUERY_SNAPSHOT_CHANGED`，UI 重新加载并保持筛选；不静默漏行。排序可在后续添加枚举，首版不接受调用方 SQL 片段。

账户流水 running balance 采用全账户资金口径：查询起点前的有效期初和全部流水形成 opening balance，再按 `occurred_on ASC, created_at_utc ASC, id ASC` 累计范围内流水；展示筛选不从累计余额中剔除其他账本资金。同一天的期初事件先于普通流水。账本/分类筛选只影响可见明细，不能把筛选后的净流量误称真实余额。若 start_on 早于账户基准，返回“基准前余额未知”而不是零。

统计：`net_expense = gross_expense - refunds`，`surplus = income - net_expense`。收入不含转账、opening、adjustment、refund；退款计实际发生月。分类占比用毛支出正值分母；仅退款月份可出现负净支出。收入为零时 savings_rate 为 None 并带 `ZERO_DENOMINATOR`；基期为零时比较百分比同样为空。标签筛选不能因多标签连接而重复累计一笔交易。

`ExportRequest` 为报告/文件后台任务，包含格式、目标路径、过滤、locale、主题、format_version，先产生稳定只读快照。UI/PDF/PNG 用同一 `ReportData`，不在绘图层重新算总数。文件先写同目录临时文件再原子替换，成功只在写入关闭完成后报告；取消清理临时文件，不损坏已有目标文件。交换格式与备份契约独立。

## 7. 解析契约与确认机制

### 7.1 ParseRequest

| 字段 | 类型 | 可空 / 约束 |
| --- | --- | --- |
| `draft_id` | UUID | 不可空；当前编辑会话标识 |
| `revision` | int | 不可空；原文/字段/上下文编辑后递增 |
| `text` | str | 不可空；1–4000 字符；不自动发送其他历史文本 |
| `reference_date` | date | 不可空；注入业务时钟，不在解析器读系统时钟 |
| `time_zone` | IANA str | 不可空 |
| `locale` | str | 不可空，默认 `zh_CN` |
| `current_book_id` | UUID | 可空；当前上下文，不凭空建账本 |
| `default_account_id` | UUID | 可空；仅缺省建议 |
| `category_candidates` | tuple[ParseChoice,...] | 不可空，可为空；仅 id/name/kind/受控别名 |
| `account_candidates` | tuple[ParseChoice,...] | 不可空，可为空；不携带余额或历史 |
| `payment_candidates` | tuple[ParseChoice,...] | 不可空，可为空 |
| `channel_account_mappings` | tuple[Mapping,...] | 不可空，可为空；显式用户映射 |

### 7.2 ParseResult 与字段证据

`ParseResult = {draft_id, revision, provider_id, status, drafts, issues, unassigned_spans}`，status 为 `single / multiple_events / ambiguous / unsupported`。`drafts` 是候选，不是已保存交易；每个候选含 `candidate_id`、原文字符区间、kind、amount、日期/时间、账本/账户/类别/渠道、对象/商家/地点/备注、以及 `field_evidence`。字段允许缺失，不能把解析 DTO 直接当 RecordTransaction payload。

每个 `FieldCandidate[T]` 包含：

| 字段 | 类型 | 意义 |
| --- | --- | --- |
| `value` | T 或 None | 候选值；金额是分，日期是业务日期 |
| `origin` | `explicit / rule_suggestion / default / ai_suggestion / user` | 字段来源，不是假造准确率 |
| `evidence_spans` | tuple[Span,...] | 原始 Unicode 文本索引 `[start,end)`；无原文证据可为空 |
| `rule_id` | str 或 None | 本地规则版本下的稳定解释标识 |
| `alternatives` | tuple[T,...] | 其他合理候选，不静默取第一项 |
| `requires_confirmation` | bool | 缺失、歧义、默认值或建议待确认 |
| `reason_code` | str 或 None | 如 `CHANNEL_NOT_ACCOUNT`、`MULTIPLE_AMOUNTS` |

原文规范化必须保存到原始文本的索引映射，不能把去空格后的 span 用在 UI 原文上。AI 的 evidence 必须验证实际子串与索引，失败时保留 suggestion 来源，不展示伪证据。没有统计校准的置信度不显示百分比。

日期、时段与精确时间分开：“昨晚”返回昨天的 date、period=evening/night 及可见解释，不填虚构的 UTC 时间。带具体时间时按配置时区转换；夏令时不存在/重复的本地时刻要求确认，不能任意选择 fold。未来日期保留候选并展示阻止保存的 issue，应用层再次拒绝。

### 7.3 多事件、冲突与确认

“昨天吃饭128，朋友转我50”返回 `multiple_events` 和两个候选；“买咖啡25打车40”同样不能只取第一金额。首版 UI 引导用户拆分、逐笔选择草稿并确认；不自动合并、不静默建立转账或收入。“下午3点花25”中的 3 点不是金额；“火锅”只支持餐饮建议/消费描述，不等于确定地点。

本地结果先显示；用户明确启用并触发 AI 后发送最小请求。AI 与用户已编辑字段冲突时，用户字段优先；AI 与规则不同则保留两个来源供用户选择，不自动覆盖。结果只有 `draft_id` 与 `revision` 都匹配当前会话才允许应用；过时响应丢弃，不因网络完成晚而把用户编辑撤销。

`ConfirmDraft` 是表现层组装最终命令的动作，不是数据库 API：所需字段全部有效、金额/日期/账户/账本明确展示、歧义已解决、多事件已拆分，用户按保存或 Ctrl+Enter 后生成新的 request_id。Enter 仅解析；两项快捷操作均须确认输入法不处于组合输入状态、草稿状态合适且没有正在提交。默认值在 UI 清楚展示，用户保存确认这些值；不要求为每个普通默认值弹额外对话框。解析器无 writer/UnitOfWork/RecordTransaction 引用；AI 不持有保存权限。

## 8. Protocol 草案

以下只规定签名，省略 DTO 实现、注释体和 adapter 代码；所有类型对应上文定义。Python 原生 async 仅用于后台网络/解析 provider，核心数据库用例由 writer worker 同步执行；未来实现使用线程与 worker event loop 的明确桥接，不在 GUI 线程 `asyncio.run`。

```python
from collections.abc import Sequence
from typing import Protocol

class CancellationToken(Protocol):
    @property
    def is_cancel_requested(self) -> bool: ...

    def raise_if_cancelled(self) -> None: ...

class Clock(Protocol):
    def now_utc(self) -> UtcInstant: ...
    def today(self, time_zone: TimezoneId) -> BusinessDate: ...

class ParseProvider(Protocol):
    provider_id: str
    api_version: int

    async def parse(
        self, request: ParseRequest, *, cancel: CancellationToken
    ) -> ParseResult: ...

class ImportProvider(Protocol):
    provider_id: str
    supported_formats: tuple[str, ...]

    def preview(
        self, request: ImportPreviewRequest, *, cancel: CancellationToken
    ) -> ImportPreviewResult: ...

class AnalysisProvider(Protocol):
    def analyze(
        self, aggregates: ReportAggregates, options: ReportOptions
    ) -> ReportData: ...

class ThemeProvider(Protocol):
    def load(self, theme_id: str) -> ThemeDescriptor: ...

class AttachmentStore(Protocol):
    def stage(self, request: AttachmentRequest) -> StagedAttachment: ...
    def finalize(self, staged: StagedAttachment) -> AttachmentReference: ...
    def discard(self, staged: StagedAttachment) -> None: ...

class ReleaseClient(Protocol):
    async def check(
        self, request: ReleaseCheckRequest, *, cancel: CancellationToken
    ) -> ReleaseCheckResult: ...

class UnitOfWork(Protocol):
    transactions: TransactionRepository
    accounts: AccountRepository
    receipts: ReceiptRepository
    audit: AuditRepository
    changes: ChangeRepository

    def __enter__(self) -> "UnitOfWork": ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...

class MutationService(Protocol):
    def execute(
        self, command: MutationEnvelope, *, cancel: CancellationToken
    ) -> MutationCallResult: ...

class QueryService(Protocol):
    def list_transactions(self, query: TransactionQuery) -> TransactionPage: ...
    def get_period_summary(self, query: SummaryQuery) -> PeriodSummary: ...
```

仓储返回领域实体/DTO，不返回 Qt model 或 Session。UoW 必须由 writer 所在线程创建和使用，不跨线程共享连接；读取 worker 同样自建连接。Attachment 接口是预留，不在 v1 暗示跨文件与数据库强事务，未来需 staging、提交后 finalize 与启动时修复/垃圾回收协议。

## 9. 任务、线程、取消与异步状态

`TaskEnvelope`：`task_id: UUID, owner_id: UUID, generation: int, operation: str`。GUI 只接收结构化 `TaskProgress / TaskSucceeded / TaskFailed / TaskCancelled`，这些消息包含同一 task_id 与 generation。`owner_id` 标识窗口/页面会话；关闭页面后忽略其结果。parse 再校验 draft revision；query 校验当前筛选 generation，两者与数据库 expected_version 分开。

任务状态：`queued → running → committing → committed` 或 `failed/cancelled`；只有纯读/解析任务可随时合作式取消。进度为未知时使用不定进度条，不虚构完成百分比。

资金取消规则：

1. 队列未开始或事务内部可回滚安全点收到取消，停止并返回 cancelled，无 receipt、无资金变更。
2. 进入 commit 边界后不承诺取消，关闭窗口可隐藏 UI，但 writer 仍完成提交或明确失败。
3. commit 成功后即使已有取消请求也返回已提交 receipt；绝不能显示“已取消”让用户以为没有入账。
4. commit/连接异常导致结果不确定时，保留原 request_id，重新连接查询 receipt/幂等重试；状态显示“正在核实保存结果”，不能立即用新 ID 再保存。

写事务不包含网络、文件选择、用户对话框或 PDF 绘图。退出时停止接受新命令，等待正在提交的短事务；随后关闭连接/热键/托盘。GUI 不强行终止 writer 线程。导入扫描、HTTP、绘图有分段取消；取消不是回滚一个已完成的导入批次，撤销必须显式执行 undo。

## 10. AI 配置与兼容 HTTP 边界

这里是 provider 的产品契约，不固定或声称已经实现任何特定 OpenAI 产品 API。配置可以选择官方服务或兼容服务，通过独立 profile/adapter 验证；具体端点、模型和结构化输出能力在实现阶段按对应官方文档测试并锁定。

`AiProviderConfig` 字段：`provider_id`、`profile_id`、`base_url`、`model`、`credential_reference`、`enabled=False`、`connect_timeout_seconds=5`、`response_timeout_seconds=30`、`max_response_bytes=65536`。凭据引用指向 Windows 凭据存储，DTO 不包含密钥明文；后端不可用时只允许用户明确选择会话凭据，不能静默写普通配置。

网络 endpoint 默认要求 HTTPS；明确配置本机自托管服务时允许 `localhost/127.0.0.1/[::1]` HTTP。endpoint 作为数据解析与校验，不拼接未验证路径/headers、不允许把用户名密码藏在 URL；跨来源重定向不转发 Authorization。首版不支持在普通表单输入任意认证脚本。

最小请求只发送本次 text、reference_date/time_zone、必要候选的临时 token 和显示名称/kind，不发送历史流水、账户余额、真实数据库 ID、备份、图片、附件或审计。真实 ID 在 adapter 内部映射为请求内 token；未选中的无关账户/分类可在本地过滤。提示长度超上限返回错误，不静默裁剪掉金额等关键字段。

兼容 HTTP adapter 的边界：

- `profile_id` 明确认证方式、endpoint 路径、请求 envelope、响应内容提取和错误映射；“兼容”不意味着支持流式、tools、JSON schema 或所有模型参数。
- v1 建议非流式完整 JSON 解析结果；profile 声明是否支持结构约束，不支持时提示模型返回 JSON并在本地严格校验。
- 响应限制大小、嵌套深度、事件数量（最多 10 个候选）与字段白名单；未知 token/枚举、无效金额、日期、超大响应返回结构错误，保留本地结果。
- HTTP 成功仍要逐字段验证；响应不执行代码、SQL、URL 跳转或写账动作。AI 输出不得创建不存在账户/类别；未匹配名称作为 unresolved candidate。
- 401/403 为配置或认证问题；429 映射限流；5xx/离线/超时映射可重试网络问题。解析任务不自动无限重试；重试仅用户显式操作或有限、可取消的 adapter 策略，可能重复计费需可见。
- 日志记录 provider/profile、状态码、耗时、错误码和 trace_id；不记录 Authorization、密钥、完整请求/响应或原文。账务内容不可放入错误日志“调试详情”。

配置启用/请求触发来自用户明确操作；AI 不可用只影响建议，手工与本地记账仍可用。官方服务与其他兼容服务各自有测试桩，不能以一个 provider 测通推定所有服务可用。

## 11. 插件 manifest 与权限边界

首版仅注册随应用发布、经审查的内置适配器，不扫描下载目录、不建立市场。manifest 是能力与兼容性描述，不能成为任意 Python 插件的安全沙箱。

```json
{
  "id": "openledger.builtin.local-parser",
  "name": "本地规则解析",
  "version": "0.1.0",
  "api_version": 1,
  "min_app_version": "0.1.0",
  "capabilities": ["parse"],
  "entry_point": "openledger.infrastructure.parsing.local:LocalRuleProvider",
  "config_schema_version": 1,
  "permissions": [],
  "built_in": true
}
```

必填 id（反域名式、稳定且唯一）、version（语义版本）、api_version（整数）、capabilities、entry_point、config_schema_version、permissions；name 用于 UI。`built_in` 不是读取外部 JSON 即可赋予信任的开关，必须来自应用注册表/发布清单。配置 schema 只校验数据，不执行 Python 表达式；插件不能提供 shell 安装指令让应用自动执行。

| capability | 注入的数据 / 接口 | 未授予能力 |
| --- | --- | --- |
| parse | ParseRequest、取消 token；网络 AI adapter 另取受控凭据引用 | 不持有 writer、不读完整数据库 |
| import | 用户选中的文件句柄/受限路径、列映射、最小候选 | 不提交交易，不扫描整个磁盘 |
| analysis | ReportAggregates 只读 DTO | 不修改流水，不默认联网 |
| theme | ThemeDescriptor 数据、许可资源路径 | 不执行任意主题脚本 |
| sync（未来） | 聚合批次、游标、冲突 DTO | v1 不启动、不暴露未实现入口 |

permissions 描述例如 `network:configured_endpoint`、`file:chosen_import`；在内置服务组装时决定注入，不能仅靠 manifest 字符串宣称已隔离第三方代码。api_version 不匹配、重复 ID、缺失入口或不支持 capability 时禁用该插件并显示独立错误；应用本地资金核心仍应启动。

## 12. 同步预留与聚合边界

v1 只保留接口，不配置云账号，不执行 push/pull，也不开放“复制数据库即同步”的入口。`change_log` 与 version 用于未来检测变化，不构成完整的多设备冲突解决方案。

```python
class SyncProvider(Protocol):
    async def push(
        self, request: SyncPushRequest, *, cancel: CancellationToken
    ) -> SyncPushResult: ...

    async def pull(
        self, request: SyncPullRequest, *, cancel: CancellationToken
    ) -> SyncPullResult: ...

    def plan_apply(self, changes: Sequence[SyncAggregate]) -> SyncApplyPlan: ...
```

预留 DTO：`SyncCursor(provider_id, opaque_token)`、`SyncAggregate(aggregate_id, aggregate_type, schema_version, entity_version, base_version, payload, tombstone)`、`SyncBatch(batch_id, device_id, changes)`、`SyncConflict(entity_id, local_version, remote_version, reason_code)`。所有金额继续使用精确整数单位；远端 version 不能直接替代本地乐观锁。

交易聚合完整包含交易头、全部 account_entries、标签关联、软删除状态；流水不能分开同步。账户期初切点与 opening 是关联聚合；退款组还需要原 expense 与关联退款额度的组级一致性。应用 pull 时仍执行与本地命令相同的资金/币种/切点/额度校验，不能直接 bulk insert 绕开用例。

未来同步 ADR 必须解决：本地 change_seq 与远端 cursor 的关系、回声抑制、离线重复写入、退款并发、跨聚合引用顺序、期初冲突、归档、命名冲突、可恢复失败和删除保留期。不得按 updated_at 最后写入覆盖，不同步账户“余额快照”作为权威。凭据、日志、完整本地 audit 和 command_receipts 默认不外发；备份包含 receipt 并不意味着云同步也需要它。附件独立 content hash 协议后续定义。

## 13. 错误模型与 UI 行为

`AppError` 为结构化结果/异常载荷：`code: str`、`message_key: str`、`field_errors: tuple[FieldIssue,...]`、`retryable: bool`、`trace_id: UUID|None`、`safe_details: mapping`。FieldIssue 含 `field_path`（如 `accepted_rows[3].amount_minor`）、`reason_code`、`message_key` 和安全参数。UI 本地化 message_key；技术异常链只写脱敏日志。不要把 SQL、账户完整内容、原文或服务密钥塞进 safe_details。

| code | 触发 | UI 行为 / 重试 |
| --- | --- | --- |
| `INVALID_ENVELOPE / UNSUPPORTED_CONTRACT_VERSION` | 信封、命令版本无效 | 阻止提交，提示版本/内部接口问题；不重试 |
| `INVALID_AMOUNT / AMOUNT_PRECISION / AMOUNT_OUT_OF_RANGE` | 金额格式、精度、上限 | 保留输入，定位金额；用户修正后新 request_id |
| `CURRENCY_MISMATCH` | 账户/账本/退款币种不一致 | 定位引用，不替用户换币 |
| `INVALID_DATE / FUTURE_DATE / DATE_BEFORE_BALANCE_START` | 日期/未来/切点冲突 | 定位日期；展示账户切点，提供期初管理入口 |
| `INVALID_TIMEZONE / AMBIGUOUS_LOCAL_TIME` | 未知时区或 DST 歧义 | 要求选择合法时区/具体时间，不猜测 |
| `MISSING_REQUIRED_FIELD / FIELD_CONFLICT` | 草稿缺少字段或多候选 | 展示字段级确认与候选；禁止静默保存 |
| `ENTITY_NOT_FOUND / ENTITY_DELETED / ENTITY_ARCHIVED` | 引用不可用 | 保留草稿并刷新候选，提示取消归档/重选 |
| `VERSION_CONFLICT` | expected_version 过旧 | 展示当前版本与草稿，允许用户重新比较后提交；不自动覆盖 |
| `NAME_CONFLICT / INVALID_CATEGORY_TREE` | 管理名称/分类树问题 | 定位管理表单，保留输入 |
| `INVALID_TRANSFER` | 同账户、不守恒、错误流水 | 定位账户/金额；用例失败不改余额 |
| `ORIGINAL_EXPENSE_REQUIRED / ORIGINAL_EXPENSE_UNAVAILABLE` | 退款引用非expense或已删 | 保留退款草稿，先处理原交易 |
| `REFUND_LIMIT_EXCEEDED` | 活跃累计退款超额 | 展示最新可退额度和相关记录，不自动降低金额 |
| `REFUND_DATE_BEFORE_EXPENSE` | 退款日期早于原支出，或原日期修改破坏关系 | 定位两条业务日期，要求用户核对，保留原资金状态 |
| `ACTIVE_REFUNDS_BLOCK_OPERATION` | 有活跃退款时删原/迁移账本 | 说明依赖关系，指向退款组/批次操作，无绕过按钮 |
| `TRANSACTION_KIND_IMMUTABLE / ADJUSTMENT_FINANCIAL_FIELDS_IMMUTABLE` | 变更已创建kind，或改旧校准资金字段 | 引导核对后删除/新建，明确影响与依赖 |
| `OPENING_REQUIRES_DEDICATED_COMMAND` | 普通交易接口操作期初 | 打开账户期初管理 |
| `BALANCE_START_CONFLICT` | 新切点越过已有事件 | 展示最早受影响日期，保留旧期初 |
| `ADJUSTMENT_DATE_MUST_BE_TODAY` | 历史余额校准 | 解释当前余额校准口径，日期改今天后重新确认 |
| `AGGREGATE_OUT_OF_RANGE` | 余额/总资产等超 signed int64 | 阻止错误结果显示或受影响写入，保留账本，导出脱敏诊断 |
| `IDEMPOTENCY_KEY_REUSED` | 同 ID 不同 type/payload | 阻止重复意图混用，保留草稿；仅新的用户保存产生新 ID |
| `QUERY_SNAPSHOT_CHANGED` | 旧分页游标数据已变 | 重置页游标、保持筛选并刷新 |
| `DATABASE_BUSY` | 锁竞争 | 保留草稿，有限等待/同 request_id 重试，超过阈值提示关闭其他实例 |
| `DATABASE_READ_ONLY / DISK_FULL / STORAGE_IO_ERROR` | 存储失败 | 明确未确认保存；保留草稿，提供目录/磁盘修复提示 |
| `COMMIT_RESULT_UNKNOWN` | 提交结果不确定 | “正在核实保存结果”；查 receipt/同 ID 重试，禁止直接重复新建 |
| `DATABASE_VERSION_TOO_NEW / MIGRATION_FAILED / INTEGRITY_FAILED` | 启动/升级/恢复不通过 | 阻止写入，保留原库和备份，提供恢复或新版入口 |
| `IMPORT_PREVIEW_STALE / IMPORT_ROW_INVALID` | 源/映射变化、行无效 | 回预览并定位行；无部分资金提交 |
| `EXACT_IMPORT_DUPLICATE / IMPORT_UNDO_CONFLICT` | 稳定流水重复/撤销成员冲突 | 展示命中行或成员；整批保持一致 |
| `UNSUPPORTED_FILE_FORMAT / FILE_ACCESS_DENIED / EXPORT_FAILED` | 文件任务错误 | 保留选项，提示选择受支持文件/可写位置 |
| `AI_NOT_CONFIGURED / AI_AUTH_FAILED` | 未配置/认证失败 | 保留本地草稿，打开设置入口 |
| `AI_RATE_LIMITED / NETWORK_UNAVAILABLE / NETWORK_TIMEOUT` | 网络/限流 | 本地可继续；用户可重试，可见取消 |
| `AI_INVALID_RESPONSE / AI_RESPONSE_TOO_LARGE` | 结构/大小/token无效 | 丢弃 AI 结果，保留本地建议，脱敏提示 |
| `PLUGIN_INCOMPATIBLE / PLUGIN_LOAD_FAILED` | 插件不兼容/注册失败 | 禁用该适配器，本地核心继续 |
| `HOTKEY_UNAVAILABLE` | 热键冲突/注册失败 | 显示失败并允许改键；托盘和主窗口仍可用 |
| `CANCELLED` | commit 前安全取消 | 保留未提交草稿；不能用于已提交结果 |
| `INTERNAL_ERROR` | 未映射故障 | 保留输入，展示 trace_id 与脱敏日志入口，不暴露 traceback |

GitHub 更新检查采用 `UpdateCheckResult(status=update_available/up_to_date/no_release/offline/rate_limited/invalid_release, version, release_url, checked_at)`，离线与暂无 Release 是独立状态，不显示为资金错误。repo 未配置时显示未配置，不能请求占位地址。只返回已验证仓库 Release 链接，点击由用户打开；无自动下载安装。

## 14. 契约验收与阶段实施

本文件没有运行时实现，因此本阶段验证是字段、约束、schema 与 UI 的交叉评审；不把文档检查当作业务测试通过。阶段 3 首先实现最小 DTO/ports 与测试设施，资金实现阶段再执行以下有实际意义的契约测试：

- 同 request_id 同 payload 重试只有一次变更；跨 command_type/payload 冲突；已成功编辑的重试优先于当前版本校验；no_change 校准重试不变成新事件。
- 期初为零仍拒绝切点前事件；同日允许；转账两账户分别校验切点；未来日期在解析和用例均阻止。
- 失败注入时交易、两侧流水、标签、审计、change_log 与 receipt 共同回滚；commit 后取消仍返回成功回执。
- 部分退款累计上限、编辑排除自身、恢复时额度/日期重新校验；原分类改变后有效退款分类随之变化；恢复原支出不自动恢复退款。
- 规则、默认、AI、用户字段来源可解释；多事件不丢金额；延迟 AI 响应不能覆盖新 revision；时区/跨年/夏令时用注入时钟测试。
- 过滤/分页快照稳定，退款按有效归属，标签不重复计数，空月/零收入/仅退款口径一致；大额聚合不依赖溢出的 SQLite SUM。
- 导入预览被修改时拒绝旧令牌；事务失败不部分成功；后续编辑/外部退款阻止批次撤销。
- AI 超时/认证/限流/无效结构均保留本地草稿；密钥与原始账务不出现在错误/日志；未启用不发网络请求。

与详细 Schema、页面交互和 ADR 有任何冲突时，阶段 2 结束前统一修订，不由实施者在资金代码中自行选择一种解释。
