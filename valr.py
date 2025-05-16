import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from requests_ratelimiter import LimiterSession
import hashlib, hmac
from dotenv import dotenv_values
import schedule
import datetime, json, pickle, os, sys, traceback, time, datetime, math

import db

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
    a = int(value*(10**dec))
    return float(a/(10**dec))

def sorting(values):
    """
    Sorting base on minTrade
    """
    return values["minTrade"]

class Config():
    """
    updateEnv() and checkVALR to be run every loop,
    updateTickers() to be run daily,
    updateVotality() to be ru weekly
    """
    STATE_FILE = "config_state.pkl"
    def __init__(self):
        self.valrStatus = "down"
        self.appSecret = None
        self.ZAR = []
        self.USDC = []
        self.USDT = []
        self.USDCZAR = 0
        self.USDTZAR = 0
        self.forbidden = []
        self.stake = []
        self.isupTS = 0
        self.postmarkKey = ""
        self.verifySalt = ""

    def updateEnv(self):
        """
        Updates config form the .env
        """
        config = dotenv_values(".env")
        self.appSecret = config["APP_SECRET"]
        self.forbidden = eval(config["FORBIDDEN"])
        self.stake = eval(config["STAKE"])
        self.postmarkKey = config["POSTMARK_KEY"]
        self.verifySalt = config["VERIFY_SALT"]
    
    def updateTickers(self, session):
        """
        Updates the ticker list, including ticker trend, per quote currency.
        Ticker format={
            "base": {baseCurrency},
            "price": {markPrice},
            "decimal": {baseDesimalPlaces},
            "minTrade": {minValue},
            "trend": {trend},
            "rsi": {rsi}
            "votality": {beta}
        }
        """
        printLog("Updating tickers . . .")
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
                if details and not any(forbidden in details["baseCurrency"] for forbidden in self.forbidden) and details["currencyPairType"] == "SPOT":
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
                        tickerTrend = trend(session,details["symbol"])
                        tickerDetails = {
                            "base": details["baseCurrency"],
                            "price": float(summaryResult["markPrice"]),
                            "decimal": int(details["baseDecimalPlaces"]),
                            "minTrade": minValue,
                            "trend": tickerTrend["trend"],
                            "rsi": tickerTrend["rsi"],
                            "votality": 1
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
                else:
                    if details:
                        printLog(f"{details["symbol"]} not allowed", True)
                        
            for key, value in enumerate(ZARlist):
                ZARlist[key]["votality"] = beta(session, value["base"], "ZAR", ZARBars)
            ZARlist.sort(key=sorting)
            self.ZAR = ZARlist
                    
            for key, value in enumerate(USDClist):
                USDClist[key]["votality"] = beta(session, value["base"], "USDC", USDCBars)
            USDClist.sort(key=sorting)
            self.USDC = USDClist
                    
            for key, value in enumerate(USDTlist):
                USDTlist[key]["votality"] = beta(session, value["base"], "USDT", USDTBars)
            USDTlist.sort(key=sorting)
            self.USDT = USDTlist
            self.saveState()
        except Exception as e:
            printLog(e, True)
   
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
                self.USDCZAR = line["markPrice"]
            if line["currencyPair"] == "USDTZAR":
                self.USDTZAR = line["markPrice"]


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
            printLog(f"VALR server status: {e}", True)

    def saveState(self):
        with open(self.STATE_FILE, 'wb') as file:
            pickle.dump({
                "valrStatus":self.valrStatus,
                "appSecret":self.appSecret,
                "ZAR":self.ZAR,
                "USDC":self.USDC,
                "USDT":self.USDT,
                "USDCZAR":self.USDCZAR,
                "USDTZAR":self.USDTZAR,
                "forbidden":self.forbidden,
                "stake":self.stake,
                "isupTS":self.isupTS,
                "postmarkKey":self.postmarkKey,
                "verifySalt":self.verifySalt

            }, file)

    def loadState(self):
        if os.path.exists(self.STATE_FILE):
            with open(self.STATE_FILE, 'rb') as file:
                state = pickle.load(file)
                self.valrStatus = state["valrStatus"]
                self.appSecret = state["appSecret"]
                self.ZAR = state["ZAR"]
                self.USDC = state["USDC"]
                self.USDT = state["USDT"]
                self.USDCZAR = state["USDCZAR"]
                self.USDTZAR = state["USDTZAR"]
                self.forbidden = state["forbidden"]
                self.stake = state["stake"]
                self.isupTS = state["isupTS"]
                self.postmarkKey = state["postmarkKey"]
                self.verifySalt = state["verifySalt"]
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
            transaction = db.Transaction(0,bot.id,"SELL",result["volume"],result["value"],account.base,bot.currency,int(time.time()))
            transaction.post()
        account.delete()
    bot.currency = newCurrency
    bot.update()

def trade(direction, quote, base, key, secret, amount, decimal=2):
    """
    Buys with quote currency, amount = value
    Sells with base currency, amount = volume
    Default Decimal = 2 for Fiat currencies
    """
    try:
        payload = {}
        if direction == "BUY":
            payload = {
                "side": "BUY",
                "quoteAmount": f"{amount:.{decimal}f}",
                "pair": f"{base}{quote}"
            }
        elif direction == "SELL":
            payload = {
                "side": "SELL",
                "baseAmount": f"{amount:.{decimal}f}",
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
            printLog(json.dumps(jsonResponse, indent=4))
            print(json.dumps(payload))
            raise ValueError(jsonResponse["message"])
        id = jsonResponse["id"]
        
        loop = 0
        while True:
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
                        details={
                            "volume":float(jsonResponse["totalExecutedQuantity"])-float(jsonResponse["totalFee"]),
                            "value":float(jsonResponse["total"])
                        }
                    elif direction == "SELL":
                        details={
                            "volume":float(jsonResponse["totalExecutedQuantity"]),
                            "value":float(jsonResponse["total"])-float(jsonResponse["totalFee"])
                        }
                    return details
                time.sleep(1)
            except Exception as e:
                printLog(f"During order check: {e}", True)
                break
    except Exception as e:
        printLog(f"During Trade: {e}", True)
        return False


def trend(session, pair):
    answer={
        "trend":1,
        "rsi":50,
        "bars":[]
    }
    url = f"https://api.valr.com/v1/public/{pair}/markprice/buckets?periodSeconds=86400"
    result = session.get(url)
    if result.status_code == 200:
        result = result.json()
        if len(result) > 60:
            shortTerm = 0
            longTerm = 0
            for line in result:
                answer["bars"].append(float(line["close"]))

            for line in result[:14]:
                shortTerm += float(line["close"])
            shortTerm = shortTerm/14
            
            for line in result[:60]:
                longTerm += float(line["close"])
            longTerm = longTerm/60
            trend = (shortTerm/longTerm)
            answer["trend"]=trunc(trend,3)

            up = 0
            down = 0
            for key, line in enumerate(result[:14]):
                change = float(result[key]["close"]) - float(result[key+1]["close"])
                if change > 0:
                    up += change
                else:
                    down += abs(change)
            up = up/14
            down = down/14
            answer["rsi"] = trunc(100-(100/(1+(up/down))),2)

        else:
            printLog(f"Bucket List for {pair} not sufficient", True)
            print(f"\tBucket size:{len(result)}")
    else:
        print(result.reason)
        print(result.content)
        raise ValueError("Error retreiving bucket data")
    return answer

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
    payload = {
        "code": code,
        "app": "BooF",
        "snippet": snippet
    }
    try:
        result = requests.post("https://www.bmd-studios.com/log", json=payload)
        if result.status_code != 200:
            printLog("Logging Error", True)
            print("Status code: "+str(result.status_code))
            print(result.text)
            print('Original exception: ')
            print(post_message)
    except Exception as c:
        printLog("Logging server down . . .", True)
        print(str(c))
        print('Original exception: ')
        print(post_message)


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
        "status":config.valrStatus
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
        if bot.active:
            checkBalances(config, bot)

    #Find total equity
    printLog("Finding bot equity . . .")
    for bot in bots:
        if bot.active:
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
        ts = int(time.time())*1000
        accounts = db.getActiveAccounts(bot_id=bot.id)
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

        accounts = db.getActiveAccounts(bot_id=bot.id)
        
        for entry in jsonResponse:
            if entry["currency"] == bot.currency:
                if float(entry["available"]) != bot.quote_balance:
                    bot.quote_balance = float(entry["available"])
                    bot.update()
                found = True
            else:
                found = False

            if not found: #Check Accounts
                for account in accounts:
                    if account.base == entry["currency"]:
                        found = True
                        if account.volume != (float(entry["available"])+account.stake):
                            if account.base in config.stake:
                                account.stake = updateStake(bot.key, bot.secret, account.base)
                            account.volume = float(entry["available"])+account.stake
                            account.update()
                        break

            if not found and entry["currency"] in ["ZAR","USDC","USDT"]: #Quote Currencies
                if bot.currency == "ZAR":
                    if entry["currency"] == "ZAR":
                        found = True
                    elif entry["currency"] == "USDC":
                        found = True
                        if float(entry["total"]) > 1: #Min base amount
                            printLog("Selling USDC for ZAR", True)
                            trade("SELL", "ZAR", "USDC", bot.key, bot.secret, float(entry["available"]))
                        pass
                    elif entry["currency"] == "USDT":
                        found = True
                        if float(entry["total"]) > 1: #Min base amount
                            printLog("Selling USDT for ZAR", True)
                            trade("SELL", "ZAR", "USDT", bot.key, bot.secret, float(entry["available"]))
                elif bot.currency == "USDC":
                    if entry["currency"] == "USDC":
                        found = True
                    elif entry["currency"] == "ZAR":
                        found = True
                        if float(entry["total"]) > 10: #Min quote amount
                            printLog("Buying USDC with ZAR", True)
                            trade("BUY", "ZAR", "USDC", bot.key, bot.secret, float(entry["available"]))
                    elif entry["currency"] == "USDT":
                        found = True
                        if float(entry["total"]) > 1: #Min base amount
                            printLog("Selling USDT for ZAR", True)
                            trade("BUY", "USDT", "USDC", bot.key, bot.secret, float(entry["available"]))
                elif bot.currency == "USDT":
                    if entry["currency"] == "USDT":
                        found = True
                    elif entry["currency"] == "USDC":
                        found = True
                        if float(entry["total"]) > 1: #Min quote amount
                            printLog("Selling USDC for USDT", True)
                            trade("SELL", "USDT", "USDC", bot.key, bot.secret, float(entry["available"]))
                    elif entry["currency"] == "ZAR":
                        found = True
                        if float(entry["total"]) > 10: #Min quote amount
                            printLog("Selling USDT for ZAR", True)
                            trade("BUY", "ZAR", "USDT", bot.key, bot.secret, float(entry["available"]))


            if not found: #Sell for ZAR tickers
                for ticker in config.ZAR:
                    if ticker["base"] == entry["currency"]:
                        found = True
                        if float(entry["total"])*float(ticker["price"]) > ticker["minTrade"]:
                            printLog(f"Selling {entry["currency"]} for ZAR", True)
                            trade("SELL", "ZAR", entry["currency"], bot.key, bot.secret, float(entry["available"]))
                        
                            
            if not found: #Sell for USDC tickers
                for ticker in config.USDC:
                    if ticker["base"] == entry["currency"]:
                        found = True
                        if float(entry["total"])*ticker["price"] > float(ticker["minTrade"]):
                            printLog(f"Selling {entry["currency"]} for USDC", True)
                            trade("SELL", "USDC", entry["currency"], bot.key, bot.secret, float(entry["available"]))
                            
        
            if not found: #SELL for USDT tickers
                for ticker in config.USDT:
                    if ticker["base"] == entry["currency"]:
                        found = True
                        if float(entry["total"])*float(ticker["price"]) > float(ticker["minTrade"]):
                            printLog(f"Selling {entry["currency"]} for USDT", True)
                            trade("SELL", "USDT", entry["currency"], bot.key, bot.secret, float(entry["available"]), int(ticker['decimal']))

        for account in accounts:
            found = False
            for entry in jsonResponse:
                if account.base == entry["currency"]:
                    found = True
            if not found and account.volume != 0:
                account.volume = 0
                account.update()

    except Exception as e:
        printLog(f"During checkBalances: {e}", True)
                        
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
                message = db.Message(0, bot.user_id, "ERROR", "Insufficient equity, pausing bot.")
                message.post()
                break
            else:
                bot.update()
                message = db.Message(0, bot.user_id, "WARNING", f"Insufficient equity, increasing margin({(bot.margin*100):0f}) to maintain normal operations.")
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

        for i in range(difference):
            account = accounts[accountNrs-i-1]
            ticker = None
            for entry in currencyList:
                if entry["base"] == account.base:
                    ticker = entry
                    break
            printLog(f"Selling {account.base} for {bot.currency} to close account", True)
            result = trade("SELL", bot.currency, account.base, bot.key, bot.secret, account.volume, ticker["decimal"])
            if result:
                transaction = db.Transaction([0,bot.id,"SELL",result["volume"],result["value"],account.base,bot.currency,int(time.time())])
                transaction.post()
                bot.equity += result["value"]
                bot.update()
            account.delete()

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
                printLog(f"Buying {newAccount["base"]} with {bot.currency} to open account", True)
                result = trade("BUY", bot.currency, newAccount["base"], bot.key, bot.secret, bot.balance_value)
                if result:
                    transaction = db.Transaction([0,bot.id,"BUY",result["volume"],result["value"],account.base,bot.currency,int(time.time())])
                    transaction.post()
                    bot.equity -= result["value"]
                    bot.update()
                    account = db.ActiveAccount([0, bot.id, newAccount["base"], result["volume"], 0, "", 0])
                    account.post()
                else:
                    account = db.ActiveAccount([0, bot.id, newAccount["base"], 0, 0, "", 0])
                    account.post()
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

    marketTrend = 0
    marketRSI = 0
    for entry in currencyList:
        marketTrend += entry["trend"]
        marketRSI += entry["rsi"]
    marketTrend = marketTrend/len(currencyList)
    marketRSI = marketRSI/len(currencyList)
    generalTrend = (marketTrend+(marketRSI/50))/2

    for account in accounts:
        currencyDetails = None
        for entry in currencyList:
            if entry["base"] == account.base:
                currencyDetails = entry


        price = float(currencyDetails["price"])
        decimal = int(currencyDetails["decimal"])
        value = account.volume * price
        
        marginFactor = currencyDetails["votality"]-1 if currencyDetails["votality"] > 0.1 else 0
        margin = bot.margin*(1+(0.2*marginFactor)) if bot.dynamic_margin else bot.margin

        weight = 1+(2*(generalTrend-1))
        weight = max(0.5, min(1, weight)) #Adjustment capped at 50% on downtrend
        balanceValue = bot.balance_value * weight if bot.refined_weight else bot.balance_value

        if value > balanceValue:
            difference = (value - balanceValue)/balanceValue
            if account.direction != "UP":
                account.direction = "UP"
                account.update()
            if difference > margin*4:
                sellVolume = (value-balanceValue)/price
                printLog(f"Withdrawing {account.base} for {bot.currency} during balancing", True)
                result = trade("SELL", bot.currency, account.base, bot.key, bot.secret, sellVolume, decimal)
                if result:
                    transaction = db.Transaction([0,bot.id,"SELL",result["volume"],result["value"],account.base,bot.currency,int(time.time())])
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
                sellVolume = (value-balanceValue)/price
                printLog(f"Selling {account.base} for {bot.currency} during balancing", True)
                result = trade("SELL", bot.currency, account.base, bot.key, bot.secret, sellVolume, decimal)
                if result:
                    transaction = db.Transaction([0,bot.id,"SELL",result["volume"],result["value"],account.base,bot.currency,int(time.time())])
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
            if difference > margin*4:
                buyValue = balanceValue-value
                if buyValue < bot.quote_balance:
                    printLog(f"Investing {account.base} with {bot.currency} during balancing", True)
                    result = trade("BUY", bot.currency, account.base, bot.key, bot.secret, buyValue)
                    if result:
                        transaction = db.Transaction([0,bot.id,"BUY",result["volume"],result["value"],account.base,bot.currency,int(time.time())])
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
                buyValue = balanceValue-value
                printLog(f"Buying {account.base} with {bot.currency} during balancing", True)
                result = trade("BUY", bot.currency, account.base, bot.key, bot.secret, buyValue)
                if result:
                    transaction = db.Transaction([0,bot.id,"BUY",result["volume"],result["value"],account.base,bot.currency,int(time.time())])
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
                            "amount":f"{diff:.{decimal}f}"
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
                        printLog(f"During Staking: {e}", True)
                elif account.stake > account.volume*.8:
                    diff = trunc((account.stake - (account.volume*0.75)),decimal)
                    try:
                        ts = int(time.time()*1000)
                        payload = {}
                        verb = "POST"
                        path = "/v1/staking/un-stake"
                        body = {
                            "currencySymbol":account.base,
                            "amount":f"{diff:.{decimal}f}"
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
                        printLog(f"During Staking: {e}", True)

def updateStake(key, secret, base):
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
        printLog(f"During Stake Upadte: {e}", True)
        return 0

@bmd_logger
def loop(session, config=Config):
    config.loadState()
    config.updateEnv()
    config.updatePrice(session)
    config.saveState()
    botLoop(config)
    config.saveState()
    bmd_report(config)


externalSession = createSession(360) #For authenticated api data, ie. account data and trading

if __name__ == "__main__":
    db.setupDB()
    internalSession = createSession(10) #For public api data, ie. ticker list and prices

    running = "running"

    config = Config()
    try:
        printLog("Loading config . . .")
        config.loadState()
    except Exception as e:
        config.updateEnv()
        config.saveState()
        config.updateTickers(internalSession)
        config.updatePrice(internalSession)
        config.saveState()

    if running != "running":
        loop(internalSession, config)
    else:
        schedule.every(10).seconds.do(loop, session=internalSession, config=config)
        schedule.every().day.at('00:00').do(config.updateTickers, internalSession)

        try:
            while True:
                try:
                    printLog("Running . . .")
                    config.checkVALR(internalSession)
                    if config.valrStatus == "online":
                        schedule.run_pending()
                except Exception as e:
                    printLog("Error during main runtime", True)
                time.sleep(1)
        except KeyboardInterrupt:
            printLog("Shutting down . . .", True)
            config.saveState()

          