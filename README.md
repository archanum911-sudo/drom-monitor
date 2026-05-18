# Drom Disk Monitor 📊

Автоматический мониторинг остатков дисков на Дроме (профиль Aniku).  
Сравнивает изменения ассортимента и цен, отправляет email-отчеты.

---

## ⚡ Быстрый старт

### 1. Установка зависимостей

```bash
cd drom-monitor
pip install -r requirements.txt
```

### 2. Настройка почты (Mail.ru)

Для отправки отчетов на **palkinns@mail.ru** нужен SMTP-пароль:

1. Зайдите в настройки Mail.ru → **Безопасность** → **Пароли для внешних приложений**
2. Создайте пароль для приложения (не путайте с паролем от почты!)
3. Сохраните полученный пароль

### 3. Первый запуск (ручной)

```bash
# Linux/Mac
export SMTP_USER="your_email@mail.ru"
export SMTP_PASS="your_smtp_password"
python drom_monitor.py --email palkinns@mail.ru

# Windows (PowerShell)
$env:SMTP_USER="your_email@mail.ru"
$env:SMTP_PASS="your_smtp_password"
python drom_monitor.py --email palkinns@mail.ru
```

При первом запуске создается базовый снапшот — отчет не будет отправлен.  
При втором и последующих запусках скрипт найдет изменения и отправит отчет.

---

## 🔧 Настройка ежедневного запуска в 10:00

### Linux / Mac (cron)

Откройте crontab:
```bash
crontab -e
```

Добавьте строку (замените пути на свои):
```cron
0 10 * * * cd /home/user/drom-monitor && SMTP_USER="your@mail.ru" SMTP_PASS="pass" python drom_monitor.py --email palkinns@mail.ru >> /home/user/drom-monitor/monitor.log 2>&1
```

Проверить установленные задачи:
```bash
crontab -l
```

### Windows (Планировщик задач)

1. Нажмите **Win+R** → `taskschd.msc`
2. **Действие** → **Создать задачу**
3. На вкладке **Общие**: имя "Drom Monitor", поставьте галочку "Выполнять независимо от регистрации"
4. На вкладке **Триггеры**: ежедневно, в 10:00
5. На вкладке **Действия**:
   - Программа: `C:\Users\ВАШ_ПОЛЬЗОВАТЕЛЬ\AppData\Local\Programs\Python\Python311\python.exe`
   - Аргументы: `drom_monitor.py --email palkinns@mail.ru`
   - Рабочая папка: `C:\Users\ВАШ_ПОЛЬЗОВАТЕЛЬ\drom-monitor`
6. На вкладке **Условия** снимите галочку "Запускать только при питании от сети"

### Облачный вариант (бесплатно) — GitHub Actions

Если у вас есть GitHub-аккаунт, можно настроить бесплатный запуск через GitHub Actions:

1. Загрузите файлы в репозиторий GitHub
2. Создайте файл `.github/workflows/monitor.yml`:

```yaml
name: Drom Monitor
on:
  schedule:
    - cron: '0 10 * * *'  # 10:00 UTC (13:00 МСК / 18:00 Владивосток)
  workflow_dispatch:

jobs:
  monitor:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: python drom_monitor.py --email palkinns@mail.ru
        env:
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASS: ${{ secrets.SMTP_PASS }}
```

3. В настройках репозитория добавьте Secrets: `SMTP_USER` и `SMTP_PASS`

---

## 📋 Параметры командной строки

| Параметр | Описание | Пример |
|----------|----------|--------|
| `--email` | Email для отчета (обязательно) | `--email palkinns@mail.ru` |
| `--smtp-user` | SMTP логин (или env SMTP_USER) | `--smtp-user my@mail.ru` |
| `--smtp-pass` | SMTP пароль (или env SMTP_PASS) | `--smtp-pass abc123` |
| `--smtp-host` | SMTP сервер (по умолч: smtp.mail.ru) | `--smtp-host smtp.yandex.ru` |
| `--smtp-port` | SMTP порт (по умолч: 465) | `--smtp-port 465` |
| `--no-email` | Только вывод в консоль | `--no-email` |
| `--force` | Отправить отчет даже без изменений | `--force` |

---

## 📧 Что приходит в отчете

Отчет содержит:
- **Общая статистика** — количество позиций, новых, удаленных, изменений цен
- **🆕 Новые позиции** — диски, которые появились с прошлой проверки
- **💰 Изменения цен** — повышение/понижение с указанием % изменения
- **🗑 Удаленные позиции** — диски, которых больше нет на Дроме

---

## 🛠 Использование с другим SMTP

| Провайдер | SMTP хост | Порт |
|-----------|-----------|------|
| Mail.ru | `smtp.mail.ru` | 465 |
| Yandex | `smtp.yandex.ru` | 465 |
| Gmail | `smtp.gmail.com` | 465 |
| Outlook | `smtp.office365.com` | 587 |

> Для Gmail нужно создать **App Password** (не основной пароль!)

---

## ⚠️ Важные замечания

1. **Первый запуск** — создает базовый снапшот, отчет не отправляется
2. **Snapshot** хранится в файле `snapshot.json` в рабочей папке
3. **Задержка** между запросами к Дрому — 1 секунда (встроена в requests)
4. **Таймаут** — 30 секунд на страницу
5. **Логи** — при проблемах проверяйте `monitor.log`

---

## 📞 Поддержка

Если скрипт не работает:
1. Проверьте интернет-соединение
2. Убедитесь, что Mail.ru не блокирует SMTP (попробуйте через VPN)
3. Проверьте правильность SMTP-пароля (не путайте с паролем от почты!)
4. Запустите с `--no-email` чтобы проверить парсинг без отправки
