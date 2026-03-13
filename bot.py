import os
import logging
import asyncio
from datetime import datetime
from typing import Dict, List, Tuple
import random
import string

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters,
    ConversationHandler, CallbackQueryHandler, ContextTypes
)
from supabase import create_client, Client

# ==================== CONFIGURATION ====================
TOKEN = os.environ.get("BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ADMIN_ID = int(os.environ.get("ADMIN_ID"))  # Telegram user ID of the admin
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # e.g. https://your-app.onrender.com/webhook

# Initialize Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== DATABASE TABLES ====================
# We'll use raw SQL to create tables (run once manually in Supabase SQL editor)
"""
-- Users table
CREATE TABLE users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    joined_at TIMESTAMP DEFAULT NOW()
);

-- Coupons table
CREATE TABLE coupons (
    id SERIAL PRIMARY KEY,
    type TEXT NOT NULL,  -- '500', '1000', '2000', '4000'
    code TEXT NOT NULL UNIQUE,
    used BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Orders table
CREATE TABLE orders (
    id SERIAL PRIMARY KEY,
    order_id TEXT UNIQUE,
    user_id BIGINT,
    coupon_type TEXT,
    quantity INTEGER,
    amount_paid DECIMAL(10,2),
    status TEXT DEFAULT 'pending',  -- 'pending', 'approved', 'declined'
    created_at TIMESTAMP DEFAULT NOW(),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Delivered codes table (to keep track of which codes were given to which order)
CREATE TABLE delivered_codes (
    order_id TEXT,
    code TEXT,
    PRIMARY KEY (order_id, code)
);

-- Prices table
CREATE TABLE prices (
    coupon_type TEXT,
    quantity INTEGER,  -- 1,5,10,20
    price DECIMAL(10,2),
    PRIMARY KEY (coupon_type, quantity)
);

-- QR code image (store as text, base64 or URL)
CREATE TABLE qr_image (
    id INTEGER PRIMARY KEY DEFAULT 1,
    image_data TEXT  -- base64 encoded image or file_id
);

-- Insert default prices (you can change via admin panel)
INSERT INTO prices (coupon_type, quantity, price) VALUES
    ('500', 1, 10.00),
    ('500', 5, 45.00),
    ('500', 10, 80.00),
    ('500', 20, 150.00),
    ('1000', 1, 18.00),
    ('1000', 5, 85.00),
    ('1000', 10, 160.00),
    ('1000', 20, 300.00),
    ('2000', 1, 35.00),
    ('2000', 5, 160.00),
    ('2000', 10, 300.00),
    ('2000', 20, 550.00),
    ('4000', 1, 60.00),
    ('4000', 5, 280.00),
    ('4000', 10, 500.00),
    ('4000', 20, 950.00)
ON CONFLICT DO NOTHING;

-- Default QR (placeholder)
INSERT INTO qr_image (id, image_data) VALUES (1, '') ON CONFLICT DO NOTHING;
"""

# ==================== HELPER FUNCTIONS ====================

async def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def generate_order_id() -> str:
    return "ORD" + ''.join(random.choices(string.digits, k=14))

async def get_stock(coupon_type: str) -> int:
    result = supabase.table("coupons").select("*", count="exact").eq("type", coupon_type).eq("used", False).execute()
    return result.count

async def get_price(coupon_type: str, quantity: int) -> float:
    # Determine price bracket based on quantity
    if quantity <= 1:
        qty_key = 1
    elif quantity <= 5:
        qty_key = 5
    elif quantity <= 10:
        qty_key = 10
    else:
        qty_key = 20
    result = supabase.table("prices").select("price").eq("coupon_type", coupon_type).eq("quantity", qty_key).execute()
    if result.data:
        return float(result.data[0]["price"])
    return 0.0

async def get_qr_image() -> str:
    result = supabase.table("qr_image").select("image_data").eq("id", 1).execute()
    if result.data and result.data[0]["image_data"]:
        return result.data[0]["image_data"]
    return None

async def update_qr_image(image_data: str):
    supabase.table("qr_image").upsert({"id": 1, "image_data": image_data}).execute()

# ==================== USER STATES ====================
MAIN_MENU, TERMS, SELECT_TYPE, SELECT_QUANTITY, CUSTOM_QUANTITY, WAIT_PAYMENT = range(6)

# ==================== REPLY KEYBOARDS ====================
def main_menu_keyboard():
    keyboard = [
        [KeyboardButton("🛒 Buy Vouchers")],
        [KeyboardButton("📦 My Orders")],
        [KeyboardButton("📜 Disclaimer")],
        [KeyboardButton("🆘 Support")],
        [KeyboardButton("📢 Our Channels")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def admin_menu_keyboard():
    keyboard = [
        [KeyboardButton("➕ Add Coupon"), KeyboardButton("➖ Remove Coupon")],
        [KeyboardButton("📊 Stock")],
        [KeyboardButton("🎁 Get A Free Code")],
        [KeyboardButton("💰 Change Prices")],
        [KeyboardButton("📢 Broadcast")],
        [KeyboardButton("🔄 Update QR")],
        [KeyboardButton("📋 Last 10 Purchases")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ==================== HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Save user to DB if not exists
    supabase.table("users").upsert({
        "user_id": user.id,
        "username": user.username,
        "first_name": user.first_name
    }).execute()
    if await is_admin(user.id):
        await update.message.reply_text(
            f"Welcome Admin {user.first_name}!\nChoose an option:",
            reply_markup=admin_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            f"Welcome to Coupon Shopping Bot, {user.first_name}!\nUse the menu below.",
            reply_markup=main_menu_keyboard()
        )
    return MAIN_MENU

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id

    if text == "🛒 Buy Vouchers":
        # Show terms
        terms_text = (
            "1. Once coupon is delivered, no returns or refunds will be accepted.\n"
            "2. All coupons are fresh and valid.\n"
            "3. All sales are final. No refunds, no replacements.\n"
            "4. If coupon shows redeemed, try after some time (10-15 min).\n"
            "5. If there is a genuine issue and you recorded full process from payment to applying, you can contact in support.\n\n"
            "Do you agree to these terms?"
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Agree", callback_data="terms_agree")],
            [InlineKeyboardButton("❌ Decline", callback_data="terms_decline")]
        ])
        await update.message.reply_text(terms_text, reply_markup=keyboard)
        return TERMS

    elif text == "📦 My Orders":
        # Fetch orders for this user
        orders = supabase.table("orders").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(10).execute()
        if not orders.data:
            await update.message.reply_text("You have no orders yet.")
        else:
            msg = "Your recent orders:\n"
            for o in orders.data:
                msg += f"ID: {o['order_id']} | {o['coupon_type']} Off x{o['quantity']} | Status: {o['status']}\n"
            await update.message.reply_text(msg)
        return MAIN_MENU

    elif text == "📜 Disclaimer":
        disclaimer = (
            "1. 🕒 IF CODE SHOW REDEEMED: Wait for 12–13 min because all codes are checked before we add.\n"
            "2. 📦 ELIGIBILITY: Valid only for SHEINVERSE: https://www.sheinindia.in/c/sverse-5939-37961\n"
            "3. ⚡️ DELIVERY: codes are delivered immediately after payment confirmation.\n"
            "4. 🚫 NO REFUNDS: All sales final. No refunds/replacements for any codes.\n"
            "5. ❌ SUPPORT: For issues, a full screen-record from purchase to application is required."
        )
        await update.message.reply_text(disclaimer)
        return MAIN_MENU

    elif text == "🆘 Support":
        await update.message.reply_text(
            "🆘 Support Contact:\n━━━━━━━━━━━━━━\n@ProxySupportChat_bot"
        )
        return MAIN_MENU

    elif text == "📢 Our Channels":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 @PROXY_LOOTERS", url="https://t.me/PROXY_LOOTERS")]
        ])
        await update.message.reply_text(
            "Join our official channels for updates and deals:",
            reply_markup=keyboard
        )
        return MAIN_MENU

    # Admin menu options
    if await is_admin(user_id):
        if text == "➕ Add Coupon":
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("500 Off", callback_data="add_500")],
                [InlineKeyboardButton("1000 Off", callback_data="add_1000")],
                [InlineKeyboardButton("2000 Off", callback_data="add_2000")],
                [InlineKeyboardButton("4000 Off", callback_data="add_4000")]
            ])
            await update.message.reply_text("Select coupon type to add:", reply_markup=keyboard)
            # We'll handle in callback
        elif text == "➖ Remove Coupon":
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("500 Off", callback_data="remove_500")],
                [InlineKeyboardButton("1000 Off", callback_data="remove_1000")],
                [InlineKeyboardButton("2000 Off", callback_data="remove_2000")],
                [InlineKeyboardButton("4000 Off", callback_data="remove_4000")]
            ])
            await update.message.reply_text("Select coupon type to remove:", reply_markup=keyboard)
        elif text == "📊 Stock":
            stock_msg = "📊 Current Stock:\n"
            for t in ["500", "1000", "2000", "4000"]:
                count = await get_stock(t)
                stock_msg += f"▫️ {t} Off: {count}\n"
            await update.message.reply_text(stock_msg)
        elif text == "🎁 Get A Free Code":
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("500 Off", callback_data="free_500")],
                [InlineKeyboardButton("1000 Off", callback_data="free_1000")],
                [InlineKeyboardButton("2000 Off", callback_data="free_2000")],
                [InlineKeyboardButton("4000 Off", callback_data="free_4000")]
            ])
            await update.message.reply_text("Select coupon type to get free codes:", reply_markup=keyboard)
        elif text == "💰 Change Prices":
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("500 Off", callback_data="price_500")],
                [InlineKeyboardButton("1000 Off", callback_data="price_1000")],
                [InlineKeyboardButton("2000 Off", callback_data="price_2000")],
                [InlineKeyboardButton("4000 Off", callback_data="price_4000")]
            ])
            await update.message.reply_text("Select coupon type to change price:", reply_markup=keyboard)
        elif text == "📢 Broadcast":
            await update.message.reply_text("Send me the message you want to broadcast to all users:")
            return "BROADCAST"
        elif text == "🔄 Update QR":
            await update.message.reply_text("Send me the new QR code image (as photo).")
            return "UPDATE_QR"
        elif text == "📋 Last 10 Purchases":
            orders = supabase.table("orders").select("*").eq("status", "approved").order("created_at", desc=True).limit(10).execute()
            if not orders.data:
                await update.message.reply_text("No purchases yet.")
            else:
                msg = "Last 10 purchases:\n"
                for o in orders.data:
                    user = supabase.table("users").select("username").eq("user_id", o["user_id"]).execute()
                    uname = user.data[0]["username"] if user.data else "Unknown"
                    msg += f"{o['order_id']} | @{uname} | {o['coupon_type']} x{o['quantity']} | ₹{o['amount_paid']}\n"
                await update.message.reply_text(msg)
    return MAIN_MENU

