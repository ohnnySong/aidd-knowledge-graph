#!/usr/bin/env python3
"""每日 AIDD 论文管线：
1. arXiv API + bioRxiv API 拉取近 24-48h 新论文
2. 关键词初筛 → DeepSeek 精选 Top 3（AIDD 相关性）并生成中文标题
3. 下载 PDF → pypdf 提取 Introduction → DeepSeek 全文翻译成中文
4. 合并进 papers.json（保留 30 天）
"""
import os, re, json, time, datetime, sys
import requests

DEEPSEEK_KEY = os.environ["DEEPSEEK_API_KEY"]
DS_URL = "https://api.deepseek.com/chat/completions"
KEEP_DAYS = 30
TOP_N = 3

ARXIV_CATS = ["q-bio.BM", "q-bio.QM", "cs.LG"]
AIDD_KEYWORDS = [
    "drug", "molecule", "molecular", "ligand", "protein", "docking", "binding affinity",
    "inhibitor", "antibody", "peptide", "enzyme", "kinase", "receptor", "compound",
    "pharmacokinetic", "admet", "toxicity", "qsar", "de novo", "generative model",
    "diffusion", "structure prediction", "folding", "virtual screening", "lead optimization",
    "retrosynthesis", "chemical synthesis", "bioactivity", "potency", "selectivity",
    "cryptic pocket", "allosteric", "degrader", "protac", "macrocycle", "small molecule",
    "clinical trial", "drug discovery", "drug design", "drug development", "therapeutic",
    "free energy", "fep", "molecular dynamics", "force field", "cryo-em", "fragment",
]

TZ = datetime.timezone(datetime.timedelta(hours=8), "Asia/Shanghai")
TODAY = datetime.datetime.now(TZ).date()


def log(msg):
    print(f"[{datetime.datetime.now(TZ).strftime('%H:%M:%S')}] {msg}", flush=True)


def deepseek(messages, max_tokens=4000, temperature=0.2):
    r = requests.post(DS_URL, headers={"Authorization": f"Bearer {DEEPSEEK_KEY}"}, json={
        "model": "deepseek-chat", "messages": messages,
        "max_tokens": max_tokens, "temperature": temperature,
    }, timeout=180)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def fetch_arxiv():
    """拉取最近 2 天 arXiv 目标分类新论文。"""
    end = TODAY + datetime.timedelta(days=1)
    start = TODAY - datetime.timedelta(days=2)
    dr = f"submittedDate:[{start.strftime('%Y%m%d')}0000 TO {end.strftime('%Y%m%d')}2359]"
    cats = " OR ".join(f"cat:{c}" for c in ARXIV_CATS)
    params = {
        "search_query": f"({cats}) AND {dr}",
        "sortBy": "submittedDate", "sortOrder": "descending",
        "start": 0, "max_results": 200,
    }
    r = requests.get("http://export.arxiv.org/api/query", params=params, timeout=60)
    r.raise_for_status()
    text = r.text
    entries = re.findall(r"<entry>(.*?)</entry>", text, re.S)
    papers = []
    for e in entries:
        aid = re.search(r"<id>http://arxiv.org/abs/([^<]+)</id>", e)
        title = re.search(r"<title>(.*?)</title>", e, re.S)
        summary = re.search(r"<summary>(.*?)</summary>", e, re.S)
        authors = re.findall(r"<name>([^<]+)</name>", e)
        if not (aid and title and summary):
            continue
        papers.append({
            "id": aid.group(1).strip(),
            "title": re.sub(r"\s+", " ", title.group(1)).strip(),
            "abstract": re.sub(r"\s+", " ", summary.group(1)).strip(),
            "authors": ", ".join(authors[:5]) + (" et al." if len(authors) > 5 else ""),
            "url": f"https://arxiv.org/abs/{aid.group(1).strip()}",
            "pdf": f"https://arxiv.org/pdf/{aid.group(1).strip()}",
            "source": "arXiv",
        })
    log(f"arXiv 拉到 {len(papers)} 篇")
    return papers


