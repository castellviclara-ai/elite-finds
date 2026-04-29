# Agent order is fixed — do not reorder
AGENTS_ORDER = [
    "usfans",
    "hipobuy",
    "mulebuy",
    "oopbuy",
    "allchinabuy",
    "cssbuy",
    "gtbuy",
    "itaobuy",
    "kakobuy",
    "litbuy",
    "loongbuy",
    "lovegobuy",
    "orientdig",
    "sugargoo",
    "superbuy",
    "vigorbuy",
]

# Display names for the dropdown
AGENTS_DISPLAY = {
    "usfans":      "USFans ⭐",
    "hipobuy":     "Hipobuy ⭐",
    "mulebuy":     "Mulebuy ⭐",
    "oopbuy":      "OopBuy ⭐",
    "allchinabuy": "AllChinaBuy",
    "cssbuy":      "CSSBuy",
    "gtbuy":       "GTBuy",
    "itaobuy":     "ItaoBuy",
    "kakobuy":     "Kakobuy",
    "litbuy":      "LitBuy",
    "loongbuy":    "LoongBuy",
    "lovegobuy":   "LoveGoBuy",
    "orientdig":   "OrientDig",
    "sugargoo":    "Sugargoo",
    "superbuy":    "SuperBuy",
    "vigorbuy":    "VigorBuy",
}

# Affiliate codes — kakobuy has no affiliate code
AFFILIATE_CODES = {
    "usfans":      "ref=RZF9DV",
    "hipobuy":     "inviteCode=HBH2H62MZ",
    "mulebuy":     "ref=201168045",
    "oopbuy":      "inviteCode=XX5JBEAD0",
    "allchinabuy": "partnercode=uido1e&type=product",
    "cssbuy":      "promotionCode=d2da395d2834d901",
    "gtbuy":       "inviteCode=RR1CEL8AM",
    "itaobuy":     "inviteCode=JW357EZ9",
    "kakobuy":     None,
    "litbuy":      "inviteCode=DGP7GXEZ0",
    "loongbuy":    "invitecode=486V8T",
    "lovegobuy":   "invite_code=MUSBRX",
    "orientdig":   "ref=100244611",
    "sugargoo":    "memberId=3229308566093806315",
    "superbuy":    "partnercode=EH3wbX",
    "vigorbuy":    "inviteCode=j0rE2rkQ",
}

# Agents that use path-based URLs: /product/{marketplace}/{id}?{code}
FAMILY_A = {
    "usfans", "hipobuy", "mulebuy", "oopbuy",
    "litbuy", "loongbuy", "gtbuy", "itaobuy",
    "vigorbuy", "lovegobuy",
}

# Agents that use url-encoded URLs: /buy?url={encoded}&{code}
FAMILY_B = {
    "sugargoo", "superbuy", "allchinabuy", "cssbuy", "orientdig",
}

# Base domains
AGENT_DOMAINS = {
    "usfans":      "https://www.usfans.com",
    "hipobuy":     "https://www.hipobuy.com",
    "mulebuy":     "https://www.mulebuy.com",
    "oopbuy":      "https://www.oopbuy.com",
    "allchinabuy": "https://www.allchinabuy.com",
    "cssbuy":      "https://www.cssbuy.com",
    "gtbuy":       "https://www.gtbuy.com",
    "itaobuy":     "https://www.itaobuy.com",
    "kakobuy":     "https://www.kakobuy.com",
    "litbuy":      "https://www.litbuy.com",
    "loongbuy":    "https://www.loongbuy.com",
    "lovegobuy":   "https://www.lovegobuy.com",
    "orientdig":   "https://www.orientdig.com",
    "sugargoo":    "https://www.sugargoo.com",
    "superbuy":    "https://www.superbuy.com",
    "vigorbuy":    "https://www.vigorbuy.com",
}

# Tier emojis
TIER_EMOJI = {
    "premium": "💎",
    "mid":     "⚖️",
    "cheap":   "💰",
}

TIER_LABELS = {
    "premium": "PREMIUM",
    "mid":     "MID",
    "cheap":   "CHEAP",
}

# Keyword expansions
KEYWORD_EXPANSIONS = {
    "af1":          "nike air force 1",
    "air force 1":  "nike air force 1",
    "aj1":          "air jordan 1",
    "aj2":          "air jordan 2",
    "aj3":          "air jordan 3",
    "aj4":          "air jordan 4",
    "aj5":          "air jordan 5",
    "aj6":          "air jordan 6",
    "aj11":         "air jordan 11",
    "yzy":          "yeezy",
    "350":          "yeezy 350",
    "700":          "yeezy 700",
    "dunk":         "nike dunk",
    "sb dunk":      "nike sb dunk",
    "samba":        "adidas samba",
    "nb 550":       "new balance 550",
    "nb550":        "new balance 550",
    "nb 574":       "new balance 574",
    "nb574":        "new balance 574",
    "nb 9060":      "new balance 9060",
    "nb9060":       "new balance 9060",
    "tp9":          "nike tech pack",
    "ow":           "off-white",
    "travis":       "travis scott",
    "ts":           "travis scott",
    "lv":           "louis vuitton",
    "gg":           "gucci",
    "bb":           "balenciaga",
    "bb track":     "balenciaga track",
    "speedrunner":  "balenciaga speed runner",
}
