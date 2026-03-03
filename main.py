import os, json, logging, threading
from datetime import datetime, time as dtime
from flask import Flask, request, jsonify
from flask_cors import CORS
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import io

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── CONFIG ────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ["BOT_TOKEN"]
ALLOWED_ID   = os.environ.get("ALLOWED_USER_ID", "")
FOLDER_ID    = os.environ["DRIVE_FOLDER_ID"]
MORNING_HOUR = int(os.environ.get("MORNING_HOUR", "8"))
MORNING_MIN  = int(os.environ.get("MORNING_MIN", "0"))
API_SECRET   = os.environ.get("API_SECRET", "specialone2026")
FILE_NAME    = "specialone_tasks.json"
PORT         = int(os.environ.get("PORT", "8080"))

# ── FLASK ─────────────────────────────────────────────────────────
flask_app = Flask(__name__)
CORS(flask_app)

# ── GOOGLE DRIVE ──────────────────────────────────────────────────
def drive_service():
    info  = json.loads(os.environ["GOOGLE_CREDS_JSON"])
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"])
    return build("drive", "v3", credentials=creds, cache_discovery=False)

def find_file(svc):
    r = svc.files().list(
        q=f"name='{FILE_NAME}' and '{FOLDER_ID}' in parents and trashed=false",
        fields="files(id)").execute()
    files = r.get("files", [])
    return files[0]["id"] if files else None

def read_tasks():
    try:
        svc  = drive_service()
        fid  = find_file(svc)
        if not fid:
            return []
        buf  = io.BytesIO()
        dl   = MediaIoBaseDownload(buf, svc.files().get_media(fileId=fid))
        done = False
        while not done:
            _, done = dl.next_chunk()
        buf.seek(0)
        return json.loads(buf.read().decode())
    except Exception as e:
        logger.error(f"read_tasks: {e}")
        return []

def write_tasks(tasks):
    try:
        svc     = drive_service()
        content = json.dumps(tasks, ensure_ascii=False, indent=2).encode()
        media   = MediaIoBaseUpload(io.BytesIO(content), mimetype="application/json")
        fid     = find_file(svc)
        if fid:
            svc.files().update(fileId=fid, media_body=media).execute()
        else:
            svc.files().create(
                body={"name": FILE_NAME, "parents": [FOLDER_ID]},
                media_body=media).execute()
        return True
    except Exception as e:
        logger.error(f"write_tasks: {e}")
        return False

# ── API ROUTES ────────────────────────────────────────────────────
def check_auth(req):
    secret = req.headers.get("X-API-Secret") or req.args.get("secret")
    return secret == API_SECRET

@flask_app.route("/health")
def health():
    return jsonify({"status": "ok", "time": datetime.now().isoformat()})

@flask_app.route("/api/data", methods=["GET"])
def api_data_get():
    tasks = read_tasks()
    return jsonify({"tasks": tasks, "count": len(tasks)})

