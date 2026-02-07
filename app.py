import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
import plotly.graph_objects as go
import plotly.express as px
import uuid
from datetime import datetime, timedelta

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Kişisel Varlık Paneli", page_icon="💎", layout="wide", initial_sidebar_state="collapsed")

# --- AYARLAR ---
SHEET_NAME = "PortfoyVerileri"
HEDEF_SERVET_TL = 2500000 
FON_VERGI_ORANI = 0.175

PORTFOLIO = {
    "ALTIN": {
        "22 AYAR BİLEZİK (Gr)": {"key": "22 AYAR ALTIN ALIŞ", "adet": 101, "maliyet": 2080.0}, 
        "ATA ALTIN (Adet)": {"key": "ATA ALTIN ALIŞ", "adet": 13, "maliyet": 15000.0},
        "ÇEYREK ALTIN (Adet)": {"key": "ÇEYREK ALTIN ALIŞ", "adet": 1, "maliyet": 3750.0}
    },
    "FON": {
        "TLY FONU": {"key": "TLY FİYAT", "adet": 123, "maliyet": 3277.87461},
        "DFI FONU": {"key": "DFI FİYAT", "adet": 22895, "maliyet": 2.395146},
        "TP2 FONU": {"key": "TP2 FİYAT", "adet": 5679, "maliyet": 1.67554}
    },
    "NAKİT": 42000.0
}

# --- FORMATLAMA FONKSİYONLARI ---
def format_tr_money(value):
    """Para birimi formatı: 1.234,56"""
    if pd.isna(value) or value == "": return "-"
    try:
        val = float(value)
        s = "{:,.2f}".format(val)
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except: return str(value)

def format_tr_nodigit(value):
    """Kuruşsuz format: 1.234"""
    if pd.isna(value) or value == "": return "-"
    try:
        val = float(value)
        s = "{:,.0f}".format(val)
        return s.replace(",", "X").replace(".", ",").replace("X", ".")
    except: return str(value)

def format_tr_percent(value):
    """Yüzde formatı: %12,34"""
    if pd.isna(value) or value == "": return "-"
    try:
        val = float(value)
        s = "{:,.2f}".format(val)
        return "%" + s.replace(",", "X").replace(".", ",").replace("X", ".")
    except: return str(value)

@st.cache_data(ttl=60)
def load_data():
    try:
        credentials_dict = st.secrets["gcp_service_account"]
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
        client = gspread.authorize(creds)
        sheet = client.open(SHEET_NAME).sheet1
        
        data = sheet.get_all_values()
        if len(data) < 2: return pd.DataFrame()
        
        headers = data[0]
        rows = data[1:]
        df = pd.DataFrame(rows, columns=headers)
        
        def fix_turkish_number(val):
            if val is None or val == "": return 0.0
            val = str(val).strip()
            val = val.replace(".", "")
            val = val.replace(",", ".")
            try: return float(val)
            except: return 0.0

        for col in df.columns:
            if col != "Tarih":
                df[col] = df[col].apply(fix_turkish_number)
        
        df['Tarih'] = pd.to_datetime(df['Tarih'])
        return df
    except Exception as e:
        return pd.DataFrame()

def calculate_net_wealth_value(row, currency_rate=1.0):
    total = 0
    for info in PORTFOLIO["ALTIN"].values():
        total += row.get(info["key"], 0) * info["adet"]
    for info in PORTFOLIO["FON"].values():
        p = row.get(info["key"], 0)
        brut = p * info["adet"]
        kar = brut - (info["maliyet"] * info["adet"])
        vergi = kar * FON_VERGI_ORANI if kar > 0 else 0
        total += (brut - vergi)
    total += PORTFOLIO["NAKİT"]
    return total / currency_rate

def calculate_daily_change(df, current_wealth, currency_rate=1.0):
    if len(df) < 2: return 0, 0
    try:
        target_date = df.iloc[-1]['Tarih'] - timedelta(days=1)
        idx = (df['Tarih'] - target_date).abs().idxmin()
        old_wealth = calculate_net_wealth_value(df.loc[idx], currency_rate)
        diff = current_wealth - old_wealth
        pct = (diff / old_wealth * 100) if old_wealth > 0 else 0
        return diff, pct
    except: return 0, 0

