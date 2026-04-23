#!/usr/bin/env python3
"""
Create an AMC 12 Prep LaTeX document from extracted problems.

Usage:
    # Single year and version
    python create_amc12_prep.py --problems 2002A:1-5 --include-solutions

    # Single-test years 2000 and 2001 (no A/B split)
    python create_amc12_prep.py --problems 2000:1-5
    python create_amc12_prep.py --problems 2001:10

    # Multiple years/versions
    python create_amc12_prep.py --problems 2002A:1-5,2024B:10-15

    # Year range, picks up A+B automatically (and the single AMC 12 for 2000/2001)
    # e.g. 2000-2002B:10 -> 2000 Q10, 2001 Q10, 2002A Q10, 2002B Q10
    python create_amc12_prep.py --problems 2000-2002B:10
    python create_amc12_prep.py --problems 2010-2012:25   # all A+B of 2010..2012
    python create_amc12_prep.py --problems 2002A-2004B:1-3

    # From a file listing problems
    python create_amc12_prep.py --from-file problem_list.txt

    # Show available problems
    python create_amc12_prep.py --list

    # Specify a custom date for output filename (default: today)
    python create_amc12_prep.py --problems 2002A:1-5 --date 2025-12-29

Example problem_list.txt:
    2002A:1-5
    2024B:10-15
    2023A:1,3,5
    2000-2002B:10
"""

import argparse
import logging
import re
from pathlib import Path
from datetime import datetime
import os

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


