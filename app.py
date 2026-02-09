import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Kişisel Varlık Paneli", page_icon="💎", layout="wide", initial_sidebar_state="collapsed")

# --- AYARLAR ---
SHEET_NAME = "PortfoyVerileri"
HEDEF_SERVET_TL = 2000000 
HEDEF_TARIH = datetime(2026, 2, 28)
FON_VERGI_ORANI = 0.175

# VARLIK TANIMLARI VE EŞLEŞTİRME ANAHTARLARI
# Botun getirdiği fiyat isimleri (key) ile bizim ekranda gördüğümüz isimler (label)
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
        
        # 1. Fiyat Verileri (PortfoyVerileri Sayfası)
        ws_prices = sheet.worksheet("PortfoyVerileri")
        data_prices = ws_prices.get_all_values()
        if len(data_prices) > 1:
            df_prices = pd.DataFrame(data_prices[1:], columns=data_prices[0])
            # Sayı düzeltme
            for col in df_prices.columns:
                if col != "Tarih":
                    df_prices[col] = df_prices[col].astype(str).str.replace(".", "").str.replace(",", ".")
                    df_prices[col] = pd.to_numeric(df_prices[col], errors='coerce').fillna(0)
            df_prices['Tarih'] = pd.to_datetime(df_prices['Tarih'])
        else:
            df_prices = pd.DataFrame()

        # 2. İşlem Verileri (Islemler Sayfası)
        try:
            ws_trans = sheet.worksheet("Islemler")
            data_trans = ws_trans.get_all_values()
            if len(data_trans) > 1:
                df_trans = pd.DataFrame(data_trans[1:], columns=data_trans[0])
                # Sayısal çevrim
                df_trans['Adet'] = pd.to_numeric(df_trans['Adet'].astype(str).str.replace(",", "."), errors='coerce').fillna(0)
                df_trans['Fiyat'] = pd.to_numeric(df_trans['Fiyat'].astype(str).str.replace(",", "."), errors='coerce').fillna(0)
            else:
                df_trans = pd.DataFrame(columns=["Tarih", "Tür", "Varlık", "İşlem", "Adet", "Fiyat"])
        except:
            st.error("⚠️ 'Islemler' sayfası bulunamadı! Lütfen Google Sheets'te oluşturun.")
            df_trans = pd.DataFrame()

        return df_prices, df_trans

    except Exception as e:
        st.error(f"Veri Hatası: {e}")
        return pd.DataFrame(), pd.DataFrame()

# --- PORTFÖY HESAPLAMA MOTORU (MUHASEBE) ---
def calculate_portfolio_status(df_trans):
    """
    İşlemler sayfasını okur, her varlık için:
    - Güncel Adet
    - Ortalama Maliyet
    hesaplar.
    """
    portfolio_summary = {} # { "Varlık Adı": {"adet": 10, "maliyet": 500} }
    
    if df_trans.empty: return portfolio_summary

    for index, row in df_trans.iterrows():
        varlik = row['Varlık']
        islem = str(row['İşlem']).upper().strip() # ALIS / SATIS
        adet = float(row['Adet'])
        fiyat = float(row['Fiyat'])
        tutar = adet * fiyat
        tur = row['Tür']

        if varlik not in portfolio_summary:
            portfolio_summary[varlik] = {"adet": 0.0, "toplam_maliyet_tutari": 0.0, "tur": tur}
        
        current = portfolio_summary[varlik]

        if islem == "ALIS":
            current["adet"] += adet
            current["toplam_maliyet_tutari"] += tutar
        elif islem == "SATIS":
            # Satış yapıldığında maliyet tutarı, satılan oranda azalır (Ortalama Maliyet Yöntemi)
            if current["adet"] > 0:
                birim_maliyet = current["toplam_maliyet_tutari"] / current["adet"]
                dusen_maliyet = adet * birim_maliyet
                current["toplam_maliyet_tutari"] -= dusen_maliyet
                current["adet"] -= adet
            else:
                # Elde yokken satış girilirse (Short gibi)
                current["adet"] -= adet
        
    return portfolio_summary

