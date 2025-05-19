import os, time, datetime, json
import sqlite3
import base64, hmac, hashlib


db_path = "data/database.db"

def setupDB():
    """
    Creates database if it does not exists, adding or removing column as needed to match schema
    DB Path: data/database.db
    *New columns must not be NOT NULL
    """
    schema = [
        {"table_name":"user_table",
        "table_columns":{
            "id":"INTEGER PRIMARY KEY AUTOINCREMENT",
            "name":"TEXT NOT NULL",
            "email":"TEXT NOT NULL",
            "password":"TEXT NOT NULL",
            "verified":"INTEGER DEFAULT 0",
            "reminder":"TEXT DEFAULT '[] '"
            }},
        {"table_name":"bot_table",
        "table_columns":{
            "id":"INTEGER PRIMARY KEY AUTOINCREMENT",
            "user_id":"INTEGER NOT NULL",
            "name":"TEXT NOT NULL",
            "key":"TEXT NOT NULL",
            "secret":"TEXT NOT NULL",
            "currency":"TEXT NOT NULL",
            "active":"BOOL NOT NULL",
            "equity":"FLOAT DEFAULT 0",
            "quote_balance":"FLOAT DEFAULT 0",
            "balance_nr":"INTEGER NOT NULL",
            "balance_value":"FLOAT NOT NULL",
            "margin":"FLOAT DEFAULT 0.01",
            "dynamic_margin":"BOOL DEFAULT FALSE",
            "refined_weight":"BOOL DEFAULT FALSE"
            }},
        {"table_name":"active_account_table",
        "table_columns":{
            "id":"INTEGER PRIMARY KEY AUTOINCREMENT",
            "bot_id":"INTEGER NOT NULL",
            "base":"TEXT NOT NULL",
            "volume":"FLOAT NOT NULL",
            "stake":"FLOAT DEFAULT 0",
            "swing":"FLOAT NOT NULL",
            "direction":"TEXT DEFAULT 'NEUTRAL"
            }},
        {"table_name":"transaction_table",
        "table_columns":{
            "id":"INTEGER PRIMARY KEY AUTOINCREMENT",
            "bot_id":"INTEGER NOT NULL",
            "type":"TEXT NOT NULL",
            "volume":"FLOAT NOT NULL",
            "value":"FLOAT NOT NULL",
            "base":"TEXT NOT NULL",
            "quote":"TEXT NOT NULL",
            "ts":"INTEGER NOT NULL"
            }},
        {"table_name":"credit_table",
        "table_columns":{
            "id":"INTEGER PRIMARY KEY AUTOINCREMENT",
            "user_id":"INTEGER NOT NULL",
            "bot_id":"INTEGER",
            "deposit_nr":"TEXT NOT NULL",
            "value":"FLOAT NOT NULL",
            "volume":"FLOAT NOT NULL",
            "description":"TEXT NOT NULL",
            "ts":"INTEGER NOT NULL"
            }},
        {"table_name":"message_table",
        "table_columns":{
            "id":"INTEGER PRIMARY KEY AUTOINCREMENT",
            "user_id":"INTEGER NOT NULL",
            "message_type":"TEXT NOT NULL",
            "message":"TEXT NOT NULL"
            }},
        {"table_name":"token_table",
        "table_columns":{
            "id":"INTEGER PRIMARY KEY AUTOINCREMENT",
            "token":"TEXT NOT NULL DEFAULT 'blob'",
            "ts":"INTEGER NOT NULL DEFAULT 0",
            "user_id":"INTEGER NOT NULL DEFAULT 0",
            "type":"TEXT NOT NULL DEFAULT 'VERIFY'",
            "period":"INTEGER NOT NULL DEFAULT 24"
        }}
            ]
    
    
    db_dir = os.path.dirname(db_path)
    if db_dir and not os.path.exists(db_dir):
        os.makedirs(db_dir)
        
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    for table in schema:
        table_name = table["table_name"]
        query_content = None
        query_content = []
        for key,value in table["table_columns"].items():
            query_content.append(f"{key} {value}")
        cursor.execute(f"CREATE TABLE IF NOT EXISTS {table["table_name"]}(id INTEGER PRIMARY KEY AUTOINCREMENT)")
        cursor.execute(f"PRAGMA table_info({table["table_name"]})")
        table_columns = [col[1] for col in cursor.fetchall()]
        for name,definitions in table["table_columns"].items():
            found = False
            for column in table_columns:
                if column == name:
                    found = True
                    break
            if not found:
                currentTS = int(time.time())
                date = datetime.datetime.fromtimestamp(currentTS)
                dateFormat = "%d%b %Y %H:%M:%S"
                printDate = date.strftime(dateFormat)
                print(" "*100, end="\r", flush=True)
                print(f"{printDate} ~ Adding {name} - {definitions} to {table["table_name"]}", flush=True)
                cursor.execute(f"ALTER TABLE {table["table_name"]} ADD COLUMN {name} {definitions}")
        
        for column in table_columns:
            found = False
            for name,definitions in table["table_columns"].items():
                if column == name:
                    found = True
                    break
            if not found:
                currentTS = int(time.time())
                date = datetime.datetime.fromtimestamp(currentTS)
                dateFormat = "%d%b %Y %H:%M:%S"
                printDate = date.strftime(dateFormat)
                print(" "*100, end="\r", flush=True)
                print(f"{printDate} ~ Dropping {column} from {table["table_name"]}", flush=True)
                cursor.execute(f"ALTER TABLE {table["table_name"]} DROP COLUMN {column}")
        
                    
    conn.commit()
    conn.close()

