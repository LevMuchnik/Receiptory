-- Add original_filename to FTS5 index for better search coverage

-- Drop old triggers
DROP TRIGGER IF EXISTS documents_ai;
DROP TRIGGER IF EXISTS documents_ad;
DROP TRIGGER IF EXISTS documents_au;

-- Drop old FTS table and recreate with original_filename
DROP TABLE IF EXISTS documents_fts;

CREATE VIRTUAL TABLE documents_fts USING fts5(
    raw_extracted_text,
    vendor_name,
    description,
    document_title,
    original_filename,
    content='documents',
    content_rowid='id'
);

-- Rebuild FTS index from existing data
INSERT INTO documents_fts(rowid, raw_extracted_text, vendor_name, description, document_title, original_filename)
SELECT id, raw_extracted_text, vendor_name, description, document_title, original_filename FROM documents;

-- Recreate triggers with original_filename
CREATE TRIGGER documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid, raw_extracted_text, vendor_name, description, document_title, original_filename)
    VALUES (new.id, new.raw_extracted_text, new.vendor_name, new.description, new.document_title, new.original_filename);
END;

CREATE TRIGGER documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, raw_extracted_text, vendor_name, description, document_title, original_filename)
    VALUES ('delete', old.id, old.raw_extracted_text, old.vendor_name, old.description, old.document_title, old.original_filename);
END;

CREATE TRIGGER documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, raw_extracted_text, vendor_name, description, document_title, original_filename)
    VALUES ('delete', old.id, old.raw_extracted_text, old.vendor_name, old.description, old.document_title, old.original_filename);
    INSERT INTO documents_fts(rowid, raw_extracted_text, vendor_name, description, document_title, original_filename)
    VALUES (new.id, new.raw_extracted_text, new.vendor_name, new.description, new.document_title, new.original_filename);
END;
