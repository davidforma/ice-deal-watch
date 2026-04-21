"""
Ice Deal Watch CZ — scraper.py
Stahuje akční ceny zmrzlin z českých řetězců bez Selenia.
Spouštěno automaticky přes GitHub Actions každý den v 7:00.
"""

import os
import io
import datetime
import smtplib
import ssl
import logging
import requests
import pandas as pd
import xlsxwriter
from email.message import EmailMessage
from bs4 import BeautifulSoup

# ── Nastavení logů ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ── Konfigurace ─────────────────────────────────────────────────────────────
EMAIL_TO   = "david.forman@magnumicecream.com"
EMAIL_FROM = os.environ.get("GMAIL_USER", "")
EMAIL_PASS = os.environ.get("GMAIL_PASS", "")
OUTPUT_FILE = "Srovnani_zmrzlin.xlsx"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "cs-CZ,cs;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

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

# ── Pomocné funkce ──────────────────────────────────────────────────────────
def get(url, timeout=15):
    """HTTP GET s ošetřením chyb."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        log.warning(f"GET {url} selhal: {e}")
        return None


def categorize(name: str) -> tuple[str, str]:
    """Odhadne značku a kategorii z názvu produktu."""
    n = name.lower()

    brands = {
        "Magnum":        ["magnum"],
        "Algida":        ["algida", "cornetto", "solero", "carte d'or"],
        "Ben & Jerry's": ["ben & jerry", "ben&jerry"],
        "Häagen-Dazs":   ["häagen", "haagen"],
        "Nestlé":        ["míša", "maxibon", "nestle", "nestlé"],
        "Łowicz":        ["łowicz"],
        "Lidl / Gelatelli": ["gelatelli"],
        "Penny / My Label": ["my label"],
    }
    types = {
        "pinta":     ["pinta", "ben & jerry", "häagen", "haagen", "500 ml", "900 ml"],
        "multipack": ["6x", "4x", "8x", "3x", "multipack", "multi"],
        "vanička":   ["vanička", "kelímek", "kübel", "vaničce"],
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
    """Vrátí True, pokud název obsahuje klíčová slova zmrzliny."""
    kw = ["zmrzlin", "nanuk", "pinta", "sorbet", "gelato", "magnum",
          "cornetto", "solero", "häagen", "haagen", "ben & jerry",
          "maxibon", "carte d'or", "gelatelli", "míša", "zmrz"]
    t = text.lower()
    return any(k in t for k in kw)


# ── Rohlik.cz — veřejné JSON API ───────────────────────────────────────────
def get_rohlik() -> list[dict]:
    log.info("Rohlik.cz — JSON API")
    url = "https://www.rohlik.cz/api/v1/categories/300112000/products?offset=0&limit=100&sortBy=price&sales=true"
    r = get(url)
    if not r:
        return []
    try:
        data = r.json()
        products = data.get("data", {}).get("productList", [])
        out = []
        for p in products:
            name = p.get("name", "")
            if not is_ice_cream(name):
                continue
            price = p.get("price", {}).get("full", "")
            loyalty = p.get("price", {}).get("sale", "")
            b, t = categorize(name)
            out.append({
                "Název řetězce": "Rohlík",
                "Značka": b,
                "Kategorie": t,
                "Přesný název produktu": name,
                "Standardní akční cena": f"{price} Kč" if price else "",
                "Cena s věrnostní kartou": f"{loyalty} Kč" if loyalty else "",
                "Doba platnosti akce": "",
                "URL zdroje": "https://www.rohlik.cz",
            })
        log.info(f"  → {len(out)} položek")
        return out
    except Exception as e:
        log.warning(f"Rohlik JSON parse error: {e}")
        return []


# ── Košík.cz — veřejné JSON API ────────────────────────────────────────────
def get_kosik() -> list[dict]:
    log.info("Košík.cz — JSON API")
    url = "https://www.kosik.cz/api/frontend/page/category?slug=zmrzliny-a-nanuky&page=1&itemsPerPage=80"
    r = get(url)
    if not r:
        return []
    try:
        data = r.json()
        products = data.get("products", {}).get("items", [])
        out = []
        for p in products:
            name = p.get("name", "")
            if not is_ice_cream(name):
                continue
            price = p.get("price", {}).get("amount", "")
            loyalty = p.get("priceClub", {}).get("amount", "")
            valid = p.get("discount", {}).get("validTo", "")
            b, t = categorize(name)
            out.append({
                "Název řetězce": "Košík",
                "Značka": b,
                "Kategorie": t,
                "Přesný název produktu": name,
                "Standardní akční cena": f"{price} Kč" if price else "",
                "Cena s věrnostní kartou": f"{loyalty} Kč" if loyalty else "",
                "Doba platnosti akce": valid,
                "URL zdroje": "https://www.kosik.cz",
            })
        log.info(f"  → {len(out)} položek")
        return out
    except Exception as e:
        log.warning(f"Kosik JSON parse error: {e}")
        return []


# ── Kaufland — HTML scraping ────────────────────────────────────────────────
def get_kaufland() -> list[dict]:
    log.info("Kaufland — HTML scraping")
    url = "https://www.kaufland.cz/akce/aktualni-nabidka/akce.html"
    r = get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for card in soup.select(".offer-tile, .t-offer-tile"):
        try:
            name_el = card.select_one(".offer-tile__title, .t-offer-tile__title")
            price_el = card.select_one(".offer-tile__price, .t-offer-tile__price")
            valid_el = card.select_one(".offer-tile__validity, .t-offer-tile__validity")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            if not is_ice_cream(name):
                continue
            price = price_el.get_text(strip=True) if price_el else ""
            valid = valid_el.get_text(strip=True) if valid_el else ""
            b, t = categorize(name)
            out.append({
                "Název řetězce": "Kaufland",
                "Značka": b,
                "Kategorie": t,
                "Přesný název produktu": name,
                "Standardní akční cena": price,
                "Cena s věrnostní kartou": "",
                "Doba platnosti akce": valid,
                "URL zdroje": url,
            })
        except Exception:
            continue
    log.info(f"  → {len(out)} položek")
    return out


# ── Lidl — HTML scraping ────────────────────────────────────────────────────
def get_lidl() -> list[dict]:
    log.info("Lidl — HTML scraping")
    url = "https://www.lidl.cz/c/zmrzlina-a-nanuk/c220"
    r = get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for card in soup.select(".s-product-grid__item, .product-grid-box"):
        try:
            name_el = card.select_one(".m-title, .product-grid-box__description")
            price_el = card.select_one(".m-price__price, .price-box__price")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            if not is_ice_cream(name):
                continue
            price = price_el.get_text(strip=True) if price_el else ""
            b, t = categorize(name)
            out.append({
                "Název řetězce": "Lidl",
                "Značka": b,
                "Kategorie": t,
                "Přesný název produktu": name,
                "Standardní akční cena": price,
                "Cena s věrnostní kartou": "",
                "Doba platnosti akce": "",
                "URL zdroje": url,
            })
        except Exception:
            continue
    log.info(f"  → {len(out)} položek")
    return out


# ── Albert — HTML scraping ──────────────────────────────────────────────────
def get_albert() -> list[dict]:
    log.info("Albert — HTML scraping")
    url = "https://www.albert.cz/nabidka/?q=zmrzlina"
    r = get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for card in soup.select(".product-tile, .product-card"):
        try:
            name_el = card.select_one(".product-tile__name, .product-name")
            price_el = card.select_one(".product-tile__price, .price")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            if not is_ice_cream(name):
                continue
            price = price_el.get_text(strip=True) if price_el else ""
            b, t = categorize(name)
            out.append({
                "Název řetězce": "Albert",
                "Značka": b,
                "Kategorie": t,
                "Přesný název produktu": name,
                "Standardní akční cena": price,
                "Cena s věrnostní kartou": "",
                "Doba platnosti akce": "",
                "URL zdroje": url,
            })
        except Exception:
            continue
    log.info(f"  → {len(out)} položek")
    return out


# ── Billa — HTML scraping ───────────────────────────────────────────────────
def get_billa() -> list[dict]:
    log.info("Billa — HTML scraping")
    url = "https://www.billa.cz/produkty/zmrzliny-a-nanuk"
    r = get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for card in soup.select(".product-tile, .article-item"):
        try:
            name_el = card.select_one(".product-name, .article-title")
            price_el = card.select_one(".product-price, .price")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            if not is_ice_cream(name):
                continue
            price = price_el.get_text(strip=True) if price_el else ""
            b, t = categorize(name)
            out.append({
                "Název řetězce": "Billa",
                "Značka": b,
                "Kategorie": t,
                "Přesný název produktu": name,
                "Standardní akční cena": price,
                "Cena s věrnostní kartou": "",
                "Doba platnosti akce": "",
                "URL zdroje": url,
            })
        except Exception:
            continue
    log.info(f"  → {len(out)} položek")
    return out


# ── Penny — HTML scraping ───────────────────────────────────────────────────
def get_penny() -> list[dict]:
    log.info("Penny — HTML scraping")
    url = "https://www.penny.cz/akce"
    r = get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for card in soup.select(".product, .offer-product"):
        try:
            name_el = card.select_one(".product__title, .name")
            price_el = card.select_one(".product__price, .price")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            if not is_ice_cream(name):
                continue
            price = price_el.get_text(strip=True) if price_el else ""
            b, t = categorize(name)
            out.append({
                "Název řetězce": "Penny",
                "Značka": b,
                "Kategorie": t,
                "Přesný název produktu": name,
                "Standardní akční cena": price,
                "Cena s věrnostní kartou": "",
                "Doba platnosti akce": "",
                "URL zdroje": url,
            })
        except Exception:
            continue
    log.info(f"  → {len(out)} položek")
    return out


# ── Globus — HTML scraping ──────────────────────────────────────────────────
def get_globus() -> list[dict]:
    log.info("Globus — HTML scraping")
    url = "https://www.globus.cz/nabidky.html"
    r = get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for card in soup.select(".product-card, .offer-item"):
        try:
            name_el = card.select_one(".product-card__name, .offer-item__name")
            price_el = card.select_one(".product-card__price, .offer-item__price")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            if not is_ice_cream(name):
                continue
            price = price_el.get_text(strip=True) if price_el else ""
            b, t = categorize(name)
            out.append({
                "Název řetězce": "Globus",
                "Značka": b,
                "Kategorie": t,
                "Přesný název produktu": name,
                "Standardní akční cena": price,
                "Cena s věrnostní kartou": "",
                "Doba platnosti akce": "",
                "URL zdroje": url,
            })
        except Exception:
            continue
    log.info(f"  → {len(out)} položek")
    return out


# ── Tesco — HTML scraping ───────────────────────────────────────────────────
def get_tesco() -> list[dict]:
    log.info("Tesco — HTML scraping")
    url = "https://nakup.itesco.cz/groceries/cs-CZ/search?query=zmrzlina&filters=promotions"
    r = get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for card in soup.select(".product-list--list-item"):
        try:
            name_el = card.select_one(".product-details--title")
            price_el = card.select_one(".price-per-sellable-unit")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            if not is_ice_cream(name):
                continue
            price = price_el.get_text(strip=True) if price_el else ""
            b, t = categorize(name)
            out.append({
                "Název řetězce": "Tesco",
                "Značka": b,
                "Kategorie": t,
                "Přesný název produktu": name,
                "Standardní akční cena": price,
                "Cena s věrnostní kartou": "",
                "Doba platnosti akce": "",
                "URL zdroje": url,
            })
        except Exception:
            continue
    log.info(f"  → {len(out)} položek")
    return out


# ── Coop — HTML scraping ────────────────────────────────────────────────────
def get_coop() -> list[dict]:
    log.info("Coop — HTML scraping")
    url = "https://www.coop.cz/akce"
    r = get(url)
    if not r:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    for card in soup.select(".product, .product-card"):
        try:
            name_el = card.select_one(".product-name, .name")
            price_el = card.select_one(".product-price, .price")
            if not name_el:
                continue
            name = name_el.get_text(strip=True)
            if not is_ice_cream(name):
                continue
            price = price_el.get_text(strip=True) if price_el else ""
            b, t = categorize(name)
            out.append({
                "Název řetězce": "Coop",
                "Značka": b,
                "Kategorie": t,
                "Přesný název produktu": name,
                "Standardní akční cena": price,
                "Cena s věrnostní kartou": "",
                "Doba platnosti akce": "",
                "URL zdroje": url,
            })
        except Exception:
            continue
    log.info(f"  → {len(out)} položek")
    return out


# ── Excel export ────────────────────────────────────────────────────────────
def save_excel(df: pd.DataFrame, path: str):
    week_label = datetime.date.today().strftime("Týden_%Y_%W")
    workbook  = xlsxwriter.Workbook(path)
    worksheet = workbook.add_worksheet(week_label)

    # Formáty
    header_fmt = workbook.add_format({
        "bold": True, "bg_color": "#1E3A5F", "font_color": "#FFFFFF",
        "border": 1, "align": "center", "valign": "vcenter",
        "font_name": "Arial", "font_size": 10,
    })
    row_fmt = workbook.add_format({
        "border": 1, "align": "left", "valign": "vcenter",
        "font_name": "Arial", "font_size": 10,
    })
    alt_fmt = workbook.add_format({
        "border": 1, "align": "left", "valign": "vcenter",
        "font_name": "Arial", "font_size": 10, "bg_color": "#EEF4FB",
    })
    url_fmt = workbook.add_format({
        "bold": True, "font_color": "#FFFFFF", "bg_color": "#1E3A5F",
        "font_name": "Arial", "font_size": 10, "border": 1,
    })

    # Šířky sloupců
    col_widths = [18, 18, 14, 50, 22, 22, 22, 40]
    for i, w in enumerate(col_widths):
        worksheet.set_column(i, i, w)
    worksheet.set_row(0, 22)

    # Záhlaví
    for col, header in enumerate(COLUMNS):
        worksheet.write(0, col, header, header_fmt)

    # Data
    for row_idx, row in df.iterrows():
        fmt = alt_fmt if row_idx % 2 == 0 else row_fmt
        row_num = row_idx + 1
        worksheet.set_row(row_num, 18)
        for col_idx, col_name in enumerate(COLUMNS):
            worksheet.write(row_num, col_idx, row.get(col_name, ""), fmt)

    # Zdrojové URL pod tabulkou
    url_row = len(df) + 3
    worksheet.write(url_row, 0, "Zdrojové odkazy:", url_fmt)
    for i, src_url in enumerate(df["URL zdroje"].unique()):
        worksheet.write(url_row + 1 + i, 0, src_url, row_fmt)

    workbook.close()
    log.info(f"Excel uložen: {path} (list: {week_label})")


# ── E-mail ──────────────────────────────────────────────────────────────────
def send_email(path: str, count: int):
    if not EMAIL_FROM or not EMAIL_PASS:
        log.error("GMAIL_USER nebo GMAIL_PASS není nastaveno v GitHub Secrets.")
        return

    today = datetime.date.today().strftime("%d. %m. %Y")
    msg = EmailMessage()
    msg["Subject"] = f"Ice Deal Watch CZ — přehled zmrzlin {today}"
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg.set_content(
        f"Dobrý den,\n\n"
        f"v příloze je dnešní přehled akčních cen mražených krémů ({today}).\n"
        f"Celkem nalezeno {count} produktů z 10 řetězců.\n\n"
        f"Řetězce: Rohlík, Košík, Kaufland, Lidl, Albert, Billa, Penny, Globus, Tesco, Coop\n\n"
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
    log.info(f"E-mail odeslán na {EMAIL_TO}")


# ── Hlavní běh ───────────────────────────────────────────────────────────────
def main():
    log.info("=== Ice Deal Watch CZ — start ===")

    scrapers = [
        get_rohlik,
        get_kosik,
        get_kaufland,
        get_lidl,
        get_albert,
        get_billa,
        get_penny,
        get_globus,
        get_tesco,
        get_coop,
    ]

    all_rows = []
    for fn in scrapers:
        try:
            rows = fn()
            all_rows.extend(rows)
        except Exception as e:
            log.error(f"{fn.__name__} selhalo: {e}")

    if not all_rows:
        log.warning("Žádná data nebyla nalezena — e-mail nebude odeslán.")
        return

    df = pd.DataFrame(all_rows, columns=COLUMNS)
    log.info(f"Celkem nalezeno: {len(df)} produktů")

    save_excel(df, OUTPUT_FILE)
    send_email(OUTPUT_FILE, len(df))

    log.info("=== Hotovo ✓ ===")


if __name__ == "__main__":
    main()