def calculate_full_metrics(last_row, kur=1.0):
    data = []
    for name, info in PORTFOLIO["ALTIN"].items():
        gf = last_row.get(info["key"], 0)
        tm = info["maliyet"] * info["adet"]
        bd = gf * info["adet"]
        kar = bd - tm
        data.append({"Grup": "Altın", "Varlık": name, "Toplam Maliyet": tm, "Brüt Değer": bd, "Vergi": 0, "Net Servet": bd, "Net Kâr": kar})
    for name, info in PORTFOLIO["FON"].items():
        gf = last_row.get(info["key"], 0)
        tm = info["maliyet"] * info["adet"]
        bd = gf * info["adet"]
        kar = bd - tm
        vergi = kar * FON_VERGI_ORANI if kar > 0 else 0
        data.append({"Grup": "Fon", "Varlık": name, "Toplam Maliyet": tm, "Brüt Değer": bd, "Vergi": vergi, "Net Servet": bd - vergi, "Net Kâr": kar - vergi})
    cash = PORTFOLIO["NAKİT"]
    data.append({"Grup": "Nakit", "Varlık": "TL Bakiye", "Toplam Maliyet": cash, "Brüt Değer": cash, "Vergi": 0, "Net Servet": cash, "Net Kâr": 0})
    
    df = pd.DataFrame(data)
    for c in ["Toplam Maliyet", "Brüt Değer", "Vergi", "Net Servet", "Net Kâr"]:
        df[c] = df[c] / kur
    df["Kâr Oranı (%)"] = df.apply(lambda x: (x["Net Kâr"] / x["Toplam Maliyet"] * 100) if x["Toplam Maliyet"] > 0 else 0, axis=1)
    return df

def prepare_total_trend_chart(df_raw, currency_rate=1.0):
    trend_data = []
    for index, row in df_raw.iterrows():
        dt = row['Tarih']
        total_wealth = calculate_net_wealth_value(row, currency_rate)
        trend_data.append({"Tarih": dt, "Toplam Servet": total_wealth})
    return pd.DataFrame(trend_data)

