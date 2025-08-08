import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from requests_ratelimiter import LimiterSession
import hashlib, hmac
from dotenv import dotenv_values
import schedule, threading
import datetime, json, pickle, os, sys, traceback, time, datetime, math

import db, postmark, twitter

"""
Initialise Config and define createSession first.
"""
class sessionRetry(LimiterSession):
    def request(self, method, url, **kwargs):
        response = super().request(method, url, **kwargs)
        if response.history:
            for attempt in response.history:
                printLog(f"Retry triggered for {method} {url}: Status {attempt.status_code}", True)
        return response

def createSession(rate):
    """
    Rate per minute: public = 10, private = 360
    """
    session = sessionRetry(per_minute=rate, burst=5)
    retry_strategy = Retry(
        total=3,
        backoff_factor=1
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    return session

def trunc(value,dec):
    if isinstance(value, str):
        parts = value.split('.')
        if len(parts) != 2:
            raise ValueError("Invalid value format")
        intPart, decPart = parts
        if dec > 0:
            decPart = decPart[:dec]
            return ".".join([intPart,decPart])
        else:
            return intPart
    elif isinstance(value, float):
        a = int(value*(10**dec))
        return float(a/(10**dec))
    else:
        return value

def sorting(values):
    """
    Sorting base on minTrade
    """
    return values["minTrade"]

class Config():
    """
    updateEnv() and checkVALR to be run every loop,
    updateTickers() to be run daily,
    updatevolatility() to be ru weekly
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

    def updateEnv(self):
        """
        Updates config form the .env
        """
        config = dotenv_values(".env")
        self.appSecret = config["APP_SECRET"]
        self.forbidden = eval(config["FORBIDDEN"])
        self.stake = eval(config["STAKE"])
        self.postmarkKey = config["POSTMARK_KEY"]
        self.paypalKey = config["PAYPAL_ID"]
        self.paypalSecret = config["PAYPAL_SECRET"]
        self.verifySalt = config["VERIFY_SALT"]
    
    def updateTickers(self, lock, session):
        """
        Updates the ticker list, including ticker trend, per quote currency.
        Checks downturn protection rules.
        Ticker format={
            "base": {baseCurrency},
            "price": {markPrice},
            "decimal": {baseDesimalPlaces},
            "minTrade": {minValue},
            "trend": {trend},
            "rsi": {rsi}
            "volatility": {beta},
            "atr": {atr%}
        }
        """
        printLog("Updating tickers . . .", True)
        try:
            ZARlist = []
            ZARBars = []
            USDTlist = []
            USDTBars = []
            USDClist = []
            USDCBars = []
            url = "https://api.valr.com/v1/public/ordertypes?includeInactivePairs=false"
            result = session.get(url).json()

            url = "https://api.valr.com/v1/public/pairs"
            detailsResult = session.get(url).json()

            #Creating Lists for quote currencies

            for line in result:
                details = None
                if "MARKET" in line["orderTypes"]:
                    for entry in detailsResult:
                        if entry["symbol"] == line["currencyPair"]:
                            details = entry
                            break

                if details and not any(forbidden in details["baseCurrency"] for forbidden in self.forbidden) and details["currencyPairType"] == "SPOT" and details["quoteCurrency"] in ["ZAR","USDC","USDT"]:
                    summaryULR = f"https://api.valr.com/v1/public/{details["symbol"]}/marketsummary"
                    summaryResult = session.get(summaryULR).json()
                    tickerDetails = {}
                    tickerBars = {"ticker":details["baseCurrency"],
                        "bars":[]
                        }
                    if "markPrice" not in summaryResult:
                        printLog(f"Market Summary error for {details["symbol"]}", True)
                        print(json.dumps(summaryResult, indent=4))
                        print("Not added to ticker list")
                    else:
                        minValue = float(summaryResult["markPrice"])*float(details["minBaseAmount"])
                        if minValue < float(details["minQuoteAmount"]):
                            minValue = float(details["minQuoteAmount"])
                        tickerTrend = {
                            "trend":1,
                            "rsi":50,
                            "atr":0,
                            "bars":[]
                        }
                        try:
                            tickerTrend = findTrend(session,details["symbol"])
                        except Exception as e:
                            print(f"Exception find trend for {details["symbol"]}: {e}")
                        tickerDetails = {
                            "base": details["baseCurrency"],
                            "price": float(summaryResult["markPrice"]),
                            "decimal": int(details["baseDecimalPlaces"]),
                            "minTrade": minValue,
                            "trend": tickerTrend["trend"],
                            "rsi": tickerTrend["rsi"],
                            "volatility": 1,
                            "atr": tickerTrend["atr"]
                        }
                        tickerBars["bars"] = tickerTrend["bars"]
                    if details["quoteCurrency"] == "ZAR":
                        ZARlist.append(tickerDetails)
                        ZARBars.append(tickerBars)
                    elif details["quoteCurrency"] == "USDT":
                        USDTlist.append(tickerDetails)
                        USDTBars.append(tickerBars)
                    elif details["quoteCurrency"] == "USDC":
                        USDClist.append(tickerDetails)
                        USDCBars.append(tickerBars)


            for key, value in enumerate(ZARlist):
                ZARlist[key]["volatility"] = beta(session, value["base"], "ZAR", ZARBars)
            ZARlist.sort(key=sorting)
                    
            for key, value in enumerate(USDClist):
                USDClist[key]["volatility"] = beta(session, value["base"], "USDC", USDCBars)
            USDClist.sort(key=sorting)

            for key, value in enumerate(USDTlist):
                USDTlist[key]["volatility"] = beta(session, value["base"], "USDT", USDTBars)
            USDTlist.sort(key=sorting)
            
            with lock:
                printLog("Locking config, updating . . .", True)
                self.ZAR.clear()
                self.ZAR.extend(ZARlist)
                self.USDC.clear()
                self.USDC.extend(USDClist)
                self.USDT.clear()
                self.USDT.extend(USDTlist)
                self.saveState()

        except Exception as e:
            printLog(e,True)
            logPost(f"During Update Tickers:{e}",'2')
         
    def updatePrice(self, session):
        url = "https://api.valr.com/v1/public/marketsummary"
        detailsResult = session.get(url).json()
        for key, entry in enumerate(self.ZAR):
            for line in detailsResult:
                if line["currencyPair"] == f"{self.ZAR[key]["base"]}ZAR":
                    self.ZAR[key]["price"] = line["markPrice"]
                    break
        
        for key, entry in enumerate(self.USDC):
            for line in detailsResult:
                if line["currencyPair"] == f"{self.USDC[key]["base"]}USDC":
                    self.USDC[key]["price"] = line["markPrice"]
                    break
                
        for key, entry in enumerate(self.USDT):
            for line in detailsResult:
                if line["currencyPair"] == f"{self.USDT[key]["base"]}USDT":
                    self.USDT[key]["price"] = line["markPrice"]
                    break
                
        for line in detailsResult:
            if line["currencyPair"] == "USDCZAR":
                self.USDCZAR = float(line["markPrice"])
            if line["currencyPair"] == "USDTZAR":
                self.USDTZAR = float(line["markPrice"])

    def checkVALR(self, session):
        url = 'https://api.valr.com/v1/public/status'
        try:
            result = session.get(url).json()
            if result["status"] == "online":
                self.valrStatus = "online"
            else:
                self.valrStatus = "suspended"
        except Exception as e:
            self.valrStatus = "suspended"
            logPost(f"VALR server status: {e}",'2')

    def saveState(self):
        with open(self.STATE_FILE, 'wb') as file:
            pickle.dump({
                "valrStatus":self.valrStatus,
                "appSecret":self.appSecret,
                "verifySalt":self.verifySalt,
                "ZAR":self.ZAR,
                "USDC":self.USDC,
                "USDT":self.USDT,
                "USDCZAR":self.USDCZAR,
                "USDTZAR":self.USDTZAR,
                "forbidden":self.forbidden,
                "stake":self.stake,
                "postmarkKey":self.postmarkKey,
                "paypalKey":self.paypalKey,
                "paypalSecret":self.paypalSecret,
                "botTimer":self.botTimer

            }, file)

    def loadState(self):
        if os.path.exists(self.STATE_FILE):
            with open(self.STATE_FILE, 'rb') as file:
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

def validateKeys(key,secret,user_id):
    ts = int(time.time())*1000

    verb = "GET"
    path = "/v1/account/api-keys/current"
    body = ""
    url=f"https://api.valr.com{path}{body}"
    sign = getSign(secret,ts,verb,path,body)
    payload = {}
    headers = {
        'X-VALR-API-KEY': key,
        'X-VALR-SIGNATURE': str(sign),
        'X-VALR-TIMESTAMP': str(ts),
    }
    response = externalSession.get(url, headers=headers, data=payload)
    
    if response.status_code != 200:
        message = db.Message([0, user_id, "ERROR", "Invalid API Key and Secret"])
        message.post()
        return False
    jsonResponse = response.json()
    if not "View access" in jsonResponse["permissions"] or not "Trade" in jsonResponse["permissions"]:
        message = db.Message([0, user_id, "ERROR", "API Key does not allow View and Trade access"])
        message.post()
        return False
    if not jsonResponse["isSubAccount"]:
        message = db.Message([0, user_id, "WARNING", "Security Risk: API Key for main account used."])
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
        for ticker in currencyList:
            if ticker["base"] == account.base:
                decimal = ticker["decimal"]
                break
        result = trade("SELL",account.base,bot.currency,bot.key,bot.secret,account.volume,decimal)
        if result:
            transaction = db.Transaction([0,bot.id,"SELL",result["volume"],result["value"],account.base,bot.currency,int(time.time()), result["fee"]])
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
        stringAmount = f"{float(trunc(amount,decimal)):.{decimal}f}" #Scientific suppressed string from truncated amount
        if direction == "BUY":
            payload = {
                "side": "BUY",
                "quoteAmount": stringAmount,
                "pair": f"{base}{quote}"
            }
        elif direction == "SELL":
            payload = {
                "side": "SELL",
                "baseAmount": stringAmount,
                "pair": f"{base}{quote}"
            }
        ts = int(time.time()*1000)
        verb = "POST"
        path = "/v2/orders/market"
        url=f"https://api.valr.com{path}"
        sign = getSign(secret,ts,verb,path,json.dumps(payload))
        headers = {
            "Content-Type": "application/json",
            'X-VALR-API-KEY': key,
            'X-VALR-SIGNATURE': sign,
            'X-VALR-TIMESTAMP': str(ts),
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
                loop+=1
                if loop > 5: #If order cannot be filled in 5 secs, cancel
                    ts = int(time.time()*1000)
                    verb = "DELETE"
                    path = "/v2/orders/order"
                    body = {
                        "orderId":id,
                        "pair":f"{base}{quote}"
                    }
                    url=f"https://api.valr.com{path}"
                    sign = getSign(secret,ts,verb,path,json.dumps(body))
                    headers = {
                        "Content-Type": "application/json",
                        'X-VALR-API-KEY': key,
                        'X-VALR-SIGNATURE': str(sign),
                        'X-VALR-TIMESTAMP': str(ts),
                    }
                    response = externalSession.delete(url, headers=headers, json=body)
                    raise ValueError("Trade Cancelled")

                #Checking if filled
                ts = int(time.time()*1000)
                verb = "GET"
                path = f"/v1/orders/{base}{quote}/orderid/{id}"
                body = ""
                url=f"https://api.valr.com{path}"
                sign = getSign(secret,ts,verb,path)
                headers = {
                    'X-VALR-API-KEY': key,
                    'X-VALR-SIGNATURE': str(sign),
                    'X-VALR-TIMESTAMP': str(ts),
                }
                response = externalSession.get(url, headers=headers)
                response.raise_for_status()
                jsonResponse = response.json()
                if jsonResponse["orderStatusType"] == "Filled": #Finding details
                    ts = int(time.time()*1000)
                    verb = "GET"
                    path = f"/v1/orders/history/summary/orderid/{id}"
                    body = ""
                    url=f"https://api.valr.com{path}"
                    sign = getSign(secret,ts,verb,path)
                    headers = {
                        'X-VALR-API-KEY': key,
                        'X-VALR-SIGNATURE': str(sign),
                        'X-VALR-TIMESTAMP': str(ts),
                    }
                    response = externalSession.get(url, headers=headers)
                    response.raise_for_status()
                    jsonResponse = response.json()
                    if direction == "BUY":
                        price = float(jsonResponse["total"])/float(jsonResponse["totalExecutedQuantity"])
                        details={
                            "volume":float(jsonResponse["totalExecutedQuantity"]),
                            "value":float(jsonResponse["total"]),
                            "fee":float(jsonResponse["totalFee"])*price
                        }
                    elif direction == "SELL":
                        details={
                            "volume":float(jsonResponse["totalExecutedQuantity"]),
                            "value":float(jsonResponse["total"]),
                            "fee":float(jsonResponse["totalFee"])
                        }
                    return details
                time.sleep(1)
            except Exception as e:
                logPost(f"During order check: {base}{quote} {direction} ~ {e}",'2')
                return details
    except Exception as e:
        logPost(f"During Trade: {base}{quote} {direction} ~ {e}",'2')
        return None


def findTrend(session, pair):
    answer={
        "trend":1,
        "rsi":50,
        "atr":0,
        "bars":[]
    }
    url = f"https://api.valr.com/v1/public/{pair}/markprice/buckets?periodSeconds=86400"
    shortUrl = f"https://api.valr.com/v1/public/{pair}/markprice/buckets?periodSeconds=21600"
    result = session.get(url)
    if result.status_code == 200:
        result = result.json()
        if len(result) > 61:
            shortTerm = 0
            longTerm = 0
            shortResult = session.get(shortUrl)
            if shortResult.status_code == 200:
                shortResult = shortResult.json()
                shortResult = shortResult[:56]
            else:
                print(shortResult.content)
                shortResult = result[:14]

            for line in result:
                answer["bars"].append(float(line["close"]))

            shortTerm = float(shortResult[-1]["close"])
            for line in reversed(shortResult):
                shortTerm = ((shortTerm*3) + float(line["close"]))/4

            #smaShort = 0
            #for line in shortResult:
            #    smaShort += float(line["close"])
            #smaShort = smaShort/len(shortResult)
            #smaLong = 0
            #for line in result[:60]:
            #    smaLong += float(line["close"])
            #smaLong = smaLong/len(result[:60])
            #smaTrend = smaShort/smaLong
            
            longTerm = float(result[61]["close"])
            for line in reversed(result[:60]):
                longTerm = ((longTerm*6) + float(line["close"]))/7
            answer["trend"]=trunc((shortTerm/longTerm),4)

            #wmaTrend = shortTerm/longTerm
            #print(f"{pair}~ smaTrend:{smaTrend} | wmaTrend:{wmaTrend}")

            up = 0
            down = 0
            for key, line in enumerate(shortResult):
                if len(shortResult) != (key+1):
                    change = float(shortResult[key]["close"]) - float(shortResult[key+1]["close"])
                    if change > 0:
                        up += change
                    else:
                        down += abs(change)
            up = up/(len(shortResult)-1)
            down = down/(len(shortResult)-1)
            answer["rsi"] = trunc(100-(100/(1+(up/down))),3)

            atr = 0
            for key, line in enumerate(result[1:]):
                tr = max((float(line['high'])-float(line['low'])), abs(float(line['high'])-float(result[key-1]['close'])), abs(float(line['low'])-float(result[key-1]['close'])))
                atr += tr
            atr = ((atr/(len(result)-1))/longTerm)
            answer['atr'] = trunc(atr,4)


            return answer
        elif len(result)>16:
            printLog(f"Bucket List for {pair} not sufficient for trend", True)
            print(f"\tBucket size:{len(result)}")
            shortResult = session.get(shortUrl)
            if shortResult.status_code == 200:
                shortResult = shortResult.json()
            else:
                print(shortResult.content)
                shortResult = result[:14]
                print("Short list not found")
            up = 0
            down = 0
            for key, line in enumerate(shortResult):
                if len(shortResult) != (key+1):
                    change = float(shortResult[key]["close"]) - float(shortResult[key+1]["close"])
                    if change > 0:
                        up += change
                    else:
                        down += abs(change)
            up = up/(len(shortResult)-1)
            down = down/(len(shortResult)-1)
            answer["rsi"] = trunc(100-(100/(1+(up/down))),2)
            return answer
        else:
            printLog(f"Bucket List for {pair} not sufficient", True)
            print(f"\tBucket size:{len(result)}")
            return answer
    else:
        printLog(result.reason, True)
        print(result.content)
        return answer

def findGeneralTrend(currency, config=Config):
    currencyList = []
    if currency == "ZAR":
        currencyList = config.ZAR
    elif currency == "USDC":
        currencyList = config.USDC
    elif currency == "USDT":
        currencyList = config.USDT
        
    marketTrend = 0
    marketRSI = 0
    for entry in currencyList:
        marketTrend += entry["trend"]
        marketRSI += entry["rsi"]
    trend = marketTrend/len(currencyList)
    rsi = marketRSI/len(currencyList)
    return (trend+((rsi/100)+.5))/2


def beta(session, base, quote, bars):
    """
    Calculate Beta for given base in quote group
    """
    returns = []
    for ticker in bars:
        if ticker["ticker"] == base and not ticker["bars"]:
            printLog(f"Beta for {ticker["ticker"]} set to 1", True)
            return 1    #Ends Beta calcs if ticker does not have bars

        if ticker["bars"]:
            printLog(f"Calculating Beta for {ticker["ticker"]}")
            returnList = []
            for key, value in enumerate(ticker["bars"]):
                if key != 0:
                    dailyReturn = (ticker["bars"][key-1] - ticker["bars"][key])/ticker["bars"][key]
                    returnList.append(dailyReturn)
            avgReturns = sum(returnList)/len(returnList)
            details = {
                "ticker": ticker["ticker"],
                "avgReturn": avgReturns,
                "returns": returnList
            }
            returns.append(details)
    
    indexReturns = []
    days = 100
    for ticker in returns:
        if len(ticker["returns"]) < days:
            days = len(ticker["returns"])

    for day in range(days):
        avgReturns = 0
        for ticker in returns:
            avgReturns += ticker["returns"][day]
        avgReturns = avgReturns/len(returns)
        indexReturns.append(avgReturns)

    #Finding variance for index
    variance = 0
    indexAvg = sum(indexReturns)/len(indexReturns)
    for day in range(days):
        variance += (indexReturns[day]-indexAvg)**2
    variance = variance/days

    #finding covariance
    beta = 0
    found = False
    for ticker in returns:
        if ticker["ticker"] == base:
            covariance = 0
            for day in range(days):
                covariance += (ticker["returns"][day] - ticker["avgReturn"])*(indexReturns[day]-indexAvg)
            covariance = covariance/days
            beta = trunc((covariance/variance),2)
            found = True
            break
    return beta

def logPost(snippet, code='2'):
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

    payload = {
        "code": code,
        "app": "BooF",
        "snippet": msg
    }
    try:
        result = requests.post("https://www.bmd-studios.com/log", json=payload)
        if result.status_code != 200:
            printLog("Logging Error", True)
            print("Status code: "+str(result.status_code))
            print(result.text)
            print('Original exception: ')
            print(snippet)
    except Exception as c:
        printLog("Logging server down . . .", True)
        print(str(c))
        print('Original exception: ')
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

            printLog("Exception raised during "+function.__name__, True)
            exc_type, exc_value, exc_traceback = sys.exc_info()
            message = traceback.extract_tb(exc_traceback)
            post_message = f"{printDate} ~ Exception raised during "+function.__name__+'\n<br>'
            post_message += str(exc_type.__str__)+'\n'
            for line in message.format():
                post_message += line+'<br>'
            post_message += str(exc_value)
            logPost(post_message, '2')
            
    return exception_handler

def bmd_report(config=Config):
    printLog("Reporting Status . . .")
    url = 'https://www.bmd-studios.com/bot'
    headers = {
        "accept": "application/json"
    }
    payload={
        "id":"01",
        "bot_name":"BooF",
        "ts":str(int(time.time())),
        "status":f"{config.valrStatus}({trunc(config.botTimer,2)})"
    }
    result = requests.post(url=url,json=payload)
    result.raise_for_status()

def printLog(message, log=False):
    currentTS = int(time.time())
    date = datetime.datetime.fromtimestamp(currentTS)
    dateFormat = "%d %b, %Y, %H:%M:%S"
    printDate = date.strftime(dateFormat)
    print(" "*100, end="\r", flush=True)
    if log:
        print(f"{printDate} ~ {message}", flush=True)
    else:
        print(f"{printDate} ~ {message}", end="\r", flush=True) 

#User Functions


def botLoop(config = Config):
    #Check User Credit
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
                message = db.Message([0,user.id,"WARNING","All Bots paused, insufficient Credit"])
                message.post()
                message = db.Message([0,user.id,"INFO","Buy more credit before restarting you bots"])
                message.post()
                for bot in bots:
                    if bot.user_id == user.id:
                        bot.stop()

    #Sell irrelevant accounts and confirm balances
    printLog("Checking bot balances . . .")
    for bot in bots:
        checkBalances(config, bot)

    #Find total equity
    printLog("Finding bot equity . . .")
    for bot in bots:
        findEquity(config, bot)
               
    #Find Bot Balance nr and value
    printLog("Finding balance value . . .")
    for bot in bots:
        if bot.active:
            findBalance(config, bot)

    #Buy new accounts
    printLog("Setting Active Accounts . . .")
    for bot in bots:
        if bot.active:
            setAccounts(config, bot)

    #Rebalance Active Account
    printLog("Balancing Bots . . .")
    for bot in bots:
        if bot.active:
            balanceBots(config, bot)

    #Check Staking
    printLog("Setting Stake . . .")
    for bot in bots:
        if bot.active:
            setStake(config, bot)

   
def getSign(secret,ts,verb,path,body=""):   #Oauth signing
    payload = "{}{}{}{}".format(ts,verb.upper(),path,body)
    message = bytearray(payload,'utf-8')
    signature = hmac.new(bytearray(secret,'utf-8'),message,digestmod=hashlib.sha512).hexdigest()
    return signature

def checkBalances(config = Config, bot = db.Bot):
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
        jsonResponse = None
        repeat = True

        while repeat:
            ts = int(time.time())*1000
            verb = "GET"
            path = "/v1/account/balances"
            body = "?excludeZeroBalances=true"
            url=f"https://api.valr.com{path}{body}"
            sign = getSign(bot.secret,ts,verb,path,body)
            payload = {}
            headers = {
                'X-VALR-API-KEY': bot.key,
                'X-VALR-SIGNATURE': str(sign),
                'X-VALR-TIMESTAMP': str(ts),
            }
            response = externalSession.get(url, headers=headers, data=payload)
            if response.status_code != 200:
                raise ValueError(response.json()["message"])
            jsonResponse = response.json()

            repeat = False

            for entry in jsonResponse:
                found = False
                if entry["currency"] == bot.currency: #Check for bot currency
                    if float(entry["available"]) != bot.quote_balance:
                        bot.quote_balance = float(entry["available"])
                        bot.update()
                    continue

                for account in accounts:    #Check Accounts
                    if account.base == entry["currency"]:
                        found = True
                        if downturnProtection: #Liquidate for downturn protection
                            stake = 0
                            if account.base in config.stake:    #Unstake for liquidation
                                stake = updateStake(bot.key, bot.secret, entry["currency"])
                                if stake != 0:
                                    try:
                                        ts = int(time.time()*1000)
                                        payload = {}
                                        verb = "POST"
                                        path = "/v1/staking/un-stake"
                                        body = {
                                            "currencySymbol":account.base,
                                            "amount":f"{stake}"
                                        }
                                        url=f"https://api.valr.com{path}"
                                        sign = getSign(bot.secret,ts,verb,path,json.dumps(body))
                                        headers = {
                                            'Content-Type': 'application/json',
                                            'X-VALR-API-KEY': bot.key,
                                            'X-VALR-SIGNATURE': str(sign),
                                            'X-VALR-TIMESTAMP': str(ts),
                                        }
                                        response = externalSession.post(url, headers=headers, json=body)
                                        if response.status_code != 202:
                                            msg = f"Error during closing stake: \n<br>{response.reason}<br>{response.content}"
                                            logPost(msg, '2')
                                            stake = 0
                                    except Exception as e:
                                        logPost(f"Error during closing stake: {e}")
                                        stake = 0

                            currencyList = []
                            if bot.currency == "ZAR":
                                currencyList = config.ZAR
                            elif bot.currency == "USDC":
                                currencyList = config.USDC
                            elif bot.currency == "USDT":
                                currencyList = config.USDT

                            decimal = 0
                            price = 0
                            minValue = 0
                            for ticker in currencyList:
                                if ticker["base"] == account.base:
                                    decimal = int(ticker["decimal"])
                                    price = float(ticker["price"])
                                    minValue = float(ticker["minTrade"])
                                    break

                            volume = float(entry["available"])+stake  
                            if volume*price > minValue:
                                result = trade("SELL", bot.currency, account.base, bot.key, bot.secret, volume, decimal)
                                if result:
                                    transaction = db.Transaction([0,bot.id,"WITHDRAW",result["volume"],result["value"],account.base,bot.currency,int(time.time()), result["fee"]])
                                    transaction.post()
                            if account.swing != 0:
                                account.swing = 0
                                account.update()

                        if account.volume != (float(entry["available"])+account.stake):
                            if account.base in config.stake:
                                account.stake = updateStake(bot.key, bot.secret, account.base)
                            account.volume = float(entry["available"])+account.stake
                            account.update()
                        break

                if downturnProtection:
                    continue

                if not found and entry["currency"] in ["ZAR","USDC","USDT"]: #Fix Quote Currencies
                    if bot.currency == "ZAR":
                        if entry["currency"] == "ZAR":
                            found = True
                        elif entry["currency"] == "USDC":
                            found = True
                            if float(entry["total"]) > 1: #Min base amount
                                result = trade("SELL", "ZAR", "USDC", bot.key, bot.secret, entry["available"])
                                if result:
                                    transaction = db.Transaction([0,bot.id,"WITHDRAW",result["volume"],result["value"],"USDC","ZAR",int(time.time()), result["fee"]])
                                    transaction.post()
                        elif entry["currency"] == "USDT":
                            found = True
                            if float(entry["total"]) > 1: #Min base amount
                                result = trade("SELL", "ZAR", "USDT", bot.key, bot.secret, entry["available"])
                                if result:
                                    transaction = db.Transaction([0,bot.id,"WITHDRAW",result["volume"],result["value"],"USDT","ZAR",int(time.time()), result["fee"]])
                                    transaction.post()
                    elif bot.currency == "USDC":
                        if entry["currency"] == "USDC":
                            found = True
                        elif entry["currency"] == "ZAR":
                            found = True
                            if float(entry["total"]) > 10: #Min quote amount
                                result = trade("BUY", "ZAR", "USDC", bot.key, bot.secret, entry["available"])
                                if result:
                                    transaction = db.Transaction([0,bot.id,"INVEST",result["volume"],result["value"],"USDC","ZAR",int(time.time()), result["fee"]])
                                    transaction.post()
                        elif entry["currency"] == "USDT":
                            found = True
                            if float(entry["total"]) > 1: #Min base amount
                                result = trade("SELL", "USDC", "USDT", bot.key, bot.secret, entry["available"])
                                if result:
                                    transaction = db.Transaction([0,bot.id,"WITHDRAW",result["volume"],result["value"],"USDT","USDC",int(time.time()), result["fee"]])
                                    transaction.post()
                    elif bot.currency == "USDT":
                        if entry["currency"] == "USDT":
                            found = True
                        elif entry["currency"] == "USDC":
                            found = True
                            if float(entry["total"]) > 1: #Min quote amount
                                result = trade("BUY", "USDC", "USDT", bot.key, bot.secret, entry["available"])
                                if result:
                                    transaction = db.Transaction([0,bot.id,"INVEST",result["volume"],result["value"],"USDT","USDC",int(time.time()), result["fee"]])
                                    transaction.post()
                        elif entry["currency"] == "ZAR":
                            found = True
                            if float(entry["total"]) > 10: #Min quote amount
                                result = trade("BUY", "ZAR", "USDT", bot.key, bot.secret, entry["available"])
                                if result:
                                    transaction = db.Transaction([0,bot.id,"INVEST",result["volume"],result["value"],"USDT","ZAR",int(time.time()), result["fee"]])
                                    transaction.post()
                else:
                    continue

                if not found and entry["currency"] in config.stake: #Unstake tickers not on account
                    stake = updateStake(bot.key, bot.secret, entry["currency"])
                    if stake != 0:
                        try:
                            ts = int(time.time()*1000)
                            payload = {}
                            verb = "POST"
                            path = "/v1/staking/un-stake"
                            body = {
                                "currencySymbol":entry["currency"],
                                "amount":f"{stake}"
                            }
                            url=f"https://api.valr.com{path}"
                            sign = getSign(bot.secret,ts,verb,path,json.dumps(body))
                            headers = {
                                'Content-Type': 'application/json',
                                'X-VALR-API-KEY': bot.key,
                                'X-VALR-SIGNATURE': str(sign),
                                'X-VALR-TIMESTAMP': str(ts),
                            }
                            response = externalSession.post(url, headers=headers, json=body)
                            if response.status_code != 202:
                                msg = f"Error during closing stake: \n<br>{response.reason}<br>{response.content}"
                                logPost(msg, '2')
                        except Exception as e:
                            logPost(f"Error during closing stake: {e}")


                #Sell tickers not on account

                if not found: #Sell for ZAR tickers
                    for ticker in config.ZAR:
                        if ticker["base"] == entry["currency"]:
                            found = True
                            if float(entry["total"])*float(ticker["price"]) > ticker["minTrade"]:
                                repeat = True
                                result = trade("SELL", "ZAR", entry["currency"], bot.key, bot.secret, entry["available"], ticker["decimal"])
                                if result:
                                    transaction = db.Transaction([0,bot.id,"WITHDRAW",result["volume"],result["value"],entry["currency"],"ZAR",int(time.time()), result["fee"]])
                                    transaction.post()
                            break
                else:
                    continue
                
                                
                if not found: #Sell for USDC tickers
                    for ticker in config.USDC:
                        if ticker["base"] == entry["currency"]:
                            found = True
                            if float(entry["total"])*ticker["price"] > float(ticker["minTrade"]):
                                repeat = True
                                result = trade("SELL", "USDC", entry["currency"], bot.key, bot.secret, entry["available"], ticker["decimal"])
                                if result:
                                    transaction = db.Transaction([0,bot.id,"WITHDRAW",result["volume"],result["value"],entry["currency"],"USDC",int(time.time()), result["fee"]])
                                    transaction.post()
                            break
                else:
                    continue
                    
            
                if not found: #SELL for USDT tickers
                    for ticker in config.USDT:
                        if ticker["base"] == entry["currency"]:
                            found = True
                            if float(entry["total"])*float(ticker["price"]) > float(ticker["minTrade"]):
                                repeat = True
                                result = trade("SELL", "USDT", entry["currency"], bot.key, bot.secret, entry["available"], int(ticker['decimal']))
                                if result:
                                    transaction = db.Transaction([0,bot.id,"WITHDRAW",result["volume"],result["value"],entry["currency"],"USDT",int(time.time()), result["fee"]])
                                    transaction.post()
                            break
                else:
                    continue


        for account in accounts:
            found = False
            for entry in jsonResponse:
                if account.base == entry["currency"]:
                    found = True
            if not found and account.volume != 0:
                account.volume = 0
                account.update()

    except Exception as e:
        logPost(f"During checkBalances: {e}",'2')
                        
def liquidateBot(config=Config, bot=db.Bot):
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
        for entry in currencyList:
            if entry["base"] == account.base:
                decimal = int(entry["decimal"])
                minTrade = float(entry["minTrade"])
                break
        if account.volume > minTrade:
            result = trade("SELL",bot.currency,account.base,bot.key,bot.secret,account.volume,decimal)
            if result:
                transaction = db.Transaction([0,bot.id,"WITHDRAW",result["volume"],result["value"],account.base,bot.currency,int(time.time()), result["fee"]])
                transaction.post()
                account.volume -= result["volume"]
                account.update()
                bot.quote_balance += result["value"]
                bot.update()
    

def findEquity(config = Config, bot = db.Bot):
    """
    Total recorded equity for bot
    """
    total = 0
    accountns = db.getActiveAccounts(bot_id=bot.id)
    total += bot.quote_balance
    for account in accountns:
        total += account.volume*account.price(config)
    if total != bot.equity:
        bot.equity = total
        bot.update()

def findBalance(config = Config, bot = db.Bot):
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
            
        shares = balance_nr+((balance_nr)*bot.margin)
        minTradeLow = (currencyList[balance_nr-1]["minTrade"]+currencyList[balance_nr-2]["minTrade"])/2
        if balance_nr < 2:
            minTradeLow = currencyList[balance_nr-1]["minTrade"]
        equityLow = ((minTradeLow*2)/bot.margin)*shares
        if bot.equity < equityLow and balance_nr == 1:  #Bot equity very low
            bot.margin += 0.01
            if bot.margin == 0.16:
                bot.margin = 0.15
                bot.active = False
                bot.update()
                message = db.Message([0, bot.user_id, "ERROR", "Insufficient equity, pausing bot."])
                message.post()
                break
            else:
                bot.update()
                message = db.Message([0, bot.user_id, "WARNING", f"Insufficient equity, increasing margin({(bot.margin*100):0f}) to maintain normal operations."])
                message.post()
        elif bot.equity < equityLow:    #Bot equity low
            balance_nr -= 1
        elif balance_nr < len(currencyList): #Bot equity high
            shares = balance_nr+((balance_nr+1)*bot.margin)
            minTradeHigh = (currencyList[balance_nr]["minTrade"]+currencyList[balance_nr-1]["minTrade"])/2
            equityHigh = ((minTradeHigh*2.5)/bot.margin)*shares
            if bot.equity > equityHigh:
                balance_nr += 1
            else:   #Balance number found
                break
        else:   #Equity above balance number adjustments, max balance number
            balance_nr = len(currencyList)
            break
        
    if balance_nr != bot.balance_nr:
        bot.balance_nr = balance_nr
        bot.update()

    shares = balance_nr+(balance_nr*bot.margin)
    balance_value = bot.equity / shares

    if bot.balance_value != balance_value:
        bot.balance_value = balance_value
        bot.update()

def setAccounts(config = Config, bot = db.Bot):
    currencyList = []
    if bot.currency == "ZAR":
        currencyList = config.ZAR
    elif bot.currency == "USDC":
        currencyList = config.USDC
    elif bot.currency == "USDT":
        currencyList = config.USDT

    accounts = db.getActiveAccounts(bot_id=bot.id)
    if len(accounts) > bot.balance_nr:  #Close old account
        accountNrs = len(accounts)
        difference = accountNrs - bot.balance_nr

        for entry in reversed(currencyList):
            for account in accounts:
                if account.base == entry["base"]:
                    result = trade("SELL", bot.currency, account.base, bot.key, bot.secret, account.volume, entry["decimal"])
                    if result:
                        transaction = db.Transaction([0,bot.id,"WITHDRAW",result["volume"],result["value"],account.base,bot.currency,int(time.time()), result["fee"]])
                        transaction.post()
                        bot.equity += result["value"]
                        bot.update()
                    account.delete()
                    difference -= 1
                    break
            if difference <= 0:
                break


    elif len(accounts) < bot.balance_nr:    #Open new account, relative to config.'TICKERS' order
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
                                result = trade("SELL", bot.currency, account.base, bot.key, bot.secret, amount, currency["decimal"])
                                if result:
                                    transaction = db.Transaction([0,bot.id,"WITHDRAW",result["volume"],result["value"],newAccount["base"],bot.currency,int(time.time()), result["fee"]])
                                    transaction.post()
                                    bot.equity += result["value"]
                                    bot.update()

                result = trade("BUY", bot.currency, newAccount["base"], bot.key, bot.secret, bot.balance_value)
                if result:
                    transaction = db.Transaction([0,bot.id,"INVEST",result["volume"],result["value"],newAccount["base"],bot.currency,int(time.time()), result["fee"]])
                    transaction.post()
                    bot.equity -= result["value"]
                    bot.update()
                    newAAccount = db.ActiveAccount([0, bot.id, newAccount["base"], result["volume"], 0, 0, ""])
                    newAAccount.post()
                    accounts.append(newAAccount)
                else:
                    newAAccount = db.ActiveAccount([0, bot.id, newAccount["base"], 0, 0, 0, ""])
                    newAAccount.post()
                    accounts.append(newAAccount)
                difference -= 1
            if difference <= 0:
                break
                      
def balanceBots(config = Config, bot = db.Bot):
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
            marketATR += entry['atr']
    marketATR = marketATR/len(currencyList)

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
        
        volatility = abs(currencyDetails["volatility"]) if currencyDetails["volatility"] > 0.1 else 0.1
        atr = currencyDetails['atr'] if currencyDetails["atr"]!=0 else bot.margin
        margin = max(min(((bot.margin*2)+(bot.margin*volatility)+atr)/4,bot.margin*1.2),bot.margin*0.8) if bot.dynamic_margin else bot.margin

        weight = max(0.8, min(1, generalTrend)) #Adjustment capped at 80% on downtrend
        balanceValue = bot.balance_value * weight if bot.refined_weight else bot.balance_value

        if value > balanceValue:
            difference = (value - balanceValue)/balanceValue
            if account.direction != "UP":
                account.direction = "UP"
                account.update()
            if difference > margin*5:
                sellVolume = (value-balanceValue)/price
                result = trade("SELL", bot.currency, account.base, bot.key, bot.secret, sellVolume, decimal)
                if result:
                    transaction = db.Transaction([0,bot.id,"WITHDRAW",result["volume"],result["value"],account.base,bot.currency,int(time.time()), result["fee"]])
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
            elif difference < (account.swing*(1-((account.swing+margin)/2))) and difference > margin:
                printLog(f"Selling {account.base} Swing:{difference}/Margin:{margin}",True)
                sellVolume = (value-balanceValue)/price
                result = trade("SELL", bot.currency, account.base, bot.key, bot.secret, sellVolume, decimal)
                if result:
                    transaction = db.Transaction([0,bot.id,"SELL",result["volume"],result["value"],account.base,bot.currency,int(time.time()), result["fee"]])
                    transaction.post()
                    account.volume -= result["volume"]
                    account.update()
                    bot.quote_balance += result["value"]
                    bot.update()

        elif value < balanceValue:
            difference = (balanceValue - value)/balanceValue
            if account.direction != "DOWN":
                account.direction = "DOWN"
                account.update()
            if difference > margin*5:
                buyValue = balanceValue-value
                if buyValue < bot.quote_balance:
                    result = trade("BUY", bot.currency, account.base, bot.key, bot.secret, buyValue, decimal)
                    if result:
                        transaction = db.Transaction([0,bot.id,"INVEST",result["volume"],result["value"],account.base,bot.currency,int(time.time()), result["fee"]])
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
            elif difference < (account.swing*(1-((account.swing+margin)/2))) and difference > margin:
                printLog(f"Buying {account.base} Swing:{difference}/Margin:{margin}",True)
                buyValue = balanceValue-value
                result = trade("BUY", bot.currency, account.base, bot.key, bot.secret, buyValue, decimal)
                if result:
                    transaction = db.Transaction([0,bot.id,"BUY",result["volume"],result["value"],account.base,bot.currency,int(time.time()), result["fee"]])
                    transaction.post()
                    account.volume += result["volume"]
                    account.update()
                    bot.quote_balance -= result["value"]
                    bot.update()

def setStake(config = Config, bot = db.Bot):
    accounts = db.getActiveAccounts(bot_id = bot.id)
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
            if account.stake < account.volume*.7 or account.stake > account.volume*.8:
                if account.stake < account.volume*.7:
                    diff = trunc(((account.volume*0.75) - account.stake),decimal)
                    try:
                        ts = int(time.time()*1000)
                        payload = {}
                        verb = "POST"
                        path = "/v1/staking/stake"
                        body = {
                            "currencySymbol":account.base,
                            "amount": trunc(f"{diff:.{decimal+1}f}",decimal)
                        }
                        url=f"https://api.valr.com{path}"
                        sign = getSign(bot.secret,ts,verb,path,json.dumps(body))
                        headers = {
                            'Content-Type': 'application/json',
                            'X-VALR-API-KEY': bot.key,
                            'X-VALR-SIGNATURE': str(sign),
                            'X-VALR-TIMESTAMP': str(ts),
                        }
                        response = externalSession.post(url, headers=headers, json=body)
                        response.raise_for_status()
                        account.stake += diff
                        account.update()
                    except Exception as e:
                        logPost(f"During Staking: {e}")
                elif account.stake > account.volume*.8:
                    diff = trunc((account.stake - (account.volume*0.75)),decimal)
                    try:
                        ts = int(time.time()*1000)
                        payload = {}
                        verb = "POST"
                        path = "/v1/staking/un-stake"
                        body = {
                            "currencySymbol":account.base,
                            "amount":trunc(f"{diff:.{decimal+1}f}", decimal)
                        }
                        url=f"https://api.valr.com{path}"
                        sign = getSign(bot.secret,ts,verb,path,json.dumps(body))
                        headers = {
                            'Content-Type': 'application/json',
                            'X-VALR-API-KEY': bot.key,
                            'X-VALR-SIGNATURE': str(sign),
                            'X-VALR-TIMESTAMP': str(ts),
                        }
                        response = externalSession.post(url, headers=headers, json=body)
                        if response.status_code != 202:
                            print(response.reason)
                            print(response.content)
                        response.raise_for_status()
                        account.stake -= diff
                    except Exception as e:
                        logPost(f"During Staking: {e}")

def updateStake(key, secret, base):
    "Returns staked volume"
    try:
        ts = int(time.time())*1000
        payload = {}
        verb = "GET"
        path = f"/v1/staking/balances/{base}"
        body = ""
        url=f"https://api.valr.com{path}"
        sign = getSign(secret,ts,verb,path)
        headers = {
            'X-VALR-API-KEY': key,
            'X-VALR-SIGNATURE': str(sign),
            'X-VALR-TIMESTAMP': str(ts),
        }
        response = externalSession.get(url, headers=headers, data=payload)
        response.raise_for_status()
        jsonResponse = response.json()
        return float(jsonResponse["amount"])
    except Exception as e:
        logPost(f"During Stake Upadte: {e}",'2')
        return 0

#Emails

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
    postmark.sendMail(config.postmarkKey, emailBase(body), "What happened?", recipient=user.email)

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
    postmark.sendMail(config.postmarkKey, emailBase(body), "Account Verification?", recipient=user.email)

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
    postmark.sendMail(config.postmarkKey, emailBase(body), "Everything ok?", recipient=user.email)

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
    postmark.sendMail(config.postmarkKey, emailBase(body), "What happened?", recipient=user.email)

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
    postmark.sendMail(config.postmarkKey, emailBase(body), "No Credit Warning", recipient=user.email)

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
    postmark.sendMail(config.postmarkKey, emailBase(body), "Credit Warning", recipient=user.email)

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
    postmark.sendMail(config.postmarkKey, emailBase(body), "Low Credit Warning", recipient=user.email)

def downturnProtectionEmail(config=Config, user=db.User):
    body = f"""
        <p style='color:black;'>Hey {user.name},</p><br>
        <p style='color:black;'>You're receiving this email to notify you that one or more of your bots has been paused. The general trend has fallen below 0.9 and Downturn Protection has been activated. Once the trend recovers your bot will automatically resume.</p>
        <p style='color:black;'>If you wish to resume your bot regardless; please go to the bot's config page, deselect 'Downturn Protection', select 'Active' and then click 'Update'</p>
        <p style='color:black;'>If you do not want your bot to resume when the trend recovers, simply de-activate Downturn Protection without activating the bot.</p>
        <p style='color:black;'>We hope everything goes well.</p><br>
        <p style='color:black;'>Best wishes,</p>
        <p style='color:black;'>&emsp;The BooF Team</p>
    """
    print("creditsFollowUpReminderEmail sent")
    postmark.sendMail(config.postmarkKey, emailBase(body), "Downturn Protection", recipient=user.email)

# Admin Functions

def checkTokens():
    ts = int(time.time())
    tokens = db.getTokens()
    for token in tokens:
        if (token.ts+(token.period*60*60)) < ts:
            token.delete()

def checkUserReminders(config=Config):
    "Check reminders"
    ts = int(time.time())
    users = db.getUsers()
    for user in users:
        reminders = user.reminder
        credits = db.getCredits(user_id=user.id)
        for reminder in reminders[:]:
            if int(reminder["code"]) == 0:      #Verified check
                if user.verified:
                    reminders.remove(reminder)
                elif ts - int(reminder['ts']) > (5*24*60*60):   #5 Days not verified
                    found = False
                    for checkReminder in reminders:
                        if int(checkReminder["code"]) == 6:
                            found = True
                            break
                    if not found:
                        unVerifiedEmail(config, user)
                        newEntry={
                            'code':6,
                            'ts':ts,
                            'description':'Unverified for 5 days'
                        }
                        reminders.append(newEntry)

            elif int(reminder["code"]) == 1:    #0.25 credits check
                if credits['credit'] > 0.25:
                    reminders.remove(reminder)

            elif int(reminder["code"]) == 2:    #0.1 credits check
                if credits['credit'] > 0.1:
                    reminders.remove(reminder)

            elif int(reminder["code"]) == 3:    #No credits check
                if credits['credit'] > 0:
                    reminders.remove(reminder)
                else:   #Stop bots due to no credits remaining
                    bots = db.getBots(user_id=user.id)
                    for bot in bots:
                        if bot.active:
                            bot.stop()
                    if ts-int(reminder['ts']) > (7*24*60*60): #7 days no credit
                        found = False
                        for checkReminder in reminders:
                            if int(checkReminder['code']) == 4:
                                found = True
                                break
                        if not found:
                            noCreditsReminderEmail(config, user)
                            newEntry={
                                'code':4,
                                'ts':ts,
                                'description':'Out of credits for 1 week'
                            }
                            reminders.append(newEntry)

            elif int(reminder["code"]) == 4:    #1 Week credit check
                if credits['credit'] > 0:
                    reminders.remove(reminder)

            elif int(reminder['code']) == 5:    #Activity Check
                if credits['active'] > 0:
                    reminders.remove(reminder)

            elif int(reminder['code']) == 6:    #Final verified check
                if user.verified:
                    reminders.remove[reminder]
                elif (ts - int(reminder['ts'])) > (2*24*60*60): #Delete user after 7 days(2 after reminder) not verified
                    user.delete()
                    break
            
            elif int(reminder['code']) == 7:    #Activity Check
                if credits['active'] > 0:
                    reminders.remove(reminder)
                elif (ts - int(reminder['ts'])) > (14*24*60*60):    #14 Days inactive
                    feedbackEmail(config, user)
                    newEntry={
                        'code':5,
                        'ts':ts,
                        'description':'2 Weeks inactive'
                    }
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
        if credits['active'] == 0 and len(bots) != 0 and credits['credit'] > 0 and not downturn:  #Check for inactive bots
            reminders = user.reminder
            found = False
            for reminder in reminders:
                if int(reminder['code']) == 7:
                    found = True
                    break
            if not found:
                botsInactiveEmail(config, user)
                newEntry={
                    'code':7,
                    'ts':ts,
                    'description':'Bots inactive'
                }
                reminders.append(newEntry)
                user.reminder = reminders
                user.update()


        if credits['credit'] <= 0:  #0 Credits remaining
            for bot in bots:
                if bot.active:
                    bot.stop()

            reminders = user.reminder
            found = False
            for reminder in reminders:
                if int(reminder['code']) == 3:
                    found = True
                    break
            if not found:
                noCreditsEmail(config, user)
                newEntry={
                    'code':3,
                    'ts':ts,
                    'description':'Credits has run out'
                }
                reminders.append(newEntry)
                user.reminder = reminders
                user.update()

        elif credits['credit'] < 0.1:   #0.1 Credits remaining
            reminders = user.reminder
            found = False
            for reminder in reminders:
                if int(reminder['code']) == 2:
                    found = True
                    break
            if not found:
                creditsFollowUpReminderEmail(config, user)
                newEntry={
                    'code':2,
                    'ts':ts,
                    'description':'0.1Credits remaining'
                }
                reminders.append(newEntry)
                user.reminder = reminders
                user.update()

        elif credits['credit'] < 0.25:   #0.25 Credits remaining
            reminders = user.reminder
            found = False
            for reminder in reminders:
                if int(reminder['code']) == 1:
                    found = True
                    break
            if not found:
                creditsReminderEmail(config, user)
                newEntry={
                    'code':1,
                    'ts':ts,
                    'description':'0.25Credits remaining'
                }
                reminders.append(newEntry)
                user.reminder = reminders
                user.update()

        active = 0
        for bot in bots:    #Audit active bots vs active credits
            if bot.active:
                active += 1
        if credits['active'] < active:
            diff = active - credits['active']
            for i in range(diff):
                newEntry = db.Credit([0,user.id,0,'',0,0,'START',int(time.time())])
                newEntry.post()
        elif credits['active'] > active:
            diff = credits['active'] - active
            for i in range(diff):
                newEntry = db.Credit([0,user.id,0,'',0,0,'PAUSE',int(time.time())])
                newEntry.post()



#Main Loops


@bmd_logger
def user_loop(lock, session, config=Config):
    if lock.acquire(blocking=False):
        avgTimer=1
        try:
            startTime = time.perf_counter()

            config.loadState()
            config.updateEnv()
            config.updatePrice(session)
            config.saveState()
            botLoop(config)

            endTime = time.perf_counter()
            timer = (endTime-startTime)
            avgTimer = config.botTimer
            config.botTimer = (avgTimer + timer)/2
            config.saveState()
            bmd_report(config)
        finally:
            lock.release()
        printLog(f"Bot running({trunc(timer,3)}secs | avg {trunc(config.botTimer,3)}secs) . . .")
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
    config.updateTickers(lock, session)

    downturnStop = 0.9
    upturnStart = 0.98

    bots = db.getBots()
    for bot in bots:
        if bot.downturn_protection:
            credits = db.getCredits(bot_id=bot.id)
            if credits["credit"]>0:

                generalTrend = findGeneralTrend(bot.currency, config)

                if bot.active and generalTrend < downturnStop:
                    printLog(f'Downturn Protection: Liquidating bot:{bot.id} for user:{bot.user_id}', True)
                    bot.stop()
                    message = db.Message([0, bot.user_id, "WARNING", "Downturn Protection: Your bot has been stopped and liquidated due to significant negative trend"])
                    message.post()
                    downturnProtectionEmail(config, db.getUsers(id=bot.user_id))
                elif not bot.active and generalTrend > upturnStart:
                    printLog(f'Downturn Protection: Starting bot:{bot.id} for user:{bot.user_id}', True)
                    bot.start()
                    message = db.Message([0, bot.user_id, "INFO", "Downturn Protection: Your bot has been re-activated, the market is recovering"])
                    message.post()

    users = db.getUsers()
    verifiedUsers = 0
    for user in users:
        if user.verified:
            verifiedUsers += 1
    activeBots = 0
    for bot in bots:
        if bot.active:
            activeBots +=1

    reportString = f"<p>BooF Report:<p><p>Users(verified): {len(users)}({verifiedUsers}) | Bots(Active): {len(bots)}({activeBots})"
    trend = 0
    rsi = 0
    for entry in config.ZAR:
        trend += entry["trend"]
        rsi += entry["rsi"]
    trend = trend/len(config.ZAR)
    rsi = rsi/len(config.ZAR)
    generalTrend = ((trend+((rsi/100)+0.5)))/2
    ZARString = f"<p>ZAR Trend: {trunc(trend,3)} | RSI: {trunc(rsi,1)} | General Trend: {trunc(generalTrend,3)}</p>"
    trend = 0
    rsi = 0
    for entry in config.USDC:
        trend += entry["trend"]
        rsi += entry["rsi"]
    trend = trend/len(config.USDC)
    rsi = rsi/len(config.USDC)
    generalTrend = (trend+((rsi/100)+0.5))/2
    USDCString = f"<p>USDC trend: {trunc(trend,3)} | RSI: {trunc(rsi,1)} | General Trend: {trunc(generalTrend,3)}</p>"
    trend = 0
    rsi = 0
    for entry in config.USDT:
        trend += entry["trend"]
        rsi += entry["rsi"]
    trend = trend/len(config.USDT)
    rsi = rsi/len(config.USDT)
    generalTrend = (trend+((rsi/100)+0.5))/2
    USDTString = f"<p>USDT trend: {trunc(trend,3)} | RSI: {trunc(rsi,1)} | General Trend: {trunc(generalTrend,3)}</p>"

    logPost(f"{reportString}<br>{ZARString}{USDCString}{USDTString}", '1')

def trendSort(value):
    trend = (float(value["trend"])+((float(value["rsi"])/100)+0.5))/2
    return trend
    
@bmd_logger
def xUpdate(config=Config):
    
    generalTrend = findGeneralTrend('USDT', config)

    trendList = config.USDC[:]
    trendList.sort(key=trendSort, reverse=True)

    msg = "Crypto Trend Update: "
    trendStrings = []
    for entry in trendList:
        trendStrings.append(f"{entry["base"]} Trend:{trunc(entry["trend"],2)} RSI:{int(entry["rsi"])}")
    msg += " | ".join(trendStrings)
    fire = ''
    if generalTrend > 1.08:
        fire =  '🚀'
    elif generalTrend > 1.02:
        fire =  '🔥'
    elif generalTrend < 0.92:
        fire = '❄️'
    elif generalTrend < 0.98:
        fire = '🌧️'
    elif generalTrend < 1.02 and generalTrend > 0.98:
        fire= '😐'
    msg += f". The General Market Trend is {trunc(generalTrend,2)} {fire}. "
    msg += "Powered by boof-bots.com and Valr #CryptoTrends #TradingBot #HODL"
    twitter.sendTweet(msg)


def thread_update_loop(lock, session, config=Config):
    job_thread = threading.Thread(
        target=update_loop,
        args=(lock, session, config),
        daemon = True
    )
    job_thread.start()

externalSession = createSession(360) #For authenticated api data, ie. account data and trading

if __name__ == "__main__":
    db.setupDB()
    internalSession = createSession(10) #For public api data, ie. ticker list and prices

    running = "running"

    config = Config()
    dataLock = threading.Lock()

    try:
        printLog("Loading config . . .", True)
        config.loadState()
        printLog("Initial state load successful . . .")
    except Exception as e:
        printLog("Failed Loading State . . .", True)
        config.updateEnv()
        config.saveState()
        config.updateTickers(dataLock, internalSession)
        config.updatePrice(internalSession)
        config.saveState()


    if running != "running":
        printLog("Running once", True)
        config.updateTickers(dataLock, internalSession)

    else: #Main operation
        
        schedule.every(30).seconds.do(user_loop, lock=dataLock, session=internalSession, config=config)

        schedule.every(1).hours.do(admin_loop, lock=dataLock, config=config)

        schedule.every().day.at("03:00").do(thread_update_loop, lock=dataLock, session=internalSession, config=config)
        schedule.every().day.at("09:00").do(thread_update_loop, lock=dataLock, session=internalSession, config=config)
        schedule.every().day.at("15:00").do(thread_update_loop, lock=dataLock, session=internalSession, config=config)
        schedule.every().day.at("21:00").do(thread_update_loop, lock=dataLock, session=internalSession, config=config)

        schedule.every().day.at("12:00").do(xUpdate, config=config)

        try:
            while True:
                n = schedule.idle_seconds()
                if n is None:
                    break
                elif n>0:
                    time.sleep(n)
                try:
                    config.checkVALR(internalSession)
                    config.saveState()
                    if config.valrStatus == "online":
                        schedule.run_pending()
                    else:
                        logPost("Valr Status: Offline",'1')
                except Exception as e:
                    msg = f"Error during main runtime:\n<br>{e}"
                    logPost(msg,'2')
        except KeyboardInterrupt:
            printLog("Shutting down . . .", True)
            config.saveState()

          
