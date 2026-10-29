# The full guide: controlling a Fire TV from a Raspberry Pi homelab

This is the complete record of how a Raspberry Pi 5 homelab took control of a smart Fire TV. It covers the network scan, the new home screen, the ADB control script, the keepalive timer, the Buddy voice/desktop assistant integration, extra apps (Jellyfin and SmartTube), and every problem hit along the way.

> **Note on addresses:** every IP address, username, hostname and domain in this document is a placeholder. Replace them with your own.
>
> | Placeholder | Meaning |
> |---|---|
> | `192.168.1.0/24` | home network |
> | `192.168.1.10` | the Raspberry Pi (`homelab.local`) |
> | `192.168.1.50` | the Fire TV |
> | `pi` | the login user on the Pi |
> | `jellyfin.lab.example.com` | the Jellyfin name served over the VPN |
> | `100.x.y.z` | the Pi's Tailscale (VPN) address |

---

## Contents

1. [The hardware](#1-the-hardware)
2. [Finding the TVs on the network](#2-finding-the-tvs-on-the-network)
3. [Choosing the Fire TV and the dumbtv project](#3-choosing-the-fire-tv-and-the-dumbtv-project)
4. [Installing ADB on the Pi without sudo](#4-installing-adb-on-the-pi-without-sudo)
5. [Preflight check](#5-preflight-check)
6. [Installing the dumbtv home launcher](#6-installing-the-dumbtv-home-launcher)
7. [The `firetv` command](#7-the-firetv-command)
8. [Keeping the connection alive](#8-keeping-the-connection-alive)
9. [Controlling the TV through Buddy (MCP)](#9-controlling-the-tv-through-buddy-mcp)
10. [Searching Netflix and YouTube](#10-searching-netflix-and-youtube)
11. [Adding apps: Jellyfin and SmartTube](#11-adding-apps-jellyfin-and-smarttube)
12. [Making Jellyfin reachable from the TV](#12-making-jellyfin-reachable-from-the-tv)
13. [Troubleshooting log](#13-troubleshooting-log)
14. [What works, what is untested, known limits](#14-what-works-what-is-untested-known-limits)
15. [Undo and file map](#15-undo-and-file-map)

---

## 1. The hardware

| Device | Details |
|---|---|
| Raspberry Pi 5 | Hostname `homelab`, boots from NVMe, runs Coolify (Docker), AdGuard and Tailscale |
| Fire TV | Insignia NS-39DF310NA21, model code **AFTEAMR311**, Fire OS 6 (build **NS6711**). That is Android 7.1.2, SDK 25, 32-bit `armeabi-v7a` |
| Other TVs found | Two Rokus (Roku ECP in "limited" mode, so they can't be controlled) |
| Control machine | A Mac running Buddy (my own desktop assistant), which reaches the Pi over SSH. Any MCP client works the same way, including Claude Code |

---

## 2. Finding the TVs on the network

I wrote a small scanner, `tv-scan.py`. It uses only the Python standard library and makes **read-only** requests, so it never presses a button on any TV. It runs on the Pi:

```bash
python3 ~/tv-scan.py               # scans the default /24 set in SUBNET
python3 ~/tv-scan.py 192.168.1     # another /24
python3 ~/tv-scan.py --json        # machine-readable output
```

What it does:

1. **SSDP / DIAL M-SEARCH.** Looks for Roku (`roku:ecp`), DIAL (Fire TV, Cast), Samsung, LG webOS, Sony and UPnP media renderers.
2. **mDNS PTR queries.** Looks for Google Cast, AirPlay, Amazon devices, Android TV and Spotify Connect.
3. **Port check.** Tries a TCP connect on 18 common TV control ports for every address, using a thread pool.
4. **Info probes.** Sends a read-only GET to each platform's info endpoint:
   - Roku ECP on `:8060/query/device-info`
   - Google Cast `eureka_info`
   - Samsung `:8001/api/v2/`
   - AirPlay `/info`
   - Vizio, Philips and the UPnP device description XML
5. **Silent neighbours.** Reads `/proc/net/arp` to list devices that answered nothing.

**Fixes made to the scanner:**
- The first run flagged the Pi itself as an LG webOS TV. Port 3001 was a Docker service on the Pi. The scanner now skips its own addresses (`my_ips()`).
- Roku models showed raw codes. They now show as `model-name (model-number)`.

The scanner found the Fire TV with ADB on `:5555` and DIAL open, plus two Rokus. The recommendation was the Fire TV: it is a full Android device, so ADB gives complete control.

---

## 3. Choosing the Fire TV and the dumbtv project

The goal was to replace the Fire TV's ad-heavy Amazon home screen and control the TV from the homelab. The project that does this for this exact TV family is **[humblemuzzu/dumbtv](https://github.com/humblemuzzu/dumbtv)**. It provides:

- `preflight.sh`: a read-only check of the TV.
- `launcher/install.sh`: installs a clean home screen ("dumbtv home").
- A larger debloat/cleanup script. **This was deliberately not run.**

**How dumbtv home works on Fire OS 6.7.** Amazon blocks custom launchers, and this Fire OS version refuses accessibility services. dumbtv gets around both with a small "Keeper" service. The Keeper is granted the `GET_USAGE_STATS` app-op, watches which app is in front, and when the Amazon launcher appears, it calls `moveTaskToFront` to bring dumbtv home back. The result is that pressing Home on the remote lands on dumbtv home.

---

## 4. Installing ADB on the Pi without sudo

The Pi user has no passwordless sudo, so `apt install adb` was not an option. Instead the Debian packages were downloaded and unpacked into the home folder:

```bash
mkdir -p ~/adb-debs && cd ~/adb-debs
apt-get download adb        # then download each android-lib* package it depends on
for f in *.deb; do dpkg -x "$f" ~/.local/adb-root; done
```

A wrapper at `~/.local/bin/adb` points the loader at the unpacked libraries:

```sh
#!/bin/sh
export LD_LIBRARY_PATH=/home/pi/.local/adb-root/usr/lib/aarch64-linux-gnu/android:$LD_LIBRARY_PATH
exec /home/pi/.local/adb-root/usr/lib/android-sdk/platform-tools/adb "$@"
```

**On the TV:** Settings → My Fire TV → Developer options → **ADB debugging: ON**. On the first connection the TV shows "Allow USB debugging?". You **must tick "Always allow from this computer"** before pressing Allow, or the TV forgets the Pi's key and asks again every time.

```bash
adb connect 192.168.1.50:5555
adb devices      # should show: 192.168.1.50:5555   device
```

---

## 5. Preflight check

The repo was cloned to `~/dumbtv` on the Pi, and only the preflight was run. Nothing was changed on the TV at this stage.

```bash
git clone https://github.com/humblemuzzu/dumbtv ~/dumbtv
cd ~/dumbtv && ./preflight.sh
```

Preflight confirmed the model (AFTEAMR311), the Fire OS build, that ADB was authorized, and that the TV was a supported target. Before any change, the TV's settings were saved as snapshots:

```
~/dumbtv/report/settings-before-global.txt
~/dumbtv/report/settings-before-secure.txt
```

---

## 6. Installing the dumbtv home launcher

Only the launcher was installed, not the full cleanup. Before running it, I listed every step it would take and got approval. The steps were: install the dumbtv home APK, grant it the usage-stats app-op, start the Keeper service, and set dumbtv home as the default home activity.

```bash
cd ~/dumbtv && ./launcher/install.sh
```

**Result:** pressing Home on the remote opens dumbtv home (v1.6). Undo is one command (see [section 15](#15-undo-and-file-map)).

---

## 7. The `firetv` command

`~/.local/bin/firetv` is a Bash wrapper around ADB. It connects to the TV by itself when needed, so every command just works. The TV address lives in `~/.config/firetv.env`:

```bash
FIRETV_ADDR=192.168.1.50:5555
```

### Commands

| Command | What it does |
|---|---|
| `firetv status` | Model and build, screen state (Awake/Asleep), the app in front, and volume (e.g. `46/100 on speaker`) |
| `firetv shot [file.png]` | Screenshot (default `~/firetv-shots/<time>.png`). Netflix and Prime come out black because of DRM |
| `firetv apps` | Launchable apps and the short names below |
| `firetv open <app>` | `netflix`, `youtube`, `prime`, `twitch`, `music`, `shopping`, `jellyfin`, `smarttube`, `home`, or any package name |
| `firetv home\|back\|up\|down\|left\|right\|ok\|menu` | Remote buttons. The arrows and back take a count: `firetv down 3` |
| `firetv play\|pause\|playpause\|next\|prev` | Playback buttons |
| `firetv volup\|voldown [n]`, `firetv mute` | Volume |
| `firetv sleep`, `firetv wake` | Screen off and on. Wake only works while ADB still answers |
| `firetv text "words"` | Types into the focused text box |
| `firetv search <netflix\|youtube> "words"` | Opens that app's search results |
| `firetv hdmi <1\|2\|3\|av>` | Walks dumbtv home's Inputs row to that input (untested) |
| `firetv connect` | (Re)connects ADB. The timer runs this every minute |
| `firetv raw <adb shell args>` | Anything else |

### App short names

| Short name | Package |
|---|---|
| netflix | `com.netflix.ninja` |
| youtube | `com.amazon.firetv.youtube` |
| prime | `com.amazon.firebat` |
| twitch | `tv.twitch.android.viewer` |
| music | `com.amazon.bueller.music` |
| shopping | `com.amazon.shoptv.firetv.client` |
| jellyfin | `org.jellyfin.androidtv` |
| smarttube | `org.smarttube.stable` |
| home | `tv.dumbtv.home` |

### How the connection logic works

`state()` reads `adb devices` and returns one of `device`, `unauthorized`, `offline`, or nothing. `connect()` then:

- returns at once if the state is `device`;
- **does not reconnect** if the state is `unauthorized`. Every reconnect makes the TV pop up a new "Allow" dialog, so it leaves the one already on screen alone and tells you to tick Always allow;
- otherwise disconnects and reconnects with a 10 s timeout, then checks three times;
- gives a clear message for each failure:
  - `unauthorized`: "the TV has not allowed this Pi yet: tick Always allow, then press Allow"
  - `offline`: "the TV's ADB is stuck: turn ADB debugging off and on in Developer options, or unplug the TV for 10 seconds"
  - no answer: "TV is not answering (asleep, or showing an HDMI input?)"

Other details:
- `open` uses `monkey` with the `LEANBACK_LAUNCHER` category, falling back to `LAUNCHER`.
- `text` escapes shell characters and turns spaces into `%s`, which is what `input text` expects.
- `status` parses volume from the `- STREAM_MUSIC:` block of `dumpsys audio` with awk.
- Fire OS 6 has no shell command for switching inputs, so `hdmi` presses Home, Down, Left ×6, Right ×n, OK.

The full script is [`scripts/firetv`](../scripts/firetv).

---

## 8. Keeping the connection alive

ADB over Wi-Fi drops when the TV sleeps. A systemd **user** timer reconnects every minute, and lingering keeps it running without anyone logged in.

`~/.config/systemd/user/firetv-keepalive.service`
```ini
[Unit]
Description=Keep ADB connected to the Fire TV

[Service]
Type=oneshot
ExecStart=%h/.local/bin/firetv connect
```

`~/.config/systemd/user/firetv-keepalive.timer`
```ini
[Unit]
Description=Reconnect ADB to the Fire TV every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
AccuracySec=10s

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now firetv-keepalive.timer
loginctl enable-linger pi          # linger was already on
systemctl --user list-timers firetv-keepalive.timer
```

**Tested:** after a forced `adb disconnect`, the timer brought the link back in about 54 seconds.

---

## 9. Controlling the TV through Buddy (MCP)

Home Assistant's Android Debug Bridge integration was tried first and then dropped. HA runs in a Coolify bridge network, so it can't reach the Pi's ADB server easily, and the decision was to use Buddy as the single control path.

### The MCP server

`~/.local/bin/firetv-mcp` is a small MCP server written in standard-library Python. It speaks newline-delimited JSON-RPC over stdio and handles `initialize`, `ping`, `tools/list` and `tools/call`. Each tool runs the `firetv` command with a 45-second timeout and returns its output as text.

| Tool | Inputs | Notes |
|---|---|---|
| `tv_status` | none | Read-only, so Buddy runs it without asking |
| `tv_open_app` | `app` (enum of the short names) | |
| `tv_press` | `button`, `times` 1–10 | |
| `tv_volume` | `action` up/down/mute, `steps` 1–10 | Replies with the new volume |
| `tv_search` | `app` netflix/youtube, `query` ≤ 100 chars | |
| `tv_type` | `text` ≤ 100 chars | |
| `tv_input` | `input` 1/2/3/av | Untested |
| `tv_screen` | `action` sleep/wake | |

### Registering it with an assistant

**Claude Code:**

```bash
claude mcp add firetv -- ssh pi@homelab.local '~/.local/bin/firetv-mcp'
```

**Buddy** (my own assistant; shown here as a second example)

On the Mac, `~/.config/buddy/apps.json` got a new `firetv` entry (the old file was backed up as `apps.json.bak-before-firetv`):

```json
"firetv": {
  "about": "The bedroom Fire TV: open apps, press remote buttons, change volume, search Netflix and YouTube.",
  "command": "/usr/bin/ssh",
  "args": [
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
    "-o", "ServerAliveInterval=30",
    "-o", "HostKeyAlias=homelab.local",
    "pi@homelab",
    "~/.local/bin/firetv-mcp"
  ],
  "read_only_tools": ["tv_status"]
}
```

Buddy asks for confirmation before any tool that isn't in `read_only_tools`.

```bash
buddy apps reload
buddy apps list
buddy apps call firetv tv_status '{}'
```

Example `tv_status` output:

```
tv      192.168.1.50:5555 (AFTEAMR311, build NS6711)
screen  Awake
front   tv.dumbtv.home/tv.dumbtv.home.MainActivity
volume  46/100 on speaker
```

---

## 10. Searching Netflix and YouTube

Both apps accept deep links, so search goes straight to the results page.

**Netflix**
```bash
am start -a android.intent.action.VIEW -d 'nflx://www.netflix.com/search?q=inception' com.netflix.ninja
```
Netflix may show its profile picker first. Screenshots of Netflix are black because of DRM, so results were confirmed by looking at the TV.

**YouTube** (Fire TV's YouTube is the Cobalt app)
```bash
am start -a android.intent.action.VIEW \
  -d 'https://www.youtube.com/results?search_query=home%20server%20rack%20tour' \
  -S -n com.amazon.firetv.youtube/dev.cobalt.app.MainActivity
```
Why it looks like this:
- A YouTube that is already running only comes to the front and **drops the new link**. `-S` force-stops it so the link becomes the launch intent.
- Once stopped, a bare package name no longer resolves ("Intent does not match any activities"), so the activity has to be named with `-n`.
- YouTube shows "Who's watching?" first. The search survives the account pick.

From Buddy: *"search Netflix for Inception"* → `tv_search {"app":"netflix","query":"Inception"}`.

---

## 11. Adding apps: Jellyfin and SmartTube

Neither app is in the Amazon store in a usable form, so both were sideloaded. The plan was shown and approved before installing.

| App | Version | Build | Why |
|---|---|---|---|
| Jellyfin for Android TV | 0.19.10 | universal | Plays the homelab's own movies and shows |
| SmartTube | 32.68 | `armeabi-v7a` (the TV is 32-bit) | YouTube without ads |

Steps:
1. Downloaded the APKs from the projects' official GitHub releases to `~/firetv-apks/` on the Pi.
2. Checked each file against its published SHA-256 checksum.
3. Installed with `adb install`.
4. Added both to the `firetv` short names and the MCP `tv_open_app` enum.

```bash
adb -s 192.168.1.50:5555 install ~/firetv-apks/jellyfin-androidtv-v0.19.10-release.apk
adb -s 192.168.1.50:5555 install ~/firetv-apks/SmartTube_stable_32.68_armeabi-v7a.apk
```

Signing in to Jellyfin and SmartTube is left to the owner.

---

## 12. Making Jellyfin reachable from the TV

### The problem

- Jellyfin runs in Coolify on a Docker **bridge** network, exposed only through the reverse proxy at `https://jellyfin.lab.example.com`.
- That name resolves to the Pi's **Tailscale** address (`100.x.y.z`), which only devices on the VPN can reach.
- The TV is not on Tailscale, and it uses the ISP's and Google's DNS servers rather than AdGuard, so it can't use the name at all.

### The fix

Publish Jellyfin's port 8096 on the Pi's **LAN address only**, so the TV can reach it while it stays off the public interface and off the VPN-only path. In Coolify → Jellyfin service → Compose, under the image line:

```yaml
    ports:
      - '192.168.1.10:8096:8096'
```

Coolify editor trick: the visual editor did not save the change. What worked: close the popups, tick **"Use plain-text editor"**, type the edit, click **Save Changes**, then **Restart** → **Confirm**.

### Verification

- From the Mac: `curl http://192.168.1.10:8096/System/Info/Public` returned the server info.
- The TV has no curl, wget or nc, so it couldn't be tested from the TV's shell.
- In the Jellyfin app on the TV, the server address was typed over ADB:
  ```bash
  firetv text "http://192.168.1.10:8096"
  firetv ok
  ```
  The app found the server (Jellyfin 10.11.8).

Note: Jellyfin still advertises its https name through `JELLYFIN_PublishedServerUrl`, which the TV can't reach. Always type the LAN address on the TV.

---

## 13. Troubleshooting log

| Problem | Cause | Fix |
|---|---|---|
| Scanner reported the Pi as an LG webOS TV | A Docker service on the Pi uses port 3001 | Scanner skips its own IPs |
| Roku models showed as codes | Wrong field read | Use `model-name (model-number)` |
| `echo ====` failed over SSH | zsh treats `=` specially | Quote it |
| `apt install adb` impossible | No passwordless sudo | Unpack `.deb` files into `~/.local/adb-root` with a wrapper |
| ADB stuck `unauthorized` after reconnects | "Always allow" was not ticked, and every reconnect popped a new dialog | Tick Always allow; `connect()` no longer reconnects while unauthorized |
| Volume blank in `status` | `dumpsys audio` format | awk parser reading the `STREAM_MUSIC` block |
| Netflix screenshot black | DRM | Confirm on the TV |
| YouTube "Activity not started… brought to front" | Running YouTube drops new links | `-S -n com.amazon.firetv.youtube/dev.cobalt.app.MainActivity`; errors are caught with `2>&1` |
| Coolify compose edit didn't save | Visual editor | Plain-text editor → Save Changes → Restart |
| TV couldn't reach Jellyfin | Name resolves to the VPN IP; TV uses outside DNS | Publish 8096 on the LAN IP |
| Buddy couldn't reach the TV, ADB `offline` | adbd on the TV froze: the port answers but it resets the handshake | Turn ADB debugging off and on, or unplug the TV for 10 s. `firetv` now says exactly this |

---

## 14. What works, what is untested, known limits

**Working and verified**
- Network discovery.
- dumbtv home as the Home button target.
- `firetv` CLI and the keepalive timer (reconnect in about 54 s).
- Buddy control (`buddy chat` and `buddy apps call`).
- Netflix and YouTube search.
- Jellyfin and SmartTube installed; Jellyfin connects to the homelab server.
- Recovery from a frozen ADB.

**Untested**
- `tv_input` / `firetv hdmi`.
- Behaviour after the Pi reboots.
- Opening SmartTube from Buddy.
- `sleep` and `wake`.

**Known limits**
- The TV's ADB can freeze. Only a restart of ADB on the TV fixes it; the Pi can't.
- A TV that is fully off can't be woken over the network.
- Volume steps are uneven.
- ADB usually drops while an HDMI input is showing; Home on the remote brings it back.
- DRM apps can't be screenshotted.

---

## 15. Undo and file map

**Remove dumbtv home** (puts the Amazon launcher back):
```bash
~/dumbtv/launcher/install.sh --undo
```

**Stop the keepalive:**
```bash
systemctl --user disable --now firetv-keepalive.timer
```

**Remove the Jellyfin port:** delete the `ports:` block from the Jellyfin compose in Coolify and restart.

**Remove from Buddy:** restore `~/.config/buddy/apps.json.bak-before-firetv`, then `buddy apps reload`.

### Files on the Pi

| Path | Purpose |
|---|---|
| `~/tv-scan.py` | Network TV scanner |
| `~/.local/adb-root/` | Unpacked ADB |
| `~/.local/bin/adb` | ADB wrapper |
| `~/.local/bin/firetv` | Control script |
| `~/.local/bin/firetv-mcp` | MCP server for Buddy |
| `~/.config/firetv.env` | TV address |
| `~/.config/systemd/user/firetv-keepalive.{service,timer}` | Reconnect timer |
| `~/dumbtv/` | dumbtv repo, plus settings snapshots in `report/` |
| `~/firetv-apks/` | Jellyfin and SmartTube APKs |
| `~/firetv-shots/` | Screenshots |

### Files on the Mac

| Path | Purpose |
|---|---|
| `scripts/tv-scan.py` | Scanner source |
| `scripts/` and `systemd/` | Sources for `firetv`, `firetv-mcp`, the timer units and the env file |
| `~/.config/buddy/apps.json` | Buddy app registration |
