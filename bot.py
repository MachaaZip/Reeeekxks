import logging
import os
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import (
    ApplicationBuilder, ContextTypes, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters, PreCheckoutQueryHandler
)

# --- AYARLAR ---
BOT_TOKEN = "8834867490:AAF5APycsOIRUZCob_WOJ2D1EVV2rl_jjWw"
ADMIN_ID = 8882473760
BOT_USERNAME = "derincpmhesap_bot"
SUPPORT_USERNAME = "derincpm"

# Zorunlu kanallar
ZORUNLU_KANALLAR = [
    {"isim": "Derin CPM",      "link": "https://t.me/+I8XZQqqxz1iYWJk", "chat_id": -1002323438891},
    {"isim": "Derin Güvence",  "link": "https://t.me/deringuvenve",      "chat_id": "@deringuvenve"},
    {"isim": "Derin İlan",     "link": "https://t.me/derincpmilan",      "chat_id": "@derincpmilan"},
    {"isim": "CPM İlan",       "link": "https://t.me/cpmilann",          "chat_id": "@cpmilann"},
]

# Referans başına verilen puan
REFERRAL_PUAN = 1

DB_FILE = "shop_data.json"
TEMP_DIR = "temp_files"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- YARDIMCI: TXT PARSE ---
def parse_txt(file_path):
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()

    hesaplar = []

    if "Email:" in content or "Password:" in content:
        bloklar = content.split("Email:")
        for blok in bloklar:
            blok = blok.strip()
            if not blok:
                continue
            email = ""
            password = ""
            for line in blok.splitlines():
                line = line.strip()
                if not email and "@" in line and "Password" not in line:
                    email = line
                if line.startswith("Password:"):
                    password = line.replace("Password:", "").strip()
            if email and password:
                hesaplar.append(f"{email} | {password}")
        if hesaplar:
            return hesaplar

    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        if "|" in line:
            parts = line.split("|")
            if len(parts) >= 2:
                hesaplar.append(f"{parts[0].strip()} | {parts[1].strip()}")
        elif ":" in line and "@" in line and "Email" not in line and "Password" not in line:
            parts = line.split(":", 1)
            if len(parts) == 2:
                hesaplar.append(f"{parts[0].strip()} | {parts[1].strip()}")

    return hesaplar

# --- VERİ YÖNETİMİ ---
def load_data():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, ValueError):
            data = {}
        if "users" not in data:
            data["users"] = {}
        if "next_id" not in data:
            data["next_id"] = 1
        if "products" not in data:
            data["products"] = {}
        for u_id, u_data in data["products"].items():
            if "fiyat_stars" not in u_data:
                u_data["fiyat_stars"] = u_data.get("fiyat", 10)
            if "fiyat_ref" not in u_data:
                u_data["fiyat_ref"] = 10
            if "stok" not in u_data:
                u_data["stok"] = []
            if "isim" not in u_data:
                u_data["isim"] = f"Ürün {u_id}"
        return data
    return {"products": {}, "next_id": 1, "users": {}}

