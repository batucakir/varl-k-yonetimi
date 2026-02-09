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

# VARLIK EŞLEŞTİRME (Excel İsmi -> Bot İsmi)
ASSET_MAPPING = {
    "22 AYAR BİLEZİK (Gr)": "22 AYAR ALTIN ALIŞ",
    "ATA ALTIN (Adet)": "ATA ALTIN ALIŞ",
    "ÇEYREK ALTIN (Adet)": "ÇEYREK ALTIN ALIŞ",
    "TLY FONU": "TLY FİYAT",
    "DFI FONU": "DFI FİYAT",
    "TP2 FONU": "TP2 FİYAT",
    "TL Bakiye": "NAKİT" 
}

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
    try: return "%" + "{:,.2f}".format(float(value)).replace(",", "X").replace(".", ",").replace("X", ".")
    except: return str(value)

# --- VERİ ÇEKME ---
@st.cache_data(ttl=60)
def load_data():
    try:
        credentials_dict = st.secrets["gcp_service_account"]
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
        client = gspread.authorize(creds)
        sheet = client.open(SHEET_NAME)
        
        # 1. Fiyatlar
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

        # 2. İşlemler
        try:
            ws_trans = sheet.worksheet("Islemler")
            data_trans = ws_trans.get_all_values()
            if len(data_trans) > 1:
                df_trans = pd.DataFrame(data_trans[1:], columns=data_trans[0])
                df_trans['Adet'] = pd.to_numeric(df_trans['Adet'].astype(str).str.replace(",", "."), errors='coerce').fillna(0)
                df_trans['Fiyat'] = pd.to_numeric(df_trans['Fiyat'].astype(str).str.replace(",", "."), errors='coerce').fillna(0)
            else:
                df_trans = pd.DataFrame()
        except:
            st.error("⚠️ 'Islemler' sayfası bulunamadı!")
            df_trans = pd.DataFrame()

        return df_prices, df_trans
    except Exception as e:
        return pd.DataFrame(), pd.DataFrame()

# --- HESAPLAMA MOTORU ---
def calculate_portfolio_holdings(df_trans):
    """İşlemlerden eldeki güncel adedi ve maliyeti bulur"""
    portfolio = {} 
    if df_trans.empty: return portfolio

    for _, row in df_trans.iterrows():
        varlik = row['Varlık']
        islem = str(row['İşlem']).upper().strip()
        adet = float(row['Adet'])
        fiyat = float(row['Fiyat'])
        tutar = adet * fiyat
        tur = row['Tür']

        if varlik not in portfolio:
            portfolio[varlik] = {"adet": 0.0, "toplam_maliyet": 0.0, "tur": tur}
        
        curr = portfolio[varlik]
        if islem == "ALIS":
            curr["adet"] += adet
            curr["toplam_maliyet"] += tutar
        elif islem == "SATIS":
            if curr["adet"] > 0:
                birim_maliyet = curr["toplam_maliyet"] / curr["adet"]
                curr["toplam_maliyet"] -= (adet * birim_maliyet)
                curr["adet"] -= adet
            else:
                curr["adet"] -= adet
    return portfolio

def calculate_wealth_at_time(row_prices, portfolio_holdings, rate=1.0):
    """Belirli bir andaki (row_prices) toplam serveti hesaplar"""
    total = 0
    for varlik_adi, stats in portfolio_holdings.items():
        adet = stats["adet"]
        if adet <= 0: continue
        
        price_key = ASSET_MAPPING.get(varlik_adi)
        if not price_key: continue
        
        price = 1.0 if price_key == "NAKİT" else row_prices.get(price_key, 0)
        
        brut = adet * price
        
        # Vergi düşümü (Sadece grafik için yaklaşık hesap)
        maliyet = stats["toplam_maliyet"]
        kar = brut - maliyet
        vergi = 0
        if "FON" in stats["tur"].upper() and kar > 0:
            vergi = kar * FON_VERGI_ORANI
            
        total += (brut - vergi)
        
    return total / rate

def prepare_trend_chart(df_prices, portfolio_holdings, rate=1.0):
    """Zaman grafiği verisi"""
    trend_data = []
    # Son 1000 veriyi al (çok şişmesin)
    df_subset = df_prices.tail(1000)
    
    for _, row in df_subset.iterrows():
        wealth = calculate_wealth_at_time(row, portfolio_holdings, rate)
        trend_data.append({"Tarih": row['Tarih'], "Toplam Servet": wealth})
    return pd.DataFrame(trend_data)

