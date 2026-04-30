"""
Orchestrator for the image-based search pipeline.

Pipeline:
  1. If user attached an image → use those bytes directly.
  2. Else: query → Bing Images → list of candidate URLs (official brand
     domains and big retailers filtered out).
  3. For each candidate, download bytes and POST to Lovegobuy 1688 + Weidian
     in parallel. Stop at the first candidate that returns results from BOTH
     marketplaces. If none does, fall back to the candidate with the most
     total results.
  4. Filter out obviously irrelevant products (title doesn't contain any
     query keyword, English or CJK).
  5. Bucket by USD price percentiles (33/66).
"""

import asyncio
import logging
import re

from bot.services.google_images import download, get_image_candidates
from bot.services.lovegobuy import Product, search_1688, search_weidian

log = logging.getLogger(__name__)


# Tokens that are "generic categories" (any-brand product type).
# When the ONLY match in a product title is one of these, the product is
# probably not what the user is looking for (could be any brand of that type).
# These tokens are kept for matching but classified as WEAK.
_GENERIC_TOKENS: set[str] = {
    # Jewelry / accessories
    "ring", "rings", "necklace", "necklaces", "bracelet", "bracelets",
    "earring", "earrings", "earstud", "earstuds", "anklet", "pendant",
    "charm", "charms", "watch", "watches", "sunglasses", "glasses",
    "perfume", "fragrance", "wallet", "wallets", "bag", "bags",
    "backpack", "purse", "clutch", "belt", "belts", "cap", "caps",
    "hat", "hats", "scarf", "gloves", "socks", "tie",
    # Footwear types
    "shoes", "shoe", "sneakers", "sneaker", "boots", "boot",
    "sandals", "slides", "slipper", "slippers", "loafer", "loafers",
    "heels", "trainers",
    # Garments
    "hoodie", "hoodies", "sweater", "jumper", "shirt", "shirts",
    "tee", "tees", "tshirt", "tshirts", "polo", "tank",
    "pants", "trousers", "jeans", "shorts", "skirt", "dress", "suit",
    "jacket", "coat", "vest", "tracksuit", "sweatpants", "joggers",
    "blazer",
    # Generic adjectives
    "low", "high", "mid", "top", "classic", "premium", "original",
    "edition", "size", "color", "white", "black", "red", "blue",
    "green", "pink", "yellow", "grey", "gray", "brown", "navy",
    # Chinese generic categories
    "戒指", "项链", "手链", "耳环", "耳钉", "手表", "包",
    "钱包", "腰带", "皮带", "帽子", "帽", "鞋", "运动鞋",
    "板鞋", "靴", "凉鞋", "拖鞋", "卫衣", "毛衣", "短裤",
    "长裤", "牛仔裤", "牛仔", "夹克", "外套", "大衣",
    "运动服", "太阳镜", "墨镜", "项坠", "吊坠", "耳饰",
    "首饰", "饰品", "t恤", "短袖", "长袖", "衬", "衬衫",
    "polo衫", "马甲", "背心", "羽绒服", "西装", "套装",
    "睡衣", "内衣", "袜子", "围巾", "手套", "皮带",
}


