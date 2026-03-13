import os
import logging
import random
import string
from datetime import datetime
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
from supabase import create_client, Client

# ==================== CONFIG ====================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ADMIN_IDS = [int(id) for id in os.environ.get("ADMIN_IDS", "8537079657").split(",")]

WEBHOOK_URL = os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL")
if not WEBHOOK_URL:
    raise ValueError("WEBHOOK_URL or RENDER_EXTERNAL_URL must be set")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== CONSTANTS ====================
COUPON_TYPES = ["500", "1000", "2000", "4000"]

# Conversation states
SELECTING_COUPON_TYPE, SELECTING_QUANTITY, CUSTOM_QUANTITY = range(3)
WAITING_PAYER_NAME, WAITING_PAYMENT_SCREENSHOT = range(3, 5)
ENTER_COUPON = 5

# ==================== DATABASE SCHEMA (run in Supabase) ====================
"""
-- Add discount_code column to orders
ALTER TABLE orders ADD COLUMN discount_code TEXT;
"""

# ==================== HELPER FUNCTIONS ====================
def get_main_menu():
    keyboard = [
        [KeyboardButton("🛒 Buy Vouchers")],
        [KeyboardButton("📦 My Orders")],
        [KeyboardButton("📜 Disclaimer")],
        [KeyboardButton("🆘 Support"), KeyboardButton("📢 Our Channels")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_agree_decline_keyboard():
    keyboard = [
        [InlineKeyboardButton("✅ Agree", callback_data="agree_terms")],
        [InlineKeyboardButton("❌ Decline", callback_data="decline_terms")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_coupon_type_keyboard():
    keyboard = []
    for ct in COUPON_TYPES:
        keyboard.append([InlineKeyboardButton(f"{ct} Off", callback_data=f"ctype_{ct}")])
    return InlineKeyboardMarkup(keyboard)

def get_min_quantity(coupon_type):
    result = supabase.table("prices").select("min_quantity").eq("coupon_type", coupon_type).execute()
    if result.data and result.data[0].get("min_quantity") is not None:
        return result.data[0]["min_quantity"]
    return 1

def get_quantity_keyboard(coupon_type):
    prices = supabase.table("prices").select("*").eq("coupon_type", coupon_type).execute()
    if not prices.data:
        return InlineKeyboardMarkup([[InlineKeyboardButton("Error", callback_data="error")]])
    p = prices.data[0]
    min_qty = p.get("min_quantity", 1)
    keyboard = []
    if min_qty <= 1:
        keyboard.append([InlineKeyboardButton(f"1 Qty - ₹{p['price_1']}", callback_data="qty_1")])
    if min_qty <= 5:
        keyboard.append([InlineKeyboardButton(f"5 Qty - ₹{p['price_5']}", callback_data="qty_5")])
    if min_qty <= 10:
        keyboard.append([InlineKeyboardButton(f"10 Qty - ₹{p['price_10']}", callback_data="qty_10")])
    if min_qty <= 20:
        keyboard.append([InlineKeyboardButton(f"20 Qty - ₹{p['price_20']}", callback_data="qty_20")])
    keyboard.append([InlineKeyboardButton("Custom Qty", callback_data="qty_custom")])
    return InlineKeyboardMarkup(keyboard)

def generate_order_id():
    return "ORD" + "".join(random.choices(string.digits, k=14))

def generate_discount_code():
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

def get_admin_panel_keyboard():
    status = supabase.table("settings").select("value").eq("key", "bot_status").execute()
    current = status.data[0]["value"] if status.data else "on"
    status_text = "🔛 Turn Off" if current == "on" else "🔴 Turn On"
    keyboard = [
        [InlineKeyboardButton("➕ Add Coupon", callback_data="admin_add")],
        [InlineKeyboardButton("➖ Remove Coupon", callback_data="admin_remove")],
        [InlineKeyboardButton("📊 Stock", callback_data="admin_stock")],
        [InlineKeyboardButton("🎁 Get Free Code", callback_data="admin_free")],
        [InlineKeyboardButton("💰 Change Prices", callback_data="admin_prices")],
        [InlineKeyboardButton("📏 Set Min Quantity", callback_data="admin_minqty")],
        [InlineKeyboardButton("🎟️ Generate Discount Code", callback_data="admin_gen_discount")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🕒 Last 10 Purchases", callback_data="admin_last10")],
        [InlineKeyboardButton("🖼 Update QR", callback_data="admin_qr")],
        [InlineKeyboardButton(status_text, callback_data="admin_toggle")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_coupon_type_admin_keyboard(action):
    keyboard = []
    for ct in COUPON_TYPES:
        keyboard.append([InlineKeyboardButton(f"{ct} Off", callback_data=f"admin_{action}_{ct}")])
    return InlineKeyboardMarkup(keyboard)

# ---------- Bot status check ----------
async def check_bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if user.id in ADMIN_IDS:
        return True
    status = supabase.table("settings").select("value").eq("key", "bot_status").execute()
    if status.data and status.data[0]["value"] == "off":
        if update.callback_query:
            await update.callback_query.answer("⚠️ Bot is offline for maintenance.", show_alert=True)
        else:
            await update.effective_message.reply_text("⚠️ Bot is currently offline for maintenance. Please try again later.")
        return False
    return True

# ==================== HANDLERS ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_bot_status(update, context):
        return
    context.user_data.clear()
    user = update.effective_user
    supabase.table("users").upsert({
        "user_id": user.id,
        "username": user.username,
        "first_name": user.first_name
    }).execute()

    stock_msg = "✏️ AUTO EARNX CODE SHOP\n━━━━━━━━━━━━━━\n📊 Current Stock\n\n"
    for ct in COUPON_TYPES:
        count = supabase.table("coupons").select("*", count="exact").eq("type", ct).eq("is_used", False).execute()
        stock = count.count if hasattr(count, "count") else 0
        price = supabase.table("prices").select("price_1").eq("coupon_type", ct).execute()
        price_val = price.data[0]["price_1"] if price.data else "N/A"
        stock_msg += f"▫️ {ct} Off: {stock} left (₹{price_val})\n"

    await update.message.reply_text(stock_msg, reply_markup=get_main_menu())

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_bot_status(update, context):
        return
    user = update.effective_user
    text = update.message.text

    if user.id in ADMIN_IDS and context.user_data.get("admin_action"):
        await admin_message_handler(update, context)
        return

    if text == "🛒 Buy Vouchers":
        terms = (
            "1. Once coupon is delivered, no returns or refunds will be accepted.\n"
            "2. All coupons are fresh and valid.\n"
            "3. All sales are final. No refunds, no replacements.\n"
            "4. If coupon shows redeemed, try after some time (10-15 min).\n"
            "5. If there is a genuine issue and you recorded full screen from payment to applying, you can contact support."
        )
        await update.message.reply_text(terms, reply_markup=get_agree_decline_keyboard())
    elif text == "📦 My Orders":
        orders = supabase.table("orders").select("*").eq("user_id", user.id).order("created_at", desc=True).limit(10).execute()
        if not orders.data:
            await update.message.reply_text("You have no orders yet.")
        else:
            msg = "Your last orders:\n"
            for o in orders.data:
                msg += f"Order {o['order_id']}: {o['coupon_type']} x{o['quantity']} - {o['status']}\n"
            await update.message.reply_text(msg)
    elif text == "📜 Disclaimer":
        disclaimer = (
            "1. 🕒 IF CODE SHOW REDEEMED: Wait For 12–13 min Because All Codes Are Checked Before We Add.\n"
            "2. 📦 ELIGIBILITY: Valid only for SHEINVERSE: https://www.sheinindia.in/c/sverse-5939-37961\n"
            "3. ⚡️ DELIVERY: codes are delivered immediately after payment confirmation.\n"
            "4. 🚫 NO REFUNDS: All sales final. No refunds/replacements for any codes.\n"
            "5. ❌ SUPPORT: For issues, a full screen-record from purchase to application is required."
        )
        await update.message.reply_text(disclaimer)
    elif text == "🆘 Support":
        await update.message.reply_text("🆘 Support Contact:\n━━━━━━━━━━━━━━\n@AutoEarnX_Support")
    elif text == "📢 Our Channels":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("@AutoEarnX_Shein", url="https://t.me/AutoEarnX_Shein")]
        ])
        await update.message.reply_text("📢 Join our official channels for updates and deals:", reply_markup=keyboard)
    else:
        await update.message.reply_text("Use the menu buttons.")

# --- Terms callback ---
async def terms_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_bot_status(update, context):
        return
    query = update.callback_query
    await query.answer()
    if query.data == "agree_terms":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Yes, I have a coupon", callback_data="have_coupon_yes")],
            [InlineKeyboardButton("No, continue without coupon", callback_data="have_coupon_no")]
        ])
        await query.edit_message_text("Do you have a discount coupon code?", reply_markup=keyboard)
    else:
        await query.edit_message_text("Thanks for using the bot. Goodbye!")

# --- Coupon handling ---
async def have_coupon_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "have_coupon_yes":
        await query.edit_message_text("Please enter your coupon code:")
        return ENTER_COUPON
    else:
        await query.edit_message_text("🛒 Select a coupon type:", reply_markup=get_coupon_type_keyboard())
        return ConversationHandler.END

async def enter_coupon_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_bot_status(update, context):
        return ConversationHandler.END
    code = update.message.text.strip().upper()
    discount = supabase.table("discount_codes").select("*").eq("code", code).eq("used", False).execute()
    if discount.data:
        expires = discount.data[0].get("expires_at")
        if expires and datetime.fromisoformat(expires) < datetime.utcnow():
            await update.message.reply_text("This coupon has expired.")
        else:
            context.user_data["discount_code"] = code
            context.user_data["discount_value"] = discount.data[0]["value"]
            await update.message.reply_text(f"Coupon accepted! You get ₹{discount.data[0]['value']} off.")
            await update.message.reply_text("🛒 Select a coupon type:", reply_markup=get_coupon_type_keyboard())
            return ConversationHandler.END
    else:
        await update.message.reply_text("Invalid or already used coupon code. Continuing without discount.")
    await update.message.reply_text("🛒 Select a coupon type:", reply_markup=get_coupon_type_keyboard())
    return ConversationHandler.END

# --- Coupon type selection ---
async def coupon_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_bot_status(update, context):
        return
    query = update.callback_query
    await query.answer()
    ctype = query.data.split("_")[1]
    context.user_data["coupon_type"] = ctype

    count = supabase.table("coupons").select("*", count="exact").eq("type", ctype).eq("is_used", False).execute()
    stock = count.count if hasattr(count, "count") else 0
    min_qty = get_min_quantity(ctype)

    await query.edit_message_text(
        f"🏷️ {ctype} Off\n📦 Available stock: {stock}\n⚠️ Minimum quantity: {min_qty}\n\n📋 Available Packages (per-code):",
        reply_markup=get_quantity_keyboard(ctype)
    )

# --- Quantity selection ---
async def quantity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_bot_status(update, context):
        return
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "qty_custom":
        await query.edit_message_text("Please enter the quantity (number):")
        return CUSTOM_QUANTITY
    else:
        qty = int(data.split("_")[1])
        ctype = context.user_data.get("coupon_type")
        min_qty = get_min_quantity(ctype)
        if qty < min_qty:
            await query.edit_message_text(f"❌ Minimum quantity for {ctype} Off is {min_qty}. Please select a higher quantity.")
            return
        await process_quantity(update, context, qty)
        return ConversationHandler.END

async def custom_quantity_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_bot_status(update, context):
        return ConversationHandler.END
    try:
        qty = int(update.message.text)
        if qty <= 0:
            raise ValueError
        ctype = context.user_data.get("coupon_type")
        min_qty = get_min_quantity(ctype)
        if qty < min_qty:
            await update.message.reply_text(f"❌ Minimum quantity for {ctype} Off is {min_qty}. Please enter a larger number.")
            return CUSTOM_QUANTITY
        await process_quantity(update, context, qty)
    except:
        await update.message.reply_text("Invalid number. Please use the menu again.")
    return ConversationHandler.END

async def process_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE, qty):
    ctype = context.user_data["coupon_type"]
    # Check stock
    count = supabase.table("coupons").select("*", count="exact").eq("type", ctype).eq("is_used", False).execute()
    stock = count.count if hasattr(count, "count") else 0
    if stock < qty:
        await (update.message or update.callback_query.message).reply_text(f"❌ Only {stock} codes available for {ctype} Off.")
        return

    prices = supabase.table("prices").select("*").eq("coupon_type", ctype).execute()
    if not prices.data:
        await (update.message or update.callback_query.message).reply_text("Price error.")
        return
    p = prices.data[0]
    # Price bracket logic (bulk discount)
    if qty <= 1:
        price_per = p["price_1"]
    elif qty <= 5:
        price_per = p["price_5"]
    elif qty <= 10:
        price_per = p["price_10"]
    else:
        price_per = p["price_20"]
    total = price_per * qty

    discount_code = context.user_data.get("discount_code")
    discount_value = context.user_data.get("discount_value", 0)
    if discount_value:
        total -= discount_value
        if total < 0:
            total = 0

    order_id = generate_order_id()
    context.user_data["order_id"] = order_id
    context.user_data["qty"] = qty
    context.user_data["total"] = total

    # Insert order with discount_code (if any)
    order_data = {
        "order_id": order_id,
        "user_id": update.effective_user.id,
        "coupon_type": ctype,
        "quantity": qty,
        "total_price": total,
        "status": "pending"
    }
    if discount_code:
        order_data["discount_code"] = discount_code
    supabase.table("orders").insert(order_data).execute()

    # Remove discount from user_data after order creation
    context.user_data.pop("discount_code", None)
    context.user_data.pop("discount_value", None)

    qr_setting = supabase.table("settings").select("value").eq("key", "qr_image").execute()
    qr_file_id = qr_setting.data[0]["value"] if qr_setting.data and qr_setting.data[0]["value"] else None

    invoice_text = (
        f"🧾 INVOICE\n━━━━━━━━━━━━━━\n"
        f"🆔 {order_id}\n"
        f"📦 {ctype} Off (x{qty})\n"
    )
    if discount_value:
        invoice_text += f"🎟️ Discount: -₹{discount_value}\n"
    invoice_text += (
        f"💰 Pay Exactly: ₹{total}\n"
        f"⚠️ CRITICAL: You MUST pay exact amount. Do not ignore the paise (decimals), or the bot will NOT find your payment!\n\n"
        f"⏳ QR valid for 10 minutes."
    )

    if qr_file_id:
        await (update.message or update.callback_query.message).reply_photo(photo=qr_file_id, caption=invoice_text)
    else:
        await (update.message or update.callback_query.message).reply_text(invoice_text + "\n\n(QR not set by admin yet)")

    verify_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Verify Payment", callback_data=f"verify_{order_id}")]])
    await (update.message or update.callback_query.message).reply_text("After payment, click Verify.", reply_markup=verify_keyboard)

# --- Payment verification ---
async def verify_payment_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_bot_status(update, context):
        return ConversationHandler.END
    query = update.callback_query
    await query.answer()
    order_id = query.data.split("_")[1]
    context.user_data["verify_order_id"] = order_id
    await query.edit_message_text("Please enter the payer name (the name used for payment):")
    return WAITING_PAYER_NAME

async def payment_name_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_bot_status(update, context):
        return ConversationHandler.END
    context.user_data["payer_name"] = update.message.text
    await update.message.reply_text("Please send the screenshot of the payment:")
    return WAITING_PAYMENT_SCREENSHOT

async def payment_screenshot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_bot_status(update, context):
        return ConversationHandler.END
    photo = update.message.photo[-1]
    file_id = photo.file_id
    order_id = context.user_data["verify_order_id"]

    order = supabase.table("orders").select("*").eq("order_id", order_id).execute()
    if not order.data:
        await update.message.reply_text("Order not found.")
        return ConversationHandler.END
    o = order.data[0]

    user = update.effective_user
    user_mention = f"@{user.username}" if user.username else user.first_name
    payer_name = context.user_data["payer_name"]

    admin_msg = (
        f"Payment verification requested:\n"
        f"User: {user_mention} (ID: {user.id})\n"
        f"Payer Name: {payer_name}\n"
        f"Order: {o['order_id']}\n"
        f"Type: {o['coupon_type']} x{o['quantity']}\n"
        f"Total: ₹{o['total_price']}\n\n"
        f"Accept or Decline?"
    )
    accept_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Accept", callback_data=f"accept_{o['order_id']}"),
         InlineKeyboardButton("❌ Decline", callback_data=f"decline_{o['order_id']}")]
    ])

    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_photo(admin_id, photo=file_id, caption=admin_msg, reply_markup=accept_keyboard)
        except Exception as e:
            logger.error(f"Failed to send to admin {admin_id}: {e}")

    await update.message.reply_text("Verification request sent to admin. Please wait for approval.")
    context.user_data.pop("verify_order_id", None)
    context.user_data.pop("payer_name", None)
    return ConversationHandler.END

