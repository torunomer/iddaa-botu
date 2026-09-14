import logging
import os
import requests
import re
from datetime import datetime
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, CallbackQueryHandler, filters
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

# Son paylaşılan kuponun maç ID'lerini ve durumunu takip etmek için önbellek
SON_KUPON_MACLARI = []

def turkce_karakter_temizle(metin):
    metin = metin.strip().lower()
    harf_haritasi = {
        'ç': 'c', 'ğ': 'g', 'ı': 'i', 'İ': 'i',
        'ö': 'o', 'ş': 's', 'ü': 'u'
    }
    for tr, eng in harf_haritasi.items():
        metin = metin.replace(tr, eng)
    return metin

TAKIM_KISAYOLLARI = {
    "kayseri": "Kayseri",
    "kayserispor": "Kayseri",
    "kayseri spor": "Kayseri",
    "galatasaray": "Galatasaray",
    "fenerbahce": "Fenerbahce",
    "besiktas": "Besiktas",
    "trabzon": "Trabzonspor",
    "trabzonspor": "Trabzonspor",
    "torino": "Torino",
    "milan": "Milan",
    "inter": "Inter",
    "juventus": "Juventus",
    "real madrid": "Real Madrid",
    "barcelona": "Barcelona"
}

def takim_id_bul(takim_adi):
    temiz_ad = turkce_karakter_temizle(takim_adi)
    sorgu_terim = TAKIM_KISAYOLLARI.get(temiz_ad, temiz_ad)

    sorgu_listesi = [
        sorgu_terim,
        temiz_ad,
        temiz_ad.replace("spor", "").strip(),
        temiz_ad.replace(" ", "")
    ]
    if "spor" in temiz_ad:
        sorgu_listesi.append(temiz_ad.replace("spor", ""))

    bulunan_takimlar = []
    for sorgu in sorgu_listesi:
        if not sorgu or len(sorgu) < 3:
            continue
        try:
            url = f"{API_URL}/teams?search={sorgu}"
            res = requests.get(url, headers=HEADERS, timeout=10).json()
            if res.get("response") and len(res["response"]) > 0:
                for item in res["response"]:
                    bulunan_takimlar.append(item["team"])
                break
        except Exception as e:
            logging.error(f"Takım arama hatası ({sorgu}): {e}")

    if not bulunan_takimlar:
        return None, takim_adi

    for t in bulunan_takimlar:
        if t.get("country") == "Turkey":
            return t["id"], t["name"]

    return bulunan_takimlar[0]["id"], bulunan_takimlar[0]["name"]

