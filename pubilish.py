#!/usr/bin/env python3
"""
风氢扬·每日氢能产业情报简报 - 飞书自动推送
Optimized for: 燃料电池系统 / 氢能叉车 / 冷链物流车 / 加氢站 / 固定发电 / 风电制氢
"""

import json
import os
import re
import urllib.request
import urllib.parse
import base64
import hmac
import hashlib
import xml.etree.ElementTree as ET
import time as time_module
from datetime import datetime, timedelta, timezone

# ─────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────
WEBHOOK_URL   = os.environ["FEISHU_WEBHOOK_URL"]
FEISHU_SECRET = os.environ["FEISHU_SECRET"]
AI_TOKEN      = os.environ.get("AI_TOKEN", os.environ.get("GITHUB_TOKEN", ""))
AI_ENDPOINT   = os.environ.get("AI_ENDPOINT", "https://models.inference.ai.azure.com/chat/completions")
AI_MODEL      = os.environ.get("AI_MODEL", "gpt-4o")

MAX_ARTICLES   = 30
NEWS_HOURS     = 24  # 只取最近N小时的新闻
DRY_RUN        = bool(os.environ.get("DRY_RUN"))

# ─────────────────────────────────────────────
# 搜索查询 - 针对风氢扬业务线定制
# ─────────────────────────────────────────────
QUERIES = [
    # === 政策与补贴 ===
    ("氢燃料电池 政策 补贴 示范城市群 2026", "zh-CN", "CN"),
    ("加氢站 建设 规划 审批 2026", "zh-CN", "CN"),
    ("hydrogen fuel cell policy subsidy regulation 2026", "en-US", "US"),
    ("Brennstoffzelle Wasserstoff Förderung Politik EU", "de-DE", "DE"),

    # === 叉车与物料搬运（核心业务） ===
    ("hydrogen fuel cell forklift warehouse logistics", "en-US", "US"),
    ("氢能叉车 仓储 物流 物料搬运", "zh-CN", "CN"),
    ("KION Linde hydrogen forklift fuel cell", "en-GB", "GB"),

    # === 商用车与冷链 ===
    ("fuel cell truck cold chain refrigerated vehicle China", "en-US", "US"),
    ("氢燃料电池 冷藏车 冷链 物流车 重卡", "zh-CN", "CN"),

    # === 技术与材料 ===
    ("fuel cell stack membrane electrode catalyst breakthrough 2026", "en-US", "US"),
    ("燃料电池 膜电极 质子交换膜 催化剂 双极板 突破", "zh-CN", "CN"),
    ("PEM electrolyzer green hydrogen wind power cost", "en-US", "US"),

    # === 竞争对手 ===
    ("亿华通 国鸿氢能 重塑科技 捷氢科技 燃料电池", "zh-CN", "CN"),
    ("Toyota Hyundai Ballard Plug Power fuel cell strategy", "en-US", "US"),

    # === 市场与成本 ===
    ("氢气价格 绿氢 成本 加氢站 运营 2026", "zh-CN", "CN"),
    ("hydrogen price green hydrogen cost refueling station 2026", "en-US", "US"),

    # === 固定发电与储能 ===
    ("hydrogen fuel cell stationary power backup generator", "en-US", "US"),
    ("氢能 固定式发电 备用电源 热电联供", "zh-CN", "CN"),
]

# 需要过滤的低质量关键词
SKIP_KEYWORDS = [
    "stock price", "share price", "sponsored", "advertisement",
    "股价", "涨停", "跌停", "广告", "推广",
]


