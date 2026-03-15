# Discord Music Bot

A simple Discord music bot built with **discord.py**, **yt-dlp**, and **spotipy**.

## Features

- `/play <query>` — Play from a **YouTube URL**, **Spotify track URL**, or a **search query**
- `/radio [genre]` — Start a shuffled radio based on a genre, mood, or theme (e.g. `/radio techno`, `/radio chill lofi`, `/radio rocket league music`). Omit the genre for a random mix.
- `/skip` — Skip the current song
- `/stop` — Stop playback and clear the queue
- `/pause` / `/resume` — Pause and resume playback
- `/queue` — View the current queue

## Prerequisites

- Python 3.10+
- [ffmpeg](https://ffmpeg.org/download.html) installed and on your PATH
- A [Discord bot token](https://discord.com/developers/applications)
- *(Optional)* [Spotify API credentials](https://developer.spotify.com/dashboard) for Spotify URL support

## Setup

1. **Clone the repo**
   ```bash
   git clone https://github.com/<your-username>/discord-bot.git
   cd discord-bot
   ```

2. **Create a virtual environment & install dependencies**
   ```bash
   python3 -m venv venv
   source venv/bin/activate   # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Configure environment variables**
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and fill in your tokens:
   - `DISCORD_TOKEN` — your Discord bot token
   - `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` — *(optional)* for Spotify URL support

4. **Run the bot**
   ```bash
   python bot.py
   ```

## Discord Bot Permissions

When adding the bot to your server, make sure it has:
- `applications.commands` (slash commands)
- `Connect` and `Speak` (voice)

## License

[MIT](LICENSE)
