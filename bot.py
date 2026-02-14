import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime, timedelta
import re
import random
import os
import sys
import yfinance as yf

# --- AYARLAR ---
SHEET_NAME = "PortfoyVerileri"

# 1. FONLAR
MY_FUNDS = ["TLY", "DFI", "TP2", "PHE", "ROF", "PBR"]

# 2. HİSSELER (BIST 30 + SENİN LİSTEN)
BIST_30 = [
    "AKBNK.IS", "ALARK.IS", "ARCLK.IS", "ASELS.IS", "ASTOR.IS", "BIMAS.IS", "BRSAN.IS", 
    "DOAS.IS", "EKGYO.IS", "ENKAI.IS", "EREGL.IS", "FROTO.IS", "GARAN.IS", "GUBRF.IS", 
    "HEKTS.IS", "ISCTR.IS", "KCHOL.IS", "KONTR.IS", "KOZAL.IS", "KRDMD.IS", "OYAKC.IS", 
    "PETKM.IS", "PGSUS.IS", "SAHOL.IS", "SASA.IS", "SISE.IS", "TCELL.IS", "THYAO.IS", 
    "TOASO.IS", "TUPRS.IS", "YKBNK.IS"
]

MY_EXTRAS = [
    "TERA.IS", "TRHOL.IS", "TEHOL.IS", "IEYHO.IS", "ODINE.IS", "MIATK.IS", "HEDEF.IS"
]

ALL_STOCKS = sorted(list(set(BIST_30 + MY_EXTRAS)))

# --- SÜTUN YAPISI ---
BASE_COLUMNS = [
    "Tarih", "DOLAR KURU",
    "GRAM ALTIN ALIŞ", "GRAM ALTIN SATIŞ",
    "22 AYAR ALTIN ALIŞ", "22 AYAR ALTIN SATIŞ",
    "ATA ALTIN ALIŞ", "ATA ALTIN SATIŞ",
    "ÇEYREK ALTIN ALIŞ", "ÇEYREK ALTIN SATIŞ",
    "ALTIN ONS ALIŞ", "ALTIN ONS SATIŞ"
]

FUND_COLUMNS = [f"{code} FİYAT" for code in MY_FUNDS]
STOCK_COLUMNS = [f"{ticker} FİYAT" for ticker in ALL_STOCKS]
SUTUN_SIRASI = BASE_COLUMNS + FUND_COLUMNS + STOCK_COLUMNS

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15"
]

def connect_to_sheet():
    retries = 3
    for i in range(retries):
        try:
            scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
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
    if ',' in clean_str and '.' not in clean_str: return float(clean_str.replace(',', '.'))
    if ',' in clean_str and '.' in clean_str:
        if clean_str.rfind(',') > clean_str.rfind('.'): return float(clean_str.replace('.', '').replace(',', '.'))
        else: return float(clean_str.replace(',', ''))
    return float(clean_str)

def get_last_known_from_sheet(sheet):
    memory = {}
    try:
        all_values = sheet.get_all_values()
        if len(all_values) < 2: return {} 
        last_row = all_values[-1]
        headers = all_values[0]
        for i, h in enumerate(headers):
            if "FİYAT" in h and i < len(last_row):
                try:
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
        time.sleep(0.1) 
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

def fetch_stock_price(ticker):
    try:
        stock = yf.Ticker(ticker)
        # Hafta sonu olduğu için fast_info bazen boş dönebilir, history daha güvenli
        hist = stock.history(period="1d")
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
        
        # Yedek yöntem
        price = stock.fast_info['last_price']
        return float(price) if price else 0.0
    except: return 0.0

def update_all_assets(current_cache):
    print("\n🌍 VARLIK GÜNCELLEMESİ BAŞLADI...")
    new_cache = current_cache.copy()
    
    # 1. Fonlar
    for code in MY_FUNDS:
        key = f"{code} FİYAT"
        price = fetch_fund_single(code)
        if price > 0: new_cache[key] = price
        else: new_cache[key] = new_cache.get(key, 0)

    # 2. Hisseler
    print(f"   📈 {len(ALL_STOCKS)} Hisse taranıyor...")
    for ticker in ALL_STOCKS:
        key = f"{ticker} FİYAT"
        # Log kirliliği yapmasın diye print'i kaldırdım, arka planda çalışsın
        price = fetch_stock_price(ticker)
        if price > 0: new_cache[key] = price
        else: new_cache[key] = new_cache.get(key, 0)
    
    print("🌍 Tamamlandı.\n")
    return new_cache

def main():
    # Uyku modunu kapattık, her türlü çalışacak.
    tr_now = datetime.utcnow() + timedelta(hours=3)
    print(f"🤖 BOT ÇALIŞIYOR (Tamir Modu)... (Saat: {tr_now.strftime('%H:%M')})")
    
    sheet = connect_to_sheet()
    if not sheet: return

    # --- BAŞLIK ONARIM KISMI (TRY-EXCEPT YOK, HATA VARSA GÖRELİM) ---
    print("🛠️ Başlık satırı kontrol ediliyor...")
    current_headers = sheet.row_values(1)
    
    # Eğer başlıklar eksikse veya uyuşmuyorsa
    if current_headers != SUTUN_SIRASI:
        print(f"⚠️ Uyuşmazlık var! Mevcut: {len(current_headers)}, Olması Gereken: {len(SUTUN_SIRASI)}")
        print("♻️ 1. Satır siliniyor ve yeniden yazılıyor...")
        sheet.delete_row(1)
        # Biraz bekle Google kendine gelsin
        time.sleep(2) 
        sheet.insert_row(SUTUN_SIRASI, 1)
        print("✅ Başlıklar başarıyla onarıldı.")
    else:
        print("✅ Başlıklar zaten güncel.")

    # Normal akışa devam
    cached_data = get_last_known_from_sheet(sheet)
    cached_data = update_all_assets(cached_data) 
    gold = fetch_gold()
    usd = fetch_usd()
    
    ts = tr_now.strftime("%Y-%m-%d %H:%M:%S")
    row_dict = {"Tarih": ts, "DOLAR KURU": usd}
    if gold: row_dict.update(gold)
    row_dict.update(cached_data)
    
    if gold:
        row_values = []
        for col in SUTUN_SIRASI:
            row_values.append(row_dict.get(col, 0.0))
        try:
            sheet.append_row(row_values, value_input_option='USER_ENTERED')
            print(f"✅ KAYIT BAŞARILI.")
        except Exception as e:
            print(f"🔥 Yazma Hatası: {e}")

if __name__ == "__main__":
    main()
