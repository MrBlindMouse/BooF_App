
import requests, json, time
import hashlib, base64, hmac
import db, valr

def sendMail(token, content, subject, recipient = "admin@bmd-studios.com", sender = "BooF <admin@bmd-studios.com>", ):
    url = "https://api.postmarkapp.com/email"
    header = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Postmark-Server-Token": token
    }
    payload = {
        "From": sender,
        "To": recipient,
        "Subject": subject,
        "HtmlBody": content,
        "MessageStream": "outbound"
    }
    result = requests.post(url=url, headers=header, json=payload)
    print(result.reason)
    print(result.content)



if __name__ == "__main__":
    content = """
    <h1>Hello</h1>
    <h2>MrBlindMouse</h2>
    <p>The Postmark Email service is working perfectly!</p>
    """
    recipient = "neljohan1206@gmail.com"
    sendMail('956780e7-728b-49c0-b9f8-7ceb355bd27f', content, "BooF Test")