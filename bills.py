r"""
CentsRich — a desktop bill / subscription / expense tracker.

Stack: Python standard library (tkinter + sqlite3). The optional system tray adds
two small packages (pystray, pillow); without them the app still runs one-shot.

Run modes:
    python bills.py            # background tray app: sits in the tray, auto-shows the
                               #   dashboard each morning, stays running between days
    python bills.py --once     # show the floating dashboard once, then exit
    python bills.py --auto     # show once unless already dismissed today, then exit
                               #   (one-shot trigger for Task Scheduler if you skip the tray)
    python bills.py --manage   # open the manager window (add/edit/delete, import/export)
    python bills.py --shortcut # create a desktop shortcut (uses billicon.ico) and exit
    python bills.py --startup  # add a Startup-folder shortcut (auto-launch at login)

App icon:         billicon.ico (sits next to this file). Regenerate it any time
                  with:  python make_icon.py
Tray mode needs:  pip install pystray pillow   (otherwise it falls back to --once)
Notifications:    once-a-day toast at morning_time summarizing overdue / due items.
                  Toggle via the tray menu; each bill has its own "remind days before"
                  lead time (set in the Manager). (Tray mode only.)
Data lives in:    %USERPROFILE%\.centsrich\bills.db
"""

import os
import sys
import json
import sqlite3
import calendar
import threading
import subprocess
from pathlib import Path
from datetime import date, timedelta, datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# Optional system-tray support (pip install pystray pillow). The app still runs
# in one-shot mode without these — it just can't live in the background.
try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

# Windows lets us give the frameless dashboard its own taskbar button and drive
# native minimize/restore. ctypes ships with the standard library, so this adds
# no install step; on other platforms these names are simply unused.
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32
    _user32.GetParent.argtypes = [wintypes.HWND]
    _user32.GetParent.restype = wintypes.HWND
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
    _GetWindowLong = getattr(_user32, "GetWindowLongPtrW", _user32.GetWindowLongW)
    _SetWindowLong = getattr(_user32, "SetWindowLongPtrW", _user32.SetWindowLongW)
    _GetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int]
    _GetWindowLong.restype = ctypes.c_ssize_t
    _SetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _SetWindowLong.restype = ctypes.c_ssize_t

    _GWL_EXSTYLE = -20
    _WS_EX_TOOLWINDOW = 0x00000080
    _WS_EX_APPWINDOW = 0x00040000
    _SW_HIDE, _SW_SHOW, _SW_MINIMIZE = 0, 5, 6

# --------------------------------------------------------------------------- #
#  Paths / constants
# --------------------------------------------------------------------------- #
APP_DIR = Path.home() / ".centsrich"
DB_PATH = APP_DIR / "bills.db"
ICON_PATH = Path(__file__).resolve().with_name("billicon.ico")  # app icon, beside this file
APP_ID = "Royte.CentsRich"  # taskbar identity (groups windows + pinned shortcut)
SHOW_REQUEST = APP_DIR / "show.request"  # a 2nd launch drops this so the running instance pops the dashboard
HORIZON_DAYS_DEFAULT = 90
GRACE_DAYS = 7  # how far back overdue items are still materialized/shown

# Bill type (kind) and frequency are now inferred automatically — the user never
# picks them.  See infer_type() below.  The advance() engine still supports all
# legacy frequency values for backward-compatible recurrence of older records.
# Category taxonomy: top-level -> subcategories. This is the single source of
# truth for the two-level picker. Add/remove categories or subcategories here and
# the picker updates automatically — order is preserved exactly as written.
CATEGORY_TREE = {
    "HOUSING": ["Mortgage / Rent", "Utilities", "Internet", "Phone",
                "Home Supplies", "Maintenance & Repairs", "HOA Fees",
                "Property Management", "Miscellaneous"],
    "AUTO": ["Payment", "Fuel", "Maintenance & Repairs", "Registration",
             "Accessories", "Parking", "Tolls", "Miscellaneous"],
    "BOAT": ["Payment", "Fuel", "Maintenance & Repairs", "Storage / Marina Fees",
             "Registration", "Accessories", "Miscellaneous"],
    "RECREATIONAL VEHICLES": ["Payment", "Fuel", "Maintenance & Repairs",
                              "Registration", "Accessories", "Storage",
                              "Miscellaneous"],
    "INSURANCE": ["Auto", "Home", "Health", "Dental", "Vision", "Life",
                  "Disability", "Umbrella", "Pet", "Identity Theft", "Boat",
                  "Recreational Vehicle"],
    "SHOPPING": ["Clothing", "Shoes", "Accessories", "Beauty & Cosmetics",
                 "Personal Care", "Electronics", "Home Goods", "Gifts",
                 "Hobbies", "Miscellaneous"],
    "ENTERTAINMENT": ["Movies & Theaters", "Concerts & Live Events",
                      "Sports Events", "Streaming Services", "Books & Audiobooks",
                      "Games & Apps", "Events & Activities",
                      "Hobbies & Recreation", "Miscellaneous Entertainment"],
    "PERSONAL CARE": ["Haircuts", "Barber", "Nails", "Spa", "Massage", "Tanning",
                      "Personal Trainer", "Laundry & Dry Cleaning",
                      "Cosmetic Services", "Miscellaneous Personal Care"],
    "TRAVEL": ["Flights", "Hotels", "Rental Cars", "Taxis / Rideshare",
               "Public Transit", "Travel Insurance", "Luggage", "Travel Fees",
               "Miscellaneous Travel"],
    "SUBSCRIPTIONS": ["Streaming Services", "Music Services", "Cloud Storage",
                      "Software", "Memberships", "Apps", "Newspapers & Magazines",
                      "Miscellaneous Subscriptions"],
    "BUSINESS / WORK": ["Supplies", "Software", "Travel", "Meals", "Equipment",
                        "Training", "Inventory & Materials", "Pest Control",
                        "Lawn Care", "Pool Service", "Home Cleaning",
                        "Security Monitoring", "Miscellaneous"],
    "HEALTH & MEDICAL": ["Doctor Visits", "Dental Visits", "Vision Care",
                         "Prescriptions", "Medical Supplies", "Urgent Care",
                         "Hospital Bills", "Therapy", "Chiropractic",
                         "Physical Therapy", "Lab Work", "Miscellaneous Medical"],
    "PETS": ["Food", "Vet Visits", "Medications", "Grooming", "Boarding / Daycare",
             "Toys & Supplies", "Training", "Miscellaneous Pet"],
    "EDUCATION": ["Tuition", "Books & Materials", "School Supplies",
                  "Classes & Courses", "Student Loan Payments", "Testing Fees",
                  "Miscellaneous Education"],
    "CHARITY & DONATIONS": ["Donations", "Tithing", "Fundraisers", "Sponsorships",
                            "Miscellaneous Charity"],
    "LEGAL": ["Attorney Fees", "Court Fees", "Legal Documents", "Notary Services",
              "Miscellaneous Legal"],
    "GOVERNMENT & FEES": ["Permits", "Licenses", "Fines", "Parking Tickets",
                          "Toll Violations", "Miscellaneous Government"],
    "FINANCIAL": ["Bank Fees", "Interest Charges", "Loan Payments",
                  "Credit Card Payments", "Investments", "Savings", "Transfers",
                  "Taxes", "Miscellaneous"],
}

# How a chosen top-level + subcategory is stored in the single `category` field.
# A breadcrumb keeps the subcategory unambiguous (the same leaf name, e.g.
# "Miscellaneous", appears under many top-levels). Change here only if needed.
CATEGORY_SEP = " › "


def format_category(top, sub):
    """Combine a top-level category and a subcategory into the stored value."""
    return f"{top}{CATEGORY_SEP}{sub}"


