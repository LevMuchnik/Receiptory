-- 009_add_review_reason.sql
-- Why a document is sitting in needs_review.
--
-- Until now the queue said "Needs Review" and nothing else, so the two reasons
-- (a low or missing extraction confidence, and now a total that disagrees with
-- subtotal + tax) were indistinguishable — and a document flagged at 0.98
-- confidence looked arbitrary.
ALTER TABLE documents ADD COLUMN review_reason TEXT DEFAULT NULL;