async def terms_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "terms_agree":
        # Show coupon type selection
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("500 Off", callback_data="buy_500")],
            [InlineKeyboardButton("1000 Off", callback_data="buy_1000")],
            [InlineKeyboardButton("2000 Off", callback_data="buy_2000")],
            [InlineKeyboardButton("4000 Off", callback_data="buy_4000")]
        ])
        await query.edit_message_text(
            "🛒 Select a coupon type:",
            reply_markup=keyboard
        )
        return SELECT_TYPE
    else:
        await query.edit_message_text("Thanks for using the bot. Goodbye!")
        return ConversationHandler.END

async def select_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    coupon_type = query.data.split("_")[1]  # e.g., "500"
    context.user_data["coupon_type"] = coupon_type
    stock = await get_stock(coupon_type)
    # Show quantity options
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("1 Qty", callback_data="qty_1")],
        [InlineKeyboardButton("5 Qty", callback_data="qty_5")],
        [InlineKeyboardButton("10 Qty", callback_data="qty_10")],
        [InlineKeyboardButton("20 Qty", callback_data="qty_20")],
        [InlineKeyboardButton("Custom Qty", callback_data="qty_custom")]
    ])
    await query.edit_message_text(
        f"🏷️ {coupon_type} Off\n📦 Available stock: {stock}\n\n📋 Available Packages (per-code):\n"
        f"• 1 Code → ₹{await get_price(coupon_type,1)}/code\n"
        f"• 5 Codes → ₹{await get_price(coupon_type,5)}/code\n"
        f"• 10 Codes → ₹{await get_price(coupon_type,10)}/code\n"
        f"• 20+ Codes → ₹{await get_price(coupon_type,20)}/code\n\n"
        "👇 Select quantity:",
        reply_markup=keyboard
    )
    return SELECT_QUANTITY

