import telebot
import sqlite3
import logging
import os
import time
import hashlib
import threading
from telebot import types
from datetime import datetime
from threading import Lock, Thread
from dotenv import load_dotenv
from functools import wraps

# ================= XAVFSIZLIK KONFIG =================
load_dotenv()

class SecurityConfig:
    def __init__(self):
        self.API_TOKEN = os.getenv("BOT_TOKEN")
        self.CHANNEL_ID = int(os.getenv("CHANNEL_ID", "-1002449896845"))
        self.ADMIN_ID = int(os.getenv("ADMIN_ID", "2010030869"))
        self.BOT_USERNAME = os.getenv("BOT_USERNAME", "Tekin_stars_yulduz")
        self.RATE_LIMIT = 5
        self.ADMINS = [2010030869]
        
        if not self.API_TOKEN:
            raise ValueError("❌ TOKEN topilmadi!")

try:
    config = SecurityConfig()
except ValueError as e:
    print(e)
    exit(1)

# ================= RATE LIMITING =================
class RateLimiter:
    def __init__(self):
        self.user_requests = {}
        self.lock = Lock()
    
    def is_rate_limited(self, user_id, limit=5, window=5):
        now = time.time()
        with self.lock:
            if user_id not in self.user_requests:
                self.user_requests[user_id] = []
            self.user_requests[user_id] = [
                req for req in self.user_requests[user_id] 
                if now - req < window
            ]
            if len(self.user_requests[user_id]) >= limit:
                return True
            self.user_requests[user_id].append(now)
            return False

rate_limiter = RateLimiter()

