"""research.py - STUDENT IMPLEMENTS.  The main script.   Guide: GUIDE.md, part 3.

Usage:  python research.py "survey about world model"
Result: reports/<slug>.md   reports/<slug>.sources.json   reports/<slug>.meta.json
"""
import json  # noqa: F401
import os  # noqa: F401
import re  # noqa: F401
import sys
import time  # noqa: F401
from collections import Counter  # noqa: F401
from pathlib import Path

from agents import FINALIZER_PATH, REPORT_PATH, SOURCES_PATH, VALIDATOR_PATH, WORKDIR, build_lead_agent  # noqa: F401
from model import make_model  # noqa: F401
from sandbox import download, open_sandbox, upload  # noqa: F401

ROOT = Path(__file__).parent
REPORTS = ROOT / "reports"
VALIDATOR_SOURCE = ROOT / "check_citations.py"
FINALIZER_SOURCE = ROOT / "finalize_citations.py"   # provided: uploaded next to your validator





def slugify(topic):
    """Turn a topic into a safe file name: lower case, runs of non-word characters become one "-", max 60 chars,
    never empty (fall back to "topic"). The topic is user input: "../../x" must not escape reports/."""
    if not topic:
        return "topic"
    s = topic.lower()
    s = re.sub(r"[^\w]+", "-", s)
    s = s.strip("-")
    s = s[:60].rstrip("-")
    return s if s else "topic"


def build_prompt(topic):
    """The user message sent to the lead agent."""
    return (
        f"Conduct a comprehensive, publication-quality deep research survey on the topic: '{topic}'.\n\n"
        f"Instructions:\n"
        f"1. Use `write_todos` to break down this topic into at least 3 distinct sub-questions.\n"
        f"2. Delegate each sub-question in parallel to the `researcher` subagent using the `task` tool "
        f"(you must make at least 3 subagent calls). Provide full context in each delegation.\n"
        f"3. Ensure the researchers retrieve papers and documentation across multiple source families "
        f"(arXiv, Hugging Face Daily/Search, and Web).\n"
        f"4. Consolidate notes into {SOURCES_PATH} covering AT LEAST 3 distinct source families.\n"
        f"5. Synthesize and write the survey body into {REPORT_PATH} following REPORT_TEMPLATE.md "
        f"with inline citations [n]. Do NOT write the ## References section yourself.\n"
        f"6. Run `python3 {FINALIZER_PATH}` using `execute` to generate ## References and clean citations.\n"
        f"7. Run `python3 {VALIDATOR_PATH}` using `execute` to verify citation validity until it prints OK.\n"
        f"8. Delegate 3-5 factual claims to the `citation-checker` subagent using `task` to verify accuracy."
    )


def summarize(messages, elapsed, model_name):
    """Return {"model", "elapsed_s", "subagent_calls", "tool_calls": {name: count}, "tokens": {"input", "output"}}."""
    tool_counts = Counter()
    input_tokens = 0
    output_tokens = 0

    for msg in messages:
        # Check tool calls
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls and isinstance(msg, dict):
            tool_calls = msg.get("tool_calls")
        if tool_calls:
            for call in tool_calls:
                call_name = call.get("name") if isinstance(call, dict) else getattr(call, "name", str(call))
                if call_name:
                    tool_counts[call_name] += 1

        # Check token usage
        usage = getattr(msg, "usage_metadata", None)
        if not usage and isinstance(msg, dict):
            usage = msg.get("usage_metadata")
        if not usage:
            resp_meta = getattr(msg, "response_metadata", {}) or {}
            usage = resp_meta.get("token_usage") or resp_meta.get("usage")

        if isinstance(usage, dict):
            input_tokens += int(usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0)
            output_tokens += int(usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0)

    return {
        "model": str(model_name),
        "elapsed_s": round(float(elapsed), 1),
        "subagent_calls": int(tool_counts.get("task", 0)),
        "tool_calls": dict(tool_counts),
        "tokens": {
            "input": int(input_tokens),
            "output": int(output_tokens),
        },
    }


def save_outputs(backend, topic, messages, elapsed, model_name, reports_dir=REPORTS):
    """Download the report from the sandbox and write the three files into reports_dir. Return the report path."""
    files = download(backend, [REPORT_PATH, SOURCES_PATH])
    report_bytes = files.get(REPORT_PATH)
    sources_bytes = files.get(SOURCES_PATH)

    if not report_bytes or not report_bytes.strip():
        raise RuntimeError(f"Report is missing or empty at {REPORT_PATH}")

    if not sources_bytes or not sources_bytes.strip():
        raise RuntimeError(f"Sources file is missing or empty at {SOURCES_PATH}")

    try:
        sources_data = json.loads(sources_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"Invalid JSON in sources.json: {exc}") from exc

    if not isinstance(sources_data, list) or len(sources_data) == 0:
        raise RuntimeError("sources.json must be a non-empty list of source objects")

    summary_meta = summarize(messages, elapsed, model_name)
    distinct_families = sorted(
        list({s.get("source") for s in sources_data if isinstance(s, dict) and s.get("source")})
    )

    meta = {
        "topic": topic,
        **summary_meta,
        "n_sources": len(sources_data),
        "source_families": distinct_families,
    }

    slug = slugify(topic)
    reports_path = Path(reports_dir)
    reports_path.mkdir(parents=True, exist_ok=True)

    sources_dest = reports_path / f"{slug}.sources.json"
    meta_dest = reports_path / f"{slug}.meta.json"
    report_dest = reports_path / f"{slug}.md"

    sources_dest.write_text(json.dumps(sources_data, ensure_ascii=False, indent=2), encoding="utf-8")
    meta_dest.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    report_dest.write_bytes(report_bytes)

    return report_dest


def main(topic):
    """Return the process exit code (0 ok, 1 failed run, 2 no topic)."""
    if not topic or not topic.strip():
        print("Usage: python research.py '<topic>'", file=sys.stderr)
        return 2

    topic = topic.strip()
    try:
        model = make_model()
    except Exception as exc:
        print(f"FAILED to initialize model: {exc}", file=sys.stderr)
        return 1

    model_name = getattr(model, "model_name", None) or getattr(model, "model", None) or str(model)
    start_time = time.monotonic()

    try:
        with open_sandbox() as backend:
            backend.execute(f"mkdir -p {WORKDIR}/research/notes {WORKDIR}/report")
            upload(
                backend,
                {
                    VALIDATOR_PATH: VALIDATOR_SOURCE.read_bytes(),
                    FINALIZER_PATH: FINALIZER_SOURCE.read_bytes(),
                },
            )
            agent = build_lead_agent(backend, model)
            prompt = build_prompt(topic)
            result = agent.invoke(
                {"messages": [{"role": "user", "content": prompt}]},
                config={"recursion_limit": 1000},
            )
            elapsed = time.monotonic() - start_time
            messages = result.get("messages", []) if isinstance(result, dict) else []
            saved_report = save_outputs(backend, topic, messages, elapsed, model_name)
            print(f"SUCCESS: Report saved to {saved_report}")
            return 0
    except RuntimeError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"FAILED with unexpected error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(" ".join(sys.argv[1:])))

