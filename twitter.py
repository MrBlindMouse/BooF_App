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
    oauthHandler = tweepy.OAuth1UserHandler(clientID, clientSecret, access_token=accessToken, access_token_secret=accessSecret)
    api = tweepy.API(oauthHandler)
    result = api.update_status(msg)
    print("Tweet Response:")
    print(result)
