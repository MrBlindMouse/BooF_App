import requests
import tweepy
from dotenv import dotenv_values
import valr

envConfig = dotenv_values(".env")
clientID = envConfig["X_CLIENT_ID"]
clientSecret = envConfig["X_CLIENT_SECRET"]
accessToken = envConfig["X_TOKEN"]
accessSecret = envConfig["X_TOKEN_SECRET"]


def sendTweet(msg):
    client = tweepy.Client(consumer_key=clientID, consumer_secret=clientSecret, access_token=accessToken, access_token_secret=accessSecret)
    result = client.create_tweet(text=msg)
    print("Tweet Response:")
    print(result.reason)
    print(result.content)