async def quantity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "qty_custom":
        await query.edit_message_text("Please enter the quantity you want (number):")
        return CUSTOM_QUANTITY
    else:
        qty = int(data.split("_")[1])
        context.user_data["quantity"] = qty
        await show_invoice(query, context)
        return WAIT_PAYMENT

async def custom_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        qty = int(update.message.text)
        if qty <= 0:
            raise ValueError
        context.user_data["quantity"] = qty
        await show_invoice(update.message, context)
        return WAIT_PAYMENT
    except:
        await update.message.reply_text("Invalid number. Please enter a positive integer.")
        return CUSTOM_QUANTITY

async def show_invoice(message_or_query, context):
    coupon_type = context.user_data["coupon_type"]
    qty = context.user_data["quantity"]
    price_per = await get_price(coupon_type, qty)
    total = price_per * qty
    order_id = generate_order_id()
    context.user_data["order_id"] = order_id
    context.user_data["total"] = total

    # Save order as pending
    supabase.table("orders").insert({
        "order_id": order_id,
        "user_id": context.user_data.get("user_id", message_or_query.from_user.id),
        "coupon_type": coupon_type,
        "quantity": qty,
        "amount_paid": total,
        "status": "pending"
    }).execute()

    qr_data = await get_qr_image()
    if qr_data:
        # If QR is stored as file_id, send photo
        await message_or_query.reply_photo(
            photo=qr_data,
            caption=f"🧾 INVOICE\n━━━━━━━━━━━━━━\n🆔 {order_id}\n📦 {coupon_type} Off (x{qty})\n💰 Pay Exactly: ₹{total:.2f}\n\n⚠️ CRITICAL: You MUST pay exact amount. Do not ignore the paise (decimals), or the bot will NOT find your payment!\n\n⏳ QR valid for 10 minutes."
        )
    else:
        await message_or_query.reply_text(
            f"🧾 INVOICE\n━━━━━━━━━━━━━━\n🆔 {order_id}\n📦 {coupon_type} Off (x{qty})\n💰 Pay Exactly: ₹{total:.2f}\n\nQR code not set. Please contact admin."
        )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Verify Payment", callback_data="verify_payment")]
    ])
    await message_or_query.reply_text("Click below after payment:", reply_markup=keyboard)

