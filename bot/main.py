"""PRIVÉ Telegram bot — приём заявок (проверка / SOS / партнёры) и двусторонняя связь с оператором.

Переменные окружения (из /opt/prive/.env):
  BOT_TOKEN       — токен от @BotFather
  ADMIN_CHAT_ID   — id закрытой группы операторов (узнать: добавить бота в группу и написать /id)
  DB_PATH         — путь к sqlite (по умолчанию /data/prive.db)
"""
import asyncio
import logging
import os
import time

import aiosqlite
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (KeyboardButton, Message, ReactionTypeEmoji,
                           ReplyKeyboardMarkup)

import texts as T

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("prive")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)
DB_PATH = os.getenv("DB_PATH", "/data/prive.db")
RATE_LIMIT_SEC = 6 * 3600  # одна заявка каждого типа раз в 6 часов

router = Router()


# ---------- БД: минимум данных ----------
async def db_init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS leads(
            id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, ts INTEGER, status TEXT DEFAULT 'new');
        CREATE TABLE IF NOT EXISTS relay(
            admin_msg_id INTEGER PRIMARY KEY, user_id INTEGER);
        CREATE INDEX IF NOT EXISTS leads_user ON leads(user_id, kind, ts);
        """)
        await db.commit()


async def lead_recent(user_id: int, kind: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM leads WHERE user_id=? AND kind=? AND ts>? LIMIT 1",
            (user_id, kind, int(time.time()) - RATE_LIMIT_SEC))
        return await cur.fetchone() is not None


async def lead_add(user_id: int, kind: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO leads(user_id, kind, ts) VALUES(?,?,?)",
                         (user_id, kind, int(time.time())))
        await db.commit()


async def relay_remember(admin_msg_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO relay VALUES(?,?)", (admin_msg_id, user_id))
        await db.commit()


async def relay_lookup(admin_msg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM relay WHERE admin_msg_id=?", (admin_msg_id,))
        row = await cur.fetchone()
        return row[0] if row else None


# ---------- Клавиатуры ----------
def kb_main() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text=T.BTN_SCAN)],
        [KeyboardButton(text=T.BTN_SOS)],
        [KeyboardButton(text=T.BTN_PARTNER), KeyboardButton(text=T.BTN_HOW)],
    ])


def kb_cancel(*extra: str) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=e) for e in extra]] if extra else []
    rows.append([KeyboardButton(text=T.BTN_CANCEL)])
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=rows)


# ---------- Состояния ----------
class Scan(StatesGroup):
    circle = State()
    nicks = State()
    phone = State()
    photos = State()


class Sos(StatesGroup):
    story = State()


class Partner(StatesGroup):
    about = State()


def is_private(m: Message) -> bool:
    return m.chat.type == ChatType.PRIVATE


def user_line(m: Message) -> str:
    u = m.from_user
    un = f"@{u.username}" if u.username else "без username"
    return f'<a href="tg://user?id={u.id}">{u.full_name}</a> · {un} · <code>{u.id}</code>'


async def to_admin(bot: Bot, m: Message, header: str, body: str, media_ids: list[int] | None = None):
    """Отправить заявку в группу операторов (+ копии медиа из чата клиентки) и запомнить
    id сообщений, чтобы реплай оператора на любое из них ушёл клиентке."""
    if not ADMIN_CHAT_ID:
        log.warning("ADMIN_CHAT_ID не задан — заявка не доставлена")
        return
    head = await bot.send_message(
        ADMIN_CHAT_ID,
        f"{header}\n{user_line(m)}\n\n{body}\n\n<i>Ответьте реплаем на это сообщение — уйдёт клиентке.</i>")
    await relay_remember(head.message_id, m.from_user.id)
    for mid in media_ids or []:
        try:
            c = await bot.copy_message(ADMIN_CHAT_ID, from_chat_id=m.chat.id, message_id=mid,
                                       reply_to_message_id=head.message_id)
            await relay_remember(c.message_id, m.from_user.id)
        except Exception as e:
            log.warning("copy media %s failed: %s", mid, e)


# ---------- Общие ----------
@router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(T.START, reply_markup=kb_main())


@router.message(Command("id"))
async def chat_id(m: Message):
    await m.answer(f"chat_id: <code>{m.chat.id}</code>")


@router.message(F.text == T.BTN_CANCEL, F.chat.type == ChatType.PRIVATE)
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(T.CANCELLED, reply_markup=kb_main())


@router.message(F.text == T.BTN_HOW, F.chat.type == ChatType.PRIVATE)
async def how(m: Message):
    await m.answer(T.HOW, reply_markup=kb_main())


@router.message(F.text.lower().in_({"помощь", "help"}), F.chat.type == ChatType.PRIVATE)
async def help_word(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    await m.answer(T.HELP_WORD, reply_markup=kb_main())
    await to_admin(bot, m, "🚨 <b>СЛОВО «ПОМОЩЬ»</b> — возможное принуждение", "Ответить максимально аккуратно.")


# ---------- Проверка ----------
@router.message(F.text == T.BTN_SCAN, F.chat.type == ChatType.PRIVATE)
async def scan_start(m: Message, state: FSMContext):
    if await lead_recent(m.from_user.id, "scan"):
        return await m.answer(T.RATE_LIMITED, reply_markup=kb_main())
    await state.set_state(Scan.circle)
    await m.answer(T.SCAN_INTRO, reply_markup=kb_cancel())


@router.message(Scan.circle, F.video_note)
async def scan_circle(m: Message, state: FSMContext):
    await state.update_data(circle=m.message_id, photos=[])
    await state.set_state(Scan.nicks)
    await m.answer(T.SCAN_ASK_NICKS, reply_markup=kb_cancel())


@router.message(Scan.circle)
async def scan_circle_bad(m: Message):
    await m.answer(T.SCAN_NEED_CIRCLE, reply_markup=kb_cancel())


@router.message(Scan.nicks, F.text)
async def scan_nicks(m: Message, state: FSMContext):
    await state.update_data(nicks=m.text.strip())
    await state.set_state(Scan.phone)
    await m.answer(T.SCAN_ASK_PHONE, reply_markup=kb_cancel(T.BTN_SKIP))


@router.message(Scan.phone, F.text)
async def scan_phone(m: Message, state: FSMContext):
    await state.update_data(phone="" if m.text == T.BTN_SKIP else m.text.strip())
    await state.set_state(Scan.photos)
    await m.answer(T.SCAN_ASK_PHOTOS, reply_markup=kb_cancel(T.BTN_DONE, T.BTN_SKIP))


@router.message(Scan.photos, F.photo)
async def scan_photo(m: Message, state: FSMContext):
    d = await state.get_data()
    photos = d.get("photos", [])
    if len(photos) < 3:
        photos.append(m.message_id)
        await state.update_data(photos=photos)
    if len(photos) >= 3:
        await scan_finish(m, state)


@router.message(Scan.photos, F.text.in_({T.BTN_DONE, T.BTN_SKIP}))
async def scan_finish(m: Message, state: FSMContext):
    d = await state.get_data()
    await state.clear()
    body = (f"<b>Ники / ссылки:</b> {d.get('nicks', '—')}\n"
            f"<b>Телефон:</b> {d.get('phone') or '—'}\n"
            f"<b>Фото:</b> {len(d.get('photos', []))} · кружок приложен")
    media_ids = [d["circle"]] + d.get("photos", [])
    await to_admin(m.bot, m, "🔍 <b>ЗАЯВКА НА ПРОВЕРКУ</b>", body, media_ids)
    await lead_add(m.from_user.id, "scan")
    await m.answer(T.SCAN_DONE, reply_markup=kb_main())


# ---------- SOS ----------
@router.message(F.text == T.BTN_SOS, F.chat.type == ChatType.PRIVATE)
async def sos_start(m: Message, state: FSMContext, bot: Bot):
    await state.set_state(Sos.story)
    await m.answer(T.SOS_PROTOCOL, reply_markup=kb_cancel())
    await m.answer(T.SOS_ASK)
    await to_admin(bot, m, "🆘 <b>SOS — нажата кнопка</b>", "Клиентка читает протокол, описание ситуации придёт следующим сообщением.")


@router.message(Sos.story, F.text | F.photo | F.document | F.voice | F.video)
async def sos_story(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    body = m.text or m.caption or "(медиа без текста)"
    await to_admin(bot, m, "🆘 <b>SOS — ОПИСАНИЕ</b>", body, [] if m.text else [m.message_id])
    await lead_add(m.from_user.id, "sos")
    await m.answer(T.SOS_DONE, reply_markup=kb_main())


# ---------- Партнёры ----------
@router.message(F.text == T.BTN_PARTNER, F.chat.type == ChatType.PRIVATE)
async def partner_start(m: Message, state: FSMContext):
    if await lead_recent(m.from_user.id, "partner"):
        return await m.answer(T.RATE_LIMITED, reply_markup=kb_main())
    await state.set_state(Partner.about)
    await m.answer(T.PARTNER_INTRO, reply_markup=kb_cancel())


@router.message(Partner.about, F.text)
async def partner_about(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    await to_admin(bot, m, "🤝 <b>ПАРТНЁР</b>", m.text)
    await lead_add(m.from_user.id, "partner")
    await m.answer(T.PARTNER_DONE, reply_markup=kb_main())


# ---------- Обратная связь: оператор → клиентка (реплай в группе) ----------
@router.message(F.chat.id == ADMIN_CHAT_ID, F.reply_to_message)
async def admin_reply(m: Message, bot: Bot):
    uid = await relay_lookup(m.reply_to_message.message_id)
    if not uid:
        return
    try:
        await m.copy_to(uid)
        try:
            await m.react([ReactionTypeEmoji(emoji="👍")])
        except Exception:
            pass
    except Exception as e:  # клиентка заблокировала бота и т.п.
        await m.reply(f"Не доставлено: {e}")


# ---------- Клиентка пишет вне сценария → оператору ----------
@router.message(StateFilter(None), F.chat.type == ChatType.PRIVATE)
async def free_text(m: Message, bot: Bot):
    if not ADMIN_CHAT_ID:
        return await m.answer(T.START, reply_markup=kb_main())
    c = await m.copy_to(ADMIN_CHAT_ID)
    await relay_remember(c.message_id, m.from_user.id)
    info = await bot.send_message(ADMIN_CHAT_ID, f"↑ от {user_line(m)} — ответьте реплаем",
                                  reply_to_message_id=c.message_id)
    await relay_remember(info.message_id, m.from_user.id)


async def main():
    if not BOT_TOKEN:
        log.error("BOT_TOKEN не задан — бот спит. Создайте /opt/prive/.env")
        while True:
            await asyncio.sleep(3600)
    await db_init()
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    log.info("PRIVÉ bot started; admin chat: %s", ADMIN_CHAT_ID or "не задан")
    await dp.start_polling(bot, allowed_updates=["message"])


if __name__ == "__main__":
    asyncio.run(main())
