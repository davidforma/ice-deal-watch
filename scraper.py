"""
Ice Deal Watch CZ — scraper.py v2
Opravené URL adresy + lepší hlavičky proti blokování.
"""

import os
import io
import datetime
import smtplib
import ssl
import logging
import time
import requests
import pandas as pd
import xlsxwriter
from email.message import EmailMessage
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

EMAIL_TO   = "david.forman@magnumicecream.com"
EMAIL_FROM = os.environ.get("GMAIL_USER", "")
EMAIL_PASS = os.environ.get("GMAIL_PASS", "")
OUTPUT_FILE = "Srovnani_zmrzlin.xlsx"

# Rotující hlavičky — simulují různé prohlížeče
HEADERS_LIST = [
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "cs-CZ,cs;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0",
    },
    {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "cs-CZ,cs;q=0.9",
        "Connection": "keep-alive",
    },
]

COLUMNS = [
    "Název řetězce",
    "Značka",
    "Kategorie",
    "Přesný název produktu",
    "Standardní akční cena",
    "Cena s věrnostní kartou",
    "Doba platnosti akce",
    "URL zdroje",
]

_header_idx = 0

def get(url, timeout=20, json=False):
    global _header_idx
    headers = HEADERS_LIST[_header_idx % len(HEADERS_LIST)]
    _header_idx += 1
    time.sleep(1.5)  # pauza mezi requesty — méně agresivní
    try:
        r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        return r
    except Exception as e:
        log.warning(f"GET {url} selhal: {e}")
        return None


def categorize(name: str) -> tuple:
    n = name.lower()
    brands = {
        "Magnum":           ["magnum"],
        "Algida":           ["algida", "cornetto", "solero"],
        "Carte d'Or":       ["carte d'or", "carte dor"],
        "Ben & Jerry's":    ["ben & jerry", "ben&jerry"],
        "Häagen-Dazs":      ["häagen", "haagen"],
        "Nestlé / Míša":    ["míša", "maxibon", "nestle", "nestlé"],
        "Gelatelli (Lidl)": ["gelatelli"],
    }
    types = {
        "pinta":     ["pinta", "ben & jerry", "häagen", "haagen", "500 ml", "900 ml"],
        "multipack": ["6x", "4x", "8x", "3x", "multipack", "multi"],
        "vanička":   ["vanička", "kelímek", "kübel"],
        "nanuk":     ["nanuk", "tyčinka", "lízátko"],
    }
    brand = "ostatní"
    for b, keys in brands.items():
        if any(k in n for k in keys):
            brand = b
            break
    category = "ostatní"
    for t, keys in types.items():
        if any(k in n for k in keys):
            category = t
            break
    return brand, category


def is_ice_cream(text: str) -> bool:
    kw = ["zmrzlin", "nanuk", "pinta", "sorbet", "gelato", "magnum",
          "cornetto", "solero", "häagen", "haagen", "ben & jerry",
          "maxibon", "carte d'or", "gelatelli", "míša", "zmrz", "mražen"]
    return any(k in text.lower() for k in kw)


# ── Rohlik.cz ───────────────────────────────────────────────────────────────
def get_rohlik() -> list:
    log.info("Rohlik.cz")
    # Aktuální API endpoint pro kategorii zmrzlin
    url = "https://www.rohlik.cz/api/v1/categories/300112000/products?offset=0&limit=100&sortBy=price_asc&inStock=1"
    r = get(url)
    if not r:
        # Fallback — zkusíme vyhledávání
        url2 = "https://www.rohlik.cz/api/v1/search?query=zmrzlina&limit=60"
        r = get(url2)
    if not r:
        return []
    try:
        data = r.json()
        # Rohlik vrací různé struktury — zkusíme obě
        products = (
            data.get("data", {}).get("productList", []) or
            data.get("data", {}).get("products", []) or
            data.get("products", []) or
            []
        )
        out = []
        for p in products:
            name = p.get("name", "") or p.get("productName", "")
            if not name or not is_ice_cream(name):
                continue
            price_data = p.get("price", {}) or {}
            price = price_data.get("full", "") or price_data.get("amount", "")
            loyalty = price_data.get("sale", "") or p.get("salePrice", {}).get("amount", "")
            b, t = categorize(name)
            out.append({
                "Název řetězce": "Rohlík",
                "Značka": b, "Kategorie": t,
                "Přesný název produktu": name,
                "Standardní akční cena": f"{price} Kč" if price else "",
                "Cena s věrnostní kartou": f"{loyalty} Kč" if loyalty else "",
                "Doba platnosti akce": "",
                "URL zdroje": "https://www.rohlik.cz",
            })
        log.info(f"  → {len(out)} položek")
        return out
    except Exception as e:
        log.warning(f"Rohlik parse error: {e}")
        return []


