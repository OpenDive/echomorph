import os
import asyncio
from authenticator import get_periscope_cookie
from tweeterpy import TweeterPy

from dotenv import load_dotenv
load_dotenv()

async def main():
    email, username, password = os.getenv("TWITTER_EMAIL"), os.getenv("TWITTER_USERNAME"), os.getenv("TWITTER_PASSWORD")
    twitter = TweeterPy()
    twitter.login(username=username, password=password, email=email)

    ps_cookie = await get_periscope_cookie(twitter)
    print(f"Got Periscope Cookie - {ps_cookie}")
    

asyncio.run(main())
