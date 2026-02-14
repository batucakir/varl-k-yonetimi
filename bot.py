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
CONFIG_SHEET_NAME = "Ayarlar"

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
                sheet = client.open(SHEET_NAME)
                return sheet
            else:
                print("🔥 HATA: credentials.json bulunamadı!")
                return None
        except Exception as e:
            print(f"⚠️ Bağlantı denemesi {i+1} başarısız: {e}")
            time.sleep(5)
    return None

def get_watchlist(client):
    """Ayarlar sayfasından takip listesini okur"""
    try:
        sheet = client.open(SHEET_NAME)
        try:
            ws = sheet.worksheet(CONFIG_SHEET_NAME)
        except:
            # Sayfa yoksa oluştur ve varsayılanları ekle
            ws = sheet.add_worksheet(title=CONFIG_SHEET_NAME, rows=100, cols=5)
            default_assets = ["TLY", "DFI", "THYAO.IS", "ASELS.IS"]
            ws.append_row(["Sembol"])
            for asset in default_assets: ws.append_row([asset])
            return default_assets

        col_values = ws.col_values(1) # A sütununu oku
        if len(col_values) > 1:
            return [x.strip() for x in col_values[1:] if x.strip() != ""]
        return []
    except:
        return []

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

def get_last_known_from_sheet(ws_data):
    memory = {}
    try:
        all_values = ws_data.get_all_values()
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
        hist = stock.history(period="1d")
        if not hist.empty:
            return float(hist['Close'].iloc[-1])
        price = stock.fast_info['last_price']
        return float(price) if price else 0.0
    except: return 0.0

def update_assets(watchlist, current_cache):
    print(f"\n🌍 {len(watchlist)} Varlık Güncelleniyor...")
    new_cache = current_cache.copy()
    
    for item in watchlist:
        key = f"{item} FİYAT"
        price = 0.0
        
        # Fon mu Hisse mi? (3-4 harfliyse ve . yoksa Fondur)
        if len(item) <= 4 and "." not in item:
            price = fetch_fund_single(item)
        else:
            price = fetch_stock_price(item)
            
        if price > 0: 
            new_cache[key] = price
        else: 
            new_cache[key] = new_cache.get(key, 0)
    
    print("🌍 Tamamlandı.\n")
    return new_cache

def main():
    tr_now = datetime.utcnow() + timedelta(hours=3)
    print(f"🤖 BOT ÇALIŞIYOR... (Saat: {tr_now.strftime('%H:%M')})")
    
    sheet_client = connect_to_sheet()
    if not sheet_client: return
    
    # 1. Takip Listesini Oku
    watchlist = get_watchlist(sheet_client)
    print(f"📋 Takip Listesi: {watchlist}")

    # 2. Sütunları Hazırla
    base_cols = ["Tarih", "DOLAR KURU", "GRAM ALTIN ALIŞ", "GRAM ALTIN SATIŞ", 
                 "22 AYAR ALTIN ALIŞ", "22 AYAR ALTIN SATIŞ", "ATA ALTIN ALIŞ", 
                 "ATA ALTIN SATIŞ", "ÇEYREK ALTIN ALIŞ", "ÇEYREK ALTIN SATIŞ", 
                 "ALTIN ONS ALIŞ", "ALTIN ONS SATIŞ"]
    
    dynamic_cols = [f"{item} FİYAT" for item in watchlist]
    full_columns = base_cols + dynamic_cols
    
    ws_data = sheet_client.sheet1
    
    # Başlık Kontrolü
    try:
        current_headers = ws_data.row_values(1)
        # Sadece eksik varsa ekle (Basit kontrol)
        if len(current_headers) < len(full_columns) or current_headers[0] != "Tarih":
             # Daha güvenli bir yöntem: 1. satırı tamamen güncelle
             # Ancak sürekli sil-yaz yapmamak için sadece değişiklik varsa yapalım
             if current_headers != full_columns:
                 print("📝 Başlıklar güncelleniyor...")
                 ws_data.clear() # Temiz sayfa daha güvenli başlık çakışması için
                 ws_data.append_row(full_columns)
    except: pass

    # 3. Verileri Çek
    cached_data = get_last_known_from_sheet(ws_data)
    cached_data = update_assets(watchlist, cached_data)
    gold = fetch_gold()
    usd = fetch_usd()
    
    ts = tr_now.strftime("%Y-%m-%d %H:%M:%S")
    row_dict = {"Tarih": ts, "DOLAR KURU": usd}
    if gold: row_dict.update(gold)
    row_dict.update(cached_data)
    
    if gold:
        row_values = []
        for col in full_columns:
            row_values.append(row_dict.get(col, 0.0))
        try:
            ws_data.append_row(row_values, value_input_option='USER_ENTERED')
            print(f"✅ KAYIT BAŞARILI.")
        except Exception as e:
            print(f"🔥 Yazma Hatası: {e}")

if __name__ == "__main__":
    main()