# --- Admin accept/decline ---
async def admin_accept_decline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("_")
    action = data[0]
    order_id = data[1]

    order = supabase.table("orders").select("*").eq("order_id", order_id).execute()
    if not order.data:
        await query.edit_message_text("Order not found.")
        return
    o = order.data[0]

    if o["status"] != "pending":
        await query.edit_message_text(f"❌ Order {order_id} already processed (status: {o['status']}).")
        return

    if action == "accept":
        coupons = supabase.table("coupons").select("*").eq("type", o["coupon_type"]).eq("is_used", False).limit(o["quantity"]).execute()
        if len(coupons.data) < o["quantity"]:
            await query.edit_message_text("❌ Insufficient stock! Cannot accept payment.")
            return

        codes = [c["code"] for c in coupons.data]
        for c in coupons.data:
            supabase.table("coupons").update({
                "is_used": True,
                "used_by": o["user_id"],
                "used_at": datetime.utcnow().isoformat()
            }).eq("id", c["id"]).execute()

        supabase.table("orders").update({"status": "completed"}).eq("order_id", order_id).execute()

        # Mark discount code as used if present
        if o.get("discount_code"):
            supabase.table("discount_codes").update({"used": True}).eq("code", o["discount_code"]).execute()

        codes_text = "\n".join(codes)
        await context.bot.send_message(
            o["user_id"],
            f"✅ Payment accepted! Here are your codes:\n{codes_text}\n\nThanks for purchasing!"
        )
        await query.edit_message_text(f"✅ Order {order_id} completed.")
    else:
        supabase.table("orders").update({"status": "declined"}).eq("order_id", order_id).execute()
        await context.bot.send_message(
            o["user_id"],
            "❌ Your payment has been declined by admin. If there is any issue, contact support."
        )
        await query.edit_message_text(f"❌ Order {order_id} declined.")

