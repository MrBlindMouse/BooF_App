import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from requests_ratelimiter import LimiterSession
import hashlib, hmac
from dotenv import dotenv_values
import ast
import schedule, threading
import datetime, json, pickle, os, sys, traceback, time, datetime, math

import db, postmark, twitter, random

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from statistics import mean

"""
Initialise Config and define createSession first.
"""


class sessionRetry(LimiterSession):
    def request(self, method, url, **kwargs):
        response = super().request(method, url, **kwargs)
        if response.history:
            for attempt in response.history:
                printLog(
                    f"Retry triggered for {method} {url}: Status {attempt.status_code}",
                    True,
                )
        return response


def createSession(rate):
    """
    Rate per minute: public = 10, private = 360
    """
    session = sessionRetry(per_minute=rate, burst=5)
    retry_strategy = Retry(total=3, backoff_factor=1)
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    return session


def trunc(value, dec):
    if isinstance(value, str):
        parts = value.split(".")
        if len(parts) != 2:
            raise ValueError("Invalid value format")
        intPart, decPart = parts
        if dec > 0:
            decPart = decPart[:dec]
            return ".".join([intPart, decPart])
        else:
            return intPart
    elif isinstance(value, float):
        a = int(value * (10**dec))
        return float(a / (10**dec))
    else:
        raise TypeError("Invalid type for value")


def sorting(values):
    """
    Sorting base on minTrade
    """
    return values["minTrade"]


class Config:
    """
    updateEnv() and checkVALR to be run every loop,
    updateTickers() to be run daily,
    updatevolatility() to be run weekly
    """

    STATE_FILE = "config_state.pkl"

    def __init__(self):
        self.valrStatus = "down"
        self.appSecret = None
        self.verifySalt = None
        self.ZAR = []
        self.USDC = []
        self.USDT = []
        self.USDCZAR = 0
        self.USDTZAR = 0
        self.forbidden = []
        self.stake = []
        self.postmarkKey = ""
        self.botTimer = 1
        self.minSpread = 0.03

    def updateEnv(self):
        """
        Updates config form the .env
        """
        config = dotenv_values(".env")
        self.appSecret = config["APP_SECRET"]
        self.forbidden = ast.literal_eval(config["FORBIDDEN"])
        self.stake = ast.literal_eval(config["STAKE"])
        self.postmarkKey = config["POSTMARK_KEY"]
        self.paypalKey = config["PAYPAL_ID"]
        self.paypalSecret = config["PAYPAL_SECRET"]
        self.verifySalt = config["VERIFY_SALT"]

    def updateTickers(self, lock):
        """
        Updates the ticker list, including ticker trend, per quote currency.
        Checks downturn protection rules.
        Ticker format={
            "base": {baseCurrency},
            "price": {markPrice},
            "decimal": {baseDesimalPlaces},
            "tick": {tickSize},
            "minTrade": {minValue},
            "trend": {trend},
            "rsi": {rsi}
            "volatility": {beta},
            "atr": {atr%},
            "liquidity": {depth/volume}
        }
        """
        printLog("Updating tickers . . .", True)
        quote_lists = {"ZAR": [], "USDC": [], "USDT": []}
        try:
            price_list = []
            with open("prices.json", "r") as f:
                price_list = json.load(f)
            for ticker in price_list:
                if ticker["active"] and ticker["market"]:
                    ticker_details = {
                        "base": "",
                        "price": float(ticker["price"]),
                        "decimal": int(ticker["decimal"]),
                        "tick":ticker["tick"],
                        "minTrade": float(ticker["min_value"]),
                        "trend": 1,
                        "rsi": 50,
                        "volatility": 1,
                        "atr": 0,
                        "bars": [],
                        "liquidity":0,
                    }
                    for quote in ["ZAR", "USDC", "USDT"]:
                        if ticker["ticker"].endswith(quote):
                            ticker_details["base"] = ticker["ticker"][: -len(quote)]
                            if any(
                                word in ticker_details["base"]
                                for word in self.forbidden
                            ):
                                continue
                            indicator_data = findIndicators(ticker["ticker"])
                            if (
                                indicator_data["long_spread"] > self.minSpread
                                or indicator_data["short_spread"]
                                > (self.minSpread + 0.01)
                            ):
                                continue
                            ticker_details["trend"] = indicator_data["trend"]
                            ticker_details["rsi"] = indicator_data["rsi"]
                            ticker_details["atr"] = indicator_data["atr"]
                            ticker_details["bars"] = indicator_data["bars"]
                            ticker_details["liquidity"] = indicator_data["liquidity_ratio"]
                            quote_lists[quote].append(ticker_details)
            for quote in ["ZAR", "USDC", "USDT"]:
                ticker_list = quote_lists[quote]
                market_returns = findMarketReturns(ticker_list)
                for ticker in ticker_list:
                    if market_returns:
                        ticker["volatility"] = (
                            beta(ticker["bars"], market_returns)
                            if len(ticker["bars"]) > (24*7) #3days
                            else 1
                        )
                    else:
                        ticker["volatility"] = 1
                    ticker.pop("bars")

            with lock:
                printLog("Locking config, updating . . .", True)
                self.ZAR.clear()
                self.ZAR.extend(quote_lists["ZAR"])
                self.USDC.clear()
                self.USDC.extend(quote_lists["USDC"])
                self.USDT.clear()
                self.USDT.extend(quote_lists["USDT"])
                self.saveState()

        except Exception as e:
            printLog(e, True)
            logPost(f"During Update Tickers:{e}", "2")

    def updatePrice(self):
        price_list = []
        with open("prices.json", "r") as f:
            price_list = json.load(f)

        quote_list = ["ZAR", "USDC", "USDT"]
        for quote in quote_list:
            ticker_list = getattr(self, quote)
            for ticker in ticker_list:
                for entry in price_list:
                    if entry["ticker"] == f"{ticker['base']}{quote}":
                        ticker["price"] = float(entry["price"])
                        break

        for entry in price_list:
            if entry["ticker"] == "USDCZAR":
                self.USDCZAR = float(entry["price"])
            if entry["ticker"] == "USDTZAR":
                self.USDTZAR = float(entry["price"])

    def checkVALR(self, session):
        url = "https://api.valr.com/v1/public/status"
        try:
            result = session.get(url).json()
            if result["status"] == "online":
                self.valrStatus = "online"
            else:
                self.valrStatus = "suspended"
        except Exception as e:
            self.valrStatus = "suspended"
            logPost(f"VALR server status: {e}", "2")

    def saveState(self):
        with open(self.STATE_FILE, "wb") as file:
            pickle.dump(
                {
                    "valrStatus": self.valrStatus,
                    "appSecret": self.appSecret,
                    "verifySalt": self.verifySalt,
                    "ZAR": self.ZAR,
                    "USDC": self.USDC,
                    "USDT": self.USDT,
                    "USDCZAR": self.USDCZAR,
                    "USDTZAR": self.USDTZAR,
                    "forbidden": self.forbidden,
                    "stake": self.stake,
                    "postmarkKey": self.postmarkKey,
                    "paypalKey": self.paypalKey,
                    "paypalSecret": self.paypalSecret,
                    "botTimer": self.botTimer,
                },
                file,
            )

    def loadState(self):
        if os.path.exists(self.STATE_FILE):
            with open(self.STATE_FILE, "rb") as file:
                state = pickle.load(file)
                self.valrStatus = state["valrStatus"]
                self.appSecret = state["appSecret"]
                self.verifySalt = state["verifySalt"]
                self.ZAR = state["ZAR"]
                self.USDC = state["USDC"]
                self.USDT = state["USDT"]
                self.USDCZAR = float(state["USDCZAR"])
                self.USDTZAR = float(state["USDTZAR"])
                self.forbidden = state["forbidden"]
                self.stake = state["stake"]
                self.postmarkKey = state["postmarkKey"]
                self.paypalKey = state["paypalKey"]
                self.paypalSecret = state["paypalSecret"]
                self.botTimer = float(state["botTimer"])
        else:
            printLog("State file not found", True)
            raise


def validateKeys(key, secret, user_id):
    ts = int(time.time()) * 1000

    verb = "GET"
    path = "/v1/account/api-keys/current"
    body = ""
    url = f"https://api.valr.com{path}{body}"
    sign = getSign(secret, ts, verb, path, body)
    payload = {}
    headers = {
        "X-VALR-API-KEY": key,
        "X-VALR-SIGNATURE": str(sign),
        "X-VALR-TIMESTAMP": str(ts),
    }
    response = externalSession.get(url, headers=headers, data=payload)

    if response.status_code != 200:
        message = db.Message([0, user_id, "ERROR", "Invalid API Key and Secret"])
        message.post()
        return False
    jsonResponse = response.json()
    if (
        "View access" not in jsonResponse["permissions"]
        or "Trade" not in jsonResponse["permissions"]
    ):
        message = db.Message(
            [0, user_id, "ERROR", "API Key does not allow View and Trade access"]
        )
        message.post()
        return False
    if not jsonResponse["isSubAccount"]:
        message = db.Message(
            [0, user_id, "WARNING", "Security Risk: API Key for main account used."]
        )
        message.post()
    return True


