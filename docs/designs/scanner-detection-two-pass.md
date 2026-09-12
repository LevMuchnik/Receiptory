# Design: detect on the raw frame first, and let long receipts past the area floor

Written 2026-09-12, from an on-device report: the live outline stopped locking on a receipt.
Branch: fix/scanner-detect-preprocessing (stacked on fix/scanner-scanic-1.6, PR #37)
Status: IMPLEMENTED, NOT deployed. Needs an on-device verdict (S26 Ultra) before it ships.

## Problem

A 2026-09-12 capture of a long till slip on a wood desk would not lock. The same frame fails
identically on scanic 1.0.6, so this is not the upgrade: both causes are ours.

## Root causes

**1. Our preprocessing blocks scanic.** `classical-detector.preprocess` rewrites the frame
before scanic sees it: `shadowNormalize` (grayscale ÷ heavily blurred copy) plus a
`saturationPrior` mask, composed at `saturationWeight`. On the failing frame it is the
difference between finding nothing and finding the receipt:

| Frame #50, detector config | Result |
|---|---|
| shipped (preprocessing on) | nothing found; the quad returned is not the receipt (IoU 0.00) |
| preprocessing off, pinned scanic options | receipt found, IoU 0.755 |
| preprocessing off, scanic defaults | receipt found, IoU 0.903 |

It is not simply bad: over the 32 labelled corpus frames, preprocessing ON scores 1 correct /
2 wrong / 29 none, and OFF scores 6 / 3 / 23 — but neither dominates. Frame #47 (the Naya
receipt beside a white pad) needs it ON; #50 needs it OFF. The four preprocessing params are a
live tuning surface (see `mobile-scanner-detection-and-capture.md`), so the answer is not to
delete them.

**2. The area floor cannot see a till slip.** `minAreaFraction` 0.12 rejected a receipt
covering 11.9% of the frame — while it spanned 56% of the frame's height. Held far enough away
to fit in frame, a long slip is mostly background by area. The floor exists to reject small
noise, and by area alone it cannot tell the two apart.

## Decision

**Raw frame first, preprocessed frame as fallback.** `detect` runs a pass with `shadowNorm` and
`saturationPrior` forced off; only if that produces no accepted quad does it run the configured
(preprocessed) pass. The new `rawPassFirst` param (default true) turns the behaviour off, which
is what keeps the Lab's A/B honest: with it left on, both panels would run the same raw pass and
agree on every frame that pass accepts — exactly the comparison the Lab exists to make. Panel B
now seeds `rawPassFirst: false` with both preprocessing steps off, and "preprocessing ON, no raw
pass" stays expressible, so the measurement below can be reproduced with shipped code.

**A narrow exemption from the area floor**, all four required together:

```ts
spanFraction >= minSpanFraction (0.5)   // reaches half of a frame dimension
&& aspect     >= minSpanAspect   (2.5)  // and is receipt-shaped, not a blob
&& aspect     <= maxSpanAspect   (8)    // and not a strip: a table edge is 12:1
&& areaFraction >= minSpanAreaFraction (0.09)  // and has not vanished
```

The ceiling and the 0.09 floor are not decoration. At the first draft's 0.06 with no ceiling, a
500x45px strip in an 800x450 detection frame cleared every gate (6% area, span 0.64, convexity
1.0, 90-degree corners): a table edge, a keyboard row, a strip light. That is worse than an
ordinary wrong box, because a table edge does not move — the smoother locks on it, the badge
reads "Document detected", and `noDetectionStreak` resets so the "shoot anyway" escape hatch
never appears. A silent bad capture behind a confident green UI. Both new bounds refuse it
independently, and a test pins the exact shape.

`maxAreaFraction` is untouched: length buys nothing at the upper bound, or the scanner would
lock onto the whole photo.

Rejected alternatives, both measured:
- **Just lower `minAreaFraction`** (0.10 / 0.08 / 0.06 / 0.05 / 0.04). Never fixed the failing
  frame at any threshold — the receipt was not found at all with preprocessing on — and added
  wrong boxes.
- **The span rule unqualified** (`area >= 0.12 OR span >= 0.5`). Fixes the frame, but costs 2
  more wrong boxes than the elongation-qualified form.
- **Preprocessing off entirely.** Loses #47, a real capture of the owner's own receipt.

## Evidence

Measured with the app's own `ClassicalDetector` in headless Chromium over 50 corpus frames
(32 labelled), shipped code from `b97b5a8` versus this branch, both against scanic 1.6.0.

| | Correct | Wrong box | No box | #50 | #52 | Median | p90 |
|---|---|---|---|---|---|---|---|
| Shipped | 1 | 2 | 29 | missed | 0.99 | 71ms | 90ms |
| **This branch** | **6** | 3 | 23 | **0.76** | **1.00** | 91ms | 121ms |

Per-frame: #5, #6, #7, #18, #26 gained correct boxes (IoU 0.92-0.97); #21 became a wrong box;
#12 changed rejection reason only. Recent 4K captures #42/#43/#44/#47 are unchanged or better.
(Timings are same-run comparisons on one machine; absolute values drift with load, the ratio
does not.)

The trade is deliberate: a wrong box costs a drag in review, and review now draws exactly what
gets filed. A missing box costs the whole capture. The exception is a wrong box that never
moves, which is why the exemption has an aspect ceiling.

## Cost

The early return fires only on an ACCEPTED quad, so every empty or rejected frame pays both
scanic passes — and an empty frame is the live viewfinder's steady state while the user is still
hunting for the receipt. Over the corpus that is most frames, which is why the median rose
rather than fell. `preprocess` itself runs once, not twice (it returns the input untouched when
both flags are off); the doubled work is scanic's contour pass plus one extra 800x450 canvas per
frame.

The coupling that needs an on-device check: `smoother.ts` sets `staleMs` 250 against
`MIN_DETECT_INTERVAL_MS` 80, and its own comment says to re-check one when the other moves.
Headroom was 250 − (80 + 90) = 80ms; it is now 250 − (80 + 121) = 49ms, measured in headless
Chromium rather than on an S26 Ultra. Any frame above ~170ms of detect latency breaches it and
the smoother resets the lock, which is the documented "box goes jumpy" failure. If that shows up
on device, `rawPassFirst: false` disables the second pass outright, or the fallback can be
rate-limited to every Nth frame.

## Open

- On-device verdict pending. The instrument fix from 2026-09-04 means overlay geometry is
  trustworthy now, but the verdict is still the owner's.
- The review screen's "No document found" panel sits over the image and reads as a popup
  demanding a click. Separate work; fixing detection removes most of its appearances.
