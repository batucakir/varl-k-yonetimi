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
    st.error("⚠️ 'yfinance' kütüphanesi eksik!")
    st.stop()

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Varlık Paneli", page_icon="💎", layout="wide", initial_sidebar_state="expanded")
SNAPSHOT_SHEET_NAME = "Snapshot"
# --- ÖZEL CSS ---
st.markdown("""
<style>
/* Uygulama & sidebar arka plan */
[data-testid="stAppViewContainer"] {
    background-color: #050608;
}
[data-testid="stSidebar"] {
    background-color: #101119;
    border-right: 1px solid #242632;
}

/* Genel yazı tipi & renkler */
html, body, [class*="css"]  {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif;
    color: #f5f5f5;
}

/* Ana başlık */
.main-title {
    text-align: center;
    color: #4e8cff;
    font-size: 26px;
    font-weight: 800;
    letter-spacing: 0.5px;
    margin-bottom: 8px;
}

/* Küçük başlık (sidebar info satırları) */
.sidebar-label {
    font-size: 13px;
    font-weight: 600;
    color: #d0d3ff;
    margin-top: 10px;
    margin-bottom: -4px;
}

/* Tarih / saat yazıları */
.sidebar-caption {
    font-size: 12px;
    color: #b0b3c5;
}

/* USD / EUR kartları */
.currency-card {
    background: radial-gradient(circle at 10% 20%, #25293a 0%, #151623 80%);
    padding: 10px;
    border-radius: 12px;
    border: 1px solid #34384a;
    margin-bottom: 8px;
    text-align: center;
    box-shadow: 0 8px 20px rgba(0,0,0,0.35);
}
.currency-title {
    font-size: 13px;
    color: #b7b9cc;
    margin-bottom: 2px;
}
.currency-value {
    font-size: 22px;
    font-weight: 700;
    color: #ffffff;
}

/* Metric değerleri biraz büyük ve kalın */
[data-testid="stMetricValue"] {
    font-size: 28px;
    font-weight: 800;
}

/* Bölüm kartı (ana sayfadaki bloklar için) */
.section-card {
    border-radius: 16px;
    padding: 16px 18px;
    margin-bottom: 20px;
    background: linear-gradient(135deg, #111320 0%, #141724 60%, #10121c 100%);
    border: 1px solid #26293a;
    box-shadow: 0 10px 25px rgba(0,0,0,0.45);
}

/* Section başlık çizgisi */
.section-title {
    font-size: 18px;
    font-weight: 700;
    margin-bottom: 10px;
    display: flex;
    align-items: center;
    gap: 8px;
}
.section-title span {
    font-size: 20px;
}

/* DataFrame header'larını biraz koyulaştıralım */
thead tr th {
    background-color: #1c1f2b !important;
    color: #f5f5f5 !important;
}

/* Tabs üzerindeki ince çizgiyi kaldırıp daha temiz görünüm */
button[role="tab"] {
    border-radius: 999px !important;
    padding: 4px 14px !important;
}

/* Expander başlıkları biraz daha okunaklı olsun */
.streamlit-expanderHeader {
    font-weight: 600;
    font-size: 14px;
    color: #e2e4ff;
}

/* Kur kartı değişim satırları */
.currency-change-row {
    font-size: 11px;
    margin-top: 4px;
    line-height: 1.3;
}

.currency-change-label {
    color: #9ca0b8;
    margin-right: 4px;
}

.currency-change-pos {
    color: #4caf50;
    font-weight: 600;
}

.currency-change-neg {
    color: #ff5252;
    font-weight: 600;
}

.currency-change-flat {
    color: #b0b3c5;
    font-weight: 500;
}

/* Radio / menü butonları daha pill gibi dursun */
[data-baseweb="radio"] > div {
    background: #161827;
    padding: 4px 6px;
    border-radius: 999px;
}
[data-baseweb="radio"] label {
    padding: 2px 10px;
    border-radius: 999px;
}

/* Biraz button hover efekti */
button[kind="secondary"] {
    border-radius: 999px !important;
}
button:hover {
    filter: brightness(1.05);
}

</style>
""", unsafe_allow_html=True)


# --- AYARLAR ---
SHEET_NAME = "PortfoyVerileri"
CONFIG_SHEET_NAME = "Ayarlar"
HEDEF_SERVET_TL = 2250000
HEDEF_TARIH = datetime(2026, 3, 31)
FON_VERGI_ORANI = 0.175
MY_FUNDS = ["TLY", "DFI", "TP2", "PHE", "ROF", "PBR"]

# --- YARDIMCI FONKSİYONLAR ---
def clean_numeric(value):
    if pd.isna(value) or value == "" or value is None:
        return 0.0
    s = str(value).strip()
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except:
        return 0.0

def format_tr_money(value):
    if pd.isna(value) or value == 0:
        return "-"
    try:
        return "{:,.2f}".format(float(value)).replace(",", "X").replace(".", ",").replace("X", ".")
    except:
        return str(value)
        
def pretty_metric(value, currency="TL"):
    try:
        v = float(value or 0.0)
    except:
        v = 0.0
    if abs(v) < 1e-12:
        return f"0 {currency}"
    return f"{format_tr_money(v)} {currency}"

# --- VERİ BAĞLANTISI ---
def get_client():
    credentials_dict = st.secrets["gcp_service_account"]
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_dict(credentials_dict, scope)
    return gspread.authorize(creds)
    
def ensure_snapshot_sheet(sheet):
    """
    Snapshot sheet yoksa oluşturur ve header yazar.
    """
    try:
        ws = sheet.worksheet(SNAPSHOT_SHEET_NAME)
        return ws
    except:
        ws = sheet.add_worksheet(title=SNAPSHOT_SHEET_NAME, rows="1000", cols="20")
        ws.append_row([
            "Tarih",
            "ToplamServetTL",
            "NetYatirimTL",
            "PerformansTL",
            "ALTIN_pct",
            "FON_pct",
            "NAKIT_pct",
            "HISSE_pct",
            "DOVIZ_pct",
        ], value_input_option="USER_ENTERED")
        return ws

@st.cache_data(ttl=60)
def load_data():
    try:
        client = get_client()
        sheet = client.open(SHEET_NAME)

        # Prices sheet (sheet1)
        ws_prices = sheet.sheet1
        data_prices = ws_prices.get_all_values()
        if len(data_prices) > 1:
            df_prices = pd.DataFrame(data_prices[1:], columns=data_prices[0])
            for col in df_prices.columns:
                if col != "Tarih":
                    df_prices[col] = df_prices[col].apply(clean_numeric)

            df_prices['Tarih'] = pd.to_datetime(df_prices['Tarih'], errors='coerce')
            df_prices = df_prices.dropna(subset=['Tarih']).sort_values("Tarih").copy()

            # ✅ EKLEME: 0 olan fiyatları eksik say -> ffill ile düzelt
            price_cols = [c for c in df_prices.columns if c != "Tarih"]
            df_prices[price_cols] = df_prices[price_cols].replace(0, np.nan).ffill().fillna(0)
        else:
            df_prices = pd.DataFrame()

        # Transactions
        ws_trans = sheet.worksheet("Islemler")
        data_trans = ws_trans.get_all_values()

        if len(data_trans) > 1:
            df_trans = pd.DataFrame(data_trans[1:], columns=data_trans[0])

            # 🔧 BURAYI EKLE: duplicate sütun isimlerini at
            df_trans = df_trans.loc[:, ~df_trans.columns.duplicated()]
        else:
            df_trans = pd.DataFrame(columns=["Tarih","Tür","Varlık","İşlem","Adet","Fiyat"])

        if "Kaynak" not in df_trans.columns:
            df_trans["Kaynak"] = ""

        if not df_trans.empty:
            df_trans['Adet'] = df_trans['Adet'].apply(clean_numeric)
            df_trans['Fiyat'] = df_trans['Fiyat'].apply(clean_numeric)
            df_trans['Tarih'] = pd.to_datetime(df_trans['Tarih'], dayfirst=True, errors='coerce')
            # ✅ Kaynak kolonu yoksa (eski sheet'ler için) oluştur
            if "Kaynak" not in df_trans.columns:
                df_trans["Kaynak"] = ""

        # Watchlist
        try:
            ws_conf = sheet.worksheet(CONFIG_SHEET_NAME)
            watchlist = [x for x in ws_conf.col_values(1)[1:] if x]
        except:
            watchlist = []

        return df_prices, df_trans, watchlist
    except:
        return pd.DataFrame(), pd.DataFrame(), []

def find_smart_price(row, asset_name):
    s = str(asset_name).upper()
    
    # 1. NAKİT KONTROLÜ
    if "TL BAKIYE" in s:
        return 1.0

    # 2. ALTIN KONTROLÜ (Tam Eşleşme)
    gmap = {
        "22 AYAR BİLEZİK (GR)": "22 AYAR ALTIN ALIŞ",
        "ATA ALTIN (ADET)": "ATA ALTIN ALIŞ",
        "ÇEYREK ALTIN (ADET)": "ÇEYREK ALTIN ALIŞ",
        "22 AYAR BİLEZİK": "22 AYAR ALTIN ALIŞ",
        "ATA ALTIN": "ATA ALTIN ALIŞ",
        "ÇEYREK ALTIN": "ÇEYREK ALTIN ALIŞ"
    }
    if s in gmap:
        return float(row.get(gmap[s], 0))

    # 3. HİSSE KONTROLÜ (Tam Eşleşme)
    # "ODINE HİSSE" içinden sadece "ODINE" kısmını alalım
    ticker = s.replace("HİSSE", "").replace("HISSE", "").strip()
    
    # Excel sütunlarında "ODINE.IS FİYAT" veya "ODINE FİYAT" ara
    exact_match_1 = f"{ticker}.IS FİYAT"
    exact_match_2 = f"{ticker} FİYAT"
    
    if exact_match_1 in row.index:
        return float(row[exact_match_1] or 0)
    if exact_match_2 in row.index:
        return float(row[exact_match_2] or 0)
    if ticker in row.index: # TLY, PHE gibi fonlar için
        return float(row[ticker] or 0)

    return 0.0

