#!/usr/bin/env python3
"""
Scraper obedových menu pre Kreston Slovakia.
Spúšťa sa cez GitHub Actions každý pracovný deň ráno.
Výstup: menus.json (servírovaný cez GitHub Pages, žiadne CORS problémy).
"""

import json
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag

BRAT = ZoneInfo("Europe/Bratislava")
now  = datetime.now(BRAT)

SK_DAYS_PY = ["Pondelok","Utorok","Streda","Štvrtok","Piatok","Sobota","Nedeľa"]
TODAY_NAME  = SK_DAYS_PY[now.weekday()]
TODAY_DATE  = now.strftime("%d.%m.%Y")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

DAY_CLASS_MAP = {
    "Pondelok": "pondelok",
    "Utorok":   "utorok",
    "Streda":   "streda",
    "Štvrtok":  "stvrtok",
    "Piatok":   "piatok",
    "Sobota":   "sobota",
    "Nedeľa":   "nedela",
}


def fetch(url: str) -> str | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  ✗ {url}: {e}", file=sys.stderr)
        return None


def sections_to_list(sections: dict) -> list:
    result = []
    for title, items in sections.items():
        if items:
            result.append({"title": title, "items": items})
    return result


# ─── Lunch Break ──────────────────────────────────────────────────────────────

def parse_lunch_break(html: str) -> list:
    doc = BeautifulSoup(html, "html.parser")
    sections = {
        "🥣 Polievky":       [],
        "🍖 Mäsové menu":    [],
        "🥗 Bezmäsité menu": [],
        "⭐ Špeciál menu":   [],
    }

    toggle_content = None
    for t in doc.select(".et_pb_toggle"):
        title_el = t.select_one(".et_pb_toggle_title")
        if title_el and TODAY_DATE in title_el.get_text():
            toggle_content = t.select_one(".et_pb_toggle_content")
            if toggle_content:
                break

    if not toggle_content:
        for t in doc.select(".et_pb_toggle"):
            if "et_pb_toggle_open" in (t.get("class") or []):
                toggle_content = t.select_one(".et_pb_toggle_content")
                if toggle_content:
                    break

    if not toggle_content:
        toggle_content = doc.select_one(".et_pb_toggle_content")

    if not toggle_content:
        return sections_to_list(sections)

    cur          = None
    pending_name = None

    for p in toggle_content.select("p"):
        text = p.get_text(separator="\n").strip()
        if not text:
            continue
        up = text.upper()

        if "POLIEVKA" in up and "V CENE" not in up:
            cur = "🥣 Polievky"; pending_name = None; continue
        if "MÄSOVÉ MENU" in up:
            cur = "🍖 Mäsové menu"; pending_name = None; continue
        if "BEZMÄSITÉ MENU" in up:
            cur = "🥗 Bezmäsité menu"; pending_name = None; continue
        if up.startswith("ŠPECIÁL") or up.startswith("SPECIAL"):
            cur = "⭐ Špeciál menu"; pending_name = None; continue
        if "SLADKÉ MENU" in up or "SLADKE MENU" in up:
            cur = "⭐ Špeciál menu"; pending_name = None; continue
        if "V CENE MENU" in up:
            continue

        if not cur:
            continue

        # Polievky
        if cur == "🥣 Polievky":
            # Preskočiť ceny a nerozpoznané CAPS hlavičky
            if re.match(r"^\d", text) and "€" in text:
                continue
            stripped = re.sub(r"\s+", "", text)
            if stripped.isupper() and len(text) < 30:
                continue
            cleaned = re.sub(r"^\d[,.]?\d*l\s*", "", text, flags=re.IGNORECASE)
            for s in cleaned.split("/"):
                name = re.sub(r"\s*\(\d[\d,]*\)\s*$", "", s).strip()
                if len(name) > 3:
                    sections[cur].append({"name": name, "price": "v cene menu"})
            continue

        # Iba cena "9,90 €"
        only_price = re.match(r"^(\d+[.,]\d+)\s*€$", text)
        if only_price:
            if pending_name:
                sections[cur].append({
                    "name":  pending_name,
                    "price": only_price.group(1).replace(",", ".") + " €",
                })
                pending_name = None
            continue

        pending_name = None

        # Viaceré riadky: posledný je cena
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        if len(lines) >= 2:
            pm = re.match(r"^(\d+[.,]\d+)\s*€$", lines[-1])
            if pm:
                price = pm.group(1).replace(",", ".") + " €"
                name  = " ".join(lines[:-1])
                name  = re.sub(r"\s*\(\d[\d,]*\)\s*$", "", name).strip()
                if len(name) > 3:
                    sections[cur].append({"name": name, "price": price})
                continue

        # Inline cena "Jedlo 9,50 €"
        inline = re.match(r"^(.+?)\s+(\d+[.,]\d+)\s*€", text)
        if inline:
            name  = re.sub(r"\s*\(\d[\d,]*\)\s*$", "", inline.group(1)).strip()
            price = inline.group(2).replace(",", ".") + " €"
            if len(name) > 3:
                sections[cur].append({"name": name, "price": price})
            continue

        # Iba popis — cena príde v ďalšom odstavci
        name = re.sub(r"\s*\(\d[\d,]*\)\s*$", "", text).strip()
        if len(name) > 3:
            pending_name = name

    return sections_to_list(sections)


