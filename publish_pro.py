#!/usr/bin/env python3
"""
Fuel Cell Intelligence  -  Daily News Publisher
================================================

A self-contained, config-driven tool that:

  1. Searches Google News RSS feeds (and direct RSS feeds) for hydrogen/fuel-cell news.
  2. Summarises the articles with an AI model (GitHub Models / Azure OpenAI).
  3. Pushes formatted interactive cards into a Feishu (Lark) group chat via webhook.

--- Quick Start ---

  1.  pip install -r requirements.txt       # only needs the stdlib + requests
  2.  python pubilish.py --setup             # generates config/ and .env.example
  3.  Edit .env (or set the env vars listed) and config/sources.json
  4.  python pubilish.py --dry-run           # preview locally
  5.  python pubilish.py                     # push to Feishu

--- Modes ---

  --setup          Create config/ directory + template files, then exit.
  --dry-run        Print cards to stdout instead of sending them.
  --console        Print a raw Markdown report (no AI structure, no Feishu).
  --output FILE    Write the AI report body to a Markdown file.
  --quiet          Only show warnings and errors.
  --verbose        Show debug-level detail.

--- Secrets (env vars or .env) ---

  FEISHU_WEBHOOK_URL    Feishu bot incoming-webhook URL  (required)
  FEISHU_SECRET         Webhook signing secret            (required)
  GITHUB_TOKEN          GitHub personal-access token for AI endpoint (optional;
                        without it AI analysis is skipped and raw card is sent)

Author  : https://github.com/luo3711/feishu-daily-news
License : MIT
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import sys
import time as _time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent
CONFIG_DIR = HERE / "config"

logger = logging.getLogger("fuelcell")

# ---------------------------------------------------------------------------
# .env loader (no external dependency)
# ---------------------------------------------------------------------------

def _load_dotenv(dotenv_path: Optional[Path] = None) -> None:
    """Parse a ``.env`` file into ``os.environ``, ignoring quoted values."""
    if dotenv_path is None:
        dotenv_path = HERE / ".env"
    if not dotenv_path.is_file():
        return
    for raw in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'\"").strip()
        if key and key not in os.environ:
            os.environ[key] = val

_load_dotenv()

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_json(name: str) -> Dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Config file missing: {path}\nRun --setup first.")
    return json.loads(path.read_text(encoding="utf-8"))

def _load_text(name: str) -> str:
    path = CONFIG_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"Config file missing: {path}\nRun --setup first.")
    return path.read_text(encoding="utf-8")

# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------

def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"Environment variable {name} is not set. "
            f"Create a .env file or export it."
        )
    return val

# ---------------------------------------------------------------------------
# HTTP primitives
# ---------------------------------------------------------------------------

def http_get(url: str, headers: Optional[Dict[str, str]] = None) -> str:
    """GET *url* and return the UTF-8-decoded body."""
    req = Request(url, headers=headers or {})
    with urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")

def http_post_json(
    url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 120,
) -> Dict[str, Any]:
    """POST JSON *payload* to *url*, return parsed JSON response."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    hdr = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        hdr.update(headers)
    req = Request(url, data=body, headers=hdr, method="POST")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ===================================================================
# Search
# ===================================================================

def search_google_news(
    query: str, hl: str, gl: str, ceid_map: Dict[str, str],
    max_results: int = 50,
) -> List[Dict[str, str]]:
    """Search Google News RSS for *query* and return article dicts."""
    ceid = ceid_map.get(gl, f"{gl}:{hl.split('-')[0]}")
    url = (
        f"https://news.google.com/rss/search?"
        f"q={quote(query)}&hl={hl}&gl={gl}&ceid={ceid}"
    )
    xml_data = http_get(url, {"User-Agent": "Mozilla/5.0"})
    root = ET.fromstring(xml_data)

    skip_words = {"stock", "share price", "sponsored", "advertisement"}
    results: List[Dict[str, str]] = []
    for item in root.findall(".//item"):
        t_el = item.find("title")
        l_el = item.find("link")
        s_el = item.find("source")
        p_el = item.find("pubDate")

        title = (t_el.text or "").strip()
        link = l_el.text if l_el is not None and l_el.text else ""
        source = (s_el.text or "Unknown").strip()
        pubdate = p_el.text if p_el is not None and p_el.text else ""

        if not title or any(w in title.lower() for w in skip_words):
            continue

        results.append({
            "title": title, "url": link, "source": source,
            "date": pubdate, "region": gl,
        })
        if len(results) >= max_results:
            break
    return results


