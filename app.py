from flask import Flask, render_template, request, redirect, url_for, jsonify, session, abort, send_from_directory
from werkzeug.exceptions import HTTPException
from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField, BooleanField, PasswordField, HiddenField, SelectField, IntegerField, RadioField
from wtforms.validators import DataRequired, Email, Length, EqualTo, ValidationError, NumberRange
from dotenv import dotenv_values
import time, json, requests
import hashlib, base64, hmac
import shortuuid, uuid

import db, valr, postmark, paypal

app = Flask("BooF", static_folder='static')
envConfig = dotenv_values(".env")
app.config["SECRET_KEY"] = envConfig["APP_SECRET"]
app.config['PERMANENT_SESSION_LIFETIME'] = 604800
app.config['SESSION_COOKIE_SECURE'] = False #Set to True in production
app.config['SESSION_COOKIE_SAMESITE']='Strict' #Set to 'Lax' from 'Strict' if something strange happens with cookies
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_TIME = 15*60

paypalID = envConfig["PAYPAL_ID"]
paypalSecret = envConfig["PAYPAL_SECRET"]
paypalMode = envConfig["PAYPAL_MODE"]

turnstileKey = envConfig["TURNSTILE_KEY"]
turnstileSecret = envConfig["TURNSTILE_SECRET"]

# Forms
class loginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    permanent = BooleanField("Keep me signed in", default=False)
    submit = SubmitField("Login")

class signupForm(FlaskForm):
    name = StringField("Username", validators=[DataRequired(),Length(min=3, max=20, message="Username must be 3-20 characters long")])
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=8, message="Password must be at least 8 characters")], name='password')
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password', message="Passwords must match")])
    submit = SubmitField('Sign Up')

    def validate_email(self, email):
        user = db.getUsers(email=email.data)
        if user:
            raise ValidationError('That email is already registered.')

def profileForm(user):
    class Form(FlaskForm):
        name = StringField("Username", validators=[DataRequired(),Length(min=3, max=20, message="Username must be 3-20 characters long")], default=user.name)
        oldPassword = PasswordField("Previous Password")
        password = PasswordField("New Password")
        confirm_password = PasswordField('Confirm New Password', validators=[EqualTo('password', message="Passwords must match")])
        submit = SubmitField('Update')

        def validate_password(self, field):
            if len(field.data) > 8:
                if len(self.oldPassword.data) == 0:
                    raise ValidationError("Confirm your previous password!")
            elif len(field.data) > 0:
                raise ValidationError("Password must be at least 8 characters")
            elif len(field.data) == 0 and len(self.oldPassword.data) != 0:
                raise ValidationError("Please enter your new password.")
    return Form()

class createBotForm(FlaskForm):
    name = StringField("Username", validators=[DataRequired(),Length(min=3, max=20, message="Bot name must be 3-20 characters long")], default="BooF Bot")
    key = StringField("API Key", validators=[DataRequired()])
    secret = StringField("API Secret", validators=[DataRequired()])
    submit = SubmitField("Create")

def botConfigForm(bot = db.Bot):
    class Form(FlaskForm):
        name = StringField("Bot Name",validators=[DataRequired(),Length(min=3, max=20, message="Username must be 3-20 characters long")], default=bot.name)
        currency = SelectField("Bot Currency", choices=["ZAR","USDC","USDT"], default=bot.currency)
        margin = IntegerField("Trading Margin", validators=[DataRequired(), NumberRange(min=2, max=15)], default=int(bot.margin*100))
        refinedWeight = BooleanField("Refined Weight", default=bot.refined_weight)
        dynamicMargin = BooleanField("Dynamic Margin", default=bot.dynamic_margin)
        downturnProtection = BooleanField("Downturn Protection", default=bot.downturn_protection)
        active = BooleanField("Active", default=bot.active)
        submit = SubmitField("Update")
    return Form()

def userLogin(email, password):
    user = db.getUsers(email=email)
    if user and user.password == password:
        return True
    else:
        return False

class createResetForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    submit = SubmitField("Reset")

#Utill

def trunc(value,dec):
    a = int(value*(10**dec))
    return float(a/(10**dec))

#Emails

def userVerificationEmail(user = db.User):
    config = valr.Config()
    config.loadState()
    key = config.verifySalt
    ts = int(time.time())

    recipient = user.email

    verifyKey = encodeToken(user.id, key, ts=ts)
    token=db.Token([0,verifyKey,ts,user.id,"VERIFY",24])
    token.post()

    body = f"""
        <p style='color:black;'>Hi {user.name},</p><br>
        <p style='color:black;'>Thank you for joining BooF Bots! We’re excited to have you on board.<p>
        <p style='color:black;'>To complete your account setup, please verify your email address by clicking the link below:</p>
        <p style='color:black;'><a href="https://www.boof-bots.com/verify/{verifyKey}">Verify Your Account</a></p>
        <p style='color:black;'>Alternatively, you can copy and paste the following URL into your web browser:</p>
        <p style='color:black;'>https://www.boof-bots.com/verify/{verifyKey}</p>
        <p style='color:black;'>Please note that this verification link will expire in 24 hours. If you need a new link, you can request one from your profile page.</p>
        <p style='color:black;'>Once verified, you’ll be able to create and configure bots for your VALR account with ease.</p>
        <p style='color:black;'>We’re here to help—please reach out if you have any questions!</p>
        <p style='color:black;'>Best Regards,</p>
        <p style='color:black;'>&emsp;The BooF Team</p>
    """
    htmlBody = render_template('emailBase.html', message=body)
    postmark.sendMail(config.postmarkKey, htmlBody, "BooF Account Verivication", recipient=recipient)

