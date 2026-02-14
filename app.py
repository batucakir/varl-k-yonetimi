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
def format_tr_money(value):
    if pd.isna(value) or value == "" or value == 0: return "-"
    try: return "{:,.2f}".format(float(value)).replace(",", "X").replace(".", ",").replace("X", ".")
    except: return str(value)

def format_tr_percent(value):
    if pd.isna(value) or value == "": return "-"
    try:
        val = float(value)
        arrow = "▲" if val > 0 else "▼" if val < 0 else "-"
        return f"{arrow} %" + "{:,.2f}".format(abs(val)).replace(",", "X").replace(".", ",").replace("X", ".")
    except: return str(value)

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
            for col in df_prices.columns:
                if col != "Tarih":
                    df_prices[col] = df_prices[col].astype(str).str.replace(".", "").str.replace(",", ".")
                    df_prices[col] = pd.to_numeric(df_prices[col], errors='coerce').fillna(0)
            df_prices['Tarih'] = pd.to_datetime(df_prices['Tarih'])
        else: df_prices = pd.DataFrame()

        # 2. İşlemler
        try:
            ws_trans = sheet.worksheet("Islemler")
            data_trans = ws_trans.get_all_values()
            if len(data_trans) > 1:
                df_trans = pd.DataFrame(data_trans[1:], columns=data_trans[0])
                df_trans['Adet'] = pd.to_numeric(df_trans['Adet'].astype(str).str.replace(",", "."), errors='coerce').fillna(0)
                df_trans['Fiyat'] = pd.to_numeric(df_trans['Fiyat'].astype(str).str.replace(",", "."), errors='coerce').fillna(0)
                df_trans['Tarih'] = pd.to_datetime(df_trans['Tarih'], dayfirst=True)
            else: df_trans = pd.DataFrame()
        except: df_trans = pd.DataFrame()

        # 3. Takip Listesi (Ayarlar)
        try:
            ws_conf = sheet.worksheet(CONFIG_SHEET_NAME)
            vals = ws_conf.col_values(1)
            watchlist = [x for x in vals[1:] if x]
        except: watchlist = []

        return df_prices, df_trans, watchlist
    except: return pd.DataFrame(), pd.DataFrame(), []

# --- DİNAMİK MAPPING OLUŞTURUCU (DÜZELTİLDİ) ---
def create_asset_mapping(watchlist):
    # 1. SABİT VARLIKLAR (Bunlar her zaman çalışmalı)
    mapping = {
        "22 AYAR BİLEZİK (Gr)": "22 AYAR ALTIN ALIŞ",
        "ATA ALTIN (Adet)": "ATA ALTIN ALIŞ",
        "ÇEYREK ALTIN (Adet)": "ÇEYREK ALTIN ALIŞ",
        "TL Bakiye": "NAKİT",
        # FONLAR BURAYA SABİTLENDİ (HATA ÇÖZÜMÜ)
        "TLY FONU": "TLY FİYAT",
        "DFI FONU": "DFI FİYAT",
        "TP2 FONU": "TP2 FİYAT",
        "PHE FONU": "PHE FİYAT",
        "ROF FONU": "ROF FİYAT",
        "PBR FONU": "PBR FİYAT"
    }
    
    # 2. DİNAMİK LİSTE (Hisseler İçin)
    for item in watchlist:
        # Hisse ise (THYAO.IS)
        if ".IS" in item:
            clean_name = item.replace(".IS", "")
            mapping[f"{clean_name} (Hisse)"] = f"{item} FİYAT"
            
    return mapping

# --- HESAPLAMA MOTORLARI ---
def save_transaction(date_obj, tur, varlik, islem, adet, fiyat):
    try:
        client = get_client()
        sheet = client.open(SHEET_NAME)
        ws = sheet.worksheet("Islemler")
        date_str = date_obj.strftime("%d.%m.%Y")
        row = [date_str, tur, varlik, islem, str(adet).replace(".", ","), str(fiyat).replace(".", ",")]
        ws.append_row(row, value_input_option='USER_ENTERED')
        st.success(f"✅ İşlem Eklendi: {varlik}")
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
        
        current_list = ws.col_values(1)
        if symbol not in current_list:
            ws.append_row([symbol])
            return True
        return False
    except: return False

def calculate_portfolio_holdings(df_trans):
    portfolio = {} 
    if df_trans.empty: return portfolio
    for _, row in df_trans.iterrows():
        varlik = row['Varlık']
        islem = str(row['İşlem']).upper().strip()
        adet = float(row['Adet'])
        fiyat = float(row['Fiyat'])
        tutar = adet * fiyat
        tur = row['Tür']
        if varlik not in portfolio: portfolio[varlik] = {"adet": 0.0, "toplam_maliyet": 0.0, "tur": tur}
        curr = portfolio[varlik]
        if islem == "ALIS":
            curr["adet"] += adet
            curr["toplam_maliyet"] += tutar
        elif islem == "SATIS":
            if curr["adet"] > 0:
                avg = curr["toplam_maliyet"] / curr["adet"]
                curr["toplam_maliyet"] -= (adet * avg)
                curr["adet"] -= adet
            else: curr["adet"] -= adet
    return portfolio

