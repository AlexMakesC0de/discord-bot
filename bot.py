import os
import re
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
    if SPOTIFY_TRACK_RE.match(query):
        resolved = resolve_spotify_query(query)
        if resolved is None:
            await interaction.followup.send("❌ Could not resolve Spotify track. Check your Spotify API credentials.")
            return
        search = resolved
    elif not YOUTUBE_RE.match(query):
        # Plain search — prefix with ytsearch: for yt-dlp
        search = f"ytsearch:{query}"

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


bot.run(DISCORD_TOKEN)
