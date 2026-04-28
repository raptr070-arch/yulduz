import telebot
import sqlite3
import logging
import os
import time
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
        self.ADMIN_ID = int(os.getenv("ADMIN_ID", "2010030869"))
        self.BOT_USERNAME = os.getenv("BOT_USERNAME", "stars_sovga_gifbot")
        self.RATE_LIMIT = 5
        self.ADMINS = [2010030869]
        self.REFERRAL_BONUS = 1  # Har bir taklif uchun bonus
        
        if not self.API_TOKEN:
            raise ValueError("❌ TOKEN topilmadi!")

try:
    config = SecurityConfig()
except ValueError as e:
    print(e)
    exit(1)

# ================= OBUNA TEKSHIRISH KONFIG =================
REQUIRED_CHANNELS = [
    {
        "id": -1002449896845,
        "username": "@Tekin_stars_yulduz",
        "url": "https://t.me/Tekin_stars_yulduz",
        "name": "📢 KANAL"
    },
    {
        "id": -1001234567890,  # ← Guruh ID ni o'zgartiring
        "username": "@guruh_username",  # ← Guruh username
        "url": "https://t.me/guruh_username",  # ← Guruh linki
        "name": "👥 GURUH"
    }
]

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
    handlers=[logging.StreamHandler()]
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
                invited_by INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS invite_history(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inviter_id INTEGER,
                invited_id INTEGER,
                invited_username TEXT,
                invited_name TEXT,
                source TEXT DEFAULT 'link',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """)
            self.conn.commit()

    def create_user(self, uid, username, name, invited_by=0):
        with lock:
            self.cur.execute(
                "INSERT OR IGNORE INTO users(user_id, username, first_name, invited_by) VALUES(?,?,?,?)",
                (uid, username, name, invited_by)
            )
            self.conn.commit()

    def get(self, uid):
        with lock:
            self.cur.execute(
                "SELECT invites, stars, vip, invited_by FROM users WHERE user_id=?", 
                (uid,)
            )
            row = self.cur.fetchone()
            return (row[0], row[1], row[2], row[3]) if row else (0, 0, 0, 0)

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

    def add_invite_history(self, inviter_id, invited_id, invited_username, invited_name, source="link"):
        with lock:
            self.cur.execute("""
                INSERT INTO invite_history(inviter_id, invited_id, invited_username, invited_name, source)
                VALUES(?,?,?,?,?)
            """, (inviter_id, invited_id, invited_username, invited_name, source))
            self.conn.commit()

    def check_duplicate_invite(self, inviter_id, invited_id):
        """Takroriy taklifni tekshirish"""
        with lock:
            self.cur.execute("""
                SELECT COUNT(*) FROM invite_history 
                WHERE inviter_id=? AND invited_id=?
            """, (inviter_id, invited_id))
            return self.cur.fetchone()[0] > 0

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
            self.cur.execute("SELECT COUNT(*) FROM invite_history")
            stats["total_invites_history"] = self.cur.fetchone()[0]
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

    def get_user_invites_detail(self, uid):
        """Foydalanuvchi taklif qilgan odamlar ro'yxati"""
        with lock:
            self.cur.execute("""
                SELECT invited_id, invited_username, invited_name, source, created_at
                FROM invite_history
                WHERE inviter_id=?
                ORDER BY created_at DESC
                LIMIT 20
            """, (uid,))
            return self.cur.fetchall()

db = DB()

# ================= OBUNA TEKSHIRISH =================
def check_sub(uid):
    """Ham kanal, ham guruhga obunani tekshirish"""
    not_subscribed = []
    
    for channel in REQUIRED_CHANNELS:
        try:
            member = bot.get_chat_member(channel["id"], uid)
            if member.status not in ['member', 'administrator', 'creator']:
                not_subscribed.append(channel)
        except:
            try:
                member = bot.get_chat_member(channel["username"], uid)
                if member.status not in ['member', 'administrator', 'creator']:
                    not_subscribed.append(channel)
            except:
                not_subscribed.append(channel)
    
    return not_subscribed

def check_all_subs(uid):
    """Hammaga obuna bo'lganligini tekshirish"""
    return len(check_sub(uid)) == 0

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
    seen = {}
    for item in SHOP_ITEMS:
        if item["price"] not in seen:
            seen[item["price"]] = []
        seen[item["price"]].append(item)
    return seen

# ================= MENU =================
def menu(uid, chat_id):
    invites, stars, vip, _ = db.get(uid)
    
    not_subscribed = check_sub(uid)
    sub_status = ""
    if not_subscribed:
        channels_list = "\n".join([f"• {ch['name']} - {ch['username']}" for ch in not_subscribed])
        sub_status = f"\n\n⚠️ <b>Obuna bo'lmagan:</b>\n{channels_list}\n<i>Obuna bo'lish uchun /start bosing</i>"
    
    text = f"""
🌟 <b>REFERRAL SYSTEM</b> 🌟

👤 Sizning holatingiz:
👥 Taklif qilganingiz: <b>{invites}</b> ta
⭐ Yulduzlar: <b>{stars}</b>
👑 VIP: <b>{"✅ HA" if vip else "❌ YO'Q"}</b>{sub_status}

🎯 <i>Har 2 ta taklif = 1 yulduz</i>
"""
    m = types.InlineKeyboardMarkup(row_width=1)
    m.add(types.InlineKeyboardButton("🔗 Mening Invite Linkim", callback_data="link"))
    m.add(types.InlineKeyboardButton("🛒 Sovg'alar Do'koni", callback_data="shop"))
    m.add(types.InlineKeyboardButton("🏆 Top Foydalanuvchilar", callback_data="top"))
    m.add(types.InlineKeyboardButton("📊 Takliflarim tarixi", callback_data="invite_history"))
    bot.send_message(chat_id, text, reply_markup=m)

def shop_menu(chat_id, uid):
    _, stars, _, _ = db.get(uid)
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

# ================= CALLBACK HANDLER =================
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
            f"🔗 Sizning invite linkingiz:\n<code>{link}</code>\n\n👥 Bu link orqali odamlar qo'shilsa, sizga taklif qo'shiladi!"
        )
    elif data == "top":
        send_top(call.message.chat.id)
    elif data == "invite_history":
        show_invite_history(call.message.chat.id, uid)
    elif data.startswith("buy_"):
        try:
            price = int(data.split("_")[1])
            buy_item(call, uid, price)
        except:
            bot.answer_callback_query(call.id, "❌ Xatolik!", show_alert=True)
    elif data == "check_sub":
        check_subscription(call)

    bot.answer_callback_query(call.id)

