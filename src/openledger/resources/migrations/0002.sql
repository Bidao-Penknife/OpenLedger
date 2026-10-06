-- Migration 0002: native currencies, exact two-sided transfers and capture inbox.
-- Runner snapshots the validated old ledger, disables FK enforcement only for the
-- atomic table rebuild, checks all foreign keys before commit and re-enables them.
-- Original 0001.sql remains immutable. No monetary values or receipt hashes change.


DROP VIEW v_account_balances;

DROP VIEW v_cashflow_transactions;

CREATE TABLE books_new (
    id TEXT PRIMARY KEY CHECK (length(id) = 36),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 80),
    description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
    currency_code TEXT NOT NULL DEFAULT 'CNY' CHECK (currency_code IN ('AED','AFN','ALL','AMD','AOA','ARS','AUD','AWG','AZN','BAM','BBD','BDT','BHD','BIF','BMD','BND','BOB','BOV','BRL','BSD','BTN','BWP','BYN','BZD','CAD','CDF','CHE','CHF','CHW','CLF','CLP','CNY','COP','COU','CRC','CUP','CVE','CZK','DJF','DKK','DOP','DZD','EGP','ERN','ETB','EUR','FJD','FKP','GBP','GEL','GHS','GIP','GMD','GNF','GTQ','GYD','HKD','HNL','HTG','HUF','IDR','ILS','INR','IQD','IRR','ISK','JMD','JOD','JPY','KES','KGS','KHR','KMF','KPW','KRW','KWD','KYD','KZT','LAK','LBP','LKR','LRD','LSL','LYD','MAD','MDL','MGA','MKD','MMK','MNT','MOP','MRU','MUR','MVR','MWK','MXN','MXV','MYR','MZN','NAD','NGN','NIO','NOK','NPR','NZD','OMR','PAB','PEN','PGK','PHP','PKR','PLN','PYG','QAR','RON','RSD','RUB','RWF','SAR','SBD','SCR','SDG','SEK','SGD','SHP','SLE','SOS','SRD','SSP','STN','SVC','SYP','SZL','THB','TJS','TMT','TND','TOP','TRY','TTD','TWD','TZS','UAH','UGX','USD','USN','UYI','UYU','UYW','UZS','VED','VES','VND','VUV','WST','XAD','XAF','XCD','XCG','XOF','XPF','YER','ZAR','ZMW','ZWG')),
    sort_order INTEGER NOT NULL DEFAULT 0 CHECK (sort_order >= 0),
    is_archived INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
    created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    updated_at_utc TEXT NOT NULL CHECK (updated_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1)
) STRICT;

INSERT INTO books_new(id,name,description,currency_code,sort_order,is_archived,created_at_utc,updated_at_utc,version) SELECT id,name,description,currency_code,sort_order,is_archived,created_at_utc,updated_at_utc,version FROM books;

DROP TABLE books;

ALTER TABLE books_new RENAME TO books;

CREATE UNIQUE INDEX uq_books_active_name ON books(name COLLATE NOCASE)
    WHERE is_archived = 0;

