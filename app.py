import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import base64
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
@st.cache_data(ttl=300)
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

last_updated       = data_store.get('last_updated', '未知')
results_all        = data_store.get('results', [])
candidates_all     = data_store.get('candidates', [])

PATTERN_LABEL = {'A': '型態A（漲後整理）', 'B': '型態B（多頭排列）'}


# ==========================================
# 標題列
# ==========================================
st.markdown(f"""
    <div style='display:flex; justify-content:space-between; align-items:baseline;
                border-bottom:2px solid #000; padding-top:4px; padding-bottom:5px; margin-bottom:12px;'>
        <div style='font-size:2rem; font-weight:900;'>台股潛力股掃描</div>
        <div style='font-size:0.85rem; font-weight:700;'>更新：{last_updated}｜主清單 {len(results_all)} 檔｜候補 {len(candidates_all)} 檔</div>
    </div>
""", unsafe_allow_html=True)

tab_main, tab_cand = st.tabs([f'主清單（{len(results_all)}）', f'候補清單（{len(candidates_all)}）'])


# ==========================================
# 批次下載 K 線
# ==========================================
@st.cache_data(ttl=300)
def fetch_charts(symbols):
    end   = datetime.now() + timedelta(days=1)
    start = end - timedelta(days=271)
    data  = yf.download(
        symbols,
        start=start.strftime('%Y-%m-%d'),
        end=end.strftime('%Y-%m-%d'),
        group_by='ticker',
        progress=False,
        auto_adjust=True,
    )
    return data


# ==========================================
# 共用繪圖函式
# ==========================================
def render_chart_grid(stock_list, stock_data, tab_prefix='main'):
    """stock_list: list of result dicts"""
    if not stock_list:
        st.info("目前沒有符合條件的標的。")
        return

    symbol_list = [r['symbol'] for r in stock_list]
    info_map    = {r['symbol']: r for r in stock_list}

    # 下載 TradingView 名單按鈕
    selected_syms = [sym for sym in symbol_list
                     if st.session_state.get(f'cb_{tab_prefix}_{sym}', False)]
    if selected_syms:
        tv_content = '\n'.join(
            f"{'TWSE' if s.endswith('.TW') else 'TPEX'}:{s.split('.')[0]}"
            for s in selected_syms
        )
        filename = f"{datetime.now().strftime('%Y%m%d')}.txt"
        st.download_button(
            label=f"⬇ 下載 {filename}（已選 {len(selected_syms)} 檔）",
            data=tv_content,
            file_name=filename,
            mime="text/plain",
            key=f'dl_{tab_prefix}',
        )
        st.code(tv_content, language=None)

    cols = st.columns(2)
    for i, sym in enumerate(symbol_list):
        try:
            df = stock_data[sym].copy() if len(symbol_list) > 1 else stock_data.copy()
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.dropna(subset=['Close'])
            if df.empty:
                continue

            info        = info_map.get(sym, {})
            pattern_str = PATTERN_LABEL.get(info.get('pattern', ''), '')
            inst_tags   = ''
            if info.get('foreign_buy'):
                inst_tags += ' 🔵外資'
            if info.get('trust_buy'):
                inst_tags += ' 🟡投信'
            sector     = info.get('sector', '')
            sector_tag = f'  【{sector}】' if sector else ''
            cand_tag   = f'  ⚠️ {info["candidate_reason"]}' if info.get('candidate_reason') else ''

            df['MA5']  = df['Close'].rolling(5).mean()
            df['MA20'] = df['Close'].rolling(20).mean()
            df['MA60'] = df['Close'].rolling(60).mean()

            plot_df = df.tail(120).copy()
            plot_df['DateStr'] = plot_df.index.strftime('%m-%d')

            fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                row_heights=[0.8, 0.2], vertical_spacing=0.03)

            fig.add_trace(go.Candlestick(
                x=plot_df['DateStr'],
                open=plot_df['Open'], high=plot_df['High'],
                low=plot_df['Low'],   close=plot_df['Close'],
                increasing_line_color='#E32636', decreasing_line_color='#008F39',
                increasing_fillcolor='#E32636',  decreasing_fillcolor='#008F39',
                increasing_line_width=0.7, decreasing_line_width=0.7,
                name='K線'
            ), row=1, col=1)

            fig.add_trace(go.Scatter(x=plot_df['DateStr'], y=plot_df['MA5'],
                                     line=dict(color='#e67e22', width=1), name='5MA'), row=1, col=1)
            fig.add_trace(go.Scatter(x=plot_df['DateStr'], y=plot_df['MA20'],
                                     line=dict(color='#8e44ad', width=1), name='20MA'), row=1, col=1)
            fig.add_trace(go.Scatter(x=plot_df['DateStr'], y=plot_df['MA60'],
                                     line=dict(color='#36b9cc', width=1.2), name='60MA'), row=1, col=1)

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
                title=dict(
                    text=f"<b>{sym}</b>  {info.get('name', '')}  ｜ {pattern_str}{inst_tags}{sector_tag}{cand_tag}",
                    font=dict(color='black', size=15)
                ),
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
                st.checkbox('加入名單', key=f'cb_{tab_prefix}_{sym}')
                st.plotly_chart(fig, use_container_width=True, key=f"fig_{tab_prefix}_{sym}",
                                theme=None, config={'staticPlot': True, 'displayModeBar': False})
                st.markdown("<br>", unsafe_allow_html=True)

        except Exception:
            continue

    st.write("---")
    st.write("已經到底囉！")


