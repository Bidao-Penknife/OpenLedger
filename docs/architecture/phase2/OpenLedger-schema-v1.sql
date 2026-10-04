-- OpenLedger v1.0 target schema: Phase 2 executable design, not a migration.
-- Requires SQLite >= 3.37, STRICT tables, built-in JSON functions.
-- Amounts are CNY integer fen. Per-transaction maximum: 99_999_999_999_999.
-- UUIDs are generated/validated by the application; SQL checks only length.
-- UTC format: YYYY-MM-DDTHH:MM:SS.sssZ. Calendar validity is an application rule.
-- Single writer validates aggregate invariants under a write transaction.
-- Execute against an empty disposable database only. No seed or Alembic revision.
-- Every runtime connection must enable and verify foreign_keys separately.
PRAGMA foreign_keys = ON;

BEGIN;

CREATE TABLE books (
    id TEXT PRIMARY KEY CHECK (length(id) = 36),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 80),
    description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
    currency_code TEXT NOT NULL DEFAULT 'CNY' CHECK (currency_code = 'CNY'),
    sort_order INTEGER NOT NULL DEFAULT 0 CHECK (sort_order >= 0),
    is_archived INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
    created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    updated_at_utc TEXT NOT NULL CHECK (updated_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1)
) STRICT;

CREATE UNIQUE INDEX uq_books_active_name ON books(name COLLATE NOCASE)
    WHERE is_archived = 0;

CREATE TABLE accounts (
    id TEXT PRIMARY KEY CHECK (length(id) = 36),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 80),
    account_type TEXT NOT NULL
        CHECK (account_type IN ('cash', 'bank', 'wechat', 'alipay', 'custom')),
    currency_code TEXT NOT NULL DEFAULT 'CNY' CHECK (currency_code = 'CNY'),
    balance_start_on TEXT NOT NULL CHECK (balance_start_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' AND substr(balance_start_on, 6, 2) BETWEEN '01' AND '12' AND substr(balance_start_on, 9, 2) BETWEEN '01' AND '31'),
    description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
    sort_order INTEGER NOT NULL DEFAULT 0 CHECK (sort_order >= 0),
    is_archived INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
    created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    updated_at_utc TEXT NOT NULL CHECK (updated_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1)
    -- No authoritative balance column; even a zero opening has a time cut point.
) STRICT;

CREATE UNIQUE INDEX uq_accounts_active_name ON accounts(name COLLATE NOCASE)
    WHERE is_archived = 0;

CREATE TABLE categories (
    id TEXT PRIMARY KEY CHECK (length(id) = 36),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 80),
    transaction_kind TEXT NOT NULL CHECK (transaction_kind IN ('income', 'expense')),
    parent_id TEXT REFERENCES categories(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    color TEXT CHECK (color IS NULL OR color GLOB '#[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]'),
    sort_order INTEGER NOT NULL DEFAULT 0 CHECK (sort_order >= 0),
    is_archived INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
    created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    updated_at_utc TEXT NOT NULL CHECK (updated_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    CHECK (parent_id IS NULL OR parent_id <> id),
    UNIQUE (id, transaction_kind),
    FOREIGN KEY (parent_id, transaction_kind)
        REFERENCES categories(id, transaction_kind) ON DELETE RESTRICT ON UPDATE RESTRICT
    -- One parent-child level, cycles and historical kind changes are app rules.
) STRICT;

CREATE UNIQUE INDEX uq_categories_active_root_name
    ON categories(transaction_kind, name COLLATE NOCASE)
    WHERE parent_id IS NULL AND is_archived = 0;
CREATE UNIQUE INDEX uq_categories_active_child_name
    ON categories(parent_id, name COLLATE NOCASE)
    WHERE parent_id IS NOT NULL AND is_archived = 0;
CREATE INDEX ix_categories_parent ON categories(parent_id);

CREATE TABLE payment_methods (
    id TEXT PRIMARY KEY CHECK (length(id) = 36),
    code TEXT NOT NULL UNIQUE CHECK (length(code) BETWEEN 1 AND 80),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 80),
    default_account_id TEXT REFERENCES accounts(id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    sort_order INTEGER NOT NULL DEFAULT 0 CHECK (sort_order >= 0),
    is_archived INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
    created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    updated_at_utc TEXT NOT NULL CHECK (updated_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1)
    -- A payment channel mapping suggests an account; it does not fix the payer.
) STRICT;

CREATE UNIQUE INDEX uq_payment_methods_active_name
    ON payment_methods(name COLLATE NOCASE) WHERE is_archived = 0;
CREATE INDEX ix_payment_methods_default_account ON payment_methods(default_account_id);

CREATE TABLE tags (
    id TEXT PRIMARY KEY CHECK (length(id) = 36),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 80),
    color TEXT CHECK (color IS NULL OR color GLOB '#[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]'),
    sort_order INTEGER NOT NULL DEFAULT 0 CHECK (sort_order >= 0),
    is_archived INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
    created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    updated_at_utc TEXT NOT NULL CHECK (updated_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1)
) STRICT;

