import os
from datetime import datetime, timedelta
from dateparser.search import search_dates 
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# جلب التوكن من المتغيرات البيئية
TOKEN = os.environ.get("TELEGRAM_TOKEN")

def get_oman_time():
    """دالة مساعدة لجلب الوقت الحالي بتوقيت عُمان"""
    return datetime.utcnow() + timedelta(hours=4)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دالة الترحيب"""
    welcome_message = (
        "أهلاً بك! أنا مساعدك الشخصي 🤖\n"
        "يمكنني تذكيرك بمواعيدك ومهامك.\n\n"
        "أوامر يمكنك استخدامها:\n"
        "1️⃣ للجدولة قل: 'ذكرني غدا الساعة 9 صباحا بكذا'\n"
        "2️⃣ لمعرفة الوقت قل: 'كم الوقت'\n"
        "3️⃣ لمعرفة تذكيراتك قل: 'تذكيراتي'\n"
    )
    await update.message.reply_text(welcome_message)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة النصوص وتوزيع المهام"""
    text = update.message.text
    
    # 1. أمر الاستعلام عن الوقت
    if "الوقت" in text and "ذكرني" not in text:
        now_str = get_oman_time().strftime('%Y-%m-%d %I:%M %p')
        await update.message.reply_text(f"🕰️ الوقت الحالي لدي هو:\n{now_str}")
        return

    # 2. أمر الاستعلام عن قائمة التذكيرات
    if "تذكيراتي" in text or "التذكيرات" in text:
        jobs = context.job_queue.jobs()
        if not jobs:
            await update.message.reply_text("📭 ليس لديك أي تذكيرات مجدولة حالياً.")
            return
        
        msg = "📋 قائمة التذكيرات المجدولة:\n\n"
        for i, job in enumerate(jobs, 1):
            msg += f"{i}. {job.data}\n"
            
        msg += "\n⚠️ تذكر: في الاستضافة المجانية قد تُلغى التذكيرات إذا دخل الخادم في وضع النوم."
        await update.message.reply_text(msg)
        return

    # 3. أمر إضافة تذكير جديد
    if "ذكرني" in text:
        try:
            oman_now = get_oman_time()
            settings = {
                'TIMEZONE': 'Asia/Muscat',
                'RELATIVE_BASE': oman_now
            }
            
            dates = search_dates(text, languages=['ar'], settings=settings)
            
            if dates:
                date_str, dt = dates[0]
                dt = dt.replace(tzinfo=None)
                
                delay = (dt - oman_now).total_seconds()
                
                if delay > 0:
                    # أضفنا نص الرسالة ووقت التذكير في بيانات الوظيفة (job.data) لتظهر في القائمة
                    formatted_time = dt.strftime('%Y-%m-%d %I:%M %p')
                    job_info = f"تذكير: '{text}' (⏰ {formatted_time})"
                    
                    context.job_queue.run_once(
                        send_reminder,
                        delay,
                        chat_id=update.effective_chat.id,
                        data=job_info # حفظ تفاصيل التذكير هنا
                    )
                    
                    await update.message.reply_text(f"✅ تمت الجدولة بنجاح!\nسأقوم بتذكيرك في:\n⏰ {formatted_time}")
                else:
                    await update.message.reply_text("عذراً، هذا الوقت يبدو أنه في الماضي! تأكد من كتابة الوقت في المستقبل.")
            else:
                await update.message.reply_text("لم أستطع تحديد الوقت بدقة. حاول استخدام صيغ أوضح مثل: 'بعد دقيقتين'.")
                
        except Exception as e:
            error_name = type(e).__name__
            error_details = str(e)
            await update.message.reply_text(f"⚠️ اكتشفت خطأ برمجياً:\n{error_name}: {error_details}")
    else:
        await update.message.reply_text("أنا جاهز! يمكنك قول 'الوقت'، 'تذكيراتي'، أو 'ذكرني بـ...'")

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    """إرسال الإشعار للمستخدم"""
    job = context.job
    # job.data تحتوي الآن على النص والوقت المنسق
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