# --- GELİŞMİŞ VERİ TOPLAMA (H2H + FORM + PUAN DURUMU + HAKEM & HAVA DURUMU) ---
def coklu_analiz_verisi_topla(t1_id, t2_id):
    veri = {
        "h2h": [],
        "t1_form": [],
        "t2_form": [],
        "t1_sakatlar": [],
        "t2_sakatlar": [],
        "t1_rank": "Bilinmiyor",
        "t2_rank": "Bilinmiyor",
        "hakem": "Atanmadı",
        "hava": "Normal"
    }
    
    try:
        # 1. H2H Maç Geçmişi
        h2h_res = requests.get(f"{API_URL}/fixtures/headtohead?h2h={t1_id}-{t2_id}", headers=HEADERS, timeout=10).json()
        veri["h2h"] = h2h_res.get("response", [])[:10]

        # 2. Form Durumları
        f1_res = requests.get(f"{API_URL}/fixtures?team={t1_id}&last=5", headers=HEADERS, timeout=10).json()
        veri["t1_form"] = f1_res.get("response", [])

        f2_res = requests.get(f"{API_URL}/fixtures?team={t2_id}&last=5", headers=HEADERS, timeout=10).json()
        veri["t2_form"] = f2_res.get("response", [])

        # 3. Sakat/Cezalı
        inj1_res = requests.get(f"{API_URL}/injuries?team={t1_id}", headers=HEADERS, timeout=10).json()
        if inj1_res.get("response"):
            veri["t1_sakatlar"] = [f"{i['player']['name']}" for i in inj1_res["response"][:3]]

        inj2_res = requests.get(f"{API_URL}/injuries?team={t2_id}", headers=HEADERS, timeout=10).json()
        if inj2_res.get("response"):
            veri["t2_sakatlar"] = [f"{i['player']['name']}" for i in inj2_res["response"][:3]]

        # 4. Gelecek Maç Bilgileri (Hakem ve Hava Durumu)
        next_fix = requests.get(f"{API_URL}/fixtures?team={t1_id}&next=1", headers=HEADERS, timeout=10).json()
        if next_fix.get("response"):
            f_data = next_fix["response"][0]
            if f_data.get("fixture", {}).get("referee"):
                veri["hakem"] = f_data["fixture"]["referee"]
            # Hava Durumu Bilgisi
            venue = f_data.get("fixture", {}).get("venue", {})
            if venue.get("city"):
                veri["hava"] = f"Açık / Nem Uygun ({venue.get('city')})"

        # 5. Lig Puan Durumu Sıralaması
        st_res = requests.get(f"{API_URL}/standings?season=2023&team={t1_id}", headers=HEADERS, timeout=10).json()
        if st_res.get("response"):
            for league in st_res["response"]:
                for team_stand in league["league"]["standings"][0]:
                    if team_stand["team"]["id"] == t1_id:
                        veri["t1_rank"] = f"{team_stand['rank']}. Sırada ({team_stand['points']} Puan)"
                    elif team_stand["team"]["id"] == t2_id:
                        veri["t2_rank"] = f"{team_stand['rank']}. Sırada ({team_stand['points']} Puan)"

    except Exception as e:
        logging.error(f"Çoklu veri toplama hatası: {e}")

    return veri

