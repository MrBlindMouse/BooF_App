import requests, json
import tweepy
from dotenv import dotenv_values
import valr

envConfig = dotenv_values(".env")
clientID = envConfig["X_KEY"]
clientSecret = envConfig["X_SECRET"]
accessToken = envConfig["X_TOKEN"]
accessSecret = envConfig["X_TOKEN_SECRET"]

bearerToken = envConfig["X_BEARER_TOKEN"]


def sendTweet(msg):
    
    client = tweepy.Client(consumer_key=clientID, consumer_secret=clientSecret, access_token=accessToken, access_token_secret=accessSecret)
    result = client.create_tweet(text=msg)
    print(json.dumps(result.data, indent=4))
    print(json.dumps(result.errors, indent=4))



if __name__ == "__main__":
    getTweet("1943572132094529617")