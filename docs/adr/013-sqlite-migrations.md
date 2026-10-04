# ADR-013：显式 SQLite 事务与线性迁移

状态：阶段 4 已采用。日期：2026-10-02。

## 决策

阶段 4 使用 Python 3.12 标准库 `sqlite3` 实现本地线性迁移，不新增 ORM 或迁移框架依赖。此决定修订阶段 2 的 SQLAlchemy/Alembic 实施规划，服务与领域接口保持不依赖数据库框架。第一版仅一个数据库、一个顺序迁移分支；原始 STRICT DDL 可直接保留阶段 2 验证过的约束、索引和视图，显式事务便于注入失败验证资金原子性。此方案是当前范围的选择，不宣称替代通用 ORM、分支合并或多数据库迁移工具。

`resources/migrations/0001.sql` 保留原设计的 14 张表、27 个显式索引、2 个视图。增加 `app_preferences` 单行表，将默认账本/账户切换与聚合修改放在同一个事务；迁移执行器另创建 `schema_migrations` 表。实际共 16 张业务和元数据表，不计 SQLite 内部表。历史 `alembic_version` 表保留为空，仅供未来兼容，不使用它表示已应用版本。

每个版本记录精确 SQL UTF-8 字节的 SHA256 与 UTC 应用时间；`PRAGMA user_version` 与已知连续迁移版本一致，`PRAGMA application_id` 固定为 `0x4F4C4447`。启动拒绝未知非空数据库、未来版本、缺失历史、摘要不一致、未知表/索引/视图、外键不一致和结构损坏，不自动认领或修复用户文件。当前版本启动先用只读连接检查，有效库不创建迁移备份、不产生启动写事务。

执行器用 `sqlite3.complete_statement` 分割带分号的 SQL，逐条 `execute`，由外层 `BEGIN IMMEDIATE` 和一次提交控制 DDL、迁移记录和版本标记。不用 `executescript`，避免其事务行为让半套 DDL 提前提交。迁移执行时 SQLite authorizer 禁止自行提交、回滚、保存点、挂接数据库和修改 PRAGMA，避免后续修订意外越过执行器的原子边界。已有数据库确实需要新修订时，先用 SQLite backup API 保存升级前快照，随后原子执行新修订；失败回滚原库，保留快照供诊断。

## 连接与锁

每次操作新建并显式关闭连接，`isolation_level=None` 禁止 Python 自动开始事务；不跨线程传递连接，不设置 `check_same_thread=False`。每个实例的 `RLock` 串行化本实例写入，其他实例或进程由 SQLite 锁保护。所有连接启用并验证外键，设置 busy timeout 与 `synchronous=FULL`。写上下文执行 `BEGIN IMMEDIATE`，异常回滚全部操作；BUSY/LOCKED 对外转换为稳定的 `DATABASE_BUSY`。

读上下文使用只读 URI 与 `BEGIN`，首次查询建立快照，退出时回滚并关闭。DELETE 日志下读事务会阻止其他连接提交；因此调用方应尽快把结果转成 DTO 并释放连接，不长期向界面暴露游标。真实锁竞争测试确认失败写入回滚，读事务释放后能够重试。

新库始终采用 DELETE 回滚日志。本机 SQLite 3.45.3 不通过 ADR-011 的 WAL 门槛；现有 WAL 库在不安全运行库中拒绝打开，不自动切换或假定可写。通过版本门槛的运行库可以保留现有 WAL，但第一版不自动启用 WAL；门槛模拟测试只验证策略路径，不证明本机 SQLite 已修复 WAL。

## 后果与验证

迁移资源随 wheel 和 Windows 冻结制品一起打包。已应用迁移必须保持原字节不变；任何修订创建新文件，不能修改 `0001.sql` 后让旧库静默接受。恢复流程使用相同的数据库校验，资金默认数据由应用用例创建，数据库迁移不隐式生成账户或余额。

测试覆盖真实文件的 STRICT、外键、对象数量、中文路径、跨实例和线程写锁、只读快照、业务异常回滚、迁移中途失败、升级前快照、重复初始化、未知/未来/篡改数据库及保守 WAL 门槛。阶段 4 仅实现本地线性迁移，不支持迁移分支、自动降级或未知数据库修复。

依据：[Python 3.12 sqlite3 事务控制](https://docs.python.org/3.12/library/sqlite3.html#transaction-control)、[SQLite 事务](https://www.sqlite.org/lang_transaction.html)、[SQLite backup API](https://www.sqlite.org/backup.html)。