"""
Classes for DB tables
"""

class User():
    """
    New user data list=[id:0, name:{name}, email:{email}, password:{password}, verified:{ts}, reminder:[]]
    Credit Reminder array:
    [{
        'code':1,
        'ts':int(time.time()),
        'description':'1 week credit reminder
    }]
    Reminder Codes:
        1 - 0.25 Credits Remaining
        2 - 0.1 Credits Remaining
        3 - Credits has run out
        4 - Out of credits 1 week
        5 - 2 weeks inactivity
        6 - 5 days not verified
    """
    def __init__(self,data):
        self.id = int(data[0])
        self.name = data[1]
        self.email = data[2]
        self.password = data[3]
        self.verified = bool(data[4])
        self.reminder = json.loads(data[5])

    def post(self):
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            query = "INSERT INTO user_table(name, email, password, verified, reminder) VALUES(?, ?, ?, ?, ?)"
            cursor.execute(query,[self.name, self.email, self.password, self.verified, json.dumps(self.reminder)])
            conn.commit()
            
    def update(self):
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            query = "UPDATE user_table SET name=?, email=?, password=?, verified=?, reminder=? WHERE id=?"
            cursor.execute(query,[self.name, self.email, self.password, self.verified, json.dumps(self.reminder), self.id])
            conn.commit()

    def delete(self):
        with sqlite3.connect(db_path) as connection:
            query = "DELETE FROM user_table WHERE id=?"
            data = [self.id]
            cursor = connection.cursor()
            cursor.execute(query,data)
            connection.commit()

def getUsers(id=None, email=None):
    """
    Return the list of users,
    Or a user matching the params
    """
    if id:
        user=None
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            data = cursor.execute("SELECT * FROM user_table WHERE id = ?", [id]).fetchone()
            if data:
                user = User(list(data))
        return user
    elif email:
        user=None
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            data = cursor.execute("SELECT * FROM user_table WHERE email = ?", [email]).fetchone()
            if data:
                user = User(list(data))
        return user
    else:
        users=[]
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            data = cursor.execute("SELECT * FROM user_table").fetchall()
            if data:
                for entry in data:
                    users.append(User(entry))
        return users