def infer_type(data: dict) -> dict:
    """Auto-compute *kind* and *frequency* from the form inputs.

    Rules (the user never touches these fields):
      • anchor_date present + SUBSCRIPTIONS category → subscription, monthly
      • anchor_date present (any other category)     → bill, monthly
      • anchor_date absent                           → expense, one-time
        (anchor_date is back-filled with today so the DB NOT-NULL stays happy)
    """
    has_due = bool(data.get("anchor_date"))
    cat_top = (data.get("category") or "").split(CATEGORY_SEP)[0].strip().upper()

    if not has_due:
        data.update(kind="expense", frequency="one-time",
                    anchor_date=date.today().isoformat(),
                    end_date=None, autopay=0, notify=0, notify_days_before=0,
                    custom_interval=None, custom_unit=None)
    elif cat_top == "SUBSCRIPTIONS":
        data.update(kind="subscription", frequency="monthly",
                    custom_interval=None, custom_unit=None)
    else:
        data.update(kind="bill", frequency="monthly",
                    custom_interval=None, custom_unit=None)
    return data


# Dark theme palette
BG = "#1e1f24"
BG_BAR = "#26272e"
BG_ROW = "#24252b"
FG = "#e7e7ea"
FG_MUTED = "#9aa0aa"
RED = "#ef4444"
AMBER = "#f59e0b"
BLUE = "#3b82f6"
GREEN = "#22c55e"
FOREST_GREEN = "#228b22"

FONT_SCALE = 1.2  # global UI text scale (all fonts 20% larger)


# --------------------------------------------------------------------------- #
#  Windows integration (app icon, taskbar identity, desktop shortcut)
# --------------------------------------------------------------------------- #
def set_app_user_model_id():
    """Tell Windows this process is its own app, so the taskbar uses our window
    icon (not the generic python icon) and groups/pins it correctly."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except Exception:
            pass


_singleton_handle = None  # kept alive for the process lifetime to hold the mutex


def acquire_single_instance() -> bool:
    """True if this is the first/only instance; False if one is already running.
    Backed by a named mutex on Windows (held until the process exits)."""
    global _singleton_handle
    if sys.platform != "win32":
        return True
    ERROR_ALREADY_EXISTS = 183
    _singleton_handle = ctypes.windll.kernel32.CreateMutexW(
        None, False, "Local\\CentsRich_singleton")
    return ctypes.windll.kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def request_show():
    """Ask an already-running instance to bring up its dashboard."""
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        SHOW_REQUEST.write_text(datetime.now().isoformat(), encoding="utf-8")
    except Exception:
        pass


def set_window_icon(window, default=False):
    """Apply billicon.ico to a window (title bar + taskbar). No-op if missing."""
    if sys.platform != "win32" or not ICON_PATH.exists():
        return
    try:
        if default:
            window.iconbitmap(default=str(ICON_PATH))  # also applies to later windows
        else:
            window.iconbitmap(str(ICON_PATH))
    except tk.TclError:
        pass


def scale_fonts(root):
    """Enlarge every point-sized font in this interpreter by FONT_SCALE, so all
    windows created from this root render their text proportionally larger."""
    try:
        current = float(root.tk.call("tk", "scaling"))
        root.tk.call("tk", "scaling", current * FONT_SCALE)
    except (tk.TclError, ValueError):
        pass


def tray_image():
    """Prefer the real .ico for the tray; fall back to the drawn glyph."""
    if ICON_PATH.exists():
        try:
            return Image.open(ICON_PATH)
        except Exception:
            pass
    return make_tray_image()


def python_launcher() -> Path:
    """The pythonw.exe next to the current interpreter (console-less launch)."""
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        cand = exe.with_name("pythonw.exe")
        if cand.exists():
            return cand
    return exe


def _write_shortcut(folder_expr: str, tip: str = "") -> None:
    """Create a 'CentsRich.lnk' that launches the tray app (pythonw + custom
    icon) inside the folder named by a PowerShell GetFolderPath() expression.
    Uses the built-in Windows COM scripting host, so it needs no extra packages."""
    if sys.platform != "win32":
        print("Shortcuts are a Windows-only feature.")
        return
    script = Path(__file__).resolve()
    workdir = script.parent
    icon = ICON_PATH if ICON_PATH.exists() else script
    launcher = python_launcher()
    # PowerShell resolves the real folder path (handles OneDrive redirection).
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$dir = {folder_expr}; "
        "$lnk = Join-Path $dir 'CentsRich.lnk'; "
        "$s = $ws.CreateShortcut($lnk); "
        f"$s.TargetPath = '{launcher}'; "
        f"$s.Arguments = '\"{script}\"'; "
        f"$s.WorkingDirectory = '{workdir}'; "
        f"$s.IconLocation = '{icon}'; "
        "$s.Description = 'CentsRich'; "
        "$s.Save(); "
        "Write-Output $lnk"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, check=True,
        )
        print("Created shortcut:", out.stdout.strip())
        if tip:
            print(tip)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        detail = getattr(e, "stderr", "") or str(e)
        print("Could not create the shortcut:", detail)


def create_desktop_shortcut() -> None:
    """Desktop shortcut for launching the tray app on demand."""
    _write_shortcut(
        "[Environment]::GetFolderPath('Desktop')",
        tip="Tip: right-click it -> 'Pin to taskbar' (or 'Show more options' "
            "on Windows 11) to pin CentsRich.")


def create_startup_shortcut() -> None:
    """Startup-folder shortcut so the tray app auto-launches at login."""
    _write_shortcut(
        "[Environment]::GetFolderPath('Startup')",
        tip="CentsRich will now start automatically at login. "
            "Remove it any time from the folder opened by:  shell:startup")


# --------------------------------------------------------------------------- #
#  Database
# --------------------------------------------------------------------------- #
def connect() -> sqlite3.Connection:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_column(conn, table, column, ddl):
    """Add a column if a pre-existing database is missing it (lightweight migration)."""
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bills (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT    NOT NULL,
            kind            TEXT    NOT NULL DEFAULT 'bill',
            amount          REAL    NOT NULL DEFAULT 0,
            currency        TEXT    NOT NULL DEFAULT 'USD',
            category        TEXT,
            frequency       TEXT    NOT NULL,
            custom_interval INTEGER,
            custom_unit     TEXT,
            anchor_date     TEXT    NOT NULL,
            end_date        TEXT,
            autopay         INTEGER NOT NULL DEFAULT 0,
            notify          INTEGER NOT NULL DEFAULT 1,
            notify_days_before INTEGER NOT NULL DEFAULT 1,
            notes           TEXT,
            active          INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
            updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS occurrences (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            bill_id     INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
            due_date    TEXT    NOT NULL,
            amount      REAL    NOT NULL,
            paid        INTEGER NOT NULL DEFAULT 0,
            paid_date   TEXT,
            amount_paid REAL,
            UNIQUE(bill_id, due_date)
        );
        CREATE INDEX IF NOT EXISTS idx_occ_due  ON occurrences(due_date);
        CREATE INDEX IF NOT EXISTS idx_occ_paid ON occurrences(paid);

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    # migrations for databases created before a column existed
    _ensure_column(conn, "bills", "notify", "notify INTEGER NOT NULL DEFAULT 1")
    _ensure_column(conn, "bills", "notify_days_before",
                   "notify_days_before INTEGER NOT NULL DEFAULT 1")
    defaults = {
        "horizon_days": str(HORIZON_DAYS_DEFAULT),
        "last_dismissed_date": "",
        "last_shown_date": "",
        "morning_time": "08:00",
        "notifications": "1",       # toast reminders on/off (tray mode only)
        "notify_days_before": "1",  # also remind for items due within N days
        "last_notified_date": "",
    }
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    conn.commit()


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
#  Recurrence engine
# --------------------------------------------------------------------------- #
def add_months(d: date, n: int) -> date:
    """Add n months, clamping to month-end (Jan 31 + 1 month -> Feb 28/29)."""
    month_index = d.month - 1 + n
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def advance(d: date, bill: sqlite3.Row):
    freq = bill["frequency"]
    if freq == "weekly":
        return d + timedelta(weeks=1)
    if freq == "monthly":
        return add_months(d, 1)
    if freq == "yearly":
        return add_months(d, 12)
    if freq == "custom":
        n = bill["custom_interval"] or 1
        unit = bill["custom_unit"] or "months"
        if unit == "days":
            return d + timedelta(days=n)
        if unit == "weeks":
            return d + timedelta(weeks=n)
        if unit == "months":
            return add_months(d, n)
        if unit == "years":
            return add_months(d, 12 * n)
    return None  # one-time


def occurrences_between(bill, start_from: date, horizon: date):
    """Yield ISO due-dates for a bill from start_from..horizon (correct phase)."""
    anchor = date.fromisoformat(bill["anchor_date"])
    end = date.fromisoformat(bill["end_date"]) if bill["end_date"] else None

    if bill["frequency"] == "one-time":
        if anchor <= horizon and (end is None or anchor <= end):
            yield anchor.isoformat()
        return

    cur = anchor
    # fast-forward to the first occurrence >= start_from while keeping phase
    guard = 0
    while cur < start_from:
        nxt = advance(cur, bill)
        if nxt is None or nxt <= cur:
            return
        cur = nxt
        guard += 1
        if guard > 10000:
            return

    guard = 0
    while cur <= horizon:
        if end and cur > end:
            return
        yield cur.isoformat()
        nxt = advance(cur, bill)
        if nxt is None or nxt <= cur:
            return
        cur = nxt
        guard += 1
        if guard > 5000:
            return


def materialize_all(conn) -> None:
    """Idempotently create occurrence rows for every active bill up to horizon."""
    today = date.today()
    horizon_days = int(get_setting(conn, "horizon_days", HORIZON_DAYS_DEFAULT))
    horizon = today + timedelta(days=horizon_days)
    start_from = today - timedelta(days=GRACE_DAYS)

    bills = conn.execute("SELECT * FROM bills WHERE active = 1").fetchall()
    for b in bills:
        for due in occurrences_between(b, start_from, horizon):
            paid = 1 if (b["autopay"] and date.fromisoformat(due) < today) else 0
            conn.execute(
                "INSERT OR IGNORE INTO occurrences "
                "(bill_id, due_date, amount, paid, paid_date) VALUES (?,?,?,?,?)",
                (b["id"], due, b["amount"], paid, due if paid else None),
            )
    conn.commit()


# --------------------------------------------------------------------------- #
#  Services
# --------------------------------------------------------------------------- #
def get_dashboard(conn):
    today = date.today().isoformat()
    week_end = (date.today() + timedelta(days=7)).isoformat()
    rows = conn.execute(
        """
        SELECT o.id, o.bill_id, o.due_date, o.amount, o.paid,
               b.name, b.category, b.currency
          FROM occurrences o JOIN bills b ON b.id = o.bill_id
         WHERE o.paid = 0
         ORDER BY o.due_date ASC, b.name ASC
        """
    ).fetchall()
    # Show each bill only once, keyed by its unique bill id: keep the earliest
    # unpaid occurrence so recurring bills don't repeat. Same-named bills with
    # different ids remain separate. Sections are mutually exclusive by date, so
    # a bill lands in exactly one of them.
    seen, unique = set(), []
    for r in rows:
        if r["bill_id"] in seen:
            continue
        seen.add(r["bill_id"])
        unique.append(r)
    past_due = [r for r in unique if r["due_date"] < today]
    due_today = [r for r in unique if r["due_date"] == today]
    due_week = [r for r in unique if today < r["due_date"] <= week_end]
    upcoming = [r for r in unique if r["due_date"] > week_end]
    # every section sorted by due date ascending (Past Due shows oldest first)
    for section in (past_due, due_today, due_week, upcoming):
        section.sort(key=lambda r: r["due_date"])
    return past_due, due_today, due_week, upcoming


def mark_paid(conn, occ_id: int, paid: bool):
    today = date.today().isoformat()
    conn.execute(
        "UPDATE occurrences SET paid=?, paid_date=?, "
        "amount_paid=CASE WHEN ?=1 THEN amount ELSE NULL END WHERE id=?",
        (1 if paid else 0, today if paid else None, 1 if paid else 0, occ_id),
    )
    conn.commit()


def items_due_for_notice(conn):
    """Unpaid occurrences overdue or due within each bill's own notify_days_before."""
    today = date.today().isoformat()
    return conn.execute(
        """
        SELECT o.due_date, o.amount, b.name
          FROM occurrences o JOIN bills b ON b.id = o.bill_id
         WHERE o.paid = 0 AND b.active = 1 AND b.notify = 1
           AND o.due_date <= date(?, '+' || COALESCE(b.notify_days_before, 1) || ' days')
         ORDER BY o.due_date ASC, b.name ASC
        """,
        (today,),
    ).fetchall()


