import os
import json
import re
from datetime import datetime, timedelta, timezone
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

# تعريف المنطقة الزمنية لسلطنة عُمان (GMT+4)
OMAN_TZ = timezone(timedelta(hours=4), name="Asia/Muscat")

def get_oman_time():
    # إرجاع الوقت الحالي كـ Timezone-Aware
    return datetime.now(OMAN_TZ)

# إعداد نموذج الذكاء الاصطناعي كـ NLU فقط
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    # تلقين النموذج ليعمل كمستخرج بيانات فقط ولا يتحدث أبداً
    system_instruction = (
        "أنت محرك NLU (Natural Language Understanding) صامت وعالي الدقة. "
        "مهمتك الوحيدة هي قراءة نص المستخدم، وفهم الموعد المطلوب بناءً على 'الوقت الحالي' المرفق في الطلب (توقيت عُمان GMT+4)، "
        "واستخراج المهمة والوقت الدقيق. "
        "يجب أن ترد بصيغة JSON فقط، بدون أي نصوص إضافية. "
        "الهيكل المطلوب:\n"
        "{\n"
        '  "task": "وصف المهمة المستخرج بدون كلمات التذكير",\n'
        '  "datetime": "YYYY-MM-DDTHH:MM:SS+04:00"\n'
        "}\n"
        "إذا لم يكن النص طلباً لجدولة موعد، أرجع كائن JSON فارغ هكذا: {}"
    )
    # استخدام نموذج سريع (Flash) لأنه الأنسب لهذه المهام
    model = genai.GenerativeModel('gemini-2.5-flash', system_instruction=system_instruction) 
else:
    model = None

async def load_pending_reminders(application: Application):
    print("جاري التحقق من التذكيرات المعلقة...")
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
        "مهمتي هي تذكيرك بمواعيدك بدقة. اكتب لي ما تريد تذكره مباشرة، "
        "مثال: 'ذكرني بكرة الساعة 5 العصر أكلم أحمد'"
    )
    keyboard = [
        [KeyboardButton("📋 تذكيراتي"), KeyboardButton("⏱️ الوقت الحالي")]
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
            await update.message.reply_text("📭 ليس لديك أي تذكيرات مجدولة حالياً.")
            return
        msg = "📋 قائمة التذكيرات المجدولة:\n\n"
        for i, row in enumerate(response.data, 1):
            task_time = datetime.fromisoformat(row['remind_at'])
            arabic_period = "صباحاً" if task_time.hour < 12 else "مساءً"
            formatted_time = f"{task_time.strftime('%I').lstrip('0')}:{task_time.strftime('%M')} {arabic_period}"
            msg += f"{i}. {row['task']} (⏰ {task_time.strftime('%Y-%m-%d')} - {formatted_time})\n"
        await update.message.reply_text(msg)
        return

    if text == "⏱️ الوقت الحالي":
        now_oman = get_oman_time()
        arabic_period = "صباحاً" if now_oman.hour < 12 else "مساءً"
        await update.message.reply_text(f"⏱️ الوقت المحلي: {now_oman.strftime('%I').lstrip('0')}:{now_oman.strftime('%M')} {arabic_period}")
        return

    if any(word in text.lower() for word in ["احذف", "إلغاء", "حذف"]) and "تذكير" in text.lower():
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
                await update.message.reply_text("❌ الرقم غير صحيح. تحقق من 'تذكيراتي'.")
        else:
             await update.message.reply_text("❌ يرجى تحديد رقم التذكير، مثال: 'احذف التذكير 1'")
        return

    # 2. إرسال النص للذكاء الاصطناعي لفهمه واستخراج الموعد
    if not model:
        await update.message.reply_text("❌ واجهة الذكاء الاصطناعي غير متصلة.")
        return

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action='typing')
        
        # تمرير الوقت الحالي الفعلي للنموذج لتجنب الهلوسة في حساب المواعيد
        current_time_iso = get_oman_time().isoformat()
        prompt = f"الوقت الحالي للمستخدم هو: {current_time_iso}\nنص المستخدم: {text}"
        
        response = await model.generate_content_async(prompt)
        ai_output = response.text.strip()

        # تنظيف استجابة الذكاء الاصطناعي من أي علامات Markdown
        if ai_output.startswith("```json"):
            ai_output = ai_output[7:-3].strip()
        elif ai_output.startswith("```"):
            ai_output = ai_output[3:-3].strip()

        parsed_data = json.loads(ai_output)

        if not parsed_data or "datetime" not in parsed_data:
            await update.message.reply_text("عذراً، لم أتمكن من استخراج موعد واضح من رسالتك. جرب صيغة مثل: 'ذكرني بعد نص ساعة بـ...'")
            return

        dt_oman = datetime.fromisoformat(parsed_data["datetime"])
        task_text = parsed_data.get("task", "تذكير بموعد")
        
        delay = (dt_oman - get_oman_time()).total_seconds()
        
        if delay > 0:
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
        else:
            await update.message.reply_text("عذراً، الوقت الذي تم فهمه يقع في الماضي. يرجى تحديد وقت في المستقبل.")

    except json.JSONDecodeError:
        print(f"فشل في تحليل JSON من النموذج: {ai_output}")
        await update.message.reply_text("عذراً، حدث خطأ في معالجة طلبك داخلياً. يرجى المحاولة مرة أخرى.")
    except Exception as e:
        print(f"Error: {e}")
        await update.message.reply_text("عذراً، حدث خطأ غير متوقع.")

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
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    PORT = int(os.environ.get('PORT', '5000'))
    RENDER_URL = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "")

    if RENDER_URL:
        application.run_webhook(listen="0.0.0.0", port=PORT, url_path=TOKEN, webhook_url=f"https://{RENDER_URL}/{TOKEN}")
    else:
        print("Bot is running in strict NLU mode...")
        application.run_polling()

if __name__ == '__main__':
    main()
