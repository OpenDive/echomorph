import asyncio
import uuid
import json
import time
from typing import Any, Dict

import aiohttp
from aiohttp import ClientResponse, CookieJar


class TwitterAuth:
    """
    Stores authentication info needed for requests:
      - bearer_token
      - guest_token
      - cookie_jar (for storing or updating cookies)
    """
    def __init__(self, bearer_token: str, guest_token: str):
        self.bearer_token = bearer_token
        self.guest_token = guest_token
        # We'll keep a cookie jar for all requests:
        self.cookie_jar = aiohttp.CookieJar()

    async def fetch(self, url: str, **kwargs) -> aiohttp.ClientResponse:
        """
        In TypeScript, this was auth.fetch(...).
        We'll create an aiohttp session on the fly here.
        For more complex scenarios, you might keep a persistent session object.
        """
        async with aiohttp.ClientSession(cookie_jar=self.cookie_jar) as session:
            return await session.request(url=url, **kwargs)

    def get_cookie_string(self, domain_url: str) -> str:
        """
        Returns a 'Cookie' header string from the cookie jar for a given domain (like onboardingTaskUrl).
        In TypeScript: `auth.cookieJar().getCookieString(onboardingTaskUrl)`
        """
        cookies = self.cookie_jar.filter_cookies(domain_url)
        cookie_list = [f"{k}={v.value}" for k, v in cookies.items()]
        return "; ".join(cookie_list)


def generate_random_id() -> str:
    """
    Equivalent to the TS generateRandomId() that returns a UUID v4-like string.
    """
    return str(uuid.uuid4())


async def update_cookie_jar(cookie_jar: aiohttp.CookieJar, response: aiohttp.ClientResponse):
    """
    If needed, manually update cookies from response. 
    By default, aiohttp updates the jar automatically if using the same session.
    """
    pass
    # If you want to manually parse 'Set-Cookie', do so here.


async def fetch_authenticate_periscope(auth: TwitterAuth) -> str:
    """
    Equivalent to TypeScript's 'fetchAuthenticatePeriscope(auth)'.
    Returns a Periscope JWT on success.
    """
    query_id = "r7VUmxbfqNkx7uwjgONSNw"
    operation_name = "AuthenticatePeriscope"
    variables = {}
    features = {}

    # Encode for the URL
    from aiohttp.helpers import quote
    variables_encoded = quote(json.dumps(variables), safe='')
    features_encoded = quote(json.dumps(features), safe='')

    url = (
        f"https://x.com/i/api/graphql/{query_id}/{operation_name}"
        f"?variables={variables_encoded}&features={features_encoded}"
    )

    onboarding_task_url = "https://api.twitter.com/1.1/onboarding/task.json"

    # Need 'ct0' cookie for X-CSRF
    cookies = auth.cookie_jar.filter_cookies(onboarding_task_url)
    x_csrf_token = cookies.get("ct0")
    if x_csrf_token is None:
        raise RuntimeError("CSRF Token (ct0) not found in cookies.")

    client_transaction_id = generate_random_id()

    headers = {
        "Accept": "*/*",
        "Authorization": f"Bearer {auth.bearer_token}",
        "Content-Type": "application/json",
        "Cookie": auth.get_cookie_string(onboarding_task_url),
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "x-guest-token": auth.guest_token,
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-active-user": "yes",
        "x-csrf-token": x_csrf_token.value,
        "x-client-transaction-id": client_transaction_id,
        "sec-ch-ua-platform": "\"Windows\"",
        "sec-ch-ua": "\"Google Chrome\";v=\"131\", \"Chromium\";v=\"131\", \"Not_A Brand\";v=\"24\"",
        "x-twitter-client-language": "en",
        "sec-ch-ua-mobile": "?0",
        "Referer": "https://x.com/i/spaces/start",
    }

    response = await auth.fetch(url, method="GET", headers=headers)
    await update_cookie_jar(auth.cookie_jar, response)

    if response.status != 200:
        error_text = await response.text()
        raise RuntimeError(f"Error {response.status}: {error_text}")

    data = await response.json()
    if "errors" in data and len(data["errors"]) > 0:
        raise RuntimeError(f"API Errors: {json.dumps(data['errors'])}")
    if not data.get("data") or not data["data"].get("authenticate_periscope"):
        raise RuntimeError("Periscope authentication failed, no data returned.")

    return data["data"]["authenticate_periscope"]


