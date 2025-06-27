import requests, json, time, uuid
from dotenv import dotenv_values
import db

envConfig = dotenv_values(".env")
baseURL = "https://api-m.paypal.com" if envConfig["PAYPAL_MODE"] == "LIVE" else "https://api-m.sandbox.paypal.com"

#Active Functions

def createOrder(prodID, amount, userID):
    token = getAccessToken(envConfig["PAYPAL_ID"],envConfig["PAYPAL_SECRET"])
    url = f"{baseURL}/v2/checkout/orders"
    headers = {
        "Content-Type": "application/json",
        "PayPal-Request-Id": str(uuid.uuid4()),
        "Authorization": f"Bearer {token.token}",
        "Prefer": 'return=minimal' if envConfig["PAYPAL_MODE"] == "LIVE" else 'return=representation'
    }
    product = findProducts(prodID)
    data = {
        "intent": "CAPTURE",
        "purchase_units":[{
            "description":product["description"],
            "items":[{
                "name":product["name"],
                "quantity":str(amount),
                "unit_amount":{
                    "currency_code":"USD",
                    "value":str(product["price"])
                }
            }],
            "amount":{
                "currency_code":"USD",
                "value":str(int(amount)*float(product["price"])),
                "breakdown":{
                    "item_total":{
                        "currency_code":"USD",
                        "value":str(int(amount)*float(product["price"]))
                    }
                }
            },
            "custom_id":f"userID_{userID}"
        }]
    }
    result = requests.post(url=url, headers=headers, data=json.dumps(data))
    try:
        result.raise_for_status()
        jsonResult = result.json()
        return jsonResult
    except Exception as e:
        print(f"Create order exception: {e}")
        print(result.reason)
        print(result.content)
        return None

def captureOrder(data, userID):
    token = getAccessToken(envConfig["PAYPAL_ID"],envConfig["PAYPAL_SECRET"])
    url = f"{baseURL}/v2/checkout/orders/{data["orderID"]}/capture"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token.token}",
        "Prefer": 'return=representation'
    }
    print(f"Capturing order {data["orderID"]} . . .")
    result = requests.post(url, headers=headers)
    try:
        result.raise_for_status()
        jsonResult = result.json()
        print(json.dumps(jsonResult, indent=4))
        if jsonResult["status"] == "COMPLETED":
            print("Success!")
            product = findProducts(data["productID"])
            value = float(jsonResult["purchase_units"][0]["amount"]["value"])
            volume = int(jsonResult["purchase_units"][0]["items"][0]["quantity"])*product["credits"]
            captureCredit = db.Credit([0,userID,0,data["orderID"],value,volume,"CREDIT",int(time.time())])
            captureCredit.post()
            message = db.Message([0, userID, "INFO", f"Purchase Successful! {volume}x Credits added to account!"])
            message.post()
            return "COMPLETED"
        else:
            return "failed"
    except Exception as e:
        print(f"Capture order exception: {e}")
        print(result.reason)
        print(result.content)
        return "failed"

def captureSubsription(data, userID):
    token = getAccessToken(envConfig["PAYPAL_ID"],envConfig["PAYPAL_SECRET"])
    url = f"{baseURL}/v1/billing/subscriptions/{data["planID"]}"
    headers = {
        "Authorization": f"Bearer {token.token}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    result = requests.get(url, headers=headers)
    try:
        result.raise_for_status()
        jsonResult = result.json()
        if jsonResult["status"] == "ACTIVE":
            newSubscription = db.Subscription([0, userID, jsonResult["id"], int(time.time()), jsonResult["quantity"], jsonResult["status"]])
            newSubscription.post()
            message = db.Message([0, userID, "INFO", f"Subscription Successfully Activated for {jsonResult["quantity"]}x credits every 4 weeks!"])
            message.post()
        return jsonResult["status"]
    except Exception as e:
        print(f"Capture Subsription error: {e}")
        print(result.reason)
        print(result.content)
        return "failed"

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

#Subscription functions

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
                    print(f"Plan '{entry["name"]}' found")
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
    "Create subscription plan, check paypal dev docs for plan file(json), add 'max_amount':{} manually and plan 'id':{} automatically after reboot"
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

def suspendSubscription(subscriptionID, reason=None):
    token = getAccessToken(envConfig["PAYPAL_ID"],envConfig["PAYPAL_SECRET"])
    url =f"{baseURL}//v1/billing/subscriptions/{subscriptionID}/suspend"
    headers = {
        "Authorization": f"Bearer {token.token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    data = {}
    if reason:
        data={
            "reason":reason
        }
    else:
        data = {
            "reason": "Client suspended subscription"
        }
    result = requests.post(url=url, headers=headers, data=json.dumps(data))
    if result.status_code == 204:
        return "SUCCESS"
    else:
        print("Subscription Suspension Failed:")
        print(result.reason)
        print(result.content)
        return "FAILED"
        
def cancelSubscription(subscriptionID, reason=None):
    token = getAccessToken(envConfig["PAYPAL_ID"],envConfig["PAYPAL_SECRET"])
    url =f"{baseURL}//v1/billing/subscriptions/{subscriptionID}/cancel"
    headers = {
        "Authorization": f"Bearer {token.token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    data = {}
    if reason:
        data={
            "reason":reason
        }
    else:
        data = {
            "reason": "Client cancelled subscription"
        }
    result = requests.post(url=url, headers=headers, data=json.dumps(data))
    if result.status_code == 204:
        return "SUCCESS"
    else:
        print("Subscription Cancellation Failed:")
        print(result.reason)
        print(result.content)
        return "FAILED"
     
def activateSubscription(subscriptionID):
    token = getAccessToken(envConfig["PAYPAL_ID"],envConfig["PAYPAL_SECRET"])
    url =f"{baseURL}//v1/billing/subscriptions/{subscriptionID}/activate"
    headers = {
        "Authorization": f"Bearer {token.token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    data = {
        "reason": "Client re-activated subscription"
    }
    result = requests.post(url=url, headers=headers, data=json.dumps(data))
    if result.status_code == 204:
        return "SUCCESS"
    else:
        print("Subscription Activation Failed:")
        print(result.reason)
        print(result.content)
        return "FAILED"

#Product Functions

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

#Webhook Management

def checkHooks(hookURL=None, token=db.Token):
    url=f"{baseURL}/v1/notifications/webhooks"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token.token}"
    }
    result = requests.get(url=url, headers=headers)
    print("Checking Hooks . . .")
    result.raise_for_status()
    jsonResult = result.json()
    if jsonResult["webhooks"]:
        if len(jsonResult["webhooks"]) > 1:
            for entry in jsonResult["webhooks"][1:]:
                deleteHook(entry["id"], token)
        if hookURL:
            if jsonResult["webhooks"][0]["url"] != hookURL:
                deleteHook(jsonResult["webhooks"][0]["id"], token)
                return createHook(hookURL, token)
            else:
                print(f"Webhook found: {jsonResult["webhooks"][0]["id"]}")
                return jsonResult["webhooks"][0]["id"]
    else:
        if hookURL:
            return createHook(hookURL, token)

def createHook(hookURL, token=db.Token):
    url = f"{baseURL}/v1/notifications/webhooks"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token.token}"
    }
    data = {
        "url":hookURL,
        "event_types":[
            {
                "name":"*"
            }
        ]
    }
    print(f"Creating Webhook: {hookURL}")
    result = requests.post(url=url, headers=headers, data=json.dumps(data))
    try:
        result.raise_for_status()
        jsonResult = result.json()
        return jsonResult["id"]
    except Exception as e:
        print(f"Failed creating webhook: {e}")
        print(result.reason)
        print(result.content)

