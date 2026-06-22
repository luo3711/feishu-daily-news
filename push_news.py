#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
燃料电池汽车产业情报日报
基于 Google News RSS（免费，无需 API Key）
全球搜索 → 自动分类 → 信源标注 → 飞书推送
"""

import json, os, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
import hmac, hashlib, base64, time as time_module
from datetime import datetime, timedelta

# ── 配置 ────────────────────────────────────────────
WEBHOOK_URL   = os.environ["FEISHU_WEBHOOK_URL"]
FEISHU_SECRET = os.environ["FEISHU_SECRET"]

# 搜索查询 — 英文全球 + 中文国内
QUERIES = [
    ("燃料電気自動車+水素+政策+補助金",     "ja-JP",  "JP"),   # 日本
    ("fuel+cell+vehicle+policy+hydrogen",    "en-US",  "US"),   # 全球英文
    ("hydrogen+fuel+cell+FCEV+regulation",   "en-GB",  "GB"),   # 欧洲视角
    ("Brennstoffzelle+Fahrzeug+Politik",     "de-DE",  "DE"),   # 德国（欧洲氢能中心）
    ("pila+combustible+vehículo+hidrógeno",  "es-ES",  "ES"),   # 西班牙/拉美
    ("pile+combustible+véhicule+hydrogène",  "fr-FR",  "FR"),   # 法国
]
CN_QUERIES = [
    ("燃料电池汽车+政策+氢能+产业+补贴",          "zh-CN", "CN"),
    ("氢能+燃料电池+示范城市群+政策",              "zh-CN", "CN"),
]

MAX_RESULTS = 20
# ────────────────────────────────────────────────────


def search_google_news(query, hl, gl, max_results=50):
    """免费 Google News RSS 搜索"""
    ceid_map = {
        "CN": "CN:zh-Hans", "US": "US:en", "GB": "GB:en",
        "JP": "JP:ja", "DE": "DE:de", "FR": "FR:fr", "ES": "ES:es",
    }
    ceid = ceid_map.get(gl, f"{gl}:{hl.split('-')[0]}")
    rss_url = (
        f"https://news.google.com/rss/search?"
        f"q={urllib.parse.quote(query)}&hl={hl}&gl={gl}&ceid={ceid}"
    )
    req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        xml_data = resp.read().decode("utf-8")

    root = ET.fromstring(xml_data)
    results = []
    for item in root.findall(".//item"):
        title_el = item.find("title")
        link_el = item.find("link")
        source_el = item.find("source")
        pubdate_el = item.find("pubDate")

        title = title_el.text if title_el is not None else ""
        link = link_el.text if link_el is not None else ""
        source = source_el.text if source_el is not None else "Unknown"
        pubdate = pubdate_el.text if pubdate_el is not None else ""

        # 过滤无用内容
        skip_words = ["stock", "share price", "股", "advertisement", "sponsored"]
        if any(w in title.lower() for w in skip_words):
            continue

        results.append({
            "title": title.strip(),
            "url": link,
            "source": source.strip(),
            "date": pubdate,
            "region": gl,
        })
        if len(results) >= max_results:
            break
    return results


def search_all():
    """执行全部查询并合并去重"""
    seen = set()
    all_results = []

    for query, hl, gl in QUERIES + CN_QUERIES:
        try:
            results = search_google_news(query, hl, gl)
            print(f"  [{gl}] {query[:40]}... → {len(results)} results")
            for r in results:
                key = r["title"][:80]  # 用标题去重
                if key not in seen:
                    seen.add(key)
                    all_results.append(r)
        except Exception as e:
            print(f"  [WARN] {gl} failed: {e}")

    # 按日期排序（新的在前）
    def parse_date(r):
        try:
            return datetime.strptime(r["date"], "%a, %d %b %Y %H:%M:%S %Z")
        except Exception:
            return datetime.min
    all_results.sort(key=parse_date, reverse=True)

    return all_results[:MAX_RESULTS]


def classify(results):
    """自动分类"""
    categories = {
        "政策法规": [],
        "产业动态": [],
        "技术创新": [],
        "市场投资": [],
        "国际合作": [],
    }

    policy_kw = ["policy", "regulation", "subsidy", "incentive", "government",
                 "ban", "mandate", "law", "bill", "standard", "target",
                 "政策", "补贴", "法规", "标准", "政府", "国家", "目标", "规划", "示范"]
    tech_kw = ["technology", "breakthrough", "stack", "membrane", "catalyst",
               "efficiency", "durability", "performance",
               "技术", "突破", "效率", "催化剂", "膜", "电堆", "续航"]
    market_kw = ["investment", "funding", "market", "IPO", "stock", "acquisition",
                 "partnership", "revenue", "sales", "deployment",
                 "投资", "融资", "市场", "上市", "销售", "交付", "量产"]
    intl_kw = ["EU", "Europe", "China", "Japan", "Korea", "Germany", "US", "California",
               "agreement", "cooperation", "trade",
               "欧盟", "日本", "韩国", "德国", "美国", "合作", "国际", "全球"]

    for r in results:
        text = (r["title"] + " " + r["source"]).lower()
        if any(kw.lower() in text for kw in policy_kw):
            categories["政策法规"].append(r)
        elif any(kw.lower() in text for kw in intl_kw):
            categories["国际合作"].append(r)
        elif any(kw.lower() in text for kw in tech_kw):
            categories["技术创新"].append(r)
        elif any(kw.lower() in text for kw in market_kw):
            categories["市场投资"].append(r)
        else:
            categories["产业动态"].append(r)

    return {k: v for k, v in categories.items() if v}


def generate_report(categories):
    """生成 Markdown 报告"""
    today = datetime.now().strftime("%Y-%m-%d")
    total = sum(len(v) for v in categories.values())

    lines = [
        f"# 🔋 燃料电池汽车产业情报日报",
        f"",
        f"📅 **{today}**  |  📰 **{total} 条情报**  |  🌍 **全球信源**",
        f"",
        "---",
        "",
    ]

    # 按重要性排序
    priority = ["政策法规", "国际合作", "产业动态", "技术创新", "市场投资"]

    for cat in priority:
        items = categories.get(cat, [])
        if not items:
            continue
        region_flags = {
            "CN": "🇨🇳", "US": "🇺🇸", "GB": "🇬🇧", "JP": "🇯🇵",
            "DE": "🇩🇪", "FR": "🇫🇷", "ES": "🇪🇸",
        }
        lines.append(f"## 📌 {cat}（{len(items)}条）")
        lines.append("")
        for i, item in enumerate(items, 1):
            flag = region_flags.get(item["region"], "🌐")
            # 提取真实域名
            source_display = item["source"]
            if len(source_display) > 15:
                source_display = source_display[:15] + "…"
            lines.append(f"**{i}. {item['title']}**")
            lines.append(f"")
            lines.append(f"📅 {item['date'][:22]}  |  {flag} [{source_display}]({item['url']})")
            lines.append("")
        lines.append("---")
        lines.append("")

    lines += [
        "",
        "> 💡 **关于本报**",
        "> 基于 Google News 全球信源自动检索生成，覆盖中、日、韩、美、欧等主要市场。",
        "> 搜索范围包含各国政府公告、行业媒体、研究机构发布的最新资讯。",
        "> 仅供研究参考，不构成投资建议。",
        "",
        f"> 🤖 由 GitHub Actions 自动生成推送 | {today}",
    ]

    return "\n".join(lines)


def send_to_feishu(markdown, title):
    """飞书卡片推送，带重试"""
    for attempt in range(3):
        ts = str(int(time_module.time()))
        sign_key = (ts + "\n" + FEISHU_SECRET).encode("utf-8")
        sig = base64.b64encode(hmac.new(sign_key, b"", hashlib.sha256).digest()).decode()
        url = f"{WEBHOOK_URL}?timestamp={ts}&sign={sig}"

        content = markdown[:4800]
        if len(markdown) > 4800:
            content += "\n\n> ⚠️ 内容过长已截断"

        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "blue",
                },
                "elements": [{"tag": "markdown", "content": content}],
            },
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode())
            if result.get("code") == 0:
                print(f"[OK] Pushed to Feishu (attempt {attempt+1})")
                return
            else:
                print(f"[RETRY {attempt+1}] Feishu error: {result}")
                time_module.sleep(2)
        except Exception as e:
            print(f"[RETRY {attempt+1}] HTTP error: {e}")
            time_module.sleep(2)
    raise Exception("Feishu push failed after 3 retries")
    """飞书卡片推送"""
    ts = str(int(time_module.time()))
    sign_key = (ts + "\n" + FEISHU_SECRET).encode("utf-8")
    sig = base64.b64encode(hmac.new(sign_key, b"", hashlib.sha256).digest()).decode()
    url = f"{WEBHOOK_URL}?timestamp={ts}&sign={sig}"

    # 飞书卡片限制 ~5000 字符
    content = markdown[:4800]
    if len(markdown) > 4800:
        content += "\n\n> ⚠️ 内容过长已截断"

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue",
            },
            "elements": [{"tag": "markdown", "content": content}],
        },
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode())
    if result.get("code") != 0:
        raise Exception(f"Feishu error: {result}")
    print("[OK] Pushed to Feishu")


def main():
    print("🔋 Fuel Cell Intelligence Daily")
    print("=" * 50)
    print("Searching global sources...")
    results = search_all()

    if not results:
        print("No results, sending empty notice...")
        send_to_feishu("⚠️ 今日未检索到燃料电池汽车新情报。", "🔋 燃料电池情报日报")
        return

    print(f"\nTotal: {len(results)} unique results")
    print("Classifying...")
    categories = classify(results)
    for cat, items in categories.items():
        print(f"  {cat}: {len(items)} items")

    print("Generating report...")
    report = generate_report(categories)
    title = f'🔋 燃料电池汽车情报日报 — {datetime.now().strftime("%m.%d")}'
    send_to_feishu(report, title)
    print("[OK] Done!")


if __name__ == "__main__":
    main()