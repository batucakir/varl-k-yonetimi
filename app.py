import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
import numpy as np

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Kişisel Varlık Paneli", page_icon="💎", layout="wide", initial_sidebar_state="expanded")

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
        
        # 1. Fiyatlar
        ws_prices = sheet.sheet1 
        data_prices = ws_prices.get_all_values()
        if len(data_prices) > 1:
            df_prices = pd.DataFrame(data_prices[1:], columns=data_prices[0])
            df_prices.columns = df_prices.columns.str.strip()
            
            for col in df_prices.columns:
                if col != "Tarih":
                    df_prices[col] = df_prices[col].astype(str).str.replace(",", ".")
                    df_prices[col] = pd.to_numeric(df_prices[col], errors='coerce')
            
            df_prices['Tarih'] = pd.to_datetime(df_prices['Tarih'], errors='coerce')
            
            # SMART FILL
            df_prices = df_prices.replace(0, np.nan).ffill().fillna(0)
            
            if not df_prices.empty:
                if df_prices.iloc[-1]["DOLAR KURU"] < 10: 
                    df_prices = df_prices.iloc[:-1]

        else: df_prices = pd.DataFrame()

        # 2. İşlemler
        try:
            ws_trans = sheet.worksheet("Islemler")
            data_trans = ws_trans.get_all_values()
            if len(data_trans) > 1:
                df_trans = pd.DataFrame(data_trans[1:], columns=data_trans[0])
                df_trans.columns = df_trans.columns.str.strip()
                df_trans['Adet'] = pd.to_numeric(df_trans['Adet'].astype(str).str.replace(",", "."), errors='coerce').fillna(0)
                df_trans['Fiyat'] = pd.to_numeric(df_trans['Fiyat'].astype(str).str.replace(",", "."), errors='coerce').fillna(0)
                df_trans['Tarih'] = pd.to_datetime(df_trans['Tarih'], dayfirst=True)
            else: df_trans = pd.DataFrame()
        except: df_trans = pd.DataFrame()

        # 3. Takip Listesi
        try:
            ws_conf = sheet.worksheet(CONFIG_SHEET_NAME)
            vals = ws_conf.col_values(1)
            watchlist = [x for x in vals[1:] if x]
        except: watchlist = []

        return df_prices, df_trans, watchlist
    except: return pd.DataFrame(), pd.DataFrame(), []

# --- ANALİZ MOTORU ---
def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def get_pct_change(df, col, minutes):
    if df.empty: return 0.0
    current_price = df.iloc[-1][col]
    target_time = df.iloc[-1]['Tarih'] - timedelta(minutes=minutes)
    closest_row = df.iloc[(df['Tarih'] - target_time).abs().argsort()[:1]]
    if closest_row.empty: return 0.0
    old_price = closest_row.iloc[0][col]
    if old_price == 0: return 0.0
    return (current_price - old_price) / old_price

# --- MAPPING & FİYAT ---
def create_asset_mapping(watchlist):
    mapping = {
        "22 AYAR BİLEZİK (Gr)": "22 AYAR ALTIN ALIŞ",
        "ATA ALTIN (Adet)": "ATA ALTIN ALIŞ",
        "ÇEYREK ALTIN (Adet)": "ÇEYREK ALTIN ALIŞ",
        "TL Bakiye": "NAKİT"
    }
    for f in MY_FUNDS: mapping[f"{f} FONU"] = f"{f} FİYAT"
    for item in watchlist:
        if ".IS" in item: mapping[f"{item.replace('.IS', '')} (Hisse)"] = f"{item} FİYAT"
    return mapping

def find_smart_price(row, asset_name):
    if asset_name == "TL Bakiye": return 1.0
    search_term = asset_name.replace(" FONU", "").replace(" (Adet)", "").replace(" (Gr)", "").replace(" (Hisse)", "").strip()
    gold_map = {"22 AYAR BİLEZİK": "22 AYAR ALTIN ALIŞ", "ATA ALTIN": "ATA ALTIN ALIŞ", "ÇEYREK ALTIN": "ÇEYREK ALTIN ALIŞ"}
    if search_term in gold_map: return row.get(gold_map[search_term], 0)
    for col in row.index:
        if search_term in col: return row[col]
    return 0.0