def encodeToken(id, key, ts=None):
    if ts == None:
        ts = int(time.time())
    
    payload = {
        "id":id,
        "ts":ts,
        "nonce":shortuuid.ShortUUID().random(8)
    }

    jsonPayload = json.dumps(payload, sort_keys=True)
    bytesPayload = jsonPayload.encode('utf-8')
    encodedPayload = base64.urlsafe_b64encode(bytesPayload).decode('utf-8').rstrip('=')

    hmacObj = hmac.new(key.encode('utf-8'), encodedPayload.encode('utf-8'), hashlib.sha256)
    sig = base64.urlsafe_b64encode(hmacObj.digest()).decode('utf-8').rstrip('=')

    return f"{encodedPayload}.{sig}"

def decodeToken(token):
    config = valr.Config()
    config.loadState()
    key = config.verifySalt
    encodedPayload, sig = token.split('.')
    hmacObj = hmac.new(key.encode('utf-8'), encodedPayload.encode('utf-8'), hashlib.sha256)
    expectedSig = base64.urlsafe_b64encode(hmacObj.digest()).decode('utf-8').rstrip('=')
    if not hmac.compare_digest(sig, expectedSig):
        return False
    encodedPayload += '=' * (-len(encodedPayload) % 4)
    bytesPayload = base64.urlsafe_b64decode(encodedPayload)
    payload = json.loads(bytesPayload.decode('utf-8'))
    if 'id' not in payload or 'ts' not in payload or 'nonce' not in payload:
        return False   
    return payload

def passwordResetEmail(user=db.User):
    config = valr.Config()
    config.loadState()
    key = config.verifySalt
    ts = int(time.time())
    
    verifyKey = encodeToken(user.id, key, ts=ts)
    token=db.Token([0,verifyKey,ts,user.id,"RESET",1])
    token.post()
    
    body=f"""
        <p style='color:black;'>Hi {user.name},</p><br>
        <p style='color:black;'>A request to reset your password has been submitted. If this was not you, please ignore this email.<p>
        <p style='color:black;'>To reset your password, click on the below link and follow the instructions on the webpage:</p>
        <p style='color:black;'><a href="https://www.boof-bots.com/verify/{verifyKey}">Verify Link</a></p>
        <p style='color:black;'>- or -</p>
        <p style='color:black;'>Copy the following into you web browser:</p>
        <p style='color:black;'>https://www.boof-bots.com/verify/{verifyKey}</p>
        <p style='color:black;'>This link will only stay active for 1 hour. If this time has passed, please resubmit the request.</p><br>
        <p style='color:black;'>Hope all goes well!</p>
        <p style='color:black;'>&emsp;The BooF Team</p>
        """
    htmlBody = render_template('emailBase.html', message=body)
    postmark.sendMail(config.postmarkKey, htmlBody, "Password Reset Request", recipient=user.email)


#Routes

@app.route('/login', methods=["GET", "POST"])
def login():
    if 'id' in session and session.modified == False:
        return redirect(url_for('home'))
    message = []
    if session.modified:
        session.pop('id', default=None)
        message.append({
            "type":"ERROR",
            "message":"Stop that!"
        })

    form = loginForm()

    if "attempt" in session and session["attempt"] == MAX_FAILED_ATTEMPTS:
        if int(time.time()) - session["last_attempt_time"] < LOCKOUT_TIME:
            message.append({
                "type":"ERROR",
                "message":"Too many failed attempts, try again later."
            })
        else:
            session.pop("attempt", None)
            return redirect(url_for('login'))
    else:
        if form.validate_on_submit():
            turnstileToken = request.form.get('cf-turnstile-response')
            if not turnstileToken:
                session["error"] = "Turnstile failed!"
                return redirect(url_for('login'))
            url = "https://challenges.cloudflare.com/turnstile/v0/siteverify" 
            data={
                'secret': turnstileSecret,
                'response':turnstileToken
            }
            headers = {'Content-Type':'application/json'}
            result = requests.post(url=url, data=json.dumps(data), headers=headers)
            try:
                result.raise_for_status()
                jsonResult = result.json()
                if jsonResult["success"] != True:
                    session["error"] = "Turnstile verification failed! Are you a bot?"
                    return redirect(url_for('login'))
            except Exception as e:
                session["error"] = "Turnstile verification failed!"
                return redirect(url_for('login'))

            email = form.email.data
            password = form.password.data
            permanent = form.permanent.data
            if userLogin(email,password):
                if permanent:
                    session.permanent = True
                session.pop("attempt", None)
                session.pop('last_attempt_time', None)
                user = db.getUsers(email=email)
                session["id"] = user.id
                return redirect(url_for('home'))
            else:
                session["error"] = "Incorrect email or password"
                session["last_attempt_time"] = int(time.time())
                if "attempt" in session:
                    session["attempt"] += 1
                else:
                    session["attempt"] = 1
                time.sleep(1)
                return redirect(url_for('login'))

    if "error" in session:
        message.append({
                "type":"ERROR",
                "message":session["error"]
            })
        session.pop("error", None)
    if "message" in session:
        message.append({
                "type":"INFO",
                "message":session["message"]
            })
        session.pop("message", None)

    return render_template('login.html', form=form, turnstileKey=turnstileKey, messages=message, meta="BooF Bots, Automated Trading Bots for Valr")

@app.route('/signup', methods=["GET","POST"])
def signup():
    form = signupForm()
    if form.validate_on_submit():
        turnstileToken = request.form.get('cf-turnstile-response')
        if not turnstileToken:
            session["error"] = "Turnstile failed!"
            return redirect(url_for('login'))
        url = "https://challenges.cloudflare.com/turnstile/v0/siteverify" 
        data={
            'secret': turnstileSecret,
            'response':turnstileToken
        }
        headers = {'Content-Type':'application/json'}
        result = requests.post(url=url, data=json.dumps(data), headers=headers)
        try:
            result.raise_for_status()
            jsonResult = result.json()
            if jsonResult["success"] != True:
                session["error"] = "Turnstile verification failed! Are you a bot?"
                return redirect(url_for('login'))
        except Exception as e:
            session["error"] = "Turnstile verification failed!"
            return redirect(url_for('login'))
        ts = int(time.time())
        name = form.name.data
        email = form.email.data
        password = form.password.data
        newUser = db.User([0, name, email, password, False, json.dumps([{"code":0,"ts":ts,"description":"New Unverified account"}])])
        newUser.post()
        user = db.getUsers(email=newUser.email) # To find proper user.id
        bonus = db.Credit([0, user.id, 0, "", 0, 1, "BONUS", ts])
        bonus.post()
        userVerificationEmail(user)
        message = db.Message([0, user.id, "INFO", "An email has been sent to your email address, follow the instructions to verify your account"])
        message.post()
        session["message"] = "Signup Successful! Please log in using email and password"
        valr.logPost("New user signed up",'1')
        return redirect(url_for('login'))
    return render_template("signup.html", form=form, turnstileKey=turnstileKey, meta="BooF Signup")

