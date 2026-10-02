# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A radio jukebox for an old Android phone running Termux. MPD plays the radio streams, a single-file Flask app ([radio_web.py](radio_web.py)) is the web remote, and a Cloudflare quick tunnel exposes that remote publicly. Development happens on Windows, but everything runs only on the phone under Termux. There is no build step, test suite or linter.

## Runtime architecture

- **MPD** listens on `0.0.0.0:6600` and outputs audio through Android OpenSLES (`type "sles"`, software mixer, see [mpd.conf](mpd.conf)). Its paths use Termux conventions: playlists in `~/.config/mpd/playlists`, music in `~/storage/shared/Music`. Queue and state survive restarts through `state_file`.
- **radio_web.py** is stateless. Every route opens a fresh `MPDClient` connection to `localhost:6600`, runs one or two MPD commands, disconnects, then redirects to `/` (POST-redirect-GET). The whole UI is one inline Jinja template (`HTML`) rendered with `render_template_string`. New features follow the same pattern: a POST route plus a `<form>` in the template. There is no JavaScript.
- **Station names** come from `#EXTINF` lines in the `.m3u` files (all files in `PLAYLIST_DIR` plus `~/.config/mpd/nomes.m3u`, where names typed in "add stream" are appended), never from MPD tags. MPD exposes `#EXTINF` as the `name` tag, but once a stream plays MPD replaces that queue entry's tags with the stream's ICY tags for good. MPD's `save` writes bare URLs, so `/save_playlist` writes the `.m3u` itself to keep names. `PLAYLIST_DIR` in `radio_web.py` must match `playlist_directory` in `mpd.conf`.
- **Radio search** (`/search`, GET) queries the radio-browser.info API with stdlib `urllib`, so there is no extra dependency. Results are posted to `/add_stream` with a `next` field so the user lands back on the results. HLS (`.m3u8`) results are filtered out: the Windows MPD build cannot play them, and Termux support is unconfirmed. Network errors are caught inside the route, because the app-wide `OSError` handler reports them as "MPD indisponível".
- **start.sh** kills any old instances, runs `mpd`, loads the `radios` playlist only if the restored queue is empty (to avoid duplicates), starts the web app with `nohup` (logs to `~/web.log`), then runs `cloudflared tunnel --url http://localhost:8080` in the foreground. The trycloudflare URL changes on every start.
- **install.sh** is idempotent and is meant to be run as `curl | bash`. All logic sits inside `main()` so bash reads the whole script before running any of it. It symlinks `~/.config/mpd/mpd.conf` to the repo copy, so edits to `mpd.conf` apply after `git pull`. By contrast, `radios.m3u` is copied with `cp -n` and never overwritten, because the web UI's "save playlist" rewrites the installed copy. `REPO_URL` points to the public repo `github.com/vitor-gsousa/vee-radio`. It can be overridden with `VEE_RADIO_REPO`.

## Running / testing on the device

```bash
~/start.sh                              # start MPD + web (port 8080) + tunnel
~/stop.sh                               # stop everything, release the wake lock
cd ~/vee-radio && git pull && ~/start.sh   # update
mpc status / mpc playlist               # inspect MPD directly
```

To check web UI changes locally without the phone, you need a running MPD. On Windows the official build works (`https://www.musicpd.org/download/win32/<versão>/mpd.exe`). It needs a `libz.dll` next to it, and a copy of Git's `C:\Program Files\Git\ucrt64\bin\zlib1.dll` renamed to `libz.dll` works. Use `audio_output { type "null" }`. Real radio streams play fine through it, so ICY tag behaviour can be observed. The Python dependencies are `flask` and `python-mpd2`.

## Constraints

- Shell scripts use the Termux shebang `#!/data/data/com.termux/files/usr/bin/bash` and must keep LF line endings (enforced by [.gitattributes](.gitattributes)). Termux rejects CRLF.
- All user-facing text (UI, script output, README, comments) is in European Portuguese. Keep it that way.
- The web remote has no authentication, so anyone with the tunnel link can control it.