# ==========================================
# 主清單 Tab
# ==========================================
with tab_main:
    col_pattern, col_sector, col_sort = st.columns([2, 4, 2])
    with col_pattern:
        pattern_options  = ['全部', '型態A（漲後整理）', '型態B（多頭排列）']
        selected_pattern = st.selectbox('型態', pattern_options, label_visibility='collapsed', key='main_pattern')
    with col_sector:
        all_sectors      = sorted(set(r.get('sector', '') for r in results_all if r.get('sector', '')))
        selected_sectors = st.multiselect('產業別', all_sectors, placeholder='全部產業', key='main_sector')
    with col_sort:
        sort_options  = ['代號', '成交量↓', '收盤價↓', '外資買超↓', '投信買超↓']
        selected_sort = st.selectbox('排序', sort_options, label_visibility='collapsed', key='main_sort')

    pattern_filter_map = {'型態A（漲後整理）': 'A', '型態B（多頭排列）': 'B'}
    results = list(results_all)
    if selected_pattern != '全部':
        results = [r for r in results if r.get('pattern') == pattern_filter_map[selected_pattern]]
    if selected_sectors:
        results = [r for r in results if r.get('sector', '') in selected_sectors]

    sort_key_map = {
        '代號':      lambda x: x['symbol'],
        '成交量↓':   lambda x: -x.get('volume', 0),
        '收盤價↓':   lambda x: -x.get('close', 0),
        '外資買超↓': lambda x: -x.get('foreign_amount', 0),
        '投信買超↓': lambda x: -x.get('trust_amount', 0),
    }
    results.sort(key=sort_key_map[selected_sort])

    main_syms = tuple(r['symbol'] for r in results)
    if main_syms:
        with st.spinner(f"載入 {len(main_syms)} 檔 K 線資料..."):
            stock_data_main = fetch_charts(main_syms)
        render_chart_grid(results, stock_data_main, tab_prefix='main')
    else:
        st.info("目前沒有符合條件的標的。")


# ==========================================
# 候補清單 Tab
# ==========================================
with tab_cand:
    col_sector2, col_sort2 = st.columns([5, 2])
    with col_sector2:
        all_sectors2      = sorted(set(r.get('sector', '') for r in candidates_all if r.get('sector', '')))
        selected_sectors2 = st.multiselect('產業別', all_sectors2, placeholder='全部產業', key='cand_sector')
    with col_sort2:
        selected_sort2 = st.selectbox('排序', sort_options, label_visibility='collapsed', key='cand_sort')

    candidates = list(candidates_all)
    if selected_sectors2:
        candidates = [r for r in candidates if r.get('sector', '') in selected_sectors2]
    candidates.sort(key=sort_key_map[selected_sort2])

    cand_syms = tuple(r['symbol'] for r in candidates)
    if cand_syms:
        with st.spinner(f"載入 {len(cand_syms)} 檔 K 線資料..."):
            stock_data_cand = fetch_charts(cand_syms)
        render_chart_grid(candidates, stock_data_cand, tab_prefix='cand')
    else:
        st.info("目前沒有候補標的。")
