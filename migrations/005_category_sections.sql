-- Migration 005: Add section column to categories for issued document types
-- Sections: 'expense' (default), 'issued' (documents user issues), 'other' (non-financial)
-- System categories get NULL section (section-agnostic)
-- Recreates table to change UNIQUE(name) to UNIQUE(name, section)

-- Disable foreign keys for table recreation
PRAGMA foreign_keys = OFF;

-- Step 1: Create new table with UNIQUE(name, section) constraint
CREATE TABLE categories_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    section TEXT,
    is_system INTEGER NOT NULL DEFAULT 0,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    display_order INTEGER,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(name, section)
);

-- Step 2: Copy existing data, setting section based on is_system
INSERT INTO categories_new (id, name, description, section, is_system, is_deleted, display_order, created_at, updated_at)
    SELECT id, name, description,
           CASE WHEN is_system = 1 THEN NULL ELSE 'expense' END,
           is_system, is_deleted, display_order, created_at, updated_at
    FROM categories;

-- Step 3: Drop old table and rename
DROP TABLE categories;
ALTER TABLE categories_new RENAME TO categories;

-- Step 4: Re-enable foreign keys
PRAGMA foreign_keys = ON;

-- Step 5: Verify foreign key integrity
PRAGMA foreign_key_check;

-- Step 6: Rename not_a_receipt to uncategorized
UPDATE categories SET name = 'uncategorized', description = 'Does not fit any category'
    WHERE name = 'not_a_receipt' AND is_system = 1;

-- Step 7: Seed issued document categories (Israeli accounting types)
INSERT OR IGNORE INTO categories (name, description, section, display_order, is_system, is_deleted)
VALUES
    ('Tax Invoice', 'Standard invoice with VAT (חשבונית מס)', 'issued', 1, 0, 0),
    ('Receipt (Issued)', 'Payment confirmation issued to client (קבלה)', 'issued', 2, 0, 0),
    ('Tax Invoice-Receipt', 'Combined invoice and payment confirmation (חשבונית מס קבלה)', 'issued', 3, 0, 0),
    ('Credit Note', 'Document that cancels or reduces a previous invoice (חשבונית מס זיכוי)', 'issued', 4, 0, 0),
    ('Transaction Invoice', 'Invoice without VAT for exempt transactions (חשבון עסקה)', 'issued', 5, 0, 0),
    ('Work Order', 'Order for work or services (הזמנת עבודה)', 'issued', 6, 0, 0),
    ('Price Quote', 'Price quote or estimate before issuing invoice (הצעת מחיר)', 'issued', 7, 0, 0);
