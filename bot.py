#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Спутник дня — три показателя: Спорт, Бизнес, Духовность.
Награды выдаются только при соблюдении баланса.
Теперь с еженедельными автоотчётами и красивыми карточками прогресса.

Python 3.10+, python-telegram-bot 21.x
"""

import os
import sqlite3
from datetime import datetime, timedelta, time
from typing import Dict, Any, Optional, Tuple

import pytz
from PIL import Image, ImageDraw, ImageFont

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler, MessageHandler, filters
)

# ---------------- Config ----------------
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "bot.db")
os.makedirs(os.path.join(os.path.dirname(__file__), "data"), exist_ok=True)

POINTS_TRAIN = 2
POINTS_SALE = 10
DEFAULT_SALE_THRESHOLD = 0  # 0 = любая продажа даёт очки
DEFAULT_TZ = "Asia/Yekaterinburg"  # Тюмень (UTC+5)
WEEKLY_HOUR = 20  # 20:00 местного времени, воскресенье
CARD_W, CARD_H = 1200, 628  # Social-card friendly

# ------------- DB helpers ---------------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        created_at TEXT,
        tz TEXT,
        notify INTEGER DEFAULT 1,
        sale_threshold INTEGER DEFAULT 0
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS sport_types(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        name TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS sport_schedule(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        type_id INTEGER,
        dow INTEGER,          -- 0=Mon ... 6=Sun
        at_time TEXT          -- 'HH:MM'
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS log_sport(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        dt TEXT,
        type_id INTEGER
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS log_business(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        dt TEXT,
        calls INTEGER DEFAULT 0,
        visibility INTEGER DEFAULT 0,
        sale_amount INTEGER DEFAULT 0,
        cash_in INTEGER DEFAULT 0
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS log_spirit(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        dt TEXT,
        sleep_hours REAL DEFAULT 0,
        meditation_min INTEGER DEFAULT 0,
        reading_min INTEGER DEFAULT 0,
        note TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS points(
        user_id INTEGER PRIMARY KEY,
        value INTEGER DEFAULT 0
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS goals(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        title TEXT,
        reward TEXT,
        sport_min INTEGER DEFAULT 0,
        business_min INTEGER DEFAULT 0,
        sales_min INTEGER DEFAULT 0,
        spirit_min INTEGER DEFAULT 0,
        points_min INTEGER DEFAULT 0,
        deadline TEXT,
        created_at TEXT
    )
    """)
    conn.commit()
    conn.close()

