"""
research.py — NOVA's research mode.

Pipeline (each step reports progress to core.state so the blue UI can
show it live):

    1. plan          — normalise the topic
    2. wikipedia     — REST summary of the best-matching article
    3. related       — follow the article's own "related pages" links
    4. web           — DuckDuckGo lite: other sites writing about it
    5. summary       — the local brain (Ollama/Qwen) composes an executive
                       summary; falls back to an extractive one offline
    6. pdf           — write a structured PDF to the Desktop and open it

Every fetch degrades gracefully: research proceeds with whatever
succeeded, and only total failure produces a persona-flavoured error.
The LLM is used for synthesis ONLY — fetching, structuring and PDF
writing are all deterministic local code.
"""

import os
import re
import threading
import time
import urllib.parse

import requests

import config
from core.state import state

_UA = {"User-Agent": "Mozilla/5.0 (NOVA research assistant)"}
_FAIL_PREFIXES = ("The web page didn't", "Web extraction", "That page gave")
_RESEARCH_LOCK = threading.Lock()          # one research at a time
_thread = None                             # the running worker, if any


# ----------------------------------------------------------------- desktop ----
def desktop_dir() -> str:
    """The user's Desktop — OneDrive-redirected folders handled."""
    home = os.path.expanduser("~")
    for candidate in (
        os.path.join(home, "OneDrive", "Desktop"),
        os.path.join(home, "Desktop"),
        os.path.join(home, "OneDrive", "桌面"),   # zh-CN Windows
        os.path.join(home, "OneDrive", "Bureau"),  # fr-FR
    ):
        if os.path.isdir(candidate):
            return candidate
    return home  # last resort: the profile folder, still findable


# ------------------------------------------------------------- clean topic ----
def clean_topic(text: str) -> str:
    """'research quantum computing for me please' -> 'quantum computing'."""
    t = (text or "").strip().strip(".?! ")
    t = re.sub(
        r"^(?:please\s+|nova[,\s]+|(?:can|could)\s+you\s+)*"
        r"(?:do|prepare|make|create|write|compile|put\s+together|start|begin)?\s*"
        r"(?:a\s+|an\s+|some\s+)?(?:quick\s+|brief\s+|detailed\s+|small\s+)?"
        r"research\s+(?:on|about|for|into|regarding)?\s*",
        "", t, flags=re.I,
    )
    t = re.sub(r"\s+(?:for me|please|now|thanks|thank you)\b.*$", "", t, flags=re.I)
    return t.strip(" .?!") or "general knowledge"


def safe_filename(topic: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", topic.lower()).strip("_")[:48] or "research"


# ----------------------------------------------------------------- fetchers ----
def _get(url: str, timeout: int = 10) -> str | None:
    """GET a page, returning extracted readable text or None on failure."""
    try:
        r = requests.get(url, timeout=timeout, headers=_UA)
        if r.status_code != 200:
            return None
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
        return text[:1500] if text else None
    except Exception:
        return None


def fetch_wikipedia_summary(topic: str):
    """(title, extract, related_titles) or None."""
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query", "list": "search", "srsearch": topic,
                "srlimit": 1, "format": "json",
            },
            timeout=8, headers=_UA,
        )
        hits = r.json().get("query", {}).get("search", [])
        if not hits:
            return None
        title = hits[0]["title"]
        s = requests.get(
            "https://en.wikipedia.org/api/rest_v1/page/summary/"
            + urllib.parse.quote(title),
            timeout=8, headers=_UA,
        )
        if s.status_code != 200:
            return None
        data = s.json()
        extract = data.get("extract") or ""
        related = [p["title"] for p in data.get("related", [])][:4]
        return title, extract, related
    except Exception:
        return None


