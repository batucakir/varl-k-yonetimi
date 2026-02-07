import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime
import re
import random
import os
import sys

# --- AYARLAR ---
SHEET_NAME = "PortfoyVerileri"
MY_FUNDS = ["TLY", "DFI", "TP2"]

# CLOUD İÇİN ÖZEL: Zamanlayıcıyı GitHub Actions (YAML) dosyası yöneteceği için
# Kodun içindeki saat kontrolünü kaldırıp "Her çalıştığında güncelle" moduna alıyoruz.
# Böylece veri kaçırma şansı olmuyor.

# --- SENİN BELİRLEDİĞİN SABİT SÜTUN SIRASI ---
SUTUN_SIRASI = [
    "Tarih", 
    "DOLAR KURU",
    "GRAM ALTIN ALIŞ", "GRAM ALTIN SATIŞ",
    "22 AYAR ALTIN ALIŞ", "22 AYAR ALTIN SATIŞ",
    "ATA ALTIN ALIŞ", "ATA ALTIN SATIŞ",
    "ÇEYREK ALTIN ALIŞ", "ÇEYREK ALTIN SATIŞ",
    "ALTIN ONS ALIŞ", "ALTIN ONS SATIŞ",
    "TLY FİYAT", "DFI FİYAT", "TP2 FİYAT"
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15"
]

def connect_to_sheet():
    """Bağlantı koparsa 3 kere tekrar dener"""
    retries = 3
    for i in range(retries):
        try:
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            # GitHub Actions, secret'tan credentials.json dosyasını oluşturmuş olacak
            if os.path.exists('credentials.json'):
                creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
                client = gspread.authorize(creds)
                sheet = client.open(SHEET_NAME).sheet1
                return sheet
            else:
                print("🔥 HATA: credentials.json bulunamadı!")
                return None
        except Exception as e:
            print(f"⚠️ Bağlantı denemesi {i+1} başarısız: {e}")
            time.sleep(5)
    return None

def clean_currency(value_str):
    if not value_str: return 0.0
    value_str = str(value_str).strip()
    match = re.search(r'([\d\.,]+)', value_str)
    if not match: return 0.0
    clean_str = match.group(1)
    
    # 1.234,56 -> 1234.56 (Format temizleme)
    if ',' in clean_str and '.' not in clean_str:
        return float(clean_str.replace(',', '.'))
    if ',' in clean_str and '.' in clean_str:
        if clean_str.rfind(',') > clean_str.rfind('.'): 
             return float(clean_str.replace('.', '').replace(',', '.'))
        else:
             return float(clean_str.replace(',', ''))
    return float(clean_str)

def get_last_known_from_sheet(sheet):
    """
    HAFIZA FONKSİYONU:
    Bot her açıldığında Google Sheets'teki son satırı okur.
    Böylece bot kapalıyken kaydedilmiş son fon fiyatlarını hafızaya alır.
    Sıfır yazma riskini ortadan kaldırır.
    """
    print("☁️ Google Sheets hafızası taranıyor...")
    memory = {}
    try:
        all_values = sheet.get_all_values()
        if len(all_values) < 2: return {} 
        
        last_row = all_values[-1]
        headers = all_values[0]
        
        for i, h in enumerate(headers):
            if "FİYAT" in h and i < len(last_row):
                try:
                    # Sheet'ten gelen veriyi sayıya çevir
                    val_str = str(last_row[i]).replace(",", ".")
                    val = float(val_str)
                    if val > 0:
                        memory[h] = val
                        print(f"   💡 Hafızada: {h} = {val}")
                except: pass
    except Exception as e:
        print(f"⚠️ Hafıza okuma hatası: {e}")
    return memory

def fetch_usd():
    try:
        url = "https://api.exchangerate-api.com/v4/latest/USD"
        resp = requests.get(url, timeout=5).json()
        return float(resp['rates']['TRY'])
    except: return 0.0

def fetch_gold():
    url = f"https://anlikaltinfiyatlari.com/altin/kapalicarsi?v={random.random()}"
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    data = {}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.content, "html.parser")
        targets = {"GRAM ALTIN": ["GRAM ALTIN"], "22 AYAR ALTIN": ["22 AYAR"], 
                   "ATA ALTIN": ["ATA ALTIN"], "ÇEYREK ALTIN": ["ÇEYREK ALTIN"], "ALTIN ONS": ["ONS"]}
        for row in soup.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) > 2:
                name = cols[0].get_text(strip=True).upper()
                for key, keywords in targets.items():
                    if any(k in name for k in keywords):
                        data[f"{key} ALIŞ"] = clean_currency(cols[1].get_text())
                        data[f"{key} SATIŞ"] = clean_currency(cols[2].get_text())
        return data
    except Exception as e:
        print(f"⚠️ Altın Hatası: {e}")
        return {}