def calculate_wealth_at_snapshot(row_prices, portfolio_holdings, asset_map, rate=1.0):
    total = 0
    for varlik_adi, stats in portfolio_holdings.items():
        adet = stats["adet"]
        if adet <= 0: continue
        price_key = asset_map.get(varlik_adi)
        if not price_key: continue
        price = 1.0 if price_key == "NAKİT" else row_prices.get(price_key, 0)
        brut = adet * price
        maliyet = stats["toplam_maliyet"]
        kar = brut - maliyet
        vergi = 0
        if "FON" in stats["tur"].upper() and kar > 0: vergi = kar * FON_VERGI_ORANI
        total += (brut - vergi)
    return total / rate

def prepare_historical_trend(df_prices, df_trans, asset_map, rate=1.0):
    if df_prices.empty: return pd.DataFrame()
    df_prices = df_prices.sort_values("Tarih").reset_index(drop=True)
    if not df_trans.empty: df_trans = df_trans.sort_values("Tarih").reset_index(drop=True)
    
    trend_data = []
    running_port = {}
    trans_idx = 0
    total_trans = len(df_trans)
    
    for _, price_row in df_prices.iterrows():
        curr_date = price_row['Tarih']
        while trans_idx < total_trans:
            tr = df_trans.iloc[trans_idx]
            if tr['Tarih'] <= curr_date:
                var = tr['Varlık']
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
            if not pk: continue
            p = 1.0 if pk == "NAKİT" else price_row.get(pk, 0)
            tot += (qty * p)
        
        if tot > 0: trend_data.append({"Tarih": curr_date, "Toplam Servet": tot/rate})
    return pd.DataFrame(trend_data)

def get_historical_price(df_prices, col_name, days_ago):
    if df_prices.empty or col_name not in df_prices.columns: return 0
    target = datetime.now() - timedelta(days=days_ago)
    row = df_prices.iloc[(df_prices['Tarih'] - target).abs().argsort()[:1]]
    return row.iloc[0][col_name] if not row.empty else 0

def calculate_performance_table(df_prices, portfolio, asset_map, rate=1.0):
    data = []
    if df_prices.empty: return pd.DataFrame()
    last = df_prices.iloc[-1]
    
    for varlik, stats in portfolio.items():
        if stats["adet"] <= 0: continue
        pk = asset_map.get(varlik)
        if not pk or pk == "NAKİT": continue
        curr_p = last.get(pk, 0)
        if curr_p == 0: continue
        
        p1 = get_historical_price(df_prices, pk, 1)
        p7 = get_historical_price(df_prices, pk, 7)
        p30 = get_historical_price(df_prices, pk, 30)
        p180 = get_historical_price(df_prices, pk, 180)
        
        def pct(old, new): return ((new - old) / old * 100) if old > 0 else 0
        data.append({
            "Varlık": varlik, "Anlık Fiyat": curr_p/rate,
            "1 Gün": pct(p1, curr_p), "1 Hafta": pct(p7, curr_p),
            "1 Ay": pct(p30, curr_p), "6 Ay": pct(p180, curr_p)
        })
    return pd.DataFrame(data)