def build_notice(rows):
    """Turn due rows into a (title, body) pair for a toast notification."""
    today = date.today().isoformat()
    overdue = [r for r in rows if r["due_date"] < today]
    due_today = [r for r in rows if r["due_date"] == today]
    soon = [r for r in rows if r["due_date"] > today]

    headline = []
    if overdue:
        headline.append(f"{len(overdue)} overdue")
    if due_today:
        headline.append(f"{len(due_today)} due today")
    if soon:
        headline.append(f"{len(soon)} due soon")
    title = "CentsRich: " + (", ".join(headline) if headline else "nothing due")

    total = sum(r["amount"] for r in rows)
    names = ", ".join(r["name"] for r in rows[:4])
    if len(rows) > 4:
        names += f", +{len(rows) - 4} more"
    body = f"{names}  -  ${total:,.2f} total"
    return title, body


def save_bill(conn, data: dict):
    data = infer_type(data)  # auto-fill kind, frequency, custom_* from the inputs
    fields = (
        "name", "kind", "amount", "category", "frequency", "custom_interval",
        "custom_unit", "anchor_date", "end_date", "autopay", "notify",
        "notify_days_before", "notes", "active",
    )
    if data.get("id"):
        sets = ", ".join(f"{f}=?" for f in fields) + ", updated_at=datetime('now')"
        conn.execute(
            f"UPDATE bills SET {sets} WHERE id=?",
            tuple(data.get(f) for f in fields) + (data["id"],),
        )
    else:
        cols = ", ".join(fields)
        ph = ", ".join("?" for _ in fields)
        conn.execute(
            f"INSERT INTO bills ({cols}) VALUES ({ph})",
            tuple(data.get(f) for f in fields),
        )
    conn.commit()
    materialize_all(conn)


def delete_bill(conn, bill_id: int):
    conn.execute("DELETE FROM occurrences WHERE bill_id=?", (bill_id,))
    conn.execute("DELETE FROM bills WHERE id=?", (bill_id,))
    conn.commit()


def next_due(conn, bill_id: int):
    today = date.today().isoformat()
    row = conn.execute(
        "SELECT MIN(due_date) d FROM occurrences WHERE bill_id=? AND paid=0 AND due_date>=?",
        (bill_id, today),
    ).fetchone()
    return row["d"] if row and row["d"] else "-"


def export_data(conn, path: str):
    payload = {
        "bills": [dict(r) for r in conn.execute("SELECT * FROM bills").fetchall()],
        "occurrences": [dict(r) for r in conn.execute("SELECT * FROM occurrences").fetchall()],
        "settings": [dict(r) for r in conn.execute("SELECT * FROM settings").fetchall()],
    }
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def import_data(conn, path: str):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    conn.execute("DELETE FROM occurrences")
    conn.execute("DELETE FROM bills")
    for b in payload.get("bills", []):
        cols = ", ".join(b.keys())
        ph = ", ".join("?" for _ in b)
        conn.execute(f"INSERT INTO bills ({cols}) VALUES ({ph})", tuple(b.values()))
    for s in payload.get("settings", []):
        set_setting(conn, s["key"], s["value"])
    conn.commit()
    materialize_all(conn)


