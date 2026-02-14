import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta

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

def format_tr_nodigit(value):
    if pd.isna(value) or value == "": return "-"
    try: return "{:,.0f}".format(float(value)).replace(",", "X").replace(".", ",").replace("X", ".")
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
        
        # 1. Fiyatlar
        ws_prices = sheet.sheet1 
        data_prices = ws_prices.get_all_values()
        if len(data_prices) > 1:
            df_prices = pd.DataFrame(data_prices[1:], columns=data_prices[0])
            # Sütun isimlerini temizle (Başındaki sonundaki boşlukları at)
            df_prices.columns = df_prices.columns.str.strip()
            
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

# --- AKILLI FİYAT BULUCU (YENİ ÖZELLİK) ---
def find_price_for_asset(row, asset_name):
    """
    Sütun isimlerinde asset_name'i arar. 
    Örn: asset_name="TLY" ise "TLY FİYAT", "TLY FONU" sütunlarını bulur.
    """
    if asset_name == "TL Bakiye": return 1.0
    
    # 1. Temiz isim (Parantezleri at)
    clean_name = asset_name.split(" (")[0].split(" FONU")[0].strip()
    
    # 2. Altınlar İçin Özel Mapping (Değişmez)
    gold_map = {
        "22 AYAR BİLEZİK": "22 AYAR ALTIN ALIŞ",
        "ATA ALTIN": "ATA ALTIN ALIŞ",
        "ÇEYREK ALTIN": "ÇEYREK ALTIN ALIŞ",
        "GRAM ALTIN": "GRAM ALTIN ALIŞ"
    }
    if clean_name in gold_map:
        target_col = gold_map[clean_name]
        return row.get(target_col, 0)

    # 3. Genel Arama (Hisse ve Fonlar)
    # Row'daki tüm sütunları gez
    for col in row.index:
        # Sütun isminde bizim varlığın ismi geçiyor mu?
        if clean_name in col:
            # Emin olmak için FİYAT veya FON kelimesi veya .IS var mı bakalım
            if "FİYAT" in col or "FON" in col or ".IS" in col or clean_name == col:
                return row[col]
    
    return 0.0

# --- HESAPLAMA ---
def calculate_portfolio(df_trans, df_prices):
    port = {}
    last_prices = df_prices.iloc[-1] if not df_prices.empty else {}
    prev_prices = df_prices.iloc[-2] if len(df_prices) > 1 else last_prices

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
    
    for v, d in port.items():
        if d["adet"] <= 0.001: continue
        
        # AKILLI FİYAT ÇEKME
        curr_price = find_price_for_asset(last_prices, v)
        
        # Eğer güncel fiyat 0 ise (Bot çekemediyse), dünküne bak
        if curr_price == 0:
            curr_price = find_price_for_asset(prev_prices, v)
        
        # Hala 0 ise, servet erimesin diye maliyet fiyatını baz al (Geçici çözüm)
        if curr_price == 0 and v != "TL Bakiye":
             # Burada opsiyonel olarak maliyet fiyatı kullanılabilir ama 0 bırakmak hatayı görmeni sağlar.
             pass 

        guncel_deger = d["adet"] * curr_price
        vergi = 0
        if "FON" in str(d["tur"]).upper() and guncel_deger > d["maliyet"]:
            vergi = (guncel_deger - d["maliyet"]) * FON_VERGI_ORANI
            
        net_deger = guncel_deger - vergi
        kar = net_deger - d["maliyet"]
        
        table_rows.append({
            "Grup": d["tur"], "Varlık": v, "Adet": d["adet"],
            "Fiyat": curr_price, "Maliyet": d["maliyet"],
            "Net Değer": net_deger, "Net Kâr": kar
        })
        total_wealth += net_deger
        
    return pd.DataFrame(table_rows), total_wealth

