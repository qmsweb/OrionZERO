import os
import dateparser
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# جلب التوكن من المتغيرات البيئية
TOKEN = os.environ.get("TELEGRAM_TOKEN")

# تحديد المنطقة الزمنية (توقيت سلطنة عُمان GMT+4)
# هذا يضمن دقة التذكير بغض النظر عن توقيت سيرفرات Render
LOCAL_TIMEZONE = "Asia/Muscat"
tz_info = ZoneInfo(LOCAL_TIMEZONE)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """دالة الترحيب"""
    welcome_message = (
        "أهلاً بك! أنا مساعدك الشخصي 🤖\n"
        "يمكنني تذكيرك بمواعيدك ومهامك.\n\n"
        "فقط أرسل لي جملة تحتوي على كلمة 'ذكرني'، مثال:\n"
        "👉 'ذكرني غدا الساعة 9 صباحا بالذهاب للجامعة.'"
    )
    await update.message.reply_text(welcome_message)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة النصوص واستخراج الوقت"""
    text = update.message.text
    
    if "ذكرني" in text:
        try:
            # إجبار المكتبة على استخدام منطقتك الزمنية
            settings = {'TIMEZONE': LOCAL_TIMEZONE, 'RETURN_AS_TIMEZONE_AWARE': True}
            dates = dateparser.search.search_dates(text, languages=['ar'], settings=settings)
            
            if dates:
                date_str, dt = dates[0]
                
                # جلب الوقت الحالي حسب منطقتك الزمنية
                now = datetime.now(tz_info)
                
                # حساب الثواني المتبقية
                delay = (dt - now).total_seconds()
                
                if delay > 0:
                    context.job_queue.run_once(
                        send_reminder,
                        delay,
                        chat_id=update.effective_chat.id,
                        data=text
                    )
                    
                    # تنسيق الوقت لعرضه لك بصيغة جميلة (صباحاً/مساءً)
                    formatted_time = dt.strftime('%Y-%m-%d %I:%M %p')
                    await update.message.reply_text(f"✅ تمت الجدولة بنجاح!\nسأقوم بتذكيرك في:\n⏰ {formatted_time}")
                else:
                    await update.message.reply_text("عذراً، هذا الوقت يبدو أنه في الماضي! تأكد من كتابة الوقت بشكل صحيح (مثال: غدا الساعة 5 مساءً).")
            else:
                await update.message.reply_text("لم أستطع تحديد الوقت بدقة. حاول استخدام صيغ أوضح مثل: 'بعد ساعتين' أو 'غدا الساعة 5 مساءً'.")
                
        except Exception as e:
            # في حال حدث أي خطأ برمجي يتم إخبارك بدلاً من التجاهل الصامت
            print(f"Error: {e}")
            await update.message.reply_text("حدث خطأ غير متوقع أثناء فهم الوقت، يرجى المحاولة بصيغة أخرى.")
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
        print("Bot is running locally...")
        application.run_polling()

if __name__ == '__main__':
    main()
