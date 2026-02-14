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
st.set_page_config(page_title="Kişisel Varlık Paneli", page_icon="💎", layout="wide", initial_sidebar_state="collapsed")

# --- AYARLAR ---
SHEET_NAME = "PortfoyVerileri"
HEDEF_SERVET_TL = 2000000 
HEDEF_TARIH = datetime(2026, 2, 28)
FON_VERGI_ORANI = 0.175

# --- VARLIK LİSTELERİ (BOT İLE AYNI) ---
MY_FUNDS = ["TLY", "DFI", "TP2", "PHE", "ROF", "PBR"]
BIST_30 = [
    "AKBNK", "ALARK", "ARCLK", "ASELS", "ASTOR", "BIMAS", "BRSAN", 
    "DOAS", "EKGYO", "ENKAI", "EREGL", "FROTO", "GARAN", "GUBRF", 
    "HEKTS", "ISCTR", "KCHOL", "KONTR", "KOZAL", "KRDMD", "OYAKC", 
    "PETKM", "PGSUS", "SAHOL", "SASA", "SISE", "TCELL", "THYAO", 
    "TOASO", "TUPRS", "YKBNK"
]
MY_EXTRAS = ["TERA", "TRHOL", "TEHOL", "IEYHO", "ODINE", "MIATK", "HEDEF"]
ALL_STOCK_CODES = sorted(list(set(BIST_30 + MY_EXTRAS)))

# --- ASSET MAPPING OLUŞTURMA (OTOMATİK) ---
# Manuel Eklenenler
ASSET_MAPPING = {
    "22 AYAR BİLEZİK (Gr)": "22 AYAR ALTIN ALIŞ",
    "ATA ALTIN (Adet)": "ATA ALTIN ALIŞ",
    "ÇEYREK ALTIN (Adet)": "ÇEYREK ALTIN ALIŞ",
    "TL Bakiye": "NAKİT"
}
# Fonları Ekle
for f in MY_FUNDS:
    ASSET_MAPPING[f"{f} FONU"] = f"{f} FİYAT"

# Hisseleri Ekle (Otomatik)
for s in ALL_STOCK_CODES:
    # Ekranda "THYAO (Hisse)" görünsün, Botta "THYAO.IS FİYAT" aransın
    ASSET_MAPPING[f"{s} (Hisse)"] = f"{s}.IS FİYAT"

# --- FORMATLAMA ---
def format_tr_money(value):
    if pd.isna(value) or value == "": return "-"
    try: return "{:,.2f}".format(float(value)).replace(",", "X").replace(".", ",").replace("X", ".")
    except: return str(value)

def format_tr_nodigit(value):
    if pd.isna(value) or value == "": return "-"
    try: return "{:,.0f}".format(float(value)).replace(",", "X").replace(".", ",").replace("X", ".")
    except: return str(value)

def format_tr_percent(value):
    if pd.isna(value) or value == "": return "-"
    try:
        val = float(value)
        arrow = "▲" if val > 0 else "▼" if val < 0 else "-"
        return f"{arrow} %" + "{:,.2f}".format(abs(val)).replace(",", "X").replace(".", ",").replace("X", ".")
    except: return str(value)

# --- GOOGLE SHEETS BAĞLANTISI ---
def get_google_sheet_client():
    credentials_dict = st.secrets["gcp_service_account"]
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
    client = gspread.authorize(creds)
    return client

# --- VERİ ÇEKME ---
@st.cache_data(ttl=60)
def load_data():
    try:
        client = get_google_sheet_client()
        sheet = client.open(SHEET_NAME)
        
        ws_prices = sheet.worksheet("PortfoyVerileri")
        data_prices = ws_prices.get_all_values()
        if len(data_prices) > 1:
            df_prices = pd.DataFrame(data_prices[1:], columns=data_prices[0])
            for col in df_prices.columns:
                if col != "Tarih":
                    df_prices[col] = df_prices[col].astype(str).str.replace(".", "").str.replace(",", ".")
                    df_prices[col] = pd.to_numeric(df_prices[col], errors='coerce').fillna(0)
            df_prices['Tarih'] = pd.to_datetime(df_prices['Tarih'])
        else:
            df_prices = pd.DataFrame()

        try:
            ws_trans = sheet.worksheet("Islemler")
            data_trans = ws_trans.get_all_values()
            if len(data_trans) > 1:
                df_trans = pd.DataFrame(data_trans[1:], columns=data_trans[0])
                df_trans['Adet'] = pd.to_numeric(df_trans['Adet'].astype(str).str.replace(",", "."), errors='coerce').fillna(0)
                df_trans['Fiyat'] = pd.to_numeric(df_trans['Fiyat'].astype(str).str.replace(",", "."), errors='coerce').fillna(0)
                df_trans['Tarih'] = pd.to_datetime(df_trans['Tarih'], dayfirst=True)
            else:
                df_trans = pd.DataFrame()
        except:
            df_trans = pd.DataFrame()

        return df_prices, df_trans
    except:
        return pd.DataFrame(), pd.DataFrame()