def ensure_user(uid:int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
    if not cur.fetchone():
        cur.execute("INSERT INTO users(user_id, created_at, tz, sale_threshold) VALUES(?, ?, ?, ?)", (uid, datetime.utcnow().isoformat(), DEFAULT_TZ, DEFAULT_SALE_THRESHOLD))
        cur.execute("INSERT INTO points(user_id, value) VALUES(?, 0)", (uid,))
        conn.commit()
    conn.close()

def add_points(uid:int, pts:int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE points SET value=value+? WHERE user_id=?", (pts, uid))
    conn.commit()
    conn.close()

def get_points(uid:int)->int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT value FROM points WHERE user_id=?", (uid,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0

def get_user_tz(uid:int)->pytz.BaseTzInfo:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT tz FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    conn.close()
    tzname = row[0] if row and row[0] else DEFAULT_TZ
    try:
        return pytz.timezone(tzname)
    except Exception:
        return pytz.timezone(DEFAULT_TZ)

def get_sale_threshold(uid:int)->int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT sale_threshold FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else DEFAULT_SALE_THRESHOLD

# ------------- Utils --------------------
DOW_NAMES = ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"]

def fmt_money(v:int)->str:
    return f"{v:,}".replace(",", " ")

def bar(cur:int, tgt:int)->str:
    if tgt<=0: return "—"
    filled = min(10, int(10*cur/max(tgt,1)))
    return "█"*filled + "░"*(10-filled)

# ------------- Stats helpers ------------
def collect_stats(uid:int, days:int=7)->Dict[str, Any]:
    """Return stats for last `days` days."""
    since=(datetime.utcnow()-timedelta(days=days)).isoformat()
    conn=get_conn(); cur=conn.cursor()
    # sport
    cur.execute("SELECT COUNT(*) FROM log_sport WHERE user_id=? AND dt>=?", (uid, since)); sport_cnt=cur.fetchone()[0]
    # business aggregates
    cur.execute("SELECT IFNULL(SUM(calls),0), IFNULL(SUM(visibility),0), IFNULL(SUM(CASE WHEN sale_amount>0 THEN 1 ELSE 0 END),0), IFNULL(SUM(sale_amount),0), IFNULL(SUM(cash_in),0) FROM log_business WHERE user_id=? AND dt>=?", (uid, since))
    calls, vis, sales_n, sales_sum, cash_sum = cur.fetchone()
    # spirituality
    cur.execute("SELECT IFNULL(SUM(sleep_hours),0), IFNULL(SUM(meditation_min),0), IFNULL(SUM(reading_min),0) FROM log_spirit WHERE user_id=? AND dt>=?", (uid, since))
    sleep, med, read = cur.fetchone()
    conn.close()
    return {
        "sport_cnt": int(sport_cnt),
        "calls": int(calls),
        "vis": int(vis),
        "sales_n": int(sales_n),
        "sales_sum": int(sales_sum),
        "cash_sum": int(cash_sum),
        "sleep": float(sleep),
        "med": int(med),
        "read": int(read),
        "points": get_points(uid)
    }

# ------------- Card rendering -----------
def render_card(uid:int, stats7:Dict[str,Any], stats30:Dict[str,Any], path:str)->str:
    """Render a PNG progress card to `path`."""
    img = Image.new("RGB", (CARD_W, CARD_H), (234, 231, 226))
    draw = ImageDraw.Draw(img)
    # Overlay logo (top-left)
    try:
        logo_path = os.path.join(os.path.dirname(__file__), 'assets', 'logo.png')
        lg = Image.open(logo_path).convert('RGBA')
        # scale logo to ~96px height
        target_h = 96
        ratio = target_h / lg.height
        lg = lg.resize((int(lg.width*ratio), target_h))
        img.paste(lg, (36, 28), lg)
    except Exception:
        pass

    # Load fonts (fallback to default if system fonts unavailable)
    font_big = ImageFont.load_default()
    font_mid = ImageFont.load_default()
    font_small = ImageFont.load_default()
    try:
        # Typical fonts; system dependent
        font_big = ImageFont.truetype("DejaVuSans-Bold.ttf", 60)
        font_mid = ImageFont.truetype("DejaVuSans.ttf", 36)
        font_small = ImageFont.truetype("DejaVuSans.ttf", 30)
    except:
        pass

    def txt(x,y,t,fill=(235,235,245),font=font_mid):
        draw.text((x,y), t, fill=fill, font=font)

    # Header
    txt(48, 32, "СПУТНИК ДНЯ — отчёт", font=font_big)
    txt(48, 100, "🏋️ Спорт  •  💼 Бизнес  •  🕊️ Духовность", font=font_mid)

    # Blocks
    y0 = 170
    pad = 30
    # Sport
    txt(48, y0, "7д Спорт:", font=font_mid); 
    txt(300, y0, f"тренировки {stats7['sport_cnt']}", font=font_mid)
    txt(48, y0+60, "30д Спорт:", font=font_small); 
    txt(300, y0+60, f"{stats30['sport_cnt']}", font=font_small)
    # Business
    y1 = y0 + 130
    txt(48, y1, "7д Бизнес:", font=font_mid)
    txt(300, y1, f"звонки {stats7['calls']}  •  проявл. {stats7['vis']}  •  продажи {stats7['sales_n']}  •  сумма {fmt_money(stats7['sales_sum'])} ₽", font=font_mid)
    txt(48, y1+60, "30д Бизнес:", font=font_small)
    txt(300, y1+60, f"звонки {stats30['calls']}  •  проявл. {stats30['vis']}  •  продажи {stats30['sales_n']}  •  сумма {fmt_money(stats30['sales_sum'])} ₽", font=font_small)
    # Spirit
    y2 = y1 + 130
    txt(48, y2, "7д Духовность:", font=font_mid)
    txt(300, y2, f"сон {stats7['sleep']:.1f} ч  •  медитация {stats7['med']} мин  •  чтение {stats7['read']} мин", font=font_mid)
    txt(48, y2+60, "30д Духовность:", font=font_small)
    txt(300, y2+60, f"сон {stats30['sleep']:.1f} ч  •  медитация {stats30['med']} мин  •  чтение {stats30['read']} мин", font=font_small)

    # Points
    y3 = y2 + 130
    txt(48, y3, f"💎 Очки: 7д ~ {stats7['points']}  •  30д ~ {stats30['points']}", font=font_mid)

    # Footer motivation
    txt(48, CARD_H-90, "«Баланс — это ритм твоей жизни.»  Продолжай движение. 🪐", font=font_small)

    img.save(path, "PNG")
    return path


# ---- Guided Onboarding (/onboard) ----
ONB_TZ, ONB_TYPES, ONB_SCHED, ONB_THRESH, ONB_NOTIFY = range(5)

async def onboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    context.user_data["onb_state"] = ONB_TZ
    await update.message.reply_text(
        "🚀 Запустим быстрый онбординг, чтобы всё заработало за 2–3 минуты.\n\n"
        "1) Введи твой часовой пояс (пример: Asia/Yekaterinburg)."
    )

async def onboard_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    state = context.user_data.get("onb_state")
    if state is None:
        return  # ignore

    # Step 1: timezone
    if state == ONB_TZ:
        try:
            pytz.timezone(text)
        except Exception:
            await update.message.reply_text("Не понял такой часовой пояс. Пример: Asia/Yekaterinburg")
            return
        conn=get_conn(); cur=conn.cursor()
        cur.execute("UPDATE users SET tz=? WHERE user_id=?", (text, uid))
        conn.commit(); conn.close()
        context.user_data["onb_state"] = ONB_TYPES
        await update.message.reply_text(
            "✅ Часовой пояс сохранён.\n\n"
            "2) Введи *виды тренировок* через запятую (например: зал, бассейн, теннис).",
            parse_mode="Markdown"
        )
        return

    # Step 2: sport types
    if state == ONB_TYPES:
        types=[t.strip() for t in text.split(",") if t.strip()]
        if not types:
            await update.message.reply_text("Добавь хотя бы один вид, пример: зал, бассейн")
            return
        conn=get_conn(); cur=conn.cursor()
        cur.execute("DELETE FROM sport_types WHERE user_id=?", (uid,))
        for name in types:
            cur.execute("INSERT INTO sport_types(user_id,name) VALUES(?,?)",(uid,name))
        conn.commit(); conn.close()
        context.user_data["onb_state"] = ONB_SCHED
        await update.message.reply_text(
            "✅ Виды тренировок сохранены.\n\n"
            "3) Теперь *расписание*. Отправляй по одному сообщению на каждый тип.\n"
            "Формат: `тип; Пн Ср Пт; 19:00`\n"
            "Когда закончишь — напиши `готово`.",
            parse_mode="Markdown"
        )
        return

    # Step 3: schedule (multi-line until 'готово')
    if state == ONB_SCHED:
        if text.lower() == "готово":
            # move next
            context.user_data["onb_state"] = ONB_THRESH
            await update.message.reply_text(
                "🟢 Расписание принято.\n\n"
                "4) Укажи *минимальную сумму продажи* для начисления очков.\n"
                "Например `100000` или `0` — очки за любую продажу."
            )
            return
        # parse line
        try:
            part = [p.strip() for p in text.split(";")]
            typ, dows_str, at = part[0], part[1], part[2]
            dmap = { "пн":0,"вт":1,"ср":2,"чт":3,"пт":4,"сб":5,"вс":6 }
            dows = [dmap[t.lower()] for t in dows_str.split()]
            conn=get_conn(); cur=conn.cursor()
            cur.execute("SELECT id FROM sport_types WHERE user_id=? AND name=?", (uid, typ))
            row=cur.fetchone()
            if not row:
                cur.execute("INSERT INTO sport_types(user_id,name) VALUES(?,?)",(uid,typ))
                type_id=cur.lastrowid
            else:
                type_id=row[0]
            for d in dows:
                cur.execute("INSERT INTO sport_schedule(user_id,type_id,dow,at_time) VALUES(?,?,?,?)",(uid,type_id,d,at))
            conn.commit(); conn.close()
            await update.message.reply_text("Добавлено ✅. Ещё расписания? Или напиши `готово`.")
        except Exception:
            await update.message.reply_text("Не смог разобрать. Пример: `зал; Пн Ср Пт; 19:00`")
        return

    # Step 4: threshold
    if state == ONB_THRESH:
        try:
            val=int(text)
        except:
            await update.message.reply_text("Нужна сумма в рублях. Пример: 100000 или 0")
            return
        conn=get_conn(); cur=conn.cursor()
        cur.execute("UPDATE users SET sale_threshold=? WHERE user_id=?", (val, uid))
        conn.commit(); conn.close()
        context.user_data["onb_state"] = ONB_NOTIFY
        await update.message.reply_text(
            f"✅ Порог продажи установлен: {fmt_money(val)} ₽.\n\n"
            "5) Включить напоминания и еженедельные автоотчёты? (да/нет)"
        )
        return

    # Step 5: notify
    if state == ONB_NOTIFY:
        ans = text.lower()
        turn_on = ans in ("да","yes","y","+","вкл","on","конечно")
        conn=get_conn(); cur=conn.cursor()
        cur.execute("UPDATE users SET notify=? WHERE user_id=?", (1 if turn_on else 0, uid))
        conn.commit(); conn.close()
        if turn_on:
            schedule_all_jobs(context.application, uid)
            await update.message.reply_text("🔔 Напоминания и автоотчёты включены.")
        else:
            clear_all_jobs(context.application, uid)
            await update.message.reply_text("🔕 Напоминания и автоотчёты выключены (можно включить /notify).")
        context.user_data["onb_state"] = None
        await update.message.reply_text(
            "🎉 Готово! Онбординг завершён. Команды для старта:\n"
            "/log — быстрый лог\n/goals — цели\n/addgoal — добавить цель\n/report — карточка отчёта\n/stats — статистика",
        )
        return

# ------------- Bot Handlers -------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    txt = (
        "👋 Привет! Это *Спутник дня* — твой дневной спутник: Спорт × Бизнес × Духовность.\n\n"
        "🎯 Логируй действия, набирай очки (+2 тренировка, +10 продажа), ставь цели и *получай награды только при балансе всех трёх сфер*.\n\n"
        "Начни с настроек: /setup\n"
        "Быстрый лог: /log\n"
        "Цели и награды: /goals /addgoal /claim\n"
        "Статистика: /stats, еженедельный отчёт: /report\n"
        "Уведомления и автоотчёты: /notify\n"
        "Помощь: /help"
    )
    await update.message.reply_markdown_v2(txt)
    # Send brand logo
    try:
        await update.message.reply_photo(photo=open(os.path.join(os.path.dirname(__file__), 'assets', 'logo.png'), 'rb'), caption='Спутник дня — твой баланс на орбите дня.')
    except Exception:
        pass
    # ⤵️ Онбординг: спросим порог продажи для очков при первом запуске (0 = очки за любую продажу)
    try:
        threshold = get_sale_threshold(uid)
    except Exception:
        threshold = 0
    if threshold == 0 and not context.user_data.get("asked_threshold_once"):
        context.user_data["await_threshold"] = True
        context.user_data["asked_threshold_once"] = True
        await update.message.reply_text(
            "Какой минимальный размер *сделки* считать \"крупной\" для начисления очков?\n"
            "Введи число в ₽. Напиши `0`, если очки должны даваться за *любую* продажу.",
            parse_mode="Markdown"
        )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

# ---- Setup flow ----
SETUP_WAIT = range(1)

async def setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    kb = [
        [InlineKeyboardButton("🕒 Часовой пояс", callback_data="setup_tz"),
         InlineKeyboardButton("🏋️ Виды тренировок", callback_data="setup_sport_types")],
        [InlineKeyboardButton("🗓️ Расписание спорта", callback_data="setup_schedule"),
         InlineKeyboardButton("🔔 Напоминания ON/OFF", callback_data="setup_notify")],
        [InlineKeyboardButton("💼 Порог продажи (для очков)", callback_data="setup_threshold")]
    ]
    await update.message.reply_text("Что настраиваем?", reply_markup=InlineKeyboardMarkup(kb))
    return SETUP_WAIT

async def setup_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    uid = query.from_user.id
    data = query.data
    if data=="setup_tz":
        await query.edit_message_text("Введи название часового пояса (пример: *Asia/Yekaterinburg*).", parse_mode="Markdown")
        context.user_data["await_tz"]=True
    elif data=="setup_sport_types":
        await query.edit_message_text("Введи список видов тренировок через запятую (пример: *зал, бассейн, теннис*).", parse_mode="Markdown")
        context.user_data["await_sport_types"]=True
    elif data=="setup_schedule":
        await query.edit_message_text("Формат: *тип, дни недели, время*. Пример: `зал; Пн Ср Пт; 19:00`.\nМожно отправить несколько сообщений.", parse_mode="Markdown")
        context.user_data["await_schedule"]=True
    elif data=="setup_notify":
        conn=get_conn(); cur=conn.cursor()
        cur.execute("SELECT notify FROM users WHERE user_id=?", (uid,)); cur_val=cur.fetchone()[0]
        new_val=0 if cur_val else 1
        cur.execute("UPDATE users SET notify=? WHERE user_id=?", (new_val, uid))
        conn.commit(); conn.close()
        if new_val:
            schedule_all_jobs(context.application, uid)
        else:
            clear_all_jobs(context.application, uid)
        await query.edit_message_text("Напоминания и автоотчёты: " + ("ВКЛ 🔔" if new_val else "ВЫКЛ 🔕"))
    elif data=="setup_threshold":
        await query.edit_message_text("Введи порог суммы продажи для начисления очков (0 = любая): например `100000`.")
        context.user_data["await_threshold"]=True

async def setup_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip()
    if context.user_data.get("await_tz"):
        import pytz
        try:
            pytz.timezone(text)
        except Exception:
            await update.message.reply_text("Не понял такой часовой пояс. Пример: Asia/Yekaterinburg")
            return
        conn=get_conn(); cur=conn.cursor()
        cur.execute("UPDATE users SET tz=? WHERE user_id=?", (text, uid))
        conn.commit(); conn.close()
        context.user_data["await_tz"]=False
        schedule_all_jobs(context.application, uid)
        await update.message.reply_text(f"Часовой пояс установлен: {text}. Готово к напоминаниям и автоотчётам.")
        return
    if context.user_data.get("await_sport_types"):
        types=[t.strip() for t in text.split(",") if t.strip()]
        conn=get_conn(); cur=conn.cursor()
        cur.execute("DELETE FROM sport_types WHERE user_id=?", (uid,))
        for name in types:
            cur.execute("INSERT INTO sport_types(user_id,name) VALUES(?,?)",(uid,name))
        conn.commit(); conn.close()
        context.user_data["await_sport_types"]=False
        await update.message.reply_text("Виды тренировок обновлены: " + ", ".join(types))
        return
    if context.user_data.get("await_schedule"):
        try:
            part = [p.strip() for p in text.split(";")]
            typ, dows_str, at = part[0], part[1], part[2]
            dmap = { "пн":0,"вт":1,"ср":2,"чт":3,"пт":4,"сб":5,"вс":6 }
            dows = [dmap[t.lower()] for t in dows_str.split()]
            conn=get_conn(); cur=conn.cursor()
            cur.execute("SELECT id FROM sport_types WHERE user_id=? AND name=?", (uid, typ))
            row=cur.fetchone()
            if not row:
                cur.execute("INSERT INTO sport_types(user_id,name) VALUES(?,?)",(uid,typ))
                type_id=cur.lastrowid
            else:
                type_id=row[0]
            for d in dows:
                cur.execute("INSERT INTO sport_schedule(user_id,type_id,dow,at_time) VALUES(?,?,?,?)",(uid,type_id,d,at))
            conn.commit(); conn.close()
            schedule_sport_jobs(context.application, uid)
            await update.message.reply_text("Расписание добавлено ✅")
        except Exception:
            await update.message.reply_text("Не смог разобрать. Пример: `зал; Пн Ср Пт; 19:00`", parse_mode="Markdown")
        return
    if context.user_data.get("await_threshold"):
        try:
            val=int(text)
        except:
            await update.message.reply_text("Нужно число, например 100000 или 0")
            return
        conn=get_conn(); cur=conn.cursor()
        cur.execute("UPDATE users SET sale_threshold=? WHERE user_id=?", (val, uid))
        conn.commit(); conn.close()
        context.user_data["await_threshold"]=False
        await update.message.reply_text(f"Порог продажи для очков: {fmt_money(val)} ₽")

# ---- Logging ----
async def log_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id; ensure_user(uid)
    conn=get_conn(); cur=conn.cursor()
    cur.execute("SELECT id,name FROM sport_types WHERE user_id=?", (uid,))
    types=cur.fetchall(); conn.close()
    sport_buttons = [InlineKeyboardButton(f"🏋️ {name}", callback_data=f"log_sport:{tid}") for tid,name in types] or [InlineKeyboardButton("Добавь виды в /setup", callback_data="noop")]
    kb = [
        sport_buttons[:3],
        sport_buttons[3:6],
        [InlineKeyboardButton("📞 Звонок", callback_data="log_biz:call"),
         InlineKeyboardButton("✨ Проявленность", callback_data="log_biz:vis")],
        [InlineKeyboardButton("💰 Продажа", callback_data="log_biz:sale"),
         InlineKeyboardButton("🧾 Касса", callback_data="log_biz:cash")],
        [InlineKeyboardButton("😴 Сон", callback_data="log_spi:sleep"),
         InlineKeyboardButton("🧘 Медитация", callback_data="log_spi:med"),
         InlineKeyboardButton("📚 Чтение", callback_data="log_spi:read")]
    ]
    await update.message.reply_text("Выбери, что логируем:", reply_markup=InlineKeyboardMarkup(kb))

async def log_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query; await query.answer()
    uid=query.from_user.id
    data=query.data
    if data.startswith("log_sport:"):
        tid=int(data.split(":")[1])
        conn=get_conn(); cur=conn.cursor()
        cur.execute("INSERT INTO log_sport(user_id,dt,type_id) VALUES(?,?,?)",(uid, datetime.utcnow().isoformat(), tid))
        conn.commit(); conn.close()
        add_points(uid, POINTS_TRAIN)
        await query.edit_message_text("🏁 Тренировка записана! +2 очка. /stats")
    elif data=="log_biz:call":
        conn=get_conn(); cur=conn.cursor()
        cur.execute("INSERT INTO log_business(user_id,dt,calls) VALUES(?,?,1)",(uid, datetime.utcnow().isoformat()))
        conn.commit(); conn.close()
        await query.edit_message_text("📞 Звонок засчитан. /stats")
    elif data=="log_biz:vis":
        conn=get_conn(); cur=conn.cursor()
        cur.execute("INSERT INTO log_business(user_id,dt,visibility) VALUES(?,?,1)",(uid, datetime.utcnow().isoformat()))
        conn.commit(); conn.close()
        await query.edit_message_text("✨ Проявленность засчитана. /stats")
    elif data=="log_biz:cash":
        await query.edit_message_text("Введите сумму, которую добавить в кассу (руб):")
        context.user_data["await_cash"]=True
    elif data=="log_biz:sale":
        await query.edit_message_text("Введите сумму продажи (руб):")
        context.user_data["await_sale"]=True
    elif data=="log_spi:sleep":
        await query.edit_message_text("Сколько часов сна? (например 7.5)")
        context.user_data["await_sleep"]=True
    elif data=="log_spi:med":
        await query.edit_message_text("Сколько минут медитации?")
        context.user_data["await_med"]=True
    elif data=="log_spi:read":
        await query.edit_message_text("Сколько минут чтения?")
        context.user_data["await_read"]=True

async def log_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    txt=update.message.text.strip()
    if context.user_data.get("await_cash"):
        try: val=int(txt)
        except: await update.message.reply_text("Нужно целое число рублей."); return
        conn=get_conn(); cur=conn.cursor()
        cur.execute("INSERT INTO log_business(user_id,dt,cash_in) VALUES(?,?,?)",(uid, datetime.utcnow().isoformat(), val))
        conn.commit(); conn.close()
        context.user_data["await_cash"]=False
        await update.message.reply_text(f"🧾 В кассу добавлено {fmt_money(val)} ₽. /stats")
        return
    if context.user_data.get("await_sale"):
        try: val=int(txt)
        except: await update.message.reply_text("Нужно целое число рублей."); return
        conn=get_conn(); cur=conn.cursor()
        cur.execute("INSERT INTO log_business(user_id,dt,sale_amount) VALUES(?,?,?)",(uid, datetime.utcnow().isoformat(), val))
        conn.commit(); conn.close()
        if val >= get_sale_threshold(uid):
            add_points(uid, POINTS_SALE)
            await update.message.reply_text(f"💰 Продажа на {fmt_money(val)} ₽! +{POINTS_SALE} очков. /stats")
        else:
            await update.message.reply_text(f"Продажа на {fmt_money(val)} ₽ записана (меньше порога для очков). /stats")
        context.user_data["await_sale"]=False
        return
    if context.user_data.get("await_sleep"):
        try: hours=float(txt)
        except: await update.message.reply_text("Пример: 7.5"); return
        conn=get_conn(); cur=conn.cursor()
        cur.execute("INSERT INTO log_spirit(user_id,dt,sleep_hours) VALUES(?,?,?)",(uid, datetime.utcnow().isoformat(), hours))
        conn.commit(); conn.close()
        context.user_data["await_sleep"]=False
        await update.message.reply_text("😴 Сон записан. /stats"); return
    if context.user_data.get("await_med"):
        try: mins=int(txt)
        except: await update.message.reply_text("Нужно целое число минут."); return
        conn=get_conn(); cur=conn.cursor()
        cur.execute("INSERT INTO log_spirit(user_id,dt,meditation_min) VALUES(?,?,?)",(uid, datetime.utcnow().isoformat(), mins))
        conn.commit(); conn.close()
        context.user_data["await_med"]=False
        await update.message.reply_text("🧘 Медитация записана. /stats"); return
    if context.user_data.get("await_read"):
        try: mins=int(txt)
        except: await update.message.reply_text("Нужно целое число минут."); return
        conn=get_conn(); cur=conn.cursor()
        cur.execute("INSERT INTO log_spirit(user_id,dt,reading_min) VALUES(?,?,?)",(uid, datetime.utcnow().isoformat(), mins))
        conn.commit(); conn.close()
        context.user_data["await_read"]=False
        await update.message.reply_text("📚 Чтение записано. /stats"); return

# ---- Stats ----
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id; ensure_user(uid)
    s30=collect_stats(uid, 30)
    msg = (
        f"📊 *30 дней прогресса*\n"
        f"🏋️ Спорт: тренировки — {s30['sport_cnt']}\n"
        f"💼 Бизнес: звонки — {s30['calls']}, проявленности — {s30['vis']}, продажи — {s30['sales_n']}, сумма — {fmt_money(int(s30['sales_sum']))} ₽, касса — {fmt_money(int(s30['cash_sum']))} ₽\n"
        f"🕊️ Духовность: сон — {s30['sleep']:.1f} ч, медитация — {s30['med']} мин, чтение — {s30['read']} мин\n"
        f"💎 Очки: {s30['points']}\n"
    )
    await update.message.reply_markdown_v2(msg)

# ---- Goals ----
async def goals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    conn=get_conn(); cur=conn.cursor()
    cur.execute("SELECT id,title,reward,sport_min,business_min,sales_min,spirit_min,points_min,deadline,created_at FROM goals WHERE user_id=?", (uid,))
    rows=cur.fetchall(); conn.close()
    if not rows:
        await update.message.reply_text("Пока нет целей. Добавь: /addgoal")
        return
    lines=[]
    for g in rows:
        gid, title, reward, smin, bmin, salemin, spmin, pmin, deadline, created = g
        lines.append(f"• {title} → {reward} (до {deadline})\n  Мин: спорт {smin}, бизнес {bmin} (+ продажи {salemin}), духовность {spmin}, очки {pmin}")
    await update.message.reply_text("🎯 Цели:\n" + "\n".join(lines))

ADDGOAL_WAIT = range(2)

async def addgoal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Опиши цель одной строкой в формате:\n"
        "`Название | Награда | спорт_min | бизнес_min | продажи_min | дух_min | очки_min | дедлайн(YYYY-MM-DD)`\n"
        "Пример:\n"
        "`Октябрьский рывок | Айпад | 10 | 30 | 3 | 7 | 0 | 2025-10-31`",
        parse_mode="Markdown"
    )
    context.user_data["await_goal"]=True
    return ADDGOAL_WAIT