CREATE UNIQUE INDEX uq_tags_active_name ON tags(name COLLATE NOCASE)
    WHERE is_archived = 0;

CREATE TABLE import_batches (
    id TEXT PRIMARY KEY CHECK (length(id) = 36),
    source_format TEXT NOT NULL CHECK (source_format IN ('csv', 'xlsx')),
    source_file_name TEXT NOT NULL CHECK (length(source_file_name) BETWEEN 1 AND 255),
    file_digest TEXT NOT NULL CHECK (length(file_digest) = 64 AND file_digest NOT GLOB '*[^0-9a-f]*'),
    mapping_hash TEXT NOT NULL CHECK (length(mapping_hash) = 64 AND mapping_hash NOT GLOB '*[^0-9a-f]*'),
    mapping_json TEXT NOT NULL CHECK (json_valid(mapping_json) AND json_type(mapping_json) = 'object'),
    format_version INTEGER NOT NULL DEFAULT 1 CHECK (format_version >= 1),
    status TEXT NOT NULL CHECK (status IN ('committed', 'reverted')),
    source_row_count INTEGER NOT NULL CHECK (source_row_count >= 1),
    accepted_row_count INTEGER NOT NULL
        CHECK (accepted_row_count >= 1 AND accepted_row_count <= source_row_count),
    reverted_at_utc TEXT CHECK (reverted_at_utc IS NULL OR reverted_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    updated_at_utc TEXT NOT NULL CHECK (updated_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    UNIQUE (id, file_digest, mapping_hash),
    CHECK ((status = 'committed' AND reverted_at_utc IS NULL)
        OR (status = 'reverted' AND reverted_at_utc IS NOT NULL))
    -- Preview stays outside the DB. Accepted rows/batch commit atomically.
) STRICT;

CREATE INDEX ix_import_batches_digest ON import_batches(file_digest);
CREATE INDEX ix_import_batches_created ON import_batches(created_at_utc);

CREATE TABLE command_receipts (
    request_id TEXT PRIMARY KEY CHECK (length(request_id) = 36),
    command_type TEXT NOT NULL CHECK (length(command_type) BETWEEN 1 AND 80),
    payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64 AND payload_hash NOT GLOB '*[^0-9a-f]*'),
    result_version INTEGER NOT NULL DEFAULT 1 CHECK (result_version >= 1),
    result_json TEXT NOT NULL CHECK (json_valid(result_json) AND json_type(result_json) = 'object'),
    completed_at_utc TEXT NOT NULL CHECK (completed_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z')
    -- Only successful commands are stored, in the same transaction as mutations.
    -- No transaction FK: a receipt may describe edits/deletes or a whole batch.
    -- Reusing request_id with changed type/hash is IDEMPOTENCY_KEY_REUSED.
) STRICT;

CREATE INDEX ix_command_receipts_completed ON command_receipts(completed_at_utc);

CREATE TABLE transactions (
    id TEXT PRIMARY KEY CHECK (length(id) = 36),
    kind TEXT NOT NULL CHECK (kind IN (
        'income', 'expense', 'expense_refund', 'transfer', 'opening', 'adjustment'
    )),
    amount_minor INTEGER NOT NULL CHECK (amount_minor BETWEEN 1 AND 99999999999999),
    currency_code TEXT NOT NULL DEFAULT 'CNY' CHECK (currency_code = 'CNY'),
    source TEXT NOT NULL DEFAULT 'manual'
        CHECK (source IN ('manual', 'local_rule', 'ai_assisted', 'import', 'system')),
    book_id TEXT REFERENCES books(id) ON DELETE RESTRICT ON UPDATE RESTRICT,
    category_id TEXT,
    payment_method_id TEXT REFERENCES payment_methods(id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    occurred_on TEXT NOT NULL CHECK (occurred_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' AND substr(occurred_on, 6, 2) BETWEEN '01' AND '12' AND substr(occurred_on, 9, 2) BETWEEN '01' AND '31'),
    occurrence_precision TEXT NOT NULL DEFAULT 'date'
        CHECK (occurrence_precision IN ('date', 'period', 'exact')),
    time_period TEXT CHECK (time_period IN ('morning', 'noon', 'afternoon', 'evening', 'night')),
    occurred_at_utc TEXT CHECK (occurred_at_utc IS NULL OR occurred_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    time_zone TEXT NOT NULL
        CHECK (length(time_zone) BETWEEN 1 AND 64),
    counterparty TEXT CHECK (counterparty IS NULL OR length(counterparty) <= 200),
    merchant TEXT CHECK (merchant IS NULL OR length(merchant) <= 200),
    location TEXT CHECK (location IS NULL OR length(location) <= 200),
    note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 4000),
    source_text TEXT CHECK (source_text IS NULL OR length(source_text) <= 8192),
    original_transaction_id TEXT REFERENCES transactions(id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    adjustment_reason TEXT CHECK (
        adjustment_reason IS NULL OR length(trim(adjustment_reason)) BETWEEN 1 AND 1000
    ),
    balance_before_minor INTEGER,
    balance_target_minor INTEGER,
    import_batch_id TEXT REFERENCES import_batches(id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    import_file_digest TEXT CHECK (import_file_digest IS NULL OR (length(import_file_digest) = 64 AND import_file_digest NOT GLOB '*[^0-9a-f]*')),
    import_mapping_hash TEXT CHECK (import_mapping_hash IS NULL OR (length(import_mapping_hash) = 64 AND import_mapping_hash NOT GLOB '*[^0-9a-f]*')),
    import_source_row INTEGER CHECK (import_source_row IS NULL OR import_source_row >= 1),
    external_source TEXT CHECK (external_source IS NULL OR length(external_source) BETWEEN 1 AND 160),
    external_transaction_id TEXT CHECK (external_transaction_id IS NULL OR length(external_transaction_id) BETWEEN 1 AND 255),
    created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    updated_at_utc TEXT NOT NULL CHECK (updated_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    deleted_at_utc TEXT CHECK (deleted_at_utc IS NULL OR deleted_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    CHECK ((kind IN ('income', 'expense') AND book_id IS NOT NULL AND category_id IS NOT NULL)
        OR (kind IN ('expense_refund', 'transfer', 'opening', 'adjustment')
            AND book_id IS NULL AND category_id IS NULL)),
    CHECK ((kind = 'expense_refund' AND original_transaction_id IS NOT NULL
            AND original_transaction_id <> id)
        OR (kind <> 'expense_refund' AND original_transaction_id IS NULL)),
    CHECK ((occurrence_precision = 'date' AND time_period IS NULL AND occurred_at_utc IS NULL)
        OR (occurrence_precision = 'period' AND time_period IS NOT NULL AND occurred_at_utc IS NULL)
        OR (occurrence_precision = 'exact' AND time_period IS NULL AND occurred_at_utc IS NOT NULL)),
    CHECK ((kind = 'adjustment' AND adjustment_reason IS NOT NULL
            AND balance_before_minor IS NOT NULL AND balance_target_minor IS NOT NULL
            AND balance_before_minor <> balance_target_minor)
        OR (kind <> 'adjustment' AND adjustment_reason IS NULL
            AND balance_before_minor IS NULL AND balance_target_minor IS NULL)),
    CHECK ((import_batch_id IS NULL AND import_file_digest IS NULL AND import_mapping_hash IS NULL AND import_source_row IS NULL)
        OR (import_batch_id IS NOT NULL AND import_file_digest IS NOT NULL AND import_mapping_hash IS NOT NULL AND import_source_row IS NOT NULL)),
    CHECK ((external_source IS NULL AND external_transaction_id IS NULL)
        OR (external_source IS NOT NULL AND external_transaction_id IS NOT NULL)),
    FOREIGN KEY (category_id, kind) REFERENCES categories(id, transaction_kind)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    FOREIGN KEY (import_batch_id, import_file_digest, import_mapping_hash)
        REFERENCES import_batches(id, file_digest, mapping_hash)
        ON DELETE RESTRICT ON UPDATE RESTRICT
    -- Refund original kind/liveness/limit and opening date are cross-row app rules.
) STRICT;

CREATE INDEX ix_transactions_active_date_kind
    ON transactions(occurred_on, kind) WHERE deleted_at_utc IS NULL;
CREATE INDEX ix_transactions_active_book_date
    ON transactions(book_id, occurred_on) WHERE deleted_at_utc IS NULL;
CREATE INDEX ix_transactions_active_category_date
    ON transactions(category_id, occurred_on) WHERE deleted_at_utc IS NULL;
CREATE INDEX ix_transactions_active_refund_original
    ON transactions(original_transaction_id) WHERE deleted_at_utc IS NULL;
CREATE INDEX ix_transactions_import_batch ON transactions(import_batch_id);
CREATE INDEX ix_transactions_payment_method ON transactions(payment_method_id);
CREATE UNIQUE INDEX uq_transactions_import_source_row
    ON transactions(import_file_digest, import_mapping_hash, import_source_row)
    WHERE import_file_digest IS NOT NULL;
CREATE UNIQUE INDEX uq_transactions_external_id
    ON transactions(external_source, external_transaction_id)
    WHERE external_source IS NOT NULL;

CREATE TABLE account_entries (
    id TEXT PRIMARY KEY CHECK (length(id) = 36),
    transaction_id TEXT NOT NULL REFERENCES transactions(id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    account_id TEXT NOT NULL REFERENCES accounts(id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    delta_minor INTEGER NOT NULL
        CHECK (delta_minor BETWEEN -99999999999999 AND 99999999999999 AND delta_minor <> 0),
    UNIQUE (transaction_id, account_id)
    -- No independent version/soft-delete: entries belong to a transaction aggregate.
    -- No redundant role column. Active opening uniqueness needs a cross-table
    -- predicate; enforce under the one writer/UoW, not a misleading partial index.
) STRICT;

CREATE INDEX ix_account_entries_account_transaction ON account_entries(account_id, transaction_id);

CREATE TABLE transaction_tags (
    transaction_id TEXT NOT NULL REFERENCES transactions(id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    tag_id TEXT NOT NULL REFERENCES tags(id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    PRIMARY KEY (transaction_id, tag_id)
    -- Link changes update the owning transaction version/audit/change_log.
) STRICT;

CREATE INDEX ix_transaction_tags_tag ON transaction_tags(tag_id, transaction_id);

CREATE TABLE attachments (
    id TEXT PRIMARY KEY CHECK (length(id) = 36),
    transaction_id TEXT NOT NULL REFERENCES transactions(id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    relative_path TEXT NOT NULL UNIQUE CHECK (
        length(relative_path) BETWEEN 1 AND 1024
        AND substr(relative_path, 1, 1) <> '/'
        AND instr(relative_path, '\') = 0
        AND instr(relative_path, ':') = 0
        AND instr('/' || relative_path || '/', '/../') = 0
        AND instr('/' || relative_path || '/', '/./') = 0
        AND instr('/' || relative_path || '/', '//') = 0
    ),
    original_file_name TEXT NOT NULL CHECK (length(original_file_name) BETWEEN 1 AND 255),
    mime_type TEXT NOT NULL CHECK (length(mime_type) BETWEEN 1 AND 100),
    size_bytes INTEGER NOT NULL CHECK (size_bytes BETWEEN 1 AND 20971520),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64 AND sha256 NOT GLOB '*[^0-9a-f]*'),
    created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    updated_at_utc TEXT NOT NULL CHECK (updated_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    deleted_at_utc TEXT CHECK (deleted_at_utc IS NULL OR deleted_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z')
    -- Future image store must resolve paths under the data root and verify MIME,
    -- byte size/hash and transaction state. SQL path checks are only defense-in-depth.
) STRICT;

CREATE INDEX ix_attachments_transaction ON attachments(transaction_id);
CREATE INDEX ix_attachments_sha256 ON attachments(sha256);

CREATE TABLE audit_events (
    id TEXT PRIMARY KEY CHECK (length(id) = 36),
    request_id TEXT NOT NULL REFERENCES command_receipts(request_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    entity_type TEXT NOT NULL CHECK (entity_type IN (
        'book', 'account', 'category', 'payment_method', 'tag',
        'transaction', 'attachment', 'import_batch'
    )),
    entity_id TEXT NOT NULL CHECK (length(entity_id) = 36),
    action TEXT NOT NULL CHECK (action IN (
        'create', 'update', 'soft_delete', 'restore', 'archive', 'unarchive'
    )),
    old_version INTEGER CHECK (old_version IS NULL OR old_version >= 1),
    new_version INTEGER NOT NULL CHECK (new_version >= 1),
    before_json TEXT CHECK (before_json IS NULL OR (
        json_valid(before_json) AND json_type(before_json) = 'object'
    )),
    after_json TEXT NOT NULL CHECK (json_valid(after_json) AND json_type(after_json) = 'object'),
    created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    CHECK ((action = 'create' AND old_version IS NULL AND new_version = 1 AND before_json IS NULL)
        OR (action <> 'create' AND old_version IS NOT NULL
            AND new_version = old_version + 1 AND before_json IS NOT NULL)),
    UNIQUE (entity_type, entity_id, new_version)
    -- Local append-only application policy; this is not a tamper-proof ledger.
    -- Snapshots contain permitted local data, never API secrets.
) STRICT;

CREATE INDEX ix_audit_events_entity ON audit_events(entity_type, entity_id, created_at_utc);
CREATE INDEX ix_audit_events_request ON audit_events(request_id);
CREATE INDEX ix_audit_events_created ON audit_events(created_at_utc);

CREATE TABLE change_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_event_id TEXT NOT NULL UNIQUE REFERENCES audit_events(id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    entity_type TEXT NOT NULL CHECK (entity_type IN (
        'book', 'account', 'category', 'payment_method', 'tag',
        'transaction', 'attachment', 'import_batch'
    )),
    entity_id TEXT NOT NULL CHECK (length(entity_id) = 36),
    version INTEGER NOT NULL CHECK (version >= 1),
    operation TEXT NOT NULL CHECK (operation IN (
        'create', 'update', 'soft_delete', 'restore', 'archive', 'unarchive'
    )),
    changed_at_utc TEXT NOT NULL CHECK (changed_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    UNIQUE (entity_type, entity_id, version)
    -- A local monotonically increasing cursor, not a full cloud sync protocol.
    -- Application matches entity/version/operation to its linked audit event.
) STRICT;

CREATE INDEX ix_change_log_entity ON change_log(entity_type, entity_id, version);

CREATE TABLE alembic_version (
    version_num TEXT NOT NULL PRIMARY KEY CHECK (length(version_num) BETWEEN 1 AND 32)
    -- Placeholder shape only. Real revisions are managed in future migrations.
) STRICT;

-- Balances always include archived accounts; deletion is inherited from the head.
CREATE VIEW v_account_balances AS
WITH active_entry_totals AS (
    SELECT e.account_id, SUM(e.delta_minor) AS balance_minor
    FROM account_entries AS e
    JOIN transactions AS t ON t.id = e.transaction_id
    WHERE t.deleted_at_utc IS NULL
    GROUP BY e.account_id
)
SELECT a.id AS account_id, a.name AS account_name, a.account_type,
       a.currency_code, a.balance_start_on, a.is_archived,
       COALESCE(s.balance_minor, 0) AS balance_minor
FROM accounts AS a
LEFT JOIN active_entry_totals AS s ON s.account_id = a.id;

-- One row per income/expense/refund, as guaranteed by entry cardinality in UoW.
-- Refund date/account are actual receipt date/account; book/category are inherited.
-- Filter this view by occurred_on/book_id/category_id/account_id for all reports.
-- Gross expense gives positive chart denominators; refund stays a separate measure.
CREATE VIEW v_cashflow_transactions AS
SELECT t.id AS transaction_id, t.kind, t.amount_minor, t.currency_code, t.source,
       t.occurred_on, t.occurrence_precision, t.time_period, t.occurred_at_utc,
       t.time_zone,
       CASE WHEN t.kind = 'expense_refund' THEN original.book_id ELSE t.book_id END AS book_id,
       CASE WHEN t.kind = 'expense_refund' THEN original.category_id ELSE t.category_id END AS category_id,
       e.account_id, e.delta_minor AS account_delta_minor, t.payment_method_id,
       t.original_transaction_id, t.counterparty, t.merchant, t.location, t.note,
       CASE WHEN t.kind = 'income' THEN t.amount_minor ELSE 0 END AS income_minor,
       CASE WHEN t.kind = 'expense' THEN t.amount_minor ELSE 0 END AS gross_expense_minor,
       CASE WHEN t.kind = 'expense_refund' THEN t.amount_minor ELSE 0 END AS refund_minor,
       CASE WHEN t.kind = 'expense' THEN t.amount_minor
            WHEN t.kind = 'expense_refund' THEN -t.amount_minor ELSE 0 END AS net_expense_minor,
       t.created_at_utc, t.updated_at_utc, t.version
FROM transactions AS t
JOIN account_entries AS e ON e.transaction_id = t.id
LEFT JOIN transactions AS original ON original.id = t.original_transaction_id
WHERE t.deleted_at_utc IS NULL
  AND (t.kind IN ('income', 'expense')
       OR (t.kind = 'expense_refund'
           AND original.kind = 'expense' AND original.deleted_at_utc IS NULL));

COMMIT;