# --------------------------------------------------------------------------- #
#  Floating dashboard window
# --------------------------------------------------------------------------- #
class Dashboard:
    def __init__(self, parent, conn, on_close=None):
        self.conn = conn
        self.on_close = on_close
        self.win = tk.Toplevel(parent)
        win = self.win
        win.title("CentsRich Command Center")
        set_window_icon(win)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.98)
        win.configure(bg=BG)

        # window-state tracking for the custom min/max/restore controls
        self._maximized = False
        self._normal_geometry = None  # size+pos remembered before maximizing

        w, h, margin = 360, 600, 16
        sw = win.winfo_screenwidth()
        win.geometry(f"{w}x{h}+{sw - w - margin}+{margin}")

        self._build_titlebar()
        self._build_body()
        self.refresh()
        win.lift()
        win.focus_force()
        # Give the borderless window its own taskbar button so the OS can
        # minimize/restore it natively (Windows only; harmless no-op elsewhere).
        if sys.platform == "win32":
            win.after(20, self._init_taskbar_button)

    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def _build_titlebar(self):
        bar = tk.Frame(self.win, bg=BG_BAR, height=40)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        title = tk.Label(bar, text="  CentsRich", bg=BG_BAR, fg=FG,
                         font=("Segoe UI", round(11 * 1.2), "bold"))
        title.pack(side="left", padx=6)

        # Window controls, packed right-to-left so they read [min][max][close]
        # left-to-right with close in the far corner (standard Windows order).
        close = tk.Label(bar, text="✕", bg=BG_BAR, fg=FG_MUTED,
                         font=("Segoe UI", 11), cursor="hand2")
        close.pack(side="right", padx=(4, 10))
        close.bind("<Button-1>", lambda e: self.dismiss())
        self._hover(close, RED)

        self.max_btn = tk.Label(bar, text="□", bg=BG_BAR, fg=FG_MUTED,
                                font=("Segoe UI", 11), cursor="hand2")
        self.max_btn.pack(side="right", padx=4)
        self.max_btn.bind("<Button-1>", lambda e: self._toggle_maximize())
        self._hover(self.max_btn, FG)

        self.min_btn = tk.Label(bar, text="—", bg=BG_BAR, fg=FG_MUTED,
                                font=("Segoe UI", 11), cursor="hand2")
        self.min_btn.pack(side="right", padx=4)
        self.min_btn.bind("<Button-1>", lambda e: self._minimize())
        self._hover(self.min_btn, FG)

        manage = tk.Label(bar, text="Manage", bg=BG_BAR, fg="white",
                          font=("Segoe UI", 8), cursor="hand2")
        manage.pack(side="right", padx=4)
        manage.bind("<Button-1>", lambda e: open_manager(self.win, self.conn, self.refresh))

        # drag the frameless window by its title bar
        for widget in (bar, title):
            widget.bind("<Button-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._on_drag)

    def _start_drag(self, e):
        self._dx, self._dy = e.x, e.y

    def _on_drag(self, e):
        x = self.win.winfo_x() + e.x - self._dx
        y = self.win.winfo_y() + e.y - self._dy
        self.win.geometry(f"+{x}+{y}")

    # --- window controls (custom, since the frame is borderless) ----------- #
    def _hover(self, widget, color):
        """Light up a control on hover, dim it again on leave."""
        widget.bind("<Enter>", lambda e: widget.config(fg=color), add="+")
        widget.bind("<Leave>", lambda e: widget.config(fg=FG_MUTED), add="+")

    def _hwnd(self):
        """The top-level OS window handle backing this Toplevel."""
        self.win.update_idletasks()
        hwnd = _user32.GetParent(self.win.winfo_id())
        return hwnd if hwnd else self.win.winfo_id()

    def _init_taskbar_button(self):
        # Owned Tk Toplevels share the (hidden) root window's taskbar button, so
        # a minimized borderless window has no button to click and looks "lost".
        # Setting WS_EX_APPWINDOW forces it to get its own taskbar button.
        try:
            hwnd = self._hwnd()
            style = _GetWindowLong(hwnd, _GWL_EXSTYLE)
            style = (style & ~_WS_EX_TOOLWINDOW) | _WS_EX_APPWINDOW
            _SetWindowLong(hwnd, _GWL_EXSTYLE, style)
            # the new ex-style only registers once the window is re-shown
            _user32.ShowWindow(hwnd, _SW_HIDE)
            _user32.ShowWindow(hwnd, _SW_SHOW)
            self.win.attributes("-topmost", True)
            self.win.lift()
        except Exception:
            pass  # worst case: no dedicated taskbar button, but never lost

    def _minimize(self):
        # Native minimize to the taskbar. overrideredirect stays on, so the
        # window keeps its custom frame when restored from the taskbar.
        if sys.platform == "win32":
            try:
                _user32.ShowWindow(self._hwnd(), _SW_MINIMIZE)
                return
            except Exception:
                pass
        try:
            self.win.iconify()
        except tk.TclError:
            pass

    def _toggle_maximize(self):
        if self._maximized:
            self._restore()
        else:
            self._maximize()

    def _maximize(self):
        self._normal_geometry = self.win.geometry()
        self.win.update_idletasks()
        try:
            mw, mh = self.win.wm_maxsize()  # work area (screen minus taskbar)
        except tk.TclError:
            mw = self.win.winfo_screenwidth()
            mh = self.win.winfo_screenheight()
        self.win.geometry(f"{mw}x{mh}+0+0")
        self._maximized = True
        self.max_btn.config(text="❐")  # restore glyph

    def _restore(self):
        if self._normal_geometry:
            self.win.geometry(self._normal_geometry)
        self._maximized = False
        self.max_btn.config(text="□")  # maximize glyph

    def _build_body(self):
        # scrollable area
        container = tk.Frame(self.win, bg=BG)
        container.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(container, bg=BG, highlightthickness=0)
        sb = tk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.body = tk.Frame(self.canvas, bg=BG)
        self.body.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self._body_window = self.canvas.create_window(
            (0, 0), window=self.body, anchor="nw", width=344)
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        # keep the inner frame as wide as the canvas so rows reflow on resize/maximize
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self._body_window, width=e.width),
        )
        # only grab the mouse wheel while the pointer is over this window
        self.canvas.bind("<Enter>",
                         lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>",
                         lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120), "units")

    def refresh(self):
        for child in self.body.winfo_children():
            child.destroy()

        past_due, due_today, due_week, upcoming = get_dashboard(self.conn)

        self._section("Past Due", past_due, RED)
        self._section("Due Today", due_today, FOREST_GREEN)
        self._section("Due This Week", due_week, AMBER)
        self._section("Upcoming", upcoming, BLUE)

        # Last Updated stamp — rewritten on every refresh (add/pay/recalc)
        ts = datetime.now().strftime("%b %d, %Y  %I:%M:%S %p")
        tk.Label(self.body, text=f"Last Updated: {ts}", bg=BG, fg=FG_MUTED,
                 font=("Segoe UI", 8), anchor="w").pack(fill="x", padx=14, pady=(12, 14))

    def _section(self, title, items, color):
        head = tk.Frame(self.body, bg=BG)
        head.pack(fill="x", padx=14, pady=(12, 2))
        tk.Label(head, text=title.upper(), bg=BG, fg=color,
                 font=("Segoe UI", 12, "bold")).pack(side="left")
        tk.Label(head, text=f" {len(items)}", bg=BG, fg=FG_MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        # section total, right-justified
        section_total = sum(r["amount"] for r in items)
        tk.Label(head, text=f"${section_total:,.2f}", bg=BG, fg=color,
                 font=("Segoe UI", 12, "bold")).pack(side="right")

        if not items:
            tk.Label(self.body, text="Nothing here \U0001F389", bg=BG, fg="#5a5d66",
                     font=("Segoe UI", 9), anchor="w").pack(fill="x", padx=18, pady=2)
            return

        today = date.today().isoformat()
        for r in items:
            row = tk.Frame(self.body, bg=BG_ROW)
            row.pack(fill="x", padx=10, pady=2)

            var = tk.IntVar(value=r["paid"])
            cb = tk.Checkbutton(
                row, variable=var, bg=BG_ROW, activebackground=BG_ROW,
                selectcolor=BG_BAR, bd=0, highlightthickness=0,
                command=lambda oid=r["id"], v=var: self._toggle(oid, v),
            )
            cb.pack(side="left", padx=(6, 2))

            due = r["due_date"]
            if due < today:  # Past Due:  MM/DD (X days late)
                d = date.fromisoformat(due)
                days_late = (date.today() - d).days
                label = f"{d.month:02d}/{d.day:02d} ({days_late} days late)"
            else:
                label = due[5:]
            tk.Label(row, text=r["name"], bg=BG_ROW, fg=FG, anchor="w",
                     font=("Segoe UI", 10)).pack(side="left", fill="x", expand=True)
            tk.Label(row, text=f"${r['amount']:,.2f}", bg=BG_ROW, fg=FG,
                     font=("Segoe UI", 10, "bold")).pack(side="right", padx=(4, 8))
            tk.Label(row, text=label, bg=BG_ROW, fg=FG_MUTED,
                     font=("Segoe UI", 9)).pack(side="right", padx=4)

    def _toggle(self, occ_id, var):
        mark_paid(self.conn, occ_id, bool(var.get()))
        self.refresh()

    def dismiss(self):
        set_setting(self.conn, "last_dismissed_date", date.today().isoformat())
        if self.on_close:
            self.on_close()
        self.win.destroy()


# --------------------------------------------------------------------------- #
#  Calendar date picker (pure tkinter — no extra packages)
# --------------------------------------------------------------------------- #
class DatePicker(tk.Toplevel):
    """A small popup calendar. Click a day to write YYYY-MM-DD into `var`."""
    WEEK = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"]
    _cal = calendar.Calendar(firstweekday=6)  # Sunday-first weeks

    def __init__(self, anchor_widget, var, allow_clear=False):
        super().__init__(anchor_widget)
        self.var = var
        self.allow_clear = allow_clear
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=BG_BAR, highlightbackground="#3a3b44", highlightthickness=1)
        try:
            d = date.fromisoformat(var.get())
        except (ValueError, TypeError):
            d = date.today()
        self.year, self.month = d.year, d.month
        self.grid_frame = tk.Frame(self, bg=BG_BAR)
        self.grid_frame.pack(padx=6, pady=6)
        self._build()
        self.update_idletasks()
        self.geometry(f"+{anchor_widget.winfo_rootx()}"
                      f"+{anchor_widget.winfo_rooty() + anchor_widget.winfo_height()}")
        self.grab_set()
        self.bind("<Escape>", lambda e: self.destroy())

    def _build(self):
        for w in self.grid_frame.winfo_children():
            w.destroy()
        g = self.grid_frame

        head = tk.Frame(g, bg=BG_BAR)
        head.grid(row=0, column=0, columnspan=7, sticky="ew", pady=(0, 4))
        tk.Button(head, text="‹", command=lambda: self._shift(-1), bg=BG_ROW, fg=FG,
                  bd=0, width=3, activebackground=BLUE).pack(side="left")
        tk.Label(head, text=f"{calendar.month_name[self.month]} {self.year}",
                 bg=BG_BAR, fg=FG, font=("Segoe UI", 10, "bold")).pack(side="left", expand=True)
        tk.Button(head, text="›", command=lambda: self._shift(1), bg=BG_ROW, fg=FG,
                  bd=0, width=3, activebackground=BLUE).pack(side="right")

        for c, wd in enumerate(self.WEEK):
            tk.Label(g, text=wd, bg=BG_BAR, fg=FG_MUTED,
                     font=("Segoe UI", 8, "bold"), width=3).grid(row=1, column=c)

        today = date.today()
        try:
            selected = date.fromisoformat(self.var.get())
        except (ValueError, TypeError):
            selected = None
        for r, week in enumerate(self._cal.monthdayscalendar(self.year, self.month), 2):
            for c, day in enumerate(week):
                if day == 0:
                    tk.Label(g, text="", bg=BG_BAR, width=3).grid(row=r, column=c)
                    continue
                d = date(self.year, self.month, day)
                bg = GREEN if selected and d == selected else (BLUE if d == today else BG_ROW)
                tk.Button(g, text=str(day), width=3, bd=0, bg=bg, fg=FG,
                          activebackground=BLUE,
                          command=lambda dd=d: self._choose(dd)).grid(
                              row=r, column=c, padx=1, pady=1)

        foot = tk.Frame(g, bg=BG_BAR)
        foot.grid(row=99, column=0, columnspan=7, sticky="ew", pady=(4, 0))
        tk.Button(foot, text="Today", command=lambda: self._choose(date.today()),
                  bg=BG_ROW, fg=FG, bd=0, padx=8).pack(side="left")
        if self.allow_clear:
            tk.Button(foot, text="Clear", command=self._clear,
                      bg=BG_ROW, fg=FG, bd=0, padx=8).pack(side="left", padx=4)
        tk.Button(foot, text="Cancel", command=self.destroy,
                  bg=BG_ROW, fg=FG, bd=0, padx=8).pack(side="right")

    def _shift(self, n):
        m = self.month - 1 + n
        self.year += m // 12
        self.month = m % 12 + 1
        self._build()

    def _choose(self, d):
        self.var.set(d.isoformat())
        self.destroy()

    def _clear(self):
        self.var.set("")
        self.destroy()


