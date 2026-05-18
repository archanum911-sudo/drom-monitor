#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Drom Disk Monitor - Отслеживание остатков дисков на Дроме
Автоматическое сравнение изменений и отправка email-отчетов

Использование:
    python drom_monitor.py --pdf /path/to/stock.pdf --email your@email.ru

Настройка cron (каждый день в 10:00):
    0 10 * * * cd /path/to/drom-monitor && python drom_monitor.py --pdf /path/to/stock.pdf --email palkinns@mail.ru >> log.txt 2>&1
"""

import argparse
import json
import os
import re
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

# ==================== КОНФИГУРАЦИЯ ====================

BASE_URL = "https://baza.drom.ru/user/Aniku/wheel/disc/"
PARAMS = {
    "condition": ["new"],
    "goodPresentState": ["present"],
    "inSetQuantity": ["4"]
}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

SNAPSHOT_FILE = Path(__file__).parent / "snapshot.json"


# ==================== ПАРСИНГ ДРОМА ====================

def fetch_page(page_num: int) -> Optional[str]:
    """Загружает страницу с Дрома"""
    url = f"{BASE_URL}"
    params = {
        "condition%5B%5D": "new",
        "goodPresentState%5B%5D": "present",
        "inSetQuantity%5B%5D": "4",
        "page": page_num
    }
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)
        resp.encoding = "cp1251"
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        print(f"[ERROR] Ошибка загрузки страницы {page_num}: {e}")
    return None


def parse_listings(html: str) -> List[Dict]:
    """Извлекает список дисков из HTML"""
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
                price_text = price_elem.get_text(strip=True)
                price_clean = re.sub(r"[^\d]", "", price_text)
                price = int(price_clean) if price_clean else None
            
            link_elem = item.find("a", class_="bull-item__self-link")
            link_title = link_elem.get_text(strip=True) if link_elem else None
            link_href = link_elem["href"] if link_elem else None
            
            specs_elem = item.find("div", class_="bull-item__annotation-row")
            specs = specs_elem.get_text(strip=True) if specs_elem else None
            
            # Extract diameter
            diam = None
            if specs:
                m = re.search(r'(\d+)x(\d+)"', specs)
                if m:
                    diam = int(m.group(2))
                else:
                    m = re.search(r'R(\d+)', title or "")
                    if m:
                        diam = int(m.group(1))
            
            # Extract brand
            brand = extract_brand(title or link_title or "")
            
            # Unique ID from href or title
            listing_id = link_href.split("-")[-1].replace(".html", "") if link_href else None
            
            if title and price and listing_id:
                listings.append({
                    "id": listing_id,
                    "title": title or link_title,
                    "price": price,
                    "specs": specs,
                    "diameter": diam,
                    "brand": brand,
                    "href": f"https://baza.drom.ru{link_href}" if link_href and not link_href.startswith("http") else link_href,
                    "timestamp": datetime.now().isoformat()
                })
        except Exception:
            continue
    
    return listings


def extract_brand(title: str) -> str:
    """Определяет бренд по названию"""
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
    ]
    for keyword, brand_name in brands:
        if keyword in title:
            return brand_name
    return "Other"


def fetch_all_listings() -> List[Dict]:
    """Загружает все страницы и возвращает список дисков"""
    all_listings = []
    seen_ids = set()
    
    print("[INFO] Начинаем загрузку с Дрома...")
    
    for page in range(1, 18):
        print(f"[INFO] Загрузка страницы {page}/17...")
        html = fetch_page(page)
        if not html:
            print(f"[WARN] Не удалось загрузить страницу {page}")
            continue
        
        listings = parse_listings(html)
        new_count = 0
        for l in listings:
            if l["id"] not in seen_ids:
                seen_ids.add(l["id"])
                all_listings.append(l)
                new_count += 1
        
        print(f"[INFO] Страница {page}: +{new_count} новых (всего: {len(all_listings)})")
        
        # Если страница пустая или мало результатов — последняя страница
        if len(listings) < 10:
            break
    
    print(f"[INFO] Всего загружено: {len(all_listings)} позиций")
    return all_listings


# ==================== СРАВНЕНИЕ ====================

def load_snapshot() -> Optional[List[Dict]]:
    """Загружает предыдущий снапшот"""
    if SNAPSHOT_FILE.exists():
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_snapshot(listings: List[Dict]):
    """Сохраняет текущий снапшот"""
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(listings, f, ensure_ascii=False, indent=2)
    print(f"[INFO] Снапшот сохранен: {SNAPSHOT_FILE}")


def compare_listings(
    current: List[Dict],
    previous: List[Dict]
) -> Tuple[List[Dict], List[Dict], List[Dict], Dict]:
    """
    Сравнивает текущие и предыдущие данные.
    Возвращает: (new_items, removed_items, price_changed, stats)
    """
    prev_by_id = {item["id"]: item for item in previous}
    curr_by_id = {item["id"]: item for item in current}
    
    prev_ids = set(prev_by_id.keys())
    curr_ids = set(curr_by_id.keys())
    
    # Новые позиции
    new_ids = curr_ids - prev_ids
    new_items = [curr_by_id[i] for i in new_ids]
    
    # Удаленные позиции
    removed_ids = prev_ids - curr_ids
    removed_items = [prev_by_id[i] for i in removed_ids]
    
    # Изменения цен
    price_changed = []
    common_ids = curr_ids & prev_ids
    for lid in common_ids:
        old_price = prev_by_id[lid]["price"]
        new_price = curr_by_id[lid]["price"]
        if old_price != new_price:
            change_pct = round((new_price - old_price) / old_price * 100, 1)
            item = curr_by_id[lid].copy()
            item["old_price"] = old_price
            item["price_change_pct"] = change_pct
            price_changed.append(item)
    
    # Статистика
    stats = {
        "total_current": len(current),
        "total_previous": len(previous),
        "new_count": len(new_items),
        "removed_count": len(removed_items),
        "price_changed_count": len(price_changed),
        "price_increased": len([x for x in price_changed if x["price_change_pct"] > 0]),
        "price_decreased": len([x for x in price_changed if x["price_change_pct"] < 0]),
    }
    
    return new_items, removed_items, price_changed, stats


# ==================== ОТПРАВКА EMAIL ====================

def send_email_report(
    to_email: str,
    new_items: List[Dict],
    removed_items: List[Dict],
    price_changed: List[Dict],
    stats: Dict,
    smtp_host: str = "smtp.mail.ru",
    smtp_port: int = 465,
    smtp_user: str = None,
    smtp_pass: str = None
):
    """Отправляет HTML-отчет на email"""
    
    if not smtp_user or not smtp_pass:
        print("[ERROR] Не указаны SMTP-логин и пароль для отправки email")
        print("[INFO] Укажите через переменные окружения SMTP_USER и SMTP_PASS")
        return False
    
    subject = f"📊 Drom Monitor — отчет за {datetime.now().strftime('%d.%m.%Y')}"
    
    html = build_html_report(new_items, removed_items, price_changed, stats)
    
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
        print(f"[ERROR] Ошибка отправки email: {e}")
        return False


def build_html_report(
    new_items: List[Dict],
    removed_items: List[Dict],
    price_changed: List[Dict],
    stats: Dict
) -> str:
    """Создает HTML-отчет"""
    
    date_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    
    # Секция новых позиций
    new_section = ""
    if new_items:
        rows = ""
        for item in sorted(new_items, key=lambda x: x["price"]):
            rows += f"""
            <tr>
                <td style="padding:8px;border-bottom:1px solid #eee;">{item['brand']}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;"><a href="{item['href']}">{item['title'][:70]}</a></td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:center;">R{item.get('diameter', '?')}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;font-weight:bold;color:#2e7d32;">{item['price']:,.0f} ₽</td>
            </tr>"""
        new_section = f"""
        <h3 style="color:#2e7d32;">🆕 Новые позиции ({len(new_items)})</h3>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <thead><tr style="background:#f5f5f5;">
                <th style="padding:8px;text-align:left;">Бренд</th>
                <th style="padding:8px;text-align:left;">Название</th>
                <th style="padding:8px;text-align:center;">R</th>
                <th style="padding:8px;text-align:right;">Цена</th>
            </tr></thead>
            <tbody>{rows}</tbody>
        </table>"""
    
    # Секция удаленных позиций
    removed_section = ""
    if removed_items:
        rows = ""
        for item in sorted(removed_items, key=lambda x: x["price"]):
            rows += f"""
            <tr>
                <td style="padding:8px;border-bottom:1px solid #eee;">{item['brand']}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;">{item['title'][:70]}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:center;">R{item.get('diameter', '?')}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;color:#999;">{item['price']:,.0f} ₽</td>
            </tr>"""
        removed_section = f"""
        <h3 style="color:#c62828;">🗑 Удаленные позиции ({len(removed_items)})</h3>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <thead><tr style="background:#f5f5f5;">
                <th style="padding:8px;text-align:left;">Бренд</th>
                <th style="padding:8px;text-align:left;">Название</th>
                <th style="padding:8px;text-align:center;">R</th>
                <th style="padding:8px;text-align:right;">Была цена</th>
            </tr></thead>
            <tbody>{rows}</tbody>
        </table>"""
    
    # Секция изменений цен
    price_section = ""
    if price_changed:
        rows = ""
        for item in sorted(price_changed, key=lambda x: x["price_change_pct"], reverse=True):
            color = "#2e7d32" if item["price_change_pct"] > 0 else "#c62828"
            arrow = "▲" if item["price_change_pct"] > 0 else "▼"
            rows += f"""
            <tr>
                <td style="padding:8px;border-bottom:1px solid #eee;">{item['brand']}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;"><a href="{item['href']}">{item['title'][:60]}</a></td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;text-decoration:line-through;color:#999;">{item['old_price']:,.0f} ₽</td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;font-weight:bold;">{item['price']:,.0f} ₽</td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;color:{color};">{arrow} {abs(item['price_change_pct'])}%</td>
            </tr>"""
        price_section = f"""
        <h3 style="color:#1565c0;">💰 Изменения цен ({len(price_changed)})</h3>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <thead><tr style="background:#f5f5f5;">
                <th style="padding:8px;text-align:left;">Бренд</th>
                <th style="padding:8px;text-align:left;">Название</th>
                <th style="padding:8px;text-align:right;">Старая</th>
                <th style="padding:8px;text-align:right;">Новая</th>
                <th style="padding:8px;text-align:right;">Δ</th>
            </tr></thead>
            <tbody>{rows}</tbody>
        </table>"""
    
    # Итоговая статистика
    total_change = stats["new_count"] - stats["removed_count"]
    change_sign = "+" if total_change > 0 else ""
    
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;background:#fafafa;padding:20px;">
<div style="max-width:900px;margin:0 auto;background:#fff;padding:30px;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.1);">
    <h2 style="color:#333;border-bottom:2px solid #4472C4;padding-bottom:10px;">
        📊 Drom Monitor — Отчет по дискам Aniku
    </h2>
    <p style="color:#666;">Дата: <strong>{date_str}</strong> | Профиль: Aniku (Владивосток)</p>
    
    <div style="display:flex;gap:15px;margin:20px 0;flex-wrap:wrap;">
        <div style="background:#e3f2fd;padding:15px;border-radius:6px;text-align:center;min-width:120px;">
            <div style="font-size:24px;font-weight:bold;color:#1565c0;">{stats['total_current']}</div>
            <div style="font-size:12px;color:#666;">Всего позиций</div>
        </div>
        <div style="background:#e8f5e9;padding:15px;border-radius:6px;text-align:center;min-width:120px;">
            <div style="font-size:24px;font-weight:bold;color:#2e7d32;">+{stats['new_count']}</div>
            <div style="font-size:12px;color:#666;">Новых</div>
        </div>
        <div style="background:#ffebee;padding:15px;border-radius:6px;text-align:center;min-width:120px;">
            <div style="font-size:24px;font-weight:bold;color:#c62828;">-{stats['removed_count']}</div>
            <div style="font-size:12px;color:#666;">Удалено</div>
        </div>
        <div style="background:#fff3e0;padding:15px;border-radius:6px;text-align:center;min-width:120px;">
            <div style="font-size:24px;font-weight:bold;color:#ef6c00;">{stats['price_changed_count']}</div>
            <div style="font-size:12px;color:#666;">Цен изменено</div>
        </div>
        <div style="background:#f3e5f5;padding:15px;border-radius:6px;text-align:center;min-width:120px;">
            <div style="font-size:24px;font-weight:bold;color:#6a1b9a;">{change_sign}{total_change}</div>
            <div style="font-size:12px;color:#666;">Итого Δ</div>
        </div>
    </div>
    
    {new_section}
    {price_section}
    {removed_section}
    
    <hr style="margin:30px 0;border:none;border-top:1px solid #eee;">
    <p style="color:#999;font-size:12px;text-align:center;">
        Drom Monitor | Автоматический отчет | 
        <a href="https://baza.drom.ru/user/Aniku/wheel/disc/">Открыть профиль на Дроме</a>
    </p>
</div>
</body></html>"""
    
    return html


