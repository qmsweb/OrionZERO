import os
import re
from datetime import datetime, timedelta
from dateparser.search import search_dates 
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from supabase import create_client, Client
import google.generativeai as genai

# جلب المتغيرات البيئية
TOKEN = os.environ.get("TELEGRAM_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    system_instruction = (
        "أنت مساعد شخصي ذكي ومفيد عبر تيليجرام. "
        "1. يجب أن تكون إجاباتك قصيرة جداً، مختصرة، ومباشرة في صلب الموضوع دون مقدمات طويلة. "
        "2. عند الحاجة لتمييز نص أو جعله عريضاً، استخدم علامة نجمة واحدة فقط مثل *هذا*، ولا تستخدم نجمتين متتاليتين أبداً."
    )
    model = genai.GenerativeModel('gemini-2.5-flash', system_instruction=system_instruction) 
else:
    model = None

def get_oman_time():
    return datetime.utcnow() + timedelta(hours=4)

# ذاكرة مؤقتة لمعرفة من ضغط على زر "إضافة تذكير"
awaiting_reminder_users = set()

async def load_pending_reminders(application: Application):
    print("جاري التحقق من التذكيرات المعلقة في قاعدة البيانات...")
    now_oman = get_oman_time().isoformat()
    try:
        response = supabase.table("reminders").select("*").gte("remind_at", now_oman).execute()
        reminders = response.data
        for row in reminders:
            remind_at = datetime.fromisoformat(row['remind_at'])
            delay = (remind_at - get_oman_time()).total_seconds()
            if delay > 0:
                job_info = {"db_id": row['id'], "task": row['task']}
                application.job_queue.run_once(
                    send_reminder, delay, chat_id=row['chat_id'], name=str(row['id']), data=job_info
                )
        print(f"تمت إعادة جدولة {len(reminders)} تذكير بنجاح.")
    except Exception as e:
        print(f"خطأ أثناء جلب التذكيرات: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = (
        "أهلاً بك! أنا مساعدك الشخصي 🤖\n\n"
        "اختر من القائمة بالأسفل، أو تحدث معي مباشرة!"
    )
    keyboard = [
        [KeyboardButton("➕ إضافة تذكير جديد")],
        [KeyboardButton("📋 تذكيراتي"), KeyboardButton("⏱️ الوقت الحالي")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    text_lower = text.lower()
    chat_id = update.effective_chat.id
    
    # --- 1. التفاعل المباشر والصارم مع الأزرار ---
    if text == "➕ إضافة تذكير جديد":
        awaiting_reminder_users.add(chat_id) # حفظ حالة المستخدم
        await update.message.reply_text("أرسل لي التذكير والوقت الآن!\nمثال: 'المحاضرة بعد 10 دقائق' أو 'المحاضرة 10:40 صباحا'")
        return

    if text == "📋 تذكيراتي":
        awaiting_reminder_users.discard(chat_id) # إلغاء حالة التذكير إن وجدت
        now_oman = get_oman_time().isoformat()
        response = supabase.table("reminders").select("*").gte("remind_at", now_oman).eq("chat_id", chat_id).order("remind_at").execute()
        if not response.data:
            await update.message.reply_text("📭 ليس لديك أي تذكيرات مجدولة حالياً.")
            return
        msg = "📋 قائمة التذكيرات المجدولة:\n\n"
        for i, row in enumerate(response.data, 1):
            task_time_oman = datetime.fromisoformat(row['remind_at'])
            arabic_period = "صباحاً" if task_time_oman.hour < 12 else "مساءً"
            formatted_time = f"{task_time_oman.strftime('%I').lstrip('0')}:{task_time_oman.strftime('%M')} {arabic_period}"
            msg += f"{i}. {row['task']} (⏰ {task_time_oman.strftime('%Y-%m-%d')} - {formatted_time})\n"
        await update.message.reply_text(msg)
        return

    if text == "⏱️ الوقت الحالي":
        awaiting_reminder_users.discard(chat_id)
        now_oman = get_oman_time()
        arabic_period = "صباحاً" if now_oman.hour < 12 else "مساءً"
        await update.message.reply_text(f"⏱️ الوقت الحالي هو: {now_oman.strftime('%I').lstrip('0')}:{now_oman.strftime('%M')} {arabic_period}")
        return

    # --- 2. حذف تذكير ---
    if any(word in text_lower for word in ["احذف", "إلغاء", "حذف", "الغي"]) and "تذكير" in text_lower:
        match = re.search(r'\d+', text)
        if match:
            idx = int(match.group()) - 1
            now_oman = get_oman_time().isoformat()
            response = supabase.table("reminders").select("*").gte("remind_at", now_oman).eq("chat_id", chat_id).order("remind_at").execute()
            if 0 <= idx < len(response.data):
                target_id = response.data[idx]['id']
                supabase.table("reminders").delete().eq("id", target_id).execute()
                current_jobs = context.job_queue.get_jobs_by_name(str(target_id))
                for job in current_jobs:
                    job.schedule_removal()
                await update.message.reply_text(f"✅ تم حذف التذكير رقم {idx + 1} بنجاح.")
            else:
                await update.message.reply_text("❌ الرقم غير صحيح. قل 'تذكيراتي' لمعرفة الأرقام الصحيحة.")
        else:
             await update.message.reply_text("❌ يرجى تحديد رقم التذكير، مثال: 'احذف التذكير 1'")
        return

    # --- 3. معالجة وإنشاء التذكير (بذكاء فائق) ---
    # يتحقق إذا كان المستخدم ضغط على الزر سابقاً، أو كتب كلمة "ذكرني"
    is_reminder_intent = chat_id in awaiting_reminder_users or any(word in text_lower for word in ["ذكرني", "نبهني", "تذكير", "ذكر"])
    
    if is_reminder_intent:
        if chat_id in awaiting_reminder_users:
            awaiting_reminder_users.remove(chat_id) # مسح الحالة بعد استلام الطلب
            
        try:
            oman_now = get_oman_time()
            dt_oman = None
            date_str = ""
            
            # أ. الخوارزمية اليدوية الجديدة: اصطياد الأوقات النسبية (بعد X دقائق/ساعات/أيام)
            rel_match = re.search(r'بعد\s+(\d+)\s*(دقيق|ساع|يوم|ايام)', text_lower)
            if rel_match:
                num = int(rel_match.group(1))
                unit = rel_match.group(2)
                if 'دقيق' in unit:
                    dt_oman = oman_now + timedelta(minutes=num)
                elif 'ساع' in unit:
                    dt_oman = oman_now + timedelta(hours=num)
                elif 'يوم' in unit or 'ايام' in unit:
                    dt_oman = oman_now + timedelta(days=num)
                date_str = rel_match.group(0) # استخراج النص المحتوي على الوقت لإزالته لاحقاً
            
            # ب. استخدام المكتبة فقط للأوقات الثابتة (مثل 10:40 صباحا)
            else:
                settings = {'TIMEZONE': 'Asia/Muscat', 'RELATIVE_BASE': oman_now, 'PREFER_DATES_FROM': 'future', 'RETURN_AS_TIMEZONE_AWARE': False}
                dates = search_dates(text_lower, languages=['ar', 'en'], settings=settings)
                
                if dates:
                    date_str, dt_oman = dates[0]
                    has_am_pm = bool(re.search(r'\b(ص|م|صباح|مساء|am|pm)\b', text_lower))
                    is_relative = any(word in date_str.lower() for word in ['in', 'minute', 'hour', 'day', 'tomorrow'])
                    
                    if not is_relative and not has_am_pm and 1 <= dt_oman.hour <= 11:
                        dt_am = dt_oman.replace(hour=dt_oman.hour)
                        dt_pm = dt_oman.replace(hour=dt_oman.hour + 12)
                        
                        if dt_am < oman_now and dt_pm > oman_now:
                            dt_oman = dt_pm
                        elif dt_am > oman_now and dt_pm > oman_now:
                            dt_oman = dt_am if (dt_am - oman_now) < (dt_pm - oman_now) else dt_pm
                        elif dt_am < oman_now and dt_pm < oman_now:
                            dt_oman = dt_am + timedelta(days=1)
                    
                    if dt_oman < oman_now:
                        dt_oman += timedelta(days=1)

            # تنفيذ الحفظ في حالة تم تحديد الوقت
            if dt_oman:
                delay = (dt_oman - get_oman_time()).total_seconds()
                if delay > 0:
                    # تنظيف النص لاستخراج عنوان المهمة (مثل: المحاضرة)
                    task_text = text.replace(date_str, "").replace("ذكرني", "").replace("نبهني", "").replace("بـ", "").replace("أن", "").strip()
                    # إزالة حروف الجر المتصلة بالوقت إن وجدت
                    task_text = re.sub(r'^(في|على|ب)\s+', '', task_text).strip()
                    
                    if not task_text or task_text in ["ب", "في", "على"]:
                        task_text = "تذكير بموعد"

                    insert_response = supabase.table("reminders").insert({
                        "chat_id": chat_id,
                        "task": task_text,
                        "remind_at": dt_oman.isoformat()
                    }).execute()
                    
                    db_id = insert_response.data[0]['id']
                    
                    context.job_queue.run_once(
                        send_reminder, delay, chat_id=chat_id, name=str(db_id), data={"db_id": db_id, "task": task_text}
                    )
                    
                    arabic_period = "صباحاً" if dt_oman.hour < 12 else "مساءً"
                    formatted_time = f"{dt_oman.strftime('%I').lstrip('0')}:{dt_oman.strftime('%M')} {arabic_period}"
                    
                    await update.message.reply_text(f"✅ تمت الجدولة بنجاح!\nسأنبهك بـ: {task_text}\n⏰ الموعد: {formatted_time} ({dt_oman.strftime('%Y-%m-%d')})")
                    return
                else:
                    await update.message.reply_text("عذراً، لم أستطع تحديد وقت في المستقبل.")
                    return
            else:
                await update.message.reply_text("لم أستطع تحديد الوقت بدقة. جرب: 'المحاضرة بعد 10 دقائق'")
                return
                
        except Exception as e:
            await update.message.reply_text(f"حدث خطأ، يرجى المحاولة بصيغة أخرى.\n{str(e)}")
        return

    # --- 4. الرد بواسطة جيميناي (لا يعمل إلا إذا لم يكن النص زراً أو تذكيراً) ---
    if model:
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action='typing')
            response = await model.generate_content_async(update.message.text)
            final_text = response.text.replace("**", "*")
            await update.message.reply_text(final_text)
        except Exception as e:
            print(f"Gemini API Error: {e}")
            await update.message.reply_text("عذراً، حدث خطأ أثناء التواصل مع الذكاء الاصطناعي 🤕.")
    else:
        await update.message.reply_text("لم أفهم طلبك تماماً 🤔. يمكنك قول 'ذكرني بـ...'")

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    task = job_data['task']
    db_id = job_data['db_id']
    await context.bot.send_message(chat_id=context.job.chat_id, text=f"🔔 حان الموعد!\n\n{task}")
    try:
        supabase.table("reminders").delete().eq("id", db_id).execute()
    except Exception as e:
        print(f"خطأ في حذف التذكير من DB: {e}")

def main():
    application = Application.builder().token(TOKEN).post_init(load_pending_reminders).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    PORT = int(os.environ.get('PORT', '5000'))
    RENDER_URL = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")

    if RENDER_URL:
        application.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f"https://{RENDER_URL}/{TOKEN}")
    else:
        print("Bot is running...")
        application.run_polling()

if __name__ == '__main__':
    main()