# --- GRAFİK VERİSİ ---
def prepare_historical_trend(df_prices, df_trans, rate=1.0):
    if df_prices.empty: return pd.DataFrame()
    df_prices = df_prices.sort_values("Tarih").reset_index(drop=True)
    if not df_trans.empty: 
        df_trans = df_trans.sort_values("Tarih").reset_index(drop=True)
    
    trend_data = []
    running_port = {}
    trans_idx = 0
    total_trans = len(df_trans)
    
    for _, price_row in df_prices.iterrows():
        curr_date = price_row['Tarih']
        while trans_idx < total_trans:
            tr = df_trans.iloc[trans_idx]
            if tr['Tarih'] <= curr_date:
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
            p = find_price_for_asset(price_row, v)
            tot += (qty * p)
        
        if tot > 0: trend_data.append({"Tarih": curr_date, "Toplam Servet": tot/rate})
    return pd.DataFrame(trend_data)

def get_historical_price(df_prices, col_name, days_ago):
    if df_prices.empty or col_name not in df_prices.columns: return 0
    target = datetime.now() - timedelta(days=days_ago)
    row = df_prices.iloc[(df_prices['Tarih'] - target).abs().argsort()[:1]]
    return row.iloc[0][col_name] if not row.empty else 0

def calculate_performance_table(df_prices, portfolio_df, rate=1.0):
    data = []
    if df_prices.empty or portfolio_df.empty: return pd.DataFrame()
    last = df_prices.iloc[-1]
    
    for index, row in portfolio_df.iterrows():
        varlik = row["Varlık"]
        # Sütun adını bulmak için basit bir hack
        col_name = None
        for col in df_prices.columns:
            clean_name = varlik.split(" (")[0].strip()
            if clean_name in col:
                col_name = col
                break
        
        if not col_name: continue
        
        curr_p = last.get(col_name, 0)
        p1 = get_historical_price(df_prices, col_name, 1)
        p7 = get_historical_price(df_prices, col_name, 7)
        p30 = get_historical_price(df_prices, col_name, 30)
        
        def pct(old, new): return ((new - old) / old * 100) if old > 0 else 0
        data.append({
            "Varlık": varlik, "Anlık Fiyat": curr_p/rate,
            "1 Gün": pct(p1, curr_p), "1 Hafta": pct(p7, curr_p),
            "1 Ay": pct(p30, curr_p)
        })
    return pd.DataFrame(data)

# --- İŞLEMLER ---
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
        if symbol not in current:
            ws.append_row([symbol])
            return True
        return False
    except: return False