def main():
    st.markdown("<h1 style='text-align: center; color: #DAA520;'>💎 Kişisel Varlık Paneli</h1>", unsafe_allow_html=True)
    df_csv = load_data()
    
    if not df_csv.empty:
        last_row = df_csv.iloc[-1]
        usd = last_row.get("DOLAR KURU", 1.0)
        if usd == 0 or pd.isna(usd): usd = 1.0
        
        with st.sidebar:
            st.header("⚙️ Ayarlar")
            st.write(f"💵 Dolar: **{format_tr_money(usd)} TL**")
            st.write(f"⚖️ Stopaj: **%{FON_VERGI_ORANI*100}**")
            if st.button("🔄 Yenile"):
                st.cache_data.clear()
                st.rerun()

        tab_tl, tab_usd = st.tabs(["🇹🇷 TL Görünüm", "🇺🇸 USD Görünüm"])
        for tab, currency, rate in [(tab_tl, "TL", 1.0), (tab_usd, "$", usd)]:
            with tab:
                df_m = calculate_full_metrics(last_row, rate)
                net_wealth = df_m["Net Servet"].sum()
                net_profit = df_m["Net Kâr"].sum()
                daily_chg, daily_pct = calculate_daily_change(df_csv, net_wealth, rate)
                
                c1, c2, c3 = st.columns([2, 1, 1])
                c1.metric("🚀 TOPLAM SERVET", f"{currency} {format_tr_money(net_wealth)}", f"{format_tr_percent(daily_pct)} (24s)")
                c2.metric("💰 Net Kâr", f"{currency} {format_tr_money(net_profit)}", delta_color="inverse")
                c3.metric("📈 Genel Kâr Oranı", f"{format_tr_percent((net_profit/df_m['Toplam Maliyet'].sum()*100))}")
                st.divider()
                
                if currency == "TL":
                    prog = min(net_wealth / HEDEF_SERVET_TL, 1.0)
                    st.subheader(f"🎯 Hedef: {format_tr_money(HEDEF_SERVET_TL)} TL")
                    st.progress(prog)
                    st.divider()

                st.subheader("📋 Detaylı Varlık & Kâr Tablosu")
                st.dataframe(
                    df_m.style.format({
                        "Toplam Maliyet": format_tr_money,
                        "Brüt Değer": format_tr_nodigit,
                        "Vergi": format_tr_money,
                        "Net Servet": format_tr_money,
                        "Net Kâr": format_tr_money,
                        "Kâr Oranı (%)": format_tr_percent
                    }), 
                    use_container_width=True, 
                    hide_index=True
                )
                
                st.divider()

                st.subheader(f"📈 Zamansal Servet Değişimi ({currency})")
                df_trend = prepare_total_trend_chart(df_csv, rate)
                if not df_trend.empty:
                    fig_trend = px.area(df_trend, x="Tarih", y="Toplam Servet", line_shape='spline')
                    fig_trend.update_layout(xaxis_title=None, yaxis_title=None, height=400, hovermode="x unified", showlegend=False)
                    fig_trend.update_traces(line_color='#2E8B57', fillcolor='rgba(46, 139, 87, 0.2)')
                    st.plotly_chart(fig_trend, use_container_width=True, key=f"trend_{currency}_{uuid.uuid4()}")
                st.divider()

                col_g1, col_g2 = st.columns(2)
                with col_g1:
                    st.subheader("Varlık Dağılımı")
                    fig_p = px.pie(df_m, values='Net Servet', names='Grup', hole=0.5, color='Grup', color_discrete_map={'Altın':'#FFD700', 'Fon':'#4169E1', 'Nakit':'#90EE90'})
                    st.plotly_chart(fig_p, use_container_width=True, key=f"p_{currency}_{uuid.uuid4()}")
                with col_g2:
                    st.subheader("Maliyet vs Net Değer")
                    fig_b = go.Figure()
                    fig_b.add_trace(go.Bar(name='Maliyet', x=df_m['Varlık'], y=df_m['Toplam Maliyet'], marker_color='lightgrey'))
                    fig_b.add_trace(go.Bar(name='Net Servet', x=df_m['Varlık'], y=df_m['Net Servet'], marker_color='forestgreen'))
                    fig_b.update_layout(barmode='group')
                    st.plotly_chart(fig_b, use_container_width=True, key=f"b_{currency}_{uuid.uuid4()}")
                
                # --- YENİ BÖLÜM: ALTIN PİYASASI VE MAKAS ANALİZİ ---
                st.divider()
                st.subheader("🥇 Canlı Piyasa: Altın Alış-Satış ve Makas Analizi (TL)")
                
                gold_cols = st.columns(4)
                # Gösterilecek altın tipleri ve veri anahtarları
                gold_types = [
                    ("Gram Altın", "GRAM ALTIN"),
                    ("Ata Altın", "ATA ALTIN"),
                    ("22 Ayar Bilezik", "22 AYAR ALTIN"),
                    ("Çeyrek Altın", "ÇEYREK ALTIN")
                ]

                for i, (name, key_prefix) in enumerate(gold_types):
                    # Verileri çek
                    alis = last_row.get(f"{key_prefix} ALIŞ", 0) / rate
                    satis = last_row.get(f"{key_prefix} SATIŞ", 0) / rate
                    
                    # Makas hesabı
                    fark = satis - alis
                    if satis > 0:
                        yuzde_makas = (fark / satis) * 100
                    else:
                        yuzde_makas = 0
                    
                    with gold_cols[i]:
                        # Özel tasarım kutucuk (CSS ile)
                        st.markdown(f"""
                        <div style="
                            border: 1px solid #444; 
                            border-radius: 8px; 
                            padding: 10px; 
                            background-color: #262730;
                            text-align: center;">
                            <h4 style="margin: 0; color: #FFD700;">{name}</h4>
                            <div style="margin-top: 5px; font-size: 0.9em; color: #AAA;">Alış / Satış</div>
                            <div style="font-size: 1.1em; font-weight: bold; margin: 5px 0;">
                                {format_tr_money(alis)} / <span style="color:#4CAF50">{format_tr_money(satis)}</span>
                            </div>
                            <div style="
                                background-color: #3e2723; 
                                color: #ffab91; 
                                border-radius: 4px; 
                                padding: 2px 5px; 
                                font-size: 0.85em;
                                margin-top: 8px;">
                                ✂ Makas: {format_tr_money(fark)} (%{yuzde_makas:.2f})
                            </div>
                        </div>
                        """, unsafe_allow_html=True)

    else:
        st.info("☁️ Veri bekleniyor...")
    
    time.sleep(60)
    st.rerun()

if __name__ == "__main__":
    main()
