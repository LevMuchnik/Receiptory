-- 004_add_source_url.sql
-- Add source_url column to track documents fetched from URLs
ALTER TABLE documents ADD COLUMN source_url TEXT DEFAULT NULL;