# --- HESAPLAMA ---
def calculate_portfolio(df_trans, df_prices):
    port = {}
    last_prices = df_prices.iloc[-1] if not df_prices.empty else {}
    if not df_prices.empty and len(df_prices) > 1: prev_prices = df_prices.iloc[-2]
    else: prev_prices = last_prices

    for _, row in df_trans.iterrows():
        varlik = str(row['Varlık']).strip()
        islem = str(row['İşlem']).upper().strip()
        adet = float(row['Adet'])
        fiyat = float(row['Fiyat'])
        tur = row['Tür']
        if varlik not in port: port[varlik] = {"adet": 0.0, "maliyet": 0.0, "tur": tur}
        if islem == "ALIS":
            port[varlik]["adet"] += adet
            port[varlik]["maliyet"] += (adet * fiyat)
        elif islem == "SATIS":
            if port[varlik]["adet"] > 0:
                avg = port[varlik]["maliyet"] / port[varlik]["adet"]
                port[varlik]["maliyet"] -= (adet * avg)
                port[varlik]["adet"] -= adet
            else: port[varlik]["adet"] -= adet

    table_rows = []
    total_wealth = 0
    total_tax = 0

    for v, d in port.items():
        if d["adet"] <= 0.001: continue
        curr_price = find_smart_price(last_prices, v)
        if curr_price == 0: curr_price = find_smart_price(prev_prices, v)
        
        guncel_deger = d["adet"] * curr_price
        vergi = 0
        if "FON" in str(d["tur"]).upper() and guncel_deger > d["maliyet"]:
            vergi = (guncel_deger - d["maliyet"]) * FON_VERGI_ORANI
            
        net_deger = guncel_deger - vergi
        kar = net_deger - d["maliyet"]
        
        table_rows.append({
            "Grup": d["tur"], "Varlık": v, "Adet": d["adet"], "Fiyat": curr_price,
            "Maliyet": d["maliyet"], "Net Değer": net_deger, "Net Kâr": kar, "Vergi": vergi
        })
        total_wealth += net_deger
        total_tax += vergi

    return pd.DataFrame(table_rows), total_wealth, total_tax

# --- GRAFİK ---
def prepare_historical_trend(df_prices, df_trans, asset_map, rate=1.0):
    if df_prices.empty: return pd.DataFrame()
    trend_data = []
    running_port = {}
    trans_idx = 0
    df_prices = df_prices.sort_values("Tarih")
    df_trans = df_trans.sort_values("Tarih") if not df_trans.empty else df_trans
    
    for _, price_row in df_prices.iterrows():
        curr_date = price_row['Tarih']
        if price_row.get("DOLAR KURU", 0) < 10: continue
        while trans_idx < len(df_trans):
            try: trans_date = df_trans.iloc[trans_idx]['Tarih']
            except: trans_date = curr_date
            if trans_date <= curr_date:
                tr = df_trans.iloc[trans_idx]
                var = str(tr['Varlık']).strip()
                isl = str(tr['İşlem']).upper().strip()
                ad = float(tr['Adet'])
                cur_qty = running_port.get(var, 0.0)
                if isl == "ALIS": running_port[var] = cur_qty + ad
                elif isl == "SATIS": running_port[var] = cur_qty - ad
                trans_idx += 1
            else: break
        tot = 0
        for v, qty in running_port.items():
            if qty <= 0: continue
            pk = asset_map.get(v)
            p = 0
            if pk == "NAKİT": p = 1.0
            elif pk:
                for col in price_row.index:
                    if pk in col: p = price_row[col]; break
            tot += (qty * p)
        if tot > 0: trend_data.append({"Tarih": curr_date, "Toplam Servet": tot/rate})
    return pd.DataFrame(trend_data)

