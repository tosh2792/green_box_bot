import os
import logging
import time
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from models import Session, Product, DeliverySlot, Order, OrderItem, Cart, init_db
from datetime import datetime

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

# Константы для ConversationHandler
ADD_NAME, ADD_CATEGORY, ADD_QUANTITY, ADD_PRICE, ADD_PHOTO = range(5)
EDIT_SELECT, EDIT_ACTION, EDIT_QUANTITY, EDIT_PRICE = range(5, 9)
ORDER_ADDRESS, ORDER_PHONE, ORDER_SLOT = range(9, 12)
ADMIN_CANCEL_REASON = 12

# Кэш для блокировки товаров
product_lock_cache = {}
lock_cache_expiry = {}
cache_lock = threading.Lock()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def get_main_keyboard(user_id: int):
    keyboard = [
        [InlineKeyboardButton("💰 Цены", callback_data="prices")],
        [InlineKeyboardButton("🛒 ЗАКАЗАТЬ 🛒", callback_data="order")],
        [InlineKeyboardButton("📦 Мои заказы", callback_data="my_order")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("Внести товар", callback_data="admin_add")],
        [InlineKeyboardButton("Редактировать товары", callback_data="admin_edit")],
        [InlineKeyboardButton("Заказы", callback_data="admin_orders")],
        [InlineKeyboardButton("Слоты доставки", callback_data="admin_slots")]
    ]
    return InlineKeyboardMarkup(keyboard)

def lock_product(product_id, user_id, quantity):
    current_time = time.time()
    expiry_time = current_time + 300

    with cache_lock:
        expired_keys = [key for key, expiry in lock_cache_expiry.items() if expiry < current_time]
        for key in expired_keys:
            if key in product_lock_cache:
                del product_lock_cache[key]
            if key in lock_cache_expiry:
                del lock_cache_expiry[key]

        key = f"{product_id}_{user_id}"
        for cache_key in list(product_lock_cache.keys()):
            if cache_key.startswith(f"{product_id}_") and cache_key != key:
                return False

        product_lock_cache[key] = {
            'product_id': product_id,
            'user_id': user_id,
            'quantity': quantity,
            'locked_at': current_time
        }
        lock_cache_expiry[key] = expiry_time

    return True

def unlock_product(product_id, user_id):
    key = f"{product_id}_{user_id}"
    with cache_lock:
        if key in product_lock_cache:
            del product_lock_cache[key]
        if key in lock_cache_expiry:
            del lock_cache_expiry[key]

def get_locked_quantity(product_id):
    with cache_lock:
        total_locked = 0
        current_time = time.time()

        expired_keys = [key for key, expiry in lock_cache_expiry.items() if expiry < current_time]
        for key in expired_keys:
            if key in product_lock_cache:
                del product_lock_cache[key]
            if key in lock_cache_expiry:
                del lock_cache_expiry[key]

        for key, lock_info in product_lock_cache.items():
            if key.startswith(f"{product_id}_"):
                total_locked += lock_info['quantity']

        return total_locked

def get_available_quantity(product_id):
    session = Session()
    product = session.query(Product).filter(Product.id == product_id).first()
    if not product:
        session.close()
        return 0

    locked = get_locked_quantity(product_id)
    available = max(0, product.quantity - locked)
    session.close()
    return available

# ========== ОСНОВНЫЕ КОМАНДЫ ==========

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    welcome_text = "Здравствуйте!\nЗдесь вы можете заказать свежие овощи, фрукты и ягоды с доставкой до двери!🍅🍉🍒"
    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard(user_id))

async def admin_panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        return
    await update.message.reply_text("👨‍💼 Админ-панель:", reply_markup=get_admin_keyboard())

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    await query.edit_message_text("👨‍💼 Админ-панель:", reply_markup=get_admin_keyboard())

async def back_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return
    await query.edit_message_text("👨‍💼 Админ-панель:", reply_markup=get_admin_keyboard())

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    welcome_text = "Здравствуйте!\nЗдесь вы можете заказать свежие овощи, фрукты и ягоды с доставкой до двери!🍅🍉🍒"
    await query.edit_message_text(welcome_text, reply_markup=get_main_keyboard(user_id))

# ========== ФУНКЦИИ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ==========