@app.route('/home')
def home():
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))
    if 'id' not in session:
        return redirect(url_for('login'))

    #Loading User data  
    userData = db.getUsers(id=session["id"])
    if not userData:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))
    credits = db.getCredits(user_id=userData.id)
    user={
        "name":userData.name,
        "equity":0,
        "verified":userData.verified,
        "credits": credits["credit"],
        "time": credits["time"]
    }

    config = valr.Config()
    config.loadState()
    bots = []
    botData = db.getBots(user_id=session["id"])
    for bot in botData:
        bots.append(bot.id)
        if bot.currency == "ZAR":
            user["equity"] += bot.equity
        elif bot.currency == "USDC":
            user["equity"] += bot.equity*config.USDCZAR
        elif bot.currency == "USDT":
            user["equity"] += bot.equity*config.USDTZAR

    #Loading User messages
    messages = []
    if userData.verified != 1:
        messages.append({
            "type":"INFO",
            "message":"Please complete the verification process to access full features."
        })
    if "message" in session:
        messages.append({
            "type":"INFO",
            "message":session["message"]
        })
        session.pop("message", None)  
    if "error" in session:
        messages.append({
            "type":"ERROR",
            "message":session["error"]
        })
        session.pop("error", None)
    userMessages = db.getMessages(userData.id)
    for message in userMessages:
        messages.append({
            "type":message.message_type,
            "message":message.message
        })
        message.delete()


    return render_template('home.html', messages=messages, user=user, bots=bots, meta="BooF Home page")

@app.route('/botstats/<id>')
def botstats(id):
    if 'id' not in session:
        return redirect(url_for('login'))
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))
    bot = db.getBots(id=id)
    if not bot:
        abort(400)
    if bot.user_id != session['id']:
        abort(400)

    #Loading Bot data
    now = int(time.time())
    bot = db.getBots(id=id)
    accountDetails={
        "high_account":'',
        "high_swing":0,
        "low_account":'',
        "low_swing":0
    }
    transaction = {}
    profit = 0
    botResult={
        "name": bot.name,
        "id":bot.id,
        "status": "Running . . .",
        "currency": bot.currency,
        "equity": bot.equity,
        "transaction":{},
        "accountDetails":{
                "high_account":'',
                "high_swing":0,
                "low_account":'',
                "low_swing":0
            },
        "profit": 0,
        "realizedProfit":0,
        "chartDates":[],
        "chartCount":[]
        }
    credit = db.getCredits(bot_id=bot.id)
    if not bot.active:
        if credit["credit"] > 0 and bot.downturn_protection:
            botResult["status"] = "Paused under <b>Donwturn Protection</b>."
        elif credit["credit"] <= 0:
            botResult["status"] = "Suspended!"
        else:
            botResult["status"] = "Paused."

    accounts = db.getActiveAccounts(bot_id=bot.id)
    for account in accounts:
        if account.direction == "UP" and trunc((account.swing*100),2) > botResult["accountDetails"]["high_swing"]:
            botResult["accountDetails"]["high_swing"] = trunc((account.swing*100),2)
            botResult["accountDetails"]["high_account"] = account.base
        elif account.direction == "DOWN" and trunc((account.swing*100),2) > botResult["accountDetails"]["low_swing"]:
            botResult["accountDetails"]["low_swing"] = trunc((account.swing*100),2)
            botResult["accountDetails"]["low_account"] = account.base
    transactions = db.getTransactions(bot.id)
    for line in reversed(transactions):
        if line.type in ["BUY","SELL"]:
            botResult["transaction"]={
                "base":line.base,
                "quote":line.quote,
                "type":line.type,
                "volume":line.volume,
                "price":trunc((line.value/line.volume),2)
            }
            break
    valrConfig = valr.Config()
    valrConfig.loadState()
    for account in accounts:
        buyVolume = 0
        buyValue = 0
        sellVolume = 0
        sellValue = 0
        fees = 0
        for line in transactions:
            if line.base == account.base and line.quote == bot.currency:
                if line.type == "BUY":
                    fees += line.fee
                    buyVolume += line.volume
                    buyValue += line.value
                elif line.type == "SELL":
                    fees += line.fee
                    sellVolume += line.volume
                    sellValue += line.value
        realizedProfit = 0
        if sellVolume != 0 and buyVolume != 0:
            realizedProfit = (((sellValue/sellVolume)-(buyValue/buyVolume))*min(sellVolume,buyVolume))-fees
        botResult["realizedProfit"] += realizedProfit
        for line in transactions:
            if line.base == account.base and line.quote == bot.currency:
                if line.type == "INVEST":
                    fees += line.fee
                    buyVolume += line.volume
                    buyValue += line.value
                elif line.type == "WITHDRAW":
                    fees += line.fee
                    sellVolume += line.volume
                    sellValue += line.value
        if sellVolume > buyVolume:
            diff = sellVolume-buyVolume
            buyVolume += diff
            buyValue += diff*account.price(valrConfig)
        elif sellVolume < buyVolume:
            diff = buyVolume-sellVolume
            sellVolume += diff
            sellValue += diff*account.price(valrConfig)
        totalProfit = 0
        if sellVolume != 0 and buyVolume != 0:
            totalProfit = (((sellValue/sellVolume)-(buyValue/buyVolume))*sellVolume)-fees
        botResult["profit"] += totalProfit

    ts = int(time.time())
    dates = []
    for i in range(28):
        date = time.localtime(ts-(i*24*60*60))
        dates.append(date)
    for date in dates:
        count = 0
        for transaction in transactions:
            if transaction.quote == bot.currency and transaction.type in ["SELL","BUY"]:
                entryDate = time.localtime(transaction.ts)
                if date.tm_mday == entryDate.tm_mday:
                    count += 1
        botResult["chartCount"].append(count)
        botResult["chartDates"].append(str(time.strftime("%d %b",date)))
    return botResult

