import gspread
from oauth2client.service_account import ServiceAccountCredentials
import yfinance as yf
import pandas as pd
import time
from datetime import datetime, timedelta
import os

# --- AYARLAR ---
SHEET_NAME = "PortfoyVerileri"

def connect_to_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    if os.path.exists('credentials.json'):
        creds = ServiceAccountCredentials.from_json_keyfile_name('credentials.json', scope)
        return gspread.authorize(creds).open(SHEET_NAME)
    return None

def main():
    print("🚀 GEÇMİŞ VERİ TAMAMLAYICI BAŞLATILDI...")
    sheet = connect_to_sheet()
    if not sheet: 
        print("🔥 HATA: credentials.json bulunamadı!")
        return
    
    ws = sheet.sheet1
    headers = ws.row_values(1)
    
    # 1 Yıllık Tarih Aralığı
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365)
    
    print(f"📅 {start_date.strftime('%Y-%m-%d')} ve {end_date.strftime('%Y-%m-%d')} arası veriler çekiliyor...")

    # Piyasa Verilerini Çek (Dolar, Euro, Ons Altın)
    # USDTRY=X, EURTRY=X, GC=F (Gold Futures)
    market_tickers = {
        "DOLAR KURU": "USDTRY=X",
        "EURO KURU": "EURTRY=X",
        "ALTIN_ONS": "GC=F"
    }
    
    market_data = yf.download(list(market_tickers.values()), start=start_date, end=end_date, interval="1d")['Close']
    
    # Hisse Senetlerini Bul
    stock_cols = [h for h in headers if ".IS" in h]
    if stock_cols:
        stock_tickers = [h.replace(" FİYAT", "") for h in stock_cols]
        stock_data = yf.download(stock_tickers, start=start_date, end=end_date, interval="1d")['Close']
    else:
        stock_data = pd.DataFrame()

    # Fonlar (Geçmiş veri TEFAS'ta zor olduğu için şimdilik 0 veya son fiyat basılır)
    fund_cols = [h for h in headers if " FİYAT" in h and ".IS" not in h]

    # --- VERİ BİRLEŞTİRME ---
    all_dates = market_data.index
    history_rows = []

    for date in all_dates:
        row_dict = {"Tarih": date.strftime("%Y-%m-%d 18:00:00")}
        
        # Döviz
        usd = market_data.loc[date, "USDTRY=X"]
        eur = market_data.loc[date, "EURTRY=X"]
        row_dict["DOLAR KURU"] = round(usd, 4)
        row_dict["EURO KURU"] = round(eur, 4)
        
        # Gram Altın Tahmini (ONS / 31.1 * USD)
        ons = market_data.loc[date, "GC=F"]
        gram = (ons / 31.1035) * usd
        
        # Altın Sütunlarını Doldur
        gold_keys = ["GRAM ALTIN", "22 AYAR ALTIN", "ATA ALTIN", "ÇEYREK ALTIN"]
        for gk in gold_keys:
            if f"{gk} ALIŞ" in headers:
                # Basit bir çarpanla Kapalıçarşı simülasyonu
                multiplier = 1.0 if "GRAM" in gk else 0.916 if "22 AYAR" in gk else 1.0 # Basit mantık
                row_dict[f"{gk} ALIŞ"] = round(gram * multiplier, 2)
                row_dict[f"{gk} SATIŞ"] = round(gram * multiplier * 1.01, 2) # %1 makas
        
        if f"ALTIN ONS ALIŞ" in headers:
            row_dict["ALTIN ONS ALIŞ"] = round(ons, 2)
            row_dict["ALTIN ONS SATIŞ"] = round(ons * 1.001, 2)

        # Hisseler
        for col in stock_cols:
            ticker = col.replace(" FİYAT", "")
            if ticker in stock_data.columns:
                row_dict[col] = round(stock_data.loc[date, ticker], 2)
        
        # Fonlar (Fon verisi olmadığı için 0 basıyoruz, bot çalıştıkça dolacak)
        for col in fund_cols:
            row_dict[col] = 0

        # Satırı oluştur
        row_values = [row_dict.get(h, 0) for h in headers]
        history_rows.append(row_values)

    # --- SHEET'E YAZ ---
    print(f"✍️ {len(history_rows)} günlük veri Excel'e yazılıyor...")
    # Mevcut verilerin üstüne yazmamak için sayfayı temizleyip yeniden yazmak veya başa eklemek gerekir.
    # En güvenlisi: Mevcut verileri koruyup, geçmişi en başa eklemektir.
    
    # Mevcut verileri oku (Botun topladığı 9 Şubat sonrası)
    current_data = ws.get_all_values()[1:] # Başlık hariç
    
    # Tüm veriyi temizle
    ws.clear()
    
    # Başlıkları yaz
    ws.append_row(headers)
    
    # Geçmişi yaz
    ws.append_rows(history_rows)
    
    # Botun topladığı yeni verileri geri yaz
    if current_data:
        ws.append_rows(current_data)

    print("✅ İŞLEM BAŞARIYLA TAMAMLANDI. Artık 1 yıllık grafiğin var!")

if __name__ == "__main__":
    main()