# English ↔ CJK / colloquial token mapping used for the relevance filter.
# Each English keyword maps to additional tokens that should also count as
# "this product is about X" when found in the title (English or Chinese).
_KEYWORD_ALIASES: dict[str, list[str]] = {
    # Sneaker brands
    "nike": ["耐克", "耐", "奈", "nk"],
    "jordan": ["乔丹", "aj"],
    "air jordan": ["乔丹", "aj"],
    "yeezy": ["椰子", "yzy"],
    "adidas": ["阿迪", "阿迪达斯", "三叶草"],
    "samba": ["桑巴"],
    "gazelle": ["瞪羚"],
    "campus": ["校园"],
    "puma": ["彪马"],
    "new balance": ["新百伦", "纽巴伦", "nb"],
    "converse": ["匡威"],
    "vans": ["万斯"],
    "asics": ["亚瑟士"],
    "reebok": ["锐步"],
    "hoka": ["霍卡"],
    "salomon": ["萨洛蒙"],
    # Sneaker models
    "air force": ["空军", "af"],
    "air force 1": ["空军", "af1", "af 1", "空军一号"],
    "af1": ["空军", "air force"],
    "af 1": ["空军", "air force"],
    "dunk": ["dk"],
    "dunk low": ["dk", "dunk"],
    "dunk high": ["dk", "dunk"],
    "blazer": ["开拓者"],
    "cortez": ["阿甘"],
    "max 90": ["max90"],
    "air max": ["max", "气垫"],
    "1906": ["1906r"],
    "550": [],
    "990": [],
    "327": [],
    "old skool": ["old-skool"],
    # Luxury / streetwear
    "dior": ["迪奥"],
    "gucci": ["古驰"],
    "louis vuitton": ["路易威登", "lv"],
    "balenciaga": ["巴黎世家", "巴黎"],
    "fendi": ["芬迪"],
    "prada": ["普拉达"],
    "hermes": ["爱马仕"],
    "chanel": ["香奈儿"],
    "burberry": ["巴宝莉", "博柏利"],
    "moncler": ["盟可睐"],
    "off-white": ["off white"],
    "stone island": ["石头岛", "stoneisland"],
    "supreme": ["sup", "supreme"],
    "bape": ["猿人头", "a bathing ape"],
    "essentials": ["fog", "fear of god", "ess"],
    "fear of god": ["fog", "essentials", "ess"],
    "fog": ["fear of god", "essentials", "ess"],
    # Streetwear / contemporary
    "denim tears": ["dt", "丹宁泪", "丹宁眼泪", "denim tear"],
    "casablanca": ["卡萨布兰卡", "casa", "casablanc"],
    "rhude": ["鲁德", "rh"],
    "stussy": ["斯图西", "stu"],
    "palace": ["帕乐斯", "palace"],
    "kith": ["凯斯"],
    "aime leon dore": ["艾米里昂多尔", "ald"],
    "ald": ["aime leon dore", "艾米里昂多尔"],
    "anti social social club": ["assc", "反社会"],
    "assc": ["anti social", "反社会"],
    "vetements": ["vetements"],
    "rick owens": ["瑞克欧文斯", "ro", "rick"],
    "drkshdw": ["rick owens", "ro"],
    "y-3": ["y3", "山本耀司"],
    "yohji yamamoto": ["山本耀司", "yohji"],
    "comme des garcons": ["cdg", "川久保玲", "comme"],
    "cdg": ["comme des garcons", "川久保玲"],
    "play": ["cdg play", "川久保玲"],
    "issey miyake": ["三宅一生", "issey"],
    "acne studios": ["阿克尼", "acne"],
    "maison kitsune": ["狐狸头", "kitsune", "kitsuné"],
    "ami paris": ["爱米", "ami"],
    "loewe": ["罗意威"],
    "jacquemus": ["雅克穆斯", "jacque"],
    "kaws": ["卡通", "卡通头骨"],
    "sp5der": ["蜘蛛", "spider", "sp"],
    "spider": ["sp5der", "蜘蛛"],
    "trapstar": ["陷阱明星", "trap"],
    "corteiz": ["crtz", "阔提兹"],
    "crtz": ["corteiz", "阔提兹"],
    "eric emanuel": ["ee", "艾瑞克", "eric"],
    "ee shorts": ["eric emanuel", "ee"],
    "synaworld": ["syna", "synaworld"],
    "syna": ["synaworld", "syna"],
    "drew house": ["drew", "笑脸", "笑脸drew"],
    "drew": ["drew house", "笑脸"],
    "broken planet": ["bp", "broken", "破碎星球"],
    "amiri": ["阿姆瑞", "amiri"],
    "chrome hearts": ["克罗心", "ch", "chrome"],
    "represent": ["represent"],
    "purple brand": ["purple"],
    "ksubi": ["库斯比"],
    "diesel": ["迪赛"],
    "louis vuitton x supreme": ["lv x sup", "lv supreme"],
    "off-white": ["off white", "ow", "owt"],
    "off white": ["ow", "owt"],
    # More luxury bag brands
    "goyard": ["戈雅"],
    "celine": ["思琳", "塞林"],
    "saint laurent": ["圣罗兰", "ysl"],
    "ysl": ["saint laurent", "圣罗兰"],
    "bottega veneta": ["葆蝶家", "bv"],
    "bottega": ["葆蝶家", "bv"],
    "loro piana": ["洛罗皮亚纳"],
    "miu miu": ["缪缪", "miumiu"],
    "valentino": ["华伦天奴"],
    "givenchy": ["纪梵希"],
    "lanvin": ["浪凡"],
    "berluti": ["伯鲁帝"],
    "tods": ["托德斯", "tod's"],
    "fendi": ["芬迪"],
    # Modern hot brands
    "yupoo": ["yupoo", "y poo"],
    "alyx": ["艾力克斯", "1017"],
    "1017 alyx": ["alyx", "艾力克斯"],
    "needles": ["蝴蝶"],
    "wtaps": ["wtaps"],
    "human made": ["human", "心型"],
    "billionaire boys club": ["bbc", "billionaire"],
    "bbc": ["billionaire boys club"],
    "icecream": ["ice cream"],
    "patta": ["patta"],
    "carhartt": ["卡哈特", "wip"],
    "carhartt wip": ["卡哈特", "wip"],
    "dickies": ["迪凯斯"],
    "the north face": ["北面", "tnf"],
    "tnf": ["the north face", "北面"],
    "arcteryx": ["始祖鸟", "arc'teryx"],
    "arc'teryx": ["始祖鸟", "arcteryx"],
    "patagonia": ["巴塔哥尼亚"],
    "columbia": ["哥伦比亚"],
    "uniqlo": ["优衣库"],
    "champion": ["冠军"],
    # Jewelry brands
    "pandora": [
        "潘多拉", "潘家多拉", "潘多", "潘家", "pan", "pda",
        "panjia", "pangjia", "pangadora", "panjiadora", "pangjiadora",
        "pan jia", "pang jia", "pan dora", "panj",
    ],
    "cartier": ["卡地亚"],
    "tiffany": ["蒂芙尼", "tiff"],
    "swarovski": ["施华洛世奇"],
    "van cleef": ["梵克雅宝", "vca"],
    "bulgari": ["宝格丽"],
    "bvlgari": ["宝格丽"],
    "tasaki": ["塔思琦"],
    "mikimoto": ["御木本"],
    "apm": ["apm monaco"],
    # Watch brands
    "rolex": ["劳力士", "劳"],
    "omega": ["欧米茄"],
    "audemars piguet": ["爱彼", "ap"],
    "ap watch": ["爱彼", "audemars"],
    "patek philippe": ["百达翡丽", "pp"],
    "richard mille": ["理查德米勒", "rm"],
    "cartier watch": ["卡地亚"],
    "iwc": ["万国"],
    "hublot": ["宇舶"],
    "tag heuer": ["豪雅"],
    "panerai": ["沛纳海"],
    "jaeger": ["积家"],
    # Perfume / beauty brands
    "tom ford": ["汤姆福特", "tf"],
    "creed": ["信仰"],
    "le labo": ["乐博"],
    "byredo": ["柏芮朵"],
    "maison margiela": ["梅森马吉拉", "mmm"],
    # Garment categories
    "hoodie": ["卫衣", "连帽", "帽衫"],
    "sweater": ["毛衣"],
    "shorts": ["短裤"],
    "pants": ["裤", "长裤"],
    "trousers": ["裤", "长裤"],
    "jeans": ["牛仔裤", "牛仔"],
    "shirt": ["衬衫", "衬"],
    "tee": ["t恤", "短袖"],
    "t-shirt": ["t恤", "短袖"],
    "tshirt": ["t恤", "短袖"],
    "jacket": ["夹克", "外套"],
    "coat": ["大衣", "外套"],
    "polo": ["polo衫"],
    "tracksuit": ["运动服"],
    "shoes": ["鞋"],
    "sneakers": ["鞋", "运动鞋", "板鞋"],
    "boots": ["靴", "靴子"],
    "sandals": ["凉鞋"],
    "slides": ["拖鞋"],
    "bag": ["包"],
    "backpack": ["背包", "双肩包"],
    "wallet": ["钱包"],
    "belt": ["腰带", "皮带"],
    "cap": ["帽子", "帽"],
    "hat": ["帽子", "帽"],
    "sunglasses": ["太阳镜", "墨镜"],
    "watch": ["手表", "表"],
}


