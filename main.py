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

        if ai_output.startswith("```json"): ai_output = ai_output[7:-3].strip()
        elif ai_output.startswith("```"): ai_output = ai_output[3:-3].strip()

        parsed_data = json.loads(ai_output)

        if not parsed_data:
            await update.message.reply_text("عذراً، لم أتمكن من استخراج موعد أو روتين واضح. جرب صيغة أوضح.")
            return

        task_text = parsed_data.get("task", "تذكير")
        req_type = parsed_data.get("type", "reminder")

        if req_type == "routine" and "time" in parsed_data and "days" in parsed_data:
            time_str = parsed_data["time"]
            days = parsed_data["days"]
            
            # حفظ الروتين في قاعدة البيانات
            insert_res = supabase.table("routines").insert({
                "chat_id": chat_id,
                "task": task_text,
                "time": time_str,
                "days": days
            }).execute()
            
            db_id = insert_res.data[0]['id']
            
            # جدولة الروتين
            schedule_routine_job(context.application, chat_id, db_id, task_text, time_str, days)
            
            await update.message.reply_text(f"✅ تم تفعيل الروتين بنجاح!\nسأذكرك بـ: *{task_text}*")

        elif req_type == "reminder" and "datetime" in parsed_data:
            dt_oman = datetime.fromisoformat(parsed_data["datetime"])
            delay = (dt_oman - get_oman_time()).total_seconds()
            
            if delay > 0:
                insert_res = supabase.table("reminders").insert({
                    "chat_id": chat_id, "task": task_text, "remind_at": dt_oman.isoformat()
                }).execute()
                
                db_id = insert_res.data[0]['id']
                context.job_queue.run_once(send_reminder, delay, chat_id=chat_id, name=f"rem_{db_id}", data={"db_id": db_id, "task": task_text})
                
                arabic_period = "صباحاً" if dt_oman.hour < 12 else "مساءً"
                formatted_time = f"{dt_oman.strftime('%I').lstrip('0')}:{dt_oman.strftime('%M')} {arabic_period}"
                await update.message.reply_text(f"✅ تمت الجدولة بنجاح!\nسأنبهك بـ: *{task_text}*\n⏰ الموعد: {formatted_time}")
            else:
                await update.message.reply_text("عذراً، الوقت المطلوب يقع في الماضي.")

    except json.JSONDecodeError:
        await update.message.reply_text("عذراً، حدث خطأ في معالجة طلبك داخلياً.")
        except Exception as e:
        error_msg = str(e)
        print(f"Error: {error_msg}")
        await update.message.reply_text(f"عذراً، حدث خطأ غير متوقع. \nتفاصيل الخطأ:\n`{error_msg}`")



# ----------------- التعامل مع الأزرار (حذف الروتين) -----------------

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data.startswith("del_routine_"):
        routine_id = data.split("del_routine_")[1]
        
        # حذفه من قاعدة البيانات
        supabase.table("routines").delete().eq("id", routine_id).execute()
        
        # إيقاف المهمة المجدولة في النظام
        current_jobs = context.job_queue.get_jobs_by_name(f"routine_{routine_id}")
        for job in current_jobs:
            job.schedule_removal()
            
        await query.edit_message_text("✅ تم حذف هذا الروتين بنجاح.")


# ----------------- دوال إرسال التنبيهات -----------------

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    """إرسال التذكير لمرة واحدة وحذفه من القاعدة"""
    job_data = context.job.data
    await context.bot.send_message(chat_id=context.job.chat_id, text=f"🔔 حان الموعد!\n\n*{job_data['task']}*")
    try:
        supabase.table("reminders").delete().eq("id", job_data['db_id']).execute()
    except Exception as e:
        pass

async def send_routine_reminder(context: ContextTypes.DEFAULT_TYPE):
    """إرسال التذكير الروتيني (لا يحذف من القاعدة لأنه متكرر)"""
    job_data = context.job.data
    await context.bot.send_message(chat_id=context.job.chat_id, text=f"🔁 تذكير روتيني:\n\n*{job_data['task']}*")


# ----------------- التشغيل -----------------

def main():
    application = Application.builder().token(TOKEN).post_init(load_pending_reminders).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(CallbackQueryHandler(handle_callback)) # معالج الأزرار

    PORT = int(os.environ.get('PORT', '5000'))
    RENDER_URL = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")

    if RENDER_URL:
        application.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f"https://{RENDER_URL}/{TOKEN}")
    else:
        print("Bot is running...")
        application.run_polling()

if __name__ == '__main__':
    main()