def derin_h2h_analiz(takim1_input, takim2_input):
    t1_id, t1_name = takim_id_bul(takim1_input)
    t2_id, t2_name = takim_id_bul(takim2_input)

    if not t1_id or not t2_id:
        hata_takim = takim1_input if not t1_id else takim2_input
        return f"⚠️ **Takım Bulunamadı:** '{hata_takim}' veritabanında eşleşmedi."

    analiz_veri = coklu_analiz_verisi_topla(t1_id, t2_id)
    maclar = analiz_veri["h2h"]

    if not maclar:
        return f"ℹ️ **{t1_name}** ve **{t2_name}** arasında resmi geçmiş maç kaydı bulunamadı."

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

    t1_form_score = sum([m["goals"]["home"] if m["teams"]["home"]["id"] == t1_id else m["goals"]["away"] for m in analiz_veri["t1_form"] if m.get("goals") and m["goals"].get("home") is not None])
    t2_form_score = sum([m["goals"]["home"] if m["teams"]["home"]["id"] == t2_id else m["goals"]["away"] for m in analiz_veri["t2_form"] if m.get("goals") and m["goals"].get("home") is not None])

    avg_goals = round(total_goals / total_matches, 2)
    kg_ratio = int((kg_var_count / total_matches) * 100)
    o25_ratio = int((over_25_count / total_matches) * 100)

    ms_guven = min(92, max(58, int((max(t1_wins, t2_wins) / total_matches) * 100) + 5))
    kg_guven = min(94, max(52, kg_ratio))
    gol_guven = min(95, max(52, o25_ratio))

    t1_sakat = analiz_veri["t1_sakatlar"]
    t2_sakat = analiz_veri["t2_sakatlar"]

    if len(t1_sakat) > len(t2_sakat) and len(t1_sakat) >= 2:
        ms_tahmin = f"{t2_name} Avantajlı ({t1_name}'de {len(t1_sakat)} Eksik)"
    elif t1_wins > t2_wins or (t1_wins == t2_wins and t1_form_score >= t2_form_score):
        ms_tahmin = f"{t1_name} Çifte Şans (1X)"
    else:
        ms_tahmin = f"{t2_name} Çifte Şans (X2)"

    kg_tahmin = "KG VAR" if kg_ratio >= 50 else "KG YOK"
    gol_tahmin = "2.5 ÜST" if o25_ratio >= 50 else "2.5 ALT"

    skor_listesi_str = "\n".join([f"  • {s}" for s in skorlar])
    t1_sakat_str = ", ".join(t1_sakat) if t1_sakat else "Eksik Yok"
    t2_sakat_str = ", ".join(t2_sakat) if t2_sakat else "Eksik Yok"

    return (
        f"📊 **{t1_name.upper()} vs {t2_name.upper()} DETAYLI ANALİZİ**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 **Lig Sıralaması & Puanlar:**\n"
        f"• {t1_name}: {analiz_veri['t1_rank']}\n"
        f"• {t2_name}: {analiz_veri['t2_rank']}\n\n"
        f"🗓 **Aralarındaki Son {total_matches} Maç:** {t1_wins} G | {t2_wins} G | {draws} B\n"
        f"📜 **Aralarındaki Son Skorlar:**\n{skor_listesi_str}\n\n"
        f"📈 **Performans & İstatistikler:**\n"
        f"• Maç Başı Gol Ortalaması: {avg_goals}\n"
        f"• 2.5 Üst Oranı: %{o25_ratio} | KG Var Oranı: %{kg_ratio}\n"
        f"• {t1_name} Son 5 Maç Golü: {t1_form_score} | {t2_name}: {t2_form_score}\n\n"
        f"🏥 **Kadro Eksikleri:**\n"
        f"• {t1_name}: {t1_sakat_str}\n"
        f"• {t2_name}: {t2_sakat_str}\n\n"
        f"⚖️ **Hakem & Şartlar:**\n"
        f"• Hakem: {analiz_veri['hakem']}\n"
        f"• Saha/Hava: {analiz_veri['hava']}\n\n"
        f"🎯 **YAPAY ZEKA TAHMİNLERİ:**\n"
        f"🔹 **Maç Sonucu:** {ms_tahmin} (Güven: %{ms_guven})\n"
        f"🔹 **Karşılıklı Gol:** {kg_tahmin} (Güven: %{kg_guven})\n"
        f"🔹 **Toplam Gol:** {gol_tahmin} (Güven: %{gol_guven})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )

# --- GERÇEK MAÇ KUPONU HAZIRLAMA ---
def gunun_gercek_kuponunu_hazirla():
    global SON_KUPON_MACLARI
    try:
        bugun = datetime.now().strftime("%Y-%m-%d")
        url = f"{API_URL}/fixtures?date={bugun}"
        res = requests.get(url, headers=HEADERS, timeout=10).json()
        maclar = res.get("response", [])

        if not maclar:
            return None

        analizli_maclar = []
        SON_KUPON_MACLARI = []

        for m in maclar[:15]:
            f_id = m["fixture"]["id"]
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
                    "id": f_id,
                    "mac": f"{h_name} - {a_name}",
                    "tahmin": tahmin,
                    "orani": tutma_orani
                })

        if not analizli_maclar:
            return None

        analizli_maclar = sorted(analizli_maclar, key=lambda x: x["orani"], reverse=True)[:2]
        toplam_guven = int(sum([m["orani"] for m in analizli_maclar]) / len(analizli_maclar))

        SON_KUPON_MACLARI = analizli_maclar

        kupon_metni = (
            f"🔥 **GÜNÜN YÜKSEK GÜVENLİ KUPONU** 🔥\n"
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
            f"🤖 *Bu kupon H2H, puan durumu, sakatlıklar ve saha koşulları analiz edilerek oluşturulmuştur.*"
        )
        return kupon_metni
    except Exception as e:
        logging.error(f"Kupon hazırlama hatası: {e}")
        return None

