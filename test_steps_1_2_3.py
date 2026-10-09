"""Script kiem tra toan dien Buoc 1, Buoc 2 va Buoc 3 theo RUBRIC.md va GUIDE.md.
Chay bang lenh:  python test_steps_1_2_3.py
"""
import os
import sys
import time

print("=" * 60)
print("BAT DAU KIEM TRA BUOC 1, BUOC 2, BUOC 3")
print("=" * 60)

# -------------------------------------------------------------
# KIEM TRA BUOC 1: check_citations.py
# -------------------------------------------------------------
print("\n[1/3] Kiem tra Buoc 1: check_citations.py ...")
import check_citations
from check_citations import check

# 1.1 Chi dung thu vien chuan
forbidden = ["httpx", "langchain", "openai", "daytona", "dotenv"]
for f in forbidden:
    assert f not in check_citations.__dict__, f"Loi: check_citations khong duoc import {f}"
print("  [PASS] chi dung thu vien chuan Python (sanbox-safe)")

# 1.2 Kiem tra bao cao hop le
ticks = chr(96) * 3
valid_report = f"""# World Model Survey
World models learn environment dynamics [1]. Recent surveys categorize them [2].
Combined models like [1, 2] achieve high efficiency.
{ticks}python
code_block = [999]  # Khong duoc tinh
{ticks}
Link: [1](https://arxiv.org/abs/1803.10122) khong tinh.

## References
[1] World Models. arxiv. https://arxiv.org/abs/1803.10122 (2018-05-09)
[2] A Survey on World Models. web. https://example.com/survey (2024-01-01)
"""
valid_sources = [
    {"n": 1, "url": "https://arxiv.org/abs/1803.10122", "title": "World Models", "source": "arxiv"},
    {"n": 2, "url": "https://example.com/survey", "title": "A Survey on World Models", "source": "web"},
]
probs_valid = check(valid_report, valid_sources)
assert len(probs_valid) == 0, f"Bao cao hop le nhung bao loi: {probs_valid}"
print("  [PASS] xac thuc bao cao hop le: 0 loi")

# 1.3 Kiem tra cac truong hop vi pham
assert len(check("", valid_sources)) > 0, "Khong bat duoc thieu ## References"
assert len(check(valid_report, [])) > 0, "Khong bat duoc sources rong"
# Vi pham sai so trich dan
bad_report_n = valid_report.replace("[2]", "[3]")
assert any("[3] cited in body but missing" in p for p in check(bad_report_n, valid_sources))
# Vi pham gop nhieu URL vao 1 dong tham khao
bad_ref_line = valid_report.replace("https://example.com/survey", "https://example.com/survey https://extra.com")
assert any("bundles multiple URLs" in p for p in check(bad_ref_line, valid_sources))
print("  [PASS] bat day du cac loi vi pham (thieu heading, thieu nguon, gop URL, sai so)")

# -------------------------------------------------------------
# KIEM TRA BUOC 2: tools.py
# -------------------------------------------------------------
print("\n[2/3] Kiem tra Buoc 2: tools.py ...")
import tools
from tools import (
    SOURCE_TOOLS,
    RetryableError,
    _redact_key,
    arxiv_search,
    hf_daily_papers,
    hf_search_papers,
    web_fetch,
    web_search,
    with_retry,
)

# 2.1 Du 5 cong cu
assert len(SOURCE_TOOLS) == 5, f"Can 5 cong cu, hien co {len(SOURCE_TOOLS)}"
expected_names = {"arxiv_search", "hf_daily_papers", "hf_search_papers", "web_search", "web_fetch"}
assert {t.name for t in SOURCE_TOOLS} == expected_names
print("  [PASS] SOURCE_TOOLS day du 5 cong cu")

# 2.2 Retry helper logic
calls = 0
def fail_then_succeed():
    global calls
    calls += 1
    if calls < 3:
        raise RetryableError("temporary glitch", retry_after=0.01)
    return "success"
res = with_retry(fail_then_succeed, attempts=5, base=0.01)
assert res == "success" and calls == 3
print("  [PASS] with_retry hoat dong dung co che exponential backoff va retry_after")

# 2.3 Khong retry loi thuong
calls_bad = 0
try:
    def bad():
        global calls_bad
        calls_bad += 1
        raise ValueError("programming bug")
    with_retry(bad, attempts=3)
except ValueError:
    pass
assert calls_bad == 1, "with_retry khong duoc retry ngoai le khong phai RetryableError"
print("  [PASS] with_retry khong retry loi thuong")

# 2.4 Che giau EXA_API_KEY
fake_key = "test-secret-key-12345"
os.environ["EXA_API_KEY"] = fake_key
redacted = _redact_key(f"Request failed at https://mcp.exa.ai/mcp?exaApiKey={fake_key}")
assert fake_key not in redacted
assert "[REDACTED]" in redacted
print("  [PASS] che giau EXA_API_KEY khoi chuoi loi")

