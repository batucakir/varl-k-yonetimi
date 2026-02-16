import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime
import numpy as np

# -------------------------------------------------
# SAYFA AYARLARI
# -------------------------------------------------
st.set_page_config(
    page_title="Varlık Paneli",
    page_icon="💎",
    layout="wide",
    initial_sidebar_state="expanded"
)

# -------------------------------------------------
# AYARLAR
# -------------------------------------------------
SHEET_NAME = "PortfoyVerileri"
CONFIG_SHEET_NAME = "Ayarlar"
HEDEF_SERVET_TL = 2000000
HEDEF_TARIH = datetime(2026, 2, 28)
FON_VERGI_ORANI = 0.175

# -------------------------------------------------
# YARDIMCI FONKSİYONLAR
# -------------------------------------------------
def clean_numeric(value):
    if pd.isna(value) or value in ["", None]:
        return 0.0
    s = str(value).strip()
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return 0.0


def format_tr_money(value):
    try:
        return "{:,.2f}".format(float(value)).replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return "-"


def get_client():
    credentials_dict = st.secrets["gcp_service_account"]
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
    return gspread.authorize(creds)


@st.cache_data(ttl=60)
def load_data():
    try:
        client = get_client()
        sheet = client.open(SHEET_NAME)

        # Fiyatlar
        ws_prices = sheet.sheet1
        data_prices = ws_prices.get_all_values()

        if len(data_prices) > 1:
            df_prices = pd.DataFrame(data_prices[1:], columns=data_prices[0])

            for col in df_prices.columns:
                if col != "Tarih":
                    df_prices[col] = df_prices[col].apply(clean_numeric)

            df_prices["Tarih"] = pd.to_datetime(df_prices["Tarih"], errors="coerce")
            df_prices = df_prices.dropna(subset=["Tarih"]).sort_values("Tarih")

            # 0 değerleri eksik kabul edip ffill
            price_cols = [c for c in df_prices.columns if c != "Tarih"]
            df_prices[price_cols] = (
                df_prices[price_cols]
                .replace(0, np.nan)
                .ffill()
                .fillna(0)
            )
        else:
            df_prices = pd.DataFrame()

        # İşlemler
        ws_trans = sheet.worksheet("Islemler")
        data_trans = ws_trans.get_all_values()

        if len(data_trans) > 1:
            df_trans = pd.DataFrame(data_trans[1:], columns=data_trans[0])
            df_trans["Adet"] = df_trans["Adet"].apply(clean_numeric)
            df_trans["Fiyat"] = df_trans["Fiyat"].apply(clean_numeric)
            df_trans["Tarih"] = pd.to_datetime(df_trans["Tarih"], dayfirst=True, errors="coerce")
        else:
            df_trans = pd.DataFrame()

        # Watchlist
        try:
            ws_conf = sheet.worksheet(CONFIG_SHEET_NAME)
            watchlist = [x for x in ws_conf.col_values(1)[1:] if x]
        except Exception:
            watchlist = []

        return df_prices, df_trans, watchlist

    except Exception:
        return pd.DataFrame(), pd.DataFrame(), []


# -------------------------------------------------
# PORTFÖY HESAPLAMA
# -------------------------------------------------
def calculate_portfolio(df_trans, df_prices):
    if df_trans.empty or df_prices.empty:
        return pd.DataFrame(), 0, 0

    port = {}
    last_prices = df_prices.iloc[-1]

    for _, row in df_trans.iterrows():
        varlik = str(row["Varlık"]).strip()
        islem = str(row["İşlem"]).upper().strip()
        adet = row["Adet"]
        fiyat = row["Fiyat"]
        tur = row["Tür"]

        if varlik not in port:
            port[varlik] = {"adet": 0.0, "maliyet": 0.0, "tur": tur}

        if islem == "ALIS":
            port[varlik]["adet"] += adet
            port[varlik]["maliyet"] += adet * fiyat
        else:
            if port[varlik]["adet"] > 0:
                ort = port[varlik]["maliyet"] / port[varlik]["adet"]
                port[varlik]["maliyet"] -= adet * ort
                port[varlik]["adet"] -= adet

    rows = []
    toplam = 0
    toplam_vergi = 0

    for v, d in port.items():
        if d["adet"] <= 0:
            continue

        fiyat = last_prices.get(v, 0)
        deger = d["adet"] * fiyat

        vergi = 0
        if "FON" in str(d["tur"]).upper() and deger > d["maliyet"]:
            vergi = (deger - d["maliyet"]) * FON_VERGI_ORANI

        net = deger - vergi

        rows.append(
            {
                "Grup": d["tur"],
                "Varlık": v,
                "Net Değer": net,
                "Net Kâr": net - d["maliyet"],
            }
        )

        toplam += net
        toplam_vergi += vergi

    return pd.DataFrame(rows), toplam, toplam_vergi


# -------------------------------------------------
# ANA PROGRAM
# -------------------------------------------------
def main():
    df_prices, df_trans, watchlist = load_data()

    if df_prices.empty:
        st.warning("Veri bulunamadı.")
        return

    df_view, toplam, toplam_vergi = calculate_portfolio(df_trans, df_prices)

    st.title("💎 Varlık Paneli")

    st.subheader("Toplam Varlık")
    st.metric("Toplam", format_tr_money(toplam) + " TL")

    st.subheader("Hedef")
    st.progress(min(toplam / HEDEF_SERVET_TL, 1.0))

    kalan = HEDEF_SERVET_TL - toplam
    kalan_yuzde = (kalan / HEDEF_SERVET_TL) * 100

    st.write(f"Kalan: {format_tr_money(kalan)} TL (%{kalan_yuzde:.1f})")

    # Servet Grafiği
    st.subheader("📈 Servet Değişimi")

    fig = px.line(df_prices, x="Tarih", y=df_prices.columns[1])
    fig.update_layout(height=400)
    st.plotly_chart(fig, use_container_width=True)


if __name__ == "__main__":
    main()
