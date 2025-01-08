# ----- Standard Library Imports -----
import os
import uuid
import json
import time
from typing import Any, Dict

# ----- Third-Party Imports -----
import httpx
from fake_useragent import FakeUserAgent

# ----- Local Imports -----
from tweeterpy import TweeterPy


def generate_random_id() -> str:
    """
    Generate a UUID v4-like string.

    :return: A randomly generated UUID4 string.
    """
    return str(uuid.uuid4())


async def fetch_authenticate_periscope(scraper: TweeterPy) -> str:
    """
    Retrieve a Periscope JWT via Twitter's 'AuthenticatePeriscope' endpoint.
    This is the Python equivalent of TypeScript's 'fetchAuthenticatePeriscope(auth)'.

    :param scraper: A TweeterPy object used to manage session cookies / requests.
    :return:        A string representing the Periscope JWT.
    :raises httpx.HTTPStatusError: If the HTTP request fails with a non-2xx status.
    """
    client = httpx.AsyncClient(proxies=None, timeout=httpx.Timeout(10, read=30))
    auth_periscope_url = (
        "https://x.com/i/api/graphql/r7VUmxbfqNkx7uwjgONSNw/AuthenticatePeriscope"
    )

    client_transaction_id = generate_random_id()
    gt = scraper.request_client.session.cookies.get("gt")
    csrf = scraper.request_client.session.cookies.get("ct0")
    cookie = format_cookie(scraper.request_client.session.cookies.items())

    headers = {
        "Accept": "*/*",
        "Authorization": (
            "Bearer AAAAAAAAAAAAAAAAAAAAAFQODgEAAAAAVHTp76lzh3rFzcHbmHVvQxYYpTw%3DckAlMINMjmCwx"
            "UcaXbAN4XqJVdgMJaHqNOFgPMK0zN1qLqLQCF"
        ),
        "Content-Type": "application/json",
        "Cookie": cookie,
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "X-Guest-Token": gt,
        "X-Twitter-Auth-Type": "OAuth2Session",
        "X-Twitter-Active-User": "yes",
        "X-Csrf-Token": csrf,
        "X-Client-Transaction-ID": client_transaction_id,
        "sec-ch-ua-platform": "\"Windows\"",
        "sec-ch-ua": "\"Google Chrome\";v=\"131\", \"Chromium\";v=\"131\", \"Not_A Brand\";v=\"24\"",
        "X-Twitter-Client-Language": "en",
        "sec-ch-ua-mobile": "?0",
        "Referer": "https://x.com/i/spaces/start",
    }

    response = await client.get(auth_periscope_url, headers=headers)
    response.raise_for_status()

    data = response.json()
    return data["data"]["authenticate_periscope"]


async def fetch_login_twitter_token(jwt: str, scraper: TweeterPy) -> Dict[str, Any]:
    """
    Login to Twitter via Proxsee using a Periscope JWT.  
    Returns a dictionary typically containing {"cookie": ..., "user": ...}.

    :param jwt:     The Periscope JWT obtained from fetch_authenticate_periscope().
    :param scraper: A TweeterPy object (unused here, but kept for consistency).
    :return:        A dictionary with "cookie" and "user" keys.
    :raises httpx.HTTPStatusError: If the HTTP request fails with a non-2xx status.
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
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": "https://x.com",
        "sec-ch-ua": "\"Google Chrome\";v=\"131\", \"Chromium\";v=\"131\", \"Not_A Brand\";v=\"24\"",
        "sec-ch-ua-platform": "\"Windows\"",
        "sec-ch-ua-mobile": "?0",
        "X-Periscope-User-Agent": "Twitter/m5",
        "X-Idempotence": idempotence_key,
        "X-Attempt": "1",
    }

    response = await client.post(url, headers=headers, json=payload)
    response.raise_for_status()

    return response.json()


async def get_periscope_cookie(scraper: TweeterPy) -> str:
    """
    End-to-end function that:
      1) Authenticates with Periscope to get a JWT
      2) Logs in to Twitter via Proxsee with that JWT
      3) Returns the cookie field from the response

    :param scraper: A TweeterPy object used for session/cookie management.
    :return:        The string value of the cookie.
    :raises httpx.HTTPStatusError: If either request fails.
    """
    jwt_token = await fetch_authenticate_periscope(scraper)
    login_response = await fetch_login_twitter_token(jwt_token, scraper)
    return login_response["cookie"]


def format_cookie(cookies: Dict[str, str]) -> str:
    """
    Format cookies into a string suitable for the HTTP 'Cookie' header, 
    filtering out and rewriting certain keys as needed.

    :param cookies: A dictionary of cookie name-value pairs.
    :return:        A formatted cookie string.
    """
    allowed_headers = [
        "guest_id_marketing",
        "guest_id_ads",
        "personalization_id",
        "guest_id",
        "kdt",
        "twid",
        "ct0",
        "auth_token",
        "att",
    ]
    percentage_filter = ["guest_id_marketing", "guest_id_ads", "guest_id"]
    p_id = "personalization_id"
    twid = "twid"
    second_p_id = False

    return_value = ""
    for key, value in cookies:
        # Handle 'twid' cookie specially by decoding '%3D' to '='
        if key in twid:
            fmt_twid = value.replace("%3D", "=")
            return_value += f"{key}=\"{fmt_twid}\"; "
        elif key in allowed_headers:
            # Filter out entries that contain '%' if key is in percentage_filter
            if key in percentage_filter:
                if "%" in value:
                    return_value += f"{key}={value}; "
            # For personalization_id, skip the first occurrence if second_p_id is False
            elif key in p_id:
                if second_p_id:
                    return_value += f"{key}={value}; "
                else:
                    second_p_id = True
            else:
                return_value += f"{key}={value}; "

    # Remove the trailing semicolon and space
    return return_value[:-2]
