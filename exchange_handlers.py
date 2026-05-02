import logging
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.fsm.context import FSMContext
from datetime import datetime

from states import ExchangeState
from exchange_config import CURRENCIES, DEFAULT_RATES, get_currency_by_id
from database import get_user, load_db, save_db
from config import ADMIN_IDS

log = logging.getLogger(__name__)
exchange_router = Router()
CANCEL_TEXTS = {"❌ Бекор қилиш", "❌ Отменить"}



def get_lang(uid: int) -> str:
    u = get_user(uid)
    return (u.get("lang") or "uz") if u else "uz"


def get_rate_info(from_id: str, to_id: str) -> dict | None:
    try:
        from exchange_config import get_effective_rate
        r = get_effective_rate(from_id, to_id)
        if r:
            return r
    except Exception as e:
        log.warning(f"rates_api xato: {e}")
    db     = load_db()
    manual = db.get("manual_rates", {})
    key    = f"{from_id}:{to_id}"
    return manual.get(key) or db.get("rates", DEFAULT_RATES).get(key)


def get_payment_card(cur_id: str) -> str:
    db = load_db()
    return db.get("payment_cards", {
        "uzcard": "8600 1666 0393 7029",
        "humo":   "9860 0000 0000 0000"
    }).get(cur_id, "")


def get_order(order_id: int) -> dict | None:
    db = load_db()
    return db.get("orders", {}).get(str(order_id))


def get_payment_destination(cur_id: str) -> tuple[str, str]:
    destination = (get_payment_card(cur_id) or "").strip()
    if not destination:
        return "", "wallet"
    destination_type = "card" if cur_type(cur_id) == "card" else "wallet"
    return destination, destination_type


def is_cancel_text(text: str | None) -> bool:
    return (text or "").strip() in CANCEL_TEXTS


def calc_receive(send, rate, commission):
    return round(send * rate * (1 - commission / 100), 6)

def calc_send(receive, rate, commission):
    return round(receive / rate / (1 - commission / 100), 2)

def fmt(num) -> str:
    try:
        if isinstance(num, float) and num != int(num):
            return f"{num:.6f}".rstrip("0").rstrip(".")
        return str(int(num))
    except:
        return str(num)

def cur_type(cur_id: str) -> str:
    c = get_currency_by_id(cur_id)
    return c["type"] if c else "crypto"

def main_menu_kb(lang: str):
    from keyboards import main_menu_keyboard
    return main_menu_keyboard(lang)

def cancel_kb(lang: str) -> ReplyKeyboardMarkup:
    label = "❌ Бекор қилиш" if lang == "uz" else "❌ Отменить"
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=label)]], resize_keyboard=True)

def get_next_order_id() -> int:
    db = load_db()
    orders = db.get("orders", {})
    return max((int(k) for k in orders), default=1000) + 1

def save_order(order: dict):
    db = load_db()
    db.setdefault("orders", {})[str(order["order_id"])] = order
    save_db(db)

def update_order_status(order_id: int, status: str, extra: dict | None = None):
    db = load_db()
    if str(order_id) in db.get("orders", {}):
        db["orders"][str(order_id)]["status"] = status
        if extra:
            db["orders"][str(order_id)].update(extra)
        save_db(db)


def admin_receipt_kb(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Тасдиқлаш", callback_data=f"OCONF_{order_id}")],
        [InlineKeyboardButton(text="❌ Бекор қилиш", callback_data=f"OREJ_{order_id}")],
    ])


def build_receipt_caption(order: dict, phone: str, receipt_time: str, fallback_full_name: str, fallback_user_id: int) -> str:
    from_id   = order.get("from_id", "")
    to_id     = order.get("to_id", "")
    from_name = order.get("from_name", "—")
    to_name   = order.get("to_name", "—")
    from_flag = "🇺🇿" if from_id in ("uzcard", "humo") else "🇺🇸"
    to_flag   = "🇺🇿" if to_id   in ("uzcard", "humo") else "🇺🇸"
    return (
        f"🔔 Янги буюртма!\n\n"
        f"🆔 Алмашув: {order.get('order_id', '—')}\n"
        f"🔀: {from_name} ➡️ {to_name}\n"
        f"{from_flag} {from_name}: {order.get('sender_card', '—')}\n"
        f"💸: {fmt(order.get('send_amount', 0))}\n\n"
        f"{to_flag} {to_name}: {order.get('receiver_card', '—')}\n"
        f"{fmt(order.get('recv_amount', 0))} {to_name}\n\n"
        f"📌 Тўлов: Текширувда.\n"
        f"📆 Ўтказма санаси: {receipt_time}\n"
        f"👤 {order.get('full_name', fallback_full_name)} (@{order.get('username', '—')})\n"
        f"📞 {phone}"
    )