def deleteHook(id, token=db.Token):
    url=f"{baseURL}/v1/notifications/webhooks/{id}"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token.token}"
    }
    print(f"Deleting Webhook: {id}")
    deleteResult = requests.delete(url=url, headers=headers)
    if deleteResult.status_code != 204:
        print("Error Deleting webhook")
        print(deleteResult.reason)
        print(deleteResult.content)
        raise ValueError("Error Deleting webhook")

def verifyHook(data, header):
    url=f"{baseURL}/v1/notifications/verify-webhook-signature"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token.token}"
    }
    hookID = None
    with open("product_list.json", "r") as file:
        product_list = json.load(file)
        hookID = product_list["hooks"][0]["id"]
    data = {
        "auth_algo": header["PAYPAL-AUTH-ALGO"],
        "cert_url": header["PAYPAL-CERT-UR"],
        "transmission_id": header["PAYPAL-TRANSMISSION-ID"],
        "transmission_sig": header["PAYPAL-TRANSMISSION-SIG"],
        "transmission_time": header["PAYPAL-TRANSMISSION-TIME"],
        "webhook_id": hookID,
        "webhook_event":{
            "event_version": data["event_version"],
            "resource_version": data["resource_version"]
        }
    }
    result = requests.post(url=url, headers = headers, data=json.dumps(data))
    try:
        result.raise_for_status()
        jsonResult = result.json()
        return jsonResult["verification_status"]
    except Exception as e:
        print(f"Failed verification of webhook: {e}")
        print(result.reason)
        print(result.content)
        return "FAILED"


#General Functions

def findProducts(id=None):
    "Return a list of products on file, or specific product by ID"
    if not id:
        prodList = None
        with open("product_list.json", "r") as file:
            prodList = json.load(file)
        if prodList:
            return prodList["products"]
        else:
            return None
    else:
        prodList = None
        with open("product_list.json", "r") as file:
            prodList = json.load(file)
        if prodList:
            for prod in prodList["products"]:
                if prod["id"] == id:
                    return prod
        else:
            return None

def findSubscriptions():
    "Returns a list of subscription plans on file"
    prodList = None
    with open("product_list.json", "r") as file:
        prodList = json.load(file)
    if prodList:
        return prodList["plans"]
    else:
        return None

def setupPaypal(hookURL=None):
    "Setup and Update products and subscriptions, to be run every update"
    token = getAccessToken(envConfig["PAYPAL_ID"],envConfig["PAYPAL_SECRET"])
    
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

    if product_list["hooks"]:
        id = checkHooks(hookURL, token)
        if id != product_list["hooks"][0]["id"]:
            deleteHook(id, token)
            newID = createHook(hookURL, token)
            if newID:
                product_list["hooks"][0]["id"] = newID
                with open("product_list.json", "w") as file:
                    json.dump(product_list, file, indent=4)
        else:
            print("Hook id and url checked!")
    else:
        newID = createHook(hookURL, token)
        if newID:
            product_list["hooks"].append({"id":newID})
            with open("product_list.json", "w") as file:
                json.dump(product_list, file, indent=4)
