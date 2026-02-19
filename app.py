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
    [data-testid="stMetricValue"] { font-size: 26px; font-weight: bold; }
    .currency-card {
        background-color: #262730; padding: 10px; border-radius: 10px;
        border: 1px solid #41444b; margin-bottom: 10px; text-align: center;
    }
    .currency-value { font-size: 22px; font-weight: bold; color: #ffffff; }
    .rebalance-buy { color: #00FF00; font-weight: bold; }
    .rebalance-sell { color: #FF4B4B; font-weight: bold; }
</style>
""", unsafe_allow_html=True)

# --- AYARLAR ---
SHEET_NAME = "PortfoyVerileri"
CONFIG_SHEET_NAME = "Ayarlar"
HEDEF_SERVET_TL = 2000000
HEDEF_TARIH = datetime(2026, 2, 28)
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
        df_trans = pd.DataFrame(data_trans[1:], columns=data_trans[0]) if len(data_trans) > 1 else pd.DataFrame(columns=["Tarih","Tür","Varlık","İşlem","Adet","Fiyat"])
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
    if "TL Bakiye" in asset_name:
        return 1.0

    sterm = asset_name.replace(" (Adet)", "").replace(" (Gr)", "").replace(" (Hisse)", "").replace(" FONU", "").strip()

    gmap = {
        "22 AYAR BİLEZİK": "22 AYAR ALTIN ALIŞ",
        "ATA ALTIN": "ATA ALTIN ALIŞ",
        "ÇEYREK ALTIN": "ÇEYREK ALTIN ALIŞ"
    }
    if sterm in gmap:
        return row.get(gmap[sterm], 0)

    for col in row.index:
        if sterm in col:
            return row[col]
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

# --- REALIZED P&L HESAPLAMA ---
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

    s = s.replace("(HİSSE)", "").replace("(HISSE)", "").replace("(HISSE)", "")
    s = s.replace(" HİSSE", "").replace(" HISSE", "")
    s = s.replace(" FONU", "").replace(" FON", "")
    s = s.strip()

    # TL bakiye normalize
    if "TL" in s and "BAKIYE" in s:
        return "TL BAKIYE"

    return s


def calculate_realized_pnl(df_trans):
    if df_trans is None or df_trans.empty:
        return 0.0, 0.0, 0.0

    df = df_trans.dropna(subset=["Tarih"]).sort_values("Tarih").copy()

    positions = {}  # varlık -> {"adet":..., "maliyet":...}
    total_realized = 0.0
    today_realized = 0.0
    month_realized = 0.0

    today = datetime.now().date()
    this_month = datetime.now().month
    this_year = datetime.now().year

    for _, row in df.iterrows():
        raw_v = str(row.get("Varlık", "")).strip()
        v = _canon_asset_name(raw_v)

        tur = str(row.get("Tür", "")).upper().strip()
        islem = str(row.get("İşlem", "")).upper().strip()
        adet = float(row.get("Adet", 0) or 0)
        fiyat = float(row.get("Fiyat", 0) or 0)
        tarih = row.get("Tarih")

        if not v or adet <= 0:
            continue

        # ✅ Nakit bacaklarını realized hesabından çıkar
        if v == "TL BAKIYE" or tur in ["NAKİT", "NAKIT"]:
            continue

        if v not in positions:
            positions[v] = {"adet": 0.0, "maliyet": 0.0}

        if islem == "ALIS":
            positions[v]["adet"] += adet
            positions[v]["maliyet"] += adet * fiyat

        elif islem == "SATIS":
            held = positions[v]["adet"]
            if held <= 0:
                # elde yoksa realized yazma (isim uyuşmazlığı / eksik kayıt olabilir)
                continue

            qty = min(adet, held)  # ✅ sadece eldeki kadarını kapat
            avg_cost = positions[v]["maliyet"] / held if held > 0 else 0.0
            realized = (fiyat - avg_cost) * qty

            total_realized += realized

            if pd.notna(tarih):
                if tarih.date() == today:
                    today_realized += realized
                if tarih.month == this_month and tarih.year == this_year:
                    month_realized += realized

            # pozisyonu düş
            positions[v]["adet"] -= qty
            positions[v]["maliyet"] -= avg_cost * qty

            # adet > held ise (fazla satış) burada bilerek yok sayıyoruz.

    return total_realized, month_realized, today_realized


    for _, r in df.iterrows():
        tur = str(r.get("Tür", "")).upper().strip()
        varlik = str(r.get("Varlık", "")).upper().strip()
        islem = str(r.get("İşlem", "")).upper().strip()
        adet = float(r.get("Adet", 0) or 0)
        tarih = r.get("Tarih")

        # sadece TL Bakiye
        if varlik != "TL BAKIYE":
            continue

        net = 0.0

        # dış giriş
        if tur in ["NAKİT_GİRİŞ", "NAKIT_GIRIS"]:
            if islem == "ALIS":
                total_in += adet
                net = adet

        # dış çıkış
        elif tur in ["NAKİT_ÇIKIŞ", "NAKIT_CIKIS", "NAKİT_CIKIŞ"]:
            if islem == "SATIS":
                total_out += adet
                net = -adet

        else:
            continue  # normal NAKİT trade hareketlerini saymıyoruz

        if pd.notna(tarih):
            if tarih.date() == today:
                today_net += net
            if tarih.month == this_month and tarih.year == this_year:
                month_net += net

    return total_in, total_out, month_net, today_net

def calculate_external_cashflows(df_trans):
    """
    Dış nakit akışı sadece TL Bakiye satırlarında ve Kaynak alanına göre sayılır:
      - DIS_GIRIS  + ALIS  => dışarıdan para girişi
      - DIS_CIKIS  + SATIS => dışarıya para çıkışı
    PORTFOY_ICI hareketler (altın satıp TL’ye geçmek gibi) cashflow sayılmaz.
    """
    if df_trans.empty:
        return 0.0, 0.0, 0.0, 0.0  # total_in, total_out, month_net, today_net

    df = df_trans.dropna(subset=["Tarih"]).sort_values("Tarih").copy()

    # Kaynak kolonu yoksa güvenli şekilde ekle
    if "Kaynak" not in df.columns:
        df["Kaynak"] = ""

    today = datetime.now().date()
    this_month = datetime.now().month
    this_year = datetime.now().year

    total_in = 0.0
    total_out = 0.0
    month_net = 0.0
    today_net = 0.0

    for _, r in df.iterrows():
        varlik = str(r.get("Varlık", "")).upper().strip()
        islem = str(r.get("İşlem", "")).upper().strip()
        kaynak = str(r.get("Kaynak", "")).upper().strip()
        adet = float(r.get("Adet", 0) or 0)
        tarih = r.get("Tarih")

        # sadece TL Bakiye satırları
        if varlik != "TL BAKIYE":
            continue

        net = 0.0

        if kaynak == "DIS_GIRIS" and islem == "ALIS":
            total_in += adet
            net = adet

        elif kaynak == "DIS_CIKIS" and islem == "SATIS":
            total_out += adet
            net = -adet

        else:
            continue  # PORTFOY_ICI veya boş ise cashflow sayma

        if pd.notna(tarih):
            if tarih.date() == today:
                today_net += net
            if tarih.month == this_month and tarih.year == this_year:
                month_net += net

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
        islem = str(row.get("İşlem", "")).upper().strip()
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
    st.subheader("⚖️ Portföy Rebalans Asistanı")
    df_grp = df_view.groupby("Grup")["Net Değer"].sum().reset_index()
    total_val = df_grp["Net Değer"].sum()
    cols = st.columns(len(df_grp)) if len(df_grp) > 0 else []

    target_ratios = {}
    for i, row in df_grp.iterrows():
        target_ratios[row["Grup"]] = cols[i].number_input(
            f"Hedef % ({row['Grup']})",
            0, 100, int(100 / len(df_grp)) if len(df_grp) > 0 else 0,
            key=f"reb_val_{i}"
        )

    analysis = []
    for _, row in df_grp.iterrows():
        fark = ((total_val * target_ratios[row["Grup"]]) / 100) - row["Net Değer"]
        aks = (
            f"✅ {format_tr_money(fark)} TL AL" if fark > 1000
            else f"🚨 {format_tr_money(abs(fark))} TL SAT" if fark < -1000
            else "🆗 Dengeli"
        )
        analysis.append({
            "Grup": row["Grup"],
            "Mevcut Değer": row["Net Değer"],
            "Mevcut Oran": f"%{(row['Net Değer'] / total_val * 100):.1f}" if total_val > 0 else "%0.0",
            "Hedef Oran": f"%{target_ratios[row['Grup']]:.1f}",
            "Aksiyon": aks
        })

    st.dataframe(pd.DataFrame(analysis), use_container_width=True, hide_index=True)

# --- PASTA RENKLEME (Varlık bazlı) ---
def asset_color(name: str) -> str:
    n = str(name).upper()
    # Altın ailesi
    if "ALTIN" in n or "BİLEZİK" in n or "BILEZIK" in n or "ÇEYREK" in n or "CEYREK" in n or "ATA" in n:
        return "#FFD700"   # sarı
    # Fon
    if "FON" in n:
        return "#2ca02c"   # yeşil
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
        st.markdown("<h1 style='text-align: center; color: #4e8cff;'>💎 Varlık Paneli</h1>", unsafe_allow_html=True)
        last = df_prices.iloc[-1]

        usd = float(last.get("DOLAR KURU", 0.0) or 0.0)
        eur = float(last.get("EURO KURU", 0.0) or 0.0)

        st.markdown(
            f'<div class="currency-card"><div class="currency-title">🇺🇸 USD</div><div class="currency-value">{usd:.2f} ₺</div></div>'
            f'<div class="currency-card"><div class="currency-title">🇪🇺 EUR</div><div class="currency-value">{eur:.2f} ₺</div></div>',
            unsafe_allow_html=True
        )

        page = st.radio("Menü", ["Portföyüm", "Piyasa Takip"], label_visibility="collapsed")
        if st.button("🔄 Verileri Yenile", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

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

                # Son fiyat önerisi + default fiyat
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

        st.divider()
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
            st.subheader("🗂️ Snapshot Geçmişi")
            fig_s = px.line(df_snap, x="Tarih", y=["ToplamServetTL", "PerformansTL"])
            fig_s.update_layout(height=320, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig_s, use_container_width=True)
        else:
            st.caption("Henüz snapshot yok. Snapshot Kaydet butonuna basınca oluşacak.")

        df_cf = external_cashflow_table(df_trans, limit=30)

        tabs = st.tabs(["🇹🇷 TL Görünüm", "🇺🇸 USD Görünüm", "🇪🇺 EUR Görünüm"])

        for i, (tab, curr, rate) in enumerate(zip(tabs, ["TL", "$", "€"], [1.0, usd if usd > 0 else 1.0, eur if eur > 0 else 1.0])):
            with tab:
                c1, c2, c3 = st.columns(3)
                c4, c5, c6 = st.columns(3)
                
                c4.metric("Toplam Realized", pretty_metric(total_realized / rate, curr))
                c5.metric("Bu Ay Realized", pretty_metric(month_realized / rate, curr))
                c6.metric("Bugün Realized", pretty_metric(today_realized / rate, curr))
                p1, p2 = st.columns(2)

                # --- TL Bakiye (portföy içi nakit) ---
                tl_row = df_view[df_view["Varlık"].str.upper().str.contains("TL BAKIYE", na=False)]
                tl_balance = float(tl_row["Net Değer"].sum()) if not tl_row.empty else 0.0
                
                n1, n2 = st.columns(2)
                n1.metric("Portföy İçi Nakit (TL Bakiye)", pretty_metric(tl_balance / rate, curr))

                st.subheader("💸 Dış Nakit Akışı (Cashflow)")
                
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
                
                c1.metric("Toplam Varlık", f"{format_tr_money(tot_w / rate)} {curr}", f"Vergi: -{format_tr_money(tot_t / rate)}")
                c2.metric("Net Kâr", f"{format_tr_money(df_view['Net Kâr'].sum() / rate)} {curr}" if not df_view.empty else f"0 {curr}")
                c3.metric("Kâr Oranı", f"%{((df_view['Net Kâr'].sum() / df_view['Maliyet'].sum()) * 100) if (not df_view.empty and df_view['Maliyet'].sum() > 0) else 0:,.2f}")

                if curr == "TL":
                    st.divider()
                    st.subheader(f"🎯 Hedef: {format_tr_money(HEDEF_SERVET_TL)} TL")
                    st.progress(min(tot_w / HEDEF_SERVET_TL, 1.0) if HEDEF_SERVET_TL > 0 else 0.0)

                    # ✅ DÜZELTME: kalan yüzdesi doğru
                    remain = HEDEF_SERVET_TL - tot_w
                    progress_pct = (tot_w / HEDEF_SERVET_TL) * 100 if HEDEF_SERVET_TL > 0 else 0
                    remain_pct = (remain / HEDEF_SERVET_TL) * 100 if HEDEF_SERVET_TL > 0 else 0

                    h1, h2 = st.columns(2)
                    h1.write(f"🏁 Kalan: **{format_tr_money(remain)} TL** (%{remain_pct:.1f})")
                    h2.write(f"⏳ Bitiş: **{HEDEF_TARIH.strftime('%d.%m.%Y')}** ({(HEDEF_TARIH - datetime.now()).days} Gün)")
                    st.caption(f"✅ Tamamlanan: %{progress_pct:.1f}")

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

                col1, col2 = st.columns(2)
                with col1:
                    st.subheader("🍕 Varlık Dağılımı")
                    view_mode = st.radio("Görünüm", ["Ana Gruplar", "Varlık Bazlı (Kırılımlı)"], horizontal=True, key=f"v_mode_{i}")
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
                        color_map = {a: asset_color(a) for a in df_p[g_col].astype(str).unique()}

                    fig_p = px.pie(
                        df_p,
                        values="Net Değer",
                        names=g_col,
                        hole=0.45,
                        color=g_col,
                        color_discrete_map=color_map
                    )

                    # ✅ EKLEME: Eski versiyon gibi bold label + yüzde içeride
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
                    st.subheader("📊 Kâr/Zarar Durumu")
                    if not df_view.empty:
                        fig_b = go.Figure([go.Bar(
                            name='Net Değer',
                            x=df_view['Varlık'],
                            y=df_view['Net Değer'] / rate,
                            marker_color='forestgreen'
                        )])
                        fig_b.update_layout(height=420, margin=dict(l=10, r=10, t=10, b=10))
                        st.plotly_chart(fig_b, use_container_width=True, key=f"bar_chart_{i}")
                    else:
                        st.info("Henüz görüntülenecek varlık yok.")

                st.subheader("📋 Detaylı Varlık Listesi")
                
                if not df_view.empty:
                    df_show = df_view.copy()
                
                    # ✅ Yeni kolonlar (TL bazında hesapla)
                    # Ortalama maliyet: maliyet / adet  (adet 0 ise 0)
                    df_show["Ortalama Maliyet"] = np.where(
                        df_show["Adet"] > 0,
                        df_show["Maliyet"] / df_show["Adet"],
                        0.0
                    )
                
                    # Brüt değer: adet * fiyat (vergisiz)
                    df_show["Brüt Değer"] = df_show["Adet"] * df_show["Fiyat"]
                
                    # ✅ Kur dönüşümü (gösterim için)
                    for c in ["Fiyat", "Maliyet", "Brüt Değer", "Net Değer", "Net Kâr", "Vergi", "Ortalama Maliyet"]:
                        df_show[c] = df_show[c] / rate
                
                    # ✅ Kâr % (maliyet 0 ise 0 yaz)
                    df_show["Kâr %"] = np.where(
                        df_show["Maliyet"] > 0,
                        (df_show["Net Kâr"] / df_show["Maliyet"]) * 100,
                        0.0
                    )
                
                    st.dataframe(
                        df_show.style.format({
                            "Fiyat": "{:,.2f}",
                            "Ortalama Maliyet": "{:,.4f}",
                            "Maliyet": "{:,.4f}",
                            "Brüt Değer": "{:,.4f}",
                            "Net Değer": "{:,.4f}",
                            "Net Kâr": "{:,.4f}",
                            "Vergi": "{:,.4f}",
                            "Kâr %": "%{:,.2f}",
                        }),
                        use_container_width=True,
                        hide_index=True
                    )
                else:
                    st.info("Henüz işlem/varlık yok.")
                
                    st.divider()
                    st.subheader("🥇 Kıymetli Metal Alım-Satım Farkları")
                    gm = st.columns(4)
                    for idx, (n, k) in enumerate([("Gram", "GRAM ALTIN"), ("Ata", "ATA ALTIN"), ("22 Ayar", "22 AYAR ALTIN"), ("Çeyrek", "ÇEYREK ALTIN")]):
                        s = float(last.get(f"{k} SATIŞ", 0) or 0)
                        a = float(last.get(f"{k} ALIŞ", 0) or 0)
                        diff = s - a
                        p_diff = (diff / s) * 100 if s > 0 else 0
                        gm[idx].metric(n, f"{s:,.2f} ₺", f"Makas: {diff:,.2f} ₺ (%{p_diff:.2f})")
                    st.divider()
                    if not df_view.empty:
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
        st.markdown("## 🌍 Detaylı Piyasa Analizi")

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

if __name__ == "__main__":
    main()
