
import os
import sqlite3
from datetime import datetime, timedelta
import pytz
from PIL import Image, ImageDraw, ImageFont
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
import asyncio

# ----------------- Config -----------------
TOKEN = os.getenv("TELEGRAM_TOKEN")
BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "data", "bot.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

DEFAULT_TZ = "Asia/Yekaterinburg"
POINTS_TRAIN = 2
POINTS_SALE = 10

CARD_W, CARD_H = 1200, 628
COLOR_BG = (234,231,226)   # warm grey
COLOR_TEXT = (34,34,34)
COLOR_ACCENT = (46,125,50) # green
COLOR_GOLD = (212,175,55)

# ----------------- DB -----------------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_conn(); cur = conn.cursor()
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
        dow INTEGER,
        at_time TEXT
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        kind TEXT,
        value INTEGER,
        payload TEXT,
        created_at TEXT
    )
    """)
    conn.commit(); conn.close()

def ensure_user(uid: int):
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT 1 FROM users WHERE user_id=?", (uid,))
    if not cur.fetchone():
        cur.execute(
            "INSERT INTO users(user_id, created_at, tz, sale_threshold) VALUES(?, ?, ?, ?)",
            (uid, datetime.utcnow().isoformat(), DEFAULT_TZ, 0)
        )
        conn.commit()
    conn.close()

def get_tz(uid:int)->str:
    conn=get_conn(); cur=conn.cursor()
    cur.execute("SELECT tz FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row and row[0] else DEFAULT_TZ

def get_sale_threshold(uid:int)->int:
    conn=get_conn(); cur=conn.cursor()
    cur.execute("SELECT sale_threshold FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone(); conn.close()
    return int(row[0]) if row and row[0] is not None else 0

def set_sale_threshold(uid:int, val:int):
    conn=get_conn(); cur=conn.cursor()
    cur.execute("UPDATE users SET sale_threshold=? WHERE user_id=?", (val, uid))
    conn.commit(); conn.close()

# ----------------- Helpers -----------------
def fmt_money(v:int)->str:
    s = f"{v:,}".replace(",", " ")
    return s + " ₽"

def log_action(uid:int, kind:str, value:int|None=None, payload:str|None=None):
    conn=get_conn(); cur=conn.cursor()
    cur.execute("INSERT INTO logs(user_id, kind, value, payload, created_at) VALUES(?,?,?,?,?)",
                (uid, kind, value, payload, datetime.utcnow().isoformat()))
    conn.commit(); conn.close()

def get_stats(uid:int, days:int):
    since = datetime.utcnow() - timedelta(days=days)
    conn=get_conn(); cur=conn.cursor()
    cur.execute("SELECT kind, value, created_at FROM logs WHERE user_id=? AND created_at>=?",
                (uid, since.isoformat()))
    rows = cur.fetchall(); conn.close()
    out = {"sport":0, "calls":0, "acts":0, "sales":0, "cash":0, "sleep":0, "med":0, "read":0}
    for k,v,ts in rows:
        if k=="sport": out["sport"] += 1
        elif k=="call": out["calls"] += 1
        elif k=="act": out["acts"] += 1
        elif k=="sale":
            out["sales"] += 1
            out["cash"] += (v or 0)
        elif k=="sleep": out["sleep"] += (v or 0)
        elif k=="med": out["med"] += (v or 0)
        elif k=="read": out["read"] += (v or 0)
    return out

# ----------------- Card -----------------
def render_card(uid:int, path:str)->str:
    s7 = get_stats(uid, 7)
    s30 = get_stats(uid, 30)

    img = Image.new("RGB", (CARD_W, CARD_H), COLOR_BG)
    d = ImageDraw.Draw(img)
    try:
        f_big = ImageFont.truetype("DejaVuSans-Bold.ttf", 64)
        f_mid = ImageFont.truetype("DejaVuSans.ttf", 36)
        f_sm  = ImageFont.truetype("DejaVuSans.ttf", 28)
    except:
        f_big = f_mid = f_sm = ImageFont.load_default()

    # Logo
    try:
        logo_path = os.path.join(BASE_DIR, "assets", "logo.png")
        lg = Image.open(logo_path).convert("RGBA")
        h = 96; ratio = h / lg.height
        lg = lg.resize((int(lg.width*ratio), h))
        img.paste(lg, (36, 28), lg)
    except Exception:
        pass

    d.text((48, 140), "Спутник дня — отчёт", fill=COLOR_TEXT, font=f_big)
    d.line((48, 210, CARD_W-48, 210), fill=COLOR_ACCENT, width=4)

    # 7 days
    y = 250
    d.text((48, y), "За 7 дней", fill=COLOR_TEXT, font=f_mid)
    d.text((300, y), f"Спорт: {s7['sport']} • Продаж: {s7['sales']} • Касса: {fmt_money(s7['cash'])}", fill=COLOR_TEXT, font=f_mid)
    d.line((48, y+40, CARD_W-48, y+40), fill=COLOR_ACCENT, width=2)

    # 30 days
    y = 320
    d.text((48, y), "За 30 дней", fill=COLOR_TEXT, font=f_mid)
    d.text((300, y), f"Спорт: {s30['sport']} • Продаж: {s30['sales']} • Касса: {fmt_money(s30['cash'])}", fill=COLOR_TEXT, font=f_mid)
    d.line((48, y+40, CARD_W-48, y+40), fill=COLOR_ACCENT, width=2)

    d.text((48, CARD_H-70), "Баланс — это ритм твоей жизни.", fill=COLOR_TEXT, font=f_sm)

    cards_dir = os.path.join(BASE_DIR, "data", "cards")
    os.makedirs(cards_dir, exist_ok=True)
    img.save(path)
    return path

# ----------------- Handlers -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    txt = (
        "Привет! Я — Спутник дня.\n"
        "Помогаю держать баланс: тело × дело × душа.\n\n"
        "Команды:\n"
        "/onboard — быстрая настройка\n"
        "/log — быстрый лог\n"
        "/report — карточка отчёта\n"
        "/stats — краткая статистика\n"
    )
    await update.message.reply_text(txt)
    # send logo
    try:
        with open(os.path.join(BASE_DIR, "assets", "logo.png"), "rb") as f:
            await update.message.reply_photo(photo=f, caption="Спутник дня — твой баланс на орбите дня.")
    except Exception:
        pass

# Onboarding simplified
ONB_TZ, ONB_TYPES, ONB_THRESH, ONB_NOTIFY = range(4)

async def onboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    context.user_data["onb_state"] = ONB_TZ
    await update.message.reply_text("Шаг 1/4: Введи часовой пояс (пример: Asia/Yekaterinburg).")

async def onboard_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if "onb_state" not in context.user_data:
        return
    state = context.user_data["onb_state"]
    text = update.message.text.strip()

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
        await update.message.reply_text("Шаг 2/4: Введи виды тренировок через запятую (например: зал, бассейн, теннис).")
        return

    if state == ONB_TYPES:
        types = [t.strip() for t in text.split(",") if t.strip()]
        if not types:
            await update.message.reply_text("Добавь хотя бы один вид, пример: зал, бассейн")
            return
        conn=get_conn(); cur=conn.cursor()
        cur.execute("DELETE FROM sport_types WHERE user_id=?", (uid,))
        for name in types:
            cur.execute("INSERT INTO sport_types(user_id,name) VALUES(?,?)",(uid,name))
        conn.commit(); conn.close()
        context.user_data["onb_state"] = ONB_THRESH
        await update.message.reply_text("Шаг 3/4: Введи минимальную сумму продажи для очков (напр. 100000). 0 — очки за любую продажу.")
        return

    if state == ONB_THRESH:
        try:
            val = int(text)
        except:
            await update.message.reply_text("Нужно число в рублях. Пример: 100000 или 0.")
            return
        set_sale_threshold(uid, val)
        context.user_data["onb_state"] = ONB_NOTIFY
        await update.message.reply_text("Шаг 4/4: Включить напоминания и автоотчёты? (да/нет)")
        return

    if state == ONB_NOTIFY:
        ans = text.lower()
        on = ans in ("да","yes","y","+","вкл","on","конечно")
        conn=get_conn(); cur=conn.cursor()
        cur.execute("UPDATE users SET notify=? WHERE user_id=?", (1 if on else 0, uid))
        conn.commit(); conn.close()
        context.user_data.pop("onb_state", None)
        await update.message.reply_text("Готово! Используй /log для действий и /report для карточки.")
        return

async def log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ensure_user(uid)
    await update.message.reply_text(
        "Что записать?\n"
        "Напиши одно из:\n"
        "спорт\nзвонок\nактивность\nпродажа 120000\nкасса 50000\nсон 7\nмедитация 15\nкнига 20"
    )

async def log_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip().lower()
    if text == "спорт":
        log_action(uid, "sport", None, None)
        await update.message.reply_text(f"Тренировка записана (+{POINTS_TRAIN} очка).")
        return
    if text == "звонок":
        log_action(uid, "call", None, None)
        await update.message.reply_text("Звонок записан.")
        return
    if text == "активность":
        log_action(uid, "act", None, None)
        await update.message.reply_text("Проявленность записана.")
        return
    if text.startswith("продажа"):
        parts = text.split()
        if len(parts)>=2 and parts[1].isdigit():
            amount = int(parts[1])
            thr = get_sale_threshold(uid)
            pts = POINTS_SALE if amount >= thr else 0
            log_action(uid, "sale", amount, None)
            await update.message.reply_text(f"Продажа {amount} ₽. Очки: {pts}.")
        else:
            await update.message.reply_text("Формат: продажа 120000")
        return
    if text.startswith("касса"):
        parts = text.split()
        if len(parts)>=2 and parts[1].isdigit():
            amount = int(parts[1]); log_action(uid, "cash", amount, None)
            await update.message.reply_text(f"Касса +{amount} ₽.")
        else:
            await update.message.reply_text("Формат: касса 50000")
        return
    if text.startswith("сон"):
        parts = text.split()
        if len(parts)>=2 and parts[1].isdigit():
            hours = int(parts[1]); log_action(uid, "sleep", hours, None)
            await update.message.reply_text(f"Сон {hours} ч записан.")
        return
    if text.startswith("медитация"):
        parts = text.split()
        if len(parts)>=2 and parts[1].isdigit():
            mins = int(parts[1]); log_action(uid, "med", mins, None)
            await update.message.reply_text(f"Медитация {mins} мин записана.")
        return
    if text.startswith("книга"):
        parts = text.split()
        if len(parts)>=2 and parts[1].isdigit():
            mins = int(parts[1]); log_action(uid, "read", mins, None)
            await update.message.reply_text(f"Чтение {mins} мин записано.")
        return

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    cards_dir = os.path.join(BASE_DIR, "data", "cards")
    os.makedirs(cards_dir, exist_ok=True)
    p = os.path.join(cards_dir, f"report_{uid}.png")
    render_card(uid, p)
    with open(p, "rb") as f:
        await update.message.reply_photo(photo=f, caption="Отчёт Спутника дня.")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    s30 = get_stats(uid, 30)
    txt = (
        "Статистика за 30 дней:\n"
        f"Спорт: {s30['sport']}\n"
        f"Продаж: {s30['sales']}\n"
        f"Касса: {s30['cash']} ₽\n"
        f"Сон: {s30['sleep']} ч, медитация: {s30['med']} мин, чтение: {s30['read']} мин"
    )
    await update.message.reply_text(txt)

# ----------------- App -----------------
def build_app()->Application:
    if not TOKEN:
        raise RuntimeError("TELEGRAM_TOKEN is not set")
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("onboard", onboard))
    app.add_handler(CommandHandler("log", log_cmd))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("stats", stats))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, onboard_text))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_text))
    return app

def main():
    app = build_app()
    # Clean webhook & pending updates to avoid conflicts
    try:
        asyncio.get_event_loop().run_until_complete(app.bot.delete_webhook(drop_pending_updates=True))
    except Exception:
        pass
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
