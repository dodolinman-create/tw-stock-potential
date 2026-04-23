import requests
import pandas as pd
import yfinance as yf
import json
import time
from datetime import datetime, timedelta

# ==========================================
# 參數設定
# ==========================================
MIN_PRICE = 20              # 最低股價（元）
MIN_AVG_VOLUME = 1000       # 20 日均量最低門檻（張）
VOLUME_RATIO = 1.2          # 近 3 日均量 / 20 日均量
NEAR_HIGH_RATIO = 0.90      # 收盤 >= 20 日最高 × 90%
MAX_SINGLE_DAY_RISE = 0.15  # 排除近 20 日最大單日漲幅 > 15%
INSTITUTION_DAYS = 5        # 法人買超累計天數
MA10_MA20_GAP_RATIO = 0.03  # MA10 與 MA20 糾結門檻（3%）
BATCH_SIZE = 50

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}


# ==========================================
# Step 1：取得近 N 個交易日日期
# ==========================================
def get_recent_trading_dates(n=5):
    """回傳最近 n 個「可能是交易日」的日期列表（跳過週末）"""
    dates = []
    d = datetime.now()
    while len(dates) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:  # 0=Mon ~ 4=Fri
            dates.append(d)
    return dates


# ==========================================
# Step 2：爬上市（TWSE）三大法人
# ==========================================
def fetch_twse_institution(date: datetime):
    """回傳 dict: {股票代號: {'foreign': int, 'trust': int, 'name': str}}"""
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
    except Exception as e:
        print(f"   ⚠️ TWSE {date_str} 失敗：{e}")
        return {}


# ==========================================
# Step 3：爬上櫃（TPEX）三大法人
# ==========================================
def fetch_tpex_institution(date: datetime):
    """回傳 dict: {股票代號: {'foreign': int, 'trust': int, 'name': str}}"""
    # TPEX 使用民國年
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
            # 欄位結構（共 24 欄）：
            # 0:代號 1:名稱
            # 2-4: 外資  買/賣/超
            # 5-7: 外資自營  買/賣/超
            # 8-10: 投信  買/賣/超
            # 11-13: 自營(自行) 買/賣/超
            # 14-16: 自營(避險) 買/賣/超
            # 17-19: 合計外資 買/賣/超
            # 20-22: 合計自營 買/賣/超
            # 23: 三大法人合計超
            def parse(s):
                try:
                    return int(str(s).replace(',', '').replace('+', '') or 0)
                except:
                    return 0
            foreign = parse(row[4])   # 外資買賣超
            trust   = parse(row[10])  # 投信買賣超
            result[code] = {'foreign': foreign, 'trust': trust, 'name': name}
        return result
    except Exception as e:
        print(f"   ⚠️ TPEX {date_str} 失敗：{e}")
        return {}


# ==========================================
# Step 4：彙整 N 日累計法人買超
# ==========================================
def get_institution_buyers(days=INSTITUTION_DAYS):
    print(f"📡 抓取近 {days} 個交易日法人買超資料...")
    dates = get_recent_trading_dates(days)

    # {code: {foreign_sum, trust_sum, name}}
    accum = {}

    for d in dates:
        print(f"   日期：{d.strftime('%Y-%m-%d')}", end=" ")
        twse = fetch_twse_institution(d)
        tpex = fetch_tpex_institution(d)
        combined = {**twse, **tpex}
        if not combined:
            print("（無資料，可能為假日）")
            continue
        print(f"→ {len(combined)} 檔")
        for code, vals in combined.items():
            if code not in accum:
                accum[code] = {'foreign': 0, 'trust': 0, 'name': vals.get('name', '')}
            accum[code]['foreign'] += vals['foreign']
            accum[code]['trust']   += vals['trust']
            if not accum[code]['name']:
                accum[code]['name'] = vals.get('name', '')
        time.sleep(1)

    # 篩選：外資或投信任一累計買超 > 0，回傳 {code: {'name', 'foreign', 'trust'}}
    buyers = {
        code: {'name': v['name'], 'foreign': v['foreign'], 'trust': v['trust']}
        for code, v in accum.items()
        if v['foreign'] > 0 or v['trust'] > 0
    }
    print(f"✅ 外資或投信近 {days} 日有淨買超：{len(buyers)} 檔")
    return buyers