# ─────────────────────────────────────────────
# 网络工具
# ─────────────────────────────────────────────
def http_get(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def http_post_json(url, payload, headers=None, timeout=120):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    h = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


# ─────────────────────────────────────────────
# 新闻搜索
# ─────────────────────────────────────────────
def parse_pubdate(date_str):
    """解析 RSS 日期格式"""
    try:
        return datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S %Z")
    except (ValueError, TypeError):
        return datetime.min


def is_within_hours(pubdate_str, hours=NEWS_HOURS):
    """判断新闻是否在时间窗口内"""
    dt = parse_pubdate(pubdate_str)
    if dt == datetime.min:
        return True  # 无法解析时保留
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return (now - dt) < timedelta(hours=hours)


def search_google_news(query, hl, gl, max_results=50):
    """搜索 Google News RSS"""
    ceid_map = {
        "CN": "CN:zh-Hans", "US": "US:en", "GB": "GB:en",
        "JP": "JP:ja", "DE": "DE:de", "FR": "FR:fr",
    }
    ceid = ceid_map.get(gl, f"{gl}:{hl.split('-')[0]}")
    rss_url = (
        f"https://news.google.com/rss/search?"
        f"q={urllib.parse.quote(query)}&hl={hl}&gl={gl}&ceid={ceid}"
    )

    xml_data = http_get(rss_url)
    root = ET.fromstring(xml_data)
    results = []

    for item in root.findall(".//item"):
        t = item.find("title")
        l = item.find("link")
        s = item.find("source")
        p = item.find("pubDate")

        title = t.text.strip() if t is not None and t.text else ""
        link = l.text if l is not None else ""
        source = s.text.strip() if s is not None and s.text else "Unknown"
        pubdate = p.text if p is not None else ""

        # 过滤
        if not title:
            continue
        title_lower = title.lower()
        if any(w in title_lower for w in SKIP_KEYWORDS):
            continue
        if not is_within_hours(pubdate):
            continue

        results.append({
            "title": title,
            "url": link,
            "source": source,
            "date": pubdate,
            "region": gl,
        })
        if len(results) >= max_results:
            break

    return results


def search_all():
    """执行全部搜索并去重"""
    seen_titles = set()
    seen_urls = set()
    all_results = []

    for query, hl, gl in QUERIES:
        try:
            results = search_google_news(query, hl, gl)
            print(f"  [{gl}] \"{query[:30]}...\" -> {len(results)} results")
            for r in results:
                # 双重去重：标题前80字 + URL
                title_key = r["title"][:80].lower()
                url_key = r["url"]
                if title_key not in seen_titles and url_key not in seen_urls:
                    seen_titles.add(title_key)
                    seen_urls.add(url_key)
                    all_results.append(r)
        except Exception as e:
            print(f"  [WARN] [{gl}] {query[:30]}: {e}")
        time_module.sleep(0.5)  # 避免请求过快

    # 按时间排序（最新在前）
    all_results.sort(key=lambda r: parse_pubdate(r["date"]), reverse=True)
    return all_results[:MAX_ARTICLES]


# ─────────────────────────────────────────────
# AI 分析
# ─────────────────────────────────────────────
def ai_analyze(articles):
    """调用 AI 生成结构化情报分析"""
    # 构建新闻列表文本
    region_flags = {"CN": "🇨🇳", "US": "🇺🇸", "GB": "🇬🇧", "JP": "🇯🇵", "DE": "🇩🇪", "FR": "🇫🇷"}
    articles_text = ""
    for i, a in enumerate(articles, 1):
        flag = region_flags.get(a["region"], "🌐")
        articles_text += f"\n{i}. {flag} [{a['source']}] {a['title']}"

    prompt = f"""你是风氢扬氢能科技公司的首席产业分析师。风氢扬主营：燃料电池系统（80-256kW）、氢能叉车、冷链物流车、加氢站、固定式发电、风电制氢。客户包括凯傲/林德、天顺风能等。

基于以下 {len(articles)} 条今日全球新闻，输出严格 JSON：

{articles_text}

输出 JSON 字段：

{{
  "headline": {{
    "signal": "今日最关键信号（≤40字）",
    "opportunity": "最大机会（≤40字）",
    "risk": "最大风险（≤40字）"
  }},
  "key_points": ["5条今日重点，格式：[CN/欧美/日韩] 一句话"],
  "sections": {{
    "policy": "## 一、政策与监管信号\\n\\n分中国/欧美/日韩。写明：谁、做了什么、具体数字、影响。区分实质利好与表态。",
    "technology": "## 二、技术与产业化进展\\n\\n写明：企业、突破内容、性能指标、阶段（实验室/中试/量产）。与风氢扬可对标标注【可对标】。",
    "competition": "## 三、竞争格局与企业动向\\n\\n点名企业，分析战略动向。分「机会」和「威胁」两维度。",
    "market": "## 四、市场与需求动态\\n\\n氢气价格、订单、加氢站进度、示范项目。",
    "action": "## 五、行动建议\\n\\n4-5条。每条格式：\\n- 🔴/🟡/🟢 [优先级高/中/低] 具体行动 | 依据 | 关联业务线"
  }},
  "risk_alert": "如有重大风险（安全事故/政策转向/供应链断裂/竞品威胁），写1-2句预警。无则写'今日无重大风险预警'。",
  "sources_summary": "今日覆盖X个区域，涉及Y个信息源，Z条有效新闻。"
}}

要求：
- 只输出 JSON，不要 markdown 代码块
- 语言：中文为主，专有名词保留英文
- 风格：精炼、专业、只说实质内容，不要套话
- 如果某个板块今日无相关新闻，写"今日该领域无重大动态"即可"""

    if not AI_TOKEN:
        return {"raw": True, "text": "⚠️ AI_TOKEN 未配置，无法生成分析。请设置环境变量 AI_TOKEN。"}

    messages = [
        {"role": "system", "content": "你是资深氢能产业分析师，服务于风氢扬公司决策层。输出精炼、专业、可操作。严格 JSON 格式。"},
        {"role": "user", "content": prompt},
    ]

    # 第一次尝试：结构化 JSON
    try:
        result = http_post_json(AI_ENDPOINT, {
            "model": AI_MODEL,
            "messages": messages,
            "max_tokens": 4500,
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }, headers={"Authorization": f"Bearer {AI_TOKEN}"})

        content = result["choices"][0]["message"]["content"]
        data = json.loads(content)
        return {"raw": False, "data": data}

    except Exception as e:
        print(f"  [WARN] Structured JSON failed: {e}")

    # 降级：纯文本 Markdown
    try:
        result = http_post_json(AI_ENDPOINT, {
            "model": AI_MODEL,
            "messages": [
                {"role": "system", "content": "你是资深氢能产业分析师。输出 Markdown 格式情报简报。"},
                {"role": "user", "content": f"基于以下新闻撰写五段式情报简报（政策/技术/竞争/市场/行动建议）：\n{articles_text}"},
            ],
            "max_tokens": 3500,
            "temperature": 0.3,
        }, headers={"Authorization": f"Bearer {AI_TOKEN}"})
        return {"raw": True, "text": result["choices"][0]["message"]["content"]}

    except Exception as e2:
        return {"raw": True, "text": f"⚠️ AI 分析完全失败: {e2}"}


# ─────────────────────────────────────────────
# 飞书卡片构建
# ─────────────────────────────────────────────
def build_summary_card(ai_result, today_full, article_count, elapsed_sec):
    """构建摘要卡片（第一张卡）"""
    title = f"🔋 风氢扬·氢能情报日报 | {today_full}"

    if ai_result.get("raw"):
        content = (
            f"**📊 今日概览**：{article_count} 条有效新闻\n\n"
            f"⚠️ 结构化摘要生成失败，详见正文卡片。\n\n"
            f"---\n*生成耗时 {elapsed_sec:.1f}s*"
        )
        return title, content, "red"

    data = ai_result["data"]
    headline = data.get("headline", {})
    key_points = data.get("key_points", [])
    risk_alert = data.get("risk_alert", "")
    sources_summary = data.get("sources_summary", "")

    lines = []

    # 今日定调
    lines.append("**📌 今日定调**\n")
    if isinstance(headline, dict):
        lines.append(f"🔴 **最关键信号**：{headline.get('signal', '-')}")
        lines.append(f"🟢 **最大机会**：{headline.get('opportunity', '-')}")
        lines.append(f"⚠️ **最大风险**：{headline.get('risk', '-')}")
    else:
        # 兼容旧格式（字符串）
        for i, line in enumerate(str(headline).split("\n")):
            labels = ["🔴 最关键信号", "🟢 最大机会", "⚠️ 最大风险"]
            label = labels[i] if i < 3 else f"第{i+1}点"
            lines.append(f"{label}：{line.strip()}")

    lines.append("")

    # 风险预警（如果有）
    if risk_alert and "无重大风险" not in risk_alert:
        lines.append(f"**🚨 风险预警**：{risk_alert}\n")

    # 今日重点
    lines.append("**📋 今日重点**\n")
    for i, kp in enumerate(key_points, 1):
        lines.append(f"{i}. {kp}")

    lines.append("")
    lines.append("---")
    lines.append(f"*{sources_summary} | 生成耗时 {elapsed_sec:.1f}s*")

    return title, "\n".join(lines), "blue"


def build_section_cards(ai_result, today_full):
    """构建正文分段卡片"""
    cards = []

    if ai_result.get("raw"):
        body = ai_result.get("text", "")
        chunks = split_markdown(body)
        for idx, chunk in enumerate(chunks, 1):
            t = f"📄 情报正文 ({idx}/{len(chunks)}) | {today_full}" if len(chunks) > 1 else f"📄 情报正文 | {today_full}"
            cards.append((t, chunk, "grey"))
        return cards

    data = ai_result["data"]
    sections = data.get("sections", {})

    if isinstance(sections, dict):
        # 新版结构化格式
        section_configs = [
            ("policy", "📜 政策与监管", "indigo"),
            ("technology", "🔬 技术与产业化", "turquoise"),
            ("competition", "⚔️ 竞争格局", "orange"),
            ("market", "📊 市场与需求", "green"),
            ("action", "🎯 行动建议", "red"),
        ]
        for key, label, color in section_configs:
            content = sections.get(key, "")
            if content and "无重大动态" not in content:
                cards.append((f"{label} | {today_full}", content, color))
    elif isinstance(sections, str):
        # 兼容旧版（整段字符串）
        chunks = split_markdown(sections)
        for idx, chunk in enumerate(chunks, 1):
            t = f"📄 情报正文 ({idx}/{len(chunks)}) | {today_full}"
            cards.append((t, chunk, "grey"))

    return cards


def build_source_cards(articles, today):
    """构建信源列表卡片（按区域分组）"""
    region_flags = {"CN": "🇨🇳", "US": "🇺🇸", "GB": "🇬🇧", "JP": "🇯🇵", "DE": "🇩🇪", "FR": "🇫🇷"}
    bucket_order = [
        ("🇨🇳 中国", {"CN"}),
        ("🇺🇸🇬🇧🇩🇪🇫🇷 欧美", {"US", "GB", "DE", "FR"}),
        ("🇯🇵 日韩", {"JP", "KR"}),
        ("🌐 其他", None),
    ]
    buckets = {name: [] for name, _ in bucket_order}
    for a in articles:
        for name, regs in bucket_order:
            if regs is None or a["region"] in regs:
                buckets[name].append(a)
                break

    cards = []
    current = ""
    current_len = 0
    card_idx = 1

    def flush():
        nonlocal current, current_len, card_idx
        if current.strip():
            title = f"📰 信源列表 ({card_idx}) | {today}"
            cards.append((title, current.strip(), "wathet"))
            card_idx += 1
        current = ""
        current_len = 0

    global_idx = 0
    for name, _ in bucket_order:
        items = buckets[name]
        if not items:
            continue
        header = f"**{name}** ({len(items)}条)\n\n"
        if current_len + len(header) > 4000 and current:
            flush()
        current += header
        current_len += len(header)

        for a in items:
            global_idx += 1
            flag = region_flags.get(a["region"], "🌐")
            t = a["title"][:60] + ("…" if len(a["title"]) > 60 else "")
            line = f"{global_idx}. {flag} [{t}]({a['url']})\n   *{a['source']}*\n\n"
            if current_len + len(line) > 4000:
                flush()
            current += line
            current_len += len(line)

    flush()
    return cards


def split_markdown(content, limit=4200):
    """将长文本按段落分割为多张卡片"""
    if not content:
        return []
    if len(content) <= limit:
        return [content]

    chunks = []
    current = ""
    for para in content.split("\n\n"):
        seg = para + "\n\n"
        if len(current) + len(seg) > limit and current:
            chunks.append(current.rstrip())
            current = ""
        current += seg
    if current.strip():
        chunks.append(current.rstrip())

    # 处理超长单段
    final = []
    for c in chunks:
        if len(c) <= limit:
            final.append(c)
        else:
            for i in range(0, len(c), limit):
                final.append(c[i:i + limit])
    return final


# ─────────────────────────────────────────────
# 飞书推送
# ─────────────────────────────────────────────
def send_card(title, content, color="blue"):
    """发送飞书交互卡片，带签名和重试"""
    card_data = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title[:80]},
                "template": color,
            },
            "elements": [
                {"tag": "markdown", "content": content},
            ],
        },
    }
    data = json.dumps(card_data, ensure_ascii=False).encode("utf-8")

    for attempt in range(3):
        try:
            ts = str(int(time_module.time()))
            string_to_sign = f"{ts}\n{FEISHU_SECRET}"
            hmac_code = hmac.new(
                string_to_sign.encode("utf-8"),
                digestmod=hashlib.sha256,
            ).digest()
            sign = base64.b64encode(hmac_code).decode("utf-8")

            url = f"{WEBHOOK_URL}&timestamp={ts}&sign={sign}"
            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode())

            if result.get("code") == 0:
                return True
            print(f"  [WARN] Feishu returned code={result.get('code')}: {result.get('msg')}")

        except Exception as e:
            print(f"  [WARN] Send attempt {attempt+1} failed: {e}")

        if attempt < 2:
            time_module.sleep(2 * (attempt + 1))

    return False


