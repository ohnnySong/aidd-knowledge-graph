#!/usr/bin/env python3
"""一次性回填：给 papers.json 里已有论文补 related_nodes 图谱映射。"""
import os, re, json, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util
spec = importlib.util.spec_from_file_location("fp", os.path.join(os.path.dirname(os.path.abspath(__file__)), "fetch_papers.py"))
fp = importlib.util.module_from_spec(spec); spec.loader.exec_module(fp)

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "papers.json")
data = json.load(open(path, encoding="utf-8"))
names = fp.node_catalog()
cat = "；".join(f"{i+1}.{n}" for i, n in enumerate(names))

todo = []
for day in data.get("days", []):
    for p in day.get("papers", []):
        if "related_nodes" not in p:
            todo.append(p)
print(f"待回填 {len(todo)} 篇")
if not todo:
    sys.exit(0)

listing = "\n\n".join(f"[{i}] {p['title_en']}" for i, p in enumerate(todo))
prompt = (
    "你是 AI 药物发现（AIDD）领域的研究分析师。下面是一个论文列表（编号+标题）。\n"
    "请为每篇论文从「知识图谱节点清单」中挑出 0-2 个技术上最相关的节点编号"
    "（论及该方法/模型本身或直接技术相关才选，泛泛相关不选，没有合适的给空数组）。\n"
    f"节点清单：{cat}\n"
    "严格输出 JSON 数组：[{\"idx\": 编号, \"nodes\": [节点编号]}]\n\n"
    f"论文列表：\n{listing}"
)
out = fp.deepseek([{"role": "user", "content": prompt}], max_tokens=2000)
m = re.search(r"\[.*\]", out, re.S)
res = json.loads(m.group(0))
for r in res:
    p = todo[int(r["idx"])]
    p["related_nodes"] = [int(x) for x in (r.get("nodes") or []) if 1 <= int(x) <= len(names)][:2]
    print(f"  {p['title_en'][:60]} -> {p['related_nodes']}")

json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("回填完成")
