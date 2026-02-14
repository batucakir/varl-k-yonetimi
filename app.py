import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import uuid

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Kişisel Varlık Paneli", page_icon="💎", layout="wide", initial_sidebar_state="expanded")

# --- AYARLAR ---
SHEET_NAME = "PortfoyVerileri"
CONFIG_SHEET_NAME = "Ayarlar"
HEDEF_SERVET_TL = 2000000 
HEDEF_TARIH = datetime(2026, 2, 28)
FON_VERGI_ORANI = 0.175

# --- FORMATLAMA FONKSİYONLARI ---
def format_turkish_currency(value):
    if pd.isna(value) or value == "" or value == 0: return "-"
    # 1234.56 -> "1.234,56"
    try:
        return "{:,.2f}".format(float(value)).replace(",", "X").replace(".", ",").replace("X", ".")
    except: return str(value)

def format_percent(value):
    try:
        val = float(value)
        color = "green" if val > 0 else "red" if val < 0 else "gray"
        arrow = "▲" if val > 0 else "▼" if val < 0 else "➖"
        # Renklendirme dataframe içinde zor olduğu için ok işareti kullanıyoruz
        return f"{arrow} %{abs(val):.2f}"
    except: return "-"

# --- VERİ BAĞLANTISI ---
def get_client():
    credentials_dict = st.secrets["gcp_service_account"]
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
    return gspread.authorize(creds)

@st.cache_data(ttl=60)
def load_data():
    try:
        client = get_client()
        sheet = client.open(SHEET_NAME)
        
        # 1. Fiyat Verileri
        ws_prices = sheet.sheet1 # İlk sayfa her zaman veridir
        data_prices = ws_prices.get_all_values()
        if len(data_prices) > 1:
            df_prices = pd.DataFrame(data_prices[1:], columns=data_prices[0])
            for col in df_prices.columns:
                if col != "Tarih":
                    df_prices[col] = df_prices[col].astype(str).str.replace(",", ".")
                    df_prices[col] = pd.to_numeric(df_prices[col], errors='coerce').fillna(0)
            df_prices['Tarih'] = pd.to_datetime(df_prices['Tarih'])
        else: df_prices = pd.DataFrame()

        # 2. İşlemler
        try:
            ws_trans = sheet.worksheet("Islemler")
            data_trans = ws_trans.get_all_values()
            df_trans = pd.DataFrame(data_trans[1:], columns=data_trans[0])
            df_trans['Adet'] = pd.to_numeric(df_trans['Adet'].astype(str).str.replace(",", "."), errors='coerce').fillna(0)
            df_trans['Fiyat'] = pd.to_numeric(df_trans['Fiyat'].astype(str).str.replace(",", "."), errors='coerce').fillna(0)
        except: df_trans = pd.DataFrame()

        # 3. Takip Listesi (Ayarlar)
        try:
            ws_conf = sheet.worksheet(CONFIG_SHEET_NAME)
            watchlist = [x[0] for x in ws_conf.get_all_values()[1:] if x]
        except: watchlist = []

        return df_prices, df_trans, watchlist
    except Exception as e:
        return pd.DataFrame(), pd.DataFrame(), []

# --- İŞLEMLER ---
def add_to_watchlist(symbol):
    client = get_client()
    sheet = client.open(SHEET_NAME)
    try:
        ws = sheet.worksheet(CONFIG_SHEET_NAME)
    except:
        ws = sheet.add_worksheet(CONFIG_SHEET_NAME, 100, 5)
        ws.append_row(["Sembol"])
    
    # Var mı kontrol et
    existing = [x[0] for x in ws.get_all_values()]
    if symbol not in existing:
        ws.append_row([symbol])
        return True
    return False

# --- HESAPLAMA ---
def calculate_portfolio(df_trans, df_prices):
    portfolio = {}
    last_prices = df_prices.iloc[-1] if not df_prices.empty else {}
    
    for _, row in df_trans.iterrows():
        varlik = row['Varlık']
        islem = str(row['İşlem']).upper()
        adet = float(row['Adet'])
        fiyat = float(row['Fiyat']) # Maliyet
        
        if varlik not in portfolio: 
            portfolio[varlik] = {"adet": 0.0, "maliyet_tl": 0.0, "guncel_fiyat": 0.0, "tur": row['Tür']}
        
        curr = portfolio[varlik]
        if islem == "ALIS":
            curr["adet"] += adet
            curr["maliyet_tl"] += (adet * fiyat)
        elif islem == "SATIS":
            if curr["adet"] > 0:
                avg_cost = curr["maliyet_tl"] / curr["adet"]
                curr["maliyet_tl"] -= (adet * avg_cost)
                curr["adet"] -= adet

    # Güncel Değerleri Ekle
    total_wealth = 0
    clean_portfolio = []
    
    for varlik, data in portfolio.items():
        if data["adet"] <= 0.001: continue
        
        # Fiyat Eşleştirme (Mapping mantığı burada dinamik olmalı)
        # Basit mantık: Varlık isminde geçen kelimeyi sütunlarda ara
        current_price = 1.0 # Varsayılan (Nakit)
        
        # Mapping Denemesi
        search_key = varlik
        if "FON" in varlik: search_key = varlik.split()[0] # "TLY FONU" -> "TLY"
        if "Hisse" in varlik: search_key = varlik.split()[0] # "THYAO (Hisse)" -> "THYAO"
        
        # Sütunlarda ara
        for col in last_prices.index:
            if search_key in col and "FİYAT" in col:
                current_price = last_prices[col]
                break
            # Altınlar için özel mapping
            if varlik == "22 AYAR BİLEZİK (Gr)": current_price = last_prices.get("22 AYAR ALTIN ALIŞ", 0)
            if varlik == "ATA ALTIN (Adet)": current_price = last_prices.get("ATA ALTIN ALIŞ", 0)
            if varlik == "ÇEYREK ALTIN (Adet)": current_price = last_prices.get("ÇEYREK ALTIN ALIŞ", 0)

        guncel_deger = data["adet"] * current_price
        kar_zarar = guncel_deger - data["maliyet_tl"]
        kar_oran = (kar_zarar / data["maliyet_tl"] * 100) if data["maliyet_tl"] > 0 else 0
        
        clean_portfolio.append({
            "Varlık": varlik,
            "Adet": format_turkish_currency(data["adet"]).replace("X", ""), # Adet formatı düzeltildi
            "Birim Fiyat": format_turkish_currency(current_price),
            "Maliyet": format_turkish_currency(data["maliyet_tl"]),
            "Güncel Değer": format_turkish_currency(guncel_deger),
            "Kâr/Zarar": format_turkish_currency(kar_zarar),
            "Kâr %": format_percent(kar_oran),
            "raw_wealth": guncel_deger # Sıralama ve toplam için
        })
        total_wealth += guncel_deger
        
    return pd.DataFrame(clean_portfolio), total_wealth