class AMC12PrepBuilder:
    def __init__(self, problems_root, output_root=None):
        self.problems_root = Path(problems_root)
        # Where default output files are written. Defaults to problems_root
        # for backward compatibility, but callers typically point this at
        # the math-mico-plan/ directory.
        self.output_root = Path(output_root) if output_root else self.problems_root
        self.found_problems = {}
        self._discover_problems()

    def _discover_problems(self):
        """Discover all available AMC problem directories."""
        for item in self.problems_root.iterdir():
            if item.is_dir():
                # Match pattern: YYYY-AMC12{A/B}_Problems, or YYYY-AMC12_Problems
                # (2000 and 2001 did not have an A/B split).
                match = re.match(r'(\d{4})-AMC12([AB]?)_Problems$', item.name)
                if match:
                    year = int(match.group(1))
                    version = match.group(2)  # '' for 2000/2001
                    key = f"{year}{version}"
                    self.found_problems[key] = item

    def list_problems(self):
        """List all available AMC problem sets."""
        if not self.found_problems:
            logger.warning("No AMC problem directories found")
            return

        for key in sorted(self.found_problems.keys()):
            problem_dir = self.found_problems[key]
            problem_files = list(problem_dir.glob("*.org"))
            problem_numbers = sorted([int(re.search(r'Problem(\d+)', f.name).group(1))
                                     for f in problem_files if re.search(r'Problem(\d+)', f.name)])
            logger.info(f"{key}: Problems {min(problem_numbers)}-{max(problem_numbers)} "
                       f"({len(problem_files)} total)")

    @staticmethod
    def _parse_problem_numbers(problem_list):
        """Parse a problem list like '1-5', '1,3,5', or '10' into a sorted unique list."""
        numbers = []
        for part in problem_list.split(','):
            part = part.strip()
            if not part:
                continue
            if '-' in part:
                start, end = map(int, part.split('-'))
                numbers.extend(range(start, end + 1))
            else:
                numbers.append(int(part))
        return sorted(set(numbers))

    @staticmethod
    def _expand_year_version_range(start_year, start_version, end_year, end_version):
        """
        Expand a (start_year, start_version) .. (end_year, end_version) range
        into an ordered list of (year, version) pairs.

        Rules:
        - Years 2000 and 2001 only have a single AMC 12 (version = '').
        - Other years have both 'A' and 'B'.
        - If start_version == 'B', skip 'A' of start_year.
        - If end_version == 'A', skip 'B' of end_year.
        - Empty start_version / end_version means "include everything in that year".
        """
        pairs = []
        for year in range(start_year, end_year + 1):
            if year in (2000, 2001):
                pairs.append((year, ''))
                continue

            versions = ['A', 'B']
            if year == start_year and start_version == 'B':
                versions = ['B']
            if year == end_year and end_version == 'A':
                versions = [v for v in versions if v == 'A']
            for v in versions:
                pairs.append((year, v))
        return pairs

    def parse_problem_spec(self, spec):
        """
        Parse a problem specification into (year, version, problem_number) tuples.

        Supported formats:
          - Single set:  'YYYY[V]:problem_list'
              e.g. '2002A:1-5', '2024B:1,3,5', '2000:10' (single AMC 12)
          - Year range:  'YYYY[V]-YYYY[V]:problem_list'
              e.g. '2000-2002B:10' -> 2000 AMC12 Q10, 2001 AMC12 Q10,
                                     2002 AMC12A Q10, 2002 AMC12B Q10
              e.g. '2002A-2004B:1-3', '2010-2012:25'
        """
        spec = spec.strip()

        # Year-range format first: YYYY[V]-YYYY[V]:problem_list
        range_match = re.match(r'(\d{4})([AB]?)-(\d{4})([AB]?):(.+)$', spec)
        if range_match:
            start_year = int(range_match.group(1))
            start_version = range_match.group(2)
            end_year = int(range_match.group(3))
            end_version = range_match.group(4)
            problem_list = range_match.group(5)

            if start_year > end_year:
                logger.error(f"Invalid range (start year > end year): {spec}")
                return []

            problem_numbers = self._parse_problem_numbers(problem_list)
            if not problem_numbers:
                logger.error(f"No problem numbers parsed from spec: {spec}")
                return []

            problems = []
            for year, version in self._expand_year_version_range(
                start_year, start_version, end_year, end_version
            ):
                key = f"{year}{version}"
                if key not in self.found_problems:
                    label = f"{year} AMC 12{version}" if version else f"{year} AMC 12"
                    logger.warning(f"Problem set {label} not found; skipping")
                    continue
                for num in problem_numbers:
                    problems.append((year, version, num))
            return problems

        # Single-set format: YYYY[V]:problem_list
        single_match = re.match(r'(\d{4})([AB]?):(.+)$', spec)
        if not single_match:
            logger.error(f"Invalid problem spec: {spec}")
            return []

        year = int(single_match.group(1))
        version = single_match.group(2)
        problem_list = single_match.group(3)

        # 2000 and 2001 had only a single AMC 12 (no A/B); reject/normalize.
        if year in (2000, 2001):
            if version:
                logger.warning(
                    f"Year {year} had a single AMC 12 (no A/B). "
                    f"Ignoring version '{version}'."
                )
                version = ''
        else:
            if not version:
                logger.error(
                    f"Year {year} requires a version (e.g. {year}A:1-5 or {year}B:1-5)."
                )
                return []

        key = f"{year}{version}"
        if key not in self.found_problems:
            label = f"{year} AMC 12{version}" if version else f"{year} AMC 12"
            logger.error(f"Problem set {label} not found")
            return []

        problem_numbers = self._parse_problem_numbers(problem_list)
        return [(year, version, num) for num in problem_numbers]

    def read_problem(self, year, version, problem_num):
        """Read a problem from the .org file and extract content."""
        key = f"{year}{version}"
        if key not in self.found_problems:
            return None

        filename = f"AMC12{version}_{year}_Problem{problem_num:02d}.org"
        filepath = self.found_problems[key] / filename

        if not filepath.exists():
            logger.warning(f"Problem file not found: {filepath}")
            return None

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()

            # Extract problem statement and answer choices
            problem = self._parse_org_content(content, year, version, problem_num)
            return problem
        except Exception as e:
            logger.error(f"Error reading {filepath}: {e}")
            return None

    def _clean_latex_text(self, text):
        """Clean and normalize LaTeX text."""
        if not text:
            return text

        # Convert [asy]...[/asy] to LaTeX \begin{asy}...\end{asy} format
        # This preserves Asymptote code inline within problem text, matching the original .org structure
        def convert_asy(match):
            asy_content = match.group(1).strip()
            # Uncomment variable definitions that are commented out (e.g., // real l = ...)
            # This fixes cases where variable definitions are commented but used in the code
            asy_content = re.sub(r'//\s*((?:real|pair|int|bool|path|guide|string|transform|picture|frame|Label|Legend|size|margin|pen|projection|triple)\s+\w+\s*=)', r'\1', asy_content)

            # Format the Asymptote code with proper line breaks and indentation
            asy_lines = []
            for line in asy_content.split(';'):
                line = line.strip()
                if line:
                    asy_lines.append(line + ';')
            formatted_asy = '\n'.join(asy_lines)
            return f'\n\\begin{{asy}}\n{formatted_asy}\n\\end{{asy}}\n'

        text = re.sub(r'\[asy\](.*?)\[/asy\]', convert_asy, text, flags=re.DOTALL | re.IGNORECASE)

        # Replace deprecated {n \choose k} with modern \binom{n}{k}
        text = re.sub(r'\{\s*(\d+)\s*\\choose\s*(\d+)\s*\}', r'\\binom{\1}{\2}', text)

        # Remove control characters and strange Unicode
        text = ''.join(char for char in text if ord(char) >= 32 or char in '\n\t')

        # Remove multiple spaces (but preserve intentional line breaks in Asymptote)
        lines = text.split('\n')
        cleaned_lines = [' '.join(line.split()) for line in lines]
        text = '\n'.join(cleaned_lines)

        return text

    def _parse_org_content(self, content, year, version, problem_num):
        """Parse .org file content to extract problem and solutions."""
        lines = content.split('\n')

        problem_data = {
            'year': year,
            'version': version,
            'number': problem_num,
            'problem_text': '',
            'answer_choices': '',
            'solution': ''
        }

        # Extract problem statement (between "Problem" header and answer choices)
        in_problem = False
        in_solution = False
        solution_lines = []

        for i, line in enumerate(lines):
            # Clean the line
            line = self._clean_latex_text(line)

            # Match "Problem" or "Problem N" (some .org files use "Problem 10" format)
            if line.strip() == 'Problem' or re.match(r'^Problem\s+\d+$', line.strip()):
                in_problem = True
                in_solution = False
                continue

            if line.strip().startswith('Solution'):
                in_problem = False
                in_solution = True
                continue

            # Extract problem content
            if in_problem and line.strip():
                # Check if this line is answer choices (starts with $ and contains answer format)
                if line.strip().startswith('$') and ('qquad' in line or '(A)' in line or '(B)' in line):
                    # This is the answer choices line
                    problem_data['answer_choices'] = line.strip()
                    in_problem = False  # Stop collecting problem text
                else:
                    # Accumulate problem text
                    if problem_data['problem_text']:
                        problem_data['problem_text'] += ' '
                    problem_data['problem_text'] += line.strip()

            # Extract solution content
            if in_solution and line.strip():
                # Stop at "See also" or copyright notice
                if any(stop in line for stop in ['See also', 'See Also', 'These problems', 'copyrighted']):
                    break
                if line.strip().startswith('~') or line.startswith('http') or line.startswith('youtu'):
                    continue
                if line.strip() == 'Solution' or line.strip().startswith('Solution '):
                    continue
                solution_lines.append(line)

        # Clean solution
        solution_text = '\n'.join(solution_lines).strip()
        problem_data['solution'] = self._clean_latex_text(solution_text)
        return problem_data

    def generate_latex(self, problems, include_solutions=False, title="AMC 12 Prep 2026",
                       include_toc=False):
        """Generate LaTeX document content."""
        latex_parts = []

        # Document header
        latex_parts.append(r'\documentclass[12pt]{article}')
        latex_parts.append(r'\usepackage{amsmath}')
        latex_parts.append(r'\usepackage{amssymb}')
        latex_parts.append(r'\usepackage{amsthm}')
        latex_parts.append(r'\usepackage{geometry}')
        latex_parts.append(r'\geometry{margin=1in}')
        latex_parts.append(r'\usepackage{xcolor}')
        latex_parts.append(r'\usepackage{asymptote}')
        latex_parts.append('')
        latex_parts.append(r'\title{' + title + '}')
        latex_parts.append(r'\author{}')
        latex_parts.append(r'\date{\today}')
        latex_parts.append('')
        # Hide section numbering
        latex_parts.append(r'\setcounter{secnumdepth}{0}')
        latex_parts.append('')
        latex_parts.append(r'\begin{document}')
        latex_parts.append(r'\maketitle')
        if include_toc:
            latex_parts.append(r'\tableofcontents')
            latex_parts.append(r'\newpage')
        latex_parts.append('')

        # Group problems by year and version
        grouped = {}
        for problem in problems:
            if problem is None:
                continue
            key = f"{problem['year']} AMC 12{problem['version']}"
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(problem)

        # Add problems to document
        for group_key in sorted(grouped.keys()):
            latex_parts.append(r'\section{' + group_key + '}')
            latex_parts.append('')

            for problem in grouped[group_key]:
                problem_label = f"{problem['year']} AMC12{problem['version']} Problem {problem['number']}"
                latex_parts.append(r'\subsection{' + problem_label + '}')
                latex_parts.append('')

                # Problem statement (now includes embedded Asymptote code if present)
                latex_parts.append(r'\textbf{Problem:}')
                latex_parts.append(problem['problem_text'])
                latex_parts.append('')

                # Answer choices
                latex_parts.append(problem['answer_choices'])
                latex_parts.append('')

                # Student fill-in fields (answer + time spent)
                latex_parts.append(r'\vspace{0.3cm}')
                latex_parts.append(r'\noindent\textbf{Answer:}~\underline{\hspace{5cm}}\hfill'
                                   r'\textbf{Time:}~\underline{\hspace{1.2cm}}:\underline{\hspace{1.2cm}}')
                latex_parts.append('')

                # Solution (optional)
                if include_solutions and problem['solution']:
                    latex_parts.append(r'\textbf{Solution:}')
                    latex_parts.append(problem['solution'])
                    latex_parts.append('')

                latex_parts.append(r'\vspace{0.5cm}')
                latex_parts.append('')

        latex_parts.append(r'\end{document}')

        return '\n'.join(latex_parts)

    def save_document(self, latex_content, output_path=None, date_str=None):
        """Save LaTeX document to file."""
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")
        else:
            date_str = normalize_date_str(date_str)
        if output_path is None:
            # Default output path:
            #   {output_root}/math-{YYYY-MM}/amc12-{YYYY-MM-DD}-student.tex
            # where output_root is typically the math-mico-plan/ directory.
            year_month = year_month_from_date_str(date_str)
            output_dir = (
                self.output_root
                / f"math-{year_month}"
            )
            output_path = output_dir / f"amc12-{date_str}-student.tex"
        else:
            output_path = Path(output_path)

        # Create directory if it doesn't exist
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(latex_content)

        logger.info(f"LaTeX document saved to: {output_path}")
        return output_path


