import asyncio
import json
import logging
import time
import aiohttp
from typing import Optional, Dict, Any, Set, List
from tweeterpy import TweeterPy

from chat_client import ChatClient
from custom_janus_client import JanusClient, EventEmitter, JanusConfig
from authenticator import get_periscope_cookie, generate_random_id

logger = logging.getLogger(__name__)


# ----- Example type hints, similar to your TypeScript interfaces -----

class AudioData:
    def __init__(self, bitsPerSample: int, sampleRate: int, channelCount: int, numberOfFrames: int, samples: bytes):
        self.bitsPerSample = bitsPerSample
        self.sampleRate = sampleRate
        self.channelCount = channelCount
        self.numberOfFrames = numberOfFrames
        self.samples = samples


class AudioDataWithUser(AudioData):
    def __init__(self, bitsPerSample: int, sampleRate: int, channelCount: int, numberOfFrames: int, samples: bytes, userId: str):
        super().__init__(bitsPerSample, sampleRate, channelCount, numberOfFrames, samples)
        self.userId = userId


class SpeakerRequest:
    def __init__(self, userId: str, username: str, displayName: str, sessionUUID: str):
        self.userId = userId
        self.username = username
        self.displayName = displayName
        self.sessionUUID = sessionUUID


class OccupancyUpdate:
    def __init__(self, occupancy: int, totalParticipants: int):
        self.occupancy = occupancy
        self.totalParticipants = totalParticipants


class SpaceConfig:
    def __init__(self, mode: str, title: Optional[str] = None, description: Optional[str] = None, languages: Optional[List[str]] = None):
        self.mode = mode
        self.title = title
        self.description = description
        self.languages = languages


class BroadcastCreated:
    def __init__(self, room_id: str, credential: str, stream_name: str, webrtc_gw_url: str, broadcast: Dict[str, Any], access_token: str, endpoint: str, share_url: str, stream_url: str):
        self.room_id = room_id
        self.credential = credential
        self.stream_name = stream_name
        self.webrtc_gw_url = webrtc_gw_url
        self.broadcast = broadcast
        self.access_token = access_token
        self.endpoint = endpoint
        self.share_url = share_url
        self.stream_url = stream_url


class TurnServersInfo:
    def __init__(self, ttl: str, username: str, password: str, uris: List[str]):
        self.ttl = ttl
        self.username = username
        self.password = password
        self.uris = uris


class Plugin:
    """
    Equivalent to the TS Plugin interface.
    """
    def onAttach(self, space: "Space"):
        pass

    def init(self, params: Dict[str, Any]):
        pass

    def onAudioData(self, data: AudioDataWithUser):
        pass

    def cleanup(self):
        pass


class PluginRegistration:
    def __init__(self, plugin: Plugin, config: Optional[Dict[str, Any]] = None):
        self.plugin = plugin
        self.config = config
        
        
