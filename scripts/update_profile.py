"""Refresh the profile's SVG cards from GitHub's public data; stdlib only."""

import argparse
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


FONT = "'Cascadia Code','Consolas','Liberation Mono',monospace"
COLORS = ["#b4f57b", "#79d4ca", "#c2a6ef", "#e0c38b", "#8eaa9b"]
LEVELS = ["#1c2922", "#254c36", "#3e7750", "#73ad62", "#b4f57b"]
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def fetch(url):
    headers = {"User-Agent": "Wangpengyu01-Profile/1.0", "Accept-Language": "en-US"}
    # The calendar is fetched anonymously, exactly as a public visitor sees it.
    if url.startswith("https://api.github.com/"):
        headers["Accept"] = "application/vnd.github+json"
        if os.environ.get("GH_TOKEN"):
            headers["Authorization"] = "Bearer " + os.environ["GH_TOKEN"]
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers=headers), timeout=30) as response:
                return response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError) as error:
            retryable = not isinstance(error, HTTPError) or error.code in (429, 500, 502, 503, 504)
            if not retryable or attempt == 2:
                raise
            time.sleep(2 ** attempt)


def api(path):
    return json.loads(fetch("https://api.github.com/" + path))


def public_owned(repos, username):
    return [r for r in repos if not r["private"]
            and r["owner"]["login"].casefold() == username.casefold()]


def summarize_repositories(repos, username):
    owned = public_owned(repos, username)
    return {"repos": len(owned),
            "stars": sum(r["stargazers_count"] for r in owned if not r["fork"])}


class CalendarParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cells, self.counts = {}, {}
        self.tooltip_id, self.tooltip_text = None, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "td" and "data-date" in attrs:
            self.cells[attrs["id"]] = {"date": attrs["data-date"],
                                      "level": int(attrs["data-level"])}
        if tag == "tool-tip":
            self.tooltip_id, self.tooltip_text = attrs.get("for"), []

    def handle_data(self, data):
        if self.tooltip_id:
            self.tooltip_text.append(data)

    def handle_endtag(self, tag):
        if tag == "tool-tip" and self.tooltip_id:
            match = re.match(r"^(No|[\d,]+)\s+contributions?\b",
                             "".join(self.tooltip_text).strip())
            if match:
                self.counts[self.tooltip_id] = (0 if match[1] == "No"
                                               else int(match[1].replace(",", "")))
            self.tooltip_id = None


def parse_calendar(html, start, end):
    parser = CalendarParser()
    parser.feed(html)
    days = []
    for cell_id, cell in parser.cells.items():
        if start <= date.fromisoformat(cell["date"]) <= end:
            if cell_id not in parser.counts or cell["level"] not in range(5):
                raise ValueError("GitHub calendar format changed: missing contribution counts")
            days.append({**cell, "count": parser.counts[cell_id]})
    days.sort(key=lambda day: day["date"])
    expected = [(start + timedelta(days=i)).isoformat()
                for i in range((end - start).days + 1)]
    if [day["date"] for day in days] != expected:
        raise ValueError("GitHub returned an incomplete calendar; existing assets are preserved")
    return days


def text(x, y, value, size=16, color="#a3b7ab", extra=""):
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" {extra}>'
            f'{escape(str(value))}</text>')


def frame(width, height, title, updated):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
            f'<title id="title">{escape(title)}</title>',
            '<desc id="desc">Daily snapshot of GitHub public profile data.</desc>',
            f'<rect x="1" y="1" width="{width-2}" height="{height-2}" rx="16" '
            'fill="#0d1410" stroke="#2a3932" stroke-width="2"/>',
            f'<g font-family="{FONT}">',
            '<circle cx="32" cy="35" r="4" fill="#b4f57b"/>',
            text(48, 41, title, 16, "#d2dfd6"),
            text(width-28, 41, updated, 13, "#8eaa9b", 'text-anchor="end"'),
            f'<path d="M 28 65 H {width-28}" stroke="#2a3932"/>']


