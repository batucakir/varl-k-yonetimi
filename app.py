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

# --- KRİTİK: FON LİSTESİ (BURAYA EKLENMEYEN FON GÖRÜNMEZ) ---
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
            # Sayısal dönüşüm
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
                df_trans['Adet'] = pd.to_numeric(df_trans['Adet'].astype(str).str.replace(",", "."), errors='coerce').fillna(0)
                df_trans['Fiyat'] = pd.to_numeric(df_trans['Fiyat'].astype(str).str.replace(",", "."), errors='coerce').fillna(0)
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

# --- VARLIK EŞLEŞTİRME (MAPPING) - FONLAR İÇİN DÜZELTİLDİ ---
def create_asset_mapping(watchlist):
    # 1. Sabit Varlıklar
    mapping = {
        "22 AYAR BİLEZİK (Gr)": "22 AYAR ALTIN ALIŞ",
        "ATA ALTIN (Adet)": "ATA ALTIN ALIŞ",
        "ÇEYREK ALTIN (Adet)": "ÇEYREK ALTIN ALIŞ",
        "TL Bakiye": "NAKİT"
    }
    
    # 2. Fonları Zorla Ekle (Excel'de "TLY FONU" yazıyor, Botta "TLY FİYAT")
    for f in MY_FUNDS:
        mapping[f"{f} FONU"] = f"{f} FİYAT"

    # 3. Hisseleri Dinamik Ekle
    for item in watchlist:
        if ".IS" in item:
            clean_name = item.replace(".IS", "")
            mapping[f"{clean_name} (Hisse)"] = f"{item} FİYAT"
            
    return mapping

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

def calculate_portfolio(df_trans, df_prices, mapping):
    port = {}
    last_prices = df_prices.iloc[-1] if not df_prices.empty else {}
    
    for _, row in df_trans.iterrows():
        varlik = row['Varlık']
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
        
        pk = mapping.get(v)
        # Nakit kontrolü
        curr_price = 1.0 if pk == "NAKİT" else last_prices.get(pk, 0)
        
        # Eğer fiyat 0 geliyorsa (Bot çekememişse), maliyet fiyatından göster ki servet sıfırlanmasın
        if curr_price == 0 and pk != "NAKİT":
             # Opsiyonel: Eski fiyatı bulmaya çalışabiliriz ama şimdilik 0 kalsın hata belli olsun
             pass

        guncel_deger = d["adet"] * curr_price
        
        # Vergi
        vergi = 0
        if "FON" in d["tur"] and guncel_deger > d["maliyet"]:
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