def _query_terms(query: str) -> tuple[list[str], list[str]]:
    """
    Build two lists of lowercase tokens to match against product titles:
      - strong terms: brand names, model names, distinctive aliases.
        At least ONE strong term must appear in a title for a strong match.
      - weak terms: generic categories ("ring", "shoes", colors, "low").
        Used as fallback when the query has no strong terms at all.
    """
    if not query:
        return [], []
    q = query.lower().strip()
    strong: set[str] = set()
    weak: set[str] = set()

    def _add(t: str) -> None:
        t = t.strip().lower()
        if not t:
            return
        if t in _GENERIC_TOKENS or (t.isdigit() and len(t) <= 2):
            weak.add(t)
        else:
            strong.add(t)

    # Whole-word tokens (require length>1 unless digit)
    for tok in re.split(r"[\s\-_/,.]+", q):
        if not tok:
            continue
        if tok.isdigit() or len(tok) > 1:
            _add(tok)

    # Full query phrase counts as strong if it contains any non-generic word
    if any(t not in _GENERIC_TOKENS for t in re.split(r"\s+", q) if t):
        strong.add(q)
    else:
        weak.add(q)

    # Keyword aliases — multi-word matches and their CJK equivalents.
    # All aliases of a brand-style keyword are STRONG.
    for kw, aliases in _KEYWORD_ALIASES.items():
        if kw in q:
            # The keyword itself: strong unless it's purely a generic word
            if kw in _GENERIC_TOKENS:
                weak.add(kw)
            else:
                strong.add(kw)
            for a in aliases:
                a = a.lower()
                if a in _GENERIC_TOKENS:
                    weak.add(a)
                else:
                    strong.add(a)

    return [t for t in strong if t], [t for t in weak if t]