def fetch_related_extracts(titles: list[str]) -> list[tuple[str, str]]:
    """Batch-fetch short extracts for related article titles."""
    if not titles:
        return []
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query", "prop": "extracts", "explaintext": 1,
                "exintro": 1, "format": "json", "titles": "|".join(titles),
            },
            timeout=10, headers=_UA,
        )
        pages = r.json().get("query", {}).get("pages", {})
        out = []
        for p in pages.values():
            ex = (p.get("extract") or "").strip()
            if ex:
                out.append((p.get("title", "related"), ex[:900]))
        return out
    except Exception:
        return []


def fetch_web_sources(topic: str, limit: int = 3) -> list[tuple[str, str, str]]:
    """
    (title, url, snippet) triples. DuckDuckGo first (it 202-challenges
    some networks); when it yields nothing, the encyclopedia's own search
    hits step in — real articles with real URLs, never empty.
    """
    out = _ddg_sources(topic, limit)
    if out:
        return out
    return _wiki_search_sources(topic, limit)


def _ddg_sources(topic: str, limit: int) -> list[tuple[str, str, str]]:
    """DuckDuckGo HTML results, unwrapped. Best-effort; may be empty."""
    out = []
    for endpoint in ("https://html.duckduckgo.com/html/",
                     "https://lite.duckduckgo.com/lite/"):
        try:
            r = requests.get(endpoint, params={"q": topic},
                             timeout=10, headers=_UA)
            if r.status_code != 200:
                continue
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(r.text, "html.parser")
            anchors = soup.select("a.result__a") or soup.select("a.result-link")
            for a in anchors[:limit * 2]:
                title = a.get_text(strip=True)
                url = a.get("href", "")
                if "uddg=" in url:  # ddg wraps links: /l/?uddg=<encoded>
                    m = re.search(r"uddg=([^&]+)", url)
                    if m:
                        url = urllib.parse.unquote(m.group(1))
                if not title or not url.startswith("http"):
                    continue
                out.append((title, url, ""))
                if len(out) >= limit:
                    return out
        except Exception:
            continue
    return out


def _wiki_search_sources(topic: str, limit: int) -> list[tuple[str, str, str]]:
    """Top encyclopedia search hits as (title, url, intro-snippet)."""
    try:
        r = requests.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query", "list": "search", "srsearch": topic,
                "srlimit": limit + 1, "format": "json",
            },
            timeout=8, headers=_UA,
        )
        hits = r.json().get("query", {}).get("search", [])
        out = []
        for h in hits[:limit + 1]:
            title = h.get("title", "")
            if not title:
                continue
            url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_"))
            snippet = re.sub(r"<[^>]+>", "", h.get("snippet", ""))
            out.append((title, url, snippet[:400]))
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


# ------------------------------------------------------------------ summary ----
def compose_summary(topic: str, sections: list[tuple[str, str]]) -> str:
    """
    The local brain condenses the gathered material into a tight executive
    summary. Offline / busy -> extractive fallback (first sentences of the
    richest sections). Returns summary text (possibly empty).
    """
    material = "\n\n".join(f"[{t}]\n{b}" for t, b in sections if b)
    if not material:
        return ""
    try:
        import json as _json

        resp = requests.post(
            f"{config.OLLAMA_URL}/api/generate",
            json={
                "model": config.OLLAMA_MODELS[0],
                "prompt": (
                    "You are NOVA, a precise research assistant. Using ONLY the "
                    "material below, write a 5-7 sentence executive summary of "
                    "the topic. No preamble, no bullet points, plain sentences.\n\n"
                    f"TOPIC: {topic}\n\nMATERIAL:\n{material[:6000]}"
                ),
                "stream": False,
                "keep_alive": "10m",
                "options": {"temperature": 0.4, "num_predict": 220},
            },
            timeout=90,
        )
        if resp.status_code == 200:
            text = (resp.json().get("response") or "").strip()
            if text:
                return text
    except Exception:
        pass
    # extractive fallback: first 2 sentences of each section, capped
    parts = []
    for title, body in sections:
        if not body:
            continue
        sentences = re.split(r"(?<=[.!?])\s+", body)
        parts.append(" ".join(sentences[:2]))
        if len(" ".join(parts)) > 900:
            break
    return "\n\n".join(parts)