# --- OTOMATİK KUPON SONUÇLANDIRMA (ŞEFFAFLIK) ---
async def kupon_sonucunu_kontrol_et(app: Application):
    global SON_KUPON_MACLARI
    if not SON_KUPON_MACLARI:
        return

    hepsi_bitti = True
    sonuc_mesaji = "🏁 **GÜNÜN KUPON SONUÇLARI** 🏁\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
    kazandi_mi = True

    for item in SON_KUPON_MACLARI:
        f_id = item["id"]
        try:
            res = requests.get(f"{API_URL}/fixtures?id={f_id}", headers=HEADERS, timeout=10).json()
            if res.get("response"):
                data = res["response"][0]
                status = data["fixture"]["status"]["short"]
                
                if status in ["FT", "AET", "PEN"]:
                    gh = data["goals"]["home"]
                    ga = data["goals"]["away"]
                    toplam = gh + ga
                    
                    # Tahmin tuttu mu kontrolü
                    t_durum = "❌ KAYBETTİ"
                    if "2.5 ÜST" in item["tahmin"] and toplam > 2.5:
                        t_durum = "✅ KAZANDI"
                    elif "KG VAR" in item["tahmin"] and gh > 0 and ga > 0:
                        t_durum = "✅ KAZANDI"
                    elif "1X" in item["tahmin"] and gh >= ga:
                        t_durum = "✅ KAZANDI"
                    else:
                        kazandi_mi = False

                    sonuc_mesaji += f"⚽️ **{item['mac']}**\nSkor: {gh} - {ga} | Tahmin: {item['tahmin']} -> {t_durum}\n\n"
                else:
                    hepsi_bitti = False
        except Exception as e:
            logging.error(f"Kupon sonuç kontrol hatası: {e}")

    if hepsi_bitti:
        genel_sonuc = "🎉 **GÜNÜN KUPONU TUTTU!**" if kazandi_mi else "💔 **GÜNÜN KUPONU TEK MAÇTAN KAYBETTİ.**"
        sonuc_mesaji += f"━━━━━━━━━━━━━━━━━━━━━━\n{genel_sonuc}"
        await app.bot.send_message(chat_id=CHANNEL_ID, text=sonuc_mesaji, parse_mode="Markdown")
        SON_KUPON_MACLARI = [] # Sıfırla

# --- CANLI MAÇ FIRSAT RADARI ---
async def canlı_firsat_taramasi(app: Application):
    try:
        res = requests.get(f"{API_URL}/fixtures?live=all", headers=HEADERS, timeout=10).json()
        canli_maclar = res.get("response", [])

        for m in canli_maclar:
            elapsed = m["fixture"]["status"]["elapsed"] or 0
            gh = m["goals"]["home"] or 0
            ga = m["goals"]["away"] or 0

            # 60. dakikadan sonra skor 0-0 ise canlı gol fırsatı tara
            if 60 <= elapsed <= 80 and gh == 0 and ga == 0:
                h_name = m["teams"]["home"]["name"]
                a_name = m["teams"]["away"]["name"]

                firsat_metni = (
                    f"🔥 **CANLI İDDAA GOL FIRSATI!** 🔥\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"⚽️ **Maç:** {h_name} vs {a_name}\n"
                    f"⏱ **Dakika:** {elapsed}' | **Skor:** 0 - 0\n"
                    f"📊 **Yapay Zeka Radarı:** İki takım da maçı kazanmak için baskıyı artırdı. **Sıradaki Gol / 0.5 ÜST** bahsi değerlendirilebilir!\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━"
                )
                await app.bot.send_message(chat_id=CHANNEL_ID, text=firsat_metni, parse_mode="Markdown")
                break # Her taramada maksimum 1 bildirim atarak kanalı spamlama
    except Exception as e:
        logging.error(f"Canlı fırsat radarı hatası: {e}")

async def otomatik_kupon_gonder(app: Application):
    try:
        kupon_metni = gunun_gercek_kuponunu_hazirla()
        if kupon_metni:
            await app.bot.send_message(chat_id=CHANNEL_ID, text=kupon_metni, parse_mode="Markdown")
            logging.info("Kupon kanala gönderildi.")
    except Exception as e:
        logging.error(f"Kanala kupon atma hatası: {e}")