def filter_by_relevance(
    products: list[Product],
    query: str,
) -> list[Product]:
    """
    Drop products whose title doesn't contain a query keyword.

    Two-tier matching:
      - If the query has STRONG terms (brand/model), require at least one
        strong term in the product title. No fallback — if a marketplace
        has zero strong matches, we'd rather show nothing than show
        unrelated brands. (Show user "no results" so they can refine.)
      - If the query is purely generic (e.g. "ring"), require any term to
        match, and fall back to the original list if too few would remain.
    """
    strong_terms, weak_terms = _query_terms(query)
    if not strong_terms and not weak_terms:
        return products

    by_mkt: dict[str, list[Product]] = {}
    for p in products:
        by_mkt.setdefault(p.marketplace, []).append(p)

    out: list[Product] = []
    if strong_terms:
        # STRONG MODE: require ≥1 strong term in title.
        def matches(p: Product) -> bool:
            haystack = f"{p.title or ''} {p.title_cn or ''}".lower()
            return any(t in haystack for t in strong_terms)

        for mkt, lst in by_mkt.items():
            kept = [p for p in lst if matches(p)]
            log.info(
                "Relevance filter [strong, %s]: %d → %d (query=%r, strong=%s)",
                mkt, len(lst), len(kept), query, sorted(strong_terms),
            )
            out.extend(kept)
    else:
        # WEAK MODE: any term match, with fallback to keep some results.
        def matches(p: Product) -> bool:
            haystack = f"{p.title or ''} {p.title_cn or ''}".lower()
            return any(t in haystack for t in weak_terms)

        min_keep = 3
        for mkt, lst in by_mkt.items():
            kept = [p for p in lst if matches(p)]
            if len(kept) >= min_keep:
                log.info(
                    "Relevance filter [weak, %s]: %d → %d (query=%r)",
                    mkt, len(lst), len(kept), query,
                )
                out.extend(kept)
            else:
                log.info(
                    "Relevance filter [weak, %s]: only %d/%d — keeping all (query=%r)",
                    mkt, len(kept), len(lst), query,
                )
                out.extend(lst)
    return out