@app.route('/botreport/<id>')
def botreport(id):
    if 'id' not in session:
        return redirect(url_for('login'))
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))
    bot = db.getBots(id=id)
    if bot.user_id != session['id']:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))
    valrConfig = valr.Config()
    valrConfig.loadState()
    data = {}
    data['name'] = bot.name
    data['status'] = bot.active
    data['equity'] = bot.equity
    data['currency'] = bot.currency
    data["margin"] = int(bot.margin*100)
    data['accounts'] = []
    data['trend'] = 1
    data['balance'] = bot.balance_value
    data['dynamicMargin'] = bot.dynamic_margin
    rsi = 0
    trend = 0
    currencyList = []
    if bot.currency == "ZAR":
        currencyList = valrConfig.ZAR
    elif bot.currency == "USDC":
        currencyList = valrConfig.USDC
    elif bot.currency == "USDT":
        currencyList = valrConfig.USDT

    for line in currencyList:
        rsi += line['rsi']
        trend += line['trend']
    data['trend'] = ((trend/len(currencyList))+(((rsi/len(currencyList))/100)+.5))/2

    data['weightedBalance'] = 0
    if bot.refined_weight:
        trendFactor = (8*((data['trend']-1)**2)) * ((data['trend']-1)/abs(data['trend']-1))
        #weight = 1+trendFactor
        weight = data['trend']
        weight = max(0.8, min(1, weight))
        data['weightedBalance'] = data['balance'] * weight

    accounts = db.getActiveAccounts(bot_id=id)
    transactions = db.getTransactions(bot_id=id)
    for account in accounts:
        price = 0
        atr=bot.margin
        volatility = 1
        for entry in currencyList:
            if entry['base'] == account.base:
                price = float(entry["price"])
                atr = float(entry["atr"]) if float(entry["atr"]) != 0 else bot.margin
                volatility = float(entry["volatility"]) if float(entry["volatility"]) > 0.1 else 0.1
                break
        accountEntry = {
            "base":account.base,
            "swing":account.swing,
            "direction":account.direction,
            "volume":account.volume,
            "price":price,
            "stake":account.stake,
            "volumeBought":0,
            "valueBought":0,
            "volumeSold":0,
            "valueSold":0,
            "avgBuyPrice":0,
            "avgSellPrice":0,
            "fees":0,
            "adjustedMargin":max(min(((bot.margin*2)+(bot.margin*volatility)+atr)/4,bot.margin*1.2),bot.margin*0.8)
        }
        for entry in transactions:
            if entry.base == account.base and entry.quote == bot.currency:
                if entry.type == "BUY":
                    accountEntry["volumeBought"] += entry.volume
                    accountEntry["valueBought"] += entry.value
                    accountEntry["fees"] += entry.fee
                elif entry.type == "SELL":
                    accountEntry["volumeSold"] += entry.volume
                    accountEntry["valueSold"] += entry.value
                    accountEntry["fees"] += entry.fee
        if accountEntry['volumeBought'] != 0:
            accountEntry["avgBuyPrice"] = accountEntry["valueBought"]/accountEntry["volumeBought"]
        if accountEntry['volumeSold'] != 0:
            accountEntry["avgSellPrice"] = accountEntry["valueSold"]/accountEntry["volumeSold"]
        
        data["accounts"].append(accountEntry)
    feeTotal = 0
    profitRealized = 0
    for entry in data["accounts"]:
        feeTotal += entry["fees"]
        profitRealized += min(entry["volumeBought"],entry["volumeSold"])*(entry["avgSellPrice"]-entry["avgBuyPrice"]) if entry["volumeBought"] > 0 and entry["volumeSold"] > 0 else 0

    data["totalFees"] = feeTotal
    data["realizedProfit"] = profitRealized
    credit = db.getCurrentCredits(bot_id=id)
    data["runtime"] = credit["time"]
    data["cost"] = credit["credit"]
    return render_template("report.html", data=data, meta="Bot Performance Report")

@app.route('/botconfig/<id>', methods=["POST","GET"])
def botconfig(id):
    if "id" not in session:
        return redirect(url_for('login'))
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))
    bot = db.getBots(id=id)
    form = botConfigForm(bot)
    if request.method=="POST":
        name = form.name.data
        currency = form.currency.data
        margin = int(form.margin.data)/100
        refinedWeight = form.refinedWeight.data
        dynamicMargin = form.dynamicMargin.data
        downturnProtection = form.downturnProtection.data
        active = form.active.data

        if currency != bot.currency:
            valr.updateCurrency(currency, bot)
            message = db.Message([0,bot.user_id,"INFO","Bot Currency Changed!"])
            message.post()

        if name != bot.name:
            bot.name = name
            bot.update()
            message = db.Message([0,bot.user_id,'INFO',"Bot Name Changed!"])
            message.post()

        if margin != bot.margin:
            bot.margin = margin
            bot.update()
            message = db.Message([0,bot.user_id,'INFO',"Trading Margin Updated!"])
            message.post()

        if refinedWeight != bot.refined_weight:
            bot.refined_weight = refinedWeight
            bot.update()
            msg = "Refined Weight Enabled!" if refinedWeight else "Refined Weight Disabled!"
            message = db.Message([0,bot.user_id,'INFO',msg])
            message.post()

        if dynamicMargin != bot.dynamic_margin:
            bot.dynamic_margin = dynamicMargin
            bot.update()
            msg = "Dynamic Margin Enabled!" if dynamicMargin else "Dynamic Margin Disabled!"
            message = db.Message([0,bot.user_id,'INFO',msg])
            message.post()

        if downturnProtection != bot.downturn_protection:
            bot.downturn_protection = downturnProtection
            bot.update()
            msg = "Downturn Protection Enabled!" if downturnProtection else "Downturn Protection Disabled!"
            message = db.Message([0,bot.user_id,'INFO',msg])
            message.post()
        if active != bot.active:
            bot.active = active
            bot.update()
            if active:
                message = db.Message([0,bot.user_id,"INFO",f"Bot '{bot.name}' started"])
                message.post()
                credit = db.Credit([0,bot.user_id,bot.id,'',0,0,'START',int(time.time())])
                credit.post()
            else:
                message = db.Message([0,bot.user_id,"INFO",f"Bot '{bot.name}' paused"])
                message.post()
                credit = db.Credit([0,bot.user_id,bot.id,'',0,0,'PAUSE',int(time.time())])
                credit.post()

        return redirect(url_for('home'))
    return render_template("botconfig.html", form=form, id=bot.id, meta="Bot configuration")

