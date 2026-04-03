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
    ('Office & Supplies', 'Office supplies, stationery, printer ink, small equipment', 10),
    ('Subscriptions & Software', 'SaaS, software licenses, cloud services, streaming, gym, memberships', 11),
    ('Hardware & Equipment', 'Computers, monitors, phones, peripherals, furniture', 12),
    ('Travel', 'Flights, trains, taxis, public transport, tolls, parking', 13),
    ('Car Fuel', 'Gasoline, diesel, EV charging', 14),
    ('Car Maintenance', 'Vehicle repairs, servicing, tires, car wash, registration, insurance', 15),
    ('Accommodation', 'Hotels, Airbnb, lodging for business trips', 16),
    ('Meals & Entertainment', 'Business meals, client dinners, coffee meetings', 17),
    ('Groceries', 'Supermarket, food, household consumables', 18),
    ('Clothing', 'Clothes, shoes, accessories', 19),
    ('Home & Garden', 'Home repairs, maintenance, appliances, garden supplies', 20),
    ('Communication', 'Phone plans, internet service, SIM cards', 21),
    ('Professional Services', 'Accountant, lawyer, consultant, freelancer fees, translation services', 22),
    ('Insurance', 'Business insurance, professional liability, health insurance', 23),
    ('Education & Training', 'Courses, conferences, books, certifications, professional development', 24),
    ('Marketing & Advertising', 'Online ads, print ads, promotional materials, business cards, website costs', 25),
    ('Rent & Workspace', 'Office rent, coworking space, home office expenses', 26),
    ('Banking & Finance', 'Bank fees, payment processing fees, currency exchange, credit card fees', 27),
    ('Taxes & Government', 'Tax payments, license fees, permits, government charges, municipal fees', 28),
    ('Medical', 'Health-related expenses', 29),
    ('Children & Family', 'Childcare, school fees, kids activities, baby supplies', 30),
    ('Pets', 'Vet visits, pet food, grooming', 31),
    ('Donations & Gifts', 'Charitable donations, gifts', 32),
    ('Personal', 'Personal purchases not related to business activity', 33),
    ('Utilities', 'Electricity, water, gas, waste disposal', 34),
    ('Shipping & Delivery', 'Courier, postal services, package delivery, customs fees', 35),
    ('Other', 'Expenses that do not fit any other category', 99);

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