async def addgoal_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("await_goal"): return
    uid=update.effective_user.id
    parts=[p.strip() for p in update.message.text.split("|")]
    if len(parts)!=8:
        await update.message.reply_text("Нужно 8 полей, смотри пример выше.")
        return
    title,reward,smin,bmin,salesmin,spmin,pmin,deadline=parts
    try:
        smin=int(smin); bmin=int(bmin); salesmin=int(salesmin); spmin=int(spmin); pmin=int(pmin)
        datetime.strptime(deadline, "%Y-%m-%d")
    except Exception:
        await update.message.reply_text("Проверь числа и дату (YYYY-MM-DD).")
        return
    conn=get_conn(); cur=conn.cursor()
    cur.execute("""INSERT INTO goals(user_id,title,reward,sport_min,business_min,sales_min,spirit_min,points_min,deadline,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   (uid,title,reward,smin,bmin,salesmin,spmin,pmin,deadline,datetime.utcnow().isoformat()))
    conn.commit(); conn.close()
    context.user_data["await_goal"]=False
    await update.message.reply_text("Цель добавлена ✅. Смотри /goals")

async def claim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    now = datetime.utcnow()
    conn=get_conn(); cur=conn.cursor()
    cur.execute("SELECT id,title,reward,sport_min,business_min,sales_min,spirit_min,points_min,deadline,created_at FROM goals WHERE user_id=?", (uid,))
    rows=cur.fetchall()
    if not rows:
        await update.message.reply_text("Нет целей для проверки. /addgoal")
        conn.close(); return
    eligible=[]; missing=[]
    for g in rows:
        gid,title,reward,smin,bmin,salesmin,spmin,pmin,deadline,created = g
        deadline_dt = datetime.strptime(deadline, "%Y-%m-%d")
        if now > deadline_dt + timedelta(days=1):
            continue
        cur.execute("SELECT COUNT(*) FROM log_sport WHERE user_id=? AND dt>=? AND dt<=?",
                    (uid, created, deadline_dt.isoformat()))
        sport_cnt = cur.fetchone()[0]
        cur.execute("""SELECT IFNULL(SUM(calls),0), IFNULL(SUM(visibility),0), IFNULL(SUM(CASE WHEN sale_amount>0 THEN 1 ELSE 0 END),0)
                       FROM log_business WHERE user_id=? AND dt>=? AND dt<=?""",
                    (uid, created, deadline_dt.isoformat()))
        calls, vis, sales_n = cur.fetchone()
        business_actions = int(calls)+int(vis)
        cur.execute("""SELECT IFNULL(COUNT(*),0) FROM log_spirit
                       WHERE user_id=? AND dt>=? AND dt<=? AND (sleep_hours>0 OR meditation_min>0 OR reading_min>0)""",
                    (uid, created, deadline_dt.isoformat()))
        spirit_n = cur.fetchone()[0]
        pts = get_points(uid)
        ok = (sport_cnt>=smin) and (business_actions>=bmin) and (sales_n>=salesmin) and (spirit_n>=spmin) and (pts>=pmin)
        if ok:
            eligible.append(f"✅ {title} → *{reward}*")
        else:
            miss = []
            if sport_cnt<smin: miss.append(f"спорт {sport_cnt}/{smin}")
            if business_actions<bmin: miss.append(f"бизнес {business_actions}/{bmin}")
            if sales_n<salesmin: miss.append(f"продажи {sales_n}/{salesmin}")
            if spirit_n<spmin: miss.append(f"дух {spirit_n}/{spmin}")
            if pts<pmin: miss.append(f"очки {pts}/{pmin}")
            missing.append(f"⏳ {title}: не хватает — " + ", ".join(miss))
    conn.close()
    if eligible:
        await update.message.reply_markdown_v2("🏆 Доступные награды:\n" + "\n".join(eligible))
    if missing:
        await update.message.reply_text("Что ещё добить до награды:\n" + "\n".join(missing))
    if not eligible and not missing:
        await update.message.reply_text("Все цели просрочены или не созданы. /addgoal")

# ---- Notifications & Еженедельный отчётs ----
def clear_all_jobs(app, uid:int):
    for job in app.job_queue.get_jobs_by_name(f"sport_rem_{uid}"):
        job.schedule_removal()
    for job in app.job_queue.get_jobs_by_name(f"weekly_{uid}"):
        job.schedule_removal()

def schedule_sport_jobs(app, uid:int):
    for job in app.job_queue.get_jobs_by_name(f"sport_rem_{uid}"):
        job.schedule_removal()
    conn=get_conn(); cur=conn.cursor()
    cur.execute("SELECT s.dow, s.at_time, t.name FROM sport_schedule s JOIN sport_types t ON s.type_id=t.id WHERE s.user_id=?", (uid,))
    rows=cur.fetchall(); conn.close()
    tz = get_user_tz(uid)
    for dow, at, name in rows:
        hh,mm = map(int, at.split(":"))
        app.job_queue.run_daily(
            callback=remind_sport,
            time=time(hour=hh, minute=mm, tzinfo=tz),
            days=(dow,),
            data={"uid":uid, "text":f"🏋️ Напоминание: {name} сегодня в {at}"},
            name=f"sport_rem_{uid}"
        )

def schedule_weekly_report(app, uid:int):
    for job in app.job_queue.get_jobs_by_name(f"weekly_{uid}"):
        job.schedule_removal()
    tz = get_user_tz(uid)
    app.job_queue.run_daily(
        callback=send_weekly_report,
        time=time(hour=WEEKLY_HOUR, minute=0, tzinfo=tz),
        days=(6,),  # Sunday
        data={"uid":uid},
        name=f"weekly_{uid}"
    )

def schedule_all_jobs(app, uid:int):
    schedule_sport_jobs(app, uid)
    schedule_weekly_report(app, uid)

async def remind_sport(context: ContextTypes.DEFAULT_TYPE):
    data=context.job.data; uid=data["uid"]
    try:
        await context.bot.send_message(chat_id=uid, text=data["text"])
    except Exception:
        pass

async def send_weekly_report(context: ContextTypes.DEFAULT_TYPE):
    uid=context.job.data["uid"]
    await _send_report_card(context.bot, uid)

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    await _send_report_card(context.bot, uid, notify=True, chat_id=uid)

async def _send_report_card(bot, uid:int, notify:bool=False, chat_id:Optional[int]=None):
    s7 = collect_stats(uid, 7)
    s30 = collect_stats(uid, 30)
    # render card
    img_path = os.path.join(os.path.dirname(__file__), "data", f"report_{uid}.png")
    render_card(uid, s7, s30, img_path)
    caption = (
        "🗓️ *Отчёт Спутника дня*\n"
        f"🏋️ Спорт: {s7['sport_cnt']} трен. (7д) • {s30['sport_cnt']} (30д)\n"
        f"💼 Бизнес: звонки {s7['calls']}, проявленности {s7['vis']}, продажи {s7['sales_n']} (7д)\n"
        f"🕊️ Дух: сон {s7['sleep']:.1f} ч, медитация {s7['med']} мин, чтение {s7['read']} мин\n"
        f"💎 Очки: {s30['points']} (накоплено)"
    )
    try:
        await bot.send_photo(chat_id=chat_id or uid, photo=InputFile(img_path), caption=caption, parse_mode="Markdown")
    except Exception:
        # Fallback: send text only
        await bot.send_message(chat_id=chat_id or uid, text=caption, parse_mode="Markdown")

async def notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid=update.effective_user.id
    conn=get_conn(); cur=conn.cursor()
    cur.execute("SELECT notify FROM users WHERE user_id=?", (uid,)); cur_val=cur.fetchone()[0]
    new_val=0 if cur_val else 1
    cur.execute("UPDATE users SET notify=? WHERE user_id=?", (new_val, uid))
    conn.commit(); conn.close()
    if new_val:
        schedule_all_jobs(context.application, uid)
    else:
        clear_all_jobs(context.application, uid)
    await update.message.reply_text("Напоминания и автоотчёты: " + ("ВКЛ 🔔" if new_val else "ВЫКЛ 🔕"))

# ------------- Wiring -------------------
def build_app():
    token = os.getenv("TELEGRAM_TOKEN")
    if not token and os.path.exists(os.path.join(os.path.dirname(__file__), ".env")):
        for line in open(os.path.join(os.path.dirname(__file__), ".env"), "r", encoding="utf-8"):
            if line.startswith("TELEGRAM_TOKEN="):
                token=line.strip().split("=",1)[1]
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN не найден. Создай .env или переменную окружения.")
    init_db()
    app = ApplicationBuilder().token(token).build()
    # commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("onboard", onboard))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("setup", setup))
    app.add_handler(CallbackQueryHandler(setup_cb, pattern="^setup_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, onboard_text))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, setup_text))
    app.add_handler(CommandHandler("log", log_menu))
    app.add_handler(CallbackQueryHandler(log_cb, pattern="^(log_sport|log_biz|log_spi):"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_text))  # keep order
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("goals", goals))
    app.add_handler(CommandHandler("addgoal", addgoal))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, addgoal_text))  # keep order
    app.add_handler(CommandHandler("claim", claim))
    app.add_handler(CommandHandler("notify", notify))
    app.add_handler(CommandHandler("report", report))
    return app

def main():
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
