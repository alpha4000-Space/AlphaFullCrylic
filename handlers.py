from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, Contact, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from datetime import datetime

from config import ADMIN_IDS
from database import get_user, save_user, get_channels, add_channel, remove_channel, get_all_users, load_db, save_db
from keyboards import (
    lang_keyboard, subscribe_keyboard, phone_keyboard,
    main_menu_keyboard, settings_inline_keyboard,
    settings_info_text, admin_keyboard, referral_inline_keyboard, partners_keyboard
)
from states import RegisterState, AdminState, SettingsState, ReferralState, PartnersState, SupportState
from texts import t
from exchange_config import CURRENCIES
from referral_service import (
    parse_referrer_from_start_text,
    apply_referred_by_for_new_user,
    ensure_user_referral_fields_by_id,
    get_referrals_count,
    format_money,
    create_withdraw_request,
    update_referral_card,
    approve_withdraw_request,
    reject_withdraw_request,
)

router = Router()
REFERRAL_CARD_BUTTONS = ["💳 Картани қўшиш/янгилаш"]
REFERRAL_WITHDRAW_BUTTONS = ["💰 Бонусни ечиб олиш"]
REFERRAL_HOME_BUTTONS = ["🏠 Бош менью"]
PARTNERS_ADD_BUTTONS = ["✏️ Қўшиш / ўзгартириш"]
PARTNERS_DELETE_BUTTONS = ["❌ Ўчириш"]
SUPPORT_MENU_TEXTS = [
    "💱 Валюта айирбошлаш",
    "📊 Курс",
    "👥 Ҳамёнлар",
    "👥 Реферал",
    "⚙️ Созламалар",
    "📞 Қайта алоқа",
    "🔄 Алмашувлар",
    "📖 Қўлланма",
    "🔙 Орқага",
]


def referral_withdraw_kb(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Тасдиқлаш", callback_data=f"RWD_OK_{req_id}")],
        [InlineKeyboardButton(text="❌ Бекор қилиш", callback_data=f"RWD_NO_{req_id}")],
    ])


def support_admin_reply_kb(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Жавоб ёзиш", callback_data=f"SUP_REPLY_{user_id}")]
    ])


def _support_header_text(message: Message) -> str:
    user_id = message.from_user.id
    user = get_user(user_id) or {}
    full_name = f"{user.get('name', '')} {user.get('surname', '')}".strip() or message.from_user.full_name
    username = f"@{user.get('username')}" if user.get("username") else "—"
    phone = user.get("phone", "—")
    created = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    return (
        "📞 Қайта алоқа хабари\n\n"
        f"👤 {full_name} ({username})\n"
        f"🆔 {user_id}\n"
        f"📞 {phone}\n"
        f"🕐 {created}"
    )


async def _send_support_to_admins(message: Message, bot: Bot):
    header = _support_header_text(message)
    uid = message.from_user.id
    for aid in ADMIN_IDS:
        try:
            await bot.send_message(aid, header, reply_markup=support_admin_reply_kb(uid))
            await bot.copy_message(aid, message.chat.id, message.message_id)
        except Exception:
            pass


async def send_referral_panel(message: Message, bot: Bot):
    user_id = message.from_user.id
    user = ensure_user_referral_fields_by_id(user_id) or get_user(user_id) or {}
    me = await bot.get_me()
    username = me.username or "bot"
    link = f"https://t.me/{username}?start=ref_{user_id}"
    referrals = get_referrals_count(user_id)
    bonus = format_money(user.get("referral_bonus", 0.0))
    card = user.get("referral_card") or "киритилмаган"

    text = (
        "👥 Сизнинг реферал бўлимингиз\n\n"
        f"🔗 Ҳавола: {link}\n\n"
        f"👤 Рефераллар сони: {referrals}\n"
        f"💰 Бонус баланси: {bonus} сўм\n"
        f"💳 Карта: {card}"
    )
    await message.answer(text, reply_markup=referral_inline_keyboard("uz"))


def _currency_help_text() -> str:
    lines = [f"• {c['name']} ({c['id']})" for c in CURRENCIES]
    return "\n".join(lines)


def _resolve_currency(text: str | None) -> dict | None:
    raw = (text or "").strip().lower()
    if not raw:
        return None
    compact = raw.replace(" ", "").replace("-", "").replace("_", "").replace("(", "").replace(")", "")
    for cur in CURRENCIES:
        if raw == cur["id"].lower():
            return cur
        if raw == cur["name"].lower():
            return cur
        cur_compact = cur["name"].lower().replace(" ", "").replace("-", "").replace("_", "").replace("(", "").replace(")", "")
        if compact == cur_compact:
            return cur
    return None


