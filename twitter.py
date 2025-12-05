import requests, json
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

def sendTweet(quote_data):
    attempts = 0
    max_attempts = 5
    while attempts < max_attempts:
        try:
            post = generate_post(quote_data)
        except Exception as e:
            print(f"Error during post generation: {e}")
            attempts += 1
            continue
        post = post.strip('"')
        post = post.strip("'")
        print(f"Generated post: {post} (Length: {len(post)})")
        attempts += 1
        if len(post) <= 280:
            break

    if len(post) > 280:
        print("Failed to generate under 280 chars after max attempts.")
        return

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

def generate_post(quote_data):
    groq_client = Groq(api_key=groqKey)
    prompt = f"Generate a short crypto market update X post (under 280 chars, aim for 200-250) incorporating this data: '{quote_data}'. Make the post in a neutral or lightly engaging, professional and non-cringe. Use hashtags #VALR #CryptoTrading #BitcoinAfrica at the end. Avoid any dates, times, specifics or predictions you might get wrong, or hints you're an AI—sound like a human crypto trader."
    response = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",  # Or another Groq model
        messages=[{"role": "user", "content": prompt}],
        max_tokens=100
    )
    return response.choices[0].message.content.strip()