# --- YENİ İŞLEM KAYDETME ---
def save_transaction(date_obj, tur, varlik, islem, adet, fiyat):
    try:
        client = get_google_sheet_client()
        sheet = client.open(SHEET_NAME)
        ws = sheet.worksheet("Islemler")
        date_str = date_obj.strftime("%d.%m.%Y")
        row = [date_str, tur, varlik, islem, str(adet).replace(".", ","), str(fiyat).replace(".", ",")]
        ws.append_row(row, value_input_option='USER_ENTERED')
        st.success(f"✅ İşlem Başarıyla Eklendi: {varlik} ({islem})")
        time.sleep(1)
        st.cache_data.clear()
        st.rerun()
    except Exception as e:
        st.error(f"Kayıt Hatası: {e}")

# --- GÜNCEL PORTFÖYÜ HESAPLA ---
def calculate_current_portfolio(df_trans):
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
                birim_maliyet = curr["toplam_maliyet"] / curr["adet"]
                curr["toplam_maliyet"] -= (adet * birim_maliyet)
                curr["adet"] -= adet
            else: curr["adet"] -= adet
    return portfolio

# --- ZAMAN MAKİNESİ ---
def prepare_true_historical_trend(df_prices, df_trans, rate=1.0):
    if df_prices.empty: return pd.DataFrame()
    df_prices = df_prices.sort_values("Tarih").reset_index(drop=True)
    if not df_trans.empty:
        df_trans = df_trans.sort_values("Tarih").reset_index(drop=True)
    
    trend_data = []
    running_portfolio = {} 
    trans_idx = 0
    total_trans = len(df_trans)
    
    for _, price_row in df_prices.iterrows():
        current_date = price_row['Tarih']
        while trans_idx < total_trans:
            trans_row = df_trans.iloc[trans_idx]
            trans_date = trans_row['Tarih']
            if trans_date <= current_date:
                varlik = trans_row['Varlık']
                islem = str(trans_row['İşlem']).upper().strip()
                adet = float(trans_row['Adet'])
                curr_qty = running_portfolio.get(varlik, 0.0)
                if islem == "ALIS": running_portfolio[varlik] = curr_qty + adet
                elif islem == "SATIS": running_portfolio[varlik] = curr_qty - adet
                trans_idx += 1
            else: break 
        
        total_wealth = 0
        for varlik_adi, adet in running_portfolio.items():
            if adet <= 0: continue
            price_key = ASSET_MAPPING.get(varlik_adi)
            if not price_key: continue
            price = 1.0 if price_key == "NAKİT" else price_row.get(price_key, 0)
            total_wealth += (adet * price)
        
        if total_wealth > 0:
            trend_data.append({"Tarih": current_date, "Toplam Servet": total_wealth / rate})
        
    return pd.DataFrame(trend_data)

# --- PERFORMANS VE ANLIK ---
def get_historical_price(df_prices, col_name, days_ago):
    if df_prices.empty or col_name not in df_prices.columns: return 0
    target_date = datetime.now() - timedelta(days=days_ago)
    closest_row = df_prices.iloc[(df_prices['Tarih'] - target_date).abs().argsort()[:1]]
    if not closest_row.empty:
        found_date = closest_row.iloc[0]['Tarih']
        if abs((found_date - target_date).days) > 5 and days_ago < 30: return 0 
        return closest_row.iloc[0][col_name]
    return 0

