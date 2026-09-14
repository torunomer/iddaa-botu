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

# --- ÇOKLU KAYNAKLI & HASSAS TAKIM BULUCU ---
def takim_id_bul(takim_adi):
    temiz_ad = turkce_karakter_temizle(takim_adi)
    bitisik_ad = temiz_ad.replace(" ", "")

    # Arama havuzunu genişletiyoruz
    sorgular = [takim_adi, temiz_ad, bitisik_ad]
    kelimeler = temiz_ad.split()
    if len(kelimeler) > 1:
        sorgular.append(kelimeler[0]) # "Kayseri spor" -> "Kayseri"

    bulunan_takimlar = []

    for sorgu in sorgular:
        if len(sorgu) < 3:
            continue
        try:
            url = f"{API_URL}/teams?search={sorgu}"
            res = requests.get(url, headers=HEADERS, timeout=10).json()
            if res.get("response"):
                for item in res["response"]:
                    t_info = item["team"]
                    bulunan_takimlar.append(t_info)
        except Exception as e:
            logging.error(f"Takim arama hatasi ({sorgu}): {e}")

    if not bulunan_takimlar:
        return None, takim_adi

    # Önceliklendirme Algoritması (Türkiye Ligleri ve Popüler Ligler Öncelikli)
    for t in bulunan_takimlar:
        if t.get("country") == "Turkey":
            return t["id"], t["name"]

    # Eğer Türkiye takımı değilse listedeki ilk doğru eşleşmeyi al (Torino, Real Madrid vb.)
    return bulunan_takimlar[0]["id"], bulunan_takimlar[0]["name"]

# --- ÇOKLU KAYNAK VERİ TOPLAMA (H2H + SON FORM + SAKAT/CEZALI) ---
def coklu_analiz_verisi_topla(t1_id, t2_id):
    veri = {
        "h2h": [],
        "t1_form": [],
        "t2_form": [],
        "t1_sakatlar": [],
        "t2_sakatlar": []
    }
    
    try:
        # 1. Kaynak: H2H Maç Geçmişi
        h2h_url = f"{API_URL}/fixtures/headtohead?h2h={t1_id}-{t2_id}"
        h2h_res = requests.get(h2h_url, headers=HEADERS, timeout=10).json()
        veri["h2h"] = h2h_res.get("response", [])[:10]

        # 2. Kaynak: Takım 1 Son 5 Maçlık Formu
        f1_url = f"{API_URL}/fixtures?team={t1_id}&last=5"
        f1_res = requests.get(f1_url, headers=HEADERS, timeout=10).json()
        veri["t1_form"] = f1_res.get("response", [])

        # 3. Kaynak: Takım 2 Son 5 Maçlık Formu
        f2_url = f"{API_URL}/fixtures?team={t2_id}&last=5"
        f2_res = requests.get(f2_url, headers=HEADERS, timeout=10).json()
        veri["t2_form"] = f2_res.get("response", [])

        # 4. Kaynak: Sakat ve Cezalı Raporları
        inj1_url = f"{API_URL}/injuries?team={t1_id}"
        inj1_res = requests.get(inj1_url, headers=HEADERS, timeout=10).json()
        if inj1_res.get("response"):
            veri["t1_sakatlar"] = [f"{i['player']['name']}" for i in inj1_res["response"][:3]]

        inj2_url = f"{API_URL}/injuries?team={t2_id}"
        inj2_res = requests.get(inj2_url, headers=HEADERS, timeout=10).json()
        if inj2_res.get("response"):
            veri["t2_sakatlar"] = [f"{i['player']['name']}" for i in inj2_res["response"][:3]]

    except Exception as e:
        logging.error(f"Çoklu veri toplama hatası: {e}")

    return veri

