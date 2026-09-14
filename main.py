import logging
import os
import requests
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- RENDER SAĞLIK KONTROLÜ ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot 7/24 Aktif!")

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()

def run_http_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
    server.serve_forever()

# --- AYARLAR ---
BOT_TOKEN = "8637743696:AAG2S2JUlIjTUL-1vURcuUehclenxQD57Nw"
API_FOOTBALL_KEY = "4a1ec060be81f9b3ec70fdf58f5043bf"
CHANNEL_ID = "@iddaa_analiz_kuponlar_2026"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

API_URL = "https://v3.football.api-sports.io"
HEADERS = {'x-apisports-key': API_FOOTBALL_KEY}

# --- 10 FARKLI KUPON LİSTESİ VE SIRASI ---
KUPONLAR = [
    "🔥 **GÜNÜN KUPONU #1 (YÜKSEK GÜVEN)** 🔥\n1. Arsenal - Chelsea | KG Var (1.60)\n2. Real Madrid - Barca | 2.5 ÜST (1.55)\nToplam Oran: 2.48",
    "💣 **GÜNÜN KUPONU #2 (SÜRPRİZ / ORAN)** 💣\n1. Bayern - Dortmund | MS 1 & 2.5 ÜST (1.95)\n2. Inter - Milan | KG Var (1.65)\nToplam Oran: 3.21",
    "⚡️ **GÜNÜN KUPONU #3 (İY/MS TEK MAÇ)** ⚡️\n1. PSG - Marseille | İY 1 / MS 1 (1.80)\nToplam Oran: 1.80",
    "⚽️ **GÜNÜN KUPONU #4 (GOL BÜLTENİ)** ⚽️\n1. Man City - Liverpool | 3.5 ÜST (2.10)\n2. Benfica - Porto | KG Var (1.70)\nToplam Oran: 3.57",
    "⚖️ **GÜNÜN KUPONU #5 (İDEAL KUPON)** ⚖️\n1. Ajax - Feyenoord | 2.5 ÜST (1.50)\n2. Napoli - Lazio | MS 1 (1.75)\nToplam Oran: 2.62",
    "🛡 **GÜNÜN KUPONU #6 (ALT/ÜST KUPONU)** 🛡\n1. Atletico Madrid - Sevilla | 2.5 ALT (1.75)\n2. Juventus - Roma | 2.5 ALT (1.65)\nToplam Oran: 2.88",
    "🚨 **GÜNÜN KUPONU #7 (CANLI ALARM KUPONU)** 🚨\n1. Leverkusen - Leipzig | KG Var (1.55)\n2. Celtic - Rangers | 2.5 ÜST (1.60)\nToplam Oran: 2.48",
    "🌙 **GÜNÜN KUPONU #8 (GECE BÜLTENİ)** 🌙\n1. Flamengo - Palmeiras | MS 1 (1.85)\n2. Boca Juniors - River | KG Var (1.90)\nToplam Oran: 3.51",
    "📐 **GÜNÜN KUPONU #9 (KORNER / İSTATİSTİK)** 📐\n1. Trabzonspor - BJK | 9.5 Korner ÜST (1.65)\n2. GS - FB | 4.5 Kart ÜST (1.50)\nToplam Oran: 2.47",
    "🏁 **GÜNÜN KUPONU #10 (GÜNÜN KAPANIŞI)** 🏁\n1. Sporting - Braga | MS 1 & 1.5 ÜST (1.70)\n2. Villarreal - Betis | KG Var (1.60)\nToplam Oran: 2.72"
]

kupon_index = 0

async def otomatik_kupon_gonder(app: Application):
    global kupon_index
    try:
        kupon_metni = KUPONLAR[kupon_index]
        await app.bot.send_message(chat_id=CHANNEL_ID, text=kupon_metni, parse_mode="Markdown")
        logging.info(f"Kupon #{kupon_index + 1} kanala atıldı.")
        kupon_index = (kupon_index + 1) % len(KUPONLAR)
    except Exception as e:
        logging.error(f"Kanala mesaj atma hatası: {e}")