def search_direct_rss(
    rss_url: str, region: str, source_name: str, max_results: int = 20,
) -> List[Dict[str, str]]:
    """Fetch articles from a direct site RSS feed."""
    xml_data = http_get(rss_url, {"User-Agent": "Mozilla/5.0"})
    root = ET.fromstring(xml_data)

    skip_words = {"stock", "share price", "sponsored", "advertisement"}
    results: List[Dict[str, str]] = []
    for item in root.findall(".//item"):
        t_el = item.find("title")
        l_el = item.find("link")
        p_el = item.find("pubDate")

        title = (t_el.text or "").strip()
        link = l_el.text if l_el is not None and l_el.text else ""
        pubdate = p_el.text if p_el is not None and p_el.text else ""

        if not title or any(w in title.lower() for w in skip_words):
            continue

        results.append({
            "title": title, "url": link, "source": source_name,
            "date": pubdate, "region": region,
        })
        if len(results) >= max_results:
            break
    return results


def search_all(
    queries: List[Dict[str, str]],
    feeds: List[Dict[str, str]],
    ceid_map: Dict[str, str],
    max_articles: int,
) -> List[Dict[str, str]]:
    """Run every configured query / feed and return de-duplicated articles."""
    seen: set = set()
    all_results: List[Dict[str, str]] = []

    for q in queries:
        try:
            _time.sleep(0.5)  # gentle rate-limit
            results = search_google_news(
                q["query"], q["hl"], q["gl"], ceid_map,
            )
            logger.info("  [%s] -> %d results", q["gl"], len(results))
            for r in results:
                key = r["title"][:80]
                if key not in seen:
                    seen.add(key)
                    all_results.append(r)
        except Exception as exc:
            logger.warning("  [%s] query failed: %s", q["gl"], exc)

    for feed in feeds:
        try:
            results = search_direct_rss(
                feed["url"], feed.get("region", "XX"), feed.get("name", "RSS"),
            )
            logger.info("  [RSS:%s] -> %d results", feed.get("name"), len(results))
            for r in results:
                key = r["title"][:80]
                if key not in seen:
                    seen.add(key)
                    all_results.append(r)
        except Exception as exc:
            logger.warning("  [RSS:%s] failed: %s", feed.get("name"), exc)

    # Sort by publication date descending
    def pub_date(r: Dict[str, str]) -> datetime:
        try:
            return datetime.strptime(r["date"], "%a, %d %b %Y %H:%M:%S %Z")
        except (ValueError, KeyError):
            return datetime.min

    all_results.sort(key=pub_date, reverse=True)
    return all_results[:max_articles]


# ===================================================================
# AI Analysis
# ===================================================================

def ai_analyze(
    articles: List[Dict[str, str]],
    settings: Dict[str, Any],
    prompt_system: str,
    prompt_user: str,
    github_token: str,
    region_flags: Dict[str, str],
) -> Dict[str, Any]:
    """Send articles to AI endpoint and return structured summary."""
    # Build article list text
    parts: List[str] = []
    for i, a in enumerate(articles):
        flag = region_flags.get(a["region"], "")
        parts.append(f"\n{i + 1}. {flag} {a['title']} | {a['source']}")
    articles_text = "".join(parts)

    # When no token, return raw-marker so callers can fall back gracefully
    if not github_token:
        return {"raw": True, "text": "AI_TOKEN_MISSING"}

    ai_url = settings["ai_endpoint"]
    ai_model = settings["ai_model"]

    try:
        result = http_post_json(ai_url, {
            "model": ai_model,
            "messages": [
                {"role": "system", "content": prompt_system},
                {"role": "user", "content": prompt_user.format(
                    article_count=len(articles),
                    articles_text=articles_text,
                )},
            ],
            "max_tokens": settings.get("max_tokens", 5000),
            "temperature": settings.get("temperature", 0.3),
            "response_format": {"type": "json_object"},
        }, headers={"Authorization": f"Bearer {github_token}"})
        content = result["choices"][0]["message"]["content"]
        data = json.loads(content)
        return {
            "raw": False,
            "headline": data.get("headline", ""),
            "key_points": data.get("key_points", []),
            "sections": data.get("sections", ""),
        }
    except Exception as exc:
        logger.warning("AI JSON parse failed, falling back to raw text: %s", exc)
        try:
            result = http_post_json(ai_url, {
                "model": ai_model,
                "messages": [
                    {"role": "system", "content": prompt_system},
                    {"role": "user", "content": (
                        "Based on the following news articles, write a "
                        "five-section Markdown intelligence brief "
                        "(Policy / Technology / Competition / Market / "
                        "Action Items):\n" + articles_text
                    )},
                ],
                "max_tokens": 3000,
                "temperature": settings.get("temperature", 0.3),
            }, headers={"Authorization": f"Bearer {github_token}"})
            return {"raw": True, "text": result["choices"][0]["message"]["content"]}
        except Exception as exc2:
            return {"raw": True, "text": f"AI analysis failed: {exc2}"}


