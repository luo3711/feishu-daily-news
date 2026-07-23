#!/usr/bin/env python3
"""Fuel Cell Intelligence Daily - Config-Driven Edition"""

import json, os, urllib.request, urllib.parse, base64, hmac, hashlib
import xml.etree.ElementTree as ET
import time as time_module
from datetime import datetime
from pathlib import Path

# ── Load config files ──────────────────────────────────────────────
CONFIG_DIR = Path(__file__).parent / "config"

def _load_json(name):
    with open(CONFIG_DIR / name, "r", encoding="utf-8") as f:
        return json.load(f)

def _load_text(name):
    with open(CONFIG_DIR / name, "r", encoding="utf-8") as f:
        return f.read()

CFG    = _load_json("settings.json")
SRC    = _load_json("sources.json")
PROMPT_SYSTEM = _load_text("prompt_system.txt")
PROMPT_USER   = _load_text("prompt_user.txt")

# Env vars
WEBHOOK_URL   = os.environ["FEISHU_WEBHOOK_URL"]
FEISHU_SECRET = os.environ["FEISHU_SECRET"]
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")

# ── Helpers ────────────────────────────────────────────────────────
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

# ── Search ─────────────────────────────────────────────────────────
def search_google_news(query, hl, gl, max_results=50):
    ceid = CFG["ceid_map"].get(gl, f'{gl}:{hl.split("-")[0]}')
    rss_url = (
        f"https://news.google.com/rss/search?"
        f"q={urllib.parse.quote(query)}&hl={hl}&gl={gl}&ceid={ceid}"
    )
    xml_data = http_get(rss_url, {"User-Agent": "Mozilla/5.0"})
    root = ET.fromstring(xml_data)
    results = []
    for item in root.findall(".//item"):
        t = item.find("title"); l = item.find("link")
        s = item.find("source"); p = item.find("pubDate")
        title = t.text.strip() if t is not None and t.text else ""
        link = l.text if l is not None else ""
        source = s.text.strip() if s is not None and s.text else "Unknown"
        pubdate = p.text if p is not None else ""
        skip = ["stock", "share price", "sponsored", "advertisement"]
        if not title or any(w in title.lower() for w in skip):
            continue
        results.append({
            "title": title, "url": link, "source": source,
            "date": pubdate, "region": gl,
        })
        if len(results) >= max_results:
            break
    return results


def search_direct_rss(rss_url, region, source_name, max_results=20):
    """Fetch articles directly from a site RSS feed."""
    xml_data = http_get(rss_url, {"User-Agent": "Mozilla/5.0"})
    root = ET.fromstring(xml_data)
    results = []
    for item in root.findall(".//item"):
        t = item.find("title"); l = item.find("link"); p = item.find("pubDate")
        title = t.text.strip() if t is not None and t.text else ""
        link = l.text if l is not None else ""
        pubdate = p.text if p is not None else ""
        skip = ["stock", "share price", "sponsored", "advertisement"]
        if not title or any(w in title.lower() for w in skip):
            continue
        results.append({
            "title": title, "url": link, "source": source_name,
            "date": pubdate, "region": region,
        })
        if len(results) >= max_results:
            break
    return results

def search_all():
    seen = set(); all_results = []
    for q in SRC["queries"]:
        try:
            time_module.sleep(0.5)  # avoid rate limiting
            results = search_google_news(q["query"], q["hl"], q["gl"])
            print(f"  [{q['gl']}] -> {len(results)} results")
            for r in results:
                key = r["title"][:80]
                if key not in seen:
                    seen.add(key); all_results.append(r)
        except Exception as e:
            print(f"  [WARN] {q['gl']}: {e}")
    # Direct RSS feeds
    for feed in SRC.get("feeds", []):
        try:
            results = search_direct_rss(feed["url"], feed["region"], feed.get("name", "RSS"))
            print(f"  [RSS:{feed.get('name')}] -> {len(results)} results")
            for r in results:
                key = r["title"][:80]
                if key not in seen:
                    seen.add(key); all_results.append(r)
        except Exception as e:
            print(f"  [WARN] RSS {feed.get('name')}: {e}")

    def pd(r):
        try: return datetime.strptime(r["date"], "%a, %d %b %Y %H:%M:%S %Z")
        except: return datetime.min
    all_results.sort(key=pd, reverse=True)
    return all_results[:SRC["max_articles"]]

