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
                path = self.uri[self.uri.find("/ws") :]
                sign = self.get_signature(secret=secret, ts=ts, verb="GET", path=path)

                headers = {
                    "X-VALR-API-KEY": key,
                    "X-VALR-SIGNATURE": sign,
                    "X-VALR-TIMESTAMP": str(ts),
                }

                self.websocket = await asyncio.wait_for(
                    websockets.connect(
                        self.uri, additional_headers=headers, ping_interval=30
                    ),
                    timeout=10,
                )
                logger.info("WebSocket connection established")
                return self.websocket

            except (
                websockets.exceptions.WebSocketException,
                asyncio.TimeoutError,
            ) as e:
                delay = min(60, self.backoff_factor**attempt)  # Cap max delay
                logger.warning(
                    f"Connection attempt {attempt + 1} failed: {e}. Retrying in {delay}s..."
                )
                await asyncio.sleep(delay)

        logger.error("Max reconnection attempts reached")
        return None

    def get_signature(
        self, secret: str, ts: int, verb: str, path: str, body: str = ""
    ) -> str:
        payload = f"{ts}{verb.upper()}{path}{body}"
        message = payload.encode("utf-8")
        signature = hmac.new(
            secret.encode("utf-8"), message, digestmod=hashlib.sha512
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
                    if not ticker_refresh_task or ticker_refresh_task.done():
                        logger.info("Starting ticker refresh task . . .")
                        ticker_refresh_task = asyncio.create_task(
                            periodic_ticker_refresh(self)
                        )

                await self.receive_message()

            except (websockets.exceptions.ConnectionClosed, asyncio.CancelledError):
                logger.warning("Connection lost, attempting to reconnect...")
                for task in [ping_task, prices_task, ticker_refresh_task]:
                    if task and not task.done():
                        task.cancel()
                ping_task = prices_task = ticker_refresh_task = None
                await self.close()
                continue
            except KeyboardInterrupt:
                logger.info("Shutting down...")
                self.running = False
                break
            except Exception as e:
                logger.error(f"Unexpected error in main loop: {e}")
                await asyncio.sleep(1)

        for task in [ping_task, prices_task, ticker_refresh_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

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
            "step": 0,
            "volume": 0,
            "ts": 0,
        }
        self.minutes = []
        self.active = bool(data["active"])
        self.decimal = int(data["decimal"])
        self.min_quote = float(data["minQuote"])
        self.min_base = float(data["minBase"])
        self.market = bool(data["market"])
        self.limit = bool(data["limit"])

    def details(self):
        data = {}
        minute_list = self.minutes.copy()
        minute_list.append(self.ohlc)
        data["price"] = self.ohlc["close"]
        data["depth"] = sum(entry["depth"] for entry in minute_list) / len(minute_list)
        data["spread"] = sum(entry["spread"] for entry in minute_list) / len(
            minute_list
        )
        data["step"] = sum(entry["step"] for entry in minute_list) / len(minute_list)
        data["volume"] = self.ohlc["volume"]
        data["active"] = self.active
        data["decimal"] = self.decimal
        data["min_value"] = (
            self.min_quote
            if self.min_quote > (self.min_base * self.ohlc["close"])
            else (self.min_base * self.ohlc["close"])
        )
        data["market"] = self.market
        data["limit"] = self.limit
        return data

    def prune(self, ts: int):
        cutoff_time = ts - 3600  # 1 hour frame
        while self.minutes and self.minutes[0]["ts"] < cutoff_time:
            self.minutes.pop(0)

    def _reset_ohlc(self):
        self.ohlc = {
            "open": self.minutes[-1]["close"] if self.minutes else 0,
            "high": self.minutes[-1]["close"] if self.minutes else 0,
            "low": self.minutes[-1]["close"] if self.minutes else 0,
            "close": self.minutes[-1]["close"] if self.minutes else 0,
            "depth": 0,
            "spread": 0,
            "step": 0,
            "volume": 0,
            "ts": 0,
        }

    def live_data(
        self,
        price: Optional[float] = None,
        depth: Optional[float] = None,
        spread: Optional[float] = None,
        step: Optional[float] = None,
        volume: Optional[float] = None,
    ):
        """Update live market data"""

        ts = int(time.time())
        last_minute = ts // 60 * 60

        if self.ohlc["ts"] == 0:
            self.ohlc = {
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "depth": depth or 0,
                "spread": spread or 0,
                "volume": volume or 0,
                "step": step or 0,
                "ts": last_minute,
            }
        else:
            if last_minute != self.ohlc["ts"]:
                self.minutes.append(self.ohlc.copy())
                self._reset_ohlc()
                self.ohlc["ts"] = last_minute

            self.ohlc["close"] = price
            if price > self.ohlc["high"]:
                self.ohlc["high"] = price
            if price < self.ohlc["low"] or self.ohlc["low"] == 0:
                self.ohlc["low"] = price
            if depth is not None:
                self.ohlc["depth"] = (
                    ((self.ohlc["depth"] * 3) + depth) / 4
                    if self.ohlc["depth"] != 0
                    else depth
                )
            if spread is not None:
                self.ohlc["spread"] = (
                    ((self.ohlc["spread"] * 3) + spread) / 4
                    if self.ohlc["spread"] != 0
                    else spread
                )
            if step is not None:
                self.ohlc["step"] = (
                    ((self.ohlc["step"] * 3) + step) / 4
                    if self.ohlc["step"] != 0
                    else step
                )
            if volume is not None:
                self.ohlc["volume"] += volume


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
        "step": sum(m["step"] for m in ohlcList) / len(ohlcList),
        "volume": sum(m["volume"] for m in ohlcList),
        "ts": ohlcList[-1]["ts"],
    }


