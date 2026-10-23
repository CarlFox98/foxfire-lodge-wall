# Foxfire Lodge portrait wall

The pictures on the portrait wall in **Foxfire Lodge** (a VRChat world by NeoTheFox). It's **opt-in only**:

- **Discord:** react with 🦊 on the pinned sign-up message in the server's `#🖼️lodge-wall🖼️` channel. Remove your reaction to leave.
- **Twitch:** redeem the channel-points reward **"Put me on the Lodge wall"**. Redeem **"Take me off the Lodge wall"** to leave.

Your profile picture and display name appear on the wall (and on the public images on this site) within about 15 minutes, and leaving takes effect just as fast. Want to be removed for good, or saw something that shouldn't be there? Message NeoTheFox.

## How it works

`build.py` runs every 15 minutes in GitHub Actions (`.github/workflows/update-wall.yml`). It reads the Discord reactions and Twitch redemptions, downloads profile pictures, builds two 2048 px sheets and a small info file, and publishes them to GitHub Pages:

| File | Contents |
| --- | --- |
| `hallway.png` | 58 portraits (10 x 6 cells of 200 x 240 px from the top-left) and their names (8 x 12 strips of 256 x 48 px from y = 1440) |
| `mosaic.png` | up to 240 faces, newest first (16 x 16 tiles of 128 px) |
| `wall.json` | `{"count": …, "updated": "…"}` for the kiosk |

The world downloads these at runtime (VRChat trusts `*.github.io`), so the wall updates without re-uploading the world. The layouts must match `M4HallwayBuilder.cs` in the world project.

Hallway order: people who joined in the last 7 days first, then everyone else in a different random order each hour. The mosaic shows everyone (newest first, up to 240).

`state/roster.json` is the list of people on the wall (platform, user id, display name, picture address, join date). `state/twitch.enc` is the Twitch token, **encrypted**; the key lives only in the repository's secrets. `blocklist.txt` keeps anyone off the wall.

## Settings (repository → Settings → Secrets and variables → Actions)

| Name | Kind | What |
| --- | --- | --- |
| `DISCORD_BOT_TOKEN` | secret | token of the "Foxfire Lodge Wall" Discord bot (needs View Channel + Read Message History in `#🖼️lodge-wall🖼️`) |
| `DISCORD_CHANNEL_ID` | variable | id of `#lodge-wall` |
| `DISCORD_MESSAGE_ID` | variable | id of the pinned sign-up message |
| `TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET` | secrets | from the Twitch developer app (redirect URL `http://localhost:3000/`) |
| `TWITCH_REFRESH_TOKEN`, `TWITCH_STATE_KEY` | secrets | from `setup/setup-twitch.ps1` (run once on your PC) |

Either platform can be left unconfigured; it's simply skipped.