# ── Košík.cz ────────────────────────────────────────────────────────────────
def get_kosik() -> list:
    log.info("Košík.cz")
    # Aktuální endpointy Košíku
    urls_to_try = [
        "https://www.kosik.cz/api/frontend/page/category?slug=zmrzliny&page=1&itemsPerPage=60",
        "https://www.kosik.cz/api/frontend/page/category?slug=mrazene-krémy-a-zmrzliny&page=1&itemsPerPage=60",
        "https://www.kosik.cz/api/frontend/search?query=zmrzlina&page=1&itemsPerPage=60",
    ]
    data = None
    used_url = ""
    for url in urls_to_try:
        r = get(url)
        if r:
            try:
                data = r.json()
                used_url = url
                break
            except Exception:
                continue
    if not data:
        return []
    try:
        products = (
            data.get("products", {}).get("items", []) or
            data.get("items", []) or
            []
        )
        out = []
        for p in products:
            name = p.get("name", "")
            if not is_ice_cream(name):
                continue
            price = (p.get("price", {}) or {}).get("amount", "")
            loyalty = (p.get("priceClub", {}) or {}).get("amount", "")
            valid = (p.get("discount", {}) or {}).get("validTo", "")
            b, t = categorize(name)
            out.append({
                "Název řetězce": "Košík",
                "Značka": b, "Kategorie": t,
                "Přesný název produktu": name,
                "Standardní akční cena": f"{price} Kč" if price else "",
                "Cena s věrnostní kartou": f"{loyalty} Kč" if loyalty else "",
                "Doba platnosti akce": valid,
                "URL zdroje": "https://www.kosik.cz",
            })
        log.info(f"  → {len(out)} položek")
        return out
    except Exception as e:
        log.warning(f"Kosik parse error: {e}")
        return []


# ── Kaufland ─────────────────────────────────────────────────────────────────
def get_kaufland() -> list:
    log.info("Kaufland")
    # Kaufland má veřejné JSON API pro akce
    url = "https://www.kaufland.cz/service/offer/list?format=json&limit=200&category=zmrzlina"
    r = get(url)
    if r:
        try:
            data = r.json()
            offers = data.get("offers", data.get("items", []))
            out = []
            for p in offers:
                name = p.get("title", p.get("name", ""))
                if not is_ice_cream(name):
                    continue
                price = p.get("price", p.get("salesPrice", ""))
                valid = f"{p.get('validFrom','')} – {p.get('validTo','')}".strip(" –")
                b, t = categorize(name)
                out.append({
                    "Název řetězce": "Kaufland",
                    "Značka": b, "Kategorie": t,
                    "Přesný název produktu": name,
                    "Standardní akční cena": str(price),
                    "Cena s věrnostní kartou": "",
                    "Doba platnosti akce": valid,
                    "URL zdroje": "https://www.kaufland.cz/akce",
                })
            if out:
                log.info(f"  → {len(out)} položek (JSON API)")
                return out
        except Exception:
            pass

    # Fallback — HTML
    url2 = "https://www.kaufland.cz/akce/aktualni-nabidka/"
    r2 = get(url2)
    if not r2:
        return []
    soup = BeautifulSoup(r2.text, "html.parser")
    out = []
    selectors = [
        (".offer-tile__title", ".offer-tile__price", ".offer-tile__validity"),
        (".t-offer-tile__title", ".t-offer-tile__price", ".t-offer-tile__validity"),
        ("[class*='offer'][class*='title']", "[class*='offer'][class*='price']", None),
    ]
    for name_sel, price_sel, valid_sel in selectors:
        cards = soup.select(name_sel)
        if cards:
            for card_name in cards:
                name = card_name.get_text(strip=True)
                if not is_ice_cream(name):
                    continue
                parent = card_name.parent
                price_el = parent.select_one(price_sel) if price_sel else None
                valid_el = parent.select_one(valid_sel) if valid_sel else None
                b, t = categorize(name)
                out.append({
                    "Název řetězce": "Kaufland",
                    "Značka": b, "Kategorie": t,
                    "Přesný název produktu": name,
                    "Standardní akční cena": price_el.get_text(strip=True) if price_el else "",
                    "Cena s věrnostní kartou": "",
                    "Doba platnosti akce": valid_el.get_text(strip=True) if valid_el else "",
                    "URL zdroje": url2,
                })
            break
    log.info(f"  → {len(out)} položek")
    return out