@flask_app.route("/api/data", methods=["POST"])
def api_data_post():
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        data  = request.get_json()
        tasks = data.get("tasks", data) if isinstance(data, dict) else data
        if not isinstance(tasks, list):
            return jsonify({"error": "Expected array"}), 400
        if write_tasks(tasks):
            return jsonify({"ok": True, "count": len(tasks)})
        return jsonify({"error": "Drive write failed"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@flask_app.route("/tasks", methods=["GET"])
def api_tasks_get():
    return jsonify(read_tasks())

@flask_app.route("/tasks", methods=["POST"])
def api_tasks_post():
    if not check_auth(request):
        return jsonify({"error": "Unauthorized"}), 401
    try:
        tasks = request.get_json()
        if not isinstance(tasks, list):
            return jsonify({"error": "Expected array"}), 400
        if write_tasks(tasks):
            return jsonify({"ok": True, "count": len(tasks)})
        return jsonify({"error": "Drive write failed"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── HELPERS ───────────────────────────────────────────────────────
def deadline_days(dl):
    if not dl: return None
    try:
        d   = datetime.strptime(dl, "%Y-%m-%d")
        now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return (d - now).days
    except: return None

def dl_label(dl):
    d = deadline_days(dl)
    if d is None: return ""
    if d < 0:     return f"⚠️ {abs(d)}d overdue"
    if d == 0:    return "🔴 TODAY"
    if d == 1:    return "🟡 Tomorrow"
    if d <= 7:    return f"🟡 {d}d left"
    return f"📅 {dl}"

def pri_icon(p):
    return {"high":"🔴","mid":"🟡","low":"🟢"}.get(p,"⚪")

def allowed(update):
    if not ALLOWED_ID: return True
    return str(update.effective_user.id) == ALLOWED_ID

def next_id(tasks):
    return max((t.get("id", 0) for t in tasks), default=0) + 1

def open_tasks(tasks):
    return [t for t in tasks if t.get("status") != "done"]

def clean(s):
    return str(s).replace('*','').replace('_','').replace('[','').replace(']','')

def format_task(t, i=None):
    prefix = f"{i}. " if i else "• "
    dl     = dl_label(t.get("deadline", ""))
    dl_str = f"  {dl}" if dl else ""
    return f"{prefix}{pri_icon(t.get('pri','low'))} *{clean(t['name'])}*{dl_str}\n   _{t.get('cat','')}_"

def task_score(t):
    s = 0
    if t.get("pri") == "high": s += 30
    if t.get("pri") == "mid":  s += 15
    d = deadline_days(t.get("deadline"))
    if d is not None:
        if d < 0:    s += 60
        elif d <= 3: s += 50
        elif d <= 7: s += 35
    if t.get("status") == "wip": s += 10
    return s

def build_morning_message(tasks):
    today   = datetime.now().strftime("%A, %d %b %Y")
    opened  = open_tasks(tasks)
    top3    = sorted(opened, key=task_score, reverse=True)[:3]
    overdue = [t for t in opened if deadline_days(t.get("deadline")) is not None
               and deadline_days(t.get("deadline")) < 0]
    lines = [
        "🦇 *SPECIAL ONE — Morning Brief*",
        f"📅 {today}", "",
        f"*📊 Status:* {len(opened)} open · {len(tasks)-len(opened)} done", "",
        "*⚡ Top 3 להיום:*",
    ]
    for i, t in enumerate(top3, 1):
        lines.append(format_task(t, i))
    if overdue:
        lines += ["", f"*🚨 Overdue ({len(overdue)}):*"]
        for t in overdue[:3]:
            lines.append(format_task(t))
    lines += ["", "💬 שלח טקסט להוספת משימה",
              "/tasks — כל המשימות  |  /done — סמן כהושלם"]
    return "\n".join(lines)

# ── TELEGRAM HANDLERS ─────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    await update.message.reply_text(
        f"🦇 *SPECIAL ONE Bot*\n\nID: `{update.effective_user.id}`\n\n"
        f"/tasks · /today · /done · /stats\n\n"
        f"*הוסף משימה:*\n"
        f"`משימה` — Mid\n`! משימה` — High 🔴\n`~ משימה` — Low 🟢\n"
        f"`משימה | 2026-03-15` — עם דדליין",
        parse_mode="Markdown")

async def cmd_tasks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    tasks  = read_tasks()
    opened = open_tasks(tasks)
    if not opened:
        await update.message.reply_text("✅ אין משימות פתוחות!"); return
    cats = {}
    for t in opened:
        cats.setdefault(t.get("cat", "General"), []).append(t)
    lines = [f"📋 *{len(opened)} משימות פתוחות:*\n"]
    for cat, ct in cats.items():
        lines.append(f"*{clean(cat)}*")
        for t in sorted(ct, key=task_score, reverse=True):
            lines.append(format_task(t))
        lines.append("")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    await update.message.reply_text(
        build_morning_message(read_tasks()), parse_mode="Markdown")

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    tasks   = read_tasks()
    opened  = open_tasks(tasks)
    done    = [t for t in tasks if t.get("status") == "done"]
    overdue = [t for t in opened if deadline_days(t.get("deadline","")) is not None
               and deadline_days(t.get("deadline","")) < 0]
    cats = {}
    for t in opened:
        c = t.get("cat","General"); cats[c] = cats.get(c,0)+1
    lines = ["📊 *SPECIAL ONE Stats*\n",
             f"✅ הושלמו: *{len(done)}*",
             f"📂 פתוחות: *{len(opened)}*",
             f"🔴 High: *{len([t for t in opened if t.get('pri')=='high'])}*",
             f"⚠️ Overdue: *{len(overdue)}*", "", "*לפי פרויקט:*"]
    for cat, count in sorted(cats.items(), key=lambda x:-x[1]):
        lines.append(f"  • {clean(cat)}: {count}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    tasks  = read_tasks()
    opened = sorted(open_tasks(tasks), key=task_score, reverse=True)[:10]
    if not opened:
        await update.message.reply_text("אין משימות פתוחות!"); return
    buttons = [[InlineKeyboardButton(
        f"{pri_icon(t.get('pri','low'))} {t['name'][:38]}",
        callback_data=f"done_{t['id']}")] for t in opened]
    await update.message.reply_text(
        "✅ *איזו משימה הושלמה?*",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown")

async def cb_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    task_id = int(q.data.replace("done_",""))
    tasks   = read_tasks()
    task    = next((t for t in tasks if t.get("id") == task_id), None)
    if not task:
        await q.edit_message_text("משימה לא נמצאה."); return
    task["status"]       = "done"
    task["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    if write_tasks(tasks):
        await q.edit_message_text(
            f"✅ *{clean(task['name'])}* — הושלם! 🎉", parse_mode="Markdown")
    else:
        await q.edit_message_text("❌ שגיאה בשמירה.")

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    text = update.message.text.strip()
    if not text: return
    pri, name = "mid", text
    if text.startswith("!"):   pri, name = "high", text[1:].strip()
    elif text.startswith("~"): pri, name = "low",  text[1:].strip()
    deadline = ""
    if "|" in name:
        parts = name.split("|", 1)
        name, deadline = parts[0].strip(), parts[1].strip()
    tasks = read_tasks()
    tasks.append({
        "id":         next_id(tasks),
        "name":       name,
        "detail":     "",
        "cat":        "ARMA GIDEON",
        "dept":       "—",
        "pri":        pri,
        "status":     "open",
        "deadline":   deadline,
        "notes":      "נוסף מהבוט",
        "checklist":  [],
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M")
    })
    if write_tasks(tasks):
        dl_str = f"\n📅 {deadline}" if deadline else ""
        await update.message.reply_text(
            f"✅ נוסף: {pri_icon(pri)} *{clean(name)}*{dl_str}",
            parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ שגיאה בשמירה.")

async def morning_brief(ctx: ContextTypes.DEFAULT_TYPE):
    if not ALLOWED_ID: return
    try:
        await ctx.bot.send_message(
            chat_id=int(ALLOWED_ID),
            text=build_morning_message(read_tasks()),
            parse_mode="Markdown")
        logger.info("Morning brief sent.")
    except Exception as e:
        logger.error(f"morning_brief: {e}")

# ── FLASK in background thread ────────────────────────────────────
def run_flask():
    logger.info(f"✅ Flask API on port {PORT}")
    flask_app.run(host="0.0.0.0", port=PORT, use_reloader=False)

# ── MAIN — Bot runs in main thread ───────────────────────────────
def main():
    # Flask in background
    threading.Thread(target=run_flask, daemon=True).start()

    # Bot in main thread
    tg = Application.builder().token(BOT_TOKEN).build()
    tg.add_handler(CommandHandler("start", cmd_start))
    tg.add_handler(CommandHandler("tasks", cmd_tasks))
    tg.add_handler(CommandHandler("today", cmd_today))
    tg.add_handler(CommandHandler("done",  cmd_done))
    tg.add_handler(CommandHandler("stats", cmd_stats))
    tg.add_handler(CallbackQueryHandler(cb_done, pattern="^done_"))
    tg.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    tg.job_queue.run_daily(morning_brief, time=dtime(MORNING_HOUR, MORNING_MIN, 0))

    logger.info(f"🦇 Bot running — brief at {MORNING_HOUR:02d}:{MORNING_MIN:02d}")
    tg.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