def fetch_fund_single(code):
    url = f"https://www.tefas.gov.tr/FonAnaliz.aspx?FonKod={code}"
    headers = {"User-Agent": random.choice(USER_AGENTS), "Referer": "https://www.tefas.gov.tr/"}
    try:
        # Cloud'da çalıştığı için biraz daha hızlı olabilir, sleep'i kıstık ama kaldırmadık
        time.sleep(1) 
        resp = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.content, "html.parser")
        
        top_list = soup.find("ul", class_="top-list")
        if top_list:
            for li in top_list.find_all("li"):
                if "Son Fiyat" in li.get_text():
                    return clean_currency(li.get_text().split("Fiyat")[-1])
        
        item = soup.select_one(".top-list > li:nth-of-type(1) span:last-child")
        if item: return clean_currency(item.get_text())
        return 0.0
    except: return 0.0

def update_all_funds(current_cache):
    """
    Fonları günceller. 
    Eğer TEFAS'tan veri çekemezse (0 gelirse), HAFIZADAKİ ESKİ DEĞERİ KORUR.
    Cloud Modu: Her çalıştığında güncellemeyi dener.
    """
    print("\n🌍 FON GÜNCELLEMESİ BAŞLADI (Cloud)...")
    new_cache = current_cache.copy()
    
    for code in MY_FUNDS:
        key = f"{code} FİYAT"
        # Her çalıştığında deniyoruz ki veri hep taze olsun
        print(f"   ⏳ {code} aranıyor...", end=" ")
        price = fetch_fund_single(code)
        
        if price > 0:
            new_cache[key] = price
            print(f"✅ {price}")
        else:
            old = new_cache.get(key, 0)
            print(f"❌ Çekilemedi, Eski veri korunuyor: {old}")
    
    print("🌍 Tamamlandı.\n")
    return new_cache

def main():
    print(f"🤖 CLOUD BOT BAŞLATILIYOR... (Hedef: {SHEET_NAME})")
    
    sheet = connect_to_sheet()
    if not sheet:
        print("🔥 Bağlantı kurulamadı, çıkılıyor.")
        return

    # Başlık yoksa ekle
    if not sheet.get_all_values():
        sheet.append_row(SUTUN_SIRASI)
        print("📝 Tablo boştu, başlıklar eklendi.")

    # 1. ADIM: HAFIZAYI YÜKLE (En önemlisi bu)
    cached_funds = get_last_known_from_sheet(sheet)
    
    # 2. ADIM: FONLARI GÜNCELLE
    # Cloud'da 'while' döngüsü olmadığı için, bot her uyandığında bir kez dener.
    cached_funds = update_all_funds(cached_funds)
    
    # 3. ADIM: ALTIN VE DOLAR
    gold = fetch_gold()
    usd = fetch_usd()
    
    # 4. ADIM: VERİYİ PAKETLE
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row_dict = {"Tarih": ts, "DOLAR KURU": usd}
    if gold: row_dict.update(gold)
    
    # Hafızadaki fon verilerini ekle (Yeni çekilenler veya eskiler)
    row_dict.update(cached_funds)
    
    # 5. ADIM: YAZMA VE BİTİRME
    if gold:
        # Sütun sırasına göre listeyi hazırla
        row_values = []
        for col in SUTUN_SIRASI:
            val = row_dict.get(col, 0.0)
            row_values.append(val)
        
        try:
            # value_input_option='USER_ENTERED' -> Google'a "Bu sayıdır" demeyi zorlar
            sheet.append_row(row_values, value_input_option='USER_ENTERED')
            
            gram = row_dict.get('GRAM ALTIN ALIŞ', 0)
            tp2 = row_dict.get('TP2 FİYAT', 0)
            print(f"[{ts.split()[1]}] ✅ CLOUD KAYIT BAŞARILI. Gram: {gram} | TP2: {tp2}")
        except Exception as e:
            print(f"🔥 Yazma Hatası: {e}")
            # Hata olsa bile döngü olmadığı için script burada biter
            # Bir sonraki turda (15 dk sonra) tekrar dener.

if __name__ == "__main__":
    main()
