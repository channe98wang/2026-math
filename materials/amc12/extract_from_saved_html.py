#!/usr/bin/env python3
"""
Extract AMC problem from a saved HTML file (saved from Chrome).

Usage:
1. Open Chrome and load the AMC problem page
2. Press Ctrl+S to save as HTML
3. Run: python extract_from_saved_html.py problem_page.html

The script automatically detects the version, year, and problem number from the page!
"""

import argparse
import re
from pathlib import Path
from bs4 import BeautifulSoup
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Import the original downloader to reuse its methods
import sys
sys.path.insert(0, str(Path(__file__).parent))
from download_amc12_problems import AMC12Downloader


def extract_problem_info(html):
    """
    Extract version, year, and problem number from the HTML page.
    Looks for patterns like "2002 AMC 12A Problems/Problem 1"
    """
    # Pattern: "YYYY AMC 12[A/B] Problems/Problem N"
    pattern = r'(\d{4})\s+AMC\s+12([AB])\s+Problems.*?Problem\s+(\d+)'

    match = re.search(pattern, html, re.IGNORECASE)
    if match:
        year = int(match.group(1))
        version = f"12{match.group(2).upper()}"
        problem = int(match.group(3))
        return version, year, problem

    # Also check the page title
    soup = BeautifulSoup(html, 'html.parser')
    title = soup.find('title')
    if title:
        match = re.search(pattern, title.text, re.IGNORECASE)
        if match:
            year = int(match.group(1))
            version = f"12{match.group(2).upper()}"
            problem = int(match.group(3))
            return version, year, problem

    return None, None, None


def main():
    # Determine default output directory following project structure
    script_dir = Path(__file__).parent

    parser = argparse.ArgumentParser(
        description="Extract AMC problem from a saved HTML file (auto-detects version/year/problem)",
        epilog=f"Example: python extract_from_saved_html.py problem_page.html\nOutput: {script_dir}/{{year}}-{{version}}_Problems/"
    )
    parser.add_argument('html', help='Path to saved HTML file from Chrome')
    parser.add_argument('--output', '-o', default=str(script_dir), help=f'Output directory (default: {script_dir})')

    args = parser.parse_args()

    html_file = Path(args.html)
    if not html_file.exists():
        logger.error(f"File not found: {html_file}")
        return 1

    logger.info(f"Reading HTML from: {html_file}")

    try:
        with open(html_file, 'r', encoding='utf-8') as f:
            html = f.read()

        # Extract problem info from HTML
        version, year, problem = extract_problem_info(html)

        if not version or not year or not problem:
            logger.error("Could not detect AMC version, year, or problem number from HTML")
            logger.info("Make sure the page is a valid AoPS AMC problem page")
            return 1

        logger.info(f"Detected: AMC {version} {year}, Problem {problem}")

        # Create output directory following convention: {year}-AMC12{A/B}_Problems
        output_dir = Path(args.output) / f"{year}-AMC{version}_Problems"
        output_dir.mkdir(parents=True, exist_ok=True)

        soup = BeautifulSoup(html, 'html.parser')
        downloader = AMC12Downloader()
        content = downloader.extract_problem_content(soup, version, year, problem)

        if not content.strip():
            logger.error(f"No content found for Problem {problem}")
            return 1

        downloader.save_problem(content, version, year, problem, str(output_dir))
        logger.info(f"Successfully extracted AMC {version} {year} Problem {problem}")
        return 0

    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
