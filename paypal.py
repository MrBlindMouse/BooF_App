import requests, json, time, uuid
from dotenv import dotenv_values
import db

envConfig = dotenv_values(".env")
baseURL = "https://api-m.paypal.com" if envConfig["PAYPAL_MODE"] == "LIVE" else "https://api-m.sandbox.paypal.com"

def getAccessToken(id, secret):
    token = db.getTokens(type="PAYPAL")
    print(token)
    ts = int(time.time())
    if token and (token.ts+token.period) > ts:
        print("Returning from DB")
        return token
    elif not token:
        print("Calling new token")
        url = f"{baseURL}/v1/oauth2/token"
        headers = {
            "Content-Type": "application/json",
            'Accept-Language': 'en_US'
        }
        data = {
            'grant_type': 'client_credentials'
        }
        auth=(id,secret)
        result = None
        try:
            result = requests.post(url=url, headers=headers, auth=auth, data=data)
            result.raise_for_status()
            jsonResult = result.json()
            token = jsonResult["access_token"]
            period = int(jsonResult["expires_in"])/(60*60)#hours
            tokenEntry = db.Token([0,token,ts,0,"PAYPAL",period])
            tokenEntry.post()
            return tokenEntry
        except requests.exceptions.HTTPError as e:
            print(f"HTTP error: {e}")
            print(f"Result: {result.text}")
        except Exception as e:
            print(f"Error: {e}")
    else:
        print("Updating token . . .")
        token.delete()
        url = f"{baseURL}/v1/oauth2/token"
        headers = {
            "Content-Type": "application/json",
            'Accept-Language': 'en_US'
        }
        data = {
            'grant_type': 'client_credentials'
        }
        auth=(id,secret)
        result = None
        try:
            result = requests.post(url=url, headers=headers, auth=auth, data=data)
            result.raise_for_status()
            jsonResult = result.json()
            token = jsonResult["access_token"]
            period = int(jsonResult["expires_in"])/(60*60)#hours
            tokenEntry = db.Token([0,token,ts,0,"PAYPAL",period])
            tokenEntry.post()
            return tokenEntry
        except requests.exceptions.HTTPError as e:
            print(f"HTTP error: {e}")
            print(f"Result: {result.text}")
        except Exception as e:
            print(f"Error: {e}")

def checkPlans(token=db.Token):
    if not token:
        return None
    url = f"{baseURL}/v1/billing/plans?sort_by=create_time&sort_order=desc"
    header = {
        "Authorization": f"Bearer {token.token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Prefer": "return=representation"
    }
    result = None
    jsonResult = None
    try:
        result = requests.get(url, headers=header)
        result.raise_for_status()
        jsonResult = result.json()
        print(json.dumps(jsonResult, indent=4))
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error: {e}")
        print(f"Result: {result.text}")
    except Exception as e:
        print(f"Error: {e}")

def createPlan(token = db.Token):
    if not token:
        return None
    url = f"{baseURL}/v1/billing/plans"
    UID = str(uuid.uuid4())
    headers = {
        "Authorization": f"Bearer {toekn.token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "PayPal-Request-Id": f"{UID}",
        "Prefer": "return=representation"
    }

def createProduct(token=db.Token):
    url = f"{baseURL}/v1/catalogs/products"
    UID = str(uuid.uuid4())
    headers = {
        "Authorization": f"Bearer {toekn.token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "PayPal-Request-Id": f"{UID}",
        "Prefer": "return=representation"
    }


    

if __name__ == "__main__":
    productList=[{
        "name": "Credit x1",
        "description": "BooF Bot's 4 week Credit",
        "type": "SERVICE",
        "category": "SOFTWARE",
        "image_url":"",
        "home_url":"https://www.boof-bots.com"
    }]
    token = getAccessToken(envConfig["PAYPAL_ID"],envConfig["PAYPAL_SECRET"])
    print(token)
    checkPlans(token)

