#!/usr/bin/env python3
# -*- coding: utf-8 -*-
'''每日 Hacker News 新闻推送到飞书群'''

import json, os, urllib.request, hmac, hashlib, base64, time as time_module

WEBHOOK_URL = os.environ['FEISHU_WEBHOOK_URL']
SECRET = os.environ['FEISHU_SECRET']
COUNT = 10

def fetch_hn(count=10):
    top_url = 'https://hacker-news.firebaseio.com/v0/topstories.json'
    with urllib.request.urlopen(top_url, timeout=15) as resp:
        story_ids = json.loads(resp.read().decode())[:count]
    stories = []
    for sid in story_ids:
        try:
            item_url = f'https://hacker-news.firebaseio.com/v0/item/{sid}.json'
            with urllib.request.urlopen(item_url, timeout=10) as resp:
                item = json.loads(resp.read().decode())
            stories.append({
                'title': item.get('title', 'No Title'),
                'url': item.get('url', f'https://news.ycombinator.com/item?id={sid}'),
                'score': item.get('score', 0),
            })
        except Exception:
            continue
    return stories

def build_message(stories):
    from datetime import datetime
    today = datetime.now().strftime('%Y年%m月%d日')
    lines = [
        f'📰 **每日新闻速递** — {today}',
        f'来源: Hacker News  |  共 {len(stories)} 条',
        '',
    ]
    medals = ['🥇', '🥈', '🥉']
    for i, s in enumerate(stories):
        prefix = medals[i] if i < 3 else f'{i+1}.'
        lines.append(f'{prefix} [{s["title"]}]({s["url"]})  👍{s["score"]}')
    lines.extend(['', '---', '🤖 由 GitHub Actions 自动推送'])
    return {
        'msg_type': 'interactive',
        'card': {
            'header': {
                'title': {'tag': 'plain_text', 'content': f'📰 每日新闻速递 — {today}'},
                'template': 'blue',
            },
            'elements': [{'tag': 'markdown', 'content': '\n'.join(lines)}],
        },
    }

def send(payload):
    ts = str(int(time_module.time()))
    sign_key = (ts + '\n' + SECRET).encode('utf-8')
    sig = base64.b64encode(hmac.new(sign_key, b'', hashlib.sha256).digest()).decode()
    url = f'{WEBHOOK_URL}?timestamp={ts}&sign={sig}'
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json; charset=utf-8'}, method='POST')
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode())
    if result.get('code') != 0:
        raise Exception(f'Feishu error: {result}')
    print('[OK] Pushed to Feishu')

if __name__ == '__main__':
    stories = fetch_hn(COUNT)
    print(f'Fetched {len(stories)} stories')
    payload = build_message(stories)
    send(payload)
    print('Done!')