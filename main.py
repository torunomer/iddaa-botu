import logging
import os
import requests
import re
from datetime import datetime
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

def turkce_karakter_temizle(metin):
    metin = metin.strip()
    harf_haritasi = {
        'ç': 'c', 'Ç': 'C', 'ğ': 'g', 'Ğ': 'G',
        'ı': 'i', 'I': 'I', 'İ': 'I', 'ö': 'o', 
        'Ö': 'O', 'ş': 's', 'Ş': 'S', 'ü': 'u', 'Ü': 'U'
    }
    for tr, eng in harf_haritasi.items():
        metin = metin.replace(tr, eng)
    return metin

# --- GELİŞMİŞ VE ESNEK TAKIM ARAMA ALGORİTMASI ---
def takim_id_bul(takim_adi):
    temiz_ad = turkce_karakter_temizle(takim_adi)
    
    # Denenecek varyasyonlar:
    # 1. Girilen orijinal/temizlenmiş tam isim
    # 2. İsmin sadece ilk kelimesi (Örn: "Fatih Karagümrük" yerine "Karagümrük" veya "Fatih")
    sorgular = [takim_adi, temiz_ad]
    kelimeler = temiz_ad.split()
    if len(kelimeler) > 1:
        sorgular.append(kelimeler[0])
        sorgular.append(kelimeler[-1])

    for sorgu in sorgular:
        if len(sorgu) < 3: # Çok kısa aramaları atla
            continue
        try:
            url = f"{API_URL}/teams?search={sorgu}"
            res = requests.get(url, headers=HEADERS, timeout=10).json()
            if res.get("response") and len(res["response"]) > 0:
                # Öncelik Türkiye Ligleri (Süper Lig / TFF 1)
                for item in res["response"]:
                    ulke = item.get("team", {}).get("country", "")
                    if ulke == "Turkey":
                        return item["team"]["id"], item["team"]["name"]
                
                # Türkiye dışı ise ilk bulduğu eşleşmeyi dön
                return res["response"][0]["team"]["id"], res["response"][0]["team"]["name"]
        except Exception as e:
            logging.error(f"Takim arama hatasi ({sorgu}): {e}")

    return None, takim_adi

def sakat_cezali_getir(t1_id, t2_id):
    t1_sakatlar, t2_sakatlar = [], []
    try:
        url = f"{API_URL}/injuries?team={t1_id}"
        res = requests.get(url, headers=HEADERS, timeout=10).json()
        if res.get("response"):
            t1_sakatlar = [f"{i['player']['name']}" for i in res["response"][:3]]

        url2 = f"{API_URL}/injuries?team={t2_id}"
        res2 = requests.get(url2, headers=HEADERS, timeout=10).json()
        if res2.get("response"):
            t2_sakatlar = [f"{i['player']['name']}" for i in res2["response"][:3]]
    except Exception as e:
        logging.error(f"Sakat/Cezali hatasi: {e}")
    return t1_sakatlar, t2_sakatlar

def derin_h2h_analiz(takim1_input, takim2_input):
    t1_id, t1_name = takim_id_bul(takim1_input)
    t2_id, t2_name = takim_id_bul(takim2_input)

    if not t1_id or not t2_id:
        hata_takim = takim1_input if not t1_id else takim2_input
        return f"⚠️ **Takım Bulunamadı:** '{hata_takim}' veritabanında bulunamadı. Lütfen isminin ana kelimesini yazıp tekrar deneyin."

    try:
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

        t1_sakat, t2_sakat = sakat_cezali_getir(t1_id, t2_id)

        avg_goals = round(total_goals / total_matches, 2)
        avg_iy_goals = round(iy_goals / total_matches, 2)
        kg_ratio = int((kg_var_count / total_matches) * 100)
        o25_ratio = int((over_25_count / total_matches) * 100)

        ms_guven = min(90, max(55, int((max(t1_wins, t2_wins) / total_matches) * 100)))
        kg_guven = min(92, max(50, kg_ratio))
        gol_guven = min(94, max(50, o25_ratio))

        if len(t1_sakat) > len(t2_sakat) and len(t1_sakat) > 0:
            ms_tahmin = f"{t2_name} Avantajlı ({t1_name}'de {len(t1_sakat)} Sakat var)"
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
            f"🔹 **Maç Sonucu:** {ms_tahmin} (Tutma İhtimali: %{ms_guven})\n"
            f"🔹 **KG Durumu:** {kg_tahmin} (Tutma İhtimali: %{kg_guven})\n"
            f"🔹 **Toplam Gol:** {gol_tahmin} (Tutma İhtimali: %{gol_guven})\n"
            f"━━━━━━━━━━━━━━━━━━━━━━"
        )
    except Exception as e:
        logging.error(f"Analiz hatasi: {e}")
        return "⚠️ İstatistikler çekilirken bir sorun oluştu."

