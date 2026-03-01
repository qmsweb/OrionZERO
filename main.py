import os
import re  # مكتبة مدمجة في بايثون (لا تحتاج تثبيت) لتحليل النصوص بذكاء
from datetime import datetime, timedelta
from dateparser.search import search_dates 
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from supabase import create_client, Client
import google.generativeai as genai

# جلب المتغيرات البيئية
TOKEN = os.environ.get("TELEGRAM_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# تهيئة الاتصال بقاعدة بيانات Supabase (لحفظ التذكيرات)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# تهيئة جوجل جيميناي مع System Prompt
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    
    # التعليمات الأساسية لجيميناي (System Prompt)
    system_instruction = (
        "أنت مساعد شخصي ذكي ومفيد عبر تيليجرام. "
        "1. يجب أن تكون إجاباتك قصيرة جداً، مختصرة، ومباشرة في صلب الموضوع دون مقدمات طويلة. "
        "2. عند الحاجة لتمييز نص أو جعله عريضاً، استخدم علامة نجمة واحدة فقط مثل *هذا*، ولا تستخدم نجمتين متتاليتين أبداً."
    )
    
    # استخدام موديل فلاش مع إرفاق التعليمات
    model = genai.GenerativeModel(
        'gemini-2.5-flash',
        system_instruction=system_instruction
    ) 
else:
    model = None
    print("تحذير: لم يتم العثور على مفتاح GEMINI_API_KEY")

def get_utc_now():
    return datetime.utcnow()

def get_oman_time():
    return get_utc_now() + timedelta(hours=4)

async def load_pending_reminders(application: Application):
    """جلب التذكيرات المعلقة من Supabase عند بدء التشغيل"""
    print("جاري التحقق من التذكيرات المعلقة في قاعدة البيانات...")
    now_utc = get_utc_now().isoformat()
    
    try:
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
        print(f"تمت إعادة جدولة {len(reminders)} تذكير بنجاح من Supabase.")
    except Exception as e:
        print(f"خطأ أثناء جلب التذكيرات: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = (
        "أهلاً بك! أنا مساعدك الشخصي 🤖\n"
        "يمكنني تذكيرك بمواعيدك (محفوظة في قاعدة البيانات)، والإجابة باختصار بذكاء.\n\n"
        "أوامر يمكنك استخدامها:\n"
        "1️⃣ للجدولة: 'ذكرني بكرة 9 الصبح أرسل الإيميل'\n"
        "2️⃣ للتذكيرات: 'قائمة التذكيرات'\n"
        "3️⃣ للوقت: 'كم الساعة' أو 'الوقت'\n"
        "💬 أو تحدث معي وسأجيبك باختصار!"
    )
    await update.message.reply_text(welcome_message)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.lower().strip()
    chat_id = update.effective_chat.id
    
    # 1. قائمة التذكيرات (معدلة لتعرض صباحاً/مساءً)
    if any(word in text for word in ["تذكيراتي", "تذكيرات", "مهام", "قائمة"]):
        now_utc = get_utc_now().isoformat()
        response = supabase.table("reminders").select("*").gte("remind_at", now_utc).eq("chat_id", chat_id).execute()
        
        if not response.data:
            await update.message.reply_text("📭 ليس لديك أي تذكيرات مجدولة حالياً.")
            return
            
        msg = "📋 قائمة التذكيرات المجدولة:\n\n"
        for i, row in enumerate(response.data, 1):
            task_time_oman = datetime.fromisoformat(row['remind_at']) + timedelta(hours=4)
            # تحويل الوقت للغة العربية
            arabic_period = "صباحاً" if task_time_oman.hour < 12 else "مساءً"
            formatted_time = f"{task_time_oman.strftime('%I').lstrip('0')}:{task_time_oman.strftime('%M')} {arabic_period}"
            formatted_date = task_time_oman.strftime('%Y-%m-%d')
            
            msg += f"{i}. {row['task']} (⏰ {formatted_date} - {formatted_time})\n"
            
        await update.message.reply_text(msg)
        return

    # 2. الوقت (معدلة لتعرض صباحاً/مساءً)
    if any(word in text for word in ["الوقت", "الساعة"]):
        now_oman = get_oman_time()
        arabic_period = "صباحاً" if now_oman.hour < 12 else "مساءً"
        formatted_now = f"{now_oman.strftime('%I').lstrip('0')}:{now_oman.strftime('%M')} {arabic_period}"
        await update.message.reply_text(f"⏱️ الوقت الحالي هو: {formatted_now}")
        return

    # 3. إنشاء وحفظ التذكير بذكاء تام
    if any(word in text for word in ["ذكرني", "نبهني", "تذكير", "ذكر"]):
        try:
            oman_now = get_oman_time()
            settings = {'TIMEZONE': 'Asia/Muscat', 'RELATIVE_BASE': oman_now, 'PREFER_DATES_FROM': 'future'}
            
            dates = search_dates(text, languages=['ar', 'en'], settings=settings)
            
            if dates:
                date_str, dt_oman = dates[0]
                dt_oman = dt_oman.replace(tzinfo=None)
                
                # --- بداية التعديل الذكي لفهم الوقت والأرقام ---
                # التحقق مما إذا كان الوقت المكتوب هو وقت نسبي (مثل: بعد 5 دقائق، كمان ساعة)
                relative_keywords = ['بعد', 'كمان', 'in', 'after', 'دقائق', 'دقيقة', 'ساعة', 'ساعات', 'يوم', 'ايام']
                is_relative = any(word in date_str.lower() for word in relative_keywords)
                
                if not is_relative:
                    # فحص إذا كان المستخدم قد حدد الفترة الزمنية صراحة في النص
                    has_am_pm = bool(re.search(r'\b(ص|م|صباح|مساء|am|pm)\b', text.lower()))
                    
                    # إذا لم يتم تحديد الفترة وكانت الساعة بنظام 12 (من 1 إلى 11)
                    if not has_am_pm and 1 <= dt_oman.hour <= 11:
                        dt_am = dt_oman.replace(hour=dt_oman.hour)
                        dt_pm = dt_oman.replace(hour=dt_oman.hour + 12)
                        
                        # اختيار الوقت الأقرب بناءً على المقارنة بالوقت الحالي
                        if dt_am < oman_now and dt_pm > oman_now:
                            dt_oman = dt_pm
                        elif dt_am > oman_now and dt_pm > oman_now:
                            dt_oman = dt_am if (dt_am - oman_now) < (dt_pm - oman_now) else dt_pm
                        elif dt_am < oman_now and dt_pm < oman_now:
                            # إذا كان كلاهما في الماضي، نعتمد الصباح لليوم التالي
                            dt_oman = dt_am + timedelta(days=1)
                
                # طبقة حماية إضافية: إذا كان الوقت النهائي لا يزال في الماضي، أضف يوماً
                if dt_oman < oman_now:
                    dt_oman += timedelta(days=1)
                # --- نهاية التعديل الذكي ---

                dt_utc = dt_oman - timedelta(hours=4)
                delay = (dt_utc - get_utc_now()).total_seconds()
                
                if delay > 0:
                    task_text = text.replace(date_str, "").replace("ذكرني", "").replace("نبهني", "").replace("بـ", "").replace("أن", "").strip()
                    if not task_text or task_text == "ب":
                        task_text = "تذكير عام"

                    # حفظ التذكير في Supabase
                    insert_response = supabase.table("reminders").insert({
                        "chat_id": chat_id,
                        "task": task_text,
                        "remind_at": dt_utc.isoformat()
                    }).execute()
                    
                    db_id = insert_response.data[0]['id']
                    
                    context.job_queue.run_once(
                        send_reminder,
                        delay,
                        chat_id=chat_id,
                        data={"db_id": db_id, "task": task_text}
                    )
                    
                    # تجهيز رسالة التأكيد باللغة العربية
                    arabic_period = "صباحاً" if dt_oman.hour < 12 else "مساءً"
                    formatted_time = f"{dt_oman.strftime('%I').lstrip('0')}:{dt_oman.strftime('%M')} {arabic_period}"
                    formatted_date = dt_oman.strftime('%Y-%m-%d')
                    
                    await update.message.reply_text(f"✅ تمت الجدولة بنجاح!\nسأنبهك بـ: {task_text}\n⏰ الموعد: {formatted_time} ({formatted_date})")
                else:
                    await update.message.reply_text("عذراً، هذا الوقت يبدو أنه في الماضي!")
            else:
                await update.message.reply_text("لم أستطع تحديد الوقت بدقة.")
                
        except Exception as e:
            await update.message.reply_text(f"حدث خطأ، يرجى المحاولة بصيغة أخرى.\n{str(e)}")
        return

    # 4. الرد بواسطة جيميناي
    if model:
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action='typing')
            response = await model.generate_content_async(update.message.text)
            
            # استبدال إضافي كإجراء احترازي في حال أصر النموذج على استخدام **
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
    
    await context.bot.send_message(
        chat_id=context.job.chat_id, 
        text=f"🔔 حان الموعد!\n\n{task}"
    )
    
    # حذف التذكير من Supabase بعد تنفيذه
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