def save_data(data):
    with open(DB_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_user(data, user_id):
    if "users" not in data:
        data["users"] = {}
    uid = str(user_id)
    if uid not in data["users"]:
        data["users"][uid] = {
            "ref_points": 0,
            "referred_by": None,
            "ref_count": 0,
            "purchases": []
        }
    u = data["users"][uid]
    u.setdefault("ref_points", 0)
    u.setdefault("referred_by", None)
    u.setdefault("ref_count", 0)
    u.setdefault("purchases", [])
    return u

bekleyen_islem = {}

# --- KANAL KONTROLÜ ---
async def eksik_kanallar(context, user_id):
    eksik = []
    for kanal in ZORUNLU_KANALLAR:
        try:
            uye = await context.bot.get_chat_member(chat_id=kanal["chat_id"], user_id=user_id)
            if uye.status in ("left", "kicked"):
                eksik.append(kanal)
        except Exception as e:
            logging.warning(f"Kanal kontrolü başarısız ({kanal['isim']}): {e}")
            continue
    return eksik

def katilim_mesaji(eksik_liste):
    metin = "🔒 **Botu kullanmak için aşağıdaki kanallara katılmanız gerekiyor:**\n\n"
    keyboard = []
    for kanal in eksik_liste:
        metin += f"• {kanal['isim']}\n"
        keyboard.append([InlineKeyboardButton(f"📢 {kanal['isim']}", url=kanal["link"])])
    keyboard.append([InlineKeyboardButton("✅ Katıldım, Kontrol Et", callback_data="check_join")])
    keyboard.append([InlineKeyboardButton("💬 Destek", url=f"https://t.me/{SUPPORT_USERNAME}")])
    return metin, InlineKeyboardMarkup(keyboard)

# --- ANA MENÜ ---
def ana_menu(data):
    keyboard = []
    for u_id, u_data in data["products"].items():
        if u_data["stok"]:
            keyboard.append([InlineKeyboardButton(
                f"{u_data['isim']} - ⭐️{u_data['fiyat_stars']} | 🎯{u_data['fiyat_ref']}",
                callback_data=f"view_{u_id}"
            )])
    keyboard.append([InlineKeyboardButton("👤 Profilim", callback_data="profile")])
    keyboard.append([InlineKeyboardButton("❤️ Bağış Yap", callback_data="donate")])
    keyboard.append([InlineKeyboardButton("💬 Destek", url=f"https://t.me/{SUPPORT_USERNAME}")])
    return InlineKeyboardMarkup(keyboard)

# --- YÖNETİCİ FONKSİYONLARI ---

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Bu komut sadece yönetici içindir.")
        return
    keyboard = [
        [InlineKeyboardButton("📦 Yeni Ürün Ekle", callback_data="admin_new_product")],
        [InlineKeyboardButton("✏️ Ürün Düzenle / Sil", callback_data="admin_edit_menu")],
        [InlineKeyboardButton("📤 Stok Yükle (.txt)", callback_data="admin_upload_stock")],
        [InlineKeyboardButton("📊 Ürünleri Listele", callback_data="admin_list_products")],
        [InlineKeyboardButton("👥 Kullanıcı Bilgisi", callback_data="admin_user_info")],
        [InlineKeyboardButton("🎁 Kullanıcıya Puan Ver", callback_data="admin_give_points")]
    ]
    await update.message.reply_text(
        "👋 **Yönetici Paneli**\n\n"
        "Fiyat belirlerken `Yıldız/Referans` formatını kullanın.\n"
        "Örnek: `25/30` (25 Yıldız VEYA 30 Referans puanı)",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id != ADMIN_ID:
        await query.edit_message_text("⛔ Bu komut sadece yönetici içindir.")
        return

    data = load_data()

    if query.data == "admin_new_product":
        bekleyen_islem[user_id] = {"action": "new_product", "step": "name"}
        await query.edit_message_text("📝 Yeni ürünün **ismini** yazın:", parse_mode="Markdown")

    elif query.data == "admin_edit_menu":
        if not data["products"]:
            await query.edit_message_text("❌ Henüz ürün yok.")
            return
        keyboard = []
        for u_id, u_data in data["products"].items():
            keyboard.append([InlineKeyboardButton(
                f"{u_data['isim']} ({len(u_data['stok'])} stok)",
                callback_data=f"admin_edit_pick_{u_id}"
            )])
        keyboard.append([InlineKeyboardButton("🔙 Geri", callback_data="admin_back")])
        await query.edit_message_text("✏️ Düzenlemek istediğiniz ürünü seçin:", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith("admin_edit_pick_"):
        urun_id = query.data.replace("admin_edit_pick_", "")
        urun = data["products"].get(urun_id)
        if not urun:
            await query.edit_message_text("❌ Ürün bulunamadı.")
            return
        keyboard = [
            [InlineKeyboardButton("✏️ İsmi Değiştir", callback_data=f"admin_edit_name_{urun_id}")],
            [InlineKeyboardButton("💰 Fiyatı Değiştir", callback_data=f"admin_edit_price_{urun_id}")],
            [InlineKeyboardButton("📤 Stok Ekle", callback_data=f"admin_edit_addstock_{urun_id}")],
            [InlineKeyboardButton("🗑️ Stokları Temizle", callback_data=f"admin_edit_clearstock_{urun_id}")],
            [InlineKeyboardButton("❌ Ürünü Sil", callback_data=f"admin_edit_delete_{urun_id}")],
            [InlineKeyboardButton("🔙 Geri", callback_data="admin_edit_menu")]
        ]
        await query.edit_message_text(
            f"📦 **{urun['isim']}**\n\n"
            f"⭐️ Yıldız: {urun['fiyat_stars']}\n"
            f"🎯 Referans: {urun['fiyat_ref']}\n"
            f"📊 Stok: {len(urun['stok'])}\n\n"
            f"Ne yapmak istersiniz?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif query.data.startswith("admin_edit_name_"):
        urun_id = query.data.replace("admin_edit_name_", "")
        bekleyen_islem[user_id] = {"action": "edit_name", "product_id": urun_id}
        await query.edit_message_text(f"✏️ Yeni ismi yazın:")

    elif query.data.startswith("admin_edit_price_"):
        urun_id = query.data.replace("admin_edit_price_", "")
        bekleyen_islem[user_id] = {"action": "edit_price", "product_id": urun_id}
        await query.edit_message_text(
            f"💰 Yeni fiyatı `Yıldız/Referans` formatında yazın.\nÖrnek: `25/30`",
            parse_mode="Markdown"
        )

    elif query.data.startswith("admin_edit_addstock_"):
        urun_id = query.data.replace("admin_edit_addstock_", "")
        bekleyen_islem[user_id] = {"action": "upload_stock", "product_id": urun_id}
        await query.edit_message_text(
            f"📄 **{data['products'][urun_id]['isim']}** için .txt dosyasını gönderin.",
            parse_mode="Markdown"
        )

    elif query.data.startswith("admin_edit_clearstock_"):
        urun_id = query.data.replace("admin_edit_clearstock_", "")
        data["products"][urun_id]["stok"] = []
        save_data(data)
        await query.edit_message_text(
            f"✅ **{data['products'][urun_id]['isim']}** için tüm stoklar temizlendi."
        )

    elif query.data.startswith("admin_edit_delete_"):
        urun_id = query.data.replace("admin_edit_delete_", "")
        keyboard = [
            [InlineKeyboardButton("✅ Evet, Sil", callback_data=f"admin_edit_confirmdelete_{urun_id}")],
            [InlineKeyboardButton("❌ Vazgeç", callback_data=f"admin_edit_pick_{urun_id}")]
        ]
        await query.edit_message_text(
            f"⚠️ **{data['products'][urun_id]['isim']}** ürününü silmek istediğinizden emin misiniz?\n\n"
            f"Bu işlem geri alınamaz!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif query.data.startswith("admin_edit_confirmdelete_"):
        urun_id = query.data.replace("admin_edit_confirmdelete_", "")
        if urun_id in data["products"]:
            isim = data["products"][urun_id]["isim"]
            del data["products"][urun_id]
            save_data(data)
            await query.edit_message_text(f"✅ **{isim}** ürünü silindi.")
        else:
            await query.edit_message_text("❌ Ürün zaten yok.")

    elif query.data == "admin_upload_stock":
        if not data["products"]:
            await query.edit_message_text("❌ Henüz ürün yok.")
            return
        keyboard = []
        for u_id, u_data in data["products"].items():
            keyboard.append([InlineKeyboardButton(u_data["isim"], callback_data=f"admin_upload_{u_id}")])
        keyboard.append([InlineKeyboardButton("🔙 Geri", callback_data="admin_back")])
        await query.edit_message_text("📦 Hangi ürüne stok yükleyeceksiniz?", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data.startswith("admin_upload_"):
        urun_id = query.data.replace("admin_upload_", "")
        bekleyen_islem[user_id] = {"action": "upload_stock", "product_id": urun_id}
        await query.edit_message_text(
            f"📄 **{data['products'][urun_id]['isim']}** için .txt dosyasını gönderin.",
            parse_mode="Markdown"
        )

    elif query.data == "admin_list_products":
        if not data["products"]:
            await query.edit_message_text("❌ Ürün yok.")
            return
        metin = "📋 **Ürünler:**\n\n"
        for u_id, u_data in data["products"].items():
            metin += f"🔹 **{u_data['isim']}**\n"
            metin += f"   ⭐️ {u_data['fiyat_stars']} | 🎯 {u_data['fiyat_ref']} Referans\n"
            metin += f"   Stok: {len(u_data['stok'])}\n\n"
        await query.edit_message_text(metin, parse_mode="Markdown")

    elif query.data == "admin_user_info":
        bekleyen_islem[user_id] = {"action": "user_info"}
        await query.edit_message_text("👤 Bilgisi görülecek kullanıcının **ID**'sini yazın:")

    elif query.data == "admin_give_points":
        bekleyen_islem[user_id] = {"action": "give_points", "step": "user"}
        await query.edit_message_text("🎁 Puan verilecek kullanıcının **ID**'sini yazın:")

    elif query.data == "admin_back":
        keyboard = [
            [InlineKeyboardButton("📦 Yeni Ürün Ekle", callback_data="admin_new_product")],
            [InlineKeyboardButton("✏️ Ürün Düzenle / Sil", callback_data="admin_edit_menu")],
            [InlineKeyboardButton("📤 Stok Yükle (.txt)", callback_data="admin_upload_stock")],
            [InlineKeyboardButton("📊 Ürünleri Listele", callback_data="admin_list_products")],
            [InlineKeyboardButton("👥 Kullanıcı Bilgisi", callback_data="admin_user_info")],
            [InlineKeyboardButton("🎁 Kullanıcıya Puan Ver", callback_data="admin_give_points")]
        ]
        await query.edit_message_text(
            "👋 **Yönetici Paneli**",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

# --- STOK YÜKLEME ---

async def handle_admin_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID or user_id not in bekleyen_islem:
        return
    islem = bekleyen_islem[user_id]
    if islem.get("action") != "upload_stock":
        return

    document = update.message.document
    if not document.file_name.endswith('.txt'):
        await update.message.reply_text("❌ Lütfen .txt uzantılı bir dosya gönderin.")
        return

    os.makedirs(TEMP_DIR, exist_ok=True)
    file_path = os.path.join(TEMP_DIR, f"{document.file_id}.txt")

    try:
        file = await context.bot.get_file(document.file_id)
        await file.download_to_drive(file_path)
    except Exception as e:
        await update.message.reply_text(f"❌ Dosya indirilemedi: {e}")
        return

    try:
        hesaplar = parse_txt(file_path)
    except Exception as e:
        await update.message.reply_text(f"❌ Dosya okunamadı: {e}")
        return
    finally:
        try:
            os.remove(file_path)
        except OSError:
            pass

    if not hesaplar:
        await update.message.reply_text("❌ Dosya boş veya formata uygun değil.")
        return

    data = load_data()
    urun_id = islem["product_id"]
    data["products"][urun_id]["stok"].extend(hesaplar)
    save_data(data)
    del bekleyen_islem[user_id]

    await update.message.reply_text(
        f"✅ **Stok Yüklendi!**\n\n"
        f"Ürün: {data['products'][urun_id]['isim']}\n"
        f"Eklenen: {len(hesaplar)} adet\n"
        f"Toplam Stok: {len(data['products'][urun_id]['stok'])}",
        parse_mode="Markdown"
    )

# --- METİN İŞLEMLERİ (Admin) ---

async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_ID or user_id not in bekleyen_islem:
        return

    islem = bekleyen_islem[user_id]
    text = update.message.text.strip()

    if islem.get("action") == "new_product":
        if islem["step"] == "name":
            islem["name"] = text
            islem["step"] = "price"
            await update.message.reply_text(
                f"💰 **{text}** için fiyatı yazın.\n\n"
                "Format: `Yıldız/Referans`\nÖrnek: `25/30`",
                parse_mode="Markdown"
            )
        elif islem["step"] == "price":
            try:
                if "/" not in text:
                    await update.message.reply_text("❌ Format `Yıldız/Referans` olmalı. Örnek: `25/30`")
                    return
                parts = text.split("/")
                fiyat_stars = int(parts[0].strip())
                fiyat_ref = int(parts[1].strip())
                if fiyat_stars <= 0 or fiyat_ref <= 0:
                    raise ValueError

                data = load_data()
                urun_id = str(data["next_id"])
                data["products"][urun_id] = {
                    "isim": islem["name"],
                    "fiyat_stars": fiyat_stars,
                    "fiyat_ref": fiyat_ref,
                    "stok": []
                }
                data["next_id"] += 1
                save_data(data)
                del bekleyen_islem[user_id]

                await update.message.reply_text(
                    f"✅ **Ürün Oluşturuldu!**\n\n"
                    f"📦 {islem['name']}\n"
                    f"⭐️ {fiyat_stars} Yıldız\n"
                    f"🎯 {fiyat_ref} Referans Puanı\n\n"
                    f"Şimdi `/admin` ile stok yükleyebilirsiniz.",
                    parse_mode="Markdown"
                )
            except (ValueError, IndexError):
                await update.message.reply_text("❌ Geçersiz format. Örnek: `25/30`")

    elif islem.get("action") == "edit_name":
        data = load_data()
        urun_id = islem["product_id"]
        eski_isim = data["products"][urun_id]["isim"]
        data["products"][urun_id]["isim"] = text
        save_data(data)
        del bekleyen_islem[user_id]
        await update.message.reply_text(f"✅ İsim değiştirildi:\n`{eski_isim}` → `{text}`", parse_mode="Markdown")

    elif islem.get("action") == "edit_price":
        try:
            if "/" not in text:
                await update.message.reply_text("❌ Format `Yıldız/Referans` olmalı. Örnek: `25/30`")
                return
            parts = text.split("/")
            fiyat_stars = int(parts[0].strip())
            fiyat_ref = int(parts[1].strip())
            if fiyat_stars <= 0 or fiyat_ref <= 0:
                raise ValueError

            data = load_data()
            urun_id = islem["product_id"]
            data["products"][urun_id]["fiyat_stars"] = fiyat_stars
            data["products"][urun_id]["fiyat_ref"] = fiyat_ref
            save_data(data)
            del bekleyen_islem[user_id]

            await update.message.reply_text(
                f"✅ **Fiyat güncellendi!**\n\n"
                f"📦 {data['products'][urun_id]['isim']}\n"
                f"⭐️ {fiyat_stars} Yıldız\n"
                f"🎯 {fiyat_ref} Referans",
                parse_mode="Markdown"
            )
        except (ValueError, IndexError):
            await update.message.reply_text("❌ Geçersiz format. Örnek: `25/30`")

    elif islem.get("action") == "user_info":
        try:
            target_id = str(int(text))
            data = load_data()
            if target_id not in data["users"]:
                await update.message.reply_text("❌ Kullanıcı bulunamadı.")
                del bekleyen_islem[user_id]
                return
            u = data["users"][target_id]
            await update.message.reply_text(
                f"👤 **Kullanıcı Bilgisi**\n\n"
                f"🆔 ID: `{target_id}`\n"
                f"🎯 Referans Puanı: {u.get('ref_points', 0)}\n"
                f"👥 Davet Edilen: {u.get('ref_count', 0)}\n"
                f"🛒 Satın Alımlar: {len(u.get('purchases', []))}",
                parse_mode="Markdown"
            )
            del bekleyen_islem[user_id]
        except ValueError:
            await update.message.reply_text("❌ Geçerli bir ID girin.")

    elif islem.get("action") == "give_points":
        if islem["step"] == "user":
            try:
                target_id = str(int(text))
                islem["target_id"] = target_id
                islem["step"] = "amount"
                await update.message.reply_text(f"🎁 `{target_id}` kullanıcısına kaç puan vermek istersiniz?")
            except ValueError:
                await update.message.reply_text("❌ Geçerli bir ID girin.")
        elif islem["step"] == "amount":
            try:
                miktar = int(text)
                target_id = islem["target_id"]
                data = load_data()
                if target_id not in data["users"]:
                    data["users"][target_id] = {
                        "ref_points": 0, "referred_by": None,
                        "ref_count": 0, "purchases": []
                    }
                data["users"][target_id]["ref_points"] = data["users"][target_id].get("ref_points", 0) + miktar
                save_data(data)
                del bekleyen_islem[user_id]

                await update.message.reply_text(
                    f"✅ `{target_id}` kullanıcısına **{miktar}** puan eklendi.\n"
                    f"Yeni toplam: {data['users'][target_id]['ref_points']} 🎯",
                    parse_mode="Markdown"
                )
                try:
                    await context.bot.send_message(
                        chat_id=int(target_id),
                        text=f"🎁 **Puan Kazandınız!**\n\n"
                             f"Yönetici size **{miktar}** referans puanı verdi.\n"
                             f"Toplam: {data['users'][target_id]['ref_points']} 🎯",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
            except ValueError:
                await update.message.reply_text("❌ Geçerli bir sayı girin.")

# --- KULLANICI FONKSİYONLARI ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    # Admin için zorunluluk yok
    if user.id != ADMIN_ID:
        eksik = await eksik_kanallar(context, user.id)
        if eksik:
            metin, markup = katilim_mesaji(eksik)
            await update.message.reply_text(metin, reply_markup=markup, parse_mode="Markdown")
            return

    data = load_data()
    u = get_user(data, user.id)

    # Referans işleme - her kişi = 1 puan
    if context.args:
        ref_id = context.args[0]
        if ref_id.startswith("ref_"):
            ref_id = ref_id.replace("ref_", "")
            if ref_id != str(user.id) and u["referred_by"] is None and ref_id in data["users"]:
                u["referred_by"] = ref_id
                data["users"][ref_id]["ref_points"] = data["users"][ref_id].get("ref_points", 0) + REFERRAL_PUAN
                data["users"][ref_id]["ref_count"] = data["users"][ref_id].get("ref_count", 0) + 1
                try:
                    await context.bot.send_message(
                        chat_id=int(ref_id),
                        text=f"🎉 **Yeni Referans!**\n\n"
                             f"Bir kullanıcı sizin davet linkinizle katıldı.\n"
                             f"+{REFERRAL_PUAN} Referans Puanı kazandınız!\n"
                             f"Toplam: {data['users'][ref_id]['ref_points']} puan 🎯",
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass

    save_data(data)

    await update.message.reply_text(
        f"🤖 **Mağazaya Hoş Geldiniz, {user.first_name}!**\n\n"
        "Ürünleri inceleyin veya profil sayfanızdan referans linkinizi alın.",
        reply_markup=ana_menu(data),
        parse_mode="Markdown"
    )

async def check_join_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    eksik = await eksik_kanallar(context, user_id)
    if eksik:
        metin, markup = katilim_mesaji(eksik)
        await query.edit_message_text(
            "❌ **Henüz tüm kanallara katılmadınız!**\n\n" + metin,
            reply_markup=markup,
            parse_mode="Markdown"
        )
        return

    data = load_data()
    get_user(data, user_id)
    save_data(data)
    await query.edit_message_text(
        "✅ **Katılımınız onaylandı!**\n\nAna menüye yönlendiriliyorsunuz...",
        parse_mode="Markdown"
    )
    await context.bot.send_message(
        chat_id=user_id,
        text="🏪 **Ana Menü**\n\nBir ürün seçin:",
        reply_markup=ana_menu(data),
        parse_mode="Markdown"
    )

async def user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if user_id != ADMIN_ID:
        eksik = await eksik_kanallar(context, user_id)
        if eksik:
            metin, markup = katilim_mesaji(eksik)
            await query.edit_message_text(metin, reply_markup=markup, parse_mode="Markdown")
            return

    data = load_data()
    u = get_user(data, user_id)

    if query.data.startswith("view_"):
        urun_id = query.data.replace("view_", "")
        urun = data["products"].get(urun_id)
        if not urun:
            await query.edit_message_text("❌ Ürün bulunamadı.")
            return
        keyboard = [
            [InlineKeyboardButton(f"⭐️ {urun['fiyat_stars']} Yıldız ile Al", callback_data=f"buystars_{urun_id}")],
            [InlineKeyboardButton(f"🎯 {urun['fiyat_ref']} Referans ile Al", callback_data=f"buyref_{urun_id}")],
            [InlineKeyboardButton("🔙 Geri", callback_data="back_main")]
        ]
        await query.edit_message_text(
            f"📦 **{urun['isim']}**\n\n"
            f"⭐️ Yıldız Fiyatı: {urun['fiyat_stars']}\n"
            f"🎯 Referans Fiyatı: {urun['fiyat_ref']}\n"
            f"📊 Stok: {len(urun['stok'])}\n\n"
            f"Ödeme yöntemini seçin:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )

    elif query.data.startswith("buystars_"):
        urun_id = query.data.replace("buystars_", "")
        urun = data["products"].get(urun_id)
        if not urun or not urun["stok"]:
            await query.edit_message_text("❌ Stok bitti!")
            return
        await context.bot.send_invoice(
            chat_id=user_id,
            title=urun["isim"],
            description=f"{urun['isim']} satın alımı",
            payload=f"buy_{urun_id}",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label=urun["isim"], amount=urun["fiyat_stars"])]
        )
        await query.edit_message_text("💳 Ödeme faturası gönderildi.")

    elif query.data.startswith("buyref_"):
        urun_id = query.data.replace("buyref_", "")
        urun = data["products"].get(urun_id)
        if not urun or not urun["stok"]:
            await query.edit_message_text("❌ Stok bitti!")
            return
        if u["ref_points"] < urun["fiyat_ref"]:
            await query.edit_message_text(
                f"❌ **Yetersiz Referans Puanı!**\n\n"
                f"Gereken: {urun['fiyat_ref']} 🎯\n"
                f"Sizde: {u['ref_points']} 🎯",
                parse_mode="Markdown"
            )
            return
        u["ref_points"] -= urun["fiyat_ref"]
        hesap = urun["stok"].pop(0)
        u["purchases"].append({"urun": urun["isim"], "hesap": hesap})
        save_data(data)

        await query.edit_message_text(
            f"✅ **Satın Alma Başarılı!**\n\n"
            f"📦 Ürün: {urun['isim']}\n"
            f"🔑 Hesap:\n`{hesap}`\n\n"
            f"Kalan Referans Puanınız: {u['ref_points']} 🎯",
            parse_mode="Markdown"
        )
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"💰 **Referans Satışı!**\n\n"
                     f"Ürün: {urun['isim']}\n"
                     f"Alıcı: {user_id}\n"
                     f"Kalan Stok: {len(urun['stok'])}",
                parse_mode="Markdown"
            )
        except Exception:
            pass

    elif query.data == "profile":
        ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
        await query.edit_message_text(
            f"👤 **Profilim**\n\n"
            f"🆔 ID: `{user_id}`\n"
            f"🎯 Referans Puanı: **{u['ref_points']}**\n"
            f"👥 Davet Edilen: **{u['ref_count']}**\n"
            f"🛒 Satın Alım: **{len(u['purchases'])}**\n\n"
            f"🔗 **Davet Linkiniz:**\n`{ref_link}`\n\n"
            f"ℹ️ Her davet ettiğiniz kişi için **+{REFERRAL_PUAN} puan** kazanırsınız.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data="back_main")]]),
            parse_mode="Markdown"
        )

    elif query.data == "donate":
        context.user_data['awaiting_donation'] = True
        await query.edit_message_text("💰 Kaç Yıldız bağışlamak istersiniz? (Örnek: 10)")

    elif query.data == "back_main":
        await query.edit_message_text(
            "🏪 **Ana Menü**\n\nBir ürün seçin:",
            reply_markup=ana_menu(data),
            parse_mode="Markdown"
        )

async def handle_donation_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get('awaiting_donation'):
        try:
            miktar = int(update.message.text)
            if miktar <= 0:
                raise ValueError
            context.user_data['awaiting_donation'] = False
            await context.bot.send_invoice(
                chat_id=update.effective_user.id,
                title="Bağış",
                description="Geliştiriciye destek",
                payload="donate",
                provider_token="",
                currency="XTR",
                prices=[LabeledPrice(label="Bağış", amount=miktar)]
            )
            await update.message.reply_text("💳 Bağış faturası gönderildi. Teşekkürler!")
        except ValueError:
            await update.message.reply_text("❌ Geçerli bir sayı girin.")
            context.user_data['awaiting_donation'] = False

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id != ADMIN_ID:
        eksik = await eksik_kanallar(context, user_id)
        if eksik:
            metin, markup = katilim_mesaji(eksik)
            await update.message.reply_text(metin, reply_markup=markup, parse_mode="Markdown")
            return

    data = load_data()
    u = get_user(data, user_id)
    ref_link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"

    await update.message.reply_text(
        f"👤 **Profilim**\n\n"
        f"🆔 ID: `{user_id}`\n"
        f"🎯 Referans Puanı: **{u['ref_points']}**\n"
        f"👥 Davet Edilen: **{u['ref_count']}**\n"
        f"🛒 Satın Alım: **{len(u['purchases'])}**\n\n"
        f"🔗 **Davet Linkiniz:**\n`{ref_link}`\n\n"
        f"ℹ️ Her davet için **+{REFERRAL_PUAN} puan** kazanırsınız.",
        parse_mode="Markdown"
    )

# --- BAKİYE KOMUTU (ADMIN) ---
async def bakiye_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Bu komut sadece yönetici içindir.")
        return
    try:
        result = await context.bot.get_my_star_balance()
        await update.message.reply_text(
            f"⭐️ **Stars Bakiyeniz**\n\n"
            f"Toplam: {result.amount} ⭐️\n\n"
            f"💡 Çekmek için: @BotFather → /mybots → Botunuz → Stars",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ Bakiye alınamadı: {e}\n\n"
            f"Botunuzda Stars ödemesi henüz yapılmamış olabilir."
        )

# --- ÖDEME İŞLEMLERİ ---

async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    payment = update.message.successful_payment
    user_id = update.effective_user.id
    payload = payment.invoice_payload

    if payload == "donate":
        await update.message.reply_text(
            f"🙏 **Teşekkürler!**\n\n{payment.total_amount} ⭐️ bağışınız alındı.",
            parse_mode="Markdown"
        )
        return

    if payload.startswith("buy_"):
        urun_id = payload.replace("buy_", "")
        data = load_data()
        urun = data["products"].get(urun_id)
        u = get_user(data, user_id)

        if urun and urun["stok"]:
            hesap = urun["stok"].pop(0)
            u["purchases"].append({"urun": urun["isim"], "hesap": hesap})
            save_data(data)

            await update.message.reply_text(
                f"✅ **Satın Alma Başarılı!**\n\n"
                f"📦 Ürün: {urun['isim']}\n"
                f"🔑 Hesap:\n`{hesap}`\n\nTeşekkürler!",
                parse_mode="Markdown"
            )
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"💰 **Satış!**\n\n"
                         f"Ürün: {urun['isim']}\n"
                         f"Fiyat: {payment.total_amount} ⭐️\n"
                         f"Alıcı: {user_id}\n"
                         f"Kalan Stok: {len(urun['stok'])}",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logging.error(f"Admin bildirim hatası: {e}")
        else:
            await update.message.reply_text("⚠️ Stok bitti, yönetici ile iletişime geçin.")

# --- HATA YÖNETİMİ ---

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logging.error(f"Hata: {context.error}", exc_info=context.error)

# --- ANA ÇALIŞTIRICI ---

if __name__ == '__main__':
    if not os.path.exists(DB_FILE):
        save_data({"products": {}, "next_id": 1, "users": {}})

    os.makedirs(TEMP_DIR, exist_ok=True)

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("profil", profile_command))
    app.add_handler(CommandHandler("bakiye", bakiye_command))

    app.add_handler(CallbackQueryHandler(check_join_callback, pattern="^check_join$"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
    app.add_handler(CallbackQueryHandler(user_callback, pattern="^(view_|buystars_|buyref_|profile|donate|back_main)"))

    app.add_handler(MessageHandler(filters.Document.ALL & filters.User(ADMIN_ID), handle_admin_document))
    app.add_handler(MessageHandler(filters.TEXT & filters.User(ADMIN_ID), handle_admin_text))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.User(ADMIN_ID), handle_donation_text))

    app.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))

    app.add_error_handler(error_handler)

    print("🚀 Bot çalışıyor...")
    app.run_polling()