async def show_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    session = Session()
    products = session.query(Product).filter(Product.is_available == True, Product.quantity > 0).all()
    session.close()

    if not products:
        await query.edit_message_text("🍃 Товаров пока нет в наличии.", reply_markup=get_main_keyboard(query.from_user.id))
        return

    text = "📋 Актуальные цены:\n\n"
    for p in products:
        emoji = "🥒" if p.category == "Овощи" else "🍉" if p.category == "Фрукты" else "🍒"
        text += f"{emoji} {p.name} — *{p.price_per_kg} р/кг* — Осталось {p.quantity} шт.\n"

    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="back_main")]]
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def show_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("🥒 Овощи", callback_data="cat_Овощи")],
        [InlineKeyboardButton("🍉 Фрукты", callback_data="cat_Фрукты")],
        [InlineKeyboardButton("🍒 Ягоды", callback_data="cat_Ягоды")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_main")]
    ]
    await query.edit_message_text("🌿 Выберите категорию:", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_category_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    category = query.data.replace("cat_", "")
    context.user_data['category'] = category

    session = Session()
    products = session.query(Product).filter(
        Product.category == category,
        Product.is_available == True,
        Product.quantity > 0
    ).all()
    session.close()

    if not products:
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="order")]]
        await query.edit_message_text(f"🍃 В категории '{category}' пока нет товаров.", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    keyboard = []
    category_emoji = "🥒" if category == "Овощи" else "🍉" if category == "Фрукты" else "🍒"
    for p in products:
        keyboard.append([InlineKeyboardButton(f"{category_emoji} {p.name} — {p.price_per_kg} р/кг", callback_data=f"prod_{p.id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="order")])

    await query.edit_message_text(f"✨ Категория: {category}", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.replace("prod_", ""))
    context.user_data['current_product'] = product_id
    context.user_data['selected_qty'] = 1

    session = Session()
    product = session.query(Product).filter(Product.id == product_id).first()
    session.close()

    if not product or product.quantity <= 0:
        await query.edit_message_text("Товар закончился.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Назад", callback_data="order")]]))
        return

    await show_product_card(query, product, 1)

async def show_product_card(query, product, selected_qty):
    category_emoji = "🥒" if product.category == "Овощи" else "🍉" if product.category == "Фрукты" else "🍒"
    available_qty = get_available_quantity(product.id)
    text = f"{category_emoji} *{product.name}*\n\n💰 *Цена: {product.price_per_kg} р/кг*\n📦 Доступно: {available_qty} шт.\n\n⚠️ *Внимание!* Выберите количество товара в штуках.\n\n✅ Выбрано: {selected_qty} шт."
    keyboard = [
        [
            InlineKeyboardButton("1️⃣", callback_data="qty_1"),
            InlineKeyboardButton("2️⃣", callback_data="qty_2"),
            InlineKeyboardButton("3️⃣", callback_data="qty_3"),
            InlineKeyboardButton("4️⃣", callback_data="qty_4")
        ],
        [
            InlineKeyboardButton("➖1", callback_data="qty_minus"),
            InlineKeyboardButton("➕1", callback_data="qty_plus")
        ],
        [InlineKeyboardButton("🛒 В корзину", callback_data="add_to_cart")],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"cat_{product.category}")]
    ]

    if product.photo_id:
        try:
            await query.message.delete()
            await query.message.chat.send_photo(
                photo=product.photo_id,
                caption=text,
                parse_mode='Markdown',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except:
            await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def handle_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_id = context.user_data.get('current_product')
    if not product_id:
        return

    session = Session()
    product = session.query(Product).filter(Product.id == product_id).first()
    session.close()

    if not product:
        return

    current_qty = context.user_data.get('selected_qty', 1)

    if query.data == "qty_minus":
        current_qty = max(1, current_qty - 1)
    elif query.data == "qty_plus":
        available_qty = get_available_quantity(product_id)
        current_qty = min(available_qty, current_qty + 1)
    elif query.data.startswith("qty_"):
        available_qty = get_available_quantity(product_id)
        current_qty = min(available_qty, int(query.data.replace("qty_", "")))

    context.user_data['selected_qty'] = current_qty
    category_emoji = "🥒" if product.category == "Овощи" else "🍉" if product.category == "Фрукты" else "🍒"
    available_qty = get_available_quantity(product_id)
    text = f"{category_emoji} *{product.name}*\n\n💰 *Цена: {product.price_per_kg} р/кг*\n📦 Доступно: {available_qty} шт.\n\n⚠️ *Внимание!* Выберите количество товара в штуках.\n\n✅ Выбрано: {current_qty} шт."
    keyboard = [
        [
            InlineKeyboardButton("1️⃣", callback_data="qty_1"),
            InlineKeyboardButton("2️⃣", callback_data="qty_2"),
            InlineKeyboardButton("3️⃣", callback_data="qty_3"),
            InlineKeyboardButton("4️⃣", callback_data="qty_4")
        ],
        [
            InlineKeyboardButton("➖1", callback_data="qty_minus"),
            InlineKeyboardButton("➕1", callback_data="qty_plus")
        ],
        [InlineKeyboardButton("🛒 В корзину", callback_data="add_to_cart")],
        [InlineKeyboardButton("🔙 Назад", callback_data=f"cat_{product.category}")]
    ]

    try:
        await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
    except:
        try:
            await query.edit_message_caption(caption=text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))
        except:
            pass

async def add_to_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_id = context.user_data.get('current_product')
    qty = context.user_data.get('selected_qty', 1)
    user_id = query.from_user.id

    if not lock_product(product_id, user_id, qty):
        await query.answer("Товар временно недоступен. Попробуйте позже!", show_alert=True)
        return

    session = Session()
    product = session.query(Product).filter(Product.id == product_id).first()

    if product:
        existing = session.query(Cart).filter(Cart.user_id == user_id, Cart.product_id == product_id).first()
        if existing:
            existing.quantity += qty
        else:
            cart_item = Cart(
                user_id=user_id,
                product_id=product_id,
                product_name=product.name,
                quantity=qty,
                price_per_kg=product.price_per_kg
            )
            session.add(cart_item)
        session.commit()
    session.close()

    await query.answer("Товар добавлен в корзину!")
    await show_cart(query, user_id)