@app.route('/logout')
def logout():
    if "id" in session:
        session.pop("id", None)
        session["message"] = "You have been logged out."
    return redirect(url_for('login'))

@app.route('/about')
def about():
    return render_template('about.html', meta="The Why and the How of BooF Bots.")
    
@app.route('/howto')
def howto():
    return render_template('howto.html', meta="How to set up and use BooF Bots")
    
@app.route('/terms')
def terms():
    return render_template('terms.html', meta="The Terms and Conditions of Boof Bots")

@app.route('/verify/<uid>')
def verify(uid):
    data = decodeToken(uid)
    if not data:
        valr.logPost('Token altered!', '3')
        return abort(404)
    token = db.getTokens(uid)
    if not token:
        valr.logPost(f'Fake Token Used!<br> Submitted token: {uid}', '3')
        return abort(404)
    if token.ts != int(data["ts"]) or token.user_id != int(data["id"]):
        valr.logPost(f'Token altered!<br>DB token: {token.token}<br>Submitted token: {uid}', '3')
        return abort(404)
    ts = int(time.time())
    if (ts-token.ts) > (token.period*60*60):
        valr.logPost(f'Expired token used,<br>token: {uid}', '2')
        return abort(404)
    user = db.getUsers(id=token.user_id)
    if not user:
        valr.logPost(f'User for token does not exist, token: {token.token}', '3')
        return abort(404)
    if token.type == "VERIFY":
        user.verified = True
        user.update()
        message = db.Message([0,user.id,"INFO","Your account has successfully been verified!"])
        message.post()
        token.delete()
        return render_template("verify.html", type="VERIFY", user=user.name, meta="BooF User Verification")
    elif token.type == "RESET":
        newPassword = shortuuid.ShortUUID().random(8)
        user.password = newPassword
        user.update()
        message = db.Message([0, user.id,"WARNING","Your password has been reset, change your password ASAP!"])
        message.post()
        token.delete()
        return render_template("verify.html", user=user.name, type="RESET", password=newPassword, meta="BooF Password Reset Verification")
    return abort(404)

@app.route('/config', methods=["GET","POST"])
def config():
    if "id" not in session:
        return redirect(url_for('login'))
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))

    user = db.getUsers(id=session["id"])
    if not user:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))

    form = profileForm(user)
    verified = True if user.verified == 1 else False

    if form.validate_on_submit():
        if form.name.data != user.name:
            user.name = form.name.data
            user.update()
            message = db.Message([0, user.id, "INFO", "Username Updated!"])
            message.post()
        if form.oldPassword.data and form.oldPassword.data == user.password:
            user.password = form.password.data
            user.update()
            message = db.Message([0, user.id, "WARNING", "Password Updated!"])
            message.post()
        elif form.oldPassword.data and form.oldPassword.data != user.password:
            message = db.Message([0, user.id, "ERROR", "Incorrect Password"])
            message.post()

        return redirect(url_for('home'))

    return render_template("config.html", form=form, verified=verified, meta="BooF User Configuration")

@app.route('/resend')
def resend():
    if 'id' not in session:
        return redirect(url_for('login'))
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))
    user = db.getUsers(id=session['id'])
    userVerificationEmail(user)
    message = db.Message([0,user.id,'INFO','Verification email has been resent'])
    message.post()
    valr.logPost(f"Verification email requested for {user.email}, ID:{user.id}",'1')
    return redirect(url_for('home'))

@app.route('/market')
def market():
    valrConfig = valr.Config()
    valrConfig.loadState()
    zarTrend = 0
    zarRSI = 0
    for line in valrConfig.ZAR:
        zarTrend += line["trend"]
        zarRSI += line["rsi"]
    zarTrend = ((zarTrend/len(valrConfig.ZAR))+(((zarRSI/len(valrConfig.ZAR))/100)+.5))/2
    #zarTrend = ((zarTrend/len(valrConfig.ZAR))+((zarRSI/len(valrConfig.ZAR))/50))/2
    usdcTrend = 0
    usdcRSI = 0
    for line in valrConfig.USDC:
        usdcTrend += line["trend"]
        usdcRSI += line["rsi"]
    usdcTrend = ((usdcTrend/len(valrConfig.USDC))+(((usdcRSI/len(valrConfig.USDC))/100)+.5))/2
    #usdcTrend = ((usdcTrend/len(valrConfig.USDC))+((usdcRSI/len(valrConfig.USDC))/50))/2
    usdtTrend = 0
    usdtRSI = 0
    for line in valrConfig.USDT:
        usdtTrend += line["trend"]
        usdtRSI += line["rsi"]
    usdtTrend = ((usdtTrend/len(valrConfig.USDT))+(((usdtRSI/len(valrConfig.USDT))/100)+.5))/2
    #usdtTrend = ((usdtTrend/len(valrConfig.USDT))+((usdtRSI/len(valrConfig.USDT))/50))/2
    details={
        "ZARList":valrConfig.ZAR,
        "ZARTrend":trunc(zarTrend,3),
        "USDCList":valrConfig.USDC,
        "USDCTrend":trunc(usdcTrend,3),
        "USDTList":valrConfig.USDT,
        "USDTTrend":trunc(usdtTrend,3),
    }
    return render_template("market.html", details=details, meta="Technical Analysis of the Crypto Market Trends, for BooF Bots")