def step1_kb() -> InlineKeyboardMarkup:
    rows = []
    for cur in CURRENCIES:
        rows.append([
            InlineKeyboardButton(text=f"🔷 {cur['name']}", callback_data=f"EX1_{cur['id']}"),
            InlineKeyboardButton(text=f"🔶 {cur['name']}", callback_data=f"EX1_{cur['id']}"),
        ])
    rows.append([InlineKeyboardButton(text="🏠 Бош менью", callback_data="EX_CANCEL")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def step2_kb(from_id: str) -> InlineKeyboardMarkup:
    rows = []
    for cur in CURRENCIES:
        selected = cur["id"] == from_id
        left  = InlineKeyboardButton(
            text=f"🔷 {cur['name']} ✅" if selected else f"🔷 {cur['name']}",
            callback_data="EX_NOOP"
        )
        right = InlineKeyboardButton(
            text="■", callback_data="EX_NOOP"
        ) if selected else InlineKeyboardButton(
            text=f"🔶 {cur['name']}", callback_data=f"EX2_{cur['id']}"
        )
        rows.append([left, right])
    rows.append([InlineKeyboardButton(text="🏠 Бош менью", callback_data="EX_CANCEL")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def amount_type_kb(from_name: str, to_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⬆️ Berishni kiritish ({from_name})", callback_data="EX_AMT_SEND")],
        [InlineKeyboardButton(text=f"⬇️ Olishni kiritish ({to_name})",   callback_data="EX_AMT_RECV")],
        [InlineKeyboardButton(text="🏠 Бош менью",                       callback_data="EX_CANCEL")],
    ])


def confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Тўловга ўтиш", callback_data="EX_CONFIRM")],
        [InlineKeyboardButton(text="❌ Бекор қилиш",    callback_data="EX_CANCEL")],
    ])


def payment_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧾 Чекни юбориш", callback_data="EX_RECEIPT")],
        [InlineKeyboardButton(text="❌ Бекор қилиш",    callback_data="EX_CANCEL")],
    ])


@exchange_router.message(F.text.in_(["💱 Valyuta ayirboshlash", "💱 Обмен валют"]))
async def ex_start(message: Message, state: FSMContext):
    await state.clear()
    await state.set_state(ExchangeState.choosing_from)
    await message.answer(
        "🔄 Алмашув: қайси томондан бошлайсиз (🔷 беринг / 🔶 олинг):",
        reply_markup=step1_kb()
    )


@exchange_router.callback_query(F.data.startswith("EX1_"))
async def ex_choose_from(callback: CallbackQuery, state: FSMContext):
    from_id = callback.data[4:]
    cur     = get_currency_by_id(from_id)
    if not cur:
        await callback.answer("❌ Xato!", show_alert=True); return

    lang = get_lang(callback.from_user.id)
    await state.set_state(ExchangeState.choosing_to)
    await state.update_data(from_id=from_id, from_name=cur["name"])

    try:
        await callback.message.edit_text(
            "✅ Берасиз: танланди. Энди оладиган валютани танланг:" if lang=="uz"
            else "✅ Выбрана валюта отдачи. Выберите валюту получения:",
            reply_markup=step2_kb(from_id)
        )
    except Exception:
        await callback.message.answer(
            "✅ Берасиз: танланди. Энди оладиган валютани танланг:",
            reply_markup=step2_kb(from_id)
        )
    await callback.answer()

