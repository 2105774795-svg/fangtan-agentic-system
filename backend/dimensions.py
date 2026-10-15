"""覆盖度引擎：商户调研 7 维度模板 + 关键词分类 + 缺口提醒。

设计依据：03-方案设计/03-教练式设计-03-教练式设计-实习生赋能.md §2.2(d)
"摸排干净" = 每个维度都有实质内容落到转写里。
分类用关键词打分（免费、零延迟），LLM 精化留作 Phase 2。
"""
import time

# 商户调研 7 维度（每维度：关键词用于命中归档，subs 为标准子问题树）
MERCHANT_DIMS = [
    {"id": "profile", "name": "经营者画像", "icon": "👤",
     "keywords": ["入行", "干这行", "开店", "摆摊", "年轻", "老家", "学过", "手艺", "打工", "继承", "爸妈", "师傅"],
     "subs": ["您是怎么开始干这个的？", "干了多少年了？", "家里人支持吗？"]},
    {"id": "business", "name": "经营现状", "icon": "🏪",
     "keywords": ["流水", "营业额", "收入", "客流", "客人", "顾客", "单", "销量", "货", "品类", "进货", "旺季", "淡季", "一天"],
     "subs": ["现在一天流水大概多少？", "什么时段生意最好？", "淡旺季差多少？"]},
    {"id": "cost", "name": "成本结构", "icon": "💰",
     "keywords": ["房租", "租金", "人工", "工资", "成本", "水电", "物业费", "抽佣", "平台扣", "转让费", "押金", "进货价", "亏", "利润"],
     "subs": ["房租一个月多少？占流水几成？", "人工这块怎么算的？", "平台抽佣多少个点？"]},
    {"id": "competition", "name": "竞争与位置", "icon": "⚔️",
     "keywords": ["同行", "竞争", "隔壁", "对面", "档口", "位置", "人流", "电商", "直播", "网购", "外卖", "一条街", "商圈", "顾客被抢"],
     "subs": ["这条街同行多吗？大家活得怎么样？", "电商/外卖对你们影响大吗？", "您这个位置算好吗？"]},
    {"id": "policy", "name": "政策与环境", "icon": "🏛️",
     "keywords": ["政策", "补贴", "政府", "城管", "市场监管", "税", "办证", "消防", "疫情", "拆迁", "修路", "改造", "创文"],
     "subs": ["这两年政策对生意有影响吗？", "办过补贴吗？好不好申请？"]},
    {"id": "family", "name": "家庭与个人", "icon": "🏠",
     "keywords": ["老婆", "老公", "孩子", "上学", "房子", "贷款", "房贷", "家用", "养老", "回老家", "孩子帮", "夫妻"],
     "subs": ["家里谁帮着看店？", "收入主要花在哪儿？", "孩子将来接这个摊吗？"]},
    {"id": "outlook", "name": "变化与预期", "icon": "🔮",
     "keywords": ["最好", "最差", "最难", "打算", "明年", "以后", "转型", "扩张", "关店", "转让", "希望", "后悔", "如果"],
     "subs": ["这几年最好和最差的时候各是什么时候？", "明年有什么打算？", "后悔过吗？"]},
]

DIM_MAP = {d["id"]: d for d in MERCHANT_DIMS}
ALL_DIM_IDS = [d["id"] for d in MERCHANT_DIMS]


def classify(text: str) -> list[str]:
    """把一句话归档到 0~2 个维度。"""
    hits = [(sum(1 for k in d["keywords"] if k in text), d["id"])
            for d in MERCHANT_DIMS]
    hits = [h for h in hits if h[0] > 0]
    hits.sort(reverse=True)
    return [i for _, i in hits[:2]]


def coverage_report(utterance_dims: list[list[str]], asked_dims: set[str]) -> dict:
    """汇总覆盖情况。utterance_dims: 每句话命中的维度列表。"""
    covered = set(asked_dims)
    for ds in utterance_dims:
        covered.update(ds)
    missing = [i for i in ALL_DIM_IDS if i not in covered]
    return {
        "covered": sorted(covered),
        "missing": missing,
        "ratio": round(len(covered) / len(ALL_DIM_IDS), 3),
        "dims": [{"id": d["id"], "name": d["name"], "icon": d["icon"],
                  "covered": d["id"] in covered,
                  "subs": d["subs"] if d["id"] not in covered else []}
                 for d in MERCHANT_DIMS],
    }


def gap_suggestion_payload(coverage: dict) -> dict | None:
    """缺口提醒卡片：选第一个未覆盖维度，给它的第一个子问题。"""
    if not coverage["missing"]:
        return None
    if coverage.get("mode") == "outline":
        dim = next(item for item in coverage["dims"] if item["id"] == coverage["missing"][0])
        if coverage.get("engine") == "ai":
            return {"dim": dim["id"], "dim_name": "筛选提纲", "question": dim.get("follow_up") or dim["item"],
                    "outline_item": dim["item"], "why": dim.get("reason") or "根据逐字稿证据，这道问题还需要追问。"}
        return {"dim": dim["id"], "dim_name": "筛选提纲", "question": dim["item"],
                "outline_item": dim["item"], "why": "这道已筛选的问题尚未标记为已摸排"}
    dim = DIM_MAP[coverage["missing"][0]]
    others = "、".join(DIM_MAP[i]["name"] for i in coverage["missing"])
    return {
        "dim": dim["id"], "dim_name": dim["name"],
        "question": dim["subs"][0],
        "missing_names": others,
        "why": f"已覆盖 {int(coverage['ratio']*100)}%，「{others}」还没摸排到",
    }
