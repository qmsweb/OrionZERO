import os
import dateparser
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# جلب التوكن من المتغيرات البيئية (للحماية عند الرفع على الخوادم)
TOKEN = os.environ.get("TELEGRAM_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دالة الترحيب عند بدء المحادثة"""
    welcome_message = (
        "أهلاً بك! أنا مساعدك الشخصي 🤖\n"
        "يمكنني تذكيرك بمواعيدك ومهامك.\n\n"
        "فقط أرسل لي جملة تحتوي على كلمة 'ذكرني'، مثال:\n"
        "👉 'ذكرني غدا الساعة 9 صباحا بموعد الطبيب'"
    )
    await update.message.reply_text(welcome_message)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دالة معالجة الرسائل واستخراج وقت التذكير"""
    text = update.message.text
    
    if "ذكرني" in text:
        # استخدام dateparser لاستخراج التاريخ والوقت من النص العربي
        # نبحث داخل النص عن أي صيغة تدل على وقت
        dates = dateparser.search.search_dates(text, languages=['ar'])
        
        if dates:
            # نأخذ أول تاريخ تم استخراجه
            date_str, dt = dates[0]
            now = datetime.now()
            
            # حساب الثواني المتبقية حتى موعد التذكير
            delay = (dt - now).total_seconds()
            
            if delay > 0:
                # جدولة التذكير
                context.job_queue.run_once(
                    send_reminder,
                    delay,
                    chat_id=update.effective_chat.id,
                    data=text # نرسل نص الرسالة الأصلي ليعيد إرساله
                )
                
                # رسالة تأكيد للمستخدم
                formatted_time = dt.strftime('%Y-%m-%d %H:%M')
                await update.message.reply_text(f"تمت الجدولة بنجاح! سأقوم بتذكيرك في:\n⏰ {formatted_time}")
            else:
                await update.message.reply_text("عذراً، هذا الوقت قد مضى بالفعل! جرب وقتاً في المستقبل.")
        else:
            await update.message.reply_text("لم أستطع تحديد الوقت بدقة. حاول كتابته بصيغة أوضح (مثل: بعد ساعتين، غدا مساءً).")
    else:
        await update.message.reply_text("أنا جاهز! ابدأ رسالتك بكلمة 'ذكرني' لضبط التذكير.")

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    """دالة إرسال الإشعار في الوقت المحدد"""
    job = context.job
    await context.bot.send_message(
        chat_id=job.chat_id, 
        text=f"🔔 حان وقت التذكير الذي طلبته:\n\n{job.data}"
    )

def main():
    # إعداد التطبيق
    application = Application.builder().token(TOKEN).build()

    # إضافة الأوامر ومعالجات الرسائل
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # إعدادات الرفع على Render (استخدام Webhook)
    PORT = int(os.environ.get('PORT', '5000'))
    RENDER_URL = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")

    if RENDER_URL:
        # إذا كان الكود يعمل على Render
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN, # إضافة التوكن كمسار أمان
            webhook_url=f"https://{RENDER_URL}/{TOKEN}"
        )
    else:
        # إذا كان الكود يعمل على حاسوبك الشخصي (للتجربة)
        print("Bot is running locally...")
        application.run_polling()

if __name__ == '__main__':
    main()
