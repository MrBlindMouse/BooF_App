import requests
import tweepy
from dotenv import dotenv_values
import valr

envConfig = dotenv_values(".env")
clientID = envConfig["X_KEY"]
clientSecret = envConfig["X_SECRET"]
accessToken = envConfig["X_TOKEN"]
accessSecret = envConfig["X_TOKEN_SECRET"]
oathClientSecret = envConfig["OAUTH2_CLIENT_SECRET"]


def sendTweet(msg):
    oathHandler = tweepy.OAuth2UserHandler(client_id=clientID, redirect_uri="https://boof-bots.com",scope=["tweet.write"], client_secret=oathClientSecret)
    client = tweepy.Client()
    result = client.create_tweet(text=msg)
    print("Tweet Response:")
    print(result.reason)
    print(result.content)