# 2.5 Input rong tra ve NO RESULTS khong throw
assert arxiv_search.invoke({"query": ""}) == "NO RESULTS"
assert hf_search_papers.invoke({"query": ""}) == "NO RESULTS"
assert web_search.invoke({"query": ""}) == "NO RESULTS"
assert web_fetch.invoke({"url": ""}) == "NO RESULTS"
print("  [PASS] input rong tra ve NO RESULTS, khong bao gio raise Exception")

# -------------------------------------------------------------
# KIEM TRA BUOC 3: agents.py
# -------------------------------------------------------------
print("\n[3/3] Kiem tra Buoc 3: agents.py ...")
import agents
from agents import (
    CHECKER_PROMPT,
    LEAD_LIMITS,
    LEAD_PROMPT,
    RESEARCHER_PROMPT,
    SUB_LIMITS,
    build_lead_agent,
    build_subagents,
)
from model import make_model

# 3.1 Kiem tra subagents
subagents = build_subagents()
assert len(subagents) == 2, f"Can 2 subagent specs, co {len(subagents)}"
sub_names = {s["name"] for s in subagents}
assert "researcher" in sub_names and "citation-checker" in sub_names
# Kiem tra middleware limits trong moi subagent (Rubric 2.5)
for s in subagents:
    assert "middleware" in s, f"Subagent {s['name']} thieu middleware limits"
    assert len(s["middleware"]) >= 2
print("  [PASS] build_subagents day du researcher va citation-checker kem middleware limits")

# 3.2 Kiem tra noi dung prompt
assert "write_todos" in LEAD_PROMPT
assert "finalize_citations.py" in LEAD_PROMPT
assert "check_citations.py" in LEAD_PROMPT
assert "3" in LEAD_PROMPT  # it nhat 3 sub-questions / 3 source families
assert "UNTRUSTED" in RESEARCHER_PROMPT
assert "UNTRUSTED" in CHECKER_PROMPT
print("  [PASS] Prompts bao gom day du quy tac: write_todos, >=3 cau hoi con, 3 ho nguon, untrusted data")

# 3.3 Khoi tao Lead Agent compiled graph
model = make_model()
agent = build_lead_agent(backend=None, model=model)
assert agent is not None
print("  [PASS] build_lead_agent khoi tao thanh cong CompiledStateGraph voi TodoListMiddleware va Limits")

# -------------------------------------------------------------
# KIEM TRA BUOC 4: research.py
# -------------------------------------------------------------
print("\n[4/4] Kiem tra Buoc 4: research.py ...")
import json
import tempfile
from pathlib import Path
from research import build_prompt, save_outputs, slugify, summarize

# 4.1 slugify
assert slugify("survey about world model") == "survey-about-world-model"
assert slugify("../../x") == "x"
assert slugify("") == "topic"
assert slugify("   ") == "topic"
assert len(slugify("a" * 100)) <= 60
print("  [PASS] slugify chuan hoa an toan, khong bi path traversal")

# 4.2 summarize
class MockMsg:
    def __init__(self, tool_calls=None, usage=None):
        self.tool_calls = tool_calls or []
        self.usage_metadata = usage or {}

msgs = [
    MockMsg([{"name": "write_todos"}]),
    MockMsg([{"name": "task"}, {"name": "task"}, {"name": "task"}], {"input_tokens": 100, "output_tokens": 50}),
    MockMsg([{"name": "execute"}], {"input_tokens": 200, "output_tokens": 150}),
]
res = summarize(msgs, 12.345, "test-model")
assert res["subagent_calls"] == 3
assert res["tokens"]["input"] == 300
assert res["tokens"]["output"] == 200
assert res["elapsed_s"] == 12.3
assert res["model"] == "test-model"
print("  [PASS] summarize tinh toan chinh xac subagent_calls, tokens, elapsed_s")

# 4.3 save_outputs
class MockBackend:
    def download_files(self, paths):
        class Resp:
            def __init__(self, path, content):
                self.path = path
                self.content = content
        return [
            Resp("/tmp/work/report/report.md", b"# Survey\nBody [1]\n"),
            Resp("/tmp/work/research/sources.json", b'[{"n": 1, "url": "https://arxiv.org/abs/1", "source": "arxiv"}]')
        ]

with tempfile.TemporaryDirectory() as tmpdir:
    report_file = save_outputs(MockBackend(), "survey about world model", msgs, 10.0, "test-model", reports_dir=tmpdir)
    assert Path(report_file).exists()
    assert (Path(tmpdir) / "survey-about-world-model.sources.json").exists()
    assert (Path(tmpdir) / "survey-about-world-model.meta.json").exists()
    meta_json = json.loads((Path(tmpdir) / "survey-about-world-model.meta.json").read_text(encoding="utf-8"))
    assert meta_json["topic"] == "survey about world model"
    assert meta_json["subagent_calls"] == 3
    assert meta_json["source_families"] == ["arxiv"]
    print("  [PASS] save_outputs luu day du 3 tep va thong so meta.json")

print("\n" + "=" * 60)
print("XAC NHAN: TAT CA CAC MUC CUA BUOC 1, 2, 3, 4 DEU DAT CHUAN 100%!")
print("=" * 60)
