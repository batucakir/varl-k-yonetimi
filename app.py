import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import numpy as np

# --- KÜTÜPHANE KONTROLÜ ---
try:
    import yfinance as yf
except ImportError:
    st.error("⚠️ 'yfinance' kütüphanesi eksik!")
    st.stop()

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Varlık Paneli", page_icon="💎", layout="wide", initial_sidebar_state="expanded")

# --- ÖZEL CSS ---
st.markdown("""
<style>
    [data-testid="stMetricValue"] { font-size: 26px; font-weight: bold; }
    .currency-card {
        background-color: #262730; padding: 10px; border-radius: 10px;
        border: 1px solid #41444b; margin-bottom: 10px; text-align: center;
    }
    .rebalance-buy { color: #00FF00; font-weight: bold; }
    .rebalance-sell { color: #FF4B4B; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# --- AYARLAR ---
SHEET_NAME = "PortfoyVerileri"
CONFIG_SHEET_NAME = "Ayarlar"
HEDEF_SERVET_TL = 2000000 
HEDEF_TARIH = datetime(2026, 2, 28)
FON_VERGI_ORANI = 0.175
MY_FUNDS = ["TLY", "DFI", "TP2", "PHE", "ROF", "PBR"]

# --- FORMATLAMA ---
def format_tr_money(value):
    if pd.isna(value) or value == "" or value == 0: return "-"
    try: return "{:,.2f}".format(float(value)).replace(",", "X").replace(".", ",").replace("X", ".")
    except: return str(value)

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
        ws_prices = sheet.sheet1 
        data_prices = ws_prices.get_all_values()
        if len(data_prices) > 1:
            df_prices = pd.DataFrame(data_prices[1:], columns=data_prices[0])
            df_prices.columns = df_prices.columns.str.strip()
            
            # AGRESİF SAYI ONARICI (Nokta binlik, Virgül ondalık ise düzeltir)
            for col in df_prices.columns:
                if col != "Tarih":
                    df_prices[col] = df_prices[col].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
                    df_prices[col] = pd.to_numeric(df_prices[col], errors='coerce')
            
            df_prices['Tarih'] = pd.to_datetime(df_prices['Tarih'], errors='coerce')
            df_prices = df_prices.dropna(subset=['Tarih']).sort_values("Tarih")
            # Boşlukları (Hafta sonları) bir önceki günle doldur
            df_prices = df_prices.replace(0, np.nan).ffill().bfill().fillna(0)
        else: df_prices = pd.DataFrame()
        
        try:
            ws_trans = sheet.worksheet("Islemler")
            data_trans = ws_trans.get_all_values()
            if len(data_trans) > 1:
                df_trans = pd.DataFrame(data_trans[1:], columns=data_trans[0])
                df_trans.columns = df_trans.columns.str.strip()
                for c in ['Adet', 'Fiyat']:
                    df_trans[c] = df_trans[c].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
                    df_trans[c] = pd.to_numeric(df_trans[c], errors='coerce').fillna(0)
                df_trans['Tarih'] = pd.to_datetime(df_trans['Tarih'], dayfirst=True, errors='coerce')
            else: df_trans = pd.DataFrame()
        except: df_trans = pd.DataFrame()
        
        try:
            ws_conf = sheet.worksheet(CONFIG_SHEET_NAME)
            watchlist = [x for x in ws_conf.col_values(1)[1:] if x]
        except: watchlist = []
        return df_prices, df_trans, watchlist
    except: return pd.DataFrame(), pd.DataFrame(), []

# --- ANALİZ MOTORLARI ---
def calculate_rsi(series, period=14):
    if len(series) < period: return pd.Series([50.0]*len(series))
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))

# DOĞRU % DEĞİŞİM FORMÜLÜ: ((Yeni-Eski)/Eski)
def get_pct_change(df, col, minutes):
    if df.empty or len(df) < 2: return 0.0
    current_price = float(df.iloc[-1][col])
    target_time = df.iloc[-1]['Tarih'] - timedelta(minutes=minutes)
    
    # Geçmişteki en yakın veriyi bul
    past_df = df[df['Tarih'] <= target_time]
    if past_df.empty: 
        old_price = float(df.iloc[0][col])
    else:
        old_price = float(past_df.iloc[-1][col])
        
    if old_price == 0: return 0.0
    # MATEMATİKSEL FORMÜL: ((Son - İlk) / İlk)
    return (current_price - old_price) / old_price

@st.cache_data(ttl=3600)
def get_bist_data(start_date):
    try:
        df_bist = yf.download("XU100.IS", start=start_date, progress=False)
        if not df_bist.empty:
            if isinstance(df_bist.columns, pd.MultiIndex): df_bist = df_bist.xs('Close', level=0, axis=1)
            elif 'Close' in df_bist.columns: df_bist = df_bist['Close']
            return df_bist.iloc[:, 0] if isinstance(df_bist, pd.DataFrame) else df_bist
        return pd.Series()
    except: return pd.Series()

# --- MAPPING & FİYAT ---
def create_asset_mapping(watchlist):
    mapping = {"22 AYAR BİLEZİK (Gr)": "22 AYAR ALTIN ALIŞ", "ATA ALTIN (Adet)": "ATA ALTIN ALIŞ", "ÇEYREK ALTIN (Adet)": "ÇEYREK ALTIN ALIŞ", "TL Bakiye": "NAKİT"}
    for f in MY_FUNDS: mapping[f"{f} FONU"] = f"{f} FİYAT"
    for item in watchlist:
        if ".IS" in item: mapping[f"{item.replace('.IS', '')} (Hisse)"] = f"{item} FİYAT"
    return mapping

def find_smart_price(row, asset_name):
    if asset_name == "TL Bakiye": return 1.0
    sterm = asset_name.replace(" FONU", "").replace(" (Adet)", "").replace(" (Gr)", "").replace(" (Hisse)", "").strip()
    gmap = {"22 AYAR BİLEZİK": "22 AYAR ALTIN ALIŞ", "ATA ALTIN": "ATA ALTIN ALIŞ", "ÇEYREK ALTIN": "ÇEYREK ALTIN ALIŞ"}
    if sterm in gmap: return row.get(gmap[sterm], 0)
    for col in row.index:
        if sterm in col: return row[col]
    return 0.0

# --- HESAPLAMA ---
def calculate_portfolio(df_trans, df_prices):
    port = {}
    if df_prices.empty: return pd.DataFrame(), 0, 0
    last_prices = df_prices.iloc[-1]
    for _, row in df_trans.iterrows():
        var, isl, ad, fi, tur = str(row['Varlık']).strip(), str(row['İşlem']).upper().strip(), float(row['Adet']), float(row['Fiyat']), row['Tür']
        if var not in port: port[var] = {"adet": 0.0, "maliyet": 0.0, "tur": tur}
        if isl == "ALIS":
            port[var]["adet"] += ad
            port[var]["maliyet"] += (ad * fi)
        elif isl == "SATIS":
            if port[var]["adet"] > 0:
                avg = port[var]["maliyet"] / port[var]["adet"]
                port[var]["maliyet"] -= (ad * avg)
                port[var]["adet"] -= ad
            else: port[var]["adet"] -= ad
    table_rows, tot_w, tot_t = [], 0, 0
    for v, d in port.items():
        if d["adet"] <= 0.001: continue
        cp = find_smart_price(last_prices, v)
        gd = d["adet"] * cp
        vergi = (gd - d["maliyet"]) * FON_VERGI_ORANI if "FON" in str(d["tur"]).upper() and gd > d["maliyet"] else 0
        nd = gd - vergi
        kar = nd - d["maliyet"]
        table_rows.append({"Grup": d["tur"], "Varlık": v, "Adet": d["adet"], "Fiyat": cp, "Maliyet": d["maliyet"], "Net Değer": nd, "Net Kâr": kar, "Vergi": vergi})
        tot_w += nd
        tot_t += vergi
    return pd.DataFrame(table_rows), tot_w, tot_t

def prepare_historical_trend(df_prices, df_trans, asset_map):
    if df_prices.empty or df_trans.empty: return pd.DataFrame()
    df_prices, df_trans = df_prices.sort_values("Tarih"), df_trans.sort_values("Tarih")
    
    first_trans_date = df_trans['Tarih'].min()
    current_assets, _, _ = calculate_portfolio(df_trans, df_prices)
    
    trend_data, running_port, trans_idx = [], {}, 0
    for _, pr in df_prices.iterrows():
        cd = pr['Tarih']
        if cd < first_trans_date: continue
        
        while trans_idx < len(df_trans):
            td = df_trans.iloc[trans_idx]['Tarih']
            if td <= cd:
                tr = df_trans.iloc[trans_idx]
                v, isl, ad = str(tr['Varlık']).strip(), str(tr['İşlem']).upper().strip(), float(tr['Adet'])
                running_port[v] = running_port.get(v, 0.0) + (ad if isl == "ALIS" else -ad)
                trans_idx += 1
            else: break
            
        tot = 0
        # 1.8M FIX: Eğer o gün için adet yoksa (geçmiş veri), bugünkü güncel bakiyeyi temel al
        for _, asset in current_assets.iterrows():
            v = asset['Varlık']
            qty = running_port.get(v, asset['Adet']) 
            if qty <= 0.001: continue
            tot += (qty * find_smart_price(pr, v))
        if tot > 0: trend_data.append({"Tarih": cd, "Toplam Servet": tot})
    return pd.DataFrame(trend_data)

# --- ANA PROGRAM ---
def main():
    df_prices, df_trans, watchlist = load_data()
    ASSET_MAPPING = create_asset_mapping(watchlist)
    
    with st.sidebar:
        st.markdown("<h1 style='text-align: center; color: #4e8cff;'>💎 Varlık Paneli</h1>", unsafe_allow_html=True)
        if not df_prices.empty:
            last = df_prices.iloc[-1]
            st.markdown(f'<div style="display: flex; gap: 10px; margin-bottom: 20px;"><div class="currency-card" style="flex: 1;"><div class="currency-title">🇺🇸 USD</div><div class="currency-value">{last["DOLAR KURU"]:.2f} ₺</div></div><div class="currency-card" style="flex: 1;"><div class="currency-title">🇪🇺 EUR</div><div class="currency-value">{last["EURO KURU"]:.2f} ₺</div></div></div>', unsafe_allow_html=True)
        page = st.radio("Menü", ["Portföyüm", "Piyasa Takip"], label_visibility="collapsed")
        if st.button("🔄 Verileri Yenile", use_container_width=True): st.cache_data.clear(); st.rerun()
        with st.expander("➕ İşlem Ekle"):
            with st.form("add"):
                f_date, f_tur = st.date_input("Tarih", datetime.now()), st.selectbox("Tür", ["ALTIN", "FON", "HİSSE", "NAKİT", "DÖVİZ"])
                f_varlik = st.selectbox("Varlık", ["TLY FONU", "DFI FONU", "TP2 FONU", "TL Bakiye", "22 AYAR BİLEZİK (Gr)", "ATA ALTIN (Adet)"] + [x + " (Hisse)" for x in watchlist if ".IS" in x])
                f_islem, f_adet, f_fiyat = st.selectbox("İşlem", ["ALIS", "SATIS"]), st.number_input("Adet", 0.0, step=0.01), st.number_input("Fiyat", 0.0, step=0.01)
                if st.form_submit_button("Kaydet", use_container_width=True):
                    try:
                        client = get_client()
                        sheet = client.open(SHEET_NAME); ws = sheet.worksheet("Islemler")
                        row = [f_date.strftime("%d.%m.%Y"), f_tur, f_varlik, f_islem, str(f_adet).replace(".", ","), str(f_fiyat).replace(".", ",")]
                        ws.append_row(row, value_input_option='USER_ENTERED')
                        st.success(f"✅ Eklendi: {f_varlik}"); time.sleep(1); st.cache_data.clear(); st.rerun()
                    except Exception as e: st.error(f"Hata: {e}")
        with st.expander("🛠️ Takip Listesi"):
            ns = st.text_input("Sembol (Örn: SASA.IS)")
            if st.button("Ekle", use_container_width=True): 
                try:
                    client = get_client()
                    sheet = client.open(SHEET_NAME)
                    try: ws = sheet.worksheet(CONFIG_SHEET_NAME)
                    except: ws = sheet.add_worksheet(CONFIG_SHEET_NAME, 100, 5); ws.append_row(["Sembol"])
                    if ns not in ws.col_values(1): ws.append_row([ns]); st.success("Eklendi"); time.sleep(1); st.cache_data.clear(); st.rerun()
                except: pass

    if page == "Portföyüm" and not df_trans.empty and not df_prices.empty:
        df_view, tot_w, tot_t = calculate_portfolio(df_trans, df_prices)
        st.metric("Toplam Varlık", f"{format_tr_money(tot_w)} TL", f"Vergi: -{format_tr_money(tot_t)} TL", delta_color="inverse")
        
        st.subheader("📈 Servet Değişimi")
        df_trend = prepare_historical_trend(df_prices, df_trans, ASSET_MAPPING)
        if not df_trend.empty:
            fig = px.area(df_trend, x="Tarih", y="Toplam Servet")
            # Grafiği doğru seviyede (1.8M) tutmak için y ekseni limitlerini otomatik ayarla
            fig.update_layout(yaxis_range=[df_trend["Toplam Servet"].min()*0.98, df_trend["Toplam Servet"].max()*1.02], height=400, hovermode="x unified")
            fig.update_traces(line_color='#00FF00', fillcolor='rgba(0, 255, 0, 0.1)')
            st.plotly_chart(fig, use_container_width=True)

        st.subheader("📋 Varlık Detayı")
        st.dataframe(df_view.style.format({"Adet": "{:,.0f}", "Fiyat": "{:,.4f}", "Maliyet": "{:,.2f}", "Net Değer": "{:,.2f}", "Net Kâr": "{:,.2f}", "Vergi": "{:,.2f}"}), use_container_width=True, hide_index=True)

    elif page == "Piyasa Takip" and not df_prices.empty:
        st.markdown("## 🌍 Detaylı Piyasa Analizi")
        # 3 AY ve 6 AY Sütunları eklendi
        ivs = {"1 Gün": 1440, "1 Hafta": 10080, "1 Ay": 43200, "3 Ay": 129600, "6 Ay": 259200, "1 Yıl": 525600}
        market_data = []
        for col in df_prices.columns:
            if col in ["Tarih", "D"]: continue
            if any(x in col for x in ["FİYAT", "ALTIN", "DOLAR", "KURU"]):
                series = df_prices[col].replace(0, np.nan).ffill()
                if series.empty: continue
                row = {"Varlık": col.replace(" FİYAT", ""), "Fiyat": series.iloc[-1], "RSI": calculate_rsi(series).iloc[-1], "Trend": series.tail(30).tolist()}
                for k, v in ivs.items(): row[f"{k} Değişim"] = get_pct_change(df_prices, col, v)
                market_data.append(row)
        
        df_m = pd.DataFrame(market_data)
        
        # YEŞİL/KIRMIZI BOLD FONKSİYONU
        def color_change(val):
            color = '#00FF00' if val > 0.0001 else '#FF4B4B' if val < -0.0001 else 'white'
            return f'color: {color}; font-weight: bold'

        col_config = {"Varlık": st.column_config.TextColumn("Varlık", width="small"), "Fiyat": st.column_config.NumberColumn("Fiyat", format="%.4f TL"), "RSI": st.column_config.NumberColumn("RSI", format="%.0f"), "Trend": st.column_config.LineChartColumn("Trend", y_min=0, width="small")}
        for k in ivs.keys(): col_config[f"{k} Değişim"] = st.column_config.NumberColumn(f"{k} Değişim", format="%.2f %%")
        
        t1, t2, t3 = st.tabs(["📈 Hisseler", "📊 Fonlar", "🥇 Altın/Döviz"])
        def show_table(keyword):
            if keyword == "Hisse": df_s = df_m[df_m["Varlık"].str.contains(".IS", na=False)]
            elif keyword == "Fon": df_s = df_m[df_m["Varlık"].apply(lambda x: len(str(x))<=4 and "." not in str(x))]
            else: df_s = df_m[df_m["Varlık"].str.contains("ALTIN|DOLAR|EURO|KURU", na=False)]
            if not df_s.empty:
                # Sadece değişim sütunlarına stil uygula
                st.dataframe(df_s.style.applymap(color_change, subset=[c for c in df_s.columns if "Değişim" in c]), column_config=col_config, use_container_width=True, hide_index=True)
        with t1: show_table("Hisse")
        with t2: show_table("Fon")
        with t3: show_table("Emtia")

if __name__ == "__main__":
    main()