# --- API YARDIMCI FONKSİYONLARI ---
def takim_id_bul(takim_adi):
    try:
        url = f"{API_URL}/teams?search={takim_adi}"
        res = requests.get(url, headers=HEADERS, timeout=10).json()
        if res.get("response"):
            return res["response"][0]["team"]["id"], res["response"][0]["team"]["name"]
    except Exception as e:
        logging.error(f"Takim arama hatasi: {e}")
    return None, takim_adi

def sakat_cezali_getir(t1_id, t2_id):
    """API üzerinden son sakat ve cezalı bilgisini kontrol eder"""
    t1_sakatlar, t2_sakatlar = [], []
    try:
        # Son/Gelecek fikstür üzerinden sakatlık verilerini tara
        url = f"{API_URL}/injuries?team={t1_id}"
        res = requests.get(url, headers=HEADERS, timeout=10).json()
        if res.get("response"):
            t1_sakatlar = [f"{i['player']['name']} ({i['player']['type']})" for i in res["response"][:3]]

        url2 = f"{API_URL}/injuries?team={t2_id}"
        res2 = requests.get(url2, headers=HEADERS, timeout=10).json()
        if res2.get("response"):
            t2_sakatlar = [f"{i['player']['name']} ({i['player']['type']})" for i in res2["response"][:3]]
    except Exception as e:
        logging.error(f"Sakat/Cezali hatasi: {e}")
    return t1_sakatlar, t2_sakatlar

