#!/usr/bin/env python3
"""Fuel Cell Intelligence Daily - Clean Professional Edition"""

import json, os, urllib.request, urllib.parse, base64, hmac, hashlib
import xml.etree.ElementTree as ET
import time as time_module
from datetime import datetime

WEBHOOK_URL   = os.environ["FEISHU_WEBHOOK_URL"]
FEISHU_SECRET = os.environ["FEISHU_SECRET"]
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
AI_ENDPOINT   = "https://models.inference.ai.azure.com/chat/completions"
AI_MODEL = "gpt-4o"

QUERIES = [
    ("fuel cell vehicle policy hydrogen regulation subsidy 2026", "en-US", "US"),
    ("hydrogen fuel cell FCEV industry strategy investment", "en-GB", "GB"),
    ("Brennstoffzelle Wasserstoff Fahrzeug EU Politik", "de-DE", "DE"),
    ("pile combustible hydrogene vehicule France", "fr-FR", "FR"),
    ("fuel cell vehicle Toyota Honda Hyundai Japan Korea", "en-US", "US"),
    ("fuel cell truck bus commercial vehicle deployment China hydrogen", "zh-CN", "CN"),
    ("fuel cell stack membrane catalyst breakthrough technology", "en-US", "US"),
]

MAX_ARTICLES = 25


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

    prompt = f"""你是燃料电池汽车产业研究总监。为项目团队撰写今日产业情报简报。语言精炼、专业、只说实质内容。

今日全球新闻（{len(articles)}条）：
{articles_text}

严格按以下结构输出，每个部分约 200 字，Markdown 格式：

## 一、政策信号

逐条列出今日各国政府、监管机构的具体政策动向。每条写明：谁（国家/机构）、做了什么（补贴金额/技术路线/目标数字）、影响范围。区分实质利好与表态性信号。

## 二、技术产业化

列出今日实质性技术突破和量产进展。写明：哪个企业、突破什么、具体性能指标、当前阶段（实验室/中试/量产）。与国内项目有关联的标注【可对标】。

## 三、竞争格局

今日新闻反映的产业链竞争变化。点名具体企业（丰田、现代、巴拉德、亿华通等），分析其战略动向、市场份额变化、供应链动作。按机会和风险两个维度归纳。

## 四、行动建议

4-5 条可操作建议。每条：具体行动 + 情报依据 + 优先级（高/中/低）。聚焦技术对标、政策申报、供应链风控、合作机会。

只输出四个部分的内容。不要开头语、结束语、免责声明。"""

    if not GITHUB_TOKEN:
        return "AI_TOKEN_MISSING"

    result = http_post_json(AI_ENDPOINT, {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": "你是资深燃料电池产业分析师。输出精炼、专业、只说实质内容。不要客套话、不要免责声明、不要开头和结尾语。"},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 3000,
        "temperature": 0.3,
    }, headers={"Authorization": f"Bearer {GITHUB_TOKEN}"})
    return result["choices"][0]["message"]["content"]


def send_card(title, content, color="blue"):
    ts = str(int(time_module.time()))
    sk = (ts + "\n" + FEISHU_SECRET).encode("utf-8")
    sig = base64.b64encode(hmac.new(sk, b"", hashlib.sha256).digest()).decode()

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": title[:80]}, "template": color},
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
            if attempt < 2: time_module.sleep(2)
        except:
            if attempt < 2: time_module.sleep(2)
    return False


def build_source_cards(articles, today):
    flags = {"CN":"CN","US":"US","GB":"UK","JP":"JP","DE":"DE","FR":"FR"}
    cards = []
    current = []; current_len = 0; card_idx = 1

    for i, a in enumerate(articles, 1):
        flag = flags.get(a["region"], "")
        t = a["title"][:65] + ("..." if len(a["title"])>65 else "")
        line = f"{i}. [{flag}] [{t}]({a['url']})\n   *{a['source']}*\n\n"
        if current_len + len(line) > 4500:
            title = f"Sources {card_idx} - {today}"
            cards.append((title, "".join(current).strip(), "green" if card_idx==1 else "yellow"))
            card_idx += 1; current = []; current_len = 0
        current.append(line); current_len += len(line)

    if current:
        title = f"Sources {card_idx} - {today}"
        cards.append((title, "".join(current).strip(), "green" if card_idx==1 else "yellow"))
    return cards


def main():
    td = datetime.now(); today = td.strftime("%m.%d"); today_full = td.strftime("%Y-%m-%d")

    print("[1/3] Searching...")
    articles = search_all()
    print(f"  {len(articles)} articles")

    if not articles:
        send_card("No News", "No fuel cell news today.", "red"); return

    print("[2/3] AI Analyzing...")
    ai_report = ai_analyze(articles)
    print(f"  {len(ai_report)} chars")

    print("[3/3] Pushing...")

    # Card 1: AI Report
    send_card(f"Fuel Cell Intelligence - {today_full}", ai_report)
    print("  Card 1 OK")

    # Cards 2+: Sources
    for title, content, color in build_source_cards(articles, today):
        time_module.sleep(1)
        send_card(title, content, color)
        print(f"  {title} OK")

    print("Done!")


if __name__ == "__main__":
    main()
