#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Анику — Единый мониторинг (Дром + сайт aniku.ru)

Парсит оба источника, сопоставляет по артикулам, формирует единый отчет.
Каждый артикул в одном экземпляре, без дублей.
"""

import argparse
import json
import os
import re
import smtplib
import sys
from collections import Counter, defaultdict
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

# ==================== ИСТОЧНИКИ ====================

SOURCE_DROM = "Дром (Анику)"
SOURCE_SITE = "aniku.ru"

HEADERS_DROM = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}
HEADERS_SITE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html",
}

BASE_DROM = "https://baza.drom.ru/user/Aniku/wheel/disc/"
BASE_SITE = "https://aniku.ru/fulllist"

SNAPSHOT_FILE = Path(__file__).parent / "snapshot.json"


# ==================== ИЗВЛЕЧЕНИЕ АРТИКУЛА ====================

def extract_article(title: str) -> Optional[str]:
    """Извлекает артикул из названия товара. Примеры: (S023), (B103), (FG007-FG008)."""
    if not title:
        return None
    patterns = [
        r'\(([A-Z]{1,5}\d{2,}[A-Z]?)\)',
        r'\(([A-Z]{1,5}\d{2,}-[A-Z]{1,5}\d{2,})\)',
        r'\(([A-Z]\d{2,}[A-Z]?)\)',
    ]
    for pat in patterns:
        m = re.search(pat, title)
        if m:
            return m.group(1)
    return None


def extract_brand(title: str) -> str:
    title = title.upper()
    brands = [
        ("BMW", "BMW"), ("HRE", "HRE"), ("VOSSEN", "Vossen"), ("RAYS", "RAYS"),
        ("TE37", "RAYS"), ("CE28", "RAYS"), ("VOLK", "RAYS"), ("57X", "RAYS"),
        ("BBS", "BBS"), ("ADVAN", "Advan"), ("SSR", "SSR"), ("WORK", "Work"),
        ("SHOGUN", "Shogun"), ("WALD", "Wald"), ("XXR", "XXR"), ("ENKEI", "Enkei"),
        ("RGW", "RGW"), ("FBX", "FBX"), ("MLJ", "MLJ"), ("KAHH", "Kahn"),
        ("KAHN", "Kahn"), ("MANSORY", "Mansory"), ("NISMO", "Nismo"),
        ("ADV.1", "ADV.1"), ("PROCAST", "Procast"), ("PRODRIVE", "ProDrive"),
        ("MHT", "MHT"), ("BUDDY", "Buddy Club"), ("OASIS", "Oasis"),
        ("PLATIN", "Platin"), ("TUFF", "Tuff A.T."), ("VORSTEINER", "Vorsteiner"),
        ("WEDS", "Weds"), ("ROTA", "Rota"), ("OZ", "OZ"), ("DUB", "DUB"),
        ("INFINITY", "Infinity"), ("BLACK RHINO", "Black Rhino"), ("PDW", "PDW"),
        ("VPS", "VPS"), ("NIVA", "NIVA"), ("STYLE", "Style"),
        ("TAW", "TAW"), ("KOSEI", "Kosei"), ("YOKOHAMA", "Yokohama"),
        ("AVID", "Avid"), ("CONCEPT", "Concept"), ("SHOWY", "Showy"),
        ("COSMIS", "Cosmis"), ("GFS", "GFS"),
    ]
    for keyword, brand_name in brands:
        if keyword in title:
            return brand_name
    return "Other"


# ==================== ПАРСИНГ ДРОМА ====================

def fetch_drom_page(page_num: int) -> Optional[str]:
    params = {
        "condition%5B%5D": "new",
        "goodPresentState%5B%5D": "present",
        "inSetQuantity%5B%5D": ["1", "2", "4", "5"],
        "center": "131.95554587876572,43.13602108559458",
        "zoom": "16",
        "page": page_num
    }
    try:
        resp = requests.get(BASE_DROM, params=params, headers=HEADERS_DROM, timeout=30)
        resp.encoding = "cp1251"
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None


def parse_drom_listings(html: str) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    items = soup.find_all("div", class_=lambda x: x and "bull-item_inline" in x)
    listings = []
    for item in items:
        try:
            img = item.find("img", class_="bull-image-preloader")
            title = img["alt"].replace(" фото", "") if img else None
            price_elem = item.find("div", {"data-role": "price"})
            price = None
            if price_elem:
                pt = price_elem.get_text(strip=True)
                pc = re.sub(r"[^\d]", "", pt)
                price = int(pc) if pc else None
            link_elem = item.find("a", class_="bull-item__self-link")
            link_title = link_elem.get_text(strip=True) if link_elem else None
            link_href = link_elem["href"] if link_elem else None
            specs_elem = item.find("div", class_="bull-item__annotation-row")
            specs = specs_elem.get_text(strip=True) if specs_elem else None
            diam = None
            if specs:
                m = re.search(r'(\d+)x(\d+)"', specs)
                if m:
                    diam = int(m.group(2))
                else:
                    m = re.search(r'R(\d+)', title or "")
                    if m:
                        diam = int(m.group(1))
            brand = extract_brand(title or link_title or "")
            lid = link_href.split("-")[-1].replace(".html", "") if link_href else None
            article = extract_article(title or link_title or "")
            if title and price and lid:
                listings.append({
                    "id": lid,
                    "title": title or link_title,
                    "price": price,
                    "specs": specs,
                    "diameter": diam,
                    "brand": brand,
                    "article": article,
                    "href": f"https://baza.drom.ru{link_href}" if link_href and not link_href.startswith("http") else link_href,
                })
        except Exception:
            continue
    return listings


def fetch_all_drom() -> List[Dict]:
    all_items = []
    seen = set()
    print(f"[INFO] Загружаем {SOURCE_DROM}...")
    for page in range(1, 18):
        html = fetch_drom_page(page)
        if not html:
            continue
        items = parse_drom_listings(html)
        for it in items:
            if it["id"] not in seen:
                seen.add(it["id"])
                all_items.append(it)
        print(f"  Стр. {page}: {len(items)} (всего: {len(all_items)})")
        if len(items) < 10:
            break
    print(f"[OK] {SOURCE_DROM}: {len(all_items)} позиций, {sum(1 for x in all_items if x['article'])} с артикулом")
    return all_items


# ==================== ПАРСИНГ aniku.ru ====================

def fetch_site_page(page_num: int) -> Optional[str]:
    try:
        resp = requests.get(BASE_SITE, params={"page": page_num}, headers=HEADERS_SITE, timeout=30)
        resp.encoding = "utf-8"
        if resp.status_code == 200:
            return resp.text
    except Exception:
        pass
    return None


def parse_site_products(html: str) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    products = []
    for li in soup.find_all("li", class_="product"):
        try:
            link = li.find("a", href=re.compile(r"fulllist\?id="))
            if not link:
                continue
            href = link.get("href", "")
            id_match = re.search(r"id=(\d+)", href)
            product_id = id_match.group(1) if id_match else None
            title = link.get_text(strip=True)
            title = re.sub(r'\s*во\s+Владивостоке\s*$', '', title, flags=re.IGNORECASE)
            price_tag = li.find("h2", style=re.compile(r"color:blue", re.I))
            price = None
            if price_tag:
                price_text = price_tag.get_text(strip=True)
                price = int(re.sub(r"[^\d]", "", price_text))
            text = li.get_text(separator=" ", strip=True)
            w_match = re.search(r'Ширина:\s*([\d.]+)"', text)
            width = float(w_match.group(1)) if w_match else None
            diam_match = re.search(r'Ширина:\s*[\d.]+"x(\d+)', text)
            diameter = int(diam_match.group(1)) if diam_match else None
            pcd_match = re.search(r'PCD:\s*([\dx/]+)', text)
            pcd = pcd_match.group(1) if pcd_match else None
            et_match = re.search(r'ET:\s*(\d+)', text)
            et = int(et_match.group(1)) if et_match else None
            cb_match = re.search(r'ЦО:\s*([\d,]+)', text)
            cb = cb_match.group(1).replace(",", ".") if cb_match else None
            brand = extract_brand(title)
            article = extract_article(title)
            if product_id and title and price:
                products.append({
                    "id": f"site_{product_id}",
                    "title": title,
                    "price": price,
                    "diameter": diameter,
                    "width": width,
                    "pcd": pcd,
                    "et": et,
                    "cb": cb,
                    "brand": brand,
                    "article": article,
                    "href": f"https://aniku.ru{href}" if href.startswith("/") else href,
                })
        except Exception:
            continue
    return products


def fetch_all_site() -> List[Dict]:
    import time
    all_items = []
    seen = set()
    print(f"[INFO] Загружаем {SOURCE_SITE}...")
    for page in range(0, 47):
        html = fetch_site_page(page)
        if not html:
            continue
        items = parse_site_products(html)
        for it in items:
            if it["id"] not in seen:
                seen.add(it["id"])
                all_items.append(it)
        print(f"  Стр. {page}: {len(items)} (всего: {len(all_items)})")
        if not items:
            break
        time.sleep(0.2)
    print(f"[OK] {SOURCE_SITE}: {len(all_items)} позиций, {sum(1 for x in all_items if x['article'])} с артикулом")
    return all_items


# ==================== ОБЪЕДИНЕНИЕ ПО АРТИКУЛАМ ====================

def merge_by_article(drom_items: List[Dict], site_items: List[Dict]) -> Tuple[Dict, Dict, Dict]:
    drom_by_art = {}
    for it in drom_items:
        art = it.get("article")
        if art:
            drom_by_art[art] = it

    site_by_art = {}
    for it in site_items:
        art = it.get("article")
        if art:
            site_by_art[art] = it

    all_articles = set(drom_by_art.keys()) | set(site_by_art.keys())

    merged = {}
    only_site = {}
    only_drom = {}

    for art in all_articles:
        d = drom_by_art.get(art)
        s = site_by_art.get(art)

        if d and s:
            merged[art] = {
                "article": art,
                "brand": d.get("brand") or s.get("brand"),
                "diameter": d.get("diameter") or s.get("diameter"),
                "title_drom": d["title"],
                "title_site": s["title"],
                "price_drom": d["price"],
                "price_site": s["price"],
                "href_drom": d["href"],
                "href_site": s["href"],
            }
        elif s and not d:
            only_site[art] = s
        elif d and not s:
            only_drom[art] = d

    return merged, only_site, only_drom


# ==================== СНАПШОТ ====================

def load_snapshot() -> Optional[Dict]:
    if SNAPSHOT_FILE.exists():
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_snapshot(data: Dict):
    try:
        with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"[INFO] Снапшот сохранен: {SNAPSHOT_FILE}")
    except TypeError as e:
        print(f"[ERROR] Ошибка сериализации JSON: {e}")
        clean_data = {
            "drom_total": data.get("drom_total", 0),
            "site_total": data.get("site_total", 0),
            "timestamp": data.get("timestamp", datetime.now().isoformat()),
            "merged_count": len(data.get("merged", {})),
            "only_site_count": len(data.get("only_site", {})),
            "only_drom_count": len(data.get("only_drom", {})),
        }
        with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
            json.dump(clean_data, f, ensure_ascii=False, indent=2)
        print(f"[WARN] Сохранена упрощенная версия снапшота")


def compare_with_previous(current: Dict, previous) -> Dict:
    """Сравнивает текущее состояние с предыдущим. Возвращает статистику изменений."""
    stats = {
        "drom_total": current.get("drom_total", 0),
        "site_total": current.get("site_total", 0),
        "merged": len(current.get("merged", {})),
        "only_site": len(current.get("only_site", {})),
        "only_drom": len(current.get("only_drom", {})),
    }

    # Если previous — старый формат (list) или None — пропускаем сравнение
    if not previous or isinstance(previous, list):
        if isinstance(previous, list):
            print("[WARN] Обнаружен старый формат снапшота (list). Сравнение пропущено.")
        return stats

    prev_merged = previous.get("merged", {})
    curr_merged = current.get("merged", {})
    price_changes = []
    for art, data in curr_merged.items():
        if art in prev_merged:
            old = prev_merged[art]
            if old.get("price_site") != data.get("price_site") or old.get("price_drom") != data.get("price_drom"):
                price_changes.append({
                    "article": art,
                    "brand": data["brand"],
                    "title": data["title_site"][:50],
                    "old_site": old.get("price_site"),
                    "new_site": data.get("price_site"),
                    "old_drom": old.get("price_drom"),
                    "new_drom": data.get("price_drom"),
                })

    prev_only_site = set(previous.get("only_site", {}).keys())
    curr_only_site = set(current.get("only_site", {}).keys())
    new_site_items = [current["only_site"][art] for art in (curr_only_site - prev_only_site)]

    prev_only_drom = set(previous.get("only_drom", {}).keys())
    curr_only_drom = set(current.get("only_drom", {}).keys())
    new_drom_items = [current["only_drom"][art] for art in (curr_only_drom - prev_only_drom)]

    stats["price_changes"] = price_changes
    stats["new_site_items"] = new_site_items
    stats["new_drom_items"] = new_drom_items
    stats["prev_merged_count"] = len(prev_merged)
    stats["price_change_count"] = len(price_changes)

    return stats


# ==================== ОТЧЕТ (единая таблица без дублей) ====================

def build_html_report(current: Dict, stats: Dict) -> str:
    date_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    merged = current.get("merged", {})
    only_site = current.get("only_site", {})
    only_drom = current.get("only_drom", {})

    # Собираем все артикулы в единый список (без дублей)
    all_items = []

    # 1. Дубли — одна строка
    for art, data in merged.items():
        all_items.append({
            "article": art,
            "brand": data["brand"],
            "diameter": data.get("diameter"),
            "title": data["title_site"],
            "price_site": data["price_site"],
            "price_drom": data["price_drom"],
            "href_site": data["href_site"],
            "href_drom": data["href_drom"],
            "status": "Оба",
            "status_icon": "🟢",
            "status_color": "#2e7d32",
        })

    # 2. Только на сайте
    for art, item in only_site.items():
        all_items.append({
            "article": art,
            "brand": item["brand"],
            "diameter": item.get("diameter"),
            "title": item["title"],
            "price_site": item["price"],
            "price_drom": None,
            "href_site": item["href"],
            "href_drom": None,
            "status": "Только сайт",
            "status_icon": "🟡",
            "status_color": "#ed6c00",
        })

    # 3. Только на Дроме
    for art, item in only_drom.items():
        all_items.append({
            "article": art,
            "brand": item["brand"],
            "diameter": item.get("diameter"),
            "title": item["title"],
            "price_site": None,
            "price_drom": item["price"],
            "href_site": None,
            "href_drom": item["href"],
            "status": "Только Дром",
            "status_icon": "🔴",
            "status_color": "#c62828",
        })

    # Сортируем: Только сайт → Оба → Только Дром, затем по цене
    status_order = {"Только сайт": 0, "Оба": 1, "Только Дром": 2}
    all_items.sort(key=lambda x: (status_order[x["status"]], -(x["price_site"] or x["price_drom"] or 0)))

    # Карточки статистики
    cards = f"""
    <div style="display:flex;gap:10px;margin:20px 0;flex-wrap:wrap;">
        <div style="background:#e3f2fd;padding:14px;border-radius:6px;text-align:center;min-width:90px;">
            <div style="font-size:20px;font-weight:bold;color:#1565c0;">{stats['drom_total']}</div>
            <div style="font-size:11px;color:#666;">{SOURCE_DROM}</div>
        </div>
        <div style="background:#e8f5e9;padding:14px;border-radius:6px;text-align:center;min-width:90px;">
            <div style="font-size:20px;font-weight:bold;color:#2e7d32;">{stats['site_total']}</div>
            <div style="font-size:11px;color:#666;">{SOURCE_SITE}</div>
        </div>
        <div style="background:#f3e5f5;padding:14px;border-radius:6px;text-align:center;min-width:90px;">
            <div style="font-size:20px;font-weight:bold;color:#6a1b9a;">{len(all_items)}</div>
            <div style="font-size:11px;color:#666;">Уникальных арт.</div>
        </div>
        <div style="background:#e0f2f1;padding:14px;border-radius:6px;text-align:center;min-width:90px;">
            <div style="font-size:20px;font-weight:bold;color:#00695c;">{stats['merged']}</div>
            <div style="font-size:11px;color:#666;">🟢 Оба</div>
        </div>
        <div style="background:#fff3e0;padding:14px;border-radius:6px;text-align:center;min-width:90px;">
            <div style="font-size:20px;font-weight:bold;color:#ed6c00;">{stats['only_site']}</div>
            <div style="font-size:11px;color:#666;">🟡 Только сайт</div>
        </div>
        <div style="background:#ffebee;padding:14px;border-radius:6px;text-align:center;min-width:90px;">
            <div style="font-size:20px;font-weight:bold;color:#c62828;">{stats['only_drom']}</div>
            <div style="font-size:11px;color:#666;">🔴 Только Дром</div>
        </div>
    </div>"""

    # Единая таблица всех артикулов
    rows = ""
    for item in all_items:
        ps = f"{item['price_site']:,.0f} ₽" if item['price_site'] else "—"
        pd = f"{item['price_drom']:,.0f} ₽" if item['price_drom'] else "—"
        d = f"R{item['diameter']}" if item['diameter'] else "—"
        title_link = item['title'][:50]
        if item['href_site']:
            title_link = f'<a href="{item["href_site"]}">{title_link}</a>'
        elif item['href_drom']:
            title_link = f'<a href="{item["href_drom"]}">{title_link}</a>'

        bg = {"Только сайт": "#fff8e1", "Оба": "", "Только Дром": "#ffebee"}[item["status"]]

        rows += f"""
        <tr style="background:{bg};">
            <td style="padding:8px;border-bottom:1px solid #eee;font-family:monospace;font-size:12px;font-weight:bold;">{item['article']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;text-align:center;font-size:16px;">{item['status_icon']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;color:{item['status_color']};font-size:11px;font-weight:bold;">{item['status']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;">{item['brand']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;">{title_link}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;text-align:center;">{d}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;">{ps}</td>
            <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;">{pd}</td>
        </tr>"""

    full_table = f"""
    <h3 style="color:#333;margin-top:25px;">📋 Полный каталог артикулов Анику ({len(all_items)} шт.)</h3>
    <p style="color:#666;font-size:12px;margin-bottom:10px;">
        🟢 Оба источника | 🟡 Только aniku.ru (стоит выложить на Дром) | 🔴 Только Дром (проверить)
    </p>
    <div style="overflow-x:auto;">
    <table style="width:100%;border-collapse:collapse;font-size:12px;min-width:800px;">
        <thead><tr style="background:#f5f5f5;">
            <th style="padding:10px;text-align:left;width:70px;">Арт.</th>
            <th style="padding:10px;text-align:center;width:30px;"></th>
            <th style="padding:10px;text-align:left;width:90px;">Статус</th>
            <th style="padding:10px;text-align:left;">Бренд</th>
            <th style="padding:10px;text-align:left;">Название</th>
            <th style="padding:10px;text-align:center;">R</th>
            <th style="padding:10px;text-align:right;">aniku.ru</th>
            <th style="padding:10px;text-align:right;">Дром</th>
        </tr></thead>
        <tbody>{rows}</tbody>
    </table>
    </div>"""

    # Блок: Изменения цен
    price_change_section = ""
    price_changes = stats.get("price_changes", [])
    if price_changes:
        pc_rows = ""
        for pc in price_changes[:15]:
            pc_rows += f"""
            <tr>
                <td style="padding:8px;border-bottom:1px solid #eee;font-family:monospace;font-size:12px;">{pc['article']}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;">{pc['brand']}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;">{pc['title'][:45]}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;text-decoration:line-through;color:#999;">{pc['old_site']:,.0f} / {pc['old_drom']:,.0f}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;font-weight:bold;">{pc['new_site']:,.0f} / {pc['new_drom']:,.0f}</td>
            </tr>"""
        price_change_section = f"""
        <h3 style="color:#ef6c00;margin-top:25px;">💰 Изменения цен с прошлого запуска ({len(price_changes)})</h3>
        <table style="width:100%;border-collapse:collapse;font-size:12px;">
            <thead><tr style="background:#f5f5f5;">
                <th style="padding:8px;text-align:left;width:70px;">Арт.</th>
                <th style="padding:8px;text-align:left;">Бренд</th>
                <th style="padding:8px;text-align:left;">Название</th>
                <th style="padding:8px;text-align:right;">Старая цена (сайт/дром)</th>
                <th style="padding:8px;text-align:right;">Новая цена (сайт/дром)</th>
            </tr></thead>
            <tbody>{pc_rows}</tbody>
        </table>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;background:#fafafa;padding:20px;">
<div style="max-width:1100px;margin:0 auto;background:#fff;padding:30px;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.1);">
    <h2 style="color:#333;border-bottom:2px solid #4472C4;padding-bottom:10px;">
        📊 Анику — Единый каталог (без дублей)
    </h2>
    <p style="color:#666;">Дата: <strong>{date_str}</strong> | Каждый артикул в одном экземпляре</p>
    {cards}
    {full_table}
    {price_change_section}
    <hr style="margin:30px 0;border:none;border-top:1px solid #eee;">
    <p style="color:#999;font-size:11px;text-align:center;">
        Анику Monitor | {SOURCE_DROM} + {SOURCE_SITE} |
        <a href="https://aniku.ru/fulllist">aniku.ru</a> |
        <a href="https://baza.drom.ru/user/Aniku/wheel/disc/">Дром</a>
    </p>
</div>
</body></html>"""
    return html


def send_email(to_email: str, html: str, smtp_user: str, smtp_pass: str,
               smtp_host: str = "smtp.mail.ru", smtp_port: int = 465) -> bool:
    if not smtp_user or not smtp_pass:
        print("[ERROR] Не указаны SMTP-логин и пароль")
        return False
    subject = f"📊 Анику — отчет за {datetime.now().strftime('%d.%m.%Y')}"
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        server = smtplib.SMTP_SSL(smtp_host, smtp_port)
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, to_email, msg.as_string())
        server.quit()
        print(f"[SUCCESS] Отчет отправлен на {to_email}")
        return True
    except Exception as e:
        print(f"[ERROR] Ошибка отправки: {e}")
        return False


# ==================== ОСНОВНАЯ ЛОГИКА ====================

def main():
    parser = argparse.ArgumentParser(description="Анику — Единый мониторинг")
    parser.add_argument("--email", required=True)
    parser.add_argument("--smtp-host", default="smtp.mail.ru")
    parser.add_argument("--smtp-port", type=int, default=465)
    parser.add_argument("--smtp-user", default=os.environ.get("SMTP_USER"))
    parser.add_argument("--smtp-pass", default=os.environ.get("SMTP_PASS"))
    parser.add_argument("--no-email", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    print(f"{'='*60}")
    print(f"АНИКУ — Единый мониторинг (Дром + aniku.ru)")
    print(f"{'='*60}")

    drom_items = fetch_all_drom()
    site_items = fetch_all_site()

    merged, only_site, only_drom = merge_by_article(drom_items, site_items)

    print(f"\n{'='*60}")
    print(f"СОПОСТАВЛЕНИЕ ПО АРТИКУЛАМ")
    print(f"{'='*60}")
    print(f"  Дубли (оба):          {len(merged)}")
    print(f"  Только aniku.ru:      {len(only_site)}")
    print(f"  Только Дром:          {len(only_drom)}")

    current_state = {
        "drom_total": len(drom_items),
        "site_total": len(site_items),
        "merged": merged,
        "only_site": {k: {**v, "href": v["href"]} for k, v in only_site.items()},
        "only_drom": {k: {**v, "href": v["href"]} for k, v in only_drom.items()},
        "timestamp": datetime.now().isoformat(),
    }

    previous = load_snapshot()
    stats = compare_with_previous(current_state, previous)

    print(f"\n  Изменений цен:        {stats.get('price_change_count', 0)}")
    print(f"  Новых на сайте:       {len(stats.get('new_site_items', []))}")
    print(f"  Новых на Дроме:       {len(stats.get('new_drom_items', []))}")

    save_snapshot(current_state)

    has_changes = (
        len(only_site) > 0 or len(only_drom) > 0 or
        stats.get("price_change_count", 0) > 0 or
        len(stats.get("new_site_items", [])) > 0 or
        len(stats.get("new_drom_items", [])) > 0
    )

    if has_changes or args.force:
        html = build_html_report(current_state, stats)
        if not args.no_email:
            send_email(
                to_email=args.email,
                html=html,
                smtp_user=args.smtp_user,
                smtp_pass=args.smtp_pass,
                smtp_host=args.smtp_host,
                smtp_port=args.smtp_port
            )
        else:
            print("[--no-email] Email не отправлен")
    else:
        print("[INFO] Изменений не обнаружено — отчет не отправлен (используйте --force)")


if __name__ == "__main__":
    main()