# ==================== ADMIN PANEL ====================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Unauthorized.")
        return
    await update.message.reply_text("Admin Panel", reply_markup=get_admin_panel_keyboard())

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await query.edit_message_text("Unauthorized.")
        return

    context.user_data.pop("admin_action", None)
    context.user_data.pop("broadcast", None)
    context.user_data.pop("awaiting_qr", None)

    data = query.data

    if data == "admin_add":
        await query.edit_message_text("Select coupon type to add:", reply_markup=get_coupon_type_admin_keyboard("add"))
    elif data == "admin_remove":
        await query.edit_message_text("Select coupon type to remove:", reply_markup=get_coupon_type_admin_keyboard("remove"))
    elif data == "admin_stock":
        msg = "Current Stock:\n"
        for ct in COUPON_TYPES:
            count = supabase.table("coupons").select("*", count="exact").eq("type", ct).eq("is_used", False).execute()
            stock = count.count if hasattr(count, "count") else 0
            msg += f"{ct} Off: {stock}\n"
        await query.edit_message_text(msg)
    elif data == "admin_free":
        await query.edit_message_text("Select coupon type to get free codes:", reply_markup=get_coupon_type_admin_keyboard("free"))
    elif data == "admin_prices":
        await query.edit_message_text("Select coupon type to change prices:", reply_markup=get_coupon_type_admin_keyboard("prices"))
    elif data == "admin_minqty":
        await query.edit_message_text("Select coupon type to set minimum quantity:", reply_markup=get_coupon_type_admin_keyboard("minqty"))
    elif data == "admin_gen_discount":
        context.user_data["admin_action"] = "gen_discount"
        await query.edit_message_text("Enter the discount value in rupees (e.g., 50):")
    elif data == "admin_broadcast":
        context.user_data["broadcast"] = True
        await query.edit_message_text("Send the message you want to broadcast to all users:")
    elif data == "admin_last10":
        orders = supabase.table("orders").select("*").order("created_at", desc=True).limit(10).execute()
        if not orders.data:
            await query.edit_message_text("No orders yet.")
        else:
            msg = "Last 10 purchases:\n"
            for o in orders.data:
                user = supabase.table("users").select("username").eq("user_id", o["user_id"]).execute()
                username = user.data[0]["username"] if user.data else "Unknown"
                msg += f"{o['order_id']}: {username} - {o['coupon_type']} x{o['quantity']} - {o['status']} - {o['created_at'][:19]}\n"
            await query.edit_message_text(msg)
    elif data == "admin_qr":
        context.user_data["awaiting_qr"] = True
        await query.edit_message_text("Send the new QR code image.")
    elif data == "admin_toggle":
        status = supabase.table("settings").select("value").eq("key", "bot_status").execute()
        current = status.data[0]["value"] if status.data else "on"
        new_status = "off" if current == "on" else "on"
        supabase.table("settings").upsert({"key": "bot_status", "value": new_status}).execute()
        await query.edit_message_text(f"Bot status changed to {new_status.upper()}.")
    elif data.startswith("admin_add_"):
        ctype = data.split("_")[2]
        context.user_data["admin_action"] = ("add", ctype)
        await query.edit_message_text(f"Send the coupon codes for {ctype} Off (one per line):")
    elif data.startswith("admin_remove_"):
        ctype = data.split("_")[2]
        context.user_data["admin_action"] = ("remove", ctype)
        await query.edit_message_text(f"How many codes to remove from {ctype} Off? (send a number)")
    elif data.startswith("admin_free_"):
        ctype = data.split("_")[2]
        context.user_data["admin_action"] = ("free", ctype)
        await query.edit_message_text(f"How many free codes from {ctype} Off? (send a number)")
    elif data.startswith("admin_prices_"):
        ctype = data.split("_")[2]
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("1 Qty", callback_data=f"admin_price_qty_{ctype}_1")],
            [InlineKeyboardButton("5 Qty", callback_data=f"admin_price_qty_{ctype}_5")],
            [InlineKeyboardButton("10 Qty", callback_data=f"admin_price_qty_{ctype}_10")],
            [InlineKeyboardButton("20 Qty", callback_data=f"admin_price_qty_{ctype}_20")]
        ])
        await query.edit_message_text(f"Select quantity for {ctype} Off price change:", reply_markup=keyboard)
    elif data.startswith("admin_price_qty_"):
        parts = data.split("_")
        ctype = parts[3]
        qty = parts[4]
        context.user_data["admin_action"] = ("price", ctype, qty)
        await query.edit_message_text(f"Enter new price for {ctype} Off, {qty} Qty:")
    elif data.startswith("admin_minqty_"):
        ctype = data.split("_")[2]
        context.user_data["admin_action"] = ("minqty", ctype)
        await query.edit_message_text(f"Enter minimum quantity for {ctype} Off:")