async def show_cart(query, user_id):
    session = Session()
    cart_items = session.query(Cart).filter(Cart.user_id == user_id).all()
    session.close()

    if not cart_items:
        keyboard = [[InlineKeyboardButton("« Назад", callback_data="back_main")]]
        await query.edit_message_text("Ваша корзина пуста.", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    text = "🛒 Ваша корзина:\n\n"
    for item in cart_items:
        text += f"• {item.product_name} x{item.quantity} шт.\n"
    text += "\nℹ️ Итоговая стоимость будет рассчитана при доставке."

    keyboard = [
        [InlineKeyboardButton("✅ Оформить заказ", callback_data="checkout")],
        [InlineKeyboardButton("🔄 Продолжить покупки", callback_data="order")],
        [InlineKeyboardButton("🗑 Очистить корзину", callback_data="clear_cart")]
    ]

    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except:
        await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def clear_cart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Корзина очищена!")
    user_id = query.from_user.id
    session = Session()
    cart_items = session.query(Cart).filter(Cart.user_id == user_id).all()

    for item in cart_items:
        unlock_product(item.product_id, user_id)

    session.query(Cart).filter(Cart.user_id == user_id).delete()
    session.commit()
    session.close()

    keyboard = [[InlineKeyboardButton("« Назад", callback_data="back_main")]]
    await query.edit_message_text("Корзина очищена.", reply_markup=InlineKeyboardMarkup(keyboard))

async def checkout_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = Session()
    cart_items = session.query(Cart).filter(Cart.user_id == user_id).all()

    for item in cart_items:
        available_qty = get_available_quantity(item.product_id)
        if available_qty < item.quantity:
            product = session.query(Product).filter(Product.id == item.product_id).first()
            if product:
                await query.edit_message_text(
                    f"❌ Товар '{product.name}' больше не доступен в количестве {item.quantity} шт.\n"
                    f"Доступно только {available_qty} шт.\n\n"
                    "Пожалуйста, обновите корзину.",
                    reply_markup=get_main_keyboard(user_id)
                )
                session.close()
                return ConversationHandler.END

    session.close()

    if not cart_items:
        await query.edit_message_text("Корзина пуста.", reply_markup=get_main_keyboard(query.from_user.id))
        return ConversationHandler.END

    await query.edit_message_text("📍 Введите адрес доставки:")
    return ORDER_ADDRESS

async def get_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['address'] = update.message.text
    await update.message.reply_text("📞 Введите номер телефона для связи:")
    return ORDER_PHONE

async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['phone'] = update.message.text
    session = Session()
    slots = session.query(DeliverySlot).filter(DeliverySlot.is_active == True).all()
    session.close()

    if not slots:
        await update.message.reply_text("Нет доступных слотов доставки.", reply_markup=get_main_keyboard(update.effective_user.id))
        return ConversationHandler.END

    keyboard = []
    for slot in slots:
        keyboard.append([InlineKeyboardButton(f"{slot.start_hour}:00 - {slot.end_hour}:00", callback_data=f"slot_{slot.id}")])

    await update.message.reply_text(
        "🕐 Выберите время доставки:\n\n"
        "⚠️ *Внимание!* Доставка осуществляется только на *СЕГОДНЯШНИЙ ДЕНЬ*!",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ORDER_SLOT

async def send_order_notification_to_admin(context: ContextTypes.DEFAULT_TYPE, order: Order):
    if ADMIN_ID == 0:
        logger.warning("ADMIN_ID не установлен, уведомление не отправлено")
        return

    try:
        text = f"🆕 *Новый заказ!* #{order.id}\n\n"
        text += f"👤 Пользователь: {order.user_name}\n"
        text += f"📞 Телефон: {order.phone}\n"
        text += f"📍 Адрес: {order.address}\n"
        text += f"🕐 Время доставки: {order.delivery_slot}\n"
        text += f"📅 Дата создания: {order.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        text += f"📋 Статус: *Ожидает подтверждения*\n\n"
        text += "*Товары:*\n"

        session = Session()
        order_items = session.query(OrderItem).filter(OrderItem.order_id == order.id).all()

        for item in order_items:
            text += f"• {item.product_name} x{item.quantity}\n"

        session.close()

        keyboard = [
            [
                InlineKeyboardButton("✅ Подтвердить", callback_data=f"admin_accept_{order.id}"),
                InlineKeyboardButton("❌ Отменить", callback_data=f"admin_cancel_{order.id}")
            ],
            [InlineKeyboardButton("📋 Все заказы", callback_data="admin_orders")]
        ]

        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=text,
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления администратору: {e}")

async def select_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    slot_id = int(query.data.replace("slot_", ""))
    user_id = query.from_user.id

    session = Session()
    slot = session.query(DeliverySlot).filter(DeliverySlot.id == slot_id).first()
    cart_items = session.query(Cart).filter(Cart.user_id == user_id).all()

    order = Order(
        user_id=user_id,
        user_name=query.from_user.full_name,
        delivery_slot=f"{slot.start_hour}:00 - {slot.end_hour}:00",
        address=context.user_data.get('address'),
        phone=context.user_data.get('phone'),
        status='pending'
    )
    session.add(order)
    session.flush()

    for item in cart_items:
        order_item = OrderItem(
            order_id=order.id,
            product_id=item.product_id,
            product_name=item.product_name,
            quantity=item.quantity,
            price_per_kg=item.price_per_kg
        )
        session.add(order_item)
        unlock_product(item.product_id, user_id)

    session.query(Cart).filter(Cart.user_id == user_id).delete()
    session.commit()

    # Отправляем уведомление администратору
    await send_order_notification_to_admin(context, order)

    session.close()

    # Отправляем сообщение клиенту о том, что заказ ожидает подтверждения
    await query.edit_message_text(
        f"✅ *Заказ #{order.id} оформлен!*\n\n"
        f"📍 *Адрес:* {context.user_data.get('address')}\n"
        f"📞 *Телефон:* {context.user_data.get('phone')}\n"
        f"🕐 *Доставка:* {slot.start_hour}:00 - {slot.end_hour}:00\n\n"
        "📋 *Ваш заказ ожидает подтверждения администратором.*\n"
        "Вы получите уведомление, когда заказ будет подтвержден.\n\n"
        "⏳ Обычно это занимает не более 15 минут.",
        parse_mode='Markdown',
        reply_markup=get_main_keyboard(user_id)
    )

    # Также отправляем отдельное сообщение для уверенности
    await context.bot.send_message(
        chat_id=user_id,
        text="📋 *Ваш заказ ожидает подтверждения администратором.*\n"
             "Вы получите уведомление, когда заказ будет подтвержден.",
        parse_mode='Markdown'
    )

    return ConversationHandler.END

async def show_my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    session = Session()
    orders = session.query(Order).filter(Order.user_id == user_id).order_by(Order.created_at.desc()).limit(10).all()

    if not orders:
        session.close()
        keyboard = [[InlineKeyboardButton("« Назад", callback_data="back_main")]]
        await query.edit_message_text("У вас нет заказов.", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    text = "📦 *ВАШИ ЗАКАЗЫ*\n\n"

    for order in orders:
        status_emoji = {
            'pending': '⏳',
            'active': '✅',
            'on_the_way': '🚗',
            'delivered': '🎉',
            'cancelled': '❌'
        }.get(order.status, '❓')

        status_text = {
            'pending': 'Ожидает подтверждения',
            'active': 'Подтвержден',
            'on_the_way': 'Курьер направляется',
            'delivered': 'Доставлен',
            'cancelled': 'Отменен'
        }.get(order.status, 'Неизвестно')

        text += f"{status_emoji} *Заказ #{order.id}*\n"
        text += f"📅 *Дата:* {order.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        text += f"📋 *Статус:* {status_text}\n"
        text += f"🕐 *Доставка:* {order.delivery_slot}\n"
        text += f"📍 *Адрес:* {order.address}\n"

        if order.status == 'on_the_way' and order.on_the_way_at:
            text += f"🚗 *Вышел:* {order.on_the_way_at.strftime('%H:%M')}\n"

        if order.status == 'delivered' and order.delivered_at:
            text += f"✅ *Доставлен:* {order.delivered_at.strftime('%H:%M')}\n"

        if order.status == 'cancelled' and order.cancel_reason:
            text += f"📝 *Причина:* {order.cancel_reason}\n"

        text += "─" * 20 + "\n"

    session.close()

    keyboard = [[InlineKeyboardButton("« Назад", callback_data="back_main")]]
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

# ========== ФУНКЦИИ ДЛЯ ДОБАВЛЕНИЯ ТОВАРА (ИСПРАВЛЕННЫЕ) ==========

async def admin_add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    session = Session()
    products = session.query(Product).order_by(Product.name).all()
    session.close()

    if not products:
        # Нет товаров, начинаем стандартный процесс
        await query.edit_message_text("Введите наименование товара:")
        return ADD_NAME

    # Предлагаем выбрать из существующих товаров
    keyboard = []
    for product in products:
        keyboard.append([InlineKeyboardButton(f"{product.name} ({product.category})", callback_data=f"draft_{product.id}")])

    keyboard.append([InlineKeyboardButton("➕ Новый товар", callback_data="new_product")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_admin")])

    await query.edit_message_text(
        "Выберите товар из существующих или создайте новый:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ConversationHandler.END

async def select_product_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    product_id = int(query.data.replace("draft_", ""))

    session = Session()
    product = session.query(Product).filter(Product.id == product_id).first()
    session.close()

    if not product:
        await query.answer("Товар не найден!", show_alert=True)
        return

    # Сохраняем данные товара в контекст
    context.user_data['new_product_name'] = product.name
    context.user_data['new_product_category'] = product.category

    # Отправляем сообщение с запросом количества
    await query.message.reply_text(
        f"Товар: {product.name}\n"
        f"Категория: {product.category}\n\n"
        "Введите количество в штуках:"
    )
    return ADD_QUANTITY

async def new_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Очищаем старые данные
    context.user_data.pop('new_product_name', None)
    context.user_data.pop('new_product_category', None)

    await query.message.reply_text("Введите наименование товара:")
    return ADD_NAME

async def admin_get_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_product_name'] = update.message.text

    keyboard = [
        [InlineKeyboardButton("🥒 Овощи", callback_data="newcat_Овощи")],
        [InlineKeyboardButton("🍉 Фрукты", callback_data="newcat_Фрукты")],
        [InlineKeyboardButton("🍒 Ягоды", callback_data="newcat_Ягоды")]
    ]

    await update.message.reply_text(
        "Выберите категорию:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return ADD_CATEGORY

async def admin_get_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Сохраняем категорию
    category = query.data.replace("newcat_", "")
    context.user_data['new_product_category'] = category

    await query.edit_message_text(
        f"Товар: {context.user_data['new_product_name']}\n"
        f"Категория: {category}\n\n"
        "Введите количество в штуках:"
    )
    return ADD_QUANTITY

async def admin_get_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        quantity = int(update.message.text)
        if quantity <= 0:
            await update.message.reply_text("Количество должно быть положительным числом. Введите снова:")
            return ADD_QUANTITY

        context.user_data['new_product_quantity'] = quantity
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите целое число:")
        return ADD_QUANTITY

    await update.message.reply_text("Введите цену за 1 кг (в рублях):")
    return ADD_PRICE

async def admin_get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text)
        if price <= 0:
            await update.message.reply_text("Цена должна быть положительным числом. Введите снова:")
            return ADD_PRICE

        context.user_data['new_product_price'] = price
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите число (например: 150.50):")
        return ADD_PRICE

    await update.message.reply_text("Отправьте фото товара (или напишите 'пропустить'):")
    return ADD_PHOTO

async def admin_get_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_id = None

    if update.message.photo:
        photo_id = update.message.photo[-1].file_id
    elif update.message.text and update.message.text.lower() == 'пропустить':
        photo_id = None
    else:
        await update.message.reply_text("Пожалуйста, отправьте фото или напишите 'пропустить':")
        return ADD_PHOTO

    # Создаем товар в базе данных
    name = context.user_data.get('new_product_name')
    category = context.user_data.get('new_product_category')
    quantity = context.user_data.get('new_product_quantity')
    price = context.user_data.get('new_product_price')

    if not all([name, category, quantity, price]):
        await update.message.reply_text(
            "Ошибка при создании товара. Не все данные заполнены.",
            reply_markup=get_admin_keyboard()
        )
        return ConversationHandler.END

    session = Session()

    # Проверяем, не существует ли уже товар с таким названием
    existing_product = session.query(Product).filter(
        Product.name.ilike(name),
        Product.category == category
    ).first()

    if existing_product:
        # Обновляем существующий товар
        existing_product.quantity += quantity
        existing_product.price_per_kg = price
        if photo_id:
            existing_product.photo_id = photo_id
        existing_product.is_available = True

        message = f"✅ Товар обновлен:\n{name}\nКоличество добавлено: +{quantity} шт.\nНовая цена: {price} р/кг"
    else:
        # Создаем новый товар
        product = Product(
            name=name,
            category=category,
            quantity=quantity,
            price_per_kg=price,
            photo_id=photo_id,
            is_available=True
        )
        session.add(product)
        message = f"✅ Товар добавлен:\n{name} - *{price} р/кг*\nКоличество: {quantity} шт."

    session.commit()
    session.close()

    # Очищаем контекст
    context.user_data.pop('new_product_name', None)
    context.user_data.pop('new_product_category', None)
    context.user_data.pop('new_product_quantity', None)
    context.user_data.pop('new_product_price', None)

    await update.message.reply_text(
        message,
        parse_mode='Markdown',
        reply_markup=get_admin_keyboard()
    )

    return ConversationHandler.END

# ========== ФУНКЦИИ АДМИНИСТРАТОРА ДЛЯ УПРАВЛЕНИЯ ЗАКАЗАМИ ==========

async def admin_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    session = Session()
    orders = session.query(Order).filter(
        Order.status.in_(['pending', 'active', 'on_the_way'])
    ).order_by(
        Order.status.desc(),
        Order.created_at.desc()
    ).all()

    if not orders:
        session.close()
        keyboard = [
            [InlineKeyboardButton("❌ Отмененные заказы", callback_data="admin_cancelled")],
            [InlineKeyboardButton("🚚 Доставленные заказы", callback_data="admin_delivered_list")],
            [InlineKeyboardButton("🔙 Назад", callback_data="back_admin")]
        ]
        await query.edit_message_text("Нет активных заказов.", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # Группируем заказы по статусу
    pending_orders = [o for o in orders if o.status == 'pending']
    active_orders = [o for o in orders if o.status == 'active']
    on_the_way_orders = [o for o in orders if o.status == 'on_the_way']

    text = "📦 *ЗАКАЗЫ*\n\n"

    # Форматируем каждый заказ отдельно
    for order in pending_orders + active_orders + on_the_way_orders:
        text += format_order_for_admin(order)
        text += "\n"

    session.close()

    # Создаем кнопки управления для каждого заказа
    keyboard = []

    # Для ожидающих заказов
    for order in pending_orders:
        keyboard.append([
            InlineKeyboardButton(f"✅ Подтвердить #{order.id}", callback_data=f"admin_accept_{order.id}")
        ])
        keyboard.append([
            InlineKeyboardButton(f"❌ Отменить #{order.id}", callback_data=f"admin_cancel_{order.id}")
        ])
        keyboard.append([])  # Пустая строка для разделения

    # Для активных заказов
    for order in active_orders:
        keyboard.append([
            InlineKeyboardButton(f"🚗 Направляюсь #{order.id}", callback_data=f"admin_on_the_way_{order.id}")
        ])
        keyboard.append([
            InlineKeyboardButton(f"❌ Отменить #{order.id}", callback_data=f"admin_cancel_{order.id}")
        ])
        keyboard.append([])

    # Для заказов "в пути"
    for order in on_the_way_orders:
        keyboard.append([
            InlineKeyboardButton(f"🎉 Доставлено #{order.id}", callback_data=f"admin_delivered_{order.id}")
        ])
        keyboard.append([])

    keyboard.append([InlineKeyboardButton("❌ Отмененные заказы", callback_data="admin_cancelled")])
    keyboard.append([InlineKeyboardButton("🚚 Доставленные заказы", callback_data="admin_delivered_list")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_admin")])

    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

def format_order_for_admin(order):
    """Форматирует информацию о заказе для администратора"""
    session = Session()
    order_items = session.query(OrderItem).filter(OrderItem.order_id == order.id).all()
    session.close()

    status_emoji = {
        'pending': '⏳',
        'active': '✅',
        'on_the_way': '🚗',
        'delivered': '🎉',
        'cancelled': '❌'
    }.get(order.status, '❓')

    status_text = {
        'pending': 'Ожидает подтверждения',
        'active': 'Подтвержден',
        'on_the_way': 'В пути',
        'delivered': 'Доставлен',
        'cancelled': 'Отменен'
    }.get(order.status, 'Неизвестно')

    text = f"{status_emoji} *Заказ #{order.id}*\n"
    text += f"👤 *Клиент:* {order.user_name}\n"
    text += f"📅 *Дата:* {order.created_at.strftime('%d.%m.%Y %H:%M')}\n"
    text += f"📍 *Адрес:* {order.address}\n"
    text += f"📞 *Телефон:* {order.phone}\n"
    text += f"🕐 *Доставка:* {order.delivery_slot}\n"
    text += f"📋 *Статус:* {status_text}\n"

    if order_items:
        text += "🛒 *Товары:*\n"
        for item in order_items:
            text += f"  • {item.product_name} x{item.quantity}\n"

    text += "─" * 30 + "\n"
    return text

async def admin_accept_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    order_id = int(query.data.replace("admin_accept_", ""))

    session = Session()
    order = session.query(Order).filter(Order.id == order_id).first()

    if not order:
        await query.answer("Заказ не найден!", show_alert=True)
        session.close()
        return

    # Обновляем статус заказа
    order.status = 'active'
    session.commit()

    # Уведомляем пользователя
    try:
        user_text = f"✅ *Ваш заказ #{order.id} подтвержден!*\n\n"
        user_text += f"📍 Адрес: {order.address}\n"
        user_text += f"📞 Телефон: {order.phone}\n"
        user_text += f"🕐 Доставка: {order.delivery_slot}\n\n"
        user_text += "Курьер свяжется с вами перед выездом.\n"
        user_text += "Спасибо за покупку! 🍅🍉🍒"

        await context.bot.send_message(
            chat_id=order.user_id,
            text=user_text,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления пользователю: {e}")

    session.close()

    await query.answer("Заказ подтвержден!", show_alert=True)

    # Обновляем сообщение с заказами
    await admin_orders(update, context)

async def admin_on_the_way(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Направляюсь'"""
    query = update.callback_query
    await query.answer()

    order_id = int(query.data.replace("admin_on_the_way_", ""))

    session = Session()
    order = session.query(Order).filter(Order.id == order_id).first()

    if not order:
        await query.answer("Заказ не найден!", show_alert=True)
        session.close()
        return

    # Обновляем статус заказа
    order.status = 'on_the_way'
    order.on_the_way_at = datetime.now()
    session.commit()

    # Уведомляем пользователя
    try:
        user_text = f"🚗 *Курьер направляется к вам!*\n\n"
        user_text += f"📦 Заказ #{order.id}\n"
        user_text += f"📍 Адрес: {order.address}\n"
        user_text += f"📞 Телефон курьера: +7 (XXX) XXX-XX-XX\n\n"
        user_text += "⏳ *Ожидайте курьера в течение 10-15 минут!*\n\n"
        user_text += "Спасибо за терпение! 🍅🍉🍒"

        await context.bot.send_message(
            chat_id=order.user_id,
            text=user_text,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления пользователю: {e}")

    session.close()

    await query.answer("Клиент уведомлен, что курьер направляется!", show_alert=True)

    # Обновляем сообщение с заказами
    await admin_orders(update, context)

async def admin_mark_delivered(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопки 'Доставлено'"""
    query = update.callback_query
    await query.answer()

    order_id = int(query.data.replace("admin_delivered_", ""))

    session = Session()
    order = session.query(Order).filter(Order.id == order_id).first()

    if not order:
        await query.answer("Заказ не найден!", show_alert=True)
        session.close()
        return

    # Обновляем статус заказа
    order.status = 'delivered'
    order.delivered_at = datetime.now()
    session.commit()

    # Уведомляем пользователя
    try:
        user_text = f"🎉 *Ваш заказ доставлен успешно!*\n\n"
        user_text += f"📦 Заказ #{order.id}\n"
        user_text += f"📍 Адрес: {order.address}\n"
        user_text += f"🕐 Время доставки: {order.delivered_at.strftime('%H:%M')}\n\n"
        user_text += "🙏 *Спасибо за покупку!*\n\n"
        user_text += "Надеемся, вам понравились наши свежие овощи и фрукты! 🍅🍉🍒\n"
        user_text += "Ждем вас снова! 💚"

        await context.bot.send_message(
            chat_id=order.user_id,
            text=user_text,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления пользователю: {e}")

    session.close()

    await query.answer("Заказ отмечен как доставленный!", show_alert=True)

    # Обновляем сообщение с заказами
    await admin_orders(update, context)

async def admin_start_cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    order_id = int(query.data.replace("admin_cancel_", ""))
    context.user_data['cancel_order_id'] = order_id

    await query.edit_message_text("📝 Введите причину отмены заказа:")
    return ADMIN_CANCEL_REASON

async def admin_finish_cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reason = update.message.text
    order_id = context.user_data.get('cancel_order_id')

    if not order_id:
        await update.message.reply_text("Ошибка: не найден ID заказа", reply_markup=get_admin_keyboard())
        return ConversationHandler.END

    session = Session()
    order = session.query(Order).filter(Order.id == order_id).first()

    if not order:
        await update.message.reply_text("Заказ не найден", reply_markup=get_admin_keyboard())
        session.close()
        return ConversationHandler.END

    user_id = order.user_id
    old_status = order.status

    order.status = 'cancelled'
    order.cancel_reason = reason
    order.cancelled_at = datetime.now()

    # Если заказ был активен, возвращаем товары на склад
    if old_status in ['active', 'on_the_way']:
        for item in order.items:
            product = session.query(Product).filter(Product.id == item.product_id).first()
            if product:
                product.quantity += item.quantity

    session.commit()
    session.close()

    try:
        user_text = f"❌ *Ваш заказ #{order_id} отменен*\n\n"
        user_text += f"📝 *Причина отмены:* {reason}\n"
        user_text += f"🕐 *Время отмены:* {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
        user_text += "Если у вас есть вопросы, свяжитесь с нами."

        await context.bot.send_message(
            chat_id=user_id,
            text=user_text,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления пользователю: {e}")

    await update.message.reply_text(
        f"✅ Заказ #{order_id} отменен. Пользователь уведомлен.",
        reply_markup=get_admin_keyboard()
    )

    await admin_orders(update, context)
    return ConversationHandler.END

async def admin_cancelled_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    session = Session()
    cancelled_orders = session.query(Order).filter(Order.status == 'cancelled').order_by(Order.cancelled_at.desc()).limit(10).all()

    if not cancelled_orders:
        session.close()
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_orders")]]
        await query.edit_message_text("Нет отмененных заказов.", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    text = "❌ *Отмененные заказы:*\n\n"
    for order in cancelled_orders:
        text += f"❌ *Заказ #{order.id}*\n"
        text += f"👤 {order.user_name}\n"
        text += f"📅 Создан: {order.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        text += f"🕐 Отменен: {order.cancelled_at.strftime('%d.%m.%Y %H:%M')}\n"
        if order.cancel_reason:
            text += f"📝 Причина: {order.cancel_reason}\n"
        text += "─" * 30 + "\n\n"

    session.close()

    keyboard = [
        [InlineKeyboardButton("📋 Активные заказы", callback_data="admin_orders")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_admin")]
    ]

    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_delivered_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    session = Session()
    delivered_orders = session.query(Order).filter(Order.status == 'delivered').order_by(Order.delivered_at.desc()).limit(10).all()

    if not delivered_orders:
        session.close()
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="admin_orders")]]
        await query.edit_message_text("Нет доставленных заказов.", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    text = "🎉 *Доставленные заказы:*\n\n"
    for order in delivered_orders:
        text += f"🎉 *Заказ #{order.id}*\n"
        text += f"👤 {order.user_name}\n"
        text += f"📅 Создан: {order.created_at.strftime('%d.%m.%Y %H:%M')}\n"
        text += f"✅ Доставлен: {order.delivered_at.strftime('%d.%m.%Y %H:%M')}\n"
        text += f"📍 Адрес: {order.address}\n"
        text += "─" * 30 + "\n\n"

    session.close()

    keyboard = [
        [InlineKeyboardButton("📋 Активные заказы", callback_data="admin_orders")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_admin")]
    ]

    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_slots(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    session = Session()
    slots = session.query(DeliverySlot).order_by(DeliverySlot.start_hour).all()
    session.close()

    keyboard = []
    for slot in slots:
        status = "✅" if slot.is_active else "❌"
        keyboard.append([InlineKeyboardButton(f"{status} {slot.start_hour}:00 - {slot.end_hour}:00", callback_data=f"toggleslot_{slot.id}")])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="back_admin")])

    await query.edit_message_text("Слоты доставки (нажмите для переключения):", reply_markup=InlineKeyboardMarkup(keyboard))

async def toggle_slot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    slot_id = int(query.data.replace("toggleslot_", ""))

    session = Session()
    slot = session.query(DeliverySlot).filter(DeliverySlot.id == slot_id).first()
    slot.is_active = not slot.is_active
    session.commit()
    session.close()

    await admin_slots(update, context)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    with cache_lock:
        keys_to_remove = []
        for key, lock_info in product_lock_cache.items():
            if lock_info['user_id'] == user_id:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del product_lock_cache[key]
            if key in lock_cache_expiry:
                del lock_cache_expiry[key]

    await update.message.reply_text("Операция отменена.", reply_markup=get_admin_keyboard() if update.effective_user.id == ADMIN_ID else get_main_keyboard(update.effective_user.id))
    return ConversationHandler.END

# ========== ГЛАВНАЯ ФУНКЦИЯ ==========

def main():
    init_db()

    application = Application.builder().token(TOKEN).build()

    # ConversationHandler для добавления товара
    add_product_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_product_start, pattern="^admin_add$")],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_name)],
            ADD_CATEGORY: [CallbackQueryHandler(admin_get_category, pattern="^newcat_")],
            ADD_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_quantity)],
            ADD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_get_price)],
            ADD_PHOTO: [MessageHandler(filters.PHOTO | filters.TEXT, admin_get_photo)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    # Обработчики для выбора черновиков
    application.add_handler(CallbackQueryHandler(select_product_draft, pattern="^draft_"))
    application.add_handler(CallbackQueryHandler(new_product, pattern="^new_product$"))

    # ConversationHandler для отмены заказа администратором
    admin_cancel_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_start_cancel_order, pattern="^admin_cancel_\\d+$")],
        states={
            ADMIN_CANCEL_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_finish_cancel_order)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    # ConversationHandler для оформления заказа
    checkout_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(checkout_start, pattern="^checkout$")],
        states={
            ORDER_ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_address)],
            ORDER_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            ORDER_SLOT: [CallbackQueryHandler(select_slot, pattern="^slot_")]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    # Основные команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel_command))

    # ConversationHandlers
    application.add_handler(add_product_handler)
    application.add_handler(admin_cancel_handler)
    application.add_handler(checkout_handler)

    # CallbackQueryHandlers для пользователя
    application.add_handler(CallbackQueryHandler(show_prices, pattern="^prices$"))
    application.add_handler(CallbackQueryHandler(show_categories, pattern="^order$"))
    application.add_handler(CallbackQueryHandler(show_category_products, pattern="^cat_"))
    application.add_handler(CallbackQueryHandler(show_product, pattern="^prod_"))
    application.add_handler(CallbackQueryHandler(handle_quantity, pattern="^qty_"))
    application.add_handler(CallbackQueryHandler(add_to_cart, pattern="^add_to_cart$"))
    application.add_handler(CallbackQueryHandler(clear_cart, pattern="^clear_cart$"))
    application.add_handler(CallbackQueryHandler(show_my_orders, pattern="^my_order$"))
    application.add_handler(CallbackQueryHandler(back_to_main, pattern="^back_main$"))

    # CallbackQueryHandlers для администратора
    application.add_handler(CallbackQueryHandler(show_admin_panel, pattern="^admin_panel$"))
    application.add_handler(CallbackQueryHandler(back_to_admin, pattern="^back_admin$"))
    application.add_handler(CallbackQueryHandler(admin_orders, pattern="^admin_orders$"))
    application.add_handler(CallbackQueryHandler(admin_slots, pattern="^admin_slots$"))
    application.add_handler(CallbackQueryHandler(toggle_slot, pattern="^toggleslot_"))
    application.add_handler(CallbackQueryHandler(admin_accept_order, pattern="^admin_accept_\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_on_the_way, pattern="^admin_on_the_way_\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_mark_delivered, pattern="^admin_delivered_\\d+$"))
    application.add_handler(CallbackQueryHandler(admin_cancelled_orders, pattern="^admin_cancelled$"))
    application.add_handler(CallbackQueryHandler(admin_delivered_list, pattern="^admin_delivered_list$"))

    print("✅ Бот запущен!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()