@exchange_router.callback_query(F.data.startswith("EX2_"))
async def ex_choose_to(callback: CallbackQuery, state: FSMContext):
    to_id  = callback.data[4:]
    to_cur = get_currency_by_id(to_id)
    lang   = get_lang(callback.from_user.id)
    data   = await state.get_data()
    from_id   = data.get("from_id")
    from_name = data.get("from_name")

    if not from_id:
        # State йўқолган — қайтадан бошлаш
        await state.clear()
        await state.set_state(ExchangeState.choosing_from)
        await callback.message.edit_text(
            "🔄 Қайтадан бошланг:", reply_markup=step1_kb()
        )
        await callback.answer(); return

    if to_id == from_id:
        await callback.answer("❌ Бир хил валюта танлаб бўлмайди!", show_alert=True); return

    rate_info = get_rate_info(from_id, to_id)
    if not rate_info:
        await callback.answer("❌ Бу жуфтлик учун курс мавжуд эмас!", show_alert=True); return

    await state.set_state(ExchangeState.choosing_amount_type)
    await state.update_data(to_id=to_id, to_name=to_cur["name"])

    today     = datetime.now().strftime("%d.%m.%Y")
    rate_disp = rate_info.get("rate_display", f"1 {from_name} = {rate_info.get('rate','?')} {to_cur['name']}")

    text = (
        f"🔖 Алмашувингиз:\n\n"
        f"⬆️: {from_name}\n"
        f"⬇️: {to_cur['name']}\n"
        f"📈 Курс: {rate_disp}\n"
        f"🕗 Сана: {today}\n\n"
        f"Қуйида суммани киритиш учун тугмалардан фойдаланинг:"
        if lang == "uz" else
        f"🔖 Ваш обмен:\n\n"
        f"⬆️: {from_name}\n"
        f"⬇️: {to_cur['name']}\n"
        f"📈 Курс: {rate_disp}\n"
        f"🕗 Дата: {today}\n\n"
        f"Используйте кнопки ниже для ввода суммы:"
    )
    try:
        await callback.message.edit_text(text, reply_markup=amount_type_kb(from_name, to_cur["name"]))
    except Exception:
        await callback.message.answer(text, reply_markup=amount_type_kb(from_name, to_cur["name"]))
    await callback.answer()


@exchange_router.callback_query(F.data.in_(["EX_AMT_SEND", "EX_AMT_RECV"]))
async def ex_choose_amount_type(callback: CallbackQuery, state: FSMContext):
    lang  = get_lang(callback.from_user.id)
    data  = await state.get_data()
    from_id   = data.get("from_id")
    to_id     = data.get("to_id")
    from_name = data.get("from_name", "")
    to_name   = data.get("to_name", "")

    if not from_id or not to_id:
        await callback.answer("❌ Qaytadan boshlang", show_alert=True)
        await state.clear()
        await state.set_state(ExchangeState.choosing_from)
        await callback.message.edit_text("🔄 Qaytadan:", reply_markup=step1_kb())
        return

    atype     = "send" if callback.data == "EX_AMT_SEND" else "recv"
    rate_info = get_rate_info(from_id, to_id)
    if not rate_info:
        await callback.answer("❌ Курс топилмади!", show_alert=True); return

    await state.set_state(ExchangeState.entering_amount)
    await state.update_data(amount_type=atype)

    if atype == "send":
        min_v, max_v, cur_label, prefix = rate_info["min"], rate_info["max"], from_name, "⬆️"
    else:
        r, c = rate_info["rate"], rate_info["commission"]
        min_v = round(rate_info["min"] * r * (1 - c / 100), 6)
        max_v = round(rate_info["max"] * r * (1 - c / 100), 6)
        cur_label, prefix = to_name, "⬇️"

    text = (
        f"{prefix} Бериш миқдорини {cur_label}'да киритинг:\n\n"
        f"Minmal: {fmt(min_v)}\n"
        f"Максимал: {fmt(max_v)}"
        if lang == "uz" else
        f"{prefix} Введите сумму в {cur_label}:\n\nМинимум: {fmt(min_v)}\nМаксимум: {fmt(max_v)}"
    )
    try:
        await callback.message.edit_text(text)
    except Exception:
        pass
    await callback.message.answer(
        "👇 Миқдорни киритинг:" if lang=="uz" else "👇 Введите сумму:",
        reply_markup=cancel_kb(lang)
    )
    await callback.answer()