async def _try_candidate(image_bytes: bytes) -> list[Product]:
    """Run 1688 + Weidian in parallel for one image."""
    results = await asyncio.gather(
        search_1688(image_bytes),
        search_weidian(image_bytes),
        return_exceptions=True,
    )
    products: list[Product] = []
    for r in results:
        if isinstance(r, Exception):
            log.warning("Image-search source raised: %s", r)
            continue
        products.extend(r)
    return _dedupe(products)


async def search_with_query(
    query: str,
    max_candidates: int = 4,
) -> tuple[list[Product], str | None]:
    """
    Resolve a text query into products by trying multiple Bing image
    candidates. Returns (products, source_image_url).

    Stops at the first candidate where BOTH marketplaces return at least
    one result. Otherwise picks the candidate with the most total results.
    """
    candidates = await get_image_candidates(query, max_results=max_candidates)
    if not candidates:
        log.info("No image candidates for query=%r", query)
        return [], None

    best: tuple[int, list[Product], str] | None = None
    for url in candidates:
        img = await download(url)
        if not img:
            continue
        products = await _try_candidate(img)
        marketplaces = {p.marketplace for p in products}
        log.info(
            "Candidate %s → 1688=%d weidian=%d",
            url[:80],
            sum(1 for p in products if p.marketplace == "1688"),
            sum(1 for p in products if p.marketplace == "weidian"),
        )
        # Ideal: both marketplaces returned something.
        if "1688" in marketplaces and "weidian" in marketplaces:
            return products, url
        # Track best fallback by total count
        if best is None or len(products) > best[0]:
            best = (len(products), products, url)

    if best:
        log.info("No candidate had both sources; using best fallback (%d total)", best[0])
        return best[1], best[2]
    return [], candidates[0] if candidates else None


# ─── Backwards-compat helpers ────────────────────────────────────────────────


async def fetch_reference_image(query: str) -> tuple[bytes | None, str | None]:
    """Legacy: resolve a query to a single reference image (bytes, source_url)."""
    candidates = await get_image_candidates(query, max_results=1)
    if not candidates:
        return None, None
    url = candidates[0]
    img = await download(url)
    if not img:
        return None, url
    return img, url


async def search_by_image(image_bytes: bytes) -> list[Product]:
    """Legacy: run 1688 + Weidian in parallel for already-downloaded bytes."""
    return await _try_candidate(image_bytes)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _dedupe(products: list[Product]) -> list[Product]:
    seen: set[tuple[str, str]] = set()
    out: list[Product] = []
    for p in products:
        key = (p.marketplace, p.product_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def bucket_by_price(products: list[Product]) -> list[Product]:
    """
    Assign tier (premium / mid / cheap) by USD price percentiles.
    Mutates p.tier in place. Sorts: premium (high→low), mid, cheap (low→high).
    """
    priced = [p for p in products if p.price_usd > 0]
    if not priced:
        for p in products:
            p.tier = "mid"
        return products

    if len(priced) < 3:
        for p in products:
            p.tier = "mid"
    else:
        prices = sorted(p.price_usd for p in priced)
        n = len(prices)
        lo = prices[max(1, n // 3)]
        hi = prices[max(2, (2 * n) // 3)]
        # If thresholds collapse, spread across full range
        if hi - lo < 1.0:
            lo = prices[0] + (prices[-1] - prices[0]) / 3
            hi = prices[0] + 2 * (prices[-1] - prices[0]) / 3
        for p in products:
            if p.price_usd >= hi:
                p.tier = "premium"
            elif p.price_usd >= lo:
                p.tier = "mid"
            else:
                p.tier = "cheap"

    tier_order = {"premium": 0, "mid": 1, "cheap": 2}
    products.sort(
        key=lambda p: (
            tier_order.get(p.tier, 1),
            -p.price_usd if p.tier == "premium" else p.price_usd,
        )
    )
    return products
