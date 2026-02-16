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
    .currency-value { font-size: 22px; font-weight: bold; color: #ffffff; }
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

# --- YARDIMCI FONKSİYONLAR ---
def clean_numeric(value):
    if pd.isna(value) or value == "" or value is None: return 0.0
    s = str(value).strip()
    if "." in s and "," in s: s = s.replace(".", "").replace(",", ".")
    elif "," in s: s = s.replace(",", ".")
    try: return float(s)
    except: return 0.0

def format_tr_money(value):
    if pd.isna(value) or value == 0: return "-"
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
            for col in df_prices.columns:
                if col != "Tarih": df_prices[col] = df_prices[col].apply(clean_numeric)
            df_prices['Tarih'] = pd.to_datetime(df_prices['Tarih'], errors='coerce')
            df_prices = df_prices.dropna(subset=['Tarih']).sort_values("Tarih").ffill().fillna(0)
        else: df_prices = pd.DataFrame()
        
        ws_trans = sheet.worksheet("Islemler")
        data_trans = ws_trans.get_all_values()
        df_trans = pd.DataFrame(data_trans[1:], columns=data_trans[0])
        df_trans['Adet'] = df_trans['Adet'].apply(clean_numeric)
        df_trans['Fiyat'] = df_trans['Fiyat'].apply(clean_numeric)
        df_trans['Tarih'] = pd.to_datetime(df_trans['Tarih'], dayfirst=True, errors='coerce')
        
        try:
            ws_conf = sheet.worksheet(CONFIG_SHEET_NAME)
            watchlist = [x for x in ws_conf.col_values(1)[1:] if x]
        except: watchlist = []
        return df_prices, df_trans, watchlist
    except: return pd.DataFrame(), pd.DataFrame(), []

def find_smart_price(row, asset_name):
    if "TL Bakiye" in asset_name: return 1.0
    sterm = asset_name.replace(" (Adet)", "").replace(" (Gr)", "").replace(" (Hisse)", "").replace(" FONU", "").strip()
    gmap = {"22 AYAR BİLEZİK": "22 AYAR ALTIN ALIŞ", "ATA ALTIN": "ATA ALTIN ALIŞ", "ÇEYREK ALTIN": "ÇEYREK ALTIN ALIŞ"}
    if sterm in gmap: return row.get(gmap[sterm], 0)
    for col in row.index:
        if sterm in col: return row[col]
    return 0.0

def calculate_portfolio(df_trans, df_prices):
    if df_trans.empty or df_prices.empty: return pd.DataFrame(), 0, 0
    port = {}
    last_prices = df_prices.iloc[-1]
    for _, row in df_trans.iterrows():
        v, isl, ad, fi, tur = str(row['Varlık']).strip(), str(row['İşlem']).upper().strip(), row['Adet'], row['Fiyat'], row['Tür']
        if v not in port: port[v] = {"adet": 0.0, "maliyet": 0.0, "tur": tur}
        if isl == "ALIS":
            port[v]["adet"] += ad
            port[v]["maliyet"] += (ad * fi)
        else:
            if port[v]["adet"] > 0:
                avg = port[v]["maliyet"] / port[v]["adet"]
                port[v]["maliyet"] -= (ad * avg); port[v]["adet"] -= ad
            else: port[v]["adet"] -= ad
    
    rows, tot_w, tot_t = [], 0, 0
    for v, d in port.items():
        if d["adet"] <= 0.001: continue
        cp = find_smart_price(last_prices, v)
        val = d["adet"] * cp
        vergi = (val - d["maliyet"]) * FON_VERGI_ORANI if "FON" in str(d["tur"]).upper() and val > d["maliyet"] else 0
        nd = val - vergi
        rows.append({"Grup": d["tur"], "Varlık": v, "Adet": d["adet"], "Fiyat": cp, "Maliyet": d["maliyet"], "Net Değer": nd, "Net Kâr": nd - d["maliyet"], "Vergi": vergi})
        tot_w += nd; tot_t += vergi
    return pd.DataFrame(rows), tot_w, tot_t

def prepare_historical_trend(df_prices, df_trans, rate=1.0):
    if df_prices.empty or df_trans.empty: return pd.DataFrame()
    df_prices, df_trans = df_prices.sort_values("Tarih"), df_trans.sort_values("Tarih")
    current_port_table, _, _ = calculate_portfolio(df_trans, df_prices)
    running_port, trend_data, trans_idx = {}, [], 0
    first_date = df_trans['Tarih'].min()
    for _, pr in df_prices.iterrows():
        cd = pr['Tarih']
        if cd < first_date: continue
        while trans_idx < len(df_trans):
            td = df_trans.iloc[trans_idx]['Tarih']
            if td <= cd:
                tr = df_trans.iloc[trans_idx]
                running_port[tr['Varlık']] = running_port.get(tr['Varlık'], 0.0) + (tr['Adet'] if tr['İşlem'].upper() == "ALIS" else -tr['Adet'])
                trans_idx += 1
            else: break
        tot = 0
        for _, asset in current_port_table.iterrows():
            v = asset['Varlık']
            qty = running_port.get(v, asset['Adet'])
            tot += (qty * find_smart_price(pr, v))
        if tot > 0: trend_data.append({"Tarih": cd, "Toplam Servet": tot/rate})
    return pd.DataFrame(trend_data)

# --- REBALANS ASİSTANI ---
def render_rebalance_assistant(df_view):
    st.subheader("⚖️ Portföy Rebalans Asistanı")
    df_grp = df_view.groupby("Grup")["Net Değer"].sum().reset_index()
    total_val = df_grp["Net Değer"].sum()
    cols = st.columns(len(df_grp))
    target_ratios = {}
    for i, row in df_grp.iterrows():
        target_ratios[row["Grup"]] = cols[i].number_input(f"Hedef % ({row['Grup']})", 0, 100, int(100/len(df_grp)), key=f"reb_val_{i}")
    analysis = []
    for i, row in df_grp.iterrows():
        fark = ((total_val * target_ratios[row["Grup"]]) / 100) - row["Net Değer"]
        aks = f"✅ {format_tr_money(fark)} TL AL" if fark > 1000 else f"🚨 {format_tr_money(abs(fark))} TL SAT" if fark < -1000 else "🆗 Dengeli"
        analysis.append({"Grup": row["Grup"], "Mevcut Değer": row["Net Değer"], "Mevcut Oran": f"%{(row['Net Değer']/total_val*100):.1f}", "Hedef Oran": f"%{target_ratios[row['Grup']]:.1f}", "Aksiyon": aks})
    st.dataframe(pd.DataFrame(analysis), use_container_width=True, hide_index=True)

# --- ANA PROGRAM ---
def main():
    df_prices, df_trans, watchlist = load_data()
    if df_prices.empty: st.stop()
    
    with st.sidebar:
        st.markdown("<h1 style='text-align: center; color: #4e8cff;'>💎 Varlık Paneli</h1>", unsafe_allow_html=True)
        last = df_prices.iloc[-1]
        usd, eur = last["DOLAR KURU"], last["EURO KURU"]
        st.markdown(f'<div class="currency-card"><div class="currency-title">🇺🇸 USD</div><div class="currency-value">{usd:.2f} ₺</div></div><div class="currency-card"><div class="currency-title">🇪🇺 EUR</div><div class="currency-value">{eur:.2f} ₺</div></div>', unsafe_allow_html=True)
        page = st.radio("Menü", ["Portföyüm", "Piyasa Takip"], label_visibility="collapsed")
        if st.button("🔄 Verileri Yenile", use_container_width=True): st.cache_data.clear(); st.rerun()
        
        with st.expander("➕ İşlem Ekle"):
            with st.form("add_trans"):
                f_date = st.date_input("Tarih", datetime.now())
                f_tur = st.selectbox("Tür", ["ALTIN", "FON", "HİSSE", "NAKİT", "DÖVİZ"])
                f_varlik = st.selectbox("Varlık", ["TLY FONU", "DFI FONU", "TP2 FONU", "TL Bakiye", "22 AYAR BİLEZİK (Gr)", "ATA ALTIN (Adet)"] + [x + " (Hisse)" for x in watchlist])
                f_islem = st.selectbox("İşlem", ["ALIS", "SATIS"])
                f_adet = st.number_input("Adet", 0.0, step=0.01)
                f_fiyat = st.number_input("Fiyat", 0.0, step=0.01)
                if st.form_submit_button("Kaydet"):
                    try:
                        client = get_client(); sheet = client.open(SHEET_NAME); ws = sheet.worksheet("Islemler")
                        ws.append_row([f_date.strftime("%d.%m.%Y"), f_tur, f_varlik, f_islem, str(f_adet).replace(".", ","), str(f_fiyat).replace(".", ",")], value_input_option='USER_ENTERED')
                        st.success("✅ Eklendi"); time.sleep(1); st.cache_data.clear(); st.rerun()
                    except: st.error("Hata!")

        with st.expander("🛠️ Takip Listesi"):
            ns = st.text_input("Hisse Sembolü (Örn: SASA.IS)")
            if st.button("Takibe Ekle", use_container_width=True): 
                try:
                    client = get_client(); sheet = client.open(SHEET_NAME); ws = sheet.worksheet(CONFIG_SHEET_NAME)
                    if ns not in ws.col_values(1): ws.append_row([ns]); st.success("Eklendi"); time.sleep(1); st.cache_data.clear(); st.rerun()
                except: pass

    if page == "Portföyüm":
        df_view, tot_w, tot_t = calculate_portfolio(df_trans, df_prices)
        tabs = st.tabs(["🇹🇷 TL Görünüm", "🇺🇸 USD Görünüm", "🇪🇺 EUR Görünüm"])
        for i, (tab, curr, rate) in enumerate(zip(tabs, ["TL", "$", "€"], [1.0, usd, eur])):
            with tab:
                c1, c2, c3 = st.columns(3)
                c1.metric("Toplam Varlık", f"{format_tr_money(tot_w/rate)} {curr}", f"Vergi: -{format_tr_money(tot_t/rate)}")
                c2.metric("Net Kâr", f"{format_tr_money(df_view['Net Kâr'].sum()/rate)} {curr}")
                c3.metric("Kâr Oranı", f"%{((df_view['Net Kâr'].sum()/df_view['Maliyet'].sum())*100) if df_view['Maliyet'].sum()>0 else 0:,.2f}")
                
                if curr == "TL":
                    st.divider()
                    st.subheader(f"🎯 Hedef: {format_tr_money(HEDEF_SERVET_TL)} TL")
                    st.progress(min(tot_w/HEDEF_SERVET_TL, 1.0))
                    h1, h2 = st.columns(2)
                    h1.write(f"🏁 Kalan: **{format_tr_money(HEDEF_SERVET_TL - tot_w)} TL** ({((tot_w/HEDEF_SERVET_TL)*100):.1f}%)")
                    h2.write(f"⏳ Bitiş: **{HEDEF_TARIH.strftime('%d.%m.%Y')}** ({(HEDEF_TARIH - datetime.now()).days} Gün)")
                
                st.subheader("📈 Servet Değişimi")
                df_trend = prepare_historical_trend(df_prices, df_trans, rate)
                if not df_trend.empty:
                    fig_t = px.area(df_trend, x="Tarih", y="Toplam Servet")
                    fig_t.update_layout(yaxis_range=[df_trend["Toplam Servet"].min()*0.98, df_trend["Toplam Servet"].max()*1.02], height=400)
                    st.plotly_chart(fig_t, use_container_width=True, key=f"trend_chart_{i}")
                
                col1, col2 = st.columns(2)
                with col1:
                    st.subheader("🍕 Varlık Dağılımı")
                    view_mode = st.radio("Görünüm", ["Ana Gruplar", "Varlık Bazlı (Kırılımlı)"], horizontal=True, key=f"v_mode_{i}")
                    g_col = "Grup" if view_mode == "Ana Gruplar" else "Varlık"
                    df_p = df_view.groupby(g_col)["Net Değer"].sum().reset_index()
                    c_map = {"ALTIN": "#FFD700", "FON": "#2ca02c", "NAKİT": "#1f77b4", "HİSSE": "#d62728"}
                    fig_p = px.pie(df_p, values="Net Değer", names=g_col, hole=0.4, color=g_col if view_mode == "Ana Gruplar" else None, color_discrete_map=c_map if view_mode == "Ana Gruplar" else None)
                    st.plotly_chart(fig_p, use_container_width=True, key=f"pie_chart_{i}")
                with col2:
                    st.subheader("📊 Kâr/Zarar Durumu")
                    fig_b = go.Figure([go.Bar(name='Net Değer', x=df_view['Varlık'], y=df_view['Net Değer']/rate, marker_color='forestgreen')])
                    st.plotly_chart(fig_b, use_container_width=True, key=f"bar_chart_{i}")

                st.subheader("📋 Detaylı Varlık Listesi")
                df_show = df_view.copy()
                for c in ["Fiyat", "Maliyet", "Net Değer", "Net Kâr", "Vergi"]: df_show[c] = df_show[c] / rate
                df_show["Kâr %"] = (df_show["Net Kâr"] / df_show["Maliyet"]) * 100
                st.dataframe(df_show.style.format({"Net Değer": "{:,.2f}", "Kâr %": "%{:,.2f}"}), use_container_width=True, hide_index=True)

                if curr == "TL":
                    st.divider()
                    st.subheader("🥇 Kıymetli Metal Alım-Satım Farkları")
                    gm = st.columns(4)
                    for idx, (n, k) in enumerate([("Gram", "GRAM ALTIN"), ("Ata", "ATA ALTIN"), ("22 Ayar", "22 AYAR ALTIN"), ("Çeyrek", "ÇEYREK ALTIN")]):
                        s, a = last.get(f"{k} SATIŞ", 0), last.get(f"{k} ALIŞ", 0)
                        diff = s - a
                        p_diff = (diff / s) * 100 if s > 0 else 0
                        gm[idx].metric(n, f"{s:,.2f} ₺", f"Makas: {diff:,.2f} ₺ (%{p_diff:.2f})")
                    st.divider()
                    render_rebalance_assistant(df_view)

    elif page == "Piyasa Takip":
        st.markdown("## 🌍 Detaylı Piyasa Analizi")
        # Piyasa takip kodu burada devam eder...