# ── Lidl ─────────────────────────────────────────────────────────────────────
def get_lidl() -> list:
    log.info("Lidl")
    urls = [
        "https://www.lidl.cz/c/zmrzlina-nanuk-a-sorbet/c220",
        "https://www.lidl.cz/q/zmrzlina",
        "https://www.lidl.cz/p/zmrzliny",
    ]
    soup = None
    used_url = ""
    for url in urls:
        r = get(url)
        if r and len(r.text) > 5000:
            soup = BeautifulSoup(r.text, "html.parser")
            used_url = url
            break
    if not soup:
        return []
    out = []
    for card in soup.select(".s-product-grid__item, .product-grid-box, [class*='product-item']"):
        try:
            name_el = card.select_one(".m-title, [class*='title'], [class*='name']")
            price_el = card.select_one(".m-price__price, [class*='price']")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            if not is_ice_cream(name):
                continue
            b, t = categorize(name)
            out.append({
                "Název řetězce": "Lidl",
                "Značka": b, "Kategorie": t,
                "Přesný název produktu": name,
                "Standardní akční cena": price_el.get_text(strip=True) if price_el else "",
                "Cena s věrnostní kartou": "",
                "Doba platnosti akce": "",
                "URL zdroje": used_url,
            })
        except Exception:
            continue
    log.info(f"  → {len(out)} položek")
    return out


# ── Albert ───────────────────────────────────────────────────────────────────
def get_albert() -> list:
    log.info("Albert")
    urls = [
        "https://www.albert.cz/search/?q=zmrzlina",
        "https://www.albert.cz/produkty/mrazene-vyrobky/zmrzliny-a-nanuky/",
    ]
    soup = None
    used_url = ""
    for url in urls:
        r = get(url)
        if r and len(r.text) > 3000:
            soup = BeautifulSoup(r.text, "html.parser")
            used_url = url
            break
    if not soup:
        return []
    out = []
    for card in soup.select(".product-tile, .product-card, [class*='ProductCard'], [class*='product-item']"):
        try:
            name_el = card.select_one("[class*='name'], [class*='title'], h3, h4")
            price_el = card.select_one("[class*='price'], [class*='Price']")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            if not is_ice_cream(name):
                continue
            b, t = categorize(name)
            out.append({
                "Název řetězce": "Albert",
                "Značka": b, "Kategorie": t,
                "Přesný název produktu": name,
                "Standardní akční cena": price_el.get_text(strip=True) if price_el else "",
                "Cena s věrnostní kartou": "",
                "Doba platnosti akce": "",
                "URL zdroje": used_url,
            })
        except Exception:
            continue
    log.info(f"  → {len(out)} položek")
    return out


# ── Billa ────────────────────────────────────────────────────────────────────
def get_billa() -> list:
    log.info("Billa")
    urls = [
        "https://www.billa.cz/produkty/mrazene-zbozi/zmrzliny-a-nanuky",
        "https://www.billa.cz/hledat?query=zmrzlina",
    ]
    soup = None
    used_url = ""
    for url in urls:
        r = get(url)
        if r and len(r.text) > 3000:
            soup = BeautifulSoup(r.text, "html.parser")
            used_url = url
            break
    if not soup:
        return []
    out = []
    for card in soup.select("[class*='product'], [class*='article'], [class*='item']"):
        try:
            name_el = card.select_one("[class*='name'], [class*='title'], h3, h4")
            price_el = card.select_one("[class*='price']")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            if len(name) < 4 or not is_ice_cream(name):
                continue
            b, t = categorize(name)
            out.append({
                "Název řetězce": "Billa",
                "Značka": b, "Kategorie": t,
                "Přesný název produktu": name,
                "Standardní akční cena": price_el.get_text(strip=True) if price_el else "",
                "Cena s věrnostní kartou": "",
                "Doba platnosti akce": "",
                "URL zdroje": used_url,
            })
        except Exception:
            continue
    log.info(f"  → {len(out)} položek")
    return out


# ── Penny ────────────────────────────────────────────────────────────────────
def get_penny() -> list:
    log.info("Penny")
    url = "https://www.penny.cz/akce"
    r = get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for card in soup.select("[class*='product'], [class*='offer'], [class*='item']"):
        try:
            name_el = card.select_one("[class*='title'], [class*='name'], h3, h4")
            price_el = card.select_one("[class*='price']")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            if len(name) < 4 or not is_ice_cream(name):
                continue
            b, t = categorize(name)
            out.append({
                "Název řetězce": "Penny",
                "Značka": b, "Kategorie": t,
                "Přesný název produktu": name,
                "Standardní akční cena": price_el.get_text(strip=True) if price_el else "",
                "Cena s věrnostní kartou": "",
                "Doba platnosti akce": "",
                "URL zdroje": url,
            })
        except Exception:
            continue
    log.info(f"  → {len(out)} položek")
    return out