def render_stats(metrics, mobile=False):
    width, height = (640, 520) if mobile else (1280, 360)
    out = frame(width, height, "GITHUB / TELEMETRY", metrics["updated"])
    values = [(metrics["repos"], "PUBLIC REPOS"), (metrics["stars"], "STARS EARNED"),
              (metrics["followers"], "FOLLOWERS"),
              (metrics["contributions"], "CONTRIBUTIONS / 12M")]
    for i, (value, label) in enumerate(values):
        x = 32 + (i % 2) * 304 if mobile else 48 + i * 304
        y = 125 + (i // 2) * 112 if mobile else 133
        out += [text(x, y, f"{value:,}", 48, "#b4f57b", 'font-weight="700"'),
                text(x, y+31, label, 14, "#a3b7ab")]
    section_y = 282 if mobile else 196
    out += [f'<path d="M 28 {section_y} H {width-28}" stroke="#2a3932"/>',
            text(32 if mobile else 48, section_y+34, "LANGUAGE MIX / PUBLIC SOURCE CODE", 14)]
    languages = sorted(metrics["languages"].items(), key=lambda pair: (-pair[1], pair[0]))
    if len(languages) > 5:
        languages = languages[:4] + [("Other", sum(n for _, n in languages[4:]))]
    total = sum(n for _, n in languages)
    left, bar_y, bar_width = (32, 338, 576) if mobile else (48, 248, 1184)
    out += [f'<defs><clipPath id="bar"><rect x="{left}" y="{bar_y}" '
            f'width="{bar_width}" height="12" rx="6"/></clipPath></defs>',
            '<g clip-path="url(#bar)">',
            f'<rect x="{left}" y="{bar_y}" width="{bar_width}" height="12" fill="#1c2922"/>']
    offset = 0
    for i, (_, count) in enumerate(languages):
        size = bar_width * count / total if total else 0
        out.append(f'<rect x="{left+offset:.3f}" y="{bar_y}" width="{size:.3f}" '
                   f'height="12" fill="{COLORS[i]}"/>')
        offset += size
    out.append('</g>')
    if not total:
        out.append(text(left, bar_y+46, "No public language data yet", 16))
    for i, (name, count) in enumerate(languages):
        x = 32 + (i % 2) * 304 if mobile else 48 + i * 237
        y = 385 + (i // 2) * 34 if mobile else 293
        out += [f'<circle cx="{x+4}" cy="{y-5}" r="4" fill="{COLORS[i]}"/>',
                text(x+17, y, f"{name} {count/total*100:.1f}%", 14)]
    out += [text(32 if mobile else 48, height-23,
                 "By code bytes / forks excluded / daily sync", 12, "#8eaa9b"), '</g></svg>']
    return "\n".join(out) + "\n"


def render_contributions(days, updated, mobile=False):
    width, height = (640, 370) if mobile else (1280, 350)
    out = frame(width, height, "GITHUB / CONTRIBUTIONS", updated)
    total = sum(d["count"] for d in days)
    active = sum(d["count"] > 0 for d in days)
    streak = best = 0
    for day in days:
        streak = streak + 1 if day["count"] else 0
        best = max(best, streak)
    if mobile:
        out += [text(32, 114, f"{total:,}", 44, "#b4f57b", 'font-weight="700"'),
                text(200, 110, "contributions / 12M", 16),
                text(32, 144, f"{active} active days / {best} day best streak", 15)]
    else:
        out += [text(48, 100, f"{total:,} contributions", 24, "#b4f57b"),
                text(445, 100, f"{active} active days / {best} day best streak", 16)]
    start = date.fromisoformat(days[0]["date"])
    first_sunday = start - timedelta(days=(start.weekday()+1) % 7)
    left, top, pitch, size = (50, 190, 10, 8) if mobile else (98, 144, 21, 18)
    for weekday, label in [(1, "Mon"), (3, "Wed"), (5, "Fri")]:
        out.append(text(13 if mobile else 48, top+weekday*pitch+size-2,
                        label, 11 if mobile else 13))
    month_labels = {}
    for index, day in enumerate(days):
        current = date.fromisoformat(day["date"])
        week = (current-first_sunday).days // 7
        weekday = (current.weekday()+1) % 7
        x, y = left + week*pitch, top + weekday*pitch
        out.append(f'<rect x="{x}" y="{y}" width="{size}" height="{size}" rx="2" '
                   f'fill="{LEVELS[day["level"]]}" data-date="{current.isoformat()}">'
                   f'<title>{current.isoformat()}: {day["count"]:,} contributions</title></rect>')
        if current.day == 1 or index == 0:
            month_labels[week] = MONTHS[current.month-1]
    for week, label in month_labels.items():
        out.append(text(left+week*pitch, top-14, label, 11 if mobile else 14))
    legend_x, legend_y = (406, 297) if mobile else (1030, 317)
    out.append(text(legend_x-43, legend_y+8, "Less", 11 if mobile else 13))
    for i, color in enumerate(LEVELS):
        out.append(f'<rect x="{legend_x+i*18}" y="{legend_y}" width="12" height="12" '
                   f'rx="2" fill="{color}"/>')
    out.append(text(legend_x+94, legend_y+9, "More", 11 if mobile else 13))
    out.append(text(32 if mobile else 48, 348 if mobile else 326,
                    f'{days[0]["date"]} — {days[-1]["date"]}', 12))
    out += ['</g></svg>']
    return "\n".join(out) + "\n"


def collect(username):
    now = datetime.now(timezone.utc)
    today = now.date()
    start = today - timedelta(days=364)
    profile = api(f"users/{username}")
    repos, page = [], 1
    while True:
        batch = api(f"users/{username}/repos?per_page=100&type=owner&page={page}")
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    owned = public_owned(repos, username)
    sources = [r for r in owned if not r["fork"]]
    languages = Counter()
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(lambda r: api(f'repos/{username}/{r["name"]}/languages'), sources):
            languages.update(result)
    # GitHub's default endpoint is rolling; explicit from/to selects a calendar year.
    calendar_url = f"https://github.com/users/{username}/contributions"
    days = parse_calendar(fetch(calendar_url), start, today)
    return {**summarize_repositories(owned, username), "followers": profile["followers"],
            "contributions": sum(day["count"] for day in days),
            "languages": dict(languages), "days": days,
            "updated": now.astimezone(timezone(timedelta(hours=8))).date().isoformat(),
            "sources": {"repositories": f"https://api.github.com/users/{username}/repos",
                        "contributions": calendar_url}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default="Wangpengyu01")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1]/"assets")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", args.username):
        parser.error("Invalid GitHub username")
    metrics = collect(args.username)
    outputs = {"stats.svg": render_stats(metrics), "stats-mobile.svg": render_stats(metrics, True),
               "contributions.svg": render_contributions(metrics["days"], metrics["updated"]),
               "contributions-mobile.svg": render_contributions(metrics["days"], metrics["updated"], True)}
    for svg in outputs.values():
        ET.fromstring(svg)
    outputs["metrics.json"] = json.dumps(metrics, ensure_ascii=False, indent=2) + "\n"
    args.output.mkdir(parents=True, exist_ok=True)
    for name, content in outputs.items():
        destination = args.output / name
        temporary = destination.with_suffix(destination.suffix+".tmp")
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(destination)
    print(f'Updated {args.username}: {metrics["repos"]} public repos, '
          f'{metrics["contributions"]} contributions, {len(metrics["days"])} days')


if __name__ == "__main__":
    main()
