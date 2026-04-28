import telebot
import sqlite3
import logging
import os
import time
import json
import hashlib
import threading
from telebot import types
from datetime import datetime, timedelta
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
        self.BOT_USERNAME = os.getenv("BOT_USERNAME", "stars_sovga_gifbot")
        self.RATE_LIMIT = 5  # 5 soniyada maksimal so'rov
        self.BAN_DURATION = 3600  # 1 soat ban
        self.ADMINS = [2010030869, 987654321]  # Qo'shimcha adminlar
        
        # Tokenni tekshirish
        if not self.API_TOKEN or self.API_TOKEN == "YOUR_BOT_TOKEN_HERE":
            raise ValueError("❌ TOKEN topilmadi yoki o'zgarmagan!")
        
        if len(self.API_TOKEN) < 45:  # Telegram tokenlari odatda 45+ belgidan iborat
            raise ValueError("❌ Noto'g'ri TOKEN formati!")

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
        """5 soniyada 5 ta so'rovdan ko'p bo'lsa, bloklanadi"""
        now = time.time()
        
        with self.lock:
            if user_id not in self.user_requests:
                self.user_requests[user_id] = []
            
            # Eski so'rovlarni tozalash
            self.user_requests[user_id] = [
                req for req in self.user_requests[user_id] 
                if now - req < window
            ]
            
            if len(self.user_requests[user_id]) >= limit:
                return True
            
            self.user_requests[user_id].append(now)
            return False

rate_limiter = RateLimiter()

# ================= ENKRIPTSIYA =================
class DataEncryptor:
    @staticmethod
    def encrypt(data):
        """Ma'lumotlarni hashlash"""
        if isinstance(data, str):
            return hashlib.sha256(data.encode()).hexdigest()
        return hashlib.sha256(str(data).encode()).hexdigest()
    
    @staticmethod
    def secure_token(token):
        """Tokenni xavfsiz saqlash"""
        return f"{token[:10]}...{token[-10:]}"  # Tokennning faqat bir qismini ko'rsatish

# ================= DECORATORLAR =================
def require_admin(func):
    """Admin tekshirish dekoratori"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if len(args) > 0:
            message = args[0]
            user_id = message.from_user.id if hasattr(message, 'from_user') else message.chat.id
        if user_id not in config.ADMINS and user_id != config.ADMIN_ID:
            bot.reply_to(message, "❌ Bu buyruq faqat admin uchun!")
            return
        return func(*args, **kwargs)
    return wrapper

def rate_limit_check(func):
    """Rate limiting dekoratori"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if len(args) > 0 and hasattr(args[0], 'from_user'):
            user_id = args[0].from_user.id
            if rate_limiter.is_rate_limited(user_id, limit=config.RATE_LIMIT):
                bot.reply_to(args[0], "⚠️ Juda ko'p so'rov! Iltimos, biroz kuting.")
                return
        return func(*args, **kwargs)
    return wrapper

# ================= BOT INITSIALIZATSIYA =================
bot = telebot.TeleBot(
    config.API_TOKEN, 
    parse_mode="HTML", 
    threaded=True,
    num_threads=4  # Ko'proq thread = tezroq ishlash
)

# ================= LOGGING KENGAYTIRILGAN =================
class SecurityLogger:
    def __init__(self):
        self.suspicious_actions = []
    
    def log_suspicious(self, user_id, action, ip=None):
        entry = {
            "user_id": user_id,
            "action": action,
            "time": datetime.now().isoformat(),
            "ip": ip
        }
        self.suspicious_actions.append(entry)
        logger.warning(f"⚠️ Shubhali harakat: {action} - User: {user_id}")
        
        # 10 ta shubhali harakatdan keyin admin ogohlantirish
        if len(self.suspicious_actions) >= 10:
            alert = "🚨 <b>XAVFSIZLIK OGOHLANTIRISH</b>\n\nSo'nggi shubhali harakatlar:\n"
            for act in self.suspicious_actions[-5:]:
                alert += f"• {act}\n"
            bot.send_message(config.ADMIN_ID, alert)

sec_logger = SecurityLogger()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot_security.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("BOT")

# ================= DATABASE KENGAYTIRILGAN =================
lock = Lock()

