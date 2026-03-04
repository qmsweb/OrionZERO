import os
import json
import re
from datetime import datetime, timedelta, timezone, time
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from supabase import create_client, Client
import google.generativeai as genai

# جلب المتغيرات البيئية
TOKEN = os.environ.get("TELEGRAM_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# تعريف المنطقة الزمنية لسلطنة عُمان (GMT+4)
OMAN_TZ = timezone(timedelta(hours=4), name="Asia/Muscat")

def get_oman_time():
    return datetime.now(OMAN_TZ)

# إعداد نموذج الذكاء الاصطناعي كـ NLU
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    system_instruction = (
        "أنت محرك NLU (Natural Language Understanding) صامت وعالي الدقة. "
        "مهمتك قراءة نص المستخدم بناءً على 'الوقت الحالي' (توقيت عُمان GMT+4)، "
        "وتحديد ما إذا كان الطلب 'تذكيراً لمرة واحدة' أو 'روتين متكرر'. "
        "يجب أن ترد بصيغة JSON فقط، بدون أي نصوص إضافية.\n\n"
        "إذا كان تذكيراً لمرة واحدة استخدم هذا الهيكل:\n"
        "{\n"
        '  "type": "reminder",\n'
        '  "task": "وصف المهمة المستخرج",\n'
        '  "datetime": "YYYY-MM-DDTHH:MM:SS+04:00"\n'
        "}\n\n"
        "إذا كان روتيناً متكرراً (مثل: كل يوم، كل يوم أحد وثلاثاء الساعة 5، يومياً)، استخدم هذا الهيكل:\n"
        "{\n"
        '  "type": "routine",\n'
        '  "task": "وصف المهمة",\n'
        '  "time": "HH:MM",\n' # بصيغة 24 ساعة
        '  "days": [0, 1, 2, 3, 4, 5, 6]\n' # الأيام المطلوبة: 0=الإثنين، 1=الثلاثاء... 6=الأحد. إذا كان كل يوم ضع جميع الأرقام من 0 إلى 6.
        "}\n\n"
        "إذا لم يكن النص طلباً لجدولة، أرجع {}"
    )
    model = genai.GenerativeModel('gemini-2.5-flash', system_instruction=system_instruction) 
else:
    model = None

# ----------------- وظائف التحميل عند بدء التشغيل -----------------

async def load_pending_reminders(application: Application):
    print("جاري التحقق من التذكيرات والروتينات المعلقة...")
    now_oman = get_oman_time().isoformat()
    
    # 1. تحميل التذكيرات لمرة واحدة
    try:
        response = supabase.table("reminders").select("*").gte("remind_at", now_oman).execute()
        for row in response.data:
            remind_at = datetime.fromisoformat(row['remind_at'])
            delay = (remind_at - get_oman_time()).total_seconds()
            if delay > 0:
                application.job_queue.run_once(
                    send_reminder, delay, chat_id=row['chat_id'], name=f"rem_{row['id']}", data={"db_id": row['id'], "task": row['task']}
                )
        print(f"تمت إعادة جدولة {len(response.data)} تذكير بنجاح.")
    except Exception as e:
        print(f"خطأ أثناء جلب التذكيرات: {e}")

    # 2. تحميل الروتينات المتكررة
    try:
        response = supabase.table("routines").select("*").execute()
        for row in response.data:
            schedule_routine_job(application, row['chat_id'], row['id'], row['task'], row['time'], row['days'])
        print(f"تمت إعادة جدولة {len(response.data)} روتين بنجاح.")
    except Exception as e:
        print(f"خطأ أثناء جلب الروتينات: {e}")


def schedule_routine_job(application: Application, chat_id, db_id, task, time_str, days):
    """دالة مساعدة لجدولة الروتين في JobQueue"""
    h, m = map(int, time_str.split(':'))
    # تعيين الوقت مع المنطقة الزمنية لعُمان
    t = time(hour=h, minute=m, tzinfo=OMAN_TZ)
    # استخدام run_daily لجدولة الروتين حسب الأيام المطلوبة
    application.job_queue.run_daily(
        send_routine_reminder, 
        time=t, 
        days=tuple(days), 
        chat_id=chat_id, 
        name=f"routine_{db_id}", 
        data={"db_id": db_id, "task": task}
    )

# ----------------- التعامل مع الرسائل والأوامر -----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = (
        "أهلاً بك! أنا مساعدك الشخصي 🤖\n\n"
        "يمكنني تذكيرك بمواعيدك لمرة واحدة، أو إنشاء **روتين متكرر**.\n"
        "مثال تذكير: 'ذكرني بكرة الساعة 5 العصر أكلم أحمد'\n"
        "مثال روتين: 'ذكرني كل يوم أحد وثلاثاء الساعة 9 الصباح أشرب ماي'"
    )
    keyboard = [
        [KeyboardButton("📋 تذكيراتي"), KeyboardButton("🔁 روتيناتي")],
        [KeyboardButton("⏱️ الوقت الحالي")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    
    # 1. التعامل مع الأوامر الثابتة
    if text == "📋 تذكيراتي":
        now_oman = get_oman_time().isoformat()
        response = supabase.table("reminders").select("*").gte("remind_at", now_oman).eq("chat_id", chat_id).order("remind_at").execute()
        if not response.data:
            await update.message.reply_text("📭 ليس لديك أي تذكيرات مجدولة لمرة واحدة.")
            return
        msg = "📋 قائمة التذكيرات:\n\n"
        for i, row in enumerate(response.data, 1):
            task_time = datetime.fromisoformat(row['remind_at'])
            arabic_period = "صباحاً" if task_time.hour < 12 else "مساءً"
            formatted_time = f"{task_time.strftime('%I').lstrip('0')}:{task_time.strftime('%M')} {arabic_period}"
            msg += f"{i}. {row['task']} (⏰ {task_time.strftime('%Y-%m-%d')} - {formatted_time})\n"
        await update.message.reply_text(msg)
        return

    if text == "🔁 روتيناتي":
        response = supabase.table("routines").select("*").eq("chat_id", chat_id).execute()
        if not response.data:
            await update.message.reply_text("📭 ليس لديك أي روتينات مجدولة حالياً.")
            return
        
        await update.message.reply_text("🔁 قائمة الروتينات الخاصة بك:")
        days_names = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
        
        for row in response.data:
            h, m = map(int, row['time'].split(':'))
            arabic_period = "صباحاً" if h < 12 else "مساءً"
            formatted_time = f"{h if h<=12 else h-12}:{m:02d} {arabic_period}"
            
            routine_days_ar = [days_names[d] for d in row['days']]
            days_str = "كل يوم" if len(row['days']) == 7 else "، ".join(routine_days_ar)
            
            msg = f"📌 المهمة: {row['task']}\n⏰ الوقت: {formatted_time}\n📅 الأيام: {days_str}"
            
            # زر الحذف المدمج
            keyboard = [[InlineKeyboardButton("❌ حذف الروتين", callback_data=f"del_routine_{row['id']}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(msg, reply_markup=reply_markup)
        return

    if text == "⏱️ الوقت الحالي":
        now_oman = get_oman_time()
        arabic_period = "صباحاً" if now_oman.hour < 12 else "مساءً"
        await update.message.reply_text(f"⏱️ الوقت المحلي: {now_oman.strftime('%I').lstrip('0')}:{now_oman.strftime('%M')} {arabic_period}")
        return

    # 2. إرسال النص للذكاء الاصطناعي للفهم
    if not model:
        await update.message.reply_text("❌ واجهة الذكاء الاصطناعي غير متصلة.")
        return

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action='typing')
        current_time_iso = get_oman_time().isoformat()
        prompt = f"الوقت الحالي للمستخدم هو: {current_time_iso}\nنص المستخدم: {text}"
        
        response = await model.generate_content_async(prompt)
        ai_output = response.text.strip()

        if ai_output.startswith("
http://googleusercontent.com/immersive_entry_chip/0
http://googleusercontent.com/immersive_entry_chip/1