def updateCurrency(newCurrency, bot=db.Bot):
    config = Config()
    config.loadState()

    currencyList = []
    if bot.currency == "ZAR":
        currencyList = config.ZAR
    elif bot.currency == "USDC":
        currencyList = config.USDC
    elif bot.currency == "USDT":
        currencyList = config.USDT

    accounts = db.getActiveAccounts(bot_id=bot.id)
    for account in accounts:
        decimal = 0
        tick = 0
        for ticker in currencyList:
            if ticker["base"] == account.base:
                decimal = int(ticker["decimal"])
                break
        result = trade(
            direction = "SELL",
            quote = bot.currency,
            base = account.base,
            key = bot.key,
            secret = bot.secret,
            amount = account.volume,
            decimal = decimal,
        )
        if result:
            transaction = db.Transaction(
                [
                    0,
                    bot.id,
                    "SELL",
                    result["volume"],
                    result["value"],
                    account.base,
                    bot.currency,
                    int(time.time()),
                    result["fee"],
                ]
            )
            transaction.post()
        account.delete()
    bot.currency = newCurrency
    bot.balance_nr = 0
    bot.balance_value = 0
    bot.update()


def trade(direction, quote, base, key, secret, amount, decimal=2):
    """
    Buys with quote currency, amount = value
    Sells with base currency, amount = volume
    Default Decimal = 2 for Fiat currencies
    """
    try:
        payload = {}
        stringAmount = f"{float(trunc(amount, decimal)):.{decimal}f}"  # Scientific suppressed string from truncated amount
        if direction == "BUY":
            payload = {
                "side": "BUY",
                "quoteAmount": stringAmount,
                "pair": f"{base}{quote}",
            }
        elif direction == "SELL":
            payload = {
                "side": "SELL",
                "baseAmount": stringAmount,
                "pair": f"{base}{quote}",
            }
        ts = int(time.time() * 1000)
        verb = "POST"
        path = "/v2/orders/market"
        url = f"https://api.valr.com{path}"
        sign = getSign(secret, ts, verb, path, json.dumps(payload))
        headers = {
            "Content-Type": "application/json",
            "X-VALR-API-KEY": key,
            "X-VALR-SIGNATURE": sign,
            "X-VALR-TIMESTAMP": str(ts),
        }
        response = externalSession.post(url=url, headers=headers, json=payload)
        jsonResponse = response.json()
        if response.status_code != 201:
            raise ValueError(jsonResponse["message"])
        id = jsonResponse["id"]

        loop = 0
        while True:
            details = None
            try:
                loop += 1
                if loop > 5:  # If order cannot be filled in 5 secs, cancel
                    ts = int(time.time() * 1000)
                    verb = "DELETE"
                    path = "/v2/orders/order"
                    body = {"orderId": id, "pair": f"{base}{quote}"}
                    url = f"https://api.valr.com{path}"
                    sign = getSign(secret, ts, verb, path, json.dumps(body))
                    headers = {
                        "Content-Type": "application/json",
                        "X-VALR-API-KEY": key,
                        "X-VALR-SIGNATURE": str(sign),
                        "X-VALR-TIMESTAMP": str(ts),
                    }
                    response = externalSession.delete(url, headers=headers, json=body)
                    raise ValueError("Trade Cancelled")

                # Checking if filled
                ts = int(time.time() * 1000)
                verb = "GET"
                path = f"/v1/orders/{base}{quote}/orderid/{id}"
                body = ""
                url = f"https://api.valr.com{path}"
                sign = getSign(secret, ts, verb, path)
                headers = {
                    "X-VALR-API-KEY": key,
                    "X-VALR-SIGNATURE": str(sign),
                    "X-VALR-TIMESTAMP": str(ts),
                }
                response = externalSession.get(url, headers=headers)
                response.raise_for_status()
                jsonResponse = response.json()
                if jsonResponse["orderStatusType"] == "Filled":  # Finding details
                    ts = int(time.time() * 1000)
                    verb = "GET"
                    path = f"/v1/orders/history/summary/orderid/{id}"
                    body = ""
                    url = f"https://api.valr.com{path}"
                    sign = getSign(secret, ts, verb, path)
                    headers = {
                        "X-VALR-API-KEY": key,
                        "X-VALR-SIGNATURE": str(sign),
                        "X-VALR-TIMESTAMP": str(ts),
                    }
                    response = externalSession.get(url, headers=headers)
                    response.raise_for_status()
                    jsonResponse = response.json()
                    if direction == "BUY":
                        price = float(jsonResponse["total"]) / float(
                            jsonResponse["totalExecutedQuantity"]
                        )
                        details = {
                            "volume": float(jsonResponse["totalExecutedQuantity"]),
                            "value": float(jsonResponse["total"]),
                            "fee": float(jsonResponse["totalFee"]) * price,
                        }
                    elif direction == "SELL":
                        details = {
                            "volume": float(jsonResponse["totalExecutedQuantity"]),
                            "value": float(jsonResponse["total"]),
                            "fee": float(jsonResponse["totalFee"]),
                        }
                    return details
                time.sleep(1)
            except Exception as e:
                logPost(f"During order check: {base}{quote} {direction} ~ {e}", "2")
                return details
    except Exception as e:
        logPost(f"During Trade: {base}{quote} {direction} ~ {e}", "2")
        return None


def checkDiscontinued(config=Config, bot=db.Bot):
    accounts = db.getActiveAccounts(bot_id=bot.id)

    quote_list = {"ZAR": config.ZAR, "USDC": config.USDC, "USDT": config.USDT}
    ticker_list = quote_list[bot.currency]
    for account in accounts:
        found = False
        for ticker in ticker_list:
            if ticker["base"] == account.base:
                found = True
                break
        if not found:
            ticker = {}
            with open("prices.json", "r") as f:
                file = json.load(f)
                for entry in file:
                    if entry["ticker"] == f"{account.base}{bot.currency}":
                        ticker = entry
                        break
            message = db.Message(
                [0, bot.user_id, "WARNING", f"Delisting {account.base}!"]
            )
            message.post()
            sell_amount = account.volume
            if account.stake != 0:
                message = db.Message(
                    [
                        0,
                        bot.user_id,
                        "INFO",
                        f"{account.base} has a staked amount, please unstake and sell if desired.",
                    ]
                )
                message.post()
                sell_amount -= account.stake

            success = trade(
                "SELL",
                bot.currency,
                account.base,
                bot.key,
                bot.secret,
                sell_amount,
                ticker["decimal"],
            )
            if success is not None:
                bot.equity += trade["value"]
                bot.update()
            account.delete()

def get_daily_ratios(bars) -> float:
    if not bars:
        return 0.0
    day_groups = defaultdict(list)
    for bar in bars:
        day_start = bar["ts"] // 86400 * 86400
        day_groups[day_start].append(bar)
    sorted_days = sorted(day_groups, reverse=True)[:7]
    daily_ratios = []
    for day_ts in sorted_days:
        group = day_groups[day_ts]
        if group:
            depth_avg = sum(b["depth"] for b in group) / len(group)
            vol_sum = sum(b["volume"] for b in group)
            ratio = (depth_avg / vol_sum * 100) if vol_sum > 0 else 0
            daily_ratios.append(ratio)
    return sum(daily_ratios)/len(daily_ratios)

def findIndicators(pair):
    try:
        bars = []
        day = 24
        short_hours = 7 * day
        long_hours = 21 * day
        with open("history.json", "r") as f:
            history = json.load(f)
            for quote in ["ZAR", "USDC", "USDT"]:
                if pair.endswith(quote):
                    bars = history[quote][pair[: -len(quote)]]
                    break
        day_bars = bars[-day:]
        short_bars = bars[-short_hours:]
        long_bars = bars[-long_hours:]
        answer = {
            "trend": 1,
            "rsi": 50,
            "atr": 0,
            "liquidity_ratio": 0,
            "short_spread": mean(bar["spread"] for bar in short_bars),
            "long_spread": mean(bar["spread"] for bar in long_bars),
            "bars": bars,
        }
        # Depth Ratio
        answer["liquidity_ratio"] = get_daily_ratios(long_bars)

        if len(bars) < 28:
            return answer

        # Trend
        short_sma = mean([bar["close"] for bar in short_bars])
        long_sma = mean([bar["close"] for bar in long_bars])

        answer["trend"] = round(short_sma / long_sma, 3)

        # RSI
        up = 0
        down = 0
        for i in range(len(short_bars) - 1):
            change = short_bars[i+1]["close"] - short_bars[i]["close"]
            if change > 0:
                up += abs(change)
            else:
                down += abs(change)
        avg_up = up / (len(short_bars) - 1)
        avg_down = down / (len(short_bars) - 1)
        if avg_down == 0:
            answer["rsi"] = 100
        else:
            rs = avg_up / avg_down
            answer["rsi"] = trunc(100 - (100 / (1 + rs)), 2)

        # ATR

        agg_bars = []
        for i in range(0, len(short_bars), 6):
            group = short_bars[i:i+6]
            if not group:
                continue
            agg_open = group[0]["open"]
            agg_high = max(b["high"] for b in group)
            agg_low = min(b["low"] for b in group)
            agg_close = group[-1]["close"]
            agg_bars.append({
                "open": agg_open,
                "high": agg_high,
                "low": agg_low,
                "close": agg_close
            })

        if len(agg_bars) < 2:
            answer["atr"] = 0
        else:
            atr = 0
            for i in range(1, len(agg_bars)):
                prev_close = agg_bars[i - 1]["close"]
                high = agg_bars[i]["high"]
                low = agg_bars[i]["low"]
                tr = max((high - low), abs(high - prev_close), abs(prev_close - low))
                atr += tr
            atr = atr / (len(agg_bars) - 1)
            answer["atr"] = trunc(atr / long_sma, 3) if long_sma != 0 else 0
        return answer
    except Exception as e:
        raise RuntimeError(f"Failed during findIndicators: {e}") from e


