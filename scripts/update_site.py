"""Refresh the HTML credit tables and totals from the project CSV."""

import argparse
import csv
import json
import os
import subprocess
from collections import Counter
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
COUNT_FILE = ROOT / "scripts" / "openalex_count.json"
WORKS_FILTER = "primary_location.source.id:s125754415,topics.id:t11483,type:article,publication_year:2020-"
ROLES = {
    "Edited by": ("editor", "Editor"),
    "Contributed by": ("contributor", "Contributor"),
    "No editor listed": ("unlisted", "No editor listed"),
}


def canonical_name(name):
    return "Kerry Emanuel" if name == "Kerry A. Emanuel" else name


def check_openalex():
    args = [
        "curl", "--fail", "--silent", "--show-error", "--retry", "3",
        "--connect-timeout", "10", "--max-time", "30",
        "--get", "--data-urlencode", f"filter={WORKS_FILTER}",
        "--data", "per_page=1", "--data", "select=id",
    ]
    if key := os.getenv("OPENALEX_API_KEY"):
        args.extend(["--header", f"Authorization: Bearer {key}"])
    result = subprocess.run([*args, "https://api.openalex.org/works"], stdout=subprocess.PIPE)
    if result.returncode:
        raise SystemExit(f"OpenAlex count request failed (curl exit {result.returncode}).")
    count = json.loads(result.stdout)["meta"]["count"]
    if type(count) is not int or count < 0:
        raise ValueError("OpenAlex returned an invalid work count")
    return {"count": count, "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def freshness(snapshot, listed):
    count = snapshot["count"]
    label = "Lag" if count > listed else "Latest"
    checked = datetime.fromisoformat(snapshot["checked_at"]).astimezone(timezone.utc)
    return [
        f'<details class="freshness {label.lower()}"><summary>{label}</summary>',
        f'<p>OpenAlex reports {count} works. This page lists {listed}.<br>'
        f'Last checked {checked:%Y-%m-%d at %H:%M} UTC.</p></details>',
    ]


def href(url):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError(f"Expected an article URL, got {url!r}")
    return escape(url)


def article_row(work):
    day = date.fromisoformat(work["publication_date"])
    role, label = ROLES[work["credit"]]
    name = work["credited_name"]
    title = escape(work["title"])
    note = work["note"].strip()
    board = work["accepting_editorial_board_member"]
    if board:
        note = f"{note} Accepted by editorial board member {board}.".strip()
    note_html = f'<small class="article-note">{escape(note)}</small>' if note else ""
    name_html = f'<span class="credit-name">{escape(name)}</span>' if name else ""
    return (
        f'<tr data-year="{day.year}" data-role="{role}" data-person="{escape(canonical_name(name))}">\n'
        f'  <td class="date"><time datetime="{day}">{day:%b} {day.day}, {day.year}</time></td>\n'
        f'  <td class="work"><a class="article-title" href="{href(work["doi"])}">{title}</a>{note_html}</td>\n'
        f'  <td class="person">{name_html}<span class="role {role}">{label}</span></td>\n'
        f'  <td class="source"><a href="{href(work["credit_source_url"])}" '
        f'aria-label="Credit source for {title}">Credit source</a></td>\n'
        '</tr>'
    )


def build_sections(works):
    counts = Counter(work["credit"] for work in works)
    editor_counts = Counter(
        canonical_name(work["credited_name"]) for work in works if work["credit"] == "Edited by"
    )
    ranked = sorted(editor_counts.items(), key=lambda item: (-item[1], item[0].casefold()))
    stats = [
        ("Works", len(works)),
        ("Edited", counts["Edited by"]),
        ("Contributed", counts["Contributed by"]),
        ("No editor listed", counts["No editor listed"]),
    ]
    years = sorted({date.fromisoformat(work["publication_date"]).year for work in works}, reverse=True)
    ordered = sorted(works, key=lambda work: work["publication_date"], reverse=True)
    return {
        "STATS": [f"<div><dt>{label}</dt><dd>{total}</dd></div>" for label, total in stats],
        "CHART": [
            f'<li><span class="chart-name">{escape(name)}</span><span class="track" aria-hidden="true">'
            f'<span style="width: {100 * total / ranked[0][1]:g}%"></span></span><strong>{total}</strong></li>'
            for name, total in ranked[:3]
        ],
        "EDITORS": [f"<li><span>{escape(name)}</span><strong>{total}</strong></li>" for name, total in ranked],
        "YEARS": [f'<option value="{year}">{year}</option>' for year in years],
        "ARTICLES": [article_row(work) for work in ordered],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-openalex", action="store_true", help="Fetch a new count before rendering.")
    options = parser.parse_args()
    with (ROOT / "pnas_cyclone_editor_credits.csv").open(encoding="utf-8-sig", newline="") as source:
        works = list(csv.DictReader(source))
    snapshot = check_openalex() if options.check_openalex else json.loads(COUNT_FILE.read_text(encoding="utf-8"))
    sections = build_sections(works)
    sections["FRESHNESS"] = freshness(snapshot, len(works))
    index = ROOT / "index.html"
    original = index.read_text(encoding="utf-8")
    document = original
    for section, lines in sections.items():
        start, end = f"<!-- BEGIN {section} -->", f"<!-- END {section} -->"
        if document.count(start) != 1 or document.count(end) != 1:
            raise ValueError(f"Expected one marker pair for {section}")
        before, _, rest = document.partition(start)
        _, closing, after = rest.partition(end)
        if not closing:
            raise ValueError(f"Reversed markers for {section}")
        document = before + start + "\n" + "\n".join(lines) + "\n" + end + after
    if document != original:
        with TemporaryDirectory(dir=index.parent) as staging:
            temporary = Path(staging) / index.name
            temporary.write_text(document, encoding="utf-8")
            temporary.chmod(index.stat().st_mode)
            temporary.replace(index)
    if options.check_openalex:
        with TemporaryDirectory(dir=COUNT_FILE.parent) as staging:
            temporary = Path(staging) / COUNT_FILE.name
            temporary.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
            temporary.replace(COUNT_FILE)
        print(f"OpenAlex reports {snapshot['count']} works. The page lists {len(works)}.")


if __name__ == "__main__":
    main()