# --- ANA UYGULAMA ---
def main():
    df_prices, df_trans, watchlist = load_data()
    # Dinamik Mapping Oluştur
    ASSET_MAPPING = create_asset_mapping(watchlist)
    
    # --- SIDEBAR ---
    with st.sidebar:
        st.title("💎 Varlık Paneli")
        page = st.radio("Menü", ["Portföyüm", "Piyasa Takip"])
        st.divider()
        
        if not df_prices.empty:
            last_prices = df_prices.iloc[-1]
            usd = last_prices.get("DOLAR KURU", 1.0)
            if usd == 0: usd = 1.0
            st.write(f"💵 Dolar: **{format_tr_money(usd)} TL**")
        else: usd = 1.0
        
        if st.button("🔄 Yenile"):
            st.cache_data.clear()
            st.rerun()
            
        st.divider()
        with st.expander("➕ İşlem Ekle"):
            with st.form("add_trans"):
                f_date = st.date_input("Tarih", datetime.now())
                f_tur = st.selectbox("Tür", ["ALTIN", "FON", "HİSSE", "NAKİT", "DÖVİZ"])
                f_varlik = st.selectbox("Varlık", list(ASSET_MAPPING.keys()))
                f_islem = st.selectbox("İşlem", ["ALIS", "SATIS"])
                f_adet = st.number_input("Adet", min_value=0.0, step=0.01)
                f_fiyat = st.number_input("Fiyat", min_value=0.0, step=0.01)
                if st.form_submit_button("Kaydet"):
                    save_transaction(f_date, f_tur, f_varlik, f_islem, f_adet, f_fiyat)
                    
        with st.expander("🛠️ Takip Listesi"):
            new_sym = st.text_input("Sembol (Örn: SASA.IS, TLY)")
            if st.button("Ekle"):
                if add_to_watchlist_sheet(new_sym): st.success("Eklendi! Bot bir dahaki sefere çekecek.")
                else: st.warning("Zaten var veya hata.")

    # --- SAYFA 1: PORTFÖY ---
    if page == "Portföyüm":
        st.markdown("<h2 style='text-align: center;'>💎 Varlık Portföyü</h2>", unsafe_allow_html=True)
        if not df_trans.empty and not df_prices.empty:
            portfolio = calculate_portfolio_holdings(df_trans)
            
            tab_tl, tab_usd = st.tabs(["🇹🇷 TL Görünüm", "🇺🇸 USD Görünüm"])
            for tab, currency, rate in [(tab_tl, "TL", 1.0), (tab_usd, "$", usd)]:
                with tab:
                    # Tablo Verisi Hazırla
                    rows = []
                    last_prices = df_prices.iloc[-1]
                    for varlik, stats in portfolio.items():
                        adet = stats["adet"]
                        if adet <= 0.001: continue
                        pk = ASSET_MAPPING.get(varlik)
                        fiyat = 1.0 if pk == "NAKİT" else last_prices.get(pk, 0)
                        brut = adet * fiyat
                        maliyet = stats["toplam_maliyet"]
                        vergi = (brut - maliyet) * FON_VERGI_ORANI if "FON" in stats["tur"] and brut > maliyet else 0
                        net = brut - vergi
                        rows.append({
                            "Grup": stats["tur"], "Varlık": varlik, "Adet": adet,
                            "Fiyat": fiyat/rate, "Maliyet": maliyet/rate, "Net Değer": net/rate,
                            "Net Kâr": (net - maliyet)/rate
                        })
                    df_view = pd.DataFrame(rows)
                    
                    if not df_view.empty:
                        # KPI Kartları
                        tot_wealth = df_view["Net Değer"].sum()
                        tot_profit = df_view["Net Kâr"].sum()
                        tot_cost = df_view["Maliyet"].sum()
                        ratio = (tot_profit / tot_cost * 100) if tot_cost > 0 else 0
                        
                        prev_w = calculate_wealth_at_snapshot(df_prices.iloc[-2], portfolio, ASSET_MAPPING, rate)
                        diff_pct = (tot_wealth - prev_w) / prev_w * 100 if prev_w > 0 else 0
                        
                        c1, c2, c3 = st.columns(3)
                        c1.metric("Toplam Varlık", f"{format_tr_money(tot_wealth)} {currency}", delta_color="off")
                        c2.metric("Net Kâr", f"{format_tr_money(tot_profit)} {currency}", f"{format_tr_percent(diff_pct)} (Son Veri)")
                        c3.metric("Kâr Oranı", f"{format_tr_percent(ratio)}")
                        st.divider()
                        
                        # Hedef (TL Sadece)
                        if currency == "TL":
                            prog = min(tot_wealth/HEDEF_SERVET_TL, 1.0)
                            st.subheader(f"🎯 Hedef: {format_tr_money(HEDEF_SERVET_TL)} TL")
                            st.progress(prog)
                            st.caption(f"Kalan: {format_tr_money(HEDEF_SERVET_TL - tot_wealth)} TL")
                            st.divider()
                            
                        # Tablo
                        st.subheader("📋 Varlık Detayı")
                        df_view["Kâr %"] = df_view.apply(lambda x: x["Net Kâr"]/x["Maliyet"]*100 if x["Maliyet"]>0 else 0, axis=1)
                        st.dataframe(df_view.style.format({
                            "Adet": format_tr_nodigit, "Fiyat": format_tr_money, "Maliyet": format_tr_money,
                            "Net Değer": format_tr_money, "Net Kâr": format_tr_money, "Kâr %": format_tr_percent
                        }), use_container_width=True, hide_index=True)
                        st.divider()
                        
                        # Grafik (Area Chart - Zoomlu)
                        st.subheader("📈 Servet Değişimi")
                        df_trend = prepare_historical_trend(df_prices, df_trans, ASSET_MAPPING, rate)
                        if not df_trend.empty:
                            fig = px.area(df_trend, x="Tarih", y="Toplam Servet")
                            min_y = df_trend["Toplam Servet"].min() * 0.999
                            max_y = df_trend["Toplam Servet"].max() * 1.001
                            fig.update_layout(yaxis_range=[min_y, max_y], height=400, hovermode="x unified")
                            fig.update_traces(line_color='#2E8B57', fillcolor='rgba(46, 139, 87, 0.2)')
                            st.plotly_chart(fig, use_container_width=True, key=f"trend_{currency}")
                        
                        # Pasta ve Bar
                        c1, c2 = st.columns(2)
                        with c1:
                            st.subheader("Dağılım")
                            # Türkçe Etiketli Pasta
                            df_pie = df_view.groupby("Grup")["Net Değer"].sum().reset_index()
                            total_p = df_pie["Net Değer"].sum()
                            df_pie["Etiket"] = df_pie.apply(lambda x: f"<b>{x['Grup']}</b><br>%{x['Net Değer']/total_p*100:.2f}", axis=1)
                            fig_p = px.pie(df_pie, values="Net Değer", names="Grup", hole=0.5)
                            fig_p.update_traces(text=df_pie["Etiket"], textinfo="text", textfont_size=15)
                            st.plotly_chart(fig_p, use_container_width=True, key=f"pie_{currency}")
                        with c2:
                            st.subheader("Maliyet vs Değer")
                            fig_b = go.Figure()
                            fig_b.add_trace(go.Bar(name='Maliyet', x=df_view['Varlık'], y=df_view['Maliyet'], marker_color='lightgrey'))
                            fig_b.add_trace(go.Bar(name='Net Değer', x=df_view['Varlık'], y=df_view['Net Değer'], marker_color='forestgreen'))
                            st.plotly_chart(fig_b, use_container_width=True, key=f"bar_{currency}")
                        
                        # Performans Tablosu
                        st.subheader("📊 Performans Karnesi")
                        df_perf = calculate_performance_table(df_prices, portfolio, ASSET_MAPPING, rate)
                        if not df_perf.empty:
                            st.dataframe(df_perf.style.format({
                                "Anlık Fiyat": format_tr_money, "1 Gün": format_tr_percent, 
                                "1 Hafta": format_tr_percent, "1 Ay": format_tr_percent, "6 Ay": format_tr_percent
                            }), use_container_width=True, hide_index=True)
                        
                        # Altın Makas
                        st.subheader("🥇 Altın Makas")
                        gold_cols = st.columns(4)
                        for i, (name, key) in enumerate([("Gram", "GRAM ALTIN"), ("Ata", "ATA ALTIN"), ("22 Ayar", "22 AYAR ALTIN"), ("Çeyrek", "ÇEYREK ALTIN")]):
                            alis = last_prices.get(f"{key} ALIŞ", 0) / rate
                            satis = last_prices.get(f"{key} SATIŞ", 0) / rate
                            makas = satis - alis
                            yuzde = makas/satis*100 if satis>0 else 0
                            gold_cols[i].metric(name, format_tr_money(satis), f"Makas: {format_tr_money(makas)} (%{yuzde:.2f})", delta_color="inverse")

    # --- SAYFA 2: PİYASA TAKİP ---
    elif page == "Piyasa Takip":
        st.markdown("<h2 style='text-align: center;'>🌍 Piyasa İzleme</h2>", unsafe_allow_html=True)
        if not df_prices.empty:
            last = df_prices.iloc[-1]
            prev = df_prices.iloc[-2] if len(df_prices)>1 else last
            
            market_data = []
            # Tüm fiyat sütunlarını tara
            for col in df_prices.columns:
                if "FİYAT" in col or "ALTIN" in col or "DOLAR" in col:
                    if col == "Tarih": continue
                    name = col.replace(" FİYAT", "").replace(" ALIŞ", "").replace(" SATIŞ", "")
                    price = last[col]
                    old_price = prev[col]
                    diff = (price - old_price) / old_price if old_price > 0 else 0
                    
                    market_data.append({
                        "Enstrüman": name,
                        "Fiyat": price,
                        "Değişim": diff
                    })
            
            df_m = pd.DataFrame(market_data)
            
            # ŞIK TABLO GÖSTERİMİ (Formatlı)
            st.dataframe(
                df_m,
                column_config={
                    "Enstrüman": st.column_config.TextColumn("Varlık", width="medium"),
                    "Fiyat": st.column_config.NumberColumn("Fiyat (TL)", format="%.2f TL", width="medium"),
                    "Değişim": st.column_config.NumberColumn("Değişim", format="%.2f %%", width="small")
                },
                use_container_width=True,
                hide_index=True,
                height=(len(df_m) * 35) + 38 # Otomatik yükseklik (Scroll yok)
            )
        else: st.warning("Veri yok.")

if __name__ == "__main__":
    main()
