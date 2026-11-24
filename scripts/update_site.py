"""Generate the HTML credit tables and public JSON dataset from the project CSV."""

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
PROFILES_FILE = ROOT / "scripts" / "editor_profiles.json"
WORKS_FILTER = "primary_location.source.id:s125754415,topics.id:t11483,type:article,publication_year:2020-"
ROLES = {
    "Edited by": "editor",
    "Contributed by": "contributor",
    "Commentary": "commentary",
    "No editor listed": "unlisted",
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


def public_article(work, profiles):
    doi = work["doi"].removeprefix("https://doi.org/")
    if not doi.startswith("10.1073/pnas.") or not doi.removeprefix("10.1073/pnas.").isalnum():
        raise ValueError(f"Invalid PNAS article DOI: {work['doi']!r}")
    name = work["credited_name"].strip()
    canonical = canonical_name(name)
    return {
        "title": work["title"],
        "publication_date": date.fromisoformat(work["publication_date"]).isoformat(),
        "doi": doi,
        "credit_source_url": f"https://www.pnas.org/doi/full/{doi}",
        "role": ROLES[work["credit"]],
        "credited_name": name or None,
        "canonical_name": canonical or None,
        "openalex_author_url": profiles.get(canonical, {}).get("openalex_author_url"),
        "accepting_editorial_board_member": work["accepting_editorial_board_member"].strip() or None,
        "note": work["note"].strip() or None,
    }


def author_link(name, url, css_class):
    if not name:
        return ""
    if not url:
        return f'<span class="{css_class}">{escape(name)}</span>'
    return (f'<a class="{css_class}" href="{escape(url)}" target="_blank" rel="noopener" '
            f'aria-label="OpenAlex author profile for {escape(name)}">{escape(name)}</a>')


def article_row(number, article):
    day = date.fromisoformat(article["publication_date"])
    role = article["role"]
    label = "No editor listed" if role == "unlisted" else role.capitalize()
    name = article["credited_name"]
    title = escape(article["title"])
    publisher_url = escape(article["credit_source_url"])
    note = "" if role == "commentary" else (article["note"] or "")
    board = article["accepting_editorial_board_member"]
    if board:
        note = f"{note} Accepted by editorial board member {board}.".strip()
    note_html = f'<small class="article-note">{escape(note)}</small>' if note else ""
    name_html = author_link(name, article["openalex_author_url"], "credit-name")
    return (
        '<tr>\n'
        f'  <td class="row-number">{number}</td>\n'
        f'  <td class="date"><time datetime="{day}">{day:%b} {day.day}, {day.year}</time></td>\n'
        f'  <td class="work"><a class="article-title" href="{publisher_url}" target="_blank" rel="noopener">{title}</a>{note_html}</td>\n'
        f'  <td class="person">{name_html}<span class="role {role}">{label}</span></td>\n'
        f'  <td class="source"><a href="{publisher_url}" target="_blank" rel="noopener" '
        f'aria-label="PNAS HTML for {title}">PNAS HTML</a></td>\n'
        '</tr>'
    )


def build_dataset(works, snapshot):
    profiles = json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
    articles = sorted((public_article(work, profiles) for work in works),
                      key=lambda a: a["publication_date"], reverse=True)
    article_years = [date.fromisoformat(a["publication_date"]).year for a in articles]
    years = list(range(min(article_years), max(article_years) + 1)) if article_years else []
    edited = defaultdict(Counter)
    contributed = defaultdict(Counter)
    for article, year in zip(articles, article_years):
        name = article["canonical_name"]
        if article["role"] == "editor":
            edited[name][year] += 1
        elif article["role"] == "contributor":
            contributed[name][year] += 1

    names = sorted(edited.keys() | contributed.keys(),
                   key=lambda name: (-edited[name].total(), -contributed[name].total(), name.casefold()))
    people = []
    for name in names:
        annual_counts = [
            {"year": year, "edited": edited[name][year], "contributed": contributed[name][year]}
            for year in years
        ]
        profile = profiles.get(name, {})
        people.append({"name": name, "openalex_author_url": profile.get("openalex_author_url"),
                       "nas_membership": profile.get("nas_membership"), "annual_counts": annual_counts})
    roles = Counter(a["role"] for a in articles)
    return {
        "journal": "Proceedings of the National Academy of Sciences",
        "topic": "Tropical and Extratropical Cyclones Research",
        "openalex_filter": WORKS_FILTER,
        "openalex_count": snapshot["count"],
        "openalex_count_checked_at": snapshot["checked_at"],
        "listed_count": len(articles),
        "role_counts": {role: roles[role] for role in ROLES.values()},
        "years": years,
        "people": people,
        "articles": articles,
    }


def editor_table(dataset):
    headings = "".join(f'<th scope="col">{year}</th>' for year in dataset["years"])
    rows = [f'<thead><tr><th scope="col" class="row-number" aria-label="Row number">#</th>'
            f'<th scope="col">Editors</th>{headings}</tr></thead>', "<tbody>"]
    for number, person in enumerate(dataset["people"], start=1):
        cells = []
        for counts in person["annual_counts"]:
            value = str(counts["edited"])
            if counts["contributed"]:
                value += f'/{counts["contributed"]}'
            cells.append("<td>0</td>" if value == "0" else f'<td class="nonzero">{value}</td>')
        name = escape(person["name"])
        heading = author_link(person["name"], person["openalex_author_url"], "editor-name")
        if membership := person["nas_membership"]:
            url = escape(membership["profile_url"])
            year = membership["election_year"]
            description = f'{escape(membership["membership_type"])}, elected {year}'
            heading += (f' <a class="nas-profile" href="{url}" target="_blank" rel="noopener" '
                        f'title="{description}" aria-label="NAS profile for {name}, {description}">{year}</a>')
        rows.append(f'<tr><td class="row-number">{number}</td><th scope="row">{heading}</th>{"".join(cells)}</tr>')
    return [*rows, "</tbody>"]


def write_if_changed(path, contents):
    if path.exists() and path.read_text(encoding="utf-8") == contents:
        return
    with TemporaryDirectory(dir=path.parent) as staging:
        temporary = Path(staging) / path.name
        temporary.write_text(contents, encoding="utf-8")
        if path.exists():
            temporary.chmod(path.stat().st_mode)
        temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-openalex", action="store_true", help="Fetch a new count before rendering.")
    options = parser.parse_args()
    with (ROOT / "pnas_cyclone_editor_credits.csv").open(encoding="utf-8-sig", newline="") as source:
        works = list(csv.DictReader(source))
    snapshot = check_openalex() if options.check_openalex else json.loads(COUNT_FILE.read_text(encoding="utf-8"))
    dataset = build_dataset(works, snapshot)
    sections = {
        "EDITOR_TABLE": editor_table(dataset),
        "ARTICLES": [article_row(number, article) for number, article in enumerate(dataset["articles"], start=1)],
        "FRESHNESS": freshness(snapshot, len(works)),
    }
    index = ROOT / "index.html"
    document = index.read_text(encoding="utf-8")
    for section, lines in sections.items():
        start, end = f"<!-- BEGIN {section} -->", f"<!-- END {section} -->"
        if document.count(start) != 1 or document.count(end) != 1:
            raise ValueError(f"Expected one marker pair for {section}")
        before, _, rest = document.partition(start)
        _, closing, after = rest.partition(end)
        if not closing:
            raise ValueError(f"Reversed markers for {section}")
        document = before + start + "\n" + "\n".join(lines) + "\n" + end + after
    write_if_changed(index, document)
    write_if_changed(ROOT / "data.json", json.dumps(dataset, ensure_ascii=False, indent=2) + "\n")
    if options.check_openalex:
        write_if_changed(COUNT_FILE, json.dumps(snapshot, indent=2) + "\n")
        print(f"OpenAlex reports {snapshot['count']} works. The page lists {len(works)}.")


if __name__ == "__main__":
    main()