# ===================================================================
# Feishu card delivery
# ===================================================================

def _make_signature(secret: str, timestamp: int) -> str:
    """Build the HMAC-SHA256 signature required by Feishu webhooks."""
    raw = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(raw, b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def send_card(
    title: str,
    content: str,
    color: str,
    webhook_url: str,
    secret: str,
) -> bool:
    """Push one interactive card to the Feishu webhook (3 retries)."""
    payload: Dict[str, Any] = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title[:80]},
                "template": color,
            },
            "elements": [{"tag": "markdown", "content": content}],
        },
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    for attempt in range(3):
        ts = int(_time.time())
        sig = _make_signature(secret, ts)
        signed_url = f"{webhook_url}?timestamp={ts}&sign={sig}"

        req = Request(
            signed_url, data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode())
            if result.get("code") == 0:
                return True
            if attempt < 2:
                _time.sleep(2)
        except Exception:
            if attempt < 2:
                _time.sleep(2)
    return False


# ===================================================================
# Card builders
# ===================================================================

def split_markdown(content: str, limit: int = 4500) -> List[str]:
    """Break a long Markdown string into Feishu-card-sized chunks."""
    if not content:
        return []
    if len(content) <= limit:
        return [content]

    chunks: List[str] = []
    current = ""
    for para in content.split("\n\n"):
        seg = para + "\n\n"
        if len(current) + len(seg) > limit and current:
            chunks.append(current.rstrip())
            current = ""
        current += seg
    if current.strip():
        chunks.append(current.rstrip())

    # Force-split any remaining over-long chunk
    final: List[str] = []
    for c in chunks:
        if len(c) <= limit:
            final.append(c)
        else:
            for i in range(0, len(c), limit):
                final.append(c[i:i + limit])
    return final


def build_summary_card(
    ai_result: Dict[str, Any],
    today_full: str,
    settings: Dict[str, Any],
) -> Tuple[str, str, str]:
    """Return (title, content, color) for the summary / headline card."""
    if ai_result.get("raw"):
        return (
            f"Fuel Cell Intelligence - {today_full}",
            "Summary generation failed; see report cards.",
            settings.get("no_news_card_color", "red"),
        )

    headline: str = ai_result.get("headline", "")
    key_points: List[str] = ai_result.get("key_points", [])

    labels = ["Most Important Signal", "Biggest Opportunity", "Biggest Risk"]
    lines = ["## Today's Brief", ""]
    for i, line in enumerate(headline.split("\n")):
        label = labels[i] if i < len(labels) else f"Point {i + 1}"
        lines.append(f"**{label}**: {line.strip()}")

    lines += ["", "## Key Points", ""]
    for i, kp in enumerate(key_points, 1):
        lines.append(f"{i}. {kp}")

    title = f"Fuel Cell Intelligence - {today_full}"
    color = settings.get("summary_card_color", "blue")
    return title, "\n".join(lines), color