# ==========================================
# Step 5：技術面篩選
# ==========================================
def passes_technical_filter(df):
    """回傳型態字串 'A'（漲後整理）或 'B'（多頭排列），不符合回傳 None"""
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

    # 1. 股價門檻
    if latest_close < MIN_PRICE:
        return None

    # 2. 流動性（張）
    avg_vol_20 = float(volume.iloc[-20:].mean())
    if avg_vol_20 / 1000 < MIN_AVG_VOLUME:
        return None

    # 3. 收盤在 MA60 之上
    if len(latest_ma60_vals) < 1 or latest_close < float(latest_ma60_vals.iloc[-1]):
        return None

    # 4. MA60 穩定向上（四點多段確認）
    if len(latest_ma60_vals) < 21:
        return None
    ma60_now = float(latest_ma60_vals.iloc[-1])
    ma60_5   = float(latest_ma60_vals.iloc[-6])
    ma60_10  = float(latest_ma60_vals.iloc[-11])
    ma60_20  = float(latest_ma60_vals.iloc[-21])
    if not (ma60_now > ma60_5 > ma60_10 > ma60_20):
        return None

    # 5. 接近 20 日高點
    high_20 = float(df['High'].astype(float).iloc[-20:].max())
    if latest_close < high_20 * NEAR_HIGH_RATIO:
        return None

    # 7. 排除剛暴漲（追高風險）
    daily_ret = close.iloc[-20:].pct_change().dropna()
    if float(daily_ret.max()) > MAX_SINGLE_DAY_RISE:
        return None

    # 8. 型態分類
    ma10_ma20_gap = abs(latest_ma10 - latest_ma20) / latest_ma20
    if ma10_ma20_gap <= MA10_MA20_GAP_RATIO:
        return 'A'  # 漲後整理（均線糾結蓄力）
    elif latest_ma10 > latest_ma20 and latest_ma20 > ma60_now:
        return 'B'  # 多頭排列（MA10 > MA20 > MA60）
    return None


# ==========================================
# Step 6：批次下載 yfinance
# ==========================================
def download_batch(tickers, start_date, end_date):
    all_data = {}
    total = (len(tickers) + BATCH_SIZE - 1) // BATCH_SIZE
    for i in range(total):
        batch = tickers[i*BATCH_SIZE:(i+1)*BATCH_SIZE]
        if not batch:
            continue
        print(f"   ➤ 第 {i+1}/{total} 批次 ({len(batch)} 檔)...", end=" ", flush=True)
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
            print("✅")
            time.sleep(2)
        except Exception as e:
            print(f"❌ ({e})")
            time.sleep(10)
    return all_data


# ==========================================
# 主程式
# ==========================================
def main():
    print("=" * 50)
    print("  台股強勢潛力股掃描器")
    print(f"  執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    # Step 1：法人買超清單
    institution_map = get_institution_buyers()
    if not institution_map:
        print("❌ 無法取得法人資料，程式終止")
        return

    # institution_map = {code: {'name': str, 'foreign': int, 'trust': int}}
    codes = list(institution_map.keys())

    # 轉 yfinance 代號（上市 .TW、上櫃 .TWO 都試）
    all_tickers = [f"{c}.TW" for c in codes] + \
                  [f"{c}.TWO" for c in codes]

    print(f"\n📦 下載 {len(all_tickers)} 個代號的技術資料...")
    end_date   = datetime.now() + timedelta(days=1)
    start_date = end_date - timedelta(days=200)

    data_dict = download_batch(
        all_tickers,
        start_date.strftime('%Y-%m-%d'),
        end_date.strftime('%Y-%m-%d'),
    )

    # Step 2：技術面篩選
    print(f"\n🔍 技術面篩選（共 {len(data_dict)} 檔有資料）...")
    passed = []
    for sym, df in data_dict.items():
        try:
            pattern = passes_technical_filter(df)
            if pattern:
                latest = df.iloc[-1]
                ma20 = float(df['Close'].astype(float).rolling(20).mean().iloc[-1])
                code = sym.replace('.TW', '').replace('.TWO', '')
                inst = institution_map.get(code, {})
                passed.append({
                    'symbol':      sym,
                    'name':        inst.get('name', ''),
                    'close':       round(float(latest['Close']), 2),
                    'ma20':        round(ma20, 2),
                    'volume':      int(float(latest['Volume']) // 1000),
                    'date':        df.index[-1].strftime('%Y-%m-%d'),
                    'pattern':     pattern,
                    'foreign_buy': inst.get('foreign', 0) > 0,
                    'trust_buy':   inst.get('trust', 0) > 0,
                })
        except Exception:
            continue

    passed.sort(key=lambda x: x['symbol'])

    tw_time = datetime.utcnow() + timedelta(hours=8)
    output = {
        'last_updated': tw_time.strftime('%Y-%m-%d %H:%M:%S'),
        'total': len(passed),
        'results': passed,
    }

    with open('screen_results.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 完成！共找到 {len(passed)} 檔強勢潛力股")
    print("📄 已存入 screen_results.json")


if __name__ == '__main__':
    main()