# --- GELİŞMİŞ H2H + SAKAT/CEZALI + PUAN ANALİZ MOTORU ---
def derin_h2h_analiz(takim1_input, takim2_input):
    t1_id, t1_name = takim_id_bul(takim1_input)
    t2_id, t2_name = takim_id_bul(takim2_input)

    if not t1_id or not t2_id:
        return f"⚠️ **Arama Hatası:** '{takim1_input}' veya '{takim2_input}' takımı bulunamadı. Lütfen orijinal/İngilizce isimleriyle yazın (Örn: Galatasaray, Fenerbahce)."

    try:
        # 1. H2H Maç Geçmişi
        h2h_url = f"{API_URL}/fixtures/headtohead?h2h={t1_id}-{t2_id}"
        h2h_res = requests.get(h2h_url, headers=HEADERS, timeout=10).json()
        maclar = h2h_res.get("response", [])[:10]

        if not maclar:
            return f"ℹ️ **{t1_name}** ve **{t2_name}** arasında kayıtlı geçmiş maç verisi bulunamadı."

        total_matches = len(maclar)
        t1_wins, t2_wins, draws = 0, 0, 0
        total_goals, iy_goals = 0, 0
        kg_var_count, over_25_count = 0, 0
        skorlar = []

        for m in maclar:
            gh = m["goals"]["home"] if m["goals"]["home"] is not None else 0
            ga = m["goals"]["away"] if m["goals"]["away"] is not None else 0
            
            if len(skorlar) < 5:
                skorlar.append(f"{m['teams']['home']['name']} {gh} - {ga} {m['teams']['away']['name']}")

            home_id = m["teams"]["home"]["id"]
            if gh == ga:
                draws += 1
            elif (home_id == t1_id and gh > ga) or (home_id != t1_id and ga > gh):
                t1_wins += 1
            else:
                t2_wins += 1

            m_total = gh + ga
            total_goals += m_total
            if gh > 0 and ga > 0:
                kg_var_count += 1
            if m_total > 2.5:
                over_25_count += 1

            ht_h = m["score"]["halftime"]["home"] or 0
            ht_a = m["score"]["halftime"]["away"] or 0
            iy_goals += (ht_h + ht_a)

        # 2. Sakat & Cezalı Bilgileri Çek
        t1_sakat, t2_sakat = sakat_cezali_getir(t1_id, t2_id)

        avg_goals = round(total_goals / total_matches, 2)
        avg_iy_goals = round(iy_goals / total_matches, 2)
        kg_ratio = int((kg_var_count / total_matches) * 100)
        o25_ratio = int((over_25_count / total_matches) * 100)

        # Sakat/Cezalı Etkili Tahmin Motoru
        if len(t1_sakat) > len(t2_sakat):
            ms_tahmin = f"{t2_name} Avantajlı ({t1_name}'de {len(t1_sakat)} Kritik Eksik var)"
        elif t1_wins >= t2_wins:
            ms_tahmin = f"{t1_name} Çifte Şans (1X)"
        else:
            ms_tahmin = f"{t2_name} Çifte Şans (X2)"

        kg_tahmin = "KG VAR" if kg_ratio >= 50 else "KG YOK"
        gol_tahmin = "2.5 ÜST" if o25_ratio >= 50 else "2.5 ALT"

        skor_listesi_str = "\n".join([f"  • {s}" for s in skorlar])
        
        t1_sakat_str = ", ".join(t1_sakat) if t1_sakat else "Yok / Bildirilmedi"
        t2_sakat_str = ", ".join(t2_sakat) if t2_sakat else "Yok / Bildirilmedi"

        return (
            f"📊 **{t1_name.upper()} vs {t2_name.upper()} DERİN ANALİZ RAPORU**\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🗓 **Aralarındaki Son {total_matches} Maç:** {t1_wins} G | {t2_wins} G | {draws} B\n\n"
            f"📜 **Son Skorlar:**\n{skor_listesi_str}\n\n"
            f"⚽️ **Gol İstatistikleri:**\n"
            f"• Maç Başı Gol Ort.: {avg_goals}\n"
            f"• İlk Yarı Gol Ort.: {avg_iy_goals}\n"
            f"• 2.5 Üst Biten Maçlar: %{o25_ratio}\n"
            f"• KG Var Biten Maçlar: %{kg_ratio}\n\n"
            f"🏥 **Sakat / Cezalı Durumu:**\n"
            f"• {t1_name}: {t1_sakat_str}\n"
            f"• {t2_name}: {t2_sakat_str}\n\n"
            f"🎯 **SİSTEMİN YAPAY ZEKA TAHMİNİ:**\n"
            f"🔹 **Maç Sonucu:** {ms_tahmin}\n"
            f"🔹 **KG Durumu:** {kg_tahmin} (%{kg_ratio} Güven)\n"
            f"🔹 **Toplam Gol:** {gol_tahmin} (%{o25_ratio} Güven)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
    except Exception as e:
        logging.error(f"Analiz hatasi: {e}")
        return "⚠️ İstatistikler çekilirken bir sorun oluştu."

# --- BOT DİNLENMESİ ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Futbol Analiz Botu Aktif!\n\nDoğrudan `Galatasaray Fenerbahce` yazarak H2H ve Sakat/Cezalı analiz raporu alabilirsiniz.")

async def metin_dinleyici(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    kelimeler = update.message.text.strip().split()
    if len(kelimeler) >= 2:
        await update.message.reply_text(derin_h2h_analiz(kelimeler[0], kelimeler[1]), parse_mode="Markdown")

def main():
    Thread(target=run_http_server, daemon=True).start()
    
    app = Application.builder().token(BOT_TOKEN).job_queue(None).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, metin_dinleyici))

    # 2 Saatte 1 Otomatik Kupon Paylaşımı
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        otomatik_kupon_gonder, 
        'interval', 
        hours=2, 
        args=[app]
    )
    scheduler.start()

    print("🤖 Tüm Özellikleriyle Gelişmiş Bot Aktif!")
    app.run_polling()

if __name__ == "__main__":
    main()
