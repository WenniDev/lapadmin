from pydantic import BaseModel, computed_field
from typing import Optional, List
from string import Template
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
import os

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
CLIENT_SECRET_FILE = "keys/client_secret.json"
TOKEN_FILE = "keys/token.json"

DATA_DIR = Path("data").resolve()

is_ready = os.path.exists(CLIENT_SECRET_FILE)

_creds = None


def _get_credentials():
    """Lazily load (and cache) the OAuth credentials.

    Deferred so importing this module never blocks on the interactive OAuth
    flow (or fails outright) when Google credentials aren't configured -
    only code paths that actually talk to YouTube pay that cost. Credentials
    refresh their own access token on demand, so caching the object itself
    (rather than a built client) is safe long-term.
    """
    global _creds
    if _creds is not None:
        return _creds

    if not is_ready:
        raise RuntimeError(
            f"{CLIENT_SECRET_FILE} not found, YouTube integration is not configured."
        )

    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        creds.refresh(Request())
    else:
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
        creds = flow.run_local_server()
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())

    _creds = creds
    return _creds


def get_youtube():
    """Build a fresh YouTube API client.

    httplib2's transport isn't thread-safe, so a single client can't be
    shared across concurrent requests (the Streams page polls several
    games in parallel) without corrupting the underlying TLS connection -
    only the credentials are cached and reused, not the built client.
    """
    return build("youtube", "v3", credentials=_get_credentials())


class YoutubeStream(BaseModel):
    title: str
    description: str
    id: str
    url: str
    video_embed_url: Optional[str] = None
    chat_iframe_url: Optional[str] = None
    playing: bool
    created: datetime
    started: Optional[datetime] = None
    ended: Optional[datetime] = None

    @property
    def playing(self) -> bool:
        return self.started is not None and self.ended is None

    def start(self):
        if self.playing:
            raise Exception("Stream is already playing.")

        self.started = datetime.now()
        self.playing = True

        get_youtube().liveBroadcasts().transition(
            broadcastStatus="live",
            id=self.id,
            part="status"
        ).execute()

        print(f'Started stream: {self.title}')

    def stop(self):
        print(f'Stopping stream: {self.title}')


class GameStreamEntry(BaseModel):
    game: str
    playlist: Optional[str] = None
    unlisted: Optional[bool] = False


class GameStreamConfig(BaseModel):
    entries: List[GameStreamEntry]
    title: str
    description: str


STREAM_STATUS_LABELS = {
    "created": "En attente",
    "ready": "En attente",
    "active": "Connecté",
    "inactive": "En attente",
    "error": "Erreur",
}

STREAM_HEALTH_LABELS = {
    "good": "Bon",
    "ok": "Correct",
    "bad": "Mauvais",
    "noData": "Aucune donnée",
}


class LiveStreamInfo(BaseModel):
    id: str
    title: str
    rtmp_url: str
    stream_key: str
    resolution: Optional[str] = None
    frame_rate: Optional[str] = None
    stream_status: Optional[str] = None
    health_status: Optional[str] = None

    @computed_field
    @property
    def is_active(self) -> bool:
        return self.stream_status == "active"

    @computed_field
    @property
    def stream_status_label(self) -> str:
        return STREAM_STATUS_LABELS.get(self.stream_status, self.stream_status or "Inconnu")

    @computed_field
    @property
    def health_status_label(self) -> Optional[str]:
        if not self.health_status:
            return None
        return STREAM_HEALTH_LABELS.get(self.health_status, self.health_status)


STATUS_LABELS = {
    "created": "Créé",
    "ready": "Prêt",
    "testStarting": "Démarrage du test",
    "testing": "En test",
    "liveStarting": "Démarrage",
    "live": "En direct",
    "complete": "Terminé",
    "revoked": "Annulé",
}

PRIVACY_LABELS = {
    "public": "Public",
    "unlisted": "Non répertorié",
    "private": "Privé",
}


class BroadcastInfo(BaseModel):
    id: str
    title: str
    url: str
    status: str
    privacy: str
    published_at: datetime

    @computed_field
    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    @computed_field
    @property
    def privacy_label(self) -> str:
        return PRIVACY_LABELS.get(self.privacy, self.privacy)