def check_subscription(call):
    """Obuna tekshirish callback"""
    uid = call.from_user.id
    
    if check_all_subs(uid):
        db.create_user(uid, call.from_user.username, call.from_user.first_name)
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        bot.answer_callback_query(call.id, "✅ Obuna tasdiqlandi!", show_alert=False)
        menu(uid, call.message.chat.id)
    else:
        not_subscribed = check_sub(uid)
        channels_list = "\n".join([f"• {ch['name']} - {ch['username']}" for ch in not_subscribed])
        bot.answer_callback_query(
            call.id, 
            f"❌ Hali obuna bo'lmadingiz!\n\n{channels_list}", 
            show_alert=True
        )

def show_invite_history(chat_id, uid):
    """Taklif tarixini ko'rsatish"""
    history = db.get_user_invites_detail(uid)
    
    if not history:
        return bot.send_message(chat_id, "❌ Hali hech kimni taklif qilmagansiz!")
    
    text = f"📊 <b>TAKLIFLAR TARIXI</b>\n\n"
    for i, (invited_id, username, name, source, date) in enumerate(history, 1):
        user_display = f"@{username}" if username else name
        source_emoji = "🔗" if source == "link" else "👥"
        text += f"{i}. {source_emoji} {user_display}\n"
        text += f"   🆔 <code>{invited_id}</code>\n"
        text += f"   📅 {date}\n\n"
    
    text += f"👥 Jami: {len(history)} ta taklif"
    bot.send_message(chat_id, text)