# --- ANA UYGULAMA ---
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
        
        if st.button("🔄 Yenile"):
            st.cache_data.clear()
            st.rerun()
            
        st.divider()
        with st.expander("➕ İşlem Ekle"):
            with st.form("add"):
                f_date = st.date_input("Tarih", datetime.now())
                f_tur = st.selectbox("Tür", ["ALTIN", "FON", "HİSSE", "NAKİT", "DÖVİZ"])
                f_varlik = st.selectbox("Varlık", list(ASSET_MAPPING.keys()))
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
            df_view, tot_wealth = calculate_portfolio(df_trans, df_prices, ASSET_MAPPING)
            
            tab1, tab2 = st.tabs(["🇹🇷 TL Görünüm", "🇺🇸 USD Görünüm"])
            for t, curr, rate in [(tab1, "TL", 1.0), (tab2, "$", usd)]:
                with t:
                    if not df_view.empty:
                        # KPI
                        net_profit = df_view["Net Kâr"].sum()
                        cost = df_view["Maliyet"].sum()
                        ratio = (net_profit / cost * 100) if cost > 0 else 0
                        
                        c1, c2, c3 = st.columns(3)
                        c1.metric("Toplam Varlık", f"{format_tr_money(tot_wealth/rate)} {curr}")
                        c2.metric("Net Kâr", f"{format_tr_money(net_profit/rate)} {curr}")
                        c3.metric("Kâr Oranı", f"%{format_tr_money(ratio)}")
                        st.divider()
                        
                        # Tablo
                        st.subheader("📋 Detaylı Tablo")
                        # Gösterim için kopyala ve formatla
                        df_show = df_view.copy()
                        df_show["Fiyat"] = df_show["Fiyat"] / rate
                        df_show["Maliyet"] = df_show["Maliyet"] / rate
                        df_show["Net Değer"] = df_show["Net Değer"] / rate
                        df_show["Net Kâr"] = df_show["Net Kâr"] / rate
                        df_show["Kâr %"] = df_show.apply(lambda x: x["Net Kâr"]/x["Maliyet"]*100 if x["Maliyet"]>0 else 0, axis=1)
                        
                        st.dataframe(df_show.style.format({
                            "Adet": "{:,.0f}", "Fiyat": "{:,.2f}", "Maliyet": "{:,.2f}",
                            "Net Değer": "{:,.2f}", "Net Kâr": "{:,.2f}", "Kâr %": "%{:,.2f}"
                        }), use_container_width=True, hide_index=True)
                        
                        # Grafikler
                        st.divider()
                        c1, c2 = st.columns(2)
                        with c1:
                            fig = px.pie(df_view, values="Net Değer", names="Grup", hole=0.4)
                            st.plotly_chart(fig, use_container_width=True)
                        with c2:
                            fig = px.bar(df_view, x="Varlık", y=["Maliyet", "Net Kâr"], barmode="stack")
                            st.plotly_chart(fig, use_container_width=True)

    # --- SAYFA 2: PİYASA TAKİP (DÜZELTİLMİŞ) ---
    elif page == "Piyasa Takip":
        st.markdown("## 🌍 Piyasa İzleme")
        if not df_prices.empty:
            last = df_prices.iloc[-1]
            prev = df_prices.iloc[-2] if len(df_prices)>1 else last
            
            # Verileri Kategorize Et
            hisseler, fonlar, emtia = [], [], []
            
            for col in df_prices.columns:
                if col == "Tarih": continue
                
                # Fiyat ve Değişim Hesapla
                price = last[col]
                old = prev[col]
                
                # -1% Hatası Düzeltme: Eğer eski fiyat 0 veya yoksa değişim 0'dır.
                diff = 0
                if old > 0 and price > 0:
                    diff = (price - old) / old
                
                item = {
                    "Enstrüman": col.replace(" FİYAT", "").replace(" ALIŞ", "").replace(" SATIŞ", ""),
                    "Fiyat": price,
                    "Değişim": diff
                }
                
                if ".IS" in col: hisseler.append(item)
                elif "ALTIN" in col or "DOLAR" in col: emtia.append(item)
                elif len(col) <= 10: fonlar.append(item) # Kısa isimler genelde fon
            
            # --- SEKME YAPISI ---
            t1, t2, t3 = st.tabs(["📈 Hisseler", "📊 Fonlar", "🥇 Altın/Döviz"])
            
            def show_table(data_list):
                if not data_list:
                    st.info("Bu kategoride veri yok.")
                    return
                df = pd.DataFrame(data_list)
                
                # Renkli Sütun Ayarı
                st.dataframe(
                    df,
                    column_config={
                        "Enstrüman": st.column_config.TextColumn("Varlık", width="medium"),
                        "Fiyat": st.column_config.NumberColumn("Fiyat (TL)", format="%.2f TL", width="medium"),
                        "Değişim": st.column_config.NumberColumn(
                            "Günlük Değişim",
                            format="%.2f %%",
                            help="Bir önceki kayda göre değişim"
                        )
                    },
                    use_container_width=True,
                    hide_index=True,
                    height=(len(df) * 35) + 38
                )

            with t1: show_table(hisseler)
            with t2: show_table(fonlar)
            with t3: show_table(emtia)

if __name__ == "__main__":
    main()
