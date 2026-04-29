# Spec: Smart Search — Multi-page parallel fetch + Chinese name mapping

## Alternative sources investigated (all blocked)

Before committing to uufinds improvements, all major QC/find sites were tested from server:

| Site | Status | Reason |
|---|---|---|
| findqc.com | ❌ Cloudflare L5 | 403, impossible to bypass |
| cnfans.com | ❌ Auth required | API found (`/search-api/detail/keywords-search-list`) but returns "keyword contains sensitive words" without session token |
| kakobuy.com | ❌ Signed requests | API found (`hbapi.kakobuy.com`) but returns `code:1002 Illegal request` — no keyword search, only product URL lookup |
| mulebuy.com | ❌ No public API | Pure SPA, API on unknown internal domain |
| oopbuy.com | ❌ No public API | Pure SPA |
| sugargoo.com | ❌ No public API | Pure SPA |
| superbuy.com | ❌ Cloudflare | 403 |
| pandabuy.com | ❌ Down | 502 |
| loongbuy.com | ❌ Cloudflare | 403 |

**Conclusion: uufinds is the only viable source.** The solution is to use it smarter.

---

## Problem statement

uufinds.com has two fundamental limitations:

1. **Multi-word queries fail silently** — `nike dunk low` returns 0 results, but `dunk` returns hundreds. The API indexes by individual keywords, not full phrases.
2. **Chinese product names are invisible** — Products like Dior shorts are listed as `Dior短裤` or `迪奥短裤`. A search for `dior shorts` never matches them because the type-of-garment word (`shorts`) only exists in Chinese (`短裤`) in the product subject.
3. **Single page (48 items) is not enough** — `dior` has 2000 products but only 1 "shorts" in the first 48. Fetching 3 pages in parallel yields 6 shorts results.

The result: users searching `dior shorts`, `nike dunk low`, or `chrome hearts hoodie` get 0 results or completely irrelevant results.

---

## Requirements

### R1 — Smart query decomposition + multi-page fetch

When a query returns fewer than 3 results after filtering:

1. **Split into brand + type tokens** — detect which tokens are brand names vs product type descriptors
2. **Fetch brand-only across 3 pages in parallel** — `asyncio.gather` on pages 1, 2, 3 (48 items each = 144 items total)
3. **Filter client-side by product type** — keep only products whose subject contains the type token(s) in English OR their Chinese equivalent from `GARMENT_ZH`
4. **Fallback chain**:
   - Step 1: original query (1 page) → if ≥3 results after filter, done
   - Step 2: brand-only × 3 pages parallel + client-side type filter → if ≥3 results, done
   - Step 3: brand-only × 3 pages unfiltered → show with footer note
   - Step 4: if still 0, show "no results" message

### R2 — Chinese garment type mapping

A static dictionary maps English garment/product types to their Chinese equivalents for client-side filtering:

```python
GARMENT_ZH = {
    "shorts":     ["短裤", "短"],
    "pants":      ["裤子", "长裤", "裤"],
    "hoodie":     ["卫衣", "连帽"],
    "sweatshirt": ["卫衣"],
    "jacket":     ["外套", "夹克"],
    "coat":       ["大衣", "外套"],
    "shirt":      ["衬衫", "上衣"],
    "tee":        ["T恤", "短袖"],
    "t-shirt":    ["T恤", "短袖"],
    "sweater":    ["毛衣", "针织"],
    "vest":       ["背心", "马甲"],
    "dress":      ["连衣裙", "裙"],
    "skirt":      ["裙子", "短裙"],
    "bag":        ["包", "手提包", "背包"],
    "wallet":     ["钱包"],
    "hat":        ["帽子", "帽"],
    "cap":        ["帽子", "棒球帽"],
    "sneakers":   ["鞋", "运动鞋"],
    "shoes":      ["鞋"],
    "boots":      ["靴子", "靴"],
    "belt":       ["腰带", "皮带"],
    "socks":      ["袜子", "袜"],
    "glasses":    ["眼镜"],
    "sunglasses": ["墨镜", "太阳镜"],
    "watch":      ["手表", "腕表"],
    "bracelet":   ["手链", "手环"],
    "necklace":   ["项链"],
    "ring":       ["戒指"],
    "earrings":   ["耳环", "耳饰"],
}
```