def buy_item(call, uid, price):
    _, stars, _, _ = db.get(uid)
    
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

    _, new_stars, _, _ = db.get(uid)
    
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
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}️⃣"
        text += f"{medal} <b>{user}</b> — 👥 {invites} ta | ⭐ {stars} yulduz\n"
    text += "\n🔥 Har 2 ta odam qo'shsangiz = 1 yulduz"
    
    bot.send_message(chat_id, text)

# ================= GURUHGA QO'SHILGANLARNI HISOBLASH =================
@bot.message_handler(content_types=['new_chat_members'])
def handle_new_members(message):
    """Guruhga yangi odam qo'shilganda"""
    new_members = message.new_chat_members
    
    for new_member in new_members:
        if new_member.is_bot:
            continue  # Botlarni hisoblamaslik
        
        # Taklif qilgan odamni aniqlash
        inviter_id = message.from_user.id
        invited_id = new_member.id
        
        # O'zini o'zi taklif qilolmaysiz
        if inviter_id == invited_id:
            continue
        
        # Takroriy taklifni tekshirish
        if db.check_duplicate_invite(inviter_id, invited_id):
            continue
        
        # Taklif qiluvchini ma'lumotlar bazasiga qo'shish
        db.create_user(inviter_id, message.from_user.username, message.from_user.first_name)
        
        # Taklif qilingan odamni qo'shish
        db.create_user(invited_id, new_member.username, new_member.first_name, inviter_id)
        
        # Taklif tarixiga qo'shish
        db.add_invite_history(
            inviter_id, 
            invited_id, 
            new_member.username, 
            new_member.first_name, 
            "group"
        )
        
        # Taklif sonini oshirish
        db.add_invite(inviter_id, 1)
        
        # Guruhga xabar yuborish
        welcome_text = f"""
🎉 <b>YANGI ISHTIROKCHI!</b>

👤 <a href='tg://user?id={invited_id}'>{new_member.first_name}</a> guruhga qo'shildi!

👥 Taklif qilgan: <a href='tg://user?id={inviter_id}'>{message.from_user.first_name}</a>
⭐ Taklif qiluvchiga +1 taklif qo'shildi!

<i>Har 2 ta taklif = 1 yulduz</i>
"""
        try:
            bot.send_message(message.chat.id, welcome_text)
        except:
            pass
        
        # Taklif qiluvchiga shaxsiy xabar
        try:
            personal_text = f"""
🎉 <b>TABRIKLAYMIZ!</b>

Siz {new_member.first_name} ni guruhga taklif qildingiz!
👥 Takliflar: +1
⭐ Yana 1 ta taklif qilsangiz, +1 yulduz olasiz!

<i>Bot: @{config.BOT_USERNAME}</i>
"""
            bot.send_message(inviter_id, personal_text)
        except:
            pass

