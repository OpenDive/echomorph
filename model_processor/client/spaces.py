# ----- Standard Library Imports -----
import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Set

# ----- Third-Party Imports -----
import aiohttp
from pyee.base import EventEmitter

# ----- Local Imports -----
from authenticator import get_periscope_cookie, generate_random_id
from chat_client import ChatClient
from custom_janus_client import JanusClient, JanusConfig
from tweeterpy import TweeterPy

logger = logging.getLogger(__name__)


# ----- Data Models -----


class AudioData:
    """
    Holds audio data parameters and raw PCM samples.
    """

    def __init__(
        self,
        bitsPerSample: int,
        sampleRate: int,
        channelCount: int,
        numberOfFrames: int,
        samples: bytes,
    ):
        """
        :param bitsPerSample:  Bits per audio sample.
        :param sampleRate:     The sample rate in Hz.
        :param channelCount:   The number of channels in the audio data.
        :param numberOfFrames: The number of audio frames in `samples`.
        :param samples:        The actual PCM data in bytes.
        """
        self.bitsPerSample = bitsPerSample
        self.sampleRate = sampleRate
        self.channelCount = channelCount
        self.numberOfFrames = numberOfFrames
        self.samples = samples


class AudioDataWithUser(AudioData):
    """
    Extends AudioData by including the user ID associated with the audio.
    """

    def __init__(
        self,
        bitsPerSample: int,
        sampleRate: int,
        channelCount: int,
        numberOfFrames: int,
        samples: bytes,
        userId: str,
    ):
        """
        :param userId: The identifier of the user who sent this audio data.
        """
        super().__init__(bitsPerSample, sampleRate, channelCount, numberOfFrames, samples)
        self.userId = userId


class SpeakerRequest:
    """
    Represents a request from a user to speak in the audio space.
    """

    def __init__(self, userId: str, username: str, displayName: str, sessionUUID: str):
        """
        :param userId:      Unique identifier for the user.
        :param username:    The user's handle or username.
        :param displayName: The user's display name.
        :param sessionUUID: A unique ID for this speaker request session.
        """
        self.userId = userId
        self.username = username
        self.displayName = displayName
        self.sessionUUID = sessionUUID


class OccupancyUpdate:
    """
    Contains updated occupancy information for the space.
    """

    def __init__(self, occupancy: int, totalParticipants: int):
        """
        :param occupancy:         Current number of active participants.
        :param totalParticipants: Total participants that have joined so far.
        """
        self.occupancy = occupancy
        self.totalParticipants = totalParticipants


class SpaceConfig:
    """
    Holds configuration details for creating and managing an audio space.
    """

    def __init__(
        self,
        mode: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        languages: Optional[List[str]] = None,
    ):
        """
        :param mode:        The mode of the space (e.g., INTERACTIVE).
        :param title:       Title of the space.
        :param description: Description of the space.
        :param languages:   Preferred languages for the space.
        """
        self.mode = mode
        self.title = title
        self.description = description
        self.languages = languages


class BroadcastCreated:
    """
    Represents the data returned from creating a broadcast.
    """

    def __init__(
        self,
        room_id: str,
        credential: str,
        stream_name: str,
        webrtc_gw_url: str,
        broadcast: Dict[str, Any],
        access_token: str,
        endpoint: str,
        share_url: str,
        stream_url: str,
    ):
        """
        :param room_id:       The ID of the newly created room.
        :param credential:    A credential string for authentication with Janus.
        :param stream_name:   Name of the RTMP/WebRTC stream.
        :param webrtc_gw_url: The Janus gateway URL to use.
        :param broadcast:     Additional broadcast metadata from the server.
        :param access_token:  Token required for chat or other operations.
        :param endpoint:      Chat or other API endpoint.
        :param share_url:     A URL to share the broadcast.
        :param stream_url:    The actual media stream URL (if applicable).
        """
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
    """
    Contains TURN server configuration details.
    """

    def __init__(self, ttl: str, username: str, password: str, uris: List[str]):
        """
        :param ttl:      Time-to-live for the TURN credentials.
        :param username: Username for TURN servers.
        :param password: Password for TURN servers.
        :param uris:     A list of TURN server URIs.
        """
        self.ttl = ttl
        self.username = username
        self.password = password
        self.uris = uris


