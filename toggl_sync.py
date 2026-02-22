#!/usr/bin/env python3
"""
Toggl Auto-Fill CLI
Fills incomplete workdays in Toggl Track based on your past activity.
"""

import os
import json
import random
import calendar
import datetime
import requests
import base64
import yaml
from zoneinfo import ZoneInfo

# ─── Optional: load from .env file ────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ──── Load config.yaml ────────────────────────────────────────────────────────
def _load_config():
    try:
        with open(os.path.join(os.path.dirname(__file__), "config.yaml")) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}

_cfg = _load_config()

def _cfg_get(key, env_var, default, cast=None):
    """Return env var if set, else config.yaml value, else default."""
    val = os.getenv(env_var)
    if val is not None:
        return cast(val) if cast else val
    return _cfg.get(key, default)

# ══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION  —  loaded from config.yaml, overridable via environment variables
# ══════════════════════════════════════════════════════════════════════════════
TOGGL_API_TOKEN    = os.getenv("TOGGL_API_TOKEN", "YOUR_TOGGL_API_TOKEN")
TOGGL_WORKSPACE_ID = os.getenv("TOGGL_WORKSPACE_ID", "")   # optional, auto-detected
TIMEZONE           = os.getenv("TIMEZONE", "Europe/Madrid")  # your local timezone
WORKDAY_HOURS      = _cfg_get("workday_hours",      "WORKDAY_HOURS",      8,       int)
DAY_START_TIME     = _cfg_get("day_start_time",     "DAY_START_TIME",     "09:15")
BILLABLE           = _cfg_get("billable",           "BILLABLE",           True,    lambda v: v.lower() == "true")
TIME_OFF_PROJECT   = _cfg_get("time_off_project",   "TIME_OFF_PROJECT",   "Time Off - (UNPAID)")
DAILY_STANDUP_MINS = _cfg_get("daily_standup_mins", "DAILY_STANDUP_MINS", 15,      int)

# DEFAULT_PROJECTS: comma-separated env var → list, or list from yaml
_dp_env = os.getenv("DEFAULT_PROJECTS")
if _dp_env is not None:
    DEFAULT_PROJECTS = [p.strip() for p in _dp_env.split(",") if p.strip()]
else:
    DEFAULT_PROJECTS = _cfg.get("default_projects", [])
# ══════════════════════════════════════════════════════════════════════════════

TZ = ZoneInfo(TIMEZONE)
TOGGL_BASE = "https://api.track.toggl.com/api/v9"

_h, _m = DAY_START_TIME.split(":")
DAY_START = datetime.time(int(_h), int(_m))


# ─── Toggl helpers ─────────────────────────────────────────────────────────────

def toggl_auth_header():
    token = f"{TOGGL_API_TOKEN}:api_token"
    encoded = base64.b64encode(token.encode()).decode()
    return {"Authorization": f"Basic {encoded}", "Content-Type": "application/json"}


def toggl_get(path, params=None):
    r = requests.get(f"{TOGGL_BASE}{path}", headers=toggl_auth_header(), params=params)
    r.raise_for_status()
    return r.json()


def toggl_post(path, data):
    r = requests.post(f"{TOGGL_BASE}{path}", headers=toggl_auth_header(), json=data)
    if not r.ok:
        raise requests.HTTPError(f"{r.status_code} {r.reason}: {r.text}", response=r)
    return r.json()


def get_workspace_id():
    global TOGGL_WORKSPACE_ID
    if TOGGL_WORKSPACE_ID:
        return int(TOGGL_WORKSPACE_ID)
    me = toggl_get("/me")
    TOGGL_WORKSPACE_ID = me["default_workspace_id"]
    return TOGGL_WORKSPACE_ID


def get_projects():
    wid = get_workspace_id()
    projects = toggl_get(f"/workspaces/{wid}/projects")
    return {p["name"]: p["id"] for p in (projects or [])}