# ==================== ОСНОВНАЯ ЛОГИКА ====================

def main():
    parser = argparse.ArgumentParser(description="Мониторинг остатков дисков на Дроме")
    parser.add_argument("--email", required=True, help="Email для отправки отчета")
    parser.add_argument("--smtp-host", default="smtp.mail.ru", help="SMTP сервер")
    parser.add_argument("--smtp-port", type=int, default=465, help="SMTP порт")
    parser.add_argument("--smtp-user", default=os.environ.get("SMTP_USER"), help="SMTP логин")
    parser.add_argument("--smtp-pass", default=os.environ.get("SMTP_PASS"), help="SMTP пароль")
    parser.add_argument("--no-email", action="store_true", help="Только вывод в консоль, без email")
    parser.add_argument("--force", action="store_true", help="Отправить отчет даже без изменений")
    args = parser.parse_args()
    
    # Загружаем текущие данные
    current_listings = fetch_all_listings()
    
    if not current_listings:
        print("[ERROR] Не удалось загрузить данные с Дрома")
        sys.exit(1)
    
    # Загружаем предыдущий снапшот
    previous_listings = load_snapshot()
    
    if previous_listings is None:
        print("[INFO] Первый запуск — сохраняем базовый снапшот")
        save_snapshot(current_listings)
        print(f"[INFO] Сохранено {len(current_listings)} позиций. Следующий запуск покажет изменения.")
        sys.exit(0)
    
    # Сравниваем
    new_items, removed_items, price_changed, stats = compare_listings(
        current_listings, previous_listings
    )
    
    # Выводим статистику
    print("\n" + "="*50)
    print(f"СРАВНЕНИЕ: {stats['total_previous']} → {stats['total_current']} позиций")
    print(f"  Новых:     +{stats['new_count']}")
    print(f"  Удалено:   -{stats['removed_count']}")
    print(f"  Цен измен: {stats['price_changed_count']} (▲{stats['price_increased']} / ▼{stats['price_decreased']})")
    print("="*50)
    
    # Сохраняем текущий снапшот
    save_snapshot(current_listings)
    
    # Отправляем email если есть изменения или force
    has_changes = stats["new_count"] > 0 or stats["removed_count"] > 0 or stats["price_changed_count"] > 0
    
    if has_changes or args.force:
        if not args.no_email:
            success = send_email_report(
                to_email=args.email,
                new_items=new_items,
                removed_items=removed_items,
                price_changed=price_changed,
                stats=stats,
                smtp_host=args.smtp_host,
                smtp_port=args.smtp_port,
                smtp_user=args.smtp_user,
                smtp_pass=args.smtp_pass
            )
            if not success:
                # Выводим текстовый отчет как fallback
                print("\n[FALLBACK] Текстовый отчет:")
                for item in new_items[:5]:
                    print(f"  [NEW] {item['title'][:60]} - {item['price']:,}₽")
                for item in price_changed[:5]:
                    print(f"  [PRICE] {item['title'][:50]} - {item['old_price']:,}₽ → {item['price']:,}₽ ({item['price_change_pct']}%)")
        else:
            print("\n[--no-email] Email не отправлен, только консольный вывод")
    else:
        print("[INFO] Изменений не обнаружено — отчет не отправлен (используйте --force для принудительной отправки)")


if __name__ == "__main__":
    main()
