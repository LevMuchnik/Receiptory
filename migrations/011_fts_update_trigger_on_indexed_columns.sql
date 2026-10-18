-- 011_fts_update_trigger_on_indexed_columns.sql
-- documents_au (003) fired on every UPDATE of documents and rewrote the whole
-- search row, the full extracted text included. Rate writes (#72), status
-- changes and exports touch none of the indexed columns, so the trigger now
-- fires only when an UPDATE writes one of them (SQLite: the column is in the
-- SET list, whether or not its value changes). The body is 003's, unchanged.
DROP TRIGGER IF EXISTS documents_au;
CREATE TRIGGER documents_au AFTER UPDATE OF raw_extracted_text, vendor_name, description, document_title, original_filename ON documents BEGIN
    INSERT INTO documents_fts(documents_fts, rowid, raw_extracted_text, vendor_name, description, document_title, original_filename)
    VALUES ('delete', old.id, old.raw_extracted_text, old.vendor_name, old.description, old.document_title, old.original_filename);
    INSERT INTO documents_fts(rowid, raw_extracted_text, vendor_name, description, document_title, original_filename)
    VALUES (new.id, new.raw_extracted_text, new.vendor_name, new.description, new.document_title, new.original_filename);
END;
