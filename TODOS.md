# Project TODOs

Deferred items captured during planning and review. Each entry includes the why, current state, and where to start.

---

## Test set as ML fine-tuning corpus

**What:** Once the scanner test set has 500+ annotated frames, evaluate fine-tuning the Sprint 3 ML detector on the user's distribution (Hebrew thermal-paper receipts, his specific lighting, his specific surfaces).

**Why:** Off-the-shelf document-corner-regression models are trained on US/EU office documents and standard receipts. They likely leave accuracy on the table for Hebrew thermal-paper receipts on home-NAS-user surfaces. Fine-tuning on the user's own annotated frames closes that gap.

**Pros:**
- Test-set storage shape (`scanner_test_frames` table + JPEGs + `ground_truth_json`) is already exactly right for export to a training dataset — no schema work needed
- Even modest fine-tuning (last few layers, 100 epochs on 500 frames) tends to lift hit rate noticeably for narrow distributions
- Compounds the value of every annotated frame the user has accumulated

**Cons:**
- Fine-tuning needs a careful eval-on-held-out-split process to avoid overfitting and regressing on out-of-distribution receipts
- Requires a Python training pipeline (PyTorch / ONNX export) — not currently in the stack
- Probably ~1 week of work end-to-end, depending on model choice

**Context:**
- Source: eng review of `docs/plans/2026-05-10-scanner-detection-overhaul.md`, decision D9
- Storage shape: see Sprint 1.5 of that plan (`scanner_test_frames` table)
- Ground truth shape: same as `Quad` type (4 corners, normalized 0–1)
- The Sprint 3 model selection (3.1 in the plan) should prefer architectures with documented fine-tuning recipes — don't pick a model whose weights are fixed-only

**Depends on / blocked by:**
- Sprint 3 ships first and proves off-the-shelf accuracy is meaningful but insufficient
- Test set has accumulated ≥500 annotated frames (likely 2–3 months of daily use post-Sprint 1)
- User has access to a machine that can run Python training (NAS likely cannot; cloud GPU rental or local workstation)
