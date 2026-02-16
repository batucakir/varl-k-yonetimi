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

# ✅ EKLEME: Retry/backoff için
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- AYARLAR ---
SHEET_NAME = "PortfoyVerileri"
CONFIG_SHEET_NAME = "Ayarlar"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]

# ✅ EKLEME: Session + Retry
def make_session():
    session = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=0.8,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session

def connect_to_sheet():
    try:
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        if os.path.exists('credentials.json'):
            creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
            client = gspread.authorize(creds)
            return client.open(SHEET_NAME)
        return None
    except Exception as e:
        print(f"🔥 Bağlantı Hatası: {e}")
        return None

def get_last_row_data(ws):
    """Sheets'teki en son satırı bir sözlük olarak döndürür (Fallback için)"""
    try:
        all_values = ws.get_all_values()
        if len(all_values) < 2:
            return {}
        headers = all_values[0]
        last_row = all_values[-1]
        return {headers[i]: last_row[i] for i in range(len(headers)) if i < len(last_row)}
    except:
        return {}

def clean_currency(value_str):
    if not value_str:
        return 0.0
    s = str(value_str).replace("TL", "").replace("%", "").strip()
    match = re.search(r'([\d\.,]+)', s)
    if not match:
        return 0.0
    clean_str = match.group(1)
    if ',' in clean_str and '.' in clean_str:
        if clean_str.rfind(',') > clean_str.rfind('.'):
            return float(clean_str.replace('.', '').replace(',', '.'))
        return float(clean_str.replace(',', ''))
    if ',' in clean_str:
        return float(clean_str.replace(',', '.'))
    return float(clean_str)

def fetch_fx(session=None):
    data = {"DOLAR KURU": 0.0, "EURO KURU": 0.0}
    session = session or requests
    try:
        r = session.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=10).json()
        data["DOLAR KURU"] = round(float(r['rates']['TRY']), 4)
        r2 = session.get("https://api.exchangerate-api.com/v4/latest/EUR", timeout=10).json()
        data["EURO KURU"] = round(float(r2['rates']['TRY']), 4)
    except:
        pass
    return data

def fetch_gold(session=None):
    session = session or requests
    url = f"https://anlikaltinfiyatlari.com/altin/kapalicarsi?v={random.random()}"
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    data = {}
    try:
        resp = session.get(url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.content, "html.parser")
        targets = {
            "GRAM ALTIN": "GRAM ALTIN",
            "22 AYAR ALTIN": "22 AYAR",
            "ATA ALTIN": "ATA ALTIN",
            "ÇEYREK ALTIN": "ÇEYREK",
            "ALTIN ONS": "ONS"
        }
        for row in soup.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) > 2:
                name = cols[0].get_text(strip=True).upper()
                for key, keyword in targets.items():
                    if keyword in name:
                        data[f"{key} ALIŞ"] = clean_currency(cols[1].get_text())
                        data[f"{key} SATIŞ"] = clean_currency(cols[2].get_text())
    except:
        pass
    return data

def fetch_fund(code, session=None):
    """TEFAS Veri Çekme - Daha kararlı sürüm"""
    session = session or requests
    url = f"https://www.tefas.gov.tr/FonAnaliz.aspx?FonKod={code}"
    headers = {"User-Agent": random.choice(USER_AGENTS), "X-Requested-With": "XMLHttpRequest"}
    try:
        time.sleep(1.5)  # Rate limit koruması
        resp = session.get(url, headers=headers, timeout=20)
        soup = BeautifulSoup(resp.content, "html.parser")

        # Seçenek 1: ID ile bulma
        price_span = soup.find("span", id="MainContent_FormViewMainContent_LabelLastPrice")
        if price_span:
            return clean_currency(price_span.get_text())

        # Seçenek 2: Liste tarama
        items = soup.select(".top-list li")
        for item in items:
            if "Son Fiyat" in item.text:
                spans = item.find_all("span")
                if spans:
                    return clean_currency(spans[-1].text)
    except Exception as e:
        print(f"❌ Fon çekme hatası ({code}): {e}")
    return 0.0

def fetch_stock(ticker):
    """yfinance - Daha derin arama"""
    try:
        time.sleep(0.5)
        t = yf.Ticker(ticker)
        # 1. Metot: Fast Info
        try:
            price = t.fast_info['last_price']
            if price and price > 0:
                return round(float(price), 2)
        except:
            pass

        # 2. Metot: History
        hist = t.history(period="1d", interval="1m")
        if not hist.empty:
            return round(float(hist['Close'].iloc[-1]), 2)

        # 3. Metot: Regular Info
        return round(float(t.info.get('regularMarketPrice', 0.0)), 2)
    except:
        return 0.0

def main():
    tr_now = datetime.utcnow() + timedelta(hours=3)
    print(f"🚀 BOT V82 BAŞLATILDI... ({tr_now.strftime('%H:%M:%S')})")

    # ✅ EKLEME: Retry’li session
    session = make_session()

    sheet = connect_to_sheet()
    if not sheet:
        return

    ws_data = sheet.sheet1
    last_known = get_last_row_data(ws_data)  # Geçmiş verileri al (Fallback için)

    # Sütun yapısı
    headers = ws_data.row_values(1)

    # 1. Temel Veriler
    fx_data = fetch_fx(session=session)
    gold_data = fetch_gold(session=session)

    # 2. Varlık Döngüsü
    row_dict = {"Tarih": tr_now.strftime("%Y-%m-%d %H:%M:%S")}
    row_dict.update(fx_data)
    row_dict.update(gold_data)

    for h in headers:
        if h in row_dict or h == "Tarih":
            continue

        # Sütun isminden sembolü ayıkla (Örn: "THYAO.IS FİYAT" -> "THYAO.IS")
        symbol = h.replace(" FİYAT", "").strip()
        print(f"🔍 İşleniyor: {symbol}...", end=" ")

        price = 0.0

        # ✅ EKLEME: Fon tespiti regex ile daha güvenli
        # (3-4 karakter, harf/rakam, nokta yok)
        is_fund = bool(re.fullmatch(r"[A-Z0-9]{3,4}", symbol))

        if is_fund:
            price = fetch_fund(symbol, session=session)
        else:
            price = fetch_stock(symbol)

        # --- FALLBACK MEKANİZMASI ---
        if price <= 0:
            old_val = last_known.get(h, "0")
            price = clean_currency(old_val)
            print(f"⚠️ Çekilemedi! Eski veri kullanıldı: {price}")
        else:
            print(f"✅ Başarılı: {price}")

        row_dict[h] = price

    # 3. Yazma İşlemi
    new_row = [row_dict.get(h, 0.0) for h in headers]

    try:
        ws_data.append_row(new_row, value_input_option='USER_ENTERED')
        print(f"🎉 VERİ KAYDEDİLDİ: {tr_now.strftime('%H:%M')}")
    except Exception as e:
        print(f"🔥 Kayıt Hatası: {e}")

if __name__ == "__main__":
    main()