@exchange_router.message(ExchangeState.entering_amount)
async def ex_enter_amount(message: Message, state: FSMContext):
    lang = get_lang(message.from_user.id)
    if is_cancel_text(message.text):
        await do_cancel(message, state); return

    raw = (message.text or "").replace(" ", "").replace(",", ".")
    try:
        amount = float(raw)
        if amount <= 0: raise ValueError
    except:
        await message.answer("❌ Фақат рақам киритинг (масалан: 1242423):"); return

    data      = await state.get_data()
    from_id   = data.get("from_id","")
    to_id     = data.get("to_id","")
    from_name = data.get("from_name","")
    to_name   = data.get("to_name","")
    atype     = data.get("amount_type","send")
    rate_info = get_rate_info(from_id, to_id)

    if not rate_info:
        await message.answer("❌ Курс топилмади. Қайтадан бошланг.")
        await do_cancel(message, state); return

    rate = rate_info["rate"]
    comm = rate_info["commission"]
    mn   = rate_info["min"]
    mx   = rate_info["max"]

    if atype == "send":
        send_amt = amount
        recv_amt = calc_receive(amount, rate, comm)
        if send_amt < mn:
            await message.answer(f"❌ Minmal: {fmt(mn)} {from_name}"); return
        if send_amt > mx:
            await message.answer(f"❌ Максимал: {fmt(mx)} {from_name}"); return
    else:
        recv_amt = amount
        send_amt = calc_send(amount, rate, comm)
        min_r = round(mn * rate * (1 - comm/100), 6)
        max_r = round(mx * rate * (1 - comm/100), 6)
        if recv_amt < min_r:
            await message.answer(f"❌ Minmal: {fmt(min_r)} {to_name}"); return
        if recv_amt > max_r:
            await message.answer(f"❌ Максимал: {fmt(max_r)} {to_name}"); return

    await state.set_state(ExchangeState.entering_sender_card)
    await state.update_data(send_amount=send_amt, recv_amount=recv_amt)

    preview = f"✅ Hisoblandi:\n⬆️ Бирасиз: {fmt(send_amt)} {from_name}\n⬇️ Оласиз: {fmt(recv_amt)} {to_name}\n\n"
    if cur_type(from_id) == "card":
        ask = preview + f"💳 {from_name} карта рақамингизни киритинг:\nМисол: 8600123456789123"
    else:
        ask = preview + f"📲 {from_name} wallet манзилингизни киритинг:"
    await message.answer(ask, reply_markup=cancel_kb(lang))



def validate_card(text: str) -> bool:
    """16 та рақам, бўшлиқ ёки чизиқсиз"""
    digits = text.replace(" ", "").replace("-", "")
    return digits.isdigit() and len(digits) == 16

def validate_wallet(text: str) -> bool:
    """26-42 белги, фақат ҳарф ва рақам"""
    clean = text.strip()
    return clean.isalnum() and 26 <= len(clean) <= 42

def validate_input(text: str, is_card: bool) -> bool:
    return validate_card(text) if is_card else validate_wallet(text)


@exchange_router.message(ExchangeState.entering_sender_card)
async def ex_sender_card(message: Message, state: FSMContext):
    lang = get_lang(message.from_user.id)
    if is_cancel_text(message.text):
        await do_cancel(message, state); return

    data    = await state.get_data()
    from_id = data.get("from_id", "")
    to_id   = data.get("to_id", "")
    to_name = data.get("to_name", "")
    is_card = cur_type(from_id) == "card"
    text    = (message.text or "").strip()

    if not validate_input(text, is_card):
        if is_card:
            await message.answer("❌ Нотўғри карта рақами!\n\n16 та рақам киритинг.\nМисол: 8600123456789123")
        else:
            await message.answer("❌ Нотўғри wallet манзили!\n\n26-42 белги, фақат ҳарф ва рақамдан иборат бўлиши керак.")
        return

    await state.set_state(ExchangeState.entering_receiver_card)
    await state.update_data(sender_card=text)

    if cur_type(to_id) == "card":
        ask = f"💳 {to_name} қабул қиладиган карта рақамини киритинг:\nМисол: 8600123456789123"
    else:
        ask = f"📲 {to_name} қабул қиладиган wallet манзилингизни киритинг:"
    await message.answer(ask, reply_markup=cancel_kb(lang))


