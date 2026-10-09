"""tools.py - STUDENT IMPLEMENTS.  Source tools for the research agents.   Guide: GUIDE.md, part 1.

Rules for every tool:
  * runs on the HOST (not in the sandbox): API keys must never enter the sandbox;
  * returns a STRING (JSON text of compact records) and NEVER raises:
        "NO RESULTS"  when the source answers with nothing,
        "ERROR: ..."  when the source keeps failing after the retries (the agent then tries another source);
  * the docstring is the tool description the LLM reads: keep it precise (what it does, what it returns, when to use it).
Try your tools without any agent:   python tools.py
"""
import json
import os
import random
import re
import time
import xml.etree.ElementTree

import httpx
from dotenv import load_dotenv
from langchain_core.tools import tool

load_dotenv()

# ---- constants (given) ----
ARXIV_URL = "https://export.arxiv.org/api/query"  # https only: http answers 301
HF_DAILY_URL = "https://huggingface.co/api/daily_papers"
HF_SEARCH_URL = "https://huggingface.co/api/papers/search"
EXA_URL = "https://mcp.exa.ai/mcp"

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_last_arxiv_time = 0.0


class RetryableError(Exception):
    """Given. Raise it inside a call to ask with_retry to wait and try again (retry_after in seconds, optional)."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def _parse_retry_after(header_val):
    if not header_val:
        return None
    try:
        return float(header_val)
    except (ValueError, TypeError):
        return None


def _redact_key(text: str) -> str:
    key = (os.getenv("EXA_API_KEY") or "").strip()
    if key and key in text:
        text = text.replace(key, "[REDACTED]")
    return text


# ---- TODO 1: retry helper ----
def with_retry(fn, *, attempts=5, base=1.0, cap=30.0):
    """Call fn(); when it raises RetryableError or retryable network errors, wait and call it again."""
    last_exc = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            retryable = False
            delay = None

            if isinstance(exc, RetryableError):
                retryable = True
                delay = exc.retry_after
            elif isinstance(exc, httpx.HTTPStatusError):
                if exc.response.status_code in RETRYABLE_STATUS_CODES:
                    retryable = True
                    delay = _parse_retry_after(exc.response.headers.get("Retry-After"))
            elif isinstance(exc, httpx.TransportError):
                retryable = True

            if not retryable:
                raise exc

            last_exc = exc
            if attempt == attempts - 1:
                # Do not sleep after the last attempt, re-raise immediately
                raise exc

            if delay is None:
                jitter = random.uniform(0.0, 1.0)
                delay = base * (2**attempt) + jitter

            delay = min(delay, cap)
            time.sleep(delay)

    if last_exc is not None:
        raise last_exc


# ---- TODO 2: arXiv ----
@tool
def arxiv_search(query: str, max_results: int = 10) -> str:
    """Search arXiv papers by keywords, newest first. Returns a JSON list of {id, url, published, title, summary}."""
    global _last_arxiv_time
    try:
        terms = re.findall(r"[\w\-]+", query)
        if not terms:
            return "NO RESULTS"

        now = time.monotonic()
        elapsed = now - _last_arxiv_time
        if elapsed < 3.0:
            time.sleep(3.0 - elapsed)

        search_query = " AND ".join(f"all:{t}" for t in terms)
        clamped_max = max(1, min(max_results, 30))
        params = {
            "search_query": search_query,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": clamped_max,
        }

        def _fetch():
            headers = {"User-Agent": "ResearchSurvey/1.0 (academic; mailto:survey@example.org)"}
            with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as client:
                resp = client.get(ARXIV_URL, params=params)
                resp.raise_for_status()
                return resp.text

        try:
            xml_text = with_retry(_fetch, attempts=5, base=4.0, cap=60.0)
        finally:
            _last_arxiv_time = time.monotonic()

        root = xml.etree.ElementTree.fromstring(xml_text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", ns)
        if not entries:
            return "NO RESULTS"

        records = []
        for entry in entries:
            raw_id = (entry.findtext("atom:id", "", ns) or "").strip()
            paper_id = raw_id.split("/abs/")[-1] if "/abs/" in raw_id else raw_id
            paper_id = re.sub(r"v\d+$", "", paper_id)
            if not paper_id:
                continue
            url = f"https://arxiv.org/abs/{paper_id}"
            published = (entry.findtext("atom:published", "", ns) or "").strip()[:10]
            raw_title = entry.findtext("atom:title", "", ns) or ""
            title = " ".join(raw_title.split())
            raw_summary = entry.findtext("atom:summary", "", ns) or ""
            summary = " ".join(raw_summary.split())[:600]

            records.append({
                "id": paper_id,
                "url": url,
                "published": published,
                "title": title,
                "summary": summary,
            })

        if not records:
            return "NO RESULTS"

        return json.dumps(records, ensure_ascii=False)
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


# ---- TODO 3: Hugging Face ----
@tool
def hf_daily_papers(limit: int = 30, date: str = "", keyword: str = "") -> str:
    """Hugging Face Daily Papers = what is trending in AI research. Returns a JSON list of
    {id, url, published, title, summary, upvotes, github, stars} sorted by upvotes. `date` is YYYY-MM-DD (empty = latest).
    `keyword` filters title/summary; there is no topic search on this endpoint (use hf_search_papers for a topic)."""
    try:
        clamped_limit = max(1, min(limit, 100))
        params = {"limit": clamped_limit}
        if date.strip():
            params["date"] = date.strip()

        def _fetch():
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                resp = client.get(HF_DAILY_URL, params=params)
                resp.raise_for_status()
                return resp.json()

        data = with_retry(_fetch, attempts=4, base=1.0, cap=30.0)
        if not isinstance(data, list) or not data:
            return "NO RESULTS"

        records = []
        kw = keyword.strip().lower()
        for item in data:
            paper = item.get("paper") if isinstance(item, dict) and isinstance(item.get("paper"), dict) else item
            if not isinstance(paper, dict):
                continue
            paper_id = paper.get("id")
            if not paper_id:
                continue

            title = " ".join(str(paper.get("title") or item.get("title") or "").split())
            raw_summary = paper.get("summary") or item.get("summary") or ""
            summary = " ".join(str(raw_summary).split())[:600]
            published = str(paper.get("publishedAt") or item.get("publishedAt") or "")[:10]
            upvotes = int(paper.get("upvotes") or item.get("upvotes") or 0)
            github = str(paper.get("githubRepo") or item.get("githubRepo") or "")
            stars = int(paper.get("githubStars") or item.get("githubStars") or 0)
            url = f"https://huggingface.co/papers/{paper_id}"

            if kw:
                searchable = f"{title} {summary}".lower()
                if kw not in searchable:
                    continue

            records.append({
                "id": str(paper_id),
                "url": url,
                "published": published,
                "title": title,
                "summary": summary,
                "upvotes": upvotes,
                "github": github,
                "stars": stars,
            })

        if not records:
            return "NO RESULTS"

        records.sort(key=lambda r: r["upvotes"], reverse=True)
        return json.dumps(records, ensure_ascii=False)
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


@tool
def hf_search_papers(query: str, limit: int = 10) -> str:
    """Search Hugging Face papers by topic. Returns a JSON list of
    {id, url, published, title, summary, upvotes, github, stars}."""
    try:
        q = query.strip()
        if not q:
            return "NO RESULTS"
        clamped_limit = max(1, min(limit, 50))
        params = {"q": q, "limit": clamped_limit}

        def _fetch():
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                resp = client.get(HF_SEARCH_URL, params=params)
                resp.raise_for_status()
                return resp.json()

        data = with_retry(_fetch, attempts=4, base=1.0, cap=30.0)
        if not isinstance(data, list) or not data:
            return "NO RESULTS"

        records = []
        for item in data:
            paper = item.get("paper") if isinstance(item, dict) and isinstance(item.get("paper"), dict) else item
            if not isinstance(paper, dict):
                continue
            paper_id = paper.get("id")
            if not paper_id:
                continue

            title = " ".join(str(paper.get("title") or item.get("title") or "").split())
            raw_summary = (
                paper.get("ai_summary")
                or item.get("ai_summary")
                or paper.get("summary")
                or item.get("summary")
                or ""
            )
            summary = " ".join(str(raw_summary).split())[:600]
            published = str(paper.get("publishedAt") or item.get("publishedAt") or "")[:10]
            upvotes = int(paper.get("upvotes") or item.get("upvotes") or 0)
            github = str(paper.get("githubRepo") or item.get("githubRepo") or "")
            stars = int(paper.get("githubStars") or item.get("githubStars") or 0)
            url = f"https://huggingface.co/papers/{paper_id}"

            records.append({
                "id": str(paper_id),
                "url": url,
                "published": published,
                "title": title,
                "summary": summary,
                "upvotes": upvotes,
                "github": github,
                "stars": stars,
            })

        if not records:
            return "NO RESULTS"

        return json.dumps(records, ensure_ascii=False)
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


# ---- TODO 4: web search / fetch through the Exa MCP endpoint ----
def _call_exa_mcp(tool_name: str, arguments: dict) -> str:
    key = (os.getenv("EXA_API_KEY") or "").strip()
    url = f"{EXA_URL}?exaApiKey={key}" if key else EXA_URL
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }

    def _do_post():
        with httpx.Client(timeout=45.0) as client:
            resp = client.post(url, headers=headers, json=payload)
            if resp.status_code in RETRYABLE_STATUS_CODES:
                resp.raise_for_status()
            return resp

    resp = with_retry(_do_post, attempts=5, base=2.0, cap=60.0)

    # Parse SSE text
    data_obj = None
    for line in resp.text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            raw_json = line[5:].strip()
            if raw_json:
                data_obj = json.loads(raw_json)
                break

    if data_obj is None:
        try:
            data_obj = resp.json()
        except Exception:
            raise RuntimeError(f"Exa returned invalid response: {_redact_key(resp.text[:200])}")

    if "error" in data_obj:
        err = data_obj["error"]
        err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        if "rate limit" in err_msg.lower() or "429" in err_msg:
            raise RetryableError(f"Exa rate limited: {_redact_key(err_msg)}", retry_after=20.0)
        raise RuntimeError(f"Exa MCP error: {_redact_key(err_msg)}")

    result = data_obj.get("result", {})
    meta = result.get("_meta", {})
    if (
        meta.get("rate_limited")
        or meta.get("rateLimited")
        or ("rate" in str(meta).lower() and "limit" in str(meta).lower())
    ):
        raise RetryableError("Exa rate limit signaled in _meta", retry_after=20.0)

    content_list = result.get("content", [])
    texts = []
    for item in content_list:
        if isinstance(item, dict) and item.get("type") == "text":
            t = item.get("text", "")
            if "rate limit" in t.lower() and "quota" in t.lower():
                raise RetryableError(f"Exa rate limit in text: {t[:100]}", retry_after=20.0)
            if t:
                texts.append(t)

    return "\n\n".join(texts).strip()


@tool
def web_search(query: str, objective: str = "", num_results: int = 5) -> str:
    """Search the web (Exa). Describe the ideal page in natural language. Returns clean text of the top results with URLs."""
    try:
        q = query.strip()
        if not q:
            return "NO RESULTS"
        obj = objective.strip() if objective.strip() else f"Find research papers and technical surveys related to {q}"
        clamped_num = max(1, min(num_results, 20))
        arguments = {
            "query": q,
            "objective": obj,
            "numResults": clamped_num,
        }
        text = _call_exa_mcp("web_search_exa", arguments)
        if not text:
            return "NO RESULTS"
        return text
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {_redact_key(str(e))}"


@tool
def web_fetch(url: str) -> str:
    """Read the full content of one web page (e.g. an arXiv abstract page) as markdown. Long pages are truncated."""
    try:
        u = url.strip()
        if not u or not (u.startswith("http://") or u.startswith("https://")):
            return "NO RESULTS"
        arguments = {"urls": [u]}
        text = _call_exa_mcp("web_fetch_exa", arguments)
        if not text:
            return "NO RESULTS"
        return text[:12000]
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {_redact_key(str(e))}"


# ---- TODO 5: registry (the researcher subagent gets exactly these) ----
SOURCE_TOOLS = [arxiv_search, hf_daily_papers, hf_search_papers, web_search, web_fetch]


if __name__ == "__main__":
    for name, fn, args in [
        ("arxiv_search", arxiv_search, {"query": "world model", "max_results": 3}),
        ("hf_daily_papers", hf_daily_papers, {"limit": 20}),
        ("hf_search_papers", hf_search_papers, {"query": "world model", "limit": 3}),
        ("web_search", web_search, {"query": "survey paper on world models", "num_results": 2}),
        ("web_fetch", web_fetch, {"url": "https://arxiv.org/abs/1803.10122"}),
    ]:
        try:
            print(f"== {name}\n{fn.invoke(args)[:400]}\n")
        except NotImplementedError as exc:
            print(f"== {name}: not implemented yet ({exc})\n")