# ================= DECORATORLAR =================
def require_admin(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if len(args) > 0:
            message = args[0]
            user_id = message.from_user.id
            if user_id != config.ADMIN_ID and user_id not in config.ADMINS:
                bot.reply_to(message, "❌ Bu buyruq faqat admin uchun!")
                return
        return func(*args, **kwargs)
    return wrapper

def rate_limit_check(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if len(args) > 0 and hasattr(args[0], 'from_user'):
            user_id = args[0].from_user.id
            if rate_limiter.is_rate_limited(user_id):
                bot.reply_to(args[0], "⚠️ Juda ko'p so'rov! Biroz kuting.")
                return
        return func(*args, **kwargs)
    return wrapper

# ================= BOT INIT =================
bot = telebot.TeleBot(
    config.API_TOKEN, 
    parse_mode="HTML", 
    threaded=True,
    num_threads=4
)

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("BOT")

# ================= DATABASE =================
lock = Lock()

class DB:
    def __init__(self):
        self.conn = sqlite3.connect("bot.db", check_same_thread=False)
        self.cur = self.conn.cursor()
        self.init()

    def init(self):
        with lock:
            self.cur.executescript("""
            CREATE TABLE IF NOT EXISTS users(
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                invites INTEGER DEFAULT 0,
                stars INTEGER DEFAULT 0,
                vip INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            self.conn.commit()

    def create_user(self, uid, username, name):
        with lock:
            self.cur.execute(
                "INSERT OR IGNORE INTO users(user_id, username, first_name) VALUES(?,?,?)",
                (uid, username, name)
            )
            self.conn.commit()

    def get(self, uid):
        with lock:
            self.cur.execute(
                "SELECT invites, stars, vip FROM users WHERE user_id=?", 
                (uid,)
            )
            row = self.cur.fetchone()
            return (row[0], row[1], row[2]) if row else (0, 0, 0)

    def add_invite(self, uid, count=1):
        with lock:
            self.cur.execute(
                "UPDATE users SET invites = invites + ? WHERE user_id=?", 
                (count, uid)
            )
            self.conn.commit()
            self.recalc_stars(uid)

    def recalc_stars(self, uid):
        with lock:
            self.cur.execute("SELECT invites FROM users WHERE user_id=?", (uid,))
            row = self.cur.fetchone()
            invites = row[0] if row else 0
            stars = invites // 2
            self.cur.execute(
                "UPDATE users SET stars=? WHERE user_id=?", 
                (stars, uid)
            )
            self.conn.commit()
            return invites, stars

    def sub_star(self, uid, amount):
        with lock:
            self.cur.execute(
                "UPDATE users SET stars = MAX(0, stars - ?) WHERE user_id=?", 
                (amount, uid)
            )
            self.conn.commit()

    def grant_vip(self, uid):
        with lock:
            self.cur.execute(
                "UPDATE users SET vip = 1 WHERE user_id=?", 
                (uid,)
            )
            self.conn.commit()

    def check_ban(self, uid):
        with lock:
            self.cur.execute(
                "SELECT is_banned FROM users WHERE user_id=?", 
                (uid,)
            )
            row = self.cur.fetchone()
            return row and row[0] == 1

    def ban_user(self, uid):
        with lock:
            self.cur.execute(
                "UPDATE users SET is_banned = 1 WHERE user_id=?", 
                (uid,)
            )
            self.conn.commit()

    def unban_user(self, uid):
        with lock:
            self.cur.execute(
                "UPDATE users SET is_banned = 0 WHERE user_id=?", 
                (uid,)
            )
            self.conn.commit()

    def get_stats(self):
        with lock:
            stats = {}
            self.cur.execute("SELECT COUNT(*) FROM users")
            stats["total_users"] = self.cur.fetchone()[0]
            self.cur.execute("SELECT SUM(invites) FROM users")
            stats["total_invites"] = self.cur.fetchone()[0] or 0
            self.cur.execute("SELECT SUM(stars) FROM users")
            stats["total_stars"] = self.cur.fetchone()[0] or 0
            self.cur.execute("SELECT COUNT(*) FROM users WHERE vip = 1")
            stats["vip_users"] = self.cur.fetchone()[0]
            self.cur.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1")
            stats["banned_users"] = self.cur.fetchone()[0]
            return stats

    def get_top(self, limit=10):
        with lock:
            self.cur.execute("""
                SELECT username, first_name, invites, stars
                FROM users 
                WHERE is_banned = 0
                ORDER BY invites DESC 
                LIMIT ?
            """, (limit,))
            return self.cur.fetchall()

    def search_user(self, query):
        with lock:
            self.cur.execute("""
                SELECT user_id, username, first_name, invites, stars
                FROM users
                WHERE user_id = ? OR 
                      username LIKE ? OR 
                      first_name LIKE ?
            """, (query, f"%{query}%", f"%{query}%"))
            return self.cur.fetchall()

    def get_inactive_users(self, days=7):
        with lock:
            self.cur.execute("""
                SELECT user_id, username, first_name, created_at
                FROM users
                WHERE created_at < datetime('now', ?)
            """, (f'-{days} days',))
            return self.cur.fetchall()

db = DB()

# ================= KANALGA OBUNA TEKSHIRISH =================
def check_sub(uid):
    """Foydalanuvchi kanalga obuna bo'lganligini tekshirish"""
    try:
        member = bot.get_chat_member(config.CHANNEL_ID, uid)
        return member.status in ['member', 'administrator', 'creator']
    except:
        return True  # Xatolik bo'lsa, o'tkazib yuborish

# ================= SHOP =================
SHOP_ITEMS = [
    {"price": 15, "name": "❤️ Heart Gift", "emoji": "❤️", "photo": "https://i.imgur.com/8Yp9Z2M.jpg", "desc": "Chiroyli yurak sovg'asi"},
    {"price": 15, "name": "🧸 Teddy Bear", "emoji": "🧸", "photo": "https://i.imgur.com/5f2vL8K.jpg", "desc": "Yoqimli ayiqcha"},
    {"price": 25, "name": "🎁 Gift Box", "emoji": "🎁", "photo": "https://i.imgur.com/3vX9pLm.jpg", "desc": "Qizil lenta bilan sovg'a"},
    {"price": 25, "name": "🌹 Red Rose", "emoji": "🌹", "photo": "https://i.imgur.com/7zK9pQm.jpg", "desc": "Romantik atirgul"},
    {"price": 50, "name": "🎂 Birthday Cake", "emoji": "🎂", "photo": "https://i.imgur.com/9pL2mNx.jpg", "desc": "Shamli tort + VIP"},
    {"price": 50, "name": "💐 Flower Bouquet", "emoji": "💐", "photo": "https://i.imgur.com/XkP5vRt.jpg", "desc": "Gullar to'plami + VIP"},
    {"price": 100, "name": "🏆 Golden Trophy", "emoji": "🏆", "photo": "https://i.imgur.com/vL9pQmN.jpg", "desc": "Oltin kubok + VIP"},
    {"price": 100, "name": "💍 Diamond Ring", "emoji": "💍", "photo": "https://i.imgur.com/kP8mNxZ.jpg", "desc": "Olmos uzuk + VIP"}
]

def get_shop_items():
    """Takroriy narxlarni birlashtirish"""
    seen = {}
    for item in SHOP_ITEMS:
        if item["price"] not in seen:
            seen[item["price"]] = []
        seen[item["price"]].append(item)
    return seen

# ================= MENU =================
def menu(uid, chat_id):
    invites, stars, vip = db.get(uid)
    text = f"""
🌟 <b>REFERRAL SYSTEM</b> 🌟

👤 Sizning holatingiz:
👥 Taklif qilganingiz: <b>{invites}</b> ta
⭐ Yulduzlar: <b>{stars}</b>
👑 VIP: <b>{"✅ HA" if vip else "❌ YO'Q"}</b>

🎯 <i>Har 2 ta taklif = 1 yulduz</i>
"""
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(types.InlineKeyboardButton("🔗 Mening Invite Linkim", callback_data="link"))
    m.add(types.InlineKeyboardButton("🛒 Sovg'alar Do'koni", callback_data="shop"))
    m.add(types.InlineKeyboardButton("🏆 Top Foydalanuvchilar", callback_data="top"))
    bot.send_message(chat_id, text, reply_markup=m)

def shop_menu(chat_id, uid):
    _, stars, _ = db.get(uid)
    markup = types.InlineKeyboardMarkup(row_width=3)
    
    shop_data = get_shop_items()
    for price in sorted(shop_data.keys()):
        item = shop_data[price][0]
        markup.add(types.InlineKeyboardButton(
            f"{item['emoji']} {price}⭐", 
            callback_data=f"buy_{price}"
        ))

    text = f"""
🎁 <b>TELEGRAM GIFTS DO'KONI</b>

⭐ Sizning balansingiz: <b>{stars}</b> yulduz

Kerakli sovg'ani tanlang 👇
"""
    bot.send_message(chat_id, text, reply_markup=markup)

# ================= CALLBACK =================
@bot.callback_query_handler(func=lambda c: True)
def callback_handler(call):
    uid = call.from_user.id
    data = call.data

    if data == "shop":
        shop_menu(call.message.chat.id, uid)
    elif data == "link":
        link = f"https://t.me/{config.BOT_USERNAME}?start={uid}"
        bot.send_message(
            call.message.chat.id, 
            f"🔗 Sizning invite linkingiz:\n<code>{link}</code>"
        )
    elif data == "top":
        send_top(call.message.chat.id)
    elif data.startswith("buy_"):
        try:
            price = int(data.split("_")[1])
            buy_item(call, uid, price)
        except:
            bot.answer_callback_query(call.id, "❌ Xatolik!", show_alert=True)
    elif data == "check_sub":
        if check_sub(uid):
            db.create_user(uid, call.from_user.username, call.from_user.first_name)
            bot.delete_message(call.message.chat.id, call.message.message_id)
            menu(uid, call.message.chat.id)
        else:
            bot.answer_callback_query(call.id, "❌ Hali obuna bo'lmadingiz!", show_alert=True)

    bot.answer_callback_query(call.id)

def buy_item(call, uid, price):
    _, stars, _ = db.get(uid)
    
    if stars < price:
        return bot.answer_callback_query(call.id, "❌ Yetarli yulduz yo'q!", show_alert=True)

    shop_data = get_shop_items()
    items = shop_data.get(price, [])
    if not items:
        return bot.answer_callback_query(call.id, "❌ Bunday sovg'a topilmadi!", show_alert=True)
    
    item = items[0]
    db.sub_star(uid, price)

    extra = ""
    if price >= 50:
        db.grant_vip(uid)
        extra = "\n\n👑 <b>VIP</b> statusi berildi!"

    _, new_stars, _ = db.get(uid)
    
    caption = f"""
🎉 <b>Sizga sovg'a yetkazildi!</b> 🎉

{item['emoji']} <b>{item['name']}</b>
{item['desc']}

💰 Sarflandi: <b>{price} ⭐</b>
⭐ Qoldi: <b>{new_stars}</b>{extra}

Rahmat! Yana taklif qiling ✨
"""
    bot.send_photo(call.message.chat.id, item['photo'], caption=caption)
    bot.answer_callback_query(call.id, "✅ Sovg'a yetkazildi!", show_alert=True)

    # Admin ga xabar
    user = call.from_user
    admin_text = f"""
🛍 <b>YANGI SOTUV!</b>

👤 Foydalanuvchi: <a href='tg://user?id={user.id}'>{user.first_name}</a>
🆔 ID: <code>{user.id}</code>
📛 Username: @{user.username if user.username else 'yoq'}

🎁 Sovg'a: <b>{item['name']}</b> {item['emoji']}
💰 Narxi: <b>{price} yulduz</b>
"""
    try:
        bot.send_message(config.ADMIN_ID, admin_text)
    except:
        pass

def send_top(chat_id):
    top = db.get_top(10)
    if not top:
        return bot.send_message(chat_id, "❌ Hali hech kim yo'q!")
    
    text = "🏆 <b>ENG FAOL REFERRALLAR</b>\n\n"
    for i, (username, name, invites, stars) in enumerate(top, 1):
        user = f"@{username}" if username else name
        text += f"{i}️⃣ <b>{user}</b> — 👥 {invites} ta | ⭐ {stars} yulduz\n"
    text += "\n🔥 Har 2 ta odam qo'shsangiz = 1 yulduz"
    
    bot.send_message(chat_id, text)

# ================= LEADERBOARD =================
def send_leaderboard():
    try:
        top = db.get_top(10)
        if not top:
            return
        text = "🏆 <b>ENG FAOL REFERRALLAR</b>\n\n"
        for i, (username, name, invites, stars) in enumerate(top, 1):
            user = f"@{username}" if username else name
            text += f"{i}️⃣ <b>{user}</b> — 👥 {invites} ta | ⭐ {stars} yulduz\n"
        text += "\n🔥 Har 2 ta odam qo'shsangiz = 1 yulduz"
        
        try:
            bot.send_message(config.CHANNEL_ID, text)
        except:
            pass
    except Exception as e:
        logger.error(f"Leaderboard xatosi: {e}")

def leaderboard_scheduler():
    while True:
        send_leaderboard()
        time.sleep(120)  # 2 daqiqa

# ================= START =================
@bot.message_handler(commands=["start"])
@rate_limit_check
def start(m):
    uid = m.from_user.id
    
    if db.check_ban(uid):
        return bot.send_message(m.chat.id, "❌ Siz bloklangansiz!")
    
    if not check_sub(uid):
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(
            "📢 Kanalga obuna bo'lish", 
            url=f"https://t.me/{config.BOT_USERNAME.replace('_bot', '').replace('bot', '')}"
        ))
        markup.add(types.InlineKeyboardButton(
            "✅ Obuna bo'ldim", 
            callback_data="check_sub"
        ))
        return bot.send_message(
            m.chat.id, 
            "❌ Avval kanalga obuna bo'ling!", 
            reply_markup=markup
        )
    
    db.create_user(uid, m.from_user.username, m.from_user.first_name)
    
    # Referrer
    if m.text and len(m.text.split()) > 1:
        try:
            referrer_id = int(m.text.split()[1])
            if referrer_id != uid:
                db.add_invite(referrer_id)
                bot.send_message(referrer_id, "🎉 Yangi odam qo'shildi! +1 taklif")
        except:
            pass
    
    menu(uid, m.chat.id)

# ================= ADMIN BUYRUQLARI =================
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    uid = message.from_user.id
    
    if uid != config.ADMIN_ID and uid not in config.ADMINS:
        return bot.reply_to(message, "❌ Ruxsat yo'q!")
    
    stats = db.get_stats()
    
    admin_text = f"""
🔐 <b>ADMIN PANEL</b>

📊 <b>Statistika:</b>
• Umumiy foydalanuvchilar: {stats['total_users']}
• Jami takliflar: {stats['total_invites']}
• Jami yulduzlar: {stats['total_stars']}
• VIP foydalanuvchilar: {stats['vip_users']}
• Ban qilingan: {stats['banned_users']}

⚙️ <b>Buyruqlar:</b>
/ban [user_id] - Ban qilish
/unban [user_id] - Bandan chiqarish
/addstars [user_id] [miqdori] - Yulduz qo'shish
/search [id/username] - Qidirish
/inactive [kun] - Nofaol foydalanuvchilar
/top - Top foydalanuvchilar
"""
    
    bot.send_message(uid, admin_text)

@bot.message_handler(commands=['ban'])
@require_admin
def ban_user_cmd(message):
    try:
        uid = int(message.text.split()[1])
        db.ban_user(uid)
        bot.reply_to(message, f"✅ {uid} ban qilindi!")
    except:
        bot.reply_to(message, "❌ Format: /ban [user_id]")

@bot.message_handler(commands=['unban'])
@require_admin
def unban_user_cmd(message):
    try:
        uid = int(message.text.split()[1])
        db.unban_user(uid)
        bot.reply_to(message, f"✅ {uid} bandan chiqarildi!")
    except:
        bot.reply_to(message, "❌ Format: /unban [user_id]")

@bot.message_handler(commands=['addstars'])
@require_admin
def add_stars_cmd(message):
    try:
        parts = message.text.split()
        uid = int(parts[1])
        amount = int(parts[2])
        
        invites_to_add = amount * 2
        db.add_invite(uid, invites_to_add)
        bot.reply_to(message, f"✅ {uid} ga {amount} yulduz qo'shildi!")
    except:
        bot.reply_to(message, "❌ Format: /addstars [user_id] [miqdori]")

@bot.message_handler(commands=['search'])
@require_admin
def search_user_cmd(message):
    try:
        query = message.text.split(maxsplit=1)[1]
        results = db.search_user(query)
        
        if not results:
            return bot.reply_to(message, "❌ Topilmadi!")
        
        text = "🔍 <b>Qidiruv natijalari:</b>\n\n"
        for user in results[:10]:
            text += f"🆔 {user[0]} | @{user[1] or 'yoq'} | {user[2]}\n"
            text += f"👥 {user[3]} taklif | ⭐ {user[4]} yulduz\n\n"
        
        bot.reply_to(message, text)
    except:
        bot.reply_to(message, "❌ Format: /search [id/username]")

@bot.message_handler(commands=['inactive'])
@require_admin
def inactive_users_cmd(message):
    try:
        days = int(message.text.split()[1]) if len(message.text.split()) > 1 else 7
        users = db.get_inactive_users(days)
        
        if not users:
            return bot.reply_to(message, f"✅ {days} kundan beri nofaol foydalanuvchilar yo'q!")
        
        text = f"⏰ <b>{days} kundan beri nofaol:</b>\n\n"
        for user in users[:20]:
            text += f"🆔 {user[0]} | @{user[1] or 'yoq'} | {user[2]}\n"
        
        bot.reply_to(message, text)
    except:
        bot.reply_to(message, "❌ Format: /inactive [kun]")

@bot.message_handler(commands=['top'])
def top_cmd(message):
    """Top foydalanuvchilar buyrug'i"""
    send_top(message.chat.id)

@bot.message_handler(commands=['stats'])
def user_stats(message):
    uid = message.from_user.id
    
    if db.check_ban(uid):
        return bot.reply_to(message, "❌ Siz bloklangansiz!")
    
    invites, stars, vip = db.get(uid)
    
    text = f"""
📊 <b>STATISTIKANGIZ</b>

👥 Takliflar: {invites}
⭐ Yulduzlar: {stars}
👑 VIP: {"✅" if vip else "❌"}
"""
    bot.reply_to(message, text)

@bot.message_handler(commands=['help'])
def help_cmd(message):
    help_text = """
🤖 <b>BOT YORDAM</b>

📌 <b>Asosiy buyruqlar:</b>
/start - Botni ishga tushirish
/stats - Statistikangiz
/help - Yordam
/top - Top foydalanuvchilar

🔗 <b>Referral:</b>
/start [referrer_id] - Do'stingiz orqali qo'shilish

🛍 <b>Do'kon:</b>
Yulduzlaringizni sovg'alarga almashtiring!

📊 <b>Qoidalar:</b>
• Har 2 ta taklif = 1 ⭐ yulduz
• 50+ yulduzli sovg'alar VIP beradi
• Spam qilganlar ban qilinadi

💡 <b>Maslahat:</b>
Do'stlaringizni taklif qiling va sovg'alar yig'ing!
"""
    bot.reply_to(message, help_text)

# ================= MAIN =================
if __name__ == "__main__":
    print(f"🔐 Bot xavfsizlik: faol")
    print(f"🆔 Admin ID: {config.ADMIN_ID}")
    print("🚀 BOT ISHGA TUSHIRILDI")
    
    # Threadlarni ishga tushirish
    Thread(target=leaderboard_scheduler, daemon=True).start()
    
    # Asosiy loop
    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=30)
        except KeyboardInterrupt:
            print("👋 Bot to'xtatildi")
            break
        except Exception as e:
            logger.critical(f"KRITIK XATOLIK: {e}")
            time.sleep(5)
