#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


GRAPHQL_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        totalContributions
        weeks {
          firstDay
          contributionDays {
            contributionCount
            date
            weekday
          }
        }
      }
    }
  }
}
"""


BACKGROUND = "#0d1117"
PANEL = "#161b22"
BORDER = "#30363d"
TEXT = "#f0f6fc"
MUTED = "#8b949e"
ACCENT = "#58a6ff"
HEATMAP_ZERO = "#161b22"
HEATMAP_SCALE = ["#0e4429", "#006d32", "#26a641", "#39d353"]


@dataclass(frozen=True)
class ContributionDay:
    day: date
    count: int
    weekday: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate local profile metrics assets.")
    parser.add_argument("--username", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timezone", default="Asia/Karachi")
    parser.add_argument("--lookback-days", type=int, default=365)
    parser.add_argument("--input", help="Load a saved GraphQL payload for local testing.")
    return parser.parse_args()


def get_timezone(name: str):
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


def fetch_payload(username: str, token: str, from_dt: datetime, to_dt: datetime) -> dict[str, Any]:
    body = json.dumps(
        {
            "query": GRAPHQL_QUERY,
            "variables": {
                "login": username,
                "from": from_dt.isoformat(),
                "to": to_dt.isoformat(),
            },
        }
    ).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "TalhaArjumand-profile-metrics",
        "Accept": "application/json",
    }
    req = request.Request("https://api.github.com/graphql", data=body, headers=headers, method="POST")
    try:
        with request.urlopen(req) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub GraphQL request failed: {exc.code} {detail}") from exc
    if payload.get("errors"):
        raise RuntimeError(f"GitHub GraphQL returned errors: {payload['errors']}")
    return payload


def load_calendar(payload: dict[str, Any]) -> dict[str, Any]:
    if "data" in payload:
        return payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    return payload


def flatten_days(calendar: dict[str, Any]) -> list[ContributionDay]:
    days: list[ContributionDay] = []
    for week in calendar["weeks"]:
        for entry in week["contributionDays"]:
            days.append(
                ContributionDay(
                    day=date.fromisoformat(entry["date"]),
                    count=int(entry["contributionCount"]),
                    weekday=int(entry["weekday"]),
                )
            )
    days.sort(key=lambda item: item.day)
    return days


def quantify_scale(days: list[ContributionDay]) -> list[int]:
    counts = sorted(day.count for day in days if day.count > 0)
    if not counts:
        return [1, 2, 3]
    return [
        counts[min(len(counts) - 1, math.floor((len(counts) - 1) * 0.25))],
        counts[min(len(counts) - 1, math.floor((len(counts) - 1) * 0.50))],
        counts[min(len(counts) - 1, math.floor((len(counts) - 1) * 0.75))],
    ]


def heatmap_color(count: int, thresholds: list[int]) -> str:
    if count <= 0:
        return HEATMAP_ZERO
    if count <= thresholds[0]:
        return HEATMAP_SCALE[0]
    if count <= thresholds[1]:
        return HEATMAP_SCALE[1]
    if count <= thresholds[2]:
        return HEATMAP_SCALE[2]
    return HEATMAP_SCALE[3]


def compute_streaks(days: list[ContributionDay], today: date) -> dict[str, Any]:
    active_days = [item.day for item in days if item.count > 0]
    if not active_days:
        return {
            "current_length": 0,
            "current_start": None,
            "current_end": None,
            "longest_length": 0,
            "longest_start": None,
            "longest_end": None,
        }

    longest_length = 0
    longest_start = active_days[0]
    longest_end = active_days[0]

    run_start = active_days[0]
    prev = active_days[0]
    run_length = 1
    for current in active_days[1:]:
        if current == prev + timedelta(days=1):
            run_length += 1
        else:
            if run_length > longest_length:
                longest_length = run_length
                longest_start = run_start
                longest_end = prev
            run_start = current
            run_length = 1
        prev = current
    if run_length > longest_length:
        longest_length = run_length
        longest_start = run_start
        longest_end = prev

    latest_active = active_days[-1]
    if latest_active < today - timedelta(days=1):
        current_length = 0
        current_start = None
        current_end = None
    else:
        current_end = latest_active
        current_start = latest_active
        current_length = 1
        index = len(active_days) - 2
        while index >= 0 and active_days[index] == current_start - timedelta(days=1):
            current_start = active_days[index]
            current_length += 1
            index -= 1

    return {
        "current_length": current_length,
        "current_start": current_start,
        "current_end": current_end,
        "longest_length": longest_length,
        "longest_start": longest_start,
        "longest_end": longest_end,
    }


def format_date(day: date | None) -> str:
    if day is None:
        return "n/a"
    return day.strftime("%b %d, %Y")


def json_safe_summary(summary: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in summary.items():
        if isinstance(value, date):
            output[key] = value.isoformat()
        elif isinstance(value, dict):
            output[key] = json_safe_summary(value)
        else:
            output[key] = value
    return output


def compute_summary(calendar: dict[str, Any], days: list[ContributionDay], tz_name: str) -> dict[str, Any]:
    tz = get_timezone(tz_name)
    now = datetime.now(tz)
    today = now.date()
    streaks = compute_streaks(days, today)
    busiest = max(days, key=lambda item: item.count, default=None)
    active_count = sum(1 for item in days if item.count > 0)
    total = int(calendar["totalContributions"])
    week_count = max(len(calendar["weeks"]), 1)
    average_week = total / week_count
    average_active = total / active_count if active_count else 0.0
    return {
        "generated_at": now.isoformat(),
        "timezone": tz_name,
        "total_contributions": total,
        "active_days": active_count,
        "average_per_active_day": round(average_active, 1),
        "average_per_week": round(average_week, 1),
        "busiest_day": {
            "date": busiest.day.isoformat() if busiest else None,
            "count": busiest.count if busiest else 0,
        },
        **streaks,
    }


def xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def render_panel_header(title: str, subtitle: str, width: int) -> str:
    return (
        f'<text x="32" y="44" fill="{TEXT}" font-size="22" font-weight="700">{xml_escape(title)}</text>'
        f'<text x="{width - 32}" y="44" fill="{MUTED}" font-size="12" text-anchor="end">{xml_escape(subtitle)}</text>'
    )


def render_streak_svg(summary: dict[str, Any]) -> str:
    width = 720
    height = 220
    column_x = [32, 250, 470]
    current_label = "Current streak" if summary["current_length"] else "No active streak"
    current_range = (
        f"{format_date(summary['current_start'])} - {format_date(summary['current_end'])}"
        if summary["current_length"]
        else "No qualifying contribution on the latest tracked day"
    )
    longest_range = f"{format_date(summary['longest_start'])} - {format_date(summary['longest_end'])}"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
  <title id="title">GitHub streak snapshot</title>
  <desc id="desc">Current streak, total contributions, and longest streak generated from authenticated GitHub data.</desc>
  <rect width="{width}" height="{height}" rx="18" fill="{PANEL}" stroke="{BORDER}" />
  {render_panel_header("Streak Snapshot", f"Updated {summary['generated_at'][:10]}", width)}
  <line x1="227" y1="68" x2="227" y2="182" stroke="{BORDER}" />
  <line x1="447" y1="68" x2="447" y2="182" stroke="{BORDER}" />
  <text x="{column_x[0]}" y="104" fill="{ACCENT}" font-size="44" font-weight="800">{summary['current_length']}</text>
  <text x="{column_x[0]}" y="132" fill="{TEXT}" font-size="16" font-weight="700">{xml_escape(current_label)}</text>
  <text x="{column_x[0]}" y="156" fill="{MUTED}" font-size="12">{xml_escape(current_range)}</text>

  <text x="{column_x[1]}" y="104" fill="{ACCENT}" font-size="44" font-weight="800">{summary['total_contributions']}</text>
  <text x="{column_x[1]}" y="132" fill="{TEXT}" font-size="16" font-weight="700">Total contributions</text>
  <text x="{column_x[1]}" y="156" fill="{MUTED}" font-size="12">Last 52 weeks</text>

  <text x="{column_x[2]}" y="104" fill="{ACCENT}" font-size="44" font-weight="800">{summary['longest_length']}</text>
  <text x="{column_x[2]}" y="132" fill="{TEXT}" font-size="16" font-weight="700">Longest streak</text>
  <text x="{column_x[2]}" y="156" fill="{MUTED}" font-size="12">{xml_escape(longest_range)}</text>
</svg>
"""