# ── AI ─────────────────────────────────────────────────────────────
def ai_analyze(articles):
    flags = CFG["region_flags"]
    articles_text = ""
    for i, a in enumerate(articles):
        flag = flags.get(a["region"], "")
        articles_text += f"\n{i+1}. {flag} {a['title']} | {a['source']}"

    if not GITHUB_TOKEN:
        return {"raw": True, "text": "AI_TOKEN_MISSING"}

    try:
        result = http_post_json(CFG["ai_endpoint"], {
            "model": CFG["ai_model"],
            "messages": [
                {"role": "system", "content": PROMPT_SYSTEM},
                {"role": "user", "content": PROMPT_USER.format(
                    article_count=len(articles),
                    articles_text=articles_text,
                )},
            ],
            "max_tokens": CFG["max_tokens"],
            "temperature": CFG["temperature"],
            "response_format": {"type": "json_object"},
        }, headers={"Authorization": f"Bearer {GITHUB_TOKEN}"})
        content = result["choices"][0]["message"]["content"]
        data = json.loads(content)
        return {
            "raw": False,
            "headline": data.get("headline", ""),
            "key_points": data.get("key_points", []),
            "sections": data.get("sections", ""),
        }
    except Exception as e:
        print(f"  [WARN] AI JSON failed: {e}; fallback to raw text")
        try:
            result = http_post_json(CFG["ai_endpoint"], {
                "model": CFG["ai_model"],
                "messages": [
                    {"role": "system", "content": PROMPT_SYSTEM},
                    {"role": "user", "content": f"基于以下新闻撰写五段式 Markdown 情报简报（政策/技术/竞争/市场/行动建议）：\n{articles_text}"},
                ],
                "max_tokens": 3000,
                "temperature": CFG["temperature"],
            }, headers={"Authorization": f"Bearer {GITHUB_TOKEN}"})
            return {"raw": True, "text": result["choices"][0]["message"]["content"]}
        except Exception as e2:
            return {"raw": True, "text": f"AI分析失败: {e2}"}

# ── Feishu ─────────────────────────────────────────────────────────
def send_card(title, content, color):
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title[:80]},
                "template": color,
            },
            "elements": [{"tag": "markdown", "content": content}],
        },
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    for attempt in range(3):
        ts = str(int(time_module.time()))
        sk = (ts + "\n" + FEISHU_SECRET).encode("utf-8")
        sig = base64.b64encode(
            hmac.new(sk, b"", hashlib.sha256).digest()
        ).decode()
        url = f"{WEBHOOK_URL}?timestamp={ts}&sign={sig}"
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode())
            if result.get("code") == 0:
                return True
            if attempt < 2: time_module.sleep(2)
        except:
            if attempt < 2: time_module.sleep(2)
    return False

# ── Card building ──────────────────────────────────────────────────
def split_markdown(content, limit=4500):
    if not content: return []
    if len(content) <= limit: return [content]
    chunks = []; current = ""
    for p in content.split("\n\n"):
        seg = p + "\n\n"
        if len(current) + len(seg) > limit and current:
            chunks.append(current.rstrip()); current = ""
        current += seg
    if current.strip(): chunks.append(current.rstrip())
    final = []
    for c in chunks:
        if len(c) <= limit: final.append(c)
        else:
            for i in range(0, len(c), limit):
                final.append(c[i:i+limit])
    return final