async def admin_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    text = update.message.text
    photo = update.message.photo[-1] if update.message.photo else None

    if context.user_data.get("broadcast"):
        users = supabase.table("users").select("user_id").execute()
        success = 0
        for u in users.data:
            try:
                await context.bot.send_message(u["user_id"], text)
                success += 1
            except:
                pass
        await update.message.reply_text(f"Broadcast sent to {success}/{len(users.data)} users.")
        context.user_data.pop("broadcast", None)
        return

    if context.user_data.get("awaiting_qr"):
        if photo:
            file_id = photo.file_id
            supabase.table("settings").upsert({"key": "qr_image", "value": file_id}).execute()
            await update.message.reply_text("QR code updated.")
            context.user_data.pop("awaiting_qr", None)
        else:
            await update.message.reply_text("Please send an image.")
        return

    admin_action = context.user_data.get("admin_action")
    if not admin_action:
        return

    if admin_action[0] == "add":
        ctype = admin_action[1]
        codes = text.strip().split("\n")
        inserted = 0
        for code in codes:
            code = code.strip()
            if code:
                try:
                    supabase.table("coupons").insert({"code": code, "type": ctype}).execute()
                    inserted += 1
                except:
                    pass
        await update.message.reply_text(f"{inserted} coupons added to {ctype} Off.")
        context.user_data.pop("admin_action", None)

    elif admin_action[0] == "remove":
        ctype = admin_action[1]
        try:
            num = int(text)
            coupons = supabase.table("coupons").select("id").eq("type", ctype).eq("is_used", False).order("id").limit(num).execute()
            ids = [c["id"] for c in coupons.data]
            if ids:
                supabase.table("coupons").delete().in_("id", ids).execute()
            await update.message.reply_text(f"Removed {len(ids)} coupons from {ctype} Off.")
        except ValueError:
            await update.message.reply_text("Invalid number.")
        context.user_data.pop("admin_action", None)

    elif admin_action[0] == "free":
        ctype = admin_action[1]
        try:
            num = int(text)
            coupons = supabase.table("coupons").select("code").eq("type", ctype).eq("is_used", False).limit(num).execute()
            if len(coupons.data) < num:
                await update.message.reply_text(f"Only {len(coupons.data)} available.")
            codes = [c["code"] for c in coupons.data]
            for c in coupons.data:
                supabase.table("coupons").update({
                    "is_used": True,
                    "used_by": update.effective_user.id,
                    "used_at": datetime.utcnow().isoformat()
                }).eq("code", c["code"]).execute()
            await update.message.reply_text(f"Here are your free codes:\n" + "\n".join(codes))
        except ValueError:
            await update.message.reply_text("Invalid number.")
        context.user_data.pop("admin_action", None)

    elif admin_action[0] == "price":
        ctype = admin_action[1]
        qty = admin_action[2]
        try:
            new_price = float(text)
            col = f"price_{qty}"
            supabase.table("prices").update({col: new_price}).eq("coupon_type", ctype).execute()
            await update.message.reply_text(f"Price updated for {ctype} Off, {qty} Qty: ₹{new_price}")
        except ValueError:
            await update.message.reply_text("Invalid number.")
        context.user_data.pop("admin_action", None)

    elif admin_action[0] == "minqty":
        ctype = admin_action[1]
        try:
            min_qty = int(text)
            if min_qty < 1:
                raise ValueError
            supabase.table("prices").update({"min_quantity": min_qty}).eq("coupon_type", ctype).execute()
            await update.message.reply_text(f"Minimum quantity for {ctype} Off set to {min_qty}.")
        except ValueError:
            await update.message.reply_text("Invalid number (must be >=1).")
        context.user_data.pop("admin_action", None)

    elif admin_action == "gen_discount":
        try:
            value = float(text)
            code = generate_discount_code()
            supabase.table("discount_codes").insert({
                "code": code,
                "value": value,
                "created_by": update.effective_user.id
            }).execute()
            await update.message.reply_text(f"✅ Discount code generated: `{code}` with value ₹{value}")
        except ValueError:
            await update.message.reply_text("Invalid number.")
        context.user_data.pop("admin_action", None)