def render_summary_svg(summary: dict[str, Any]) -> str:
    width = 720
    height = 220
    busiest = summary["busiest_day"]
    busiest_text = (
        f"{busiest['count']} on {format_date(date.fromisoformat(busiest['date']))}"
        if busiest["date"]
        else "n/a"
    )
    stats = [
        ("Active days", str(summary["active_days"]), "Days with at least one contribution"),
        ("Avg / active day", str(summary["average_per_active_day"]), "Contribution density on active days"),
        ("Avg / week", str(summary["average_per_week"]), "Last 52 weeks"),
        ("Peak day", busiest_text, "Most productive day in the current window"),
    ]
    blocks = []
    for index, (label, value, hint) in enumerate(stats):
        x = 32 if index % 2 == 0 else 372
        y = 92 if index < 2 else 154
        blocks.append(
            f'<text x="{x}" y="{y}" fill="{ACCENT}" font-size="24" font-weight="800">{xml_escape(value)}</text>'
            f'<text x="{x}" y="{y + 24}" fill="{TEXT}" font-size="14" font-weight="700">{xml_escape(label)}</text>'
            f'<text x="{x}" y="{y + 44}" fill="{MUTED}" font-size="11">{xml_escape(hint)}</text>'
        )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
  <title id="title">GitHub contribution summary</title>
  <desc id="desc">Contribution density and throughput summary generated from authenticated GitHub data.</desc>
  <rect width="{width}" height="{height}" rx="18" fill="{PANEL}" stroke="{BORDER}" />
  {render_panel_header("Contribution Summary", f"Timezone {summary['timezone']}", width)}
  {''.join(blocks)}
