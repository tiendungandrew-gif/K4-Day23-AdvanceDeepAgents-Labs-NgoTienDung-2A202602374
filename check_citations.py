"""check_citations.py - STUDENT IMPLEMENTS `check`.   Runs INSIDE the sandbox (standard library only).

research.py uploads this file to the sandbox and the lead agent runs it with the `execute` tool:
    python3 /tmp/work/research/check_citations.py [report.md] [sources.json]
It must exit 0 and print "OK: ..." when the report is consistent, else print each problem and exit 1.
"""
import json
import sys

REPORT = "/tmp/work/report/report.md"
SOURCES = "/tmp/work/research/sources.json"


import re

_CODE = re.compile(r"(```.*?```|`[^`\n]*`)", re.DOTALL)
_GROUP = re.compile(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\](?!\()")
_REF_HEADING = re.compile(r"(?m)^##[ \t]+References[ \t]*$")


def _group_numbers(group_str):
    numbers = []
    for part in re.split(r"\s*,\s*", group_str):
        span = re.fullmatch(r"(\d+)\s*[–-]\s*(\d+)", part)
        if span:
            a, b = int(span.group(1)), int(span.group(2))
            if 0 <= b - a <= 200:
                numbers.extend(range(a, b + 1))
            else:
                numbers.extend([a, b])
        elif part.isdigit():
            numbers.append(int(part))
    return numbers


def check(report_text, sources):
    """Return a list of problem strings (empty list = OK)."""
    problems = []

    if not isinstance(sources, list) or len(sources) == 0:
        return ["no sources in sources.json"]

    seen_urls = {}
    source_by_n = {}

    for idx, s in enumerate(sources):
        if not isinstance(s, dict):
            problems.append(f"source entry at index {idx} is not a JSON object")
            continue
        n = s.get("n")
        if not isinstance(n, int) or isinstance(n, bool):
            problems.append(f"source entry at index {idx} has non-integer n: {n!r}")
        elif n in source_by_n:
            problems.append(f"duplicate source number n={n}")
        else:
            source_by_n[n] = s

        url = s.get("url")
        if not isinstance(url, str) or not (url.startswith("http://") or url.startswith("https://")):
            problems.append(f"source [{n if n is not None else idx}] URL must start with http:// or https://, got: {url!r}")
        else:
            if url in seen_urls:
                problems.append(f"duplicate URL in sources: {url!r} (sources [{seen_urls[url]}] and [{n}])")
            else:
                seen_urls[url] = n

    matches = list(_REF_HEADING.finditer(report_text))
    if not matches:
        problems.append("missing heading '## References'")
        body = report_text
        ref_section = ""
    else:
        last_match = matches[-1]
        body = report_text[:last_match.start()]
        ref_section = report_text[last_match.end():]

    # Find citations in the body only (exclude code blocks and markdown links)
    clean_body = _CODE.sub("", body)
    cited = set()
    for match in _GROUP.finditer(clean_body):
        for num in _group_numbers(match.group(1)):
            cited.add(num)

    for n in sorted(cited):
        if n not in source_by_n:
            problems.append(f"[{n}] cited in body but missing from sources.json")

    for n in sorted(source_by_n.keys()):
        if n not in cited:
            problems.append(f"source [{n}] never cited in body")

    ref_lines = [line.strip() for line in ref_section.splitlines() if line.strip()]
    seen_ref_n = set()

    for line in ref_lines:
        line_match = re.match(r"^\[(\d+)\](?:\s+(.*))?$", line)
        if not line_match:
            problems.append(f"References section line does not start with [n]: {line[:80]!r}")
            continue

        ref_n = int(line_match.group(1))
        if ref_n in seen_ref_n:
            problems.append(f"reference line [{ref_n}] appears more than once")
        seen_ref_n.add(ref_n)

        if ref_n not in source_by_n:
            problems.append(f"reference line [{ref_n}] is not in sources.json")

        urls_in_line = [u.rstrip(".,;)>") for u in re.findall(r"https?://[^\s)\]]+", line)]
        if len(urls_in_line) == 0:
            problems.append(f"reference line [{ref_n}] contains no URL")
        elif len(urls_in_line) > 1:
            problems.append(f"reference line [{ref_n}] bundles multiple URLs: {urls_in_line}")
        else:
            line_url = urls_in_line[0]
            if ref_n in source_by_n:
                expected_url = source_by_n[ref_n].get("url")
                if line_url != expected_url:
                    problems.append(
                        f"reference line [{ref_n}] URL {line_url!r} does not match sources.json URL {expected_url!r}"
                    )

    for n in sorted(source_by_n.keys()):
        if n not in seen_ref_n:
            problems.append(f"missing reference line for source [{n}]")

    return problems


def main(argv):
    report_path = argv[1] if len(argv) > 1 else REPORT
    sources_path = argv[2] if len(argv) > 2 else SOURCES
    try:
        with open(report_path, encoding="utf-8") as f:
            report = f.read()
        with open(sources_path, encoding="utf-8") as f:
            sources = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"cannot read inputs: {exc}")
        return 1
    problems = check(report, sources)
    if problems:
        print("\n".join(problems))
        return 1
    print(f"OK: {len(sources)} sources, all citations resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