def _get_user_wallets(user_id: int) -> dict:
    user = get_user(user_id) or {}
    wallets = user.get("wallets", {})
    return wallets if isinstance(wallets, dict) else {}


def _save_user_wallet(user_id: int, cur_id: str, value: str) -> bool:
    db = load_db()
    users = db.get("users", {})
    user = users.get(str(user_id))
    if not user:
        return False
    wallets = user.get("wallets", {})
    if not isinstance(wallets, dict):
        wallets = {}
    wallets[cur_id] = value.strip()
    user["wallets"] = wallets
    save_db(db)
    return True


def _delete_user_wallet(user_id: int, cur_id: str) -> bool:
    db = load_db()
    users = db.get("users", {})
    user = users.get(str(user_id))
    if not user:
        return False
    wallets = user.get("wallets", {})
    if not isinstance(wallets, dict):
        wallets = {}
    existed = cur_id in wallets
    wallets.pop(cur_id, None)
    user["wallets"] = wallets
    save_db(db)
    return existed


def _partners_text(user_id: int) -> str:
    wallets = _get_user_wallets(user_id)
    empty = "бўш"
    title = "📁 Сизнинг ҳамёнларингиз:"
    lines = [title, ""]
    for cur in CURRENCIES:
        val = wallets.get(cur["id"], empty)
        lines.append(f"💸 {cur['name']}: {val}")
    return "\n".join(lines)


async def send_partners_panel(message: Message):
    await message.answer(_partners_text(message.from_user.id), reply_markup=partners_keyboard("uz"))


def _mask_payment_value(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return "—"
    digits_only = "".join(ch for ch in raw if ch.isdigit())
    if len(digits_only) >= 12:
        tail = digits_only[-4:]
        return f"**** **** **** {tail}"
    if len(raw) <= 8:
        return raw
    return f"{raw[:6]}...{raw[-4:]}"


def _normalize_created_at(value: str) -> str:
    v = (value or "").strip()
    if not v:
        return "—"
    from datetime import datetime as _dt
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            parsed = _dt.strptime(v, fmt)
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
    return v


def _order_status_label(status: str) -> str:
    st = (status or "").strip()
    if st in ("pending_payment", "receipt_sent"):
        return "Янги"
    if st == "completed":
        return "Тасдиқланган"
    if st == "cancelled":
        return "Бекор қилинган"
    return st or "Номаълум"


def _get_user_orders(user_id: int) -> list[dict]:
    db = load_db()
    orders = list(db.get("orders", {}).values())
    result = []
    for o in orders:
        try:
            if int(o.get("user_id", 0)) == int(user_id):
                result.append(o)
        except Exception:
            continue
    result.sort(key=lambda x: int(x.get("order_id", 0)), reverse=True)
    return result


def _fmt(n):
    try:
        return str(int(n)) if isinstance(n, float) and n == int(n) else f"{n:.6f}".rstrip("0").rstrip(".")
    except: return str(n)

def _format_order_block(order: dict) -> str:
    send_amount = order.get("send_amount", 0)
    recv_amount = order.get("recv_amount", order.get("receive_amount", 0))
    sender = _mask_payment_value(order.get("sender_card", ""))
    receiver = _mask_payment_value(order.get("receiver_card", ""))
    status = _order_status_label(order.get("status", ""))
    created_at = _normalize_created_at(order.get("created_at", ""))
    return (
        f"🆔 ИД: {order.get('order_id', '—')}\n"
        f"🔁 {order.get('from_name', '—')} → {order.get('to_name', '—')}\n"
        f"💰 {_fmt(send_amount)} → {_fmt(recv_amount)}\n"
        f"📤 Жўнатувчи: {sender}\n"
        f"📥 Қабул қилувчи: {receiver}\n"
        f"📅 Яратилган: {created_at}\n"
        f"📌 {status}"
    )


def _transfers_inline_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📣 Барча алмашувларни кўриш", callback_data="TR_ALL")]
    ])


def _paginate_order_blocks(blocks: list[str], first_title: str) -> list[str]:
    if not blocks:
        return [first_title]
    sep = "\n\n——————————\n\n"
    pages: list[str] = []
    current_blocks: list[str] = []
    current_len = 0
    limit = 3800
    for block in blocks:
        add = len(block) + (len(sep) if current_blocks else 0)
        if current_blocks and (current_len + add) > limit:
            prefix = first_title if not pages else "🔄 Давоми:"
            pages.append(prefix + "\n\n" + sep.join(current_blocks))
            current_blocks = [block]
            current_len = len(block)
        else:
            current_blocks.append(block)
            current_len += add
    if current_blocks:
        prefix = first_title if not pages else "🔄 Давоми:"
        pages.append(prefix + "\n\n" + sep.join(current_blocks))
    return pages



