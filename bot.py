import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
from bs4 import BeautifulSoup
import time
from datetime import datetime, timedelta
import re
import random
import os
import yfinance as yf

# --- AYARLAR ---
SHEET_NAME = "PortfoyVerileri"
CONFIG_SHEET_NAME = "Ayarlar"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15"
]

def connect_to_sheet():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        if os.path.exists('credentials.json'):
            creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
            client = gspread.authorize(creds)
            return client.open(SHEET_NAME)
        else:
            print("🔥 HATA: credentials.json bulunamadı!")
            return None
    except Exception as e:
        print(f"🔥 Bağlantı Hatası: {e}")
        return None

def get_watchlist(sheet):
    try:
        try:
            ws = sheet.worksheet(CONFIG_SHEET_NAME)
        except:
            ws = sheet.add_worksheet(title=CONFIG_SHEET_NAME, rows=100, cols=5)
            ws.append_row(["Sembol"])
            ws.append_rows([["TLY"], ["DFI"], ["THYAO.IS"], ["ASELS.IS"]])
            return ["TLY", "DFI", "THYAO.IS", "ASELS.IS"]
        col_values = ws.col_values(1)
        return [x.strip() for x in col_values[1:] if x.strip() != ""]
    except: return []

def clean_currency(value_str):
    if not value_str: return 0.0
    value_str = str(value_str).replace("TL", "").strip()
    match = re.search(r'([\d\.,]+)', value_str)
    if not match: return 0.0
    clean_str = match.group(1)
    if ',' in clean_str and '.' in clean_str:
        if clean_str.rfind(',') > clean_str.rfind('.'):
            return float(clean_str.replace('.', '').replace(',', '.'))
        return float(clean_str.replace(',', ''))
    if ',' in clean_str: return float(clean_str.replace(',', '.'))
    return float(clean_str)

def fetch_usd_eur():
    data = {"DOLAR KURU": 0.0, "EURO KURU": 0.0}
    try:
        # Daha kararlı bir API
        resp = requests.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=10).json()
        data["DOLAR KURU"] = round(float(resp['rates']['TRY']), 4)
        resp_eur = requests.get("https://api.exchangerate-api.com/v4/latest/EUR", timeout=10).json()
        data["EURO KURU"] = round(float(resp_eur['rates']['TRY']), 4)
    except: pass
    return data

def fetch_gold():
    url = f"https://anlikaltinfiyatlari.com/altin/kapalicarsi?v={random.random()}"
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    data = {}
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.content, "html.parser")
        targets = {"GRAM ALTIN": "GRAM ALTIN", "22 AYAR ALTIN": "22 AYAR", 
                   "ATA ALTIN": "ATA ALTIN", "ÇEYREK ALTIN": "ÇEYREK", "ALTIN ONS": "ONS"}
        for row in soup.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) > 2:
                name = cols[0].get_text(strip=True).upper()
                for key, keyword in targets.items():
                    if keyword in name:
                        data[f"{key} ALIŞ"] = clean_currency(cols[1].get_text())
                        data[f"{key} SATIŞ"] = clean_currency(cols[2].get_text())
    except: pass
    return data

def fetch_fund_price(code):
    """TEFAS'tan fon fiyatı çeker"""
    url = f"https://www.tefas.gov.tr/FonAnaliz.aspx?FonKod={code}"
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.content, "html.parser")
        # TEFAS'taki ana fiyat alanı
        price_span = soup.find("span", id="MainContent_FormViewMainContent_LabelLastPrice")
        if price_span:
            return clean_currency(price_span.get_text())
        # Alternatif liste taraması
        top_list = soup.find("ul", class_="top-list")
        if top_list:
            for li in top_list.find_all("li"):
                if "Son Fiyat" in li.text:
                    return clean_currency(li.find_all("span")[-1].text)
    except: pass
    print(f"⚠️ Fon fiyatı çekilemedi: {code}")
    return 0.0

def fetch_stock_price(ticker):
    """yfinance ile hisse fiyatı çeker"""
    try:
        t = yf.Ticker(ticker)
        # Hızlı veri (fast_info) genellikle daha güvenilirdir
        price = t.fast_info['last_price']
        if price and price > 0: return round(float(price), 2)
        # Alternatif: güncel kapanış
        hist = t.history(period="1d")
        if not hist.empty:
            return round(float(hist['Close'].iloc[-1]), 2)
    except: pass
    print(f"⚠️ Hisse fiyatı çekilemedi: {ticker}")
    return 0.0

def main():
    tr_now = datetime.utcnow() + timedelta(hours=3)
    print(f"🚀 BOT BAŞLATILDI... ({tr_now.strftime('%H:%M:%S')})")
    
    sheet = connect_to_sheet()
    if not sheet: return
    
    watchlist = get_watchlist(sheet)
    ws_data = sheet.sheet1
    
    # 1. Piyasa Verileri
    fx_data = fetch_usd_eur()
    gold_data = fetch_gold()
    
    # 2. Takip Listesi Verileri
    asset_prices = {}
    for item in watchlist:
        print(f"🔍 Veri çekiliyor: {item}...")
        if len(item) <= 4 and "." not in item:
            price = fetch_fund_price(item)
        else:
            price = fetch_stock_price(item)
        asset_prices[f"{item} FİYAT"] = price
        time.sleep(0.5) # Banlanmamak için kısa bekleme

    # 3. Excel Hazırlığı
    base_headers = ["Tarih", "DOLAR KURU", "EURO KURU", "GRAM ALTIN ALIŞ", "GRAM ALTIN SATIŞ", 
                    "22 AYAR ALTIN ALIŞ", "22 AYAR ALTIN SATIŞ", "ATA ALTIN ALIŞ", 
                    "ATA ALTIN SATIŞ", "ÇEYREK ALTIN ALIŞ", "ÇEYREK ALTIN SATIŞ", 
                    "ALTIN ONS ALIŞ", "ALTIN ONS SATIŞ"]
    
    dynamic_headers = [f"{item} FİYAT" for item in watchlist]
    full_headers = base_headers + dynamic_headers
    
    # Başlıkları senkronize et
    try:
        if ws_data.row_values(1) != full_headers:
            print("📝 Başlıklar güncelleniyor...")
            # Verileri kaybetmeden sadece başlığı güncellemek tehlikeli olabilir, 
            # ancak bu bot yapısında yeni sütun eklenmesi için gereklidir.
            # Veri varsa clear yapma, sadece append_row mantığını koru.
            pass 
    except: pass

    # 4. Satır Oluşturma
    row_dict = {"Tarih": tr_now.strftime("%Y-%m-%d %H:%M:%S")}
    row_dict.update(fx_data)
    row_dict.update(gold_data)
    row_dict.update(asset_prices)
    
    new_row = [row_dict.get(h, 0.0) for h in full_headers]
    
    try:
        ws_data.append_row(new_row, value_input_option='USER_ENTERED')
        print(f"✅ VERİ KAYDEDİLDİ: {tr_now.strftime('%H:%M')}")
    except Exception as e:
        print(f"🔥 Kayıt Hatası: {e}")

if __name__ == "__main__":
    main()