class Bot:
    """
    New bot data list=[id:0, user_id:{user_id}, name:{name}, key:{key}, secret:{secret}, currency:{""}, active:{False}, equity:{0}, balance_nr:{0}, balance_value:{0}, margin:{0}, dynamic_margin:{False}, refined_weight:{False}]
    """
    def __init__(self, data):
        self.id = int(data[0])
        self.user_id = int(data[1])
        self.name = data[2]
        self.key = data[3]
        self.secret = data[4]
        self.currency = data[5]
        self.active = bool(data[6])
        self.equity = float(data[7])
        self.balance_nr = int(data[8])
        self.balance_value = float(data[9])
        self.margin = float(data[10])
        self.dynamic_margin = bool(data[11])
        self.refined_weight = bool(data[12])
        self.quote_balance = float(data[13])
        
    def __repr__(self):
        repr = f"""
        {self.id}
        {self.user_id}
        {self.name}
        {self.key}
        {self.secret}
        {self.currency}
        {self.active}
        {self.equity} 
        {self.quote_balance} 
        {self.balance_nr}
        {self.balance_value}
        {self.margin}
        {self.dynamic_margin}
        {self.refined_weight}
        """
        return repr

    def post(self):
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            query = "INSERT INTO bot_table(user_id, name, key, secret, currency, active, equity, quote_balance balance_nr, balance_value, margin, dynamic_margin, refined_weight) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            cursor.execute(query,[self.user_id, self.name, self.key, self.secret, self.currency, self.active, self.equity, self.quote_balance, self.balance_nr, self.balance_value, self.margin, self.dynamic_margin, self.refined_weight])
            conn.commit()
            
    def update(self):
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            query = "UPDATE bot_table SET name=?, key=?, secret=?, currency=?, active=?, equity=?, quote_balance=?, balance_nr=?, balance_value=?, margin=?, dynamic_margin=?, refined_weight=? WHERE id=?"
            cursor.execute(query,[self.name, self.key, self.secret, self.currency, self.active, self.equity, self.quote_balance, self.balance_nr, self.balance_value, self.margin, self.dynamic_margin, self.refined_weight, self.id])
            conn.commit()
            
    def delete(self):
        with sqlite3.connect(db_path) as connection:
            query = "DELETE FROM bot_table WHERE id=?"
            data = [self.id]
            cursor = connection.cursor()
            cursor.execute(query,data)
            connection.commit()

    def start(self):
        if not self.active:
            self.active = True
            self.update()
            stopEntry = Credit([0, self.user_id, self.id, "", 0, 0, "START", time.time()])
            stopEntry.post()

    def stop(self):
        if self.active:
            self.active = False
            self.update()
            stopEntry = Credit([0, self.user_id, self.id, "", 0, 0, "PAUSE", time.time()])
            stopEntry.post()

def getBots(id=None, user_id=None):
    """
    Return the list of bots,
    Or a bot matching the params
    """
    if id:
        bot=None
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            data = cursor.execute("SELECT * FROM bot_table WHERE id = ?", [id]).fetchone()
            if data:
                bot = Bot(list(data))
        return bot
    elif user_id:
        bot=[]
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            data = cursor.execute("SELECT * FROM bot_table WHERE user_id = ?", [user_id]).fetchall()
            if data:
                for entry in data:
                    bot.append(Bot(entry))
        return bot
    else:
        bots=[]
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            data = cursor.execute("SELECT * FROM bot_table").fetchall()
            if data:
                for entry in data:
                    bots.append(Bot(entry))
        return bots


class ActiveAccount:
    """
    New Active Account data list=[id:0, bot_id:{bot_id}, base:{base} volume:{0}, swing:{0}, direction:{"}, stake:{0}]
    """
    def __init__(self,data):
        self.id = int(data[0])
        self.bot_id = int(data[1])
        self.base = data[2]
        self.volume = float(data[3])
        self.swing = float(data[4])
        self.direction = data[5]
        self.stake = float(data[6])
        
    def post(self):
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            query = "INSERT INTO active_account_table(bot_id, base, volume, stake, swing, direction) VALUES(?, ?, ?, ?, ?, ?)"
            cursor.execute(query,[self.bot_id, self.base, self.volume, self.stake, self.swing, self.direction])
            conn.commit()
    
    def update(self):
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            query = "UPDATE active_account_table SET volume=?, stake=?, swing=?, direction=? WHERE id=?"
            cursor.execute(query,[self.volume, self.stake, self.swing, self.direction, self.id])
            conn.commit()
            
    def delete(self):
        with sqlite3.connect(db_path) as connection:
            query = "DELETE FROM active_account_table WHERE id=?"
            data = [self.id]
            cursor = connection.cursor()
            cursor.execute(query,data)
            connection.commit()
    
    def price(self, config):
        price=0
        bot = getBots(id=self.bot_id)
        if bot.currency == "ZAR":
            for entry in config.ZAR:
                if entry["base"] == self.base:
                    return float(entry["price"])
        elif bot.currency == "USDC":
            for entry in config.USDC:
                if entry["base"] == self.base:
                    return float(entry["price"])
        elif bot.currency == "USDT":
            for entry in config.USDT:
                if entry["base"] == self.base:
                    return float(entry["price"])
        else:
            return None

