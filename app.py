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
st.set_page_config(page_title="Varlık Paneli", page_icon="💎", layout="wide")

# --- ÖZEL CSS ---
st.markdown("""
<style>
    [data-testid="stMetricValue"] { font-size: 26px; font-weight: bold; }
    .currency-card { background-color: #262730; padding: 10px; border-radius: 10px; border: 1px solid #41444b; text-align: center; }
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
    if pd.isna(value) or value == 0: return "-"
    try: return "{:,.2f}".format(float(value)).replace(",", "X").replace(".", ",").replace("X", ".")
    except: return str(value)

def format_tr_percent(value):
    if pd.isna(value): return "-"
    return "%" + "{:,.2f}".format(float(value)).replace(".", ",")

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
        
        # Fiyat Verileri Onarma
        ws_prices = sheet.sheet1 
        data_prices = ws_prices.get_all_values()
        df_prices = pd.DataFrame(data_prices[1:], columns=data_prices[0])
        df_prices.columns = df_prices.columns.str.strip()
        
        # Tarih ve Sayı Temizliği
        df_prices['Tarih'] = pd.to_datetime(df_prices['Tarih'], errors='coerce')
        for col in df_prices.columns:
            if col != "Tarih":
                df_prices[col] = df_prices[col].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
                df_prices[col] = pd.to_numeric(df_prices[col], errors='coerce')
        
        df_prices = df_prices.dropna(subset=['Tarih']).sort_values("Tarih").drop_duplicates(subset=['Tarih'], keep='last')
        df_prices = df_prices.ffill().fillna(0)

        # İşlemler
        ws_trans = sheet.worksheet("Islemler")
        data_trans = ws_trans.get_all_values()
        df_trans = pd.DataFrame(data_trans[1:], columns=data_trans[0])
        df_trans['Adet'] = pd.to_numeric(df_trans['Adet'].astype(str).str.replace(",", "."), errors='coerce').fillna(0)
        df_trans['Fiyat'] = pd.to_numeric(df_trans['Fiyat'].astype(str).str.replace(",", "."), errors='coerce').fillna(0)
        df_trans['Tarih'] = pd.to_datetime(df_trans['Tarih'], dayfirst=True, errors='coerce')

        # İzleme Listesi
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

def get_pct_change(df, col, days):
    if df.empty or len(df) < 5: return 0.0
    curr = df.iloc[-1][col]
    target_dt = df.iloc[-1]['Tarih'] - timedelta(days=days)
    old_df = df[df['Tarih'] <= target_dt]
    old = old_df.iloc[-1][col] if not old_df.empty else df.iloc[0][col]
    return (curr - old) / old if old != 0 else 0

@st.cache_data(ttl=3600)
def get_bist_data(start_date):
    try:
        df = yf.download("XU100.IS", start=start_date, progress=False)
        return df['Close'].iloc[:, 0] if isinstance(df['Close'], pd.DataFrame) else df['Close']
    except: return pd.Series()

# --- HESAPLAMALAR ---
def calculate_portfolio(df_trans, df_prices):
    port = {}
    last_p = df_prices.iloc[-1]
    for _, row in df_trans.iterrows():
        v, isl, ad, fi, tur = str(row['Varlık']).strip(), str(row['İşlem']).upper().strip(), row['Adet'], row['Fiyat'], row['Tür']
        if v not in port: port[v] = {"adet": 0.0, "maliyet": 0.0, "tur": tur}
        if isl == "ALIS":
            port[v]["adet"] += ad
            port[v]["maliyet"] += (ad * fi)
        else:
            if port[v]["adet"] > 0:
                avg = port[v]["maliyet"] / port[v]["adet"]
                port[v]["maliyet"] -= (ad * avg)
                port[v]["adet"] -= ad
    
    rows, tot_w, tot_t = [], 0, 0
    for v, d in port.items():
        if d["adet"] <= 0: continue
        cp = 1.0 if v == "TL Bakiye" else 0
        sv = v.replace(" FONU", "").replace(" (Gr)", "").replace(" (Adet)", "").replace(" (Hisse)", "").strip()
        for col in df_prices.columns:
            if sv in col: cp = last_p[col]; break
        
        val = d["adet"] * cp
        vergi = (val - d["maliyet"]) * FON_VERGI_ORANI if "FON" in str(d["tur"]).upper() and val > d["maliyet"] else 0
        nd = val - vergi
        rows.append({"Grup": d["tur"], "Varlık": v, "Adet": d["adet"], "Fiyat": cp, "Maliyet": d["maliyet"], "Net Değer": nd, "Net Kâr": nd - d["maliyet"], "Vergi": vergi})
        tot_w += nd
        tot_t += vergi
    return pd.DataFrame(rows), tot_w, tot_t

def prepare_historical_trend(df_prices, df_trans):
    # Günlük gruplama
    df_prices['D'] = df_prices['Tarih'].dt.date
    daily = df_prices.groupby('D').last().reset_index()
    daily['Tarih'] = pd.to_datetime(daily['D'])
    
    # Bugün elinde olan adetleri baz al (Geçmişte adet verisi yoksa 1.8M göstermek için)
    current_assets, _, _ = calculate_portfolio(df_trans, df_prices)
    
    trend = []
    for _, row in daily.iterrows():
        day_total = 0
        for _, asset in current_assets.iterrows():
            v = asset['Varlık']
            qty = asset['Adet']
            p = 1.0 if v == "TL Bakiye" else 0
            sv = v.replace(" FONU", "").replace(" (Gr)", "").replace(" (Adet)", "").replace(" (Hisse)", "").strip()
            for col in daily.columns:
                if sv in col: p = row[col]; break
            day_total += (qty * p)
        trend.append({"Tarih": row['Tarih'], "Toplam Servet": day_total})
    return pd.DataFrame(trend)

def render_rebalance_assistant(df_view):
    st.subheader("⚖️ Portföy Rebalans Asistanı")
    df_grp = df_view.groupby("Grup")["Net Değer"].sum().reset_index()
    total = df_grp["Net Değer"].sum()
    cols = st.columns(len(df_grp))
    target = {}
    for i, row in df_grp.iterrows():
        target[row["Grup"]] = cols[i].number_input(f"Hedef % ({row['Grup']})", 0, 100, int(100/len(df_grp)))
    
    analysis = []
    for _, row in df_grp.iterrows():
        fark = ((total * target[row["Grup"]]) / 100) - row["Net Değer"]
        aksiyon = f"✅ {format_tr_money(fark)} TL AL" if fark > 1000 else f"🚨 {format_tr_money(abs(fark))} TL SAT" if fark < -1000 else "🆗 Dengeli"
        analysis.append({"Grup": row["Grup"], "Mevcut Oran": f"%{(row['Net Değer']/total*100):.1f}", "Hedef Oran": f"%{target[row['Grup']]:.1f}", "Aksiyon": aksiyon})
    st.dataframe(pd.DataFrame(analysis), use_container_width=True, hide_index=True)

# --- ANA PROGRAM ---
def main():
    df_prices, df_trans, watchlist = load_data()
    
    with st.sidebar:
        st.header("💎 Varlık Paneli")
        if not df_prices.empty:
            last = df_prices.iloc[-1]
            st.write(f"🇺🇸 USD: **{last['DOLAR KURU']:.2f} ₺**")
            st.write(f"🇪🇺 EUR: **{last['EURO KURU']:.2f} ₺**")
        page = st.radio("Menü", ["Portföyüm", "Piyasa Takip"])
        if st.button("🔄 Verileri Yenile"): st.cache_data.clear(); st.rerun()

    if page == "Portföyüm":
        df_view, tot_w, tot_t = calculate_portfolio(df_trans, df_prices)
        df_trend = prepare_historical_trend(df_prices, df_trans)
        
        tab1, tab2, tab3 = st.tabs(["🇹🇷 TL Görünüm", "🇺🇸 USD Görünüm", "🇪🇺 EUR Görünüm"])
        with tab1:
            c1, c2, c3 = st.columns(3)
            c1.metric("Toplam Varlık", f"{format_tr_money(tot_w)} TL", f"Vergi: -{format_tr_money(tot_t)} TL", delta_color="inverse")
            c2.metric("Net Kâr", f"{format_tr_money(df_view['Net Kâr'].sum())} TL")
            c3.metric("Kâr Oranı", f"%{format_tr_money(df_view['Net Kâr'].sum()/df_view['Maliyet'].sum()*100 if df_view['Maliyet'].sum()>0 else 0)}")
            
            st.subheader("📈 Servet Değişimi (1 Yıllık)")
            fig = px.area(df_trend, x="Tarih", y="Toplam Servet")
            fig.update_layout(yaxis_range=[df_trend["Toplam Servet"].min()*0.98, df_trend["Toplam Servet"].max()*1.02], height=400)
            st.plotly_chart(fig, use_container_width=True)
            
            st.subheader("📋 Varlık Detayı")
            st.dataframe(df_view.style.format({"Fiyat": "{:,.4f}", "Net Değer": "{:,.2f}", "Net Kâr": "{:,.2f}"}), use_container_width=True, hide_index=True)
            
            st.divider()
            render_rebalance_assistant(df_view)

    elif page == "Piyasa Takip":
        st.subheader("🌍 Detaylı Piyasa Analizi")
        ivs = {"1 Gün": 1, "1 Hafta": 7, "1 Ay": 30, "6 Ay": 180, "1 Yıl": 365}
        m_data = []
        for col in df_prices.columns:
            if any(x in col for x in ["FİYAT", "ALTIN", "KURU"]):
                s = df_prices[col].replace(0, np.nan).ffill()
                row = {"Varlık": col.replace(" FİYAT", ""), "Fiyat": s.iloc[-1], "RSI": calculate_rsi(s).iloc[-1], "Trend": s.tail(30).tolist()}
                for k, v in ivs.items(): row[k] = get_pct_change(df_prices, col, v)
                m_data.append(row)
        st.dataframe(pd.DataFrame(m_data).style.format({"Fiyat": "{:,.2f}", "RSI": "{:,.0f}", "1 Gün": "{:.2%}", "1 Hafta": "{:.2%}", "1 Ay": "{:.2%}", "6 Ay": "{:.2%}", "1 Yıl": "{:.2%}"}), column_config={"Trend": st.column_config.LineChartColumn("30 Kayıt")}, use_container_width=True, hide_index=True)

if __name__ == "__main__":
    main()