# --- ANA UYGULAMA ---
def main():
    df_prices, df_trans, watchlist = load_data()
    
    # --- SIDEBAR ---
    with st.sidebar:
        st.title("💎 Varlık Paneli V4.2")
        page = st.radio("Menü", ["Portföyüm", "Piyasa Takip"])
        st.divider()
        
        # Manuel Hisse Ekleme
        with st.expander("🛠️ Takip Listesi Yönetimi"):
            new_symbol = st.text_input("Sembol Ekle (Örn: THYAO.IS, TLY)", help="Sonuna .IS koymayı unutma!")
            if st.button("Listeye Ekle"):
                if new_symbol:
                    if add_to_watchlist(new_symbol):
                        st.success("Eklendi! Bot bir sonraki turda veriyi çekecek.")
                    else:
                        st.warning("Zaten listede var.")
        
        if st.button("Yenile"):
            st.cache_data.clear()
            st.rerun()

    # --- SAYFA 1: PORTFÖYÜM ---
    if page == "Portföyüm":
        st.markdown("### 💼 Varlık Durumu")
        if not df_trans.empty:
            df_port, total_wealth = calculate_portfolio(df_trans, df_prices)
            
            # Kartlar
            k1, k2, k3 = st.columns(3)
            k1.metric("Toplam Servet", f"₺ {format_turkish_currency(total_wealth)}")
            
            if not df_port.empty:
                # Toplam Kar
                total_cost = df_trans["Adet"] * df_trans["Fiyat"] # Yaklaşık
                # Daha detaylı kar hesabı yukarıdaki fonksiyondan gelmeli ama basit tutalım
                
                st.divider()
                st.subheader("📋 Varlık Tablosu")
                
                # Tabloyu Göster (String formatlı olduğu için sıralama bozulabilir ama görüntü mükemmel olur)
                # Görüntü amaçlı kolonları seç
                display_cols = ["Varlık", "Adet", "Birim Fiyat", "Güncel Değer", "Kâr/Zarar", "Kâr %"]
                st.dataframe(
                    df_port[display_cols], 
                    use_container_width=True, 
                    height=(len(df_port) * 35) + 38, # Otomatik Yükseklik Hesabı
                    hide_index=True
                )
                
                # Grafik (Zaman)
                st.divider()
                if not df_prices.empty:
                    st.subheader("Servet Değişimi")
                    # Basit bir trend (Detaylı hesaplama önceki kodlarda vardı, buraya eklenebilir)
                    # Şimdilik sadece Dolar kurunu gösterelim örnek olarak veya boş geçelim
                    # (Önceki kodun karmaşıklığını azalttım, istenirse eklenir)

    # --- SAYFA 2: PİYASA TAKİP ---
    elif page == "Piyasa Takip":
        st.markdown("### 🌍 Piyasa Ekranı")
        
        if not df_prices.empty:
            last_row = df_prices.iloc[-1]
            prev_row = df_prices.iloc[-2] if len(df_prices) > 1 else last_row
            
            # Veriyi Hazırla
            market_data = []
            
            # Sütunları tara (Tarih hariç hepsi)
            for col in df_prices.columns:
                if "FİYAT" in col or "ALTIN" in col or "DOLAR" in col:
                    name = col.replace(" FİYAT", "").replace(" ALIŞ", "").replace(" SATIŞ", "")
                    price = last_row[col]
                    old_price = prev_row[col]
                    
                    if price > 0:
                        diff = (price - old_price) / old_price * 100 if old_price > 0 else 0
                        
                        market_data.append({
                            "Enstrüman": name,
                            "Fiyat": format_turkish_currency(price),
                            "Değişim": format_percent(diff),
                            "raw_diff": diff # Sıralama için gizli
                        })
            
            df_market = pd.DataFrame(market_data)
            
            # Tabloyu Göster
            if not df_market.empty:
                # Kullanıcı sıralama yapabilsin diye raw veriyi saklayıp string gösteriyoruz
                st.dataframe(
                    df_market[["Enstrüman", "Fiyat", "Değişim"]],
                    use_container_width=True,
                    height=(len(df_market) * 35) + 38, # Scroll olmaması için tam yükseklik
                    hide_index=True,
                    column_config={
                        "Enstrüman": st.column_config.TextColumn("Varlık Adı", width="medium"),
                        "Fiyat": st.column_config.TextColumn("Fiyat (TL)", width="medium"),
                        "Değişim": st.column_config.TextColumn("Günlük %", width="small")
                    }
                )
        else:
            st.warning("Veri bekleniyor...")

if __name__ == "__main__":
    main()