def findGeneralTrend(currency, config=Config):
    quote_currencies = {"ZAR": config.ZAR, "USDC": config.USDC, "USDT": config.USDT}
    currencyList = quote_currencies[currency]
    marketTrend = 0
    marketRSI = 0
    for entry in currencyList:
        marketTrend += entry["trend"]
        marketRSI += entry["rsi"]
    trend = marketTrend / len(currencyList)
    rsi = marketRSI / len(currencyList)
    return (trend + ((rsi / 100) + 0.5)) / 2


def findMarketReturns(ticker_list):
    try:
        ticker_list = [ticker for ticker in ticker_list if len(ticker["bars"]) > (24*3)]
        if not ticker_list:
            return None
        num_tickers = len(ticker_list)
        num_bars = min([len(ticker["bars"]) for ticker in ticker_list])
        num_bars = min(num_bars, (24*7)) #1 week
        closes = [
            [bar["close"] for bar in ticker["bars"][-num_bars:]]
            for ticker in ticker_list
        ]
        market_returns = []
        for i in range(1, num_bars):
            day_returns = [
                (closes[j][i] - closes[j][i - 1]) / closes[j][i - 1]
                for j in range(num_tickers)
                if closes[j][i - 1] != 0
            ]
            market_returns.append(sum(day_returns) / len(day_returns))
        return market_returns
    except Exception as e:
        raise RuntimeError(f"Failed during findMarketReturns: {e}") from e


def beta(bar_list, market_returns):
    try:
        close_list = [bar["close"] for bar in bar_list]
        close_list = close_list[-(24*7):] #1week
        return_list = [
            (close_list[i] - close_list[i - 1]) / close_list[i - 1]
            for i in range(1, len(close_list))
            if close_list[i - 1] != 0
        ]
        if len(return_list) > len(market_returns):
            return_list = return_list[-len(market_returns) :]
        else:
            market_returns = market_returns[-len(return_list) :]
        mean_stock = sum(return_list) / len(return_list)
        mean_market = sum(market_returns) / len(market_returns)
        covariance = sum(
            (sr - mean_stock) * (mr - mean_market)
            for sr, mr in zip(return_list, market_returns)
        ) / (len(return_list) - 1)
        var_market = sum((mr - mean_market) ** 2 for mr in market_returns) / (
            len(market_returns) - 1
        )
        return covariance / var_market if var_market != 0 else 0.0
    except Exception as e:
        e.args = ("During beta calculation",)
        raise


def logPost(snippet, code="2"):
    """
    Code '1': Info
    Code '2': Error
    Code '3': Emergency
    """

    currentTS = int(time.time())
    date = datetime.datetime.fromtimestamp(currentTS)
    dateFormat = "%d %b, %Y, %H:%M:%S"
    printDate = date.strftime(dateFormat)
    msg = f"{printDate} ~ {snippet}"

    payload = {"code": code, "app": "BooF", "snippet": msg}
    try:
        result = requests.post("https://www.bmd-studios.com/log", json=payload)
        if result.status_code != 200:
            printLog("Logging Error", True)
            print("Status code: " + str(result.status_code))
            print(result.text)
            print("Original exception: ")
            print(snippet)
    except Exception as c:
        printLog("Logging server down . . .", True)
        print(str(c))
        print("Original exception: ")
        print(snippet)


