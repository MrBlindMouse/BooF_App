import requests
import json
import time
from dotenv import dotenv_values
import websockets
import asyncio
from typing import Optional, Dict, Any, Set, List
from collections import deque
import hashlib
import hmac
import logging
import pickle
import os
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class WebSocketClient:
    def __init__(self, uri: str, max_retries: int = 5, backoff_factor: float = 2):
        self.uri = uri
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.message_queue = deque()
        self.running = False
        self.current_subscriptions: Set[str] = set()
        self.config = dotenv_values(".env")

    async def connect(self) -> Optional[websockets.WebSocketClientProtocol]:
        for attempt in range(self.max_retries):
            try:
                key = self.config.get("VALR_KEY")
                secret = self.config.get("VALR_SECRET")
                
                if not key or not secret:
                    logger.error("Missing VALR_KEY or VALR_SECRET in .env file")
                    return None
                
                ts = int(time.time() * 1000)
                path = self.uri[self.uri.find("/ws"):]
                sign = self.get_signature(secret=secret, ts=ts, verb="GET", path=path)

                headers = {
                    'X-VALR-API-KEY': key,
                    'X-VALR-SIGNATURE': sign,
                    'X-VALR-TIMESTAMP': str(ts),
                }

                self.websocket = await asyncio.wait_for(
                    websockets.connect(self.uri, additional_headers=headers, ping_interval=30),
                    timeout=10
                )
                logger.info("WebSocket connection established")
                return self.websocket
                
            except (websockets.exceptions.WebSocketException, asyncio.TimeoutError) as e:
                delay = min(60, self.backoff_factor ** attempt)  # Cap max delay
                logger.warning(f"Connection attempt {attempt + 1} failed: {e}. Retrying in {delay}s...")
                await asyncio.sleep(delay)
                
        logger.error("Max reconnection attempts reached")
        return None

    def get_signature(self, secret: str, ts: int, verb: str, path: str, body: str = "") -> str:
        payload = f"{ts}{verb.upper()}{path}{body}"
        message = payload.encode('utf-8')
        signature = hmac.new(
            secret.encode('utf-8'), 
            message, 
            digestmod=hashlib.sha512
        ).hexdigest()
        return signature

    async def send_message(self, message: str):
        if not self.websocket:
            self.message_queue.append(message)
            return
        try:
            await self.websocket.send(message)
            logger.debug(f"Sent message: {message}")
        except websockets.exceptions.ConnectionClosed:
            self.message_queue.append(message)
            logger.warning("Cannot send: Connection closed")
        except Exception as e:
            logger.error(f"Cannot send: {e}")

    async def flush_queue(self):
        while self.message_queue and self.websocket:
            message = self.message_queue.popleft()
            await self.send_message(message)

    async def receive_message(self):
        try:
            message = await asyncio.wait_for(self.websocket.recv(), timeout=60)
            process_message(json.loads(message))
        except asyncio.TimeoutError:
            logger.warning("No message received within timeout")
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Connection closed")
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode JSON message: {e}")
        except Exception as e:
            logger.error(f"Receive error: {e}")

    async def send_ping(self):
        while self.running and self.websocket:
            try:
                await self.websocket.ping()
                await asyncio.sleep(30)
            except Exception as e:
                logger.error(f"Ping error: {e}")
                break

    async def close(self):
        if self.websocket:
            try:
                await self.websocket.close()
                logger.info("Connection closed gracefully")
            except Exception as e:
                logger.error(f"Close error: {e}")
            finally:
                self.websocket = None

    async def update_subscriptions(self, ticker_list: List[str]):
        new_subscriptions = set(ticker_list)
        
        if new_subscriptions == self.current_subscriptions:
            logger.debug("No subscription changes needed")
            return
        
        if not self.websocket:
            logger.warning("No WebSocket connection for subscription update")
            return
        
        try:
            await self.send_message(json.dumps(subscription_data()))
            
            added = new_subscriptions - self.current_subscriptions
            removed = self.current_subscriptions - new_subscriptions
            self.current_subscriptions = new_subscriptions
            
            if added:
                logger.info(f"Added subscriptions: {sorted(added)}")
            if removed:
                logger.info(f"Removed subscriptions: {sorted(removed)}")
            
            logger.info(f"Updated subscriptions for {len(ticker_list)} pairs")
            
        except Exception as e:
            logger.error(f"Failed to update subscriptions: {e}")

    async def run(self, initial_message: Optional[Dict[str, Any]] = None):
        global tickers
        self.running = True
        ping_task = None
        prices_task = None
        backup_task = None
        ticker_refresh_task = None
        
        while self.running:
            try:
                if not self.websocket:
                    self.websocket = await self.connect()
                    if not self.websocket:
                        break
                    
                    if initial_message:
                        await self.send_message(json.dumps(initial_message))
                        if "subscriptions" in initial_message:
                            pairs = initial_message["subscriptions"][0].get("pairs", [])
                            self.current_subscriptions = set(pairs)
                        initial_message = None
                    else:
                        await self.send_message(json.dumps(subscription_data()))
                        logger.info("Re-Applying ticker subscriptions . . .")
                    
                    await self.flush_queue()
                    
                    if not ping_task or ping_task.done():
                        logger.info("Starting ping task . . .")
                        ping_task = asyncio.create_task(self.send_ping())
                    if not prices_task or prices_task.done():
                        logger.info("Starting price print task . . .")
                        prices_task = asyncio.create_task(post_prices())
                    if not backup_task or backup_task.done():
                        logger.info("Starting backup task . . .")
                        backup_task = asyncio.create_task(periodic_backup())
                    if not ticker_refresh_task or ticker_refresh_task.done():
                        logger.info("Starting ticker refresh task . . .")
                        ticker_refresh_task = asyncio.create_task(periodic_ticker_refresh(self))
                
                await self.receive_message()
                
            except (websockets.exceptions.ConnectionClosed, asyncio.CancelledError):
                logger.warning("Connection lost, attempting to reconnect...")
                for task in [ping_task, prices_task, backup_task, ticker_refresh_task]:
                    if task and not task.done():
                        task.cancel()
                ping_task = prices_task = backup_task = ticker_refresh_task = None
                await self.close()
                continue
            except KeyboardInterrupt:
                logger.info("Shutting down...")
                self.running = False
                break
            except Exception as e:
                logger.error(f"Unexpected error in main loop: {e}")
                await asyncio.sleep(1)

        for task in [ping_task, prices_task, backup_task, ticker_refresh_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        await save_tickers_backup()
        await self.close()


class Ticker:
    def __init__(self, data: Dict[str, Any]):
        self.ohlc = {
            "open": 0,
            "high": 0,
            "low": 0,
            "close": 0,
            "depth": 0,
            "spread": 0,
            "ts": 0
        }
        self.minutes = []
        self.active = data["active"]
        self.decimal = data["decimal"]
        self.min_quote = data["minQuote"]
        self.min_base = data["minBase"]
        self.market = data["market"]
        self.limit = data["limit"]

    def details(self):
        data = {}
        data["price"] = self.ohlc["close"]
        data["rsi_short"] = self.rsi("SHORT")
        data["rsi_medium"] = self.rsi("MEDIUM")
        data["rsi_long"] = self.rsi("LONG")
        data["rsi_xlong"] = self.rsi("XLONG")
        return data

    def update(self, ts: int):
        cutoff_time = ts - (21 * 24 * 3600 * 1000 + 3600 * 1000)  # 21 days + 1 hour buffer
        while self.minutes and self.minutes[0]["ts"] < cutoff_time:
            self.minutes.pop(0)

    def _reset_ohlc(self):
        self.ohlc = {
            "open": 0,
            "high": 0,
            "low": 0,
            "close": 0,
            "depth": 0,
            "spread": 0,
            "ts": 0
        }

    def live_data(self, price: Optional[float] = None, depth: Optional[float] = None, 
                  spread: Optional[float] = None):
        """Update live market data"""
        if price is None:
            return
            
        ts = int(time.time() * 1000)
        current_minute = ts // 60000

        if self.ohlc["ts"] == 0:
            minute_start = current_minute * 60000
            self.ohlc = {
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "depth": depth or 0,
                "spread": spread or 0,
                "ts": minute_start
            }
        else:
            ohlc_minute = self.ohlc["ts"] // 60000
            if current_minute > ohlc_minute:
                if self.ohlc["ts"] > 0:
                    self.minutes.append(self.ohlc.copy())
                self._reset_ohlc()
                minute_start = current_minute * 60000
                self.ohlc = {
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "depth": depth or 0,
                    "spread": spread or 0,
                    "ts": minute_start
                }
                self.update(ts)
            else:
                self.ohlc["close"] = price
                if price > self.ohlc["high"]:
                    self.ohlc["high"] = price
                if price < self.ohlc["low"] or self.ohlc["low"] == 0:
                    self.ohlc["low"] = price
                if depth is not None:
                    self.ohlc["depth"] = (self.ohlc["depth"] * 3 + depth) / 4
                if spread is not None:
                    self.ohlc["spread"] = (self.ohlc["spread"] * 3 + spread) / 4

    def rsi(self, length: str):
        """
        Set length to: 
        "SHORT" - 60x 1 minute periods
        "MEDIUM" - 14x 1 hour periods
        "LONG" - 14x 6 hour periods
        "XLONG" - 21x 1 day periods
        """
        if length == "SHORT":
            ohlcList = self.minutes[-60:]
            period = 60
        elif length == "MEDIUM":
            minutes_for_hours = self.minutes[- (14 * 60):]
            ohlcList = aggregate_to(minutes_for_hours, 60)[-14:]
            period = 14
        elif length == "LONG":
            minutes_for_6h = self.minutes[- (14 * 6 * 60):]
            ohlcList = aggregate_to(minutes_for_6h, 360)[-14:]
            period = 14
        elif length == "XLONG":
            minutes_for_days = self.minutes[- (21 * 1440):]
            ohlcList = aggregate_to(minutes_for_days, 1440)[-21:]
            period = 21
        else:
            raise ValueError("Unknown length arg")
        
        if len(ohlcList) < min(10, period):
            return 50

        up = 0.0
        down = 0.0
        for key in range(len(ohlcList) - 1):
            change = ohlcList[key + 1]["close"] - ohlcList[key]["close"]
            if change > 0:
                up += change
            else:
                down += abs(change)
        count = len(ohlcList) - 1
        if count == 0:
            return 50
        up /= count
        down /= count
        if down == 0:
            return 100 if up > 0 else 50
        rs = up / down
        return round(100 - (100 / (1 + rs)), 1)

def aggregate(ohlcList: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not ohlcList:
        return None
    lows = [m["low"] for m in ohlcList if m["low"] > 0]
    min_low = min(lows) if lows else ohlcList[-1]["close"]
    return {
        "open": ohlcList[0]["open"],
        "high": max(m["high"] for m in ohlcList),
        "low": min_low,
        "close": ohlcList[-1]["close"],
        "depth": sum(m["depth"] for m in ohlcList) / len(ohlcList),
        "spread": sum(m["spread"] for m in ohlcList) / len(ohlcList),
        "ts": ohlcList[-1]["ts"]
    }

def aggregate_to(ohlcList: List[Dict[str, Any]], block_size: int) -> List[Dict[str, Any]]:
    aggregated = []
    for i in range(0, len(ohlcList), block_size):
        block = ohlcList[i:i + block_size]
        if block:
            agg = aggregate(block)
            if agg:
                aggregated.append(agg)
    return aggregated

def get_backup_filename():
    return "tickers_backup.pkl"

def get_metadata_filename():
    return "tickers_backup_metadata.json"

async def save_tickers_backup():
    try:
        backup_file = get_backup_filename()
        metadata_file = get_metadata_filename()
        temp_backup_file = backup_file + ".tmp"
        temp_metadata_file = metadata_file + ".tmp"
        
        metadata = {
            "timestamp": int(time.time()),
            "datetime": datetime.now().isoformat(),
            "ticker_count": sum(len(currency_tickers) for currency_tickers in tickers.values()),
            "currencies": list(tickers.keys())
        }
        
        with open(temp_backup_file, 'wb') as f:
            pickle.dump(tickers, f, protocol=pickle.HIGHEST_PROTOCOL)
        
        with open(temp_metadata_file, 'w') as f:
            json.dump(metadata, f, indent=2)
        
        if os.path.exists(backup_file):
            os.replace(temp_backup_file, backup_file)
        else:
            os.rename(temp_backup_file, backup_file)
            
        if os.path.exists(metadata_file):
            os.replace(temp_metadata_file, metadata_file)
        else:
            os.rename(temp_metadata_file, metadata_file)
        
        logger.info(f"Backup saved successfully at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
    except Exception as e:
        logger.error(f"Failed to save backup: {e}")
        for temp_file in [temp_backup_file, temp_metadata_file]:
            try:
                if os.path.exists(temp_file):
                    os.remove(temp_file)
            except:
                pass

def load_tickers_backup():
    global tickers
    
    backup_file = get_backup_filename()
    metadata_file = get_metadata_filename()
    
    if not os.path.exists(backup_file):
        logger.info("No backup file found, starting fresh")
        return False
    
    try:
        backup_info = {}
        if os.path.exists(metadata_file):
            with open(metadata_file, 'r') as f:
                backup_info = json.load(f)
        
        with open(backup_file, 'rb') as f:
            loaded_tickers = pickle.load(f)
        
        if not isinstance(loaded_tickers, dict):
            raise ValueError("Backup data is not a dictionary")
        
        expected_currencies = ["ZAR", "USDC", "USDT"]
        for currency in expected_currencies:
            if currency not in loaded_tickers:
                logger.warning(f"Currency {currency} not found in backup")
                loaded_tickers[currency] = {}
        
        tickers = loaded_tickers
        
        backup_time = backup_info.get('datetime', 'unknown')
        ticker_count = backup_info.get('ticker_count', 'unknown')
        
        logger.info(f"Backup loaded successfully from {backup_time}")
        logger.info(f"Loaded {ticker_count} tickers")
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to load backup: {e}")
        logger.info("Continuing with fresh initialization")
        return False

async def periodic_backup():
    while True:
        try:
            await asyncio.sleep(3600)
            await save_tickers_backup()
        except asyncio.CancelledError:
            logger.info("Backup task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in periodic backup: {e}")

async def periodic_ticker_refresh(websocket_client: WebSocketClient):
    while True:
        try:
            await asyncio.sleep(900)
            logger.info("Refreshing ticker list from API...")
            
            success = await refresh_tickers_from_api(websocket_client)
            if success:
                logger.info("Ticker list refresh completed successfully")
            else:
                logger.warning("Ticker list refresh failed")
                
        except asyncio.CancelledError:
            logger.info("Ticker refresh task cancelled")
            break
        except Exception as e:
            logger.error(f"Error in periodic ticker refresh: {e}")

async def refresh_tickers_from_api(websocket_client: WebSocketClient):
    global tickers
    
    try:
        temp_tickers = {"ZAR": {}, "USDC": {}, "USDT": {}}
        success = init_tickers(temp_tickers)
        
        if not success:
            return False
        
        changes_made = False
        added_tickers = []
        removed_tickers = []
        
        for quote_currency in ["ZAR", "USDC", "USDT"]:
            current_bases = set(tickers[quote_currency].keys())
            new_bases = set(temp_tickers[quote_currency].keys())
            
            to_add = new_bases - current_bases
            for base_currency in to_add:
                ticker_data = temp_tickers[quote_currency][base_currency]
                tickers[quote_currency][base_currency] = Ticker(ticker_data)
                added_tickers.append(f"{base_currency}{quote_currency}")
                changes_made = True
            
            to_remove = current_bases - new_bases
            for base_currency in to_remove:
                del tickers[quote_currency][base_currency]
                removed_tickers.append(f"{base_currency}{quote_currency}")
                changes_made = True
            
            for base_currency in current_bases.intersection(new_bases):
                existing_ticker = tickers[quote_currency][base_currency]
                new_config = temp_tickers[quote_currency][base_currency]
                
                if (existing_ticker.active != new_config["active"] or
                    existing_ticker.market != new_config["market"] or
                    existing_ticker.limit != new_config["limit"]):
                    
                    existing_ticker.active = new_config["active"]
                    existing_ticker.decimal = new_config["decimal"]
                    existing_ticker.min_quote = new_config["minQuote"]
                    existing_ticker.min_base = new_config["minBase"]
                    existing_ticker.market = new_config["market"]
                    existing_ticker.limit = new_config["limit"]
                    changes_made = True
        
        if added_tickers:
            logger.info(f"Added {len(added_tickers)} new tickers: {sorted(added_tickers)}")
        if removed_tickers:
            logger.info(f"Removed {len(removed_tickers)} delisted tickers: {sorted(removed_tickers)}")
        if not changes_made:
            logger.info("No ticker changes detected")
        
        if changes_made:
            ticker_list = []
            for quote_currency in tickers:
                for base_currency in tickers[quote_currency]:
                    ticker_list.append(f"{base_currency}{quote_currency}")
            
            await websocket_client.update_subscriptions(ticker_list)
            logger.info(f"Updated WebSocket subscriptions for {len(ticker_list)} pairs")
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to refresh tickers from API: {e}")
        return False

def subscription_data():
    global tickers
    ticker_list = []
    for quote_currency in tickers:
        for base_currency in tickers[quote_currency]:
            ticker_list.appen(f"{base_currency}{quote_currency}")
    return {
            "type": "SUBSCRIBE",
            "subscriptions": [
                {
                    "event": "OB_L1_D10_SNAPSHOT",
                    "pairs": ticker_list
                }
            ]
        }

def init_tickers(tickers: Dict[str, Dict]) -> bool:
    try:
        url = "https://api.valr.com/v1/public/pairs"
        result = requests.get(url, timeout=10)
        result.raise_for_status()
        pairs_data = result.json()
        
        for entry in pairs_data:
            if entry["currencyPairType"] != "SPOT":
                continue
            
            ticker_data = {
                "active": bool(entry["active"]),
                "decimal": entry["baseDecimalPlaces"],
                "minQuote": entry["minQuoteAmount"],
                "minBase": entry["minBaseAmount"],
                "market": False,
                "limit": False
            }
            
            quote_currency = entry["quoteCurrency"]
            base_currency = entry["baseCurrency"]
            
            if quote_currency in tickers:
                tickers[quote_currency][base_currency] = ticker_data
        
        url = "https://api.valr.com/v1/public/ordertypes"
        result = requests.get(url, timeout=10)
        result.raise_for_status()
        order_types_data = result.json()
        
        for entry in order_types_data:
            currency_pair = entry["currencyPair"]
            order_types = entry["orderTypes"]
            
            for quote_currency in tickers:
                for base_currency in tickers[quote_currency]:
                    if f"{base_currency}{quote_currency}" == currency_pair:
                        tickers[quote_currency][base_currency]["limit"] = "LIMIT" in order_types
                        tickers[quote_currency][base_currency]["market"] = "MARKET" in order_types
                        break
        
        total_tickers = sum(len(v) for v in tickers.values())
        logger.info(f"Initialized {total_tickers} tickers from API")
        return True
        
    except Exception as e:
        logger.error(f"Failed to initialize tickers: {e}")
        return False

def process_message(message: Dict[str, Any]):
    global tickers
    
    try:
        if message["type"] != "OB_L1_D10_SNAPSHOT":
            return
        
        pair_symbol = message["ps"]
        data = message["d"]
        
        if not data.get("a") or not data.get("b"):
            return
        
        ask_price, ask_volume = float(data["a"][0][0]), float(data["a"][0][1])
        bid_price, bid_volume = float(data["b"][0][0]), float(data["b"][0][1])
        
        total_volume = ask_volume + bid_volume
        if total_volume == 0:
            return
        
        price = (ask_price * ask_volume + bid_price * bid_volume) / total_volume
        spread = abs(ask_price - bid_price) / price if price > 0 else 0
        
        depth = (sum(float(entry[1]) for entry in data["a"][:5]) + 
                 sum(float(entry[1]) for entry in data["b"][:5]))
        
        for quote_currency in ["ZAR", "USDC", "USDT"]:
            if pair_symbol.endswith(quote_currency):
                base_currency = pair_symbol[:-len(quote_currency)]
                if base_currency in tickers[quote_currency]:
                    tickers[quote_currency][base_currency].live_data(
                        price=price, depth=depth, spread=spread
                    )
                break
                
    except (KeyError, ValueError, IndexError) as e:
        logger.error(f"Error processing message: {e}")
        logger.debug(f"Message content: {json.dumps(message, indent=2)}")
    except Exception as e:
        logger.error(f"Unexpected error in process_message: {e}")

async def post_prices():
    global tickers
    while True:
        try:
            dataList = []
            for quote_currency, currency_tickers in tickers.items():
                for base_currency, ticker_data in currency_tickers.items():
                    if ticker_data.market and ticker_data.limit:
                        priceData = ticker_data.details()
                        dataList.append({
                            "ticker": f"{base_currency}{quote_currency}",
                            "price": priceData["price"],
                            "rsi_short": priceData["rsi_short"],
                            "rsi_medium": priceData["rsi_medium"],
                            "rsi_long": priceData["rsi_long"],
                            "rsi_xlong": priceData["rsi_xlong"],
                            "market": ticker_data.market,
                            "limit": ticker_data.limit,
                            "ts": int(time.time() * 1000)
                        })

            with open('prices.json', 'w') as f:
                json.dump(dataList, f, indent=4)
            
            await asyncio.sleep(20)
            
        except Exception as e:
            logger.error(f"Error in post_prices: {e}")
            break

tickers = {
    "ZAR": {},
    "USDC": {},
    "USDT": {}
}

async def main():
    """Main entry point"""
    try:
        backup_loaded = load_tickers_backup()
        
        if not backup_loaded:
            init_tickers(tickers)
        else:
            logger.info("Refreshing ticker configuration from API...")
            temp_tickers = {"ZAR": {}, "USDC": {}, "USDT": {}}
            init_tickers(temp_tickers)
            
            for quote_currency in temp_tickers:
                for base_currency, new_config in temp_tickers[quote_currency].items():
                    if base_currency in tickers[quote_currency]:
                        existing_ticker = tickers[quote_currency][base_currency]
                        if hasattr(existing_ticker, 'active'):
                            existing_ticker.active = new_config["active"]
                            existing_ticker.decimal = new_config["decimal"]
                            existing_ticker.min_quote = new_config["minQuote"]
                            existing_ticker.min_base = new_config["minBase"]
                            existing_ticker.market = new_config["market"]
                            existing_ticker.limit = new_config["limit"]
                    else:
                        tickers[quote_currency][base_currency] = Ticker(new_config)
        
        ticker_list = []
        for quote_currency in tickers:
            for base_currency, ticker_data in tickers[quote_currency].items():
                if not isinstance(ticker_data, Ticker):
                    tickers[quote_currency][base_currency] = Ticker(ticker_data)
                ticker_list.append(f"{base_currency}{quote_currency}")
        
        if not ticker_list:
            logger.error("No tickers found. Check your .env configuration.")
            return
        
        initial_message = subscription_data()
        
        logger.info(f"Subscribing to {len(ticker_list)} pairs")
        
        client = WebSocketClient(uri="wss://api.valr.com/ws/trade")
        await client.run(initial_message)
        
    except Exception as e:
        logger.error(f"Error in main: {e}")

if __name__ == "__main__":
    asyncio.run(main())