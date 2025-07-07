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
oauthBearer = envConfig["X_BEARER_TOKEN"]


def sendTweet(msg):
    client = tweepy.Client(bearer_token=oauthBearer)
    result = client.create_tweet(text=msg)
    print(result.reason)
    print(result.content)
