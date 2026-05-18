#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Мониторинг остатков дисков "Анику" на Дроме и aniku.ru
Отчёт: единая таблица без дублей по артикулам
"""

import requests
from bs4 import BeautifulSoup
import re
import json
import os
import sys
import smtplib
import time
import argparse
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict, Tuple, Optional

# ============ КОНФИГУРАЦИЯ ============
BASE_DROM = "https://vladivostok.baza.drom.ru/user/Anikuya/wheel/disc/"
SITE_URL = "https://aniku.ru/fulllist"

SNAPSHOT_FILE = "snapshot.json"

# Поддерживаем оба формата секретов: SMTP_USER/SMTP_PASS и EMAIL_FROM/EMAIL_PASS
EMAIL_FROM = os.getenv("SMTP_USER") or os.getenv("EMAIL_FROM") or ""
EMAIL_PASS = os.getenv("SMTP_PASS") or os.getenv("EMAIL_PASS") or ""

# Парсим аргумент --email или берём из env EMAIL_TO
parser = argparse.ArgumentParser()
parser.add_argument("--email", default=os.getenv("EMAIL_TO", "palkinns@mail.ru"))
args, _ = parser.parse_known_args()
EMAIL_TO = args.email

HEADERS_DROM = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
    "Referer": "https://baza.drom.ru/",
}

HEADERS_SITE = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

# ============ ИЗВЛЕЧЕНИЕ АРТИКУЛА ============
ARTICLE_RE = re.compile(r'\(([A-Z]{1,5}\d{2,}[A-Z0-9\-]*)\)')

def extract_article(name: str) -> Optional[str]:
    """Извлекает артикул из скобок, например '(S023)' → 'S023'"""
    if not name:
        return None
    m = ARTICLE_RE.search(name)
    return m.group(1) if m else None

# ============ ПАРСИНГ ДРОМА ============
def fetch_all_drom() -> List[Dict]:
    """Парсит все страницы Дрома с фильтрами inSetQuantity=1,2,4,5"""
    all_items = []
    seen = set()
    session = requests.Session()
    session.headers.update(HEADERS_DROM)

    try:
        session.get("https://baza.drom.ru/", timeout=30)
        time.sleep(1)
    except Exception as e:
        print(f"[WARN] Прогрев не удался: {e}")

    for page in range(1, 25):
        params = [
            ("condition[]", "new"),
            ("goodPresentState[]", "present"),
            ("inSetQuantity[]", "1"),
            ("inSetQuantity[]", "2"),
            ("inSetQuantity[]", "4"),
            ("inSetQuantity[]", "5"),
            ("page", str(page)),
        ]
        try:
            resp = session.get(BASE_DROM, params=params, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            
            if "робот" in resp.text.lower() or "капч" in resp.text.lower():
                print(f"[ERROR] Дром выдал капчу на стр. {page}")
                break
            
            items = parse_drom_page(soup)
            if not items:
                print(f"[Дром] Стр. {page} — пусто, остановка")
                break

            new_on_page = 0
            for item in items:
                key = (item.get("article"), item.get("price"))
                if key not in seen:
                    seen.add(key)
                    all_items.append(item)
                    new_on_page += 1
            print(f"[Дром] Стр. {page}: +{new_on_page} новых (всего {len(all_items)})")

            if new_on_page == 0:
                break
            time.sleep(1.5)
        except Exception as e:
            print(f"[ERROR] Дром стр. {page}: {e}")
            break

    return all_items

def parse_drom_page(soup: BeautifulSoup) -> List[Dict]:
    """Парсит одну страницу результатов Дрома"""
    items = []
    rows = soup.select("div.bull-item-content")
    for row in rows:
        try:
            name_el = row.select_one("a[data-ftid='bulls-list_bull'] .bull-title span")
            name = name_el.get_text(strip=True) if name_el else "Без названия"

            price_el = row.select_one("span[data-ftid='bull_price']")
            price_raw = price_el.get_text(strip=True) if price_el else "0"
            price = int(re.sub(r'[^\d]', '', price_raw)) if re.sub(r'[^\d]', '', price_raw) else 0

            loc_el = row.select_one(".bull-item__field__location")
            location = loc_el.get_text(strip=True) if loc_el else ""

            qty = 1
            qty_patterns = [
                row.select_one(".bull-item__field__setQuantity"),
                row.select_one(".bull-item__field__quantity"),
            ]
            for qp in qty_patterns:
                if qp:
                    txt = qp.get_text(strip=True)
                    qm = re.search(r'(\d+)', txt)
                    if qm:
                        qty = int(qm.group(1))
                        break

            article = extract_article(name)
            items.append({
                "name": name,
                "price": price,
                "location": location,
                "quantity": qty,
                "article": article,
                "source": "Анику (Дром)",
            })
        except Exception as e:
            print(f"[WARN] Ошибка парсинга строки Дром: {e}")
            continue
    return items

# ============ ПАРСИНГ ANIKU.RU ============
def fetch_site() -> List[Dict]:
    """Парсит aniku.ru — все страницы каталога дисков"""
    all_items = []
    page = 1
    while True:
        try:
            url = f"{SITE_URL}?page={page}" if page > 1 else SITE_URL
            resp = requests.get(url, headers=HEADERS_SITE, timeout=30)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")
            
            if "робот" in resp.text.lower() or "капч" in resp.text.lower():
                print(f"[ERROR] Сайт выдал капчу на стр. {page}")
                break
            
            items = parse_site_page(soup)
            if not items:
                break
            all_items.extend(items)
            print(f"[Сайт] Стр. {page}: +{len(items)} (всего {len(all_items)})")

            next_link = soup.select_one("a.next, a[rel='next']")
            if not next_link:
                pagination = soup.select(".pagination a, .paging a")
                has_next = any(("page=" + str(page + 1)) in (a.get("href") or "") for a in pagination)
                if not has_next:
                    break
            page += 1
            time.sleep(1)
        except Exception as e:
            print(f"[ERROR] Сайт стр. {page}: {e}")
            break
    return all_items

def parse_site_page(soup: BeautifulSoup) -> List[Dict]:
    """Парсит одну страницу каталога aniku.ru"""
    items = []
    products = soup.select("div.product-item, li.product, .product")
    
    if not products:
        products = soup.select("a[href*='fulllist?id=']")
    
    for prod in products:
        try:
            name_el = prod.select_one(".product-name a, h3 a, a.name, .product-title a")
            if not name_el:
                name_el = prod if prod.name == 'a' else None
            if not name_el:
                continue
                
            name = name_el.get_text(strip=True) if hasattr(name_el, 'get_text') else name_el.get('title', '')
            if not name:
                continue
            
            price = 0
            price_container = prod.select_one(".price, .product-price, .current-price")
            if price_container:
                price_raw = price_container.get_text(strip=True)
                price = int(re.sub(r'[^\d]', '', price_raw)) if re.sub(r'[^\d]', '', price_raw) else 0
            
            qty = 1
            article = extract_article(name)
            href = name_el.get("href", "") if hasattr(name_el, 'get') else ""
            
            items.append({
                "name": name,
                "price": price,
                "quantity": qty,
                "article": article,
                "source": "Урал Кастомс (сайт)",
                "url": href if href.startswith("http") else f"https://aniku.ru{href}" if href else "",
            })
        except Exception as e:
            print(f"[WARN] Ошибка парсинга товара сайта: {e}")
            continue
    
    return items

# ============ ОБЪЕДИНЕНИЕ И СРАВНЕНИЕ ============
def merge_by_article(drom_items: List[Dict], site_items: List[Dict]) -> Tuple[Dict, Dict, Dict]:
    drom_by_art: Dict[str, Dict] = {}
    site_by_art: Dict[str, Dict] = {}

    for item in drom_items:
        art = item.get("article")
        if art:
            drom_by_art[art] = item

    for item in site_items:
        art = item.get("article")
        if art:
            site_by_art[art] = item

    merged = {}
    only_site = {}
    only_drom = {}

    for art in set(drom_by_art.keys()) & set(site_by_art.keys()):
        merged[art] = {"drom": drom_by_art[art], "site": site_by_art[art]}

    for art in set(site_by_art.keys()) - set(drom_by_art.keys()):
        only_site[art] = site_by_art[art]

    for art in set(drom_by_art.keys()) - set(site_by_art.keys()):
        only_drom[art] = drom_by_art[art]

    return merged, only_site, only_drom

def compare_with_previous(current_items: List[Dict]) -> List[str]:
    alerts = []
    if not os.path.exists(SNAPSHOT_FILE):
        return alerts

    try:
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            previous = json.load(f)
    except Exception as e:
        print(f"[WARN] Не удалось загрузить снапшот: {e}")
        return alerts

    if isinstance(previous, list):
        print("[WARN] Старый формат снапшота (list), пропускаем")
        return alerts
    
    if not all(isinstance(v, dict) for v in previous.values()):
        print("[WARN] Некорректный формат снапшота, пропускаем")
        return alerts

    current_dict = {item["article"]: item for item in current_items if item.get("article")}

    for art, prev_data in previous.items():
        if art in current_dict:
            curr = current_dict[art]
            prev_price = prev_data.get("price", 0)
            curr_price = curr.get("price", 0)
            if prev_price and curr_price and abs(prev_price - curr_price) > max(prev_price * 0.05, 100):
                direction = "↑" if curr_price > prev_price else "↓"
                alerts.append(f'{direction} {art}: {prev_price:,} → {curr_price:,} ₽ ({curr.get("name", "")[:40]})')
        else:
            name = prev_data.get("name", "") if isinstance(prev_data, dict) else ""
            alerts.append(f'❌ Исчез: {art} ({name[:40]})')

    for art, curr in current_dict.items():
        if art not in previous:
            alerts.append(f'🆕 Новый: {art} — {curr.get("price", 0):,} ₽ ({curr.get("name", "")[:40]})')

    return alerts

# ============ ФОРМИРОВАНИЕ ОТЧЁТА ============
def build_html_report(merged: Dict, only_site: Dict, only_drom: Dict, alerts: List[str]) -> str:
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    total = len(merged) + len(only_site) + len(only_drom)

    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Отчёт Анику — {now}</title>
<style>
    body {{ font-family: Arial, sans-serif; margin: 20px; color: #333; }}
    h1 {{ font-size: 20px; margin-bottom: 5px; }}
    h2 {{ font-size: 16px; margin-top: 20px; margin-bottom: 10px; }}
    .summary {{ background: #f5f5f5; padding: 12px; border-radius: 6px; margin-bottom: 20px; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
    th {{ background: #4a4a4a; color: white; position: sticky; top: 0; }}
    tr:nth-child(even) {{ background: #fafafa; }}
    .both {{ background: #e8f5e9 !important; }}
    .site-only {{ background: #fff9c4 !important; }}
    .drom-only {{ background: #ffebee !important; }}
    .price {{ font-weight: bold; color: #2e7d32; white-space: nowrap; }}
    .article {{ font-family: monospace; font-weight: bold; color: #1565c0; }}
    .alert-box {{ background: #fff3e0; border-left: 4px solid #ff9800; padding: 10px; margin-bottom: 20px; }}
    .footer {{ margin-top: 20px; font-size: 11px; color: #999; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: bold; }}
    .badge-both {{ background: #4caf50; color: white; }}
    .badge-site {{ background: #ff9800; color: white; }}
    .badge-drom {{ background: #f44336; color: white; }}
</style>
</head>
<body>
<h1>📊 Отчёт по остаткам — {now}</h1>
<div class="summary">
    <b>Всего уникальных артикулов:</b> {total}<br>
    🟢 Оба источника: {len(merged)} | 🟡 Только сайт: {len(only_site)} | 🔴 Только Дром: {len(only_drom)}
</div>
"""

    if alerts:
        html += '<div class="alert-box"><b>⚡ Изменения с прошлого раза:</b><br>' + "<br>".join(alerts[:30]) + "</div>"

    html += """
<h2>📋 Единая таблица позиций</h2>
<table>
<thead>
<tr>
    <th>#</th>
    <th>Артикул</th>
    <th>Название</th>
    <th>Цена Дром</th>
    <th>Цена Сайт</th>
    <th>Наличие</th>
    <th>Статус</th>
</tr>
</thead>
<tbody>
"""

    row_num = 0
    for art in sorted(merged.keys()):
        data = merged[art]
        d = data["drom"]
        s = data["site"]
        row_num += 1
        d_price = f"{d.get('price', 0):,} ₽" if d.get('price') else "—"
        s_price = f"{s.get('price', 0):,} ₽" if s.get('price') else "—"
        d_qty = d.get('quantity', 1)
        s_qty = s.get('quantity', 1)
        qty_str = f"Дром: {d_qty} / Сайт: {s_qty}"
        html += f"""<tr class="both">
<td>{row_num}</td>
<td class="article">{art}</td>
<td>{d.get('name', s.get('name', ''))}</td>
<td class="price">{d_price}</td>
<td class="price">{s_price}</td>
<td>{qty_str}</td>
<td><span class="badge badge-both">🟢 Оба</span></td>
</tr>"""

    for art in sorted(only_site.keys()):
        item = only_site[art]
        row_num += 1
        s_price = f"{item.get('price', 0):,} ₽" if item.get('price') else "—"
        s_qty = item.get('quantity', 1)
        html += f"""<tr class="site-only">
<td>{row_num}</td>
<td class="article">{art}</td>
<td>{item.get('name', '')}</td>
<td>—</td>
<td class="price">{s_price}</td>
<td>Сайт: {s_qty}</td>
<td><span class="badge badge-site">🟡 Только сайт</span></td>
</tr>"""

    for art in sorted(only_drom.keys()):
        item = only_drom[art]
        row_num += 1
        d_price = f"{item.get('price', 0):,} ₽" if item.get('price') else "—"
        d_qty = item.get('quantity', 1)
        html += f"""<tr class="drom-only">
<td>{row_num}</td>
<td class="article">{art}</td>
<td>{item.get('name', '')}</td>
<td class="price">{d_price}</td>
<td>—</td>
<td>Дром: {d_qty}</td>
<td><span class="badge badge-drom">🔴 Только Дром</span></td>
</tr>"""

    html += f"""</tbody></table>
<div class="footer">
Сформировано: {now}<br>
Источники: Дром (Анику) + aniku.ru (Урал Кастомс)
</div>
</body></html>"""
    return html