def get_existing_entries(date: datetime.date):
    """Return time entries already logged for a given date."""
    start = datetime.datetime(date.year, date.month, date.day, tzinfo=TZ).isoformat()
    end   = datetime.datetime(date.year, date.month, date.day, 23, 59, 59, tzinfo=TZ).isoformat()
    entries = toggl_get("/me/time_entries", params={"start_date": start, "end_date": end})
    return entries or []


def create_time_entry(description, project_id, start: datetime.datetime, duration_seconds: int, billable=True):
    wid = get_workspace_id()
    data = {
        "description": description,
        "start": start.isoformat(),
        "duration": duration_seconds,
        "workspace_id": wid,
        "billable": billable,
        "created_with": "toggl-autofill-cli",
    }
    if project_id:
        data["project_id"] = project_id
    return toggl_post(f"/workspaces/{wid}/time_entries", data)


# ─── Month analysis ────────────────────────────────────────────────────────────

def get_month_entries(year, month):
    """Fetch all time entries for the given month in a single API call."""
    first_day = datetime.date(year, month, 1)
    last_day  = datetime.date(year, month, calendar.monthrange(year, month)[1])
    start = datetime.datetime(first_day.year, first_day.month, first_day.day, tzinfo=TZ).isoformat()
    end   = datetime.datetime(last_day.year, last_day.month, last_day.day, 23, 59, 59, tzinfo=TZ).isoformat()
    entries = toggl_get("/me/time_entries", params={"start_date": start, "end_date": end})
    return entries or []


def analyze_month(entries, year, month):
    """Group entries by date, sum duration per day. Returns {date: total_seconds}."""
    totals = {}
    for e in entries:
        dur = e.get("duration", 0)
        if dur < 0:  # running timer — skip
            continue
        desc = (e.get("description") or "").lower()
        if desc == "break":
            continue
        start_str = e.get("start", "")
        if not start_str:
            continue
        try:
            dt = datetime.datetime.fromisoformat(start_str).astimezone(TZ)
        except ValueError:
            continue
        d = dt.date()
        if d.year == year and d.month == month:
            totals[d] = totals.get(d, 0) + dur
    return totals


# ─── Past activities from Toggl ────────────────────────────────────────────────

def get_past_activities(days=14):
    """Fetch unique task descriptions from the past N days, most recent first."""
    today = datetime.date.today()
    start_date = today - datetime.timedelta(days=days)
    start = datetime.datetime(start_date.year, start_date.month, start_date.day, tzinfo=TZ).isoformat()
    end   = datetime.datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=TZ).isoformat()
    entries = toggl_get("/me/time_entries", params={"start_date": start, "end_date": end})
    entries = sorted(entries or [], key=lambda e: e.get("start", ""), reverse=True)
    seen = set()
    activities = []
    for e in entries:
        desc = (e.get("description") or "").strip()
        if desc and desc.lower() not in ("break", "daily") and desc not in seen:
            seen.add(desc)
            activities.append(desc)
    return activities


# ─── Break insertion ───────────────────────────────────────────────────────────

def insert_breaks(entries, break_secs=900):
    """
    Split entries longer than 2h into random 2h–3h work chunks with 15-min
    Break entries between them. Breaks are additive (don't shrink work budget).
    Example: 8h → [2h15m work, 15m break, 3h work, 15m break, 2h45m work] = 8h work + 30m breaks.
    Returns expanded list (no start times yet).
    """
    MIN_BREAK_THRESHOLD = 7200  # 2h in seconds
    result = []
    for entry in entries:
        dur = entry["duration"]
        if dur <= MIN_BREAK_THRESHOLD:
            result.append(entry)
        else:
            remaining = dur
            while remaining > 0:
                # Random continuous block: 2h–3h in 15-min steps
                steps = random.randint(8, 12)  # 8×15min=2h … 12×15min=3h
                max_chunk = steps * 900
                chunk = min(remaining, max_chunk)
                e = dict(entry)
                e["duration"] = chunk
                result.append(e)
                remaining -= chunk
                if remaining > 0:
                    result.append({"description": "Break", "duration": break_secs,
                                   "project_id": None, "billable": False, "_is_break": True})
                    # breaks are NOT subtracted from remaining work
    return result


# ─── Helpers ───────────────────────────────────────────────────────────────────

