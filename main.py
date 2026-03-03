import os, json, logging, io
from datetime import datetime, time as dtime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# הגדרות לוגים
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# משיכת נתונים מה-Variables של Railway
BOT_TOKEN    = os.environ["BOT_TOKEN"]
ALLOWED_ID   = os.environ.get("ALLOWED_USER_ID", "")
FOLDER_ID    = os.environ["DRIVE_FOLDER_ID"]
MORNING_HOUR = int(os.environ.get("MORNING_HOUR", "8"))
MORNING_MIN  = int(os.environ.get("MORNING_MIN", "0"))

# חיבור לגוגל דרייב
def get_drive_service():
    info = json.loads(os.environ["GOOGLE_CREDS_JSON"])
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=['https://www.googleapis.com/auth/drive'])
    return build('drive', 'v3', credentials=creds)

# פקודת התחלה
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ALLOWED_ID: return
    await update.message.reply_text("הבוט פעיל! שלח תמונה והיא תעלה לדרייב.")

# טיפול בתמונה
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != ALLOWED_ID: return
    
    msg = await update.message.reply_text("מעלה לדרייב... ⏳")
    photo_file = await update.message.photo[-1].get_file()
    buf = io.BytesIO()
    await photo_file.download_to_memory(buf)
    buf.seek(0)

    filename = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    
    try:
        service = get_drive_service()
        file_metadata = {'name': filename, 'parents': [FOLDER_ID]}
        media = MediaIoBaseUpload(buf, mimetype='image/jpeg', resumable=True)
        service.files().create(body=file_metadata, media_body=media).execute()
        await msg.edit_text(f"✅ עלה בהצלחה!\nשם: {filename}")
    except Exception as e:
        logger.error(e)
        await msg.edit_text("❌ תקלה בהעלאה.")

# הודעת בוקר
async def morning_msg(context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=ALLOWED_ID, text="בוקר טוב! הבוט מוכן לעבודה. ☀️")

def main():
    # יצירת הבוט
    app = Application.builder().token(BOT_TOKEN).build()
    
    # הוספת פקודות
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    
    # תזמון הודעת בוקר
    if app.job_queue:
        app.job_queue.run_daily(morning_msg, time=dtime(MORNING_HOUR, MORNING_MIN))
    
    logger.info("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