</svg>
"""


def render_activity_svg(calendar: dict[str, Any], days: list[ContributionDay], summary: dict[str, Any]) -> str:
    width = 1120
    height = 280
    cell = 12
    gap = 4
    left = 90
    top = 78
    weeks = calendar["weeks"]
    thresholds = quantify_scale(days)

    month_labels = []
    previous_month = None
    for index, week in enumerate(weeks):
        month = date.fromisoformat(week["firstDay"]).strftime("%b")
        if month != previous_month:
            x = left + index * (cell + gap)
            month_labels.append(
                f'<text x="{x}" y="54" fill="{MUTED}" font-size="12">{month}</text>'
            )
            previous_month = month

    weekday_labels = [
        ('Mon', top + 1 * (cell + gap) + 10),
        ('Wed', top + 3 * (cell + gap) + 10),
        ('Fri', top + 5 * (cell + gap) + 10),
    ]

    squares = []
    for week_index, week in enumerate(weeks):
        for entry in week["contributionDays"]:
            weekday = int(entry["weekday"])
            x = left + week_index * (cell + gap)
            y = top + weekday * (cell + gap)
            count = int(entry["contributionCount"])
            color = heatmap_color(count, thresholds)
            squares.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="3" fill="{color}" />'
            )

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
  <title id="title">GitHub contribution activity</title>
  <desc id="desc">Heatmap of contributions generated from authenticated GitHub data for the last 52 weeks.</desc>
  <rect width="{width}" height="{height}" rx="20" fill="{PANEL}" stroke="{BORDER}" />
  {render_panel_header("Contribution Activity", f"{summary['total_contributions']} contributions in the last year", width)}
  {''.join(month_labels)}
  {''.join(f'<text x="34" y="{y}" fill="{MUTED}" font-size="12">{label}</text>' for label, y in weekday_labels)}
  {''.join(squares)}
  <text x="32" y="{height - 18}" fill="{MUTED}" font-size="11">Generated from authenticated GitHub contribution data.</text>
</svg>
"""


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def update_readme_asset_urls(repo_root: Path, summary: dict[str, Any], username: str) -> None:
    readme_path = repo_root / "README.md"
    content = readme_path.read_text(encoding="utf-8")
    stamp = datetime.fromisoformat(summary["generated_at"]).strftime("%Y%m%d%H%M%S")
    base = f"https://raw.githubusercontent.com/{username}/{username}/main/assets/profile-metrics"
    replacements = {
        "streak.svg": f'{base}/streak.svg?v={stamp}',
        "summary.svg": f'{base}/summary.svg?v={stamp}',
        "activity.svg": f'{base}/activity.svg?v={stamp}',
    }
    for asset, target in replacements.items():
        content = re.sub(
            rf'src="[^"]*profile-metrics/{re.escape(asset)}(?:\?v=\d+)?"',
            f'src="{target}"',
            content,
        )
    readme_path.write_text(content, encoding="utf-8")


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir)
    if args.input:
        payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    else:
        token = os.environ.get("PROFILE_METRICS_TOKEN")
        if not token:
            raise RuntimeError("PROFILE_METRICS_TOKEN is required when --input is not provided.")
        tz = get_timezone(args.timezone)
        now = datetime.now(tz)
        from_dt = datetime.combine(now.date() - timedelta(days=args.lookback_days - 1), time.min, tzinfo=tz)
        to_dt = datetime.combine(now.date(), time.max, tzinfo=tz)
        payload = fetch_payload(args.username, token, from_dt, to_dt)

    calendar = load_calendar(payload)
    days = flatten_days(calendar)
    summary = compute_summary(calendar, days, args.timezone)

    output_dir.mkdir(parents=True, exist_ok=True)
    write_text(output_dir / "streak.svg", render_streak_svg(summary))
    write_text(output_dir / "summary.svg", render_summary_svg(summary))
    write_text(output_dir / "activity.svg", render_activity_svg(calendar, days, summary))
    write_text(output_dir / "summary.json", json.dumps(json_safe_summary(summary), indent=2))
    update_readme_asset_urls(Path.cwd(), summary, args.username)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