def fmt_duration(secs):
    h = secs // 3600
    m = (secs % 3600) // 60
    if h and m:
        return f"{h}h {m:02d}m"
    elif h:
        return f"{h}h"
    else:
        return f"{m}m"


def parse_hours(val):
    """Accept '1:30' (H:mm) or '1.5' (decimal). Returns float hours."""
    if ":" in val:
        h, m = val.split(":", 1)
        return int(h) + int(m) / 60
    return float(val)


def pick_project(projects, task_name):
    """Let user pick a Toggl project for a task. Auto-selects if DEFAULT_PROJECTS yields one match."""
    if not projects:
        return None
    if DEFAULT_PROJECTS:
        filtered = {k: v for k, v in projects.items() if k in DEFAULT_PROJECTS}
        if not filtered:
            filtered = projects  # fallback to all if none match
    else:
        filtered = projects
    proj_list = list(filtered.items())
    if len(proj_list) == 1:
        return proj_list[0][1]  # auto-select, no prompt
    print(f"\n  Project for '{task_name}':")
    for i, (name, _) in enumerate(proj_list):
        print(f"    [{i+1}] {name}")
    print(f"    [0] No project")
    choice = input("  Choice: ").strip()
    try:
        idx = int(choice)
        if idx == 0:
            return None
        return proj_list[idx - 1][1]
    except (ValueError, IndexError):
        return None


# ─── State persistence ─────────────────────────────────────────────────────────

STATE_FILE = ".toggl_state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ─── Interactive day filler ────────────────────────────────────────────────────

def fill_day(date: datetime.date, existing_entries, activities, projects):
    """Interactive filling loop for one incomplete day."""
    workday_secs = WORKDAY_HOURS * 3600
    already_logged = sum(
        max(e.get("duration", 0), 0)
        for e in existing_entries
        if (e.get("description") or "").lower() != "break"
    )
    remaining_secs = workday_secs - already_logged
    if remaining_secs <= 0:
        return

    # Find cursor = end of last existing entry (or day start)
    cursor_dt = datetime.datetime.combine(date, DAY_START, tzinfo=TZ)
    for e in existing_entries:
        start_str = e.get("start", "")
        dur = e.get("duration", 0)
        if dur > 0 and start_str:
            try:
                st = datetime.datetime.fromisoformat(start_str).astimezone(TZ)
                end_t = st + datetime.timedelta(seconds=dur)
                if end_t > cursor_dt:
                    cursor_dt = end_t
            except ValueError:
                pass

    print(f"\n  ── {date.strftime('%A %d %b')} — {fmt_duration(remaining_secs)} to fill ──")
    new_entries = []
    rem = remaining_secs

    # Auto-add Daily standup if not already logged today
    if DAILY_STANDUP_MINS > 0:
        daily_already = any(
            (e.get("description") or "").lower() == "daily"
            for e in existing_entries
        )
        if not daily_already and rem > 0:
            daily_secs = DAILY_STANDUP_MINS * 60
            daily_proj = pick_project(projects, "Daily")
            new_entries.append({"description": "Daily", "duration": daily_secs,
                                "project_id": daily_proj, "billable": BILLABLE})
            rem -= daily_secs

    for activity in activities:
        if rem <= 0:
            break
        prompt = f"  '{activity}' — hours or H:mm (Enter=fill {fmt_duration(rem)} remaining, 0=skip): "
        val = input(prompt).strip()
        if val == "":
            proj_id = pick_project(projects, activity)
            new_entries.append({"description": activity, "duration": rem, "project_id": proj_id, "billable": BILLABLE})
            rem = 0
            break
        elif val == "0":
            continue
        else:
            try:
                hours = parse_hours(val)
            except ValueError:
                print("  Invalid input, skipping.")
                continue
            secs = min(int(hours * 3600), rem)
            proj_id = pick_project(projects, activity)
            new_entries.append({"description": activity, "duration": secs, "project_id": proj_id, "billable": BILLABLE})
            rem -= secs

    # Free-form prompt if time still remains
    if rem > 0:
        val = input(
            f"\n  {fmt_duration(rem)} still unaccounted. "
            "Add a custom task (or press Enter to skip day): "
        ).strip()
        if val:
            proj_id = pick_project(projects, val)
            new_entries.append({"description": val, "duration": rem, "project_id": proj_id, "billable": BILLABLE})
            rem = 0

    if not new_entries:
        print("  Skipping day — no entries to add.")
        return

    # Insert breaks for long entries
    new_entries = insert_breaks(new_entries)

    # Assign sequential start times from cursor
    schedule = []
    t = cursor_dt
    for entry in new_entries:
        e = dict(entry)
        e["start"] = t
        schedule.append(e)
        t += datetime.timedelta(seconds=entry["duration"])

    # Display planned entries
    print(f"\n  Planned for {date.strftime('%a %d %b')}:")
    total_new = 0
    for s in schedule:
        mins = s["duration"] // 60
        print(f"    {s['start'].strftime('%H:%M')} | {mins:3}m | {s['description']}")
        if not s.get("_is_break"):
            total_new += s["duration"]
    total_all = already_logged + total_new
    print(f"  Total: {fmt_duration(total_all)}  (was {fmt_duration(already_logged)}, adding {fmt_duration(total_new)})")

    confirm = input("\n  Push to Toggl? (y/N): ").strip().lower()
    if confirm != "y":
        print("  Cancelled.")
        return

    success = 0
    work_entries = [e for e in schedule if not e.get("_is_break")]
    for entry in schedule:
        if entry.get("_is_break"):
            continue   # gap only — not pushed to Toggl
        try:
            create_time_entry(
                entry["description"],
                entry.get("project_id"),
                entry["start"],
                entry["duration"],
                billable=entry.get("billable", True),
            )
            success += 1
        except Exception as ex:
            print(f"  ✗ Failed '{entry['description']}': {ex}")

    print(f"  Created {success}/{len(work_entries)} entries.")