def save_snapshot(items: List[Dict]):
    snap = {}
    for item in items:
        art = item.get("article")
        if art:
            snap[art] = {
                "name": item.get("name", ""),
                "price": item.get("price", 0),
                "quantity": item.get("quantity", 1),
            }
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    print(f"[OK] Снапшот сохранён: {len(snap)} артикулов")

# ============ ОТПРАВКА EMAIL ============
def send_email(subject: str, html_body: str) -> bool:
    print(f"[DEBUG] EMAIL_FROM={EMAIL_FROM[:3]}... | EMAIL_TO={EMAIL_TO} | PASS={'*' if EMAIL_PASS else 'EMPTY'}")
    
    if not EMAIL_FROM or not EMAIL_PASS:
        print("[SKIP] Email не настроен: отсутствует логин или пароль")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = EMAIL_TO

        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.mail.ru", 465) as server:
            server.login(EMAIL_FROM, EMAIL_PASS)
            server.sendmail(EMAIL_FROM, [EMAIL_TO], msg.as_string())
        print(f"[OK] Письмо отправлено на {EMAIL_TO}")
        return True
    except Exception as e:
        print(f"[ERROR] Ошибка отправки email: {e}")
        return False

# ============ ГЛАВНЫЙ ПРОЦЕСС ============
def main():
    print(f"\n{'='*50}")
    print(f"🚀 Запуск — {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")
    print(f"EMAIL_FROM={'SET' if EMAIL_FROM else 'EMPTY'} | EMAIL_TO={EMAIL_TO} | EMAIL_PASS={'SET' if EMAIL_PASS else 'EMPTY'}")
    print(f"{'='*50}\n")

    print("[1/4] Парсинг Дрома...")
    drom_items = fetch_all_drom()
    print(f"✅ Дром: {len(drom_items)} позиций")

    print("\n[2/4] Парсинг aniku.ru...")
    site_items = fetch_site()
    print(f"✅ Сайт: {len(site_items)} позиций")

    print("\n[3/4] Объединение по артикулам...")
    merged, only_site, only_drom = merge_by_article(drom_items, site_items)

    unified_items = []
    for art, data in merged.items():
        unified_items.append({
            "article": art,
            "name": data["drom"].get("name", data["site"].get("name", "")),
            "price": data["drom"].get("price") or data["site"].get("price", 0),
            "quantity": (data["drom"].get("quantity", 0) + data["site"].get("quantity", 0)),
        })
    for art, item in only_site.items():
        unified_items.append({
            "article": art,
            "name": item.get("name", ""),
            "price": item.get("price", 0),
            "quantity": item.get("quantity", 1),
        })
    for art, item in only_drom.items():
        unified_items.append({
            "article": art,
            "name": item.get("name", ""),
            "price": item.get("price", 0),
            "quantity": item.get("quantity", 1),
        })

    alerts = compare_with_previous(unified_items)

    print("\n[4/4] Формирование и отправка отчёта...")
    html_report = build_html_report(merged, only_site, only_drom, alerts)

    now_str = datetime.now().strftime("%d.%m.%Y")
    subject = f"📊 Анику — остатки {now_str} | Уникальных: {len(unified_items)}"
    send_email(subject, html_report)

    save_snapshot(unified_items)

    print(f"\n{'='*50}")
    print(f"✅ Готово! Уникальных артикулов: {len(unified_items)}")
    print(f"   🟢 Оба: {len(merged)} | 🟡 Только сайт: {len(only_site)} | 🔴 Только Дром: {len(only_drom)}")
    print(f"{'='*50}\n")

if __name__ == "__main__":
    main()
