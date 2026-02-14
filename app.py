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
    st.error("⚠️ 'yfinance' kütüphanesi eksik! Lütfen requirements.txt dosyasına 'yfinance' ekleyin.")
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
    .currency-title { font-size: 14px; color: #b0b3b8; margin-bottom: 5px; }
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

# --- FORMATLAMA ---
def format_tr_money(value):
    if pd.isna(value) or value == "" or value == 0: return "-"
    try: return "{:,.2f}".format(float(value)).replace(",", "X").replace(".", ",").replace("X", ".")
    except: return str(value)

def format_tr_percent(value):
    if pd.isna(value) or value == "": return "-"
    try:
        val = float(value)
        return "%" + "{:,.2f}".format(val).replace(".", ",")
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
        ws_prices = sheet.sheet1 
        data_prices = ws_prices.get_all_values()
        if len(data_prices) > 1:
            df_prices = pd.DataFrame(data_prices[1:], columns=data_prices[0])
            df_prices.columns = df_prices.columns.str.strip()
            for col in df_prices.columns:
                if col != "Tarih":
                    df_prices[col] = df_prices[col].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
                    df_prices[col] = pd.to_numeric(df_prices[col], errors='coerce')
            df_prices['Tarih'] = pd.to_datetime(df_prices['Tarih'], errors='coerce')
            df_prices = df_prices.dropna(subset=['Tarih']).sort_values("Tarih")
            df_prices = df_prices.ffill().fillna(0)
        else: df_prices = pd.DataFrame()
        
        try:
            ws_trans = sheet.worksheet("Islemler")
            data_trans = ws_trans.get_all_values()
            if len(data_trans) > 1:
                df_trans = pd.DataFrame(data_trans[1:], columns=data_trans[0])
                df_trans.columns = df_trans.columns.str.strip()
                df_trans['Adet'] = df_trans['Adet'].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
                df_trans['Adet'] = pd.to_numeric(df_trans['Adet'], errors='coerce').fillna(0)
                df_trans['Fiyat'] = df_trans['Fiyat'].astype(str).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
                df_trans['Fiyat'] = pd.to_numeric(df_trans['Fiyat'], errors='coerce').fillna(0)
                df_trans['Tarih'] = pd.to_datetime(df_trans['Tarih'], dayfirst=True, errors='coerce')
                df_trans = df_trans.dropna(subset=['Tarih'])
            else: df_trans = pd.DataFrame()
        except: df_trans = pd.DataFrame()
        
        try:
            ws_conf = sheet.worksheet(CONFIG_SHEET_NAME)
            vals = ws_conf.col_values(1)
            watchlist = [x for x in vals[1:] if x]
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

def get_pct_change(df, col, minutes):
    if df.empty or len(df) < 2: return 0.0
    current_price = df.iloc[-1][col]
    target_time = df.iloc[-1]['Tarih'] - timedelta(minutes=minutes)
    past_df = df[df['Tarih'] <= target_time]
    old_price = past_df.iloc[-1][col] if not past_df.empty else df.iloc[0][col]
    if old_price == 0: return 0.0
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
    prev_prices = df_prices.iloc[-2] if len(df_prices) > 1 else last_prices
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
        if cp == 0: cp = find_smart_price(prev_prices, v)
        gd = d["adet"] * cp
        vergi = (gd - d["maliyet"]) * FON_VERGI_ORANI if "FON" in str(d["tur"]).upper() and gd > d["maliyet"] else 0
        nd = gd - vergi
        kar = nd - d["maliyet"]
        table_rows.append({"Grup": d["tur"], "Varlık": v, "Adet": d["adet"], "Fiyat": cp, "Maliyet": d["maliyet"], "Net Değer": nd, "Net Kâr": kar, "Vergi": vergi})
        tot_w += nd
        tot_t += vergi
    return pd.DataFrame(table_rows), tot_w, tot_t

def prepare_historical_trend(df_prices, df_trans, asset_map, rate=1.0):
    if df_prices.empty or df_trans.empty: return pd.DataFrame()
    trend_data, running_port, trans_idx = [], {}, 0
    df_prices = df_prices.sort_values("Tarih")
    df_trans = df_trans.sort_values("Tarih")
    
    # KRİTİK DÜZELTME: Grafik sadece ilk işlem tarihinden başlar
    first_trans_date = df_trans['Tarih'].min()
    
    for _, pr in df_prices.iterrows():
        cd = pr['Tarih']
        if cd < first_trans_date: continue
        
        while trans_idx < len(df_trans):
            td = df_trans.iloc[trans_idx]['Tarih']
            if td <= cd:
                tr = df_trans.iloc[trans_idx]
                v, isl, ad = str(tr['Varlık']).strip(), str(tr['İşlem']).upper().strip(), float(tr['Adet'])
                cq = running_port.get(v, 0.0)
                running_port[v] = cq + ad if isl == "ALIS" else cq - ad
                trans_idx += 1
            else: break
        tot = 0
        for v, qty in running_port.items():
            if qty <= 0.001: continue
            p = find_smart_price(pr, v)
            tot += (qty * p)
        if tot > 0: trend_data.append({"Tarih": cd, "Toplam Servet": tot/rate})
    return pd.DataFrame(trend_data)

# --- KIYASLAMA GRAFİĞİ ---
def render_benchmark_chart(df_trend, df_prices):
    if df_trend.empty or df_prices.empty: return
    df_port = df_trend.set_index("Tarih").resample("D").last().dropna()
    df_market = df_prices.set_index("Tarih").resample("D").last().dropna()
    if df_port.empty: return
    bist = get_bist_data(df_port.index.min())
    df_b = pd.DataFrame(index=df_port.index)
    df_b["Portföyüm"] = df_port["Toplam Servet"]
    if not bist.empty:
        try: bist.index = bist.index.tz_localize(None)
        except: pass
        df_b["BIST 100"] = bist.reindex(df_b.index, method='ffill')
    df_b["Dolar"], df_b["Gram Altın"] = df_market["DOLAR KURU"].reindex(df_b.index, method='ffill'), df_market["GRAM ALTIN SATIŞ"].reindex(df_b.index, method='ffill')
    df_n = df_b.copy()
    for col in df_n.columns:
        fv = df_n[col].iloc[0]
        df_n[col] = (df_n[col] / fv - 1) * 100 if fv > 0 else 0
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df_n.index, y=df_n["Portföyüm"], name='💎 Portföyüm', line=dict(color='#2E8B57', width=4)))
    if "BIST 100" in df_n.columns: fig.add_trace(go.Scatter(x=df_n.index, y=df_n["BIST 100"], name='BIST 100', line=dict(color='#1f77b4', width=2)))
    fig.add_trace(go.Scatter(x=df_n.index, y=df_n["Dolar"], name='Dolar ($)', line=dict(color='#A9A9A9', width=2, dash='dot')))
    fig.add_trace(go.Scatter(x=df_n.index, y=df_n["Gram Altın"], name='Gram Altın', line=dict(color='#FFD700', width=2)))
    fig.update_layout(title="🏆 Performans Kıyaslama (İşlem Başlangıcından İtibaren % Getiri)", yaxis_title="Getiri (%)", hovermode="x unified", height=450, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
    st.plotly_chart(fig, use_container_width=True)

# --- REBALANS ASİSTANI ---
def render_rebalance_assistant(df_view):
    if df_view.empty: return
    st.subheader("⚖️ Portföy Rebalans (Dengeleme) Asistanı")
    df_grp = df_view.groupby("Grup")["Net Değer"].sum().reset_index()
    total_val = df_grp["Net Değer"].sum()
    cols = st.columns(len(df_grp))
    target_ratios = {}
    for i, row in df_grp.iterrows():
        target_ratios[row["Grup"]] = cols[i].number_input(f"Hedef % ({row['Grup']})", 0, 100, int(100/len(df_grp)))
    analysis = []
    for i, row in df_grp.iterrows():
        fark_tl = ((total_val * target_ratios[row["Grup"]]) / 100) - row["Net Değer"]
        tavsiye = f"✅ {format_tr_money(fark_tl)} TL AL" if fark_tl > 1000 else f"🚨 {format_tr_money(abs(fark_tl))} TL SAT" if fark_tl < -1000 else "🆗 Dengeli"
        analysis.append({"Grup": row["Grup"], "Mevcut Değer": row["Net Değer"], "Mevcut Oran": f"%{(row['Net Değer']/total_val*100):.1f}", "Hedef Oran": f"%{target_ratios[row['Grup']]:.1f}", "Aksiyon": tavsiye})
    df_ana = pd.DataFrame(analysis)
    def style_aksiyon(val): return f"color: {'#00FF00' if 'AL' in val else '#FF4B4B' if 'SAT' in val else 'white'}; font-weight: bold"
    st.dataframe(df_ana.style.applymap(style_aksiyon, subset=['Aksiyon']).format({"Mevcut Değer": "{:,.2f} TL"}), use_container_width=True, hide_index=True)

# --- İŞLEMLER ---
def save_transaction(date_obj, tur, varlik, islem, adet, fiyat):
    try:
        client = get_client()
        sheet = client.open(SHEET_NAME)
        ws = sheet.worksheet("Islemler")
        date_str = date_obj.strftime("%d.%m.%Y")
        row = [date_str, tur, varlik, islem, str(adet).replace(".", ","), str(fiyat).replace(".", ",")]
        ws.append_row(row, value_input_option='USER_ENTERED')
        st.success(f"✅ Eklendi: {varlik}"); time.sleep(1); st.cache_data.clear(); st.rerun()
    except Exception as e: st.error(f"Hata: {e}")

# --- ANA PROGRAM ---
def main():
    df_prices, df_trans, watchlist = load_data()
    ASSET_MAPPING = create_asset_mapping(watchlist)
    with st.sidebar:
        st.markdown("<h1 style='text-align: center; color: #4e8cff;'>💎 Varlık Paneli</h1>", unsafe_allow_html=True)
        if not df_prices.empty:
            last = df_prices.iloc[-1]
            usd, eur = last.get("DOLAR KURU", 1.0), last.get("EURO KURU", 1.0)
            st.markdown(f'<div style="display: flex; gap: 10px; margin-bottom: 20px;"><div class="currency-card" style="flex: 1;"><div class="currency-title">🇺🇸 USD</div><div class="currency-value">{usd:.2f} ₺</div></div><div class="currency-card" style="flex: 1;"><div class="currency-title">🇪🇺 EUR</div><div class="currency-value">{eur:.2f} ₺</div></div></div>', unsafe_allow_html=True)
        else: usd, eur = 1.0, 1.0
        page = st.radio("Menü", ["Portföyüm", "Piyasa Takip"], label_visibility="collapsed")
        st.divider()
        if st.button("🔄 Verileri Yenile", use_container_width=True): st.cache_data.clear(); st.rerun()
        with st.expander("➕ İşlem Ekle"):
            with st.form("add"):
                f_date, f_tur = st.date_input("Tarih", datetime.now()), st.selectbox("Tür", ["ALTIN", "FON", "HİSSE", "NAKİT", "DÖVİZ"])
                default_assets = ["TLY FONU", "DFI FONU", "TP2 FONU", "TL Bakiye", "22 AYAR BİLEZİK (Gr)", "ATA ALTIN (Adet)"]
                f_varlik = st.selectbox("Varlık", default_assets + [x + " (Hisse)" for x in watchlist if ".IS" in x])
                f_islem, f_adet, f_fiyat = st.selectbox("İşlem", ["ALIS", "SATIS"]), st.number_input("Adet", min_value=0.0, step=0.01), st.number_input("Fiyat", min_value=0.0, step=0.01)
                if st.form_submit_button("Kaydet", use_container_width=True): save_transaction(f_date, f_tur, f_varlik, f_islem, f_adet, f_fiyat)

    if page == "Portföyüm":
        if not df_trans.empty and not df_prices.empty:
            df_view, tot_wealth, tot_tax = calculate_portfolio(df_trans, df_prices)
            tab1, tab2, tab3 = st.tabs(["🇹🇷 TL Görünüm", "🇺🇸 USD Görünüm", "🇪🇺 EUR Görünüm"])
            for t, curr, rate in [(tab1, "TL", 1.0), (tab2, "$", usd), (tab3, "€", eur)]:
                with t:
                    if not df_view.empty:
                        net_p, cost = df_view["Net Kâr"].sum(), df_view["Maliyet"].sum()
                        df_trend = prepare_historical_trend(df_prices, df_trans, ASSET_MAPPING, rate)
                        c1, c2, c3 = st.columns(3)
                        c1.metric("Toplam Varlık", f"{format_tr_money(tot_wealth/rate)} {curr}", f"Vergi: -{format_tr_money(tot_tax/rate)} {curr}", delta_color="inverse")
                        c2.metric("Net Kâr", f"{format_tr_money(net_p/rate)} {curr}")
                        c3.metric("Kâr Oranı", f"%{format_tr_money(net_p/cost*100 if cost>0 else 0)}")
                        st.divider()
                        if curr == "TL":
                            st.subheader(f"🎯 Hedef: {format_tr_money(HEDEF_SERVET_TL)} TL")
                            st.progress(min(tot_wealth/HEDEF_SERVET_TL, 1.0))
                            h1, h2 = st.columns(2)
                            h1.caption(f"🏁 Kalan: **{format_tr_money(HEDEF_SERVET_TL - tot_wealth)} TL**")
                            h2.caption(f"⏳ Bitiş: **{HEDEF_TARIH.strftime('%d.%m.%Y')}** ({(HEDEF_TARIH - datetime.now()).days} Gün)")
                            st.divider()
                        st.subheader("📈 Servet Değişimi")
                        if not df_trend.empty:
                            fig = px.area(df_trend, x="Tarih", y="Toplam Servet")
                            fig.update_layout(yaxis_range=[df_trend["Toplam Servet"].min()*0.98, df_trend["Toplam Servet"].max()*1.02], height=400, hovermode="x unified")
                            fig.update_traces(line_color='#2E8B57', fillcolor='rgba(46, 139, 87, 0.2)')
                            st.plotly_chart(fig, use_container_width=True, key=f"trend_{curr}")
                            if curr == "TL": st.divider(); render_benchmark_chart(df_trend, df_prices)
                        c1, c2 = st.columns(2)
                        with c1:
                            st.subheader("Dağılım")
                            grp_mode = st.radio("Görünüm", ["Ana Gruplar", "Detaylı"], horizontal=True, key=f"rad_{curr}")
                            df_pie = df_view.groupby("Grup" if grp_mode == "Ana Gruplar" else "Varlık")["Net Değer"].sum().reset_index()
                            fig_p = px.pie(df_pie, values="Net Değer", names=df_pie.columns[0], hole=0.4, color_discrete_sequence=px.colors.qualitative.Prism)
                            fig_p.update_traces(textinfo="percent+label", textfont_size=18); st.plotly_chart(fig_p, use_container_width=True, key=f"pie_{curr}")
                        with c2:
                            st.subheader("Kâr/Zarar Durumu")
                            fig_b = go.Figure([go.Bar(name='Maliyet', x=df_view['Varlık'], y=df_view['Maliyet']/rate, marker_color='lightgrey'), go.Bar(name='Net Değer', x=df_view['Varlık'], y=df_view['Net Değer']/rate, marker_color='forestgreen')])
                            fig_b.update_layout(xaxis_tickangle=0); st.plotly_chart(fig_b, use_container_width=True, key=f"bar_{curr}")
                        if curr == "TL":
                            st.divider(); render_rebalance_assistant(df_view); st.divider()
                        st.subheader("🥇 Altın Makas")
                        last_p = df_prices.iloc[-1]
                        gold_cols = st.columns(4)
                        for i, (name, key) in enumerate([("Gram", "GRAM ALTIN"), ("Ata", "ATA ALTIN"), ("22 Ayar", "22 AYAR ALTIN"), ("Çeyrek", "ÇEYREK ALTIN")]):
                            satis, alis = last_p.get(f"{key} SATIŞ", 0) / rate, last_p.get(f"{key} ALIŞ", 0) / rate
                            makas = satis - alis
                            gold_cols[i].metric(name, format_tr_money(satis), f"Makas: {format_tr_money(makas)} (%{makas/satis*100 if satis>0 else 0:.2f})", delta_color="inverse")

    elif page == "Piyasa Takip":
        st.markdown("## 🌍 Detaylı Piyasa Analizi")
        if not df_prices.empty:
            market_data = []
            intervals = {"10 Dk": 10, "30 Dk": 30, "1 Saat": 60, "3 Saat": 180, "6 Saat": 360, "1 Gün": 1440, "3 Gün": 4320, "1 Hafta": 10080, "1 Ay": 43200, "1 Yıl": 525600}
            for col in df_prices.columns:
                if col == "Tarih" or "D" == col: continue
                if any(x in col for x in ["FİYAT", "ALTIN", "DOLAR", "KURU"]):
                    clean_name, series = col.replace(" FİYAT", "").replace(" ALIŞ", "").replace(" SATIŞ", ""), df_prices[col].replace(0, np.nan).ffill()
                    if series.empty: continue
                    row = {"Varlık": clean_name, "Fiyat": series.iloc[-1], "RSI": calculate_rsi(series).iloc[-1], "Trend": series.tail(30).tolist()}
                    for label, mins in intervals.items(): row[label] = get_pct_change(df_prices, col, mins)
                    market_data.append(row)
            df_m = pd.DataFrame(market_data)
            t1, t2, t3 = st.tabs(["📈 Hisseler", "📊 Fonlar", "🥇 Altın/Döviz"])
            col_config = {"Varlık": st.column_config.TextColumn("Varlık", width="small"), "Fiyat": st.column_config.NumberColumn("Fiyat", format="%.4f TL"), "RSI": st.column_config.NumberColumn("RSI", format="%.0f"), "Trend": st.column_config.LineChartColumn("Trend", y_min=0, width="small")}
            for k in intervals.keys(): col_config[k] = st.column_config.NumberColumn(k, format="%.2f %%")
            def show_table(keyword):
                if keyword == "Hisse": df_s = df_m[df_m["Varlık"].str.contains(".IS", na=False)]
                elif keyword == "Fon": df_s = df_m[df_m["Varlık"].apply(lambda x: len(str(x))<=4 and "." not in str(x))]
                else: df_s = df_m[df_m["Varlık"].str.contains("ALTIN|DOLAR|EURO|KURU", na=False)]
                if not df_s.empty: st.dataframe(df_s, column_config=col_config, use_container_width=True, hide_index=True)
            with t1: show_table("Hisse")
            with t2: show_table("Fon")
            with t3: show_table("Emtia")

if __name__ == "__main__":
    main()