# ─── Month fill orchestrator ───────────────────────────────────────────────────

def run_month_fill():
    today = datetime.date.today()
    year, month = today.year, today.month
    month_name = today.strftime("%B %Y")

    print(f"\n━━ {month_name} ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  Fetching month entries from Toggl...")
    all_entries = get_month_entries(year, month)
    day_totals  = analyze_month(all_entries, year, month)

    workday_secs = WORKDAY_HOURS * 3600
    now = datetime.datetime.now(TZ)

    # Collect working days up to and including today
    num_days = calendar.monthrange(year, month)[1]
    working_days = [
        datetime.date(year, month, d)
        for d in range(1, num_days + 1)
        if datetime.date(year, month, d).weekday() < 5  # Mon–Fri
    ]

    # Print overview
    over_count = 0
    under_days = []
    for d in working_days:
        if d > today:
            break
        total = day_totals.get(d, 0)
        label = d.strftime("%a %d")
        if d == today and now.hour < 18:
            print(f"  {label}  ─  (today, skipping — not yet 6 PM)")
            continue
        if total >= workday_secs:
            diff = total - workday_secs
            if diff > 0:
                over_count += 1
                print(f"  {label}  !!  {fmt_duration(total)}  → OVER by {fmt_duration(diff)}")
            else:
                print(f"  {label}  ✓  {fmt_duration(total)}")
        else:
            needed = workday_secs - total
            under_days.append(d)
            print(f"  {label}  ✗  {fmt_duration(total)}  → needs {fmt_duration(needed)}")

    print("─" * 44)
    print(f"  {over_count} day(s) over, {len(under_days)} day(s) to fill")

    if not under_days:
        print("\n  All days complete!")
        return

    # Fetch activities and projects once for the whole session
    print("\n  Fetching recent activities from Toggl...")
    activities = get_past_activities(days=14)
    projects   = get_projects()

    if not activities:
        print("  No past activities found. You'll be prompted for custom tasks.")

    # Fill each incomplete day
    for d in under_days:
        if d > today:
            break
        if d == today and now.hour < 18:
            continue
        existing = []
        for e in all_entries:
            start_str = e.get("start", "")
            try:
                dt = datetime.datetime.fromisoformat(start_str).astimezone(TZ)
                if dt.date() == d:
                    existing.append(e)
            except ValueError:
                pass
        fill_day(d, existing, activities, projects)

    print("\n  Month fill complete.")


