import logging
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from datetime import datetime

from config import ADMIN_IDS
from database import load_db, save_db, get_all_users, get_channels, add_channel, remove_channel
from exchange_config import CURRENCIES, DEFAULT_RATES, get_currency_by_id
from referral_service import (
    award_referral_bonus_for_order,
    format_money,
    get_referral_settings,
    ensure_user_referral_fields,
    admin_adjust_referral_bonus,
    get_pending_withdrawals,
    get_withdraw_request,
    approve_withdraw_request,
    reject_withdraw_request,
)

log = logging.getLogger(__name__)
admin_config_router = Router()

class ACS(StatesGroup):
    man_to        = State()
    man_rate      = State()
    man_min       = State()
    man_max       = State()
    man_comm      = State()
    man_field_val = State()
    card_val      = State()
    ch_id         = State()
    ch_link       = State()
    ch_name       = State()
    ch_del        = State()
    broadcast     = State()
    ref_set_val   = State()
    ref_uid       = State()
    ref_amount    = State()



def is_admin(uid): return uid in ADMIN_IDS

def get_settings():
    return load_db().get("rate_settings", {})

def save_settings(s):
    db = load_db(); db["rate_settings"] = s; save_db(db)

def get_cards():
    return load_db().get("payment_cards", {
        "uzcard": "8600 1666 0393 7029",
        "humo":   "9860 0000 0000 0000"
    })

def save_cards(c):
    db = load_db(); db["payment_cards"] = c; save_db(db)

def get_manual():
    return load_db().get("manual_rates", {})

def save_manual(r):
    db = load_db(); db["manual_rates"] = r; save_db(db)

def get_orders():
    return load_db().get("orders", {})

def set_order_status(oid, status):
    db = load_db()
    order = db.get("orders", {}).get(str(oid))
    if not order:
        return None
    order["status"] = status
    order["updated_at"] = datetime.now().strftime("%d.%m.%Y %H:%M")
    save_db(db)
    return order

def get_transaction_channel_id():
    db = load_db()
    configured = db.get("transaction_channel_id")
    if configured is not None:
        try:
            return int(configured)
        except Exception:
            pass
    channels = db.get("channels", [])
    if channels:
        try:
            return int(channels[0].get("channel_id"))
        except Exception:
            return channels[0].get("channel_id")
    return None

def cname(cid):
    c = get_currency_by_id(cid)
    return c["name"] if c else cid

def fmt(v):
    try:
        if isinstance(v, float) and v != int(v):
            return f"{v:.6f}".rstrip("0").rstrip(".")
        return str(int(v))
    except:
        return str(v)

def build_channel_transaction_text(order: dict, bot_title: str, bot_username: str) -> str:
    recv_amount = order.get("recv_amount", order.get("receive_amount", 0))
    ts = order.get("updated_at") or order.get("created_at", "—")
    return (
        f"{bot_title} [ BOT ]\n"
        f"Obmen orqali bot - {bot_username}\n"
        f"ID: {order.get('order_id', '—')}\n"
        f"👤 :{order.get('full_name', '—')}\n"
        f"🔁 :{order.get('from_name', '—')}➡️{order.get('to_name', '—')}\n"
        f"🕐 Status:✅\n"
        f"📝 :{ts}\n"
        f"💱 :{fmt(recv_amount)} {order.get('to_name', '')}"
    )


async def send_transaction_to_channel(bot: Bot, order: dict):
    channel_id = get_transaction_channel_id()
    if not channel_id:
        return
    bot_title = "Exchange"
    bot_username = "@bot"
    try:
        me = await bot.get_me()
        bot_title = me.full_name or me.first_name or bot_title
        if me.username:
            bot_username = f"@{me.username}"
    except Exception:
        pass
    text = build_channel_transaction_text(order, bot_title, bot_username)
    try:
        await bot.send_message(channel_id, text)
    except Exception as e:
        log.warning(f"Channel send xato: {e}")


async def safe_edit_admin_message(cb: CallbackQuery, text: str):
    try:
        await cb.message.edit_text(text)
        return
    except Exception:
        pass
    try:
        await cb.message.edit_caption(caption=text)
        return
    except Exception:
        pass
    try:
        await cb.message.answer(text)
    except Exception:
        pass


def ref_admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚙️ Sozlamalar", callback_data="REFADM_SETTINGS")],
        [InlineKeyboardButton(text="➕ Bonus qo'shish", callback_data="REFADM_ADD")],
        [InlineKeyboardButton(text="➖ Bonus ayirish", callback_data="REFADM_SUB")],
        [InlineKeyboardButton(text="📋 Кутилаётган ечишлар", callback_data="REFADM_PENDING")],
        [InlineKeyboardButton(text="🔙 Орқага", callback_data="REFADM_BACK")],
    ])


