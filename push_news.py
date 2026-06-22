#!/usr/bin/env python3
"""Fuel Cell Intelligence Daily - Multi-Card Edition"""

import json, os, urllib.request, urllib.parse, base64, hmac, hashlib
import xml.etree.ElementTree as ET
import time as time_module
from datetime import datetime, timedelta

WEBHOOK_URL   = os.environ["FEISHU_WEBHOOK_URL"]
FEISHU_SECRET = os.environ["FEISHU_SECRET"]
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
REPO          = os.environ.get("GITHUB_REPOSITORY", "bake7791/feishu-daily-news")
AI_ENDPOINT   = "https://models.inference.ai.azure.com/chat/completions"
AI_MODEL      = "gpt-4o-mini"

QUERIES = [
    ("fuel cell vehicle policy hydrogen regulation 2026", "en-US", "US"),
    ("hydrogen fuel cell FCEV industry government strategy", "en-GB", "GB"),
    ("Brennstoffzelle Wasserstoff Fahrzeug EU Politik", "de-DE", "DE"),
    ("pile combustible hydrogene vehicule politique France", "fr-FR", "FR"),
    ("fuel cell vehicle Toyota Honda Japan policy", "ja-JP", "JP"),
    ("hydrogen fuel cell industry news China policy", "zh-CN", "CN"),
]

MAX_ARTICLES = 30
CARD_LIMIT = 4500


def http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8")


def http_post_json(url, payload, headers=None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h = {"Content-Type": "application/json; charset=utf-8"}
    if headers: h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def search_google_news(query, hl, gl, max_results=50):
    ceid_map = {"CN":"CN:zh-Hans","US":"US:en","GB":"GB:en","JP":"JP:ja","DE":"DE:de","FR":"FR:fr"}
    ceid = ceid_map.get(gl, f'{gl}:{hl.split("-")[0]}')
    rss_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl={hl}&gl={gl}&ceid={ceid}"
    xml_data = http_get(rss_url, {"User-Agent": "Mozilla/5.0"})
    root = ET.fromstring(xml_data)
    results = []
    for item in root.findall(".//item"):
        t = item.find("title"); l = item.find("link"); s = item.find("source"); p = item.find("pubDate")
        title = t.text.strip() if t is not None and t.text else ""
        link = l.text if l is not None else ""
        source = s.text.strip() if s is not None and s.text else "Unknown"
        pubdate = p.text if p is not None else ""
        skip = ["stock", "share price", "sponsored", "advertisement"]
        if not title or any(w in title.lower() for w in skip):
            continue
        results.append({"title":title,"url":link,"source":source,"date":pubdate,"region":gl})
        if len(results)>=max_results: break
    return results


def search_all():
    seen = set(); all_results = []
    for query, hl, gl in QUERIES:
        try:
            results = search_google_news(query, hl, gl)
            print(f"  [{gl}] -> {len(results)} results")
            for r in results:
                key = r["title"][:80]
                if key not in seen:
                    seen.add(key); all_results.append(r)
        except Exception as e:
            print(f"  [WARN] {gl}: {e}")
    def pd(r):
        try: return datetime.strptime(r["date"], "%a, %d %b %Y %H:%M:%S %Z")
        except: return datetime.min
    all_results.sort(key=pd, reverse=True)
    return all_results[:MAX_ARTICLES]


def ai_analyze(articles):
    articles_text = ""
    for i, a in enumerate(articles):
        flag = {"CN":"[CN]","US":"[US]","GB":"[UK]","JP":"[JP]","DE":"[DE]","FR":"[FR]"}.get(a["region"],"")
        articles_text += f"\n{i+1}. {flag} {a['title']} | {a['source']}"

    prompt = f"""You are a senior fuel cell vehicle industry analyst. Analyze today's global news ({len(articles)} items) and write a DETAILED report in Chinese using Markdown.

{articles_text}

Structure (write 200-400 chars for each section, be substantive):

## 1. Today's Key Developments
Pick the 5 most significant news items. For each: summarize the key takeaway (60-80 chars), explain WHY it matters (1-2 sentences), note the source country. Be specific with facts and figures.

## 2. Policy & Regulatory Landscape
Analyze new policy signals, regulation changes, subsidy adjustments across China, EU, Japan, US. What direction are governments heading? What are the concrete policy details?

## 3. Industry & Technology Trends
What breakthroughs, production milestones, supply chain shifts, or corporate strategies emerged? Be specific about companies, technologies, numbers.

## 4. Market & Investment Signals
Funding rounds, partnerships, market forecasts, deployment numbers. What are the financial and commercial indicators?

## 5. Strategic Assessment
Synthesize 3-5 key themes from today's intelligence. What should industry watchers pay attention to? (~150 chars)

Write in professional but accessible Chinese. Use specific data points when available."""

    if not GITHUB_TOKEN:
        return "AI_TOKEN_MISSING"

    result = http_post_json(AI_ENDPOINT, {
        "model": AI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 3000,
        "temperature": 0.3,
    }, headers={"Authorization": f"Bearer {GITHUB_TOKEN}"})
    return result["choices"][0]["message"]["content"]


def send_card(title, content, color="blue"):
    """Send a single Feishu card"""
    ts = str(int(time_module.time()))
    sk = (ts + "\n" + FEISHU_SECRET).encode("utf-8")
    sig = base64.b64encode(hmac.new(sk, b"", hashlib.sha256).digest()).decode()

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": title}, "template": color},
            "elements": [{"tag": "markdown", "content": content}],
        },
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    for attempt in range(3):
        ts = str(int(time_module.time()))
        sk = (ts + "\n" + FEISHU_SECRET).encode("utf-8")
        sig = base64.b64encode(hmac.new(sk, b"", hashlib.sha256).digest()).decode()
        url = f"{WEBHOOK_URL}?timestamp={ts}&sign={sig}"
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode())
            if result.get("code") == 0:
                return True
            if attempt < 2:
                time_module.sleep(2)
        except:
            if attempt < 2:
                time_module.sleep(2)
    return False


