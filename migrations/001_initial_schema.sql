-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Settings key-value store
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Categories
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    is_system INTEGER NOT NULL DEFAULT 0,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    display_order INTEGER,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Seed system categories
INSERT INTO categories (name, description, is_system, display_order) VALUES
    ('pending', 'Awaiting classification', 1, 0),
    ('not_a_receipt', 'Not a financial document', 1, 1),
    ('failed', 'Processing failed', 1, 2);

-- Seed starter user categories
INSERT INTO categories (name, description, display_order) VALUES
    ('office_supplies', 'Office equipment and supplies', 10),
    ('travel', 'Travel and transportation expenses', 11),
    ('meals', 'Meals and dining expenses', 12),
    ('utilities', 'Utility bills (electricity, water, internet, phone)', 13),
    ('other', 'Uncategorized expenses', 99);

-- Documents
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_type TEXT,
    original_filename TEXT NOT NULL,
    stored_filename TEXT,
    file_hash TEXT UNIQUE NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    page_count INTEGER,
    submission_date TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    submission_channel TEXT NOT NULL DEFAULT 'web_upload',
    sender_identifier TEXT,
    receipt_date TEXT,
    document_title TEXT,
    vendor_name TEXT,
    vendor_tax_id TEXT,
    vendor_receipt_id TEXT,
    client_name TEXT,
    client_tax_id TEXT,
    description TEXT,
    line_items TEXT,
    subtotal REAL,
    tax_amount REAL,
    total_amount REAL,
    currency TEXT,
    converted_amount REAL,
    conversion_rate REAL,
    payment_method TEXT,
    payment_identifier TEXT,
    language TEXT,
    additional_fields TEXT,
    raw_extracted_text TEXT,
    category_id INTEGER REFERENCES categories(id),
    status TEXT NOT NULL DEFAULT 'pending',
    extraction_confidence REAL,
    processing_model TEXT,
    processing_tokens_in INTEGER,
    processing_tokens_out INTEGER,
    processing_cost_usd REAL,
    processing_date TEXT,
    processing_attempts INTEGER NOT NULL DEFAULT 0,
    processing_error TEXT,
    manually_edited INTEGER NOT NULL DEFAULT 0,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    edit_history TEXT DEFAULT '[]',
    user_notes TEXT,
    last_exported_date TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX idx_documents_status ON documents(status);
CREATE INDEX idx_documents_category ON documents(category_id);
CREATE INDEX idx_documents_receipt_date ON documents(receipt_date);
CREATE INDEX idx_documents_hash ON documents(file_hash);

-- FTS5 for full-text search
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    raw_extracted_text,
    vendor_name,
    description,
    document_title,
    content='documents',
    content_rowid='id'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, raw_extracted_text, vendor_name, description, document_title)
    VALUES (new.id, new.raw_extracted_text, new.vendor_name, new.description, new.document_title);
END;

CREATE TRIGGER documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, raw_extracted_text, vendor_name, description, document_title)
    VALUES ('delete', old.id, old.raw_extracted_text, old.vendor_name, old.description, old.document_title);
END;

CREATE TRIGGER documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, raw_extracted_text, vendor_name, description, document_title)
    VALUES ('delete', old.id, old.raw_extracted_text, old.vendor_name, old.description, old.document_title);
    INSERT INTO documents_fts(rowid, raw_extracted_text, vendor_name, description, document_title)
    VALUES (new.id, new.raw_extracted_text, new.vendor_name, new.description, new.document_title);
END;

-- Backups
CREATE TABLE IF NOT EXISTS backups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    completed_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    size_bytes INTEGER,
    destination TEXT,
    error TEXT,
    backup_type TEXT NOT NULL
);
