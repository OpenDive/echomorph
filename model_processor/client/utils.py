import asyncio
import time
import aiohttp
from typing import Any, Dict, Optional

from spaces import BroadcastCreated, TurnServersInfo

async def authorize_token(cookie: str) -> str:
    """
    Python equivalent of authorizeToken(cookie: string): Promise<string>.
    Calls https://proxsee.pscp.tv/api/v2/authorizeToken and returns authorization_token.
    """
    headers = {
        "X-Periscope-User-Agent": "Twitter/m5",
        "Content-Type": "application/json",
        "X-Idempotence": str(int(time.time() * 1000)),  # approximate to Date.now()
        "Referer": "https://x.com/",
        "X-Attempt": "1",
    }

    url = "https://proxsee.pscp.tv/api/v2/authorizeToken"
    body = {
        "service": "guest",
        "cookie": cookie,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=body) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Failed to authorize token => {resp.status}")
            data = await resp.json()
            authorization_token = data.get("authorization_token")
            if not authorization_token:
                raise RuntimeError(
                    "authorize_token: Missing authorization_token in response"
                )
            return authorization_token


async def publish_broadcast(
    title: str,
    broadcast: Dict[str, Any],  # or BroadcastCreated
    cookie: str,
    janus_session_id: Optional[int] = None,
    janus_handle_id: Optional[int] = None,
    janus_publisher_id: Optional[int] = None,
):
    """
    Python equivalent of publishBroadcast(...).
    Calls https://proxsee.pscp.tv/api/v2/publishBroadcast to finalize the broadcast config.
    """
    headers = {
        "X-Periscope-User-Agent": "Twitter/m5",
        "Content-Type": "application/json",
        "Referer": "https://x.com/",
        "X-Idempotence": str(int(time.time() * 1000)),
        "X-Attempt": "1",
    }

    url = "https://proxsee.pscp.tv/api/v2/publishBroadcast"
    body = {
        "accept_guests": True,
        "broadcast_id": broadcast["room_id"],
        "webrtc_handle_id": janus_handle_id,
        "webrtc_session_id": janus_session_id,
        "janus_publisher_id": janus_publisher_id,
        "janus_room_id": broadcast["room_id"],
        "cookie": cookie,
        "status": title,
        "conversation_controls": 0,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=body) as resp:
            if resp.status != 200:
                raise RuntimeError(
                    f"Failed to publish broadcast => {resp.status}"
                )
            # The original code doesn't check for JSON; it just does a POST.


async def get_turn_servers(cookie: str) -> Dict[str, Any]:  # or TurnServersInfo
    """
    Python equivalent of getTurnServers(cookie: string).
    POSTs to https://proxsee.pscp.tv/api/v2/turnServers and returns the JSON response.
    """
    headers = {
        "X-Periscope-User-Agent": "Twitter/m5",
        "Content-Type": "application/json",
        "Referer": "https://x.com/",
        "X-Idempotence": str(int(time.time() * 1000)),
        "X-Attempt": "1",
    }

    url = "https://proxsee.pscp.tv/api/v2/turnServers"
    body = {"cookie": cookie}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=body) as resp:
            if resp.status != 200:
                raise RuntimeError(
                    f"Failed to get turn servers => {resp.status}"
                )
            return await resp.json()


async def get_region() -> str:
    """
    Python equivalent of getRegion().
    POSTs to https://signer.pscp.tv/region with an empty JSON body.
    Returns the 'region' field from the response.
    """
    url = "https://signer.pscp.tv/region"
    headers = {
        "Content-Type": "application/json",
        "Referer": "https://x.com",
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json={}) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Failed to get region => {resp.status}")
            data = await resp.json()
            region = data.get("region")
            if not region:
                raise RuntimeError("No 'region' in response data")
            return region


async def create_broadcast(
    description: Optional[str],
    languages: Optional[list],
    cookie: str,
    region: str
) -> Dict[str, Any]:  # or BroadcastCreated
    """
    Python equivalent of createBroadcast(...).
    Creates a new broadcast via https://proxsee.pscp.tv/api/v2/createBroadcast
    and returns the JSON response.
    """
    headers = {
        "X-Periscope-User-Agent": "Twitter/m5",
        "Content-Type": "application/json",
        "X-Idempotence": str(int(time.time() * 1000)),
        "Referer": "https://x.com/",
        "X-Attempt": "1",
    }

    url = "https://proxsee.pscp.tv/api/v2/createBroadcast"
    body = {
        "app_component": "audio-room",
        "content_type": "visual_audio",
        "cookie": cookie,
        "conversation_controls": 0,
        "description": description or "",
        "height": 1080,
        "is_360": False,
        "is_space_available_for_replay": False,
        "is_webrtc": True,
        "languages": languages if languages is not None else [],
        "region": region,
        "width": 1920,
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=body) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(
                    f"Failed to create broadcast => {resp.status} {text}"
                )
            data = await resp.json()
            return data


# ----------------------
# Example usage / test
# ----------------------
#if __name__ == "__main__":
#    async def main():
#        # Example usage (just placeholders here)
#        cookie = "some_cookie_value"
#
#        # 1) Authorize
#        token = await authorize_token(cookie)
#        print("Got authorization token:", token)
#
#        # 2) Get region
#        region = await get_region()
#        print("Region =>", region)
#
#        # 3) Create broadcast
#        bc = await create_broadcast(
#            description="Test broadcast",
#            languages=["en"],
#            cookie=cookie,
#            region=region
#        )
#        print("Broadcast created =>", bc)
#
#        # 4) Get TURN servers
#        turn = await get_turn_servers(cookie)
#        print("TURN servers =>", turn)
#
#        # 5) Publish broadcast
#        # (Here we pass the bc dict as broadcast)
#        await publish_broadcast(
#            title="Hello World",
#            broadcast=bc,
#            cookie=cookie,
#            janus_session_id=12345,
#            janus_handle_id=67890,
#            janus_publisher_id=111213
#        )
#        print("Broadcast published")
#
#    asyncio.run(main())