def calculate_asset_performance(df_prices, portfolio, rate=1.0):
    performance_data = []
    if df_prices.empty: return pd.DataFrame()
    last_row = df_prices.iloc[-1]
    for varlik, stats in portfolio.items():
        if stats["adet"] <= 0: continue
        price_key = ASSET_MAPPING.get(varlik)
        if not price_key or price_key == "NAKİT": continue
        current_price = last_row.get(price_key, 0)
        if current_price == 0: continue
        p_1d = get_historical_price(df_prices, price_key, 1)
        p_1w = get_historical_price(df_prices, price_key, 7)
        p_1m = get_historical_price(df_prices, price_key, 30)
        p_6m = get_historical_price(df_prices, price_key, 180)
        def calc_pct(old, new): return ((new - old) / old * 100) if old > 0 else 0
        row = {
            "Varlık": varlik, "Anlık Fiyat": current_price / rate,
            "1 Gün (%)": calc_pct(p_1d, current_price), "1 Hafta (%)": calc_pct(p_1w, current_price),
            "1 Ay (%)": calc_pct(p_1m, current_price), "6 Ay (%)": calc_pct(p_6m, current_price)
        }
        performance_data.append(row)
    return pd.DataFrame(performance_data)

def calculate_wealth_at_snapshot(row_prices, portfolio_holdings, rate=1.0):
    total = 0
    for varlik_adi, stats in portfolio_holdings.items():
        adet = stats["adet"]
        if adet <= 0: continue
        price_key = ASSET_MAPPING.get(varlik_adi)
        if not price_key: continue
        price = 1.0 if price_key == "NAKİT" else row_prices.get(price_key, 0)
        brut = adet * price
        maliyet = stats["toplam_maliyet"]
        kar = brut - maliyet
        vergi = 0
        if "FON" in stats["tur"].upper() and kar > 0: vergi = kar * FON_VERGI_ORANI
        total += (brut - vergi)
    return total / rate

