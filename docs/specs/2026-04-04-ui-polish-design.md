# Receiptory UI Polish — Design Spec

## Goal

Restyle the entire Receiptory frontend for visual consistency, density, and polish. Dense data-driven aesthetic (Vercel/GitHub style) with Slate + Blue color palette. Must work on desktop and mobile, light and dark modes. No structural or functional changes — purely visual.

## Design System

### Color Palette

**Dark mode:**
- Background: `#0f172a` (slate-900)
- Surface/cards: `#1e293b` (slate-800)
- Surface raised: `#334155` (slate-700) — for inputs, hover states
- Border: `#334155` (slate-700)
- Text primary: `#e2e8f0` (slate-200)
- Text secondary: `#94a3b8` (slate-400)
- Text muted: `#64748b` (slate-500)
- Accent: `#3b82f6` (blue-500)
- Accent hover: `#2563eb` (blue-600)

**Light mode:**
- Background: `#f8fafc` (slate-50)
- Surface/cards: `#ffffff`
- Surface raised: `#f1f5f9` (slate-100) — for inputs, hover states
- Border: `#e2e8f0` (slate-200)
- Text primary: `#0f172a` (slate-900)
- Text secondary: `#475569` (slate-600)
- Text muted: `#94a3b8` (slate-400)
- Accent: `#2563eb` (blue-600)
- Accent hover: `#1d4ed8` (blue-700)

**Status colors (both modes):**
- Processed: `#22c55e` (green-500) — badge bg, white text
- Pending: `#f59e0b` (amber-500) — badge bg, black text
- Failed: `#ef4444` (red-500) — badge bg, white text
- Needs review: `#8b5cf6` (violet-500) — badge bg, white text
- Processing: `#3b82f6` (blue-500) — badge bg, white text

### Typography

- **Single font family:** Inter for everything (drop Manrope headline font)
- **Hierarchy via weight and size only:**
  - Page titles: 18px, font-weight 700
  - Section headers: 14px, font-weight 600
  - Body text: 14px, font-weight 400
  - Small/meta text: 12px, font-weight 400 or 500
  - Stat numbers: 24px, font-weight 800
  - Table data: 13px, font-weight 400
  - Monospace for dates and amounts: `font-variant-numeric: tabular-nums` on Inter (no separate mono font needed)

### Spacing & Density

- **Card padding:** `p-4` (16px) everywhere, down from `p-6`
- **Table row height:** `py-2` (8px vertical padding), down from `py-3`
- **Section gaps:** `gap-4` (16px), down from `gap-6`
- **Form field gaps:** `gap-3` (12px)
- **Border radius:** `rounded-lg` (8px) for cards, `rounded-md` (6px) for inputs/badges, `rounded-sm` (4px) for small elements

### Elevation

- **No shadows** on cards or surfaces. Use `border` (1px solid `border` color) for all container boundaries.
- **Exception:** Dropdowns/popovers keep a subtle shadow for depth: `0 4px 16px rgba(0,0,0,0.12)` light / `0 4px 16px rgba(0,0,0,0.4)` dark.

## Component Changes

### Sidebar (Sidebar.tsx)

- **Always dark** — `slate-900` background regardless of theme mode
- **Width:** Keep 240px (16rem) on desktop
- **Logo area:** App name "Receiptory" in 16px semibold white, no decorations or gradients
- **Nav items:** Icon (20px Material Symbol) + label (14px). Default: `slate-400` text. Hover: `slate-200` text + `slate-800` bg. Active: white text + `blue-500` left border (3px) + `slate-800/50` bg
- **Upload button:** At sidebar bottom, full-width, outline style with `slate-600` border + `slate-300` text. Hover fills slightly
- **Mobile bottom nav:** Same palette — `slate-900` bg, `slate-400` icons, `blue-500` active
- **Mobile overlay:** `slate-900` bg with `slate-800` border-right

### Header (in App.tsx/AppLayout)