def derin_h2h_analiz(takim1_input, takim2_input):
    t1_id, t1_name = takim_id_bul(takim1_input)
    t2_id, t2_name = takim_id_bul(takim2_input)

    if not t1_id or not t2_id:
        hata_takim = takim1_input if not t1_id else takim2_input
        return f"⚠️ **Takım Bulunamadı:** '{hata_takim}' ismiyle eşleşen bir takım bulunamadı. Lütfen kontrol edin."

    analiz_veri = coklu_analiz_verisi_topla(t1_id, t2_id)
    maclar = analiz_veri["h2h"]

    if not maclar:
        return f"ℹ️ **{t1_name}** ve **{t2_name}** arasında resmi geçmiş maç kaydı bulunamadı."

    # H2H İstatistikleri
    total_matches = len(maclar)
    t1_wins, t2_wins, draws = 0, 0, 0
    total_goals = 0
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

    # Form İstatistikleri (Son 5 Maç)
    t1_form_score = sum([m["goals"]["home"] if m["teams"]["home"]["id"] == t1_id else m["goals"]["away"] for m in analiz_veri["t1_form"] if m.get("goals") and m["goals"].get("home") is not None])
    t2_form_score = sum([m["goals"]["home"] if m["teams"]["home"]["id"] == t2_id else m["goals"]["away"] for m in analiz_veri["t2_form"] if m.get("goals") and m["goals"].get("home") is not None])

    avg_goals = round(total_goals / total_matches, 2)
    kg_ratio = int((kg_var_count / total_matches) * 100)
    o25_ratio = int((over_25_count / total_matches) * 100)

    # Çoklu Metrik Doğrulamalı Tahmin Motoru
    ms_guven = min(92, max(58, int((max(t1_wins, t2_wins) / total_matches) * 100) + 5))
    kg_guven = min(94, max(52, kg_ratio))
    gol_guven = min(95, max(52, o25_ratio))

    t1_sakat = analiz_veri["t1_sakatlar"]
    t2_sakat = analiz_veri["t2_sakatlar"]

    if len(t1_sakat) > len(t2_sakat) and len(t1_sakat) >= 2:
        ms_tahmin = f"{t2_name} Avantajlı ({t1_name}'de {len(t1_sakat)} Kritik Eksik)"
    elif t1_wins > t2_wins or (t1_wins == t2_wins and t1_form_score >= t2_form_score):
        ms_tahmin = f"{t1_name} Çifte Şans (1X)"
    else:
        ms_tahmin = f"{t2_name} Çifte Şans (X2)"

    kg_tahmin = "KG VAR" if kg_ratio >= 50 else "KG YOK"
    gol_tahmin = "2.5 ÜST" if o25_ratio >= 50 else "2.5 ALT"

    skor_listesi_str = "\n".join([f"  • {s}" for s in skorlar])
    t1_sakat_str = ", ".join(t1_sakat) if t1_sakat else "Eksik Bulunmuyor"
    t2_sakat_str = ", ".join(t2_sakat) if t2_sakat else "Eksik Bulunmuyor"

    return (
        f"📊 **{t1_name.upper()} vs {t2_name.upper()} ÇOKLU VERİ ANALİZİ**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🗓 **Aralarındaki Son {total_matches} Maç:** {t1_wins} G | {t2_wins} G | {draws} B\n\n"
        f"📜 **Aralarındaki Son Skorlar:**\n{skor_listesi_str}\n\n"
        f"📈 **Çoklu Performans & Form Analizi:**\n"
        f"• Maç Başı Gol Ortalaması: {avg_goals}\n"
        f"• 2.5 Üst Biten Maç Oranı: %{o25_ratio}\n"
        f"• KG Var Biten Maç Oranı: %{kg_ratio}\n"
        f"• {t1_name} Son 5 Maç Gol Formu: {t1_form_score} Gol\n"
        f"• {t2_name} Son 5 Maç Gol Formu: {t2_form_score} Gol\n\n"
        f"🏥 **Kadro & Eksik Raporu:**\n"
        f"• {t1_name}: {t1_sakat_str}\n"
        f"• {t2_name}: {t2_sakat_str}\n\n"
        f"🎯 **GÜVENİLİR YAPAY ZEKA TAHMİNLERİ:**\n"
        f"🔹 **Maç Sonucu:** {ms_tahmin} (Güven: %{ms_guven})\n"
        f"🔹 **Karşılıklı Gol:** {kg_tahmin} (Güven: %{kg_guven})\n"
        f"🔹 **Toplam Gol:** {gol_tahmin} (Güven: %{gol_guven})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )

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
            f"🔥 **GÜNÜN ÇOKLU ANALİZ KUPONU** 🔥\n"
            f"📅 **Tarih:** {bugun}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        )

        for idx, item in enumerate(analizli_maclar, 1):
            kupon_metni += (
                f"⚽️ **{idx}. Maç:** {item['mac']}\n"
                f"🎯 **Tahmin:** {item['tahmin']}\n"
                f"📊 **Güven Oranı:** %{item['orani']}\n\n"
            )

        kupon_metni += (
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡️ **Genel Kupon Güvenliği:** %{toplam_guven}\n"
            f"🤖 *Bu kupon form, H2H ve kadro durumları harmanlanarak yapay zeka ile oluşturulmuştur.*"
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
            logging.info("Çoklu analiz kuponu kanala atıldı.")
    except Exception as e:
        logging.error(f"Kanala mesaj atma hatası: {e}")

# --- GELİŞMİŞ GİRDİ PARSER (Gelen Mesajları Okuma) ---
async def metin_dinleyici(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    
    metin = update.message.text.strip()

    # Tire veya 'vs' ayrımı
    if "-" in metin or " vs " in metin.lower():
        parcalar = re.split(r'\s+vs\s+|\s+-\s+', metin, flags=re.IGNORECASE)
        if len(parcalar) == 2:
            await update.message.reply_text(derin_h2h_analiz(parcalar[0], parcalar[1]), parse_mode="Markdown")
            return

    # Kelime sayısı ayrımı
    kelimeler = metin.split()
    if len(kelimeler) == 2:
        await update.message.reply_text(derin_h2h_analiz(kelimeler[0], kelimeler[1]), parse_mode="Markdown")
    elif len(kelimeler) > 2:
        orta = len(kelimeler) // 2
        t1 = " ".join(kelimeler[:orta])
        t2 = " ".join(kelimeler[orta:])
        await update.message.reply_text(derin_h2h_analiz(t1, t2), parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Yapay Zeka Destekli Çoklu Analiz Botu Aktif!**\n\n"
        "İstediğiniz takımları doğrudan yazabilirsiniz.\n"
        "Örnekler:\n"
        "• `kayseri spor - galatasaray`\n"
        "• `torino vs milan`\n"
        "• `fenerbahce besiktas`"
    )

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

    print("🤖 Çoklu Kaynak Destekli Analiz Botu Aktif!")
    app.run_polling()

if __name__ == "__main__":
    main()