def calculate_portfolio(df_trans, df_prices):
    if df_trans.empty or df_prices.empty:
        return pd.DataFrame(), 0, 0

    port = {}
    last_prices = df_prices.iloc[-1]

    for _, row in df_trans.iterrows():
        v = str(row.get('Varlık', '')).strip()
        isl = str(row.get('İşlem', '')).upper().strip()
        ad = float(row.get('Adet', 0.0) or 0.0)
        fi = float(row.get('Fiyat', 0.0) or 0.0)
        tur = row.get('Tür', '')

        if v == "":
            continue

        if v not in port:
            port[v] = {"adet": 0.0, "maliyet": 0.0, "tur": tur}

        if isl == "ALIS":
            port[v]["adet"] += ad
            port[v]["maliyet"] += (ad * fi)
        else:
            if port[v]["adet"] > 0:
                avg = port[v]["maliyet"] / port[v]["adet"]
                port[v]["maliyet"] -= (ad * avg)
                port[v]["adet"] -= ad
            else:
                port[v]["adet"] -= ad

    rows, tot_w, tot_t = [], 0.0, 0.0
    for v, d in port.items():
        if d["adet"] <= 0.001:
            continue

        cp = find_smart_price(last_prices, v)
        val = d["adet"] * cp

        vergi = 0.0
        if "FON" in str(d["tur"]).upper() and val > d["maliyet"]:
            # PHE fonu stopaj yoksa vergi alma
            if "PHE" in str(v).upper():
                vergi = 0.0
            else:
                vergi = (val - d["maliyet"]) * FON_VERGI_ORANI


        nd = val - vergi

        rows.append({
            "Grup": d["tur"],
            "Varlık": v,
            "Adet": d["adet"],
            "Fiyat": cp,
            "Maliyet": d["maliyet"],
            "Net Değer": nd,
            "Net Kâr": nd - d["maliyet"],
            "Vergi": vergi
        })
        tot_w += nd
        tot_t += vergi

    return pd.DataFrame(rows), tot_w, tot_t
    
TZ_OFFSET = 3  # Türkiye: UTC+3

def now_tr():
    # Sunucu UTC ise yerel TR saati:
    return datetime.utcnow() + timedelta(hours=TZ_OFFSET)

def _canon_asset_name(v: str) -> str:
    """
    Aynı varlığın farklı yazımlarını tek anahtara indirger.
    Örn:
      'ASELS.IS (Hisse)' -> 'ASELS.IS'
      'ASELS HISSE'      -> 'ASELS'
      'TLY FONU'         -> 'TLY'
      'TL Bakiye'        -> 'TL BAKIYE'
    """
    s = str(v or "").strip().upper()

    s = s.replace("(HİSSE)", "").replace("(HISSE)", "")
    s = s.replace(" HİSSE", "").replace(" HISSE", "")
    s = s.replace(" FONU", "").replace(" FON", "")
    s = s.strip()

    if "TL" in s and "BAKIYE" in s:
        return "TL BAKIYE"

    return s


def _normalize_islem(val: str) -> str:
    """
    İşlem tipini normalize eder:
    'Satış', 'SATIŞ', 'satis' -> 'SATIS'
    'Alış', 'ALIŞ', 'alis'    -> 'ALIS'
    """
    s = str(val or "").upper().strip()
    s = s.replace("Ş", "S")
    return s

def _normalize_date(dt):
    """
    Tarih objesini güvenli şekilde sadece date() tipine indirger.
    """
    if pd.isna(dt):
        return None
    try:
        return pd.Timestamp(dt).date()
    except:
        return None

def calculate_realized_pnl(df_trans):
    """
    Portföy içi realized P&L:
      - sadece PORTFOY_ICI işlemler
      - NAKİT / TL Bakiye bacakları hariç
      - total_realized : tüm zamanların toplam realized'ı
      - month_realized : SON realized gününün bulunduğu ayın toplam realized'ı
      - today_realized : SON realized gününün realized'ı
    """
    if df_trans is None or df_trans.empty:
        return 0.0, 0.0, 0.0

    # Tarihe göre sırala, NaN'leri at
    df = df_trans.dropna(subset=["Tarih"]).sort_values("Tarih").copy()

    # Bu satırlar yoksa bile güvenli ol
    if "Kaynak" not in df.columns:
        df["Kaynak"] = ""

    positions = {}     # varlık -> {adet, maliyet}
    rows = []          # her satış için: gün + realized

    for _, row in df.iterrows():
        raw_v = str(row.get("Varlık", "")).strip()
        v = _canon_asset_name(raw_v)

        tur = str(row.get("Tür", "")).upper().strip()
        kaynak = str(row.get("Kaynak", "")).upper().strip()
        islem = _normalize_islem(row.get("İşlem", ""))
        adet = float(row.get("Adet", 0) or 0)
        fiyat = float(row.get("Fiyat", 0) or 0)
        tarih = row.get("Tarih")
        gun = _normalize_date(tarih)

        # Geçersiz kayıtları at
        if not v or adet <= 0 or gun is None:
            continue

        # Dış giriş/çıkışlar realized değil → sadece PORTFOY_ICI
        if kaynak and kaynak != "PORTFOY_ICI":
            continue

        # Nakit ve TL Bakiye hiç girmesin
        if v == "TL BAKIYE" or tur == "NAKİT":
            continue

        # Pozisyon sözlüğü
        if v not in positions:
            positions[v] = {"adet": 0.0, "maliyet": 0.0}

        if islem == "ALIS":
            positions[v]["adet"] += adet
            positions[v]["maliyet"] += adet * fiyat

        elif islem == "SATIS":
            held = positions[v]["adet"]
            if held <= 0:
                # Eldeki adetten fazla satmış eski kayıt vs → realized sayma
                continue

            qty = min(adet, held)
            avg_cost = positions[v]["maliyet"] / held if held > 0 else 0.0
            realized = (fiyat - avg_cost) * qty

            # Gün bazlı kayıt tut
            rows.append({
                "Gun": gun,
                "Varlık": v,
                "Adet": qty,
                "Fiyat": fiyat,
                "MaliyetOrt": avg_cost,
                "Realized": realized,
            })

            # Pozisyonu azalt
            positions[v]["adet"] -= qty
            positions[v]["maliyet"] -= avg_cost * qty

    # Hiç satış yoksa
    if not rows:
        return 0.0, 0.0, 0.0

    df_sales = pd.DataFrame(rows)

    # Güvenlik: çok küçük yuvarlama hatalarını sıfıra çek
    df_sales["Realized"] = df_sales["Realized"].round(6)

    df_sales["Gun"] = pd.to_datetime(df_sales["Gun"])
    per_day = df_sales.groupby("Gun")["Realized"].sum().sort_index()

    total_realized = float(per_day.sum())

    last_day = per_day.index.max()   # Timestamp

    month_mask = (
        (per_day.index.year == last_day.year) &
        (per_day.index.month == last_day.month)
    )
    month_realized = float(per_day[month_mask].sum())

    today_realized = float(per_day.loc[last_day])

    return total_realized, month_realized, today_realized

def calculate_external_cashflows(df_trans):
    """
    Dış nakit akışlarını toplar:
    - Varlık = TL BAKIYE
    - Kaynak = DIS_GIRIS / DIS_CIKIS
    Çıktı:
      total_in  : tüm DIS_GIRIS (pozitif)
      total_out : tüm DIS_CIKIS (pozitif)
      month_net : son nakit akışı gününün AYI için net (giriş-çıkış)
      today_net : son nakit akışı gününün neti
    """
    if df_trans is None or df_trans.empty:
        return 0.0, 0.0, 0.0, 0.0

    df = df_trans.dropna(subset=["Tarih"]).copy()
    if "Kaynak" not in df.columns:
        df["Kaynak"] = ""

    # Normalizasyon
    df["Varlık_u"]  = df["Varlık"].astype(str).str.upper().str.strip()
    df["Kaynak_u"]  = df["Kaynak"].astype(str).str.upper().str.strip()
    df["Islem_u"]   = df["İşlem"].astype(str).str.upper().str.strip()
    df["Tarih_dt"]  = pd.to_datetime(df["Tarih"], errors="coerce")

    # Sadece TL BAKIYE ve DIS_GIRIS / DIS_CIKIS satırları
    df = df[
        (df["Varlık_u"] == "TL BAKIYE") &
        (df["Kaynak_u"].isin(["DIS_GIRIS", "DIS_CIKIS"]))
    ].copy()

    if df.empty:
        return 0.0, 0.0, 0.0, 0.0

    # Net tutar (TL cinsinden): DIS_GIRIS = +, DIS_CIKIS = -
    def _net(row):
        amt = float(row.get("Adet", 0) or 0)
        if row["Kaynak_u"] == "DIS_GIRIS" and row["Islem_u"] == "ALIS":
            return amt
        if row["Kaynak_u"] == "DIS_CIKIS" and row["Islem_u"] == "SATIS":
            return -amt
        return 0.0

    df["Net"] = df.apply(_net, axis=1)

    # Toplam giriş / çıkış
    total_in  = float(df.loc[df["Net"] > 0, "Net"].sum())
    total_out = float(-df.loc[df["Net"] < 0, "Net"].sum())

    # Gün bazlı net
    df["Gun"] = df["Tarih_dt"].dt.date
    per_day = df.groupby("Gun")["Net"].sum().sort_index()

    if per_day.empty:
        return total_in, total_out, 0.0, 0.0

    last_day = per_day.index.max()
    today_net = float(per_day.loc[last_day])

    # Son günün AYI için net
    month_mask = (
        (per_day.index >= last_day.replace(day=1)) &
        (per_day.index <= last_day)
    )
    month_net = float(per_day[month_mask].sum())

    return total_in, total_out, month_net, today_net
    
