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

async def update_schedule():
    global day_schedule
    day_schedule.clear()

    async with httpx.AsyncClient() as client:
        r = await client.get(API_URL, timeout=30)
        data = r.json()

    blocks = data["components"][4]["schedule"]["dnipro"]["group_5.1"][2]

    for block in blocks:
        start_dt = float_time_to_datetime(block["start"])
        end_dt = float_time_to_datetime(block["end"])

        day_schedule.append({"type": "start", "time": start_dt})
        day_schedule.append({"type": "end", "time": end_dt})

        # Уведомления
        scheduler.add_job(
            send_notification,
            "date",
            run_date=start_dt - timedelta(minutes=30),
            args=["⚠️ Отключение света через 30 минут!"],
        )
        scheduler.add_job(
            send_notification,
            "date",
            run_date=start_dt - timedelta(minutes=10),
            args=["⚠️ Отключение света через 10 минут!"],
        )

        scheduler.add_job(
            send_notification,
            "date",
            run_date=end_dt - timedelta(minutes=30),
            args=["✅ Включение света через 30 минут!"],
        )
        scheduler.add_job(
            send_notification,
            "date",
            run_date=end_dt - timedelta(minutes=10),
            args=["✅ Включение света через 10 минут!"],
        )

    day_schedule.sort(key=lambda x: x["time"])
    print("Schedule updated:", day_schedule)


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


@dp.message(Command("schedule"))
async def schedule_cmd(message: Message):
    if not day_schedule:
        await message.answer("📅 График на сегодня пуст или еще не загружен.")
        return
    
    msg = "📅 **График отключений (Группа 5.1):**\n\n"
    
    # Шаг 2 позволяет брать элементы парами: (0,1), (2,3), (4,5)
    for i in range(0, len(day_schedule), 2):
        try:
            # Время выключения (start)
            off_time = day_schedule[i]["time"].strftime("%H:%M")
            # Время включения (end)
            on_time = day_schedule[i+1]["time"].strftime("%H:%M")
            
            msg += f"🌑 {off_time} ———— 💡 {on_time}\n"
        except IndexError:
            # Если вдруг в списке нечетное количество элементов
            off_time = day_schedule[i]["time"].strftime("%H:%M")
            msg += f"🌑 {off_time} ———— 💡 ??\n"
    
    msg += "\n*Данные обновляются каждые 30 минут.*"
    
    await message.answer(msg, parse_mode="Markdown")


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