### R3 — Brand token detection

A static set of known brand names used to split queries into `brand_tokens` + `type_tokens`:

```python
KNOWN_BRANDS = {
    "dior", "louis vuitton", "lv", "gucci", "gg",
    "chrome hearts", "stone island", "fear of god", "fog", "essentials",
    "amiri", "rhude", "off-white", "ow", "palm angels",
    "balenciaga", "bb", "givenchy", "versace", "fendi",
    "burberry", "moncler", "canada goose",
    "nike", "adidas", "jordan", "new balance", "nb",
    "puma", "reebok", "asics", "salomon", "new era",
    "supreme", "bape", "stussy", "palace", "kith",
    "travis scott", "ts", "yeezy", "yzy",
}
```

Detection logic: iterate tokens of the query, if a token (or bigram) matches a known brand → brand token. Remaining tokens → type tokens.

### R4 — Parallel page fetching

Multi-page fetches use `asyncio.gather` on pages 1+2+3 simultaneously. Since each page takes ~0.5s, 3 pages in parallel adds ~0.5s vs ~1.5s sequential. Total added latency target vs current: <1s.

### R5 — Result deduplication

When merging results from multiple searches, deduplicate by `product_id` (spuNo). Keep the instance with the higher `qc_image_count`.

### R6 — User-facing messaging

- If fallback was used, the embed footer notes it: `"Showing [brand] products filtered by type — no exact match found"`
- If brand-only unfiltered was used: `"No exact match for '[query]' — showing all [brand] products"`
- Normal results: no change to current footer

---

## Acceptance criteria

| Scenario | Expected behaviour |
|---|---|
| `dior shorts` | Returns Dior products with 短裤/shorts in subject |
| `nike dunk low` | Returns dunk products (brand=nike, type=dunk low) |
| `chrome hearts hoodie` | Returns Chrome Hearts 卫衣/hoodie products |
| `jordan 4` | Works as before (already returns results) |
| `af1` | Works as before (expansion → nike air force 1) |
| `yeezy 350` | Works as before |
| Unknown brand + type | Falls back to brand-only, shows with note |
| Completely unknown query | Shows "no results" message as before |

---

## Implementation approach

1. **`bot/config.py`** — Add `GARMENT_ZH` dict and `KNOWN_BRANDS` set

2. **`bot/services/uufinds.py`**:
   - Add `search_page(keyword, page, page_size) -> list[Product]` — fetches a single page
   - Add `split_query(query) -> (brand_tokens: str, type_tokens: list[str])` — uses `KNOWN_BRANDS`
   - Add `_matches_type(subject, type_tokens) -> bool` — checks English + Chinese equivalents from `GARMENT_ZH`
   - Add `smart_search(keyword) -> tuple[list[Product], str | None]` — implements the 4-step fallback chain:
     - Step 1: `search_with_retry(keyword)` → filter → if ≥3, return `(products, None)`
     - Step 2: `asyncio.gather(search_page(brand,1), search_page(brand,2), search_page(brand,3))` → merge → filter by type → if ≥3, return `(products, None)` (no note needed if results are good)
     - Step 3: same 3-page fetch unfiltered → return `(products, f"No exact match — showing all {brand} products")`
     - Step 4: return `([], None)`

3. **`bot/cogs/tickets.py`** — Replace `search_with_retry` call with `smart_search`. Append `fallback_note` to embed footer if present.

4. **Test** — Manually verify `dior shorts`, `nike dunk low`, `chrome hearts hoodie`, `jordan 4`, `af1` in Discord.
