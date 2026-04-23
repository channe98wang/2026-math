import json
import os
import re
import sys
import argparse
import csv
from collections import Counter
from typing import Dict, List, Tuple, Optional

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import LabelEncoder

BASE_DIR = os.path.join("training-plans", "plan-gen", "amc12")

TARGET_DIRECTORIES = [
    "2016-AMC12A_Problems",
    "2016-AMC12B_Problems",
    "2017-AMC12A_Problems",
    "2017-AMC12B_Problems",
    "2018-AMC12A_Problems",
    "2018-AMC12B_Problems",
    "2019-AMC12A_Problems",
    "2019-AMC12B_Problems",
    "2020-AMC12A_Problems",
    "2020-AMC12B_Problems",
    "2021-AMC12A_Problems",
    "2021-AMC12B_Problems",
    "2021-Fall-AMC12A_Problems",
    "2021-Fall-AMC12B_Problems",
    "2022-AMC12A_Problems",
    "2022-AMC12B_Problems",
    "2023-AMC12A_Problems",
    "2023-AMC12B_Problems",
    "2024-AMC12A_Problems",
    "2024-AMC12B_Problems",
    "2025-AMC12A_Problems",
]

TRAINING_SOURCE = os.path.join(BASE_DIR, "AMC12B_2025_PREP_MASTER.md")

PRIMARY_TOPICS = ("Algebra", "Geometry", "Combinatorics", "Number Theory")


