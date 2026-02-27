import os
import logging
import psycopg2
import psycopg2.extras
from datetime import datetime
import uuid
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Telegram imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# Bot token and admin ID
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0'))

# Database connection string
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    # Fallback to individual components if DATABASE_URL not set
    DB_HOST = os.getenv('DB_HOST', 'aws-1-ap-south-1.pooler.supabase.com')
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_NAME = os.getenv('DB_NAME', 'postgres')
    DB_USER = os.getenv('DB_USER', 'postgres.epfnnoqxjierfaizufbo')
    DB_PASSWORD = os.getenv('DB_PASSWORD')
    
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
AMOUNT, GIFT_CARD_CODE, GIFT_CARD_SCREENSHOT, UPI_AMOUNT, UPI_PAYER_NAME, UPI_SCREENSHOT, COUPON_QUANTITY, ADMIN_COUPON_INPUT, ADMIN_REMOVE_QUANTITY, ADMIN_PRICE_INPUT = range(10)

# Database connection functions
def get_db_connection():
    """Get a database connection"""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        raise

def execute_query(query, params=None, fetch='none'):
    """
    Execute a database query
    fetch: 'none' for no results (CREATE, INSERT, UPDATE, DELETE)
           'one' for single row
           'all' for multiple rows
    """
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(query, params or ())
        
        if fetch == 'all':
            result = cur.fetchall()
        elif fetch == 'one':
            result = cur.fetchone()
        else:  # 'none' - for queries that don't return results
            result = None
            
        conn.commit()
        cur.close()
        return result
    except Exception as e:
        logger.error(f"Database error: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

def execute_insert(query, params=None):
    """Execute an insert query"""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(query, params or ())
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        logger.error(f"Database insert error: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

# Initialize database tables
def init_database():
    """Create tables if they don't exist"""
    try:
        logger.info("Initializing database tables...")
        
        # Users table - use execute_query with fetch='none' for CREATE statements
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
        logger.info("✅ Users table ready")
        
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
        logger.info("✅ Coupons table ready")
        
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
        logger.info("✅ Orders table ready")
        
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
        logger.info("✅ Pending orders table ready")
        
        # Settings table
        execute_query("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                qr_code_id TEXT,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """, fetch='none')
        logger.info("✅ Settings table ready")
        
        # Prices table
        execute_query("""
            CREATE TABLE IF NOT EXISTS prices (
                type TEXT PRIMARY KEY,
                price INTEGER NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """, fetch='none')
        logger.info("✅ Prices table ready")
        
        # Insert default prices if not exists
        default_prices = [('500', 500), ('1K', 1000), ('2K', 2000), ('4K', 4000)]
        for price_type, price in default_prices:
            # Check if price exists
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
                logger.info(f"✅ Default price for {price_type} inserted")
        
        logger.info("✅ Database tables initialized successfully")
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")
        raise

# Initialize database on startup
try:
    init_database()
    logger.info("✅ Connected to database successfully!")
except Exception as e:
    logger.error(f"❌ Database connection failed: {e}")
    raise

# [REST OF YOUR BOT CODE REMAINS THE SAME - all the keyboard functions, handlers, etc.]

# User menu keyboard
def get_user_keyboard():
    keyboard = [
        [KeyboardButton("💰 Add Coins"), KeyboardButton("🎟️ Buy Coupon")],
        [KeyboardButton("👤 Balance"), KeyboardButton("📦 My Orders")],
        [KeyboardButton("🆘 Support"), KeyboardButton("⚠️ Disclaimer")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# Admin menu keyboard
def get_admin_keyboard():
    keyboard = [
        [KeyboardButton("➕ Add Coupon"), KeyboardButton("➖ Remove Coupon")],
        [KeyboardButton("📊 Stock"), KeyboardButton("💰 Change Prices")],
        [KeyboardButton("🔄 Update QR"), KeyboardButton("📋 Last 10 Buyers")],
        [KeyboardButton("👑 Admin Panel"), KeyboardButton("🔙 Back to User Menu")]
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

# Start command
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
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
            (user_id, user.username, user.first_name, user.last_name)
        )
    
    # Show appropriate keyboard (admin gets extra option)
    if user_id == ADMIN_ID:
        keyboard = [
            [KeyboardButton("💰 Add Coins"), KeyboardButton("🎟️ Buy Coupon")],
            [KeyboardButton("👤 Balance"), KeyboardButton("📦 My Orders")],
            [KeyboardButton("🆘 Support"), KeyboardButton("⚠️ Disclaimer")],
            [KeyboardButton("👑 Admin Panel")]
        ]
        await update.message.reply_text(
            f"Welcome To The AutoEarnX Selling Bot, {user.first_name}! 🚀\n\n"
            f"Use the buttons below to navigate:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
    else:
        await update.message.reply_text(
            f"Welcome To The AutoEarnX Selling Bot, {user.first_name}! 🚀\n\n"
            f"Use the buttons below to navigate:",
            reply_markup=get_user_keyboard()
        )

# Handle user menu
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
    
    # Admin panel
    elif text == "👑 Admin Panel" and user_id == ADMIN_ID:
        await update.message.reply_text(
            "Welcome to Admin Panel!",
            reply_markup=get_admin_keyboard()
        )

# Handle admin menu
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
            result = execute_query(
                "SELECT COUNT(*) as count FROM coupons WHERE type = %s AND is_used = FALSE",
                (coupon_type,),
                fetch='one'
            )
            stocks[coupon_type] = result['count'] if result else 0
        
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
        result = execute_query("""
            SELECT o.*, u.username, u.first_name 
            FROM orders o 
            JOIN users u ON o.user_id = u.user_id 
            ORDER BY o.created_at DESC 
            LIMIT 10
        """)
        
        if result:
            buyers_text = "📋 Last 10 Buyers:\n\n"
            for order in result:
                buyers_text += f"👤 User: {order['first_name']} (@{order['username'] or 'N/A'})\n"
                buyers_text += f"🎟️ Coupon: {order['coupon_code']}\n"
                buyers_text += f"💰 Amount: {order['amount']} 🪙\n"
                buyers_text += f"📅 Date: {order['created_at'][:10]}\n"
                buyers_text += "━━━━━━━━━━━━━━━\n"
            await update.message.reply_text(buyers_text)
        else:
            await update.message.reply_text("No orders found.")
    
    elif text == "🔙 Back to User Menu":
        # Show admin the user menu with admin panel option
        keyboard = [
            [KeyboardButton("💰 Add Coins"), KeyboardButton("🎟️ Buy Coupon")],
            [KeyboardButton("👤 Balance"), KeyboardButton("📦 My Orders")],
            [KeyboardButton("🆘 Support"), KeyboardButton("⚠️ Disclaimer")],
            [KeyboardButton("👑 Admin Panel")]
        ]
        await update.message.reply_text(
            "Returning to user menu...",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )

# Handle button callbacks
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = query.from_user.id
    
    # Get user balance
    result = execute_query(
        "SELECT balance FROM users WHERE user_id = %s",
        (user_id,),
        fetch='one'
    )
    balance = result['balance'] if result else 0
    
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
        
        # Get stock
        stock_result = execute_query(
            "SELECT COUNT(*) as count FROM coupons WHERE type = %s AND is_used = FALSE",
            (coupon_type,),
            fetch='one'
        )
        stock = stock_result['count'] if stock_result else 0
        
        # Get price
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
            "Main Menu"
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
                "Admin Panel"
            )
    
    # Admin approval/rejection
    elif data.startswith("approve_"):
        parts = data.split('_')
        order_id = parts[1]
        payment_method = parts[2]
        
        # Get order details
        order_result = execute_query(
            "SELECT * FROM pending_orders WHERE id = %s",
            (order_id,),
            fetch='one'
        )
        
        if order_result:
            user_id = order_result['user_id']
            amount = order_result['amount']
            
            # Add balance to user
            execute_query(
                "UPDATE users SET balance = balance + %s WHERE user_id = %s",
                (amount, user_id)
            )
            
            # Update order status
            execute_query(
                "UPDATE pending_orders SET status = 'approved' WHERE id = %s",
                (order_id,)
            )
            
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
        execute_query(
            "UPDATE pending_orders SET status = 'declined' WHERE id = %s",
            (order_id,)
        )
        
        # Get user_id
        order_result = execute_query(
            "SELECT user_id FROM pending_orders WHERE id = %s",
            (order_id,),
            fetch='one'
        )
        
        if order_result:
            user_id = order_result['user_id']
            
            # Notify user
            await context.bot.send_message(
                user_id,
                "❌ Your payment has been declined. Please contact support for more information."
            )
        
        await query.edit_message_text(f"Order {order_id} declined!")

# Handle additional callbacks
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

# Handle messages
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    # Handle admin menu
    if user_id == ADMIN_ID and text in ["👑 Admin Panel", "➕ Add Coupon", "➖ Remove Coupon", "📊 Stock", "💰 Change Prices", "🔄 Update QR", "📋 Last 10 Buyers", "🔙 Back to User Menu"]:
        await handle_admin_menu(update, context)
        return
    
    # Handle regular user menu
    if text in ["💰 Add Coins", "🎟️ Buy Coupon", "👤 Balance", "📦 My Orders", "🆘 Support", "⚠️ Disclaimer"]:
        await handle_user_menu(update, context)
        return
    
    # Handle coupon quantity
    if context.user_data.get('awaiting_quantity'):
        try:
            quantity = int(text)
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
                await update.message.reply_text(f"❌ Not enough diamonds! Available: {balance} 🪙")
                context.user_data['awaiting_quantity'] = False
                return
            
            # Deduct balance
            execute_query(
                "UPDATE users SET balance = balance - %s WHERE user_id = %s",
                (total_price, user_id)
            )
            
            # Get coupons
            coupons_result = execute_query(
                "SELECT * FROM coupons WHERE type = %s AND is_used = FALSE LIMIT %s",
                (coupon_type, quantity)
            )
            
            coupon_codes = []
            for coupon in coupons_result:
                # Mark as used
                execute_query(
                    "UPDATE coupons SET is_used = TRUE, used_by = %s, used_at = NOW() WHERE id = %s",
                    (user_id, coupon['id'])
                )
                coupon_codes.append(coupon['code'])
            
            # Create order record
            order_id = str(uuid.uuid4())[:8]
            for code in coupon_codes:
                execute_query(
                    "INSERT INTO orders (id, user_id, coupon_code, amount, created_at) VALUES (%s, %s, %s, %s, NOW())",
                    (order_id, user_id, code, price)
                )
            
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
        coupons = text.split('\n')
        
        for code in coupons:
            code = code.strip()
            if code:
                try:
                    execute_query(
                        "INSERT INTO coupons (code, type, is_used, created_at) VALUES (%s, %s, FALSE, NOW())",
                        (code, coupon_type)
                    )
                except Exception as e:
                    logger.error(f"Failed to insert coupon {code}: {e}")
                    await update.message.reply_text(f"⚠️ Failed to insert coupon: {code}")
        
        await update.message.reply_text(f"✅ {len(coupons)} coupons added successfully!")
        context.user_data['awaiting_coupons'] = False
    
    # Admin: Remove coupons
    elif context.user_data.get('awaiting_remove_quantity'):
        try:
            quantity = int(text)
            coupon_type = context.user_data.get('admin_coupon_type')
            
            # Get coupons to remove
            coupons_result = execute_query(
                "SELECT id FROM coupons WHERE type = %s AND is_used = FALSE LIMIT %s",
                (coupon_type, quantity)
            )
            
            if len(coupons_result) < quantity:
                await update.message.reply_text(f"Not enough coupons! Available: {len(coupons_result)}")
                return
            
            # Delete coupons
            for coupon in coupons_result:
                execute_query(
                    "DELETE FROM coupons WHERE id = %s",
                    (coupon['id'],)
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
            
            # Update price
            execute_query(
                "UPDATE prices SET price = %s, updated_at = NOW() WHERE type = %s",
                (price, coupon_type)
            )
            
            await update.message.reply_text(f"✅ Price for {coupon_type} changed to {price} successfully!")
            context.user_data['awaiting_price'] = False
            
        except ValueError:
            await update.message.reply_text("Please send a valid number.")
    
    # Admin: Update QR
    elif context.user_data.get('awaiting_qr') and update.message.photo:
        photo = update.message.photo[-1]
        file_id = photo.file_id
        
        # Save QR code to database
        execute_query(
            "INSERT INTO settings (key, qr_code_id, updated_at) VALUES (%s, %s, NOW()) ON CONFLICT (key) DO UPDATE SET qr_code_id = %s, updated_at = NOW()",
            ('upi_qr', file_id, file_id)
        )
        
        await update.message.reply_text("✅ QR code updated successfully!")
        context.user_data['awaiting_qr'] = False

# Handle photos
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Handle gift card screenshot
    if context.user_data.get('awaiting_screenshot'):
        photo = update.message.photo[-1]
        file_id = photo.file_id
        
        # Create pending order
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
            )
        )
        
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
            )
        )
        
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
    
    # Handle admin QR code update
    elif context.user_data.get('awaiting_qr'):
        photo = update.message.photo[-1]
        file_id = photo.file_id
        
        # Save QR code to database
        execute_query(
            "INSERT INTO settings (key, qr_code_id, updated_at) VALUES (%s, %s, NOW()) ON CONFLICT (key) DO UPDATE SET qr_code_id = %s, updated_at = NOW()",
            ('upi_qr', file_id, file_id)
        )
        
        await update.message.reply_text("✅ QR code updated successfully!")
        context.user_data['awaiting_qr'] = False

# Main function
def main():
    """Start the bot."""
    # Create application
    application = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(CallbackQueryHandler(button_callback, pattern="^(terms_agree|terms_decline|coupon_|payment_|back_to_menu|admin_|approve_|decline_).*$"))
    application.add_handler(CallbackQueryHandler(handle_callback, pattern="^(submit_giftcard|paid_upi)$"))
    
    # Start bot
    print("🤖 Bot is starting...")
    application.run_polling()

if __name__ == '__main__':
    main()
