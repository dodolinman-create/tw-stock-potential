import streamlit as st
import pandas as pd
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import json
import time
from io import StringIO
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
# 掃描參數
# ==========================================
MIN_PRICE = 20
MIN_AVG_VOLUME = 1000
VOLUME_RATIO = 1.2
NEAR_HIGH_RATIO = 0.85
CANDIDATE_NEAR_HIGH_RATIO = 0.82
MAX_SINGLE_DAY_RISE = 0.15
INSTITUTION_DAYS = 5
MIN_CONSECUTIVE_BUY_DAYS = 3
MA10_MA20_GAP_RATIO = 0.03
BATCH_SIZE = 50
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

PATTERN_LABEL = {'A': '型態A（漲後整理）', 'B': '型態B（多頭排列）'}


# ==========================================
# 掃描函式
# ==========================================
def fetch_sector_map():
    sector_map = {}
    for mode in ['2', '4']:
        url = f'https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}'
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30, verify=False)
            resp.encoding = 'big5'
            tables = pd.read_html(StringIO(resp.text), header=0)
            if not tables:
                continue
            df = tables[0]
            for _, row in df.iterrows():
                first = str(row.iloc[0])
                if '　' not in first:
                    continue
                code = first.split('　')[0].strip()
                if not code.isdigit() or len(code) < 4:
                    continue
                sector = str(row.iloc[4]).strip() if len(row) > 4 else ''
                if sector and sector.lower() != 'nan':
                    sector_map[code] = sector
        except Exception:
            pass
    return sector_map


def get_recent_trading_dates(n=5):
    dates = []
    d = datetime.now()
    while len(dates) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            dates.append(d)
    return dates


def fetch_twse_institution(date):
    date_str = date.strftime('%Y%m%d')
    url = f"https://www.twse.com.tw/fund/T86?response=json&date={date_str}&selectType=ALLBUT0999"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        data = resp.json()
        if data.get('stat') != 'OK':
            return {}
        result = {}
        for row in data.get('data', []):
            code = row[0].strip()
            if not code.isdigit() or code.startswith('0'):
                continue
            name    = row[1].strip()
            foreign = int(row[4].replace(',', '').replace('+', '') or 0)
            trust   = int(row[10].replace(',', '').replace('+', '') or 0)
            result[code] = {'foreign': foreign, 'trust': trust, 'name': name}
        return result
    except Exception:
        return {}


def fetch_tpex_institution(date):
    roc_year = date.year - 1911
    date_str = f"{roc_year}/{date.month:02d}/{date.day:02d}"
    url = (
        "https://www.tpex.org.tw/web/stock/3insti/daily_trade/"
        f"3itrade_hedge_result.php?l=zh-tw&se=EW&t=D&d={requests.utils.quote(date_str)}"
    )
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15, verify=False)
        data = resp.json()
        tables = data.get('tables', [])
        if not tables:
            return {}
        result = {}
        for row in tables[0].get('data', []):
            code = row[0].strip()
            if not code.isdigit() or code.startswith('0'):
                continue
            name = row[1].strip()
            def parse(s):
                try:
                    return int(str(s).replace(',', '').replace('+', '') or 0)
                except:
                    return 0
            foreign = parse(row[4])
            trust   = parse(row[10])
            result[code] = {'foreign': foreign, 'trust': trust, 'name': name}
        return result
    except Exception:
        return {}


def get_institution_buyers(days=INSTITUTION_DAYS, min_consecutive=MIN_CONSECUTIVE_BUY_DAYS):
    dates = get_recent_trading_dates(days)
    per_day = {}
    for d in dates:
        twse = fetch_twse_institution(d)
        tpex = fetch_tpex_institution(d)
        combined = {**twse, **tpex}
        if not combined:
            continue
        for code, vals in combined.items():
            if code not in per_day:
                per_day[code] = {'name': vals.get('name', ''), 'daily': []}
            per_day[code]['daily'].append((vals['foreign'], vals['trust']))
            if not per_day[code]['name']:
                per_day[code]['name'] = vals.get('name', '')
        time.sleep(0.5)

    strict, loose = {}, {}
    for code, v in per_day.items():
        daily = v['daily']
        total_foreign = sum(f for f, t in daily)
        total_trust   = sum(t for f, t in daily)
        if not (total_foreign > 0 or total_trust > 0):
            continue
        entry = {'name': v['name'], 'foreign': total_foreign, 'trust': total_trust}
        recent = daily[:min_consecutive]
        is_consecutive = (len(recent) >= min_consecutive and
                          all(f > 0 or t > 0 for f, t in recent))
        if is_consecutive:
            strict[code] = entry
        else:
            loose[code] = entry
    return strict, loose


