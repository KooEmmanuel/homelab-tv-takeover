# The prompts

Paste these into Claude Code one at a time, in order. Prompts 1–5 are all you need with just a laptop (Path 1). Prompt 6 is for letting an AI control the TV (Path 2), and 7 is for a homelab (Path 3). Replace the parts in `<angle brackets>` with your own details. Read what Claude plans to do before you say yes, especially anything that changes the TV.

---

## 1. Find your TV

From a homelab (what I did, Path 3):

```
I have a Raspberry Pi homelab on my home network (ssh <user>@<pi-hostname>, network <your network, e.g. 192.168.1.0/24>).
I want my homelab to control my smart TV.

First, find every smart TV on my network from the Pi. Write a small Python script (standard library only,
nothing to install) that I can run on the Pi. It should:
- send SSDP/DIAL discovery and mDNS queries (Roku, Fire TV/Amazon, Google Cast, AirPlay, Samsung, LG)
- check common TV control ports on every address in the network
- for anything that looks like a TV, read its name, brand and model (read-only requests only, don't press
  anything on the TVs)

Run it on the Pi over ssh, then give me a table of the TVs you found: name, brand, model, IP, and how each
one can be controlled. Then tell me which one is the best to take over and why.
```

From a laptop (no homelab needed, Path 1):

```
Find the smart TVs on my home network from this computer. Use only read-only discovery (SSDP, mDNS and
common TV ports) and don't press anything on the TVs. Give me a table with each TV's name, brand, model,
IP address and how it can be controlled.
```

## 2. Research a new home screen for your exact TV

Turn on Developer Options and ADB debugging on the TV first (see the README).

```
My TV is a <brand and model> running <Fire OS / Android TV version>, and ADB debugging is on.
I want to replace its home screen with a clean one, without rooting it. Find well-maintained open-source
projects that do this, tell me which ones were tested on my model and version, and whether every change
can be undone. Include sources. Don't run anything yet.
```

The one I used, if you also have a Fire TV:

```
I discovered a repo that changes the home screen of Fire TVs and lets you control them:
https://github.com/humblemuzzu/dumbtv
Check whether it supports my exact TV and Fire OS version, explain how it works on my version,
and list what it changes and how to undo it. Don't run anything yet.
```

## 3. Connect and check (read-only)

```
Install adb on this computer if it isn't there. Connect to my TV over ADB at <TV IP>:5555. I'll accept the prompt on the TV (and tick "Always allow").
Then run a read-only check and tell me the model, the software build, and whether it matches what the
project was tested on. Don't change anything on the TV yet.
```

## 4. Install only the new home screen

```
Install only the new home screen, not the full cleanup. Read the install script first, show me every
command it will run and the one command that undoes it, and wait for my OK before running anything.
```

## 5. Control it from the command line

```
Make me a small command I can run to control my TV over ADB: home, back, arrows, ok, volume up/down,
open an app by name, search Netflix and YouTube, and take a screenshot. It should reconnect on its own
if the connection drops. Test it with harmless commands only (status, a screenshot, Home, volume up
then down).
```

## 6. Let your AI assistant control it

```
Wrap that command in a small MCP server (standard-library Python, stdio) with tools for: TV status,
opening an app, pressing a remote button, changing the volume, and searching Netflix or YouTube.
Mark the status tool as read-only. Then show me how to add it to Claude Code.
```

## 7. Optional: your own apps on the TV

```
I run Jellyfin on my homelab. Install the official Jellyfin Android TV app on the TV from the project's
GitHub releases (check the checksum first), and make sure the TV can reach my Jellyfin server on my
home network. Tell me what you plan to change before you change it.
```