# ================= START HANDLER =================
@bot.message_handler(commands=["start"])
@rate_limit_check
def start(m):
    uid = m.from_user.id
    
    if db.check_ban(uid):
        return bot.send_message(m.chat.id, "❌ Siz bloklangansiz!")
    
    # Obuna tekshirish
    if not check_all_subs(uid):
        not_subscribed = check_sub(uid)
        
        markup = types.InlineKeyboardMarkup(row_width=1)
        for channel in not_subscribed:
            markup.add(types.InlineKeyboardButton(
                f"{channel['name']} - OBUNA BO'LISH", 
                url=channel['url']
            ))
        markup.add(types.InlineKeyboardButton(
            "✅ HAMMASIGA OBUNA BO'LDIM", 
            callback_data="check_sub"
        ))
        
        channels_list = "\n".join([f"• {ch['name']}: {ch['username']}" for ch in not_subscribed])
        text = f"""❌ <b>OBUNA TEKSHIRUVI</b>

Botdan foydalanish uchun quyidagi kanal va guruhlarga obuna bo'ling:

{channels_list}

Obuna bo'lgach, "✅ HAMMASIGA OBUNA BO'LDIM" tugmasini bosing."""
        
        return bot.send_message(m.chat.id, text, reply_markup=markup)
    
    # Foydalanuvchini yaratish
    invited_by = 0
    if m.text and len(m.text.split()) > 1:
        try:
            invited_by = int(m.text.split()[1])
        except:
            invited_by = 0
    
    db.create_user(uid, m.from_user.username, m.from_user.first_name, invited_by)
    
    # Referrer orqali kelgan bo'lsa
    if invited_by > 0 and invited_by != uid:
        if not db.check_duplicate_invite(invited_by, uid):
            # Taklif qiluvchini ham yaratish
            try:
                inviter_info = bot.get_chat(invited_by)
                db.create_user(invited_by, inviter_info.username, inviter_info.first_name)
            except:
                pass
            
            # Taklif tarixiga qo'shish
            db.add_invite_history(invited_by, uid, m.from_user.username, m.from_user.first_name, "link")
            
            # Taklif sonini oshirish
            db.add_invite(invited_by, 1)
            
            # Taklif qiluvchiga xabar
            try:
                bot.send_message(
                    invited_by, 
                    f"🎉 {m.from_user.first_name} sizning linkingiz orqali botga qo'shildi!\n👥 +1 taklif"
                )
            except:
                pass
    
    # Asosiy menu
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
• Jami tarixiy takliflar: {stats['total_invites_history']}
• Jami yulduzlar: {stats['total_stars']}
• VIP foydalanuvchilar: {stats['vip_users']}
• Ban qilingan: {stats['banned_users']}

⚙️ <b>Buyruqlar:</b>
/ban [user_id] - Ban qilish
/unban [user_id] - Bandan chiqarish
/addstars [user_id] [miqdori] - Yulduz qo'shish
/search [id/username] - Qidirish
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

@bot.message_handler(commands=['top'])
def top_cmd(message):
    send_top(message.chat.id)

@bot.message_handler(commands=['stats'])
def user_stats(message):
    uid = message.from_user.id
    
    if db.check_ban(uid):
        return bot.reply_to(message, "❌ Siz bloklangansiz!")
    
    invites, stars, vip, invited_by = db.get(uid)
    
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

👥 <b>Guruh:</b>
Guruhga odam qo'shsangiz, avtomatik hisoblanadi!

🛍 <b>Do'kon:</b>
Yulduzlaringizni sovg'alarga almashtiring!

📊 <b>Qoidalar:</b>
• Har 2 ta taklif = 1 ⭐ yulduz
• 50+ yulduzli sovg'alar VIP beradi
• Spam qilganlar ban qilinadi
"""
    bot.reply_to(message, help_text)

# ================= LEADERBOARD =================
def send_leaderboard():
    try:
        top = db.get_top(10)
        if not top:
            return
        text = "🏆 <b>ENG FAOL REFERRALLAR</b>\n\n"
        for i, (username, name, invites, stars) in enumerate(top, 1):
            user = f"@{username}" if username else name
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}️⃣"
            text += f"{medal} <b>{user}</b> — 👥 {invites} ta | ⭐ {stars} yulduz\n"
        text += "\n🔥 Har 2 ta odam qo'shsangiz = 1 yulduz"
        
        for channel in REQUIRED_CHANNELS:
            try:
                bot.send_message(channel["id"], text)
            except:
                pass
    except Exception as e:
        logger.error(f"Leaderboard xatosi: {e}")

def leaderboard_scheduler():
    while True:
        send_leaderboard()
        time.sleep(120)  # 2 daqiqa

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