def fetch_biorxiv():
    """拉取最近 2 天 bioRxiv 新论文（仅第一页 100 篇，够用了）。"""
    start = TODAY - datetime.timedelta(days=2)
    url = f"https://api.biorxiv.org/details/biorxiv/{start.isoformat()}/{TODAY.isoformat()}/0"
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        coll = r.json().get("collection", [])
    except Exception as ex:
        log(f"bioRxiv 拉取失败（跳过）: {ex}")
        return []
    papers = []
    for it in coll:
        doi = it.get("doi", "")
        if not doi:
            continue
        papers.append({
            "id": doi,
            "title": re.sub(r"\s+", " ", it.get("title", "")).strip(),
            "abstract": re.sub(r"\s+", " ", it.get("abstract", "")).strip(),
            "authors": re.sub(r"\s+", " ", it.get("authors", "")).strip(),
            "url": f"https://www.biorxiv.org/content/{doi}",
            "pdf": f"https://www.biorxiv.org/content/{doi}v1.full.pdf",
            "source": "bioRxiv",
        })
    log(f"bioRxiv 拉到 {len(papers)} 篇")
    return papers


def keyword_prefilter(papers, cap=60):
    """关键词初筛，控制送给 LLM 的候选量。"""
    scored = []
    for p in papers:
        blob = (p["title"] + " " + p["abstract"]).lower()
        hits = sum(1 for kw in AIDD_KEYWORDS if kw in blob)
        if hits >= 2:
            scored.append((hits, p))
    scored.sort(key=lambda x: -x[0])
    picked = [p for _, p in scored[:cap]]
    log(f"关键词初筛后剩 {len(picked)} 篇候选")
    return picked


def node_catalog():
    """从 index.html 的 STAGES 提取 56 个节点名，给 LLM 做映射候选。"""
    html_path = os.path.join(os.path.dirname(__file__), "..", "index.html")
    try:
        html = open(html_path, encoding="utf-8").read()
        seg = html[html.index("const STAGES = ["):html.index("let nodeIdx")]
        names = re.findall(r"(?<![A-Za-z])n:'((?:[^'\\]|\\.)*)'", seg)
        names = [re.sub(r"（[^）]*）", "", s).strip() for s in names]
        return names
    except Exception as ex:
        log(f"节点目录提取失败（跳过映射）: {ex}")
        return []


def llm_pick(candidates):
    """DeepSeek 从候选中选 AIDD 相关性最强的 Top N，生成中文标题并映射图谱节点。"""
    if not candidates:
        return []
    listing = "\n\n".join(
        f"[{i}] {p['title']}\n{p['abstract'][:600]}" for i, p in enumerate(candidates)
    )
    names = node_catalog()
    node_note = ""
    node_schema = ""
    if names:
        cat = "；".join(f"{i+1}.{n}" for i, n in enumerate(names))
        node_note = (
            "另外，为每篇论文从下面的「知识图谱节点清单」中挑出 0-2 个技术上最相关的节点编号"
            "（论及该方法/模型本身或直接技术相关才选，泛泛相关不选，没有合适的给空数组）。\n"
            f"节点清单：{cat}\n"
        )
        node_schema = ', "nodes": [节点编号]'
    prompt = (
        "你是 AI 药物发现（AIDD）领域的研究分析师。下面是今日新发布的论文列表（编号+标题+摘要节选）。\n"
        f"请选出与「AI/计算方法驱动药物研发」相关性最强、学术或产业价值最高的 {TOP_N} 篇。\n"
        "评判标准：方法新颖性、与制药研发环节（靶点发现/分子生成/结构预测/ADMET/临床）的直接相关性、机构影响力。\n"
        "纯机器学习理论、与生物医药无关的论文不要选。\n"
        f"{node_note}"
        "严格输出 JSON 数组，不要输出其他内容：\n"
        f'[{{"idx": 编号, "title_zh": "中文标题（准确、书面）"{node_schema}}}]\n\n'
        f"论文列表：\n{listing}"
    )
    out = deepseek([{"role": "user", "content": prompt}], max_tokens=1000)
    m = re.search(r"\[.*\]", out, re.S)
    picks = json.loads(m.group(0))
    result = []
    for pk in picks[:TOP_N]:
        p = dict(candidates[int(pk["idx"])])
        p["title_zh"] = pk.get("title_zh", "")
        nl = [int(x) for x in (pk.get("nodes") or []) if isinstance(x, (int, float)) and 1 <= int(x) <= len(names)]
        p["related_nodes"] = nl[:2]
        result.append(p)
    log(f"DeepSeek 精选 {len(result)} 篇")
    return result