# ----- Plugin System -----


class Plugin:
    """
    Basic interface for a plugin with attach, init, audio handling, and cleanup.
    """

    def onAttach(self, space: "Space"):
        """
        Called when the plugin is first attached to the Space instance.
        """
        pass

    def init(self, params: Dict[str, Any]):
        """
        Called when the Space is fully initialized, passing plugin configuration and the space object.
        """
        pass

    def onAudioData(self, data: AudioDataWithUser):
        """
        Called when new audio data arrives from a speaker.
        """
        pass

    def cleanup(self):
        """
        Cleanup routine when the space is stopped or plugin is removed.
        """
        pass


class PluginRegistration:
    """
    Holds a plugin instance and its configuration for registration in the Space.
    """

    def __init__(self, plugin: Plugin, config: Optional[Dict[str, Any]] = None):
        """
        :param plugin: The plugin instance.
        :param config: An optional dictionary of plugin-specific configuration.
        """
        self.plugin = plugin
        self.config = config


# ----- API Helpers -----


async def authorize_token(cookie: str) -> str:
    """
    Authorize a token with the Periscope service via /api/v2/authorizeToken.

    :param cookie: The Periscope cookie (retrieved by a separate mechanism).
    :return:       An authorization_token string.
    :raises RuntimeError: If the token request fails or the token is missing.
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
                raise RuntimeError("authorize_token: Missing authorization_token in response")
            return authorization_token


async def publish_broadcast(
    title: str,
    broadcast: Dict[str, Any],
    cookie: str,
    janus_session_id: Optional[int] = None,
    janus_handle_id: Optional[int] = None,
    janus_publisher_id: Optional[int] = None,
):
    """
    Finalize broadcast configuration by calling /api/v2/publishBroadcast.

    :param title:             Title or status of the broadcast.
    :param broadcast:         Dictionary containing broadcast details (room_id, credential, etc.).
    :param cookie:            The Periscope cookie.
    :param janus_session_id:  Optional Janus session ID.
    :param janus_handle_id:   Optional Janus handle ID.
    :param janus_publisher_id:Optional publisher ID assigned by Janus.
    :raises RuntimeError:     If the request to publish fails.
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
                raise RuntimeError(f"Failed to publish broadcast => {resp.status}")
            # NOTE: The response body isn't strictly used in the original code.