async def fetch_login_twitter_token(jwt: str, auth: TwitterAuth) -> Dict[str, Any]:
    """
    Logs in to Twitter via Proxsee using the Periscope JWT, returns { cookie, user }.
    """
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
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Referer": "https://x.com/",
        "sec-ch-ua": "\"Google Chrome\";v=\"131\", \"Chromium\";v=\"131\", \"Not_A Brand\";v=\"24\"",
        "sec-ch-ua-platform": "\"Windows\"",
        "sec-ch-ua-mobile": "?0",
        "X-Periscope-User-Agent": "Twitter/m5",
        "X-Idempotence": idempotence_key,
        "X-Attempt": "1",
    }

    response = await auth.fetch(url, method="POST", headers=headers, data=json.dumps(payload))
    await update_cookie_jar(auth.cookie_jar, response)

    if response.status != 200:
        error_text = await response.text()
        raise RuntimeError(f"Error {response.status}: {error_text}")

    data = await response.json()
    if "cookie" not in data or "user" not in data:
        raise RuntimeError("Twitter authentication failed, missing data.")

    return data


async def get_periscope_cookie(auth: TwitterAuth) -> str:
    """
    1) authenticatePeriscope -> get JWT
    2) loginTwitterToken -> get { cookie, user }
    3) return the cookie
    """
    jwt_token = await fetch_authenticate_periscope(auth)
    login_response = await fetch_login_twitter_token(jwt_token, auth)
    return login_response["cookie"]


async def update_guest_token(
    bearer_token: str,
    cookie_jar: CookieJar,
) -> str:
    """
    Calls Twitter's guest activation endpoint to acquire a new guest token.
    - `bearer_token`: The "App" Bearer token used for authentication (from dev portal).
    - `cookie_jar`: A CookieJar holding any necessary cookies.

    Returns:
        The new guest_token as a string.

    Raises:
        RuntimeError if the request fails or no guest_token is found.
    """

    guest_activate_url = "https://api.twitter.com/1.1/guest/activate.json"

    # Build the 'Cookie' header from the jar, if needed.
    # This is similar to 'await this.getCookieString()' in TypeScript.
    # We'll assume you have some helper or do it inline:
    cookie_str = _get_cookie_string(cookie_jar, "https://api.twitter.com")

    headers = {
        "Authorization": f"Bearer {bearer_token}",
        "Cookie": cookie_str,
    }

    # Create a session to POST
    async with aiohttp.ClientSession(cookie_jar=cookie_jar) as session:
        async with session.post(guest_activate_url, headers=headers) as res:
            # In TypeScript you had `res.ok` and `res.text()`.
            # In aiohttp, we check status != 200:
            if res.status != 200:
                err_text = await res.text()
                raise RuntimeError(f"Failed to activate guest => {res.status}: {err_text}")

            # The body should have JSON with 'guest_token'.
            data = await res.json()
            if not data or "guest_token" not in data:
                raise RuntimeError("guest_token not found in response.")

            guest_token = data["guest_token"]
            if not isinstance(guest_token, str):
                raise RuntimeError("guest_token was not a string.")

            # If desired, we can manually parse 'Set-Cookie' from res.headers,
            # but since we pass the same session + jar, aiohttp handles it automatically.

            return guest_token


def _get_cookie_string(cookie_jar: CookieJar, domain_url: str) -> str:
    """
    Reconstruct the Cookie header string from the cookie jar for a given domain.
    This is an internal helper method, analogous to the TypeScript getCookieString().
    """
    cookies = cookie_jar.filter_cookies(domain_url)
    return "; ".join(f"{k}={v.value}" for k, v in cookies.items())
