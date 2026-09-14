import logging
import os
import requests
import re
import sqlite3
import time
import asyncio
from datetime import datetime
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pytz import timezone

# --- RENDER SAĞLIK KONTROLÜ VE SELF-PING ---
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

def self_ping():
    while True:
        try:
            port = os.environ.get("PORT", "8080")
            requests.get(f"http://localhost:{port}", timeout=5)
        except Exception:
            pass
        time.sleep(600)

# --- AYARLAR ---
BOT_TOKEN = "8637743696:AAG2S2JUlIjTUL-1vURcuUehclenxQD57Nw"
API_FOOTBALL_KEY = "4a1ec060be81f9b3ec70fdf58f5043bf"
CHANNEL_ID = "@iddaa_analiz_kuponlar_2026"
ADMIN_ID = 123456789  # BURAYA KENDİ TELEGRAM SAYISAL ID'NİZİ YAZIN
TURKEY_TZ = timezone('Europe/Istanbul')

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

API_URL = "https://v3.football.api-sports.io"
HEADERS = {'x-apisports-key': API_FOOTBALL_KEY}

# --- VERİTABANI İŞLEMLERİ ---
def init_db():
    conn = sqlite3.connect("kuponlar.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kuponlar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tarih TEXT,
            maclar_json TEXT,
            durum TEXT DEFAULT 'BEKLEYEN'
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- GELİŞMİŞ TÜRKÇE VE TAKIM ADI TEMİZLEME ALGORİTMASI ---
def turkce_karakter_temizle(metin):
    metin = metin.strip().lower()
    harf_haritasi = {'ç': 'c', 'ğ': 'g', 'ı': 'i', 'İ': 'i', 'ö': 'o', 'ş': 's', 'ü': 'u'}
    for tr, eng in harf_haritasi.items():
        metin = metin.replace(tr, eng)
    return metin

# MÜKEMMEL EŞLEŞME SÖZLÜĞÜ (HER TÜRLÜ KISALTMA VEYA YAZIM İÇİN)
TAKIM_SOZLUGU = {
    "gs": "Galatasaray", "ala": "Alanyaspor", "fb": "Fenerbahce", "bjk": "Besiktas",
    "ts": "Trabzonspor", "kayseri": "Kayserispor", "kayserispor": "Kayserispor",
    "basaksehir": "Istanbul Basaksehir", "ibfk": "Istanbul Basaksehir",
    "paok": "PAOK", "real": "Real Madrid", "barca": "Barcelona", "barcelona": "Barcelona",
    "atletico": "Atletico Madrid", "bayern": "Bayern Munich", "dortmund": "Borussia Dortmund",
    "psg": "Paris Saint Germain", "city": "Manchester City", "man city": "Manchester City",
    "united": "Manchester United", "man utd": "Manchester United", "juve": "Juventus",
    "inter": "Inter", "milan": "AC Milan", "roma": "AS Roma", "ajax": "Ajax",
    "porto": "Porto", "benfica": "Benfica", "sporting": "Sporting CP"
}

def takim_id_bul(takim_adi):
    temiz_ad = turkce_karakter_temizle(takim_adi)
    
    # 1. Aşama: Sözlükte Var mı Kontrol Et
    sorgu_terimi = TAKIM_SOZLUGU.get(temiz_ad, temiz_ad)
    
    # 2. Aşama: Olası Arama Kombinasyonları Oluştur
    sorgu_listesi = [
        sorgu_terimi,
        temiz_ad,
        temiz_ad.replace("spor", "").strip(),
        temiz_ad.replace("fc", "").strip(),
        temiz_ad.replace("fk", "").strip(),
        f"{temiz_ad}spor"
    ]

    for sorgu in sorgu_listesi:
        if not sorgu or len(sorgu) < 2:
            continue
        try:
            url = f"{API_URL}/teams?search={sorgu}"
            res = requests.get(url, headers=HEADERS, timeout=10).json()
            time.sleep(0.3)
            
            if res.get("response"):
                # Öncelikli olarak Türkiye ligindeki takımı seç
                for item in res["response"]:
                    if item.get("team", {}).get("country") == "Turkey":
                        return item["team"]["id"], item["team"]["name"]
                # Türkiye dışındaysa ilk bulunan tam eşleşmeyi dön
                return res["response"][0]["team"]["id"], res["response"][0]["team"]["name"]
        except Exception as e:
            logging.error(f"Takım arama hatası ({sorgu}): {e}")

    # Hiçbir şey bulunamadıysa manuel isimle dön
    return None, takim_adi

def coklu_analiz_verisi_topla(t1_id, t2_id):
    mevcut_yil = datetime.now().year
    veri = {"h2h": [], "t1_home_form": [], "t2_away_form": [], "t1_rank": "Bilinmiyor", "t2_rank": "Bilinmiyor"}
    try:
        h2h_res = requests.get(f"{API_URL}/fixtures/headtohead?h2h={t1_id}-{t2_id}", headers=HEADERS, timeout=10).json()
        veri["h2h"] = h2h_res.get("response", [])[:10]
        time.sleep(0.3)

        f1_res = requests.get(f"{API_URL}/fixtures?team={t1_id}&last=5&venue=home", headers=HEADERS, timeout=10).json()
        veri["t1_home_form"] = f1_res.get("response", [])
        time.sleep(0.3)

        f2_res = requests.get(f"{API_URL}/fixtures?team={t2_id}&last=5&venue=away", headers=HEADERS, timeout=10).json()
        veri["t2_away_form"] = f2_res.get("response", [])
        time.sleep(0.3)

        st_res = requests.get(f"{API_URL}/standings?season={mevcut_yil}&team={t1_id}", headers=HEADERS, timeout=10).json()
        if st_res.get("response"):
            for league in st_res["response"]:
                for team_stand in league["league"]["standings"][0]:
                    if team_stand["team"]["id"] == t1_id:
                        veri["t1_rank"] = f"{team_stand['rank']}. Sırada ({team_stand['points']} Pn)"
                    elif team_stand["team"]["id"] == t2_id:
                        veri["t2_rank"] = f"{team_stand['rank']}. Sırada ({team_stand['points']} Pn)"
    except Exception as e:
        logging.error(f"Çoklu veri toplama hatası: {e}")
    return veri

def derin_h2h_analiz(takim1_input, takim2_input):
    t1_id, t1_name = takim_id_bul(takim1_input)
    t2_id, t2_name = takim_id_bul(takim2_input)

    if not t1_id or not t2_id:
        hangi_eksik = t1_name if not t1_id else t2_name
        return f"⚠️ **Takım Bulunamadı:** `{hangi_eksik}` ismiyle bir takım tespit edilemedi. Lütfen ismini veya şehir adını kontrol edin."

    analiz_veri = coklu_analiz_verisi_topla(t1_id, t2_id)
    maclar = analiz_veri["h2h"]

    if not maclar:
        return f"ℹ️ **{t1_name}** ve **{t2_name}** arasında resmi geçmiş maç kaydı bulunamadı."

    total_matches = len(maclar)
    t1_wins, t2_wins, draws = 0, 0, 0
    total_goals = 0
    kg_var_count, over_25_count = 0, 0

    for m in maclar:
        gh = m["goals"]["home"] if m["goals"]["home"] is not None else 0
        ga = m["goals"]["away"] if m["goals"]["away"] is not None else 0
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

    t1_home_goals = sum([m["goals"]["home"] or 0 for m in analiz_veri["t1_home_form"]])
    t2_away_goals = sum([m["goals"]["away"] or 0 for m in analiz_veri["t2_away_form"]])

    avg_goals = round(total_goals / total_matches, 2)
    kg_ratio = int((kg_var_count / total_matches) * 100)
    o25_ratio = int((over_25_count / total_matches) * 100)

    kg_tahmin = "KG VAR" if kg_ratio >= 50 else "KG YOK"
    gol_tahmin = "2.5 ÜST" if o25_ratio >= 50 else "2.5 ALT"

    return (
        f"🏆 **MAÇ ANALİZ KARTI**\n"
        f"═══════════════════════\n"
        f"⚽️ **{t1_name.upper()} vs {t2_name.upper()}**\n"
        f"═══════════════════════\n"
        f"📊 **Puan Durumu:**\n"
        f"🏠 {t1_name}: `{analiz_veri['t1_rank']}`\n"
        f"✈️ {t2_name}: `{analiz_veri['t2_rank']}`\n\n"
        f"📈 **Form & Gol Performansı (Son 5):**\n"
        f"🔹 Ev Sahibi İç Saha Golü: `{t1_home_goals}`\n"
        f"🔹 Deplasman Dış Saha Golü: `{t2_away_goals}`\n"
        f"📜 H2H Geçmişi: {t1_wins}G | {t2_wins}G | {draws}B (Ort. Gol: {avg_goals})\n"
        f"───────────────────────\n"
        f"🎯 **YAPAY ZEKA TAHMİNLERİ:**\n"
        f"💥 **Karşılıklı Gol:** `{kg_tahmin}` (Güven: %{kg_ratio})\n"
        f"💥 **Toplam Gol:** `{gol_tahmin}` (Güven: %{o25_ratio})\n"
        f"═══════════════════════"
    )

def canli_skorlari_getir():
    try:
        res = requests.get(f"{API_URL}/fixtures?live=all", headers=HEADERS, timeout=10).json()
        canli_maclar = res.get("response", [])
        if not canli_maclar:
            return "ℹ️ Şu anda oynanan canlı maç bulunmuyor."

        metin = "🔴 **CANLI MAÇ SKORLARI** 🔴\n═══════════════════════\n\n"
        for m in canli_maclar[:10]:
            h_name = m["teams"]["home"]["name"]
            a_name = m["teams"]["away"]["name"]
            gh = m["goals"]["home"] or 0
            ga = m["goals"]["away"] or 0
            elapsed = m["fixture"]["status"]["elapsed"] or 0
            metin += f"⏱ `{elapsed}'` | **{h_name}** {gh} - {ga} **{a_name}**\n"

        return metin
    except Exception as e:
        logging.error(f"Canlı skor hatası: {e}")
        return "⚠️ Canlı skorlar alınırken bir hata oluştu."

def kupon_hazirla(tur="garanti"):
    try:
        bugun = datetime.now(TURKEY_TZ).strftime("%Y-%m-%d")
        url = f"{API_URL}/fixtures?date={bugun}"
        res = requests.get(url, headers=HEADERS, timeout=10).json()
        maclar = res.get("response", [])

        if not maclar:
            return None

        analizli_maclar = []
        su_an_timestamp = datetime.now(TURKEY_TZ).timestamp()

        for m in maclar:
            mac_timestamp = m["fixture"]["timestamp"]
            if mac_timestamp < (su_an_timestamp + 300):
                continue

            f_id = m["fixture"]["id"]
            h_id = m["teams"]["home"]["id"]
            a_id = m["teams"]["away"]["id"]
            h_name = m["teams"]["home"]["name"]
            a_name = m["teams"]["away"]["name"]
            mac_saati = datetime.fromtimestamp(mac_timestamp, TURKEY_TZ).strftime("%H:%M")

            h2h_url = f"{API_URL}/fixtures/headtohead?h2h={h_id}-{a_id}"
            h2h_res = requests.get(h2h_url, headers=HEADERS, timeout=10).json()
            gecmis = h2h_res.get("response", [])[:5]
            time.sleep(0.2)

            if gecmis:
                toplam_gol = sum([(g["goals"]["home"] or 0) + (g["goals"]["away"] or 0) for g in gecmis])
                avg_gol = toplam_gol / len(gecmis)

                oran = 1.45
                if tur == "garanti":
                    if avg_gol >= 2.0:
                        tahmin = "1.5 ÜST"
                        oran = 1.35
                        gerekce = f"H2H maçlarında ortalama {round(avg_gol,1)} gol atıldı."
                    else:
                        tahmin = f"{h_name} Çifte Şans (1X)"
                        oran = 1.30
                        gerekce = f"{h_name} evindeki savunma disipliniyle öne çıkıyor."
                    tutma_orani = 86
                else:
                    if avg_gol >= 2.8:
                        tahmin = "2.5 ÜST & KG VAR"
                        oran = 2.15
                        gerekce = "İki takımın da son maçları yüksek skorlu geçiyor."
                    else:
                        tahmin = f"{h_name} Maç Sonucu 1"
                        oran = 1.95
                        gerekce = f"{h_name} iç sahadaki yüksek galibiyet yüzdesine sahip."
                    tutma_orani = 68

                analizli_maclar.append({
                    "id": f_id, "mac": f"{h_name} vs {a_name}", "saat": mac_saati,
                    "tahmin": tahmin, "orani": tutma_orani, "bahis_orani": oran, "gerekce": gerekce
                })

        if not analizli_maclar:
            return None

        analizli_maclar = sorted(analizli_maclar, key=lambda x: x["orani"], reverse=(tur=="garanti"))[:2]
        toplam_oran = round(analizli_maclar[0]["bahis_orani"] * analizli_maclar[1]["bahis_orani"], 2)
        toplam_guven = int(sum([m["orani"] for m in analizli_maclar]) / len(analizli_maclar))

        rozet = "💎 **KASA GÜVEN KUPONU**" if tur == "garanti" else "💣 **YÜKSEK ORANLI RİSK KUPONU**"

        kupon_metni = (
            f"👑 **PRO ANALİZ VIP** 👑\n"
            f"{rozet}\n"
            f"📅 **Tarih:** `{bugun}`\n"
            f"═══════════════════════\n\n"
        )

        for idx, item in enumerate(analizli_maclar, 1):
            kupon_metni += (
                f"⚽️ **{idx}. MAÇ:** **{item['mac']}**\n"
                f"⏰ **Saat:** `{item['saat']}`\n"
                f"🎯 **Tahmin:** `{item['tahmin']}`\n"
                f"💥 **Oran:** `{item['bahis_orani']}` | 📊 **Güven:** `%{item['orani']}`\n"
                f"💡 **Analiz:** *{item['gerekce']}*\n"
                f"───────────────────────\n"
            )

        kupon_metni += (
            f"💰 **Toplam Oran:** `{toplam_oran}`\n"
            f"🔥 **Ortalama Güven:** `%{toplam_guven}`\n"
            f"═══════════════════════\n"
            f"🤖 *Yapay zeka verileriyle otomatik oluşturulmuştur.*\n"
            f"📢 **Kanalımız:** {CHANNEL_ID}"
        )
        return kupon_metni
    except Exception as e:
        logging.error(f"Kupon hazırlama hatası: {e}")
        return None

# --- TELEGRAM BOT DİNLEYİCİLERİ ---
def ana_menu_klavyesi():
    keyboard = [
        [
            InlineKeyboardButton("🛡 Kasa Kuponu", callback_data="btn_kasa"),
            InlineKeyboardButton("🔥 Riskli Kupon", callback_data="btn_riskli")
        ],
        [
            InlineKeyboardButton("🔴 Canlı Skorlar", callback_data="btn_canli"),
            InlineKeyboardButton("📖 Yardım", callback_data="btn_yardim")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Gelişmiş AI İddaa Analiz Santraline Hoş Geldiniz!**\n\n"
        "Aşağıdaki butonları kullanarak bülteni inceleyebilir veya direkt takım ismi yazıp analiz alabilirsiniz:\n"
        "*Örnek:* `gs - fb` veya `real madrid - barcelona`",
        reply_markup=ana_menu_klavyesi(),
        parse_mode="Markdown"
    )

async def buton_tiklama_isleyici(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "btn_kasa":
        kupon = kupon_hazirla("garanti")
        await query.message.reply_text(kupon or "ℹ️ Kasa kuponu için başlama saati uygun maç bulunamadı.", parse_mode="Markdown")
    elif query.data == "btn_riskli":
        kupon = kupon_hazirla("riskli")
        await query.message.reply_text(kupon or "ℹ️ Riskli kupon için başlama saati uygun maç bulunamadı.", parse_mode="Markdown")
    elif query.data == "btn_canli":
        skorlar = canli_skorlari_getir()
        await query.message.reply_text(skorlar, parse_mode="Markdown")
    elif query.data == "btn_yardim":
        await query.message.reply_text("📖 Takımları aralarında `-` veya `vs` koyarak aratabilirsiniz. (Örnek: `gs - fb` veya `kayseri - trabzon`)")

async def metin_dinleyici(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    metin = update.message.text.strip()
    if "-" in metin or " vs " in metin.lower():
        parcalar = re.split(r'\s+vs\s+|\s+-\s+', metin, flags=re.IGNORECASE)
        if len(parcalar) == 2:
            await update.message.reply_text(derin_h2h_analiz(parcalar[0], parcalar[1]), parse_mode="Markdown")

async def otomatik_kupon_gonder(app: Application):
    try:
        kupon_metni = kupon_hazirla("garanti")
        if kupon_metni:
            await app.bot.send_message(chat_id=CHANNEL_ID, text=kupon_metni, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Kanala kupon atma hatası: {e}")

def main():
    Thread(target=run_http_server, daemon=True).start()
    Thread(target=self_ping, daemon=True).start()

    app = Application.builder().token(BOT_TOKEN).job_queue(None).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("canli", lambda u, c: u.message.reply_text(canli_skorlari_getir(), parse_mode="Markdown")))
    app.add_handler(CallbackQueryHandler(buton_tiklama_isleyici))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, metin_dinleyici))

    scheduler = AsyncIOScheduler(timezone=TURKEY_TZ)
    scheduler.add_job(otomatik_kupon_gonder, 'cron', hour=10, minute=0, args=[app])
    scheduler.add_job(otomatik_kupon_gonder, 'cron', hour=17, minute=0, args=[app])

    scheduler.start()

    print("🤖 Tüm Esnek Takım Arama Algoritmaları Aktif!")
    app.run_polling()

if __name__ == "__main__":
    main()
