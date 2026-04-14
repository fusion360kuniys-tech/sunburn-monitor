import streamlit as st
import pandas as pd
import plotly.express as px
from sqlalchemy import create_engine, text
import time
from datetime import datetime, timedelta, timezone

# ==========================================
# 1. 基本設定とタイムゾーン（日本時間）
# ==========================================
VIEW_INTERVAL_SEC = 10 # 画面更新の間隔
SUNBURN_LIMIT_MINUTES = int(2.2 * 60) 
JST = timezone(timedelta(hours=+9), 'JST')

# ==========================================
# 2. データベース接続（Supabase）
# ==========================================
@st.cache_resource
def init_connection():
    db_url = st.secrets["SUPABASE_URL"]
    return create_engine(db_url)

engine = init_connection()

# ==========================================
# 3. データの読み込み・操作関数
# ==========================================
def load_latest_data():
    """最新の1件を取得"""
    with engine.connect() as conn:
        query = text("SELECT * FROM sensor_logs ORDER BY timestamp DESC LIMIT 1")
        return pd.read_sql_query(query, conn)

def load_history(days=1):
    """過去24時間の履歴を取得"""
    now = datetime.now(JST)
    start_time = now - timedelta(days=days)
    with engine.connect() as conn:
        query = text("SELECT * FROM sensor_logs WHERE timestamp > :start ORDER BY timestamp ASC")
        df = pd.read_sql_query(query, conn, params={"start": start_time})
        if not df.empty:
            df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_convert(JST)
            # 累積ダメージの計算（reset_flagが0の区間のみ）
            df['acc_mins'] = (df['is_alert'] & (df['reset_flag'] == 0)).cumsum()
        return df

def get_accumulated_total():
    """現在のリセットされていない累積時間を取得"""
    with engine.connect() as conn:
        query = text("SELECT COUNT(*) FROM sensor_logs WHERE is_alert = 1 AND reset_flag = 0")
        result = conn.execute(query).fetchone()
        return result[0] if result else 0

def reset_accumulation():
    with engine.begin() as conn:
        conn.execute(text("UPDATE sensor_logs SET reset_flag = 1 WHERE reset_flag = 0"))
        st.success("累積ダメージをリセットしました。")

# ==========================================
# 4. UI 構築
# ==========================================
st.set_page_config(page_title="Sunburn Monitor", layout="wide")
st.title("🌿 園芸施設：実測データモニタリング")

# --- サイドバー ---
st.sidebar.header("🛠️ 管理設定")
if st.sidebar.button("🚨 累積ダメージをリセット"):
    reset_accumulation()

# --- メイン表示 ---
latest_df = load_latest_data()
history_df = load_history()

if not latest_df.empty:
    row = latest_df.iloc[0]
    acc_total = get_accumulated_total()
    
    # アラート表示
    if row['is_alert'] == 1:
        st.error(f"🚨 【現在警告】果実温度が限界を超えています！ ({row['fruit_temp']}℃)")
    if acc_total >= SUNBURN_LIMIT_MINUTES:
        st.warning(f"🔥 【重大】累積ダメージが限界({SUNBURN_LIMIT_MINUTES}分)を突破しました！")

    # メトリクス表示
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("気温", f"{row['temperature']} °C")
    m2.metric("果実温度", f"{row['fruit_temp']} °C")
    m3.metric("日射量", f"{int(row['solar_rad'])} W/m²")
    m4.metric("累積", f"{acc_total} 分")
    
    st.caption(f"最終データ受信時刻: {pd.to_datetime(row['timestamp']).astimezone(JST).strftime('%H:%M:%S')}")

    # グラフ表示
    if not history_df.empty:
        fig_temp = px.line(history_df, x='timestamp', y=['temperature', 'fruit_temp', 'limit_temp'], 
                           title="温度推移 (°C)", color_discrete_map={"temperature":"#333", "fruit_temp":"#AB63FA", "limit_temp":"#FF4B4B"})
        st.plotly_chart(fig_temp, use_container_width=True)
        
        fig_acc = px.area(history_df, x='timestamp', y='acc_mins', title="累積ダメージ推移 (分)", color_discrete_sequence=["#EF553B"])
        st.plotly_chart(fig_acc, use_container_width=True)
else:
    st.info("📡 データの受信待機中です。現場のデバイスを起動してください。")

# 指定秒数ごとに再読み込み
time.sleep(VIEW_INTERVAL_SEC)
st.rerun()
