#!/usr/bin/env python3
"""Fuel Cell Intelligence Daily - Complete Structured Edition"""

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
    ("hydrogen price refueling station green hydrogen cost 2026", "en-US", "US"),
    ("加氢站 氢气价格 绿氢 示范项目 2026", "zh-CN", "CN"),
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

    prompt = f"""你是燃料电池汽车产业研究总监，为风氢扬公司老板撰写今日全球产业情报日报。语言精炼、专业、只说实质内容。

今日全球新闻（{len(articles)}条）：
{articles_text}

严格输出 JSON 对象，字段如下：
- headline: 字符串，3 行定调用 \\n 分隔。第1行"今日最关键信号"，第2行"最大机会"，第3行"最大风险"。每行不超过40字。
- key_points: 字符串数组，5 条今日重点，每条格式"[区域] 一句话要点"，区域用 CN/欧美/日韩 之一。
- sections: 字符串，五段 Markdown 正文（段间用 \\n\\n 分隔），依次为：

## 一、政策与监管信号
分中国/欧美/日韩，每条写明谁（国家/机构）、做了什么（补贴金额/技术路线/目标数字）、影响范围。区分实质利好与表态性信号。

## 二、技术与产业化进展
实质性技术突破和量产进展。写明企业、突破什么、具体性能指标、当前阶段（实验室/中试/量产）。与国内项目可对标的标【可对标】。

## 三、竞争格局与企业动向
点名具体企业（丰田、现代、巴拉德、亿华通等），分析战略动向、市场份额变化、供应链动作。按机会和风险两个维度归纳。

## 四、市场与需求动态
氢气价格走势、下游订单与需求、加氢站建设进度、示范项目落地情况。

## 五、对我司的影响与行动建议
4-5 条可操作建议。每条：具体行动 + 情报依据 + 优先级（高/中/低）。聚焦技术对标、政策申报、供应链风控、合作机会。

只输出 JSON 对象，不要 markdown 代码块包裹，不要开头语、结束语、免责声明。"""

    if not GITHUB_TOKEN:
        return {"raw": True, "text": "AI_TOKEN_MISSING"}

    try:
        result = http_post_json(AI_ENDPOINT, {
            "model": AI_MODEL,
            "messages": [
                {"role": "system", "content": "你是资深燃料电池产业分析师。输出精炼、专业、只说实质内容。严格按要求的 JSON 格式输出，不要任何多余文字。"},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": 4000,
            "temperature": 0.3,
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
            result = http_post_json(AI_ENDPOINT, {
                "model": AI_MODEL,
                "messages": [
                    {"role": "system", "content": "你是资深燃料电池产业分析师。输出精炼、专业、只说实质内容。"},
                    {"role": "user", "content": f"基于以下新闻撰写五段式 Markdown 情报简报（政策/技术/竞争/市场/行动建议）：\n{articles_text}"}
                ],
                "max_tokens": 3000,
                "temperature": 0.3,
            }, headers={"Authorization": f"Bearer {GITHUB_TOKEN}"})
            return {"raw": True, "text": result["choices"][0]["message"]["content"]}
        except Exception as e2:
            return {"raw": True, "text": f"AI分析失败: {e2}"}


def send_card(title, content, color="blue"):
    data = json.dumps({
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": title[:80]}, "template": color},
            "elements": [{"tag": "markdown", "content": content}],
        },
    }, ensure_ascii=False).encode("utf-8")

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


def split_markdown(content, limit=4500):
    if not content:
        return []
    if len(content) <= limit:
        return [content]
    chunks = []; current = ""
    for p in content.split("\n\n"):
        seg = p + "\n\n"
        if len(current) + len(seg) > limit and current:
            chunks.append(current.rstrip()); current = ""
        current += seg
    if current.strip():
        chunks.append(current.rstrip())
    final = []
    for c in chunks:
        if len(c) <= limit:
            final.append(c)
        else:
            for i in range(0, len(c), limit):
                final.append(c[i:i+limit])
    return final


def build_summary_card(ai_result, today_full):
    if ai_result.get("raw"):
        return (f"Fuel Cell Intelligence - {today_full}", "摘要生成失败，详见正文卡。", "blue")
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
    return (f"Fuel Cell Intelligence - {today_full}", "\n".join(lines), "blue")


def build_source_cards(articles, today):
    flags = {"CN":"CN","US":"US","GB":"UK","JP":"JP","DE":"DE","FR":"FR"}
    bucket_order = [("中国", {"CN"}), ("欧美", {"US","GB","DE","FR"}), ("日韩", {"JP","KR"}), ("其他", None)]
    buckets = {name: [] for name, _ in bucket_order}
    for a in articles:
        for name, regs in bucket_order:
            if regs is None or a["region"] in regs:
                buckets[name].append(a); break

    cards = []
    current = ""; current_len = 0; card_idx = 1
    def flush():
        nonlocal current, current_len, card_idx
        if current.strip():
            title = f"Sources {card_idx} - {today}"
            cards.append((title, current.strip(), "green" if card_idx == 1 else "yellow"))
            card_idx += 1
        current = ""; current_len = 0

    global_idx = 0
    for name, _ in bucket_order:
        items = buckets[name]
        if not items:
            continue
        header = f"## {name}\n\n"
        if current_len + len(header) > 4500 and current:
            flush()
        current += header; current_len += len(header)
        for a in items:
            global_idx += 1
            flag = flags.get(a["region"], "")
            t = a["title"][:65] + ("..." if len(a["title"]) > 65 else "")
            line = f"{global_idx}. [{flag}] [{t}]({a['url']})\n   *{a['source']}*\n\n"
            if current_len + len(line) > 4500:
                flush()
                current = ""; current_len = 0
            current += line; current_len += len(line)
    flush()
    return cards


def push(title, content, color, dry):
    if dry:
        print(f"\n=== {title} ({color}) ===\n{content}\n")
        return True
    ok = send_card(title, content, color)
    time_module.sleep(1)
    return ok


def main():
    td = datetime.now(); today = td.strftime("%m.%d"); today_full = td.strftime("%Y-%m-%d")
    dry = bool(os.environ.get("DRY_RUN"))

    print("[1/3] Searching...")
    articles = search_all()
    print(f"  {len(articles)} articles")

    if not articles:
        if dry:
            print("\n=== No News (red) ===\nNo fuel cell news today.\n")
        else:
            send_card("No News", "No fuel cell news today.", "red")
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
    if ai_result.get("raw"):
        body = ai_result.get("text", "")
    else:
        body = ai_result.get("sections", "")
    chunks = split_markdown(body)
    for idx, chunk in enumerate(chunks, 1):
        title = f"Fuel Cell Report {idx} - {today_full}" if len(chunks) > 1 else f"Fuel Cell Report - {today_full}"
        push(title, chunk, "grey", dry)
        print(f"  {title} OK" if not dry else f"  {title} (dry)")

    # Cards: Sources (region-grouped)
    for title, content, color in build_source_cards(articles, today):
        push(title, content, color, dry)
        print(f"  {title} OK" if not dry else f"  {title} (dry)")

    print("Done!")


if __name__ == "__main__":
    main()
