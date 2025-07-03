import json, requests
from requests_oauthlib import OAuth1
from dotenv import dotenv_values
import valr

envConfig = dotenv_values(".env")
xKey = envConfig["X_KEY"]
xSecret = envConfig["X_SECRET"]
xToken = envConfig["X_TOKEN"]
xTokenSecret = envConfig["X_TOKEN_SECRET"]

def xPost(message):
    auth = OAuth1(xKey, xSecret, xToken, xTokenSecret)
    url = "https://api.twitter.com/2/tweets"
    payload = {"text":message}
    result = requests.post(url, auth=auth, json=payload)
    result.raise_for_status()