# --------------------------------------------------------------------------- #
#  Category picker (inline accordion dropdown — lives inside the Manager form)
# --------------------------------------------------------------------------- #
class InlineCategoryDropdown:
    """An inline, in-form category selector (no separate window). Clicking the
    Category field expands a dropdown anchored to it: top-level categories are
    shown as an accordion; tapping one twists open its subcategories beneath it
    (two levels deep, one open at a time). Picking a subcategory writes the value
    and collapses. A small search box filters across everything. Built entirely
    from CATEGORY_TREE, so the taxonomy is never renamed, reordered, or flattened."""

    WIDTH = 290
    MAX_H = 300

    def __init__(self, parent, anchor, var):
        self.parent = parent      # the Manager window
        self.anchor = anchor      # the Category entry the dropdown hangs from
        self.var = var
        self.is_open = False
        self.expanded = None      # currently twisted-open top-level (accordion)
        self.frame = None
        self._final = None
        anchor.bind("<Button-1>", lambda e: self.toggle())
        parent.bind("<Button-1>", self._outside_click, add="+")

    # --- open / close ----------------------------------------------------- #
    def toggle(self):
        self.close() if self.is_open else self.open()

    def open(self):
        if self.is_open:
            return
        self.is_open = True
        self.expanded = None

        self.frame = tk.Frame(self.parent, bg=BG_BAR,
                              highlightbackground="#3a3b44", highlightthickness=1)
        sb = tk.Frame(self.frame, bg=BG_BAR)
        sb.pack(fill="x")
        tk.Label(sb, text="Search", bg=BG_BAR, fg=FG_MUTED,
                 font=("Segoe UI", 8)).pack(side="left", padx=(8, 4), pady=5)
        self.q_var = tk.StringVar()
        self.q_var.trace_add("write", lambda *a: self._build())
        self.q_entry = tk.Entry(sb, textvariable=self.q_var, bg=BG_ROW, fg=FG, bd=0,
                                insertbackground=FG, font=("Segoe UI", 10))
        self.q_entry.pack(side="left", fill="x", expand=True, padx=(0, 8), pady=5, ipady=3)

        body = tk.Frame(self.frame, bg=BG)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg=BG, highlightthickness=0)
        bar = tk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.list = tk.Frame(self.canvas, bg=BG)
        self.list.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self._winid = self.canvas.create_window((0, 0), window=self.list, anchor="nw")
        self.canvas.bind("<Configure>",
                         lambda e: self.canvas.itemconfigure(self._winid, width=e.width))
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")
        self.canvas.bind("<Enter>",
                         lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>",
                         lambda e: self.canvas.unbind_all("<MouseWheel>"))

        self._build()

        # Position the dropdown under the field; flip above it if there's no room.
        self.parent.update_idletasks()
        ax = self.anchor.winfo_rootx() - self.parent.winfo_rootx()
        ay = self.anchor.winfo_rooty() - self.parent.winfo_rooty()
        ah = self.anchor.winfo_height()
        win_h = self.parent.winfo_height()
        content_h = min(self.list.winfo_reqheight() + 44, self.MAX_H)
        below = win_h - (ay + ah) - 8
        above = ay - 8
        if below >= content_h or below >= above:
            top_y, height, bottom_fixed = ay + ah, max(80, min(content_h, below)), False
        else:
            height = max(80, min(content_h, above))
            top_y, bottom_fixed = ay - height, True
        self.frame.lift()
        self._final = (ax, self.WIDTH, top_y, height, bottom_fixed)
        self._slide(opening=True)
        # search is optional: the accordion is usable immediately, no focus stolen

    def close(self):
        if not self.is_open or not self.frame:
            return
        self.is_open = False
        try:
            self.canvas.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass
        self._slide(opening=False)

    def _slide(self, opening):
        # Subtle slide: animate the dropdown height (top fixed when dropping down,
        # bottom fixed when dropping up). On close, shrink to nothing and destroy.
        frame = self.frame
        ax, w, top_y, h_final, bottom_fixed = self._final
        bottom = top_y + h_final
        steps = 5

        def step(i):
            if not frame.winfo_exists():
                return
            h = max(1, int(h_final * i / steps))
            y = (bottom - h) if bottom_fixed else top_y
            frame.place(x=ax, y=y, width=w, height=h)
            if opening and i < steps:
                self.parent.after(12, lambda: step(i + 1))
            elif not opening and i > 0:
                self.parent.after(12, lambda: step(i - 1))
            elif not opening:
                frame.destroy()
                if self.frame is frame:
                    self.frame = None

        step(1 if opening else steps)

    # --- list building ---------------------------------------------------- #
    def _wheel(self, e):
        if self.canvas.winfo_exists():
            self.canvas.yview_scroll(int(-e.delta / 120), "units")

    def _build(self):
        if not self.list or not self.list.winfo_exists():
            return
        for w in self.list.winfo_children():
            w.destroy()
        self.canvas.yview_moveto(0)

        q = self.q_var.get().strip().lower()
        if q:  # search overrides the accordion with flat breadcrumb results
            matches = [(t, s) for t, subs in CATEGORY_TREE.items() for s in subs
                       if q in s.lower() or q in t.lower()]
            if not matches:
                tk.Label(self.list, text="No matches", bg=BG, fg=FG_MUTED,
                         font=("Segoe UI", 9), anchor="w").pack(fill="x", padx=14, pady=8)
            for t, s in matches:
                self._row(format_category(t, s), lambda tt=t, ss=s: self._select(tt, ss))
            return

        for top in CATEGORY_TREE:
            opened = self.expanded == top
            self._row(top, lambda tt=top: self._toggle_expand(tt),
                      twistie=("▾" if opened else "▸"), bold=True)
            if opened:  # accordion: reveal this category's subcategories inline
                for sub in CATEGORY_TREE[top]:
                    self._row(sub, lambda tt=top, ss=sub: self._select(tt, ss), indent=True)
        self._row("Clear category", self._clear, fg=FG_MUTED)

    def _row(self, text, command, twistie=None, indent=False, bold=False, fg=FG):
        row = tk.Frame(self.list, bg=BG_ROW, cursor="hand2")
        row.pack(fill="x", padx=6, pady=1)
        cells = [row]
        if twistie is not None:
            tw = tk.Label(row, text=twistie, bg=BG_ROW, fg=FG_MUTED,
                          font=("Segoe UI", 9), width=2)
            tw.pack(side="left", padx=(6, 0))
            cells.append(tw)
        left = 28 if indent else (2 if twistie is not None else 12)
        lbl = tk.Label(row, text=text, bg=BG_ROW, fg=fg, anchor="w",
                       font=("Segoe UI", 10, "bold" if bold else "normal"))
        lbl.pack(side="left", fill="x", expand=True, padx=(left, 8), pady=6)
        cells.append(lbl)

        def on_click(e, cmd=command):
            cmd()
            return "break"  # stop the click from reaching the outside-click handler

        for w in cells:
            w.bind("<Button-1>", on_click)
            w.bind("<Enter>", lambda e, cs=cells: [c.config(bg=BG_BAR) for c in cs])
            w.bind("<Leave>", lambda e, cs=cells: [c.config(bg=BG_ROW) for c in cs])

    # --- actions ---------------------------------------------------------- #
    def _toggle_expand(self, top):
        self.expanded = None if self.expanded == top else top
        self._build()

    def _select(self, top, sub):
        self.var.set(format_category(top, sub))
        self.close()

    def _clear(self):
        self.var.set("")
        self.close()

    def _outside_click(self, e):
        if not self.is_open or not self.frame:
            return
        w = e.widget
        if isinstance(w, str):
            return  # a just-destroyed/unknown widget — don't close on ambiguity
        if w is self.anchor:
            return  # the anchor's own binding toggles
        node = w
        while node is not None:
            if node is self.frame:
                return
            node = getattr(node, "master", None)
        self.close()


