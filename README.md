<div align="center">

# Discord Music Bot

**A small Discord music bot that plays YouTube audio, Spotify links and genre radio in voice channels.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![discord.py](https://img.shields.io/badge/discord.py-2.3%2B-5865F2?logo=discord&logoColor=white)

</div>

A single-file bot built with [discord.py](https://github.com/Rapptz/discord.py), [yt-dlp](https://github.com/yt-dlp/yt-dlp) and [spotipy](https://github.com/spotipy-dev/spotipy). It joins your voice channel, streams audio from YouTube through FFmpeg, and keeps a separate queue for every server it is in.

## Features

All commands are slash commands.

| Command | What it does |
| --- | --- |
| `/play <query>` | Plays a YouTube or YouTube Music URL, a Spotify track, album or playlist URL, or the top YouTube result for search terms. Adds to the queue if something is already playing. |
| `/radio [genre]` | Finds a YouTube playlist for the genre, mood or theme (for example `techno` or `chill lofi`), shuffles up to 25 tracks and replaces the queue. Leave the genre empty for a random one. |
| `/skip` | Skips the current song. |
| `/pause` / `/resume` | Pauses and resumes playback. |
| `/stop` | Stops playback, clears the queue and leaves the channel. |
| `/queue` | Shows the current song and the queue, with who requested each one. |

The bot leaves the voice channel by itself when the queue runs out.

## Getting started

### Prerequisites

- Python 3.10 or newer
- [FFmpeg](https://ffmpeg.org/download.html) on your PATH
- A Discord application with a bot token, from the [Developer Portal](https://discord.com/developers/applications). Turn on **Message Content Intent** under Bot > Privileged Gateway Intents, because the bot requests it at startup.
- Optional: Spotify API credentials from the [Spotify dashboard](https://developer.spotify.com/dashboard), only needed for Spotify links

### Install and run

```bash
git clone https://github.com/AlexMakesC0de/discord-bot.git
cd discord-bot
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env           # then fill in your values
python bot.py
```

The console prints `Logged in as ...` once the bot is connected. Slash commands are synced on startup; Discord can take a few minutes to show them the first time.

### Invite the bot

In the Developer Portal, generate an OAuth2 URL with the `bot` and `applications.commands` scopes and the **Connect** and **Speak** permissions, then open it to add the bot to your server.

## Configuration

Set these in `.env` (see [`.env.example`](.env.example)):

| Variable | Required | Purpose |
| --- | --- | --- |
| `DISCORD_TOKEN` | Yes | Your bot token |
| `SPOTIFY_CLIENT_ID` | No | Spotify app client ID |
| `SPOTIFY_CLIENT_SECRET` | No | Spotify app client secret |

Without the Spotify values the bot still works; Spotify links then reply with an error.

## How it works

- YouTube lookups run in a thread pool so yt-dlp never blocks the bot.
- Spotify links are not streamed from Spotify. The bot reads the artist and title of each track through the Spotify API and searches YouTube for it. Album and playlist tracks are queued without a lookup and resolved only when their turn comes, so a long playlist is queued instantly.
- Audio is streamed, not downloaded, using FFmpeg reconnect options so short network drops do not end the song.

## License

[MIT](LICENSE)