def getActiveAccounts(id=None, bot_id=None):
    """
    Return the list of Active Accounts,
    Or a bot matching the params
    """
    if id:
        aa=None
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            data = cursor.execute("SELECT * FROM active_account_table WHERE id = ?", [id]).fetchone()
            if data:
                aa = ActiveAccount(list(data))
        return aa
    elif bot_id:
        aa=[]
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            data = cursor.execute("SELECT * FROM active_account_table WHERE bot_id = ?", [bot_id]).fetchall()
            if data:
                for entry in data:
                    aa.append(ActiveAccount(entry))
        return aa
    else:
        aa=[]
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            data = cursor.execute("SELECT * FROM active_account_table").fetchall()
            if data:
                for entry in data:
                    aa.append(ActiveAccount(entry))
        return aa


class Transaction:
    """
    New entry data = [id:0, bot_id:{bot_id}, transaction_type:{type}, volume:{volume}, value:{value}, base:{base}, quote:{quote}, ts:{int(time.time())}]
    Types: "INVEST", "WITHDRAW", "BUY", "SELL"
    """
    def __init__(self,data):
        self.id = int(data[0])
        self.bot_id = int(data[1])
        self.type = data[2]
        self.volume = float(data[3])
        self.value = float(data[4])
        self.base = data[5]
        self.quote = data[6]
        self.ts = int(data[7])

    def post(self):
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            query = "INSERT INTO transaction_table(bot_id, type, volume, value, base, quote, ts) VALUES(?, ?, ?, ?, ?, ?, ?)"
            cursor.execute(query,[self.bot_id, self.type, self.volume, self.value, self.base, self.quote, self.ts])
            conn.commit()
            
    def delete(self):
        with sqlite3.connect(db_path) as connection:
            query = "DELETE FROM transaction_table WHERE id=?"
            data = [self.id]
            cursor = connection.cursor()
            cursor.execute(query,data)
            connection.commit()

def getTransactions(bot_id):
    """
    Return the list of transactions per bot id
    """
    transactions=[]
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        data = cursor.execute("SELECT * FROM transaction_table WHERE bot_id = ?", [bot_id]).fetchall()
        if data:
            for entry in data:
                transactions.append(Transaction(entry))
    return transactions


class Credit:
    """
    New entry data list = [id:{0}, user_id:{user_id}, bot_id:{bot_id}, deposit_nr:{''}, value:{value}, volume:{volume}, description:{description}, ts:{int(time.time())}]
    id = 0
    Bot_id = 0 if not specific
    description = [CREDIT, BONUS, START, PAUSE]
    """
    def __init__(self,data):
        self.id = int(data[0])
        self.user_id = int(data[1])
        self.bot_id = int(data[2])
        self.deposit_nr = data[3]
        self.value = float(data[4])
        self.volume = float(data[5])
        self.description = data[6]
        self.ts = int(data[7])
        
    def post(self):
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            query = "INSERT INTO credit_table(user_id, bot_id, deposit_nr, value, volume, description, ts) VALUES(?, ?, ?, ?, ?, ?, ?)"
            cursor.execute(query,[self.user_id, self.bot_id, self.deposit_nr, self.value, self.volume, self.description, self.ts])
            conn.commit()
            
    def delete(self):
        with sqlite3.connect(db_path) as connection:
            query = "DELETE FROM user_model WHERE id=?"
            data = [self.id]
            cursor = connection.cursor()
            cursor.execute(query,data)
            connection.commit()