# ---------------------------------------------------------------------- pdf ----
def _pdf_safe(text: str) -> str:
    """fpdf2 core fonts are latin-1 — map the common unicode stragglers."""
    text = (text or "").replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2014", "-").replace("\u2013", "-")
    text = text.replace("\u2026", "...").replace("\u2022", "-")
    text = text.replace("\u00a0", " ")
    return text.encode("latin-1", "replace").decode("latin-1")


def write_pdf(topic: str, summary: str, sections: list[tuple[str, str]],
              sources: list[tuple[str, str, str]]) -> str:
    """Write the structured PDF to the Desktop. Returns the path."""
    from fpdf import FPDF

    path = os.path.join(desktop_dir(), f"Research_{safe_filename(topic)}_{time.strftime('%Y%m%d')}.pdf")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=18)

    # ---- title page ----
    pdf.add_page()
    pdf.set_fill_color(12, 32, 68)          # research blue
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 26)
    pdf.cell(0, 16, _pdf_safe("NOVA RESEARCH"), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "", 15)
    pdf.multi_cell(0, 9, _pdf_safe(topic), align="C")
    pdf.set_font("Helvetica", "I", 10)
    pdf.cell(0, 8, _pdf_safe("compiled " + time.strftime("%d %B %Y, %H:%M")),
             new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(6)
    pdf.set_draw_color(70, 130, 220)
    pdf.set_line_width(0.6)
    pdf.line(30, pdf.get_y(), 180, pdf.get_y())

    # ---- body ----
    pdf.set_text_color(20, 20, 20)
    pdf.ln(8)

    def heading(txt):
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_text_color(12, 32, 68)
        pdf.cell(0, 9, _pdf_safe(txt), new_x="LMARGIN", new_y="NEXT")
        pdf.set_draw_color(70, 130, 220)
        pdf.set_line_width(0.3)
        pdf.line(pdf.l_margin, pdf.get_y(), 180, pdf.get_y())
        pdf.ln(2.5)
        pdf.set_text_color(20, 20, 20)

    def para(txt, size=11, lh=6):
        pdf.set_font("Helvetica", "", size)
        pdf.multi_cell(0, lh, _pdf_safe(txt))
        pdf.ln(1.5)

    heading("Executive Summary")
    if summary:
        para(summary)
    else:
        para("Insufficient material was gathered for a composed summary; "
             "the source sections below contain everything that was found.")

    for title, body in sections:
        heading(title)
        para(body)

    if sources:
        heading("Sources")
        for i, (t, u, _s) in enumerate(sources, 1):
            para(f"{i}. {t} - {u}", size=9.5, lh=5)

    pdf.output(path)
    return path


def open_file(path: str):
    try:
        os.startfile(path)  # Windows
    except Exception:
        pass


# ----------------------------------------------------------------- pipeline ----
def _set(step: str, pct: int, detail: str = ""):
    state.set_research(step=step, pct=pct, detail=detail)


def start_research(topic_raw: str) -> dict:
    """
    Kick off a research run in a background thread. Returns the status
    dict immediately; results land in state.research() and the PDF on the
    Desktop. A run already in progress is not interrupted.
    """
    global _thread
    topic = clean_topic(topic_raw)
    with _RESEARCH_LOCK:
        running = _thread is not None and _thread.is_alive()
    if running:
        cur = state.research()
        return {**cur, "started": False,
                "detail": "A research run is already in progress, sir."}
    state.set_research(topic=topic, step="plan", pct=2, running=True,
                       detail="Preparing the topic...", sources=[], done=False,
                       error=None, pdf_path=None)
    _thread = threading.Thread(target=_run, args=(topic,), daemon=True)
    _thread.start()
    return {**state.research(), "started": True}


def _run(topic: str):
    started = time.time()
    try:
        # 1 -------------------------------------------------------- plan ----
        _set("plan", 5, f"Topic: {topic}")
        sections: list[tuple[str, str]] = []

        # 2 -------------------------------------------------- wikipedia ----
        _set("wikipedia", 15, "Querying the encyclopedia...")
        main_title, main_extract, related = (None, "", [])
        hit = fetch_wikipedia_summary(topic)
        if hit:
            main_title, main_extract, related = hit
            if main_extract:
                sections.append((f"Overview — {main_title}", main_extract))

        # 3 ---------------------------------------------------- related ----
        _set("related", 35, "Following related articles...")
        for title, extract in fetch_related_extracts(related)[:3]:
            sections.append((f"Related — {title}", extract))

        # 4 ------------------------------------------------------- web ----
        _set("web", 55, "Scanning other sources...")
        sources = fetch_web_sources(topic, limit=3)
        # source articles double as material: pull their intro extracts so
        # the summary has more than one article to work from
        wiki_urls = [u for _t, u, _s in sources if "wikipedia.org/wiki/" in u]
        titles = [urllib.parse.unquote(u.rsplit("/", 1)[-1]).replace("_", " ") for u in wiki_urls]
        related_titles = [t for t in titles if t.lower() != (main_title or "").lower()]
        if related_titles:
            for title, extract in fetch_related_extracts(related_titles)[:3]:
                sections.append((f"Related — {title}", extract))
        for title, url, snippet in sources:
            sections.append((f"Web — {title}", snippet or url))
        state.set_research(sources=[{"title": t, "url": u} for t, u, _ in sources])

        if not sections:
            state.set_research(step="error", pct=100, running=False, done=True,
                               error=("I found nothing readable on that topic, "
                                      "sir. The connection may be down."))
            return

        # 5 -------------------------------------------------- summary ----
        _set("summary", 72, "The brain is composing the summary...")
        summary = compose_summary(topic, sections)

        # 6 ------------------------------------------------------ pdf ----
        _set("pdf", 90, "Writing the PDF to your desktop...")
        try:
            path = write_pdf(topic, summary, sections, sources)
        except Exception as e:
            state.set_research(step="error", pct=100, running=False, done=True,
                               error=f"The PDF didn't save, sir: {e}")
            return
        open_file(path)
        elapsed = int(time.time() - started)
        state.set_research(step="done", pct=100, running=False, done=True,
                           pdf_path=path,
                           detail=f"Saved to your desktop in {elapsed}s")
        state.add_announcement(
            f"Research on {topic} is complete — the PDF is on your desktop, sir."
        )
    except Exception as e:  # never leave the UI hanging
        state.set_research(step="error", pct=100, running=False, done=True,
                           error=f"Research hit a snag, sir: {e}")


def run_blocking(topic_raw: str) -> str:
    """Same pipeline, spoken-reply flavour — for the normal chat UI."""
    topic = clean_topic(topic_raw)
    _set("plan", 5, f"Topic: {topic}")
    sections = []
    hit = fetch_wikipedia_summary(topic)
    if hit:
        main_title, main_extract, related = hit
        if main_extract:
            sections.append((f"Overview — {main_title}", main_extract))
        for title, extract in fetch_related_extracts(related)[:2]:
            sections.append((f"Related — {title}", extract))
    sources = fetch_web_sources(topic, limit=3)
    for title, url, snippet in sources:
        sections.append((f"Web — {title}", snippet or url))
    if not sections:
        return "I found nothing readable on that topic, sir — the connection may be down."
    _set("summary", 72, "Composing...")
    summary = compose_summary(topic, sections)
    try:
        path = write_pdf(topic, summary, sections, sources)
    except Exception as e:
        return f"The PDF didn't save, sir: {e}"
    open_file(path)
    return (f"Research on {topic} is complete, sir — a {len(sections)}-section "
            f"PDF is on your desktop: {os.path.basename(path)}")