class SecureDB:
    def __init__(self):
        self.conn = sqlite3.connect("bot_secure.db", check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")  # Tezroq ishlash
        self.conn.execute("PRAGMA synchronous=OFF")
        self.cur = self.conn.cursor()
        self.init()
        self.optimize()

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
                ban_until TIMESTAMP,
                last_activity TIMESTAMP,
                total_invites_history INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            
            CREATE TABLE IF NOT EXISTS security_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ip_address TEXT,
                user_agent TEXT
            );
            
            CREATE TABLE IF NOT EXISTS banned_users(
                user_id INTEGER PRIMARY KEY,
                reason TEXT,
                banned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                banned_by INTEGER
            );
            
            CREATE INDEX IF NOT EXISTS idx_users_invites ON users(invites DESC);
            CREATE INDEX IF NOT EXISTS idx_users_stars ON users(stars DESC);
            CREATE INDEX IF NOT EXISTS idx_security_user ON security_log(user_id);
            """)
            self.conn.commit()

    def optimize(self):
        """Database optimizatsiyasi"""
        with lock:
            self.cur.execute("ANALYZE")
            self.cur.execute("VACUUM")
            self.conn.commit()

    def create_user(self, uid, username, name):
        with lock:
            self.cur.execute("""
                INSERT OR IGNORE INTO users(
                    user_id, username, first_name, last_activity
                ) VALUES(?,?,?, CURRENT_TIMESTAMP)
            """, (uid, username, name))
            self.conn.commit()

    def get_user_full(self, uid):
        with lock:
            self.cur.execute("""
                SELECT invites, stars, vip, is_banned, ban_until, 
                       last_activity, total_invites_history
                FROM users WHERE user_id=?
            """, (uid,))
            row = self.cur.fetchone()
            if row:
                return {
                    "invites": row[0],
                    "stars": row[1],
                    "vip": row[2],
                    "is_banned": row[3],
                    "ban_until": row[4],
                    "last_activity": row[5],
                    "total_invites_history": row[6]
                }
            return None

    def add_invite_with_history(self, uid, count=1):
        with lock:
            self.cur.execute("""
                UPDATE users 
                SET invites = invites + ?,
                    total_invites_history = total_invites_history + ?,
                    last_activity = CURRENT_TIMESTAMP
                WHERE user_id=?
            """, (count, count, uid))
            self.conn.commit()
            self.recalc_stars(uid)

    def recalc_stars(self, uid):
        with lock:
            self.cur.execute("SELECT invites FROM users WHERE user_id=?", (uid,))
            row = self.cur.fetchone()
            invites = row[0] if row else 0
            stars = invites // 2
            self.cur.execute("UPDATE users SET stars=? WHERE user_id=?", (stars, uid))
            self.conn.commit()
            return invites, stars

    def sub_star(self, uid, amount):
        with lock:
            self.cur.execute("""
                UPDATE users 
                SET stars = MAX(0, stars - ?),
                    last_activity = CURRENT_TIMESTAMP
                WHERE user_id=?
            """, (amount, uid))
            self.conn.commit()

    def grant_vip(self, uid):
        with lock:
            self.cur.execute("""
                UPDATE users 
                SET vip = 1,
                    last_activity = CURRENT_TIMESTAMP
                WHERE user_id=?
            """, (uid,))
            self.conn.commit()

    def ban_user(self, uid, reason="Spam", banned_by=0):
        with lock:
            self.cur.execute("""
                UPDATE users 
                SET is_banned = 1,
                    ban_until = datetime('now', '+1 hour')
                WHERE user_id=?
            """, (uid,))
            self.cur.execute("""
                INSERT INTO banned_users(user_id, reason, banned_by)
                VALUES(?, ?, ?)
            """, (uid, reason, banned_by))
            self.conn.commit()

    def unban_user(self, uid):
        with lock:
            self.cur.execute("""
                UPDATE users 
                SET is_banned = 0, ban_until = NULL 
                WHERE user_id=?
            """, (uid,))
            self.conn.commit()

    def check_ban(self, uid):
        with lock:
            self.cur.execute("""
                SELECT is_banned, ban_until 
                FROM users WHERE user_id=?
            """, (uid,))
            row = self.cur.fetchone()
            if row and row[0]:
                if row[1]:
                    ban_until = datetime.fromisoformat(row[1])
                    if ban_until < datetime.now():
                        self.unban_user(uid)
                        return False
                return True
            return False

    def log_security(self, uid, action, ip=None, user_agent=None):
        with lock:
            self.cur.execute("""
                INSERT INTO security_log(user_id, action, ip_address, user_agent)
                VALUES(?, ?, ?, ?)
            """, (uid, action, ip, user_agent))
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
                SELECT username, first_name, invites, stars, vip
                FROM users 
                WHERE is_banned = 0
                ORDER BY invites DESC 
                LIMIT ?
            """, (limit,))
            return self.cur.fetchall()

    def search_user(self, query):
        """Foydalanuvchini qidirish"""
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
        """Nofaol foydalanuvchilar"""
        with lock:
            self.cur.execute("""
                SELECT user_id, username, first_name, last_activity
                FROM users
                WHERE last_activity < datetime('now', ?)
            """, (f'-{days} days',))
            return self.cur.fetchall()

