import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime
import re
import random
import os

# --- AYARLAR ---
SHEET_NAME = "PortfoyVerileri"
MY_FUNDS = ["TLY", "DFI", "TP2"]

HEADERS = [
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
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    if os.path.exists('credentials.json'):
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
        client = gspread.authorize(creds)
        sheet = client.open(SHEET_NAME).sheet1
        return sheet
    return None

def clean_currency(value_str):
    """
    Web'den gelen '1.234,56' veya '1,234.56' verisini
    Python'un anlayacağı '1234.56' float formatına çevirir.
    """
    if not value_str: return 0.0
    s = str(value_str).strip()
    
    # İçinde sadece sayı ve noktalama işaretleri kalsın
    s = re.sub(r'[^\d.,]', '', s)
    
    if not s: return 0.0
    
    # Son karakter bir noktalama işaretiyse sil (örn: 123.)
    if s[-1] in ".,": s = s[:-1]
    
    # Nokta ve virgül analizi
    if "," in s and "." in s:
        # Hem nokta hem virgül varsa, sağdaki ondalıktır.
        if s.rfind(",") > s.rfind("."): # Örn: 1.234,56 (TR)
            s = s.replace(".", "").replace(",", ".")
        else: # Örn: 1,234.56 (US)
            s = s.replace(",", "")
    elif "," in s:
        # Sadece virgül var. 
        # Eğer virgül sondan 3. karakterden önceyse binliktir (1,234) -> sil
        # Eğer sondaysa ondalıktır (12,50) -> nokta yap
        # Basit çözüm: TR siteleri genelde ondalık için virgül kullanır.
        # Ama TEFAS bazen 6 haneli ondalık verir (3,123456).
        s = s.replace(",", ".")
    # Sadece nokta varsa (1.234) -> Python zaten anlar, dokunma.
    
    try:
        return float(s)
    except:
        return 0.0

def get_last_known_from_sheet(sheet):
    """Hafıza yükleme"""
    memory = {}
    try:
        all_values = sheet.get_all_values()
        if len(all_values) < 2: return {}
        last_row = all_values[-1]
        for i, h in enumerate(all_values[0]):
            if "FİYAT" in h and i < len(last_row):
                try:
                    # Google Sheets virgüllü string döndürebilir, onu floata çevir
                    val_str = str(last_row[i]).replace(",", ".")
                    val = float(val_str)
                    if val > 0: memory[h] = val
                except: pass
    except: pass
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
    except: return {}

def fetch_fund_single(code):
    url = f"https://www.tefas.gov.tr/FonAnaliz.aspx?FonKod={code}"
    headers = {"User-Agent": random.choice(USER_AGENTS), "Referer": "https://www.tefas.gov.tr/"}
    try:
        time.sleep(1)
        resp = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.content, "html.parser")
        
        # Seçici 1: Top list
        top_list = soup.find("ul", class_="top-list")
        if top_list:
            for li in top_list.find_all("li"):
                if "Son Fiyat" in li.get_text():
                    return clean_currency(li.get_text().split("Fiyat")[-1])
        # Seçici 2: Main indicators
        item = soup.select_one(".main-indicators li:first-child span")
        if item: return clean_currency(item.get_text())
        
        return 0.0
    except: return 0.0

def main():
    print("🚀 BOT ÇALIŞIYOR...")
    sheet = connect_to_sheet()
    if not sheet: return

    if not sheet.get_all_values():
        sheet.append_row(HEADERS)

    cached_funds = get_last_known_from_sheet(sheet)
    
    # Fonları güncelle
    for code in MY_FUNDS:
        key = f"{code} FİYAT"
        price = fetch_fund_single(code)
        if price > 0:
            cached_funds[key] = price
            print(f"✅ {code}: {price}")
        else:
            old = cached_funds.get(key, 0)
            print(f"❌ {code} çekilemedi, eski ({old}) kullanılıyor.")

    gold = fetch_gold()
    usd = fetch_usd()
    
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row_dict = {"Tarih": ts, "DOLAR KURU": usd}
    if gold: row_dict.update(gold)
    row_dict.update(cached_funds)
    
    if gold:
        row_values = []
        for col in HEADERS:
            # Python float'ı stringe çevirmeden direkt listeye ekle
            # Gspread bunu Google Sheets'e doğru formatta iletecek
            val = row_dict.get(col, 0.0)
            row_values.append(val)
        
        try:
            # user_entered seçeneği Google'ın sayıyı otomatik tanımasını sağlar
            sheet.append_row(row_values, value_input_option='USER_ENTERED')
            print("✅ Kayıt Başarılı.")
        except Exception as e:
            print(f"🔥 Yazma Hatası: {e}")

if __name__ == "__main__":
    main()