@app.route('/addbot', methods=["POST","GET"])
def addbot():
    if not "id" in session:
        return redirect(url_for('login'))
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))

    form = createBotForm()
    if form.validate_on_submit():
        name = form.name.data
        key = form.key.data
        secret = form.secret.data
        bots = db.getBots()
        for bot in bots:
            if bot.key == key:
                if session["id"] != bot.user_id:
                    message = db.Message([0, bot.user_id, "WARNING", "Someone tried to use your existing API key! Take steps to safeguard your VALR account!"])
                    message.post()
                    valr.logPost(f"User ID:{session['id']} just tried to use an API Key belonging to User ID:{bot.user_id}")
                message = db.Message([0, session["id"], "WARNING", "You just tried to use an existing API key!"])
                message.post()
                return redirect(url_for('home'))
        if valr.validateKeys(key, secret, session["id"]):
            bot = db.Bot([0, session["id"], name, key, secret, "ZAR", False, 0, 0, 0, 0, False, False, 0, False])
            bot.post()
            message = db.Message([0, session["id"], "INFO", "Success! New bot created!"])
            message.post()
        return redirect(url_for('home'))
    return render_template('addbot.html', form=form, meta="Add Bot")

@app.route('/buy')
def buy():
    if "id" not in session:
        return redirect(url_for('login'))
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))
    config = valr.Config()
    config.loadState()
    products = paypal.findProducts()
    plans = None
    subscriptionDetails = None
    subscription = db.getSubscriptions(user_id=session["id"])
    if not subscription:
        plans = paypal.findSubscriptions()
        for entry in plans:
            entry["custom_id"] = f"userID_{session["id"]}"
    else:
        subscriptionPlan = paypal.findSubscriptions()
        subscriptionDetails = {
            "name": subscriptionPlan[0]["name"],
            "status": subscription.status,
            "quantity": subscription.quantity,
            "price": float(subscriptionPlan[0]["billing_cycles"][0]["pricing_scheme"]["fixed_price"]["value"])*int(subscription.quantity),
            "planPrice": subscriptionPlan[0]["billing_cycles"][0]["pricing_scheme"]["fixed_price"]["value"],
            "planAmount": subscriptionPlan[0]["max_amount"],
        }
    credits = db.getCredits(user_id=session["id"], type="LIST")
    creditList = []
    ts = int(time.time())
    for credit in credits:
        if credit.description in ["CREDIT","BONUS"] and (credit.ts+(90*24*60*60))>ts:
            item = {
                "ts":credit.ts,
                "type":str(credit.description),
                "volume":credit.volume,
                "value":credit.value
            }
            creditList.append(item)

    
    return render_template('buy.html', creditList=creditList, productList=products, subscriptionList=plans, subscriptionDetails=subscriptionDetails, paypalClientId=config.paypalKey, meta="Buy Credits for BooF Bots")

@app.route('/create-order', methods=["POST"])
def create_order():
    if "id" not in session:
        return redirect(url_for('login'))
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))
    data = request.json
    print("Create Order:")
    print(json.dumps(data, indent=4))
    paypalResponse = paypal.createOrder(data["product_id"], data["amount"], session["id"])
    return jsonify({"id":paypalResponse["id"]})
    
@app.route('/capture-order', methods=["POST"])
def capture_order():
    if "id" not in session:
        return redirect(url_for('login'))
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))
    data = request.json
    print("Capture Order:")
    print(json.dumps(data, indent=4))
    paypalResponse = paypal.captureOrder(data, session["id"])
    return jsonify({"status":paypalResponse})
    
@app.route('/capture-subscription', methods=["POST"])
def capture_subscription():
    if "id" not in session:
        return redirect(url_for('login'))
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))
    data = request.json
    print("Capture subscription:")
    print(json.dumps(data, indent=4))

    paypalResponse = paypal.captureSubsription(data, session["id"])
    return jsonify({"status":paypalResponse})

@app.route('/suspend-subscription', methods=["POST"])
def suspend_subscription():
    if "id" not in session:
        return redirect(url_for('login'))
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))
    data = request.json
    print(json.dumps(data, indent=4))
    subscription = db.getSubscriptions(user_id=session["id"])
    action = paypal.suspendSubscription(subscription.subscription_id, data["reason"])
    if action == "SUCCESS":
        subscription.status = "SUSPENDED"
        subscription.update()
        message = db.Message([0,session["id"],'INFO','Your Subscription plan was successfully suspended.'])
        message.post()
    return jsonify({"status":action})
   
@app.route('/cancel-subscription', methods=["POST"])
def cancel_subscription():
    if "id" not in session:
        return redirect(url_for('login'))
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))
    data = request.json
    print(json.dumps(data, indent=4))
    subscription = db.getSubscriptions(user_id=session["id"])
    action = paypal.cancelSubscription(subscription.subscription_id, data["reason"])
    if action == "SUCCESS":
        subscription.delete()
        message = db.Message([0,session["id"],'INFO','Your Subscription plan was successfully cancelled.'])
        message.post()
    return jsonify({"status":action}) 

@app.route('/activate-subscription', methods=["POST"])
def activate_subscription():
    if "id" not in session:
        return redirect(url_for('login'))
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))
    subscription = db.getSubscriptions(user_id=session["id"])
    action = paypal.activateSubscription(subscription.subscription_id)
    if action == "SUCCESS":
        subscription.status = "ACTIVE"
        subscription.update()
        message = db.Message([0,session["id"],'INFO','Your Subscription plan was successfully re-activated.'])
        message.post()
    return jsonify({"status":action})