def build_source_cards(
    articles: List[Dict[str, str]],
    today: str,
    settings: Dict[str, Any],
) -> List[Tuple[str, str, str]]:
    """Group articles by region buckets and return card triples."""
    flags = settings.get("region_flags", {})
    buckets_def: Dict[str, List[str]] = settings.get("region_buckets", {})
    buckets: Dict[str, List[Dict[str, str]]] = {name: [] for name in buckets_def}

    for a in articles:
        placed = False
        for name, regions in buckets_def.items():
            if a["region"] in regions:
                buckets[name].append(a)
                placed = True
                break
        if not placed and "\u5176\u4ed6" in buckets:  # "鍏朵粬"
            buckets["\u5176\u4ed6"].append(a)

    cards: List[Tuple[str, str, str]] = []
    current = ""
    current_len = 0
    card_idx = 1

    def flush() -> None:
        nonlocal current, current_len, card_idx
        if current.strip():
            title = f"Sources {card_idx} - {today}"
            c = settings["source_card_color_1"] if card_idx == 1 else settings["source_card_color_2"]
            cards.append((title, current.strip(), c))
            card_idx += 1
        current = ""
        current_len = 0

    global_idx = 0
    for name in buckets_def:
        items = buckets.get(name, [])
        if not items:
            continue
        header = f"## {name}\n\n"
        if current_len + len(header) > 4500 and current:
            flush()  # type: ignore[call-arg]
        current += header
        current_len += len(header)
        for a in items:
            global_idx += 1
            flag = flags.get(a["region"], "")
            t = a["title"][:65] + ("..." if len(a["title"]) > 65 else "")
            line = f"{global_idx}. [{flag}] [{t}]({a['url']})\n   *{a['source']}*\n\n"
            if current_len + len(line) > 4500:
                flush()  # type: ignore[call-arg]
            current += line
            current_len += len(line)
    flush()

    logger.info("  Source cards built: %d", len(cards))
    return cards


# ===================================================================
# Delivery dispatcher
# ===================================================================

class Delivery:
    """Abstracts the actual delivery target (Feishu, console, file, dry-run)."""

    def __init__(self, mode: str, output_file: Optional[Path] = None,
                 webhook_url: str = "", secret: str = "") -> None:
        self.mode = mode
        self.output_file = output_file
        self.webhook_url = webhook_url
        self.secret = secret

    def push(self, title: str, content: str, color: str = "blue") -> bool:
        if self.mode == "dry-run":
            print(f"\n{'=' * 60}")
            print(f"TITLE: {title}  (color: {color})")
            print(f"{'=' * 60}")
            print(content)
            return True

        if self.mode == "console":
            print(f"\n## {title}\n\n{content}\n")
            return True

        if self.mode == "file" and self.output_file:
            with self.output_file.open("a", encoding="utf-8") as fh:
                fh.write(f"# {title}\n\n{content}\n\n---\n\n")
            return True

        # feishu mode
        ok = send_card(title, content, color, self.webhook_url, self.secret)
        _time.sleep(1)  # rate-limit guard
        return ok


# ===================================================================
# --setup scaffolding
# ===================================================================

def run_setup() -> None:
    """Create the config/ directory with all template files."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # ---- settings.json ----
    (CONFIG_DIR / "settings.json").write_text(json.dumps({
        "ai_endpoint": "https://models.inference.ai.azure.com/chat/completions",
        "ai_model": "gpt-4o",
        "max_tokens": 5000,
        "temperature": 0.3,
        "summary_card_color": "blue",
        "report_card_color": "grey",
        "source_card_color_1": "green",
        "source_card_color_2": "yellow",
        "no_news_card_color": "red",
        "ceid_map": {
            "CN": "CN:zh-Hans", "US": "US:en", "GB": "GB:en",
            "JP": "JP:ja", "DE": "DE:de", "FR": "FR:fr",
        },
        "region_flags": {
            "CN": "[CN]", "US": "[US]", "GB": "[UK]",
            "JP": "[JP]", "DE": "[DE]", "FR": "[FR]",
        },
        "region_buckets": {
            "China": ["CN"],
            "Europe / Americas": ["US", "GB", "DE", "FR"],
            "Japan / Korea": ["JP", "KR"],
            "Other": [],
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- sources.json ----
    (CONFIG_DIR / "sources.json").write_text(json.dumps({
        "max_articles": 35,
        "queries": [
            {"query": "fuel cell vehicle policy hydrogen regulation subsidy 2026", "hl": "en-US", "gl": "US"},
            {"query": "hydrogen fuel cell FCEV industry strategy investment", "hl": "en-GB", "gl": "GB"},
            {"query": "Brennstoffzelle Wasserstoff Fahrzeug EU Politik", "hl": "de-DE", "gl": "DE"},
            {"query": "fuel cell vehicle Toyota Honda Hyundai Japan Korea", "hl": "en-US", "gl": "US"},
            {"query": "hydrogen fuel cell export Europe certification market access", "hl": "en-GB", "gl": "GB"},
            {"query": "fuel cell truck bus forklift port equipment stationary power", "hl": "en-US", "gl": "US"},
            {"query": "fuel cell stack membrane electrode bipolar plate breakthrough", "hl": "en-US", "gl": "US"},
            {"query": "hydrogen price refueling station green hydrogen cost 2026", "hl": "en-US", "gl": "US"},
            {"query": "fuel cell PowerCell Ballard Plug Power order contract", "hl": "en-US", "gl": "US"},
            {"query": "green hydrogen investment funding M&A 2026", "hl": "en-US", "gl": "US"},
        ],
        "feeds": [
            {
                "url": "https://www.globalhydrogenreview.com/rss/hydrogen.xml",
                "region": "GB", "name": "Global Hydrogen Review"
            },
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- prompt_system.txt ----
    (CONFIG_DIR / "prompt_system.txt").write_text(
        "You are a fuel-cell-industry intelligence analyst. "
        "Output concise, professional, substance-only analysis. "
        "Return a JSON object following the user's schema exactly. "
        "No preamble, no closing remarks, no markdown fences.",
        encoding="utf-8",
    )

    # ---- prompt_user.txt ----
    (CONFIG_DIR / "prompt_user.txt").write_text("""You are the head of industry intelligence for a fuel-cell company. Write today's global hydrogen industry intelligence daily for the CEO.