def bmd_logger(function):
    def exception_handler(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except:
            currentTS = int(time.time())
            date = datetime.datetime.fromtimestamp(currentTS)
            dateFormat = "%d %b, %Y, %H:%M:%S"
            printDate = date.strftime(dateFormat)

            printLog("Exception raised during " + function.__name__, True)
            exc_type, exc_value, exc_traceback = sys.exc_info()
            message = traceback.extract_tb(exc_traceback)
            post_message = (
                f"{printDate} ~ Exception raised during " + function.__name__ + "\n<br>"
            )
            post_message += str(exc_type.__str__) + "\n"
            for line in message.format():
                post_message += line + "<br>"
            post_message += str(exc_value)
            logPost(post_message, "2")

    return exception_handler


def bmd_report(config=Config):
    printLog("Reporting Status . . .")
    url = "https://www.bmd-studios.com/bot"
    headers = {"accept": "application/json"}
    payload = {
        "id": "01",
        "bot_name": "BooF",
        "ts": str(int(time.time())),
        "status": f"{config.valrStatus}({trunc(config.botTimer, 2)})",
    }
    result = requests.post(url=url, json=payload)
    result.raise_for_status()


def printLog(message, log=False):
    currentTS = int(time.time())
    date = datetime.datetime.fromtimestamp(currentTS)
    dateFormat = "%d %b, %Y, %H:%M:%S"
    printDate = date.strftime(dateFormat)
    print(" " * 100, end="\r", flush=True)
    if log:
        print(f"{printDate} ~ {message}", flush=True)
    else:
        print(f"{printDate} ~ {message}", end="\r", flush=True)


# User Functions


def botLoop(config=Config):
    # Check User Credit
    users = db.getUsers()
    bots = db.getBots()

    printLog("Checking user credit . . .")
    for user in users:
        active = False
        for bot in bots:
            if bot.user_id == user.id and bot.active:
                active = True
                break
        if active:
            credit = db.getCredits(user_id=user.id)
            if credit["credit"] <= 0:
                message = db.Message(
                    [0, user.id, "WARNING", "All Bots paused, insufficient Credit"]
                )
                message.post()
                message = db.Message(
                    [0, user.id, "INFO", "Buy more credit before restarting you bots"]
                )
                message.post()
                for bot in bots:
                    if bot.user_id == user.id:
                        bot.stop()

    printLog("Checking Discontinued Accounts . . .")
    for bot in bots:
        checkDiscontinued(config, bot)

    # Sell irrelevant accounts and confirm balances
    printLog("Checking bot balances . . .")
    for bot in bots:
        checkBalances(config, bot)

    # Find total equity
    printLog("Finding bot equity . . .")
    for bot in bots:
        findEquity(config, bot)

    # Find Bot Balance nr and value
    printLog("Finding balance value . . .")
    for bot in bots:
        if bot.active:
            findBalance(config, bot)

    # Buy new accounts
    printLog("Setting Active Accounts . . .")
    for bot in bots:
        if bot.active:
            setAccounts(config, bot)

    # Rebalance Active Account
    printLog("Balancing Bots . . .")
    for bot in bots:
        if bot.active:
            balanceBots(config, bot)

    # Check Staking
    printLog("Setting Stake . . .")
    for bot in bots:
        if bot.active:
            setStake(config, bot)


def getSign(secret, ts, verb, path, body=""):  # Oauth signing
    payload = "{}{}{}{}".format(ts, verb.upper(), path, body)
    message = bytearray(payload, "utf-8")
    signature = hmac.new(
        bytearray(secret, "utf-8"), message, digestmod=hashlib.sha512
    ).hexdigest()
    return signature


def fetchBalances(bot:db.Bot):
    ts = int(time.time() * 1000)
    verb = "GET"
    path = "/v1/account/balances"
    body = "?excludeZeroBalances=true"
    url = f"https://api.valr.com{path}{body}"
    sign = getSign(bot.secret, ts, verb, path, body)
    payload = {}
    headers = {
        "X-VALR-API-KEY": bot.key,
        "X-VALR-SIGNATURE": str(sign),
        "X-VALR-TIMESTAMP": str(ts),
    }
    response = externalSession.get(url, headers=headers, data=payload)
    if response.status_code != 200:
        raise ValueError(f"During fetchBalance: {response.json()["message"]}")
    return response.json()

def updateQuoteBalance(bot:db.Bot, balances:dict):
    for entry in balances:
        if entry["currency"] == bot.currency:
            available = float(entry["available"])
            if available != bot.quote_balance:
                bot.quote_balance = available
                bot.update()
            break

def fetchPrices():
    try:
        with open('prices.json', 'r') as f:
            return json.load(f)
    except Exception as e:
        logPost(f"Error finding prices.json: {e}", '2')
        raise Exception('Error during fetchPrices') from e

def unStake(config:Config, bot:db.Bot, base, amount = None):
    stake = 0
    if amount is None:
        stake = returnStake(bot.key, bot.secret, base)
    else:
        stake = amount
    unstaked = 0
    if stake != 0:
        try:
            ts = int(time.time() * 1000)
            payload = {}
            verb = "POST"
            path = "/v1/staking/un-stake"
            body = {
            "currencySymbol": base,
            "amount": f"{stake}",
            }
            url = f"https://api.valr.com{path}"
            sign = getSign(
                bot.secret, ts, verb, path, json.dumps(body)
            )
            headers = {
                "Content-Type": "application/json",
                "X-VALR-API-KEY": bot.key,
                "X-VALR-SIGNATURE": str(sign),
                "X-VALR-TIMESTAMP": str(ts),
            }
            response = externalSession.post(
                url, headers=headers, json=body
            )
            if response.status_code == 202:
                if response.content:
                    json_response = response.json()
                    if json_response.get("requested", False):
                        unstaked = stake
                else:
                    unstaked = stake
            else:
                msg = f"Error during closing stake: \n<br>{response.reason}<br>{response.content}"
                logPost(msg, "2")
        except Exception as e:
            logPost(f"Error during closing stake: {e}")
    return unstaked


def liquidate(config:Config, bot:db.Bot, balance_entry:dict, price_data:dict) -> bool:
    base = balance_entry["currency"]
    amount = float(balance_entry["available"])
    quote = price_data["ticker"][len(base):]
    result = trade(
        "SELL",
        quote,
        base,
        bot.key,
        bot.secret,
        amount,
        int(price_data["decimal"]),
    )
    if result:
        transaction = db.Transaction(
            [
                0,
                bot.id,
                "WITHDRAW",
                result["volume"],
                result["value"],
                base,
                quote,
                int(time.time()),
                result["fee"],
            ]
        )
        transaction.post()
        return True
    return False


def limitLiquidate(config:Config, bot:db.Bot, balance_entry:dict, price_data:dict) -> bool:
    status = False
    base = balance_entry["currency"]
    amount = float(balance_entry["available"])

    price = Decimal(price_data["price"])
    tick = Decimal(price_data["tick"])
    step = (price/tick).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    
    payload = {
        "side": "SELL",
        "quantity": trunc(str(amount), int(price_data["decimal"])),
        "price": str(step*tick),
        "pair":price_data["ticker"],

    }
    ts = int(time.time() * 1000)
    verb = "POST"
    path = "/v2/orders/limit"
    url = f"https://api.valr.com{path}"
    sign = getSign(bot.secret, ts, verb, path, json.dumps(payload))
    headers = {
        "Content-Type": "application/json",
        "X-VALR-API-KEY": bot.key,
        "X-VALR-SIGNATURE": str(sign),
        "X-VALR-TIMESTAMP": str(ts),
    }
    response = externalSession.post(url=url, headers=headers, json=payload)
    jsonResponse = response.json()
    if response.status_code == 201:
        message = db.Message([
            0,
            bot.user_id,
            "WARNING",
            f"Market trades for {base} is closed, liquidating via Limit Orders. Please ensure ticker is Closed/liquidated to avoid undue loss."
        ])
        message.post()
        status = True
    else:
        raise ValueError(jsonResponse["message"])
    return status



def downturnLiquidation(config:Config, bot:db.Bot, wallet_balances:list, price_list:list):
    result = False
    for entry in wallet_balances:
        if entry["currency"] in ["ZAR" ,"USDC", "USDT"]:
            continue
        sold = False
        available = float(entry["available"])
        for ticker in price_list:   #Withdraw to bot currency
            ticker_quote = next(quote for quote in ["ZAR","USDC","USDT"] if ticker["ticker"].endswith(quote))
            ticker_base = ticker["ticker"][:-len(ticker_quote)]

            if (
                entry["currency"] == ticker_base
                and bot.currency == ticker_quote
                and ticker["market"]
                and ticker["active"]
            ):
                value = available * ticker["price"]
                if value > ticker["min_value"]:
                    sold = liquidate(config, bot, entry, ticker)
                    break
        if sold:
            result = True
            continue
        for ticker in price_list:   #Withdraw to any currency
            ticker_quote = next(quote for quote in ["ZAR","USDC","USDT"] if ticker["ticker"].endswith(quote))
            ticker_base = ticker["ticker"][:-len(ticker_quote)]
            if (
                entry["currency"] == ticker_base
                and ticker["market"]
                and ticker["active"]
            ):
                value = available * ticker["price"]
                if value > ticker["min_value"]:
                    sold = liquidate(config, bot, entry, ticker)
                    break
        if sold:
            result = True
            continue
        for ticker in price_list:   #Withdraw to any currency, limit order
            ticker_quote = next(quote for quote in ["ZAR","USDC","USDT"] if ticker["ticker"].endswith(quote))
            ticker_base = ticker["ticker"][:-len(ticker_quote)]

            if (
                entry["currency"] == ticker_base
                and ticker["limit"]
                and ticker["active"]
            ):
                value = available * ticker["price"]
                if value > ticker["min_value"]:
                    sold = limitLiquidate(config, bot, entry, ticker)
                    break
        if sold:
            result = True
    return result

def convertQuote(config:Config, bot:db.Bot, balance_entry):
    currency = balance_entry["currency"]
    available = float(balance_entry["available"])
    if currency == bot.currency:
        return False
    conversions = {
        "ZAR": {
            "USDC": ("SELL", "ZAR", "USDC", 1, "WITHDRAW"),
            "USDT": ("SELL", "ZAR", "USDT", 1, "WITHDRAW")
        },
        "USDC": {
            "ZAR": ("BUY", "ZAR", "USDC", 10, "INVEST"),
            "USDT": ("SELL", "USDC", "USDT", 1, "WITHDRAW")
        },
        "USDT": {
            "USDC": ("BUY", "USDC", "USDT", 1, "INVEST"),
            "ZAR": ("BUY", "ZAR", "USDT", 10, "INVEST")
        }
    }
    conversion = conversions[bot.currency].get(currency, {})
    if not conversion:
        return False
    side, quote, base, min_amount, transaction_type = conversion
    if available > min_amount:
        result = trade(side, quote, base, bot.key, bot.secret, available)
        if result:
            transaction = db.Transaction(
                [
                    0,
                    bot.id,
                    transaction_type,
                    result["volume"],
                    result["value"],
                    base,
                    quote,
                    int(time.time()),
                    result["fee"],
                ]
            )
            transaction.post()
            return True
    return False

def checkBalances(config:Config, bot:db.Bot):
    """
    Checks balances against active accounts, sells if balance is not found and updates account if found
    """
    try:
        printLog("Check Balances")

        downturnProtection = False
        if not bot.active:
            credits = db.getCredits(bot_id=bot.id)
            if bot.downturn_protection and credits["credit"] > 0:
                downturnProtection = True 
            else:
                return

        accounts = db.getActiveAccounts(bot_id=bot.id)
        account_tickers = {account.base for account in accounts}

        quote_currencies = ["ZAR","USDC","USDT"]
        quote_tickers = {
            "ZAR": config.ZAR,
            "USDC": config.USDC,
            "USDT": config.USDT
        }

        balances = fetchBalances(bot = bot)
        updateQuoteBalance(bot, balances)
        price_list = fetchPrices()

        staked_cache = {}
        for currency in config.stake:
            staked_cache[currency] = returnStake(bot.key, bot.secret, currency)

        if downturnProtection:
            for currency in config.stake:
                if staked_cache[currency] != 0:
                    unStake(config, bot, currency, staked_cache[currency])
            sold = downturnLiquidation(config, bot, balances, price_list)
            for account in accounts:
                account.delete()
            if sold:
                balances = fetchBalances(bot = bot)
                updateQuoteBalance(bot, balances)
                price_list = fetchPrices()

        repeat = True
        while repeat:
            repeat = False

            for entry in balances:
                currency = entry["currency"]
                available = float(entry["available"]) + staked_cache[currency] if currency in config.stake else float(entry["available"])
                if currency == bot.currency:
                    continue
                if currency in quote_currencies:
                    sold = convertQuote(config, bot, entry)
                    if sold:
                        repeat = True
                    continue

                found = currency in account_tickers

                if found:
                    account = next((account for account in accounts if account.base == currency), None)
                    if account.volume != available:
                        if currency in config.stake:
                            account.stake = staked_cache[currency]
                        account.volume = available
                        account.update()
                    continue

                else:
                    sold = False
                    for ticker in price_list:
                        ticker_quote = next(quote for quote in ["ZAR","USDC","USDT"] if ticker["ticker"].endswith(quote))
                        ticker_base = ticker["ticker"][:-len(ticker_quote)]
                        if (
                            entry["currency"] == ticker_base
                            and ticker["market"]
                            and ticker["active"]
                        ):
                            if entry["currency"] in config.stake and staked_cache[entry["currency"]] != 0:
                                unStake(config, bot, entry["currency"], staked_cache[entry["currency"]])
                            value = available * ticker["price"]
                            if value > ticker["min_value"]:
                                sold = liquidate(config, bot, entry, ticker)
                                break
                    if not sold:
                        for ticker in price_list:   #Withdraw to any currency, limit order
                            ticker_quote = next(quote for quote in ["ZAR","USDC","USDT"] if ticker["ticker"].endswith(quote))
                            ticker_base = ticker["ticker"][:-len(ticker_quote)]
                            if (
                            entry["currency"] == ticker_base
                                and ticker["limit"]
                                and ticker["active"]
                                and not ticker["market"]
                            ):
                                if entry["currency"] in config.stake and staked_cache[currency] != 0:
                                    unStake(config, bot, currency, staked_cache[currency])
                                value = available * ticker["price"]
                                if value > ticker["min_value"]:
                                    sold = limitLiquidate(config, bot, entry, ticker)
                                    break

                    if sold:
                        repeat = True
            if repeat:
                balances = fetchBalances(bot = bot)
                updateQuoteBalance(bot, balances)
                price_list = fetchPrices()
                
                
    except Exception as e:
        logPost(f"During checkBalances: {e}", "2")



def findEquity(config=Config, bot=db.Bot):
    """
    Total recorded equity for bot
    """
    total = 0
    accountns = db.getActiveAccounts(bot_id=bot.id)
    total += bot.quote_balance
    for account in accountns:
        total += account.volume * account.price(config)
    if total != bot.equity:
        bot.equity = total
        bot.update()


def findBalance(config=Config, bot=db.Bot):
    balance_nr = bot.balance_nr
    loopCount = 0
    while True:
        loopCount += 1
        if loopCount > 20:
            raise ValueError("Equity Balance error, more than 20 loops")
        balanceList = []
        if bot.currency == "ZAR":
            currencyList = config.ZAR
        elif bot.currency == "USDC":
            currencyList = config.USDC
        elif bot.currency == "USDT":
            currencyList = config.USDT

        shares = balance_nr + ((balance_nr) * bot.margin)
        minTradeLow = (
            currencyList[balance_nr - 1]["minTrade"]
            + currencyList[balance_nr - 2]["minTrade"]
        ) / 2
        if balance_nr < 2:
            minTradeLow = currencyList[balance_nr - 1]["minTrade"]
        equityLow = ((minTradeLow * 2) / bot.margin) * shares
        if bot.equity < equityLow and balance_nr == 1:  # Bot equity very low
            bot.margin += 0.01
            if bot.margin == 0.16:
                bot.margin = 0.15
                bot.active = False
                bot.update()
                message = db.Message(
                    [0, bot.user_id, "ERROR", "Insufficient equity, pausing bot."]
                )
                message.post()
                break
            else:
                bot.update()
                message = db.Message(
                    [
                        0,
                        bot.user_id,
                        "WARNING",
                        f"Insufficient equity, increasing margin({(bot.margin * 100):0f}) to maintain normal operations.",
                    ]
                )
                message.post()
        elif bot.equity < equityLow:  # Bot equity low
            balance_nr -= 1
        elif balance_nr < len(currencyList):  # Bot equity high
            shares = balance_nr + ((balance_nr + 1) * bot.margin)
            minTradeHigh = (
                currencyList[balance_nr]["minTrade"]
                + currencyList[balance_nr - 1]["minTrade"]
            ) / 2
            equityHigh = ((minTradeHigh * 2.5) / bot.margin) * shares
            if bot.equity > equityHigh:
                balance_nr += 1
            else:  # Balance number found
                break
        else:  # Equity above balance number adjustments, max balance number
            balance_nr = len(currencyList)
            break

    if balance_nr != bot.balance_nr:
        bot.balance_nr = balance_nr
        bot.update()

    shares = balance_nr + (balance_nr * bot.margin)
    balance_value = bot.equity / shares

    if bot.balance_value != balance_value:
        bot.balance_value = balance_value
        bot.update()


def setAccounts(config=Config, bot=db.Bot):
    currencyList = []
    if bot.currency == "ZAR":
        currencyList = config.ZAR
    elif bot.currency == "USDC":
        currencyList = config.USDC
    elif bot.currency == "USDT":
        currencyList = config.USDT

    accounts = db.getActiveAccounts(bot_id=bot.id)
    if len(accounts) > bot.balance_nr:  # Close old account
        accountNrs = len(accounts)
        difference = accountNrs - bot.balance_nr

        for entry in reversed(currencyList):
            for account in accounts:
                if account.base == entry["base"]:
                    result = trade(
                        "SELL",
                        bot.currency,
                        account.base,
                        bot.key,
                        bot.secret,
                        account.volume,
                        entry["decimal"],
                    )
                    if result:
                        transaction = db.Transaction(
                            [
                                0,
                                bot.id,
                                "WITHDRAW",
                                result["volume"],
                                result["value"],
                                account.base,
                                bot.currency,
                                int(time.time()),
                                result["fee"],
                            ]
                        )
                        transaction.post()
                        bot.equity += result["value"]
                        bot.update()
                    account.delete()
                    difference -= 1
                    break
            if difference <= 0:
                break

    elif (
        len(accounts) < bot.balance_nr
    ):  # Open new account, relative to config.'TICKERS' order
        currencyList = []
        accountNrs = len(accounts)
        difference = bot.balance_nr - accountNrs
        if bot.currency == "ZAR":
            currencyList = config.ZAR
        elif bot.currency == "USDC":
            currencyList = config.USDC
        elif bot.currency == "USDT":
            currencyList = config.USDT

        for newAccount in currencyList:
            found = False
            for account in accounts:
                if account.base == newAccount["base"]:
                    found = True
                    break
            if not found:
                for account in accounts:
                    if account.swing > bot.margin and account.direction == "UP":
                        for currency in currencyList:
                            if currency["base"] == account.base:
                                amount = account.volume - bot.balance_value
                                result = trade(
                                    "SELL",
                                    bot.currency,
                                    account.base,
                                    bot.key,
                                    bot.secret,
                                    amount,
                                    currency["decimal"],
                                )
                                if result:
                                    transaction = db.Transaction(
                                        [
                                            0,
                                            bot.id,
                                            "WITHDRAW",
                                            result["volume"],
                                            result["value"],
                                            newAccount["base"],
                                            bot.currency,
                                            int(time.time()),
                                            result["fee"],
                                        ]
                                    )
                                    transaction.post()
                                    bot.equity += result["value"]
                                    bot.update()

                result = trade(
                    "BUY",
                    bot.currency,
                    newAccount["base"],
                    bot.key,
                    bot.secret,
                    bot.balance_value,
                )
                if result:
                    transaction = db.Transaction(
                        [
                            0,
                            bot.id,
                            "INVEST",
                            result["volume"],
                            result["value"],
                            newAccount["base"],
                            bot.currency,
                            int(time.time()),
                            result["fee"],
                        ]
                    )
                    transaction.post()
                    bot.equity -= result["value"]
                    bot.update()
                    newAAccount = db.ActiveAccount(
                        [0, bot.id, newAccount["base"], result["volume"], 0, 0, ""]
                    )
                    newAAccount.post()
                    accounts.append(newAAccount)
                else:
                    newAAccount = db.ActiveAccount(
                        [0, bot.id, newAccount["base"], 0, 0, 0, ""]
                    )
                    newAAccount.post()
                    accounts.append(newAAccount)
                difference -= 1
            if difference <= 0:
                break


def balanceBots(config=Config, bot=db.Bot):
    accounts = db.getActiveAccounts(bot_id=bot.id)

    currencyList = []
    if bot.currency == "ZAR":
        currencyList = config.ZAR
    elif bot.currency == "USDC":
        currencyList = config.USDC
    elif bot.currency == "USDT":
        currencyList = config.USDT

    marketATR = 0
    for entry in currencyList:
        if entry["atr"] == 0:
            marketATR += bot.margin
        else:
            marketATR += entry["atr"]
    marketATR = marketATR / len(currencyList)

    generalTrend = findGeneralTrend(bot.currency, config)

    for account in accounts:
        currencyDetails = None
        for entry in currencyList:
            if entry["base"] == account.base:
                currencyDetails = entry
                break

        price = float(currencyDetails["price"])
        decimal = int(currencyDetails["decimal"])
        value = account.volume * price

        volatility = (
            abs(currencyDetails["volatility"])
            if currencyDetails["volatility"] > 0.1
            else 0.1
        )
        atr = currencyDetails["atr"] if currencyDetails["atr"] != 0 else bot.margin
        margin = (
            max(
                min(
                    ((bot.margin * 2) + (bot.margin * volatility) + atr) / 4,
                    bot.margin * 1.2,
                ),
                bot.margin * 0.8,
            )
            if bot.dynamic_margin
            else bot.margin
        )

        weight = max(0.8, min(1, generalTrend))  # Adjustment capped at 80% on downtrend
        balanceValue = (
            bot.balance_value * weight if bot.refined_weight else bot.balance_value
        )

        if value > balanceValue:
            difference = (value - balanceValue) / balanceValue
            if account.direction != "UP":
                account.direction = "UP"
                account.update()
            if difference > margin * 5:
                sellVolume = (value - balanceValue) / price
                result = trade(
                    "SELL",
                    bot.currency,
                    account.base,
                    bot.key,
                    bot.secret,
                    sellVolume,
                    decimal,
                )
                if result:
                    transaction = db.Transaction(
                        [
                            0,
                            bot.id,
                            "WITHDRAW",
                            result["volume"],
                            result["value"],
                            account.base,
                            bot.currency,
                            int(time.time()),
                            result["fee"],
                        ]
                    )
                    transaction.post()
                    account.volume -= result["volume"]
                    account.update()
                    bot.quote_balance += result["value"]
                    bot.update()
            elif difference < margin:
                if difference != account.swing:
                    account.swing = difference
                    account.update()
            elif difference > account.swing:
                account.swing = difference
                account.update()
            elif (
                difference < (account.swing * (1 - ((account.swing + margin) / 2)))
                and difference > margin
            ):
                printLog(
                    f"Selling {account.base} Swing:{difference}/Margin:{margin}", True
                )
                sellVolume = (value - balanceValue) / price
                result = trade(
                    "SELL",
                    bot.currency,
                    account.base,
                    bot.key,
                    bot.secret,
                    sellVolume,
                    decimal,
                )
                if result:
                    transaction = db.Transaction(
                        [
                            0,
                            bot.id,
                            "SELL",
                            result["volume"],
                            result["value"],
                            account.base,
                            bot.currency,
                            int(time.time()),
                            result["fee"],
                        ]
                    )
                    transaction.post()
                    account.volume -= result["volume"]
                    account.update()
                    bot.quote_balance += result["value"]
                    bot.update()

        elif value < balanceValue:
            difference = (balanceValue - value) / balanceValue
            if account.direction != "DOWN":
                account.direction = "DOWN"
                account.update()
            if difference > margin * 5:
                buyValue = balanceValue - value
                if buyValue < bot.quote_balance:
                    result = trade(
                        "BUY",
                        bot.currency,
                        account.base,
                        bot.key,
                        bot.secret,
                        buyValue,
                        decimal,
                    )
                    if result:
                        transaction = db.Transaction(
                            [
                                0,
                                bot.id,
                                "INVEST",
                                result["volume"],
                                result["value"],
                                account.base,
                                bot.currency,
                                int(time.time()),
                                result["fee"],
                            ]
                        )
                        transaction.post()
                        account.volume += result["volume"]
                        account.update()
                        bot.quote_balance -= result["value"]
                        bot.update()
            if difference < margin:
                if difference != account.swing:
                    account.swing = difference
                    account.update()
            elif difference > account.swing:
                account.swing = difference
                account.update()
            elif (
                difference < (account.swing * (1 - ((account.swing + margin) / 2)))
                and difference > margin
            ):
                printLog(
                    f"Buying {account.base} Swing:{difference}/Margin:{margin}", True
                )
                buyValue = balanceValue - value
                result = trade(
                    "BUY",
                    bot.currency,
                    account.base,
                    bot.key,
                    bot.secret,
                    buyValue,
                    decimal,
                )
                if result:
                    transaction = db.Transaction(
                        [
                            0,
                            bot.id,
                            "BUY",
                            result["volume"],
                            result["value"],
                            account.base,
                            bot.currency,
                            int(time.time()),
                            result["fee"],
                        ]
                    )
                    transaction.post()
                    account.volume += result["volume"]
                    account.update()
                    bot.quote_balance -= result["value"]
                    bot.update()


def setStake(config=Config, bot=db.Bot):
    accounts = db.getActiveAccounts(bot_id=bot.id)
    for account in accounts:
        decimal = 0
        if bot.currency == "ZAR":
            for entry in config.ZAR:
                if entry["base"] == account.base:
                    decimal = entry["decimal"]
        elif bot.currency == "USDC":
            for entry in config.USDC:
                if entry["base"] == account.base:
                    decimal = entry["decimal"]
        elif bot.currency == "USDT":
            for entry in config.USDT:
                if entry["base"] == account.base:
                    decimal = entry["decimal"]

        if account.base in config.stake:
            if (
                account.stake < account.volume * 0.7
                or account.stake > account.volume * 0.8
            ):
                if account.stake < account.volume * 0.7:
                    diff = trunc(((account.volume * 0.75) - account.stake), decimal)
                    try:
                        ts = int(time.time() * 1000)
                        payload = {}
                        verb = "POST"
                        path = "/v1/staking/stake"
                        body = {
                            "currencySymbol": account.base,
                            "amount": trunc(f"{diff:.{decimal + 1}f}", decimal),
                        }
                        url = f"https://api.valr.com{path}"
                        sign = getSign(bot.secret, ts, verb, path, json.dumps(body))
                        headers = {
                            "Content-Type": "application/json",
                            "X-VALR-API-KEY": bot.key,
                            "X-VALR-SIGNATURE": str(sign),
                            "X-VALR-TIMESTAMP": str(ts),
                        }
                        response = externalSession.post(url, headers=headers, json=body)
                        response.raise_for_status()
                        account.stake += diff
                        account.update()
                    except Exception as e:
                        logPost(f"During Staking: {e}")
                elif account.stake > account.volume * 0.8:
                    diff = trunc((account.stake - (account.volume * 0.75)), decimal)
                    try:
                        ts = int(time.time() * 1000)
                        payload = {}
                        verb = "POST"
                        path = "/v1/staking/un-stake"
                        body = {
                            "currencySymbol": account.base,
                            "amount": trunc(f"{diff:.{decimal + 1}f}", decimal),
                        }
                        url = f"https://api.valr.com{path}"
                        sign = getSign(bot.secret, ts, verb, path, json.dumps(body))
                        headers = {
                            "Content-Type": "application/json",
                            "X-VALR-API-KEY": bot.key,
                            "X-VALR-SIGNATURE": str(sign),
                            "X-VALR-TIMESTAMP": str(ts),
                        }
                        response = externalSession.post(url, headers=headers, json=body)
                        if response.status_code != 202:
                            print(response.reason)
                            print(response.content)
                        response.raise_for_status()
                        account.stake -= diff
                    except Exception as e:
                        logPost(f"During Staking: {e}")


def returnStake(key, secret, base):
    "Returns staked volume"
    try:
        ts = int(time.time()) * 1000
        payload = {}
        verb = "GET"
        path = f"/v1/staking/balances/{base}"
        body = ""
        url = f"https://api.valr.com{path}"
        sign = getSign(secret, ts, verb, path)
        headers = {
            "X-VALR-API-KEY": key,
            "X-VALR-SIGNATURE": str(sign),
            "X-VALR-TIMESTAMP": str(ts),
        }
        response = externalSession.get(url, headers=headers, data=payload)
        response.raise_for_status()
        jsonResponse = response.json()
        return float(jsonResponse["amount"])
    except Exception as e:
        logPost(f"During Stake Upadte: {e}", "2")
        return 0


# Emails


def emailBase(content):
    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>BooF</title>
    </head>
    <body>
        <div style="background-color:rgba(200, 200, 200); margin:5px; padding:10px; border-radius:30px">
            <br>
            <a href="https://www.boof-bots.com"><img src="https://www.boof-bots.com/static/images/boof.png" style="width: 320px; margin: auto;"/></a>
            <br>
            <div style="background-color: rgba(235, 255, 255); padding:20px; border-radius:30px;">
                {content}
            </div>
            <br>
            <div style="background-color:rgb(14, 118, 145); margin:5px; padding:10px; border-radius:30px;">
                <p style="color:black;"><b>BooF</b> was created with love by <a href="https://www.bmd-studios.com" target="_blank">MrBlindMouse</a></p>
                <p style="color:black;">For any queries, please contact us via email at <a href="mailto:admid@bmd-studios.com">admid@bmd-studios.com</a></p>
            </div>
        </div>
    </body>
    </html>
    """


def feedbackEmail(config=Config, user=db.User):
    body = f"""
        <p style='color:black;'>Hi {user.name},</p><br>
        <p style='color:black;'>Its been 2 weeks, is everything ok?</p>
        <p style='color:black;'>If you have any feedback to make the BooF bots better, please send us a mail at <address><a href='mailto:boof@bmd-studios.com'>boof@bmd-studios.com</a></address>!</p>
        <p style='color:black;'>We hope to see you again soon!</p><br>
        <p style='color:black;'>Best wishes,</p>
        <p style='color:black;'>&emsp;The BooF Team</p>
    """
    print("Feedback Email sent")
    postmark.sendMail(
        config.postmarkKey, emailBase(body), "What happened?", recipient=user.email
    )


def unVerifiedEmail(config=Config, user=db.User):
    body = f"""
        <p style='color:black;'>Hi {user.name},</p><br>
        <p style='color:black;'>This is a reminder that your BooF account has not been verified.</p>
        <p style='color:black;'>To verify your account, login; and under 'Profile' request a verification email. Then follow the instructions once your receive the email.</p>
        <p style='color:black;'>If you do not verify within the next 2 days, your account it will be deleted.</p><br>
        <p style='color:black;'>Hope all goes well,</p>
        <p style='color:black;'>&emsp;The BooF Team</p>
    """
    print("unVerifiedEmail sent")
    postmark.sendMail(
        config.postmarkKey,
        emailBase(body),
        "Account Verification?",
        recipient=user.email,
    )


def botsInactiveEmail(config=Config, user=db.User):
    body = f"""
        <p style='color:black;'>Hi {user.name},</p><br>
        <p style='color:black;'>Did you forget to activate your bots?</p>
        <p style='color:black;'>Under the bot's panel, click on the 'Config' button and either select 'Active' or 'Downturn Protection'(or both) to turn on the bot.</p>
        <p style='color:black;'>We hope to see you again soon!</p><br>
        <p style='color:black;'>Best wishes,</p>
        <p style='color:black;'>&emsp;The BooF Team</p>
    """
    print("botsInactiveEmail sent")
    postmark.sendMail(
        config.postmarkKey, emailBase(body), "Everything ok?", recipient=user.email
    )


def noCreditsReminderEmail(config=Config, user=db.User):
    body = f"""
        <p style='color:black;'>Hi {user.name},</p><br>
        <p style='color:black;'>Its been a while, is everything ok?</p>
        <p style='color:black;'>You're bots are in-active and credits zero. To buy more credits, log in to your account and go to the 'Buy Credits' page.</p>
        <p style='color:black;'>If you have any feedback to make the BooF bots better, please send us a mail at <address><a href='mailto:boof@bmd-studios.com'>boof@bmd-studios.com</a></address>!</p>
        <p style='color:black;'>We hope to see you again soon!</p><br>
        <p style='color:black;'>Best wishes,</p>
        <p style='color:black;'>&emsp;The BooF Team</p>
    """
    print("noCreditsReminderEmail sent")
    postmark.sendMail(
        config.postmarkKey, emailBase(body), "What happened?", recipient=user.email
    )


def noCreditsEmail(config=Config, user=db.User):
    body = f"""
        <p style='color:black;'>Hey {user.name},</p><br>
        <p style='color:black;'>I'm sorry to say, but your Credits has run out and bots as been shut down.</p>
        <p style='color:black;'>To buy more credits, log in to your account and go to the 'Buy Credits' page.</p>
        <p style='color:black;'>We hope to see you again soon!</p><br>
        <p style='color:black;'>Best wishes,</p>
        <p style='color:black;'>&emsp;The BooF Team</p>
    """
    print("noCreditsEmail sent")
    postmark.sendMail(
        config.postmarkKey, emailBase(body), "No Credit Warning", recipient=user.email
    )


def creditsReminderEmail(config=Config, user=db.User):
    body = f"""
        <p style='color:black;'>Hi {user.name},</p><br>
        <p style='color:black;'>You're Credits are running low. You've just passed the 0.25Credits mark.</p>
        <p style='color:black;'>Once your Credits run out your bots will be paused. To buy more credits, log in to your account and go to the 'Buy Credits' page.</p>
        <p style='color:black;'>We hope to see you again soon!</p><br>
        <p style='color:black;'>Best wishes,</p>
        <p style='color:black;'>&emsp;The BooF Team</p>
    """
    print("creditsReminderEmail sent")
    postmark.sendMail(
        config.postmarkKey, emailBase(body), "Credit Warning", recipient=user.email
    )


def creditsFollowUpReminderEmail(config=Config, user=db.User):
    body = f"""
        <p style='color:black;'>Hey {user.name},</p><br>
        <p style='color:black;'>You're Credits are running low. You've just passed the 0.1Credits mark and falling fast.</p>
        <p style='color:black;'>Once your Credits run out your bots will be paused. To buy more credits, log in to your account and go to the 'Buy Credits' page.</p>
        <p style='color:black;'>We hope to see you again soon!</p><br>
        <p style='color:black;'>Best wishes,</p>
        <p style='color:black;'>&emsp;The BooF Team</p>
    """
    print("creditsFollowUpReminderEmail sent")
    postmark.sendMail(
        config.postmarkKey, emailBase(body), "Low Credit Warning", recipient=user.email
    )


def downturnProtectionEmail(config=Config, user=db.User):
    body = f"""
        <p style='color:black;'>Hey {user.name},</p><br>
        <p style='color:black;'>You're receiving this email to notify you that one or more of your bots has been paused. The general trend is concerningly low and Downturn Protection has been activated. Once the trend recovers your bot will automatically resume.</p>
        <p style='color:black;'>If you wish to resume your bot regardless; please go to the bot's config page, deselect 'Downturn Protection', select 'Active' and then click 'Update'</p>
        <p style='color:black;'>If you do not want your bot to resume when the trend recovers, simply de-activate Downturn Protection without activating the bot.</p>
        <p style='color:black;'>We hope everything goes well.</p><br>
        <p style='color:black;'>Best wishes,</p>
        <p style='color:black;'>&emsp;The BooF Team</p>
    """
    print("creditsFollowUpReminderEmail sent")
    postmark.sendMail(
        config.postmarkKey, emailBase(body), "Downturn Protection", recipient=user.email
    )


# Admin Functions


def checkTokens():
    ts = int(time.time())
    tokens = db.getTokens()
    for token in tokens:
        if (token.ts + (token.period * 60 * 60)) < ts:
            token.delete()


def checkUserReminders(config=Config):
    "Check reminders"
    ts = int(time.time())
    users = db.getUsers()
    for user in users:
        reminders = user.reminder
        credits = db.getCredits(user_id=user.id)
        for reminder in reminders[:]:
            if int(reminder["code"]) == 0:  # Verified check
                if user.verified:
                    reminders.remove(reminder)
                elif ts - int(reminder["ts"]) > (
                    5 * 24 * 60 * 60
                ):  # 5 Days not verified
                    found = False
                    for checkReminder in reminders:
                        if int(checkReminder["code"]) == 6:
                            found = True
                            break
                    if not found:
                        unVerifiedEmail(config, user)
                        newEntry = {
                            "code": 6,
                            "ts": ts,
                            "description": "Unverified for 5 days",
                        }
                        reminders.append(newEntry)

            elif int(reminder["code"]) == 1:  # 0.25 credits check
                if credits["credit"] > 0.25:
                    reminders.remove(reminder)

            elif int(reminder["code"]) == 2:  # 0.1 credits check
                if credits["credit"] > 0.1:
                    reminders.remove(reminder)

            elif int(reminder["code"]) == 3:  # No credits check
                if credits["credit"] > 0:
                    reminders.remove(reminder)
                else:  # Stop bots due to no credits remaining
                    bots = db.getBots(user_id=user.id)
                    for bot in bots:
                        if bot.active:
                            bot.stop()
                    if ts - int(reminder["ts"]) > (
                        7 * 24 * 60 * 60
                    ):  # 7 days no credit
                        found = False
                        for checkReminder in reminders:
                            if int(checkReminder["code"]) == 4:
                                found = True
                                break
                        if not found:
                            noCreditsReminderEmail(config, user)
                            newEntry = {
                                "code": 4,
                                "ts": ts,
                                "description": "Out of credits for 1 week",
                            }
                            reminders.append(newEntry)

            elif int(reminder["code"]) == 4:  # 1 Week credit check
                if credits["credit"] > 0:
                    reminders.remove(reminder)

            elif int(reminder["code"]) == 5:  # Activity Check
                if credits["active"] > 0:
                    reminders.remove(reminder)

            elif int(reminder["code"]) == 6:  # Final verified check
                if user.verified:
                    reminders.remove[reminder]
                elif (ts - int(reminder["ts"])) > (
                    2 * 24 * 60 * 60
                ):  # Delete user after 7 days(2 after reminder) not verified
                    user.delete()
                    break

            elif int(reminder["code"]) == 7:  # Activity Check
                if credits["active"] > 0:
                    reminders.remove(reminder)
                elif (ts - int(reminder["ts"])) > (
                    14 * 24 * 60 * 60
                ):  # 14 Days inactive
                    feedbackEmail(config, user)
                    newEntry = {"code": 5, "ts": ts, "description": "2 Weeks inactive"}
                    reminders.append(newEntry)

        user.reminder = reminders
        user.update()


def checkUserCredits(config=Config):
    "Set reminders for credits and bots"
    users = db.getUsers()
    for user in users:
        ts = int(time.time())
        credits = db.getCredits(user_id=user.id)
        bots = db.getBots(user_id=user.id)

        downturn = False
        for bot in bots:
            if bot.downturn_protection:
                downturn = True
                break
        if (
            credits["active"] == 0
            and len(bots) != 0
            and credits["credit"] > 0
            and not downturn
        ):  # Check for inactive bots
            reminders = user.reminder
            found = False
            for reminder in reminders:
                if int(reminder["code"]) == 7:
                    found = True
                    break
            if not found:
                botsInactiveEmail(config, user)
                newEntry = {"code": 7, "ts": ts, "description": "Bots inactive"}
                reminders.append(newEntry)
                user.reminder = reminders
                user.update()

        if credits["credit"] <= 0:  # 0 Credits remaining
            for bot in bots:
                if bot.active:
                    bot.stop()

            reminders = user.reminder
            found = False
            for reminder in reminders:
                if int(reminder["code"]) == 3:
                    found = True
                    break
            if not found:
                noCreditsEmail(config, user)
                newEntry = {"code": 3, "ts": ts, "description": "Credits has run out"}
                reminders.append(newEntry)
                user.reminder = reminders
                user.update()

        elif credits["credit"] < 0.1:  # 0.1 Credits remaining
            reminders = user.reminder
            found = False
            for reminder in reminders:
                if int(reminder["code"]) == 2:
                    found = True
                    break
            if not found:
                creditsFollowUpReminderEmail(config, user)
                newEntry = {"code": 2, "ts": ts, "description": "0.1Credits remaining"}
                reminders.append(newEntry)
                user.reminder = reminders
                user.update()

        elif credits["credit"] < 0.25:  # 0.25 Credits remaining
            reminders = user.reminder
            found = False
            for reminder in reminders:
                if int(reminder["code"]) == 1:
                    found = True
                    break
            if not found:
                creditsReminderEmail(config, user)
                newEntry = {"code": 1, "ts": ts, "description": "0.25Credits remaining"}
                reminders.append(newEntry)
                user.reminder = reminders
                user.update()

        active = 0
        for bot in bots:  # Audit active bots vs active credits
            if bot.active:
                active += 1
        if credits["active"] < active:
            diff = active - credits["active"]
            for i in range(diff):
                newEntry = db.Credit(
                    [0, user.id, 0, "", 0, 0, "START", int(time.time())]
                )
                newEntry.post()
        elif credits["active"] > active:
            diff = credits["active"] - active
            for i in range(diff):
                newEntry = db.Credit(
                    [0, user.id, 0, "", 0, 0, "PAUSE", int(time.time())]
                )
                newEntry.post()


# Main Loops


@bmd_logger
def user_loop(lock, session, config=Config):
    if lock.acquire(blocking=False):
        avgTimer = 1
        try:
            startTime = time.perf_counter()

            config.loadState()
            config.updateEnv()
            config.updatePrice()
            config.saveState()
            botLoop(config)

            endTime = time.perf_counter()
            timer = endTime - startTime
            avgTimer = config.botTimer
            config.botTimer = (avgTimer + timer) / 2
            config.saveState()
            bmd_report(config)
        finally:
            lock.release()
        printLog(
            f"Bot running({trunc(timer, 3)}secs | avg {trunc(config.botTimer, 3)}secs) . . ."
        )
    else:
        printLog("Updating config, skipping bot loop", True)


@bmd_logger
def admin_loop(lock, config=Config):
    if lock.acquire(blocking=False):
        try:
            printLog("Admin Loop . . .", True)
            checkTokens()
            checkUserReminders(config)
            checkUserCredits(config)
        finally:
            lock.release()
    else:
        printLog("Updating config, skipping admin loop", True)


@bmd_logger
def update_loop(lock, session, config=Config):
    config.updateTickers(lock)

    downturnStop = 0.95
    upturnStart = 0.99

    bots = db.getBots()
    for bot in bots:
        if bot.downturn_protection:
            credits = db.getCredits(bot_id=bot.id)
            if credits["credit"] > 0:
                generalTrend = findGeneralTrend(bot.currency, config)

                if bot.active and generalTrend < downturnStop:
                    printLog(
                        f"Downturn Protection: Liquidating bot:{bot.id} for user:{bot.user_id}",
                        True,
                    )
                    bot.stop()
                    message = db.Message(
                        [
                            0,
                            bot.user_id,
                            "WARNING",
                            "Downturn Protection: Your bot has been stopped and liquidated due to significant negative trend",
                        ]
                    )
                    message.post()
                    downturnProtectionEmail(config, db.getUsers(id=bot.user_id))
                elif not bot.active and generalTrend > upturnStart:
                    printLog(
                        f"Downturn Protection: Starting bot:{bot.id} for user:{bot.user_id}",
                        True,
                    )
                    bot.start()
                    message = db.Message(
                        [
                            0,
                            bot.user_id,
                            "INFO",
                            "Downturn Protection: Your bot has been re-activated, the market is recovering",
                        ]
                    )
                    message.post()

    users = db.getUsers()
    verifiedUsers = 0
    for user in users:
        if user.verified:
            verifiedUsers += 1
    activeBots = 0
    for bot in bots:
        if bot.active:
            activeBots += 1

    reportString = f"<p>BooF Report:<p><p>Users(verified): {len(users)}({verifiedUsers}) | Bots(Active): {len(bots)}({activeBots})"
    trend = 0
    rsi = 0
    for entry in config.ZAR:
        trend += entry["trend"]
        rsi += entry["rsi"]
    trend = trend / len(config.ZAR)
    rsi = rsi / len(config.ZAR)
    generalTrend = (trend + ((rsi / 100) + 0.5)) / 2
    ZARString = f"<p>ZAR Trend: {trunc(trend, 3)} | RSI: {trunc(rsi, 1)} | General Trend: {trunc(generalTrend, 3)}</p>"
    trend = 0
    rsi = 0
    for entry in config.USDC:
        trend += entry["trend"]
        rsi += entry["rsi"]
    trend = trend / len(config.USDC)
    rsi = rsi / len(config.USDC)
    generalTrend = (trend + ((rsi / 100) + 0.5)) / 2
    USDCString = f"<p>USDC trend: {trunc(trend, 3)} | RSI: {trunc(rsi, 1)} | General Trend: {trunc(generalTrend, 3)}</p>"
    trend = 0
    rsi = 0
    for entry in config.USDT:
        trend += entry["trend"]
        rsi += entry["rsi"]
    trend = trend / len(config.USDT)
    rsi = rsi / len(config.USDT)
    generalTrend = (trend + ((rsi / 100) + 0.5)) / 2
    USDTString = f"<p>USDT trend: {trunc(trend, 3)} | RSI: {trunc(rsi, 1)} | General Trend: {trunc(generalTrend, 3)}</p>"

    logPost(f"{reportString}<br>{ZARString}{USDCString}{USDTString}", "1")



def analysis(trend:float) -> str:
    if trend > 0.98 and trend < 1.02:
        return f"a Stable trend at {trend}"
    elif trend <= 0.98 and trend > 0.95:
        return f"a Downwards trend at {trend}"
    elif trend <= 0.95:
        return f"an Extremely negative at {trend}"
    elif trend >= 1.02 and trend < 1.05:
        return f"an Upwards trend at {trend}"
    elif trend >= 1.05:
        return f"an Extremely strong trend at {trend}"


@bmd_logger
def xUpdate(config=Config):
    generalTrend = findGeneralTrend("USDT", config)

    trendList = config.USDT
    trends = []
    for entry in trendList:
        data={
            'base':entry['base'],
            'trend': (float(entry["trend"]) + ((float(entry["rsi"]) / 100) + 0.5)) / 2
        }
        trends.append(data)

    high = trends[0]
    low = trends[0]
    for entry in trends[1:]:
        if entry["trend"] > high["trend"]:
            high = entry
        if entry["trend"] < low["trend"]:
            low = entry
    low_data = f"The worst performing coin is {low["base"]} with {analysis(low["trend"])} out of 1, where 1 is neutral/horizontal"
    high_data = f"The best performing coin is {high["base"]} with {analysis(high["trend"])} out of 1, where 1 is neutral/horizontal"
    general_data = f"The general market with {analysis(generalTrend)}  out of 1, where 1 is neutral/horizontal"

    choices = [low_data, high_data, general_data]

    twitter.sendTweet(random.choice(choices))


def thread_update_loop(lock, session, config=Config):
    job_thread = threading.Thread(
        target=update_loop, args=(lock, session, config), daemon=True
    )
    job_thread.start()


externalSession = createSession(
    360
)  # For authenticated api data, ie. account data and trading

if __name__ == "__main__":
    db.setupDB()
    internalSession = createSession(
        10
    )  # For public api data, ie. ticker list and prices

    running = "running"

    config = Config()
    dataLock = threading.Lock()

    try:
        printLog("Loading config . . .", True)
        config.loadState()
        printLog("Initial state load successful . . .")
    except Exception as e:
        printLog("Failed Loading State . . .", True)
        print(e)
        config.updateEnv()
        config.saveState()
        config.updateTickers(dataLock)
        config.updatePrice()
        config.saveState()

    if running != "running":
        printLog("Running once", True)
        config.updateTickers(dataLock)

    else:  # Main operation
        config.updateTickers(dataLock)
        schedule.every(30).seconds.do(
            user_loop, lock=dataLock, session=internalSession, config=config
        )

        schedule.every(1).hours.do(admin_loop, lock=dataLock, config=config)

        schedule.every().hour.do(
            thread_update_loop, lock=dataLock, session=internalSession, config=config
        )

        #schedule.every().day.at("12:00").do(xUpdate, config=config)

        try:
            while True:
                n = schedule.idle_seconds()
                if n is None:
                    break
                elif n > 0:
                    time.sleep(n)
                try:
                    config.checkVALR(internalSession)
                    config.saveState()
                    if config.valrStatus == "online":
                        schedule.run_pending()
                    else:
                        logPost("Valr Status: Offline", "1")
                except Exception as e:
                    msg = f"Error during main runtime:\n<br>{e}"
                    logPost(msg, "2")
        except KeyboardInterrupt:
            printLog("Shutting down . . .", True)
            config.saveState()
