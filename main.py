import os, json, asyncio, logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import io

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── CONFIG ──────────────────────────────────────────────────────
BOT_TOKEN    = os.environ["BOT_TOKEN"]
ALLOWED_ID   = os.environ.get("ALLOWED_USER_ID", "")
FOLDER_ID    = os.environ["DRIVE_FOLDER_ID"]
MORNING_HOUR = int(os.environ.get("MORNING_HOUR", "8"))
MORNING_MIN  = int(os.environ.get("MORNING_MIN",  "0"))
FILE_NAME    = "specialone_tasks.json"

# ── GOOGLE DRIVE ────────────────────────────────────────────────
def drive_service():
    info  = json.loads(os.environ["GOOGLE_CREDS_JSON"])
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"])
    return build("drive", "v3", credentials=creds, cache_discovery=False)

def find_file(svc):
    r     = svc.files().list(
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

# ── HELPERS ─────────────────────────────────────────────────────
def deadline_days(dl):
    if not dl: return None
    try:
        d   = datetime.strptime(dl, "%Y-%m-%d")
        now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return (d - now).days
    except:
        return None

def dl_label(dl):
    d = deadline_days(dl)
    if d is None:  return ""
    if d < 0:      return f"⚠️ {abs(d)}d overdue"
    if d == 0:     return "🔴 TODAY"
    if d == 1:     return "🟡 Tomorrow"
    if d <= 7:     return f"🟡 {d}d left"
    return f"📅 {dl}"

def pri_icon(p):
    return {"high": "🔴", "mid": "🟡", "low": "🟢"}.get(p, "⚪")

def allowed(update: Update):
    if not ALLOWED_ID: return True
    return str(update.effective_user.id) == ALLOWED_ID

def next_id(tasks):
    return max((t.get("id", 0) for t in tasks), default=0) + 1

def open_tasks(tasks):
    return [t for t in tasks if t.get("status") != "done"]

def format_task(t, i=None):
    prefix = f"{i}\\. " if i else "• "
    dl     = dl_label(t.get("deadline", ""))
    dl_str = f"  {dl}" if dl else ""
    cat    = t.get("cat", "")
    name   = t['name'].replace('*','').replace('_','')
    return f"{prefix}{pri_icon(t.get('pri','low'))} *{name}*{dl_str}\n   _{cat}_"

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

# ── MORNING MESSAGE ─────────────────────────────────────────────
def build_morning_message(tasks):
    today  = datetime.now().strftime("%A, %d %b %Y")
    opened = open_tasks(tasks)
    top3   = sorted(opened, key=task_score, reverse=True)[:3]
    overdue= [t for t in opened
              if deadline_days(t.get("deadline")) is not None
              and deadline_days(t.get("deadline")) < 0]

    lines = [
        "🦇 *SPECIAL ONE — Morning Brief*",
        f"📅 {today}",
        "",
        f"*📊 Status:* {len(opened)} open · {len(tasks)-len(opened)} done",
        "",
        "*⚡ Top 3 להיום:*",
    ]
    for i, t in enumerate(top3, 1):
        lines.append(format_task(t, i))

    if overdue:
        lines += ["", f"*🚨 Overdue \\({len(overdue)}\\):*"]
        for t in overdue[:3]:
            lines.append(format_task(t))

    lines += [
        "",
        "💬 שלח טקסט להוספת משימה",
        "/tasks — כל המשימות  |  /done — סמן כהושלם",
    ]
    return "\n".join(lines)

# ── COMMANDS ────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    uid = update.effective_user.id
    await update.message.reply_text(
        f"🦇 *SPECIAL ONE Bot*\n\n"
        f"Your Telegram ID: `{uid}`\n\n"
        f"*פקודות:*\n"
        f"/tasks — כל המשימות הפתוחות\n"
        f"/today — brief של היום\n"
        f"/done — סמן משימה כהושלמה\n"
        f"/stats — סטטיסטיקות\n\n"
        f"*להוספת משימה — פשוט כתוב:*\n"
        f"`שם המשימה` — עדיפות Mid\n"
        f"`! שם המשימה` — עדיפות High 🔴\n"
        f"`~ שם המשימה` — עדיפות Low 🟢\n"
        f"`משימה | 2026-03-15` — עם דדליין",
        parse_mode="Markdown"
    )

async def cmd_tasks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    tasks  = read_tasks()
    opened = open_tasks(tasks)
    if not opened:
        await update.message.reply_text("✅ אין משימות פתוחות — הכל נקי\\!", parse_mode="MarkdownV2")
        return

    cats = {}
    for t in opened:
        c = t.get("cat", "General")
        cats.setdefault(c, []).append(t)

    lines = [f"📋 *{len(opened)} משימות פתוחות:*\n"]
    for cat, cat_tasks in cats.items():
        lines.append(f"*{cat}*")
        for t in sorted(cat_tasks, key=task_score, reverse=True):
            lines.append(format_task(t))
        lines.append("")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    tasks = read_tasks()
    await update.message.reply_text(build_morning_message(tasks), parse_mode="Markdown")

async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    tasks   = read_tasks()
    opened  = open_tasks(tasks)
    done    = [t for t in tasks if t.get("status") == "done"]
    high    = [t for t in opened if t.get("pri") == "high"]
    wip     = [t for t in opened if t.get("status") == "wip"]
    overdue = [t for t in opened
               if deadline_days(t.get("deadline","")) is not None
               and deadline_days(t.get("deadline","")) < 0]

    cats = {}
    for t in opened:
        c = t.get("cat","General")
        cats[c] = cats.get(c, 0) + 1

    lines = [
        "📊 *SPECIAL ONE Stats*\n",
        f"✅ הושלמו: *{len(done)}*",
        f"📂 פתוחות: *{len(opened)}*",
        f"⚡ Active \\(WIP\\): *{len(wip)}*",
        f"🔴 High priority: *{len(high)}*",
        f"⚠️ Overdue: *{len(overdue)}*",
        "",
        "*לפי פרויקט:*"
    ]
    for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
        lines.append(f"  • {cat}: {count}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    tasks  = read_tasks()
    opened = open_tasks(tasks)
    if not opened:
        await update.message.reply_text("אין משימות פתוחות!")
        return

    top = sorted(opened, key=task_score, reverse=True)[:10]
    buttons = []
    for t in top:
        label = f"{pri_icon(t.get('pri','low'))} {t['name'][:38]}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"done_{t['id']}")])

    await update.message.reply_text(
        "✅ *איזו משימה הושלמה?*",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown"
    )

async def callback_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    task_id = int(query.data.replace("done_", ""))
    tasks   = read_tasks()
    task    = next((t for t in tasks if t.get("id") == task_id), None)
    if not task:
        await query.edit_message_text("משימה לא נמצאה.")
        return
    task["status"]       = "done"
    task["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    if write_tasks(tasks):
        name = task['name'].replace('*','').replace('_','')
        await query.edit_message_text(f"✅ *{name}* — הושלם\\! 🎉", parse_mode="MarkdownV2")
    else:
        await query.edit_message_text("❌ שגיאה בשמירה.")

# ── FREE TEXT → NEW TASK ────────────────────────────────────────
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update): return
    text = update.message.text.strip()
    if not text: return

    # Priority prefix
    pri  = "mid"
    name = text
    if text.startswith("!"):
        pri  = "high"
        name = text[1:].strip()
    elif text.startswith("~"):
        pri  = "low"
        name = text[1:].strip()

    # Deadline suffix: "משימה | 2026-03-15"
    deadline = ""
    if "|" in name:
        parts    = name.split("|", 1)
        name     = parts[0].strip()
        deadline = parts[1].strip()

    tasks    = read_tasks()
    new_task = {
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
    }

    tasks.append(new_task)
    if write_tasks(tasks):
        dl_str   = f"\n📅 {deadline}" if deadline else ""
        name_clean = name.replace('*','').replace('_','')
        await update.message.reply_text(
            f"✅ נוסף: {pri_icon(pri)} *{name_clean}*{dl_str}\n\n"
            f"_💡 ! = High  ·  ~ = Low  ·  | YYYY\\-MM\\-DD = דדליין_",
            parse_mode="MarkdownV2"
        )
    else:
        await update.message.reply_text("❌ שגיאה בשמירה ל\\-Drive\\.", parse_mode="MarkdownV2")

# ── SCHEDULED MORNING BRIEF ─────────────────────────────────────
async def morning_brief(ctx: ContextTypes.DEFAULT_TYPE):
    if not ALLOWED_ID:
        return
    try:
        tasks = read_tasks()
        msg   = build_morning_message(tasks)
        await ctx.bot.send_message(chat_id=int(ALLOWED_ID), text=msg, parse_mode="Markdown")
        logger.info("Morning brief sent.")
    except Exception as e:
        logger.error(f"morning_brief error: {e}")

# ── MAIN ────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("tasks",  cmd_tasks))
    app.add_handler(CommandHandler("today",  cmd_today))
    app.add_handler(CommandHandler("done",   cmd_done))
    app.add_handler(CommandHandler("stats",  cmd_stats))
    app.add_handler(CallbackQueryHandler(callback_done, pattern="^done_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Daily morning brief
    from datetime import time as dtime
    app.job_queue.run_daily(
        morning_brief,
        time=dtime(hour=MORNING_HOUR, minute=MORNING_MIN, second=0)
    )

    logger.info(f"🦇 Special One Bot running — morning brief at {MORNING_HOUR:02d}:{MORNING_MIN:02d}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