async def verify_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    order_id = context.user_data.get("order_id")
    if not order_id:
        await query.edit_message_text("Error: No active order.")
        return ConversationHandler.END

    # Notify admin
    order = supabase.table("orders").select("*").eq("order_id", order_id).execute()
    if not order.data:
        await query.edit_message_text("Order not found.")
        return ConversationHandler.END

    order = order.data[0]
    user = supabase.table("users").select("username").eq("user_id", order["user_id"]).execute()
    username = user.data[0]["username"] if user.data else "Unknown"

    admin_msg = (
        f"Payment verification requested:\n"
        f"Order ID: {order_id}\n"
        f"User: @{username}\n"
        f"Type: {order['coupon_type']} Off x{order['quantity']}\n"
        f"Amount: ₹{order['amount_paid']}\n\n"
        f"Approve payment?"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Accept", callbackdata=f"approve_{order_id}")],
        [InlineKeyboardButton("❌ Decline", callbackdata=f"decline_{order_id}")]
    ])
    await context.bot.send_message(chat_id=ADMIN_ID, text=admin_msg, reply_markup=keyboard)

    await query.edit_message_text("Payment verification sent to admin. Please wait for approval.")
    return WAIT_PAYMENT

async def admin_approve_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("approve_"):
        order_id = data[8:]
        # Get order
        order = supabase.table("orders").select("*").eq("order_id", order_id).execute()
        if not order.data:
            await query.edit_message_text("Order not found.")
            return
        order = order.data[0]
        coupon_type = order["coupon_type"]
        qty = order["quantity"]

        # Fetch unused codes
        codes = supabase.table("coupons").select("code").eq("type", coupon_type).eq("used", False).limit(qty).execute()
        if len(codes.data) < qty:
            await query.edit_message_text("Insufficient stock!")
            # Optionally notify user
            await context.bot.send_message(chat_id=order["user_id"], text="Sorry, insufficient stock for your order. Admin has been notified.")
            return

        code_list = [c["code"] for c in codes.data]
        # Mark codes as used
        for code in code_list:
            supabase.table("coupons").update({"used": True}).eq("code", code).execute()
            supabase.table("delivered_codes").insert({"order_id": order_id, "code": code}).execute()

        # Update order status
        supabase.table("orders").update({"status": "approved"}).eq("order_id", order_id).execute()

        # Send codes to user
        codes_text = "\n".join(code_list)
        await context.bot.send_message(
            chat_id=order["user_id"],
            text=f"✅ Payment approved! Here are your codes:\n{codes_text}\n\nThanks for purchasing!"
        )

        await query.edit_message_text(f"Order {order_id} approved and codes delivered.")

    elif data.startswith("decline_"):
        order_id = data[8:]
        supabase.table("orders").update({"status": "declined"}).eq("order_id", order_id).execute()
        order = supabase.table("orders").select("user_id").eq("order_id", order_id).execute()
        if order.data:
            user_id = order.data[0]["user_id"]
            await context.bot.send_message(chat_id=user_id, text="Your payment has been declined by the admin. If you have any issue, please contact support.")
        await query.edit_message_text(f"Order {order_id} declined.")