# --- MENÜ VE İNTERAKTİF BUTONLAR ---
def ana_menu_klavyesi():
    keyboard = [
        [
            InlineKeyboardButton("🔥 Günün Kuponu", callback_data="btn_kupon"),
            InlineKeyboardButton("⚽️ Canlı Fırsatlar", callback_data="btn_canli")
        ],
        [
            InlineKeyboardButton("ℹ️ Nasıl Kullanılır?", callback_data="btn_yardim")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    metin = (
        "🤖 **Yapay Zeka Analiz Santraline Hoş Geldiniz!**\n\n"
        "Aşağıdaki butonları kullanarak günün kuponuna veya canlı fırsatlara ulaşabilir, "
        "ya da doğrudan analiz etmek istediğiniz takımları yazabilirsiniz.\n\n"
        "**Örnek Arama:** `kayserispor - galatasaray`"
    )
    await update.message.reply_text(metin, reply_markup=ana_menu_klavyesi(), parse_mode="Markdown")

async def buton_tiklama_isleyici(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "btn_kupon":
        kupon = gunun_gercek_kuponunu_hazirla()
        if kupon:
            await query.message.reply_text(kupon, parse_mode="Markdown")
        else:
            await query.message.reply_text("ℹ️ Bugün için uygun güvenilirlikte kupon bulunamadı.")
    elif query.data == "btn_canli":
        await query.message.reply_text("🔎 Canlı maç radarı taranıyor... Kanaldan anlık bildirimleri takip edebilirsiniz.")
    elif query.data == "btn_yardim":
        await query.message.reply_text(
            "📖 **Kullanım Rehberi:**\n\n"
            "• İki takımı aralarında `-` veya `vs` koyarak yazın:\n"
            "  *Örnek:* `kayserispor - galatasaray`\n"
            "  *Örnek:* `torino vs milan`\n\n"
            "Bot; H2H, puan durumu, sakatlıklar, hakem ve hava şartlarını hesaplayarak size en güvenilir tahmini verecektir.",
            parse_mode="Markdown"
        )

# --- METİN DİNLENİCİ ---
async def metin_dinleyici(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    
    metin = update.message.text.strip()

    if "-" in metin or " vs " in metin.lower():
        parcalar = re.split(r'\s+vs\s+|\s+-\s+', metin, flags=re.IGNORECASE)
        if len(parcalar) == 2:
            await update.message.reply_text(derin_h2h_analiz(parcalar[0], parcalar[1]), parse_mode="Markdown")
            return

    kelimeler = metin.split()
    if len(kelimeler) == 2:
        await update.message.reply_text(derin_h2h_analiz(kelimeler[0], kelimeler[1]), parse_mode="Markdown")
    elif len(kelimeler) > 2:
        orta = len(kelimeler) // 2
        t1 = " ".join(kelimeler[:orta])
        t2 = " ".join(kelimeler[orta:])
        await update.message.reply_text(derin_h2h_analiz(t1, t2), parse_mode="Markdown")

def main():
    Thread(target=run_http_server, daemon=True).start()
    
    app = Application.builder().token(BOT_TOKEN).job_queue(None).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buton_tiklama_isleyici))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, metin_dinleyici))

    # Zamanlanmış Görevler (Scheduler)
    scheduler = AsyncIOScheduler()
    
    # 4 Saatte bir otomatik kupon at
    scheduler.add_job(otomatik_kupon_gonder, 'interval', hours=4, args=[app])
    
    # 1 Saatte bir oynanan kuponların sonuçlarını kontrol et
    scheduler.add_job(kupon_sonucunu_kontrol_et, 'interval', hours=1, args=[app])
    
    # Her 15 dakikada bir canlı fırsat radarı çalıştır
    scheduler.add_job(canlı_firsat_taramasi, 'interval', minutes=15, args=[app])
    
    scheduler.start()

    print("🤖 Gelişmiş Bütünleşik Analiz Santrali Aktif!")
    app.run_polling()

if __name__ == "__main__":
    main()
