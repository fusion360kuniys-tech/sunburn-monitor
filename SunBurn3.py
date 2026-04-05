import streamlit as st
import pandas as pd
import plotly.express as px
import sqlite3
import random
import time
from datetime import datetime, timedelta

# ==========================================
# 1. 基本設定
# ==========================================
DB_NAME = "greenhouse_data.db"
SAVE_INTERVAL_SEC = 60
VIEW_INTERVAL_SEC = 5
SUNBURN_LIMIT_MINUTES = int(2.2 * 60) # 2.2時間 = 132分
NOTIFICATION_COOLDOWN_SEC = 300 # 通知のクールダウン（5分）

# ==========================================
# 2. セッションステートの初期化
# ==========================================
if 'last_saved_time' not in st.session_state:
    st.session_state.last_saved_time = None # 初回保存判定用
if 'last_notification_time' not in st.session_state:
    st.session_state.last_notification_time = datetime.min

# ==========================================
# 3. ロジック関数
# ==========================================
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sensor_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT, 
                temperature REAL,
                solar_rad REAL,
                fruit_temp REAL,
                limit_temp REAL,
                is_alert INTEGER,
                reset_flag INTEGER DEFAULT 0
            )
        """)
        # 既存DBへのカラム追加（エラー回避）
        try:
            conn.execute("ALTER TABLE sensor_logs ADD COLUMN limit_temp REAL")
        except sqlite3.OperationalError:
            pass

def save_to_db(t, s, f, limit_t, alert):
    now_jst = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute(
            "INSERT INTO sensor_logs (timestamp, temperature, solar_rad, fruit_temp, limit_temp, is_alert, reset_flag) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (now_jst, t, s, f, limit_t, 1 if alert else 0)
        )

def get_accumulated_minutes():
    with sqlite3.connect(DB_NAME) as conn:
        query = "SELECT COUNT(*) FROM sensor_logs WHERE is_alert = 1 AND reset_flag = 0"
        return conn.execute(query).fetchone()[0]

def load_data_range(start_date, end_date):
    with sqlite3.connect(DB_NAME) as conn:
        query = "SELECT timestamp, temperature, solar_rad, fruit_temp, limit_temp, is_alert, reset_flag FROM sensor_logs WHERE timestamp BETWEEN ? AND ?"
        df = pd.read_sql_query(query, conn, params=(
            start_date.strftime('%Y-%m-%d 00:00:00'), 
            end_date.strftime('%Y-%m-%d 23:59:59')
        ))
        if not df.empty:
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            # 累積計算
            df['acc_mins'] = (df['is_alert'] & (df['reset_flag'] == 0)).cumsum()
        return df

def get_csv_download_data(target_date):
    with sqlite3.connect(DB_NAME) as conn:
        query = "SELECT timestamp, temperature, solar_rad, fruit_temp, limit_temp, is_alert FROM sensor_logs WHERE timestamp LIKE ?"
        df = pd.read_sql_query(query, conn, params=(f"{target_date.strftime('%Y-%m-%d')}%",))
        # CSV出力用に日本語カラム名に変換（オプション）
        df.columns = ["日時", "気温(℃)", "日射量(W/m²)", "果実温度(℃)", "限界温度閾値(℃)", "アラート判定"]
        return df

def calculate_default_threshold(solar):
    if solar <= 600:
        return 52.0
    elif 600 < solar <= 700:
        return 52.0 - ((52.0 - 49.0) / 100.0) * (solar - 600)
    elif 700 < solar <= 800:
        return 49.0 - ((49.0 - 47.0) / 100.0) * (solar - 700)
    else:
        return 47.0

# ==========================================
# 4. UI 構築
# ==========================================
st.set_page_config(page_title="Sunburn Monitor", layout="wide")

alert_area = st.empty()
critical_area = st.empty()
st.title("🌿 園芸施設：日焼けダメージ蓄積モニタリング")

# --- サイドバー ---
st.sidebar.header("🛠️ 管理設定")
export_date = st.sidebar.date_input("ログ出力日を選択", datetime.now())
if st.sidebar.button("CSVデータを生成"):
    csv_df = get_csv_download_data(export_date)
    if not csv_df.empty:
        csv_data = csv_df.to_csv(index=False).encode('utf-8-sig')
        st.sidebar.download_button("📥 CSVダウンロード", csv_data, f"log_{export_date}.csv", "text/csv")
    else:
        st.sidebar.warning("データがありません。")

if st.sidebar.button("🚨 累積ダメージをリセット"):
    with sqlite3.connect(DB_NAME) as conn:
        conn.execute("UPDATE sensor_logs SET reset_flag = 1 WHERE reset_flag = 0")
    st.sidebar.success("リセットしました")

st.sidebar.subheader("⚙️ 判定モード")
threshold_mode = st.sidebar.radio("ロジック", ("デフォルト", "カスタム"))

param_a, param_b = 0.0, 0.0
if threshold_mode == "カスタム":
    p1_solar, p1_temp = st.sidebar.number_input("日射1", value=800), st.sidebar.number_input("温度1", value=47.0)
    p2_solar, p2_temp = st.sidebar.number_input("日射2", value=600), st.sidebar.number_input("温度2", value=52.0)
    if p1_solar != p2_solar:
        param_a = (p2_temp - p1_temp) / (p2_solar - p1_solar)
        param_b = p1_temp - (param_a * p1_solar)

# グラフ・メトリクス表示エリア
metrics_area = st.empty()
chart_solar = st.empty()
chart_temp = st.empty()
chart_acc = st.empty()

init_db()

# ==========================================
# 5. メインループ
# ==========================================
try:
    now = datetime.now()
    
    # 擬似データ生成
    t = round(random.uniform(25.0, 35.0), 1)
    s = round(random.uniform(500.0, 950.0), 1) 
    f = round(random.uniform(45.0, 55.0), 1)

    # 閾値計算
    limit_t = round(calculate_default_threshold(s) if threshold_mode == "デフォルト" else (param_a * s + param_b), 2)
    is_current_alert = f > limit_t
    
    # データ保存ロジック（初回またはSAVE_INTERVAL経過後）
    if st.session_state.last_saved_time is None or (now - st.session_state.last_saved_time).total_seconds() >= SAVE_INTERVAL_SEC:
        save_to_db(t, s, f, limit_t, is_current_alert)
        st.session_state.last_saved_time = now 
        
    acc_mins = get_accumulated_minutes()
    display_df = load_data_range(now - timedelta(days=1), now).tail(100)
    
    # --- アラート表示 ---
    with critical_area:
        if acc_mins >= SUNBURN_LIMIT_MINUTES:
            st.error(f"🔥 【重大警告】累積ダメージが限界({SUNBURN_LIMIT_MINUTES}分)を超過しました！")

    with alert_area:
        if is_current_alert:
            # 1. 表示：温度を超えている間は「常に」赤色で表示
            st.error(f"🚨 【現在】果実温度が限界超過中！ ({f}℃ > {limit_t}℃)")
            
            # 2. 通知：外部通知（シミュレーション）は5分間隔に制限
            time_since_notif = (now - st.session_state.last_notification_time).total_seconds()
            if time_since_notif >= NOTIFICATION_COOLDOWN_SEC:
                st.toast(f"管理者へ通知を送信しました: {f}℃")
                st.session_state.last_notification_time = now
        else:
            st.success(f"✅ 正常（限界温度: {limit_t}℃）")

    # --- メトリクス表示 ---
    with metrics_area.container():
        m1, m2, m3, m4 = st.columns(4)
        st.caption(f"最終更新: {now.strftime('%H:%M:%S')}")
        m1.metric("気温", f"{t} °C")
        m2.metric("果実温度", f"{f} °C")
        m3.metric("日射量", f"{int(s)} W/m²")
        m4.metric("累積", f"{acc_mins} 分")

    # --- グラフ表示 ---
    if not display_df.empty:
        with chart_solar:
            fig_s = px.line(display_df, x='timestamp', y='solar_rad', title="日射量 (W/m²)", color_discrete_sequence=["#FECB52"])
            st.plotly_chart(fig_s, use_container_width=True, key="s")
        
        with chart_temp:
            # グラフ描画用にlimit_tempを現在設定で再計算（描画をスムーズにするため）
            if threshold_mode == "デフォルト":
                display_df['limit_temp'] = display_df['solar_rad'].apply(calculate_default_threshold)
            else:
                display_df['limit_temp'] = param_a * display_df['solar_rad'] + param_b

            fig_t = px.line(display_df, x='timestamp', y=['temperature', 'fruit_temp', 'limit_temp'], 
                            title="温度詳細 (°C)", color_discrete_map={"temperature":"#333", "fruit_temp":"#AB63FA", "limit_temp":"#FF4B4B"})
            fig_t.update_traces(patch={"line": {"dash": "dash"}}, selector={"name": "limit_temp"})
            st.plotly_chart(fig_t, use_container_width=True, key="t")

        with chart_acc:
            fig_a = px.area(display_df, x='timestamp', y='acc_mins', title="累積ダメージ時間 (分)", color_discrete_sequence=["#EF553B"])
            fig_a.add_hline(y=SUNBURN_LIMIT_MINUTES, line_dash="dot", line_color="red")
            st.plotly_chart(fig_a, use_container_width=True, key="a")
    else:
        st.info("📊 データを蓄積しています。最初の保存までしばらくお待ちください...")

    time.sleep(VIEW_INTERVAL_SEC)
    st.rerun()

except Exception as e:
    st.error(f"システムエラー: {e}")
    time.sleep(5)
    st.rerun()