def load_problem_statement(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        raw_text = handle.read()
    lines = raw_text.splitlines()
    statement_lines: List[str] = []
    recording = False
    has_content = False

    for line in lines:
        stripped = line.strip()
        lowered = stripped.lower()
        normalized = lowered.lstrip("0123456789. ")

        if recording and normalized.startswith("solution"):
            if has_content:
                break
            else:
                continue

        if not recording:
            if (
                not stripped
                or lowered == "contents"
                or ("problem" in lowered and "amc" in lowered)
                or lowered.startswith("see also")
                or lowered.startswith("video solution")
                or normalized.startswith("remark")
                or normalized.startswith("solution")
            ):
                continue
            if normalized == "problem":
                recording = True
                continue
            recording = True
            continue

        if not stripped:
            statement_lines.append("")
            continue
        if recording and not has_content:
            if stripped[:1].isdigit():
                continue
            if normalized in {"diagram", "problem"}:
                continue
            if normalized.startswith("diagram"):
                continue
        has_content = has_content or bool(stripped)

        if lowered.startswith("video solution") or lowered.startswith("see also"):
            continue

        if lowered[:1].isdigit() and (
            "video solution" in lowered or "solution" in lowered or "see also" in lowered
        ):
            continue

        statement_lines.append(line)

    statement = "\n".join(statement_lines).strip()
    return statement if statement else raw_text


def problem_file_for_index(directory: str, index: int) -> Optional[str]:
    pattern = re.compile(rf"Problem0*{index}\.org$", re.IGNORECASE)
    for candidate in os.listdir(directory):
        if pattern.search(candidate):
            return os.path.join(directory, candidate)
    return None


def parse_problem_list(line: str) -> List[int]:
    matches = re.findall(r"P(\d{1,2})", line)
    return [int(match) for match in matches]


def extract_training_labels(training_source: str = TRAINING_SOURCE) -> Dict[str, Dict[int, str]]:
    """
    Parse the master document to retrieve labelled topics for 2024 AMC 12A, 2024 AMC 12B, and 2025 AMC 12A.
    """
    with open(training_source, "r", encoding="utf-8") as handle:
        content = handle.read()

    sections = {
        "2025-AMC12A_Problems": r"#### 2025 AMC 12A.*?#### 2024 AMC 12B",
        "2024-AMC12B_Problems": r"#### 2024 AMC 12B.*?#### 2024 AMC 12A",
        "2024-AMC12A_Problems": r"#### 2024 AMC 12A.*?### CRITICAL INSIGHTS",
    }

    labels: Dict[str, Dict[int, str]] = {}

    for folder, pattern in sections.items():
        match = re.search(pattern, content, re.DOTALL)
        if not match:
            print(f"Warning: Could not locate labeled section for {folder} in training source.", file=sys.stderr)
            continue
        section_text = match.group(0)
        topic_map: Dict[int, str] = {}
        for topic in PRIMARY_TOPICS:
            topic_pattern = rf"\*\*{topic} \(\d+\):\*\*(.*?)(?:\n\n|\Z)"
            topic_match = re.search(topic_pattern, section_text, re.DOTALL)
            if not topic_match:
                continue
            problem_numbers = parse_problem_list(topic_match.group(1))
            for number in problem_numbers:
                topic_map[number] = topic
        labels[folder] = topic_map
    return labels


class TopicPredictor:
    def __init__(self):
        self.label_encoder = LabelEncoder()
        self.model: Optional[Pipeline] = None
        self.label_to_index: Optional[Dict[str, int]] = None
        self.train()

    def train(self) -> None:
        training_labels = extract_training_labels(TRAINING_SOURCE)
        texts: List[str] = []
        labels: List[str] = []
        weights: List[float] = []
        manual_keys: set[tuple[str, int]] = set()

        for folder, problem_topics in training_labels.items():
            directory = os.path.join(BASE_DIR, folder)
            if not os.path.isdir(directory):
                continue
            for problem_number, topic in problem_topics.items():
                problem_path = problem_file_for_index(directory, problem_number)
                if not problem_path:
                    continue
                texts.append(load_problem_statement(problem_path))
                labels.append(topic)
                weights.append(1.0)
                manual_keys.add((folder, problem_number))

        PSEUDO_WEIGHT = 0.35
        for folder in TARGET_DIRECTORIES:
            directory = os.path.join(BASE_DIR, folder)
            if not os.path.isdir(directory):
                continue
            for problem_number in range(1, 16):
                key = (folder, problem_number)
                if key in manual_keys:
                    continue
                problem_path = problem_file_for_index(directory, problem_number)
                if not problem_path:
                    continue
                text = load_problem_statement(problem_path)
                lower_text = text.lower()
                geo_score = score_keywords(text, GEOMETRY_KEYWORDS)
                nt_score = score_keywords(text, NUMBER_THEORY_KEYWORDS)
                comb_score = score_keywords(text, COMBINATORICS_KEYWORDS)

                pseudo_label: Optional[str] = None
                if geo_score >= 4:
                    pseudo_label = "Geometry"
                elif nt_score >= 3:
                    pseudo_label = "Number Theory"
                else:
                    combin_indicators = (
                        "probability",
                        "permutation",
                        "permutations",
                        "combination",
                        "combinations",
                        "choose",
                        "subset",
                        "subsets",
                        "sum-free",
                        "sumfree",
                        "statements",
                        "statement",
                        "balls",
                        "bins",
                        "box",
                        "boxes",
                        "distribution",
                    )
                    if comb_score >= 3 or any(term in lower_text for term in combin_indicators):
                        pseudo_label = "Combinatorics"

                if pseudo_label:
                    texts.append(text)
                    labels.append(pseudo_label)
                    weights.append(PSEUDO_WEIGHT)

        if not texts:
            raise RuntimeError("No training data extracted from master document.")

        encoded_labels = self.label_encoder.fit_transform(labels)
        self.model = Pipeline(
            [
                ("tfidf", TfidfVectorizer(stop_words="english", ngram_range=(1, 2))),
                ("clf", LogisticRegression(max_iter=500, multi_class="auto", random_state=42)),
            ]
        )
        if weights:
            self.model.fit(texts, encoded_labels, clf__sample_weight=weights)
        else:
            self.model.fit(texts, encoded_labels)
        self.label_to_index = {
            label: idx for idx, label in enumerate(self.label_encoder.classes_)
        }

    def predict_topic(self, text: str) -> str:
        assert self.model is not None
        assert self.label_to_index is not None
        probs = self.model.predict_proba([text])[0]
        top_index = probs.argmax()
        confidence = probs[top_index]
        topic = self.label_encoder.inverse_transform([top_index])[0]

        lower_text = text.lower()
        geo_score = score_keywords(text, GEOMETRY_KEYWORDS)
        nt_score = score_keywords(text, NUMBER_THEORY_KEYWORDS)
        comb_score = score_keywords(text, COMBINATORICS_KEYWORDS)

        prob_geometry = probs[self.label_to_index.get("Geometry", 0)]
        prob_number_theory = probs[self.label_to_index.get("Number Theory", 0)]
        prob_combinatorics = probs[self.label_to_index.get("Combinatorics", 0)]
        prob_algebra = probs[self.label_to_index.get("Algebra", 0)]

        if geo_score >= 2 and prob_geometry >= prob_algebra + 0.03:
            return "Geometry"
        if nt_score >= 2 and prob_number_theory >= prob_algebra + 0.03:
            return "Number Theory"
        if comb_score >= 2 and prob_combinatorics >= prob_algebra + 0.03:
            return "Combinatorics"

        if "sum-free" in lower_text or "sumfree" in lower_text:
            return "Combinatorics"

        logic_hits = sum(lower_text.count(term) for term in ("statement", "statements", "true", "false"))
        if logic_hits >= 2 and prob_combinatorics >= 0.2:
            return "Combinatorics"

        if prob_combinatorics >= 0.25:
            combin_indicators = ("balls", "bins", "box", "boxes", "urn", "subset", "subsets", "set of", "distribution")
            if any(term in lower_text for term in combin_indicators):
                return "Combinatorics"

        if confidence < 0.45:
            fallback = classify_problem(text)
            if fallback != topic:
                fallback_prob = probs[self.label_to_index.get(fallback, top_index)]
                if fallback_prob + 0.05 >= confidence:
                    topic = fallback
            else:
                topic = fallback
        return topic


def score_keywords(text: str, keywords: Tuple[str, ...]) -> int:
    lower_text = text.lower()
    score = 0
    for keyword in keywords:
        clean_keyword = keyword.strip()
        if not clean_keyword:
            continue
        alphanumeric_only = clean_keyword.replace(" ", "").isalpha()
        if alphanumeric_only:
            pattern = rf"\b{re.escape(clean_keyword)}\b"
            occurrences = len(re.findall(pattern, lower_text))
        else:
            occurrences = lower_text.count(clean_keyword)
        if occurrences:
            score += occurrences
    return score


GEOMETRY_KEYWORDS = (
    "triangle",
    "triangles",
    "circle",
    "circles",
    "vector",
    "vectors",
    "dot product",
    "projection",
    "projections",
    "radius",
    "diameter",
    "square",
    "polygon",
    "angle",
    "perimeter",
    "area",
    "volume",
    "sphere",
    "cube",
    "prism",
    "pyramid",
    "geometry",
    "segment",
    "segments",
    "midpoint",
    "parallel",
    "perpendicular",
    "coordinate",
    "coordinates",
    "distance",
    "circumcircle",
    "inscribed",
    "tangent",
    "arc",
    "sector",
    "quadrilateral",
    "hexagon",
    "pentagon",
    "similar",
    "congruent",
    "altitude",
    "height",
    "ellipse",
    "ellipses",
    "locus",
    "polyhedron",
    "isosceles",
    "right triangle",
    "slope",
)

COMBINATORICS_KEYWORDS = (
    "probability",
    "probabilities",
    "ways",
    "arrangements",
    "permutation",
    "permutations",
    "combination",
    "combinations",
    "choose",
    "random",
    "expected",
    "expectation",
    "distribution",
    "committee",
    "seating",
    "ordering",
    "orderings",
    "selection",
    "counting",
    "pigeonhole",
    "paths",
    "routes",
    "dice",
    "cards",
    "arrange",
    "arranged",
    "arranging",
    "expected value",
    "probability that",
    "binomial",
)

NUMBER_THEORY_KEYWORDS = (
    "integer",
    "integers",
    "divisible",
    "prime",
    "primes",
    "gcd",
    "lcm",
    "mod",
    "modulo",
    "remainder",
    "remainders",
    "digit",
    "digits",
    "congruence",
    "congruent",
    "divisors",
    "divisor",
    "multiple",
    "multiples",
    "perfect square",
    "perfect cube",
    "relatively prime",
    "coprime",
    "greatest common",
    "least common",
    "units digit",
    "tens digit",
    "hundreds digit",
    "modulus",
    "repeating decimal",
    "repeating digits",
    "base-ten",
    "base-10",
    "in base",
)


def classify_problem(text: str) -> str:
    scores = {
        "Geometry": score_keywords(text, GEOMETRY_KEYWORDS),
        "Combinatorics": score_keywords(text, COMBINATORICS_KEYWORDS),
        "Number Theory": score_keywords(text, NUMBER_THEORY_KEYWORDS),
    }
    top_topic, top_score = max(scores.items(), key=lambda item: item[1])
    if top_score == 0:
        return "Algebra"
    return top_topic


def analyze_directory(directory: str, predictor: TopicPredictor) -> dict:
    topic_counts: Counter[str] = Counter()
    per_problem: dict[int, str] = {}
    for problem_number in range(1, 16):
        problem_path = problem_file_for_index(directory, problem_number)
        if not problem_path:
            continue
        text = load_problem_statement(problem_path)
        topic = predictor.predict_topic(text)
        topic_counts[topic] += 1
        per_problem[problem_number] = topic
    return {"counts": dict(topic_counts), "problems": per_problem}


def main() -> None:
    global BASE_DIR, TRAINING_SOURCE

    parser = argparse.ArgumentParser(description="Analyze AMC 12 topic distributions.")
    parser.add_argument(
        "--base-dir",
        default=BASE_DIR,
        help="Base directory containing AMC problem sets (default: training-plans/plan-gen/amc12)",
    )
    parser.add_argument(
        "--training-source",
        default=TRAINING_SOURCE,
        help="Path to the Markdown master document with labelled topics.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional path to write JSON summary output.",
    )
    parser.add_argument(
        "--csv",
        dest="csv_path",
        default=None,
        help="Optional path to write a CSV summary (per exam topic counts and shares).",
    )
    args = parser.parse_args()
    BASE_DIR = args.base_dir
    TRAINING_SOURCE = args.training_source

    predictor = TopicPredictor()
    report: dict[str, dict] = {}
    overall_counts: Counter[str] = Counter()

    for folder in TARGET_DIRECTORIES:
        directory = os.path.join(BASE_DIR, folder)
        if not os.path.isdir(directory):
            print(f"Warning: Directory not found, skipping: {directory}", file=sys.stderr)
            continue
        result = analyze_directory(directory, predictor)
        counts = result["counts"]
        total = sum(counts.values()) or 1
        shares = {topic: counts.get(topic, 0) / total for topic in PRIMARY_TOPICS if topic in counts}
        result["shares"] = shares
        report[folder] = result
        overall_counts.update(result["counts"])

    summary = {
        "per_exam": report,
        "overall_counts": dict(overall_counts),
        "total_problems": sum(overall_counts.values()),
    }

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as handle:
                json.dump(summary, handle, indent=2)
        except OSError as exc:
            print(f"Error: Failed to write JSON output to {args.output}: {exc}", file=sys.stderr)
    else:
        print(json.dumps(summary, indent=2))

    if args.csv_path:
        try:
            with open(args.csv_path, "w", newline="", encoding="utf-8") as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(["exam", "topic", "count", "share", "total"])
                for exam, data in report.items():
                    counts = data.get("counts", {})
                    shares = data.get("shares", {})
                    total = sum(counts.values()) or 1
                    for topic in PRIMARY_TOPICS:
                        count = counts.get(topic, 0)
                        share = shares.get(topic, count / total if total else 0.0)
                        writer.writerow([exam, topic, count, f"{share:.4f}", total])
        except OSError as exc:
            print(f"Error: Failed to write CSV output to {args.csv_path}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
