"""Refresh the HTML credit tables and totals from the project CSV."""

import argparse
import csv
import json
import os
import subprocess
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
COUNT_FILE = ROOT / "scripts" / "openalex_count.json"
WORKS_FILTER = "primary_location.source.id:s125754415,topics.id:t11483,type:article,publication_year:2020-"
ROLES = {
    "Edited by": ("editor", "Editor"),
    "Contributed by": ("contributor", "Contributor"),
    "Commentary": ("commentary", "Commentary"),
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


def article_row(work):
    day = date.fromisoformat(work["publication_date"])
    role, label = ROLES[work["credit"]]
    name = work["credited_name"]
    title = escape(work["title"])
    doi = work["doi"].removeprefix("https://doi.org/")
    if not doi.startswith("10.1073/pnas.") or not doi.removeprefix("10.1073/pnas.").isalnum():
        raise ValueError(f"Invalid PNAS article DOI: {work['doi']!r}")
    publisher_url = f"https://www.pnas.org/doi/full/{doi}"
    note = "" if role == "commentary" else work["note"].strip()
    board = work["accepting_editorial_board_member"]
    if board:
        note = f"{note} Accepted by editorial board member {board}.".strip()
    note_html = f'<small class="article-note">{escape(note)}</small>' if note else ""
    name_html = f'<span class="credit-name">{escape(name)}</span>' if name else ""
    return (
        '<tr>\n'
        f'  <td class="date"><time datetime="{day}">{day:%b} {day.day}, {day.year}</time></td>\n'
        f'  <td class="work"><a class="article-title" href="{publisher_url}">{title}</a>{note_html}</td>\n'
        f'  <td class="person">{name_html}<span class="role {role}">{label}</span></td>\n'
        f'  <td class="source"><a href="{publisher_url}" '
        f'aria-label="PNAS HTML for {title}">PNAS HTML</a></td>\n'
        '</tr>'
    )


def editor_table(works):
    years = [date.fromisoformat(work["publication_date"]).year for work in works]
    columns = range(min(years), max(years) + 1) if years else []
    edited = defaultdict(Counter)
    contributed = defaultdict(Counter)
    for work, year in zip(works, years):
        name = canonical_name(work["credited_name"])
        if work["credit"] == "Edited by":
            edited[name][year] += 1
        elif work["credit"] == "Contributed by":
            contributed[name][year] += 1

    def count_row(label, edits, contributions):
        cells = []
        for year in columns:
            value = str(edits[year])
            if contributions[year]:
                value += f"/{contributions[year]}"
            cells.append("<td>0</td>" if value == "0" else f'<td class="nonzero">{value}</td>')
        return f'<tr><th scope="row">{escape(label)}</th>{"".join(cells)}</tr>'

    names = sorted(edited.keys() | contributed.keys(),
                   key=lambda name: (-edited[name].total(), -contributed[name].total(), name.casefold()))
    headings = "".join(f'<th scope="col">{year}</th>' for year in columns)
    return [
        f'<thead><tr><th scope="col">Editors</th>{headings}</tr></thead>',
        "<tbody>",
        *(count_row(name, edited[name], contributed[name]) for name in names),
        "</tbody>",
    ]


def build_sections(works):
    ordered = sorted(works, key=lambda work: work["publication_date"], reverse=True)
    return {
        "EDITOR_TABLE": editor_table(works),
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
