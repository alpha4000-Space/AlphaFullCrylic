CURRENCIES = [
    {"id": "uzcard",   "name": "UZCARD",      "icon": "🔷", "type": "card"},
    {"id": "humo",     "name": "HUMO",         "icon": "🔷", "type": "card"},
    {"id": "tron",     "name": "Tron (TRX)",   "icon": "🔷", "type": "crypto"},
    {"id": "sui",      "name": "Sui (SUI)",    "icon": "🔷", "type": "crypto"},
    {"id": "bnb",      "name": "Bnb (BNB)",    "icon": "🔷", "type": "crypto"},
    {"id": "polygon",  "name": "POLYGON",      "icon": "🔷", "type": "crypto"},
    {"id": "solana",   "name": "SOLANA",       "icon": "🔷", "type": "crypto"},
    {"id": "litecoin", "name": "LITECOIN",     "icon": "🔷", "type": "crypto"},
    {"id": "dogecoin", "name": "DOGECOIN",     "icon": "🔷", "type": "crypto"},
    {"id": "toncoin",  "name": "TONCOIN",      "icon": "🔷", "type": "crypto"},
]

# Karta uchun to'lov manzillari
PAYMENT_CARDS = {
    "uzcard": "8600 1666 0393 7029",
    "humo":   "9860 0000 0000 0000",
}

CRYPTO_IDS = [c["id"] for c in CURRENCIES if c["type"] == "crypto"]
CARD_IDS   = [c["id"] for c in CURRENCIES if c["type"] == "card"]

def get_currency_by_id(currency_id: str) -> dict | None:
    for c in CURRENCIES:
        if c["id"] == currency_id:
            return c
    return None

def get_crypto_rates() -> dict:
    """DB dan kripto kurslarini qaytaradi: {crypto_id: {sell_rate, buy_rate, ...}}"""
    from database import load_db
    return load_db().get("crypto_rates", {})

def get_effective_rate(from_id: str, to_id: str) -> dict | None:
    """
    Almashuv kursini qaytaradi.
    - KARTA → KRIPTO: buy_rate ishlatiladi (user so'm beradi)
    - KRIPTO → KARTA: sell_rate ishlatiladi (user kripto beradi)
    - KARTA → KARTA: 1:1 (UZCARD↔HUMO)
    """
    from database import load_db
    db    = load_db()
    rates = db.get("crypto_rates", {})

    def cn(cid):
        c = get_currency_by_id(cid)
        return c["name"] if c else cid

    def s_min(cid): return int(10000 if cid in CARD_IDS else 1)
    def s_max(cid): return int(500_000_000 if cid in CARD_IDS else 100_000)

    # KARTA → KRIPTO
    if from_id in CARD_IDS and to_id in CRYPTO_IDS:
        r = rates.get(to_id)
        if not r or not r.get("buy_rate"):
            return None
        buy = float(r["buy_rate"])
        return {
            "rate":         1 / buy,
            "rate_display": f"1 {cn(to_id)} = {int(buy)} СЎМ",
            "min":          r.get("min", s_min(from_id)),
            "max":          r.get("max", s_max(from_id)),
            "commission":   r.get("commission", 1.0),
        }

    # КРИPTO → KARTA
    if from_id in CRYPTO_IDS and to_id in CARD_IDS:
        r = rates.get(from_id)
        if not r or not r.get("sell_rate"):
            return None
        sell = float(r["sell_rate"])
        return {
            "rate":         sell,
            "rate_display": f"1 {cn(from_id)} = {int(sell)} СЎМ",
            "min":          r.get("min", s_min(from_id)),
            "max":          r.get("max", s_max(from_id)),
            "commission":   r.get("commission", 1.0),
        }

    # KARTA → KARTA (UZCARD ↔ HUMO)
    if from_id in CARD_IDS and to_id in CARD_IDS:
        return {
            "rate":         1.0,
            "rate_display": f"1 {cn(from_id)} = 1 {cn(to_id)}",
            "min":          10000,
            "max":          50_000_000,
            "commission":   0.5,
        }

    return None
