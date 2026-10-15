"""信号检测器：轻量规则，宁可漏报不可乱报。规则来自 02-访谈技巧库 1.2。"""
import re

# (信号类型, 编译正则, 说明)
RULES = [
    ("数字未展开", re.compile(
        r"(亏|赚|赔|成本|房租|流水|收入|利润|客单价|抽佣|欠|投|进货|囤货|押金|转让费|人工)"
        r"[^，。！？]{0,10}?([0-9]+(?:\.[0-9]+)?|[一二三四五六七八九十百千]+)\s*(万|千|块|元|k|K|%|个点)?"),
     "提到具体数字但未展开，建议追问细节"),
    ("人物未展开", re.compile(
        r"(有个|一位|认识个|老客户|老主顾|贵人|带我的|合伙人|供货商|上家|下家)"),
     "提到关键人物，可追问其人其事"),
    ("时间跳跃", re.compile(r"(后来|再后来|没过多久|之后没多久|那几年)"),
     "时间线跳跃，可问'那段时间具体发生了什么'"),
    ("情感词", re.compile(r"(特别|非常|真的)?(难|不容易|心酸|后悔|害怕|吓人|绝望|崩溃|开心|激动|睡不着觉)"),
     "出现情绪信号，先接住情绪再追问"),
    ("回避标记", re.compile(r"(这个嘛|不好说|不方便讲|别提了|那都过去了|反正|懂的都懂|你懂的)"),
     "受访者回避，建议换角度或侧面追问"),
]

FINANCE_TERMS = re.compile(r"(毛利率|净利润|市占率|复购率|客单价|坪效|周转|现金流)")


def detect(text: str):
    """返回命中的信号列表 [{type, matched, hint}]，按优先级去重（每条文本最多报 2 个）。"""
    hits = []
    for name, pattern, hint in RULES:
        m = pattern.search(text)
        if m:
            hits.append({"type": name, "matched": m.group(0), "hint": hint})
        if len(hits) >= 2:
            break
    if FINANCE_TERMS.search(text) and not any(h["type"] == "数字未展开" for h in hits):
        hits.append({"type": "财经术语", "matched": FINANCE_TERMS.search(text).group(0),
                     "hint": "专业术语出现，可请对方举例解释，方便读者理解"})
    return hits[:2]