async def admin_add_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # This is called after admin selects a type and then sends codes
    # We'll use a conversation handler
    pass

# For simplicity, we'll handle admin actions via callbacks and then a state to receive codes.
# Let's implement a separate ConversationHandler for admin actions.

# Admin states:
ADD_COUPON_TYPE, ADD_COUPON_CODES = range(10, 20)
REMOVE_COUPON_TYPE, REMOVE_COUPON_AMOUNT = range(20, 30)
FREE_COUPON_TYPE, FREE_COUPON_AMOUNT = range(30, 40)
PRICE_COUPON_TYPE, PRICE_QUANTITY, PRICE_VALUE = range(40, 50)
BROADCAST_MSG = 50
UPDATE_QR = 60

async def admin_add_coupon_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    coupon_type = query.data.split("_")[1]  # add_500 -> 500
    context.user_data["admin_coupon_type"] = coupon_type
    await query.edit_message_text(f"Send me the coupon codes for {coupon_type} Off, one per line (or comma-separated).")
    return ADD_COUPON_CODES

async def admin_add_coupon_codes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    codes = []
    if ',' in text:
        codes = [c.strip() for c in text.split(',') if c.strip()]
    else:
        codes = [c.strip() for c in text.split('\n') if c.strip()]
    coupon_type = context.user_data["admin_coupon_type"]
    inserted = 0
    for code in codes:
        try:
            supabase.table("coupons").insert({"type": coupon_type, "code": code}).execute()
            inserted += 1
        except:
            pass  # duplicate or error
    await update.message.reply_text(f"{inserted} coupons added successfully for {coupon_type} Off.")
    return ConversationHandler.END

