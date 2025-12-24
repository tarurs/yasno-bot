import os
import asyncio
from datetime import datetime, timedelta
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


# ---------- startup ----------

async def on_startup():
    await update_schedule()
    scheduler.start()


async def main():
    await on_startup()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