# --- UYGULAMA ---
def main():
    df_prices, df_trans, watchlist = load_data()
    
    with st.sidebar:
        st.title("💎 Varlık Paneli")
        page = st.radio("Menü", ["Portföyüm", "Piyasa Takip"])
        st.divider()
        if not df_prices.empty:
            usd = df_prices.iloc[-1].get("DOLAR KURU", 1.0)
            st.metric("Dolar Kuru", f"{usd:.2f} TL")
        else: usd = 1.0
        
        if st.button("🔄 Yenile"):
            st.cache_data.clear()
            st.rerun()
            
        st.divider()
        with st.expander("➕ İşlem Ekle"):
            with st.form("add"):
                f_date = st.date_input("Tarih", datetime.now())
                f_tur = st.selectbox("Tür", ["ALTIN", "FON", "HİSSE", "NAKİT", "DÖVİZ"])
                # Basit bir asset listesi (Hepsini içerir)
                all_assets = ["TLY FONU", "DFI FONU", "TP2 FONU", "TL Bakiye", "22 AYAR BİLEZİK (Gr)", "ATA ALTIN (Adet)"] + [x + " (Hisse)" for x in watchlist if ".IS" in x]
                f_varlik = st.selectbox("Varlık", all_assets)
                f_islem = st.selectbox("İşlem", ["ALIS", "SATIS"])
                f_adet = st.number_input("Adet", min_value=0.0, step=0.01)
                f_fiyat = st.number_input("Fiyat", min_value=0.0, step=0.01)
                if st.form_submit_button("Kaydet"):
                    save_transaction(f_date, f_tur, f_varlik, f_islem, f_adet, f_fiyat)
                    
        with st.expander("🛠️ Takip Listesi"):
            ns = st.text_input("Sembol (Örn: SASA.IS)")
            if st.button("Ekle"): 
                if add_to_watchlist_sheet(ns): st.success("Eklendi")

    # --- SAYFA 1: PORTFÖYÜM ---
    if page == "Portföyüm":
        st.markdown("<h2 style='text-align: center;'>💎 Varlık Portföyü</h2>", unsafe_allow_html=True)
        if not df_trans.empty and not df_prices.empty:
            df_view, tot_wealth = calculate_portfolio(df_trans, df_prices)
            
            tab1, tab2 = st.tabs(["🇹🇷 TL Görünüm", "🇺🇸 USD Görünüm"])
            for t, curr, rate in [(tab1, "TL", 1.0), (tab2, "$", usd)]:
                with t:
                    if not df_view.empty:
                        # KPI
                        net_profit = df_view["Net Kâr"].sum()
                        cost = df_view["Maliyet"].sum()
                        ratio = (net_profit / cost * 100) if cost > 0 else 0
                        
                        df_trend = prepare_historical_trend(df_prices, df_trans, rate)
                        diff_pct = 0
                        if len(df_trend) > 1:
                            curr_val = df_trend.iloc[-1]["Toplam Servet"]
                            prev_val = df_trend.iloc[-2]["Toplam Servet"]
                            diff_pct = (curr_val - prev_val) / prev_val * 100
                        
                        c1, c2, c3 = st.columns(3)
                        c1.metric("Toplam Varlık", f"{format_tr_money(tot_wealth/rate)} {curr}")
                        c2.metric("Net Kâr", f"{format_tr_money(net_profit/rate)} {curr}", f"{format_tr_percent(diff_pct)} (Son Değişim)")
                        c3.metric("Kâr Oranı", f"%{format_tr_money(ratio)}")
                        st.divider()
                        
                        # Hedef
                        if curr == "TL":
                            prog = min(tot_wealth/HEDEF_SERVET_TL, 1.0)
                            st.subheader(f"🎯 Hedef: {format_tr_money(HEDEF_SERVET_TL)} TL")
                            st.progress(prog)
                            st.caption(f"Kalan: {format_tr_money(HEDEF_SERVET_TL - tot_wealth)} TL")
                            st.divider()
                            
                        # Tablo
                        st.subheader("📋 Varlık Detayı")
                        df_show = df_view.copy()
                        for c in ["Fiyat", "Maliyet", "Net Değer", "Net Kâr"]: 
                            df_show[c] = df_show[c] / rate
                        df_show["Kâr %"] = df_show.apply(lambda x: x["Net Kâr"]/x["Maliyet"]*100 if x["Maliyet"]>0 else 0, axis=1)
                        
                        st.dataframe(df_show.style.format({
                            "Adet": "{:,.0f}", "Fiyat": "{:,.2f}", "Maliyet": "{:,.2f}",
                            "Net Değer": "{:,.2f}", "Net Kâr": "{:,.2f}", "Kâr %": "%{:,.2f}"
                        }), use_container_width=True, hide_index=True)
                        st.divider()
                        
                        # Grafik
                        st.subheader("📈 Servet Değişimi")
                        if not df_trend.empty:
                            fig = px.area(df_trend, x="Tarih", y="Toplam Servet")
                            min_y = df_trend["Toplam Servet"].min() * 0.999
                            max_y = df_trend["Toplam Servet"].max() * 1.001
                            fig.update_layout(yaxis_range=[min_y, max_y], height=400, hovermode="x unified")
                            fig.update_traces(line_color='#2E8B57', fillcolor='rgba(46, 139, 87, 0.2)')
                            st.plotly_chart(fig, use_container_width=True, key=f"trend_{curr}")
                        
                        # --- PASTA GRAFİĞİ (Geri Getirilen Özellik) ---
                        c1, c2 = st.columns(2)
                        with c1:
                            st.subheader("Varlık Dağılımı")
                            # Radio Button Geri Geldi!
                            dagilim_tipi = st.radio("Görünüm Modu:", ["Ana Gruplar", "Detaylı Varlıklar"], horizontal=True, key=f"rad_{curr}")
                            col_name = 'Grup' if dagilim_tipi == "Ana Gruplar" else 'Varlık'
                            
                            df_pie = df_view.groupby(col_name)["Net Değer"].sum().reset_index()
                            total_p = df_pie["Net Değer"].sum()
                            df_pie["Etiket"] = df_pie.apply(lambda x: f"<b>{x[col_name]}</b><br>%{x['Net Değer']/total_p*100:.2f}", axis=1)
                            
                            fig_p = px.pie(df_pie, values="Net Değer", names=col_name, hole=0.4)
                            fig_p.update_traces(text=df_pie["Etiket"], textinfo="text", textfont_size=15)
                            st.plotly_chart(fig_p, use_container_width=True, key=f"pie_{curr}")
                            
                        with c2:
                            st.subheader("Maliyet vs Değer")
                            fig_b = go.Figure()
                            fig_b.add_trace(go.Bar(name='Maliyet', x=df_view['Varlık'], y=df_view['Maliyet'], marker_color='lightgrey'))
                            fig_b.add_trace(go.Bar(name='Net Değer', x=df_view['Varlık'], y=df_view['Net Değer'], marker_color='forestgreen'))
                            st.plotly_chart(fig_b, use_container_width=True, key=f"bar_{curr}")
                        
                        # Performans
                        st.subheader("📊 Performans")
                        df_perf = calculate_performance_table(df_prices, df_view, rate)
                        if not df_perf.empty:
                            st.dataframe(df_perf.style.format({
                                "Anlık Fiyat": format_tr_money, "1 Gün": format_tr_percent, 
                                "1 Hafta": format_tr_percent, "1 Ay": format_tr_percent
                            }), use_container_width=True, hide_index=True)
                        
                        # Altın Makas
                        st.subheader("🥇 Altın Makas")
                        last_p = df_prices.iloc[-1]
                        gold_cols = st.columns(4)
                        for i, (name, key) in enumerate([("Gram", "GRAM ALTIN"), ("Ata", "ATA ALTIN"), ("22 Ayar", "22 AYAR ALTIN"), ("Çeyrek", "ÇEYREK ALTIN")]):
                            alis = last_p.get(f"{key} ALIŞ", 0) / rate
                            satis = last_p.get(f"{key} SATIŞ", 0) / rate
                            makas = satis - alis
                            yuzde = makas/satis*100 if satis>0 else 0
                            gold_cols[i].metric(name, format_tr_money(satis), f"Makas: {format_tr_money(makas)} (%{yuzde:.2f})", delta_color="inverse")

    # --- SAYFA 2: PİYASA TAKİP ---
    elif page == "Piyasa Takip":
        st.markdown("## 🌍 Piyasa İzleme")
        if not df_prices.empty:
            last = df_prices.iloc[-1]
            prev = df_prices.iloc[-2] if len(df_prices)>1 else last
            
            hisseler, fonlar, emtia = [], [], []
            
            for col in df_prices.columns:
                if col == "Tarih": continue
                if "FİYAT" in col or "ALTIN" in col or "DOLAR" in col:
                    name = col.replace(" FİYAT", "").replace(" ALIŞ", "").replace(" SATIŞ", "")
                    price = last[col]
                    old = prev[col]
                    
                    diff = 0
                    if old > 0 and price > 0: diff = (price - old) / old
                    
                    item = {"Enstrüman": name, "Fiyat": price, "Değişim": diff}
                    
                    if ".IS" in col: hisseler.append(item)
                    elif "ALTIN" in col or "DOLAR" in col: emtia.append(item)
                    elif len(col) <= 12: fonlar.append(item)
            
            t1, t2, t3 = st.tabs(["📈 Hisseler", "📊 Fonlar", "🥇 Altın/Döviz"])
            
            def show_table(data):
                if not data: 
                    st.info("Veri yok.")
                    return
                df = pd.DataFrame(data)
                st.dataframe(
                    df,
                    column_config={
                        "Enstrüman": st.column_config.TextColumn("Varlık", width="medium"),
                        "Fiyat": st.column_config.NumberColumn("Fiyat", format="%.2f TL"),
                        "Değişim": st.column_config.NumberColumn("Günlük %", format="%.2f %%")
                    },
                    use_container_width=True,
                    hide_index=True
                )

            with t1: show_table(hisseler)
            with t2: show_table(fonlar)
            with t3: show_table(emtia)

if __name__ == "__main__":
    main()