# Similarly for remove, free, price, etc. (I'll provide skeleton due to length constraints)

async def admin_remove_coupon_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    coupon_type = query.data.split("_")[1]
    context.user_data["admin_coupon_type"] = coupon_type
    await query.edit_message_text(f"How many codes to remove from {coupon_type} Off? (Enter number)")
    return REMOVE_COUPON_AMOUNT

async def admin_remove_coupon_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text)
        if amount <= 0:
            raise ValueError
        coupon_type = context.user_data["admin_coupon_type"]
        # Fetch unused codes
        codes = supabase.table("coupons").select("code").eq("type", coupon_type).eq("used", False).limit(amount).execute()
        if len(codes.data) < amount:
            await update.message.reply_text(f"Only {len(codes.data)} unused codes available. Removing all.")
            amount = len(codes.data)
        for c in codes.data:
            supabase.table("coupons").delete().eq("code", c["code"]).execute()
        await update.message.reply_text(f"Removed {amount} codes from {coupon_type} Off.")
    except:
        await update.message.reply_text("Invalid number.")
    return ConversationHandler.END

async def admin_free_coupon_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    coupon_type = query.data.split("_")[1]
    context.user_data["admin_coupon_type"] = coupon_type
    await query.edit_message_text(f"How many free codes from {coupon_type} Off?")
    return FREE_COUPON_AMOUNT

async def admin_free_coupon_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        amount = int(update.message.text)
        if amount <= 0:
            raise ValueError
        coupon_type = context.user_data["admin_coupon_type"]
        codes = supabase.table("coupons").select("code").eq("type", coupon_type).eq("used", False).limit(amount).execute()
        if len(codes.data) < amount:
            await update.message.reply_text(f"Only {len(codes.data)} available. Sending all.")
            amount = len(codes.data)
        code_list = [c["code"] for c in codes.data]
        # Mark as used
        for code in code_list:
            supabase.table("coupons").update({"used": True}).eq("code", code).execute()
        codes_text = "\n".join(code_list)
        await update.message.reply_text(f"Here are your free codes:\n{codes_text}")
    except:
        await update.message.reply_text("Invalid number.")
    return ConversationHandler.END

async def admin_price_coupon_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    coupon_type = query.data.split("_")[1]
    context.user_data["admin_coupon_type"] = coupon_type
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("1 Qty", callbackdata=f"priceqty_1")],
        [InlineKeyboardButton("5 Qty", callbackdata=f"priceqty_5")],
        [InlineKeyboardButton("10 Qty", callbackdata=f"priceqty_10")],
        [InlineKeyboardButton("20 Qty", callbackdata=f"priceqty_20")]
    ])
    await query.edit_message_text("Select quantity bracket to change price:", reply_markup=keyboard)
    return PRICE_QUANTITY

async def admin_price_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    qty = int(query.data.split("_")[1])
    context.user_data["admin_price_qty"] = qty
    await query.edit_message_text(f"Enter new price for {context.user_data['admin_coupon_type']} Off, quantity {qty}+ (in rupees):")
    return PRICE_VALUE

async def admin_price_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text)
        coupon_type = context.user_data["admin_coupon_type"]
        qty = context.user_data["admin_price_qty"]
        supabase.table("prices").upsert({
            "coupon_type": coupon_type,
            "quantity": qty,
            "price": price
        }).execute()
        await update.message.reply_text(f"Price updated for {coupon_type} Off, quantity {qty}+ to ₹{price}.")
    except:
        await update.message.reply_text("Invalid price.")
    return ConversationHandler.END