- Remove `glass-header` / backdrop-blur effect
- **Light mode:** White bg, `slate-200` bottom border
- **Dark mode:** `slate-900` bg, `slate-800` bottom border
- **Left:** Page title only (18px, bold)
- **Right:** Compact search input (only on documents page desktop), theme toggle icon
- **Mobile:** Hamburger + page title centered, no search

### Dashboard (DashboardPage.tsx)

- **Stat cards row:** 4 cards across (desktop), 2x2 (mobile). Each card: bordered container, large number (24px font-weight-800), small label below (12px slate-400), no background color on the card itself. Status color only on the number if meaningful.
- **Recent activity:** Dense list — each row has: date (12px, monospace, muted) | vendor name (14px) | amount right-aligned (14px, monospace, green for income / default for expense). Rows separated by `border-b`. No card wrapper — just a bordered section.
- **Quick actions:** Row of small buttons (icon + label, 13px) — Upload, Scan, Export. Outline style matching sidebar button pattern.
- **Remove** the CTA gradient button and any decorative gradients.
- **Chart section** (if kept): Bordered container, no background fill, muted axis labels.

### Documents Page (DocumentsPage.tsx)

- **Filter bar:** Compact single row. Dropdowns get `slate-800`/white bg with `slate-700`/`slate-200` border. Small clear button per filter.
- **Table columns:** Checkbox (32px) | Date (90px, monospace, tabular-nums) | Vendor (flex) | Amount (100px, right-aligned, monospace) | Category (badge, 13px) | Status (dot + text) | Actions (icon buttons)
- **Status display:** 8px colored dot (`inline-block rounded-full`) + status text (13px). Not a full badge — less visual noise.
- **Row hover:** `slate-800`/`slate-50` background
- **Selected rows:** `blue-500/10` background tint
- **Batch actions bar:** When items selected, sticky bar at top of table. `blue-600` bg, white text, action buttons in white outline.
- **Pagination:** Bottom-right, compact. "1–20 of 47" text + prev/next icon buttons.
- **Refresh button:** Keep as-is, just restyle to outline.
- **Upload overlay (drag-drop):** `blue-500/10` overlay with dashed `blue-500` border.

### Document Detail (DocumentDetailPage.tsx)

- **Split pane:** PDF viewer left (bordered container), metadata form right
- **Metadata form sections:** Group fields under section headers:
  - **Document Info:** Date, title, type, receipt ID, language, confidence
  - **Vendor / Client:** Vendor name, vendor tax ID, client name, client tax ID
  - **Financials:** Subtotal, tax, total, currency, payment method, payment ID
  - **Category & Notes:** Category dropdown, user notes textarea
- Section headers: 12px uppercase `slate-400` with `border-b` below
- **Form inputs:** `slate-800`/white bg, `slate-700`/`slate-200` border, 13px text, `py-1.5 px-3` padding
- **Action buttons** at top: Approve (green outline), Reprocess (blue outline), Delete (red outline). Small size, 13px.

### Settings (SettingsPage.tsx)

- **Tab bar:** Horizontal, underline indicator. Inactive: `slate-400` text. Active: white/`slate-900` text + 2px `blue-500` bottom border.
- **Sections within tabs:** Separated by `border-b` with label + description on left, control on right (side-by-side layout on desktop, stacked on mobile).
- **Toggle switches** for boolean settings (replace checkboxes where appropriate).
- **Input fields:** Match document detail form styling — consistent sizing and colors.
- No card wrappers around individual sections — just rows with dividers.

### Category Manager (CategoryManager.tsx)

- Restyle to match document table density: `py-2` rows, `slate-700`/`slate-200` borders, blue drag handle color.
- System categories section: `slate-500` text, slightly more muted than current.

### Export Page (ExportPage.tsx)

- **Preset buttons:** Outlined, same style as quick-action buttons. Active/selected gets `blue-500` border + `blue-500/10` bg.
- **Date range inputs:** Match form input styling.
- **Export action button:** Primary blue, compact.

### Login Page (LoginPage.tsx)