Here are today's articles ({article_count} total):
{articles_text}

Return a JSON object with these fields:
- headline: string. 3 lines joined by \\n.
  Line 1 = "Today's most important signal"
  Line 2 = "Biggest opportunity"
  Line 3 = "Biggest risk"
  Each line <= 40 chars.
- key_points: array of strings. 5 bullet points in "[Region] one-sentence summary" format.
  Region is one of CN / EU+US / JP+KR.
- sections: string. Five-section Markdown body separated by blank lines:

**1. Core Insights**
Top 3-5 signals covering policy / technology / market / competitive dimensions.
Let the CEO understand what happened today in 30 seconds.

**2. Action Items**
4-5 actionable recommendations. Format: specific action + intelligence basis + priority (High/Med/Low).
Focus: technology benchmarking, policy applications, supply-chain risk, export market access, partnership opportunities.
Actions must relate to the company's product lines (stack / system / heavy truck / bus / forklift / port machinery / stationary power).

**3. Policy & Regulatory Signals**
Grouped by China / EU+US / JP+KR. Each entry: who, what they did, scope of impact.
Distinguish substantive positives from cosmetic signals. Pay special attention to demonstration-city cluster policies, export certification regulations, carbon tariffs, etc.

**4. Technology & Industrialisation Progress**
Substantive breakthroughs and mass-production progress. Include: company, what was broken through, concrete performance metrics, current stage (lab / pilot / mass production).
Tag [Benchmark-able] where the technology roadmap aligns with the company's.
Cover: membrane electrode / bipolar plate / stack / system integration.

**5. Market & Competitive Dynamics**
- Demand side: downstream orders (truck / bus / forklift / port), hydrogen station construction, demo projects.
- Supply side: hydrogen price trends, core component supply chain shifts.
- Companies: Toyota, Hyundai, Ballard, Plug Power, PowerCell, etc.
Analyse strategic moves and market-share shifts. Summarise as opportunities vs. risks.

Return ONLY the JSON object. No preamble, no markdown fences, no closing remarks.""", encoding="utf-8")

    # ---- .env.example ----
    (HERE / ".env.example").write_text("""# Feishu bot incoming-webhook URL (required)
FEISHU_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx

# Webhook signing secret (required)
FEISHU_SECRET=xxxxxxxx