def passes_technical_filter(df, near_high_ratio=NEAR_HIGH_RATIO):
    if len(df) < 80:
        return None
    close  = df['Close'].astype(float)
    volume = df['Volume'].astype(float)
    ma5  = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    latest_close = float(close.iloc[-1])
    latest_ma5   = float(ma5.iloc[-1])
    latest_ma10  = float(ma10.iloc[-1])
    latest_ma20  = float(ma20.iloc[-1])
    latest_ma60_vals = ma60.dropna()
    if pd.isna(latest_ma5) or pd.isna(latest_ma10) or pd.isna(latest_ma20):
        return None
    if latest_close < MIN_PRICE:
        return None
    avg_vol_20 = float(volume.iloc[-20:].mean())
    if avg_vol_20 / 1000 < MIN_AVG_VOLUME:
        return None
    if len(latest_ma60_vals) < 1 or latest_close < float(latest_ma60_vals.iloc[-1]):
        return None
    if len(latest_ma60_vals) < 21:
        return None
    ma60_now = float(latest_ma60_vals.iloc[-1])
    ma60_5   = float(latest_ma60_vals.iloc[-6])
    ma60_10  = float(latest_ma60_vals.iloc[-11])
    ma60_20  = float(latest_ma60_vals.iloc[-21])
    if not (ma60_now > ma60_5 > ma60_10 > ma60_20):
        return None
    daily_ret = close.iloc[-20:].pct_change().dropna()
    if float(daily_ret.max()) > MAX_SINGLE_DAY_RISE:
        return None
    high_20 = float(df['High'].astype(float).iloc[-20:].max())
    if latest_close < high_20 * near_high_ratio:
        return None
    ma10_ma20_gap = abs(latest_ma10 - latest_ma20) / latest_ma20
    if ma10_ma20_gap <= MA10_MA20_GAP_RATIO:
        return 'A'
    elif latest_ma10 > latest_ma20 and latest_ma20 > ma60_now:
        return 'B'
    return None