async def authorize_token(cookie: str) -> str:
    """
    Python equivalent of authorizeToken(cookie: string): Promise<string>.
    Calls https://proxsee.pscp.tv/api/v2/authorizeToken and returns authorization_token.
    """
    idempotence = generate_random_id()
    headers = {
        "X-Periscope-User-Agent": "Twitter/m5",
        "Content-Type": "application/json",
        "X-Idempotence": idempotence,
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
    idempotence = generate_random_id()
    headers = {
        "X-Periscope-User-Agent": "Twitter/m5",
        "Content-Type": "application/json",
        "Referer": "https://x.com/",
        "X-Idempotence": idempotence,
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
    idempotence = generate_random_id()
    headers = {
        "X-Periscope-User-Agent": "Twitter/m5",
        "Content-Type": "application/json",
        "Referer": "https://x.com/",
        "X-Idempotence": idempotence,
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
) -> Dict[str, Any]:
    """
    Python equivalent of createBroadcast(...).
    Creates a new broadcast via https://proxsee.pscp.tv/api/v2/createBroadcast
    and returns the JSON response.
    """
    idempotence = generate_random_id()
    headers = {
        "X-Periscope-User-Agent": "Twitter/m5",
        "Content-Type": "application/json",
        "X-Idempotence": idempotence,
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


# ----- Python version of the Space class -----

class Space(EventEmitter):
    """
    This class orchestrates:
    1) Creation of the broadcast
    2) Instantiation of Janus + Chat
    3) Approve speakers, push audio, etc.
    """

    def __init__(self, twitter_auth: TweeterPy):
        super().__init__()

        self.auth = twitter_auth

        self.janus_client: Optional["JanusClient"] = None
        self.chat_client: Optional["ChatClient"] = None
        self.auth_token: Optional[str] = None
        self.broadcast_info: Optional[BroadcastCreated] = None
        self.is_initialized = False
        self.plugins: Set[PluginRegistration] = set()

    def use(self, plugin: Plugin, config: Optional[Dict[str, Any]] = None):
        registration = PluginRegistration(plugin, config)
        self.plugins.add(registration)

        logger.info("[Space] Plugin added => %s", plugin.__class__.__name__)

        # If the plugin has onAttach, call it immediately
        if hasattr(plugin, "onAttach") and plugin.onAttach:
            plugin.onAttach(self)

        # If space is already initialized, call plugin.init() now
        if self.is_initialized and hasattr(plugin, "init") and plugin.init:
            plugin.init({
                "space": self,
                "pluginConfig": config,
            })

        return self

    async def initialize(self, config: SpaceConfig) -> BroadcastCreated:
        logger.info("[Space] Initializing...")

        # 1) get Periscope cookie from the scraper
        cookie = await get_periscope_cookie(self.auth)

        # 2) get region
        region = await get_region()
        logger.info("[Space] Got region => %s", region)

        # 3) create broadcast
        logger.info("[Space] Creating broadcast...")
        broadcast = await create_broadcast(
            description=config.description,
            languages=config.languages,
            cookie=cookie,
            region=region,
        )
        self.broadcast_info = broadcast

        # 4) Authorize token if needed
        logger.info("[Space] Authorizing token...")
        self.auth_token = await authorize_token(cookie)

        # 5) Get TURN servers
        logger.info("[Space] Getting turn servers...")
        turn_servers: TurnServersInfo = await get_turn_servers(cookie)

        # 6) Create Janus client
        janus_config = JanusConfig(
            webrtc_url=broadcast["webrtc_gw_url"],
            room_id=broadcast["room_id"],
            credential=broadcast["credential"],
            user_id=broadcast["broadcast"]["user_id"],
            stream_name=broadcast["stream_name"],
            turn_servers=turn_servers,
        )
        self.janus_client = JanusClient(config=janus_config)

        # Listen for incoming audio from Janus
        self.janus_client.on("audioDataFromSpeaker", self._handle_audio_data)
        await self.janus_client.initialize()

        # 7) Publish the broadcast
        logger.info("[Space] Publishing broadcast...")
        await publish_broadcast({
            "title": config.title or "",
            "broadcast": broadcast,
            "cookie": cookie,
            "janusSessionId": self.janus_client.getSessionId(),
            "janusHandleId": self.janus_client.getHandleId(),
            "janusPublisherId": self.janus_client.getPublisherId(),
        })

        # 8) If interactive, open chat
        if config.mode == "INTERACTIVE":
            logger.info("[Space] Connecting chat...")
            self.chat_client = ChatClient(
                broadcast.room_id,
                broadcast.access_token,
                broadcast.endpoint,
            )
            await self.chat_client.connect()
            self._setup_chat_events()

        self.is_initialized = True
        logger.info("[Space] Initialized => %s", broadcast.share_url)

        # Call init() on all plugins
        for reg in self.plugins:
            if hasattr(reg.plugin, "init") and reg.plugin.init:
                reg.plugin.init({
                    "space": self,
                    "pluginConfig": reg.config,
                })

        logger.info("[Space] All plugins initialized")
        return broadcast

    def _setup_chat_events(self):
        if not self.chat_client:
            return

        def on_speaker_request(req):
            logger.info("[Space] Speaker request => %s", req)
            self.emit("speakerRequest", req)

        def on_occupancy_update(update):
            self.emit("occupancyUpdate", update)

        def on_mute_state_changed(evt):
            self.emit("muteStateChanged", evt)

        self.chat_client.on("speakerRequest", on_speaker_request)
        self.chat_client.on("occupancyUpdate", on_occupancy_update)
        self.chat_client.on("muteStateChanged", on_mute_state_changed)

    async def approve_speaker(self, user_id: str, session_uuid: str):
        if not self.is_initialized or not self.broadcast_info:
            raise RuntimeError("[Space] Not initialized or no broadcastInfo")

        if not self.auth_token:
            raise RuntimeError("[Space] No auth token available")

        # 1) Call the request/approve endpoint
        await self._call_approve_endpoint(
            self.broadcast_info,
            self.auth_token,
            user_id,
            session_uuid
        )

        # 2) Subscribe in Janus => receive speaker's audio
        if self.janus_client:
            await self.janus_client.subscribeSpeaker(user_id)

    async def _call_approve_endpoint(
        self,
        broadcast: BroadcastCreated,
        authorization_token: str,
        user_id: str,
        session_uuid: str,
    ):
        url = "https://guest.pscp.tv/api/v1/audiospace/request/approve"
        headers = {
            "Content-Type": "application/json",
            "Referer": "https://x.com/",
            "Authorization": authorization_token,
        }
        body = {
            "ntpForBroadcasterFrame": "2208988800024000300",
            "ntpForLiveFrame": "2208988800024000300",
            "chat_token": broadcast.access_token,
            "session_uuid": session_uuid,
        }

        logger.info("[Space] Approving speaker => %s %s", url, body)
        # Example with aiohttp:
        """
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=body) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(
                        f"[Space] Failed to approve speaker => {resp.status}: {text}"
                    )
        """
        # For brevity, just show pseudo-code success:
        logger.info("[Space] Speaker approved => %s", user_id)

    def push_audio(self, samples: "Int16Array", sample_rate: int):
        if self.janus_client:
            self.janus_client.pushLocalAudio(samples, sample_rate)

    def _handle_audio_data(self, data: AudioDataWithUser):
        """
        This is called when JanusClient emits 'audioDataFromSpeaker'.
        We forward the audio to all plugins.
        """
        for reg in self.plugins:
            if hasattr(reg.plugin, "onAudioData") and reg.plugin.onAudioData:
                reg.plugin.onAudioData(data)

    async def finalize_space(self):
        logger.info("[Space] finalizeSpace => stopping broadcast gracefully")
        tasks = []

        if self.janus_client:
            tasks.append(self.janus_client.destroyRoom())

        if self.broadcast_info:
            tasks.append(self._end_audiospace({
                "broadcastId": self.broadcast_info.room_id,
                "chatToken": self.broadcast_info.access_token,
            }))

        if self.janus_client:
            tasks.append(self.janus_client.leaveRoom())

        # Run them concurrently
        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info("[Space] finalizeSpace => done.")

    async def _end_audiospace(self, params: Dict[str, str]):
        url = "https://guest.pscp.tv/api/v1/audiospace/admin/endAudiospace"
        headers = {
            "Content-Type": "application/json",
            "Referer": "https://x.com/",
            "Authorization": self.auth_token or "",
        }
        body = {
            "broadcast_id": params["broadcastId"],
            "chat_token": params["chatToken"],
        }

        logger.info("[Space] endAudiospace => %s", body)
        # Example with aiohttp pseudo-code:
        """
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=body) as resp:
                if resp.status != 200:
                    errText = await resp.text()
                    raise RuntimeError(
                        f"[Space] endAudiospace => {resp.status} {errText}"
                    )
                json_resp = await resp.json()
                logger.info("[Space] endAudiospace => success => %s", json_resp)
        """

    async def stop(self):
        logger.info("[Space] Stopping...")

        await self.finalize_space()

        if self.chat_client:
            await self.chat_client.disconnect()
            self.chat_client = None

        if self.janus_client:
            await self.janus_client.stop()
            self.janus_client = None

        for reg in self.plugins:
            if hasattr(reg.plugin, "cleanup") and reg.plugin.cleanup:
                reg.plugin.cleanup()
        self.plugins.clear()

        self.is_initialized = False
        logger.info("[Space] Stopped.")

