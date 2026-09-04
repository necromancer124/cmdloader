# DownRim Universal

DownRim is now a **universal Steam Workshop downloader** built around Valve SteamCMD. It can download public Workshop items for RimWorld **and other Steam games/apps** by auto-detecting each Workshop item's AppID through Steam's public API.

## Ready-to-use EXE

```text
release/DownRim-ready/DownRim.exe
release/DownRim-ready.zip
```

Double-click `DownRim.exe` to open the easier GUI.

## New convenience features

- Live GUI progress bar and status text.
- Select the folder where Workshop content should download.
- Downloaded files go under your selected folder:

```text
<download folder>/steamapps/workshop/content/<appid>/<workshop_id>/
```

- Manage/Delete tab to refresh downloaded Workshop folders, delete selected downloads, or delete all downloads in the selected folder.
- Clearer labels for AppID, login, and username.
- Built-in explanation button for "What is username?".
- AppID defaults to `auto`, so mixed Workshop item lists can be grouped by their real Steam app.
- Collections and best-effort nested collections are supported.
- SteamCMD can be installed from inside the app if missing.

## GUI usage

1. Run `DownRim.exe`.
2. Choose the **Download folder**.
3. Paste Workshop item links, item IDs, collection links, or collection IDs, one per line.
4. Leave **AppID** as `auto` unless an item cannot be detected. RimWorld is `294100`.
5. Leave **Steam login** as `anonymous` for public Workshop items.
6. Click **Dry Run / Check IDs** to verify IDs/AppIDs without downloading.
7. Click **Start Download**.
8. Use **Manage/Delete Downloaded** to remove downloaded Workshop folders later.

## What does Username mean?

Usually nothing — leave it blank.

Only change Steam login from `anonymous` to `user` if SteamCMD says anonymous access is not allowed. In that case, **Username** means your Steam account login name: the name you type when signing into Steam. It may be different from your public Steam display name. DownRim asks for your password only when starting and does not save it. Steam Guard may require running `steamcmd.exe` manually once.

## CLI examples

```bash
# Resolve item AppIDs without downloading
DownRim.exe --links 818773962 --list

# Download one public item with automatic AppID detection
DownRim.exe --links 818773962

# Choose download folder
DownRim.exe --download-dir "D:\\WorkshopDownloads" --links 818773962

# Download a collection and nested collections up to depth 2
DownRim.exe --collection 1884025115 --collection-depth 2

# Use a text file containing one Workshop link or ID per line
DownRim.exe --in mods.txt

# Force RimWorld AppID if Steam API auto-detection is unavailable
DownRim.exe --appid 294100 --links 818773962
```

## Important limitations

DownRim does **not** bypass Steam, game, paid/DLC, privacy, creator, region, or account restrictions. Some Workshop items cannot be downloaded anonymously. For those, try Steam user login, but SteamCMD/Steam may still block restricted content.

## Source files

- `downrim_universal.py` — current universal app used for the EXE.
- `rimworld_mod_downloader_v2.py` — older RimWorld-focused app kept for reference.
- `rimworld_mod_downloader.py` — older CLI-only RimWorld-focused script kept for reference.
