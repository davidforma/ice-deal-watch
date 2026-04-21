"""
Ice Deal Watch CZ — scraper.py v3
Zdroj dat: kupi.cz (agregátor letáků) — neblokuje GitHub servery.
Pokrytí: Albert, Penny, Lidl, Kaufland, Tesco, Billa, Globus, Rohlik, Kosik.
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
    "Původní cena",
    "Sleva %",
    "Doba platnosti akce",
    "URL zdroje",
]

# Kupi.cz URL pro každý řetězec zvlášť
KUPI_SHOPS = {
    "Albert":       "https://www.kupi.cz/slevy/zmrzliny/albert",
    "Penny":        "https://www.kupi.cz/slevy/zmrzliny/penny-market",
    "Lidl":         "https://www.kupi.cz/slevy/zmrzliny/lidl",
    "Kaufland":     "https://www.kupi.cz/slevy/zmrzliny/kaufland",
    "Tesco":        "https://www.kupi.cz/slevy/zmrzliny/tesco",
    "Billa":        "https://www.kupi.cz/slevy/zmrzliny/billa",
    "Globus":       "https://www.kupi.cz/slevy/zmrzliny/globus",
    "Rohlik":       "https://www.kupi.cz/slevy/zmrzliny/rohlik",
    "Kosik":        "https://www.kupi.cz/slevy/zmrzliny/kosik",
    "Coop":         "https://www.kupi.cz/slevy/zmrzliny/coop",
}


def get(url: str, timeout: int = 20):
    """HTTP GET s ošetřením chyb."""
    time.sleep(1.0)
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception as e:
        log.warning(f"GET {url} selhal: {e}")
        return None


def categorize(name: str) -> tuple:
    """Odhadne značku a kategorii z názvu produktu."""
    n = name.lower()

    brands = {
        "Magnum":           ["magnum"],
        "Algida":           ["algida", "cornetto", "solero"],
        "Carte d'Or":       ["carte d'or", "carte dor"],
        "Ben & Jerry's":    ["ben & jerry", "ben&jerry"],
        "Häagen-Dazs":      ["häagen", "haagen"],
        "Nestlé / Míša":    ["míša", "maxibon", "nestle", "nestlé"],
        "Gelatelli":        ["gelatelli"],
        "Magnum":           ["magnum"],
    }
    types = {
        "pinta":     ["pinta", "ben & jerry", "häagen", "haagen", "500 ml", "900 ml", "460 ml"],
        "multipack": ["6x", "4x", "8x", "3x", "multipack", "multi", "2x"],
        "vanička":   ["vanička", "kelímek", "kübel", "vaničce", "ve vaničce"],
        "nanuk":     ["nanuk", "tyčinka", "lízátko", "gigant"],
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


def scrape_kupi_shop(shop_name: str, url: str) -> list:
    """Scrapuje stránku kupi.cz pro jeden řetězec."""
    log.info(f"Scrapuji {shop_name} z kupi.cz...")
    r = get(url)
    if not r:
        log.warning(f"  → {shop_name}: stránka nedostupná")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    out = []

    # Kupi.cz zobrazuje produkty v kartičkách
    # Zkusíme různé selektory které kupi používá
    cards = (
        soup.select(".product-card") or
        soup.select(".sale-item") or
        soup.select("[class*='product-item']") or
        soup.select("[class*='sale-card']") or
        soup.select("article") or
        []
    )

    if not cards:
        # Fallback: zkusíme najít ceny podle struktury
        cards = soup.select("li")

    log.info(f"  → nalezeno {len(cards)} karet")

    for card in cards:
        try:
            # Název produktu
            name_el = (
                card.select_one("h2") or
                card.select_one("h3") or
                card.select_one("[class*='name']") or
                card.select_one("[class*='title']") or
                card.select_one("strong")
            )
            if not name_el:
                continue

            name = name_el.get_text(strip=True)
            if len(name) < 3:
                continue

            # Akční cena
            price_el = (
                card.select_one("[class*='sale-price']") or
                card.select_one("[class*='current-price']") or
                card.select_one("[class*='price-sale']") or
                card.select_one("[class*='new-price']") or
                card.select_one("[class*='price']")
            )
            price = price_el.get_text(strip=True) if price_el else ""

            # Původní cena
            orig_el = (
                card.select_one("[class*='original']") or
                card.select_one("[class*='old-price']") or
                card.select_one("del") or
                card.select_one("s")
            )
            orig_price = orig_el.get_text(strip=True) if orig_el else ""

            # Sleva %
            discount_el = (
                card.select_one("[class*='discount']") or
                card.select_one("[class*='badge']") or
                card.select_one("[class*='percent']")
            )
            discount = discount_el.get_text(strip=True) if discount_el else ""

            # Platnost
            valid_el = (
                card.select_one("[class*='valid']") or
                card.select_one("[class*='date']") or
                card.select_one("time")
            )
            valid = valid_el.get_text(strip=True) if valid_el else ""

            brand, category = categorize(name)

            out.append({
                "Název řetězce":        shop_name,
                "Značka":               brand,
                "Kategorie":            category,
                "Přesný název produktu": name,
                "Standardní akční cena": price,
                "Původní cena":         orig_price,
                "Sleva %":              discount,
                "Doba platnosti akce":  valid,
                "URL zdroje":           url,
            })
        except Exception:
            continue

    log.info(f"  → {shop_name}: {len(out)} produktů")
    return out


def scrape_kupi_all() -> list:
    """Scrapuje všechny řetězce z kupi.cz."""
    # Nejprve zkusíme souhrnnou stránku se všemi zmrzlinami
    all_url = "https://www.kupi.cz/slevy/zmrzliny"
    log.info("Scrapuji souhrnnou stránku kupi.cz/slevy/zmrzliny...")
    r = get(all_url)

    all_rows = []

    if r and len(r.text) > 5000:
        soup = BeautifulSoup(r.text, "html.parser")

        cards = (
            soup.select(".product-card") or
            soup.select(".sale-item") or
            soup.select("[class*='product-item']") or
            soup.select("article") or
            []
        )

        log.info(f"  → souhrnná stránka: {len(cards)} karet")

        for card in cards:
            try:
                name_el = (
                    card.select_one("h2") or
                    card.select_one("h3") or
                    card.select_one("[class*='name']") or
                    card.select_one("[class*='title']")
                )
                if not name_el:
                    continue
                name = name_el.get_text(strip=True)
                if len(name) < 3:
                    continue

                # Detekce řetězce z karty
                shop_el = (
                    card.select_one("[class*='shop']") or
                    card.select_one("[class*='store']") or
                    card.select_one("[class*='retailer']") or
                    card.select_one("img[alt]")
                )
                shop = ""
                if shop_el:
                    shop = shop_el.get("alt", "") or shop_el.get_text(strip=True)

                price_el = (
                    card.select_one("[class*='sale-price']") or
                    card.select_one("[class*='current-price']") or
                    card.select_one("[class*='price']")
                )
                price = price_el.get_text(strip=True) if price_el else ""

                orig_el = card.select_one("del") or card.select_one("s")
                orig_price = orig_el.get_text(strip=True) if orig_el else ""

                discount_el = card.select_one("[class*='discount']") or card.select_one("[class*='badge']")
                discount = discount_el.get_text(strip=True) if discount_el else ""

                valid_el = card.select_one("[class*='valid']") or card.select_one("time")
                valid = valid_el.get_text(strip=True) if valid_el else ""

                brand, category = categorize(name)

                all_rows.append({
                    "Název řetězce":        shop or "Neznámý",
                    "Značka":               brand,
                    "Kategorie":            category,
                    "Přesný název produktu": name,
                    "Standardní akční cena": price,
                    "Původní cena":         orig_price,
                    "Sleva %":              discount,
                    "Doba platnosti akce":  valid,
                    "URL zdroje":           all_url,
                })
            except Exception:
                continue

    # Vždy scrapujeme i jednotlivé stránky pro každý řetězec
    for shop_name, shop_url in KUPI_SHOPS.items():
        rows = scrape_kupi_shop(shop_name, shop_url)
        # Přidáme jen ty, které nejsou duplikáty
        existing_names = {r["Přesný název produktu"] + r["Název řetězce"] for r in all_rows}
        for row in rows:
            key = row["Přesný název produktu"] + row["Název řetězce"]
            if key not in existing_names:
                all_rows.append(row)
                existing_names.add(key)

    log.info(f"Celkem unikátních produktů: {len(all_rows)}")
    return all_rows


def save_excel(rows: list, path: str):
    """Uloží data do Excelu s formátováním."""
    if not rows:
        return

    df = pd.DataFrame(rows, columns=COLUMNS)
    week_label = datetime.date.today().strftime("Tyden_%Y_%W")

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
    price_fmt = workbook.add_format({
        "border": 1, "align": "center", "valign": "vcenter",
        "font_name": "Arial", "font_size": 10, "bold": True,
        "font_color": "#C0392B",
    })
    price_alt_fmt = workbook.add_format({
        "border": 1, "align": "center", "valign": "vcenter",
        "font_name": "Arial", "font_size": 10, "bold": True,
        "font_color": "#C0392B", "bg_color": "#EEF4FB",
    })

    # Šířky sloupců
    col_widths = [14, 18, 12, 52, 16, 14, 10, 24, 36]
    for i, w in enumerate(col_widths):
        worksheet.set_column(i, i, w)
    worksheet.set_row(0, 22)

    # Záhlaví
    for col, header in enumerate(COLUMNS):
        worksheet.write(0, col, header, header_fmt)

    # Data
    for row_idx, row in df.iterrows():
        is_alt = row_idx % 2 == 0
        fmt     = alt_fmt if is_alt else row_fmt
        p_fmt   = price_alt_fmt if is_alt else price_fmt
        row_num = row_idx + 1
        worksheet.set_row(row_num, 18)

        for col_idx, col_name in enumerate(COLUMNS):
            val = str(row.get(col_name, ""))
            # Ceny červeně
            if col_name in ("Standardní akční cena", "Sleva %"):
                worksheet.write(row_num, col_idx, val, p_fmt)
            else:
                worksheet.write(row_num, col_idx, val, fmt)

    # Shrnutí podle řetězce
    summary_row = len(df) + 3
    worksheet.write(summary_row, 0, "Počet produktů podle řetězce:", src_fmt)
    counts = df["Název řetězce"].value_counts()
    for i, (shop, count) in enumerate(counts.items()):
        worksheet.write(summary_row + 1 + i, 0, shop, row_fmt)
        worksheet.write(summary_row + 1 + i, 1, count, row_fmt)

    # Zdrojové URL
    url_row = summary_row + len(counts) + 3
    worksheet.write(url_row, 0, "Zdrojové odkazy:", src_fmt)
    for i, src_url in enumerate(df["URL zdroje"].unique()):
        worksheet.write(url_row + 1 + i, 0, src_url, row_fmt)

    workbook.close()
    log.info(f"Excel ulozen: {path} ({week_label}, {len(df)} radku)")
    return df


def send_email(path: str, df: pd.DataFrame):
    """Odešle e-mail s Excel přílohou."""
    if not EMAIL_FROM or not EMAIL_PASS:
        log.error("GMAIL_USER nebo GMAIL_PASS neni nastaveno!")
        return

    today     = datetime.date.today().strftime("%d. %m. %Y")
    counts    = df["Název řetězce"].value_counts().to_dict()
    shops_str = "\n".join([f"  • {k}: {v} produktů" for k, v in counts.items()])

    msg = EmailMessage()
    msg["Subject"] = f"Ice Deal Watch CZ — zmrzliny v akci {today}"
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg.set_content(
        f"Dobrý den,\n\n"
        f"v příloze je dnešní přehled akčních cen mražených krémů ({today}).\n\n"
        f"Celkem nalezeno: {len(df)} produktů\n\n"
        f"Podle řetězce:\n{shops_str}\n\n"
        f"Zdroj dat: kupi.cz (agregátor letáků)\n\n"
        f"— Ice Deal Watch CZ"
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
    log.info(f"E-mail odeslan na {EMAIL_TO}")


def send_no_data_email():
    """Záchranný e-mail když nejsou data."""
    if not EMAIL_FROM or not EMAIL_PASS:
        return
    today = datetime.date.today().strftime("%d. %m. %Y")
    msg = EmailMessage()
    msg["Subject"] = f"Ice Deal Watch CZ — {today} — zadna data"
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg.set_content(
        f"Dobrý den,\n\n"
        f"Skript Ice Deal Watch CZ proběhl dnes ({today}), ale nepodařilo se načíst žádné produkty.\n\n"
        f"Možné příčiny: kupi.cz změnil strukturu stránek.\n"
        f"Zkontrolujte logy v GitHub Actions.\n\n"
        f"— Ice Deal Watch CZ"
    )
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(EMAIL_FROM, EMAIL_PASS)
        server.send_message(msg)
    log.info(f"Zachranny e-mail odeslan na {EMAIL_TO}")


def main():
    log.info("=== Ice Deal Watch CZ v3 (kupi.cz) — start ===")

    rows = scrape_kupi_all()

    if not rows:
        log.warning("Zadna data — odesílám záchranný e-mail.")
        send_no_data_email()
        return

    df = save_excel(rows, OUTPUT_FILE)
    send_email(OUTPUT_FILE, df)
    log.info("=== Hotovo ===")


if __name__ == "__main__":
    main()
