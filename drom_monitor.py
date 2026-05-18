#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Drom Disk Monitor v2.0 — Умные алерты + Мониторинг просмотров

Добавлено:
  • Отслеживание просмотров каждого объявления (views_history.json)
  • Умные алерты (smart_alerts) — критические события приоритетно
  • Отчёт по трендам просмотров (топ рост / топ падение)
  • Гибкая конфигурация алертов через config.json
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
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9",
}

SNAPSHOT_FILE = Path(__file__).parent / "snapshot.json"
VIEWS_FILE = Path(__file__).parent / "views_history.json"
CONFIG_FILE = Path(__file__).parent / "config.json"

# ==================== УМНЫЕ АЛЕРТЫ (конфиг по умолчанию) ====================

DEFAULT_ALERTS = {
    "critical_brand_drop": {
        "enabled": True,
        "brand": "Shogun",
        "threshold_abs": 130,
        "message": "⚠️ КРИТИЧНО: Shogun упал ниже {threshold} позиций! Сейчас: {current}"
    },
    "total_positions_drop": {
        "enabled": True,
        "threshold_abs": 700,
        "message": "🚨 АССОРТИМЕНТ: Всего {current} позиций — ниже порога {threshold}!"
    },
    "price_below_floor": {
        "enabled": True,
        "threshold_price": 30000,
        "message": "💰 ЦЕНА: Обнаружены диски дешевле {threshold}₽ — проверьте себестоимость!"
    },
    "new_brand_appeared": {
        "enabled": True,
        "message": "🆕 НОВЫЙ БРЕНД: На Дроме появился новый бренд — {brand} ({count} позиций)"
    },
    "views_zero_alert": {
        "enabled": True,
        "threshold_views": 5,
        "min_age_days": 3,
        "message": "👁️ ПРОСМОТРЫ: {count} объявлений имеют ≤{threshold} просмотров за {days} дня — проверьте фото/цены!"
    },
    "views_spike_alert": {
        "enabled": True,
        "threshold_pct": 100,
        "message": "🔥 ТРЕНД: {count} объявлений набрали +{threshold}% просмотров — повышайте цены!"
    }
}


