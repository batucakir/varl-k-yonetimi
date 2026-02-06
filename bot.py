import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime
import re
import random

# --- AYARLAR ---
SHEET_NAME = "PortfoyVerileri"
MY_FUNDS = ["TLY", "DFI", "TP2"]
SCHEDULED_TIMES = ["09:15", "10:00", "13:30", "18:00"]

HEADERS = [
    "Tarih", "DOLAR KURU",
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
    retries = 3
    for i in range(retries):
        try:
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
            creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
            client = gspread.authorize(creds)
            sheet = client.open(SHEET_NAME).sheet1
            return sheet
        except Exception as e:
            print(f"⚠️ Bağlantı denemesi {i+1} başarısız: {e}")
            time.sleep(5)
    return None

def clean_currency_from_web(value_str):
    """Web sitesinden (TEFAS/Altın) gelen veriyi temizler"""
    if not value_str: return 0.0
    value_str = str(value_str).strip()
    match = re.search(r'([\d\.,]+)', value_str)
    if not match: return 0.0
    clean_str = match.group(1)
    
    # 1.234,56 -> 1234.56
    if ',' in clean_str and '.' not in clean_str:
        return float(clean_str.replace(',', '.'))
    if ',' in clean_str and '.' in clean_str:
        if clean_str.rfind(',') > clean_str.rfind('.'): 
             return float(clean_str.replace('.', '').replace(',', '.'))
        else:
             return float(clean_str.replace(',', ''))
    return float(clean_str)

def clean_value_from_sheet(value):
    """Google Sheets'ten okunan veriyi (1.234,56) Python sayısına (1234.56) çevirir"""
    if not value: return 0.0
    try:
        # Eğer zaten sayıysa direkt döndür
        if isinstance(value, (int, float)):
            return float(value)
        
        val_str = str(value).strip()
        # "1.234,56" formatını düzelt: Noktaları sil, virgülü nokta yap
        if "," in val_str and "." in val_str:
            val_str = val_str.replace(".", "") # Binlik ayracını sil
            val_str = val_str.replace(",", ".") # Ondalık ayracını düzelt
        elif "," in val_str:
            val_str = val_str.replace(",", ".")
            
        return float(val_str)
    except:
        return 0.0

def get_last_known_from_sheet(sheet):
    """Sheet'teki son satırı okur ve hafızaya alır"""
    print("☁️ Google Sheets hafızası taranıyor...")
    memory = {}
    try:
        all_values = sheet.get_all_values()
        if len(all_values) < 2: 
            print("⚠️ Tablo boş, hafıza alınamadı.")
            return {}
        
        # Son satırı al
        last_row = all_values[-1]
        headers = all_values[0]
        
        for i, h in enumerate(headers):
            # Fon sütunlarını bul
            if "FİYAT" in h and i < len(last_row):
                raw_val = last_row[i]
                val = clean_value_from_sheet(raw_val)
                
                if val > 0:
                    memory[h] = val
                    print(f"   💡 Hafızaya Alındı: {h} = {val}")
                else:
                    print(f"   ⚠️ {h} için son değer 0 veya okunamadı: {raw_val}")
                    
    except Exception as e:
        print(f"🔥 Hafıza okuma hatası: {e}")
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
                        data[f"{key} ALIŞ"] = clean_currency_from_web(cols[1].get_text())
                        data[f"{key} SATIŞ"] = clean_currency_from_web(cols[2].get_text())
        return data
    except Exception as e:
        print(f"⚠️ Altın çekilemedi: {e}")
        return {}

def fetch_fund_single(code):
    url = f"https://www.tefas.gov.tr/FonAnaliz.aspx?FonKod={code}"
    headers = {"User-Agent": random.choice(USER_AGENTS), "Referer": "https://www.tefas.gov.tr/"}
    try:
        time.sleep(random.uniform(2, 4))
        resp = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.content, "html.parser")
        
        top_list = soup.find("ul", class_="top-list")
        if top_list:
            for li in top_list.find_all("li"):
                if "Son Fiyat" in li.get_text():
                    return clean_currency_from_web(li.get_text().split("Fiyat")[-1])
        
        val = soup.select_one(".main-indicators li:first-child span")
        if val: return clean_currency_from_web(val.get_text())
        
        return 0.0
    except: return 0.0

