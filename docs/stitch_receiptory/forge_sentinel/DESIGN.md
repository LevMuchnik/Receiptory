```markdown
# Design System Specification: The Architectural Utility

## 1. Overview & Creative North Star
**Creative North Star: The Precision Instrument**

This design system moves away from the "template-heavy" look of standard utility apps toward a sophisticated, editorial-pro aesthetic. We are not just building a dashboard; we are crafting a high-fidelity instrument. The system balances "Data Density" with "Visual Breathing Room" by utilizing intentional asymmetry, layered depth, and an authoritative typographic scale. 

By prioritizing **Tonal Layering** over rigid containment, the interface feels fluid and integrated—mimicking a high-end physical workstation where tools are laid out with purpose, rather than being trapped in boxes.

---

## 2. Colors & Surface Philosophy
The palette is rooted in a deep, dependable navy (`primary`: #162839) contrasted against high-clarity off-whites. 

### The "No-Line" Rule
To achieve a premium feel, **1px solid borders for sectioning are strictly prohibited.** Boundaries must be defined through background color shifts.
*   **Surface-Container-Low (`#f2f4f6`)**: Use for the main workspace background.
*   **Surface-Container-Lowest (`#ffffff`)**: Use for high-priority cards or active work modules. 
The transition from a low-tier surface to a high-tier surface creates a natural, "built-in" edge that is cleaner than any border.

### Surface Hierarchy & Nesting
Treat the UI as a series of physical layers. 
*   **Base:** `surface` (#f7f9fb)
*   **Secondary Panes:** `surface_container` (#eceef0)
*   **Actionable Modules:** `surface_container_highest` (#e0e3e5)

### The Glass & Gradient Rule
For floating elements (modals, dropdowns, or "sticky" headers), use **Glassmorphism**.
*   **Token:** Apply `surface_container_lowest` at 85% opacity with a `backdrop-blur` of 12px.
*   **Signature Textures:** Use a subtle linear gradient on main CTAs: `primary` (#162839) to `primary_container` (#2C3E50) at a 135-degree angle. This adds "soul" and depth to critical touchpoints.

---

## 3. Typography
We utilize a dual-typeface strategy to distinguish between "Command" and "Data."

*   **Display & Headlines (Manrope):** This typeface provides the system’s "Editorial" voice. Its geometric but warm construction feels modern and authoritative.
    *   *Usage:* Page titles, high-level stats, and hero headers.
*   **Body & Labels (Inter):** Chosen for its exceptional legibility at small sizes and high data density.
    *   *Usage:* Data tables, input fields, and technical logs.

**Hierarchy of Authority:**
*   `display-md` (2.75rem / Manrope): Use sparingly for "Total Impact" numbers.
*   `title-sm` (1rem / Inter Bold): Use for section headers to ensure the eye navigates the data grid efficiently.
*   `label-sm` (0.6875rem / Inter Medium): Use for metadata tags and technical timestamps.

---

## 4. Elevation & Depth
In this system, depth is a function of light and stacking, not shadows.

*   **Tonal Layering Principle:** Place a `surface_container_lowest` card on a `surface_container_low` section. The contrast in value (white on light gray) provides sufficient lift.
*   **Ambient Shadows:** If an element must float (e.g., a popover), use an extra-diffused shadow: `box-shadow: 0 8px 32px rgba(25, 28, 30, 0.06)`. The shadow color must be a tinted version of `on_surface`, never pure black.
*   **The "Ghost Border" Fallback:** If a divider is essential for accessibility, use the `outline_variant` token at **15% opacity**. Never use 100% opaque lines.

---

## 5. Components

### Buttons & Interaction
*   **Primary:** Gradient of `primary` to `primary_container`. Border-radius: `md` (0.375rem).
*   **Secondary:** Ghost style. No background, `outline` color for text, and a `surface_variant` hover state.
*   **Tertiary:** Low-key. Text-only with `primary` color; used for "Cancel" or "Back" actions.

### Cards & Data Modules
*   **Forbidden:** Horizontal dividers between rows.
*   **Requirement:** Use vertical whitespace (Spacing Scale `4` or `5`) to separate content blocks. 
*   **Structure:** Use a `surface_container_lowest` background with `lg` (0.5rem) roundedness to house complex data sets.

### Input Fields
*   **Style:** Filled-minimal. Background: `surface_container_high`. 
*   **Focus State:** A 2px bottom-border only using `primary`. No full-box focus rings.
*   **Error State:** Text and bottom-border shift to `error` (#ba1a1a).

### Chips (Status Indicators)
*   **Processed:** Background: `secondary_container` (#7bf8a1), Text: `on_secondary_container` (#007239).
*   **Pending:** Background: `tertiary_fixed`, Text: `on_tertiary_fixed_variant`.
*   **Failed:** Background: `error_container`, Text: `on_error_container`.

---

## 6. Do’s and Don’ts

### Do:
*   **Embrace Asymmetry:** Align primary data to the left and secondary metadata to the right to create a "scannable" editorial flow.
*   **Use Spacing as a Divider:** Use the `8` (1.75rem) spacing token to separate distinct functional modules.
*   **Prioritize Typography:** Use font weight (Bold vs. Regular) and color (`on_surface` vs `on_surface_variant`) to show hierarchy before reaching for a new font size.

### Don't:
*   **Don't use 1px Borders:** This is the quickest way to make a professional tool look like a generic template.
*   **Don't use Pure Black:** Use `on_background` (#191c1e) for text to maintain a premium, "ink-on-paper" feel.
*   **Don't Over-Round:** Keep corner radii between `md` (0.375rem) and `lg` (0.5rem) for a precise, "engineered" look. Avoid `full` rounding except for status chips.

### Accessibility Note:
Ensure all `on_surface_variant` text on `surface` backgrounds maintains a 4.5:1 contrast ratio. If data density requires smaller text (`label-sm`), increase the font-weight to `Medium` (500) to compensate for the smaller scale.```