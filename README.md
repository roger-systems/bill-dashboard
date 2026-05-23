# Bill Dashboard

A modern desktop bill tracking application built with Python, tkinter, and SQLite. Manage your bills efficiently with an intuitive dashboard, recurring bill support, and comprehensive category management.

## Features

### Dashboard
- **Real-time bill overview** with sections for:
  - Past Due (sorted by oldest first, with days late)
  - Due Today
  - Due This Week
  - Upcoming Bills
- **Per-section totals** (right-justified for quick reference)
- **Last Updated timestamp** at the bottom
- **Minimalist dark theme** with semantic color coding
- **Custom Windows-7 style icon** (calendar + dollar sign design)

### Bill Management
- **Add, edit, and delete bills** via the Manager interface
- **Recurring bill support** (weekly, monthly, yearly, custom patterns)
- **Automatic occurrence materialization** (bills automatically generate future occurrences based on recurrence rules)
- **Date picker interface** for easy date selection (Anchor and End dates)
- **Bill status tracking** (paid/unpaid)

### Category System
- **18 top-level categories** with **154 subcategories** (hierarchical structure)
- **Inline accordion-style picker** with two-level navigation
- **Type-ahead search** for quick category discovery
- **Breadcrumb-style category display** (e.g., "Utilities › Electricity")

### UI/UX Enhancements
- **Native Windows controls** (minimize, maximize, close buttons)
- **Auto-sizing Manager window** that fits content perfectly
- **20% larger fonts** across the UI for better readability
- **Taskbar integration** with custom icon pinning
- **Single-instance enforcement** via named mutex (only one app runs at a time)
- **Tray icon support** with click-to-show behavior

## Requirements

- **Python 3.8+**
- **Pillow (PIL)** — For custom icon generation
- **tkinter** — Included with Python (Windows)
- **SQLite3** — Included with Python
- **Windows 7 or later** (due to Windows API integrations)

## Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/roger-systems/bill-dashboard.git
   cd bill-dashboard
   ```

2. **Install dependencies (if needed):**
   ```bash
   pip install Pillow
   ```

3. **Run the application:**
   ```bash
   python bills.py
   ```

## File Structure

```
bill-dashboard/
├── bills.py                 # Main application (Dashboard, Manager, TrayApp, Database)
├── make_icon.py            # Icon generation script (creates billicon.ico)
├── billicon.ico            # Multi-resolution Windows icon (16, 24, 32, 48, 64, 128, 256px)
├── billicon_preview.png    # 256px preview of the icon
├── billicon_32x.png        # Magnified 32px icon for quality inspection
└── README.md               # This file
```

## Usage

### Starting the App

```bash
python bills.py
```

The dashboard will open automatically. The app runs in the system tray, allowing you to minimize it while keeping it accessible from the taskbar.

### Dashboard Navigation

- **View bills by section** — Scroll through Past Due, Due Today, Due This Week, and Upcoming
- **See per-section totals** — Right-justified at the bottom of each section
- **Check last updated time** — Bottom of the dashboard
- **Click "Manage"** — Opens the Manager window to add/edit/delete bills

### Adding a Bill

1. Click the **"Manage"** button on the dashboard
2. Fill in the bill details:
   - **Name** — Bill name (e.g., "Electric Bill")
   - **Amount** — Bill amount in dollars
   - **Category** — Select from 18 top-level categories and 154 subcategories
   - **Anchor Date** — Click the calendar icon to pick the due date
   - **End Date** — For recurring bills, when to stop generating occurrences
   - **Paid** — Checkbox to mark as paid/unpaid
   - **Recurrence** — (Weekly, Monthly, Yearly, Custom, or None)

3. Click **"Save"** to add the bill

### Editing a Bill

1. Click a bill row in the Manager table
2. Modify the fields
3. Click **"Save"** to update

### Deleting a Bill

1. Click a bill row in the Manager table
2. Click **"Delete"**

### Category Picker

- **Expand/collapse** categories by clicking the ▸/▾ arrow next to each top-level category
- **Search categories** by typing in the search box (optional)
- **Select a subcategory** by clicking its name — the dropdown will close and the selection will appear in the field

## Database

The app uses **SQLite3** with the following structure:

### `bills` table
- `id` — Primary key
- `name` — Bill name
- `amount` — Bill amount (dollars)
- `category` — Category (formatted as "Top › Sub")
- `anchor_date` — First due date (YYYY-MM-DD)
- `end_date` — When to stop recurring (nullable)
- `paid` — Boolean (1 = paid, 0 = unpaid)
- `recurrence` — Recurrence pattern (weekly, monthly, yearly, custom, or null)

### `occurrences` table (auto-generated)
- Stores materialized future bill occurrences based on recurrence rules
- Automatically cleaned up when bills are deleted or updated

## Category System

The app includes a comprehensive category hierarchy:

**Top-Level Categories (18):**
- Utilities
- Transportation
- Groceries & Food
- Entertainment
- Health & Medical
- Insurance
- Housing & Rent
- Subscriptions & Memberships
- Pets
- Education
- Personal Care
- Childcare
- Office & Work
- Hobbies & Sports
- Gifts & Charity
- Miscellaneous
- Debt Payments
- Savings & Investments

**Each category has multiple subcategories** (e.g., Utilities → Electricity, Gas, Water, Internet, Phone, etc.)

## Keyboard Shortcuts

- **Alt+F4** — Close the app
- **Ctrl+S** — Save a bill (in Manager window)

## Troubleshooting

### App won't start
- Ensure Python 3.8+ is installed
- Verify Pillow is installed: `pip install Pillow`
- Check that the database file isn't corrupted

### Icon not showing in taskbar
- Regenerate the icon: `python make_icon.py`
- Restart the application

### Bills appearing twice
- This is due to the recurrence materialization system. The app deduplicates bills by keeping only the earliest unpaid occurrence per bill ID.

### Window too small
- The Manager window auto-sizes to fit content. Try resizing your display or adjusting font scaling.

## Development

### Regenerating the Icon

The custom Windows-7 style icon is generated programmatically:

```bash
python make_icon.py
```

This creates:
- `billicon.ico` — Multi-resolution icon for Windows
- `billicon_preview.png` — 256px preview
- `billicon_32x.png` — Magnified 32px for quality inspection

**Icon Design:**
- Red glossy top bar (calendar style)
- White rounded body
- Embossed red dollar sign ($) with white highlight
- Spiral binding rings (metallic)
- Soft drop shadow
- Supports 16x16 down to 256x256 resolution

## License

This project is open source. Feel free to use, modify, and distribute as needed.

## Author

Created by roger-systems

---

**Last Updated:** May 2026
