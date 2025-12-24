import os
import asyncio
from datetime import datetime, timedelta
from aiohttp import web
import pytz
import httpx

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

from apscheduler.schedulers.asyncio import AsyncIOScheduler

BOT_TOKEN = os.getenv("BOT_TOKEN")
USER_CHAT_ID = int(os.getenv("USER_CHAT_ID"))
TIMEZONE = pytz.timezone("Europe/Kyiv")

API_URL = "https://api.yasno.com.ua/api/v1/pages/home/schedule-turn-off-electricity"

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler(timezone=TIMEZONE)

# График на день
# [
#   {"type": "start", "time": datetime},
#   {"type": "end", "time": datetime}
# ]
day_schedule = []
last_schedule_state = ""  # Хранит текстовое представление графика для сравнения


# ---------- utils ----------

def float_time_to_datetime(value: float) -> datetime:
    hours = int(value)
    minutes = int((value - hours) * 60)

    now = datetime.now(TIMEZONE)

    if hours == 24:
        dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return dt + timedelta(days=1)

    return now.replace(
        hour=hours,
        minute=minutes,
        second=0,
        microsecond=0
    )



def is_power_on(now: datetime) -> bool:
    power = True
    for event in day_schedule:
        if event["type"] == "start" and now >= event["time"]:
            power = False
        if event["type"] == "end" and now >= event["time"]:
            power = True
    return power


async def send_notification(text: str):
    await bot.send_message(USER_CHAT_ID, text)


# ---------- API parsing ----------

async def update_schedule(is_manual=False):
    global day_schedule, last_schedule_state
    
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(API_URL, timeout=30)
            data = r.json()
        
        # Вытаскиваем сырые данные блоков
        blocks = data["components"][4]["schedule"]["dnipro"]["group_5.1"][2]
        
        # Создаем "отпечаток" нового графика для сравнения
        new_state = str(blocks) 
        
        # Если это не первый запуск и график изменился
        if last_schedule_state and new_state != last_schedule_state:
            await send_notification("❗ **Внимание! График отключений изменился!**")
            # Мы вызовем логику отображения графика чуть ниже
            should_notify_change = True
        else:
            should_notify_change = False

        # Обновляем состояние
        last_schedule_state = new_state
        
        # Очищаем и пересобираем график (как и раньше)
        scheduler.remove_all_jobs()
        scheduler.add_job(update_schedule, "interval", minutes=30)
        day_schedule.clear()

        for block in blocks:
            start_dt = float_time_to_datetime(block["start"])
            end_dt = float_time_to_datetime(block["end"])
            day_schedule.append({"type": "start", "time": start_dt})
            day_schedule.append({"type": "end", "time": end_dt})
            
            # Планируем уведомления (логика та же)
            for t_delta, msg in [(30, "через 30 мин"), (10, "через 10 мин")]:
                now = datetime.now(TIMEZONE)
                if start_dt - timedelta(minutes=t_delta) > now:
                    scheduler.add_job(send_notification, "date", 
                                      run_date=start_dt - timedelta(minutes=t_delta),
                                      args=[f"⚠️ Отключение света {msg}!"])
                if end_dt - timedelta(minutes=t_delta) > now:
                    scheduler.add_job(send_notification, "date", 
                                      run_date=end_dt - timedelta(minutes=t_delta),
                                      args=[f"✅ Включение света {msg}!"])

        day_schedule.sort(key=lambda x: x["time"])
        print("Schedule updated")

        # Если график изменился — отправляем новый список
        if should_notify_change:
            # Создаем фейковое сообщение для вызова команды schedule_cmd
            # (Или просто выносим логику формирования текста в отдельную функцию)
            await send_notification(format_schedule_text())

    except Exception as e:
        print(f"Ошибка обновления API: {e}")

# --- Вспомогательная функция для генерации текста графика ---
def format_schedule_text():
    if not day_schedule:
        return "📅 График на сегодня пуст или еще не загружен."
    
    msg = "📅 **График отключений (Группа 5.1):**\n\n"
    
    # Обрабатываем список парами (выкл/вкл)
    for i in range(0, len(day_schedule), 2):
        try:
            off_time = day_schedule[i]["time"].strftime("%H:%M")
            on_time = day_schedule[i+1]["time"].strftime("%H:%M")
            msg += f"🌑 {off_time} ———— 💡 {on_time}\n"
        except IndexError:
            # Если в паре не хватает конечного времени
            off_time = day_schedule[i]["time"].strftime("%H:%M")
            msg += f"🌑 {off_time} ———— 💡 ??\n"
            
    msg += "\n*Данные обновляются каждые 30 минут.*"
    return msg

# ---------- commands ----------

@dp.message(Command("status"))
async def status_cmd(message: Message):
    now = datetime.now(TIMEZONE)
    if is_power_on(now):
        await message.answer("💡 Сейчас свет ЕСТЬ")
    else:
        await message.answer("🌑 Сейчас света НЕТ")


@dp.message(Command("update"))
async def update_cmd(message: Message):
    await update_schedule()
    await message.answer("📅 График обновлён")


# --- Команда бота /schedule ---
@dp.message(Command("schedule"))
async def schedule_cmd(message: Message):
    # Просто вызываем функцию формирования текста и отправляем ответ
    text = format_schedule_text()
    await message.answer(text, parse_mode="Markdown")

# ---------- startup ----------

async def on_startup():
    await update_schedule()
    scheduler.start()


# Handler для проверки здоровья и будущих запросов ESP32
async def handle_status(request):
    now = datetime.now(TIMEZONE)
    status = "ON" if is_power_on(now) else "OFF"
    return web.json_response({"power": status})

async def main():
    await on_startup()
    
    # Настраиваем мини-сервер
    app = web.Application()
    app.router.add_get('/', handle_status) # Для Koyeb и ESP32
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Koyeb дает порт в переменной окружения PORT, по умолчанию 8000
    port = int(os.getenv("PORT", 8000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    
    print(f"🌐 HTTP Server started on port {port}")
    await site.start()

    # Запускаем бота
    print("🤖 Bot polling started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())