# ─── Menu actions ──────────────────────────────────────────────────────────────

def menu_sync_date():
    print("\n━━ Fill a Specific Date ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    date_str = input("  Enter date (YYYY-MM-DD): ").strip()
    try:
        date = datetime.date.fromisoformat(date_str)
    except ValueError:
        print("  Invalid date.")
        return

    existing   = get_existing_entries(date)
    activities = get_past_activities()
    projects   = get_projects()
    fill_day(date, existing, activities, projects)


def menu_task_hours():
    print("\n━━ Log Hours for Recent Tasks (past 2 weeks) ━━━━━━━━━━━")
    activities = get_past_activities(days=14)
    if not activities:
        print("  No past activities found in Toggl.")
        return

    projects = get_projects()
    date_str = input("  Which date to log for? (YYYY-MM-DD, blank = today): ").strip()
    if date_str:
        try:
            log_date = datetime.date.fromisoformat(date_str)
        except ValueError:
            print("  Invalid date.")
            return
    else:
        log_date = datetime.date.today()

    cursor = datetime.datetime.combine(log_date, DAY_START, tzinfo=TZ)
    entries = []

    print(f"\n  Tasks from your last 2 weeks in Toggl. Enter hours or H:mm for each (0 to skip):\n")
    for task in activities:
        h = input(f"  '{task}' — hours or H:mm (0 to skip): ").strip()
        try:
            hours = parse_hours(h)
        except ValueError:
            continue
        if hours <= 0:
            continue
        proj_id = pick_project(projects, task)
        secs = int(hours * 3600)
        entries.append({"description": task, "start": cursor, "duration": secs, "project_id": proj_id, "billable": BILLABLE})
        cursor += datetime.timedelta(seconds=secs)

    if not entries:
        print("  Nothing to log.")
        return

    total_h = sum(e["duration"] for e in entries) / 3600
    print(f"\n  Total: {total_h:.1f}h across {len(entries)} task(s).")
    confirm = input("  Push to Toggl? (y/N): ").strip().lower()
    if confirm != "y":
        return

    for e in entries:
        try:
            create_time_entry(e["description"], e["project_id"], e["start"], e["duration"], billable=e.get("billable", True))
            print(f"  ✓ '{e['description']}' logged.")
        except Exception as ex:
            print(f"  ✗ {ex}")


def menu_day_off():
    print("\n━━ Mark a Day Off ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    date_str = input("  Date (YYYY-MM-DD, blank = today): ").strip()
    if date_str:
        try:
            date = datetime.date.fromisoformat(date_str)
        except ValueError:
            print("  Invalid date.")
            return
    else:
        date = datetime.date.today()

    h_input = input(f"  Hours (blank = {WORKDAY_HOURS}h): ").strip()
    if h_input:
        try:
            hours = parse_hours(h_input)
        except ValueError:
            print("  Invalid hours.")
            return
    else:
        hours = float(WORKDAY_HOURS)

    secs = int(hours * 3600)
    projects = get_projects()
    proj_id = projects.get(TIME_OFF_PROJECT)
    if proj_id is None:
        print(f"  Warning: project '{TIME_OFF_PROJECT}' not found — logging without project.")

    start = datetime.datetime.combine(date, DAY_START, tzinfo=TZ)
    print(f"\n  Will log {fmt_duration(secs)} as 'Time Off' on {date.strftime('%a %d %b')} (not billable)")
    if proj_id:
        print(f"  Project: {TIME_OFF_PROJECT}")
    confirm = input("  Push to Toggl? (y/N): ").strip().lower()
    if confirm != "y":
        print("  Cancelled.")
        return

    try:
        create_time_entry("Time Off", proj_id, start, secs, billable=False)
        print("  Done.")
    except Exception as ex:
        print(f"  Error: {ex}")


def menu_check_today():
    print("\n━━ Today's Toggl Entries ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    entries = get_existing_entries(datetime.date.today())
    if not entries:
        print("  No entries yet today.")
        return
    total = 0
    for e in entries:
        dur = e.get("duration", 0)
        if dur < 0:
            dur = 0  # running timer
        mins = dur // 60
        print(f"  • {e.get('description', '(no description)')} — {mins}m")
        total += dur
    print(f"\n  Total logged: {fmt_duration(total)} / {WORKDAY_HOURS}h")


def menu_monthly_report():
    now = datetime.date.today()
    raw = input(f"\n  Month [YYYY-MM, Enter={now.strftime('%Y-%m')}]: ").strip()
    if raw:
        try:
            year, month = map(int, raw.split("-"))
        except ValueError:
            print("  Invalid format. Use YYYY-MM.")
            return
    else:
        year, month = now.year, now.month

    print(f"\n━━ Monthly Report: {datetime.date(year, month, 1).strftime('%B %Y')} ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    entries = get_month_entries(year, month) or []

    # Group by date
    by_day = {}
    for e in entries:
        dur = e.get("duration", 0)
        if dur < 0:
            continue
        desc = (e.get("description") or "").strip().lower()
        if desc == "break":
            continue
        start_str = e.get("start", "")
        if not start_str:
            continue
        try:
            dt = datetime.datetime.fromisoformat(start_str).astimezone(TZ)
        except ValueError:
            continue
        day = dt.date()
        if day not in by_day:
            by_day[day] = {"total": 0, "billable": 0}
        by_day[day]["total"] += dur
        if e.get("billable", False):
            by_day[day]["billable"] += dur

    num_days = calendar.monthrange(year, month)[1]
    working_days = [
        datetime.date(year, month, d)
        for d in range(1, num_days + 1)
        if datetime.date(year, month, d).weekday() < 5
    ]

    target_secs = int(WORKDAY_HOURS * 3600)
    grand_total = grand_billable = 0

    for day in working_days:
        data = by_day.get(day, {"total": 0, "billable": 0})
        total_s = data["total"]
        bill_s = data["billable"]
        grand_total += total_s
        grand_billable += bill_s
        label = day.strftime("%a %d")
        if total_s == 0:
            status = "—"
        elif total_s >= target_secs:
            status = "✓"
        else:
            status = f"⚠ {fmt_duration(target_secs - total_s)} short"
        print(f"  {label}  |  {fmt_duration(total_s):>8}  |  billable {fmt_duration(bill_s):>8}  |  {status}")

    print(f"\n  Total hours:    {fmt_duration(grand_total)}")
    print(f"  Billable hours: {fmt_duration(grand_billable)}")


def menu_view_projects():
    print("\n━━ Your Toggl Projects ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    projects = get_projects()
    if not projects:
        print("  No projects found.")
        return
    for name, pid in projects.items():
        print(f"  [{pid}] {name}")


# ─── Main menu ─────────────────────────────────────────────────────────────────

def main():
    print("\n" + "═" * 54)
    print("  Toggl Auto-Fill CLI")
    print("═" * 54)

    if TOGGL_API_TOKEN == "YOUR_TOGGL_API_TOKEN":
        print("\n  ⚠ Set your TOGGL_API_TOKEN in .env or in the script.")
        return

    while True:
        print("""
  [1] Fill incomplete days this month
  [2] Fill a specific date
  [3] Log hours for recent tasks (past 2 weeks)
  [4] View today's Toggl entries
  [5] View Toggl projects
  [6] Mark a day off
  [7] Monthly report
  [q] Quit
""")
        choice = input("  Choose: ").strip().lower()
        if choice == "1":
            run_month_fill()
        elif choice == "2":
            menu_sync_date()
        elif choice == "3":
            menu_task_hours()
        elif choice == "4":
            menu_check_today()
        elif choice == "5":
            menu_view_projects()
        elif choice == "6":
            menu_day_off()
        elif choice == "7":
            menu_monthly_report()
        elif choice in ("q", "quit", "exit"):
            print("\n  Bye!\n")
            break
        else:
            print("  Unknown option.")


if __name__ == "__main__":
    main()
