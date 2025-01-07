import os
import uuid
import json
import time
import httpx
from typing import Any, Dict
from fake_useragent import FakeUserAgent

from tweeterpy import TweeterPy

def generate_random_id() -> str:
    """
    Equivalent to the TS generateRandomId() that returns a UUID v4-like string.
    """
    return str(uuid.uuid4())


async def fetch_authenticate_periscope(scraper: TweeterPy) -> str:
    """
    Equivalent to TypeScript's 'fetchAuthenticatePeriscope(auth)'.
    Returns a Periscope JWT on success.
    """
    client = httpx.AsyncClient(proxies=None, timeout=httpx.Timeout(10, read=30))
    auth_periscope_url = "https://x.com/i/api/graphql/r7VUmxbfqNkx7uwjgONSNw/AuthenticatePeriscope"
    client_transaction_id = generate_random_id()
    gt = scraper.request_client.session.cookies.get("gt")
    csrf = scraper.request_client.session.cookies.get("ct0")
    cookie = format_cookie(scraper.request_client.session.cookies.items())

    headers = {
        "Accept": "*/*",
        "Authorization": f"Bearer AAAAAAAAAAAAAAAAAAAAAFQODgEAAAAAVHTp76lzh3rFzcHbmHVvQxYYpTw%3DckAlMINMjmCwxUcaXbAN4XqJVdgMJaHqNOFgPMK0zN1qLqLQCF",
        "Content-Type": "application/json",
        "Cookie": cookie,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "X-Guest-Token": gt,
        "X-Twitter-Auth-Type": "OAuth2Session",
        "X-Twitter-Active-User": "yes",
        "X-Csrf-Token": csrf,
        "X-Client-Transaction-ID": client_transaction_id,
        "sec-ch-ua-platform": "\"Windows\"",
        "sec-ch-ua": "\"Google Chrome\";v=\"131\", \"Chromium\";v=\"131\", \"Not_A Brand\";v=\"24\"",
        "X-Twitter-Client-Language": "en",
        "sec-ch-ua-mobile": "?0",
        "Referer": "https://x.com/i/spaces/start"
    }

    response = await client.get(auth_periscope_url, headers=headers)
    response.raise_for_status()

    data = response.json()
    return data["data"]["authenticate_periscope"]


async def fetch_login_twitter_token(jwt: str, scraper: TweeterPy) -> Dict[str, Any]:
    """
    Logs in to Twitter via Proxsee using the Periscope JWT, returns { cookie, user }.
    """
    client = httpx.AsyncClient(proxies=None, timeout=httpx.Timeout(10, read=30))
    url = "https://proxsee.pscp.tv/api/v2/loginTwitterToken"
    idempotence_key = generate_random_id()

    payload = {
        "jwt": jwt,
        "vendor_id": "m5-proxsee-login-a2011357b73e",
        "create_user": True,
    }

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Referer": "https://x.com",
        "sec-ch-ua": "\"Google Chrome\";v=\"131\", \"Chromium\";v=\"131\", \"Not_A Brand\";v=\"24\"",
        "sec-ch-ua-platform": "\"Windows\"",
        "sec-ch-ua-mobile": "?0",
        "X-Periscope-User-Agent": "Twitter/m5",
        "X-Idempotence": idempotence_key,
        "X-Attempt": "1"
    }

    response = await client.post(url, headers=headers, json=payload)
    response.raise_for_status()

    return response.json()


async def get_periscope_cookie(scraper: TweeterPy) -> str:
    """
    1) authenticatePeriscope -> get JWT
    2) loginTwitterToken -> get { cookie, user }
    3) return the cookie
    """
    jwt_token = await fetch_authenticate_periscope(scraper)
    login_response = await fetch_login_twitter_token(jwt_token, scraper)
    return login_response["cookie"]

def format_cookie(cookies: Dict[str, str]) -> str:
    allowed_headers = ["guest_id_marketing", "guest_id_ads", "personalization_id", "guest_id", "kdt", "twid", "ct0", "auth_token", "att"]
    percentage_filter = ["guest_id_marketing", "guest_id_ads", "guest_id"]
    p_id = "personalization_id"
    twid = "twid"
    second_p_id = False
    
    return_value = ""
    for key, value in cookies:
        if key in twid:
            fmt_twid = f"{value}".replace("%3D", "=")
            return_value += f"{key}=\"{fmt_twid}\"; "
        elif key in allowed_headers:
            if key in percentage_filter:
                if "%" in value:
                    return_value += f"{key}={value}; "
            elif key in p_id:
                if second_p_id:
                    return_value += f"{key}={value}; "
                else:
                    second_p_id = True
            else:
                return_value += f"{key}={value}; "

    return return_value[:-2]