def external_cashflow_table(df_trans, limit=50):
    """
    Sadece dış nakit akışı satırlarını döndürür:
    - Varlık = TL Bakiye
    - Kaynak = DIS_GIRIS / DIS_CIKIS
    - DIS_GIRIS: ALIS (+)
    - DIS_CIKIS: SATIS (-)
    """
    if df_trans is None or df_trans.empty:
        return pd.DataFrame(columns=["Tarih", "Kaynak", "İşlem", "Adet", "Net"])

    df = df_trans.dropna(subset=["Tarih"]).copy()
    if "Kaynak" not in df.columns:
        df["Kaynak"] = ""

    df["Varlık_u"] = df["Varlık"].astype(str).str.upper().str.strip()
    df["Kaynak_u"] = df["Kaynak"].astype(str).str.upper().str.strip()
    df["Islem_u"] = df["İşlem"].astype(str).str.upper().str.strip()

    df = df[df["Varlık_u"] == "TL BAKIYE"]
    df = df[df["Kaynak_u"].isin(["DIS_GIRIS", "DIS_CIKIS"])]

    if df.empty:
        return pd.DataFrame(columns=["Tarih", "Kaynak", "İşlem", "Adet", "Net"])

    # Net kolonunu hesapla
    def _net(row):
        if row["Kaynak_u"] == "DIS_GIRIS" and row["Islem_u"] == "ALIS":
            return float(row.get("Adet", 0) or 0)
        if row["Kaynak_u"] == "DIS_CIKIS" and row["Islem_u"] == "SATIS":
            return -float(row.get("Adet", 0) or 0)
        return 0.0

    df["Net"] = df.apply(_net, axis=1)

    out = df.sort_values("Tarih", ascending=False)[["Tarih", "Kaynak_u", "Islem_u", "Adet", "Net"]].copy()
    out = out.rename(columns={"Kaynak_u": "Kaynak", "Islem_u": "İşlem"})
    return out.head(limit)

def save_snapshot(df_view, tot_w, net_invested, performance):
    """
    Günün özetini Snapshot sheet'ine yazar (TL baz).
    """
    try:
        client = get_client()
        sh = client.open(SHEET_NAME)
        ws = ensure_snapshot_sheet(sh)

        # Grup yüzdeleri
        group_sum = {}
        if df_view is not None and not df_view.empty:
            group_sum = df_view.groupby("Grup")["Net Değer"].sum().to_dict()

        total = float(tot_w or 0.0)
        def pct(group_name):
            v = float(group_sum.get(group_name, 0.0) or 0.0)
            return (v / total * 100) if total > 0 else 0.0

        row = [
            datetime.now().strftime("%d.%m.%Y %H:%M"),
            float(tot_w or 0.0),
            float(net_invested or 0.0),
            float(performance or 0.0),
            pct("ALTIN"),
            pct("FON"),
            pct("NAKİT"),
            pct("HİSSE"),
            pct("DÖVİZ"),
        ]

        ws.append_row(row, value_input_option="USER_ENTERED")
        return True
    except:
        return False
        
def load_snapshots():
    try:
        client = get_client()
        sh = client.open(SHEET_NAME)
        ws = sh.worksheet(SNAPSHOT_SHEET_NAME)
        data = ws.get_all_values()
        if len(data) <= 1:
            return pd.DataFrame()
        df = pd.DataFrame(data[1:], columns=data[0])
        df["Tarih"] = pd.to_datetime(df["Tarih"], dayfirst=True, errors="coerce")
        for c in df.columns:
            if c != "Tarih":
                df[c] = df[c].apply(clean_numeric)
        df = df.dropna(subset=["Tarih"]).sort_values("Tarih")
        return df
    except:
        return pd.DataFrame()

# --- AYLIK REALIZED ÖZET ---
def realized_monthly_summary(df_trans):
    if df_trans.empty:
        return pd.DataFrame(columns=["Ay", "Realized", "Satış Adedi", "Win Rate %"])

    df = df_trans.sort_values("Tarih").copy()
    df = df.dropna(subset=["Tarih"])

    positions = {}
    rows = []

    for _, row in df.iterrows():
        v = str(row.get("Varlık", "")).strip()
        islem = _normalize_islem(row.get("İşlem", ""))
        adet = float(row.get("Adet", 0) or 0)
        fiyat = float(row.get("Fiyat", 0) or 0)
        tarih = row.get("Tarih")

        if not v:
            continue

        if v not in positions:
            positions[v] = {"adet": 0.0, "maliyet": 0.0}

        if islem == "ALIS":
            positions[v]["adet"] += adet
            positions[v]["maliyet"] += adet * fiyat

        elif islem == "SATIS":
            realized = 0.0
            if positions[v]["adet"] > 0:
                avg_cost = positions[v]["maliyet"] / positions[v]["adet"]
                realized = (fiyat - avg_cost) * adet

                positions[v]["adet"] -= adet
                positions[v]["maliyet"] -= avg_cost * adet

            # satış kaydı (TL Bakiye satışları genelde 0 realized verir; sorun değil)
            rows.append({
                "Ay": tarih.strftime("%Y-%m"),
                "Realized": realized
            })

    if not rows:
        return pd.DataFrame(columns=["Ay", "Realized", "Satış Adedi", "Win Rate %"])

    df_sales = pd.DataFrame(rows)

    # Aylık toplam realized
    g = df_sales.groupby("Ay")["Realized"].sum().reset_index()

    # Satış adedi (kaç satış işlemi var)
    cnt = df_sales.groupby("Ay")["Realized"].count().reset_index().rename(columns={"Realized": "Satış Adedi"})

    # Win rate: realized > 0 olan satışların oranı
    win = df_sales.assign(Win=df_sales["Realized"] > 0).groupby("Ay")["Win"].mean().reset_index()
    win["Win Rate %"] = win["Win"] * 100
    win = win.drop(columns=["Win"])

    out = g.merge(cnt, on="Ay", how="left").merge(win, on="Ay", how="left")
    out = out.sort_values("Ay", ascending=False)
    return out

# --- SERVET TRENDİ (DOĞRU HESAP: SADECE O TARİHE KADAR İŞLEMLER) ---
def prepare_historical_trend(df_prices, df_trans, rate=1.0):
    if df_prices.empty or df_trans.empty:
        return pd.DataFrame()

    df_prices = df_prices.sort_values("Tarih").copy()
    df_trans = df_trans.sort_values("Tarih").copy()
    df_trans = df_trans.dropna(subset=["Tarih"])

    if df_trans.empty:
        return pd.DataFrame()

    first_date = df_trans["Tarih"].min()
    running_qty = {}  # varlık -> adet
    trend_data = []
    trans_idx = 0
    trans_rows = df_trans.reset_index(drop=True)

    for _, pr in df_prices.iterrows():
        cd = pr["Tarih"]
        if pd.isna(cd) or cd < first_date:
            continue

        while trans_idx < len(trans_rows) and trans_rows.loc[trans_idx, "Tarih"] <= cd:
            tr = trans_rows.loc[trans_idx]
            v = str(tr.get("Varlık", "")).strip()
            islem = str(tr.get("İşlem", "")).upper().strip()
            ad = float(tr.get("Adet", 0.0) or 0.0)

            if v != "":
                if islem == "ALIS":
                    running_qty[v] = running_qty.get(v, 0.0) + ad
                elif islem == "SATIS":
                    running_qty[v] = running_qty.get(v, 0.0) - ad

            trans_idx += 1

        tot = 0.0
        for v, qty in running_qty.items():
            if qty <= 0:
                continue
            price = find_smart_price(pr, v)
            if price and price > 0:
                tot += qty * price

        if tot > 0:
            trend_data.append({"Tarih": cd, "Toplam Servet": tot / rate})

    return pd.DataFrame(trend_data)