async def get_turn_servers(cookie: str) -> Dict[str, Any]:
    """
    Retrieve TURN servers from /api/v2/turnServers.

    :param cookie: The Periscope cookie.
    :return:       A dictionary containing TURN server info.
    :raises RuntimeError: If the request fails or returns a non-200 status.
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
                raise RuntimeError(f"Failed to get turn servers => {resp.status}")
            return await resp.json()


async def get_region() -> str:
    """
    Retrieve the region from https://signer.pscp.tv/region.

    :return:        A string indicating the region, e.g. 'us-east'.
    :raises RuntimeError: If the request fails or region is missing.
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
    languages: Optional[List[str]],
    cookie: str,
    region: str,
) -> Dict[str, Any]:
    """
    Create a new broadcast via /api/v2/createBroadcast.

    :param description: Optional broadcast description.
    :param languages:   Optional list of language codes.
    :param cookie:      The Periscope cookie.
    :param region:      The region from get_region().
    :return:            A dictionary with the new broadcast's information.
    :raises RuntimeError: If creation fails or returns non-200.
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
                raise RuntimeError(f"Failed to create broadcast => {resp.status} {text}")
            return await resp.json()


# ----- Main Space Class -----


class Space(EventEmitter):
    """
    Manages the creation and lifecycle of a broadcast “Space,” including:
    1) Creating the broadcast
    2) Instantiating Janus + Chat
    3) Handling speaker requests, pushing audio, etc.
    """

    def __init__(self, twitter_auth: TweeterPy):
        """
        :param twitter_auth: Authentication manager (TweeterPy) for obtaining Periscope cookies, etc.
        """
        super().__init__()

        self.auth = twitter_auth
        self.janus_client: Optional[JanusClient] = None
        self.chat_client: Optional[ChatClient] = None

        self.auth_token: Optional[str] = None
        self.broadcast_info: Optional[BroadcastCreated] = None

        self.is_initialized = False
        self.plugins: Set[PluginRegistration] = set()

    def use(self, plugin: Plugin, config: Optional[Dict[str, Any]] = None) -> "Space":
        """
        Register a plugin with optional configuration. Immediately calls `onAttach(space)`.
        If the space is already initialized, also calls `plugin.init()`.

        :param plugin: The plugin instance to attach.
        :param config: Optional configuration dictionary for the plugin.
        :return:       The current Space instance (for chaining).
        """
        registration = PluginRegistration(plugin, config)
        self.plugins.add(registration)

        logger.info("[Space] Plugin added => %s", plugin.__class__.__name__)

        if hasattr(plugin, "onAttach") and plugin.onAttach:
            plugin.onAttach(self)

        if self.is_initialized and hasattr(plugin, "init") and plugin.init:
            plugin.init({"space": self, "pluginConfig": config})

        return self

    async def initialize(self, config: SpaceConfig) -> Optional[BroadcastCreated]:
        """
        Initialize the space:
          1) Get Periscope cookie
          2) Get region
          3) Create broadcast
          4) Authorize token
          5) Get TURN servers
          6) Create and initialize Janus client
        TODO: Publish broadcast, open chat, mark initialization complete, etc.

        :param config: The SpaceConfig object containing mode, title, description, languages, etc.
        :return:       A BroadcastCreated object or None if not completed.
        """
        logger.info("[Space] Initializing...")

        # 1) Get Periscope cookie
        cookie = await get_periscope_cookie(self.auth)

        # 2) Get region
        region = await get_region()
        logger.info("[Space] Got region => %s", region)

        # 3) Create broadcast
        logger.info("[Space] Creating broadcast...")
        broadcast = await create_broadcast(
            description=config.description,
            languages=config.languages,
            cookie=cookie,
            region=region,
        )
        self.broadcast_info = broadcast

        # 4) Authorize token
        logger.info("[Space] Authorizing token...")
        self.auth_token = await authorize_token(cookie)

        # 5) Get TURN servers
        logger.info("[Space] Getting turn servers...")
        turn_servers: Dict[str, Any] = await get_turn_servers(cookie)

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

        # Initialize the Janus client
        await self.janus_client.initialize()

        # TODO:
        #  - Publish the broadcast (publish_broadcast(...))
        #  - If interactive, open chat + set up chat events
        #  - Mark self.is_initialized = True
        #  - Call plugin.init() for each registered plugin
        #  - Return the broadcast info

        return None  # Return broadcast or None based on your logic

    def _setup_chat_events(self):
        """
        Attach event handlers for the ChatClient.
        """
        if not self.chat_client:
            return

        def on_speaker_request(req: SpeakerRequest):
            logger.info("[Space] Speaker request => %s", req)
            self.emit("speakerRequest", req)

        def on_occupancy_update(update: OccupancyUpdate):
            self.emit("occupancyUpdate", update)

        def on_mute_state_changed(evt: Any):
            self.emit("muteStateChanged", evt)

        self.chat_client.on("speakerRequest", on_speaker_request)
        self.chat_client.on("occupancyUpdate", on_occupancy_update)
        self.chat_client.on("muteStateChanged", on_mute_state_changed)

    async def approve_speaker(self, user_id: str, session_uuid: str):
        """
        Approve a speaker request:
          1) Call the /request/approve endpoint
          2) Subscribe in Janus to receive the speaker's audio

        :param user_id:      The user to approve as speaker.
        :param session_uuid: A session UUID from the speaker request.
        :raises RuntimeError: If the space is not initialized or no auth token is found.
        """
        if not self.is_initialized or not self.broadcast_info:
            raise RuntimeError("[Space] Not initialized or no broadcastInfo")

        if not self.auth_token:
            raise RuntimeError("[Space] No auth token available")

        await self._call_approve_endpoint(
            self.broadcast_info,
            self.auth_token,
            user_id,
            session_uuid
        )

        if self.janus_client:
            # TODO: Implement a subscribeSpeaker method in your Janus client
            await self.janus_client.subscribeSpeaker(user_id)

    async def _call_approve_endpoint(
        self,
        broadcast: BroadcastCreated,
        authorization_token: str,
        user_id: str,
        session_uuid: str,
    ):
        """
        Internal method to request/approve a speaker via the relevant Periscope API.

        :param broadcast:          The broadcast data.
        :param authorization_token:Auth token used for making requests.
        :param user_id:            The user to approve.
        :param session_uuid:       The speaker session UUID.
        """
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
        # TODO: Perform actual aiohttp request here
        logger.info("[Space] Speaker approved => %s", user_id)

    def push_audio(self, samples: "Int16Array", sample_rate: int):
        """
        Push local audio data into Janus for broadcasting.

        :param samples:    The raw PCM data in int16 format.
        :param sample_rate:The audio sample rate in Hz.
        """
        if self.janus_client:
            # TODO: Ensure pushLocalAudio exists on the Janus client
            self.janus_client.pushLocalAudio(samples, sample_rate)

    def _handle_audio_data(self, data: AudioDataWithUser):
        """
        Handle inbound audio data from Janus and distribute to plugins.

        :param data: Audio data from a remote speaker.
        """
        for reg in self.plugins:
            if hasattr(reg.plugin, "onAudioData") and reg.plugin.onAudioData:
                reg.plugin.onAudioData(data)

    async def finalize_space(self):
        """
        Gracefully stop the broadcast and related services, such as Janus and chat.
        """
        logger.info("[Space] finalizeSpace => stopping broadcast gracefully")
        tasks = []

        if self.janus_client:
            # TODO: Implement destroyRoom() and leaveRoom() in your Janus client
            tasks.append(self.janus_client.destroyRoom())
        if self.broadcast_info:
            tasks.append(
                self._end_audiospace(
                    {
                        "broadcastId": self.broadcast_info.room_id,
                        "chatToken": self.broadcast_info.access_token,
                    }
                )
            )
        if self.janus_client:
            tasks.append(self.janus_client.leaveRoom())

        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("[Space] finalizeSpace => done.")

    async def _end_audiospace(self, params: Dict[str, str]):
        """
        Inform the Periscope API that the audio space has ended.

        :param params: Must include "broadcastId" and "chatToken".
        """
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
        # TODO: Perform the actual request using aiohttp

    async def stop(self):
        """
        Stop the space and clean up all services (Janus, chat, plugins).
        """
        logger.info("[Space] Stopping...")

        await self.finalize_space()

        if self.chat_client:
            await self.chat_client.disconnect()
            self.chat_client = None

        if self.janus_client:
            # TODO: Implement stop() in your Janus client
            await self.janus_client.stop()
            self.janus_client = None

        for reg in self.plugins:
            if hasattr(reg.plugin, "cleanup") and reg.plugin.cleanup:
                reg.plugin.cleanup()
        self.plugins.clear()

        self.is_initialized = False
        logger.info("[Space] Stopped.")
