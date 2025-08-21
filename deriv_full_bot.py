# deriv_full_bot.py
import websocket, json, pandas as pd, numpy as np, time, logging, requests
from datetime import datetime
from threading import Thread
import telegram
from scipy.stats import linregress

# ===== CONFIG =====
DERIV_APP_ID = "1089"
DERIV_TOKEN = "YOUR_DERIV_TOKEN"
DERIV_WS = f"wss://ws.binaryws.com/websockets/v3?app_id={DERIV_APP_ID}"

BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID = "YOUR_CHAT_ID"

PAIRS = ["frxXAUUSD", "R_75", "R_75S"]
TIMEFRAMES = [1,5,10]
CONFIDENCE_THRESHOLD = 5
RATE_LIMIT_MINUTES = 25

logging.basicConfig(format="%(asctime)s - %(message)s",
                    level=logging.INFO,
                    filename="deriv_full_bot.log")
bot = telegram.Bot(token=BOT_TOKEN)

# ===== BOT CLASS =====
class FullBrainBot:
    def __init__(self):
        self.ws = None
        self.active = True
        self.data = {pair: {tf: pd.DataFrame() for tf in TIMEFRAMES} for pair in PAIRS}
        self.sent_signals = {}
        self.connected_sent = False

    # --- Price Action Tools ---
    def calculate_support_resistance(self, df, window=20):
        return df["low"].tail(window).min(), df["high"].tail(window).max()

    def identify_zones(self, df):
        return df["low"].tail(10).min(), df["high"].tail(10).max()

    def detect_trendline(self, df):
        highs = df["high"].tail(10).values
        lows = df["low"].tail(10).values
        x = np.arange(10)
        return linregress(x, highs).slope, linregress(x, lows).slope

    def detect_breakout(self, df):
        if len(df) < 20:
            return None
        high, low = df["high"].tail(10).max(), df["low"].tail(10).min()
        if df["close"].iloc[-1] > high: return "Breakout Up"
        if df["close"].iloc[-1] < low: return "Breakout Down"
        return None

    def detect_head_shoulders(self, df):
        if len(df) < 7: return None
        c = df["close"].tail(7).values
        if c[0] < c[2] > c[4] and c[2] > c[0] and c[2] > c[4]: return "H&S pattern"
        return None

    def combine_price_action_factors(self, df):
        support, resistance = self.calculate_support_resistance(df)
        demand, supply = self.identify_zones(df)
        slope_high, slope_low = self.detect_trendline(df)
        breakout = self.detect_breakout(df)
        hs_pattern = self.detect_head_shoulders(df)

        confidence = 0
        if df["close"].iloc[-1] > support: confidence += 1
        if df["close"].iloc[-1] < resistance: confidence += 1
        if breakout: confidence += 2
        if hs_pattern: confidence += 2
        if slope_high > 0: confidence += 1
        if slope_low < 0: confidence += 1

        if confidence >= CONFIDENCE_THRESHOLD:
            if df["close"].iloc[-1] > resistance:
                return {"type": "LONG", "entry": df["close"].iloc[-1], "sl": support, "tp": resistance}
            elif df["close"].iloc[-1] < support:
                return {"type": "SHORT", "entry": df["close"].iloc[-1], "sl": resistance, "tp": support}
        return None

    # --- Telegram Alert ---
    def send_alert(self, pair, tf, signal):
        key = f"{pair}_{tf}_{signal['type']}_{signal['entry']:.2f}"
        if key in self.sent_signals and (datetime.now() - self.sent_signals[key]).seconds/60 < RATE_LIMIT_MINUTES:
            return
        self.sent_signals[key] = datetime.now()
        msg = (f"📈 *{pair} {tf}min {signal['type']}*\n"
               f"➡️ Entry: `{signal['entry']:.2f}`\n"
               f"🛑 Stop: `{signal['sl']:.2f}`\n"
               f"🎯 Target: `{signal['tp']:.2f}`\n"
               f"⏰ {datetime.now().strftime('%H:%M UTC')}")
        try:
            bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
        except Exception as e:
            logging.error(f"TG error: {e}")

    # --- WebSocket ---
    def ws_thread(self):
        while self.active:
            try:
                self.ws = websocket.WebSocketApp(DERIV_WS,
                                                 on_open=self.on_open,
                                                 on_message=self.on_message,
                                                 on_error=self.on_error,
                                                 on_close=self.on_close)
                self.ws.run_forever()
            except Exception as e:
                logging.error(f"WS crash: {e}")
                time.sleep(5)

    def on_open(self, ws):
        logging.info("Connected to Deriv WS")
        ws.send(json.dumps({"authorize": DERIV_TOKEN}))
        if not self.connected_sent:
            bot.send_message(chat_id=CHAT_ID, text="✅ Bot Connected and ready to analyze signals")
            self.connected_sent = True
        for pair in PAIRS:
            for tf in TIMEFRAMES:
                ws.send(json.dumps({"ticks_history": pair,
                                    "adjust_start_time": 1,
                                    "count": 100,
                                    "granularity": tf*60,
                                    "subscribe": 1}))

    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            if "ohlc" not in data: return
            pair = data["ohlc"]["symbol"]
            tf = int(data["ohlc"]["granularity"]) // 60
            if tf not in TIMEFRAMES or pair not in PAIRS: return
            new_candle = pd.DataFrame([{"time": datetime.fromtimestamp(data["ohlc"]["epoch"]),
                                        "open": float(data["ohlc"]["open"]),
                                        "high": float(data["ohlc"]["high"]),
                                        "low": float(data["ohlc"]["low"]),
                                        "close": float(data["ohlc"]["close"])}])
            self.data[pair][tf] = pd.concat([self.data[pair][tf], new_candle]).tail(100)
            if signal := self.combine_price_action_factors(self.data[pair][tf]):
                self.send_alert(pair, tf, signal)
        except Exception as e:
            logging.error(f"Message error: {e}")

    def on_error(self, ws, error):
        logging.warning(f"WS error ignored: {error}")

    def on_close(self, ws, close_status, close_msg):
        logging.info(f"Disconnected: {close_msg}")
        time.sleep(5)

    def run(self):
        logging.info("Starting FullBrain Bot")
        Thread(target=self.ws_thread).start()
        while self.active: time.sleep(1)

if __name__ == "__main__":
    FullBrainBot().run()