def push(title, content, color):
    """推送或 dry-run 打印"""
    if DRY_RUN:
        print(f"\n{'='*60}")
        print(f"📇 {title} [{color}]")
        print(f"{'='*60}")
        print(content[:500] + ("..." if len(content) > 500 else ""))
        print()
        return True
    ok = send_card(title, content, color)
    time_module.sleep(1.2)  # 飞书限流保护
    return ok


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────
def main():
    start_time = time_module.time()
    td = datetime.now()
    today = td.strftime("%m.%d")
    today_full = td.strftime("%Y-%m-%d %A")

    print(f"{'='*50}")
    print(f"🔋 风氢扬·氢能情报日报 | {today_full}")
    print(f"{'='*50}")

    # Step 1: 搜索
    print("\n[1/3] 🔍 搜索全球氢能新闻...")
    articles = search_all()
    print(f"  ✅ 共获取 {len(articles)} 条有效新闻")

    if not articles:
        push("🔋 风氢扬·氢能情报日报", "今日暂无燃料电池相关新闻。", "red")
        return

    # Step 2: AI 分析
    print("\n[2/3] 🤖 AI 分析中...")
    ai_result = ai_analyze(articles)
    print(f"  ✅ 分析完成 (raw={ai_result.get('raw')})")

    elapsed = time_module.time() - start_time

    # Step 3: 推送
    print(f"\n[3/3] 📤 推送飞书... (耗时 {elapsed:.1f}s)")

    # Card 1: 摘要
    title, content, color = build_summary_card(ai_result, today_full, len(articles), elapsed)
    push(title, content, color)
    print("  ✅ 摘要卡片")

    # Card 2+: 正文分段
    section_cards = build_section_cards(ai_result, today_full)
    for t, c, col in section_cards:
        push(t, c, col)
        print(f"  ✅ {t}")

    # Card N: 信源列表
    source_cards = build_source_cards(articles, today)
    for t, c, col in source_cards:
        push(t, c, col)
        print(f"  ✅ {t}")

    total_time = time_module.time() - start_time
    print(f"\n{'='*50}")
    print(f"🎉 完成！共推送 {1 + len(section_cards) + len(source_cards)} 张卡片，总耗时 {total_time:.1f}s")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
