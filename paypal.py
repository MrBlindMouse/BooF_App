import requests, json, time, uuid
from dotenv import dotenv_values
import db

envConfig = dotenv_values(".env")
baseURL = "https://api-m.paypal.com" if envConfig["PAYPAL_MODE"] == "LIVE" else "https://api-m.sandbox.paypal.com"

def getAccessToken(id, secret):
    token = db.getTokens(type="PAYPAL")
    ts = int(time.time())
    if token and (token.ts+(token.period*60*60)) > ts:
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
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error: {e}")
        print(f"Result: {result.text}")
    except Exception as e:
        print(f"Error: {e}")

    planList = None
    with open("product_list.json", "r") as file:
        planList = json.load(file)
    if planList:
        change = False
        for plan in planList["plans"]:
            found = False
            for entry in jsonResult["plans"]:
                if entry["name"] == plan["name"]:
                    found = True
                    print(f"Plan {entry["name"]} found")
                    if not "id" in plan:
                        change = True
                        plan["id"] = entry["id"]
                    elif entry["id"] != plan["id"]:
                        change = True
                        plan["id"] = entry["id"]
                    break
            if not found:
                createPlan(plan, token)
        if change:
            print("Updating product file")
            with open("product_list.json", "w") as file:
                json.dump(planList, file, indent=4)
    else:
        raise("Product List file missing(product_list.json)")


def createPlan(planFile, token=db.Token):
    print(f"Creating plan: {planFile["name"]}")
    url = f"{baseURL}/v1/billing/plans"
    UID = str(uuid.uuid4())
    headers = {
        "Authorization": f"Bearer {token.token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "PayPal-Request-Id": f"{UID}",
        "Prefer": "return=representation"
    }
    result = None
    jsonResult = None
    try:
        result = requests.post(url, headers=headers, data=json.dumps(planFile))
        result.raise_for_status()
        jsonResult = result.json()
        print(json.dumps(jsonResult, indent=4))
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error: {e}")
        print(f"Result: {result.text}")
    except Exception as e:
        print(f"Error: {e}")

def createProduct(name, description, imageURL, homeURL, token=db.Token):
    "Create Paypal product"
    url = f"{baseURL}/v1/catalogs/products"
    UID = str(uuid.uuid4())
    headers = {
        "Authorization": f"Bearer {token.token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "PayPal-Request-Id": f"{UID}",
        "Prefer": "return=representation"
    }
    data = {
        "name":name,
        "description": description,
        "type": "SERVICE",
        "category": "SOFTWARE",
        "image_url":imageURL,
        "home_url":homeURL
    }
    result = None
    jsonResult = None
    try:
        result = requests.post(url, headers=headers, data=json.dumps(data))
        result.raise_for_status()
        jsonResult = result.json()
        print(json.dumps(jsonResult, indent=4))
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error: {e}")
        print(f"Result: {result.text}")
    except Exception as e:
        print(f"Error: {e}")

def updateProduct(id, attribute, changeTo, token=db.Token):
    """
    Update product
    Attributes: "description","category","image_url","home_url"
    Check Paypal docs for "category" descriptions
    """
    print(f"Updating {id}, {attribute} to {changeTo}")
    url = f"{baseURL}/v1/catalogs/products/{id}"
    headers = {
        "Authorization": f"Bearer {token.token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    data = [{
        "op": "replace",
        "path": f"/{attribute}",
        "value": changeTo
    }]
    result = None
    try:
        result = requests.patch(url, headers=headers, data=json.dumps(data))
        result.raise_for_status()
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error: {e}")
        print(f"Result: {result.text}")
    except Exception as e:
        print(f"Error: {e}")

def checkProducts(token=db.Token):
    """
    Compare Paypal products to on file products(product_list.json),
    Create missing Paypal products as required,
    Update on file ID's and Paypal's details as required.
    """
    url = f"{baseURL}/v1/catalogs/products?total_required=true"
    headers = {
        "Authorization": f"Bearer {token.token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    result = None
    jsonResult = None
    try:
        result = requests.get(url, headers=headers)
        result.raise_for_status()
        jsonResult = result.json()
    except requests.exceptions.HTTPError as e:
        print(f"HTTP error: {e}")
        print(f"Result: {result.text}")
    except Exception as e:
        print(f"Error: {e}")

    prodList = None
    with open("product_list.json", "r") as file:
        prodList = json.load(file)
    if prodList:
        change = False
        for product in prodList["products"]:
            found = False
            for item in jsonResult["products"]:
                if item["name"] == product["name"]:
                    found = True
                    print(f"Product '{item["name"]}' found")
                    print(json.dumps(item, indent=4))
                    if not "id" in product:
                        print("Adding ID")
                        product["id"] = item["id"]
                        change = True
                    elif product["id"] != item["id"]:
                        print("Updating ID")
                        product["id"] = item["id"]
                        change = True
                    if product["description"] != item["description"]:
                        updateProduct(product["id"], "description", product["description"], token)
                    break
            if not found:
                createProduct(product["name"], product["description"], product["image_url"], product["home_url"], token)
        if change:
            print("Updating product file")
            with open("product_list.json", "w") as file:
                json.dump(prodList, file, indent=4)
    else:
        raise("Product List file missing(product_list.json)")

def setup(token=db.Token):
    
    product_list = None
    with open("product_list.json", "r") as file:
        product_list = json.load(file)
    if product_list["products"]:
        checkProducts(token)
    else:
        print("Product list empty")
        return
    if product_list["plans"]:
        checkPlans(token)
    else:
        print("Subscription Plan list empty")

if __name__ == "__main__":
    token = getAccessToken(envConfig["PAYPAL_ID"],envConfig["PAYPAL_SECRET"])
    setup(token)