async def check_subscriptions(bot: Bot, user_id: int) -> bool:
    """Check if user is subscribed to all required channels"""
    channels = get_channels()
    if not channels:
        return True
    for ch in channels:
        try:
            member = await bot.get_chat_member(ch["channel_id"], user_id)
            if member.status in ("left", "kicked", "banned"):
                return False
        except Exception:
            return False
    return True


def get_lang(user_id: int) -> str:
    user = get_user(user_id)
    if user and "lang" in user:
        return user["lang"]
    return "uz"


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    referred_by = parse_referrer_from_start_text(message.text or "", user_id)
    if referred_by:
        await state.update_data(referred_by=referred_by)

    # Admin check
    if user_id in ADMIN_IDS:
        user = get_user(user_id)
        if user and user.get("registered"):
            lang = user.get("lang", "uz")
            await message.answer("👨‍💼 Хуш келибсиз, Админ!", reply_markup=main_menu_keyboard(lang))
            return

    user = get_user(user_id)

    if user and user.get("registered"):
        lang = user.get("lang", "uz")
        await message.answer(t(lang, "main_menu"), reply_markup=main_menu_keyboard(lang))
        return

    channels = get_channels()
    if channels:
        subscribed = await check_subscriptions(bot, user_id)
        if not subscribed:
            await message.answer(
                t("uz", "subscribe_required"),
                reply_markup=subscribe_keyboard(channels)
            )
            return

    # Ask language
    await state.set_state(RegisterState.choosing_lang)
    await message.answer(t("uz", "choose_lang"), reply_markup=lang_keyboard())



@router.callback_query(F.data == "check_subscribe")
async def check_subscribe_callback(callback: CallbackQuery, state: FSMContext, bot: Bot):
    user_id = callback.from_user.id
    subscribed = await check_subscriptions(bot, user_id)

    if not subscribed:
        channels = get_channels()
        await callback.answer(t("uz", "not_subscribed"), show_alert=True)
        return

    await callback.message.delete()

    user = get_user(user_id)
    if user and user.get("registered"):
        lang = user.get("lang", "uz")
        await callback.message.answer(t(lang, "main_menu"), reply_markup=main_menu_keyboard(lang))
        return

    await state.set_state(RegisterState.choosing_lang)
    await callback.message.answer(t("uz", "choose_lang"), reply_markup=lang_keyboard())

@router.callback_query(RegisterState.choosing_lang, F.data.startswith("lang_"))
async def choose_language(callback: CallbackQuery, state: FSMContext):
    lang = callback.data.split("_")[1]  # "uz" or "ru"

    await state.update_data(lang=lang)
    await callback.message.delete()
    await callback.answer(t(lang, "lang_selected"))

    await state.set_state(RegisterState.entering_name)
    await callback.message.answer(t(lang, "enter_name"))


# =================== REGISTRATION ===================

