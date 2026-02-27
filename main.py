import os
from datetime import datetime, timedelta
# استدعاء أداة البحث بشكل مباشر وصحيح لحل المشكلة
from dateparser.search import search_dates 
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# جلب التوكن من المتغيرات البيئية
TOKEN = os.environ.get("TELEGRAM_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دالة الترحيب"""
    welcome_message = (
        "أهلاً بك! أنا مساعدك الشخصي 🤖\n"
        "يمكنني تذكيرك بمواعيدك ومهامك.\n\n"
        "فقط أرسل لي جملة تحتوي على كلمة 'ذكرني'، مثال:\n"
        "👉 'ذكرني غدا الساعة 9 صباحا بموعد الطبيب'"
    )
    await update.message.reply_text(welcome_message)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة النصوص واستخراج الوقت"""
    text = update.message.text
    
    if "ذكرني" in text:
        try:
            # حساب توقيت عُمان يدوياً (توقيت جرينتش + 4 ساعات)
            oman_now = datetime.utcnow() + timedelta(hours=4)
            
            settings = {
                'TIMEZONE': 'Asia/Muscat',
                'RELATIVE_BASE': oman_now
            }
            
            # استخدام أداة البحث مباشرة
            dates = search_dates(text, languages=['ar'], settings=settings)
            
            if dates:
                date_str, dt = dates[0]
                dt = dt.replace(tzinfo=None)
                
                delay = (dt - oman_now).total_seconds()
                
                if delay > 0:
                    context.job_queue.run_once(
                        send_reminder,
                        delay,
                        chat_id=update.effective_chat.id,
                        data=text
                    )
                    
                    formatted_time = dt.strftime('%Y-%m-%d %I:%M %p')
                    await update.message.reply_text(f"✅ تمت الجدولة بنجاح!\nسأقوم بتذكيرك في:\n⏰ {formatted_time}")
                else:
                    await update.message.reply_text("عذراً، هذا الوقت يبدو أنه في الماضي! تأكد من كتابة الوقت في المستقبل.")
            else:
                await update.message.reply_text("لم أستطع تحديد الوقت بدقة. حاول استخدام صيغ أوضح مثل: 'بعد 5 دقائق' أو 'غدا 9 مساء'.")
                
        except Exception as e:
            error_name = type(e).__name__
            error_details = str(e)
            await update.message.reply_text(f"⚠️ اكتشفت خطأ برمجياً، أرجو إرسال هذه الرسالة للمطور:\n\n{error_name}: {error_details}")
    else:
        await update.message.reply_text("أنا جاهز! ابدأ رسالتك بكلمة 'ذكرني' لضبط التذكير.")

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    """إرسال الإشعار للمستخدم"""
    job = context.job
    await context.bot.send_message(
        chat_id=job.chat_id, 
        text=f"🔔 حان وقت التذكير الذي طلبته:\n\n{job.data}"
    )

def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    PORT = int(os.environ.get('PORT', '5000'))
    RENDER_URL = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")

    if RENDER_URL:
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"https://{RENDER_URL}/{TOKEN}"
        )
    else:
        application.run_polling()

if __name__ == '__main__':
    main()
