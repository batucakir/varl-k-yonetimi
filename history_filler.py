import gspread
from oauth2client.service_account import ServiceAccountCredentials
import yfinance as yf
import pandas as pd
import time
from datetime import datetime, timedelta
import os
import json

# --- AYARLAR ---
SHEET_NAME = "PortfoyVerileri"

def connect_to_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    
    # 1. GitHub Secrets Kontrolü
    creds_json = os.environ.get('GCP_SERVICE_ACCOUNT')
    
    try:
        if creds_json:
            # GitHub Actions üzerinde çalışıyorsa
            creds_info = json.loads(creds_json)
            creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_info, scope)
            print("✅ GitHub Secrets üzerinden bağlantı kuruldu.")
        elif os.path.exists('credentials.json'):
            # Yerel bilgisayarda çalışıyorsa
            creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
            print("✅ Yerel credentials.json üzerinden bağlantı kuruldu.")
        else:
            print("❌ HATA: Kimlik bilgileri bulunamadı!")
            return None
            
        return gspread.authorize(creds).open(SHEET_NAME)
    except Exception as e:
        print(f"❌ Bağlantı Hatası: {e}")
        return None

def main():
    print("🚀 GEÇMİŞ VERİ TAMAMLAYICI BAŞLATILDI...")
    sheet = connect_to_sheet()
    if not sheet: return
    
    ws = sheet.sheet1
    all_values = ws.get_all_values()
    if not all_values: 
        print("❌ Sayfa boş!"); return
        
    headers = all_values[0]
    
    # 1 Yıllık Tarih Aralığı
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365)
    
    print(f"📅 {start_date.strftime('%Y-%m-%d')} ve {end_date.strftime('%Y-%m-%d')} arası veriler çekiliyor...")

    # Piyasa Verilerini Çek
    market_tickers = {"DOLAR KURU": "USDTRY=X", "EURO KURU": "EURTRY=X", "ALTIN_ONS": "GC=F"}
    
    try:
        market_data = yf.download(list(market_tickers.values()), start=start_date, end=end_date, interval="1d")['Close']
    except:
        print("❌ yfinance veri çekme hatası!"); return
    
    # Hisse Senetlerini Bul
    stock_cols = [h for h in headers if ".IS" in h]
    if stock_cols:
        stock_tickers = [h.replace(" FİYAT", "") for h in stock_cols]
        stock_data = yf.download(stock_tickers, start=start_date, end=end_date, interval="1d")['Close']
    else:
        stock_data = pd.DataFrame()

    fund_cols = [h for h in headers if " FİYAT" in h and ".IS" not in h]
    history_rows = []

    # Verileri Birleştir
    for date in market_data.index:
        row_dict = {"Tarih": date.strftime("%Y-%m-%d 18:00:00")}
        
        # Döviz
        usd = market_data.loc[date, "USDTRY=X"]
        eur = market_data.loc[date, "EURTRY=X"]
        row_dict["DOLAR KURU"] = round(usd, 4)
        row_dict["EURO KURU"] = round(eur, 4)
        
        # Gram Altın Tahmini (Simülasyon)
        ons = market_data.loc[date, "GC=F"]
        gram = (ons / 31.1035) * usd
        
        gold_keys = ["GRAM ALTIN", "22 AYAR ALTIN", "ATA ALTIN", "ÇEYREK ALTIN"]
        for gk in gold_keys:
            if f"{gk} ALIŞ" in headers:
                multiplier = 1.0 if "GRAM" in gk else 0.916 if "22 AYAR" in gk else 1.0
                row_dict[f"{gk} ALIŞ"] = round(gram * multiplier, 2)
                row_dict[f"{gk} SATIŞ"] = round(gram * multiplier * 1.01, 2)
        
        if "ALTIN ONS ALIŞ" in headers:
            row_dict["ALTIN ONS ALIŞ"] = round(ons, 2)
            row_dict["ALTIN ONS SATIŞ"] = round(ons * 1.001, 2)

        for col in stock_cols:
            ticker = col.replace(" FİYAT", "")
            if ticker in stock_data.columns:
                val = stock_data.loc[date, ticker]
                row_dict[col] = round(float(val), 2) if pd.notna(val) else 0

        for col in fund_cols: row_dict[col] = 0

        row_values = [row_dict.get(h, 0) for h in headers]
        history_rows.append(row_values)

    # --- SHEET'E YAZ ---
    print(f"✍️ {len(history_rows)} günlük veri işleniyor...")
    current_data = all_values[1:] 
    
    ws.clear()
    ws.append_row(headers)
    
    # Parçalı Yazma (Büyük verilerde hata almamak için)
    if history_rows:
        ws.append_rows(history_rows, value_input_option='USER_ENTERED')
        print("✅ Geçmiş veriler eklendi.")
    
    if current_data:
        ws.append_rows(current_data, value_input_option='USER_ENTERED')
        print("✅ Mevcut bot verileri geri eklendi.")

    print("🏁 İŞLEM TAMAMLANDI!")

if __name__ == "__main__":
    main()