def extract_intro(pdf_url):
    """下载 PDF，pypdf 解析，提取 Introduction 章节。"""
    from pypdf import PdfReader
    import io
    r = requests.get(pdf_url := pdf_url, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    reader = PdfReader(io.BytesIO(r.content))
    full = "\n".join((pg.extract_text() or "") for pg in reader.pages[:15])
    if len(full) < 500:
        return None
    # 定位 Introduction 到下一章节
    m = re.search(r"(?im)^\s*(?:1[.\s]*)?introduction\s*$", full, re.M)
    if not m:
        m = re.search(r"introduction", full, re.I)
    if not m:
        return full[:6000]
    start = m.end()
    nxt = re.search(
        r"(?im)^\s*(?:2[.\s]+|ii[.\s]+)(related work|background|methods|materials|results|approach|model|preliminar)",
        full[start:],
    )
    intro = full[start:start + nxt.start()] if nxt else full[start:start + 8000]
    intro = re.sub(r"\s+", " ", intro).strip()
    return intro[:8000] if len(intro) > 300 else None


def translate_intro(paper):
    """DeepSeek 全文翻译 Introduction。"""
    try:
        intro = extract_intro(paper["pdf"])
    except Exception as ex:
        log(f"  PDF 解析失败 {paper['id']}: {ex}")
        intro = None
    if not intro:
        log(f"  未能提取 Introduction，改用摘要翻译: {paper['id']}")
        intro = "Abstract\n" + paper["abstract"]
        note = "abstract"
    else:
        note = "introduction"
    prompt = (
        "你是生物医药领域专业译者。把下面的论文 Introduction 完整翻译成中文。\n"
        "要求：忠实原文、书面学术中文、专业术语保留英文原词并括号注释（如 自由能微扰（FEP））；"
        "保留段落结构，每段之间空一行；不要加任何评论或省略。\n\n"
        f"论文标题：{paper['title']}\n\n原文：\n{intro}"
    )
    zh = deepseek([{"role": "user", "content": prompt}], max_tokens=4000, temperature=0.1)
    return note, intro, zh_trim(zh)


def zh_trim(s):
    return s.strip()


def main():
    papers = fetch_arxiv() + fetch_biorxiv()
    # 去重
    seen, uniq = set(), []
    for p in papers:
        k = re.sub(r"\W+", "", p["title"].lower())[:80]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(p)
    candidates = keyword_prefilter(uniq)
    picks = llm_pick(candidates)

    day_papers = []
    for p in picks:
        log(f"  处理: {p['title'][:60]}")
        try:
            note, intro_en, intro_zh = translate_intro(p)
        except Exception as ex:
            log(f"  翻译失败（跳过）: {ex}")
            continue
        day_papers.append({
            "title_zh": p["title_zh"], "title_en": p["title"],
            "authors": p["authors"], "source": p["source"],
            "url": p["url"], "pdf": p["pdf"],
            "text_type": note, "intro_zh": intro_zh,
            "related_nodes": p.get("related_nodes", []),
        })
        time.sleep(1)

    out_path = os.path.join(os.path.dirname(__file__), "..", "papers.json")
    data = {"days": []}
    if os.path.exists(out_path):
        try:
            data = json.load(open(out_path, encoding="utf-8"))
        except Exception:
            pass
    today_s = TODAY.isoformat()
    data["days"] = [d for d in data.get("days", []) if d.get("date") != today_s]
    if day_papers:
        data["days"].insert(0, {"date": today_s, "papers": day_papers})
    cutoff = (TODAY - datetime.timedelta(days=KEEP_DAYS)).isoformat()
    data["days"] = [d for d in data["days"] if d["date"] >= cutoff]
    data["updated"] = datetime.datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    json.dump(data, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"完成：{len(day_papers)} 篇写入 papers.json")


if __name__ == "__main__":
    main()
