import os
from datetime import datetime, timedelta
from dateparser.search import search_dates 
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from supabase import create_client, Client

# جلب المتغيرات البيئية
TOKEN = os.environ.get("TELEGRAM_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# تهيئة الاتصال بقاعدة البيانات
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_utc_now():
    """الاعتماد على UTC لتخزين التواريخ في قاعدة البيانات بشكل موحد"""
    return datetime.utcnow()

def get_oman_time():
    """للعرض فقط بتوقيت عُمان"""
    return get_utc_now() + timedelta(hours=4)

async def load_pending_reminders(application: Application):
    """دالة تعمل عند تشغيل البوت لجلب التذكيرات التي لم تُنفذ بسبب نوم السيرفر"""
    print("جاري التحقق من التذكيرات المعلقة في قاعدة البيانات...")
    now_utc = get_utc_now().isoformat()
    
    try:
        # جلب التذكيرات التي وقتها في المستقبل
        response = supabase.table("reminders").select("*").gte("remind_at", now_utc).execute()
        reminders = response.data
        
        for row in reminders:
            remind_at = datetime.fromisoformat(row['remind_at'])
            delay = (remind_at - get_utc_now()).total_seconds()
            
            if delay > 0:
                job_info = {"db_id": row['id'], "task": row['task']}
                application.job_queue.run_once(
                    send_reminder,
                    delay,
                    chat_id=row['chat_id'],
                    data=job_info
                )
        print(f"تمت إعادة جدولة {len(reminders)} تذكير بنجاح.")
    except Exception as e:
        print(f"خطأ أثناء جلب التذكيرات: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = (
        "أهلاً بك! أنا مساعدك الشخصي 🤖\n"
        "يمكنني تذكيرك بمواعيدك ومهامك (البيانات محفوظة بأمان ولن تضيع).\n\n"
        "أوامر يمكنك استخدامها:\n"
        "1️⃣ للجدولة: 'ذكرني بكرة 9 الصبح أرسل الإيميل'\n"
        "2️⃣ للتذكيرات: 'قائمة التذكيرات'\n"
    )
    await update.message.reply_text(welcome_message)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower().strip()
    chat_id = update.effective_chat.id
    
    if any(word in text for word in ["تذكيراتي", "تذكيرات", "مهام", "قائمة"]):
        now_utc = get_utc_now().isoformat()
        response = supabase.table("reminders").select("*").gte("remind_at", now_utc).eq("chat_id", chat_id).execute()
        
        if not response.data:
            await update.message.reply_text("📭 ليس لديك أي تذكيرات مجدولة حالياً.")
            return
            
        msg = "📋 قائمة التذكيرات المجدولة:\n\n"
        for i, row in enumerate(response.data, 1):
            task_time_oman = datetime.fromisoformat(row['remind_at']) + timedelta(hours=4)
            formatted_time = task_time_oman.strftime('%Y-%m-%d %I:%M %p')
            msg += f"{i}. {row['task']} (⏰ {formatted_time})\n"
            
        await update.message.reply_text(msg)
        return

    if any(word in text for word in ["ذكرني", "نبهني", "تذكير", "ذكر"]):
        try:
            oman_now = get_oman_time()
            settings = {'TIMEZONE': 'Asia/Muscat', 'RELATIVE_BASE': oman_now}
            
            dates = search_dates(text, languages=['ar', 'en'], settings=settings)
            
            if dates:
                date_str, dt_oman = dates[0]
                dt_oman = dt_oman.replace(tzinfo=None)
                
                # تحويل الوقت لـ UTC قبل الحفظ في قاعدة البيانات
                dt_utc = dt_oman - timedelta(hours=4)
                delay = (dt_utc - get_utc_now()).total_seconds()
                
                if delay > 0:
                    task_text = text.replace(date_str, "").replace("ذكرني", "").replace("نبهني", "").replace("بـ", "").replace("أن", "").strip()
                    if not task_text or task_text == "ب":
                        task_text = "تذكير عام"

                    # حفظ في قاعدة البيانات
                    insert_response = supabase.table("reminders").insert({
                        "chat_id": chat_id,
                        "task": task_text,
                        "remind_at": dt_utc.isoformat()
                    }).execute()
                    
                    db_id = insert_response.data[0]['id']
                    
                    # الجدولة في الذاكرة
                    context.job_queue.run_once(
                        send_reminder,
                        delay,
                        chat_id=chat_id,
                        data={"db_id": db_id, "task": task_text}
                    )
                    
                    await update.message.reply_text(f"✅ تمت الجدولة بنجاح!\nسأنبهك بـ: {task_text}")
                else:
                    await update.message.reply_text("عذراً، هذا الوقت يبدو أنه في الماضي!")
            else:
                await update.message.reply_text("لم أستطع تحديد الوقت بدقة.")
                
        except Exception as e:
            await update.message.reply_text(f"حدث خطأ، يرجى المحاولة بصيغة أخرى.\n{str(e)}")
    else:
        await update.message.reply_text("لم أفهم طلبك تماماً 🤔. يمكنك قول 'ذكرني بـ...'")

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    """إرسال الإشعار للمستخدم وحذفه من قاعدة البيانات"""
    job_data = context.job.data
    task = job_data['task']
    db_id = job_data['db_id']
    
    # رسالة التذكير المختصرة كما طلبت
    await context.bot.send_message(
        chat_id=context.job.chat_id, 
        text=f"🔔 حان الموعد!\n\n{task}"
    )
    
    # حذف التذكير من قاعدة البيانات بعد تنفيذه حتى لا يتراكم
    try:
        supabase.table("reminders").delete().eq("id", db_id).execute()
    except Exception as e:
        print(f"خطأ في حذف التذكير من DB: {e}")

def main():
    # استخدام post_init لتشغيل دالة استرجاع التذكيرات فور بدء البوت
    application = Application.builder().token(TOKEN).post_init(load_pending_reminders).build()

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
        print("Bot is running...")
        application.run_polling()

if __name__ == '__main__':
    main()