@app.route('/update-subscription', methods=["POST"])
def update_subscription():
    if "id" not in session:
        return redirect(url_for('login'))
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))
    subscription = db.getSubscriptions(user_id=session["id"])
    data = request.json
    action = paypal.reviseSubscription(subscription.subscription_id, int(data["amount"]))
    if action == "SUCCESS":
        subscription.quantity = int(data["amount"])
        subscription.update()
        message = db.Message([0,session["id"],'INFO',f'Your Subscription quantity was successfully updated to {data["amount"]}x Credits.'])
        message.post()
    return jsonify({"status":action})


@app.route('/reset', methods=["GET","POST"])
def reset():
    form = createResetForm()
    if form.validate_on_submit():
        turnstileToken = request.form.get('cf-turnstile-response')
        if not turnstileToken:
            session["error"] = "Turnstile failed!"
            return redirect(url_for('login'))
        url = "https://challenges.cloudflare.com/turnstile/v0/siteverify" 
        data={
            'secret': turnstileSecret,
            'response':turnstileToken
        }
        headers = {'Content-Type':'application/json'}
        result = requests.post(url=url, data=json.dumps(data), headers=headers)
        try:
            result.raise_for_status()
            jsonResult = result.json()
            if jsonResult["success"] != True:
                session["error"] = "Turnstile verification failed! Are you a bot?"
                return redirect(url_for('login'))
        except Exception as e:
            session["error"] = "Turnstile verification failed!"
            return redirect(url_for('login'))


        email=form.email.data
        user = db.getUsers(email=email)
        if not user:
            session["message"]="Reset email has been sent, please check your email for futher instructions."
            return redirect(url_for("login"))

        passwordResetEmail(user)
        session["message"]="A password reset email has been sent. Please check your inbox for further instructions."
        valr.logPost(f"Password Reset Requested for {user.email}, ID:{user.id}",'1')
        return redirect(url_for("login"))

    return render_template('reset.html', form=form, turnstileKey=turnstileKey, meta="Password Reset")

@app.route('/confirmclear/<id>')
def confirm_clear(id):
    return render_template("confirmClear.html", meta="Confirm clearing transaction data", id=id)

@app.route('/clear/<id>')
def clear(id):
    if "id" not in session:
        return redirect(url_for('login'))
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))
    bot = db.getBots(id=id)
    transactions = db.getTransactions(bot_id=bot.id)
    if transactions:
        for entry in transactions:
            entry.delete()
    message = db.Message([0,bot.user_id,"INFO",f"Transactions for Bot:'{bot.name}' has been cleared"])
    message.post()
    accounts = db.getActiveAccounts(bot_id=bot.id)
    config = valr.Config()
    config.loadState()
    if accounts:
        for account in accounts:
            value = account.volume * account.price(config)
            newTransaction = db.Transaction([0,bot.id,"INVEST",account.volume,value,account.base,bot.currency,int(time.time()),0])
            newTransaction.post()
    bot.reset()
    return redirect(url_for('home'))

@app.route('/delete/<id>')
def delete(id):
    if "id" not in session:
        return redirect(url_for('login'))
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))

    bot = db.getBots(id=id)

    if int(session['id']) != bot.user_id or bot == None:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))

    return render_template('delete.html', name=bot.name, id=bot.id, meta="Remove Bot")

@app.route('/deletebot/id')
def deleteBot(id):
    if "id" not in session:
        return redirect(url_for('login'))
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))

    bot = db.getBots(id=id)

    if int(session['id']) != bot.user_id or bot == None:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))
    transactions = db.getTransactions(bot.id)
    for entry in transactions:
        entry.delet()
    message = db.Message([0,bot.user_id,"INFO","Bot Deleted and Records Cleared"])
    message.post()
    bot.delete()
    return redirect(url_for('home'))

@app.route('/close')
def close():
    if "id" not in session:
        return redirect(url_for('login'))
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))
    return render_template('close.html', meta="Close user account")

@app.route('/closeaccount')
def closeAccount():
    if "id" not in session:
        return redirect(url_for('login'))
    if session.modified:
        session.pop('id', default=None)
        session["error"] = "Stop that!"
        return redirect(url_for('login'))
    user = db.getUsers(id=int(session["id"]))
    valr.logPost(f'Closing account for {user.email}, id:{user.id}','1')
    bots = db.getBots(user_id=user.id)
    for bot in bots:
        transactions = db.getTransactions(bot_id=bot.id)
        for entry in transactions:
            entry.delete()
        accounts = db.getActiveAccounts(bot_id=bot.id)
        for account in accounts:
            account.delete()
        if bot.active:
            credit = db.Credit([0,user.id,bot.id,'',0,0,'PAUSE',int(time.time())])
            credit.post()
        bot.delete()
    session["message"]="Account Closed"
    session.pop('id', default=None)
    return redirect(url_for('login'))


@app.errorhandler(Exception)
def handle_exception(e):
    # pass through HTTP errors
    if "id" in session:
        session.pop('id', default=None)
    if isinstance(e, HTTPException):
        jsonE = {
            "code":e.code,
            "name":e.name,
            "description":e.description
        }
        return render_template('error.html', e=jsonE, meta=f"Error Page {e.code}: {e.name}"), e.code

    httpEnvirons = request.environ
    str=""
    str += f"IP: {httpEnvirons.get("HTTP_CF_CONNECTING_IP")}<br>"
    str += f"Country: {httpEnvirons.get("HTTP_CF_IPCOUNTRY")}<br>"
    valr.logPost(f"Error code received from app<br>{e}<br>{request.url}<br>{str}")
    
    session["error"] = f"Error:'{e}"

    return redirect(url_for('login'))

@app.route('/robots.txt')
@app.route('/sitemap.xml')
def static_from_root():
    return send_from_directory(app.static_folder, request.path[1:])