- Center-aligned card on dark background (`slate-900`)
- Card: `slate-800` bg (dark) / white (light), bordered, `p-8`
- App name at top, clean form inputs, blue primary button
- Remove split layout and gradient decorations

### Status Badges (badge.tsx)

Define consistent badge variants:
```
processed:  bg-green-500     text-white
pending:    bg-amber-500     text-black
failed:     bg-red-500       text-white
review:     bg-violet-500    text-white
processing: bg-blue-500      text-white
draft:      bg-slate-500     text-white
```
Small size: `text-xs px-2 py-0.5 rounded-md font-medium`

### Buttons (button.tsx)

- **Primary:** `blue-600` bg, white text. Hover `blue-700`. 
- **Outline:** Transparent bg, `slate-300`/`slate-700` border. Hover fills with `slate-800`/`slate-100`.
- **Ghost:** No border. Hover `slate-800`/`slate-100`.
- **Destructive:** `red-500/10` bg, `red-500` text. Hover `red-500/20`.
- **Size sm:** `h-8 px-3 text-xs` — default for most actions.

### Inputs (input.tsx)

- `h-9 px-3 text-sm rounded-md`
- Dark: `slate-800` bg, `slate-700` border, `slate-200` text
- Light: white bg, `slate-200` border, `slate-900` text  
- Focus: `blue-500` ring (2px)
- Placeholder: `slate-500`

## Global CSS Changes

### index.css

- Replace all CSS custom properties with the Slate + Blue palette
- Remove `--font-headline` (Manrope) — use Inter everywhere
- Remove `.cta-gradient` class
- Remove `.glass-header` class
- Update `.chip-*` classes to match new badge variants
- Update shadow variables: ambient shadow removed (border-based), popover shadow kept

### Responsive Behavior

- All existing breakpoints (sm/md/lg) maintained
- No structural responsive changes — just visual consistency
- Sidebar: always-dark regardless of theme
- Mobile bottom nav: always-dark regardless of theme
- Table: horizontal scroll on mobile preserved

## Files Changed

| File | Change Type |
|------|------------|
| `frontend/src/index.css` | Major: replace entire color system, remove unused classes |
| `frontend/src/App.tsx` | Moderate: restyle header/layout wrapper classes |
| `frontend/src/pages/LoginPage.tsx` | Moderate: simplify layout, remove gradient |
| `frontend/src/pages/DashboardPage.tsx` | Moderate: tighten spacing, restyle stat cards |
| `frontend/src/pages/DocumentsPage.tsx` | Moderate: table density, filter bar, batch bar |
| `frontend/src/pages/DocumentDetailPage.tsx` | Minor: form sections, action buttons |
| `frontend/src/pages/ExportPage.tsx` | Minor: button/input styling |
| `frontend/src/pages/SettingsPage.tsx` | Moderate: tab bar, section layout, toggle switches |
| `frontend/src/components/Sidebar.tsx` | Moderate: always-dark, active state, upload button |
| `frontend/src/components/DocumentTable.tsx` | Moderate: row density, status dots, column sizing |
| `frontend/src/components/FilterBar.tsx` | Minor: input/dropdown styling |
| `frontend/src/components/MetadataForm.tsx` | Minor: section grouping, input sizes |
| `frontend/src/components/CategoryManager.tsx` | Minor: row density, border colors |
| `frontend/src/components/BackupPanel.tsx` | Minor: styling consistency |
| `frontend/src/components/CloudBackupPanel.tsx` | Minor: styling consistency |
| `frontend/src/components/LogViewer.tsx` | Minor: border styling |
| `frontend/src/components/ui/button.tsx` | Minor: update variant colors |
| `frontend/src/components/ui/input.tsx` | Minor: update colors and sizing |
| `frontend/src/components/ui/badge.tsx` | Minor: add status variants |
| `frontend/src/components/ui/tabs.tsx` | Minor: underline style |

## Out of Scope

- No new components
- No new pages or routes
- No backend changes
- No functionality changes
- No animation/transition additions
- Scanner page: minimal changes (fullscreen, already isolated)
