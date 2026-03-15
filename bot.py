import os
import re
import random
import asyncio
from collections import deque

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

# ── Spotify client (optional — works without it, just no Spotify URL support) ──
sp = None
if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET:
    sp = spotipy.Spotify(
        auth_manager=SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
        )
    )

# ── yt-dlp options ─────────────────────────────────────────────────────────────
YDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}

FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

# ── URL patterns ───────────────────────────────────────────────────────────────
YOUTUBE_RE = re.compile(
    r"(https?://)?(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com)/.+"
)
SPOTIFY_TRACK_RE = re.compile(
    r"(https?://)?open\.spotify\.com/track/([A-Za-z0-9]+)"
)
SPOTIFY_ALBUM_RE = re.compile(
    r"(https?://)?open\.spotify\.com/album/([A-Za-z0-9]+)"
)
SPOTIFY_PLAYLIST_RE = re.compile(
    r"(https?://)?open\.spotify\.com/playlist/([A-Za-z0-9]+)"
)


# ── Helper dataclass ──────────────────────────────────────────────────────────
class Song:
    """Represents a queued song."""

    def __init__(self, title: str, url: str, requester: discord.Member):
        self.title = title
        self.url = url
        self.requester = requester


# ── Per-guild music state ─────────────────────────────────────────────────────
class GuildMusicState:
    def __init__(self):
        self.queue: deque[Song] = deque()
        self.current: Song | None = None
        self.voice_client: discord.VoiceClient | None = None
        self.play_lock = asyncio.Lock()


guild_states: dict[int, GuildMusicState] = {}


def get_state(guild_id: int) -> GuildMusicState:
    if guild_id not in guild_states:
        guild_states[guild_id] = GuildMusicState()
    return guild_states[guild_id]


# ── Bot setup ─────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")


# ── Core audio helpers ────────────────────────────────────────────────────────
async def extract_info(query: str) -> dict | None:
    """Run yt-dlp extraction in a thread so we don't block the event loop."""
    loop = asyncio.get_event_loop()

    def _extract():
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            info = ydl.extract_info(query, download=False)
            # ytsearch returns a list of entries
            if "entries" in info:
                return info["entries"][0] if info["entries"] else None
            return info

    return await loop.run_in_executor(None, _extract)


def resolve_spotify_query(url: str) -> str | None:
    """Convert a Spotify track URL into a 'Artist - Title' search string."""
    if sp is None:
        return None
    match = SPOTIFY_TRACK_RE.match(url)
    if not match:
        return None
    track_id = match.group(2)
    track = sp.track(track_id)
    artist = track["artists"][0]["name"]
    title = track["name"]
    return f"{artist} - {title}"


def resolve_spotify_album(url: str) -> list[str]:
    """Convert a Spotify album URL into a list of 'Artist - Title' search strings."""
    if sp is None:
        return []
    match = SPOTIFY_ALBUM_RE.match(url)
    if not match:
        return []
    album_id = match.group(2)
    results = sp.album_tracks(album_id)
    queries = []
    for track in results["items"]:
        artist = track["artists"][0]["name"]
        title = track["name"]
        queries.append(f"{artist} - {title}")
    return queries


def resolve_spotify_playlist(url: str) -> list[str]:
    """Convert a Spotify playlist URL into a list of 'Artist - Title' search strings."""
    if sp is None:
        return []
    match = SPOTIFY_PLAYLIST_RE.match(url)
    if not match:
        return []
    playlist_id = match.group(2)
    results = sp.playlist_tracks(playlist_id)
    queries = []
    for item in results["items"]:
        track = item.get("track")
        if not track:
            continue
        artist = track["artists"][0]["name"]
        title = track["name"]
        queries.append(f"{artist} - {title}")
    return queries


async def extract_playlist(query: str, max_tracks: int = 25) -> list[dict]:
    """Search YouTube for a playlist matching the query and return its tracks."""
    loop = asyncio.get_event_loop()

    def _extract():
        # Search for a playlist on YouTube
        search_opts = {
            "format": "bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "source_address": "0.0.0.0",
        }
        with yt_dlp.YoutubeDL(search_opts) as ydl:
            results = ydl.extract_info(f"ytsearch5:{query} playlist", download=False)
            if not results or "entries" not in results:
                return []

            # Find the first result that links to a playlist
            playlist_url = None
            for entry in results["entries"]:
                url = entry.get("url", "")
                if "list=" in url:
                    playlist_url = url
                    break

            # If no playlist found, fall back to individual video results
            if not playlist_url:
                results = ydl.extract_info(f"ytsearch{max_tracks}:{query}", download=False)
                if not results or "entries" not in results or not results["entries"]:
                    return []
                return results["entries"][:max_tracks]

            # Extract tracks from the playlist
            playlist_opts = {
                "format": "bestaudio/best",
                "quiet": True,
                "no_warnings": True,
                "extract_flat": True,
                "source_address": "0.0.0.0",
            }
            with yt_dlp.YoutubeDL(playlist_opts) as ydl2:
                playlist = ydl2.extract_info(playlist_url, download=False)
                if not playlist or "entries" not in playlist:
                    return []
                return list(playlist["entries"])[:max_tracks]

    return await loop.run_in_executor(None, _extract)