# ==================== CONVERSATION HANDLERS ====================
custom_qty_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(quantity_callback, pattern="^qty_custom$")],
    states={
        CUSTOM_QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, custom_quantity_input)]
    },
    fallbacks=[]
)

payment_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(verify_payment_start, pattern="^verify_")],
    states={
        WAITING_PAYER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, payment_name_handler)],
        WAITING_PAYMENT_SCREENSHOT: [MessageHandler(filters.PHOTO, payment_screenshot_handler)]
    },
    fallbacks=[]
)

coupon_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(have_coupon_callback, pattern="^have_coupon_yes$")],
    states={
        ENTER_COUPON: [MessageHandler(filters.TEXT & ~filters.COMMAND, enter_coupon_handler)]
    },
    fallbacks=[]
)

# ==================== APPLICATION SETUP ====================
application = Application.builder().token(TELEGRAM_TOKEN).build()

# Command handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("admin", admin_panel))

# Conversation handlers
application.add_handler(custom_qty_conv)
application.add_handler(payment_conv)
application.add_handler(coupon_conv)

# Callback query handlers
application.add_handler(CallbackQueryHandler(terms_callback, pattern="^(agree|decline)_terms$"))
application.add_handler(CallbackQueryHandler(have_coupon_callback, pattern="^have_coupon_"))
application.add_handler(CallbackQueryHandler(coupon_type_callback, pattern="^ctype_"))
application.add_handler(CallbackQueryHandler(quantity_callback, pattern="^qty_[0-9]+$"))
application.add_handler(CallbackQueryHandler(admin_accept_decline, pattern="^(accept|decline)_"))
application.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))

# Photo handler for QR update
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id in ADMIN_IDS and context.user_data.get("awaiting_qr"):
        await admin_message_handler(update, context)
application.add_handler(MessageHandler(filters.PHOTO, photo_handler))

# General text handler (must be last)
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler))

# ==================== FLASK WEBHOOK ====================
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    application.update_queue.put_nowait(update)
    return "ok", 200

@app.route("/set_webhook", methods=["GET"])
def set_webhook():
    url = WEBHOOK_URL + "/webhook"
    application.bot.set_webhook(url=url)
    return f"Webhook set to {url}", 200

@app.route("/")
def home():
    return "Bot is running!", 200

if __name__ == "__main__":
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        url_path=TELEGRAM_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}"
    )