# ── Globus ───────────────────────────────────────────────────────────────────
def get_globus() -> list:
    log.info("Globus")
    urls = [
        "https://www.globus.cz/aktualni-nabidka/",
        "https://www.globus.cz/eshop/zmrzliny",
    ]
    soup = None
    used_url = ""
    for url in urls:
        r = get(url)
        if r and len(r.text) > 3000:
            soup = BeautifulSoup(r.text, "html.parser")
            used_url = url
            break
    if not soup:
        return []
    out = []
    for card in soup.select("[class*='product'], [class*='offer'], [class*='item']"):
        try:
            name_el = card.select_one("[class*='name'], [class*='title'], h3, h4")
            price_el = card.select_one("[class*='price']")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            if len(name) < 4 or not is_ice_cream(name):
                continue
            b, t = categorize(name)
            out.append({
                "Název řetězce": "Globus",
                "Značka": b, "Kategorie": t,
                "Přesný název produktu": name,
                "Standardní akční cena": price_el.get_text(strip=True) if price_el else "",
                "Cena s věrnostní kartou": "",
                "Doba platnosti akce": "",
                "URL zdroje": used_url,
            })
        except Exception:
            continue
    log.info(f"  → {len(out)} položek")
    return out


# ── Tesco ────────────────────────────────────────────────────────────────────
def get_tesco() -> list:
    log.info("Tesco")
    urls = [
        "https://nakup.itesco.cz/groceries/cs-CZ/search?query=zmrzlina&filters=promotions",
        "https://nakup.itesco.cz/groceries/cs-CZ/shop/mrazene-vyrobky/zmrzliny-a-nanuky/all",
    ]
    soup = None
    used_url = ""
    for url in urls:
        r = get(url)
        if r and len(r.text) > 3000:
            soup = BeautifulSoup(r.text, "html.parser")
            used_url = url
            break
    if not soup:
        return []
    out = []
    for card in soup.select(".product-list--list-item, [class*='ProductCard'], [class*='product-item']"):
        try:
            name_el = card.select_one(".product-details--title, [class*='title'], h3")
            price_el = card.select_one(".price-per-sellable-unit, [class*='price']")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            if not is_ice_cream(name):
                continue
            b, t = categorize(name)
            out.append({
                "Název řetězce": "Tesco",
                "Značka": b, "Kategorie": t,
                "Přesný název produktu": name,
                "Standardní akční cena": price_el.get_text(strip=True) if price_el else "",
                "Cena s věrnostní kartou": "",
                "Doba platnosti akce": "",
                "URL zdroje": used_url,
            })
        except Exception:
            continue
    log.info(f"  → {len(out)} položek")
    return out


# ── Coop ─────────────────────────────────────────────────────────────────────
def get_coop() -> list:
    log.info("Coop")
    url = "https://www.coop.cz/akce/zmrzlina"
    r = get(url)
    if not r:
        url = "https://www.coop.cz/akce"
        r = get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for card in soup.select("[class*='product'], [class*='offer'], [class*='item']"):
        try:
            name_el = card.select_one("[class*='name'], [class*='title'], h3, h4")
            price_el = card.select_one("[class*='price']")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            if len(name) < 4 or not is_ice_cream(name):
                continue
            b, t = categorize(name)
            out.append({
                "Název řetězce": "Coop",
                "Značka": b, "Kategorie": t,
                "Přesný název produktu": name,
                "Standardní akční cena": price_el.get_text(strip=True) if price_el else "",
                "Cena s věrnostní kartou": "",
                "Doba platnosti akce": "",
                "URL zdroje": url,
            })
        except Exception:
            continue
    log.info(f"  → {len(out)} položek")
    return out