class StreamStatusResponse(BaseModel):
    broadcast: Optional[BroadcastInfo] = None
    live_stream: Optional[LiveStreamInfo] = None


class GameStream(BaseModel):
    game: str
    playlist: Optional[str] = None
    unlisted: Optional[bool] = False
    title: str
    description: str

    @classmethod
    def from_entry(
        cls,
        entry: GameStreamEntry,
        title_template: str,
        desc_template: str
    ) -> "GameStream":
        context = {"game": entry.game}

        return cls(
            game=entry.game,
            playlist=entry.playlist,
            unlisted=entry.unlisted,
            title=Template(title_template).substitute(context),
            description=Template(desc_template).substitute(context),
        )

    def get_latest_stream(self) -> Optional[YoutubeStream]:
        return YoutubeStream(
            title="testLatest",
            description="test",
            id="test",
            url="https://www.youtube.com/watch?v=test",
            video_embed_url="https://www.youtube.com/embed/test",
            chat_iframe_url="https://www.youtube.com/live_chat?is_popout=1&v=test",
            playing=False,
            created=datetime.now(),
            started=datetime.now(),
            ended=None,
        )

    def _live_stream_title(self) -> str:
        """Title used to identify this game's persistent CDN/ingestion endpoint."""
        return f"LAP - {self.game}"

    @staticmethod
    def _live_stream_info(item: dict) -> LiveStreamInfo:
        cdn = item["cdn"]
        ingestion = cdn["ingestionInfo"]
        status = item.get("status", {})
        return LiveStreamInfo(
            id=item["id"],
            title=item["snippet"]["title"],
            rtmp_url=ingestion["ingestionAddress"],
            stream_key=ingestion["streamName"],
            resolution=cdn.get("resolution"),
            frame_rate=cdn.get("frameRate"),
            stream_status=status.get("streamStatus"),
            health_status=status.get("healthStatus", {}).get("status"),
        )

    def find_live_stream(self) -> Optional[LiveStreamInfo]:
        """Find this game's persistent liveStream (CDN/RTMP ingestion endpoint),
        if it has already been created.
        """
        title = self._live_stream_title()

        existing = get_youtube().liveStreams().list(
            part="id,snippet,cdn,status",
            mine=True,
            maxResults=50,
        ).execute()

        for item in existing.get("items", []):
            if item["snippet"]["title"] == title:
                return self._live_stream_info(item)

        return None

    def create_live_stream(self) -> LiveStreamInfo:
        """Create this game's persistent liveStream (CDN/RTMP ingestion endpoint)."""
        title = self._live_stream_title()

        created = get_youtube().liveStreams().insert(
            part="snippet,cdn,contentDetails,status",
            body={
                "snippet": {"title": title},
                "cdn": {
                    "resolution": "1080p",
                    "frameRate": "60fps",
                    "ingestionType": "rtmp",
                },
                "contentDetails": {"isReusable": True},
            },
        ).execute()

        print(f'Created live stream: {created}')

        return self._live_stream_info(created)

    def find_or_create_live_stream(self) -> LiveStreamInfo:
        """Find this game's persistent liveStream, or create it if missing.

        Reused across broadcasts so the cabinet's encoder (OBS) only ever
        needs to be configured with one stream key per game.
        """
        return self.find_live_stream() or self.create_live_stream()

    def find_latest_broadcast(self) -> Optional[BroadcastInfo]:
        """Find this game's most recent broadcast (any status), if any."""
        resp = get_youtube().liveBroadcasts().list(
            part="snippet,status",
            broadcastType="all",
            mine=True,
            maxResults=50,
        ).execute()

        matches = [
            item
            for item in resp.get("items", [])
            if item["snippet"]["title"] == self.title
        ]
        if not matches:
            return None

        matches.sort(key=lambda item: item["snippet"]["publishedAt"], reverse=True)
        item = matches[0]

        return BroadcastInfo(
            id=item["id"],
            title=item["snippet"]["title"],
            url=f"https://www.youtube.com/watch?v={item['id']}",
            status=item["status"]["lifeCycleStatus"],
            privacy=item["status"]["privacyStatus"],
            published_at=item["snippet"]["publishedAt"],
        )

    def default_privacy(self) -> str:
        return "unlisted" if self.unlisted else "public"

    def create(self, privacy: Optional[str] = None) -> YoutubeStream:
        live_stream = self.find_or_create_live_stream()

        stream = get_youtube().liveBroadcasts().insert(
            part="snippet,status,contentDetails",
            body={
                "snippet": {
                    "title": self.title,
                    "description": self.description,
                    "scheduledStartTime": datetime.now(timezone.utc).isoformat(),
                },
                "contentDetails": {
                    # Manual control only: YouTube rejects manual transition
                    # calls entirely when these are enabled, so they'd make
                    # the Lancer/Arrêter buttons never work.
                    "enableAutoStart": False,
                    "enableAutoStop": False,
                },
                "status": {
                    "privacyStatus": privacy or self.default_privacy(),
                },
            }
        ).execute()

        print(f'Created stream: {stream}')

        get_youtube().liveBroadcasts().bind(
            id=stream["id"],
            part="id,contentDetails",
            streamId=live_stream.id,
        ).execute()

        print(f'Bound live stream {live_stream.id} to broadcast {stream["id"]}')

        return YoutubeStream(
            title=self.title,
            description=self.description,
            id=stream["id"],
            url=f"https://www.youtube.com/watch?v={stream['id']}",
            video_embed_url=f"https://www.youtube.com/embed/{stream['id']}",
            chat_iframe_url=f"https://www.youtube.com/live_chat?is_popout=1&v={stream['id']}",
            playing=False,
            created=datetime.now(),
            started=None,
            ended=None,
        )

    def start_broadcast(self, privacy: Optional[str] = None) -> BroadcastInfo:
        """Ensure a live broadcast exists for this game and try to transition it to live.

        Reuses the latest broadcast unless it already ended (complete), in
        which case a new one is created with the given privacy (falling back
        to this game's configured default).
        """
        broadcast = self.find_latest_broadcast()
        if broadcast is None or broadcast.status == "complete":
            self.create(privacy=privacy)

        return self.sync_broadcast_status()

    def sync_broadcast_status(self) -> Optional[BroadcastInfo]:
        """Fetch the latest broadcast and advance it towards live if possible.

        YouTube requires going through the full ready -> testing -> live
        sequence (no skipping straight to live), and each step requires
        status.streamStatus to be "active" for the bound stream, which can
        take a little while after (re)binding an existing stream to a
        freshly created broadcast. This is called on every status poll (see
        /api/streams/<game>/status/) so the broadcast advances a step at a
        time on its own as soon as YouTube reports the stream active,
        without needing anyone to click Lancer again.
        """
        broadcast = self.find_latest_broadcast()
        if broadcast is None or broadcast.status not in ("ready", "testing"):
            return broadcast

        next_status = "testing" if broadcast.status == "ready" else "live"

        try:
            get_youtube().liveBroadcasts().transition(
                broadcastStatus=next_status,
                id=broadcast.id,
                part="status",
            ).execute()
        except HttpError as e:
            if e.resp.status != 403 or "invalidTransition" not in str(e):
                raise
            return broadcast

        return self.find_latest_broadcast()

    def stop_broadcast(self) -> Optional[BroadcastInfo]:
        """Transition the current broadcast (if any and not already ended) to complete."""
        broadcast = self.find_latest_broadcast()
        if broadcast is None or broadcast.status == "complete":
            return broadcast

        get_youtube().liveBroadcasts().transition(
            broadcastStatus="complete",
            id=broadcast.id,
            part="status",
        ).execute()

        return self.find_latest_broadcast()


def load_game_stream_config() -> GameStreamConfig:
    with (DATA_DIR / "streams.yml").open() as f:
        data = yaml.safe_load(f)["streams"]

    return GameStreamConfig(**data)


def should_create_stream(
        next_closing_time: datetime,
        now: Optional[datetime] = None
) -> bool:
    now = now or datetime.now()
    return (next_closing_time - now) >= timedelta(minutes=30)


def get() -> List[GameStream]:
    config = load_game_stream_config()
    return [
        GameStream.from_entry(entry, config.title, config.description)
        for entry in config.entries
    ]


def start():
    for stream in get():
        yt_stream = stream.get_latest_stream() or stream.create()
        if yt_stream.ended:
            yt_stream = stream.create()
        yt_stream.start()


if __name__ == "__main__":
    start()