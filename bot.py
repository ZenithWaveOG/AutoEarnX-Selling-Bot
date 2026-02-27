import os
import logging
import psycopg2
import psycopg2.extras
from datetime import datetime
import uuid
import json
from dotenv import load_dotenv
from flask import Flask, request, jsonify

# Load environment variables
load_dotenv()

# Telegram imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.ext import ApplicationBuilder

# Bot token and admin ID
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))
WEBHOOK_URL = os.getenv('WEBHOOK_URL')  # Your Render.com URL + /webhook

# Database connection string
DATABASE_URL = os.getenv('DATABASE_URL')

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Initialize bot application
telegram_app = ApplicationBuilder().token(BOT_TOKEN).build()

# Database connection functions
def get_db_connection():
    """Get a database connection with retry logic"""
    retries = 3
    for i in range(retries):
        try:
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=5)
            return conn
        except Exception as e:
            logger.error(f"Database connection attempt {i+1} failed: {e}")
            if i == retries - 1:
                raise
            import time
            time.sleep(2)

def execute_query(query, params=None, fetch='none'):
    """Execute a database query with proper error handling"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(query, params or ())
        
        if fetch == 'all':
            result = cur.fetchall()
        elif fetch == 'one':
            result = cur.fetchone()
        else:
            result = None
            
        conn.commit()
        cur.close()
        return result
    except Exception as e:
        logger.error(f"Database error: {e}")
        if conn:
            conn.rollback()
        return None if fetch in ['one', 'all'] else None
    finally:
        if conn:
            conn.close()

# Initialize database tables
def init_database():
    """Create tables if they don't exist"""
    try:
        logger.info("Initializing database tables...")
        
        # Users table
        execute_query("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                user_id BIGINT UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                balance INTEGER DEFAULT 0,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """, fetch='none')
        
        # Coupons table
        execute_query("""
            CREATE TABLE IF NOT EXISTS coupons (
                id SERIAL PRIMARY KEY,
                code TEXT UNIQUE NOT NULL,
                type TEXT NOT NULL,
                is_used BOOLEAN DEFAULT FALSE,
                used_by BIGINT,
                used_at TIMESTAMP WITH TIME ZONE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """, fetch='none')
        
        # Orders table
        execute_query("""
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                coupon_code TEXT NOT NULL,
                amount INTEGER NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """, fetch='none')
        
        # Pending orders table
        execute_query("""
            CREATE TABLE IF NOT EXISTS pending_orders (
                id TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount INTEGER NOT NULL,
                giftcard_code TEXT,
                payer_name TEXT,
                screenshot_id TEXT,
                payment_method TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """, fetch='none')
        
        # Settings table
        execute_query("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                qr_code_id TEXT,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """, fetch='none')
        
        # Prices table
        execute_query("""
            CREATE TABLE IF NOT EXISTS prices (
                type TEXT PRIMARY KEY,
                price INTEGER NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """, fetch='none')
        
        # Insert default prices if not exists
        default_prices = [('500', 500), ('1K', 1000), ('2K', 2000), ('4K', 4000)]
        for price_type, price in default_prices:
            result = execute_query(
                "SELECT * FROM prices WHERE type = %s",
                (price_type,),
                fetch='one'
            )
            if not result:
                execute_query(
                    "INSERT INTO prices (type, price, updated_at) VALUES (%s, %s, NOW())",
                    (price_type, price),
                    fetch='none'
                )
        
        logger.info("✅ Database tables initialized successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")
        return False

# Initialize database on startup
init_database()