# GitHub personal-access token for the AI endpoint (optional; skip AI if empty)
GITHUB_TOKEN=ghp_xxxxxxxx
""", encoding="utf-8")

    print("Setup complete!")
    print(f"  config/       -> {CONFIG_DIR}")
    print(f"  .env.example  -> {HERE / '.env.example'}")
    print()
    print("Next steps:")
    print("  1. Copy .env.example to .env and fill in your secrets")
    print("  2. Review config/sources.json - adjust queries for your audience")
    print("  3. Run with --dry-run to test")


# ===================================================================
# CLI
# ===================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Fuel Cell Intelligence Daily News Publisher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  %(prog)s --setup               # generate config templates\n"
            "  %(prog)s --dry-run             # preview without pushing\n"
            "  %(prog)s --console             # print raw Markdown report\n"
            "  %(prog)s --output report.md    # write report body to file\n"
        ),
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--setup", action="store_true",
                       help="Generate config/ directory and .env.example, then exit.")
    mode.add_argument("--dry-run", action="store_true",
                       help="Print cards to stdout instead of sending them.")
    mode.add_argument("--console", action="store_true",
                       help="Print a flat Markdown report (bypasses AI JSON structuring).")
    mode.add_argument("--output", metavar="FILE", type=Path,
                       help="Write AI report body to a Markdown file.")
    p.add_argument("--quiet", action="store_true",
                    help="Only show warnings and errors.")
    p.add_argument("--verbose", action="store_true",
                    help="Show debug-level detail.")
    return p


# ===================================================================
# Main
# ===================================================================

def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)

    # ---- logging ----
    if args.quiet:
        level = logging.WARNING
    elif args.verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)-7s %(message)s",
    )

    # ---- setup mode ----
    if args.setup:
        run_setup()
        return

    # ---- validate required files ----
    missing = [f for f in ["settings.json", "sources.json",
                            "prompt_system.txt", "prompt_user.txt"]
               if not (CONFIG_DIR / f).is_file()]
    if missing:
        logger.error("Config files missing: %s", ", ".join(missing))
        logger.error("Run with --setup first, then edit the generated files.")
        sys.exit(1)

    # ---- load config ----
    try:
        cfg = _load_json("settings.json")
        src = _load_json("sources.json")
        prompt_system = _load_text("prompt_system.txt")
        prompt_user = _load_text("prompt_user.txt")
    except FileNotFoundError as exc:
        logger.error(str(exc))
        sys.exit(1)

    # ---- secrets ----
    webhook_url = _require_env("FEISHU_WEBHOOK_URL")
    feishu_secret = _require_env("FEISHU_SECRET")
    github_token = os.environ.get("GITHUB_TOKEN", "")

    # ---- delivery mode ----
    if args.dry_run:
        mode = "dry-run"
    elif args.console:
        mode = "console"
        # In console mode we also skip JSON-structured AI output
        cfg = {**cfg, "console_raw": True}
    elif args.output:
        mode = "file"
    else:
        mode = "feishu"

    delivery = Delivery(
        mode=mode,
        output_file=args.output,
        webhook_url=webhook_url,
        secret=feishu_secret,
    )

    # ---- today's date ----
    td = datetime.now(timezone.utc).astimezone()
    today_full = td.strftime("%Y-%m-%d")
    today_short = td.strftime("%m.%d")

    # ---- step 1: search ----
    logger.info("[1/3] Searching for news ...")
    articles = search_all(
        src.get("queries", []),
        src.get("feeds", []),
        cfg.get("ceid_map", {}),
        src.get("max_articles", 25),
    )
    logger.info("  %d articles collected", len(articles))

    if not articles:
        delivery.push(
            "No News",
            "No fuel-cell news found today.",
            cfg.get("no_news_card_color", "red"),
        )
        logger.info("Done (no articles).")
        return

    # ---- step 2: AI analysis ----
    logger.info("[2/3] AI analysis ...")
    ai_result = ai_analyze(
        articles, cfg, prompt_system, prompt_user, github_token,
        region_flags=cfg.get("region_flags", {}),
    )
    logger.info("  raw=%s", ai_result.get("raw"))

    # ---- step 3: deliver ----
    logger.info("[3/3] Delivering cards ...")

    # Card 1: Summary
    st, sc, scol = build_summary_card(ai_result, today_full, cfg)
    delivery.push(st, sc, scol)
    logger.info("  Summary card OK")

    # Card(s): Report body
    body = ai_result.get("text", "") if ai_result.get("raw") else ai_result.get("sections", "")
    chunks = split_markdown(body)
    for idx, chunk in enumerate(chunks, 1):
        if len(chunks) > 1:
            title = f"Fuel Cell Report {idx} - {today_full}"
        else:
            title = f"Fuel Cell Report - {today_full}"
        delivery.push(title, chunk, cfg.get("report_card_color", "grey"))
        logger.info("  %s OK", title)

    # Card(s): Source list
    for title, content, color in build_source_cards(articles, today_short, cfg):
        delivery.push(title, content, color)
        logger.info("  %s OK", title)

    logger.info("Done!")


if __name__ == "__main__":
    main()