@exchange_router.message(ExchangeState.entering_receiver_card)
async def ex_receiver_card(message: Message, state: FSMContext):
    lang = get_lang(message.from_user.id)
    if is_cancel_text(message.text):
        await do_cancel(message, state); return

    data    = await state.get_data()
    to_id   = data.get("to_id", "")
    is_card = cur_type(to_id) == "card"
    text    = (message.text or "").strip()

    if not validate_input(text, is_card):
        if is_card:
            await message.answer("❌ Нотўғри карта рақами!\n\n16 та рақам киритинг.\nМисол: 8600123456789123")
        else:
            await message.answer("❌ Нотўғри wallet манзили!\n\n26-42 белги, фақат ҳарф ва рақамдан иборат бўлиши керак.")
        return

    data      = await state.get_data()
    from_name = data.get("from_name","")
    to_name   = data.get("to_name","")
    send_amt  = data.get("send_amount",0)
    recv_amt  = data.get("recv_amount",0)
    sender    = data.get("sender_card","")
    receiver  = message.text.strip()

    await state.set_state(ExchangeState.confirming)
    await state.update_data(receiver_card=receiver)

    text = (
        f"✅ Маълумотлар қабул қилинди.\n\n"
        f"🔖 Алмашувингиз:\n\n"
        f"🔄: {from_name} ➡️ {to_name}\n"
        f"⬆️ Beriш: {fmt(send_amt)} {from_name}\n"
        f"⬇️ Oliш: {fmt(recv_amt)} {to_name}\n\n"
        f"💳 {from_name}: {sender}\n"
        f"💳 {to_name}: {receiver}\n\n"
        f"*Тўлов тизими комиссияси билан.\n\n"
        f"👉 Тўловга ўтиш учун «✅ Тўловга ўтиш» тугмасини босинг."
    )
    await message.answer(text, reply_markup=confirm_kb())


@exchange_router.callback_query(F.data == "EX_CONFIRM")
async def ex_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()

    from_id   = data.get("from_id","")
    to_id     = data.get("to_id","")
    from_name = data.get("from_name","")
    to_name   = data.get("to_name","")
    send_amt  = data.get("send_amount",0)
    recv_amt  = data.get("recv_amount",0)
    sender    = data.get("sender_card","")
    receiver  = data.get("receiver_card","")
    payment_destination, destination_type = get_payment_destination(from_id)

    if not payment_destination:
        await callback.answer("❌ Тўлов манзили ҳозирча созланмаган!", show_alert=True)
        await callback.message.answer(
            "⚠️ Бу валюта учун админ тўлов манзилини ҳали созламаган. "
            "Илтимос, оператор билан боғланинг ёки кейинроқ қайта уриниб кўринг."
        )
        return

    order_id = get_next_order_id()
    order = {
        "order_id":     order_id,
        "user_id":      callback.from_user.id,
        "username":     callback.from_user.username or "",
        "full_name":    callback.from_user.full_name,
        "from_id":      from_id,  "to_id":       to_id,
        "from_name":    from_name,"to_name":      to_name,
        "send_amount":  send_amt, "recv_amount":  recv_amt,
        "sender_card":  sender,   "receiver_card":receiver,
        "payment_destination": payment_destination,
        "status":       "pending_payment",
        "created_at":   datetime.now().strftime("%d.%m.%Y %H:%M"),
    }
    save_order(order)
    await state.set_state(ExchangeState.payment_pending)
    await state.update_data(order_id=order_id)

    if destination_type == "card":
        text = (
            f"👉 Тўлов учун карта: {payment_destination}\n\n"
            f"1️⃣ Payme.uz, Upay.uz ёки Click.uz га киринг\n"
            f"2️⃣ Summa: {fmt(send_amt)} {from_name}\n"
            f"3️⃣ Karta: {payment_destination}\n\n"
            f"💠 Тўловдан сўнг «🧾 Чекни юбориш» тугмасини босинг\n"
            f"ℹ️ Оператор текширади (2–30 daqiqa)."
        )
    else:
        text = (
            f"📲 {from_name} бўйича админ wallet манзилига ўтказинг:\n\n{payment_destination}\n\n"
            f"Miqdor: {fmt(send_amt)} {from_name}\n\n"
            f"💠 Тўловдан сўнг «🧾 Чекни юбориш» ни босинг."
        )

    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(text, reply_markup=payment_kb())
    await callback.answer()