def main():
    st.markdown("<h1 style='text-align: center; color: #DAA520;'>💎 Kişisel Varlık Paneli (V2.0)</h1>", unsafe_allow_html=True)
    
    df_prices, df_trans = load_data()
    
    if not df_prices.empty and not df_trans.empty:
        last_prices = df_prices.iloc[-1]
        usd_rate = last_prices.get("DOLAR KURU", 1.0)
        if usd_rate == 0: usd_rate = 1.0
        
        # 1. Portföy Durumunu Hesapla (Islemler Sayfasından)
        portfolio_status = calculate_portfolio_status(df_trans)
        
        # 2. Anlık Değerlerle Birleştir
        metrics_data = []
        
        for varlik_adi, stats in portfolio_status.items():
            adet = stats["adet"]
            if adet <= 0.001: continue # Adeti 0 olanları gösterme
            
            toplam_maliyet = stats["toplam_maliyet_tutari"]
            tur = stats["tur"]
            
            # Anlık Fiyat Bulma
            price_key = ASSET_MAPPING.get(varlik_adi)
            anlik_birim_fiyat = 0
            
            if price_key == "NAKİT":
                anlik_birim_fiyat = 1.0
            elif price_key:
                anlik_birim_fiyat = last_prices.get(price_key, 0)
            
            brut_deger = adet * anlik_birim_fiyat
            
            # Vergi (Sadece Fonlarda Kâr varsa)
            vergi = 0
            potansiyel_kar = brut_deger - toplam_maliyet
            if "FON" in tur.upper() and potansiyel_kar > 0:
                vergi = potansiyel_kar * FON_VERGI_ORANI
            
            net_deger = brut_deger - vergi
            net_kar = net_deger - toplam_maliyet
            
            metrics_data.append({
                "Grup": tur,
                "Varlık": varlik_adi,
                "Adet": adet,
                "Birim Fiyat": anlik_birim_fiyat,
                "Toplam Maliyet": toplam_maliyet,
                "Brüt Değer": brut_deger,
                "Vergi": vergi,
                "Net Servet": net_deger,
                "Net Kâr": net_kar
            })
            
        df_metrics = pd.DataFrame(metrics_data)

        # --- GÖRÜNÜM AYARLARI ---
        with st.sidebar:
            st.header("⚙️ Ayarlar")
            st.write(f"💵 Dolar: **{format_tr_money(usd_rate)} TL**")
            if st.button("🔄 Yenile"):
                st.cache_data.clear()
                st.rerun()

        tab_tl, tab_usd = st.tabs(["🇹🇷 TL Görünüm", "🇺🇸 USD Görünüm"])
        
        for tab, currency, rate in [(tab_tl, "TL", 1.0), (tab_usd, "$", usd_rate)]:
            with tab:
                # Kura göre dönüştür
                df_view = df_metrics.copy()
                cols_to_convert = ["Birim Fiyat", "Toplam Maliyet", "Brüt Değer", "Vergi", "Net Servet", "Net Kâr"]
                for c in cols_to_convert:
                    df_view[c] = df_view[c] / rate
                
                df_view["Kâr Oranı (%)"] = df_view.apply(lambda x: (x["Net Kâr"] / x["Toplam Maliyet"] * 100) if x["Toplam Maliyet"] > 0 else 0, axis=1)

                # TOPLAMLAR
                total_net_wealth = df_view["Net Servet"].sum()
                total_tax = df_view["Vergi"].sum()
                total_net_profit = df_view["Net Kâr"].sum()
                total_cost = df_view["Toplam Maliyet"].sum()
                genel_kar_orani = (total_net_profit / total_cost * 100) if total_cost > 0 else 0

                # METRİKLER
                c1, c2, c3 = st.columns([2, 1, 1])
                c1.metric("🚀 TOPLAM PORTFÖY (NET)", f"{currency} {format_tr_money(total_net_wealth)}", f"Vergi: -{currency}{format_tr_money(total_tax)}", delta_color="inverse")
                c2.metric("💰 Net Kâr", f"{currency} {format_tr_money(total_net_profit)}", delta_color="normal")
                c3.metric("📈 Genel Kâr Oranı", f"{format_tr_percent(genel_kar_orani)}")
                
                st.divider()
                
                # HEDEF
                if currency == "TL":
                    kalan = HEDEF_SERVET_TL - total_net_wealth
                    prog = min(total_net_wealth / HEDEF_SERVET_TL, 1.0)
                    days_left = (HEDEF_TARIH - datetime.now()).days
                    st.subheader(f"🎯 Hedef: {format_tr_money(HEDEF_SERVET_TL)} TL")
                    st.progress(prog)
                    c_h1, c_h2 = st.columns(2)
                    c_h1.caption(f"Kalan: **{format_tr_money(kalan)} TL**")
                    c_h2.caption(f"Bitiş: **28 Şubat 2026** ({days_left} gün)")
                    st.divider()

                # TABLO
                st.subheader("📋 Detaylı Varlık Tablosu")
                st.dataframe(
                    df_view.style.format({
                        "Adet": format_tr_nodigit,
                        "Birim Fiyat": format_tr_money,
                        "Toplam Maliyet": format_tr_money,
                        "Brüt Değer": format_tr_nodigit,
                        "Vergi": format_tr_money,
                        "Net Servet": format_tr_money,
                        "Net Kâr": format_tr_money,
                        "Kâr Oranı (%)": format_tr_percent
                    }), use_container_width=True, hide_index=True
                )
                
                # GRAFİKLER
                st.divider()
                c_g1, c_g2 = st.columns(2)
                with c_g1:
                    st.subheader("Varlık Dağılımı")
                    fig_p = px.pie(df_view, values='Net Servet', names='Grup', hole=0.5)
                    fig_p.update_traces(textinfo='percent+label', textfont_size=18)
                    st.plotly_chart(fig_p, use_container_width=True)
                with c_g2:
                    st.subheader("Kâr/Zarar Durumu")
                    fig_b = go.Figure()
                    fig_b.add_trace(go.Bar(name='Maliyet', x=df_view['Varlık'], y=df_view['Toplam Maliyet'], marker_color='lightgrey'))
                    fig_b.add_trace(go.Bar(name='Net Değer', x=df_view['Varlık'], y=df_view['Net Servet'], marker_color='forestgreen'))
                    st.plotly_chart(fig_b, use_container_width=True)

    else:
        st.info("☁️ Veri bekleniyor... (Lütfen 'Islemler' sayfasını oluşturduğundan emin ol)")
    
    time.sleep(60)
    st.rerun()

if __name__ == "__main__":
    main()
