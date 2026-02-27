import logging
import os
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, ConversationHandler, filters
from supabase import create_client, Client
import uuid

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Supabase setup
SUPABASE_URL = "your_supabase_url"
SUPABASE_KEY = "your_supabase_key"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Bot token
BOT_TOKEN = "your_bot_token"

# Admin ID (replace with your Telegram user ID)
ADMIN_ID = 123456789  # Replace with your actual admin ID

# Conversation states
AMOUNT, GIFT_CARD_CODE, GIFT_CARD_SCREENSHOT, UPI_AMOUNT, UPI_PAYER_NAME, UPI_SCREENSHOT, COUPON_QUANTITY, ADMIN_COUPON_INPUT, ADMIN_REMOVE_QUANTITY, ADMIN_PRICE_INPUT = range(10)

# Coupon prices (default)
coupon_prices = {
    '500': 500,
    '1K': 1000,
    '2K': 2000,
    '4K': 4000
}

# User menu keyboard
def get_user_keyboard():
    keyboard = [
        [KeyboardButton("💰 Add Coins"), KeyboardButton("🎟️ Buy Coupon")],
        [KeyboardButton("👤 Balance"), KeyboardButton("📦 My Orders")],
        [KeyboardButton("🆘 Support"), KeyboardButton("⚠️ Disclaimer")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Admin menu keyboard
def get_admin_keyboard():
    keyboard = [
        [KeyboardButton("➕ Add Coupon"), KeyboardButton("➖ Remove Coupon")],
        [KeyboardButton("📊 Stock"), KeyboardButton("💰 Change Prices")],
        [KeyboardButton("🔄 Update QR"), KeyboardButton("📋 Last 10 Buyers")],
        [KeyboardButton("🔙 Back to User Menu")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Buy coupon keyboard
def get_coupon_type_keyboard():
    keyboard = [
        [InlineKeyboardButton("500 🪙", callback_data="coupon_500")],
        [InlineKeyboardButton("1K 🪙", callback_data="coupon_1K")],
        [InlineKeyboardButton("2K 🪙", callback_data="coupon_2K")],
        [InlineKeyboardButton("4K 🪙", callback_data="coupon_4K")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Terms keyboard
def get_terms_keyboard():
    keyboard = [
        [InlineKeyboardButton("✅ Agree", callback_data="terms_agree")],
        [InlineKeyboardButton("❌ Decline", callback_data="terms_decline")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Payment method keyboard
def get_payment_keyboard():
    keyboard = [
        [InlineKeyboardButton("🎁 Amazon Gift Card", callback_data="payment_amazon")],
        [InlineKeyboardButton("📱 UPI", callback_data="payment_upi")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Admin coupon selection keyboard
def get_admin_coupon_keyboard(action):
    keyboard = [
        [InlineKeyboardButton("500", callback_data=f"admin_{action}_500")],
        [InlineKeyboardButton("1K", callback_data=f"admin_{action}_1K")],
        [InlineKeyboardButton("2K", callback_data=f"admin_{action}_2K")],
        [InlineKeyboardButton("4K", callback_data=f"admin_{action}_4K")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_back")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Admin approval keyboard
def get_admin_approval_keyboard(order_id, payment_method):
    keyboard = [
        [InlineKeyboardButton("✅ Accept", callback_data=f"approve_{order_id}_{payment_method}")],
        [InlineKeyboardButton("❌ Decline", callback_data=f"decline_{order_id}_{payment_method}")]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    # Check if user exists in database
    result = supabase.table('users').select('*').eq('user_id', user_id).execute()
    
    if not result.data:
        # Create new user
        supabase.table('users').insert({
            'user_id': user_id,
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'balance': 0,
            'created_at': datetime.now().isoformat()
        }).execute()
    
    await update.message.reply_text(
        f"Welcome To The AutoEarnX Selling Bot, {user.first_name}! 🚀\n\n"
        "Use the buttons below to navigate:",
        reply_markup=get_user_keyboard()
    )

async def handle_user_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    
    if text == "💰 Add Coins":
        await update.message.reply_text(
            "💳 Select Payment Method:\n\n"
            "⚠️ Under Maintenance:\n"
            "🛠️ PhonePe Gift Card\n\n"
            "Please use other methods for deposit.",
            reply_markup=get_payment_keyboard()
        )
    
    elif text == "🎟️ Buy Coupon":
        await update.message.reply_text(
            "📋 Terms & Conditions:\n\n"
            "1. Once coupon is delivered, no returns or refunds will be accepted.\n"
            "2. All coupons are fresh and valid.\n"
            "3. All sales are final. No refunds, no replacements.\n"
            "4. If coupon shows redeem, try after some time (10-15min).\n\n"
            "Do you agree to these terms?",
            reply_markup=get_terms_keyboard()
        )
    
    elif text == "👤 Balance":
        result = supabase.table('users').select('balance').eq('user_id', user_id).execute()
        balance = result.data[0]['balance'] if result.data else 0
        await update.message.reply_text(f"💰 Your Balance: {balance} Diamonds 🪙")
    
    elif text == "📦 My Orders":
        result = supabase.table('orders').select('*').eq('user_id', user_id).order('created_at', desc=True).limit(10).execute()
        
        if result.data:
            orders_text = "📦 Your Last 10 Orders:\n\n"
            for order in result.data:
                orders_text += f"🆔 Order: {order['id'][:8]}...\n"
                orders_text += f"🎟️ Coupon: {order['coupon_code']}\n"
                orders_text += f"💰 Amount: {order['amount']} 🪙\n"
                orders_text += f"📅 Date: {order['created_at'][:10]}\n"
                orders_text += "━━━━━━━━━━━━━━━\n"
            await update.message.reply_text(orders_text)
        else:
            await update.message.reply_text("📦 You haven't made any orders yet.")
    
    elif text == "🆘 Support":
        await update.message.reply_text(
            "🆘 Support Contact:\n"
            "━━━━━━━━━━━━━━\n"
            "@AutoEarnX_Support_Bot"
        )
    
    elif text == "⚠️ Disclaimer":
        await update.message.reply_text(
            "Disclaimer:-\n"
            "1. Once coupon is delivered, no returns or refunds will be accepted.\n"
            "2. All coupons are fresh and valid.\n"
            "3. All sales are final. No refunds, no replacements.\n"
            "4. If coupon shows redeem, try after some time (10-15min)."
        )
    
    # Admin panel check
    elif text == "👑 Admin Panel" and user_id == ADMIN_ID:
        await update.message.reply_text(
            "Welcome to Admin Panel!",
            reply_markup=get_admin_keyboard()
        )

async def handle_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        return
    
    if text == "➕ Add Coupon":
        context.user_data['admin_action'] = 'add'
        await update.message.reply_text(
            "Select The Options To Add The Coupons:",
            reply_markup=get_admin_coupon_keyboard('add')
        )
    
    elif text == "➖ Remove Coupon":
        context.user_data['admin_action'] = 'remove'
        await update.message.reply_text(
            "Select The Options To Remove The Coupons:",
            reply_markup=get_admin_coupon_keyboard('remove')
        )
    
    elif text == "📊 Stock":
        # Get stock for all coupon types
        stocks = {}
        for coupon_type in ['500', '1K', '2K', '4K']:
            result = supabase.table('coupons').select('*').eq('type', coupon_type).eq('is_used', False).execute()
            stocks[coupon_type] = len(result.data)
        
        stock_text = "📊 Current Stock:\n\n"
        stock_text += f"500 Coupons: {stocks['500']} available\n"
        stock_text += f"1K Coupons: {stocks['1K']} available\n"
        stock_text += f"2K Coupons: {stocks['2K']} available\n"
        stock_text += f"4K Coupons: {stocks['4K']} available\n"
        
        await update.message.reply_text(stock_text)
    
    elif text == "💰 Change Prices":
        context.user_data['admin_action'] = 'price'
        await update.message.reply_text(
            "Select The Options To Change The Price:",
            reply_markup=get_admin_coupon_keyboard('price')
        )
    
    elif text == "🔄 Update QR":
        context.user_data['awaiting_qr'] = True
        await update.message.reply_text("Please send the new QR code image:")
    
    elif text == "📋 Last 10 Buyers":
        result = supabase.table('orders').select('*, users(*)').order('created_at', desc=True).limit(10).execute()
        
        if result.data:
            buyers_text = "📋 Last 10 Buyers:\n\n"
            for order in result.data:
                user_info = order.get('users', {})
                buyers_text += f"👤 User: {user_info.get('first_name', 'Unknown')} (@{user_info.get('username', 'N/A')})\n"
                buyers_text += f"🎟️ Coupon: {order['coupon_code']}\n"
                buyers_text += f"💰 Amount: {order['amount']} 🪙\n"
                buyers_text += f"📅 Date: {order['created_at'][:10]}\n"
                buyers_text += "━━━━━━━━━━━━━━━\n"
            await update.message.reply_text(buyers_text)
        else:
            await update.message.reply_text("No orders found.")
    
    elif text == "🔙 Back to User Menu":
        await update.message.reply_text(
            "Returning to user menu...",
            reply_markup=get_user_keyboard()
        )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    # Get user balance
    result = supabase.table('users').select('balance').eq('user_id', user_id).execute()
    balance = result.data[0]['balance'] if result.data else 0
    
    # Terms handling
    if data == "terms_agree":
        await query.edit_message_text(
            "🛒 Select a coupon type:",
            reply_markup=get_coupon_type_keyboard()
        )
    
    elif data == "terms_decline":
        await query.edit_message_text("Thanks For Using The Bot, GoodBye! 👋")
    
    # Coupon selection
    elif data.startswith("coupon_"):
        coupon_type = data.replace("coupon_", "")
        context.user_data['selected_coupon'] = coupon_type
        
        # Get stock and price
        stock_result = supabase.table('coupons').select('*').eq('type', coupon_type).eq('is_used', False).execute()
        stock = len(stock_result.data)
        
        price = coupon_prices.get(coupon_type, 0)
        
        if stock == 0:
            await query.edit_message_text(f"❌ Not enough stock! Available: 0")
            return
        
        await query.edit_message_text(
            f"How many {coupon_type} coupons do you want to buy?\n"
            f"Price per coupon: {price} 🪙\n"
            f"Available stock: {stock}\n\n"
            f"Please send the quantity:"
        )
        context.user_data['awaiting_quantity'] = True
    
    # Payment methods
    elif data == "payment_amazon":
        await query.edit_message_text(
            "Enter the number of coins to add (Method: Amazon):\n\n"
            "✅ Minimum: 30"
        )
        context.user_data['payment_method'] = 'amazon'
        context.user_data['awaiting_amount'] = True
    
    elif data == "payment_upi":
        await query.edit_message_text(
            "How much coins you need? (Minimum: 30)"
        )
        context.user_data['payment_method'] = 'upi'
        context.user_data['awaiting_upi_amount'] = True
    
    elif data == "back_to_menu":
        await query.edit_message_text(
            "Main Menu:",
            reply_markup=get_user_keyboard()
        )
    
    # Admin coupon actions
    elif data.startswith("admin_"):
        parts = data.split('_')
        action = parts[1]
        coupon_type = parts[2] if len(parts) > 2 else None
        
        if action == "add" and coupon_type:
            context.user_data['admin_coupon_type'] = coupon_type
            await query.edit_message_text(f"Please send the coupons for {coupon_type} (one per line):")
            context.user_data['awaiting_coupons'] = True
        
        elif action == "remove" and coupon_type:
            context.user_data['admin_coupon_type'] = coupon_type
            await query.edit_message_text(f"How many {coupon_type} coupons do you want to remove?")
            context.user_data['awaiting_remove_quantity'] = True
        
        elif action == "price" and coupon_type:
            context.user_data['admin_coupon_type'] = coupon_type
            await query.edit_message_text(f"Enter new price for {coupon_type} coupons:")
            context.user_data['awaiting_price'] = True
        
        elif data == "admin_back":
            await query.edit_message_text(
                "Admin Panel",
                reply_markup=get_admin_keyboard()
            )
    
    # Admin approval/rejection
    elif data.startswith("approve_"):
        parts = data.split('_')
        order_id = parts[1]
        payment_method = parts[2]
        
        # Get order details
        order_result = supabase.table('pending_orders').select('*').eq('id', order_id).execute()
        if order_result.data:
            order = order_result.data[0]
            user_id = order['user_id']
            amount = order['amount']
            
            # Add balance to user
            supabase.table('users').update({'balance': supabase.raw(f'balance + {amount}')}).eq('user_id', user_id).execute()
            
            # Update order status
            supabase.table('pending_orders').update({'status': 'approved'}).eq('id', order_id).execute()
            
            # Notify user
            await context.bot.send_message(
                user_id,
                f"🎉 Congratulations! Your order has been approved!\n"
                f"💰 {amount} Diamonds have been added to your balance."
            )
            
            await query.edit_message_text(f"Order {order_id} approved successfully!")
    
    elif data.startswith("decline_"):
        parts = data.split('_')
        order_id = parts[1]
        payment_method = parts[2]
        
        # Update order status
        supabase.table('pending_orders').update({'status': 'declined'}).eq('id', order_id).execute()
        
        # Get user_id
        order_result = supabase.table('pending_orders').select('user_id').eq('id', order_id).execute()
        if order_result.data:
            user_id = order_result.data[0]['user_id']
            
            # Notify user
            await context.bot.send_message(
                user_id,
                "❌ Your payment has been declined. Please contact support for more information."
            )
        
        await query.edit_message_text(f"Order {order_id} declined!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    # Handle regular menu
    if user_id == ADMIN_ID and text in ["👑 Admin Panel", "➕ Add Coupon", "➖ Remove Coupon", "📊 Stock", "💰 Change Prices", "🔄 Update QR", "📋 Last 10 Buyers", "🔙 Back to User Menu"]:
        await handle_admin_menu(update, context)
        return
    
    # Handle coupon quantity
    if context.user_data.get('awaiting_quantity'):
        try:
            quantity = int(text)
            coupon_type = context.user_data.get('selected_coupon')
            
            # Check stock
            stock_result = supabase.table('coupons').select('*').eq('type', coupon_type).eq('is_used', False).execute()
            stock = len(stock_result.data)
            
            if quantity > stock:
                await update.message.reply_text(f"❌ Not enough stock! Available: {stock}")
                context.user_data['awaiting_quantity'] = False
                return
            
            # Check balance
            result = supabase.table('users').select('balance').eq('user_id', user_id).execute()
            balance = result.data[0]['balance'] if result.data else 0
            total_price = coupon_prices.get(coupon_type, 0) * quantity
            
            if balance < total_price:
                await update.message.reply_text(f"❌ Not enough diamonds! Available: {balance} 🪙")
                context.user_data['awaiting_quantity'] = False
                return
            
            # Deduct balance
            supabase.table('users').update({'balance': balance - total_price}).eq('user_id', user_id).execute()
            
            # Get coupons
            coupons = stock_result.data[:quantity]
            coupon_codes = []
            for coupon in coupons:
                supabase.table('coupons').update({'is_used': True, 'used_by': user_id, 'used_at': datetime.now().isoformat()}).eq('id', coupon['id']).execute()
                coupon_codes.append(coupon['code'])
            
            # Create order record
            order_id = str(uuid.uuid4())[:8]
            for code in coupon_codes:
                supabase.table('orders').insert({
                    'id': order_id,
                    'user_id': user_id,
                    'coupon_code': code,
                    'amount': coupon_prices.get(coupon_type, 0),
                    'created_at': datetime.now().isoformat()
                }).execute()
            
            await update.message.reply_text(
                f"✅ Purchase Successful!\n\n"
                f"Your {coupon_type} coupons:\n" + "\n".join(coupon_codes) + "\n\n"
                f"Total spent: {total_price} 🪙"
            )
            
            context.user_data['awaiting_quantity'] = False
            
        except ValueError:
            await update.message.reply_text("Please send a valid number.")
    
    # Handle Amazon amount
    elif context.user_data.get('awaiting_amount'):
        try:
            amount = int(text)
            if amount < 30:
                await update.message.reply_text("Minimum amount is 30. Please enter a higher amount.")
                return
            
            context.user_data['payment_amount'] = amount
            context.user_data['awaiting_amount'] = False
            
            # Show order summary
            summary = (
                f"📝 Order Summary:\n"
                f"━━━━━━━━━━━━━━━\n"
                f"💹 Rate: 1 Rs = 1 Diamond 🪙\n"
                f"💵 Amount: ₹{amount}\n"
                f"🪙 Coins to Receive: {amount} 🪙\n"
                f"💳 Method: Amazon Gift Card\n"
                f"📅 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"━━━━━━━━━━━━━━━\n\n"
                f"Click below to proceed."
            )
            
            keyboard = [[InlineKeyboardButton("Submit Gift Card", callback_data="submit_giftcard")]]
            await update.message.reply_text(summary, reply_markup=InlineKeyboardMarkup(keyboard))
            
        except ValueError:
            await update.message.reply_text("Please send a valid number.")
    
    # Handle UPI amount
    elif context.user_data.get('awaiting_upi_amount'):
        try:
            amount = int(text)
            if amount < 30:
                await update.message.reply_text("Minimum amount is 30. Please enter a higher amount.")
                return
            
            context.user_data['payment_amount'] = amount
            context.user_data['awaiting_upi_amount'] = False
            
            # Get QR code from database
            qr_result = supabase.table('settings').select('qr_code_id').eq('key', 'upi_qr').execute()
            qr_id = qr_result.data[0]['qr_code_id'] if qr_result.data else None
            
            order_id = str(uuid.uuid4())[:8]
            context.user_data['order_id'] = order_id
            
            payment_text = (
                f"💳 Payment Request\n\n"
                f"🎫 Order No: {order_id}\n"
                f"💰 Amount: ₹{amount}\n\n"
                f"✅ After payment, click 'I Have Paid' below"
            )
            
            keyboard = [[InlineKeyboardButton("I Have Paid", callback_data="paid_upi")]]
            
            if qr_id:
                await update.message.reply_photo(
                    photo=qr_id,
                    caption=payment_text,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await update.message.reply_text(
                    payment_text + "\n\n(QR code not available, please contact support)",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            
        except ValueError:
            await update.message.reply_text("Please send a valid number.")
    
    # Handle gift card code
    elif context.user_data.get('awaiting_giftcard'):
        context.user_data['giftcard_code'] = text
        context.user_data['awaiting_giftcard'] = False
        await update.message.reply_text("📸 Now upload a screenshot of the gift card:")
        context.user_data['awaiting_screenshot'] = True
    
    # Handle UPI payer name
    elif context.user_data.get('awaiting_payer_name'):
        context.user_data['payer_name'] = text
        context.user_data['awaiting_payer_name'] = False
        await update.message.reply_text("📸 Now upload a screenshot of payment:")
        context.user_data['awaiting_upi_screenshot'] = True
    
    # Admin: Add coupons
    elif context.user_data.get('awaiting_coupons'):
        coupon_type = context.user_data.get('admin_coupon_type')
        coupons = text.split('\n')
        
        for code in coupons:
            code = code.strip()
            if code:
                supabase.table('coupons').insert({
                    'code': code,
                    'type': coupon_type,
                    'is_used': False,
                    'created_at': datetime.now().isoformat()
                }).execute()
        
        await update.message.reply_text(f"✅ {len(coupons)} coupons added successfully!")
        context.user_data['awaiting_coupons'] = False
    
    # Admin: Remove coupons
    elif context.user_data.get('awaiting_remove_quantity'):
        try:
            quantity = int(text)
            coupon_type = context.user_data.get('admin_coupon_type')
            
            # Get coupons to remove
            result = supabase.table('coupons').select('*').eq('type', coupon_type).eq('is_used', False).limit(quantity).execute()
            
            if len(result.data) < quantity:
                await update.message.reply_text(f"Not enough coupons! Available: {len(result.data)}")
                return
            
            # Delete coupons
            for coupon in result.data:
                supabase.table('coupons').delete().eq('id', coupon['id']).execute()
            
            await update.message.reply_text(f"✅ {quantity} coupons removed successfully!")
            context.user_data['awaiting_remove_quantity'] = False
            
        except ValueError:
            await update.message.reply_text("Please send a valid number.")
    
    # Admin: Change price
    elif context.user_data.get('awaiting_price'):
        try:
            price = int(text)
            coupon_type = context.user_data.get('admin_coupon_type')
            
            # Update price in database
            supabase.table('prices').upsert({
                'type': coupon_type,
                'price': price,
                'updated_at': datetime.now().isoformat()
            }).execute()
            
            # Update local variable
            coupon_prices[coupon_type] = price
            
            await update.message.reply_text(f"✅ Price for {coupon_type} changed to {price} successfully!")
            context.user_data['awaiting_price'] = False
            
        except ValueError:
            await update.message.reply_text("Please send a valid number.")
    
    # Admin: Update QR
    elif context.user_data.get('awaiting_qr') and update.message.photo:
        photo = update.message.photo[-1]
        file_id = photo.file_id
        
        # Save QR code to database
        supabase.table('settings').upsert({
            'key': 'upi_qr',
            'qr_code_id': file_id,
            'updated_at': datetime.now().isoformat()
        }).execute()
        
        await update.message.reply_text("✅ QR code updated successfully!")
        context.user_data['awaiting_qr'] = False

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Handle gift card screenshot
    if context.user_data.get('awaiting_screenshot'):
        photo = update.message.photo[-1]
        file_id = photo.file_id
        
        # Create pending order
        order_id = str(uuid.uuid4())
        supabase.table('pending_orders').insert({
            'id': order_id,
            'user_id': user_id,
            'amount': context.user_data.get('payment_amount'),
            'giftcard_code': context.user_data.get('giftcard_code'),
            'screenshot_id': file_id,
            'payment_method': 'amazon',
            'status': 'pending',
            'created_at': datetime.now().isoformat()
        }).execute()
        
        # Notify admin
        user_info = await context.bot.get_chat(user_id)
        admin_message = (
            f"🔔 New Amazon Gift Card Order!\n\n"
            f"👤 User: {user_info.full_name} (@{user_info.username})\n"
            f"🆔 User ID: {user_id}\n"
            f"💰 Amount: ₹{context.user_data.get('payment_amount')}\n"
            f"🎫 Gift Card Code: {context.user_data.get('giftcard_code')}\n"
            f"📅 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=file_id,
            caption=admin_message,
            reply_markup=get_admin_approval_keyboard(order_id, 'amazon')
        )
        
        await update.message.reply_text(
            "✅ Your request has been submitted! Please wait for admin approval."
        )
        
        context.user_data['awaiting_screenshot'] = False
    
    # Handle UPI screenshot
    elif context.user_data.get('awaiting_upi_screenshot'):
        photo = update.message.photo[-1]
        file_id = photo.file_id
        
        # Create pending order
        order_id = str(uuid.uuid4())
        supabase.table('pending_orders').insert({
            'id': order_id,
            'user_id': user_id,
            'amount': context.user_data.get('payment_amount'),
            'payer_name': context.user_data.get('payer_name'),
            'screenshot_id': file_id,
            'payment_method': 'upi',
            'status': 'pending',
            'created_at': datetime.now().isoformat()
        }).execute()
        
        # Notify admin
        user_info = await context.bot.get_chat(user_id)
        admin_message = (
            f"🔔 New UPI Payment Order!\n\n"
            f"👤 User: {user_info.full_name} (@{user_info.username})\n"
            f"🆔 User ID: {user_id}\n"
            f"💰 Amount: ₹{context.user_data.get('payment_amount')}\n"
            f"👤 Payer Name: {context.user_data.get('payer_name')}\n"
            f"📅 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        
        await context.bot.send_photo(
            chat_id=ADMIN_ID,
            photo=file_id,
            caption=admin_message,
            reply_markup=get_admin_approval_keyboard(order_id, 'upi')
        )
        
        await update.message.reply_text(
            "✅ Your request has been submitted! Please wait for admin approval."
        )
        
        context.user_data['awaiting_upi_screenshot'] = False

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "submit_giftcard":
        await query.edit_message_text("Enter your Amazon Gift Card code:")
        context.user_data['awaiting_giftcard'] = True
    
    elif data == "paid_upi":
        await query.edit_message_text("Send the payer name (person who paid):")
        context.user_data['awaiting_payer_name'] = True

def main():
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(CallbackQueryHandler(handle_callback, pattern="^(submit_giftcard|paid_upi)$"))
    
    # Start bot
    application.run_polling()

if __name__ == '__main__':
    main()