def getCredits(user_id=None, bot_id=None):
    """
    Returns the remaining credit for user,
    Requires user id or bot id
    """
    credits = []
    if bot_id and not user_id:
        bot = getBots(id=bot_id)
        user_id = bot.user_id
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        data = cursor.execute("SELECT * FROM credit_table WHERE user_id = ?", [user_id]).fetchall()
        if data:
            for entry in data:
                credits.append(Credit(entry))

    ts = int(time.time())
    creditPeriod = 4*7*24*60*60  #Secs in 4 weeks (4*7*24*60*60)
    cred_in = 0
    run = 0
    pause = 0
    start_nr = 0
    stop_nr = 0

    if not bot_id:
        for entry in credits:
            if entry.description == "CREDIT":
                cred_in += entry.volume
            elif entry.description == "BONUS":
                cred_in += entry.volume
            elif entry.description == "START":
                start_nr += 1
                run += ts-entry.ts
            elif entry.description == "PAUSE":
                stop_nr += 1
                pause += ts-entry.ts
            else:
                print("Unknown Credit entry:")
                print(entry)


        run_time = run-pause
        cred_time = cred_in * creditPeriod
        active_timer = start_nr - stop_nr
        remaining_time = 0
        if active_timer != 0:
            remaining_time = (cred_time - run_time)/active_timer
        remaining_credit = (cred_time - run_time)/creditPeriod
        details={
            "credit":remaining_credit,
            "time":remaining_time
        }
        return details
    else:
        for entry in credits:
            if entry.description == "START" and int(entry.bot_id) == int(bot_id):
                start_nr += 1
                run += ts-entry.ts
            elif entry.description == "PAUSE" and int(entry.bot_id) == int(bot_id):
                stop_nr += 1
                pause += ts-entry.ts

        run_time = run-pause

        details={
            "credit":run_time/creditPeriod,
            "time":run_time
        }
        return details


class Message:
    """
    New message data list=[id:0, user_id:{user_id}, message_type:{message_type}, message:{message}]
    Message types ["INFO","WARNING","ERROR"]
    """
    def __init__(self,data):
        self.id = int(data[0])
        self.user_id = int(data[1])
        self.message_type = data[2]
        self.message = data[3]
    
    def post(self):
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            query = "INSERT INTO message_table(user_id, message_type, message) VALUES(?, ?, ?)"
            cursor.execute(query,[self.user_id, self.message_type, self.message])
            conn.commit()
            
    def delete(self):
        with sqlite3.connect(db_path) as connection:
            query = "DELETE FROM message_table WHERE id=?"
            data = [self.id]
            cursor = connection.cursor()
            cursor.execute(query,data)
            connection.commit()

def getMessages(user_id):
    """
    Return the list of transactions per user id
    """
    messages=[]
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        data = cursor.execute("SELECT * FROM message_table WHERE user_id = ?", [user_id]).fetchall()
        if data:
            for entry in data:
                messages.append(Message(entry))
    return messages


class Token():
    """
    New message data list=[id:0, token:{token}, ts:int(time.time()), user_id:{user_id}, type:{type}, period:{period}]
    Token - Encoded token
    Types ["VERIFY","RESET"]
    Period - Lifetime of token in hrs
    """
    def __init__(self,data):
        self.id = data[0]
        self.token = data[1]
        self.ts = int(data[2])
        self.user_id = int(data[3])
        self.type = data[4]
        self.period = int(data[5])

    def post(self):
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            query = "INSERT INTO token_table(token, ts, user_id, type, period) VALUES(?, ?, ?, ?, ?)"
            cursor.execute(query,[self.token, self.ts, self.user_id, self.type, self.period])
            conn.commit()
            
    def delete(self):
        with sqlite3.connect(db_path) as connection:
            query = "DELETE FROM token_table WHERE id=?"
            data = [self.id]
            cursor = connection.cursor()
            cursor.execute(query,data)
            connection.commit()

def getToken(token):
    dbToken = None
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        data = cursor.execute("SELECT * FROM token_table WHERE token = ?", [token]).fetchone()
        if data:
            dbToken = Token(data)
    return dbToken