@app.route('/hook', methods=["POST"])
def hook():
    header = request.headers
    data = request.json
    action = paypal.verifyHook(data, header)
    if "event_type" not in data:
        msg=f"Unformatted Webhook received:<br>{json.dumps(data,indent=4)}"
        valr.logPost(msg.replace('\n','<br>').replace('    ','&emsp;'),'3')
        return jsonify({"status":"FAILED"}),400

    if action == "SUCCESS":
        if data["event_type"] == "PAYMENT.CAPTURE.COMPLETE":
            string = data["resource"]["custom_id"]
            key, userID = string.split('_')
            transactionID = data["resource"]["supplementary_data"]["related_ids"]["order_id"]
            userCredits = db.getCredits(user_id=userID,type="LIST")
            found = False
            for credit in userCredits:
                if credit.deposit_nr == transactionID:
                    found=True
                    break
            if not found:
                orderDetails = paypal.getOrderDetails(transactionID)
                if orderDetails["status"] == "COMPLETED":
                    value = float(orderDetails["purchase_units"][0]["amount"]["value"])
                    volume = int(orderDetails["purchase_units"][0]["items"][0]["quantity"])
                    newCredit = db.Credit([0,userID,0,transactionID,value,volume,"CREDIT",int(time.time())])
                    newCredit.post()
                    message=db.Message([0,userID,'INFO',f"Purchase Successful! {volume}x Credits added to account!"])
                    message.post()
                else:
                    dataString = json.dumps(data, indent=4)
                    msg=f"Paypal webhook<br>{dataString.replace('\n','<br>').replace('    ','&emsp;')}"
                    valr.logPost(msg,'1')
        elif data["event_type"] == "BILLING.SUBSCRIPTION.ACTIVATED":
            string = data["resource"]["custom_id"]
            key, userID = string.split('_')
            planID = data["resource"]["plan_id"]
            subscriptionID = data["resource"]["id"]
            status = data["resource"]["status"]
            subscription = db.getSubscriptions(userID)
            if subscription:
                if sub.status != status:
                    sub.status = status
                    sub.update()
                    message = db.Message(0,userID,'INFO','Your Subscription plan was successfully re-activated.')
                    message.post()
            else:
                dataString = json.dumps(data, indent=4)
                msg=f"UnRegistered Paypal Subscription Activation hook:<br>{dataString.replace('\n','<br>').replace('    ','&emsp;')}"
                valr.logPost(msg)
        elif data["event_type"] == "BILLING.SUBSCRIPTION.SUSPENDED":
            string = data["resource"]["custom_id"]
            key, userID = string.split('_')
            planID = data["resource"]["plan_id"]
            subscriptionID = data["resource"]["id"]
            status = data["resource"]["status"]
            subscription = db.getSubscriptions(userID)
            if subscription:
                if sub.status != status:
                    sub.status = status
                    sub.update()
                    message = db.Message(0,userID,'INFO','Your Subscription plan was successfully suspended.')
                    message.post()
            else:
                dataString = json.dumps(data, indent=4)
                msg=f"UnRegistered Paypal Subscription Suspension hook:<br>{dataString.replace('\n','<br>').replace('    ','&emsp;')}"
                valr.logPost(msg)
        elif data["event_type"] == "BILLING.SUBSCRIPTION.UPDATED":
            string = data["resource"]["custom_id"]
            key, userID = string.split('_')
            planID = data["resource"]["plan_id"]
            subscriptionID = data["resource"]["id"]
            quantity = data["resource"]["quantity"]
            subs = db.getSubscriptions(userID)
            if subs:
                if subs.quantity != quantity:
                    subs.quantity = quantity
                    subs.update()
                    message = db.Message(0,userID,'INFO',f'Your Subscription quantity was successfully updated to {quantity}x Credits.')
                    message.post()
            else:
                msg = json.dumps(data, indent=4)
                valr.logPost(f"Un-recorded subscription update hook:<br>{msg.replace('\n','<br>').replace('    ','&emsp;')}")

        elif data["event_type"] == "BILLING.SUBSCRIPTION.CREATED":
            string = data["resource"]["custom_id"]
            key, userID = string.split('_')
            planID = data["resource"]["plan_id"]
            subscriptionID = data["resource"]["id"]
            subs = db.getSubscriptions(userID)
            if not subs:
                data = {
                    "planID":subscriptionID
                }
                paypal.captureSubsription(data)
            else:
                if subs.subscription_id != subscriptionID:
                    paypal.cancelSubscription(subs.subscription_id,"Hook for new subscription")
                    subs.delete()
                    data = {
                        "planID":subscriptionID
                    }
                    paypal.captureSubsription(data)
                    message = db.Message(0,userID,'INFO','Your Subscription plan was successfully activated.')
                    message.post()
            #Finish Sub creation Logic
        elif data["event_type"] == "PAYMENT.SALE.COMPLETED":
            string = data["resource"]["custom_id"]
            key, userID = string.split('_')
            planID = data["resource"]["plan_id"]
            subsID = data["resource"]["id"]
            subs = db.getSubscriptions(userID)
            value = data["resource"]["amount"]["total"]
            if subs:
                newCredit = db.Credit([0,userID,0,subsID,value,subs.quantity,"CREDIT", int(time.time())])
                newCredit.post()
                message = db.Message([0,userID,'INFO',f"Payment received, {subs.quantity}x credits added to account"])
                message.post()
            else:
                msg = json.dumps(data, indent=4)
                valr.logPost(f"Un-recorded subscription payment received:<br>{msg.replace('\n','<br>').replace('    ','&emsp;')}")

        else:
            msg=f"Unknown Paypal Webhook received:<br>{json.dumps(data,indent=4)}"
            valr.logPost(msg.replace('\n','<br>').replace('    ','&emsp;'),'2')

        return jsonify({"status":action})

    else:
        dataString = json.dumps(data, indent=4)
        msg=f"Unverified Paypal Webhook<br>{dataString.replace('\n','<br>').replace('    ','&emsp;')}"
        valr.logPost(msg,'3')
        return jsonify({"status":"FAILED"}),400


@app.template_filter('date')
def tsConvert(s):
    return time.strftime("%Y-%m-%d %H:%M:%S",time.gmtime(s))

if __name__ == "__main__": 
    print("Dev Server!")

    app.run(debug=True, port="5005", host="192.168.10.100")
