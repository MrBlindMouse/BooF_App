import requests, json
import tweepy
from dotenv import dotenv_values
import valr

envConfig = dotenv_values(".env")
clientID = envConfig["X_KEY"]
clientSecret = envConfig["X_SECRET"]
accessToken = envConfig["X_TOKEN"]
accessSecret = envConfig["X_TOKEN_SECRET"]

def sendTweet(msg):
    
    client = tweepy.Client(consumer_key=clientID, consumer_secret=clientSecret, access_token=accessToken, access_token_secret=accessSecret)
    result = client.create_tweet(text=msg)
    print(json.dumps(result.data, indent=4))
    print(json.dumps(result.errors, indent=4))

if __name__ == "__main__":
    msg = "Crypto Trend Update: SOL Trend:0.95 RSI:61 | AVAX Trend:0.89 RSI:63 | ETH Trend:1.12 RSI:72 | BTC Trend:1.07 RSI:69 | XRP Trend:1.09 RSI:72. The General Market Trend is 1.02. Brought to you by boof-bots.com, and valr.com #CryptoTrends #TradingBot #HODL"
    print(len(msg))
    sendTweet(msg)