def ref_settings_kb() -> InlineKeyboardMarkup:
    s = get_referral_settings()
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🎁 Буюртма учун бонус: {format_money(s.get('bonus_per_completed_order', 0))} сўм",
            callback_data="REFSET_bonus_per_completed_order",
        )],
        [InlineKeyboardButton(
            text=f"💸 Min yechish: {format_money(s.get('min_withdraw', 0))} сўм",
            callback_data="REFSET_min_withdraw",
        )],
        [InlineKeyboardButton(text="🔙 Орқага", callback_data="REFADM_HOME")],
    ])


def pending_withdraw_kb(items: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for req in items[:15]:
        rows.append([InlineKeyboardButton(
            text=f"#{req.get('id')} | {req.get('user_id')} | {format_money(req.get('amount', 0))} сўм",
            callback_data=f"REFWD_VIEW_{req.get('id')}",
        )])
    rows.append([InlineKeyboardButton(text="🔙 Орқага", callback_data="REFADM_HOME")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ref_withdraw_action_kb(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Тасдиқлаш", callback_data=f"REFWD_OK_{req_id}")],
        [InlineKeyboardButton(text="❌ Бекор қилиш", callback_data=f"REFWD_NO_{req_id}")],
        [InlineKeyboardButton(text="🔙 Орқага", callback_data="REFADM_PENDING")],
    ])


def referral_stats_text() -> str:
    db = load_db()
    users = db.get("users", {})
    total_bonus = 0.0
    total_pending = 0.0
    referrals = 0
    changed = False
    for u in users.values():
        if ensure_user_referral_fields(u):
            changed = True
        try:
            total_bonus += float(u.get("referral_bonus", 0.0))
            total_pending += float(u.get("referral_pending", 0.0))
            if u.get("referred_by"):
                referrals += 1
        except Exception:
            pass
    if changed:
        save_db(db)
    pending_count = len([w for w in db.get("referral_withdrawals", {}).values() if w.get("status") == "pending"])
    return (
        "🎁 Реферал бонус boshqaruvi\n\n"
        f"👥 Userlar: {len(users)}\n"
        f"🔗 Ulangan referallar: {referrals}\n"
        f"💼 Bonus balanslar jami: {format_money(total_bonus)} сўм\n"
        f"⏳ Pending yechish jami: {format_money(total_pending)} сўм\n"
        f"📋 Pending сўровlar: {pending_count}"
    )


def adjust_mode_title(mode: str) -> str:
    return "qo'shish" if mode == "add" else "ayirish"

def admin_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="💹 Мануал курслар"),   KeyboardButton(text="💳 Тўлов карталари")],
        [KeyboardButton(text="📦 Буюртмалар"),       KeyboardButton(text="📢 Каналлар")],
        [KeyboardButton(text="👥 Фойдаланувчилар"), KeyboardButton(text="📨 Broadcast")],
        [KeyboardButton(text="🎁 Реферал бонус")],
        [KeyboardButton(text="🔙 Орқага")],
    ], resize_keyboard=True)

def xkb():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="❌ Бекор")]],
        resize_keyboard=True
    )




#  /admin

@admin_config_router.message(Command("admin"))
async def admin_enter(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.clear()
    db   = load_db()
    live = db.get("live_rates", {})
    last = db.get("last_rate_update", "Yangilanmagan")
    await message.answer(
        f"👨‍💼 Admin panel\n\n"
        f"📊 Live kurslar: {len(live)} ta\n"
        f"🕐 Охирги янгиланиш: {last}",
        reply_markup=admin_kb()
    )




#  💹 MANUAL KURSLAR

def manual_list_kb():
    manual = get_manual()
    rows   = []
    for key, info in manual.items():
        p = key.split(":")
        if len(p) == 2:
            rows.append([InlineKeyboardButton(
                text=f"💱 {cname(p[0])} ➡️ {cname(p[1])} | {info.get('rate','?')}",
                callback_data=f"MV_{key}"
            )])
    rows.append([InlineKeyboardButton(text="➕ Yangi qo'shish", callback_data="MADD")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def manual_detail_kb(key):
    info = get_manual().get(key, {})
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"💱 Kurs: {info.get('rate','—')}",           callback_data=f"ME_{key}__rate")],
        [InlineKeyboardButton(text=f"⬇️ Min: {fmt(info.get('min',0))}",          callback_data=f"ME_{key}__min")],
        [InlineKeyboardButton(text=f"⬆️ Max: {fmt(info.get('max',0))}",          callback_data=f"ME_{key}__max")],
        [InlineKeyboardButton(text=f"💸 Комиссия: {info.get('commission',1)}%",  callback_data=f"ME_{key}__commission")],
        [InlineKeyboardButton(text="🗑 Ўчириш",                                callback_data=f"MDEL_{key}")],
        [InlineKeyboardButton(text="🔙 Орқага",                                   callback_data="MBACK")],
    ])

