import os
import re
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
        "أنا أوريون، مساعدك الشخصي\n"
        "يمكنني تذكيرك بمواعيدك ومهامك.\n\n"
        "أوامر يمكنك استخدامها بأي صيغة تشبه:\n"
        "1️⃣ للجدولة: 'ذكرني بكرة 9 الصبح أرسل الإيميل'\n"
        "2️⃣ للوقت: 'كم الساعة؟' أو 'الوقت الآن'\n"
        "3️⃣ للتذكيرات: 'وش مهامي؟' أو 'قائمة التذكيرات'\n"
        "4️⃣ للإلغاء: 'امسح كل التذكيرات'\n"
    )
    await update.message.reply_text(welcome_message)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة النصوص وتوزيع المهام بمرونة (التخمين)"""
    # تحويل النص لأحرف صغيرة وتجاهل المسافات الزائدة لتسهيل البحث
    text = update.message.text.lower().strip()
    
    # 1. التخمين لطلب الوقت
    if any(word in text for word in ["وقت", "ساعة", "توقيت", "ساعتك"]):
        if not any(word in text for word in ["ذكرني", "نبهني"]): # لتجنب التعارض مع الجدولة
            now_str = get_oman_time().strftime('%Y-%m-%d %I:%M %p')
            # إضافة لمسة محلية لعرض التوقيت
            await update.message.reply_text(f"🕰️ الوقت الحالي (بتوقيت إبراء/عُمان) هو:\n{now_str}")
            return

    # 2. التخمين لقائمة التذكيرات
    if any(word in text for word in ["تذكيراتي", "تذكيرات", "مهام", "قائمة", "جدول"]):
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

    # 3. ميزة جديدة: إلغاء التذكيرات
    if any(word in text for word in ["الغاء", "إلغاء", "امسح", "احذف", "حذف"]):
        jobs = context.job_queue.jobs()
        if not jobs:
            await update.message.reply_text("📭 لا يوجد شيء لإلغائه أصلاً.")
            return
        for job in jobs:
            job.schedule_removal()
        await update.message.reply_text("🗑️ تم إلغاء ومسح جميع التذكيرات بنجاح.")
        return

    # 4. التخمين لإضافة تذكير واستخراج المهمة
    if any(word in text for word in ["ذكرني", "نبهني", "تذكير", "ذكر"]):
        try:
            oman_now = get_oman_time()
            settings = {
                'TIMEZONE': 'Asia/Muscat',
                'RELATIVE_BASE': oman_now
            }
            
            dates = search_dates(text, languages=['ar', 'en'], settings=settings)
            
            if dates:
                date_str, dt = dates[0]
                dt = dt.replace(tzinfo=None)
                
                delay = (dt - oman_now).total_seconds()
                
                if delay > 0:
                    # استخراج المهمة: تنظيف النص من الوقت والكلمات المفتاحية
                    task_text = text.replace(date_str, "").replace("ذكرني", "").replace("نبهني", "").replace("بـ", "").replace("أن", "").strip()
                    if not task_text or task_text == "ب":
                        task_text = "تذكير عام (لم تحدد مهمة)"

                    formatted_time = dt.strftime('%Y-%m-%d %I:%M %p')
                    job_info = f"المهمة: '{task_text}' (⏰ {formatted_time})"
                    
                    context.job_queue.run_once(
                        send_reminder,
                        delay,
                        chat_id=update.effective_chat.id,
                        data=job_info 
                    )
                    
                    await update.message.reply_text(f"✅ تمت الجدولة بنجاح!\nسأنبهك بـ: {task_text}\n⏰ الوقت: {formatted_time}")
                else:
                    await update.message.reply_text("عذراً، هذا الوقت يبدو أنه في الماضي! يرجى المحاولة بصيغة أخرى للمستقبل.")
            else:
                await update.message.reply_text("لم أستطع تحديد الوقت بدقة. حاول استخدام صيغ مثل: 'نبهني بعد 10 دقائق'.")
                
        except Exception as e:
            # رسالة خطأ أكثر وضوحاً
            await update.message.reply_text(f"حدث خطأ غير متوقع أثناء فهم الوقت، يرجى المحاولة بصيغة أخرى.\n(تفاصيل برمجية للمطور: {str(e)})")
    else:
        # رد مرن إذا لم يفهم البوت النية
        await update.message.reply_text("لم أفهم طلبك تماماً 🤔. يمكنك سؤالي عن 'الوقت'، أو قول 'ذكرني بـ...'")

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    """إرسال الإشعار للمستخدم"""
    job = context.job
    await context.bot.send_message(
        chat_id=job.chat_id, 
        text=f"🔔 حان الموعد!\n\n{job.data}"
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
        print("Bot is running...")
        application.run_polling()

if __name__ == '__main__':
    main()
