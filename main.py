import logging
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError

# --- AYARLAR ---
BOT_TOKEN = "8637743696:AAG2S2JUlIjTUL-1vURcuUehclenxQD57Nw"
API_FOOTBALL_KEY = "4a1ec060be81f9b3ec70fdf58f5043bf"

# Sayısal ID tırnak içinde string olmalı
CHANNEL_ID = "@iddaa_analiz_kuponlar_2026"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 İddaa Analiz Botu Aktif! /analiz [Takım1] [Takım2] şeklinde kullanabilirsin.")

async def analiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Kullanım: /analiz [Takım1] [Takım2]")
        return
    
    t1, t2 = context.args[0], context.args[1]
    mesaj = f"📊 **{t1} vs {t2} Analiz Raporu**\n\n• Tahmin: 2.5 ÜST\n• Güven: %85\n• Maç Sonucu: KG Var"
    await update.message.reply_text(mesaj, parse_mode="Markdown")

async def gunluk_kupon_paylas(context: ContextTypes.DEFAULT_TYPE):
    mesaj = "🔥 **GÜNÜN İDDAA BÜLTENİ VE HAZIR KUPONU** 🔥\n\n1. Arsenal - Chelsea | 2.5 ÜST (1.65)\n2. Real Madrid - Barca | KG Var (1.50)\n\nToplam Oran: 2.47"
    try:
        await context.bot.send_message(chat_id=CHANNEL_ID, text=mesaj, parse_mode="Markdown")
        print("✅ Otomatik kupon kanala başarıyla gönderildi!")
    except TelegramError as e:
        print(f"❌ Mesaj gönderilemedi. Hata detay: {e}")

async def canlı_mac_kontrol(context: ContextTypes.DEFAULT_TYPE):
    pass

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("analiz", analiz))

    # Otomatik görevler (İlk paylaşım 5. saniyede tetiklenir)
    job_queue = app.job_queue
    job_queue.run_repeating(gunluk_kupon_paylas, interval=86400, first=5)
    job_queue.run_repeating(canlı_mac_kontrol, interval=300, first=10)

    print("🤖 Bot ve Otomatik Kanal Bildirimleri Çalışıyor...")
    app.run_polling()

if __name__ == "__main__":
    main()