def load_or_create_config() -> dict:
    """Загружает config.json или создаёт с дефолтами."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_ALERTS, f, ensure_ascii=False, indent=2)
    return DEFAULT_ALERTS.copy()


# ==================== ПАРСИНГ ДРОМА ====================

def fetch_page(page_num: int) -> Optional[str]:
    """Загружает страницу с Дрома."""
    params = {
        "condition%5B%5D": "new",
        "goodPresentState%5B%5D": "present",
        "inSetQuantity%5B%5D": "4",
        "page": page_num
    }
    try:
        resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
        resp.encoding = "cp1251"
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        print(f"[ERROR] Ошибка загрузки страницы {page_num}: {e}")
    return None


def parse_listings(html: str) -> List[Dict]:
    """Извлекает список дисков из HTML включая просмотры."""
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

            # Парсим просмотры из всего текста элемента
            views = None
            item_text = item.get_text(separator=" ", strip=True)
            # Ищем число просмотров — обычно последнее число в блоке
            views_match = re.search(r'(\d+)\s*просмотр', item_text)
            if not views_match:
                # Пробуем другие паттерны
                views_match = re.search(r'\b(\d{1,3}(?:\s+\d{3})*)\s*(?:просмотр|просм| views?)', item_text)
            if views_match:
                views = int(views_match.group(1).replace(" ", "").replace("\xa0", ""))

            # Fallback: попробовать найти через CSS-классы
            if views is None:
                for span in item.find_all("span"):
                    txt = span.get_text(strip=True)
                    if "просмотр" in txt.lower():
                        vm = re.search(r'(\d+)', txt)
                        if vm:
                            views = int(vm.group(1))
                            break

            # Диаметр
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
            listing_id = link_href.split("-")[-1].replace(".html", "") if link_href else None

            if title and price and listing_id:
                listings.append({
                    "id": listing_id,
                    "title": title or link_title,
                    "price": price,
                    "specs": specs,
                    "diameter": diam,
                    "brand": brand,
                    "views": views,
                    "href": f"https://baza.drom.ru{link_href}" if link_href and not link_href.startswith("http") else link_href,
                    "timestamp": datetime.now().isoformat()
                })
        except Exception:
            continue

    return listings


def extract_brand(title: str) -> str:
    """Определяет бренд по названию."""
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


def fetch_all_listings() -> List[Dict]:
    """Загружает все страницы."""
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
        if len(listings) < 10:
            break

    print(f"[INFO] Всего загружено: {len(all_listings)} позиций")
    return all_listings


# ==================== ИСТОРИЯ ПРОСМОТРОВ ====================

def load_views_history() -> Dict[str, List[Dict]]:
    """Загружает историю просмотров."""
    if VIEWS_FILE.exists():
        with open(VIEWS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_views_history(history: Dict[str, List[Dict]]):
    """Сохраняет историю просмотров."""
    with open(VIEWS_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)
    print(f"[INFO] История просмотров сохранена: {VIEWS_FILE}")


def update_views_history(current: List[Dict]) -> Tuple[Dict, List[Dict], List[Dict]]:
    """
    Обновляет историю просмотров.
    Возвращает (history_dict, views_spike_items, views_drop_items)
    """
    history = load_views_history()
    today = datetime.now().strftime("%Y-%m-%d")

    views_spike = []
    views_drop = []

    for item in current:
        lid = item["id"]
        views = item.get("views")
        if views is None:
            continue

        if lid not in history:
            history[lid] = []

        # Добавляем сегодняшнюю запись (или обновляем)
        entry = {"date": today, "views": views, "price": item["price"]}
        # Убираем дубликаты за сегодня
        history[lid] = [e for e in history[lid] if e["date"] != today]
        history[lid].append(entry)

        # Сортируем по дате и ограничиваем 30 записями
        history[lid] = sorted(history[lid], key=lambda x: x["date"])[-30:]

        # Анализируем тренд (сравниваем с предыдущим днём)
        if len(history[lid]) >= 2:
            prev = history[lid][-2]
            prev_views = prev["views"]
            if prev_views > 0:
                change_pct = round((views - prev_views) / prev_views * 100, 1)
                item["views_change_pct"] = change_pct
                item["views_prev"] = prev_views

                if change_pct >= 100:
                    views_spike.append(item)
                elif change_pct <= -50:
                    views_drop.append(item)

    save_views_history(history)
    return history, views_spike, views_drop


# ==================== УМНЫЕ АЛЕРТЫ ====================

def check_smart_alerts(
    current: List[Dict],
    new_items: List[Dict],
    removed_items: List[Dict],
    price_changed: List[Dict],
    stats: Dict,
    history: Dict,
    config: Dict
) -> List[Dict]:
    """
    Проверяет условия умных алертов.
    Возвращает список сработавших алертов [{level, message, type}].
    """
    alerts = []
    today = datetime.now().strftime("%Y-%m-%d")

    # 1. Критическое падение бренда (Shogun)
    cfg = config.get("critical_brand_drop", {})
    if cfg.get("enabled", True):
        brand = cfg.get("brand", "Shogun")
        threshold = cfg.get("threshold_abs", 130)
        brand_count = sum(1 for x in current if x["brand"] == brand)
        if brand_count < threshold:
            alerts.append({
                "level": "CRITICAL",
                "type": "critical_brand_drop",
                "message": cfg["message"].format(threshold=threshold, current=brand_count)
            })

    # 2. Общее падение ассортимента
    cfg = config.get("total_positions_drop", {})
    if cfg.get("enabled", True):
        threshold = cfg.get("threshold_abs", 700)
        if stats["total_current"] < threshold:
            alerts.append({
                "level": "CRITICAL",
                "type": "total_positions_drop",
                "message": cfg["message"].format(threshold=threshold, current=stats["total_current"])
            })

    # 3. Диски ниже минимальной цены
    cfg = config.get("price_below_floor", {})
    if cfg.get("enabled", True):
        threshold = cfg.get("threshold_price", 30000)
        cheap = [x for x in current if x["price"] < threshold]
        if cheap:
            alerts.append({
                "level": "WARNING",
                "type": "price_below_floor",
                "message": cfg["message"].format(threshold=threshold),
                "details": f"Найдено {len(cheap)} позиций ниже {threshold}₽"
            })

    # 4. Появление нового бренда
    cfg = config.get("new_brand_appeared", {})
    if cfg.get("enabled", True) and new_items:
        current_brands = {x["brand"] for x in current}
        prev_brands = {x["brand"] for x in (load_snapshot() or [])}
        new_brands = current_brands - prev_brands
        for nb in new_brands:
            count = sum(1 for x in current if x["brand"] == nb)
            alerts.append({
                "level": "INFO",
                "type": "new_brand_appeared",
                "message": cfg["message"].format(brand=nb, count=count)
            })

    # 5. Объявления без просмотров
    cfg = config.get("views_zero_alert", {})
    if cfg.get("enabled", True):
        threshold = cfg.get("threshold_views", 5)
        min_days = cfg.get("min_age_days", 3)

        zero_views = []
        for item in current:
            lid = item["id"]
            views = item.get("views")
            if views is None or views > threshold:
                continue
            # Проверяем возраст объявления (есть ли история)
            if lid in history and len(history[lid]) >= min_days:
                zero_views.append(item)

        if zero_views:
            alerts.append({
                "level": "WARNING",
                "type": "views_zero_alert",
                "message": cfg["message"].format(count=len(zero_views), threshold=threshold, days=min_days),
                "details": f"{len(zero_views)} объявлений с ≤{threshold} просмотров"
            })

    # 6. Резкий рост просмотров
    cfg = config.get("views_spike_alert", {})
    if cfg.get("enabled", True):
        threshold_pct = cfg.get("threshold_pct", 100)
        spike_items = [x for x in current if x.get("views_change_pct", 0) >= threshold_pct]
        if spike_items:
            alerts.append({
                "level": "INFO",
                "type": "views_spike_alert",
                "message": cfg["message"].format(count=len(spike_items), threshold=threshold_pct),
                "details": f"{len(spike_items)} объявлений набрали +{threshold_pct}% просмотров"
            })

    return alerts


# ==================== СНАПШОТ ====================

def load_snapshot() -> Optional[List[Dict]]:
    if SNAPSHOT_FILE.exists():
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_snapshot(listings: List[Dict]):
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(listings, f, ensure_ascii=False, indent=2)
    print(f"[INFO] Снапшот сохранен: {SNAPSHOT_FILE}")


def compare_listings(current: List[Dict], previous: List[Dict]) -> Tuple[List[Dict], List[Dict], List[Dict], Dict]:
    prev_by_id = {item["id"]: item for item in previous}
    curr_by_id = {item["id"]: item for item in current}

    prev_ids = set(prev_by_id.keys())
    curr_ids = set(curr_by_id.keys())

    new_items = [curr_by_id[i] for i in (curr_ids - prev_ids)]
    removed_items = [prev_by_id[i] for i in (prev_ids - curr_ids)]

    price_changed = []
    for lid in (curr_ids & prev_ids):
        old_price = prev_by_id[lid]["price"]
        new_price = curr_by_id[lid]["price"]
        if old_price != new_price:
            change_pct = round((new_price - old_price) / old_price * 100, 1)
            item = curr_by_id[lid].copy()
            item["old_price"] = old_price
            item["price_change_pct"] = change_pct
            price_changed.append(item)

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


# ==================== ОТПРАВКА EMAIL v2 ====================

def send_email_report(
    to_email: str,
    new_items: List[Dict],
    removed_items: List[Dict],
    price_changed: List[Dict],
    stats: Dict,
    alerts: List[Dict],
    views_spike: List[Dict],
    views_drop: List[Dict],
    current: List[Dict],
    smtp_host: str = "smtp.mail.ru",
    smtp_port: int = 465,
    smtp_user: str = None,
    smtp_pass: str = None
):
    if not smtp_user or not smtp_pass:
        print("[ERROR] Не указаны SMTP-логин и пароль")
        return False

    has_critical = any(a["level"] == "CRITICAL" for a in alerts)
    alert_emoji = "🚨" if has_critical else "⚠️" if any(a["level"] == "WARNING" for a in alerts) else "ℹ️"

    subject = f"{alert_emoji} Drom Monitor v2 — отчет за {datetime.now().strftime('%d.%m.%Y')}"

    html = build_html_report(
        new_items, removed_items, price_changed, stats,
        alerts, views_spike, views_drop, current
    )

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
    new_items, removed_items, price_changed, stats,
    alerts, views_spike, views_drop, current
) -> str:
    date_str = datetime.now().strftime("%d.%m.%Y %H:%M")

    # ===== УМНЫЕ АЛЕРТЫ =====
    alerts_section = ""
    if alerts:
        rows = ""
        for a in alerts:
            color = {"CRITICAL": "#c62828", "WARNING": "#ed6c00", "INFO": "#1565c0"}[a["level"]]
            bg = {"CRITICAL": "#ffebee", "WARNING": "#fff3e0", "INFO": "#e3f2fd"}[a["level"]]
            icon = {"CRITICAL": "🚨", "WARNING": "⚠️", "INFO": "ℹ️"}[a["level"]]
            rows += f"""
            <tr style="background:{bg};">
                <td style="padding:10px;border-bottom:1px solid #eee;font-size:16px;">{icon}</td>
                <td style="padding:10px;border-bottom:1px solid #eee;font-weight:bold;color:{color};">{a["level"]}</td>
                <td style="padding:10px;border-bottom:1px solid #eee;">{a["message"]}</td>
                <td style="padding:10px;border-bottom:1px solid #eee;color:#666;font-size:12px;">{a.get("details", "")}</td>
            </tr>"""
        alerts_section = f"""
        <h3 style="color:#c62828;margin-top:25px;">🧠 Умные алерты ({len(alerts)})</h3>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <thead><tr style="background:#f5f5f5;">
                <th style="padding:10px;text-align:left;width:40px;"></th>
                <th style="padding:10px;text-align:left;width:90px;">Уровень</th>
                <th style="padding:10px;text-align:left;">Сообщение</th>
                <th style="padding:10px;text-align:left;width:200px;">Детали</th>
            </tr></thead>
            <tbody>{rows}</tbody>
        </table>"""

    # ===== ПРОСМОТРЫ: ТОП РОСТ =====
    views_spike_section = ""
    if views_spike:
        rows = ""
        for item in sorted(views_spike, key=lambda x: -x.get("views_change_pct", 0))[:10]:
            rows += f"""
            <tr>
                <td style="padding:8px;border-bottom:1px solid #eee;">{item['brand']}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;"><a href="{item['href']}">{item['title'][:60]}</a></td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;">{item.get('views_prev', '-'):,}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;font-weight:bold;color:#2e7d32;">{item.get('views', '-'):,}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;color:#2e7d32;font-weight:bold;">+{item.get('views_change_pct', 0)}%</td>
            </tr>"""
        views_spike_section = f"""
        <h3 style="color:#2e7d32;margin-top:25px;">🔥 Топ роста просмотров</h3>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <thead><tr style="background:#f5f5f5;">
                <th style="padding:8px;text-align:left;">Бренд</th>
                <th style="padding:8px;text-align:left;">Название</th>
                <th style="padding:8px;text-align:right;">Было</th>
                <th style="padding:8px;text-align:right;">Стало</th>
                <th style="padding:8px;text-align:right;">Δ</th>
            </tr></thead>
            <tbody>{rows}</tbody>
        </table>"""

    # ===== ПРОСМОТРЫ: ТОП ПАДЕНИЕ =====
    views_drop_section = ""
    if views_drop:
        rows = ""
        for item in sorted(views_drop, key=lambda x: x.get("views_change_pct", 0))[:10]:
            rows += f"""
            <tr>
                <td style="padding:8px;border-bottom:1px solid #eee;">{item['brand']}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;"><a href="{item['href']}">{item['title'][:60]}</a></td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;">{item.get('views_prev', '-'):,}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;color:#c62828;">{item.get('views', '-'):,}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;color:#c62828;">{item.get('views_change_pct', 0)}%</td>
            </tr>"""
        views_drop_section = f"""
        <h3 style="color:#c62828;margin-top:25px;">📉 Топ падения просмотров</h3>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <thead><tr style="background:#f5f5f5;">
                <th style="padding:8px;text-align:left;">Бренд</th>
                <th style="padding:8px;text-align:left;">Название</th>
                <th style="padding:8px;text-align:right;">Было</th>
                <th style="padding:8px;text-align:right;">Стало</th>
                <th style="padding:8px;text-align:right;">Δ</th>
            </tr></thead>
            <tbody>{rows}</tbody>
        </table>"""

    # ===== СВОДКА ПО ПРОСМОТРАМ =====
    views_summary = ""
    total_views = sum(x.get("views", 0) or 0 for x in current)
    avg_views = round(total_views / len(current), 0) if current else 0
    zero_count = sum(1 for x in current if x.get("views") is None)

    views_summary = f"""
    <div style="background:#e8f5e9;padding:15px;border-radius:6px;margin:15px 0;">
        <h4 style="margin:0 0 10px 0;color:#2e7d32;">👁️ Сводка по просмотрам</h4>
        <div style="display:flex;gap:20px;flex-wrap:wrap;">
            <div><b>Всего просмотров:</b> {total_views:,}</div>
            <div><b>Среднее на объявление:</b> {avg_views:,.0f}</div>
            <div><b>Без данных:</b> {zero_count}</div>
            <div><b>🔥 Рост:</b> {len(views_spike)} объявлений</div>
            <div><b>📉 Падение:</b> {len(views_drop)} объявлений</div>
        </div>
    </div>"""

    # ===== НОВЫЕ =====
    new_section = ""
    if new_items:
        rows = ""
        for item in sorted(new_items, key=lambda x: x["price"]):
            views_str = f"{item.get('views', '-'):,}" if item.get('views') else "—"
            rows += f"""
            <tr>
                <td style="padding:8px;border-bottom:1px solid #eee;">{item['brand']}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;"><a href="{item['href']}">{item['title'][:65]}</a></td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:center;">R{item.get('diameter', '?')}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;font-weight:bold;color:#2e7d32;">{item['price']:,.0f} ₽</td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:center;color:#666;">{views_str}</td>
            </tr>"""
        new_section = f"""
        <h3 style="color:#2e7d32;margin-top:25px;">🆕 Новые позиции ({len(new_items)})</h3>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <thead><tr style="background:#f5f5f5;">
                <th style="padding:8px;text-align:left;">Бренд</th>
                <th style="padding:8px;text-align:left;">Название</th>
                <th style="padding:8px;text-align:center;">R</th>
                <th style="padding:8px;text-align:right;">Цена</th>
                <th style="padding:8px;text-align:center;">Просм.</th>
            </tr></thead>
            <tbody>{rows}</tbody>
        </table>"""

    # ===== УДАЛЁННЫЕ =====
    removed_section = ""
    if removed_items:
        rows = ""
        for item in sorted(removed_items, key=lambda x: x["price"]):
            rows += f"""
            <tr>
                <td style="padding:8px;border-bottom:1px solid #eee;">{item['brand']}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;">{item['title'][:65]}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:center;">R{item.get('diameter', '?')}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;color:#999;">{item['price']:,.0f} ₽</td>
            </tr>"""
        removed_section = f"""
        <h3 style="color:#c62828;margin-top:25px;">🗑 Удаленные позиции ({len(removed_items)})</h3>
        <table style="width:100%;border-collapse:collapse;font-size:13px;">
            <thead><tr style="background:#f5f5f5;">
                <th style="padding:8px;text-align:left;">Бренд</th>
                <th style="padding:8px;text-align:left;">Название</th>
                <th style="padding:8px;text-align:center;">R</th>
                <th style="padding:8px;text-align:right;">Была цена</th>
            </tr></thead>
            <tbody>{rows}</tbody>
        </table>"""

    # ===== ЦЕНЫ =====
    price_section = ""
    if price_changed:
        rows = ""
        for item in sorted(price_changed, key=lambda x: x["price_change_pct"], reverse=True):
            color = "#2e7d32" if item["price_change_pct"] > 0 else "#c62828"
            arrow = "▲" if item["price_change_pct"] > 0 else "▼"
            rows += f"""
            <tr>
                <td style="padding:8px;border-bottom:1px solid #eee;">{item['brand']}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;"><a href="{item['href']}">{item['title'][:55]}</a></td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;text-decoration:line-through;color:#999;">{item['old_price']:,.0f} ₽</td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;font-weight:bold;">{item['price']:,.0f} ₽</td>
                <td style="padding:8px;border-bottom:1px solid #eee;text-align:right;color:{color};">{arrow} {abs(item['price_change_pct'])}%</td>
            </tr>"""
        price_section = f"""
        <h3 style="color:#1565c0;margin-top:25px;">💰 Изменения цен ({len(price_changed)})</h3>
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

    total_change = stats["new_count"] - stats["removed_count"]
    change_sign = "+" if total_change > 0 else ""

    # ===== ИТОГОВЫЙ HTML =====
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="font-family:Arial,sans-serif;background:#fafafa;padding:20px;">
<div style="max-width:950px;margin:0 auto;background:#fff;padding:30px;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,0.1);">
    <h2 style="color:#333;border-bottom:2px solid #4472C4;padding-bottom:10px;">
        📊 Drom Monitor v2 — Умные алерты + Просмотры
    </h2>
    <p style="color:#666;">Дата: <strong>{date_str}</strong> | Профиль: Aniku (Владивосток)</p>

    <!-- КАРТОЧКИ СТАТИСТИКИ -->
    <div style="display:flex;gap:12px;margin:20px 0;flex-wrap:wrap;">
        <div style="background:#e3f2fd;padding:15px;border-radius:6px;text-align:center;min-width:100px;">
            <div style="font-size:22px;font-weight:bold;color:#1565c0;">{stats['total_current']}</div>
            <div style="font-size:11px;color:#666;">Всего позиций</div>
        </div>
        <div style="background:#e8f5e9;padding:15px;border-radius:6px;text-align:center;min-width:100px;">
            <div style="font-size:22px;font-weight:bold;color:#2e7d32;">+{stats['new_count']}</div>
            <div style="font-size:11px;color:#666;">Новых</div>
        </div>
        <div style="background:#ffebee;padding:15px;border-radius:6px;text-align:center;min-width:100px;">
            <div style="font-size:22px;font-weight:bold;color:#c62828;">-{stats['removed_count']}</div>
            <div style="font-size:11px;color:#666;">Удалено</div>
        </div>
        <div style="background:#fff3e0;padding:15px;border-radius:6px;text-align:center;min-width:100px;">
            <div style="font-size:22px;font-weight:bold;color:#ef6c00;">{stats['price_changed_count']}</div>
            <div style="font-size:11px;color:#666;">Цен изменено</div>
        </div>
        <div style="background:#f3e5f5;padding:15px;border-radius:6px;text-align:center;min-width:100px;">
            <div style="font-size:22px;font-weight:bold;color:#6a1b9a;">{change_sign}{total_change}</div>
            <div style="font-size:11px;color:#666;">Итого Δ</div>
        </div>
        <div style="background:#e0f2f1;padding:15px;border-radius:6px;text-align:center;min-width:100px;">
            <div style="font-size:22px;font-weight:bold;color:#00695c;">{len(alerts)}</div>
            <div style="font-size:11px;color:#666;">Алертов</div>
        </div>
    </div>

    {alerts_section}
    {views_summary}
    {views_spike_section}
    {views_drop_section}
    {new_section}
    {price_section}
    {removed_section}

    <hr style="margin:30px 0;border:none;border-top:1px solid #eee;">
    <p style="color:#999;font-size:11px;text-align:center;">
        Drom Monitor v2.0 | Умные алерты + Мониторинг просмотров |
        <a href="https://baza.drom.ru/user/Aniku/wheel/disc/">Профиль на Дроме</a>
    </p>
