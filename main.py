import os
import re
from datetime import datetime, timedelta, timezone
from dateparser.search import search_dates 
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
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

# تعريف المنطقة الزمنية لسلطنة عُمان (GMT+4)
OMAN_TZ = timezone(timedelta(hours=4), name="Asia/Muscat")

def get_oman_time():
    return datetime.now(OMAN_TZ)

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
    
    if text == "➕ إضافة تذكير جديد":
        awaiting_reminder_users.add(chat_id)
        await update.message.reply_text("أرسل لي التذكير والوقت الآن!\nمثال: 'المحاضرة بعد 10 دقائق' أو 'المحاضرة 10:40 صباحا'")
        return

    if text == "📋 تذكيراتي":
        awaiting_reminder_users.discard(chat_id)
        now_oman = get_oman_time().isoformat()
        response = supabase.table("reminders").select("*").gte("remind_at", now_oman).eq("chat_id", chat_id).order("remind_at").execute()
        
        if not response.data:
            await update.message.reply_text("📭 ليس لديك أي تذكيرات مجدولة حالياً.")
            return
            
        msg = "📋 قائمة التذكيرات المجدولة:\n\n"
        keyboard = []
        for i, row in enumerate(response.data, 1):
            task_time_oman = datetime.fromisoformat(row['remind_at'])
            arabic_period = "صباحاً" if task_time_oman.hour < 12 else "مساءً"
            formatted_time = f"{task_time_oman.strftime('%I').lstrip('0')}:{task_time_oman.strftime('%M')} {arabic_period}"
            msg += f"{i}. {row['task']} (⏰ {task_time_oman.strftime('%Y-%m-%d')} - {formatted_time})\n"
            
            # إضافة زر الحذف تحت الرسالة
            keyboard.append([InlineKeyboardButton(f"❌ حذف التذكير ({row['task'][:10]}...)", callback_data=f"del_{row['id']}")])
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(msg, reply_markup=reply_markup)
        return

    if text == "⏱️ الوقت الحالي":
        awaiting_reminder_users.discard(chat_id)
        now_oman = get_oman_time()
        arabic_period = "صباحاً" if now_oman.hour < 12 else "مساءً"
        await update.message.reply_text(f"⏱️ الوقت الحالي هو: {now_oman.strftime('%I').lstrip('0')}:{now_oman.strftime('%M')} {arabic_period}")
        return

    is_reminder_intent = chat_id in awaiting_reminder_users or any(word in text_lower for word in ["ذكرني", "نبهني", "تذكير", "ذكر"])
    
    if is_reminder_intent:
        if chat_id in awaiting_reminder_users:
            awaiting_reminder_users.remove(chat_id)
            
        try:
            oman_now = get_oman_time()
            dt_oman = None
            date_str = ""
            
            # 1. التقاط الكلمات العربية الشائعة للمدد النسبية
            rel_match = re.search(r'بعد\s+(\d+)\s*(دقيق|دقائق|ساع|يوم|ايام|أيام)', text_lower)
            if rel_match:
                num = int(rel_match.group(1))
                unit = rel_match.group(2)
                if 'دقيق' in unit:
                    dt_oman = oman_now + timedelta(minutes=num)
                elif 'ساع' in unit:
                    dt_oman = oman_now + timedelta(hours=num)
                elif 'يوم' in unit or 'ايام' in unit or 'أيام' in unit:
                    dt_oman = oman_now + timedelta(days=num)
                date_str = rel_match.group(0)
            
            else:
                # 2. حل مشكلة التاريخ المفقود بإجبار dateparser على فهم "اليوم" إذا لم يتم ذكر يوم
                text_to_parse = text_lower
                day_indicators = ["اليوم", "غدا", "غداً", "بكرة", "يوم", "بعد", "تاريخ", "امس", "الاحد", "الاثنين", "الثلاثاء", "الاربعاء", "الخميس", "الجمعة", "السبت"]
                if not any(word in text_lower for word in day_indicators):
                    text_to_parse = "اليوم " + text_lower

                settings = {
                    'TIMEZONE': '+0400', 
                    'RELATIVE_BASE': oman_now.replace(tzinfo=None), 
                    'PREFER_DATES_FROM': 'future', 
                    'RETURN_AS_TIMEZONE_AWARE': True
                }
                dates = search_dates(text_to_parse, languages=['ar', 'en'], settings=settings)
                
                if dates:
                    # تعويض الخطأ في حال لم نستخدم الكلمة المضافة في النص الأصلي
                    date_str, parsed_dt = dates[0]
                    if date_str.startswith("اليوم "):
                        date_str = date_str.replace("اليوم ", "")

                    if parsed_dt.tzinfo is None:
                        dt_oman = parsed_dt.replace(tzinfo=OMAN_TZ)
                    else:
                        dt_oman = parsed_dt.astimezone(OMAN_TZ)
                        
                    has_am_pm = bool(re.search(r'\b(ص|م|صباح|مساء|am|pm)\b', text_lower))
                    is_relative = any(word in date_str.lower() for word in ['in', 'minute', 'hour', 'day', 'tomorrow'])
                    
                    # معالجة ذكية لمشكلة AM/PM
                    if not is_relative and not has_am_pm and 1 <= dt_oman.hour <= 11:
                        dt_am = dt_oman
                        dt_pm = dt_oman + timedelta(hours=12)
                        
                        if dt_am < oman_now and dt_pm > oman_now:
                            dt_oman = dt_pm
                        elif dt_am > oman_now and dt_pm > oman_now:
                            dt_oman = dt_am if (dt_am - oman_now) < (dt_pm - oman_now) else dt_pm
                        elif dt_am < oman_now and dt_pm < oman_now:
                            dt_oman = dt_am + timedelta(days=1)
                    
                    # إذا كان الوقت قد مضى اليوم، اجعله غداً في نفس الوقت
                    if dt_oman < oman_now:
                        dt_oman += timedelta(days=1)

            if dt_oman:
                delay = (dt_oman - get_oman_time()).total_seconds()
                if delay > 0:
                    task_text = text.replace(date_str, "").replace("ذكرني", "").replace("نبهني", "").replace("بـ", "").replace("أن", "").strip()
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
                    
                    await update.message.reply_text(f"✅ تمت الجدولة بنجاح!\nسأنبهك بـ: *{task_text}*\n⏰ الموعد: {formatted_time} ({dt_oman.strftime('%Y-%m-%d')})")
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

