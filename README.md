# 🕐 Toggl Auto-Fill CLI

CLI tool for managing and logging time entries to Toggl Track with smart day scheduling.

---

## Features

| Feature | Description |
|---|---|
| **Day filling** | Interactively fill incomplete workdays with tasks and time entries |
| **Smart breaks** | Adds 1–4 short breaks when you have few tasks; count varies day-to-day |
| **Task hour logging** | Log hours retroactively for tasks (past 2 weeks) |
| **Project picker** | Assign your Toggl projects to each entry interactively |
| **Gap filling** | Distribute remaining time to reach your 8-hour target |
| **Monthly report** | View workday hours and billable status for any month at a glance |

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Set your Toggl API token

Create a `.env` file in this folder:

```bash
TOGGL_API_TOKEN=your_token_here
TIMEZONE=Europe/Madrid  # optional
```

Get your API token from https://track.toggl.com/profile

### 3. Run

```bash
python toggl_sync.py
```

---

## Menu Options

```
[1] Fill incomplete days this month
    Finds workdays with under 8 hours logged, lets you add tasks
    interactively, inserts smart breaks, then pushes to Toggl.

[2] Fill a specific date
    Same as above but for any single date you choose.

[3] Log hours for recent tasks (past 2 weeks)
    Shows recent task descriptions from your past activity.
    You type hours for each task — great for retroactive logging.

[4] View today's Toggl entries
    Quick summary of what's already logged today.

[5] View Toggl projects
    Lists all your Toggl projects with their IDs.

[6] Mark a day off
    Log a full day as unpaid time off (or custom hours).

[7] Monthly report
    Shows a summary of logged and billable hours for each workday
    in a given month. Helps with end-of-month billing review.
```

---

## How it works

1. Your day starts at **09:15** (configurable)
2. You interactively add tasks and hours for incomplete workdays
3. If you enter **1 or 2 tasks**, the script adds **1–4 short breaks** automatically
   - The number of breaks varies from the previous day to add natural variation
4. Everything sums to exactly **8 hours** (configurable)
5. All entries are pushed to Toggl Track

---

## Customization

Edit the constants at the top of `toggl_sync.py`:

```python
WORKDAY_HOURS  = 8        # change to 7.5 etc.
DAY_START_TIME = "09:15"  # your earliest entry time
MAX_BREAKS     = 4        # max breaks to insert
TIMEZONE       = "Europe/Madrid"
```

---

## Files created automatically

| File | Purpose |
|---|---|
| `.toggl_state.json` | Tracks last break count to vary next day |

This is already in `.gitignore`.