def cur_select_kb(prefix, exclude=""):
    rows = []
    row  = []
    for cur in CURRENCIES:
        if cur["id"] == exclude: continue
        row.append(InlineKeyboardButton(text=cur["name"], callback_data=f"{prefix}{cur['id']}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row: rows.append(row)
    rows.append([InlineKeyboardButton(text="❌ Бекор", callback_data="MBACK")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@admin_config_router.message(F.text == "💹 Мануал курслар")
async def admin_manual(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.clear()
    manual = get_manual()
    await message.answer(
        f"💹 Мануал курслар ({len(manual)} ta)\n"
        f"Криптовалюта курсларини бошқариш.",
        reply_markup=manual_list_kb()
    )

@admin_config_router.callback_query(F.data == "MBACK")
async def mback(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    await state.clear()
    manual = get_manual()
    await cb.message.edit_text(f"💹 Мануал курслар ({len(manual)} ta)", reply_markup=manual_list_kb())
    await cb.answer()

@admin_config_router.callback_query(F.data.startswith("MV_"))
async def mv_view(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    key  = cb.data[3:]
    p    = key.split(":")
    info = get_manual().get(key, {})
    await cb.message.edit_text(
        f"💱 {cname(p[0])} ➡️ {cname(p[1])}\n\n"
        f"Kurs: {info.get('rate','—')}\n"
        f"Min:  {fmt(info.get('min',0))}\n"
        f"Max:  {fmt(info.get('max',0))}\n"
        f"Комиссия: {info.get('commission',1)}%",
        reply_markup=manual_detail_kb(key)
    )
    await cb.answer()

@admin_config_router.callback_query(F.data.startswith("MDEL_"))
async def mdel(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    key    = cb.data[5:]
    manual = get_manual()
    if key in manual:
        del manual[key]; save_manual(manual)
    await cb.message.edit_text("✅ Ўчирилди!", reply_markup=manual_list_kb())
    await cb.answer()

@admin_config_router.callback_query(F.data.startswith("ME_"))
async def me_field(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    raw   = cb.data[3:]
    key, field = raw.split("__", 1)
    info  = get_manual().get(key, {})
    cur_v = info.get(field, "—")
    await state.set_state(ACS.man_field_val)
    await state.update_data(man_key=key, man_field=field)
    labels = {"rate": "Kurs", "min": "Minimal", "max": "Maksimal", "commission": "Комиссия (%)"}
    await cb.message.edit_text(
        f"✏️ {labels.get(field, field)}\nҲозирги: {cur_v}\n\nЯнги қиймат:"
    )
    await cb.answer()

@admin_config_router.message(ACS.man_field_val)
async def me_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    if message.text == "❌ Бекор":
        await state.clear(); await message.answer("❌", reply_markup=admin_kb()); return
    data  = await state.get_data()
    key, field = data["man_key"], data["man_field"]
    try:
        val = float(message.text.replace(",", "."))
        if field in ("min", "max"): val = int(val)
    except:
        await message.answer("❌ Рақам киритинг:"); return
    manual = get_manual()
    if key not in manual: manual[key] = {}
    manual[key][field] = val
    save_manual(manual)
    await state.clear()
    await message.answer(f"✅ Янгиланди: {fmt(val)}", reply_markup=admin_kb())

@admin_config_router.callback_query(F.data == "MADD")
async def madd(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    await state.update_data(man_step="from")
    await cb.message.edit_text("➕ 1-валюта (FROM):", reply_markup=cur_select_kb("MFROM_"))
    await cb.answer()

@admin_config_router.callback_query(F.data.startswith("MFROM_"))
async def mfrom(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    fid = cb.data[6:]
    await state.update_data(man_from_id=fid)
    await cb.message.edit_text(
        f"✅ FROM: {cname(fid)}\n\n2-валюта (TO):",
        reply_markup=cur_select_kb("MTO_", exclude=fid)
    )
    await cb.answer()

@admin_config_router.callback_query(F.data.startswith("MTO_"))
async def mto(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    tid  = cb.data[4:]
    data = await state.get_data()
    await state.update_data(man_to_id=tid)
    await state.set_state(ACS.man_rate)
    await cb.message.edit_text(
        f"✅ {cname(data['man_from_id'])} ➡️ {cname(tid)}\n\n"
        f"💱 Курсни киритинг (1 {cname(data['man_from_id'])} = ? {cname(tid)}):"
    )
    await cb.answer()

@admin_config_router.message(ACS.man_rate)
async def mrate(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    if message.text == "❌ Бекор":
        await state.clear(); await message.answer("❌", reply_markup=admin_kb()); return
    try: v = float(message.text.replace(",", "."))
    except: await message.answer("❌ Raqam:"); return
    await state.update_data(man_rate_v=v)
    await state.set_state(ACS.man_min)
    await message.answer(f"✅ Kurs: {v}\n\n⬇️ Минимал миқдор:")

@admin_config_router.message(ACS.man_min)
async def mmin(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try: v = float(message.text.replace(",", "."))
    except: await message.answer("❌ Raqam:"); return
    await state.update_data(man_min_v=v)
    await state.set_state(ACS.man_max)
    await message.answer(f"✅ Min: {v}\n\n⬆️ Максимал миқдор:")

@admin_config_router.message(ACS.man_max)
async def mmax(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try: v = float(message.text.replace(",", "."))
    except: await message.answer("❌ Raqam:"); return
    await state.update_data(man_max_v=v)
    await state.set_state(ACS.man_comm)
    await message.answer(f"✅ Max: {v}\n\n💸 Комиссия (%):")

@admin_config_router.message(ACS.man_comm)
async def mcomm(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try: v = float(message.text.replace(",", "."))
    except: await message.answer("❌ Raqam:"); return
    data   = await state.get_data()
    key    = f"{data['man_from_id']}:{data['man_to_id']}"
    manual = get_manual()
    manual[key] = {
        "rate": data["man_rate_v"], "min": data["man_min_v"],
        "max": data["man_max_v"],   "commission": v,
    }
    save_manual(manual)
    await state.clear()
    rate_v    = data['man_rate_v']
    from_name = cname(data['man_from_id'])
    to_name   = cname(data['man_to_id'])
    if rate_v > 0 and rate_v < 1:
        rate_disp = f"1 {to_name} = {round(1 / rate_v):,} {from_name}"
    else:
        rate_disp = f"1 {from_name} = {rate_v:,} {to_name}"
    await message.answer(
        f"✅ Қўшилди!\n"
        f"💱 {from_name} ➡️ {to_name}\n"
        f"Kurs: {rate_disp}\n"
        f"Min: {fmt(data['man_min_v'])} | Max: {fmt(data['man_max_v'])} | Комиссия: {v}%",
        reply_markup=admin_kb()
    )



#  💳 TO'LOV KARTALARI

def cards_kb():
    cards = get_cards()
    rows  = []
    for cur in CURRENCIES:
        num  = cards.get(cur["id"], "—")
        icon = "💳" if cur["type"] == "card" else "📲"
        rows.append([InlineKeyboardButton(
            text=f"{icon} {cur['name']}: {num}",
            callback_data=f"CARD_{cur['id']}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@admin_config_router.message(F.text == "💳 Тўлов карталари")
async def admin_cards(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.clear()
    await message.answer("💳 Тўлов карталари / Walletlar:", reply_markup=cards_kb())

@admin_config_router.callback_query(F.data.startswith("CARD_"))
async def card_edit(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    cid   = cb.data[5:]
    cur   = get_currency_by_id(cid)
    cur_v = get_cards().get(cid, "—")
    t     = "карта рақами" if cur and cur["type"] == "card" else "wallet манзили"
    await state.set_state(ACS.card_val)
    await state.update_data(card_cid=cid)
    await cb.message.edit_text(
        f"{'💳' if cur and cur['type']=='card' else '📲'} {cur['name'] if cur else cid}\n\n"
        f"Hozirgi: <code>{cur_v}</code>\n\n"
        f"Янги {t} киритинг:",
        parse_mode="HTML"
    )
    await cb.answer()

@admin_config_router.message(ACS.card_val)
async def card_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    if message.text == "❌ Бекор":
        await state.clear(); await message.answer("❌", reply_markup=admin_kb()); return
    data  = await state.get_data()
    cid   = data["card_cid"]
    cards = get_cards()
    cards[cid] = message.text.strip()
    save_cards(cards)
    await state.clear()
    cur = get_currency_by_id(cid)
    await message.answer(
        f"✅ {cur['name'] if cur else cid} янгиланди!\n<code>{message.text.strip()}</code>",
        reply_markup=admin_kb(), parse_mode="HTML"
    )




#  📦 BUYURTMALAR

STATUS = {
    "pending_payment": "⏳ Кутилмоқда",
    "receipt_sent":    "🧾 Чек юборилган",
    "completed":       "✅ Якунланган",
    "cancelled":       "❌ Бекор",
}

def orders_kb():
    orders  = get_orders()
    pending = sum(1 for o in orders.values() if o.get("status") in ("pending_payment","receipt_sent"))
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"⏳ Kutilayotgan ({pending})", callback_data="ORD_f_pending")],
        [InlineKeyboardButton(text="🧾 Чек юборилган",           callback_data="ORD_f_receipt")],
        [InlineKeyboardButton(text="✅ Якунланган",               callback_data="ORD_f_done")],
        [InlineKeyboardButton(text="❌ Бекор qilingan",            callback_data="ORD_f_cancelled")],
        [InlineKeyboardButton(text="📋 Barchasi",                  callback_data="ORD_f_all")],
    ])

def ord_action_kb(oid, status):
    rows = []
    if status in ("pending_payment","receipt_sent"):
        rows.append([InlineKeyboardButton(text="✅ Тасдиқлаш", callback_data=f"OCONF_{oid}")])
        rows.append([InlineKeyboardButton(text="❌ Rad etish",  callback_data=f"OREJ_{oid}")])
    rows.append([InlineKeyboardButton(text="🔙 Орқага", callback_data="ORD_BACK")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@admin_config_router.message(F.text == "📦 Буюртмалар")
async def admin_orders(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.clear()
    orders = get_orders()
    p = sum(1 for o in orders.values() if o.get("status") in ("pending_payment","receipt_sent"))
    await message.answer(f"📦 Буюртмалар\nJami: {len(orders)} | ⏳: {p}", reply_markup=orders_kb())

@admin_config_router.callback_query(F.data == "ORD_BACK")
async def ord_back(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    orders = get_orders()
    p = sum(1 for o in orders.values() if o.get("status") in ("pending_payment","receipt_sent"))
    await cb.message.edit_text(f"📦 Буюртмалар\nJami: {len(orders)} | ⏳: {p}", reply_markup=orders_kb())
    await cb.answer()

@admin_config_router.callback_query(F.data.startswith("ORD_f_"))
async def ord_list(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    filt = cb.data[6:]
    fmap = {
        "pending":   ["pending_payment"],
        "receipt":   ["receipt_sent"],
        "done":      ["completed"],
        "cancelled": ["cancelled"],
        "all":       list(STATUS.keys()),
    }
    allowed  = fmap.get(filt, [])
    filtered = sorted(
        [o for o in get_orders().values() if o.get("status") in allowed],
        key=lambda x: x.get("order_id", 0), reverse=True
    )
    if not filtered:
        await cb.answer("📭 Yo'q", show_alert=True); return
    rows = []
    for o in filtered[:15]:
        icon = {"pending_payment":"⏳","receipt_sent":"🧾","completed":"✅","cancelled":"❌"}.get(o.get("status"),"❓")
        rows.append([InlineKeyboardButton(
            text=f"{icon} #{o['order_id']} | {o.get('from_name','?')}→{o.get('to_name','?')} | {fmt(o.get('send_amount',0))}",
            callback_data=f"ORD_v_{o['order_id']}"
        )])
    rows.append([InlineKeyboardButton(text="🔙", callback_data="ORD_BACK")])
    await cb.message.edit_text(f"📋 {len(filtered)} ta:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cb.answer()

@admin_config_router.callback_query(F.data.startswith("ORD_v_"))
async def ord_view(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    oid = int(cb.data[6:])
    o   = get_orders().get(str(oid))
    if not o: await cb.answer("❌", show_alert=True); return
    text = (
        f"📦 Buyurtma #{o['order_id']}\n"
        f"📅 {o.get('created_at','—')}\n"
        f"🔖 {STATUS.get(o.get('status',''),'—')}\n\n"
        f"👤 {o.get('full_name','—')} (@{o.get('username','—')})\n"
        f"🆔 {o.get('user_id','—')}\n\n"
        f"🔄 {o.get('from_name','?')} ➡️ {o.get('to_name','?')}\n"
        f"⬆️ Беради: {fmt(o.get('send_amount',0))} {o.get('from_name','')}\n"
        f"⬇️ Олади: {fmt(o.get('recv_amount', o.get('receive_amount',0)))} {o.get('to_name','')}\n\n"
        f"💳 {o.get('from_name','')}: <code>{o.get('sender_card','—')}</code>\n"
        f"💳 {o.get('to_name','')}: <code>{o.get('receiver_card','—')}</code>"
    )
    await cb.message.edit_text(text, reply_markup=ord_action_kb(oid, o.get("status","")), parse_mode="HTML")
    await cb.answer()

@admin_config_router.callback_query(F.data.startswith("OCONF_"))
async def oconf(cb: CallbackQuery, bot: Bot):
    if not is_admin(cb.from_user.id): return
    oid = int(cb.data[6:])
    order = get_orders().get(str(oid))
    if not order:
        await cb.answer("❌ Буюртма топилмади", show_alert=True)
        return
    if order.get("status") == "completed":
        await cb.answer("Бу буюртма аллақачон тасдиқланган", show_alert=True)
        return
    updated = set_order_status(oid, "completed")
    final_order = updated or order
    uid = final_order.get("user_id")
    if uid:
        try:
            from datetime import datetime
            now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
            bot_info = await bot.get_me()
            bot_username = f"@{bot_info.username}"

            from_id   = final_order.get("from_id", "")
            to_id     = final_order.get("to_id", "")
            from_name = final_order.get("from_name", "—")
            to_name   = final_order.get("to_name", "—")
            from_flag = "🇺🇿" if from_id in ("uzcard", "humo") else "🇺🇸"
            to_flag   = "🇺🇿" if to_id   in ("uzcard", "humo") else "🇺🇸"

            def _fmt(num):
                try:
                    if isinstance(num, float) and num != int(num):
                        return f"{num:.6f}".rstrip("0").rstrip(".")
                    return str(int(num))
                except:
                    return str(num)

            user_text = (
                f"🆔 Алмашув: {oid}\n"
                f"🔀: {from_name} ➡️ {to_name}\n"
                f"{from_flag} {from_name}: {final_order.get('sender_card', '—')}\n"
                f"💸: {_fmt(final_order.get('send_amount', 0))} {from_name}\n\n"
                f"{to_flag} {to_name}: {final_order.get('receiver_card', '—')}\n"
                f"{_fmt(final_order.get('recv_amount', 0))} {to_name}\n\n"
                f"📌 Тўлов: ✅ Админ томонидан тасдиқланган.\n"
                f"📆 Алмашув санаси: {final_order.get('created_at', '—')}\n"
                f"📅 Тасдиқланган сана: {now_str}\n"
                f"😊 Ҳурмат билан: {bot_username}"
            )
            await bot.send_message(uid, user_text)
        except Exception:
            pass
    bonus_info = award_referral_bonus_for_order(oid)
    if bonus_info:
        ref_uid = bonus_info.get("referrer_id")
        if ref_uid:
            try:
                await bot.send_message(
                    ref_uid,
                    f"🎁 Реферал бонусi қўшилди!\n"
                    f"💰 +{format_money(bonus_info.get('bonus_amount', 0))} сўм\n"
                    f"💼 Yangi balans: {format_money(bonus_info.get('new_balance', 0))} сўм"
                )
            except Exception:
                pass
    await send_transaction_to_channel(bot, final_order)
    await safe_edit_admin_message(cb, f"✅ Buyurtma #{oid} tasdiqlandi.")
    await cb.answer("✅")

@admin_config_router.callback_query(F.data.startswith("OREJ_"))
async def orej(cb: CallbackQuery, bot: Bot):
    if not is_admin(cb.from_user.id): return
    oid = int(cb.data[5:])
    order = get_orders().get(str(oid))
    if not order:
        await cb.answer("❌ Буюртма топилмади", show_alert=True)
        return
    if order.get("status") == "cancelled":
        await cb.answer("Бу буюртма аллақачон бекор қилинган", show_alert=True)
        return
    updated = set_order_status(oid, "cancelled")
    uid = (updated or order).get("user_id")
    if uid:
        try:
            await bot.send_message(uid, f"❌ Буюртма #{oid} бекор қилинди.\n\nСаволлар учун админ билан боғланинг.")
        except Exception:
            pass
    await safe_edit_admin_message(cb, f"❌ Буюртма #{oid} бекор қилинди.")
    await cb.answer("❌")



#  📢 KANALLAR

@admin_config_router.message(F.text == "🎁 Реферал бонус")
async def admin_referral_menu(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.clear()
    await message.answer(referral_stats_text(), reply_markup=ref_admin_kb())


@admin_config_router.callback_query(F.data == "REFADM_BACK")
@admin_config_router.callback_query(F.data == "REFADM_HOME")
async def refadm_home(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    await state.clear()
    await cb.message.edit_text(referral_stats_text(), reply_markup=ref_admin_kb())
    await cb.answer()


@admin_config_router.callback_query(F.data == "REFADM_SETTINGS")
async def refadm_settings(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    await state.clear()
    await cb.message.edit_text("⚙️ Referral sozlamalari:", reply_markup=ref_settings_kb())
    await cb.answer()


@admin_config_router.callback_query(F.data.startswith("REFSET_"))
async def refset_edit(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    field = cb.data[7:]
    if field not in ("bonus_per_completed_order", "min_withdraw"):
        await cb.answer("❌ Noma'lum maydon", show_alert=True)
        return
    settings = get_referral_settings()
    current = settings.get(field, 0)
    label = "Буюртма учун бонус" if field == "bonus_per_completed_order" else "Минимал ечиш"
    await state.set_state(ACS.ref_set_val)
    await state.update_data(ref_field=field)
    await cb.message.edit_text(
        f"⚙️ {label}\n\nҲозирги қиймат: {format_money(current)} сўм\n\nЯнги қийматни киритинг:"
    )
    await cb.answer()


@admin_config_router.message(ACS.ref_set_val)
async def refset_save(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    if (message.text or "").strip() == "❌ Бекор":
        await state.clear()
        await message.answer("❌ Бекор qilindi", reply_markup=admin_kb())
        return
    data = await state.get_data()
    field = data.get("ref_field")
    if field not in ("bonus_per_completed_order", "min_withdraw"):
        await state.clear()
        await message.answer("❌ Сессия тугади, қайтадан киритинг.", reply_markup=admin_kb())
        return
    try:
        value = float((message.text or "").replace(",", ".").strip())
        if value < 0:
            raise ValueError
    except Exception:
        await message.answer("❌ Мусбат рақам киритинг:")
        return

    db = load_db()
    settings = get_referral_settings(db)
    settings[field] = round(value, 2)
    db["referral_settings"] = settings
    save_db(db)

    await state.clear()
    await message.answer("✅ Реферал созламаси янгиланди.", reply_markup=admin_kb())


@admin_config_router.callback_query(F.data == "REFADM_ADD")
@admin_config_router.callback_query(F.data == "REFADM_SUB")
async def refadm_adjust_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    mode = "add" if cb.data.endswith("ADD") else "sub"
    await state.set_state(ACS.ref_uid)
    await state.update_data(ref_mode=mode)
    await cb.message.answer(
        f"Фойдаланувчи ID ни киритинг (бонусни {adjust_mode_title(mode)}):",
        reply_markup=xkb()
    )
    await cb.answer()


@admin_config_router.message(ACS.ref_uid)
async def refadm_adjust_uid(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    if (message.text or "").strip() == "❌ Бекор":
        await state.clear()
        await message.answer("❌ Бекор qilindi", reply_markup=admin_kb())
        return
    try:
        uid = int((message.text or "").strip())
    except Exception:
        await message.answer("❌ Фойдаланувчи ID рақам бўлиши керак:")
        return
    await state.update_data(ref_uid=uid)
    await state.set_state(ACS.ref_amount)
    await message.answer("Миқдорни киритинг (масалан: 5000):")


@admin_config_router.message(ACS.ref_amount)
async def refadm_adjust_amount(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    if (message.text or "").strip() == "❌ Бекор":
        await state.clear()
        await message.answer("❌ Бекор qilindi", reply_markup=admin_kb())
        return
    data = await state.get_data()
    mode = data.get("ref_mode", "add")
    uid = data.get("ref_uid")
    if uid is None:
        await state.clear()
        await message.answer("❌ Сессия тугади, қайтадан киритинг.", reply_markup=admin_kb())
        return
    try:
        amount = float((message.text or "").replace(",", ".").strip())
    except Exception:
        await message.answer("❌ Рақам киритинг:")
        return

    user, err = admin_adjust_referral_bonus(uid, amount, mode)
    if err == "not_found":
        await message.answer("❌ Фойдаланувчи топилмади.")
        return
    if err == "bad_amount":
        await message.answer("❌ Миқдор мусбат бўлиши керак.")
        return
    if err == "insufficient":
        await message.answer("❌ Фойдаланувчида бу миқдорни айириш учун бонус етарли эмас.")
        return

    await state.clear()
    new_balance = format_money((user or {}).get("referral_bonus", 0.0))
    await message.answer(
        f"✅ Bonus yangilandi.\nUser: {uid}\nBalans: {new_balance} сўм",
        reply_markup=admin_kb()
    )


@admin_config_router.callback_query(F.data == "REFADM_PENDING")
async def refadm_pending(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    await state.clear()
    items = get_pending_withdrawals(15)
    if not items:
        await cb.message.edit_text(
            "📭 Pending referral yechish сўровlari yo'q.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔙 Орқага", callback_data="REFADM_HOME")]
            ])
        )
        await cb.answer()
        return
    await cb.message.edit_text(
        f"📋 Кутилаётган реферал ечишлар: {len(items)} ta",
        reply_markup=pending_withdraw_kb(items)
    )
    await cb.answer()


@admin_config_router.callback_query(F.data.startswith("REFWD_VIEW_"))
async def refwd_view(cb: CallbackQuery):
    if not is_admin(cb.from_user.id): return
    try:
        req_id = int(cb.data[11:])
    except Exception:
        await cb.answer("❌ Xato", show_alert=True)
        return
    req = get_withdraw_request(req_id)
    if not req:
        await cb.answer("❌ Сўров топилмади", show_alert=True)
        return
    users = get_all_users()
    user = users.get(str(req.get("user_id")), {})
    full_name = f"{user.get('name', '')} {user.get('surname', '')}".strip() or "—"
    phone = user.get("phone", "—")
    status = req.get("status", "—")
    text = (
        f"💸 Referral yechish сўровi #{req_id}\n\n"
        f"👤 {full_name}\n"
        f"🆔 {req.get('user_id')}\n"
        f"📞 {phone}\n\n"
        f"💰 Miqdor: {format_money(req.get('amount', 0))} сўм\n"
        f"💳 Karta: {req.get('card', '—')}\n"
        f"📅 {req.get('created_at', '—')}\n"
        f"📌 Status: {status}"
    )
    await cb.message.edit_text(text, reply_markup=ref_withdraw_action_kb(req_id))
    await cb.answer()


@admin_config_router.callback_query(F.data.startswith("REFWD_OK_"))
async def refwd_approve(cb: CallbackQuery, bot: Bot):
    if not is_admin(cb.from_user.id): return
    try:
        req_id = int(cb.data[9:])
    except Exception:
        await cb.answer("❌ Xato", show_alert=True)
        return
    req, user, err = approve_withdraw_request(req_id, cb.from_user.id)
    if err == "not_found":
        await cb.answer("❌ Сўров топилмади", show_alert=True)
        return
    if err == "already_processed":
        await cb.answer("⚠️ Сўров аллақачон қайта ишланган", show_alert=True)
        return
    if req and req.get("user_id"):
        try:
            await bot.send_message(
                req.get("user_id"),
                f"✅ Реферал бонус yechish сўрови тасдиқланди.\n"
                f"💸 {format_money(req.get('amount', 0))} сўм"
            )
        except Exception:
            pass
    await cb.message.edit_text(f"✅ Referral сўров #{req_id} tasdiqlandi.")
    await cb.answer("✅")


@admin_config_router.callback_query(F.data.startswith("REFWD_NO_"))
async def refwd_reject(cb: CallbackQuery, bot: Bot):
    if not is_admin(cb.from_user.id): return
    try:
        req_id = int(cb.data[9:])
    except Exception:
        await cb.answer("❌ Xato", show_alert=True)
        return
    req, user, err = reject_withdraw_request(req_id, cb.from_user.id)
    if err == "not_found":
        await cb.answer("❌ Сўров топилмади", show_alert=True)
        return
    if err == "already_processed":
        await cb.answer("⚠️ Сўров аллақачон қайта ишланган", show_alert=True)
        return
    if req and req.get("user_id"):
        try:
            await bot.send_message(
                req.get("user_id"),
                "❌ Реферал бонус yechish сўровi bekor qilindi.\nMiqdor balansga qaytarildi."
            )
        except Exception:
            pass
    await cb.message.edit_text(f"❌ Referral сўров #{req_id} bekor qilindi.")
    await cb.answer("❌")


@admin_config_router.message(F.text == "📢 Каналлар")
async def admin_channels(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.clear()
    chs = get_channels()
    text = "📢 Каналлар:\n\n" + "\n".join(
        f"{i}. {ch['channel_name']} | {ch['channel_link']} | {ch['channel_id']}"
        for i,ch in enumerate(chs,1)
    ) if chs else "📭 Каналлар yo'q."
    await message.answer(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Қўшиш", callback_data="CH_ADD")],
        [InlineKeyboardButton(text="➖ Ўчириш", callback_data="CH_DEL")],
    ]))

@admin_config_router.callback_query(F.data == "CH_ADD")
async def ch_add(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    await state.set_state(ACS.ch_id)
    await cb.message.edit_text("Канал ID ни киритинг (масалан: -1001234567890):")
    await cb.answer()

@admin_config_router.message(ACS.ch_id)
async def ch_id_val(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try:
        cid = int(message.text.strip())
        await state.update_data(ch_id=cid)
        await state.set_state(ACS.ch_link)
        await message.answer("Kanal havolasi (https://t.me/...):")
    except: await message.answer("❌ Рақам бўлиши керак:")

@admin_config_router.message(ACS.ch_link)
async def ch_link_val(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.update_data(ch_link=message.text.strip())
    await state.set_state(ACS.ch_name)
    await message.answer("Kanal nomi:")

@admin_config_router.message(ACS.ch_name)
async def ch_name_val(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    data = await state.get_data()
    ok   = add_channel(data["ch_id"], data["ch_link"], message.text.strip())
    await state.clear()
    await message.answer(
        f"✅ {message.text.strip()} қўшилди!" if ok else "❌ Аллақачон мавжуд!",
        reply_markup=admin_kb()
    )

@admin_config_router.callback_query(F.data == "CH_DEL")
async def ch_del_start(cb: CallbackQuery, state: FSMContext):
    if not is_admin(cb.from_user.id): return
    chs = get_channels()
    if not chs: await cb.answer("📭 Yo'q", show_alert=True); return
    text = "Ўчириш учун канал ID ни киритинг:\n\n" + "\n".join(
        f"• {ch['channel_name']} → {ch['channel_id']}" for ch in chs
    )
    await state.set_state(ACS.ch_del)
    await cb.message.edit_text(text)
    await cb.answer()

@admin_config_router.message(ACS.ch_del)
async def ch_del_val(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    try:
        ok = remove_channel(int(message.text.strip()))
        await state.clear()
        await message.answer("✅ Ўчирилди!" if ok else "❌ Топилмади!", reply_markup=admin_kb())
    except: await message.answer("❌ Рақам бўлиши керак:")



#  👥 FOYDALANUVCHILAR

@admin_config_router.message(F.text == "👥 Фойдаланувчилар")
async def admin_users(message: Message):
    if not is_admin(message.from_user.id): return
    await message.answer(f"👥 Ro'yxatdan o'tganlar: {len(get_all_users())} ta")



#  📨 BROADCAST

@admin_config_router.message(F.text == "📨 Broadcast")
async def broadcast_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.set_state(ACS.broadcast)
    await message.answer("Хабарни киритинг:", reply_markup=xkb())

@admin_config_router.message(ACS.broadcast)
async def broadcast_send(message: Message, state: FSMContext, bot: Bot):
    if not is_admin(message.from_user.id): return
    if message.text == "❌ Бекор":
        await state.clear(); await message.answer("❌", reply_markup=admin_kb()); return
    users = get_all_users()
    ok = 0
    for uid in users:
        try: await bot.send_message(int(uid), message.text); ok += 1
        except: pass
    await state.clear()
    await message.answer(f"✅ {ok}/{len(users)} та юборилди!", reply_markup=admin_kb())

@admin_config_router.message(F.text == "🔙 Орқага")
async def admin_back(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.clear()
    from keyboards import main_menu_keyboard
    from database import get_user
    user = get_user(message.from_user.id)
    lang = user.get("lang", "uz") if user else "uz"
    await message.answer("🏠 Asosiy menyu", reply_markup=main_menu_keyboard(lang))