# --- REBALANS ASİSTANI ---
def render_rebalance_assistant(df_view):
    if df_view is None or df_view.empty:
        st.info("Rebalans analizi için portföy verisi bulunamadı.")
        return

    st.subheader("⚖️ Portföy Rebalans Asistanı")

    # Ana gruplar
    core_groups = ["ALTIN", "FON", "HİSSE", "NAKİT"]

    # Mevcut dağılım (TL)
    grp_vals = {}
    for g in core_groups:
        grp_vals[g] = float(df_view.loc[df_view["Grup"] == g, "Net Değer"].sum() or 0.0)

    # Diğer her şey
    others_val = float(
        df_view.loc[~df_view["Grup"].isin(core_groups), "Net Değer"].sum() or 0.0
    )
    total_val = sum(grp_vals.values()) + others_val

    if total_val <= 0:
        st.info("Toplam portföy değeri 0 görünüyor, rebalans hesaplanamıyor.")
        return

    # 🎯 Default hedefler: ALTIN 30, FON 55, HİSSE 7, NAKİT 3, DİĞER otomatik
    default_targets = {
        "ALTIN": 35.0,
        "FON": 55.0,
        "HİSSE": 7.0,
        "NAKİT": 3.0,
    }

    st.markdown("🎯 Hedef Dağılım")

    c1, c2, c3, c4 = st.columns(4)
    target_ratios = {}

    with c1:
        target_ratios["ALTIN"] = st.number_input(
            "ALTIN Hedef %",
            min_value=0.0, max_value=100.0,
            value=float(default_targets["ALTIN"]),
            step=1.0,
            key="reb_target_ALTIN",
        )
    with c2:
        target_ratios["FON"] = st.number_input(
            "FON Hedef %",
            min_value=0.0, max_value=100.0,
            value=float(default_targets["FON"]),
            step=1.0,
            key="reb_target_FON",
        )
    with c3:
        target_ratios["HİSSE"] = st.number_input(
            "HİSSE Hedef %",
            min_value=0.0, max_value=100.0,
            value=float(default_targets["HİSSE"]),
            step=1.0,
            key="reb_target_HISSE",
        )
    with c4:
        target_ratios["NAKİT"] = st.number_input(
            "NAKİT Hedef %",
            min_value=0.0, max_value=100.0,
            value=float(default_targets["NAKİT"]),
            step=1.0,
            key="reb_target_NAKIT",
        )

    # DİĞER hedefi otomatik: 100 - (diğer 4 grup)
    others_target_pct = max(
        0.0,
        100.0
        - (
            target_ratios["ALTIN"]
            + target_ratios["FON"]
            + target_ratios["HİSSE"]
            + target_ratios["NAKİT"]
        ),
    )
    target_ratios["DİĞER"] = others_target_pct

    total_target = sum(target_ratios.values())
    if abs(total_target - 100.0) > 0.01:
        st.warning(f"Hedef yüzdelerin toplamı: %{total_target:.1f}")
    else:
        st.caption("Hedef yüzdelerin toplamı %100")

    # Detay analiz satırları
    analysis_rows = []

    def add_row(name, current_val, target_pct):
        current_pct = (current_val / total_val * 100.0) if total_val > 0 else 0.0
        target_val = total_val * (target_pct / 100.0)
        diff_tl = target_val - current_val
        diff_pct = target_pct - current_pct

        if diff_tl > 1000:
            action = f"✅ {format_tr_money(diff_tl)} TL AL"
        elif diff_tl < -1000:
            action = f"🚨 {format_tr_money(abs(diff_tl))} TL SAT"
        else:
            action = "🆗 Dengeli"

        analysis_rows.append(
            {
                "Grup": name,
                "Mevcut Değer (TL)": current_val,
                "Mevcut Oran %": current_pct,
                "Hedef Oran %": target_pct,
                "Hedef Değer (TL)": target_val,
                "Fark (TL)": diff_tl,
                "Fark %": diff_pct,
                "Öneri": action,
            }
        )

    # 4 ana grup
    for g in core_groups:
        add_row(g, grp_vals[g], target_ratios[g])

    # DİĞER
    if others_val != 0 or target_ratios["DİĞER"] > 0:
        add_row("DİĞER", others_val, target_ratios["DİĞER"])

    df_analysis = pd.DataFrame(analysis_rows)

    # Küçük özet metrik
    total_deviation = df_analysis["Fark (TL)"].abs().sum() / 2.0
    avg_deviation_pct = (total_deviation / total_val * 100.0) if total_val > 0 else 0.0
    st.metric("Toplam Sapma (yaklaşık)", f"%{avg_deviation_pct:.1f}")

    st.dataframe(
        df_analysis.style.format(
            {
                "Mevcut Değer (TL)": "{:,.2f}",
                "Hedef Değer (TL)": "{:,.2f}",
                "Fark (TL)": "{:,.2f}",
                "Mevcut Oran %": "{:,.1f}",
                "Hedef Oran %": "{:,.1f}",
                "Fark %": "{:,.1f}",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )


FON_GREEN_TONES = [
    "#2ca02c",  # koyu
    "#3cb44b",
    "#66c266",
    "#8fd18f",
    "#b6e3b6",
    "#1f7a1f"
]
# --- YFINANCE TABANLI PİYASA MOTORU ---

@st.cache_data(ttl=300)
def yf_download_ohlc(symbol: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    """
    yfinance'den OHLCV verisi çeker.
    - period: '1mo', '3mo', '6mo', '1y', '5y', 'max'
    - interval: '1m', '5m', '15m', '30m', '1h', '1d', '1wk', ...
    """
    try:
        data = yf.download(
            symbol,
            period=period,
            interval=interval,
            auto_adjust=False,
            progress=False
        )
        if data is None or data.empty:
            return pd.DataFrame()

        # Index'teki tarihi kolona alıp isimleri küçültüyoruz
        data = data.reset_index().rename(columns=str.lower)
        # Beklenen kolonlar: date, open, high, low, close, adj close, volume
        return data
    except Exception:
        return pd.DataFrame()


def add_basic_indicators(df: pd.DataFrame,
                         rsi_window: int = 14,
                         ma_windows=(20, 50, 200)) -> pd.DataFrame:
    """
    - Hareketli ortalamalar (MA20, MA50, MA200)
    - RSI(14)
    - 20 günlük yıllıklaştırılmış volatilite (log değil, basit getiriler)
    Hata almamak için tüm hesaplamalar pandas Series üzerinde ve min_periods ile yapılıyor.
    """
    if df is None or df.empty:
        return df

    df = df.copy()

    if "close" not in df.columns:
        return df

    # Close'u güvenli şekilde numerik yap
    close = pd.to_numeric(df["close"], errors="coerce")
    df["close"] = close

    # --- MA'ler ---
    for w in ma_windows:
        df[f"ma_{w}"] = close.rolling(window=w, min_periods=w).mean()

    # --- RSI(14) ---
    delta = close.diff()

    # kazanç / kayıp serileri
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    roll_up = gain.rolling(window=rsi_window, min_periods=rsi_window).mean()
    roll_down = loss.rolling(window=rsi_window, min_periods=rsi_window).mean()

    rs = roll_up / roll_down
    rsi = 100.0 - (100.0 / (1.0 + rs))
    df["rsi"] = rsi

    # --- Volatilite (20 günlük, yıllıklaştırılmış) ---
    df["ret"] = close.pct_change()
    df["vol_20d"] = df["ret"].rolling(window=20, min_periods=20).std() * np.sqrt(252)

    return df


def build_signal_row(symbol: str, df: pd.DataFrame):
    """
    Tek bir sembol için:
      - Son fiyat
      - Günlük %
      - RSI
      - MA20, MA50
      - 20 günlük yıllıklaştırılmış vol
      - Basit sinyal etiketi
    """
    if df is None or df.empty:
        return None

    df = df.dropna(subset=["close"]).copy()
    if df.empty:
        return None

    last = df.iloc[-1]
    price = float(last["close"])
    rsi = float(last.get("rsi", np.nan))
    ma20 = float(last.get("ma_20", np.nan))
    ma50 = float(last.get("ma_50", np.nan))
    vol20 = float(last.get("vol_20d", np.nan))

    if len(df) >= 2:
        prev = df.iloc[-2]
        prev_close = float(prev["close"])
        daily_chg = (price / prev_close - 1.0) * 100 if prev_close > 0 else np.nan
    else:
        daily_chg = np.nan

    # Basit sinyal etiketi
    tags = []

    # Trend / momentum
    if not np.isnan(ma20) and price > ma20:
        tags.append("P>MA20")
    if not np.isnan(ma50) and price > ma50:
        tags.append("P>MA50")
    if not np.isnan(ma20) and not np.isnan(ma50):
        if ma20 > ma50:
            tags.append("MA20>MA50 (Uptrend)")
        elif ma20 < ma50:
            tags.append("MA20<MA50 (Downtrend)")

    # RSI sinyalleri
    if not np.isnan(rsi):
        if rsi > 70:
            tags.append("RSI>70 (Overbought)")
        elif rsi < 30:
            tags.append("RSI<30 (Oversold)")

    signal = ", ".join(tags)

    return {
        "Sembol": symbol,
        "Fiyat": price,
        "Günlük %": daily_chg,
        "RSI": rsi,
        "MA20": ma20,
        "MA50": ma50,
        "Yıllık Vol(20d)": vol20,
        "Sinyal": signal,
    }
# --- PASTA RENKLEME (Varlık bazlı) ---
def asset_color(name: str) -> str:
    n = str(name).upper()
    # Altın ailesi
    if "ALTIN" in n or "BİLEZİK" in n or "BILEZIK" in n or "ÇEYREK" in n or "CEYREK" in n or "ATA" in n:
        return "#FFD700"   # sarı
    # Fon
    if "FON" in n:
        idx = abs(hash(n)) % len(FON_GREEN_TONES)
        return FON_GREEN_TONES[idx]
    # Nakit
    if "TL BAKIYE" in n or "NAKİT" in n or "NAKIT" in n:
        return "#1f77b4"   # mavi
    # Hisse
    if ".IS" in n or "HİSSE" in n or "HISSE" in n:
        return "#d62728"   # kırmızı
    # Diğer
    return "#9467bd"       # mor

# --- ANA PROGRAM ---
def main():
    df_prices, df_trans, watchlist = load_data()
    if df_prices.empty:
        st.stop()

    with st.sidebar:
        # Başlık
        st.markdown("<h1 class='main-title'>💎 Varlık Paneli</h1>", unsafe_allow_html=True)
    
        # Son satır (fiyat verisi)
        last = df_prices.iloc[-1]
    
        # 📊 Sheet'teki son fiyat tarihi (Tarih kolonu zaten TR saati)
        last_price_date = last.get("Tarih")
        if pd.notna(last_price_date):
            last_price_dt = pd.to_datetime(last_price_date)
            st.markdown("<div class='sidebar-label'>📊 Son Veri Tarihi</div>", unsafe_allow_html=True)
            st.markdown(
                f"<div class='sidebar-caption'>{last_price_dt.strftime('%d.%m.%Y %H:%M:%S')}</div>",
                unsafe_allow_html=True
            )
    
        # 🌐 Uygulama zamanı (sunucu UTC → TR için +3 saat)
        app_now = datetime.utcnow() + timedelta(hours=3)
        st.markdown("<div class='sidebar-label' style='margin-top:10px;'>🕒 Uygulama Zamanı (TR)</div>", unsafe_allow_html=True)
        st.markdown(
            f"<div class='sidebar-caption'>{app_now.strftime('%d.%m.%Y %H:%M:%S')}</div>",
            unsafe_allow_html=True
        )
    
        # 💵 USD / EUR kartları + günlük / haftalık değişim
        usd = float(last.get("DOLAR KURU", 0.0) or 0.0)
        eur = float(last.get("EURO KURU", 0.0) or 0.0)
    
        # Son tarih (sheet'teki son satır)
        last_dt = df_prices["Tarih"].iloc[-1]
    
        # Belirli bir tarihten ÖNCEKİ/AYNI son fiyatı bulan yardımcı fonksiyon
        def price_on_or_before(col_name, target_dt):
            sub = df_prices[df_prices["Tarih"] <= target_dt]
            if sub.empty:
                return None
            return float(sub.iloc[-1].get(col_name, 0.0) or 0.0)
    
        # 1 gün ve 7 gün önceki değerler
        usd_prev_day   = price_on_or_before("DOLAR KURU", last_dt - pd.Timedelta(days=1))
        usd_prev_week  = price_on_or_before("DOLAR KURU", last_dt - pd.Timedelta(days=7))
        eur_prev_day   = price_on_or_before("EURO KURU", last_dt - pd.Timedelta(days=1))
        eur_prev_week  = price_on_or_before("EURO KURU", last_dt - pd.Timedelta(days=7))
    
        def pct_change(curr, prev_val):
            curr = float(curr or 0.0)
            prev_val = float(prev_val or 0.0)
            if prev_val <= 0:
                return 0.0
            return (curr - prev_val) / prev_val * 100.0
    
        # 🔴🟢 Renk kararını direkt işarete göre veriyoruz
        def fmt_change(pct):
            if pct > 0:
                cls = "currency-change-pos"
                icon = "▲"
            elif pct < 0:
                cls = "currency-change-neg"
                icon = "▼"
            else:
                cls = "currency-change-flat"
                icon = "●"
            return f'<span class="{cls}">{icon} {pct:+.2f}%</span>'
    
        usd_d = pct_change(usd, usd_prev_day) if usd_prev_day is not None else 0.0
        usd_w = pct_change(usd, usd_prev_week) if usd_prev_week is not None else 0.0
        eur_d = pct_change(eur, eur_prev_day) if eur_prev_day is not None else 0.0
        eur_w = pct_change(eur, eur_prev_week) if eur_prev_week is not None else 0.0
    
        st.markdown(
            f'''
            <div class="currency-card">
                <div class="currency-title">🇺🇸 USD</div>
                <div class="currency-value">{usd:.2f} ₺</div>
                <div class="currency-change-row">
                    <span class="currency-change-label">Günlük:</span> {fmt_change(usd_d)}<br>
                    <span class="currency-change-label">Haftalık:</span> {fmt_change(usd_w)}
                </div>
            </div>
            <div class="currency-card">
                <div class="currency-title">🇪🇺 EUR</div>
                <div class="currency-value">{eur:.2f} ₺</div>
                <div class="currency-change-row">
                    <span class="currency-change-label">Günlük:</span> {fmt_change(eur_d)}<br>
                    <span class="currency-change-label">Haftalık:</span> {fmt_change(eur_w)}
                </div>
            </div>
            ''',
            unsafe_allow_html=True
        )

    
        # Menü
        page = st.radio("Menü", ["Portföyüm", "Piyasa Takip"], label_visibility="collapsed")
    
        if st.button("🔄 Verileri Yenile", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    
        # ➕ İşlem Ekle
        with st.expander("➕ İşlem Ekle"):
            with st.form("add_trans"):
                f_date = st.date_input("Tarih", datetime.now())
                f_tur = st.selectbox("Tür", ["ALTIN", "FON", "HİSSE", "NAKİT", "DÖVİZ"])
                f_varlik = st.selectbox(
                    "Varlık",
                    ["TLY FONU", "DFI FONU", "TP2 FONU", "TL Bakiye", "22 AYAR BİLEZİK (Gr)", "ATA ALTIN (Adet)"]
                    + [x + " (Hisse)" for x in watchlist]
                )
                f_islem = st.selectbox("İşlem", ["ALIS", "SATIS"])
                f_kaynak = st.selectbox("Kaynak", ["PORTFOY_ICI", "DIS_GIRIS", "DIS_CIKIS"])
                f_adet = st.number_input("Adet", 0.0, step=0.01)
    
                suggested_price = 0.0
                try:
                    suggested_price = float(find_smart_price(last, f_varlik) or 0.0)
                except:
                    suggested_price = 0.0
    
                if suggested_price > 0:
                    st.caption("Son fiyat önerisi: " + format_tr_money(suggested_price))
    
                f_fiyat = st.number_input(
                    "Fiyat",
                    0.0,
                    step=0.01,
                    value=float(suggested_price) if suggested_price > 0 else 0.0
                )
    
                if st.form_submit_button("Kaydet"):
                    try:
                        client = get_client()
                        sheet = client.open(SHEET_NAME)
                        ws = sheet.worksheet("Islemler")
                        ws.append_row(
                            [
                                f_date.strftime("%d.%m.%Y"),
                                f_tur,
                                f_varlik,
                                f_islem,
                                str(f_adet).replace(".", ","),
                                str(f_fiyat).replace(".", ","),
                                f_kaynak
                            ],
                            value_input_option='USER_ENTERED'
                        )
                        st.success("✅ Eklendi")
                        time.sleep(1)
                        st.cache_data.clear()
                        st.rerun()
                    except:
                        st.error("Hata!")
    
        # 🛠️ Takip Listesi
        with st.expander("🛠️ Takip Listesi"):
            ns = st.text_input("Hisse Sembolü (Örn: SASA.IS)")
            if st.button("Takibe Ekle", use_container_width=True):
                try:
                    client = get_client()
                    sheet = client.open(SHEET_NAME)
                    ws = sheet.worksheet(CONFIG_SHEET_NAME)
                    if ns and ns not in ws.col_values(1):
                        ws.append_row([ns])
                        st.success("Eklendi")
                        time.sleep(1)
                        st.cache_data.clear()
                        st.rerun()
                except:
                    pass
    

    if page == "Portföyüm":
        df_view, tot_w, tot_t = calculate_portfolio(df_trans, df_prices)
        total_realized, month_realized, today_realized = calculate_realized_pnl(df_trans)
        total_in, total_out, month_net_cf, today_net_cf = calculate_external_cashflows(df_trans)
        net_invested = total_in - total_out          # dışarıdan net koyduğun para (TL baz)
        performance = tot_w - net_invested           # toplam servetten net yatırımı çıkar
    
        # 🔍 DEBUG: realized detaylarını gör
        with st.expander("DEBUG - Realized Detayı", expanded=False):
            st.write("Son 10 işlem (Islemler sheet):")
            st.dataframe(df_trans.tail(10))
    
            # Fonksiyon içinden df_sales'i göremiyoruz ama hızlı bir kontrol:
            st.write("Hesaplanan değerler:")
            st.write("total_realized =", total_realized)
            st.write("month_realized =", month_realized)
            st.write("today_realized =", today_realized)
        
        b1, b2 = st.columns([1, 3])
        with b1:
            if st.button("📌 Snapshot Kaydet (TL)", use_container_width=True):
                ok = save_snapshot(df_view, tot_w, net_invested, performance)
                if ok:
                    st.success("Snapshot kaydedildi ✅")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error("Snapshot kaydedilemedi (Sheet/izin kontrol et) ❌")
        with b2:
            st.caption("")
            
        df_snap = load_snapshots()
        
        if not df_snap.empty:
            st.subheader("Snapshot Geçmişi")
            fig_s = px.line(df_snap, x="Tarih", y=["ToplamServetTL", "PerformansTL"])
            fig_s.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig_s, use_container_width=True)
        else:
            st.caption("Henüz snapshot yok. Snapshot Kaydet butonuna basınca oluşacak.")

        df_cf = external_cashflow_table(df_trans, limit=30)

        tabs = st.tabs(["🇹🇷 TL Görünüm", "🇺🇸 USD Görünüm", "🇪🇺 EUR Görünüm"])

        for i, (tab, curr, rate) in enumerate(zip(tabs, ["TL", "$", "€"], [1.0, usd if usd > 0 else 1.0, eur if eur > 0 else 1.0])):
            with tab:
                # 🧾 Portföy özeti kartı
                st.markdown("<div class='section-card'>", unsafe_allow_html=True)
                st.markdown(
                    f"<div class='section-title'><span>📌</span>{curr} Portföy Özeti</div>",
                    unsafe_allow_html=True
                )
                
                # Üst satır: toplam varlık, net kâr, kâr oranı
                c1, c2, c3 = st.columns(3)
                c1.metric(
                    "Toplam Varlık",
                    f"{format_tr_money(tot_w / rate)} {curr}",
                    f"Vergi: -{format_tr_money(tot_t / rate)}"
                )
                c2.metric(
                    "Net Kâr",
                    f"{format_tr_money(df_view['Net Kâr'].sum() / rate)} {curr}" if not df_view.empty else f"0 {curr}"
                )
                c3.metric(
                    "Kâr Oranı",
                    f"%{((df_view['Net Kâr'].sum() / df_view['Maliyet'].sum()) * 100) if (not df_view.empty and df_view['Maliyet'].sum() > 0) else 0:,.2f}"
                )
                
                st.markdown("---")
                
                # Orta satır: realized metrikleri
                c4, c5, c6 = st.columns(3)
                c4.metric("Toplam Realized", pretty_metric(total_realized / rate, curr))
                c5.metric("Bu Ay Realized", pretty_metric(month_realized / rate, curr))
                c6.metric("Bugün Realized", pretty_metric(today_realized / rate, curr))
                
                st.markdown("---")
                
                # Alt satır: portföy içi nakit
                # --- TL Bakiye (portföy içi nakit) ---
                tl_row = df_view[df_view["Varlık"].str.upper().str.contains("TL BAKIYE", na=False)]
                tl_balance = float(tl_row["Net Değer"].sum()) if not tl_row.empty else 0.0
                
                n1, n2 = st.columns(2)
                n1.metric("Portföy İçi Nakit (TL Bakiye)", pretty_metric(tl_balance / rate, curr))
                
                st.markdown("</div>", unsafe_allow_html=True)

                # 💸 Dış nakit akışı kartı
                st.markdown("<div class='section-card'>", unsafe_allow_html=True)
                st.markdown(
                    "<div class='section-title'><span></span>Dış Nakit Akışı (Cashflow)</div>",
                    unsafe_allow_html=True
                )
                
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Toplam Giriş", pretty_metric(total_in / rate, curr))
                k2.metric("Toplam Çıkış", pretty_metric(total_out / rate, curr))
                
                # net değerleri TL bazlı hesapladık; rate ile dönüştürüp gösteriyoruz
                k3.metric("Bu Ay Net", pretty_metric(month_net_cf / rate, curr))
                k4.metric("Bugün Net", pretty_metric(today_net_cf / rate, curr))
                
                # Detay tablo (aynı ekranda kanıt)
                if df_cf.empty:
                    st.caption("Henüz DIS_GIRIS / DIS_CIKIS cashflow kaydı yok.")
                else:
                    df_cf_show = df_cf.copy()
                    df_cf_show["Adet"] = df_cf_show["Adet"] / rate
                    df_cf_show["Net"] = df_cf_show["Net"] / rate
                
                    st.dataframe(
                        df_cf_show.style.format({
                            "Adet": "{:,.2f}",
                            "Net": "{:,.2f}",
                        }),
                        use_container_width=True,
                        hide_index=True
                    )
                
                st.markdown("</div>", unsafe_allow_html=True)

                if curr == "TL":
                    remain = HEDEF_SERVET_TL - tot_w
                    progress_pct = (tot_w / HEDEF_SERVET_TL) * 100 if HEDEF_SERVET_TL > 0 else 0
                    remain_pct = (remain / HEDEF_SERVET_TL) * 100 if HEDEF_SERVET_TL > 0 else 0
                    gun_kaldi = (HEDEF_TARIH - datetime.now()).days
                
                    st.markdown("<div class='section-card'>", unsafe_allow_html=True)
                    st.markdown(
                        f"<div class='section-title'><span>🎯</span>Hedef Servet: {format_tr_money(HEDEF_SERVET_TL)} TL</div>",
                        unsafe_allow_html=True
                    )
                
                    st.progress(min(tot_w / HEDEF_SERVET_TL, 1.0) if HEDEF_SERVET_TL > 0 else 0.0)
                
                    h1, h2, h3 = st.columns(3)
                    h1.metric("Tamamlanan", f"%{progress_pct:.1f}")
                    h2.metric("Kalan Tutar", f"{format_tr_money(remain)} TL")
                    h3.metric("Kalan Gün", f"{gun_kaldi} gün")
                
                    st.caption(
                        f"⏳ Bitiş tarihi: **{HEDEF_TARIH.strftime('%d.%m.%Y')}**"
                    )
                
                    st.markdown("</div>", unsafe_allow_html=True)


                st.subheader("📈 Servet Değişimi")
                df_trend = prepare_historical_trend(df_prices, df_trans, rate)
                if not df_trend.empty:
                    fig_t = px.area(df_trend, x="Tarih", y="Toplam Servet")
                    fig_t.update_layout(
                        yaxis_range=[df_trend["Toplam Servet"].min() * 0.98, df_trend["Toplam Servet"].max() * 1.02],
                        height=420,
                        margin=dict(l=10, r=10, t=10, b=10)
                    )
                    st.plotly_chart(fig_t, use_container_width=True, key=f"trend_chart_{i}")

                # 📊 Varlık dağılımı & kâr/zarar kartı
                st.markdown("<div class='section-card'>", unsafe_allow_html=True)
                st.markdown(
                    "<div class='section-title'><span>📊</span>Varlık Dağılımı & Kâr/Zarar</div>",
                    unsafe_allow_html=True
                )
                
                col1, col2 = st.columns(2)
                
                with col1:
                    st.markdown("#### 🍕 Varlık Dağılımı")
                    view_mode = st.radio(
                        "Görünüm",
                        ["Ana Gruplar", "Varlık Bazlı (Kırılımlı)"],
                        horizontal=True,
                        key=f"v_mode_{i}"
                    )
                    g_col = "Grup" if view_mode == "Ana Gruplar" else "Varlık"
                
                    if not df_view.empty:
                        df_p = df_view.groupby(g_col)["Net Değer"].sum().reset_index()
                    else:
                        df_p = pd.DataFrame(columns=[g_col, "Net Değer"])
                
                    # Ana gruplar sabit renk
                    c_map_group = {"ALTIN": "#FFD700", "FON": "#2ca02c", "NAKİT": "#1f77b4", "HİSSE": "#d62728", "DÖVİZ": "#9467bd"}
                
                    if view_mode == "Ana Gruplar":
                        color_map = c_map_group
                    else:
                        items = df_p[g_col].astype(str).tolist()
                        fund_colors = ["#26de26","#034f0d","#73d973","#8fd18f","#b6e3b6","#1f7a1f",]
                
                        color_map = {}
                        fund_i = 0
                        for a in items:
                            u = a.upper()
                            if "FON" in u:
                                color_map[a] = fund_colors[fund_i % len(fund_colors)]
                                fund_i += 1
                            else:
                                color_map[a] = asset_color(a)
                
                    fig_p = px.pie(
                        df_p,
                        values="Net Değer",
                        names=g_col,
                        hole=0.30,
                        color=g_col,
                        color_discrete_map=color_map
                    )
                
                    fig_p.update_traces(
                        textposition="inside",
                        texttemplate="<b>%{label}</b><br><b>%{percent}</b>",
                        insidetextfont=dict(size=18),
                    )
                    fig_p.update_layout(
                        legend_title_text="",
                        height=420,
                        margin=dict(l=10, r=10, t=10, b=10),
                    )
                
                    st.plotly_chart(fig_p, use_container_width=True, key=f"pie_chart_{i}")
                
                with col2:
                    st.markdown("#### 📊 Kâr/Zarar Durumu")
                    if not df_view.empty:
                        fig_b = go.Figure([
                            go.Bar(
                                name='Net Değer',
                                x=df_view['Varlık'],
                                y=df_view['Net Değer'] / rate,
                                marker_color='forestgreen'
                            )
                        ])
                        fig_b.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10))
                        st.plotly_chart(fig_b, use_container_width=True, key=f"bar_chart_{i}")
                    else:
                        st.info("Henüz görüntülenecek varlık yok.")
                
                st.markdown("</div>", unsafe_allow_html=True)


                st.markdown("<div class='section-card'>", unsafe_allow_html=True)
                st.markdown(
                    "<div class='section-title'><span>📋</span>Detaylı Varlık Listesi</div>",
                    unsafe_allow_html=True
                )
                
                if not df_view.empty:
                    df_show = df_view.copy()
                
                    # Ortalama maliyet: maliyet / adet  (adet 0 ise 0)
                    df_show["Ortalama Maliyet"] = np.where(
                        df_show["Adet"] > 0,
                        df_show["Maliyet"] / df_show["Adet"],
                        0.0
                    )
                
                    # Brüt değer: adet * fiyat (vergisiz)
                    df_show["Brüt Değer"] = df_show["Adet"] * df_show["Fiyat"]
                
                    # Kur dönüşümü (gösterim için)
                    for c in ["Fiyat", "Maliyet", "Brüt Değer", "Net Değer", "Net Kâr", "Vergi", "Ortalama Maliyet"]:
                        df_show[c] = df_show[c] / rate
                
                    # Kâr %
                    df_show["Kâr %"] = np.where(
                        df_show["Maliyet"] > 0,
                        (df_show["Net Kâr"] / df_show["Maliyet"]) * 100,
                        0.0
                    )
                
                    st.dataframe(
                        df_show.style.format({
                            "Fiyat": "{:,.2f}",
                            "Ortalama Maliyet": "{:,.2f}",
                            "Maliyet": "{:,.2f}",
                            "Brüt Değer": "{:,.2f}",
                            "Net Değer": "{:,.2f}",
                            "Net Kâr": "{:,.4f}",
                            "Vergi": "{:,.2f}",
                            "Kâr %": "%{:,.2f}",
                        }),
                        use_container_width=True,
                        hide_index=True
                    )
                else:
                    st.info("Henüz işlem/varlık yok.")
                    
                st.markdown("</div>", unsafe_allow_html=True)
                
                st.divider()
                st.subheader("🥇 Kıymetli Metal Alım-Satım Farkları")
                
                gm = st.columns(4)
                for idx, (n, k) in enumerate([
                    ("Gram", "GRAM ALTIN"),
                    ("Ata", "ATA ALTIN"),
                    ("22 Ayar", "22 AYAR ALTIN"),
                    ("Çeyrek", "ÇEYREK ALTIN"),
                ]):
                    s = float(last.get(f"{k} SATIŞ", 0) or 0)
                    a = float(last.get(f"{k} ALIŞ", 0) or 0)
                    diff = s - a
                    p_diff = (diff / s) * 100 if s > 0 else 0
                    gm[idx].metric(n, f"{s:,.2f} ₺", f"Makas: {diff:,.2f} ₺ (%{p_diff:.2f})")
                
                st.divider()
                
                if curr == "TL" and not df_view.empty:
                    render_rebalance_assistant(df_view)
                
                st.divider()
                
                st.subheader("🧾 Aylık Realized Özeti")

                
                df_month = realized_monthly_summary(df_trans)
                
                if df_month.empty:
                    st.info("Henüz satış işlemi yok.")
                else:
                    # TL sekmesinde TL, USD sekmesinde USD göstermek için rate kullan
                    df_show = df_month.copy()
                    df_show["Realized"] = df_show["Realized"] / rate
                
                    st.dataframe(
                        df_show.style.format({
                            "Realized": "{:,.2f}",
                            "Win Rate %": "%{:.1f}"
                        }),
                        use_container_width=True,
                        hide_index=True
                    )        
                    
    elif page == "Piyasa Takip":
        # küçük versiyon numarası koydum, gerçekten yeni kodun çalıştığını anlayalım
        st.markdown("## 🌍 Detaylı Piyasa Analizi v2")

        last = df_prices.iloc[-1]
        prev = df_prices.iloc[-2] if len(df_prices) >= 2 else last

        # Üst metrikler
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("USD/TRY", f"{float(last.get('DOLAR KURU', 0) or 0):.2f}")
        m2.metric("EUR/TRY", f"{float(last.get('EURO KURU', 0) or 0):.2f}")
        m3.metric("Gram Altın (Satış)", f"{float(last.get('GRAM ALTIN SATIŞ', 0) or 0):,.2f} ₺")
        m4.metric("Ons (Satış)", f"{float(last.get('ALTIN ONS SATIŞ', 0) or 0):,.2f}")

        st.divider()

        # Sheets kolonundan sembol yakalayıcı (V2 mantığı korunuyor)
        def find_price_column_for_symbol(symbol: str):
            s = str(symbol).strip()
            if f"{s} FİYAT" in df_prices.columns:
                return f"{s} FİYAT"
            if f"{s} FIYAT" in df_prices.columns:
                return f"{s} FIYAT"
            if s in df_prices.columns:  # fonlar için (TLY gibi)
                return s
            candidates = [c for c in df_prices.columns if s in str(c)]
            if candidates:
                for c in candidates:
                    if "FİYAT" in str(c).upper() or "FIYAT" in str(c).upper():
                        return c
                return candidates[0]
            return None

        # ✅ Universe: watchlist + işlemler + sheet fiyat kolonları
        def symbols_from_transactions(df_trans_local):
            syms = set()
            if df_trans_local is None or df_trans_local.empty:
                return syms
            if "Varlık" not in df_trans_local.columns:
                return syms

            for raw in df_trans_local["Varlık"].dropna().astype(str).tolist():
                v = raw.strip()

                # "XXX.IS (Hisse)"
                if "(Hisse)" in v or "(HİSSE)" in v:
                    s = v.replace("(Hisse)", "").replace("(HİSSE)", "").strip()
                    if s:
                        syms.add(s)
                    continue

                # "TLY FONU" -> "TLY"
                if "FONU" in v.upper():
                    code = v.upper().replace("FONU", "").strip()
                    if 2 <= len(code) <= 5:
                        syms.add(code)
                    continue

                syms.add(v)

            return syms

        rows = []
        for sym in sorted(list(universe)):
            col = find_price_column_for_symbol(sym)
            if not col:
                continue
            
            # .get() içine default 0 koyarak "boş" gelirse çökmesini engelle
            lp = float(last.get(col, 0) or 0)
            pp = float(prev.get(col, 0) or 0)
            
            # Payda 0 ise hata vermemesi için kontrol
            chg = ((lp - pp) / pp) * 100 if pp > 0 else 0
            rows.append({"Sembol": sym, "Fiyat": lp, "Günlük %": chg, "Kolon": col})
            
        def symbols_from_sheet_columns(df_prices_local):
            syms = set()
            if df_prices_local is None or df_prices_local.empty:
                return syms

            excluded = {
                "Tarih",
                "DOLAR KURU", "EURO KURU",
                "GRAM ALTIN ALIŞ", "GRAM ALTIN SATIŞ",
                "22 AYAR ALTIN ALIŞ", "22 AYAR ALTIN SATIŞ",
                "ATA ALTIN ALIŞ", "ATA ALTIN SATIŞ",
                "ÇEYREK ALTIN ALIŞ", "ÇEYREK ALTIN SATIŞ",
                "ALTIN ONS ALIŞ", "ALTIN ONS SATIŞ",
            }

            for c in df_prices_local.columns:
                if c in excluded:
                    continue
                cu = str(c).upper()

                if "FİYAT" in cu or "FIYAT" in cu:
                    base = str(c).replace(" FİYAT", "").replace(" FIYAT", "").strip()
                    if base:
                        syms.add(base)
                    continue

                if str(c).strip().upper() in [x.upper() for x in MY_FUNDS]:
                    syms.add(str(c).strip())
                    continue

                if ".IS" in cu:
                    syms.add(str(c).strip())
                    continue

            return syms

        universe = set()
        for w in (watchlist or []):
            if w:
                universe.add(str(w).strip())

        universe |= symbols_from_transactions(df_trans)
        universe |= symbols_from_sheet_columns(df_prices)

        rows = []
        for sym in sorted(list(universe)):
            col = find_price_column_for_symbol(sym)
            if not col:
                continue
            lp = float(last.get(col, 0) or 0)
            pp = float(prev.get(col, 0) or 0)
            chg = ((lp - pp) / pp) * 100 if pp > 0 else 0
            rows.append({"Sembol": sym, "Fiyat": lp, "Günlük %": chg, "Kolon": col})

        df_mkt = pd.DataFrame(rows).sort_values("Günlük %", ascending=False) if rows else pd.DataFrame(columns=["Sembol", "Fiyat", "Günlük %", "Kolon"])

        c1, c2 = st.columns([1, 1.2])

        with c1:
            st.subheader("📋 Piyasa Özeti")
            if df_mkt.empty:
                st.warning("Sembol bulunamadı. Sheet kolon isimleri 'SASA.IS FİYAT' veya fonlarda 'TLY' gibi olmalı.")
            else:
                st.dataframe(
                    df_mkt.drop(columns=["Kolon"]).style.format({"Fiyat": "{:,.2f}", "Günlük %": "%{:.2f}"}),
                    use_container_width=True,
                    hide_index=True
                )

        with c2:
            st.subheader("📈 Seçili Varlık Grafiği")
            if df_mkt.empty:
                st.info("Grafik için önce Sheet'te fiyat kolonu olan bir sembol olmalı.")
            else:
                selected = st.selectbox("Sembol seç", df_mkt["Sembol"].tolist())
                col = find_price_column_for_symbol(selected)

                period = st.radio("Periyot", ["30G", "90G", "180G", "Tümü"], horizontal=True)
                if period == "30G":
                    df_slice = df_prices.tail(30)
                elif period == "90G":
                    df_slice = df_prices.tail(90)
                elif period == "180G":
                    df_slice = df_prices.tail(180)
                else:
                    df_slice = df_prices

                if col and col in df_slice.columns:
                    df_plot_local = df_slice[["Tarih", col]].copy()
                    df_plot_local = df_plot_local.rename(columns={col: "Fiyat"}).dropna()
                    fig_line = px.line(df_plot_local, x="Tarih", y="Fiyat")
                    fig_line.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10))
                    st.plotly_chart(fig_line, use_container_width=True)

                    # ================================
                    # 🧭 Zamansal Değişim Tablosu
                    # ================================
                    st.divider()
                    st.subheader("🧭 Zamansal Değişim")

                    def price_on_or_before(df, price_col, target_dt):
                        sub = df[df["Tarih"] <= target_dt]
                        if sub.empty:
                            return None
                        return float(sub.iloc[-1][price_col] or 0)

                    now_dt = df_prices["Tarih"].iloc[-1]
                    p_now = float(last.get(col, 0) or 0)

                    # Fon mu?
                    fund_mode = False
                    if selected.upper() in [x.upper() for x in MY_FUNDS]:
                        fund_mode = True
                    if "." not in selected and 2 <= len(selected) <= 5:
                        fund_mode = True

                    presets_fund = [
                        ("1D",  pd.Timedelta(days=1)),
                        ("2D",  pd.Timedelta(days=2)),
                        ("3D",  pd.Timedelta(days=3)),
                        ("1W",  pd.Timedelta(days=7)),
                        ("10D", pd.Timedelta(days=10)),
                        ("15D", pd.Timedelta(days=15)),
                        ("30D", pd.Timedelta(days=30)),
                        ("3M",  pd.Timedelta(days=90)),
                        ("6M",  pd.Timedelta(days=180)),
                        ("12M", pd.Timedelta(days=365)),
                    ]

                    presets_other = [
                        ("1m",  pd.Timedelta(minutes=1)),
                        ("5m",  pd.Timedelta(minutes=5)),
                        ("10m", pd.Timedelta(minutes=10)),
                        ("30m", pd.Timedelta(minutes=30)),
                        ("1h",  pd.Timedelta(hours=1)),
                        ("3h",  pd.Timedelta(hours=3)),
                        ("4h",  pd.Timedelta(hours=4)),
                        ("6h",  pd.Timedelta(hours=6)),
                        ("1D",  pd.Timedelta(days=1)),
                        ("2D",  pd.Timedelta(days=2)),
                        ("3D",  pd.Timedelta(days=3)),
                        ("1W",  pd.Timedelta(days=7)),
                        ("10D", pd.Timedelta(days=10)),
                        ("15D", pd.Timedelta(days=15)),
                        ("30D", pd.Timedelta(days=30)),
                        ("3M",  pd.Timedelta(days=90)),
                        ("6M",  pd.Timedelta(days=180)),
                        ("12M", pd.Timedelta(days=365)),
                    ]

                    presets = presets_fund if fund_mode else presets_other

                    out = []

                    # YTD
                    start_of_year = pd.Timestamp(year=now_dt.year, month=1, day=1)
                    p_ytd = price_on_or_before(df_prices, col, start_of_year)
                    if p_ytd and p_ytd > 0:
                        out.append({
                            "Periyot": "YTD",
                            "Başlangıç": p_ytd,
                            "Şimdi": p_now,
                            "Değişim": p_now - p_ytd,
                            "Değişim %": ((p_now - p_ytd) / p_ytd) * 100
                        })

                    for label, delta in presets:
                        t0 = now_dt - delta
                        p0 = price_on_or_before(df_prices, col, t0)
                        if p0 and p0 > 0:
                            out.append({
                                "Periyot": label,
                                "Başlangıç": p0,
                                "Şimdi": p_now,
                                "Değişim": p_now - p0,
                                "Değişim %": ((p_now - p0) / p0) * 100
                            })

                    df_perf = pd.DataFrame(out)
                    st.caption(f"Mod: {'FON (1D+)' if fund_mode else 'DİĞER (1m+)'}")

                    st.dataframe(
                        df_perf.style.format({
                            "Başlangıç": "{:,.4f}",
                            "Şimdi": "{:,.4f}",
                            "Değişim": "{:,.4f}",
                            "Değişim %": "%{:.2f}"
                        }),
                        use_container_width=True,
                        hide_index=True
                    )
                else:
                    st.error("Bu sembolün fiyat kolonu bulunamadı.")

        # --- BURADAN SONRASI: QUANT PİYASA MOTORU (YFINANCE) ---

        st.divider()
        st.markdown("## 🧠 Quant Piyasa Motoru (yfinance)")

        # yfinance ile takip edilebilir olma ihtimali yüksek olan sembolleri filtreleyelim
        # (BIST hisseleri genelde 'XXXX.IS' formatında)
        yf_universe = sorted([
            s for s in universe
            if isinstance(s, str) and (".IS" in s.upper() or s.upper() in ["SPY", "QQQ", "BTC-USD", "XU100.IS"])
        ])

        st.write("DEBUG: yf_universe =", yf_universe)

        if not yf_universe:
            st.info("yfinance ile takip edilebilir sembol bulunamadı. Örn: SASA.IS, XU100.IS gibi ekleyebilirsin.")
        else:
            default_syms = yf_universe[:5]  # ilk birkaçını default seçelim
            selected_yf = st.multiselect(
                "Sinyal üretmek istediğin semboller (yfinance):",
                yf_universe,
                default=default_syms,
            )

            period_opt = st.selectbox(
                "Veri periyodu (yfinance):",
                ["3 Ay", "6 Ay", "1 Yıl"],
                index=1,
            )

            if period_opt == "3 Ay":
                yf_period = "3mo"
            elif period_opt == "6 Ay":
                yf_period = "6mo"
            else:
                yf_period = "1y"

            summary_rows = []
            data_dict = {}

            for sym in selected_yf:
                df_yf = yf_download_ohlc(sym, period=yf_period, interval="1d")

                # Bazı sembollerde veri veya indikatör hesabı patlarsa tüm sayfa göçmesin
                try:
                    df_yf = add_basic_indicators(df_yf)
                except Exception as e:
                    st.write(f"⚠️ {sym} için indikatör hesaplanamadı:", e)
                    continue

                if df_yf is None or df_yf.empty:
                    continue

                data_dict[sym] = df_yf
                row = build_signal_row(sym, df_yf)
                if row is not None:
                    summary_rows.append(row)

            if not summary_rows:
                st.warning("Seçili semboller için yfinance verisi çekilemedi.")
            else:
                df_sig = pd.DataFrame(summary_rows)

                st.subheader("📋 Sinyal Özeti (Günlük)")
                st.dataframe(
                    df_sig.style.format({
                        "Fiyat": "{:,.2f}",
                        "Günlük %": "%{:.2f}",
                        "RSI": "{:,.1f}",
                        "MA20": "{:,.2f}",
                        "MA50": "{:,.2f}",
                        "Yıllık Vol(20d)": "{:,.2f}",
                    }),
                    use_container_width=True,
                    hide_index=True,
                )

                # Detay grafiği
                st.subheader("📊 Teknik Grafik (OHLC + MA + RSI)")

                detail_sym = st.selectbox(
                    "Detay görmek istediğin sembol:",
                    [r["Sembol"] for r in summary_rows],
                )

                df_detail = data_dict.get(detail_sym, pd.DataFrame()).copy()
                if df_detail.empty:
                    st.info("Bu sembol için veri bulunamadı.")
                else:
                    # Tarih sütunu
                    if "date" in df_detail.columns:
                        x_col = "date"
                    else:
                        x_col = df_detail.columns[0]  # ilk kolon tarih ise

                    df_plot = df_detail.dropna(subset=["close"]).copy()

                    # Fiyat + MA'ler grafiği
                    fig_price = go.Figure()

                    # OHLC (Candlestick)
                    fig_price.add_trace(
                        go.Candlestick(
                            x=df_plot[x_col],
                            open=df_plot["open"],
                            high=df_plot["high"],
                            low=df_plot["low"],
                            close=df_plot["close"],
                            name="Fiyat",
                        )
                    )

                    # MA20
                    if "ma_20" in df_plot.columns:
                        fig_price.add_trace(
                            go.Scatter(
                                x=df_plot[x_col],
                                y=df_plot["ma_20"],
                                mode="lines",
                                name="MA20",
                            )
                        )

                    # MA50
                    if "ma_50" in df_plot.columns:
                        fig_price.add_trace(
                            go.Scatter(
                                x=df_plot[x_col],
                                y=df_plot["ma_50"],
                                mode="lines",
                                name="MA50",
                            )
                        )

                    fig_price.update_layout(
                        height=520,
                        margin=dict(l=10, r=10, t=10, b=10),
                        xaxis_rangeslider_visible=False,
                    )

                    st.plotly_chart(fig_price, use_container_width=True)

                    # RSI grafiği
                    if "rsi" in df_plot.columns:
                        fig_rsi = go.Figure()
                        fig_rsi.add_trace(
                            go.Scatter(
                                x=df_plot[x_col],
                                y=df_plot["rsi"],
                                mode="lines",
                                name="RSI(14)",
                            )
                        )

                        if not df_plot.empty:
                            x_min = df_plot[x_col].min()
                            x_max = df_plot[x_col].max()

                            fig_rsi.add_shape(
                                type="line",
                                x0=x_min,
                                x1=x_max,
                                y0=70,
                                y1=70,
                                line=dict(dash="dash"),
                            )
                            fig_rsi.add_shape(
                                type="line",
                                x0=x_min,
                                x1=x_max,
                                y0=30,
                                y1=30,
                                line=dict(dash="dash"),
                            )

                        fig_rsi.update_layout(
                            height=220,
                            margin=dict(l=10, r=10, t=10, b=10),
                            yaxis=dict(range=[0, 100]),
                        )

                        st.plotly_chart(fig_rsi, use_container_width=True)
                    else:
                        st.info("RSI verisi hesaplanamadı.")
                        
if __name__ == "__main__":
    main()