@exchange_router.callback_query(F.data == "EX_RECEIPT")
async def ex_ask_receipt(callback: CallbackQuery, state: FSMContext):
    lang = get_lang(callback.from_user.id)
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        "🧾 Тўлов чекининг суратини юборинг (screenshot ёки фото):",
        reply_markup=cancel_kb(lang)
    )
    await callback.answer()


@exchange_router.message(ExchangeState.payment_pending, F.photo | F.document)
async def ex_receive_receipt(message: Message, state: FSMContext, bot: Bot):
    lang     = get_lang(message.from_user.id)
    data     = await state.get_data()
    order_id = data.get("order_id")

    if not order_id:
        await do_cancel(message, state); return

    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    receipt_type = "photo" if message.photo else "document"
    receipt_file_id = message.photo[-1].file_id if message.photo else (message.document.file_id if message.document else "")
    receipt_name = message.document.file_name if message.document else ""

    update_order_status(
        order_id,
        "receipt_sent",
        extra={
            "receipt_sent_at": now_str,
            "receipt_type": receipt_type,
            "receipt_file_id": receipt_file_id,
            "receipt_file_name": receipt_name,
        },
    )
    order = get_order(order_id) or {}
    await state.clear()

    # Bot username avtomatik olinadi
    bot_info = await bot.get_me()
    bot_username = f"@{bot_info.username}"

    from_name   = order.get("from_name", "—")
    to_name     = order.get("to_name", "—")
    sender_card = order.get("sender_card", "—")
    recv_card   = order.get("receiver_card", "—")
    send_amt    = fmt(order.get("send_amount", 0))
    recv_amt    = fmt(order.get("recv_amount", 0))

    from_flag = "🇺🇿" if order.get("from_id", "") in ("uzcard", "humo") else "🇺🇸"
    to_flag   = "🇺🇿" if order.get("to_id",   "") in ("uzcard", "humo") else "🇺🇸"

    user_text = (
        f"🆔 Алмашув: {order_id}\n"
        f"🔀: {from_name} ➡️ {to_name}\n"
        f"{from_flag} {from_name}: {sender_card}\n"
        f"💸: {send_amt}\n\n"
        f"{to_flag} {to_name}: {recv_card}\n"
        f"{recv_amt} {to_name}\n\n"
        f"📌 Тўлов: Текширувда.\n"
        f"📆 Ўтказма санаси: {now_str}\n"
        f"😊 Ҳурмат билан: {bot_username}"
    )

    await message.answer(user_text, reply_markup=main_menu_kb(lang))

    user = get_user(message.from_user.id) or {}
    phone = user.get("phone", "—")
    admin_text = build_receipt_caption(
        order=order,
        phone=phone,
        receipt_time=now_str,
        fallback_full_name=message.from_user.full_name,
        fallback_user_id=message.from_user.id,
    )

    for aid in ADMIN_IDS:
        try:
            if receipt_type == "photo":
                await bot.send_photo(
                    aid,
                    photo=receipt_file_id,
                    caption=admin_text,
                    reply_markup=admin_receipt_kb(order_id),
                )
            else:
                await bot.send_document(
                    aid,
                    document=receipt_file_id,
                    caption=admin_text,
                    reply_markup=admin_receipt_kb(order_id),
                )
        except Exception as e:
            log.warning(f"Receipt to admin {aid}: {e}")


@exchange_router.message(ExchangeState.payment_pending)
async def ex_payment_wrong(message: Message, state: FSMContext):
    if is_cancel_text(message.text):
        await do_cancel(message, state); return
    await message.answer("📸 Илтимос, тўлов чекининг СУРАТИНИ юборинг.")


@exchange_router.callback_query(F.data == "EX_CANCEL")
async def ex_cancel_cb(callback: CallbackQuery, state: FSMContext):
    lang = get_lang(callback.from_user.id)
    await state.clear()
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    await callback.message.answer(
        "❌ Алмашув бекор қилинди.",
        reply_markup=main_menu_kb(lang)
    )
    await callback.answer()


@exchange_router.callback_query(F.data == "EX_NOOP")
async def ex_noop(callback: CallbackQuery):
    await callback.answer()


async def do_cancel(message: Message, state: FSMContext):
    lang = get_lang(message.from_user.id)
    await state.clear()
    await message.answer("❌ Алмашув бекор қилинди.", reply_markup=main_menu_kb(lang))