def download_batch(tickers, start_date, end_date):
    all_data = {}
    total = (len(tickers) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(total):
        batch = tickers[i*BATCH_SIZE:(i+1)*BATCH_SIZE]
        if not batch:
            continue
        try:
            raw = yf.download(batch, start=start_date, end=end_date,
                              group_by='ticker', progress=False, auto_adjust=True)
            for sym in batch:
                try:
                    df = raw[sym] if len(batch) > 1 else raw
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(0)
                    df = df.dropna(subset=['Close'])
                    if not df.empty:
                        all_data[sym] = df
                except Exception:
                    pass
            time.sleep(1)
        except Exception:
            time.sleep(5)
    return all_data


def _build_result(sym, df, inst, sector_map, extra=None):
    latest = df.iloc[-1]
    ma20   = float(df['Close'].astype(float).rolling(20).mean().iloc[-1])
    code   = sym.split('.')[0]
    record = {
        'symbol':         sym,
        'name':           inst.get('name', ''),
        'close':          round(float(latest['Close']), 2),
        'ma20':           round(ma20, 2),
        'volume':         int(float(latest['Volume']) // 1000),
        'date':           df.index[-1].strftime('%Y-%m-%d'),
        'foreign_buy':    inst.get('foreign', 0) > 0,
        'trust_buy':      inst.get('trust', 0) > 0,
        'foreign_amount': inst.get('foreign', 0),
        'trust_amount':   inst.get('trust', 0),
        'sector':         sector_map.get(code, ''),
    }
    if extra:
        record.update(extra)
    return record


# ==========================================
# 主掃描（快取 12 小時）
# ==========================================
@st.cache_data(ttl=3600 * 12, show_spinner=False)
def run_scan():
    sector_map = fetch_sector_map()
    strict_buyers, loose_buyers = get_institution_buyers()

    if not strict_buyers and not loose_buyers:
        return None

    all_codes   = set(strict_buyers) | set(loose_buyers)
    all_tickers = [f"{c}.TW" for c in all_codes] + [f"{c}.TWO" for c in all_codes]

    end_date   = datetime.now() + timedelta(days=1)
    start_date = end_date - timedelta(days=200)
    data_dict  = download_batch(
        all_tickers,
        start_date.strftime('%Y-%m-%d'),
        end_date.strftime('%Y-%m-%d'),
    )

    main_results, main_codes = [], set()
    for sym, df in data_dict.items():
        code = sym.split('.')[0]
        if code not in strict_buyers:
            continue
        try:
            pattern = passes_technical_filter(df)
            if pattern:
                inst = strict_buyers[code]
                main_results.append(_build_result(sym, df, inst, sector_map, {'pattern': pattern}))
                main_codes.add(code)
        except Exception:
            continue
    main_results.sort(key=lambda x: x['symbol'])

    candidates, candidate_codes = [], set()
    for sym, df in data_dict.items():
        code = sym.split('.')[0]
        if code in main_codes or code in candidate_codes or code not in loose_buyers:
            continue
        try:
            pattern = passes_technical_filter(df)
            if pattern:
                inst = loose_buyers[code]
                candidates.append(_build_result(sym, df, inst, sector_map, {
                    'pattern': pattern, 'candidate_reason': '法人未連續買超',
                }))
                candidate_codes.add(code)
        except Exception:
            continue

    for sym, df in data_dict.items():
        code = sym.split('.')[0]
        if code in main_codes or code in candidate_codes or code not in strict_buyers:
            continue
        try:
            pattern = passes_technical_filter(df, near_high_ratio=CANDIDATE_NEAR_HIGH_RATIO)
            if pattern:
                close  = float(df['Close'].iloc[-1])
                high20 = float(df['High'].astype(float).iloc[-20:].max())
                pct    = close / high20 * 100
                inst   = strict_buyers[code]
                candidates.append(_build_result(sym, df, inst, sector_map, {
                    'pattern': pattern, 'candidate_reason': f'接近高點 {pct:.0f}%',
                }))
                candidate_codes.add(code)
        except Exception:
            continue

    candidates.sort(key=lambda x: x['symbol'])

    tw_time = datetime.utcnow() + timedelta(hours=8)
    return {
        'last_updated':     tw_time.strftime('%Y-%m-%d %H:%M:%S'),
        'total':            len(main_results),
        'results':          main_results,
        'candidates':       candidates,
        'candidates_total': len(candidates),
    }


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
# 載入資料（優先讀快取 JSON，無則掃描）
# ==========================================
def load_data():
    # 優先讀本機快取（本機用）
    json_path = 'screen_results.json'
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                cached = json.load(f)
            # 如果快取在 12 小時內，直接用
            last = datetime.strptime(cached.get('last_updated', '2000-01-01 00:00:00'), '%Y-%m-%d %H:%M:%S')
            if (datetime.now() - last).total_seconds() < 3600 * 12:
                return cached
        except Exception:
            pass
    # 否則線上掃描
    return run_scan()


# ==========================================
# 執行掃描
# ==========================================
with st.spinner("📡 資料載入中，首次執行需約 3～5 分鐘，請稍候..."):
    data_store = load_data()

if not data_store:
    st.error("❌ 無法取得資料，請稍後重試")
    if st.button("🔄 重新掃描"):
        st.cache_data.clear()
        st.rerun()
    st.stop()

last_updated   = data_store.get('last_updated', '未知')
results_all    = data_store.get('results', [])
candidates_all = data_store.get('candidates', [])


# ==========================================
# 標題列
# ==========================================
col_title, col_refresh = st.columns([8, 1])
with col_title:
    st.markdown(f"""
        <div style='display:flex; justify-content:space-between; align-items:baseline;
                    border-bottom:2px solid #000; padding-top:4px; padding-bottom:5px; margin-bottom:12px;'>
            <div style='font-size:2rem; font-weight:900;'>台股潛力股掃描</div>
            <div style='font-size:0.85rem; font-weight:700;'>更新：{last_updated}｜主清單 {len(results_all)} 檔｜候補 {len(candidates_all)} 檔</div>
        </div>
    """, unsafe_allow_html=True)
with col_refresh:
    if st.button("🔄 重新掃描", help="清除快取並重新從網路抓取資料"):
        st.cache_data.clear()
        st.rerun()

tab_main, tab_cand = st.tabs([f'主清單（{len(results_all)}）', f'候補清單（{len(candidates_all)}）'])


# ==========================================
# 共用繪圖函式
# ==========================================
def render_chart_grid(stock_list, stock_data, tab_prefix='main'):
    if not stock_list:
        st.info("目前沒有符合條件的標的。")
        return

    symbol_list = [r['symbol'] for r in stock_list]
    info_map    = {r['symbol']: r for r in stock_list}

    selected_syms = [sym for sym in symbol_list
                     if st.session_state.get(f'cb_{tab_prefix}_{sym}', False)]
    if selected_syms:
        tv_content = '\n'.join(
            f"{'TWSE' if s.endswith('.TW') else 'TPEX'}:{s.split('.')[0]}"
            for s in selected_syms
        )
        filename = f"{datetime.now().strftime('%Y%m%d')}.txt"
        st.download_button(
            label=f'⬇ 下載 {filename}（已選 {len(selected_syms)} 檔）',
            data=tv_content.encode('utf-8'),
            file_name=filename,
            mime='text/plain',
            key=f'dl_{tab_prefix}_{len(selected_syms)}',
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