# --------------------------------------------------------------------------- #
#  Manager window (CRUD + import/export)
# --------------------------------------------------------------------------- #
def open_manager(parent, conn, on_change=None):
    win = tk.Toplevel(parent)
    win.title("CentsRich — Command Center")
    win.configure(bg=BG)
    set_window_icon(win)

    # Prominent header so the brand is readable even when the OS title bar is small
    hdr = tk.Frame(win, bg=BG_BAR)
    hdr.pack(fill="x")
    tk.Label(hdr, text="CentsRich Command Center", bg=BG_BAR, fg=FG,
             font=("Segoe UI", 16, "bold")).pack(
                 padx=14, pady=8)

    style = ttk.Style(win)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("Treeview", background=BG_ROW, fieldbackground=BG_ROW,
                    foreground=FG, rowheight=round(24 * FONT_SCALE))
    style.configure("Treeview.Heading", background=BG_BAR, foreground=FG)

    cols = ("name", "type", "amount", "next", "remind", "active")
    tree = ttk.Treeview(win, columns=cols, show="headings", height=10)
    for c, txt, w in [
        ("name", "Name", 160), ("type", "Type", 95), ("amount", "Amount", 90),
        ("next", "Next Due", 100), ("remind", "Remind", 95), ("active", "Active", 55),
    ]:
        tree.heading(c, text=txt)
        tree.column(c, width=round(w * FONT_SCALE), anchor="w")
    tree.pack(fill="both", expand=True, padx=10, pady=10)

    # --- form ---
    form = tk.Frame(win, bg=BG)
    form.pack(fill="x", padx=10, pady=4)
    vars_ = {
        "id": tk.StringVar(),
        "name": tk.StringVar(),
        "amount": tk.StringVar(value="0"),
        "category": tk.StringVar(),
        "anchor_date": tk.StringVar(value=date.today().isoformat()),
        "end_date": tk.StringVar(),
        "autopay": tk.IntVar(),
        "notify": tk.IntVar(value=1),
        "notify_days_before": tk.StringVar(value=get_setting(conn, "notify_days_before", "1")),
        "active": tk.IntVar(value=1),
        "notes": tk.StringVar(),
    }

    def field(parent, label, r, c, widget):
        lbl = tk.Label(parent, text=label, bg=BG, fg=FG_MUTED,
                       font=("Segoe UI", 9))
        lbl.grid(row=r, column=c * 2, sticky="e", padx=4, pady=3)
        widget.grid(row=r, column=c * 2 + 1, sticky="w", padx=4, pady=3)
        return lbl  # return the label so callers can modify it later

    # Row 0 — always visible: Name | Amount | auto-inferred type indicator
    field(form, "Name", 0, 0, tk.Entry(form, textvariable=vars_["name"], width=22))
    field(form, "Amount", 0, 1, tk.Entry(form, textvariable=vars_["amount"], width=10))
    type_lbl = tk.Label(form, text="", bg=BG, fg=BLUE,
                        font=("Segoe UI", 9, "bold"))
    type_lbl.grid(row=0, column=4, columnspan=2, sticky="w", padx=8)

    # Row 1 — always visible: Category | Due Date (clearable → expense mode)
    category_entry = tk.Entry(form, textvariable=vars_["category"], width=22,
                              state="readonly", readonlybackground="white", cursor="hand2")
    field(form, "Category", 1, 0, category_entry)
    InlineCategoryDropdown(win, category_entry, vars_["category"])
    anchor_entry = tk.Entry(form, textvariable=vars_["anchor_date"], width=12,
                            state="readonly", readonlybackground="white", cursor="hand2")
    date_lbl = field(form, "Due Date", 1, 1, anchor_entry)
    anchor_entry.bind("<Button-1>",
                      lambda e: DatePicker(anchor_entry, vars_["anchor_date"],
                                           allow_clear=True))

    # Row 2 — bill-only: End Date | Remind days before
    end_entry = tk.Entry(form, textvariable=vars_["end_date"], width=12,
                         state="readonly", readonlybackground="white", cursor="hand2")
    end_lbl = field(form, "End (optional)", 2, 0, end_entry)
    end_entry.bind("<Button-1>",
                   lambda e: DatePicker(end_entry, vars_["end_date"], allow_clear=True))
    remind_entry = tk.Entry(form, textvariable=vars_["notify_days_before"], width=6)
    remind_lbl = field(form, "Remind days before", 2, 1, remind_entry)

    # Row 3 — checkboxes (Autopay/Notify are bill-only; Active is always visible)
    autopay_cb = tk.Checkbutton(form, text="Autopay", variable=vars_["autopay"],
                                bg=BG, fg=FG, selectcolor=BG_BAR, activebackground=BG)
    autopay_cb.grid(row=3, column=1, sticky="w")
    notify_cb = tk.Checkbutton(form, text="Notify", variable=vars_["notify"],
                               bg=BG, fg=FG, selectcolor=BG_BAR, activebackground=BG)
    notify_cb.grid(row=3, column=3, sticky="w")
    tk.Checkbutton(form, text="Active", variable=vars_["active"], bg=BG, fg=FG,
                   selectcolor=BG_BAR, activebackground=BG).grid(row=3, column=5, sticky="w")

    # Row 4 — always visible: Notes
    field(form, "Notes", 4, 0, tk.Entry(form, textvariable=vars_["notes"], width=22))

    # --- dynamic mode: show/hide bill-only fields based on due-date presence ---
    _bill_widgets = []
    for w in (end_lbl, end_entry, remind_lbl, remind_entry, autopay_cb, notify_cb):
        info = {k: v for k, v in w.grid_info().items() if k != "in"}
        _bill_widgets.append((w, info))

    def _update_mode(*_args):
        has_due = bool(vars_["anchor_date"].get().strip())
        cat = vars_["category"].get()
        cat_top = (cat or "").split(CATEGORY_SEP)[0].strip().upper()
        if not has_due:
            type_lbl.config(text="Expense (one-time)", fg=AMBER)
            date_lbl.config(text="Date")
            for w, _ in _bill_widgets:
                w.grid_remove()
        elif cat_top == "SUBSCRIPTIONS":
            type_lbl.config(text="Subscription (monthly)", fg=GREEN)
            date_lbl.config(text="Due Date")
            for w, info in _bill_widgets:
                w.grid(**info)
        else:
            type_lbl.config(text="Bill (monthly)", fg=BLUE)
            date_lbl.config(text="Due Date")
            for w, info in _bill_widgets:
                w.grid(**info)

    vars_["anchor_date"].trace_add("write", _update_mode)
    vars_["category"].trace_add("write", _update_mode)
    _update_mode()  # set initial state

    # --- table / form interaction ---
    def reload_tree():
        tree.delete(*tree.get_children())
        for b in conn.execute("SELECT * FROM bills ORDER BY name").fetchall():
            btype = (b["kind"] or "bill").capitalize()
            if not b["notify"]:
                remind = "off"
            elif (b["notify_days_before"] or 0) == 0:
                remind = "day of"
            else:
                remind = f"{b['notify_days_before']}d before"
            tree.insert("", "end", iid=str(b["id"]), values=(
                b["name"], btype, f"${b['amount']:,.2f}",
                next_due(conn, b["id"]), remind, "yes" if b["active"] else "no",
            ))
        if on_change:
            on_change()

    def clear_form():
        for k, v in vars_.items():
            if isinstance(v, tk.IntVar):
                v.set(1 if k in ("active", "notify") else 0)
            else:
                v.set("")
        vars_["anchor_date"].set(date.today().isoformat())
        vars_["notify_days_before"].set(get_setting(conn, "notify_days_before", "1"))

    def on_select(_e=None):
        sel = tree.selection()
        if not sel:
            return
        b = conn.execute("SELECT * FROM bills WHERE id=?", (int(sel[0]),)).fetchone()
        vars_["id"].set(str(b["id"]))
        vars_["name"].set(b["name"])
        vars_["amount"].set(str(b["amount"]))
        vars_["category"].set(b["category"] or "")
        # Expenses (one-time) → clear due date so the form shows expense mode.
        if b["frequency"] == "one-time" or b["kind"] == "expense":
            vars_["anchor_date"].set("")
        else:
            vars_["anchor_date"].set(b["anchor_date"])
        vars_["end_date"].set(b["end_date"] or "")
        vars_["autopay"].set(b["autopay"])
        vars_["notify"].set(b["notify"] if b["notify"] is not None else 1)
        vars_["notify_days_before"].set(
            str(b["notify_days_before"] if b["notify_days_before"] is not None else 1))
        vars_["active"].set(b["active"])
        vars_["notes"].set(b["notes"] or "")

    tree.bind("<<TreeviewSelect>>", on_select)

    def do_save():
        try:
            amount = float(vars_["amount"].get() or 0)
            remind = max(0, int(vars_["notify_days_before"].get() or 1))
            anchor = vars_["anchor_date"].get().strip()
            if anchor:
                date.fromisoformat(anchor)
            if vars_["end_date"].get():
                date.fromisoformat(vars_["end_date"].get())
        except ValueError:
            messagebox.showerror(
                "Invalid input",
                "Check amount, dates (YYYY-MM-DD), and the numeric fields.")
            return
        if not vars_["name"].get().strip():
            messagebox.showerror("Invalid input", "Name is required.")
            return
        # Subscriptions require a due date
        cat = vars_["category"].get().strip() or None
        cat_top = (cat or "").split(CATEGORY_SEP)[0].strip().upper()
        if cat_top == "SUBSCRIPTIONS" and not anchor:
            messagebox.showwarning(
                "Due date required",
                "Subscriptions are recurring and need a due date.\n"
                "Please set a due date, or choose a different category.")
            return
        data = {
            "id": int(vars_["id"].get()) if vars_["id"].get() else None,
            "name": vars_["name"].get().strip(),
            "amount": amount,
            "category": cat,
            "anchor_date": anchor or None,
            "end_date": vars_["end_date"].get().strip() or None,
            "autopay": vars_["autopay"].get(),
            "notify": vars_["notify"].get(),
            "notify_days_before": remind,
            "notes": vars_["notes"].get().strip() or None,
            "active": vars_["active"].get(),
        }
        save_bill(conn, data)
        clear_form()
        reload_tree()

    def do_delete():
        if not vars_["id"].get():
            return
        if messagebox.askyesno("Delete", "Delete this item and its history?"):
            delete_bill(conn, int(vars_["id"].get()))
            clear_form()
            reload_tree()

    def do_export():
        path = filedialog.asksaveasfilename(defaultextension=".json",
                                            filetypes=[("JSON", "*.json")])
        if path:
            export_data(conn, path)
            messagebox.showinfo("Export", "Data exported.")

    def do_import():
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if path and messagebox.askyesno("Import", "Replace ALL current data with this file?"):
            import_data(conn, path)
            reload_tree()

    btns = tk.Frame(win, bg=BG)
    btns.pack(fill="x", padx=10, pady=8)
    for txt, cmd, col in [
        ("New", clear_form, "#3a3b44"), ("Save", do_save, GREEN),
        ("Delete", do_delete, RED), ("Export", do_export, "#3a3b44"),
        ("Import", do_import, "#3a3b44"),
    ]:
        tk.Button(btns, text=txt, command=cmd, bg=col, fg="white",
                  bd=0, padx=14, pady=6, font=("Segoe UI", 9)).pack(side="left", padx=4)

    reload_tree()

    # Open fully sized to its content (table + form + buttons), accounting for the
    # larger fonts, and keep that as the minimum so nothing is ever clipped. The
    # window stays freely resizable larger; the table/form expand to fill.
    win.update_idletasks()
    fit_w = min(max(win.winfo_reqwidth(), 720), win.winfo_screenwidth() - 40)
    fit_h = min(win.winfo_reqheight(), win.winfo_screenheight() - 80)
    win.geometry(f"{fit_w}x{fit_h}")
    win.minsize(fit_w, fit_h)
    return win