def main():
    st.markdown("<h1 style='text-align: center; color: #DAA520;'>💎 Kişisel Varlık Paneli</h1>", unsafe_allow_html=True)
    
    df_prices, df_trans = load_data()
    
    if not df_prices.empty and not df_trans.empty:
        last_prices = df_prices.iloc[-1]
        usd = last_prices.get("DOLAR KURU", 1.0)
        if usd == 0: usd = 1.0
        
        # 1. Portföyü Hesapla
        portfolio = calculate_portfolio_holdings(df_trans)
        
        with st.sidebar:
            st.header("⚙️ Ayarlar")
            st.write(f"💵 Dolar: **{format_tr_money(usd)} TL**")
            if st.button("🔄 Yenile"):
                st.cache_data.clear()
                st.rerun()

        tab_tl, tab_usd = st.tabs(["🇹🇷 TL Görünüm", "🇺🇸 USD Görünüm"])
        
        for tab, currency, rate in [(tab_tl, "TL", 1.0), (tab_usd, "$", usd)]:
            with tab:
                # Metrik Tablosunu Hazırla
                rows = []
                for varlik, stats in portfolio.items():
                    adet = stats["adet"]
                    if adet <= 0.001: continue
                    
                    price_key = ASSET_MAPPING.get(varlik)
                    fiyat = 1.0 if price_key == "NAKİT" else last_prices.get(price_key, 0)
                    
                    brut = adet * fiyat
                    maliyet = stats["toplam_maliyet"]
                    
                    # Vergi Hesabı
                    vergi = 0
                    kar = brut - maliyet
                    if "FON" in stats["tur"].upper() and kar > 0:
                        vergi = kar * FON_VERGI_ORANI
                    
                    net_deger = brut - vergi
                    net_kar = net_deger - maliyet
                    
                    rows.append({
                        "Grup": stats["tur"], "Varlık": varlik, "Adet": adet,
                        "Birim Fiyat": fiyat/rate, "Toplam Maliyet": maliyet/rate,
                        "Brüt Değer": brut/rate, "Vergi": vergi/rate,
                        "Net Servet": net_deger/rate, "Net Kâr": net_kar/rate
                    })
                
                df_view = pd.DataFrame(rows)
                
                if not df_view.empty:
                    # TOPLAMLAR
                    tot_wealth = df_view["Net Servet"].sum()
                    tot_tax = df_view["Vergi"].sum()
                    tot_profit = df_view["Net Kâr"].sum()
                    tot_cost = df_view["Toplam Maliyet"].sum()
                    profit_ratio = (tot_profit / tot_cost * 100) if tot_cost > 0 else 0
                    
                    # GÜNLÜK DEĞİŞİM
                    if len(df_prices) > 1:
                        prev_prices = df_prices.iloc[-2] # Bir önceki kayıt
                        prev_wealth = calculate_wealth_at_time(prev_prices, portfolio, rate)
                        diff = tot_wealth - prev_wealth
                        pct = (diff / prev_wealth * 100) if prev_wealth > 0 else 0
                    else:
                        pct = 0

                    # 1. KARTLAR
                    c1, c2, c3 = st.columns([2, 1, 1])
                    c1.metric("🚀 TOPLAM PORTFÖY (NET)", f"{currency} {format_tr_money(tot_wealth)}", f"Vergi: -{currency}{format_tr_money(tot_tax)}", delta_color="inverse")
                    c2.metric("💰 Net Kâr", f"{currency} {format_tr_money(tot_profit)}", f"{format_tr_percent(pct)} (Son Kayıt)", delta_color="normal")
                    c3.metric("📈 Genel Kâr Oranı", f"{format_tr_percent(profit_ratio)}")
                    st.divider()
                    
                    # 2. HEDEF
                    if currency == "TL":
                        kalan = HEDEF_SERVET_TL - tot_wealth
                        kalan_yuzde = (kalan / HEDEF_SERVET_TL * 100) if kalan > 0 else 0
                        prog = min(tot_wealth / HEDEF_SERVET_TL, 1.0)
                        days = (HEDEF_TARIH - datetime.now()).days
                        
                        st.subheader(f"🎯 Hedef: {format_tr_money(HEDEF_SERVET_TL)} TL")
                        st.progress(prog)
                        h1, h2 = st.columns(2)
                        h1.caption(f"🏁 Kalan: **{format_tr_money(kalan)} TL** (▼ {format_tr_percent(kalan_yuzde)})")
                        h2.caption(f"⏳ Bitiş: **28 Şubat 2026** ({days} gün kaldı)")
                        st.divider()

                    # 3. TABLO
                    st.subheader("📋 Detaylı Varlık Tablosu")
                    df_view["Kâr Oranı (%)"] = df_view.apply(lambda x: (x["Net Kâr"]/x["Toplam Maliyet"]*100) if x["Toplam Maliyet"]>0 else 0, axis=1)
                    st.dataframe(df_view.style.format({
                        "Adet": format_tr_nodigit, "Birim Fiyat": format_tr_money, "Toplam Maliyet": format_tr_money,
                        "Brüt Değer": format_tr_nodigit, "Vergi": format_tr_money, "Net Servet": format_tr_money,
                        "Net Kâr": format_tr_money, "Kâr Oranı (%)": format_tr_percent
                    }), use_container_width=True, hide_index=True)
                    st.divider()

                    # 4. GRAFİKLER
                    st.subheader(f"📈 Zamansal Servet Değişimi ({currency})")
                    df_trend = prepare_trend_chart(df_prices, portfolio, rate)
                    if not df_trend.empty:
                        fig_t = px.area(df_trend, x="Tarih", y="Toplam Servet", line_shape='spline')
                        fig_t.update_layout(xaxis_title=None, yaxis_title=None, height=400, hovermode="x unified", showlegend=False)
                        fig_t.update_traces(line_color='#2E8B57', fillcolor='rgba(46, 139, 87, 0.2)')
                        st.plotly_chart(fig_t, use_container_width=True, key=f"trend_{currency}_{uuid.uuid4()}")
                    st.divider()

                    g1, g2 = st.columns(2)
                    with g1:
                        st.subheader("Varlık Dağılımı")
                        fig_p = px.pie(df_view, values='Net Servet', names='Grup', hole=0.5, color='Grup', color_discrete_map={'ALTIN':'#FFD700', 'FON':'#4169E1', 'NAKİT':'#90EE90'})
                        fig_p.update_traces(textinfo='percent+label', textfont_size=18)
                        st.plotly_chart(fig_p, use_container_width=True, key=f"pie_{currency}_{uuid.uuid4()}")
                    with g2:
                        st.subheader("Maliyet vs Net Değer")
                        fig_b = go.Figure()
                        fig_b.add_trace(go.Bar(name='Maliyet', x=df_view['Varlık'], y=df_view['Toplam Maliyet'], marker_color='lightgrey'))
                        fig_b.add_trace(go.Bar(name='Net Değer', x=df_view['Varlık'], y=df_view['Net Servet'], marker_color='forestgreen'))
                        st.plotly_chart(fig_b, use_container_width=True, key=f"bar_{currency}_{uuid.uuid4()}")

                    # 5. ALTIN MAKAS
                    st.divider()
                    st.subheader("🥇 Canlı Piyasa: Altın Alış-Satış ve Makas Analizi (TL)")
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
                                <div style="margin-top: 5px; font-size: 0.9em; color: #AAA;">Alış / Satış</div>
                                <div style="font-size: 1.1em; font-weight: bold; margin: 5px 0;">{format_tr_money(alis)} / <span style="color:#4CAF50">{format_tr_money(satis)}</span></div>
                                <div style="background-color: #3e2723; color: #ffab91; border-radius: 4px; padding: 2px 5px; font-size: 0.85em; margin-top: 8px;">✂ Makas: {format_tr_money(fark)} (%{yuzde:.2f})</div>
                            </div>""", unsafe_allow_html=True)
                else:
                    st.warning("Gösterilecek varlık bulunamadı. Lütfen 'Islemler' sayfasına veri girin.")
    else:
        st.info("☁️ Veri bekleniyor...")
    
    time.sleep(60)
    st.rerun()

if __name__ == "__main__":
    main()
