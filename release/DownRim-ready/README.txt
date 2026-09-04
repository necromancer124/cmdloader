cmdloader / DownRim Universal - Ready Build

Double-click DownRim.exe to open the GUI.

IMPORTANT for Steam login + Steam Guard / mobile authenticator:
- If you use Steam login = user, cmdloader now uses ONE SteamCMD session per AppID.
- This avoids asking your phone to approve a new login for every single item.
- If Steam Mobile asks you to confirm login, approve it once and leave the app running.
- If it still times out, click "Fix Steam Guard / 2FA login", finish login in the SteamCMD window, type quit, then retry.

Rome II:
- AppID 214950 is correct for Total War: ROME II.
- Your cleaned lists are included:
  - rome2_workshop_cleaned.txt
  - rome2_workshop_cleaned_ids_only.txt

Progress:
- Anonymous downloads still run one item at a time for clear live progress.
- Logged-in downloads run in one SteamCMD session to avoid repeated 2FA prompts.
- The progress/status updates as each item succeeds or fails.

Security:
- Your Steam password is never saved by cmdloader.
- Logs/reports mask the Steam password before writing commands.

CLI: DownRim.exe --help
