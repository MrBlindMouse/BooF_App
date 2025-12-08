import requests, json, time
from pathlib import Path

import tweepy
from dotenv import dotenv_values
import valr
from groq import Groq

envConfig = dotenv_values(".env")
groqKey = envConfig["GROQ_API_KEY"]
clientID = envConfig["X_KEY"]
clientSecret = envConfig["X_SECRET"]
accessToken = envConfig["X_TOKEN"]
accessSecret = envConfig["X_TOKEN_SECRET"]
bearerToken = envConfig["X_BEARER_TOKEN"]

def fetchPrevious(filename):
    MEMORY_FILE = Path(__file__).with_name(f'{filename}_posts.json')
    try:
        if MEMORY_FILE.exists():
            previous_posts = json.loads(MEMORY_FILE.read_text(encoding='utf-8'))
            return previous_posts
        else:
            return []
    except Exception:
        return []

def savePost(filename, post_list):
    post_list["data"] = post_list["data"][-2:]
    MEMORY_FILE = Path(__file__).with_name(f'{filename}_posts.json')
    try:
        MEMORY_FILE.write_text(json.dumps(post_list, ensure_ascii=False, indent=4), encoding="utf-8")
    except:
        pass



def sendTweet(quote_data, post_type):
    print("")
    print(quote_data)
    attempts = 0
    max_attempts = 5
    post = ''
    previous_posts = fetchPrevious(post_type)
    while attempts < max_attempts:
        post = None
        try:
            post = generate_post(quote_data, previous_posts)
        except Exception as e:
            print(f"Error during post generation: {e}")
            attempts += 1
            continue
        if post is None:
            continue
        post = post.strip('"')
        post = post.strip("'")
        print(f"Generated post: {post} (Length: {len(post)})")
        attempts += 1
        if len(post) <= 280 and len(post) > 70:
            break

    if len(post) > 280 and len(post) <= 70:
        print("Failed to generate valid post after max attempts.")
        return
    previous_posts["post"] = post
    previous_posts["data"].append(quote_data)
    savePost(post_type, previous_posts)
    client = tweepy.Client(
        bearer_token = bearerToken,
        consumer_key=clientID,
        consumer_secret=clientSecret,
        access_token=accessToken,
        access_token_secret=accessSecret
    )
    follow_up = "If you want to grow your crypto, check out this #VALR trading bot. Designed for long term consistent growth! https://boof-bots.com"
    try:
        result = client.create_tweet(text=post)
        print(json.dumps(result.data, indent=4))
        tweet_id = int(result.data['id'])
        print(f"Tweet posted: https://x.com/i/status/{tweet_id}")
        time.sleep(5)
        result = client.create_tweet(
            text = follow_up,
            in_reply_to_tweet_id = tweet_id,
        )
        tweet_id = int(result.data["id"])
        print(f"Reply posted: https://x.com/i/status/{tweet_id}")
    except tweepy.TweepyException as e:
        print(f"Failed to post tweet: {e}")
        if hasattr(e, "response") and e.response is not None:
            print(f"Twitter error details: {e.response.data}")

def generate_post(quote_data, previous_posts):
    try:
        groq_client = Groq(api_key=groqKey)
        historical_block = ''
        if previous_posts["post"]:
            historical_block = f"""
            Here is the previous post, use it to match the tone and avoid repeating phrases:\n
            {previous_posts["post"]}\n
            """
        context_block = ''
        if previous_posts["data"]:
            lines = [f"- {post}" for post in reversed(previous_posts["data"])]
            context_block = f"\nHere is the previous few days of market data, use it for continuity if applicable:\n{'\n'.join(lines)}\n\n"

        prompt = f"""
            Generate a short crypto market trend update X post (under 280 chars, aim for 200-250) incorporating this data: '{quote_data}'.
            {historical_block}
            {context_block}
            Make the post in a neutral or lightly engaging, professional and non-cringe. Use hashtags #VALR #CryptoTrading #BitcoinAfrica at the end.
            Avoid any dates, times, specifics or predictions you might get wrong, or hints you're an AI—sound like a human crypto trader.
            """
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",  # Or another Groq model
            messages=[
                {"role": "system", "content": "You are a seasoned crypto trader posting daily market updates on X."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=512,
            temperature=0.8
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        raise ValueError(e)
