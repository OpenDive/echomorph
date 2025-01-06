import asyncio
import json
import logging
from typing import Optional, Dict, Any, Set

# Placeholders for your actual modules:
# from your_module.chat_client import ChatClient
# from your_module.janus_client import JanusClient
# from your_module.scraper import Scraper
# from your_module.utils import get_turn_servers, create_broadcast, publish_broadcast, authorize_token, get_region
from chat_client import ChatClient
from janus_client import JanusClient
from utils import get_turn_servers, create_broadcast, publish_broadcast, authorize_token, get_region
from authenticator import TwitterAuth, get_periscope_cookie

logger = logging.getLogger(__name__)


class EventEmitter:
    """
    Minimal Python equivalent of Node's EventEmitter.
    """
    def __init__(self):
        self._listeners = {}

    def on(self, event_name, callback):
        if event_name not in self._listeners:
            self._listeners[event_name] = []
        self._listeners[event_name].append(callback)

    def emit(self, event_name, *args, **kwargs):
        if event_name in self._listeners:
            for callback in self._listeners[event_name]:
                callback(*args, **kwargs)


# ----- Example type hints, similar to your TypeScript interfaces -----

class AudioData:
    bitsPerSample: int
    sampleRate: int
    channelCount: int
    numberOfFrames: int
    samples: bytes  # or memoryview, or an array


class AudioDataWithUser(AudioData):
    userId: str


class SpeakerRequest:
    userId: str
    username: str
    displayName: str
    sessionUUID: str


class OccupancyUpdate:
    occupancy: int
    totalParticipants: int


class SpaceConfig:
    mode: str  # 'BROADCAST' | 'LISTEN' | 'INTERACTIVE'
    title: Optional[str] = None
    description: Optional[str] = None
    languages: Optional[list] = None


class BroadcastCreated:
    room_id: str
    credential: str
    stream_name: str
    webrtc_gw_url: str
    broadcast: Dict[str, Any]
    access_token: str
    endpoint: str
    share_url: str
    stream_url: str


class TurnServersInfo:
    ttl: str
    username: str
    password: str
    uris: list


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


# ----- Python version of the Space class -----

class Space(EventEmitter):
    """
    This class orchestrates:
    1) Creation of the broadcast
    2) Instantiation of Janus + Chat
    3) Approve speakers, push audio, etc.
    """

    def __init__(self, bearer_token: str, guest_token: str):
        super().__init__()
        
        self.auth = TwitterAuth(bearer_token=bearer_token, guest_token=guest_token)

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
        cookie = await self.get_periscope_cookie(self.auth)

        # 2) get region
        region = await get_region()
        logger.info("[Space] Got region => %s", region)

        # 3) create broadcast
        logger.info("[Space] Creating broadcast...")
        broadcast = await create_broadcast({
            "description": config.description,
            "languages": config.languages,
            "cookie": cookie,
            "region": region,
        })
        self.broadcast_info = broadcast

        # 4) Authorize token if needed
        logger.info("[Space] Authorizing token...")
        self.auth_token = await authorize_token(cookie)

        # 5) Get TURN servers
        logger.info("[Space] Getting turn servers...")
        turn_servers: TurnServersInfo = await get_turn_servers(cookie)

        # 6) Create Janus client
        self.janus_client = JanusClient({
            "webrtcUrl": broadcast.webrtc_gw_url,
            "roomId": broadcast.room_id,
            "credential": broadcast.credential,
            "userId": broadcast.broadcast["user_id"],
            "streamName": broadcast.stream_name,
            "turnServers": turn_servers,
        })

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

