import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
from datetime import datetime, timedelta

# ==========================================
# 頁面設定
# ==========================================
st.set_page_config(page_title="台股潛力股掃描", layout="wide")

st.markdown("""
    <style>
    .main, .stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] {
        background-color: #ffffff !important;
    }
    .stApp * { color: #000000 !important; font-family: "Arial", sans-serif !important; }
    [data-testid="stSidebar"] { display: none; }
    .block-container { padding-top: 1.5rem; padding-bottom: 0rem; }
    </style>
""", unsafe_allow_html=True)


# ==========================================
# 載入資料
# ==========================================
@st.cache_data(ttl=3600)
def load_results():
    try:
        with open('screen_results.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return None

data_store = load_results()

if not data_store:
    st.error("找不到篩選結果，請先執行 update_data.py")
    st.stop()

last_updated = data_store.get('last_updated', '未知')
results = data_store.get('results', [])
symbol_list = [r['symbol'] for r in results]

# ==========================================
# 標題列
# ==========================================
st.markdown(f"""
    <div style='display:flex; justify-content:space-between; align-items:baseline;
                border-bottom:2px solid #000; padding-top:20px; padding-bottom:5px; margin-bottom:12px;'>
        <div style='font-size:2rem; font-weight:900;'>台股潛力股掃描</div>
        <div style='font-size:0.85rem; font-weight:700;'>更新：{last_updated}｜共 {len(symbol_list)} 檔</div>
    </div>
""", unsafe_allow_html=True)

if not symbol_list:
    st.info("目前沒有符合條件的標的。")
    st.stop()

# ==========================================
# 批次下載 K 線
# ==========================================
@st.cache_data(ttl=3600)
def fetch_charts(symbols):
    end   = datetime.now()
    start = end - timedelta(days=270)
    data  = yf.download(
        symbols,
        start=start.strftime('%Y-%m-%d'),
        end=end.strftime('%Y-%m-%d'),
        group_by='ticker',
        progress=False,
        auto_adjust=True,
    )
    return data

with st.spinner(f"載入 {len(symbol_list)} 檔 K 線資料..."):
    stock_data = fetch_charts(tuple(symbol_list))

# ==========================================
# 繪圖（雙欄）
# ==========================================
cols = st.columns(2)

for i, sym in enumerate(symbol_list):
    try:
        df = stock_data[sym].copy() if len(symbol_list) > 1 else stock_data.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna(subset=['Close'])
        if df.empty:
            continue

        df['MA5']  = df['Close'].rolling(5).mean()
        df['MA20'] = df['Close'].rolling(20).mean()
        df['MA60'] = df['Close'].rolling(60).mean()

        plot_df = df.tail(120).copy()
        plot_df['DateStr'] = plot_df.index.strftime('%m-%d')

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            row_heights=[0.8, 0.2], vertical_spacing=0.03)

        # K 線
        fig.add_trace(go.Candlestick(
            x=plot_df['DateStr'],
            open=plot_df['Open'], high=plot_df['High'],
            low=plot_df['Low'],   close=plot_df['Close'],
            increasing_line_color='#E32636', decreasing_line_color='#008F39',
            increasing_fillcolor='#E32636',  decreasing_fillcolor='#008F39',
            increasing_line_width=0.7, decreasing_line_width=0.7,
            name='K線'
        ), row=1, col=1)

        # 均線
        fig.add_trace(go.Scatter(x=plot_df['DateStr'], y=plot_df['MA5'],
                                 line=dict(color='#e67e22', width=1), name='5MA'), row=1, col=1)
        fig.add_trace(go.Scatter(x=plot_df['DateStr'], y=plot_df['MA20'],
                                 line=dict(color='#8e44ad', width=1), name='20MA'), row=1, col=1)
        fig.add_trace(go.Scatter(x=plot_df['DateStr'], y=plot_df['MA60'],
                                 line=dict(color='#36b9cc', width=1.2), name='60MA'), row=1, col=1)

        # 成交量
        v_colors = ['#ef5350' if c >= o else '#26a69a'
                    for c, o in zip(plot_df['Close'], plot_df['Open'])]
        fig.add_trace(go.Bar(x=plot_df['DateStr'], y=plot_df['Volume'],
                             marker_color=v_colors, name='量'), row=2, col=1)

        fig.update_layout(
            height=350,
            margin=dict(l=5, r=40, t=50, b=20),
            xaxis_rangeslider_visible=False,
            template='plotly_white',
            paper_bgcolor='white',
            plot_bgcolor='white',
            title=dict(text=f"<b>{sym}</b>", font=dict(color='black', size=20)),
            font=dict(color='black'),
            showlegend=False,
            dragmode=False,
            hovermode=False,
        )
        fig.update_xaxes(type='category', nticks=10, showgrid=False,
                         zeroline=False, fixedrange=True,
                         tickfont=dict(color='black', size=11))
        fig.update_yaxes(showgrid=False, zeroline=False, fixedrange=True,
                         tickfont=dict(color='black', size=11), side='right', row=1, col=1)
        fig.update_yaxes(showgrid=False, zeroline=False, fixedrange=True,
                         showticklabels=False, row=2, col=1)

        if i % 2 == 0:
            cols = st.columns(2)

        with cols[i % 2]:
            st.plotly_chart(fig, use_container_width=True, key=f"fig_{sym}",
                            theme=None, config={'staticPlot': True, 'displayModeBar': False})
            st.markdown("<br>", unsafe_allow_html=True)

    except Exception:
        continue

st.write("---")
st.write("已經到底囉！")