# ── Excel export ─────────────────────────────────────────────────────────────
def save_excel(df: pd.DataFrame, path: str):
    week_label = datetime.date.today().strftime("Týden_%Y_%W")
    workbook  = xlsxwriter.Workbook(path)
    worksheet = workbook.add_worksheet(week_label)

    header_fmt = workbook.add_format({
        "bold": True, "bg_color": "#1E3A5F", "font_color": "#FFFFFF",
        "border": 1, "align": "center", "valign": "vcenter",
        "font_name": "Arial", "font_size": 10,
    })
    row_fmt = workbook.add_format({
        "border": 1, "align": "left", "valign": "vcenter",
        "font_name": "Arial", "font_size": 10, "text_wrap": True,
    })
    alt_fmt = workbook.add_format({
        "border": 1, "align": "left", "valign": "vcenter",
        "font_name": "Arial", "font_size": 10,
        "bg_color": "#EEF4FB", "text_wrap": True,
    })
    src_fmt = workbook.add_format({
        "bold": True, "bg_color": "#1E3A5F", "font_color": "#FFFFFF",
        "font_name": "Arial", "font_size": 10,
    })

    col_widths = [16, 18, 12, 52, 20, 20, 22, 38]
    for i, w in enumerate(col_widths):
        worksheet.set_column(i, i, w)
    worksheet.set_row(0, 22)

    for col, header in enumerate(COLUMNS):
        worksheet.write(0, col, header, header_fmt)

    for row_idx, row in df.iterrows():
        fmt = alt_fmt if row_idx % 2 == 0 else row_fmt
        row_num = row_idx + 1
        worksheet.set_row(row_num, 18)
        for col_idx, col_name in enumerate(COLUMNS):
            worksheet.write(row_num, col_idx, str(row.get(col_name, "")), fmt)

    url_row = len(df) + 3
    worksheet.write(url_row, 0, "Zdrojové odkazy:", src_fmt)
    for i, src_url in enumerate(df["URL zdroje"].unique()):
        worksheet.write(url_row + 1 + i, 0, src_url, row_fmt)

    workbook.close()
    log.info(f"Excel uložen: {path} ({week_label}, {len(df)} řádků)")


# ── E-mail ───────────────────────────────────────────────────────────────────
def send_email(path: str, df: pd.DataFrame):
    if not EMAIL_FROM or not EMAIL_PASS:
        log.error("GMAIL_USER nebo GMAIL_PASS není nastaveno!")
        return

    today = datetime.date.today().strftime("%d. %m. %Y")
    retezce = df["Název řetězce"].value_counts().to_dict()
    retezce_str = ", ".join([f"{k}: {v}ks" for k, v in retezce.items()])

    msg = EmailMessage()
    msg["Subject"] = f"Ice Deal Watch CZ — přehled zmrzlin {today}"
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg.set_content(
        f"Dobrý den,\n\n"
        f"v příloze je dnešní přehled akčních cen mražených krémů ({today}).\n\n"
        f"Celkem nalezeno: {len(df)} produktů\n"
        f"Podle řetězce: {retezce_str}\n\n"
        f"– Ice Deal Watch CZ"
    )
    with open(path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=os.path.basename(path),
        )

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(EMAIL_FROM, EMAIL_PASS)
        server.send_message(msg)
    log.info(f"E-mail odeslán → {EMAIL_TO}")


# ── Záchranný e-mail — když nejsou žádná data ────────────────────────────────
def send_no_data_email():
    """Pošle e-mail i když nejsou data — aby bylo jasné, že skript běžel."""
    if not EMAIL_FROM or not EMAIL_PASS:
        return
    today = datetime.date.today().strftime("%d. %m. %Y")
    msg = EmailMessage()
    msg["Subject"] = f"Ice Deal Watch CZ — {today} — žádná data"
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg.set_content(
        f"Dobrý den,\n\n"
        f"Skript Ice Deal Watch CZ proběhl dnes ({today}), ale nepodařilo se načíst žádné produkty.\n\n"
        f"Možné příčiny: weby blokovaly přístup nebo změnily strukturu.\n"
        f"Zkontrolujte logy v GitHub Actions.\n\n"
        f"– Ice Deal Watch CZ"
    )
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(EMAIL_FROM, EMAIL_PASS)
        server.send_message(msg)
    log.info(f"Záchranný e-mail odeslán → {EMAIL_TO}")


# ── Hlavní běh ────────────────────────────────────────────────────────────────
def main():
    log.info("=== Ice Deal Watch CZ v2 — start ===")

    scrapers = [
        get_rohlik, get_kosik, get_kaufland, get_lidl,
        get_albert, get_billa, get_penny, get_globus,
        get_tesco, get_coop,
    ]

    all_rows = []
    for fn in scrapers:
        try:
            rows = fn()
            all_rows.extend(rows)
        except Exception as e:
            log.error(f"{fn.__name__} selhalo: {e}")

    log.info(f"Celkem: {len(all_rows)} produktů")

    if not all_rows:
        log.warning("Žádná data — odesílám záchranný e-mail.")
        send_no_data_email()
        return

    df = pd.DataFrame(all_rows, columns=COLUMNS)
    save_excel(df, OUTPUT_FILE)
    send_email(OUTPUT_FILE, df)
    log.info("=== Hotovo ✓ ===")


if __name__ == "__main__":
    main()