async def resolve_track_url(video_id: str) -> dict | None:
    """Resolve a video ID into a streamable audio URL."""
    return await extract_info(f"https://www.youtube.com/watch?v={video_id}")


async def play_next(guild_id: int):
    """Play the next song in the queue, or disconnect if empty."""
    state = get_state(guild_id)

    if not state.queue:
        state.current = None
        if state.voice_client and state.voice_client.is_connected():
            await state.voice_client.disconnect()
            state.voice_client = None
        return

    song = state.queue.popleft()
    state.current = song

    source = discord.FFmpegOpusAudio(song.url, **FFMPEG_OPTS)

    def after_play(error):
        if error:
            print(f"Player error: {error}")
        asyncio.run_coroutine_threadsafe(play_next(guild_id), bot.loop)

    state.voice_client.play(source, after=after_play)


# ── Slash commands ────────────────────────────────────────────────────────────
@bot.tree.command(name="play", description="Play a song from YouTube URL, Spotify URL, or search query")
@app_commands.describe(query="YouTube/Spotify URL or search terms")
async def play(interaction: discord.Interaction, query: str):
    if not interaction.guild:
        await interaction.response.send_message("This command only works in a server.", ephemeral=True)
        return

    member = interaction.guild.get_member(interaction.user.id)
    if not member or not member.voice or not member.voice.channel:
        await interaction.response.send_message("You need to be in a voice channel.", ephemeral=True)
        return

    await interaction.response.defer()

    state = get_state(interaction.guild_id)
    voice_channel = member.voice.channel

    # Connect or move to the user's voice channel
    if state.voice_client is None or not state.voice_client.is_connected():
        state.voice_client = await voice_channel.connect()
    elif state.voice_client.channel != voice_channel:
        await state.voice_client.move_to(voice_channel)

    # Resolve the query
    search = query
    spotify_multi = []

    if SPOTIFY_ALBUM_RE.match(query):
        spotify_multi = resolve_spotify_album(query)
        if not spotify_multi:
            await interaction.followup.send("❌ Could not resolve Spotify album. Check your Spotify API credentials.")
            return
    elif SPOTIFY_PLAYLIST_RE.match(query):
        spotify_multi = resolve_spotify_playlist(query)
        if not spotify_multi:
            await interaction.followup.send("❌ Could not resolve Spotify playlist. Check your Spotify API credentials.")
            return
    elif SPOTIFY_TRACK_RE.match(query):
        resolved = resolve_spotify_query(query)
        if resolved is None:
            await interaction.followup.send("❌ Could not resolve Spotify track. Check your Spotify API credentials.")
            return
        search = resolved
    elif not YOUTUBE_RE.match(query):
        search = f"ytsearch:{query}"

    # Handle Spotify albums/playlists (multiple tracks)
    if spotify_multi:
        queued_count = 0
        first_song = None
        was_playing = state.voice_client.is_playing() or state.voice_client.is_paused()

        for sq in spotify_multi:
            info = await extract_info(f"ytsearch:{sq}")
            if info is None:
                continue
            song = Song(title=info.get("title", "Unknown"), url=info["url"], requester=member)
            state.queue.append(song)
            queued_count += 1
            if first_song is None:
                first_song = song

        if queued_count == 0:
            await interaction.followup.send("❌ Could not find any tracks.")
            return

        if not was_playing:
            await play_next(interaction.guild_id)
            await interaction.followup.send(
                f"🎶 Now playing: **{first_song.title}**\n"
                f"➕ Queued **{queued_count}** tracks from Spotify"
            )
        else:
            await interaction.followup.send(f"➕ Queued **{queued_count}** tracks from Spotify")
        return

    info = await extract_info(search)
    if info is None:
        await interaction.followup.send("❌ No results found.")
        return

    song = Song(
        title=info.get("title", "Unknown"),
        url=info["url"],
        requester=member,
    )

    state.queue.append(song)

    if not state.voice_client.is_playing() and not state.voice_client.is_paused():
        await play_next(interaction.guild_id)
        await interaction.followup.send(f"🎶 Now playing: **{song.title}**")
    else:
        await interaction.followup.send(f"➕ Queued: **{song.title}** (position {len(state.queue)})")


@bot.tree.command(name="skip", description="Skip the current song")
async def skip(interaction: discord.Interaction):
    state = get_state(interaction.guild_id)
    if state.voice_client and state.voice_client.is_playing():
        state.voice_client.stop()  # triggers after_play → play_next
        await interaction.response.send_message("⏭️ Skipped.")
    else:
        await interaction.response.send_message("Nothing is playing.", ephemeral=True)