def update_funds_smart(current_cache, force_update=False):
    """
    Fonları günceller. 
    Eğer veri çekemezse (0 gelirse), KESİNLİKLE current_cache'deki eski değeri korur.
    """
    if force_update: print("\n🌍 FON GÜNCELLEMESİ (TEFAS)...")
    
    # Hafızayı kopyala (Böylece yeni veri gelmezse eskisi silinmez)
    new_cache = current_cache.copy()
    
    for code in MY_FUNDS:
        key = f"{code} FİYAT"
        old_val = new_cache.get(key, 0)
        
        # Eğer zorunlu güncelleme ise veya hafıza boşsa (0 ise) çekmeyi dene
        if force_update or old_val == 0:
            if force_update: print(f"   ⏳ {code} aranıyor...", end=" ")
            price = fetch_fund_single(code)
            
            if price > 0:
                new_cache[key] = price
                if force_update: print(f"✅ GÜNCEL: {price}")
            else:
                # Veri çekilemedi. Eski veriyi koru.
                # new_cache zaten current_cache'in kopyası olduğu için 
                # hiçbir şey yapmamıza gerek yok, eski değer içinde duruyor.
                if force_update: print(f"❌ Çekilemedi -> Eski Veri Korunuyor: {old_val}")
    
    if force_update: print("🌍 Bitti.\n")
    return new_cache

def main():
    print(f"🚀 BOT V19 BAŞLATILIYOR... (Hedef: {SHEET_NAME})")
    
    sheet = connect_to_sheet()
    if not sheet:
        print("🔥 HATA: Sheet'e bağlanılamadı.")
        return

    # 1. HAFIZAYI YÜKLE
    # Bu değişken bot kapanana kadar korunacak
    cached_funds = get_last_known_from_sheet(sheet)
    
    # 2. Başlangıçta bir kez güncelle (Eksikleri tamamla)
    cached_funds = update_funds_smart(cached_funds, force_update=True)
    
    last_scheduled_update = ""

    while True:
        try:
            now = datetime.now()
            current_hm = now.strftime("%H:%M")
            ts = now.strftime("%Y-%m-%d %H:%M:%S")
            
            # 3. ZAMAN KONTROLÜ
            if current_hm in SCHEDULED_TIMES and current_hm != last_scheduled_update:
                # Burada dönen değer cached_funds'ı günceller
                # Eğer veri çekemezse eskisini döndürür
                cached_funds = update_funds_smart(cached_funds, force_update=True)
                last_scheduled_update = current_hm
            
            # 4. ANLIK VERİLER
            gold = fetch_gold()
            usd = fetch_usd()
            
            # 5. VERİ PAKETLEME
            row_dict = {"Tarih": ts, "DOLAR KURU": usd}
            if gold: row_dict.update(gold)
            
            # En kritik yer: cached_funds içindeki verileri (ister yeni ister eski) pakete ekle
            row_dict.update(cached_funds)
            
            # 6. YAZMA
            if gold:
                row_values = []
                for col in HEADERS:
                    # Değeri al, yoksa 0 (ama cached_funds doluysa 0 olmaz)
                    val = row_dict.get(col, 0.0)
                    row_values.append(val)
                
                try:
                    sheet.append_row(row_values)
                except:
                    print("♻️ Bağlantı tazeleniyor...")
                    sheet = connect_to_sheet()
                    sheet.append_row(row_values)
                
                gram = row_dict.get('GRAM ALTIN ALIŞ', 0)
                tp2 = row_dict.get('TP2 FİYAT', 0)
                print(f"[{ts.split()[1]}] ☁️ Kayıt OK. Gram: {gram} | TP2: {tp2}")
            
            # Dakikada 1 işlem (API kotası için)
            time.sleep(60)
            
        except Exception as e:
            print(f"⚠️ Döngü Hatası: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()