def build_ai_card(ai_report, article_count, today):
    """Card 1: AI analysis report"""
    content = ai_report[:CARD_LIMIT]
    if len(ai_report) > CARD_LIMIT:
        content = content[:CARD_LIMIT-50] + "\n\n> (AI analysis truncated due to length)"

    title = f"Fuel Cell Intelligence - {today}"
    return title, content


def build_source_cards(articles, today):
    """Cards 2+: Source links, split across multiple cards"""
    flags = {"CN":"CN","US":"US","GB":"UK","JP":"JP","DE":"DE","FR":"FR"}
    cards = []

    header = f"**Sources ({len(articles)} articles)**\n\n"
    header_len = len(header)

    current_card = [header]
    current_len = header_len
    card_idx = 1

    for i, a in enumerate(articles, 1):
        flag = flags.get(a["region"], "")
        line = f"{i}. [{flag}] {a['title'][:60]}{'...' if len(a['title'])>60 else ''}\n"
        line += f"   {a['url']}\n"
        line += f"   *{a['source']}* | {a['date'][:22]}\n\n"

        if current_len + len(line) > CARD_LIMIT:
            title = f"Sources ({card_idx}/{len(articles)}) - {today}" if card_idx == 1 else f"Sources (cont'd) - {today}"
            cards.append((title, "".join(current_card).strip(), "green" if card_idx == 1 else "yellow"))
            card_idx += 1
            current_card = []
            current_len = 0

        current_card.append(line)
        current_len += len(line)

    if current_card:
        title = f"Sources ({card_idx}/{len(articles)}) - {today}" if card_idx == 1 else f"Sources (cont'd) - {today}"
        cards.append((title, "".join(current_card).strip(), "green" if card_idx == 1 else "yellow"))

    return cards


def main():
    print("=" * 50)
    print("Fuel Cell Intelligence - Multi-Card")
    print("=" * 50)

    td = datetime.now()
    today = td.strftime("%m.%d")

    # Step 1: Search
    print("\n[1/3] Searching...")
    articles = search_all()
    print(f"  Total: {len(articles)}")

    if not articles:
        send_card(f"No News - {today}", "No fuel cell vehicle news found today.")
        return

    # Step 2: AI Analysis
    print("\n[2/3] AI Analyzing...")
    ai_report = ai_analyze(articles)
    if ai_report == "AI_TOKEN_MISSING":
        print("  AI skipped (no token)")
        ai_report = f"## Today's Intelligence\n\n{len(articles)} articles collected. See source cards below.\n\n"
    else:
        print(f"  Report: {len(ai_report)} chars")

    # Step 3: Send cards
    print("\n[3/3] Sending cards...")

    # Card 1: AI Analysis
    title1, content1 = build_ai_card(ai_report, len(articles), today)
    ok = send_card(title1, content1)
    print(f"  Card 1 (AI): {'OK' if ok else 'FAILED'}")

    # Cards 2+: Sources
    source_cards = build_source_cards(articles, today)
    for i, (title, content, color) in enumerate(source_cards):
        ok = send_card(title, content, color)
        print(f"  Card {i+2} (Sources): {'OK' if ok else 'FAILED'}")
        time_module.sleep(1)

    print(f"\nDone! {1+len(source_cards)} cards sent.")


if __name__ == "__main__":
    main()