def build_summary_card(ai_result, today_full):
    if ai_result.get("raw"):
        return f"Fuel Cell Intelligence - {today_full}", "摘要生成失败，详见正文卡。", CFG["no_news_card_color"]
    headline = ai_result.get("headline", "")
    key_points = ai_result.get("key_points", [])
    labels = ["最关键信号", "最大机会", "最大风险"]
    lines = ["## 今日定调", ""]
    for i, line in enumerate(headline.split("\n")):
        label = labels[i] if i < len(labels) else f"第{i+1}点"
        lines.append(f"**{label}**：{line.strip()}")
    lines += ["", "## 今日重点", ""]
    for i, kp in enumerate(key_points, 1):
        lines.append(f"{i}. {kp}")
    return f"Fuel Cell Intelligence - {today_full}", "\n".join(lines), CFG["summary_card_color"]

def build_source_cards(articles, today):
    flags = CFG["region_flags"]
    buckets_def = CFG["region_buckets"]
    buckets = {name: [] for name in buckets_def}
    for a in articles:
        placed = False
        for name, regs in buckets_def.items():
            if a["region"] in regs:
                buckets[name].append(a); placed = True; break
        if not placed and "其他" in buckets:
            buckets["其他"].append(a)

    cards = []
    current = ""; current_len = 0; card_idx = 1
    def flush():
        nonlocal current, current_len, card_idx
        if current.strip():
            title = f"Sources {card_idx} - {today}"
            color = CFG["source_card_color_1"] if card_idx == 1 else CFG["source_card_color_2"]
            cards.append((title, current.strip(), color))
            card_idx += 1
        current = ""; current_len = 0

    global_idx = 0
    for name in buckets_def:
        items = buckets[name]
        if not items: continue
        header = f"## {name}\n\n"
        if current_len + len(header) > 4500 and current: flush()
        current += header; current_len += len(header)
        for a in items:
            global_idx += 1
            flag = flags.get(a["region"], "")
            t = a["title"][:65] + ("..." if len(a["title"]) > 65 else "")
            line = f"{global_idx}. [{flag}] [{t}]({a['url']})\n   *{a['source']}*\n\n"
            if current_len + len(line) > 4500: flush()
            current += line; current_len += len(line)
    flush()
    print(f"  Source cards built: {len(cards)}")
    return cards

def push(title, content, color, dry):
    if dry:
        print(f"\n=== {title} ({color}) ===\n{content}\n")
        return True
    ok = send_card(title, content, color)
    time_module.sleep(1)
    return ok

# ── Main ───────────────────────────────────────────────────────────
def main():
    td = datetime.now()
    today_full = td.strftime("%Y-%m-%d")
    dry = bool(os.environ.get("DRY_RUN"))

    print("[1/3] Searching...")
    articles = search_all()
    print(f"  {len(articles)} articles")

    if not articles:
        if dry:
            print("\n=== No News ===\nNo fuel cell news today.\n")
        else:
            send_card("No News", "No fuel cell news today.", CFG["no_news_card_color"])
        return

    print("[2/3] AI Analyzing...")
    ai_result = ai_analyze(articles)
    print(f"  raw={ai_result.get('raw')}")

    print("[3/3] Pushing...")

    # Card 1: Summary
    st, sc, scol = build_summary_card(ai_result, today_full)
    push(st, sc, scol, dry)
    print("  Summary card OK" if not dry else "  Summary card (dry)")

    # Card 2+: Report body
    body = ai_result.get("text", "") if ai_result.get("raw") else ai_result.get("sections", "")
    chunks = split_markdown(body)
    for idx, chunk in enumerate(chunks, 1):
        title = (
            f"Fuel Cell Report {idx} - {today_full}"
            if len(chunks) > 1 else f"Fuel Cell Report - {today_full}"
        )
        push(title, chunk, CFG["report_card_color"], dry)
        print(f"  {title} OK" if not dry else f"  {title} (dry)")

    # Cards: Sources (region-grouped)
    for title, content, color in build_source_cards(articles, today_full.split("-")[1]):
        push(title, content, color, dry)
        print(f"  {title} OK" if not dry else f"  {title} (dry)")

    print("Done!")

if __name__ == "__main__":
    main()