# ─── Foodgarden ───────────────────────────────────────────────────────────────

def extract_recipe_items(container: Tag | None) -> list:
    if not container:
        return []
    items = []
    for item in container.select("div.recipe_item"):
        h3    = item.select_one("div.ri_text h3")
        price = item.select_one("div.ri_cart h5.price, div.ri_cart h5")
        if h3 and price:
            items.append({
                "name":  h3.get_text().strip(),
                "price": re.sub(r"\s+", " ", price.get_text()).strip(),
            })
    return items


def parse_foodgarden(html: str, has_vegan: bool = True) -> list:
    doc = BeautifulSoup(html, "html.parser")
    sections: dict[str, list] = {}
    if has_vegan:
        sections["🍽️ Denné menu"]   = []
        sections["🌱 Vegánske menu"] = []
        sections["📋 Stála ponuka"]  = []
    else:
        sections["🍽️ Denné menu"]   = []
        sections["📋 Stála ponuka"]  = []

    day_cls = DAY_CLASS_MAP.get(TODAY_NAME, TODAY_NAME.lower())
    scope   = (
        doc.select_one(f"div.weekly_menu .{day_cls}") or
        doc.select_one(f".{day_cls}") or
        doc.select_one(".dnes_varime") or
        doc.body
    )

    sections["🍽️ Denné menu"].extend(extract_recipe_items(
        scope.select_one("div.druh_menu.menu_klasik") if scope else None
    ))
    if has_vegan:
        sections["🌱 Vegánske menu"].extend(extract_recipe_items(
            scope.select_one("div.druh_menu.menu_vegan") if scope else None
        ))
    sections["📋 Stála ponuka"].extend(extract_recipe_items(
        scope.select_one("div.druh_menu.stala_ponuka") if scope else None
    ))

    return sections_to_list(sections)


# ─── Piknik ───────────────────────────────────────────────────────────────────

def parse_piknik(html: str) -> list:
    doc = BeautifulSoup(html, "html.parser")
    sections = {
        "🥣 Polievka": [],
        "🍽️ Menu":      [],
        "⭐ Špeciál":   [],
    }

    today_container = None
    for dc in doc.select("div.day-container"):
        date_div = dc.select_one(".day-date")
        if date_div and date_div.get_text().strip().upper().startswith(TODAY_NAME.upper()):
            today_container = dc
            break

    if not today_container:
        return sections_to_list(sections)

    for option in today_container.select("div.day-menu-option"):
        label    = (option.select_one("b") or BeautifulSoup("", "html.parser")).get_text().strip().rstrip(":").lower()
        meal_div = option.select_one("div.day-menu-option-meal")
        if not meal_div:
            continue

        price_el = meal_div.select_one("strong")
        price    = price_el.get_text().strip() if price_el else "v cene menu"

        name = ""
        for node in meal_div.children:
            if isinstance(node, str):
                name = node.strip()
                if name:
                    break
        name = re.sub(r"\s*\(\d[\d,]*\)\s*$", "", name).strip()
        if not name:
            continue

        if "polievka" in label:
            sections["🥣 Polievka"].append({"name": name, "price": price})
        elif "špeciál" in label or "special" in label:
            sections["⭐ Špeciál"].append({"name": name, "price": price})
        else:
            sections["🍽️ Menu"].append({"name": name, "price": price})

    return sections_to_list(sections)


# ─── Hlavná logika ────────────────────────────────────────────────────────────

def main():
    print(f"Scraping menu pre {TODAY_NAME} {TODAY_DATE} …")

    result: dict = {
        "generated": now.isoformat(),
        "date":      TODAY_DATE,
        "dayName":   TODAY_NAME,
    }

    print("  Lunch Break …")
    html = fetch("https://www.lunch-break.sk/menu-apollo/")
    result["lunchbreak"] = {"sections": parse_lunch_break(html)} if html else {"error": True}

    print("  Foodgarden Pressburg …")
    html = fetch("https://pressburg.foodgarden.sk/denne-menu-foodgarden-pressburg/")
    result["fp"] = {"sections": parse_foodgarden(html, has_vegan=True)} if html else {"error": True}

    print("  Foodgarden Apollo …")
    html = fetch("https://apollo.foodgarden.sk/denne-menu-apollo/")
    result["fa"] = {"sections": parse_foodgarden(html, has_vegan=False)} if html else {"error": True}

    print("  Piknik Plynárenská …")
    html = fetch("https://bencikculinary.sk/picknik/")
    result["piknik"] = {"sections": parse_piknik(html)} if html else {"error": True}

    with open("menus.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print("✓ menus.json uložený")


if __name__ == "__main__":
    main()
