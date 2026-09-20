import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="FCN 即時資產水位監控", layout="wide")

# 每 10 分鐘自動重整網頁與股價 (10 * 60 * 1000 毫秒)
st_autorefresh(interval=10 * 60 * 1000, key="fcn_refresh")

# ================================
# 1. 讀取 Google Sheets 資料
# ================================
# 請將下方的 ID 替換為你的 Google Sheet ID
SHEET_ID = "1Vv7YVPUGCTGen4lv7VIP9bsHwQ0-MPBLT3IT5oluYeA"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"

@st.cache_data(ttl=60)  # 快取 60 秒，避免頻繁抓取試算表
def load_sheet_data():
    try:
        df = pd.read_csv(SHEET_URL)
        df["contract_id"] = df["contract_id"].astype(str)
        return df
    except Exception as e:
        return None

df_all = load_sheet_data()

if df_all is None or df_all.empty:
    st.error("⚠️ 無法連線至 Google 試算表，請確認試算表共用權限是否已設為「知道連結的任何人皆可查看」。")
    st.stop()

# ================================
# 2. 判斷合約代號 (?s=...)
# ================================
query_params = st.query_params
contract_id = query_params.get("s", None)

if not contract_id:
    st.info("💡 歡迎使用 FCN 監控系統。請在網址後方帶入您的合約識別碼，例如：`?s=A001`")
    st.caption("展示用合約列表（測試點擊）：")
    for cid in df_all["contract_id"].unique():
        st.markdown(f"- [開啟合約 {cid}](?s={cid})")
    st.stop()

# 撈取該合約資料
contract = df_all[df_all["contract_id"] == str(contract_id)]
if contract.empty:
    st.error(f"❌ 查無此合約代號：`{contract_id}`，請向您的顧問確認。")
    st.stop()

row = contract.iloc[0]

# ================================
# 3. 解析標的與期初價
# ================================
tickers = [t.strip().upper() for t in str(row["tickers"]).split(",")]
strikes = [float(p.strip()) for p in str(row["strike_prices"]).split(",")]
ko_pct = float(row["ko_barrier"])
ki_pct = float(row["ki_barrier"])
coupon_rate = float(row["coupon_rate"])
notional = float(row["notional"])

# ================================
# 4. 抓取最新即時股價 (yfinance)
# ================================
status_data = []
for ticker, strike in zip(tickers, strikes):
    try:
        stock = yf.Ticker(ticker)
        # 優先抓 fast_info 即時價，若盤前/盤後無成交則回退到收盤價
        last_price = stock.fast_info.get("last_price")
        if not last_price:
            hist = stock.history(period="1d")
            last_price = hist["Close"].iloc[-1] if not hist.empty else strike
    except Exception:
        last_price = strike

    perf = last_price / strike
    ko_price = strike * ko_pct
    ki_price = strike * ki_pct
    dist_to_ki = ((last_price - ki_price) / strike) * 100
    dist_to_ko = ((ko_price - last_price) / strike) * 100

    status_data.append({
        "標的代號": ticker,
        "期初價 (Strike)": f"${strike:,.2f}",
        "最新市價": f"${last_price:,.2f}",
        "表現幅度 (%)": f"{((perf - 1) * 100):+.2f}%",
        f"敲出價 ({int(ko_pct*100)}%)": f"${ko_price:,.2f}",
        f"敲入價 ({int(ki_pct*100)}%)": f"${ki_price:,.2f}",
        "距敲入安全距離 (%)": f"{dist_to_ki:+.2f}%",
        "_perf": perf,
        "_ticker": ticker
    })

status_df = pd.DataFrame(status_data)
worst_row = min(status_data, key=lambda x: x["_perf"])
worst_perf = worst_row["_perf"]

# ================================
# 5. 儀表板畫面呈現
# ================================
st.title("📊 FCN 投資部位即時水位監控")
st.caption(f"合約編號：{contract_id} ｜ 發行商：{row.get('issuer', 'N/A')} ｜ 系統每 10 分鐘自動重整")

# 狀態 Banner
if worst_perf <= ki_pct:
    st.error(f"⚠️ **警戒：標的 {worst_row['_ticker']} 已跌破敲入障礙價（{int(ki_pct*100)}%）！**")
elif all(x["_perf"] >= ko_pct for x in status_data):
    st.success(f"🎉 **滿足條件：所有標的皆高於敲出價（{int(ko_pct*100)}%），近期觀察日可能提前敲出贖回！**")
else:
    st.info(f"🟢 **部位正常運作中**（最差標的距離敲入保護仍有 `{worst_row['距敲入安全距離 (%)']}` 安全邊際）")

# 頂部四格指標卡
col1, col2, col3, col4 = st.columns(4)
col1.metric("名目本金 (USD)", f"${notional:,.0f}")
col2.metric("約定年化票息", f"{coupon_rate * 100:.2f}%")
col3.metric("最差標的 (Worst-of)", f"{worst_row['_ticker']} ({((worst_perf-1)*100):+.2f}%)")
col4.metric("到期日", str(row.get("maturity_date", "N/A")))

st.write("")
st.subheader("📌 各標的詳細水位比較")
display_df = status_df.drop(columns=["_perf", "_ticker"])
st.dataframe(display_df, use_container_width=True)

# 視覺化進度水位
st.write("")
st.subheader("🛡️ 最差標的保護水位儀表")
bar_val = max(0.0, min(1.0, worst_perf))
st.progress(bar_val, text=f"{worst_row['_ticker']} 目前水位：{worst_perf * 100:.1f}% （敲入保護線：{int(ki_pct*100)}%）")