def main():
    st.markdown("<h1 style='text-align: center; color: #DAA520;'>💎 Kişisel Varlık Paneli</h1>", unsafe_allow_html=True)
    df_prices, df_trans = load_data()
    
    if not df_prices.empty and not df_trans.empty:
        last_prices = df_prices.iloc[-1]
        usd = last_prices.get("DOLAR KURU", 1.0)
        if usd == 0: usd = 1.0
        portfolio = calculate_current_portfolio(df_trans)
        
        with st.sidebar:
            st.header("⚙️ Ayarlar")
            st.write(f"💵 Dolar: **{format_tr_money(usd)} TL**")
            if st.button("🔄 Yenile"):
                st.cache_data.clear()
                st.rerun()
            st.divider()
            with st.expander("➕ Yeni İşlem Ekle", expanded=False):
                with st.form("transaction_form"):
                    f_date = st.date_input("Tarih", datetime.now())
                    f_tur = st.selectbox("Tür", ["ALTIN", "FON", "HİSSE", "NAKİT", "DÖVİZ"])
                    # GÜNCEL VARLIK LİSTESİ (HİSSELER DAHİL)
                    asset_options = list(ASSET_MAPPING.keys())
                    f_varlik = st.selectbox("Varlık", asset_options)
                    f_islem = st.selectbox("İşlem", ["ALIS", "SATIS"])
                    f_adet = st.number_input("Adet", min_value=0.0, step=0.01, format="%.2f")
                    f_fiyat = st.number_input("Birim Fiyat (TL)", min_value=0.0, step=0.01, format="%.2f")
                    submitted = st.form_submit_button("💾 Kaydet")
                    if submitted:
                        if f_adet > 0 and f_fiyat > 0:
                            save_transaction(f_date, f_tur, f_varlik, f_islem, f_adet, f_fiyat)
                        else: st.warning("Adet ve Fiyat 0'dan büyük olmalı.")

        tab_tl, tab_usd = st.tabs(["🇹🇷 TL Görünüm", "🇺🇸 USD Görünüm"])
        
        for tab, currency, rate in [(tab_tl, "TL", 1.0), (tab_usd, "$", usd)]:
            with tab:
                rows = []
                for varlik, stats in portfolio.items():
                    adet = stats["adet"]
                    if adet <= 0.001: continue
                    price_key = ASSET_MAPPING.get(varlik)
                    fiyat = 1.0 if price_key == "NAKİT" else last_prices.get(price_key, 0)
                    brut = adet * fiyat
                    maliyet = stats["toplam_maliyet"]
                    vergi = 0
                    kar = brut - maliyet
                    if "FON" in stats["tur"].upper() and kar > 0: vergi = kar * FON_VERGI_ORANI
                    net_deger = brut - vergi
                    net_kar = net_deger - maliyet
                    rows.append({
                        "Grup": stats["tur"], "Varlık": varlik, "Adet": adet,
                        "Birim Fiyat": fiyat/rate, "Toplam Maliyet": maliyet/rate,
                        "Brüt Değer": brut/rate, "Vergi": vergi/rate, "Net Servet": net_deger/rate, "Net Kâr": net_kar/rate
                    })
                df_view = pd.DataFrame(rows)
                
                if not df_view.empty:
                    tot_wealth = df_view["Net Servet"].sum()
                    tot_tax = df_view["Vergi"].sum()
                    tot_profit = df_view["Net Kâr"].sum()
                    tot_cost = df_view["Toplam Maliyet"].sum()
                    profit_ratio = (tot_profit / tot_cost * 100) if tot_cost > 0 else 0
                    if len(df_prices) > 1:
                        prev_wealth = calculate_wealth_at_snapshot(df_prices.iloc[-2], portfolio, rate)
                        diff_val = tot_wealth - prev_wealth
                        diff_pct = (diff_val / prev_wealth * 100) if prev_wealth > 0 else 0
                    else: diff_pct = 0

                    c1, c2, c3 = st.columns([2, 1, 1])
                    c1.metric("🚀 TOPLAM PORTFÖY (NET)", f"{currency} {format_tr_money(tot_wealth)}", f"Vergi: -{currency}{format_tr_money(tot_tax)}", delta_color="inverse")
                    c2.metric("💰 Net Kâr", f"{currency} {format_tr_money(tot_profit)}", f"{format_tr_percent(diff_pct)} (Son Kayıt)", delta_color="normal")
                    c3.metric("📈 Genel Kâr Oranı", f"{format_tr_percent(profit_ratio)}")
                    st.divider()

                    if currency == "TL":
                        prog = min(tot_wealth / HEDEF_SERVET_TL, 1.0)
                        kalan = HEDEF_SERVET_TL - tot_wealth
                        kalan_pct = (kalan / HEDEF_SERVET_TL * 100) if kalan > 0 else 0
                        days = (HEDEF_TARIH - datetime.now()).days
                        st.subheader(f"🎯 Hedef: {format_tr_money(HEDEF_SERVET_TL)} TL")
                        st.progress(prog)
                        h1, h2 = st.columns(2)
                        h1.caption(f"🏁 Kalan: **{format_tr_money(kalan)} TL** (▼ {format_tr_percent(kalan_pct)})")
                        h2.caption(f"⏳ Bitiş: **28 Şubat 2026** ({days} gün kaldı)")
                        st.divider()

                    st.subheader("📋 Detaylı Varlık Tablosu")
                    df_view["Kâr Oranı (%)"] = df_view.apply(lambda x: (x["Net Kâr"]/x["Toplam Maliyet"]*100) if x["Toplam Maliyet"]>0 else 0, axis=1)
                    st.dataframe(df_view.style.format({
                        "Adet": format_tr_nodigit, "Birim Fiyat": format_tr_money, "Toplam Maliyet": format_tr_money,
                        "Brüt Değer": format_tr_nodigit, "Vergi": format_tr_money, "Net Servet": format_tr_money,
                        "Net Kâr": format_tr_money, "Kâr Oranı (%)": format_tr_percent
                    }), use_container_width=True, hide_index=True)
                    st.divider()

                    st.subheader(f"📈 Gerçek Tarihsel Servet Değişimi ({currency})")
                    df_trend = prepare_true_historical_trend(df_prices, df_trans, rate)
                    if not df_trend.empty:
                        fig_t = px.area(df_trend, x="Tarih", y="Toplam Servet")
                        min_y = df_trend["Toplam Servet"].min() * 0.999
                        max_y = df_trend["Toplam Servet"].max() * 1.001
                        fig_t.update_layout(xaxis_title=None, yaxis_title=None, height=450, hovermode="x unified", showlegend=False, yaxis_range=[min_y, max_y])
                        fig_t.update_traces(line_color='#2E8B57', fillcolor='rgba(46, 139, 87, 0.2)')
                        st.plotly_chart(fig_t, use_container_width=True, key=f"trend_{currency}_{uuid.uuid4()}")
                    st.divider()

                    g1, g2 = st.columns(2)
                    with g1:
                        st.subheader("Varlık Dağılımı")
                        dagilim_tipi = st.radio("Görünüm Modu:", ["Ana Gruplar (Altın/Fon/Nakit)", "Detaylı Varlıklar"], horizontal=True, key=f"rad_{currency}")
                        if dagilim_tipi == "Ana Gruplar (Altın/Fon/Nakit)": col_name = 'Grup'
                        else: col_name = 'Varlık'
                        df_pie = df_view.groupby(col_name)["Net Servet"].sum().reset_index()
                        total_pie = df_pie["Net Servet"].sum()
                        df_pie["Yuzde"] = (df_pie["Net Servet"] / total_pie * 100)
                        df_pie["Etiket"] = df_pie.apply(lambda x: f"<b>{x[col_name]}</b><br>%{str(f'{x.Yuzde:.2f}').replace('.', ',')}", axis=1)
                        fig_p = px.pie(df_pie, values='Net Servet', names=col_name, hole=0.5)
                        fig_p.update_traces(text=df_pie["Etiket"], textinfo='text', textfont_size=17)
                        st.plotly_chart(fig_p, use_container_width=True, key=f"pie_{currency}_{uuid.uuid4()}")
                        
                    with g2:
                        st.subheader("Kâr/Zarar")
                        fig_b = go.Figure()
                        fig_b.add_trace(go.Bar(name='Maliyet', x=df_view['Varlık'], y=df_view['Toplam Maliyet'], marker_color='lightgrey'))
                        fig_b.add_trace(go.Bar(name='Net Değer', x=df_view['Varlık'], y=df_view['Net Servet'], marker_color='forestgreen'))
                        st.plotly_chart(fig_b, use_container_width=True, key=f"bar_{currency}_{uuid.uuid4()}")
                    st.divider()
                    
                    st.subheader("📊 Varlık Performans Karnesi (Geçmişe Kıyasla Değişim)")
                    df_perf = calculate_asset_performance(df_prices, portfolio, rate)
                    if not df_perf.empty:
                         st.dataframe(df_perf.style.format({
                            "Anlık Fiyat": format_tr_money, "1 Gün (%)": format_tr_percent, "1 Hafta (%)": format_tr_percent, "1 Ay (%)": format_tr_percent, "6 Ay (%)": format_tr_percent
                        }), use_container_width=True, hide_index=True)
                    
                    st.divider()
                    st.subheader("🥇 Altın Makas Analizi")
                    gold_cols = st.columns(4)
                    gold_types = [("Gram Altın", "GRAM ALTIN"), ("Ata Altın", "ATA ALTIN"), ("22 Ayar Bilezik", "22 AYAR ALTIN"), ("Çeyrek Altın", "ÇEYREK ALTIN")]
                    for i, (name, key_prefix) in enumerate(gold_types):
                        alis = last_prices.get(f"{key_prefix} ALIŞ", 0) / rate
                        satis = last_prices.get(f"{key_prefix} SATIŞ", 0) / rate
                        fark = satis - alis
                        yuzde = (fark / satis * 100) if satis > 0 else 0
                        with gold_cols[i]:
                             st.markdown(f"""
                            <div style="border: 1px solid #444; border-radius: 8px; padding: 10px; background-color: #262730; text-align: center;">
                                <h4 style="margin: 0; color: #FFD700;">{name}</h4>
                                <div style="font-size: 1.1em; font-weight: bold; margin: 5px 0;">{format_tr_money(alis)} / <span style="color:#4CAF50">{format_tr_money(satis)}</span></div>
                                <div style="background-color: #3e2723; color: #ffab91; border-radius: 4px; padding: 2px 5px; font-size: 0.85em; margin-top: 8px;">✂ Makas: {format_tr_money(fark)} (%{yuzde:.2f})</div>
                            </div>""", unsafe_allow_html=True)
    else:
        st.info("☁️ Veri bekleniyor...")
    time.sleep(60)
    st.rerun()

if __name__ == "__main__":
    main()