</div>
</body></html>"""

    return html


# ==================== ОСНОВНАЯ ЛОГИКА ====================

def main():
    parser = argparse.ArgumentParser(description="Drom Monitor v2 — Умные алерты + Просмотры")
    parser.add_argument("--email", required=True, help="Email для отправки отчета")
    parser.add_argument("--smtp-host", default="smtp.mail.ru", help="SMTP сервер")
    parser.add_argument("--smtp-port", type=int, default=465, help="SMTP порт")
    parser.add_argument("--smtp-user", default=os.environ.get("SMTP_USER"), help="SMTP логин")
    parser.add_argument("--smtp-pass", default=os.environ.get("SMTP_PASS"), help="SMTP пароль")
    parser.add_argument("--no-email", action="store_true", help="Только консоль, без email")
    parser.add_argument("--force", action="store_true", help="Отправить даже без изменений")
    args = parser.parse_args()

    # Загружаем конфиг алертов
    config = load_or_create_config()
    print(f"[INFO] Загружено {len(config)} правил умных алертов")

    # Загружаем текущие данные с Дрома
    current_listings = fetch_all_listings()
    if not current_listings:
        print("[ERROR] Не удалось загрузить данные с Дрома")
        sys.exit(1)

    # Обновляем историю просмотров
    print("[INFO] Анализируем просмотры...")
    views_history, views_spike, views_drop = update_views_history(current_listings)
    print(f"[INFO] 🔥 Рост просмотров: {len(views_spike)} | 📉 Падение: {len(views_drop)}")

    # Загружаем предыдущий снапшот
    previous_listings = load_snapshot()

    if previous_listings is None:
        print("[INFO] Первый запуск — сохраняем базовый снапшот")
        save_snapshot(current_listings)
        save_views_history(views_history)
        print(f"[INFO] Сохранено {len(current_listings)} позиций и история просмотров.")
        sys.exit(0)

    # Сравниваем позиции и цены
    new_items, removed_items, price_changed, stats = compare_listings(
        current_listings, previous_listings
    )

    # Проверяем умные алерты
    print("[INFO] Проверяем умные алерты...")
    alerts = check_smart_alerts(
        current_listings, new_items, removed_items, price_changed,
        stats, views_history, config
    )
    for a in alerts:
        icon = {"CRITICAL": "🚨", "WARNING": "⚠️", "INFO": "ℹ️"}[a["level"]]
        print(f"  {icon} [{a['level']}] {a['message']}")

    # Сводка
    print("\n" + "="*55)
    print(f"СРАВНЕНИЕ: {stats['total_previous']} → {stats['total_current']} позиций")
    print(f"  Новых:     +{stats['new_count']}")
    print(f"  Удалено:   -{stats['removed_count']}")
    print(f"  Цен измен: {stats['price_changed_count']} (▲{stats['price_increased']} / ▼{stats['price_decreased']})")
    print(f"  Алертов:   {len(alerts)}")
    print("="*55)

    # Сохраняем
    save_snapshot(current_listings)

    # Отправляем email
    has_changes = (
        stats["new_count"] > 0 or
        stats["removed_count"] > 0 or
        stats["price_changed_count"] > 0 or
        len(alerts) > 0 or
        len(views_spike) > 0 or
        len(views_drop) > 0
    )

    if has_changes or args.force:
        if not args.no_email:
            success = send_email_report(
                to_email=args.email,
                new_items=new_items,
                removed_items=removed_items,
                price_changed=price_changed,
                stats=stats,
                alerts=alerts,
                views_spike=views_spike,
                views_drop=views_drop,
                current=current_listings,
                smtp_host=args.smtp_host,
                smtp_port=args.smtp_port,
                smtp_user=args.smtp_user,
                smtp_pass=args.smtp_pass
            )
            if not success:
                print("\n[FALLBACK] Email не отправлен — смотрите лог выше")
        else:
            print("\n[--no-email] Email не отправлен")
    else:
        print("[INFO] Изменений не обнаружено — отчет не отправлен (используйте --force)")


if __name__ == "__main__":
    main()