def save_transaction(date_obj, tur, varlik, islem, adet, fiyat):
    try:
        client = get_client()
        sheet = client.open(SHEET_NAME)
        ws = sheet.worksheet("Islemler")
        date_str = date_obj.strftime("%d.%m.%Y")
        row = [date_str, tur, varlik, islem, str(adet).replace(".", ","), str(fiyat).replace(".", ",")]
        ws.append_row(row, value_input_option='USER_ENTERED')
        st.success(f"✅ Eklendi: {varlik}")
        time.sleep(1)
        st.cache_data.clear()
        st.rerun()
    except Exception as e: st.error(f"Hata: {e}")

def add_to_watchlist_sheet(symbol):
    try:
        client = get_client()
        sheet = client.open(SHEET_NAME)
        try: ws = sheet.worksheet(CONFIG_SHEET_NAME)
        except: 
            ws = sheet.add_worksheet(CONFIG_SHEET_NAME, 100, 5)
            ws.append_row(["Sembol"])
        current = ws.col_values(1)
        if symbol not in current: ws.append_row([symbol]); return True
        return False
    except: return False

# --- UYGULAMA ---
def main():
    df_prices, df_trans, watchlist = load_data()
    ASSET_MAPPING = create_asset_mapping(watchlist)
    
    with st.sidebar:
        st.title("💎 Varlık Paneli")
        page = st.radio("Menü", ["Portföyüm", "Piyasa Takip"])
        st.divider()
        if not df_prices.empty:
            usd = df_prices.iloc[-1].get("DOLAR KURU", 1.0)
            st.metric("Dolar Kuru", f"{usd:.2f} TL")
        else: usd = 1.0
        if st.button("🔄 Yenile"): st.cache_data.clear(); st.rerun()
        with st.expander("➕ İşlem Ekle"):
            with st.form("add"):
                f_date = st.date_input("Tarih", datetime.now())
                f_tur = st.selectbox("Tür", ["ALTIN", "FON", "HİSSE", "NAKİT", "DÖVİZ"])
                default_assets = ["TLY FONU", "DFI FONU", "TP2 FONU", "TL Bakiye", "22 AYAR BİLEZİK (Gr)", "ATA ALTIN (Adet)"]
                f_varlik = st.selectbox("Varlık", default_assets + [x + " (Hisse)" for x in watchlist if ".IS" in x])
                f_islem = st.selectbox("İşlem", ["ALIS", "SATIS"])
                f_adet = st.number_input("Adet", min_value=0.0, step=0.01)
                f_fiyat = st.number_input("Fiyat", min_value=0.0, step=0.01)
                if st.form_submit_button("Kaydet"): save_transaction(f_date, f_tur, f_varlik, f_islem, f_adet, f_fiyat)
        with st.expander("🛠️ Takip Listesi"):
            ns = st.text_input("Sembol (Örn: SASA.IS)")
            if st.button("Ekle"): 
                if add_to_watchlist_sheet(ns): st.success("Eklendi")

    # --- SAYFA 1: PORTFÖYÜM ---
    if page == "Portföyüm":
        st.markdown("<h2 style='text-align: center;'>💎 Varlık Portföyü</h2>", unsafe_allow_html=True)
        if not df_trans.empty and not df_prices.empty:
            df_view, tot_wealth, tot_tax = calculate_portfolio(df_trans, df_prices)
            
            tab1, tab2 = st.tabs(["🇹🇷 TL Görünüm", "🇺🇸 USD Görünüm"])
            for t, curr, rate in [(tab1, "TL", 1.0), (tab2, "$", usd)]:
                with t:
                    if not df_view.empty:
                        net_profit = df_view["Net Kâr"].sum()
                        cost = df_view["Maliyet"].sum()
                        ratio = (net_profit / cost * 100) if cost > 0 else 0
                        df_trend = prepare_historical_trend(df_prices, df_trans, ASSET_MAPPING, rate)
                        diff_pct = 0
                        if len(df_trend) > 1:
                            curr_val = df_trend.iloc[-1]["Toplam Servet"]
                            prev_val = df_trend.iloc[-2]["Toplam Servet"]
                            diff_pct = (curr_val - prev_val) / prev_val * 100
                        
                        c1, c2, c3 = st.columns(3)
                        c1.metric("Toplam Varlık", f"{format_tr_money(tot_wealth/rate)} {curr}", f"Vergi: -{format_tr_money(tot_tax/rate)} {curr}", delta_color="inverse")
                        c2.metric("Net Kâr", f"{format_tr_money(net_profit/rate)} {curr}", f"{format_tr_percent(diff_pct)} (Son Değişim)")
                        c3.metric("Kâr Oranı", f"%{format_tr_money(ratio)}")
                        st.divider()
                        
                        if curr == "TL":
                            prog = min(tot_wealth/HEDEF_SERVET_TL, 1.0)
                            st.subheader(f"🎯 Hedef: {format_tr_money(HEDEF_SERVET_TL)} TL")
                            st.progress(prog)
                            h1, h2 = st.columns(2)
                            h1.caption(f"🏁 Kalan: **{format_tr_money(HEDEF_SERVET_TL - tot_wealth)} TL**")
                            h2.caption(f"⏳ Bitiş: **{HEDEF_TARIH.strftime('%d.%m.%Y')}** ({(HEDEF_TARIH - datetime.now()).days} Gün)")
                            st.divider()
                            
                        st.subheader("📋 Varlık Detayı")
                        df_show = df_view.copy()
                        for c in ["Fiyat", "Maliyet", "Net Değer", "Net Kâr", "Vergi"]: df_show[c] = df_show[c] / rate
                        df_show["Kâr %"] = df_show.apply(lambda x: x["Net Kâr"]/x["Maliyet"]*100 if x["Maliyet"]>0 else 0, axis=1)
                        st.dataframe(df_show.style.format({
                            "Adet": "{:,.0f}", 
                            "Fiyat": "{:,.6f}",  # 6 digit
                            "Maliyet": "{:,.2f}",
                            "Net Değer": "{:,.2f}", "Net Kâr": "{:,.2f}", "Vergi": "{:,.2f}", "Kâr %": "%{:,.2f}"
                        }), use_container_width=True, hide_index=True)
                        st.divider()
                        
                        st.subheader("📈 Servet Değişimi")
                        if not df_trend.empty:
                            fig = px.area(df_trend, x="Tarih", y="Toplam Servet")
                            min_y = df_trend["Toplam Servet"].min() * 0.999
                            max_y = df_trend["Toplam Servet"].max() * 1.001
                            fig.update_layout(yaxis_range=[min_y, max_y], height=400, hovermode="x unified")
                            fig.update_traces(line_color='#2E8B57', fillcolor='rgba(46, 139, 87, 0.2)')
                            st.plotly_chart(fig, use_container_width=True, key=f"trend_{curr}")
                        
                        c1, c2 = st.columns(2)
                        with c1:
                            st.subheader("Dağılım")
                            grp_mode = st.radio("Görünüm", ["Ana Gruplar", "Detaylı"], horizontal=True, key=f"rad_{curr}")
                            grp_col = "Grup" if grp_mode == "Ana Gruplar" else "Varlık"
                            df_pie = df_view.groupby(grp_col)["Net Değer"].sum().reset_index()
                            fig_p = px.pie(df_pie, values="Net Değer", names=grp_col, hole=0.4)
                            fig_p.update_traces(textinfo="percent+label")
                            st.plotly_chart(fig_p, use_container_width=True, key=f"pie_{curr}")
                        with c2:
                            st.subheader("Kâr/Zarar Durumu")
                            fig_b = go.Figure()
                            fig_b.add_trace(go.Bar(name='Maliyet', x=df_view['Varlık'], y=df_view['Maliyet'], marker_color='lightgrey'))
                            fig_b.add_trace(go.Bar(name='Net Değer', x=df_view['Varlık'], y=df_view['Net Değer'], marker_color='forestgreen'))
                            st.plotly_chart(fig_b, use_container_width=True, key=f"bar_{curr}")
                        
                        st.subheader("🥇 Altın Makas")
                        last_p = df_prices.iloc[-1]
                        gold_cols = st.columns(4)
                        for i, (name, key) in enumerate([("Gram", "GRAM ALTIN"), ("Ata", "ATA ALTIN"), ("22 Ayar", "22 AYAR ALTIN"), ("Çeyrek", "ÇEYREK ALTIN")]):
                            alis = last_p.get(f"{key} ALIŞ", 0) / rate
                            satis = last_p.get(f"{key} SATIŞ", 0) / rate
                            makas = satis - alis
                            pct_m = makas/satis*100 if satis>0 else 0
                            gold_cols[i].metric(name, format_tr_money(satis), f"Makas: {format_tr_money(makas)} (%{pct_m:.2f})", delta_color="inverse")

    # --- SAYFA 2: PİYASA TAKİP ---
    elif page == "Piyasa Takip":
        st.markdown("## 🌍 Detaylı Piyasa Analizi")
        if not df_prices.empty:
            
            market_data = []
            
            # --- ZAMAN DİLİMLERİ ---
            intervals = {
                "10 Dk": 10, "30 Dk": 30, "1 Saat": 60, "3 Saat": 180, "6 Saat": 360,
                "1 Gün": 1440, "3 Gün": 4320, "1 Hafta": 10080, "2 Hafta": 20160,
                "1 Ay": 43200, "3 Ay": 129600, "6 Ay": 259200, "1 Yıl": 525600
            }
            
            cols = list(df_prices.columns)
            for col in cols:
                if col == "Tarih": continue
                if "FİYAT" in col or "ALTIN" in col or "DOLAR" in col:
                    clean_name = col.replace(" FİYAT", "").replace(" ALIŞ", "").replace(" SATIŞ", "")
                    series = df_prices[col]
                    current_price = series.iloc[-1]
                    
                    rsi = 50.0
                    try: rsi = calculate_rsi(series).iloc[-1]
                    except: pass
                    
                    row_data = {
                        "Enstrüman": clean_name,
                        "Fiyat": current_price,
                        "RSI": rsi,
                        "Trend": series.tail(30).tolist()
                    }
                    
                    for label, mins in intervals.items():
                        row_data[label] = get_pct_change(df_prices, col, mins)
                        
                    market_data.append(row_data)
            
            df_m = pd.DataFrame(market_data)
            
            # --- SEKMELER ---
            t1, t2, t3 = st.tabs(["📈 Hisseler", "📊 Fonlar", "🥇 Altın/Döviz"])
            
            # Dinamik Konfigürasyon
            col_config = {
                "Enstrüman": st.column_config.TextColumn("Varlık", width="small"),
                "Fiyat": st.column_config.NumberColumn("Fiyat", format="%.6f TL"),
                "RSI": st.column_config.NumberColumn("RSI", format="%.0f"),
                "Trend": st.column_config.LineChartColumn("Trend (30 Veri)", y_min=0, width="small"),
            }
            
            for label in intervals.keys():
                col_config[label] = st.column_config.NumberColumn(label, format="%.2f %%")

            def show_detailed_table(keyword):
                if keyword == "Hisse": 
                    df_show = df_m[df_m["Enstrüman"].str.contains(".IS", na=False)]
                elif keyword == "Fon": 
                    # Fonlar için kısa vadeli değişimler anlamsız, onları gizleyelim
                    df_show = df_m[df_m["Enstrüman"].apply(lambda x: len(str(x))<=4 and "." not in str(x))]
                    # Fonlarda anlık değişimleri göstermeyelim
                    display_cols = ["Enstrüman", "Fiyat", "RSI", "Trend"] + [k for k in intervals.keys() if "Dk" not in k and "Saat" not in k]
                    df_show = df_show[display_cols]
                else: 
                    df_show = df_m[df_m["Enstrüman"].str.contains("ALTIN|DOLAR", na=False)]

                if df_show.empty:
                    st.info("Veri yok.")
                    return
                
                st.dataframe(df_show, column_config=col_config, use_container_width=True, hide_index=True)

            with t1: show_detailed_table("Hisse")
            with t2: show_detailed_table("Fon")
            with t3: show_detailed_table("Emtia")

if __name__ == "__main__":
    main()
