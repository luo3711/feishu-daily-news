#!/usr/bin/env python3
"""Fuel Cell Intelligence Daily - Professional Multi-Card Edition"""

import json, os, urllib.request, urllib.parse, base64, hmac, hashlib
import xml.etree.ElementTree as ET
import time as time_module
from datetime import datetime, timedelta

WEBHOOK_URL   = os.environ["FEISHU_WEBHOOK_URL"]
FEISHU_SECRET = os.environ["FEISHU_SECRET"]
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
AI_ENDPOINT   = "https://models.inference.ai.azure.com/chat/completions"
AI_MODEL      = "gpt-4o-mini"

QUERIES = [
    ("fuel cell vehicle policy hydrogen regulation subsidy 2026", "en-US", "US"),
    ("hydrogen fuel cell FCEV industry government strategy investment", "en-GB", "GB"),
    ("Brennstoffzelle Wasserstoff Fahrzeug EU Politik Forderung", "de-DE", "DE"),
    ("pile combustible hydrogene vehicule politique France subvention", "fr-FR", "FR"),
    ("fuel cell vehicle Toyota Honda Hyundai Japan Korea policy", "en-US", "US"),
    ("hydrogen fuel cell truck bus commercial vehicle deployment China", "zh-CN", "CN"),
    ("fuel cell stack membrane catalyst breakthrough technology", "en-US", "US"),
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
    ceid_map = {"CN":"CN:zh-Hans","US":"US:en","GB":"GB:en","JP":"JP:ja","DE":"DE:de","FR":"FR:fr","KR":"KR:ko"}
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

    prompt = f"""你是一位资深燃料电池汽车产业研究总监，正在为项目组撰写每日产业情报简报。团队正在推动燃料电池汽车产业化落地，需要你从政策、技术、产业链、竞争格局等维度提供可指导决策的分析。

以下是今日全球最新相关新闻（{len(articles)}条）：

{articles_text}

请严格按以下结构撰写详细分析（每个部分 200-350 字），用中文 Markdown 格式：

## 一、今日政策信号与监管动向

逐条分析各国政府释放的具体政策信号。对每条政策，说明：具体内容（补贴金额/技术路线/目标数字）、实施时间表、对产业链各环节（整车/电堆/膜电极/氢能基础设施）的影响评估。重点区分"实质性利好"和"方向性表态"。用 **加粗** 标注关键数字和时间节点。

## 二、关键技术与产业化进展

从今日新闻中提取实质性技术突破和产业化里程碑。说明：是哪家企业/研究机构、具体突破了什么（性能指标提升数据）、处于什么阶段（实验室/中试/小批量/量产）、技术路径的可推广性评估。对国内项目组有参考价值的，明确标注 **【可对标】**。

## 三、产业链与竞争格局分析

分析今日新闻中反映的产业链变化：头部企业（丰田/现代/巴拉德/亿华通/重塑等）的战略动向；供应链关键环节（催化剂/质子交换膜/碳纸/双极板）的国产替代进展；新进入者的竞争策略；国际合作与合资动态。按 "机会" 和 "风险" 两个维度归纳。

## 四、今日情报的行动建议

基于以上分析，给出 4-5 条具体的、可操作的建议给项目组。每条建议包括：**做什么**（具体行动）、**为什么**（基于哪条情报）、**优先级**（高/中/低）。行动建议可以是：技术对标方向、政策申报窗口、供应链风险预警、合作伙伴考察方向、竞争应对策略等。

注意：专业、务实、有数据支撑。避免泛泛而谈的正确废话。每一条分析都要有明确的情报依据。"""

    if not GITHUB_TOKEN:
        return "AI_TOKEN_MISSING"

    result = http_post_json(AI_ENDPOINT, {
        "model": AI_MODEL,
        "messages": [{"role": "system", "content": "你是资深燃料电池汽车产业研究总监。输出专业、务实、有数据支撑的产业情报分析。避免空话套话。"},
                     {"role": "user", "content": prompt}],
        "max_tokens": 4000,
        "temperature": 0.3,
    }, headers={"Authorization": f"Bearer {GITHUB_TOKEN}"})
    return result["choices"][0]["message"]["content"]


def send_card(title, content, color="blue"):
    ts = str(int(time_module.time()))
    sk = (ts + "\n" + FEISHU_SECRET).encode("utf-8")
    sig = base64.b64encode(hmac.new(sk, b"", hashlib.sha256).digest()).decode()

    trim = content[:CARD_LIMIT]
    if len(content) > CARD_LIMIT:
        trim = trim[:CARD_LIMIT-60] + "\n\n---\n> (内容过长已截断，完整分析请联系项目组)"

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": title}, "template": color},
            "elements": [{"tag": "markdown", "content": trim}],
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
    header = f"**原始信源（{len(articles)}条）**\n\n"
    current_card = [header]; current_len = len(header); card_idx = 1

    for i, a in enumerate(articles, 1):
        flag = flags.get(a["region"], "")
        title = a["title"][:65] + ("..." if len(a["title"])>65 else "")
        line = f"{i}. [{flag}] [{title}]({a['url']})\n"
        line += f"   *{a['source']}* | {a['date'][:22]}\n\n"

        if current_len + len(line) > CARD_LIMIT:
            t = f"[{card_idx}/{len(articles)}] Sources - {today}" if card_idx == 1 else f"Sources (cont'd) - {today}"
            cards.append((t, "".join(current_card).strip(), "green" if card_idx == 1 else "yellow"))
            card_idx += 1; current_card = []; current_len = 0
        current_card.append(line); current_len += len(line)

    if current_card:
        t = f"[{card_idx}/{len(articles)}] Sources - {today}" if card_idx == 1 else f"Sources (cont'd) - {today}"
        cards.append((t, "".join(current_card).strip(), "green" if card_idx == 1 else "yellow"))
    return cards


def main():
    print("=" * 50)
    print("Fuel Cell Intelligence - Pro Edition")
    print("=" * 50)

    td = datetime.now()
    today = td.strftime("%m.%d")
    today_full = td.strftime("%Y-%m-%d")

    print("\n[1/3] Searching global sources...")
    articles = search_all()
    print(f"  Total: {len(articles)}")
    if not articles:
        send_card(f"No News - {today}", "No fuel cell vehicle news found.", "red")
        return

    print("\n[2/3] AI Deep Analysis (this may take 20-30s)...")
    ai_report = ai_analyze(articles)
    if ai_report == "AI_TOKEN_MISSING":
        ai_report = f"## Today's Intelligence\n\n{len(articles)} articles collected.\n\n(AI analysis unavailable - token not configured)"
    print(f"  Report: {len(ai_report)} chars")

    print("\n[3/3] Pushing cards...")

    # Card 1: AI Analysis Report
    ai_content = ai_report[:CARD_LIMIT]
    if len(ai_report) > CARD_LIMIT:
        ai_content = ai_report[:CARD_LIMIT-80] + "\n\n---\n> ⚠️ 报告过长已截断。完整分析请联系项目组获取。"

    title1 = f"Fuel Cell Intelligence - {today_full}"
    ok1 = send_card(title1, ai_content)
    print(f"  Card 1 (AI Analysis): {'OK' if ok1 else 'FAILED'}")

    # Cards 2+: Sources
    source_cards = build_source_cards(articles, today)
    for i, (t, c, clr) in enumerate(source_cards):
        time_module.sleep(1)
        ok = send_card(t, c, clr)
        print(f"  Card {i+2} (Sources): {'OK' if ok else 'FAILED'}")

    print(f"\nDone! {1+len(source_cards)} cards sent.")


if __name__ == "__main__":
    main()