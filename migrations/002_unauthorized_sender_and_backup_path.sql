-- Add unauthorized_sender system category
INSERT OR IGNORE INTO categories (name, description, is_system, display_order)
VALUES ('unauthorized_sender', 'Document from unauthorized email sender', 1, 3);

-- Add local_path column to backups table for download support
ALTER TABLE backups ADD COLUMN local_path TEXT;