def normalize_date_str(date_str: str) -> str:
    """
    Normalize a date string to YYYY-MM-DD.

    Accepts either:
    - YYYY-MM-DD (preferred)
    - YYYYMMDD (legacy)
    """
    s = str(date_str).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    if re.fullmatch(r"\d{8}", s):
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    raise ValueError(f"Invalid date format: {date_str!r}. Use YYYY-MM-DD (preferred) or YYYYMMDD (legacy).")


def year_month_from_date_str(date_str: str) -> str:
    """
    Extract YYYY-MM from a normalized YYYY-MM-DD date string.
    """
    parts = date_str.split("-")
    if len(parts) != 3:
        raise ValueError(f"Expected normalized date YYYY-MM-DD, got: {date_str!r}")
    yyyy, mm, _dd = parts
    return f"{yyyy}-{mm}"


def main():
    parser = argparse.ArgumentParser(
        description="Create an AMC 12 Prep LaTeX document from extracted problems",
        epilog="Examples:\n"
               "  python create_amc12_prep.py --problems 2002A:1-5 --include-solutions\n"
               "  python create_amc12_prep.py --problems 2002A:1-5,2024B:10-15\n"
               "  python create_amc12_prep.py --problems 2000:10          # single AMC 12 (no A/B)\n"
               "  python create_amc12_prep.py --problems 2000-2002B:10    # range: 2000 Q10, 2001 Q10, 2002A Q10, 2002B Q10\n"
               "  python create_amc12_prep.py --from-file problem_list.txt\n"
               "  python create_amc12_prep.py --list",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument('--problems', '-p', help='Problem specification (e.g., 2002A:1-5,2024B:10-15)')
    parser.add_argument('--from-file', '-f', help='Read problem list from file')
    parser.add_argument('--include-solutions', '-s', action='store_true', help='Include solutions in output')
    parser.add_argument('--include-toc', action='store_true',
                        help='Include a table of contents (off by default)')
    parser.add_argument('--list', action='store_true', help='List all available problem sets')
    parser.add_argument('--output', '-o', help='Output LaTeX file path')
    parser.add_argument('--title', '-t', default='AMC 12 Prep 2026', help='Document title')
    parser.add_argument('--date', '-d', default=datetime.now().strftime("%Y-%m-%d"),
                        help='Date for output filename (YYYY-MM-DD format, default: today; also accepts legacy YYYYMMDD)')

    args = parser.parse_args()

    # Find problems root directory.
    # Problem .org files live under <repo>/materials/amc12/<YYYY>-AMC12{A|B}_Problems/.
    # Output files are written under <repo>/math-mico-plan/ (the script's own folder).
    script_dir = Path(__file__).parent
    problems_root = script_dir.parent / "materials" / "amc12"
    output_root = script_dir

    # Initialize builder
    builder = AMC12PrepBuilder(problems_root, output_root=output_root)

    # List available problems
    if args.list:
        builder.list_problems()
        return 0

    # Determine problem specifications
    problem_specs = []

    if args.from_file:
        # Read from file
        problem_file = Path(args.from_file)
        if not problem_file.exists():
            logger.error(f"Problem file not found: {problem_file}")
            return 1

        with open(problem_file, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    problem_specs.append(line)

    elif args.problems:
        # Parse command line specs
        problem_specs = args.problems.split(',')

    else:
        logger.error("Please specify problems with --problems or --from-file")
        parser.print_help()
        return 1

    # Parse and read problems
    problems_to_include = []
    for spec in problem_specs:
        parsed = builder.parse_problem_spec(spec.strip())
        for year, version, num in parsed:
            problem = builder.read_problem(year, version, num)
            if problem:
                problems_to_include.append(problem)
                logger.info(f"Loaded: {year} AMC 12{version} Problem {num}")

    if not problems_to_include:
        logger.error("No problems were successfully loaded")
        return 1

    # Generate LaTeX
    latex_content = builder.generate_latex(
        problems_to_include,
        include_solutions=args.include_solutions,
        title=args.title,
        include_toc=args.include_toc,
    )

    # Save document
    builder.save_document(latex_content, args.output, args.date)

    logger.info(f"Successfully compiled {len(problems_to_include)} problems")
    return 0


if __name__ == "__main__":
    exit(main())
