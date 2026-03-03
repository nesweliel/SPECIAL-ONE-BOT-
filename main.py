import os, json, logging, asyncio, io
from datetime import datetime, time as dtime
from flask import Flask, jsonify, request
from flask_cors import CORS
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# ── CONFIG ──
BOT_TOKEN    = os.environ["BOT_TOKEN"]
ALLOWED_ID   = os.environ.get("ALLOWED_USER_ID", "")
FOLDER_ID    = os.environ["DRIVE_FOLDER_ID"]
PORT         = int(os.environ.get("PORT", "8080"))

# מסד נתונים זמני (לצורך הוכחת יכולת - בהמשך נחבר לדרייב)
data = {
    "tasks": [],
    "activity": [],
    "stats": {"completed": 0, "total": 0}
}

# ── FLASK API (הזנת ה-Dashboard) ──
app = Flask(__name__)
CORS(app)

@app.route('/api/data', methods=['GET'])
def get_data():
    return jsonify(data)

# ── TELEGRAM BOT ──
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ALLOWED_ID: return
    
    task_text = update.message.text
    new_task = {
        "id": len(data["tasks"]) + 1,
        "name": task_text,
        "status": "pending",
        "time": datetime.now().strftime("%H:%M")
    }
    data["tasks"].append(new_task)
    data["activity"].insert(0, f"נוספה משימה: {task_text}")
    
    await update.message.reply_text(f"✅ SPECIAL ONE עודכן: המשימה '{task_text}' נוספה ל-Dashboard.")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🦇 **SPECIAL ONE OS v5.0**\nמערכת השליטה מחוברת. שלח משימה או תמונה.")

# ── RUNNER ──
def run_flask():
    app.run(host='0.0.0.0', port=PORT)

def main():
    # הרצת ה-API בנפרד כדי שה-HTML יוכל לקרוא נתונים
    threading.Thread(target=run_flask, daemon=True).start()

    # הרצת הבוט
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.run_polling()

if __name__ == "__main__":
    main()