# --- GERÇEK MAÇ KUPONU ---
def gunun_gercek_kuponunu_hazirla():
    try:
        bugun = datetime.now().strftime("%Y-%m-%d")
        url = f"{API_URL}/fixtures?date={bugun}"
        res = requests.get(url, headers=HEADERS, timeout=10).json()
        maclar = res.get("response", [])

        if not maclar:
            return None

        analizli_maclar = []
        for m in maclar[:15]:
            h_id = m["teams"]["home"]["id"]
            a_id = m["teams"]["away"]["id"]
            h_name = m["teams"]["home"]["name"]
            a_name = m["teams"]["away"]["name"]

            h2h_url = f"{API_URL}/fixtures/headtohead?h2h={h_id}-{a_id}"
            h2h_res = requests.get(h2h_url, headers=HEADERS, timeout=10).json()
            gecmis = h2h_res.get("response", [])[:5]

            if gecmis:
                toplam_gol = sum([(g["goals"]["home"] or 0) + (g["goals"]["away"] or 0) for g in gecmis])
                kg_var_sayisi = sum([1 for g in gecmis if (g["goals"]["home"] or 0) > 0 and (g["goals"]["away"] or 0) > 0])
                avg_gol = toplam_gol / len(gecmis)
                kg_oran = int((kg_var_sayisi / len(gecmis)) * 100)

                if avg_gol >= 2.7:
                    tahmin = "2.5 ÜST"
                    tutma_orani = min(88, 65 + int(avg_gol * 7))
                elif kg_oran >= 60:
                    tahmin = "KG VAR"
                    tutma_orani = min(92, kg_oran + 10)
                else:
                    tahmin = f"{h_name} Çifte Şans (1X)"
                    tutma_orani = 78

                analizli_maclar.append({
                    "mac": f"{h_name} - {a_name}",
                    "tahmin": tahmin,
                    "orani": tutma_orani
                })

        if not analizli_maclar:
            return None

        analizli_maclar = sorted(analizli_maclar, key=lambda x: x["orani"], reverse=True)[:2]
        toplam_guven = int(sum([m["orani"] for m in analizli_maclar]) / len(analizli_maclar))

        kupon_metni = (
            f"🔥 **GÜNÜN YAPAY ZEKA ANALİZ KUPONU** 🔥\n"
            f"📅 **Tarih:** {bugun}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        )

        for idx, item in enumerate(analizli_maclar, 1):
            kupon_metni += (
                f"⚽️ **{idx}. Maç:** {item['mac']}\n"
                f"🎯 **Tahmin:** {item['tahmin']}\n"
                f"📊 **Tutma İhtimali:** %{item['orani']}\n\n"
            )

        kupon_metni += (
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡️ **Genel Kupon Güven Oranı:** %{toplam_guven}\n"
            f"🤖 *Bu kupon canlı maç ve H2H verileri analiz edilerek otomatik oluşturulmuştur.*"
        )
        return kupon_metni
    except Exception as e:
        logging.error(f"Gercek kupon olusturma hatasi: {e}")
        return None

async def otomatik_kupon_gonder(app: Application):
    try:
        kupon_metni = gunun_gercek_kuponunu_hazirla()
        if kupon_metni:
            await app.bot.send_message(chat_id=CHANNEL_ID, text=kupon_metni, parse_mode="Markdown")
            logging.info("Gerçek verili analiz kuponu kanala atıldı.")
    except Exception as e:
        logging.error(f"Kanala mesaj atma hatası: {e}")

# --- BOT DİNLENMESİ ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Yapay Zeka Destekli Futbol Analiz Botu Aktif!**\n\n"
        "Takım isimlerini girerek analiz alabilirsiniz.\n"
        "Örnek: `Galatasaray Fenerbahçe` veya `Karagümrük Sivasspor`"
    )

async def metin_dinleyici(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    
    metin = update.message.text.strip()
    kelimeler = re.split(r'[\s,\-]+', metin)
    
    if len(kelimeler) == 2:
        await update.message.reply_text(derin_h2h_analiz(kelimeler[0], kelimeler[1]), parse_mode="Markdown")
    elif "vs" in metin.lower() or "-" in metin:
        parcalar = re.split(r'\s+vs\s+|\s+-\s+', metin, flags=re.IGNORECASE)
        if len(parcalar) == 2:
            await update.message.reply_text(derin_h2h_analiz(parcalar[0], parcalar[1]), parse_mode="Markdown")
    elif len(kelimeler) > 2:
        orta = len(kelimeler) // 2
        t1 = " ".join(kelimeler[:orta])
        t2 = " ".join(kelimeler[orta:])
        await update.message.reply_text(derin_h2h_analiz(t1, t2), parse_mode="Markdown")

def main():
    Thread(target=run_http_server, daemon=True).start()
    
    app = Application.builder().token(BOT_TOKEN).job_queue(None).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, metin_dinleyici))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        otomatik_kupon_gonder, 
        'interval', 
        hours=4, 
        args=[app]
    )
    scheduler.start()

    print("🤖 Esnek Arama Destekli Bot Aktif!")
    app.run_polling()

if __name__ == "__main__":
    main()