async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    # Get all users
    users = supabase.table("users").select("user_id").execute()
    sent = 0
    for u in users.data:
        try:
            await context.bot.send_message(chat_id=u["user_id"], text=msg)
            sent += 1
            await asyncio.sleep(0.05)  # avoid flood
        except:
            pass
    await update.message.reply_text(f"Broadcast sent to {sent} users.")
    return ConversationHandler.END

async def admin_update_qr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Expect a photo
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        await update_qr_image(file_id)
        await update.message.reply_text("QR code updated successfully.")
    else:
        await update.message.reply_text("Please send a photo.")
    return ConversationHandler.END

# ==================== MAIN ====================

def main():
    application = Application.builder().token(TOKEN).build()

    # User conversation
    user_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🛒 Buy Vouchers$'), handle_main_menu)],
        states={
            TERMS: [CallbackQueryHandler(terms_callback, pattern="^terms_")],
            SELECT_TYPE: [CallbackQueryHandler(select_type_callback, pattern="^buy_")],
            SELECT_QUANTITY: [CallbackQueryHandler(quantity_callback, pattern="^(qty_|qty_custom)")],
            CUSTOM_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_quantity)],
            WAIT_PAYMENT: [CallbackQueryHandler(verify_payment_callback, pattern="^verify_payment")]
        },
        fallbacks=[CommandHandler('start', start)]
    )
    application.add_handler(user_conv)

    # Admin conversation for adding coupons
    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_add_coupon_type, pattern="^add_")],
        states={
            ADD_COUPON_CODES: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_coupon_codes)]
        },
        fallbacks=[CommandHandler('start', start)],
        map_to_parent={ConversationHandler.END: MAIN_MENU}
    )
    application.add_handler(add_conv)

    remove_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_remove_coupon_type, pattern="^remove_")],
        states={
            REMOVE_COUPON_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_remove_coupon_amount)]
        },
        fallbacks=[CommandHandler('start', start)],
        map_to_parent={ConversationHandler.END: MAIN_MENU}
    )
    application.add_handler(remove_conv)

    free_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_free_coupon_type, pattern="^free_")],
        states={
            FREE_COUPON_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_free_coupon_amount)]
        },
        fallbacks=[CommandHandler('start', start)],
        map_to_parent={ConversationHandler.END: MAIN_MENU}
    )
    application.add_handler(free_conv)

    price_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_price_coupon_type, pattern="^price_")],
        states={
            PRICE_QUANTITY: [CallbackQueryHandler(admin_price_quantity, pattern="^priceqty_")],
            PRICE_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_price_value)]
        },
        fallbacks=[CommandHandler('start', start)],
        map_to_parent={ConversationHandler.END: MAIN_MENU}
    )
    application.add_handler(price_conv)

    broadcast_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^📢 Broadcast$'), handle_main_menu)],
        states={
            BROADCAST_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast)]
        },
        fallbacks=[CommandHandler('start', start)],
        map_to_parent={ConversationHandler.END: MAIN_MENU}
    )
    application.add_handler(broadcast_conv)

    update_qr_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex('^🔄 Update QR$'), handle_main_menu)],
        states={
            UPDATE_QR: [MessageHandler(filters.PHOTO, admin_update_qr)]
        },
        fallbacks=[CommandHandler('start', start)],
        map_to_parent={ConversationHandler.END: MAIN_MENU}
    )
    application.add_handler(update_qr_conv)

    # Admin approve callback
    application.add_handler(CallbackQueryHandler(admin_approve_callback, pattern="^(approve_|decline_)"))

    # Start command
    application.add_handler(CommandHandler('start', start))

    # Other main menu messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu))

    # Set webhook
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 8443)),
        url_path=TOKEN,
        webhook_url=WEBHOOK_URL + "/" + TOKEN
    )

if __name__ == "__main__":
    main()