@bot.tree.command(name="stop", description="Stop playback and clear the queue")
async def stop(interaction: discord.Interaction):
    state = get_state(interaction.guild_id)
    state.queue.clear()
    state.current = None
    if state.voice_client:
        state.voice_client.stop()
        await state.voice_client.disconnect()
        state.voice_client = None
    await interaction.response.send_message("⏹️ Stopped and cleared the queue.")


@bot.tree.command(name="pause", description="Pause the current song")
async def pause(interaction: discord.Interaction):
    state = get_state(interaction.guild_id)
    if state.voice_client and state.voice_client.is_playing():
        state.voice_client.pause()
        await interaction.response.send_message("⏸️ Paused.")
    else:
        await interaction.response.send_message("Nothing is playing.", ephemeral=True)


@bot.tree.command(name="resume", description="Resume the paused song")
async def resume(interaction: discord.Interaction):
    state = get_state(interaction.guild_id)
    if state.voice_client and state.voice_client.is_paused():
        state.voice_client.resume()
        await interaction.response.send_message("▶️ Resumed.")
    else:
        await interaction.response.send_message("Nothing is paused.", ephemeral=True)


@bot.tree.command(name="queue", description="Show the current queue")
async def queue(interaction: discord.Interaction):
    state = get_state(interaction.guild_id)
    if not state.current and not state.queue:
        await interaction.response.send_message("The queue is empty.", ephemeral=True)
        return

    lines = []
    if state.current:
        lines.append(f"🎶 **Now playing:** {state.current.title} (requested by {state.current.requester.display_name})")

    for i, song in enumerate(state.queue, start=1):
        lines.append(f"`{i}.` {song.title} — requested by {song.requester.display_name}")

    if not state.queue and state.current:
        lines.append("\n*Queue is empty — add more songs with /play*")

    await interaction.response.send_message("\n".join(lines))


@bot.tree.command(name="radio", description="Start a radio — plays a shuffled playlist matching a genre or mood")
@app_commands.describe(genre="Genre, mood, or theme (e.g. 'techno', 'chill lofi', 'rocket league music'). Leave empty for random.")
async def radio(interaction: discord.Interaction, genre: str = None):
    if not interaction.guild:
        await interaction.response.send_message("This command only works in a server.", ephemeral=True)
        return

    member = interaction.guild.get_member(interaction.user.id)
    if not member or not member.voice or not member.voice.channel:
        await interaction.response.send_message("You need to be in a voice channel.", ephemeral=True)
        return

    await interaction.response.defer()

    state = get_state(interaction.guild_id)
    voice_channel = member.voice.channel

    if state.voice_client is None or not state.voice_client.is_connected():
        state.voice_client = await voice_channel.connect()
    elif state.voice_client.channel != voice_channel:
        await state.voice_client.move_to(voice_channel)

    # Build the search query
    random_genres = ["lofi hip hop", "synthwave", "classic rock", "jazz", "chill beats",
                     "techno", "house", "drum and bass", "indie", "ambient", "rap mix",
                     "80s hits", "90s hits", "video game music", "electronic"]
    if genre:
        search_query = f"{genre} mix"
    else:
        genre = random.choice(random_genres)
        search_query = f"{genre} mix"

    # Try to find a playlist
    tracks = await extract_playlist(search_query)

    if not tracks:
        # Fallback: search for individual videos as a pseudo-radio
        fallback_tracks = []
        for i in range(10):
            info = await extract_info(f"ytsearch:{genre} music #{random.randint(1, 100)}")
            if info:
                fallback_tracks.append(info)
        tracks = fallback_tracks

    if not tracks:
        await interaction.followup.send("❌ Couldn't find anything for that. Try a different genre.")
        return

    # Shuffle and queue the tracks
    random.shuffle(tracks)

    # Clear existing queue for radio mode
    state.queue.clear()
    if state.voice_client.is_playing() or state.voice_client.is_paused():
        state.voice_client.stop()
        await asyncio.sleep(0.5)

    queued_count = 0
    first_song = None

    for track in tracks:
        video_id = track.get("id") or track.get("url", "")
        title = track.get("title", "Unknown")

        if not video_id:
            continue

        # Always resolve through yt-dlp to get the actual audio stream URL
        resolved = await resolve_track_url(video_id)
        if not resolved:
            continue
        stream_url = resolved["url"]
        title = resolved.get("title", title)

        song = Song(title=title, url=stream_url, requester=member)
        state.queue.append(song)
        queued_count += 1
        if first_song is None:
            first_song = song

    if queued_count == 0:
        await interaction.followup.send("❌ Couldn't load any tracks. Try a different genre.")
        return

    await play_next(interaction.guild_id)
    await interaction.followup.send(
        f"📻 **Radio: {genre}** — loaded {queued_count} tracks (shuffled)\n"
        f"🎶 Now playing: **{first_song.title}**"
    )


bot.run(DISCORD_TOKEN)