def save_hour_aggregate():
    global tickers
    try:
        history_file = "history.json"
        metadata_file = "history_metadata.json"
        temp_history = history_file + ".tmp"
        temp_metadata = metadata_file + ".tmp"
        ts = int(time.time())
        hour_start = (ts // 3600) * 3600

        old_metadata = {}
        if os.path.exists(metadata_file):
            with open(metadata_file, "r") as f:
                old_metadata = json.load(f)

        if old_metadata.get("timestamp") == hour_start:
            return  # Already saved previous hour
        logger.info(
            f"Hourly save check: current {hour_start}({ts}), last saved {old_metadata.get('timestamp', 'none')}"
        )

        json_data = {}
        if os.path.exists(history_file):
            with open(history_file, "r") as f:
                json_data = json.load(f)

        for quote in tickers:
            if quote not in json_data:
                json_data[quote] = {}
            for base in tickers[quote]:
                if base not in json_data[quote]:
                    json_data[quote][base] = []
                ticker = tickers[quote][base]
                ticker.prune(ts)
                minute_list = ticker.minutes.copy()
                minute_list.append(ticker.ohlc.copy())
                hour_ohlc = aggregate(minute_list)
                hour_ohlc["ts"] = hour_start
                hour_ohlc["symbol"] = base + quote
                if not json_data[quote][base] and hour_ohlc["close"] == 0:
                    continue

                if hour_ohlc["close"] == 0:
                    hour_ohlc["open"] = json_data[quote][base][-1]["close"]
                    hour_ohlc["close"] = json_data[quote][base][-1]["close"]
                    hour_ohlc["high"] = json_data[quote][base][-1]["close"]
                    hour_ohlc["low"] = json_data[quote][base][-1]["close"]
                    hour_ohlc["depth"] = json_data[quote][base][-1]["depth"]
                    hour_ohlc["spread"] = json_data[quote][base][-1]["spread"]
                    hour_ohlc["step"] = json_data[quote][base][-1]["step"]
                    hour_ohlc["volume"] = json_data[quote][base][-1]["volume"]

                json_data[quote][base].append(hour_ohlc)

        with open(temp_history, "w") as f:
            json.dump(json_data, f, indent=4)

        metadata = {
            "timestamp": hour_start,
            "datetime": datetime.fromtimestamp(ts).isoformat(),
            "ticker_count": sum(len(v) for v in tickers.values()),
            "currencies": list(tickers.keys()),
        }
        with open(temp_metadata, "w") as f:
            json.dump(metadata, f, indent=4)

        os.replace(temp_history, history_file) if os.path.exists(
            history_file
        ) else os.rename(temp_history, history_file)
        os.replace(temp_metadata, metadata_file) if os.path.exists(
            metadata_file
        ) else os.rename(temp_metadata, metadata_file)
        logger.info(
            f"Hourly aggregate saved at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    except Exception as e:
        logger.error(f"Failed to save hourly aggregate: {e}")
        for temp in [temp_history, temp_metadata]:
            if os.path.exists(temp):
                os.remove(temp)


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

                if (
                    existing_ticker.active != new_config["active"]
                    or existing_ticker.market != new_config["market"]
                    or existing_ticker.limit != new_config["limit"]
                ):
                    existing_ticker.active = new_config["active"]
                    existing_ticker.decimal = new_config["decimal"]
                    existing_ticker.min_quote = new_config["minQuote"]
                    existing_ticker.min_base = new_config["minBase"]
                    existing_ticker.market = new_config["market"]
                    existing_ticker.limit = new_config["limit"]
                    changes_made = True

        if added_tickers:
            logger.info(
                f"Added {len(added_tickers)} new tickers: {sorted(added_tickers)}"
            )
        if removed_tickers:
            logger.info(
                f"Removed {len(removed_tickers)} delisted tickers: {sorted(removed_tickers)}"
            )
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
            ticker_list.append(f"{base_currency}{quote_currency}")
    return {
        "type": "SUBSCRIBE",
        "subscriptions": [
            {"event": "OB_L1_D10_SNAPSHOT", "pairs": ticker_list},
            {"event": "NEW_TRADE", "pairs": ticker_list},
        ],
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
                "limit": False,
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
                        tickers[quote_currency][base_currency]["limit"] = (
                            "LIMIT" in order_types
                        )
                        tickers[quote_currency][base_currency]["market"] = (
                            "MARKET" in order_types
                        )
                        break

        total_tickers = sum(len(v) for v in tickers.values())
        logger.info(f"Initialized {total_tickers} tickers from API")
        return True

    except Exception as e:
        logger.error(f"Failed to initialize tickers: {e}")
        return False


def snapshotProcess(data):
    if not data.get("a") or not data.get("b"):
        return

    ask_price, ask_volume = float(data["a"][0][0]), float(data["a"][0][1])
    bid_price, bid_volume = float(data["b"][0][0]), float(data["b"][0][1])

    total_volume = ask_volume + bid_volume
    if total_volume == 0:
        return

    price = (ask_price * bid_volume + bid_price * ask_volume) / total_volume
    spread = abs(ask_price - bid_price) / price if price > 0 else 0

    depth = 0
    for i in range(
        min(len(data["a"]), len(data["b"]))
    ):  # Finding total depth within 5% of price
        if abs(float(data["a"][i][0]) - price) / price <= 0.05:
            depth += float(data["a"][i][1])
        if abs(float(data["b"][i][0]) - price) / price <= 0.05:
            depth += float(data["b"][i][1])

    return {"price": price, "spread": spread, "depth": depth}


def process_message(message: Dict[str, Any]):
    global tickers

    try:
        if not message or not isinstance(message, dict):
            return
        if message["type"] == "OB_L1_D10_SNAPSHOT":
            pair_symbol = message["ps"]
            data = message["d"]
            result = snapshotProcess(data)
            if not result:
                return

            for quote_currency in ["ZAR", "USDC", "USDT"]:
                if pair_symbol.endswith(quote_currency):
                    base_currency = pair_symbol[: -len(quote_currency)]
                    if base_currency in tickers[quote_currency]:
                        tickers[quote_currency][base_currency].live_data(
                            price=result["price"],
                            depth=result["depth"],
                            spread=result["spread"],
                        )
                    break
        elif message["type"] == "NEW_TRADE":
            pair_symbol = message["currencyPairSymbol"]
            data = message["data"]
            volume = float(data["quantity"])

            for quote_currency in ["ZAR", "USDC", "USDT"]:
                if pair_symbol.endswith(quote_currency):
                    base_currency = pair_symbol[: -len(quote_currency)]
                    if base_currency in tickers[quote_currency]:
                        tickers[quote_currency][base_currency].live_data(volume=volume)
                    break

    except (KeyError, ValueError, IndexError) as e:
        logger.error(f"Error processing message: {e}")
        logger.debug(f"Message content: {json.dumps(message, indent=4)}")
    except Exception as e:
        logger.error(f"Unexpected error in process_message: {e}")
        logger.error(f"Message content: {json.dumps(message, indent=4)}")


async def post_prices():
    global tickers
    while True:
        save_hour_aggregate()
        try:
            dataList = []
            for quote_currency, currency_tickers in tickers.items():
                for base_currency, ticker_data in currency_tickers.items():
                    ticker_data.prune(int(time.time()))
                    if ticker_data.market or ticker_data.limit:
                        data = ticker_data.details()
                        data["ticker"] = "".join([base_currency, quote_currency])
                        dataList.append(data)

            with open("prices.json", "w") as f:
                json.dump(dataList, f, indent=4)

            await asyncio.sleep(20)

        except Exception as e:
            logger.error(f"Error in post_prices: {e}")
            break


tickers = {"ZAR": {}, "USDC": {}, "USDT": {}}


def load_history_init():
    global tickers
    history_file = "history.json"
    if not os.path.exists(history_file):
        logger.info("No history file found, starting with empty OHLC")
        return False
    try:
        with open(history_file, "r") as f:
            history = json.load(f)
        changes = 0
        for quote_currency in history:
            if quote_currency not in tickers:
                continue
            for base_currency in history[quote_currency]:
                if base_currency not in tickers[quote_currency]:
                    continue
                ticker = tickers[quote_currency][base_currency]
                bars = history[quote_currency][base_currency]
                if not bars:
                    continue
                last_bar = bars[-1]
                # Initialize current OHLC with last close as open/high/low/close for continuity
                ticker.ohlc = {
                    "open": last_bar["close"],
                    "high": last_bar["close"],
                    "low": last_bar["close"],
                    "close": last_bar["close"],
                    "depth": 0,  # Reset non-price fields
                    "spread": 0,
                    "step": 0,
                    "volume": 0,
                    "ts": int(time.time() // 60) * 60,  # Current minute start
                }
                changes += 1
        logger.info(f"Initialized OHLC open values from history for {changes} tickers")
        return True
    except Exception as e:
        logger.error(f"Failed to load history for init: {e}")
        return False


async def main():
    """Main entry point"""
    try:
        init_tickers(tickers)
        load_history_init()  # Always init config, then set OHLC from history if available
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
