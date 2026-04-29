"""
URL builders for all 16 supported agents.

Family A: path-based  → {domain}/product/{marketplace}/{product_id}?{code}
Family B: url-encoded → {domain}/buy?url={encoded_source_link}&{code}
Kakobuy:  direct      → {domain}/item/details?url={encoded_source_link}
"""

from urllib.parse import quote, urlparse, parse_qs

from bot.config import (
    AFFILIATE_CODES,
    AGENT_DOMAINS,
    FAMILY_A,
    FAMILY_B,
)


def build_agent_url(agent: str, marketplace: str, product_id: str, source_link: str) -> str:
    """
    Build an affiliate URL for the given agent.

    Args:
        agent:       agent key (e.g. "hipobuy")
        marketplace: "weidian" or "1688"
        product_id:  spuNo from uufinds (product ID on the marketplace)
        source_link: direct URL to the product on Weidian/1688
    """
    domain = AGENT_DOMAINS[agent]
    code = AFFILIATE_CODES.get(agent)

    if agent == "kakobuy":
        encoded = quote(source_link, safe="")
        return f"{domain}/item/details?url={encoded}"

    if agent in FAMILY_A:
        base = f"{domain}/product/{marketplace}/{product_id}"
        return f"{base}?{code}" if code else base

    if agent in FAMILY_B:
        encoded = quote(source_link, safe="")
        base = f"{domain}/buy?url={encoded}"
        return f"{base}&{code}" if code else base

    # Fallback: treat as Family A
    base = f"{domain}/product/{marketplace}/{product_id}"
    return f"{base}?{code}" if code else base


# ---------------------------------------------------------------------------
# Link parsing — extract marketplace + product_id from any supported URL
# ---------------------------------------------------------------------------

def parse_source_link(url: str) -> tuple[str, str] | None:
    """
    Parse a product URL and return (marketplace, product_id).
    Supports:
      - Weidian direct:  weidian.com/item.html?itemID=...
      - 1688 direct:     detail.1688.com/offer/{id}.html
      - Family A agents: domain/product/{marketplace}/{id}
      - Family B agents: domain/buy?url={encoded}  or  domain/#/home/productDetail?productLink={encoded}
      - Kakobuy:         kakobuy.com/item/details?url={encoded}

    Returns None if the URL cannot be parsed.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower().replace("www.", "")
    path = parsed.path

    # --- Direct marketplace links ---
    if "weidian.com" in host:
        qs = parse_qs(parsed.query)
        item_id = qs.get("itemID", qs.get("itemid", [None]))[0]
        if item_id:
            return ("weidian", item_id)

    if "1688.com" in host:
        # detail.1688.com/offer/123456789.html
        parts = [p for p in path.split("/") if p]
        for part in parts:
            if part.endswith(".html"):
                pid = part[:-5]
                if pid.isdigit():
                    return ("1688", pid)
            elif part.isdigit():
                return ("1688", part)

    # --- Family A agents: /product/{marketplace}/{id} ---
    if "/product/" in path:
        parts = [p for p in path.split("/") if p]
        try:
            idx = parts.index("product")
            marketplace = parts[idx + 1]   # "weidian" or "1688"
            product_id  = parts[idx + 2]
            if marketplace in ("weidian", "1688"):
                return (marketplace, product_id)
        except (ValueError, IndexError):
            pass

    # --- Kakobuy: /item/details?url={encoded} ---
    if "kakobuy.com" in host:
        qs = parse_qs(parsed.query)
        inner = qs.get("url", [None])[0]
        if inner:
            return parse_source_link(inner)

    # --- Family B agents: ?url={encoded} or ?productLink={encoded} ---
    qs = parse_qs(parsed.query)
    inner = qs.get("url", qs.get("productLink", qs.get("productlink", [None])))[0]
    if inner:
        return parse_source_link(inner)

    # --- Sugargoo hash-based: /#/home/productDetail?productLink={encoded} ---
    if parsed.fragment:
        frag_parsed = urlparse("http://x/" + parsed.fragment.lstrip("/"))
        fqs = parse_qs(frag_parsed.query)
        inner = fqs.get("productLink", fqs.get("productlink", [None]))[0]
        if inner:
            return parse_source_link(inner)

    return None


def convert_link(url: str, agent: str) -> str | None:
    """
    Convert any supported product URL to an affiliate link for the given agent.
    Returns None if the source URL cannot be parsed.
    """
    result = parse_source_link(url)
    if result is None:
        return None
    marketplace, product_id = result

    # Reconstruct the canonical source link for Family B agents
    if marketplace == "weidian":
        source_link = f"https://weidian.com/item.html?itemID={product_id}"
    else:
        source_link = f"https://detail.1688.com/offer/{product_id}.html"

    return build_agent_url(agent, marketplace, product_id, source_link)