# Keyboard functions
def get_user_keyboard():
    keyboard = [
        [KeyboardButton("💰 Add Coins"), KeyboardButton("🎟️ Buy Coupon")],
        [KeyboardButton("👤 Balance"), KeyboardButton("📦 My Orders")],
        [KeyboardButton("🆘 Support"), KeyboardButton("⚠️ Disclaimer")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_admin_keyboard():
    keyboard = [
        [KeyboardButton("➕ Add Coupon"), KeyboardButton("➖ Remove Coupon")],
        [KeyboardButton("📊 Stock"), KeyboardButton("💰 Change Prices")],
        [KeyboardButton("🔄 Update QR"), KeyboardButton("📋 Last 10 Buyers")],
        [KeyboardButton("👑 Admin Panel"), KeyboardButton("🔙 Back to User Menu")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_coupon_type_keyboard():
    keyboard = [
        [InlineKeyboardButton("500 🪙", callback_data="coupon_500")],
        [InlineKeyboardButton("1K 🪙", callback_data="coupon_1K")],
        [InlineKeyboardButton("2K 🪙", callback_data="coupon_2K")],
        [InlineKeyboardButton("4K 🪙", callback_data="coupon_4K")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_terms_keyboard():
    keyboard = [
        [InlineKeyboardButton("✅ Agree", callback_data="terms_agree")],
        [InlineKeyboardButton("❌ Decline", callback_data="terms_decline")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_payment_keyboard():
    keyboard = [
        [InlineKeyboardButton("🎁 Amazon Gift Card", callback_data="payment_amazon")],
        [InlineKeyboardButton("📱 UPI", callback_data="payment_upi")],
        [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_coupon_keyboard(action):
    keyboard = [
        [InlineKeyboardButton("500", callback_data=f"admin_{action}_500")],
        [InlineKeyboardButton("1K", callback_data=f"admin_{action}_1K")],
        [InlineKeyboardButton("2K", callback_data=f"admin_{action}_2K")],
        [InlineKeyboardButton("4K", callback_data=f"admin_{action}_4K")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_back")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_admin_approval_keyboard(order_id, payment_method):
    keyboard = [
        [InlineKeyboardButton("✅ Accept", callback_data=f"approve_{order_id}_{payment_method}")],
        [InlineKeyboardButton("❌ Decline", callback_data=f"decline_{order_id}_{payment_method}")]
    ]
    return InlineKeyboardMarkup(keyboard)

# Bot Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    try:
        # Check if user exists
        result = execute_query(
            "SELECT * FROM users WHERE user_id = %s",
            (user_id,),
            fetch='one'
        )
        
        if not result:
            # Create new user
            execute_query(
                """
                INSERT INTO users (user_id, username, first_name, last_name, balance, created_at)
                VALUES (%s, %s, %s, %s, 0, NOW())
                """,
                (user_id, user.username, user.first_name, user.last_name),
                fetch='none'
            )
            logger.info(f"New user created: {user_id}")
        
        # Show appropriate keyboard
        if user_id == ADMIN_ID:
            keyboard = [
                [KeyboardButton("💰 Add Coins"), KeyboardButton("🎟️ Buy Coupon")],
                [KeyboardButton("👤 Balance"), KeyboardButton("📦 My Orders")],
                [KeyboardButton("🆘 Support"), KeyboardButton("⚠️ Disclaimer")],
                [KeyboardButton("👑 Admin Panel")]
            ]
            await update.message.reply_text(
                f"Welcome To The AutoEarnX Selling Bot, {user.first_name}! 🚀",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            )
        else:
            await update.message.reply_text(
                f"Welcome To The AutoEarnX Selling Bot, {user.first_name}! 🚀",
                reply_markup=get_user_keyboard()
            )
    except Exception as e:
        logger.error(f"Error in start handler: {e}")
        await update.message.reply_text("❌ An error occurred. Please try again later.")

async def handle_user_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    
    try:
        if text == "💰 Add Coins":
            await update.message.reply_text(
                "💳 Select Payment Method:",
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
            result = execute_query(
                "SELECT balance FROM users WHERE user_id = %s",
                (user_id,),
                fetch='one'
            )
            balance = result['balance'] if result else 0
            await update.message.reply_text(f"💰 Your Balance: {balance} Diamonds 🪙")
        
        elif text == "📦 My Orders":
            result = execute_query(
                "SELECT * FROM orders WHERE user_id = %s ORDER BY created_at DESC LIMIT 10",
                (user_id,)
            )
            
            if result:
                orders_text = "📦 Your Last 10 Orders:\n\n"
                for order in result:
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
        
        elif text == "👑 Admin Panel" and user_id == ADMIN_ID:
            await update.message.reply_text(
                "🔧 Admin Panel\n\nSelect an option:",
                reply_markup=get_admin_keyboard()
            )
    except Exception as e:
        logger.error(f"Error in user menu handler: {e}")
        await update.message.reply_text("❌ An error occurred. Please try again.")

async def handle_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    
    if user_id != ADMIN_ID:
        return
    
    try:
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
            stocks = {}
            for coupon_type in ['500', '1K', '2K', '4K']:
                result = execute_query(
                    "SELECT COUNT(*) as count FROM coupons WHERE type = %s AND is_used = FALSE",
                    (coupon_type,),
                    fetch='one'
                )
                stocks[coupon_type] = result['count'] if result else 0
            
            stock_text = "📊 Current Stock:\n\n"
            stock_text += f"🔹 500 Coupons: {stocks['500']} available\n"
            stock_text += f"🔹 1K Coupons: {stocks['1K']} available\n"
            stock_text += f"🔹 2K Coupons: {stocks['2K']} available\n"
            stock_text += f"🔹 4K Coupons: {stocks['4K']} available\n"
            
            await update.message.reply_text(stock_text)
        
        elif text == "💰 Change Prices":
            context.user_data['admin_action'] = 'price'
            await update.message.reply_text(
                "Select The Options To Change The Price:",
                reply_markup=get_admin_coupon_keyboard('price')
            )
        
        elif text == "🔄 Update QR":
            context.user_data['awaiting_qr'] = True
            await update.message.reply_text("📤 Please send the new QR code image:")
        
        elif text == "📋 Last 10 Buyers":
            result = execute_query("""
                SELECT o.*, u.username, u.first_name, u.user_id
                FROM orders o 
                JOIN users u ON o.user_id = u.user_id 
                ORDER BY o.created_at DESC 
                LIMIT 10
            """)
            
            if result:
                buyers_text = "📋 Last 10 Buyers:\n\n"
                for i, order in enumerate(result, 1):
                    buyers_text += f"{i}. 👤 {order['first_name']} (@{order['username'] or 'N/A'})\n"
                    buyers_text += f"   🎟️ Coupon: {order['coupon_code']}\n"
                    buyers_text += f"   💰 Amount: {order['amount']} 🪙\n"
                    buyers_text += f"   📅 Date: {order['created_at'][:19]}\n"
                    buyers_text += "━━━━━━━━━━━━━━━\n"
                await update.message.reply_text(buyers_text)
            else:
                await update.message.reply_text("📭 No orders found yet.")
        
        elif text == "🔙 Back to User Menu":
            keyboard = [
                [KeyboardButton("💰 Add Coins"), KeyboardButton("🎟️ Buy Coupon")],
                [KeyboardButton("👤 Balance"), KeyboardButton("📦 My Orders")],
                [KeyboardButton("🆘 Support"), KeyboardButton("⚠️ Disclaimer")],
                [KeyboardButton("👑 Admin Panel")]
            ]
            await update.message.reply_text(
                "🔙 Returning to user menu...",
                reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            )
    except Exception as e:
        logger.error(f"Error in admin menu handler: {e}")
        await update.message.reply_text("❌ An error occurred. Please try again.")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    try:
        if data == "terms_agree":
            await query.edit_message_text(
                "🛒 Select a coupon type:",
                reply_markup=get_coupon_type_keyboard()
            )
        
        elif data == "terms_decline":
            await query.edit_message_text("Thanks For Using The Bot, GoodBye! 👋")
        
        elif data.startswith("coupon_"):
            coupon_type = data.replace("coupon_", "")
            context.user_data['selected_coupon'] = coupon_type
            
            # Get stock and price
            stock_result = execute_query(
                "SELECT COUNT(*) as count FROM coupons WHERE type = %s AND is_used = FALSE",
                (coupon_type,),
                fetch='one'
            )
            stock = stock_result['count'] if stock_result else 0
            
            price_result = execute_query(
                "SELECT price FROM prices WHERE type = %s",
                (coupon_type,),
                fetch='one'
            )
            price = price_result['price'] if price_result else 0
            
            if stock == 0:
                await query.edit_message_text(f"❌ Not enough stock! Available: 0")
                return
            
            await query.edit_message_text(
                f"📦 {coupon_type} Coupons\n\n"
                f"💎 Price per coupon: {price} 🪙\n"
                f"📊 Available stock: {stock}\n\n"
                f"How many {coupon_type} coupons do you want to buy?\n"
                f"Please send the quantity:"
            )
            context.user_data['awaiting_quantity'] = True
        
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
            await query.edit_message_text("Main Menu")
        
        elif data == "submit_giftcard":
            await query.edit_message_text("Enter your Amazon Gift Card code:")
            context.user_data['awaiting_giftcard'] = True
        
        elif data == "paid_upi":
            await query.edit_message_text("Send the payer name (person who paid):")
            context.user_data['awaiting_payer_name'] = True
        
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
                await query.edit_message_text("Admin Panel")
        
        elif data.startswith("approve_"):
            parts = data.split('_')
            order_id = parts[1]
            payment_method = parts[2]
            
            order_result = execute_query(
                "SELECT * FROM pending_orders WHERE id = %s",
                (order_id,),
                fetch='one'
            )
            
            if order_result:
                user_id = order_result['user_id']
                amount = order_result['amount']
                
                execute_query(
                    "UPDATE users SET balance = balance + %s WHERE user_id = %s",
                    (amount, user_id),
                    fetch='none'
                )
                
                execute_query(
                    "UPDATE pending_orders SET status = 'approved' WHERE id = %s",
                    (order_id,),
                    fetch='none'
                )
                
                await context.bot.send_message(
                    user_id,
                    f"🎉 Congratulations! Your order has been approved!\n"
                    f"💰 {amount} Diamonds have been added to your balance."
                )
                
                await query.edit_message_text(f"✅ Order approved successfully!")
        
        elif data.startswith("decline_"):
            parts = data.split('_')
            order_id = parts[1]
            payment_method = parts[2]
            
            execute_query(
                "UPDATE pending_orders SET status = 'declined' WHERE id = %s",
                (order_id,),
                fetch='none'
            )
            
            order_result = execute_query(
                "SELECT user_id FROM pending_orders WHERE id = %s",
                (order_id,),
                fetch='one'
            )
            
            if order_result:
                user_id = order_result['user_id']
                await context.bot.send_message(
                    user_id,
                    "❌ Your payment has been declined. Please contact support."
                )
            
            await query.edit_message_text(f"❌ Order declined!")
    
    except Exception as e:
        logger.error(f"Error in button callback: {e}")
        await query.edit_message_text("❌ An error occurred. Please try again.")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    try:
        # Handle admin menu options
        admin_options = ["➕ Add Coupon", "➖ Remove Coupon", "📊 Stock", "💰 Change Prices", 
                         "🔄 Update QR", "📋 Last 10 Buyers", "🔙 Back to User Menu"]
        
        if text in admin_options and user_id == ADMIN_ID:
            await handle_admin_menu(update, context)
            return
        
        # Handle user menu options
        user_options = ["💰 Add Coins", "🎟️ Buy Coupon", "👤 Balance", "📦 My Orders", 
                        "🆘 Support", "⚠️ Disclaimer", "👑 Admin Panel"]
        
        if text in user_options:
            await handle_user_menu(update, context)
            return
        
        # Handle coupon quantity
        if context.user_data.get('awaiting_quantity'):
            try:
                quantity = int(text)
                if quantity <= 0:
                    await update.message.reply_text("❌ Please enter a valid positive number.")
                    return
                
                coupon_type = context.user_data.get('selected_coupon')
                
                # Check stock
                stock_result = execute_query(
                    "SELECT COUNT(*) as count FROM coupons WHERE type = %s AND is_used = FALSE",
                    (coupon_type,),
                    fetch='one'
                )
                stock = stock_result['count'] if stock_result else 0
                
                if quantity > stock:
                    await update.message.reply_text(f"❌ Not enough stock! Available: {stock}")
                    context.user_data['awaiting_quantity'] = False
                    return
                
                # Get price
                price_result = execute_query(
                    "SELECT price FROM prices WHERE type = %s",
                    (coupon_type,),
                    fetch='one'
                )
                price = price_result['price'] if price_result else 0
                total_price = price * quantity
                
                # Check balance
                balance_result = execute_query(
                    "SELECT balance FROM users WHERE user_id = %s",
                    (user_id,),
                    fetch='one'
                )
                balance = balance_result['balance'] if balance_result else 0
                
                if balance < total_price:
                    await update.message.reply_text(f"❌ Not enough diamonds! Available: {balance} 🪙, Needed: {total_price} 🪙")
                    context.user_data['awaiting_quantity'] = False
                    return
                
                # Start transaction
                # Deduct balance
                execute_query(
                    "UPDATE users SET balance = balance - %s WHERE user_id = %s",
                    (total_price, user_id),
                    fetch='none'
                )
                
                # Get coupons
                coupons_result = execute_query(
                    "SELECT * FROM coupons WHERE type = %s AND is_used = FALSE LIMIT %s",
                    (coupon_type, quantity)
                )
                
                if not coupons_result or len(coupons_result) < quantity:
                    # Refund if coupon retrieval fails
                    execute_query(
                        "UPDATE users SET balance = balance + %s WHERE user_id = %s",
                        (total_price, user_id),
                        fetch='none'
                    )
                    await update.message.reply_text("❌ Error: Could not retrieve coupons. Your balance has been refunded.")
                    context.user_data['awaiting_quantity'] = False
                    return
                
                coupon_codes = []
                order_id = str(uuid.uuid4())[:8]
                
                for coupon in coupons_result:
                    # Mark as used
                    execute_query(
                        "UPDATE coupons SET is_used = TRUE, used_by = %s, used_at = NOW() WHERE id = %s",
                        (user_id, coupon['id']),
                        fetch='none'
                    )
                    coupon_codes.append(coupon['code'])
                    
                    # Create order record
                    execute_query(
                        "INSERT INTO orders (id, user_id, coupon_code, amount, created_at) VALUES (%s, %s, %s, %s, NOW())",
                        (order_id, user_id, coupon['code'], price),
                        fetch='none'
                    )
                
                # Get updated balance
                new_balance_result = execute_query(
                    "SELECT balance FROM users WHERE user_id = %s",
                    (user_id,),
                    fetch='one'
                )
                new_balance = new_balance_result['balance'] if new_balance_result else 0
                
                # Send success message
                coupons_text = "\n".join(coupon_codes)
                await update.message.reply_text(
                    f"✅ Purchase Successful!\n\n"
                    f"🎟️ Coupon Type: {coupon_type}\n"
                    f"📦 Quantity: {quantity}\n"
                    f"💰 Total Spent: {total_price} 🪙\n"
                    f"💎 New Balance: {new_balance} 🪙\n\n"
                    f"Your Coupons:\n{coupons_text}"
                )
                
                context.user_data['awaiting_quantity'] = False
                context.user_data.pop('selected_coupon', None)
                
            except ValueError:
                await update.message.reply_text("❌ Please send a valid number.")
            except Exception as e:
                logger.error(f"Error processing coupon quantity: {e}")
                await update.message.reply_text("❌ An error occurred. Please try again.")
                context.user_data['awaiting_quantity'] = False
        
        # Handle Amazon amount
        elif context.user_data.get('awaiting_amount'):
            try:
                amount = int(text)
                if amount < 30:
                    await update.message.reply_text("Minimum amount is 30. Please enter a higher amount.")
                    return
                
                context.user_data['payment_amount'] = amount
                context.user_data['awaiting_amount'] = False
                
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
                
                qr_result = execute_query(
                    "SELECT qr_code_id FROM settings WHERE key = 'upi_qr'",
                    fetch='one'
                )
                qr_id = qr_result['qr_code_id'] if qr_result else None
                
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
            coupons = [c.strip() for c in text.split('\n') if c.strip()]
            success_count = 0
            
            for code in coupons:
                try:
                    execute_query(
                        "INSERT INTO coupons (code, type, is_used, created_at) VALUES (%s, %s, FALSE, NOW())",
                        (code, coupon_type),
                        fetch='none'
                    )
                    success_count += 1
                except Exception as e:
                    logger.error(f"Failed to insert coupon {code}: {e}")
            
            await update.message.reply_text(f"✅ {success_count} coupons added successfully!")
            context.user_data['awaiting_coupons'] = False
        
        # Admin: Remove coupons
        elif context.user_data.get('awaiting_remove_quantity'):
            try:
                quantity = int(text)
                coupon_type = context.user_data.get('admin_coupon_type')
                
                coupons_result = execute_query(
                    "SELECT id FROM coupons WHERE type = %s AND is_used = FALSE LIMIT %s",
                    (coupon_type, quantity)
                )
                
                if len(coupons_result) < quantity:
                    await update.message.reply_text(f"Not enough coupons! Available: {len(coupons_result)}")
                    return
                
                for coupon in coupons_result:
                    execute_query(
                        "DELETE FROM coupons WHERE id = %s",
                        (coupon['id'],),
                        fetch='none'
                    )
                
                await update.message.reply_text(f"✅ {quantity} coupons removed successfully!")
                context.user_data['awaiting_remove_quantity'] = False
                
            except ValueError:
                await update.message.reply_text("Please send a valid number.")
        
        # Admin: Change price
        elif context.user_data.get('awaiting_price'):
            try:
                price = int(text)
                coupon_type = context.user_data.get('admin_coupon_type')
                
                execute_query(
                    "UPDATE prices SET price = %s, updated_at = NOW() WHERE type = %s",
                    (price, coupon_type),
                    fetch='none'
                )
                
                await update.message.reply_text(f"✅ Price for {coupon_type} changed to {price} successfully!")
                context.user_data['awaiting_price'] = False
                
            except ValueError:
                await update.message.reply_text("Please send a valid number.")
    
    except Exception as e:
        logger.error(f"Error in message handler: {e}")
        await update.message.reply_text("❌ An error occurred. Please try again.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    try:
        # Handle admin QR code update
        if context.user_data.get('awaiting_qr') and user_id == ADMIN_ID:
            photo = update.message.photo[-1]
            file_id = photo.file_id
            
            execute_query(
                "INSERT INTO settings (key, qr_code_id, updated_at) VALUES (%s, %s, NOW()) ON CONFLICT (key) DO UPDATE SET qr_code_id = %s, updated_at = NOW()",
                ('upi_qr', file_id, file_id),
                fetch='none'
            )
            
            await update.message.reply_text("✅ QR code updated successfully!")
            context.user_data['awaiting_qr'] = False
            return
        
        # Handle gift card screenshot
        if context.user_data.get('awaiting_screenshot'):
            photo = update.message.photo[-1]
            file_id = photo.file_id
            
            order_id = str(uuid.uuid4())
            execute_query(
                """
                INSERT INTO pending_orders 
                (id, user_id, amount, giftcard_code, screenshot_id, payment_method, status, created_at) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    order_id, 
                    user_id, 
                    context.user_data.get('payment_amount'),
                    context.user_data.get('giftcard_code'),
                    file_id,
                    'amazon',
                    'pending'
                ),
                fetch='none'
            )
            
            user_info = await context.bot.get_chat(user_id)
            admin_message = (
                f"🔔 New Amazon Gift Card Order!\n\n"
                f"👤 User: {user_info.full_name} (@{user_info.username})\n"
                f"🆔 User ID: {user_id}\n"
                f"💰 Amount: ₹{context.user_data.get('payment_amount')}\n"
                f"🎫 Gift Card Code: {context.user_data.get('giftcard_code')}"
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
            
            order_id = str(uuid.uuid4())
            execute_query(
                """
                INSERT INTO pending_orders 
                (id, user_id, amount, payer_name, screenshot_id, payment_method, status, created_at) 
                VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
                """,
                (
                    order_id,
                    user_id,
                    context.user_data.get('payment_amount'),
                    context.user_data.get('payer_name'),
                    file_id,
                    'upi',
                    'pending'
                ),
                fetch='none'
            )
            
            user_info = await context.bot.get_chat(user_id)
            admin_message = (
                f"🔔 New UPI Payment Order!\n\n"
                f"👤 User: {user_info.full_name} (@{user_info.username})\n"
                f"🆔 User ID: {user_id}\n"
                f"💰 Amount: ₹{context.user_data.get('payment_amount')}\n"
                f"👤 Payer Name: {context.user_data.get('payer_name')}"
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
    
    except Exception as e:
        logger.error(f"Error in photo handler: {e}")
        await update.message.reply_text("❌ An error occurred. Please try again.")

# Flask webhook endpoint
@app.route('/webhook', methods=['POST'])
def webhook():
    """Handle incoming Telegram updates"""
    try:
        update = Update.de_json(request.get_json(force=True), telegram_app.bot)
        telegram_app.update_queue.put_nowait(update)
        return 'ok', 200
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return 'error', 500

@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({'status': 'healthy'}), 200

@app.route('/set_webhook', methods=['GET'])
def set_webhook():
    """Set the webhook URL (call this once)"""
    if WEBHOOK_URL:
        telegram_app.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
        return jsonify({'status': 'webhook set', 'url': WEBHOOK_URL}), 200
    return jsonify({'error': 'WEBHOOK_URL not set'}), 400

def main():
    """Start the bot with webhook"""
    # Add handlers to application
    telegram_app.add_handler(CommandHandler("start", start))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    telegram_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    telegram_app.add_handler(CallbackQueryHandler(button_callback))
    
    # Initialize bot
    telegram_app.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
    logger.info(f"Webhook set to {WEBHOOK_URL}/webhook")
    
    # Run Flask app
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

if __name__ == '__main__':
    main()