@router.message(RegisterState.entering_name)
async def enter_name(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")

    name = message.text.strip()
    if not name or len(name) < 2:
        await message.answer("❌ Илтимос, тўғри исм киритинг (камида 2 та ҳарф):")
        return

    await state.update_data(name=name)
    await state.set_state(RegisterState.entering_surname)
    await message.answer(t(lang, "enter_surname"))


@router.message(RegisterState.entering_surname)
async def enter_surname(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")

    surname = message.text.strip()
    if not surname or len(surname) < 2:
        await message.answer("❌ Илтимос, тўғри фамилия киритинг (камида 2 та ҳарф):")
        return

    await state.update_data(surname=surname)
    await state.set_state(RegisterState.entering_phone)
    await message.answer(t(lang, "enter_phone"), reply_markup=phone_keyboard(lang))


@router.message(RegisterState.entering_phone, F.contact)
async def enter_phone_contact(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    contact: Contact = message.contact
    phone = contact.phone_number

    await finish_registration(message, state, data, phone, lang)


@router.message(RegisterState.entering_phone, F.text)
async def enter_phone_text(message: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    phone = message.text.strip()

    # Basic phone validation
    cleaned = phone.replace("+", "").replace(" ", "").replace("-", "")
    if not cleaned.isdigit() or len(cleaned) < 9:
        await message.answer("❌ Илтимос, тўғри телефон рақам киритинг:")
        return

    await finish_registration(message, state, data, phone, lang)


async def finish_registration(message: Message, state: FSMContext, data: dict, phone: str, lang: str):
    user_id = message.from_user.id
    name = data.get("name")
    surname = data.get("surname")
    referred_by = data.get("referred_by")

    user_data = {
        "user_id": user_id,
        "username": message.from_user.username,
        "lang": lang,
        "name": name,
        "surname": surname,
        "phone": phone,
        "registered": True
    }
    apply_referred_by_for_new_user(user_data, referred_by)
    save_user(user_id, user_data)
    ensure_user_referral_fields_by_id(user_id)

    await state.clear()
    await message.answer(
        t(lang, "registration_done", name=name, surname=surname, phone=phone),
        reply_markup=main_menu_keyboard(lang)
    )

@router.message(F.text.in_(["💱 Валюта айирбошлаш"]))
async def menu_exchange(message: Message):
    await message.answer(t("uz", "exchange_menu"))


@router.message(F.text.in_(["📊 Курс"]))
async def menu_rates(message: Message, bot: Bot):
    from database import load_db
    from exchange_config import CURRENCIES
    db = load_db()
    rates = db.get("crypto_rates", {})
    if not rates:
        await message.answer("⏳ Курслар ҳали киритилмаган.")
        return
    sell_lines = []
    buy_lines = []
    for cur in CURRENCIES:
        if cur["type"] != "crypto":
            continue
        r = rates.get(cur["id"])
        if not r:
            continue
        if r.get("sell_rate"):
            sell_lines.append(f"1 {cur['name']} = {int(r['sell_rate'])} СЎМ")
        if r.get("buy_rate"):
            buy_lines.append(f"1 {cur['name']} = {int(r['buy_rate'])} СЎМ")
    text = ""
    if sell_lines:
        text += "📉 Сотиш курси\n" + "\n".join(sell_lines) + "\n\n"
    if buy_lines:
        text += "📈 Сотиб олиш курси\n" + "\n".join(buy_lines)
    if not text:
        text = "⏳ Курслар ҳали киритилмаган."
    await message.answer(text)


@router.message(F.text.in_(["👥 Ҳамёнлар"]))
async def menu_partners(message: Message):
    await send_partners_panel(message)


@router.message(F.text.in_(PARTNERS_ADD_BUTTONS))
async def partners_add_start(message: Message, state: FSMContext):
    await state.set_state(PartnersState.waiting_currency_add)
    await message.answer("✏️ Қайси валюта ҳамёнини қўшмоқчи/ўзгартирмоқчисиз?\n\n" + _currency_help_text())


@router.message(PartnersState.waiting_currency_add)
async def partners_add_currency(message: Message, state: FSMContext):
    cur = _resolve_currency(message.text)
    if not cur:
        await message.answer("❌ Валюта топилмади. Қайта киритинг:\n\n" + _currency_help_text())
        return
    await state.update_data(partners_currency=cur["id"])
    await state.set_state(PartnersState.waiting_wallet_add)
    await message.answer(f"💳 {cur['name']} учун ҳамён манзилини киритинг:")


@router.message(PartnersState.waiting_wallet_add)
async def partners_add_wallet(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if len(value) < 4:
        await message.answer("❌ Қиймат жуда қисқа. Қайта киритинг:")
        return
    data = await state.get_data()
    cur_id = data.get("partners_currency")
    if not cur_id:
        await state.clear()
        await message.answer("❌ Жараён тугади. Қайтадан уриниб кўринг.")
        return
    ok = _save_user_wallet(message.from_user.id, cur_id, value)
    await state.clear()
    if not ok:
        await message.answer("❌ Сақлашда хатолик бўлди.")
        return
    await message.answer("✅ Ҳамён сақланди.")
    await send_partners_panel(message)


@router.message(F.text.in_(PARTNERS_DELETE_BUTTONS))
async def partners_delete_start(message: Message, state: FSMContext):
    await state.set_state(PartnersState.waiting_currency_delete)
    await message.answer("❌ Қайси валюта ҳамёнини ўчирмоқчисиз?\n\n" + _currency_help_text())


@router.message(PartnersState.waiting_currency_delete)
async def partners_delete_currency(message: Message, state: FSMContext):
    cur = _resolve_currency(message.text)
    if not cur:
        await message.answer("❌ Валюта топилмади. Қайта киритинг:\n\n" + _currency_help_text())
        return
    existed = _delete_user_wallet(message.from_user.id, cur["id"])
    await state.clear()
    if existed:
        await message.answer(f"✅ {cur['name']} ҳамёни ўчирилди.")
    else:
        await message.answer(f"ℹ️ {cur['name']} uchun saqlangan hamyon topilmadi.")
    await send_partners_panel(message)


@router.message(F.text.in_(["👥 Реферал"]))
async def menu_referral(message: Message, bot: Bot):
    await send_referral_panel(message, bot)


@router.callback_query(F.data == "REF_CARD")
async def referral_card_start_cb(callback: CallbackQuery, state: FSMContext):
    lang = get_lang(callback.from_user.id)
    await state.set_state(ReferralState.waiting_card)
    await callback.answer()
    await callback.message.answer("💳 Бонус ечиш учун картангизни киритинг:")


@router.callback_query(F.data == "REF_WITHDRAW")
async def referral_withdraw_start_cb(callback: CallbackQuery, bot: Bot):
    lang = get_lang(callback.from_user.id)
    req, err = create_withdraw_request(callback.from_user.id)
    if err == "no_card":
        await callback.answer("Аввал картани киритинг", show_alert=True)
        return
    if err == "zero":
        await callback.answer("Бонус баланси 0", show_alert=True)
        return
    if err == "min":
        await callback.answer("Минимал сумма ҳали етарли эмас", show_alert=True)
        return
    if err == "pending":
        await callback.answer("Кутилаётган сўров мавжуд", show_alert=True)
        return
    if not req:
        await callback.answer("Хатолик", show_alert=True)
        return

    await _send_withdraw_to_admins(bot, req, callback.from_user)
    await callback.message.answer("✅ Сўровингиз админга юборилди. Тасдиқланишини кутинг.")
    await callback.answer("✅")


@router.callback_query(F.data == "REF_HOME")
async def referral_home_cb(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(t("uz", "main_menu"), reply_markup=main_menu_keyboard("uz"))
    await callback.answer()


@router.message(F.text.in_(REFERRAL_CARD_BUTTONS))
async def referral_card_start(message: Message, state: FSMContext):
    await state.set_state(ReferralState.waiting_card)
    await message.answer("💳 Бонус ечиш учун картангизни киритинг:")


@router.message(ReferralState.waiting_card)
async def referral_card_save(message: Message, state: FSMContext, bot: Bot):
    if (message.text or "").strip() in REFERRAL_HOME_BUTTONS:
        await state.clear()
        await message.answer(t("uz", "main_menu"), reply_markup=main_menu_keyboard("uz"))
        return
    if (message.text or "").strip() in ["🔙 Орқага"]:
        await state.clear()
        await send_referral_panel(message, bot)
        return

    card = (message.text or "").replace(" ", "")
    if not card or len(card) < 8:
        await message.answer("❌ Карта рақамини тўғри киритинг.")
        return
    ok = update_referral_card(message.from_user.id, card)
    await state.clear()
    if not ok:
        await message.answer("❌ Сақлашда хатолик бўлди.")
        return
    await message.answer("✅ Карта сақланди.")
    await send_referral_panel(message, bot)


@router.message(F.text.in_(REFERRAL_WITHDRAW_BUTTONS))
async def referral_withdraw_start(message: Message, bot: Bot):
    req, err = create_withdraw_request(message.from_user.id)
    if err == "no_card":
        await message.answer("❌ Аввал картани киритинг.")
        return
    if err == "zero":
        await message.answer("❌ Бонус баланси 0.")
        return
    if err == "min":
        await message.answer("❌ Бонус ечиш учун минимал сумма ҳали етарли эмас.")
        return
    if err == "pending":
        await message.answer("⏳ Сизда аллақачон кутилаётган бонус ечиш сўрови бор.")
        return
    if not req:
        await message.answer("❌ Сўров юборилмади. Қайта уриниб кўринг.")
        return

    await _send_withdraw_to_admins(bot, req, message.from_user)
    await message.answer("✅ Сўровингиз админга юборилди. Тасдиқланишини кутинг.")


@router.message(F.text.in_(REFERRAL_HOME_BUTTONS))
async def referral_go_home(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(t("uz", "main_menu"), reply_markup=main_menu_keyboard("uz"))


@router.callback_query(F.data.startswith("RWD_OK_"))
async def referral_withdraw_approve(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Ruxsat yo'q", show_alert=True)
        return
    try:
        req_id = int(callback.data.split("_")[-1])
    except Exception:
        await callback.answer("❌ Xato", show_alert=True)
        return

    req, user, err = approve_withdraw_request(req_id, callback.from_user.id)
    if err == "not_found":
        await callback.answer("❌ So'rov topilmadi", show_alert=True)
        return
    if err == "already_processed":
        await callback.answer("⚠️ So'rov avval qayta ishlangan", show_alert=True)
        return

    if req:
        uid = req.get("user_id")
        if uid:
            try:
                await bot.send_message(uid, f"✅ Referral bonusingiz chiqarildi.\n💸 {format_money(req.get('amount', 0))} so'm")
            except Exception:
                pass
    await callback.message.edit_text(f"✅ Referral so'rov #{req_id} tasdiqlandi.")
    await callback.answer("✅")


@router.callback_query(F.data.startswith("RWD_NO_"))
async def referral_withdraw_reject(callback: CallbackQuery, bot: Bot):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Ruxsat yo'q", show_alert=True)
        return
    try:
        req_id = int(callback.data.split("_")[-1])
    except Exception:
        await callback.answer("❌ Xato", show_alert=True)
        return

    req, user, err = reject_withdraw_request(req_id, callback.from_user.id)
    if err == "not_found":
        await callback.answer("❌ So'rov topilmadi", show_alert=True)
        return
    if err == "already_processed":
        await callback.answer("⚠️ So'rov avval qayta ishlangan", show_alert=True)
        return

    if req:
        uid = req.get("user_id")
        if uid:
            try:
                await bot.send_message(uid, "❌ Реферал бонус ечиш сўрови бекор қилинди. Бонус балансга қайтарилди.")
            except Exception:
                pass
    await callback.message.edit_text(f"❌ Referral so'rov #{req_id} bekor qilindi.")
    await callback.answer("❌")


@router.message(F.text.in_(["📞 Қайта алоқа"]))
async def menu_callback(message: Message, state: FSMContext):
    lang = get_lang(message.from_user.id)
    await state.set_state(SupportState.user_writing)
    await message.answer("✍️ Iltimos, adminlarga yuborish uchun xabaringizni yozing:")


@router.message(SupportState.user_writing, F.text & ~F.text.in_(SUPPORT_MENU_TEXTS))
async def support_user_text(message: Message, bot: Bot):
    await _send_support_to_admins(message, bot)
    await message.answer("✅ Хабарингиз админларга юборилди.")


@router.message(SupportState.user_writing, F.photo | F.document | F.video | F.voice | F.audio | F.sticker)
async def support_user_media(message: Message, bot: Bot):
    await _send_support_to_admins(message, bot)
    await message.answer("✅ Хабарингиз админларга юборилди.")


@router.callback_query(F.data.startswith("SUP_REPLY_"))
async def support_admin_reply_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("❌ Ruxsat yo'q", show_alert=True)
        return
    try:
        uid = int(callback.data.split("_")[-1])
    except Exception:
        await callback.answer("❌ Xato", show_alert=True)
        return

    user = get_user(uid)
    if not user:
        await callback.answer("❌ User topilmadi", show_alert=True)
        return

    await state.set_state(SupportState.admin_replying)
    await state.update_data(support_uid=uid)
    await callback.answer()
    await callback.message.answer(
        f"✍️ User {uid} ga yuboriladigan javobni yozing.\n"
        f"Бекор қилиш учун: ❌ Бекор"
    )


@router.message(SupportState.admin_replying, F.text)
async def support_admin_reply_text(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return
    text = (message.text or "").strip()
    if text == "❌ Бекор":
        await state.clear()
        await message.answer("❌ Бекор қилинди.")
        return

    data = await state.get_data()
    uid = data.get("support_uid")
    if not uid:
        await state.clear()
        await message.answer("❌ Сессия тугади, қайта уриниб кўринг.")
        return

    try:
        await bot.send_message(int(uid), f"👨‍💼 Админ жавоби:\n\n{text}")
        await message.answer("✅ Жавоб юборилди.")
    except Exception:
        await message.answer("❌ Жавоб юборилмади.")
    await state.clear()


@router.message(SupportState.admin_replying, F.photo | F.document | F.video | F.voice | F.audio | F.sticker)
async def support_admin_reply_media(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return

    data = await state.get_data()
    uid = data.get("support_uid")
    if not uid:
        await state.clear()
        await message.answer("❌ Сессия тугади, қайта уриниб кўринг.")
        return

    try:
        await bot.send_message(int(uid), "👨‍💼 Админдан медиа хабар:")
        await bot.copy_message(int(uid), message.chat.id, message.message_id)
        await message.answer("✅ Жавоб юборилди.")
    except Exception:
        await message.answer("❌ Жавоб юборилмади.")
    await state.clear()


@router.message(F.text.in_(["🔄 Алмашувлар"]))
async def menu_transfers(message: Message):
    lang = get_lang(message.from_user.id)
    orders = _get_user_orders(message.from_user.id)
    title = "🔄 Сизнинг алмашувларингиз:"
    empty = "📭 Сизда ҳали алмашувлар йўқ."

    if not orders:
        await message.answer(f"{title}\n\n{empty}")
        return

    blocks = [_format_order_block(o) for o in orders[:2]]
    pages = _paginate_order_blocks(blocks, title)
    for idx, page_text in enumerate(pages):
        if idx == 0:
            await message.answer(page_text, reply_markup=_transfers_inline_kb())
        else:
            await message.answer(page_text)


@router.callback_query(F.data == "TR_ALL")
async def menu_transfers_all(callback: CallbackQuery):
    orders = _get_user_orders(callback.from_user.id)
    if not orders:
        await callback.answer("📭 Алмашув йўқ", show_alert=True)
        return
    title = "🔄 Барча алмашувларингиз:"
    blocks = [_format_order_block(o) for o in orders]
    pages = _paginate_order_blocks(blocks, title)
    for page_text in pages:
        await callback.message.answer(page_text)
    await callback.answer()


@router.message(F.text.in_(["📖 Қўлланма"]))
async def menu_guide(message: Message):
    await message.answer(t("uz", "guide_menu"))

@router.message(F.text.in_(["⚙️ Созламалар"]))
async def menu_settings(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user = get_user(user_id)
    await state.set_state(SettingsState.in_settings)
    await message.answer(settings_info_text(user, "uz"), reply_markup=settings_inline_keyboard("uz"))


@router.callback_query(F.data == "settings_lang")
async def settings_change_lang(callback: CallbackQuery, state: FSMContext):
    await state.set_state(RegisterState.choosing_lang)
    await state.update_data(changing_lang=True)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t("uz", "choose_lang"), reply_markup=lang_keyboard())
    await callback.answer()


@router.callback_query(F.data == "settings_name")
async def settings_change_name_cb(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.changing_name)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t("uz", "enter_name"))
    await callback.answer()


@router.callback_query(F.data == "settings_phone")
async def settings_change_phone_cb(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.changing_phone)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(t("uz", "enter_phone"), reply_markup=phone_keyboard("uz"))
    await callback.answer()


@router.message(SettingsState.changing_name)
async def change_name_finish(message: Message, state: FSMContext):
    user_id = message.from_user.id
    name = message.text.strip()
    if not name or len(name) < 2:
        await message.answer("❌ Илтимос, тўғри исм киритинг:")
        return
    user = get_user(user_id)
    user["name"] = name
    save_user(user_id, user)
    await state.clear()
    await message.answer(settings_info_text(get_user(user_id), "uz"), reply_markup=settings_inline_keyboard("uz"))


@router.message(SettingsState.changing_phone, F.contact)
async def change_phone_contact(message: Message, state: FSMContext):
    user_id = message.from_user.id
    user = get_user(user_id)
    user["phone"] = message.contact.phone_number
    save_user(user_id, user)
    await state.clear()
    await message.answer(settings_info_text(get_user(user_id), "uz"), reply_markup=settings_inline_keyboard("uz"))


@router.message(SettingsState.changing_phone, F.text)
async def change_phone_text(message: Message, state: FSMContext):
    user_id = message.from_user.id
    phone = message.text.strip()
    cleaned = phone.replace("+", "").replace(" ", "").replace("-", "")
    if not cleaned.isdigit() or len(cleaned) < 9:
        await message.answer("❌ Илтимос, тўғри телефон рақам киритинг:")
        return
    user = get_user(user_id)
    user["phone"] = phone
    save_user(user_id, user)
    await state.clear()
    await message.answer(settings_info_text(get_user(user_id), "uz"), reply_markup=settings_inline_keyboard("uz"))


@router.message(F.text.in_(["🔙 Орқага"]))
async def go_back(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(t("uz", "main_menu"), reply_markup=main_menu_keyboard("uz"))


@router.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("👨‍💼 Админ панели", reply_markup=admin_keyboard())


@router.message(F.text == "➕ Канал қўшиш")
async def admin_add_channel_start(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminState.waiting_channel_id)
    await message.answer("Канал ИД сини киритинг (масалан: -1001234567890):\n\n💡 Ботни каналга админ қилиб қўшинг!")


@router.message(AdminState.waiting_channel_id)
async def admin_add_channel_id(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        channel_id = int(message.text.strip())
        await state.update_data(channel_id=channel_id)
        await state.set_state(AdminState.waiting_channel_link)
        await message.answer("Канал ҳаволасини киритинг (масалан: https://t.me/kanalim):")
    except ValueError:
        await message.answer("❌ Нотўғри формат! ИД сон бўлиши керак:")


@router.message(AdminState.waiting_channel_link)
async def admin_add_channel_link(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    link = message.text.strip()
    await state.update_data(channel_link=link)
    await state.set_state(AdminState.waiting_channel_name)
    await message.answer("Канал номини киритинг:")


@router.message(AdminState.waiting_channel_name)
async def admin_add_channel_name(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    data = await state.get_data()
    name = message.text.strip()

    result = add_channel(data["channel_id"], data["channel_link"], name)
    await state.clear()

    if result:
        await message.answer(f"✅ Канал қўшилди!\n📢 {name}\n🔗 {data['channel_link']}", reply_markup=admin_keyboard())
    else:
        await message.answer("❌ Бу канал аллақачон мавжуд!", reply_markup=admin_keyboard())


@router.message(F.text == "➖ Канал ўчириш")
async def admin_remove_channel_start(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    from database import get_channels
    channels = get_channels()
    if not channels:
        await message.answer("📭 Каналлар йўқ!")
        return

    text = "📋 Мавжуд каналлар:\n\n"
    for ch in channels:
        text += f"• {ch['channel_name']} | ИД: {ch['channel_id']}\n"
    text += "\nЎчирмоқчи бўлган канал ИД сини киритинг:"

    await state.set_state(AdminState.waiting_remove_id)
    await message.answer(text)


@router.message(AdminState.waiting_remove_id)
async def admin_remove_channel(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        channel_id = int(message.text.strip())
        result = remove_channel(channel_id)
        await state.clear()
        if result:
            await message.answer("✅ Канал ўчирилди!", reply_markup=admin_keyboard())
        else:
            await message.answer("❌ Канал топилмади!", reply_markup=admin_keyboard())
    except ValueError:
        await message.answer("❌ Нотўғри формат!")


@router.message(F.text == "📋 Каналлар рўйхати")
async def admin_list_channels(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    from database import get_channels
    channels = get_channels()
    if not channels:
        await message.answer("📭 Ҳеч қандай канал қўшилмаган!")
        return

    text = "📋 Каналлар рўйхати:\n\n"
    for i, ch in enumerate(channels, 1):
        text += f"{i}. {ch['channel_name']}\n   🔗 {ch['channel_link']}\n   🆔 {ch['channel_id']}\n\n"
    await message.answer(text)


@router.message(F.text == "👥 Фойдаланувчилар")
async def admin_users_count(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    users = get_all_users()
    await message.answer(f"👥 Жами фойдаланувчилар: {len(users)} та")


@router.message(F.text == "📨 Ҳаммага хабар")
async def admin_broadcast_start(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.set_state(AdminState.waiting_broadcast)
    await message.answer("Барча фойдаланувчиларга юбориладиган хабарни киритинг:")


@router.message(AdminState.waiting_broadcast)
async def admin_broadcast_send(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return

    users = get_all_users()
    count = 0
    for user_id_str in users:
        try:
            await bot.send_message(int(user_id_str), message.text)
            count += 1
        except Exception:
            pass

    await state.clear()
    await message.answer(f"✅ Хабар {count} та фойдаланувчига юборилди!", reply_markup=admin_keyboard())


@router.callback_query(F.data.startswith("lang_"))
async def handle_lang_callback(callback: CallbackQuery, state: FSMContext):
    lang = callback.data.split("_")[1]
    user_id = callback.from_user.id

    current_state = await state.get_state()
    data = await state.get_data()

    if current_state == RegisterState.choosing_lang:
        if data.get("changing_lang"):
            # Just updating language
            user = get_user(user_id)
            if user:
                user["lang"] = lang
                save_user(user_id, user)
            await state.clear()
            await callback.message.delete()
            await callback.answer(f"✅ Тил ўзгартирилди!")
            await callback.message.answer(t(lang, "main_menu"), reply_markup=main_menu_keyboard(lang))
        else:
            await state.update_data(lang=lang)
            await callback.message.delete()
            await callback.answer(t(lang, "lang_selected"))
            await state.set_state(RegisterState.entering_name)
            await callback.message.answer(t(lang, "enter_name"))
