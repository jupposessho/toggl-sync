# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Create virtual environment and install dependencies (first time)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Run the CLI
.venv/bin/python toggl_sync.py
```

## Configuration

Required before running:
- **`.env`** — set `TOGGL_API_TOKEN` (from https://track.toggl.com/profile). Optionally set `TOGGL_WORKSPACE_ID` and `TIMEZONE`.
- **`credentials.json`** — Google OAuth2 client secrets downloaded from Google Cloud Console (Desktop App type, Calendar API enabled). On first run a browser opens for auth and `token.json` is auto-created.

Constants editable at the top of `toggl_sync.py`: `WORKDAY_HOURS`, `DAY_START_TIME`, `MAX_BREAKS`, `TIMEZONE`.

## Architecture

Single-file script (`toggl_sync.py`) with these logical sections:

- **Toggl API helpers** (`toggl_get`, `toggl_post`, `get_workspace_id`, `get_projects`, `get_existing_entries`, `create_time_entry`) — All calls go to `https://api.track.toggl.com/api/v9` with HTTP Basic auth using the API token.
- **Google Calendar helpers** (`get_calendar_service`, `fetch_calendar_events`, `fetch_unique_tasks_last_2_weeks`) — OAuth2 flow; filters out declined invites and all-day events.
- **Scheduling logic** (`build_day_schedule`) — Builds an 8-hour day by placing calendar meetings at their exact times, then interleaving user-entered tasks and auto-generated breaks into the gaps. Break count varies from the previous day (persisted in `.toggl_state.json`).
- **Menu actions** (`menu_sync_today`, `menu_sync_date`, `menu_task_hours`, `menu_check_today`, `menu_view_projects`) — Interactive CLI driven by `input()` prompts.

## Runtime-generated files

| File | Purpose |
|---|---|
| `token.json` | Google OAuth2 token (auto-refreshed) |
| `.toggl_state.json` | Persists `last_break_count` to vary breaks day-to-day |

Both should be in `.gitignore`.
