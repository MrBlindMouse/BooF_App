import json, requests
from requests_oauthlib import OAuth1
from dotenv import dotenv_values
import valr

envConfig = dotenv_values(".env")
xClientID = envConfig["X_CLIENT_ID"]
xClientSecret = envConfig["X_CLIENT_SECRET"]

def xBearerToken(clientID, clientSecret):
    url = "https://api.twitter.com/oauth2/token"
    auth = (client_id, client_secret)
    data = {"grant_type": "client_credentials"}
    
    response = requests.post(url, auth=auth, data=data)
    if response.status_code != 200:
        print("Error fetching Bearer Token:")
        print(response.reason, response.json())
        return None
    return response.json().get("access_token")

def xPost(message):
    token = xBearerToken(xClientID, xClientSecret)
    if not token:
        return

    url = "https://api.twitter.com/2/tweets"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {"text": message}
    
    result = requests.post(url, headers=headers, json=payload)
    if result.status_code == 201:
        print("X update success!")
        print(result.content)
    else:
        print("Error posting to X:")
        print(result.reason)
        print(result.content)