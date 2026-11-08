#!/usr/bin/env python3
# 把 aidd_detail_batch1-4.md 的六段式节点详稿注入 aidd-pages/index.html
import re, json, sys

BASE = '/Users/yunxiang/aunsure'
HTML = f'{BASE}/aidd-pages/index.html'

# ---------- 1. 解析四批 md ----------
SEC = {'① 原理概括':'principle','② 核心机制':'mechanism','③ 为什么有效':'why',
       '④ 代表工具与公司':'co','⑤ 局限':'lim','⑥ 关键论文':'papers'}
nodes = {}
for i in (1,2,3,4):
    text = open(f'{BASE}/aidd_detail_batch{i}.md', encoding='utf-8').read()
    # 按节点切块
    parts = re.split(r'^## 节点 (\d+)：(.+)$', text, flags=re.M)
    # parts: [前缀, num, title, body, num, title, body, ...]
    for j in range(1, len(parts), 3):
        num, title, body = int(parts[j]), parts[j+1].strip(), parts[j+2]
        d = {'_title': title}
        # 按小节切
        chunks = re.split(r'^\*\*([①②③④⑤⑥][^*]+)\*\*\s*$', body, flags=re.M)
        for k in range(1, len(chunks), 2):
            head, content = chunks[k].strip(), chunks[k+1]
            key = SEC.get(head)
            if key is None:
                print(f'!! 未识别小节: 节点{num} [{head}]'); continue
            # 去掉尾部的 *** 分隔线和说明
            content = re.split(r'^\*\*\*\s*$', content, flags=re.M)[0].strip()
            if key == 'papers':
                items = [re.sub(r'^[-*]\s*', '', l).strip()
                         for l in content.splitlines() if re.match(r'^\s*[-*]\s+', l)]
                d[key] = items
            else:
                d[key] = content
        nodes[num] = d

assert len(nodes) == 56, f'节点数不对: {len(nodes)}'
for n in range(1, 57):
    for k in ('principle','mechanism','why','co','lim','papers'):
        if k not in nodes[n]:
            print(f'!! 节点 {n} 缺字段 {k}')

# ---------- 2. 从 index.html 提取节点名并校验顺序 ----------
html = open(HTML, encoding='utf-8').read()
seg = html[html.index('const STAGES = ['):html.index('let nodeIdx')]
names = re.findall(r"(?<![A-Za-z])n:'((?:[^'\\]|\\.)*)'", seg)
names = [s.replace("\\'", "'") for s in names]
assert len(names) == 56, f'HTML 节点数不对: {len(names)}'
def norm(s):
    s = re.sub(r'\s+', '', s)
    s = re.sub(r'，\d{4}[^）]*）', '）', s)   # 去掉括号里的年份副标题
    return s
bad = 0
for n in range(1, 57):
    t, hn = norm(nodes[n]['_title']), norm(names[n-1])
    if not (t.startswith(hn) or hn.startswith(t)):
        print(f'!! 节点 {n} 名字对不上:\n   md:   {nodes[n]["_title"]}\n   html: {names[n-1]}')
        bad += 1
if bad:
    print(f'警告: {bad} 个名字不严格匹配（已按顺序对应，请人工核对上方列表）')

# ---------- 3. 生成 DETAILS ----------
def clean(s):
    if isinstance(s, str):
        return s.replace('\\$', '$')
    return s

det = {}
for n in range(1, 57):
    d = nodes[n]
    det[f'n{n-1}'] = {k: clean(d[k]) for k in ('principle','mechanism','why','co','lim','papers')}

js = 'const DETAILS = ' + json.dumps(det, ensure_ascii=False, indent=1) + ';\n\n'

# ---------- 4. 注入 ----------
if 'const DETAILS = ' in html:
    html = re.sub(r'const DETAILS = .*?\n\n(?=const STAGES)', js, html, flags=re.S)
else:
    html = html.replace('const STAGES = [', js + 'const STAGES = [', 1)
open(HTML, 'w', encoding='utf-8').write(html)
print(f'OK: 注入 {len(det)} 个节点详情，index.html 现 {len(html)} 字节')