db = SecureDB()

# ================= SHOP KENGAYTIRILGAN =================
SHOP = {
    15: {
        "name": "❤️ Heart Gift",
        "emoji": "❤️",
        "photo": "https://i.imgur.com/8Yp9Z2M.jpg",
        "desc": "Chiroyli yurak sovg'asi",
        "type": "gift"
    },
    15: {
        "name": "🧸 Teddy Bear", 
        "emoji": "🧸",
        "photo": "https://i.imgur.com/5f2vL8K.jpg",
        "desc": "Yoqimli ayiqcha",
        "type": "gift"
    },
    25: {
        "name": "🎁 Gift Box",
        "emoji": "🎁",
        "photo": "https://i.imgur.com/3vX9pLm.jpg",
        "desc": "Qizil lenta bilan sovg'a",
        "type": "gift"
    },
    25: {
        "name": "🌹 Red Rose",
        "emoji": "🌹", 
        "photo": "https://i.imgur.com/7zK9pQm.jpg",
        "desc": "Romantik atirgul",
        "type": "gift"
    },
    50: {
        "name": "🎂 Birthday Cake",
        "emoji": "🎂",
        "photo": "https://i.imgur.com/9pL2mNx.jpg",
        "desc": "Shamli tort + VIP",
        "type": "vip_gift"
    },
    50: {
        "name": "💐 Flower Bouquet",
        "emoji": "💐",
        "photo": "https://i.imgur.com/XkP5vRt.jpg",
        "desc": "Gullar to'plami + VIP",
        "type": "vip_gift"
    },
    100: {
        "name": "🏆 Golden Trophy",
        "emoji": "🏆",
        "photo": "https://i.imgur.com/vL9pQmN.jpg", 
        "desc": "Oltin kubok + VIP",
        "type": "premium"
    },
    100: {
        "name": "💍 Diamond Ring",
        "emoji": "💍",
        "photo": "https://i.imgur.com/kP8mNxZ.jpg",
        "desc": "Olmos uzuk + VIP",
        "type": "premium"
    }
}

# ================= YANGI FUNKSIYALAR =================
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    """Admin panel"""
    uid = message.from_user.id
    
    if uid not in [config.ADMIN_ID] + config.ADMINS:
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
/ban [user_id] - Foydalanuvchini ban qilish
/unban [user_id] - Bandan chiqarish
/addstars [user_id] [miqdori] - Yulduz qo'shish
/search [id/username] - Qidirish
/inactive [kun] - Nofaol foydalanuvchilar
/backup - Database backup
/clearlog - Loglarni tozalash
"""
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🔄 Yangilash", callback_data="admin_refresh"))
    markup.add(types.InlineKeyboardButton("📊 To'liq statistika", callback_data="admin_stats"))
    
    bot.send_message(uid, admin_text, reply_markup=markup)

@bot.message_handler(commands=['ban'])
@require_admin
def ban_user_cmd(message):
    try:
        uid = int(message.text.split()[1])
        db.ban_user(uid, "Admin buyrug'i", message.from_user.id)
        bot.reply_to(message, f"✅ {uid} ban qilindi!")
        sec_logger.log_suspicious(uid, "Banned by admin")
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
        # Yulduz qo'shish
        db.cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
        if not db.cur.fetchone():
            return bot.reply_to(message, "❌ Foydalanuvchi topilmadi!")
        
        # Invite qo'shish orqali yulduz berish (2 invite = 1 star)
        invites_to_add = amount * 2
        db.add_invite_with_history(uid, invites_to_add)
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

@bot.message_handler(commands=['backup'])
@require_admin
def backup_db(message):
    """Database backup"""
    try:
        import shutil
        shutil.copy2("bot_secure.db", f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
        
        with open("bot_secure.db", "rb") as f:
            bot.send_document(message.chat.id, f, caption="✅ Database backup")
    except Exception as e:
        bot.reply_to(message, f"❌ Xatolik: {e}")

@bot.message_handler(commands=['stats'])
def user_stats(message):
    """Foydalanuvchi statistikasi"""
    uid = message.from_user.id
    
    if db.check_ban(uid):
        return bot.reply_to(message, "❌ Siz bloklangansiz!")
    
    user_data = db.get_user_full(uid)
    if not user_data:
        return bot.reply_to(message, "❌ Avval /start buyrug'ini yuboring!")
    
    text = f"""
