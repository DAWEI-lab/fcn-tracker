import streamlit as st
import pandas as pd
import yfinance as yf
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="FCN 即時資產水位與配息監控", layout="wide")

# 每 10 分鐘自動重整網頁與股價 (10 * 60 * 1000 毫秒)
st_autorefresh(interval=10 * 60 * 1000, key="fcn_refresh")

# ================================
# 1. 讀取 Google Sheets 資料
# ================================
# 請確認此處已填入你的 Google Sheet ID
SHEET_ID = "1Vv7YVPUGCTGen4lv7VIP9bsHwQ0-MPBLT3IT5oluYeA"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv"

@st.cache_data(ttl=60)
def load_sheet_data():
    try:
        df = pd.read_csv(SHEET_URL)
        df["contract_id"] = df["contract_id"].astype(str).str.strip()
        return df
    except Exception:
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
    st.caption("現有合約列表（測試點擊）：")
    for cid in df_all["contract_id"].dropna().unique():
        st.markdown(f"- [開啟合約 {cid}](?s={cid})")
    st.stop()

contract = df_all[df_all["contract_id"] == str(contract_id).strip()]
if contract.empty:
    st.error(f"❌ 查無此合約代號：`{contract_id}`，請向您的顧問確認。")
    st.stop()

row = contract.iloc[0]

# ================================
# 3. 解析參數與幣別設定
# ================================
currency = str(row.get("currency", "USD")).strip().upper()
curr_symbol = "¥" if currency == "JPY" else "$"

tickers = [t.strip().upper() for t in str(row["tickers"]).split(",")]
strikes = [float(p.strip()) for p in str(row["strike_prices"]).split(",")]
ko_pct = float(row["ko_barrier"])
ki_pct = float(row["ki_barrier"])
coupon_rate = float(row["coupon_rate"])
notional = float(row["notional"])

# 配息欄位解析（若未填則給預設值）
coupon_freq = float(row.get("coupon_freq", 12)) if pd.notnull(row.get("coupon_freq")) else 12.0
paid_coupons = int(row.get("paid_coupons", 0)) if pd.notnull(row.get("paid_coupons")) else 0

# 計算各期與累積配息金額 (名目本金 * 年化票息 / 每年配息頻率)
single_coupon_amount = (notional * coupon_rate) / coupon_freq
accumulated_coupon_amount = single_coupon_amount * paid_coupons

# ================================
# 4. 抓取最新即時股價 (yfinance)
# ================================
status_data = []
for ticker, strike in zip(tickers, strikes):
    try:
        stock = yf.Ticker(ticker)
        last_price = stock.fast_info.get("last_price")
        if not last_price:
            hist = stock.history(period="1d")
            last_price = hist["Close"].iloc[-1] if not hist.empty else strike
    except Exception:
        last_price = strike

    perf = last_price / strike
    ko_price = strike * ko_pct
    ki_price = strike * ki_pct
    # 敲入安全距離：負值呈現（從最新市價還要再跌多少%會碰敲入）
    dist_to_ki = ((ki_price - last_price) / last_price) * 100

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
# 5. 畫面呈現
# ================================
st.title("📊 FCN 投資部位即時水位與配息監控")
st.caption(f"合約編號：{contract_id} ｜ 發行商：{row.get('issuer', 'N/A')} ｜ 計價幣別：{currency} ｜ 系統每 10 分鐘自動重整")

# 狀態 Banner
if worst_perf <= ki_pct:
    st.error(f"⚠️ **警戒：標的 {worst_row['_ticker']} 已跌破敲入障礙價（{int(ki_pct*100)}%）！**")
elif all(x["_perf"] >= ko_pct for x in status_data):
    st.success(f"🎉 **滿足條件：所有標的皆高於敲出價（{int(ko_pct*100)}%），近期觀察日可能提前敲出贖回！**")
else:
    st.info(f"🟢 **部位正常運作中**（最差標的距敲入線尚有 `{worst_row['距敲入安全距離 (%)']}` 緩衝空間）")

# 頂部基礎數據卡
col1, col2, col3, col4 = st.columns(4)
col1.metric("名目本金", f"{currency} {curr_symbol}{notional:,.0f}")
col2.metric("約定年化票息", f"{coupon_rate * 100:.2f}%")
col3.metric("最差標的 (Worst-of)", f"{worst_row['_ticker']} ({((worst_perf-1)*100):+.2f}%)")
col4.metric("合約到期日", str(row.get("maturity_date", "N/A")))

# 配息追蹤區塊
st.write("")
st.subheader("💰 配息收益狀況")
p_col1, p_col2, p_col3 = st.columns(3)
p_col1.metric("每期預計配息金額", f"{curr_symbol}{single_coupon_amount:,.2f} {currency}")
p_col2.metric("已領取累積票息", f"{curr_symbol}{accumulated_coupon_amount:,.2f} {currency}", delta=f"已配發 {paid_coupons} 期")
p_col3.metric("配息頻率", f"每年 {int(coupon_freq)} 期 (月配)" if coupon_freq == 12 else f"每年 {int(coupon_freq)} 期")

# 各標的比較明細表格
st.write("")
st.subheader("📌 各標的詳細水位比較")
display_df = status_df.drop(columns=["_perf", "_ticker"])
st.dataframe(display_df, use_container_width=True)

# 水位進度條
st.write("")
st.subheader("🛡️ 最差標的保護水位儀表")
bar_val = max(0.0, min(1.0, worst_perf))
st.progress(bar_val, text=f"{worst_row['_ticker']} 目前水位：{worst_perf * 100:.1f}% （敲入保護線：{int(ki_pct*100)}%）")