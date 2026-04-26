#!/usr/bin/env python3
"""
Download AMC 12 answer keys from the AoPS Wiki.

By default this script reads the AoPS AMC 12 index, downloads each answer key,
and writes parsed answer-key files to:

    materials/amc12/answer_keys

Examples:
    python materials/amc12/download_amc12_answer_keys.py
    python materials/amc12/download_amc12_answer_keys.py --source local
    python materials/amc12/download_amc12_answer_keys.py --year 2024 --version 12A
    python materials/amc12/download_amc12_answer_keys.py --fetch playwright --headful
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import quote, unquote, urlparse

from bs4 import BeautifulSoup

from download_amc12_problems import AMC12Downloader


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "answer_keys"
INDEX_TITLE = "AMC_12_Problems_and_Solutions"

logger = logging.getLogger("amc12_answer_keys")


@dataclass(frozen=True)
class Exam:
    year: int
    version: str
    season: Optional[str] = None
    title_base: Optional[str] = None

    @property
    def display_name(self) -> str:
        season = f" {self.season}" if self.season else ""
        version = "AMC 12" if self.version == "12" else f"AMC {self.version}"
        return f"{self.year}{season} {version}"

    @property
    def slug(self) -> str:
        if self.title_base:
            return self.title_base
        parts = [str(self.year)]
        if self.season:
            parts.append(self.season)
        if self.version == "12":
            parts.extend(["AMC", "12"])
        else:
            parts.extend(["AMC", self.version])
        return "_".join(parts)

    @property
    def answer_key_title(self) -> str:
        if self.slug.endswith("_Answer_Key"):
            return self.slug
        return f"{self.slug}_Answer_Key"

    @property
    def file_stem(self) -> str:
        version = "AMC12" if self.version == "12" else f"AMC{self.version}"
        parts = [version, str(self.year)]
        if self.season:
            parts.append(self.season)
        parts.append("Answer_Key")
        return "_".join(parts)


def exam_sort_key(exam: Exam) -> tuple[int, int, str]:
    season_order = {"Spring": 0, None: 1, "Fall": 2}
    return (exam.year, season_order.get(exam.season, 1), exam.version)


def configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")
    logger.setLevel(level)


def parse_exam_title(title: str) -> Optional[Exam]:
    normalized = title.strip().replace(" ", "_")
    normalized = normalized.removesuffix("_Problems").removesuffix("_Answer_Key")

    match = re.fullmatch(
        r"(?P<year>\d{4})(?:_(?P<season>Fall|Spring))?_AMC_(?P<version>12(?:[ABP])?)",
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    version = match.group("version").upper()
    season = match.group("season")
    if season:
        season = season.capitalize()

    return Exam(
        year=int(match.group("year")),
        version=version,
        season=season,
        title_base=f"{match.group('year')}"
        f"{'_' + season if season else ''}_AMC_{version}",
    )


def local_exam_from_dirname(dirname: str) -> Optional[Exam]:
    match = re.fullmatch(
        r"(?P<year>\d{4})(?:-(?P<season>Fall|Spring))?-AMC(?P<version>12(?:[ABP])?)_Problems",
        dirname,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    version = match.group("version").upper()
    season = match.group("season")
    if season:
        season = season.capitalize()

    parts = [match.group("year")]
    if season:
        parts.append(season)
    parts.extend(["AMC", version])

    return Exam(
        year=int(match.group("year")),
        version=version,
        season=season,
        title_base="_".join(parts),
    )


def title_url(base_url: str, title: str) -> str:
    return f"{base_url}?title={quote(title, safe='_')}"


def discover_local_exams(materials_dir: Path) -> list[Exam]:
    exams = {
        exam
        for path in materials_dir.iterdir()
        if path.is_dir()
        for exam in [local_exam_from_dirname(path.name)]
        if exam is not None
    }
    return sorted(exams, key=exam_sort_key)


def discover_index_exams(downloader: AMC12Downloader) -> list[Exam]:
    url = title_url(downloader.base_url, INDEX_TITLE)
    logger.info("Reading AoPS index: %s", url)
    html = downloader.fetch_html(url)
    if downloader._looks_like_cloudflare_challenge(html):
        raise RuntimeError("AoPS returned a Cloudflare challenge page.")

    soup = BeautifulSoup(html, "html.parser")
    exams: set[Exam] = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "")
        parsed = urlparse(href)
        path = parsed.path or href
        title = ""

        if "title=" in href:
            # Plain split is enough here because wiki titles use underscores.
            title = href.split("title=", 1)[1].split("&", 1)[0]
        elif "/wiki/index.php/" in path:
            title = path.rsplit("/wiki/index.php/", 1)[1]

        if not title:
            continue

        title = unquote(title)
        exam = parse_exam_title(title)
        if exam:
            exams.add(exam)

    return sorted(exams, key=exam_sort_key)


def filter_exams(
    exams: Iterable[Exam],
    year: Optional[int],
    version: Optional[str],
    season: Optional[str],
    start_year: Optional[int],
    end_year: Optional[int],
    exclude_amc12p: bool,
) -> list[Exam]:
    normalized_version = version.upper() if version else None
    normalized_season = season.capitalize() if season else None

    filtered = []
    for exam in exams:
        if year is not None and exam.year != year:
            continue
        if start_year is not None and exam.year < start_year:
            continue
        if end_year is not None and exam.year > end_year:
            continue
        if normalized_version and exam.version != normalized_version:
            continue
        if normalized_season and exam.season != normalized_season:
            continue
        if exam.version == "12P" and exclude_amc12p:
            continue
        filtered.append(exam)

    return sorted(filtered, key=exam_sort_key)


def visible_text(element) -> str:
    return re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip()


def parse_cell_answer(text: str) -> Optional[str]:
    match = re.search(r"\b([A-E])\b", text.strip().upper())
    if match:
        return match.group(1)
    return None


def parse_number(text: str) -> Optional[int]:
    match = re.search(r"\b([1-9]|1\d|2[0-5])\b", text.strip())
    if match:
        return int(match.group(1))
    return None


def extract_answers_from_tables(soup: BeautifulSoup) -> dict[int, str]:
    answers: dict[int, str] = {}

    for table in soup.find_all("table"):
        rows = [
            [visible_text(cell) for cell in row.find_all(["th", "td"])]
            for row in table.find_all("tr")
        ]
        rows = [row for row in rows if row]

        # Common row-wise format: | 1 | C |
        for row in rows:
            if len(row) < 2:
                continue
            number = parse_number(row[0])
            answer = parse_cell_answer(row[1])
            if number and answer:
                answers[number] = answer

        # Common two-row format:
        # | Problem | 1 | 2 | ... |
        # | Answer  | A | B | ... |
        for i, row in enumerate(rows[:-1]):
            next_row = rows[i + 1]
            numbers = [parse_number(cell) for cell in row]
            answer_values = [parse_cell_answer(cell) for cell in next_row]
            pairs = [
                (number, answer)
                for number, answer in zip(numbers, answer_values)
                if number and answer
            ]
            if len(pairs) >= 10:
                answers.update(pairs)

    return answers


def extract_answers_from_text(soup: BeautifulSoup) -> dict[int, str]:
    content = soup.find("div", {"id": "mw-content-text"}) or soup
    text = content.get_text("\n")
    answers: dict[int, str] = {}

    for match in re.finditer(r"(?m)^\s*([1-9]|1\d|2[0-5])\s*[\.\):\-]\s*([A-E])\s*$", text):
        answers[int(match.group(1))] = match.group(2)

    if len(answers) < 25:
        compact = re.sub(r"\s+", " ", text)
        for match in re.finditer(r"\b([1-9]|1\d|2[0-5])\s*[\.\):\-]\s*([A-E])\b", compact):
            answers.setdefault(int(match.group(1)), match.group(2))

    return answers


def extract_answers_from_lists(soup: BeautifulSoup) -> dict[int, str]:
    answers: dict[int, str] = {}
    content = soup.find("div", {"id": "mw-content-text"}) or soup

    for ordered_list in content.find_all("ol"):
        items = ordered_list.find_all("li", recursive=False)
        if len(items) < 10:
            continue

        start = parse_number(ordered_list.get("start", "1")) or 1
        for offset, item in enumerate(items):
            text = visible_text(item)
            explicit_number = parse_number(text)
            answer = parse_cell_answer(text)
            if not answer:
                continue

            number = explicit_number if explicit_number else start + offset
            if 1 <= number <= 25:
                answers[number] = answer

    return answers


def extract_answer_key(html: str) -> dict[int, str]:
    soup = BeautifulSoup(html, "html.parser")
    answers = extract_answers_from_tables(soup)
    answers.update(extract_answers_from_lists(soup))
    answers.update(extract_answers_from_text(soup))
    return {number: answers[number] for number in sorted(answers) if 1 <= number <= 25}


def page_exists(html: str, title: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    page_title = visible_text(soup.find("title")) if soup.find("title") else ""
    body_text = visible_text(soup)
    missing_markers = [
        "There is currently no text in this page",
        "This page does not exist",
        "Bad title",
    ]
    if any(marker.lower() in body_text.lower() for marker in missing_markers):
        return False
    return title.replace("_", " ") in page_title or "Answer Key" in body_text


def read_cookie_file(path: Optional[Path]) -> Optional[str]:
    if not path:
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return None
    if "PASTE THE FULL COOKIE STRING HERE" in text:
        raise ValueError("Cookie file still contains placeholder text.")
    return normalize_cookie_string(text)


def normalize_cookie_string(raw: Optional[str]) -> Optional[str]:
    if raw is None:
        return None
    value = "\n".join(line.strip() for line in str(raw).splitlines() if line.strip()).strip()
    if not value:
        return None
    if "=" not in value:
        logger.warning("Cookie has no '='; treating it as a bare cf_clearance value.")
        value = f"cf_clearance={value}"
    return value


def write_answer_key_files(output_dir: Path, exam: Exam, source_url: str, answers: dict[int, str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = output_dir / f"{exam.file_stem}.csv"
    org_path = output_dir / f"{exam.file_stem}.org"

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["problem", "answer"])
        for number in range(1, 26):
            writer.writerow([number, answers.get(number, "")])

    with org_path.open("w", encoding="utf-8") as f:
        f.write(f"{exam.display_name} Answer Key\n")
        f.write(f"Source: {source_url}\n\n")
        f.write("| Problem | Answer |\n")
        f.write("|-\n")
        for number in range(1, 26):
            f.write(f"| {number} | {answers.get(number, '')} |\n")


def write_manifest(output_dir: Path, records: list[dict]) -> None:
    manifest_path = output_dir / "amc12_answer_keys.json"
    manifest_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


def read_existing_csv(csv_path: Path) -> dict[int, str]:
    answers: dict[int, str] = {}
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                number = int(row.get("problem", ""))
            except ValueError:
                continue
            answer = (row.get("answer") or "").strip().upper()
            if 1 <= number <= 25:
                answers[number] = answer
    return answers


def make_record(exam: Exam, source_url: str, answers: dict[int, str]) -> dict:
    return {
        "year": exam.year,
        "season": exam.season,
        "version": exam.version,
        "exam": exam.display_name,
        "source_url": source_url,
        "answer_count": sum(1 for number in range(1, 26) if answers.get(number)),
        "answers": {str(number): answers.get(number, "") for number in range(1, 26)},
    }


def download_answer_key(
    downloader: AMC12Downloader,
    exam: Exam,
    output_dir: Path,
    save_html: bool,
    require_complete: bool,
    force: bool,
) -> Optional[dict]:
    source_url = title_url(downloader.base_url, exam.answer_key_title)
    csv_path = output_dir / f"{exam.file_stem}.csv"

    if csv_path.exists() and not force:
        logger.info("Skipping existing key: %s", csv_path.name)
        return make_record(exam, source_url, read_existing_csv(csv_path))

    logger.info("Downloading %s", exam.display_name)
    html = downloader.fetch_html(source_url)
    if downloader._looks_like_cloudflare_challenge(html):
        raise RuntimeError(f"AoPS returned a Cloudflare challenge for {source_url}")

    if not page_exists(html, exam.answer_key_title):
        logger.warning("Answer-key page not found: %s", source_url)
        return None

    answers = extract_answer_key(html)
    answer_count = sum(1 for number in range(1, 26) if answers.get(number))
    if require_complete and answer_count < 25:
        logger.warning(
            "Skipping incomplete key for %s: found %s/25 answers",
            exam.display_name,
            answer_count,
        )
        return None

    write_answer_key_files(output_dir, exam, source_url, answers)

    if save_html:
        html_dir = output_dir / "html"
        html_dir.mkdir(parents=True, exist_ok=True)
        (html_dir / f"{exam.file_stem}.html").write_text(html, encoding="utf-8")

    return make_record(exam, source_url, answers)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download all AMC 12 answer keys from AoPS Wiki.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Notes:
  Default output is materials/amc12/answer_keys.
  AoPS may block plain HTTP requests. If that happens, try:
    python materials/amc12/download_amc12_answer_keys.py --fetch playwright --headful
        """,
    )

    parser.add_argument(
        "--source",
        choices=["index", "local"],
        default="index",
        help="Discover exams from the AoPS index or local materials folders (default: index).",
    )
    parser.add_argument("--output", "-o", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--year", type=int, help="Only download one year.")
    parser.add_argument(
        "--version",
        "-v",
        choices=["12", "12A", "12B", "12P"],
        help="Only download one AMC 12 version.",
    )
    parser.add_argument("--season", choices=["Spring", "Fall"], help="Only download one season.")
    parser.add_argument("--start-year", type=int, help="Skip exams before this year.")
    parser.add_argument("--end-year", type=int, help="Skip exams after this year.")
    parser.add_argument(
        "--exclude-amc12p",
        action="store_true",
        help="Skip the 2002 AMC 12P answer key when using --source index.",
    )
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Save answer keys even if fewer than 25 answers were parsed.",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite existing downloaded keys.")
    parser.add_argument("--save-html", action="store_true", help="Also save the raw AoPS HTML pages.")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between downloads in seconds.")
    parser.add_argument("--timeout", type=int, default=120, help="Timeout for each request in seconds.")
    parser.add_argument(
        "--fetch",
        choices=["auto", "requests", "cloudscraper", "undetected", "curl_cffi", "playwright"],
        default="auto",
        help="Fetch method inherited from download_amc12_problems.py.",
    )
    parser.add_argument("--headful", action="store_true", help="Use a visible browser for Playwright/Chrome.")
    parser.add_argument(
        "--playwright-channel",
        choices=["chromium", "chrome", "msedge"],
        default="chromium",
    )
    parser.add_argument("--playwright-user-data-dir", default=None)
    parser.add_argument("--curl-impersonate", default="chrome120")
    parser.add_argument(
        "--cookies-from",
        choices=["none", "auto", "chrome", "edge", "firefox", "brave", "chromium", "opera"],
        default="none",
    )
    parser.add_argument("--cookie", default=None, help="Raw Cookie header string.")
    parser.add_argument("--cookie-file", type=Path, default=None, help="File containing a raw Cookie header.")
    parser.add_argument("--user-agent", default=None)
    parser.add_argument("--verbose", action="store_true")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    configure_logging(args.verbose)

    try:
        cookie_value = normalize_cookie_string(args.cookie) or read_cookie_file(args.cookie_file)
    except Exception as exc:
        logger.error("Failed to read cookie: %s", exc)
        return 1

    downloader = AMC12Downloader(
        timeout=args.timeout,
        fetch_method=args.fetch,
        headless=not args.headful,
        playwright_channel=args.playwright_channel,
        playwright_user_data_dir=args.playwright_user_data_dir,
        curl_impersonate=args.curl_impersonate,
        cookies_from=args.cookies_from,
        cookie=cookie_value,
        user_agent=args.user_agent,
    )

    try:
        if args.source == "local":
            exams = discover_local_exams(SCRIPT_DIR)
        else:
            exams = discover_index_exams(downloader)

        exams = filter_exams(
            exams,
            year=args.year,
            version=args.version,
            season=args.season,
            start_year=args.start_year,
            end_year=args.end_year,
            exclude_amc12p=args.exclude_amc12p,
        )

        if not exams:
            logger.error("No AMC 12 exams matched the requested filters.")
            return 1

        logger.info("Found %s exam(s) to process.", len(exams))
        records: list[dict] = []

        for index, exam in enumerate(exams, start=1):
            try:
                record = download_answer_key(
                    downloader=downloader,
                    exam=exam,
                    output_dir=args.output,
                    save_html=args.save_html,
                    require_complete=not args.allow_incomplete,
                    force=args.force,
                )
                if record:
                    records.append(record)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                logger.error("Failed to download %s: %s", exam.display_name, exc)

            if index < len(exams):
                time.sleep(max(0.0, args.delay))

        args.output.mkdir(parents=True, exist_ok=True)
        write_manifest(args.output, records)
        logger.info("Downloaded %s answer key(s). Manifest: %s", len(records), args.output / "amc12_answer_keys.json")
        return 0 if records else 1

    except KeyboardInterrupt:
        logger.info("Interrupted.")
        return 1
    except Exception as exc:
        logger.error("Unexpected error: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