📊 <b>STATISTIKANGIZ</b>

👥 Takliflar: {user_data['invites']}
📈 Jami tarixiy: {user_data['total_invites_history']}
⭐ Yulduzlar: {user_data['stars']}
👑 VIP: {"✅" if user_data['vip'] else "❌"}
🕐 Oxirgi faollik: {user_data['last_activity'] or 'Noma'lum'}
"""
    bot.reply_to(message, text)

@bot.message_handler(commands=['help'])
def help_cmd(message):
    """Yordam buyrug'i"""
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

# ================= RATE LIMITING QO'SHILGAN START =================
@bot.message_handler(commands=["start"])
@rate_limit_check
def start(m):
    uid = m.from_user.id
    
    # Ban tekshiruvi
    if db.check_ban(uid):
        return bot.send_message(m.chat.id, "❌ Siz bloklangansiz!")
    
    # Xavfsizlik log
    db.log_security(uid, "start_command")
    
    if not check_sub(uid):
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(
            "📢 Kanalga obuna bo'lish", 
            url=f"https://t.me/{config.CHANNEL_USERNAME[1:]}"
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
    
    # Referrer tekshirish
    if m.text and len(m.text.split()) > 1:
        try:
            referrer_id = int(m.text.split()[1])
            if referrer_id != uid:
                db.add_invite_with_history(referrer_id)
                bot.send_message(
                    referrer_id,
                    f"🎉 Yangi odam qo'shildi! +1 taklif"
                )
        except:
            pass
    
    menu(uid, m.chat.id)

# Kanalga obuna tekshirish callback
@bot.callback_query_handler(func=lambda c: c.data == "check_sub")
def check_sub_callback(call):
    uid = call.from_user.id
    if check_sub(uid):
        db.create_user(uid, call.from_user.username, call.from_user.first_name)
        bot.delete_message(call.message.chat.id, call.message.message_id)
        menu(uid, call.message.chat.id)
    else:
        bot.answer_callback_query(
            call.id, 
            "❌ Hali obuna bo'lmadingiz!", 
            show_alert=True
        )

# ================= XAVFSIZLIK MONITORING =================
def security_monitor():
    """Xavfsizlik monitoringi - har 5 daqiqada ishlaydi"""
    while True:
        try:
            stats = db.get_stats()
            
            # Shubhali faollik tekshirish
            if stats['banned_users'] > stats['total_users'] * 0.1:  # 10% dan ko'p ban
                alert = f"⚠️ Ko'p ban qilingan foydalanuvchilar: {stats['banned_users']}"
                bot.send_message(config.ADMIN_ID, alert)
            
            # Token xavfsizligi
            if config.API_TOKEN in str(bot.get_me()):
                logger.critical("🚨 TOKEN XAVFSIZLIK MUAMMOSI!")
                bot.send_message(config.ADMIN_ID, "🚨 Favqulodda: Token himoyasiz!")
            
        except Exception as e:
            logger.error(f"Xavfsizlik monitoring xatosi: {e}")
        
        time.sleep(300)  # 5 daqiqa

# ================= MAIN =================
if __name__ == "__main__":
    # Xavfsizlik tekshiruvi
    print(f"🔐 Bot himoyalangan: {DataEncryptor.secure_token(config.API_TOKEN)}")
    print(f"🆔 Admin ID: {config.ADMIN_ID}")
    print("🚀 BOT ISHGA TUSHIRILDI")
    
    # Threadlarni ishga tushirish
    threads = [
        Thread(target=leaderboard_scheduler, daemon=True),
        Thread(target=security_monitor, daemon=True),
    ]
    
    for thread in threads:
        thread.start()
    
    # Asosiy loop
    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=30)
        except KeyboardInterrupt:
            print("👋 Bot to'xtatildi")
            break
        except Exception as e:
            logger.critical(f"KRITIK XATOLIK: {e}")
            bot.send_message(config.ADMIN_ID, f"🚨 Bot qayta ishga tushirilmoqda: {e}")
            time.sleep(5)
