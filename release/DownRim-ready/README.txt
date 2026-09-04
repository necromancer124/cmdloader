DownRim Universal - Ready Build

Double-click DownRim.exe to open the easy GUI.

Your cleaned Rome II list is included:
- rome2_workshop_cleaned.txt
- rome2_workshop_cleaned_ids_only.txt

Steam Guard / two-factor fix:
1. Put your Steam login name in Username.
2. Click "Fix Steam Guard / 2FA login".
3. A SteamCMD window opens.
4. Login there, enter Steam Guard / 2FA code if asked.
5. When SteamCMD says logged in, type: quit
6. Retry DownRim with Steam login = user.

Progress bar:
- Real downloads run one Workshop item at a time.
- The bar moves after each item succeeds or fails: 1/19, 2/19, etc.

If items fail with: ERROR! Download item <id> failed (Failure)
- AppID 214950 is correct for Total War: ROME II.
- The item may be blocked for anonymous SteamCMD.
- Use Steam login = user after completing Steam Guard setup.

Where files download:
<your chosen folder>\steamapps\workshop\content\<appid>\<workshop_id>

Limits:
DownRim cannot bypass Steam restrictions. Private, paid, hidden, removed, account-required, or region-locked items can still fail.

CLI: DownRim.exe --help