# --------------------------------------------------------------------------- #
#  System-tray background app
# --------------------------------------------------------------------------- #
def make_tray_image():
    """A small white 'bill with a check' glyph drawn at runtime (no asset file)."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([8, 5, 56, 59], radius=10, fill=(255, 255, 255, 255))
    for y in (18, 28, 38):
        d.line([(18, y), (46, y)], fill=(120, 130, 145, 255), width=3)
    d.line([(19, 47), (28, 54)], fill=(34, 197, 94, 255), width=4)
    d.line([(28, 54), (47, 39)], fill=(34, 197, 94, 255), width=4)
    return img


class TrayApp:
    """Resident background app: persistent hidden root + tray icon + daily scheduler."""

    def __init__(self, conn):
        self.conn = conn
        self.root = tk.Tk()
        self.root.withdraw()
        scale_fonts(self.root)
        set_window_icon(self.root, default=True)
        self.dashboard = None
        # The tray icon runs on its own thread; the menu callbacks below execute
        # there. SQLite connections are not shareable across threads, so the menu
        # reads/writes this in-memory flag and marshals DB writes to the tk thread.
        self.notify_enabled = get_setting(self.conn, "notifications", "1") == "1"
        self.icon = pystray.Icon(
            "CentsRich", tray_image(), "CentsRich",
            menu=pystray.Menu(
                pystray.MenuItem("Show Command Center",
                                 lambda: self.root.after(0, self.show_dashboard),
                                 default=True),
                pystray.MenuItem("Command Center",
                                 lambda: self.root.after(0, self._open_manager)),
                pystray.MenuItem(
                    "Notifications",
                    self._toggle_notifications,
                    checked=lambda item: self.notify_enabled,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", self._quit),
            ),
        )

    # --- UI actions (always invoked on the tkinter thread via root.after) ---
    def show_dashboard(self):
        if self.dashboard and self.dashboard.alive():
            self.dashboard.win.deiconify()
            self.dashboard.win.lift()
            self.dashboard.win.focus_force()
            self.dashboard.refresh()
            return
        materialize_all(self.conn)
        self.dashboard = Dashboard(
            self.root, self.conn,
            on_close=lambda: setattr(self, "dashboard", None),
        )
        set_setting(self.conn, "last_shown_date", date.today().isoformat())

    def _open_manager(self):
        def on_change():
            if self.dashboard and self.dashboard.alive():
                self.dashboard.refresh()
        open_manager(self.root, self.conn, on_change=on_change)

    def _toggle_notifications(self, icon, item):
        # runs on the tray thread: flip the in-memory flag, persist on the tk thread
        self.notify_enabled = not self.notify_enabled
        value = "1" if self.notify_enabled else "0"
        self.root.after(0, lambda: set_setting(self.conn, "notifications", value))
        icon.update_menu()

    # --- scheduling ---
    def _past_morning(self):
        mt = get_setting(self.conn, "morning_time", "08:00")
        hh, mm = (int(x) for x in mt.split(":"))
        now = datetime.now()
        return (now.hour, now.minute) >= (hh, mm)

    def _maybe_show(self, initial=False):
        today = date.today().isoformat()
        if get_setting(self.conn, "last_dismissed_date") == today:
            return
        if not initial:
            if get_setting(self.conn, "last_shown_date") == today:
                return
            if not self._past_morning():
                return
        self.show_dashboard()

    def _maybe_notify(self):
        if not self.notify_enabled:
            return
        today = date.today().isoformat()
        if get_setting(self.conn, "last_notified_date") == today:
            return
        if not self._past_morning():
            return
        materialize_all(self.conn)
        rows = items_due_for_notice(self.conn)
        set_setting(self.conn, "last_notified_date", today)  # once per day, even if empty
        if not rows:
            return
        title, body = build_notice(rows)
        try:
            self.icon.notify(body, title)
        except Exception:
            pass  # never let a toast failure break the loop

    def _poll_show_request(self):
        # Another launch (e.g. clicking the taskbar icon) drops SHOW_REQUEST to
        # ask us to surface the dashboard. Check often so it feels instant.
        try:
            if SHOW_REQUEST.exists():
                SHOW_REQUEST.unlink()
                self.show_dashboard()
        except Exception:
            pass
        self.root.after(700, self._poll_show_request)

    def _tick(self):
        try:
            self._maybe_notify()
            self._maybe_show(initial=False)
        finally:
            self.root.after(60_000, self._tick)  # re-check every minute

    # --- lifecycle ---
    def _quit(self, *_):
        self.icon.stop()
        self.root.after(0, self.root.quit)

    def run(self):
        try:
            SHOW_REQUEST.unlink()  # clear any stale request from a previous run
        except OSError:
            pass
        threading.Thread(target=self.icon.run, daemon=True).start()
        # Show the dashboard on launch so clicking the app icon always opens it.
        self.root.after(800, self.show_dashboard)
        self.root.after(700, self._poll_show_request)
        self._tick()
        self.root.mainloop()


# --------------------------------------------------------------------------- #
#  Entry point
# --------------------------------------------------------------------------- #
def main():
    args = set(sys.argv[1:])

    if "--shortcut" in args:
        create_desktop_shortcut()
        return

    if "--startup" in args:
        create_startup_shortcut()
        return

    set_app_user_model_id()  # must run before any window is created

    # Tray mode (no flags) is meant to be a single resident instance. If one is
    # already running, a fresh launch just asks it to pop the dashboard and exits
    # — so clicking the taskbar icon opens the window instead of stacking copies.
    tray_mode = HAS_TRAY and not ({"--once", "--auto", "--manage"} & args)
    if tray_mode and not acquire_single_instance():
        request_show()
        return

    conn = connect()
    init_db(conn)
    materialize_all(conn)

    if "--manage" in args:
        root = tk.Tk()
        root.withdraw()
        scale_fonts(root)
        set_window_icon(root, default=True)
        win = open_manager(root, conn)
        win.protocol("WM_DELETE_WINDOW", root.destroy)
        root.mainloop()
        return

    one_shot = ("--once" in args) or ("--auto" in args) or not HAS_TRAY
    if one_shot:
        # --auto respects "dismissed today" so a scheduler trigger fires only once a day
        if "--auto" in args and get_setting(conn, "last_dismissed_date") == date.today().isoformat():
            return
        if not HAS_TRAY and not args:
            print("Tip: run 'pip install pystray pillow' to keep CentsRich "
                  "in your system tray instead of relaunching it.")
        root = tk.Tk()
        root.withdraw()
        scale_fonts(root)
        set_window_icon(root, default=True)
        Dashboard(root, conn, on_close=root.quit)
        root.mainloop()
        return

    TrayApp(conn).run()


if __name__ == "__main__":
    main()