CREATE TABLE accounts_new (
    id TEXT PRIMARY KEY CHECK (length(id) = 36),
    name TEXT NOT NULL CHECK (length(trim(name)) BETWEEN 1 AND 80),
    account_type TEXT NOT NULL
        CHECK (account_type IN ('cash', 'bank', 'wechat', 'alipay', 'custom')),
    currency_code TEXT NOT NULL DEFAULT 'CNY' CHECK (currency_code IN ('AED','AFN','ALL','AMD','AOA','ARS','AUD','AWG','AZN','BAM','BBD','BDT','BHD','BIF','BMD','BND','BOB','BOV','BRL','BSD','BTN','BWP','BYN','BZD','CAD','CDF','CHE','CHF','CHW','CLF','CLP','CNY','COP','COU','CRC','CUP','CVE','CZK','DJF','DKK','DOP','DZD','EGP','ERN','ETB','EUR','FJD','FKP','GBP','GEL','GHS','GIP','GMD','GNF','GTQ','GYD','HKD','HNL','HTG','HUF','IDR','ILS','INR','IQD','IRR','ISK','JMD','JOD','JPY','KES','KGS','KHR','KMF','KPW','KRW','KWD','KYD','KZT','LAK','LBP','LKR','LRD','LSL','LYD','MAD','MDL','MGA','MKD','MMK','MNT','MOP','MRU','MUR','MVR','MWK','MXN','MXV','MYR','MZN','NAD','NGN','NIO','NOK','NPR','NZD','OMR','PAB','PEN','PGK','PHP','PKR','PLN','PYG','QAR','RON','RSD','RUB','RWF','SAR','SBD','SCR','SDG','SEK','SGD','SHP','SLE','SOS','SRD','SSP','STN','SVC','SYP','SZL','THB','TJS','TMT','TND','TOP','TRY','TTD','TWD','TZS','UAH','UGX','USD','USN','UYI','UYU','UYW','UZS','VED','VES','VND','VUV','WST','XAD','XAF','XCD','XCG','XOF','XPF','YER','ZAR','ZMW','ZWG')),
    balance_start_on TEXT NOT NULL CHECK (balance_start_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]' AND substr(balance_start_on, 6, 2) BETWEEN '01' AND '12' AND substr(balance_start_on, 9, 2) BETWEEN '01' AND '31'),
    description TEXT NOT NULL DEFAULT '' CHECK (length(description) <= 1000),
    sort_order INTEGER NOT NULL DEFAULT 0 CHECK (sort_order >= 0),
    is_archived INTEGER NOT NULL DEFAULT 0 CHECK (is_archived IN (0, 1)),
    created_at_utc TEXT NOT NULL CHECK (created_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    updated_at_utc TEXT NOT NULL CHECK (updated_at_utc GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9].[0-9][0-9][0-9]Z'),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1)
    -- No authoritative balance column; even a zero opening has a time cut point.
) STRICT;

INSERT INTO accounts_new(id,name,account_type,currency_code,balance_start_on,description,sort_order,is_archived,created_at_utc,updated_at_utc,version) SELECT id,name,account_type,currency_code,balance_start_on,description,sort_order,is_archived,created_at_utc,updated_at_utc,version FROM accounts;

DROP TABLE accounts;

ALTER TABLE accounts_new RENAME TO accounts;

CREATE UNIQUE INDEX uq_accounts_active_name ON accounts(name COLLATE NOCASE)
    WHERE is_archived = 0;

CREATE TABLE transactions_new (
    id TEXT PRIMARY KEY CHECK (length(id) = 36),
    kind TEXT NOT NULL CHECK (kind IN (
        'income', 'expense', 'expense_refund', 'transfer', 'opening', 'adjustment'
    )),
    amount_minor INTEGER NOT NULL CHECK (amount_minor BETWEEN 1 AND 99999999999999),
    currency_code TEXT NOT NULL DEFAULT 'CNY' CHECK (currency_code IN ('AED','AFN','ALL','AMD','AOA','ARS','AUD','AWG','AZN','BAM','BBD','BDT','BHD','BIF','BMD','BND','BOB','BOV','BRL','BSD','BTN','BWP','BYN','BZD','CAD','CDF','CHE','CHF','CHW','CLF','CLP','CNY','COP','COU','CRC','CUP','CVE','CZK','DJF','DKK','DOP','DZD','EGP','ERN','ETB','EUR','FJD','FKP','GBP','GEL','GHS','GIP','GMD','GNF','GTQ','GYD','HKD','HNL','HTG','HUF','IDR','ILS','INR','IQD','IRR','ISK','JMD','JOD','JPY','KES','KGS','KHR','KMF','KPW','KRW','KWD','KYD','KZT','LAK','LBP','LKR','LRD','LSL','LYD','MAD','MDL','MGA','MKD','MMK','MNT','MOP','MRU','MUR','MVR','MWK','MXN','MXV','MYR','MZN','NAD','NGN','NIO','NOK','NPR','NZD','OMR','PAB','PEN','PGK','PHP','PKR','PLN','PYG','QAR','RON','RSD','RUB','RWF','SAR','SBD','SCR','SDG','SEK','SGD','SHP','SLE','SOS','SRD','SSP','STN','SVC','SYP','SZL','THB','TJS','TMT','TND','TOP','TRY','TTD','TWD','TZS','UAH','UGX','USD','USN','UYI','UYU','UYW','UZS','VED','VES','VND','VUV','WST','XAD','XAF','XCD','XCG','XOF','XPF','YER','ZAR','ZMW','ZWG')),
    to_amount_minor INTEGER CHECK (to_amount_minor BETWEEN 1 AND 99999999999999),
    to_currency_code TEXT CHECK (to_currency_code IN ('AED','AFN','ALL','AMD','AOA','ARS','AUD','AWG','AZN','BAM','BBD','BDT','BHD','BIF','BMD','BND','BOB','BOV','BRL','BSD','BTN','BWP','BYN','BZD','CAD','CDF','CHE','CHF','CHW','CLF','CLP','CNY','COP','COU','CRC','CUP','CVE','CZK','DJF','DKK','DOP','DZD','EGP','ERN','ETB','EUR','FJD','FKP','GBP','GEL','GHS','GIP','GMD','GNF','GTQ','GYD','HKD','HNL','HTG','HUF','IDR','ILS','INR','IQD','IRR','ISK','JMD','JOD','JPY','KES','KGS','KHR','KMF','KPW','KRW','KWD','KYD','KZT','LAK','LBP','LKR','LRD','LSL','LYD','MAD','MDL','MGA','MKD','MMK','MNT','MOP','MRU','MUR','MVR','MWK','MXN','MXV','MYR','MZN','NAD','NGN','NIO','NOK','NPR','NZD','OMR','PAB','PEN','PGK','PHP','PKR','PLN','PYG','QAR','RON','RSD','RUB','RWF','SAR','SBD','SCR','SDG','SEK','SGD','SHP','SLE','SOS','SRD','SSP','STN','SVC','SYP','SZL','THB','TJS','TMT','TND','TOP','TRY','TTD','TWD','TZS','UAH','UGX','USD','USN','UYI','UYU','UYW','UZS','VED','VES','VND','VUV','WST','XAD','XAF','XCD','XCG','XOF','XPF','YER','ZAR','ZMW','ZWG')),
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
    CHECK ((kind='transfer' AND to_amount_minor IS NOT NULL AND to_currency_code IS NOT NULL)
        OR (kind<>'transfer' AND to_amount_minor IS NULL AND to_currency_code IS NULL)),
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

INSERT INTO transactions_new(id,kind,amount_minor,currency_code,source,book_id,category_id,payment_method_id,occurred_on,occurrence_precision,time_period,occurred_at_utc,time_zone,counterparty,merchant,location,note,source_text,original_transaction_id,adjustment_reason,balance_before_minor,balance_target_minor,import_batch_id,import_file_digest,import_mapping_hash,import_source_row,external_source,external_transaction_id,created_at_utc,updated_at_utc,version,deleted_at_utc,to_amount_minor,to_currency_code) SELECT id,kind,amount_minor,currency_code,source,book_id,category_id,payment_method_id,occurred_on,occurrence_precision,time_period,occurred_at_utc,time_zone,counterparty,merchant,location,note,source_text,original_transaction_id,adjustment_reason,balance_before_minor,balance_target_minor,import_batch_id,import_file_digest,import_mapping_hash,import_source_row,external_source,external_transaction_id,created_at_utc,updated_at_utc,version,deleted_at_utc,CASE WHEN kind='transfer' THEN amount_minor ELSE NULL END,CASE WHEN kind='transfer' THEN currency_code ELSE NULL END FROM transactions;

DROP TABLE transactions;

ALTER TABLE transactions_new RENAME TO transactions;

CREATE INDEX ix_transactions_active_book_date
    ON transactions(book_id, occurred_on) WHERE deleted_at_utc IS NULL;

CREATE INDEX ix_transactions_active_category_date
    ON transactions(category_id, occurred_on) WHERE deleted_at_utc IS NULL;

CREATE INDEX ix_transactions_active_date_kind
    ON transactions(occurred_on, kind) WHERE deleted_at_utc IS NULL;

CREATE INDEX ix_transactions_active_refund_original
    ON transactions(original_transaction_id) WHERE deleted_at_utc IS NULL;

CREATE INDEX ix_transactions_import_batch ON transactions(import_batch_id);

CREATE INDEX ix_transactions_payment_method ON transactions(payment_method_id);

CREATE UNIQUE INDEX uq_transactions_external_id
    ON transactions(external_source, external_transaction_id)
    WHERE external_source IS NOT NULL;

CREATE UNIQUE INDEX uq_transactions_import_source_row
    ON transactions(import_file_digest, import_mapping_hash, import_source_row)
    WHERE import_file_digest IS NOT NULL;

CREATE TABLE audit_events_new (
    id TEXT PRIMARY KEY CHECK (length(id) = 36),
    request_id TEXT NOT NULL REFERENCES command_receipts(request_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    entity_type TEXT NOT NULL CHECK (entity_type IN (
        'book', 'account', 'category', 'payment_method', 'tag',
        'transaction', 'attachment', 'import_batch', 'rate', 'currency_settings', 'capture'
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

INSERT INTO audit_events_new(id,request_id,entity_type,entity_id,action,old_version,new_version,before_json,after_json,created_at_utc) SELECT id,request_id,entity_type,entity_id,action,old_version,new_version,before_json,after_json,created_at_utc FROM audit_events;

DROP TABLE audit_events;

ALTER TABLE audit_events_new RENAME TO audit_events;

CREATE INDEX ix_audit_events_created ON audit_events(created_at_utc);

CREATE INDEX ix_audit_events_entity ON audit_events(entity_type, entity_id, created_at_utc);

CREATE INDEX ix_audit_events_request ON audit_events(request_id);

CREATE TABLE change_log_new (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_event_id TEXT NOT NULL UNIQUE REFERENCES audit_events(id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    entity_type TEXT NOT NULL CHECK (entity_type IN (
        'book', 'account', 'category', 'payment_method', 'tag',
        'transaction', 'attachment', 'import_batch', 'rate', 'currency_settings', 'capture'
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

INSERT INTO change_log_new(seq,audit_event_id,entity_type,entity_id,version,operation,changed_at_utc) SELECT seq,audit_event_id,entity_type,entity_id,version,operation,changed_at_utc FROM change_log;

DROP TABLE change_log;

ALTER TABLE change_log_new RENAME TO change_log;

CREATE INDEX ix_change_log_entity ON change_log(entity_type, entity_id, version);

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

CREATE TABLE exchange_rates (
    id TEXT PRIMARY KEY CHECK(length(id)=36),
    currency_code TEXT NOT NULL CHECK(currency_code IN ('AED','AFN','ALL','AMD','AOA','ARS','AUD','AWG','AZN','BAM','BBD','BDT','BHD','BIF','BMD','BND','BOB','BOV','BRL','BSD','BTN','BWP','BYN','BZD','CAD','CDF','CHE','CHF','CHW','CLF','CLP','CNY','COP','COU','CRC','CUP','CVE','CZK','DJF','DKK','DOP','DZD','EGP','ERN','ETB','EUR','FJD','FKP','GBP','GEL','GHS','GIP','GMD','GNF','GTQ','GYD','HKD','HNL','HTG','HUF','IDR','ILS','INR','IQD','IRR','ISK','JMD','JOD','JPY','KES','KGS','KHR','KMF','KPW','KRW','KWD','KYD','KZT','LAK','LBP','LKR','LRD','LSL','LYD','MAD','MDL','MGA','MKD','MMK','MNT','MOP','MRU','MUR','MVR','MWK','MXN','MXV','MYR','MZN','NAD','NGN','NIO','NOK','NPR','NZD','OMR','PAB','PEN','PGK','PHP','PKR','PLN','PYG','QAR','RON','RSD','RUB','RWF','SAR','SBD','SCR','SDG','SEK','SGD','SHP','SLE','SOS','SRD','SSP','STN','SVC','SYP','SZL','THB','TJS','TMT','TND','TOP','TRY','TTD','TWD','TZS','UAH','UGX','USD','USN','UYI','UYU','UYW','UZS','VED','VES','VND','VUV','WST','XAD','XAF','XCD','XCG','XOF','XPF','YER','ZAR','ZMW','ZWG') AND currency_code<>'CNY'),
    effective_on TEXT NOT NULL CHECK(length(effective_on)=10),
    rate_text TEXT NOT NULL CHECK(length(rate_text) BETWEEN 1 AND 26),
    note TEXT NOT NULL DEFAULT '' CHECK(length(note)<=1000),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version>=1),
    UNIQUE(currency_code,effective_on)
) STRICT;
CREATE INDEX ix_exchange_rates_date ON exchange_rates(currency_code,effective_on DESC);
CREATE TABLE currency_settings (
    id TEXT PRIMARY KEY CHECK(length(id)=36),
    display_currency TEXT NOT NULL CHECK(display_currency IN ('AED','AFN','ALL','AMD','AOA','ARS','AUD','AWG','AZN','BAM','BBD','BDT','BHD','BIF','BMD','BND','BOB','BOV','BRL','BSD','BTN','BWP','BYN','BZD','CAD','CDF','CHE','CHF','CHW','CLF','CLP','CNY','COP','COU','CRC','CUP','CVE','CZK','DJF','DKK','DOP','DZD','EGP','ERN','ETB','EUR','FJD','FKP','GBP','GEL','GHS','GIP','GMD','GNF','GTQ','GYD','HKD','HNL','HTG','HUF','IDR','ILS','INR','IQD','IRR','ISK','JMD','JOD','JPY','KES','KGS','KHR','KMF','KPW','KRW','KWD','KYD','KZT','LAK','LBP','LKR','LRD','LSL','LYD','MAD','MDL','MGA','MKD','MMK','MNT','MOP','MRU','MUR','MVR','MWK','MXN','MXV','MYR','MZN','NAD','NGN','NIO','NOK','NPR','NZD','OMR','PAB','PEN','PGK','PHP','PKR','PLN','PYG','QAR','RON','RSD','RUB','RWF','SAR','SBD','SCR','SDG','SEK','SGD','SHP','SLE','SOS','SRD','SSP','STN','SVC','SYP','SZL','THB','TJS','TMT','TND','TOP','TRY','TTD','TWD','TZS','UAH','UGX','USD','USN','UYI','UYU','UYW','UZS','VED','VES','VND','VUV','WST','XAD','XAF','XCD','XCG','XOF','XPF','YER','ZAR','ZMW','ZWG')),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version>=1)
) STRICT;
INSERT INTO currency_settings(id,display_currency,created_at_utc,updated_at_utc)
VALUES('ce8f77b2-0eb1-5c8b-9072-0d1fb6613491','CNY','1970-01-01T00:00:00.000Z','1970-01-01T00:00:00.000Z');
CREATE TABLE captured_inputs (
    id TEXT PRIMARY KEY CHECK(length(id)=36),
    source_kind TEXT NOT NULL CHECK(source_kind IN ('notification','ocr','voice')),
    source_key TEXT NOT NULL UNIQUE CHECK(length(source_key)=64 AND source_key NOT GLOB '*[^0-9a-f]*'),
    source_label TEXT NOT NULL CHECK(length(source_label) BETWEEN 1 AND 160),
    text TEXT NOT NULL CHECK(length(text) BETWEEN 1 AND 4000),
    suggested_json TEXT NOT NULL CHECK(json_valid(suggested_json) AND json_type(suggested_json)='object'),
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','saved','ignored')),
    transaction_id TEXT REFERENCES transactions(id) ON DELETE RESTRICT,
    image_relative_path TEXT,
    image_size_bytes INTEGER,
    image_sha256 TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK(version>=1),
    CHECK ((state='saved' AND transaction_id IS NOT NULL) OR (state<>'saved' AND transaction_id IS NULL)),
    CHECK ((image_relative_path IS NULL AND image_size_bytes IS NULL AND image_sha256 IS NULL)
        OR (source_kind='ocr' AND image_relative_path IS NOT NULL AND image_size_bytes BETWEEN 1 AND 20971520
            AND length(image_sha256)=64 AND image_sha256 NOT GLOB '*[^0-9a-f]*'))
) STRICT;
CREATE INDEX ix_captured_inputs_state_date ON captured_inputs(state,created_at_utc DESC,id);