# دالة التعامل مع ضغطات الأزرار الشفافة (Inline Buttons)
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() 
    data = query.data

    if data.startswith("del_"):
        target_id = data.split("_")[1]
        try:
            # 1. الحذف من قاعدة البيانات
            supabase.table("reminders").delete().eq("id", target_id).execute()
            
            # 2. إيقاف الجدولة
            current_jobs = context.job_queue.get_jobs_by_name(str(target_id))
            for job in current_jobs:
                job.schedule_removal()
                
            # 3. إشعار المستخدم وتحديث الرسالة
            await query.edit_message_text(f"✅ تم حذف التذكير بنجاح.")
        except Exception as e:
            await query.edit_message_text("❌ حدث خطأ أثناء الحذف.")

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    task = job_data['task']
    db_id = job_data['db_id']
    await context.bot.send_message(chat_id=context.job.chat_id, text=f"🔔 حان الموعد!\n\n*{task}*")
    try:
        supabase.table("reminders").delete().eq("id", db_id).execute()
    except Exception as e:
        print(f"خطأ في حذف التذكير من DB: {e}")

def main():
    application = Application.builder().token(TOKEN).post_init(load_pending_reminders).build()
    
    # إضافة Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(CallbackQueryHandler(button_callback)) # للتعامل مع أزرار الحذف الشفافة

    PORT = int(os.environ.get('PORT', '5000'))
    RENDER_URL = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")

    if RENDER_URL:
        application.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f"https://{RENDER_URL}/{TOKEN}")
    else:
        print("Bot is running...")
        application.run_polling()

if __name__ == '__main__':
    main()
