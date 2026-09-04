#!/usr/bin/env python3
"""
DownRim Universal Steam Workshop Downloader

A SteamCMD front-end for downloading public Steam Workshop items from any game/app.
It can auto-detect each item's appid with Steam's public PublishedFileDetails API,
expand collections, group mixed-game downloads by appid, install SteamCMD when missing,
and run as either a simple GUI or CLI.

Important: this does not bypass Steam permissions. Private, hidden, age/region locked,
paid/DLC-gated, or account-required items may need a real Steam login and may still be
blocked by Steam/SteamCMD.
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import locale
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

APP_NAME = "DownRim Universal"
VERSION = "3.5.0"
DEFAULT_RIMWORLD_APPID = 294100
COLLECTION_API = "https://api.steampowered.com/ISteamRemoteStorage/GetCollectionDetails/v1/"
PUBLISHEDFILE_API = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
STEAMCMD_ZIP_URL = "https://steamcdn-a.akamaihd.net/client/installer/steamcmd.zip"
ID_RE = re.compile(r"(?i)\b(?:id=)?(\d{6,})\b")


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def default_steamcmd_path() -> Path:
    return app_dir() / "steamcmd.exe"


def logs_dir() -> Path:
    return app_dir() / "logs"


def config_path() -> Path:
    return app_dir() / "downrim_config.json"


def workshop_content_root(appid: int, download_dir: Optional[Path] = None) -> Path:
    base = Path(download_dir).expanduser() if download_dir is not None else app_dir()
    return base / "steamapps" / "workshop" / "content" / str(appid)


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def extract_workshop_id(text: str) -> Optional[str]:
    text = (text or "").strip()
    if not text:
        return None
    if text.isdigit():
        return text
    try:
        u = urlparse(text)
        if u.scheme in {"http", "https"} and u.netloc:
            ids = parse_qs(u.query or "").get("id") or []
            if ids and ids[0].strip().isdigit():
                return ids[0].strip()
    except Exception:
        pass
    m = ID_RE.search(text)
    return m.group(1) if m else None


def parse_lines(text: str) -> Tuple[List[str], List[str]]:
    good: List[str] = []
    bad: List[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        wid = extract_workshop_id(line)
        if wid:
            good.append(wid)
        else:
            bad.append(line)
    return dedupe(good), bad


def read_id_file(path: Path) -> Tuple[List[str], List[str]]:
    return parse_lines(path.read_text(encoding="utf-8", errors="replace"))


def dedupe(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        s = str(item).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def chunks(items: List[str], size: int) -> Iterable[List[str]]:
    size = max(1, int(size or 1))
    for i in range(0, len(items), size):
        yield items[i:i + size]


def http_post_json(url: str, payload: Dict[str, str], timeout: int = 20) -> Dict[str, Any]:
    data = urlencode(payload).encode("utf-8")
    req = Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=utf-8")
    req.add_header("User-Agent", f"DownRimUniversal/{VERSION}")
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8", errors="replace"))


def get_published_file_details(ids: List[str], timeout: int = 20) -> Dict[str, Dict[str, Any]]:
    details: Dict[str, Dict[str, Any]] = {}
    for batch in chunks(dedupe(ids), 100):
        payload: Dict[str, str] = {"itemcount": str(len(batch)), "format": "json"}
        for i, wid in enumerate(batch):
            payload[f"publishedfileids[{i}]"] = wid
        data = http_post_json(PUBLISHEDFILE_API, payload, timeout=timeout)
        for item in (data.get("response") or {}).get("publishedfiledetails") or []:
            wid = str(item.get("publishedfileid") or "").strip()
            if wid:
                details[wid] = item
    return details


def get_collection_children(collection_id: str, timeout: int = 20) -> List[str]:
    payload = {
        "collectioncount": "1",
        "publishedfileids[0]": str(collection_id),
        "format": "json",
    }
    data = http_post_json(COLLECTION_API, payload, timeout=timeout)
    collectiondetails = (data.get("response") or {}).get("collectiondetails") or []
    if not collectiondetails:
        return []
    out: List[str] = []
    for child in collectiondetails[0].get("children") or []:
        wid = str(child.get("publishedfileid") or "").strip()
        if wid.isdigit():
            out.append(wid)
    return dedupe(out)


def expand_collections(collection_ids: List[str], depth: int = 1, timeout: int = 20, log=None) -> Tuple[List[str], Dict[str, Any]]:
    depth = max(1, int(depth or 1))
    q: List[Tuple[str, int]] = [(cid, 1) for cid in dedupe(collection_ids)]
    seen_collections = set()
    items: List[str] = []
    meta: Dict[str, Any] = {"depth": depth, "collections": {}}
    while q:
        cid, level = q.pop(0)
        if cid in seen_collections:
            continue
        seen_collections.add(cid)
        if log:
            log(f"[COLLECTION] Expanding {cid} at depth {level}/{depth}")
        try:
            children = get_collection_children(cid, timeout=timeout)
            meta["collections"][cid] = {"children": children, "child_count": len(children)}
        except Exception as exc:
            meta["collections"][cid] = {"children": [], "child_count": 0, "error": str(exc)}
            if log:
                log(f"[WARN] Failed to expand collection {cid}: {exc}")
            continue
        items.extend(children)
        if level < depth:
            # Best-effort: ask collection API whether each child is also a collection.
            for child in children:
                if child in seen_collections:
                    continue
                try:
                    if get_collection_children(child, timeout=timeout):
                        q.append((child, level + 1))
                except Exception:
                    pass
    return dedupe(items), meta


def parse_appid(value: str) -> Optional[int]:
    s = str(value or "auto").strip().lower()
    if s in {"", "auto", "detect", "detected"}:
        return None
    if not s.isdigit():
        raise ValueError("AppID must be a number or 'auto'.")
    return int(s)


def appid_from_details(item: Dict[str, Any]) -> Optional[int]:
    # Steam's PublishedFileDetails API has historically returned both naming
    # styles in different examples/clients. Current public responses use
    # consumer_app_id / creator_app_id; keep the non-underscore aliases too.
    for key in ("consumer_app_id", "consumer_appid", "creator_app_id", "creator_appid"):
        val = item.get(key)
        try:
            n = int(val)
            if n > 0:
                return n
        except Exception:
            pass
    return None


def resolve_appids(item_ids: List[str], appid: Optional[int], timeout: int, log=None) -> Tuple[Dict[str, int], Dict[str, Dict[str, Any]], List[str]]:
    if appid is not None:
        return {wid: appid for wid in item_ids}, {}, []
    if log:
        log(f"[INFO] Auto-detecting appid for {len(item_ids)} item(s) via Steam API...")
    details = get_published_file_details(item_ids, timeout=timeout)
    mapping: Dict[str, int] = {}
    unresolved: List[str] = []
    for wid in item_ids:
        item = details.get(wid) or {}
        app = appid_from_details(item)
        if app is None:
            unresolved.append(wid)
        else:
            mapping[wid] = app
    return mapping, details, unresolved


def sanitize_args(args: List[str]) -> List[str]:
    """Mask secrets before logs/reports. SteamCMD login syntax is +login <user> <password>."""
    out = list(args)
    try:
        i = out.index("+login")
        if i + 1 < len(out) and out[i + 1] != "anonymous" and i + 2 < len(out):
            out[i + 2] = "********"
    except ValueError:
        pass
    return out


def build_steamcmd_args(
    steamcmd: Path,
    login: str,
    username: Optional[str],
    password: Optional[str],
    appid: int,
    ids: List[str],
    download_dir: Path,
    stop_on_failed_command: bool = True,
) -> List[str]:
    download_dir = Path(download_dir).expanduser().resolve()
    download_dir.mkdir(parents=True, exist_ok=True)
    args = [str(steamcmd), "+@ShutdownOnFailedCommand", "1" if stop_on_failed_command else "0", "+@NoPromptForPassword", "1", "+force_install_dir", str(download_dir)]
    if login == "anonymous":
        args += ["+login", "anonymous"]
    else:
        if not username:
            raise ValueError("Steam username is required for user login.")
        if password is None:
            raise ValueError("Steam password is required for user login.")
        args += ["+login", username, password]
    for wid in ids:
        args += ["+workshop_download_item", str(appid), str(wid)]
    args += ["+quit"]
    return args


def parse_steamcmd_progress(line: str) -> Optional[int]:
    matches = re.findall(r"\[\s*(\d{1,3})%\]", line or "")
    if not matches:
        return None
    try:
        return max(0, min(100, int(matches[-1])))
    except Exception:
        return None


def parse_steamcmd_download_success(line: str) -> Optional[str]:
    """Return the Workshop ID from SteamCMD's success line, if present."""
    m = re.search(r"Success\.\s+Downloaded\s+item\s+(\d+)", line or "", re.IGNORECASE)
    return m.group(1) if m else None


def parse_steamcmd_item_events(text: str) -> List[Tuple[str, str, str]]:
    """Extract per-item SteamCMD results from output, even when SteamCMD glues lines together.

    Returns (status, workshop_id, message) where status is downloaded or failed.
    """
    events: List[Tuple[str, str, str]] = []
    for m in re.finditer(r"Success\.\s+Downloaded\s+item\s+(\d+)(?:\s+to\s+\"([^\"]+)\")?", text or "", re.IGNORECASE):
        wid = m.group(1)
        path = m.group(2) or ""
        msg = f"Downloaded to {path}" if path else "Downloaded"
        events.append(("downloaded", wid, msg))
    for m in re.finditer(r"ERROR!\s+Download\s+item\s+(\d+)\s+failed\s+\(([^)]*)\)", text or "", re.IGNORECASE):
        wid = m.group(1)
        reason = (m.group(2) or "Failure").strip()
        events.append(("failed", wid, reason))
    return events


def run_steamcmd(
    args: List[str],
    cwd: Path,
    log_file: Path,
    dry_run: bool = False,
    stop_event: Optional[threading.Event] = None,
    progress=None,
) -> Dict[str, Any]:
    safe = sanitize_args(args)
    if dry_run:
        log_file.write_text("DRY RUN\n" + " ".join(safe) + "\n", encoding="utf-8", errors="replace")
        if progress:
            progress({"kind": "line", "text": "[DRY RUN] " + " ".join(safe)})
            progress({"kind": "percent", "percent": 100})
        return {"returncode": 0, "stdout": "", "stderr": "", "sanitized_cmd": " ".join(safe), "dry_run": True}

    enc = locale.getpreferredencoding(False) or "utf-8"
    if progress:
        progress({"kind": "line", "text": "Running SteamCMD..."})
    proc = subprocess.Popen(
        args,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding=enc,
        errors="replace",
        bufsize=1,
    )

    lines: List[str] = []
    assert proc.stdout is not None
    try:
        while True:
            if stop_event is not None and stop_event.is_set() and proc.poll() is None:
                proc.terminate()
            line = proc.stdout.readline()
            if line:
                lines.append(line)
                clean = line.rstrip("\r\n")
                pct = parse_steamcmd_progress(clean)
                if progress:
                    progress({"kind": "line", "text": clean})
                    if pct is not None:
                        progress({"kind": "percent", "percent": pct})
                continue
            if proc.poll() is not None:
                break
            time.sleep(0.05)
    finally:
        try:
            remaining = proc.stdout.read() if proc.stdout else ""
            if remaining:
                lines.append(remaining)
                if progress:
                    for clean in remaining.splitlines():
                        progress({"kind": "line", "text": clean})
        except Exception:
            pass

    out = "".join(lines)
    text = ["COMMAND:\n" + " ".join(safe) + "\n", f"RETURN CODE: {proc.returncode}\n\n"]
    if out:
        text.append("OUTPUT:\n" + out + "\n")
    log_file.write_text("".join(text), encoding="utf-8", errors="replace")
    return {"returncode": proc.returncode, "stdout": out, "stderr": "", "sanitized_cmd": " ".join(safe), "dry_run": False}


def inspect_download(appid: int, wid: str, download_dir: Optional[Path] = None) -> Dict[str, Any]:
    path = workshop_content_root(appid, download_dir) / str(wid)
    exists = path.exists() and path.is_dir()
    file_count = 0
    total_bytes = 0
    if exists:
        try:
            for p in path.rglob("*"):
                if p.is_file():
                    file_count += 1
                    try:
                        total_bytes += p.stat().st_size
                    except OSError:
                        pass
        except Exception:
            pass
    return {
        "workshop_id": str(wid),
        "appid": int(appid),
        "path": str(path),
        "exists": exists,
        "file_count": file_count,
        "bytes": total_bytes,
        "downloaded": bool(exists and file_count > 0),
    }


def install_steamcmd(target_dir: Path, log=None) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    exe = target_dir / "steamcmd.exe"
    if exe.exists():
        if log:
            log(f"[OK] SteamCMD already exists: {exe}")
        return exe
    if log:
        log("[INFO] Downloading SteamCMD from Valve...")
    with tempfile.TemporaryDirectory(prefix="downrim-steamcmd-") as td:
        zip_path = Path(td) / "steamcmd.zip"
        req = Request(STEAMCMD_ZIP_URL, headers={"User-Agent": f"DownRimUniversal/{VERSION}"})
        with urlopen(req, timeout=120) as resp:
            zip_path.write_bytes(resp.read())
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(target_dir)
    if not exe.exists():
        raise RuntimeError(f"SteamCMD install failed; steamcmd.exe was not found in {target_dir}")
    if log:
        log(f"[OK] SteamCMD installed: {exe}")
    return exe


def steam_login_succeeded(output: str) -> bool:
    s = output or ""
    return "to Steam Public...OK" in s or "Waiting for user info...OK" in s


def steam_guard_needs_action(output: str) -> bool:
    """True only when Steam Guard/2FA is actually blocking login.

    SteamCMD prints "protected by a Steam Guard mobile authenticator" even when
    the user approves the login successfully. Treat that as informational unless
    the login times out/fails before Steam Public...OK / user info OK.
    """
    s = (output or "").lower()
    if steam_login_succeeded(output):
        return False
    blocking_terms = (
        "wait for confirmation timed out",
        "timed out waiting for confirmation",
        "error (timeout)",
        "auth code",
        "email code",
        "phone code",
        "two-factor",
        "two factor",
    )
    return any(k in s for k in blocking_terms)


def steamcmd_failure_hint(login: str, login_ok: bool) -> str:
    if login == "user" and login_ok:
        return (
            "Steam login succeeded, so this is probably not your username/password or Steam Guard. "
            "SteamCMD itself refused this Workshop file. Make sure the Steam account owns the game, "
            "open the item in the normal Steam client and Subscribe to it, then try again. Some games/items "
            "can only be installed by the Steam client and cannot be bypassed by SteamCMD."
        )
    if login == "user":
        return "Steam Guard / 2FA did not finish. Approve the Steam Mobile prompt or use Fix Steam Guard / 2FA login once, then retry."
    return "Anonymous SteamCMD was refused. Try Steam login=user with the Steam account that owns the game, and subscribe to the item in Steam if needed."


@dataclass
class DownloadOptions:
    appid: Optional[int]
    steamcmd: Path
    login: str
    username: Optional[str]
    password: Optional[str]
    batch_size: int
    retries: int
    timeout: int
    dry_run: bool = False
    skip_existing: bool = True
    download_dir: Path = None  # type: ignore[assignment]


def perform_downloads(item_ids: List[str], options: DownloadOptions, log=print, stop_event: Optional[threading.Event] = None, progress=None) -> Dict[str, Any]:
    item_ids = dedupe(item_ids)
    if not item_ids:
        raise ValueError("No workshop item IDs to download.")
    if options.download_dir is None:
        options.download_dir = app_dir()
    options.download_dir = Path(options.download_dir).expanduser().resolve()
    options.download_dir.mkdir(parents=True, exist_ok=True)
    if not options.steamcmd.exists():
        raise FileNotFoundError(f"steamcmd.exe not found: {options.steamcmd}")
    appid_map, details, unresolved = resolve_appids(item_ids, options.appid, options.timeout, log=log)
    if unresolved:
        raise RuntimeError("Could not auto-detect appid for: " + ", ".join(unresolved) + ". Re-run with a numeric AppID.")

    by_appid: Dict[int, List[str]] = {}
    for wid, appid in appid_map.items():
        by_appid.setdefault(appid, []).append(wid)

    ld = logs_dir()
    ld.mkdir(parents=True, exist_ok=True)
    report: Dict[str, Any] = {
        "app": APP_NAME,
        "version": VERSION,
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "steamcmd": str(options.steamcmd),
        "download_dir": str(options.download_dir),
        "dry_run": options.dry_run,
        "requested_count": len(item_ids),
        "appids": sorted(by_appid),
        "published_file_details": details,
        "batches": [],
        "items": {},
        "notes": [],
    }

    any_fail = False
    total_items = sum(len(ids_for_app) for ids_for_app in by_appid.values())
    processed_items = 0
    if progress:
        progress({"kind": "overall", "processed": 0, "total": total_items, "percent": 0})
    for appid, ids_for_app in sorted(by_appid.items()):
        to_download = list(ids_for_app)
        if options.skip_existing and not options.dry_run:
            before = list(to_download)
            to_download = [wid for wid in to_download if not inspect_download(appid, wid, options.download_dir)["downloaded"]]
            skipped = [wid for wid in before if wid not in to_download]
            for wid in skipped:
                report["items"][wid] = inspect_download(appid, wid, options.download_dir)
            if skipped:
                processed_items += len(skipped)
                if progress:
                    progress({"kind": "overall", "processed": processed_items, "total": total_items, "percent": (processed_items / total_items) * 100 if total_items else 100})
                log(f"[INFO] AppID {appid}: skipping {len(skipped)} already-downloaded item(s).")
        log(f"[INFO] AppID {appid}: {len(to_download)} item(s) to download.")
        if options.login == "user" and not options.dry_run:
            # Use one SteamCMD process per AppID for authenticated downloads. Otherwise
            # Steam Guard mobile confirmation can be requested once per item and later
            # items time out while the user thinks they are already logged in.
            effective_batch_size = max(1, len(to_download))
            log("[INFO] Authenticated mode: using one SteamCMD session for this AppID to avoid repeated Steam Guard prompts.")
        else:
            effective_batch_size = 1 if not options.dry_run else max(1, int(options.batch_size))
            if to_download and effective_batch_size == 1 and int(options.batch_size) != 1:
                log("[INFO] Live item progress mode: running one Workshop item at a time so the progress bar updates after each item.")
        for batch_no, batch in enumerate(chunks(to_download, effective_batch_size), 1):
            if stop_event is not None and stop_event.is_set():
                report["notes"].append("Stopped by user.")
                return report
            args = build_steamcmd_args(
                options.steamcmd,
                options.login,
                options.username,
                options.password,
                appid,
                batch,
                options.download_dir,
                stop_on_failed_command=not (options.login == "user" and not options.dry_run),
            )
            result = None
            last_log = None
            batch_reported_done: set[str] = set()

            def batch_progress(event: Dict[str, Any]) -> None:
                nonlocal processed_items
                if event.get("kind") == "line":
                    for status, wid_done, message in parse_steamcmd_item_events(str(event.get("text", ""))):
                        if wid_done in batch and wid_done not in batch_reported_done and status == "downloaded":
                            batch_reported_done.add(wid_done)
                            processed_items += 1
                            if progress:
                                progress({
                                    "kind": "overall",
                                    "processed": processed_items,
                                    "total": total_items,
                                    "percent": (processed_items / total_items) * 100 if total_items else 100,
                                    "item_id": wid_done,
                                    "item_status": status,
                                    "message": message,
                                })
                if progress:
                    progress(event)

            for attempt in range(1, max(0, options.retries) + 2):
                last_log = ld / f"steamcmd_app{appid}_batch{batch_no:03d}_{stamp()}_attempt{attempt}.log"
                log(f"[BATCH] AppID {appid}, batch {batch_no}, attempt {attempt}: {len(batch)} item(s)")
                result = run_steamcmd(args, app_dir(), last_log, dry_run=options.dry_run, stop_event=stop_event, progress=batch_progress)
                output = (result.get("stdout", "") + "\n" + result.get("stderr", ""))
                if options.login == "user" and steam_guard_needs_action(output):
                    report["notes"].append("Steam Guard / 2FA is blocking login. Use Fix Steam Guard / 2FA login, approve in Steam Mobile, then retry.")
                    log("[WARN] Steam Guard / 2FA is blocking login. Approve the Steam Mobile prompt or use Fix Steam Guard / 2FA login, then retry.")
                    break
                if result["returncode"] == 0:
                    break
                log(f"[WARN] SteamCMD returned {result['returncode']}.")
            assert result is not None and last_log is not None
            verified_batch: List[Dict[str, Any]] = [inspect_download(appid, wid, options.download_dir) for wid in batch]
            batch_downloaded = all(info["downloaded"] for info in verified_batch)
            report["batches"].append({
                "appid": appid,
                "batch_index": batch_no,
                "workshop_ids": batch,
                "returncode": result["returncode"],
                "dry_run": result["dry_run"],
                "downloaded_despite_nonzero_returncode": bool(result["returncode"] != 0 and batch_downloaded),
                "sanitized_cmd": result["sanitized_cmd"],
                "log_file": str(last_log),
            })
            # SteamCMD can return a non-zero code while still successfully writing all
            # requested Workshop folders, especially after its first-run self-update.
            # Trust the concrete file verification over the process code in that case.
            if result["returncode"] != 0 and not options.dry_run and not batch_downloaded:
                any_fail = True
            failed_events = {wid: msg for status, wid, msg in parse_steamcmd_item_events(output) if status == "failed"}
            for info in verified_batch:
                wid = info["workshop_id"]
                item_details = details.get(wid) or {}
                title = item_details.get("title") or "Unknown title"
                info["title"] = title
                if wid in failed_events:
                    info["steamcmd_error"] = failed_events[wid]
                    info["hint"] = steamcmd_failure_hint(options.login, steam_login_succeeded(output))
                report["items"][wid] = info
                if not info["downloaded"] and not options.dry_run:
                    any_fail = True
                if wid not in batch_reported_done:
                    processed_items += 1
                    status = "downloaded" if info["downloaded"] else "failed"
                    if options.dry_run:
                        status = "checked"
                        log(f"[DRY RUN] Checked {wid} ({title}) AppID {appid}; no files downloaded.")
                    elif status == "downloaded":
                        batch_reported_done.add(wid)
                        log(f"[OK] Downloaded {wid} ({title}) -> {info['path']}")
                    else:
                        reason = failed_events.get(wid, "No downloaded files were found after SteamCMD finished.")
                        log(f"[FAILED] {wid} ({title}) AppID {appid}: {reason}")
                        log("         Hint: " + steamcmd_failure_hint(options.login, steam_login_succeeded(output)))
                    if progress:
                        progress({
                            "kind": "overall",
                            "processed": processed_items,
                            "total": total_items,
                            "percent": (processed_items / total_items) * 100 if total_items else 100,
                            "item_id": wid,
                            "item_status": status,
                        })
    report["success_count"] = sum(1 for x in report["items"].values() if x.get("downloaded"))
    report["failed"] = any_fail
    out = app_dir() / "download_report.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8", errors="replace")
    report["report_file"] = str(out)
    return report


def load_config() -> Dict[str, Any]:
    try:
        return json.loads(config_path().read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def save_config(data: Dict[str, Any]) -> None:
    try:
        config_path().write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass



def format_bytes(n: int) -> str:
    value = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{int(n)} B"


def list_downloaded_items(download_dir: Path) -> List[Dict[str, Any]]:
    root = Path(download_dir).expanduser() / "steamapps" / "workshop" / "content"
    out: List[Dict[str, Any]] = []
    if not root.exists():
        return out
    for app_folder in root.iterdir():
        if not app_folder.is_dir() or not app_folder.name.isdigit():
            continue
        appid = int(app_folder.name)
        for item_folder in app_folder.iterdir():
            if item_folder.is_dir() and item_folder.name.isdigit():
                out.append(inspect_download(appid, item_folder.name, download_dir))
    out.sort(key=lambda x: (int(x.get("appid", 0)), int(x.get("workshop_id", 0))))
    return out


def delete_downloaded_items(download_dir: Path, items: List[Dict[str, Any]]) -> Tuple[int, List[str]]:
    deleted = 0
    errors: List[str] = []
    base = Path(download_dir).expanduser().resolve()
    for item in items:
        p = Path(item.get("path", ""))
        try:
            resolved = p.resolve()
            if base not in resolved.parents:
                errors.append(f"Skipped unsafe path outside download folder: {p}")
                continue
            if p.exists() and p.is_dir():
                shutil.rmtree(p)
                deleted += 1
        except Exception as exc:
            errors.append(f"{p}: {exc}")
    return deleted, errors


def write_steam_guard_setup_bat(steamcmd: Path, username: Optional[str]) -> Path:
    """Create a small .bat that opens SteamCMD for interactive password/Steam Guard setup."""
    steamcmd = Path(steamcmd).expanduser().resolve()
    bat = app_dir() / "steamcmd_login_setup.bat"
    user_line = f"{steamcmd.name} +login {username}" if username else f"{steamcmd.name} +login"
    bat.write_text(
        "@echo off\r\n"
        "title DownRim Steam Login / Steam Guard Setup\r\n"
        "echo DownRim Steam Login / Steam Guard Setup\r\n"
        "echo.\r\n"
        "echo This window is for Steam Guard / two-factor login setup.\r\n"
        "echo Type your Steam password and Steam Guard code here if SteamCMD asks.\r\n"
        "echo When SteamCMD finishes logging in, type: quit\r\n"
        "echo DownRim does not save your password.\r\n"
        "echo.\r\n"
        f"cd /d \"{steamcmd.parent}\"\r\n"
        f"{user_line}\r\n"
        "echo.\r\n"
        "echo If login completed, close this window and retry DownRim with Steam login = user.\r\n"
        "pause\r\n",
        encoding="utf-8",
    )
    return bat


def launch_steam_guard_setup(steamcmd: Path, username: Optional[str]) -> Path:
    bat = write_steam_guard_setup_bat(steamcmd, username)
    if sys.platform.startswith("win"):
        os.startfile(str(bat))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["bash", str(bat)])
    return bat

def gui_main() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk

    root = tk.Tk()
    root.title(f"{APP_NAME} v{VERSION}")
    root.geometry("1080x780")
    cfg = load_config()

    uiq: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
    stop_event = threading.Event()
    worker: Dict[str, Optional[threading.Thread]] = {"thread": None}

    steamcmd_var = tk.StringVar(value=cfg.get("steamcmd", str(default_steamcmd_path())))
    download_dir_var = tk.StringVar(value=cfg.get("download_dir", str(app_dir())))
    appid_var = tk.StringVar(value=str(cfg.get("appid", "auto")))
    login_var = tk.StringVar(value=cfg.get("login", "anonymous"))
    username_var = tk.StringVar(value=cfg.get("username", ""))
    batch_var = tk.IntVar(value=int(cfg.get("batch_size", 1)))
    retries_var = tk.IntVar(value=int(cfg.get("retries", 1)))
    timeout_var = tk.IntVar(value=int(cfg.get("timeout", 20)))
    depth_var = tk.IntVar(value=int(cfg.get("collection_depth", 1)))
    skip_existing_var = tk.BooleanVar(value=bool(cfg.get("skip_existing", True)))
    progress_var = tk.DoubleVar(value=0.0)
    status_var = tk.StringVar(value="Ready. Paste Workshop links, then click Dry Run or Start Download.")

    def log(msg: str) -> None:
        uiq.put(("log", msg))

    def set_status(msg: str) -> None:
        uiq.put(("status", msg))

    def set_progress(percent: float, msg: str = "") -> None:
        uiq.put(("progress", {"percent": percent, "message": msg}))

    root.columnconfigure(0, weight=1)
    main = ttk.Frame(root, padding=8)
    main.pack(fill="both", expand=True)

    header = ttk.LabelFrame(main, text="1) Folders", padding=8)
    header.pack(fill="x", pady=(0, 6))
    header.columnconfigure(1, weight=1)
    ttk.Label(header, text="SteamCMD program:").grid(row=0, column=0, sticky="w")
    ttk.Entry(header, textvariable=steamcmd_var).grid(row=0, column=1, sticky="ew", padx=6)

    def browse_steamcmd():
        p = filedialog.askopenfilename(title="Choose steamcmd.exe", filetypes=[("steamcmd.exe", "steamcmd.exe"), ("Executables", "*.exe"), ("All files", "*.*")])
        if p:
            steamcmd_var.set(p)

    def install_clicked():
        try:
            exe = install_steamcmd(app_dir(), log=log)
            steamcmd_var.set(str(exe))
            messagebox.showinfo("SteamCMD installed", f"SteamCMD is ready:\n{exe}")
        except Exception as exc:
            messagebox.showerror("Install failed", str(exc))

    ttk.Button(header, text="Browse...", command=browse_steamcmd).grid(row=0, column=2, padx=3)
    ttk.Button(header, text="Install SteamCMD", command=install_clicked).grid(row=0, column=3, padx=3)
    ttk.Label(header, text="Download folder:").grid(row=1, column=0, sticky="w", pady=(6, 0))
    ttk.Entry(header, textvariable=download_dir_var).grid(row=1, column=1, sticky="ew", padx=6, pady=(6, 0))

    def browse_download_dir():
        p = filedialog.askdirectory(title="Choose where Workshop files should download")
        if p:
            download_dir_var.set(p)
            set_status(f"Downloads will go to: {p}")

    def open_download_dir():
        p = Path(download_dir_var.get()).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(str(p))  # type: ignore[attr-defined]

    ttk.Button(header, text="Choose folder...", command=browse_download_dir).grid(row=1, column=2, padx=3, pady=(6, 0))
    ttk.Button(header, text="Open folder", command=open_download_dir).grid(row=1, column=3, padx=3, pady=(6, 0))

    settings = ttk.LabelFrame(main, text="2) Simple settings", padding=8)
    settings.pack(fill="x", pady=6)
    ttk.Label(settings, text="AppID:").grid(row=0, column=0, sticky="w")
    ttk.Entry(settings, textvariable=appid_var, width=12).grid(row=0, column=1, sticky="w", padx=(4, 8))
    ttk.Label(settings, text="Leave this as auto unless a Workshop item fails to detect. RimWorld is 294100.").grid(row=0, column=2, columnspan=4, sticky="w")
    ttk.Label(settings, text="Steam login:").grid(row=1, column=0, sticky="w", pady=4)
    ttk.Combobox(settings, values=["anonymous", "user"], textvariable=login_var, width=12, state="readonly").grid(row=1, column=1, sticky="w", padx=(4, 8))
    ttk.Label(settings, text="Username:").grid(row=1, column=2, sticky="e")
    ttk.Entry(settings, textvariable=username_var, width=26).grid(row=1, column=3, sticky="w", padx=4)
    ttk.Checkbutton(settings, text="Skip already-downloaded items", variable=skip_existing_var).grid(row=1, column=4, padx=10)

    def explain_login():
        messagebox.showinfo(
            "What does username mean?",
            "For most public Workshop items, leave Steam login as 'anonymous' and leave Username empty.\n\n"
            "Only choose 'user' if SteamCMD says anonymous download is not allowed.\n\n"
            "Username means your Steam account login name — the name you type when signing into Steam. "
            "It is not your public display/nickname if those are different. DownRim asks for the password only when you start and does not save it. Steam Guard may require you to run steamcmd.exe manually once.",
        )

    ttk.Button(settings, text="What is username?", command=explain_login).grid(row=1, column=5, padx=4)

    def setup_steam_guard_login():
        try:
            steamcmd = Path(steamcmd_var.get()).expanduser()
            if not steamcmd.exists():
                messagebox.showerror("SteamCMD not found", "steamcmd.exe was not found. Click Install SteamCMD first.")
                return
            username = username_var.get().strip() or None
            launch_steam_guard_setup(steamcmd, username)
            log("[LOGIN] Opened SteamCMD login setup window. Finish password/Steam Guard there, type quit, then retry download with Steam login = user.")
            messagebox.showinfo(
                "Steam Guard setup opened",
                "A SteamCMD window was opened.\n\n"
                "In that window, login with your Steam account, enter Steam Guard / two-factor code if asked, then type: quit\n\n"
                "After that, retry DownRim with Steam login = user."
            )
        except Exception as exc:
            messagebox.showerror("Could not open Steam login setup", str(exc))

    ttk.Button(settings, text="Fix Steam Guard / 2FA login", command=setup_steam_guard_login).grid(row=2, column=4, columnspan=2, sticky="w", padx=10, pady=4)
    nums = ttk.Frame(settings)
    nums.grid(row=3, column=0, columnspan=6, sticky="w", pady=4)
    for label, var, frm, to in [("Batch size (dry-run only; real downloads use 1 for live progress)", batch_var, 1, 200), ("Retries", retries_var, 0, 10), ("API timeout", timeout_var, 5, 120), ("Collection depth", depth_var, 1, 5)]:
        ttk.Label(nums, text=label + ":").pack(side="left")
        ttk.Spinbox(nums, from_=frm, to=to, textvariable=var, width=6).pack(side="left", padx=(3, 12))

    progress_box = ttk.LabelFrame(main, text="3) Live progress", padding=8)
    progress_box.pack(fill="x", pady=6)
    ttk.Progressbar(progress_box, variable=progress_var, maximum=100).pack(fill="x")
    ttk.Label(progress_box, textvariable=status_var).pack(anchor="w", pady=(4, 0))

    tabs = ttk.Notebook(main)
    tabs.pack(fill="both", expand=True, pady=6)
    tab_items = ttk.Frame(tabs, padding=8)
    tab_manage = ttk.Frame(tabs, padding=8)
    tab_log = ttk.Frame(tabs, padding=8)
    tabs.add(tab_items, text="Download")
    tabs.add(tab_manage, text="Manage/Delete Downloaded")
    tabs.add(tab_log, text="Log")

    ttk.Label(tab_items, text="Paste Workshop item links/IDs or collection links/IDs, one per line:").pack(anchor="w")
    text = tk.Text(tab_items, height=16, wrap="word")
    text.pack(fill="both", expand=True, pady=4)
    buttons = ttk.Frame(tab_items)
    buttons.pack(fill="x")

    def load_file():
        p = filedialog.askopenfilename(title="Open text file", filetypes=[("Text files", "*.txt"), ("All files", "*.*")])
        if p:
            text.insert("end", Path(p).read_text(encoding="utf-8", errors="replace") + "\n")

    def save_settings():
        save_config({
            "steamcmd": steamcmd_var.get(), "download_dir": download_dir_var.get(), "appid": appid_var.get(),
            "login": login_var.get(), "username": username_var.get(), "batch_size": int(batch_var.get()),
            "retries": int(retries_var.get()), "timeout": int(timeout_var.get()),
            "collection_depth": int(depth_var.get()), "skip_existing": bool(skip_existing_var.get()),
        })

    def start_download(dry_run: bool = False):
        if worker["thread"] and worker["thread"].is_alive():
            messagebox.showinfo("Already running", "A download is already running.")
            return
        item_ids, bad = parse_lines(text.get("1.0", "end"))
        if bad:
            log("[WARN] Ignored lines with no Workshop ID: " + ", ".join(bad[:10]))
        if not item_ids:
            messagebox.showwarning("No IDs", "Paste at least one Workshop item or collection link/ID.")
            return
        save_settings()
        password: Optional[str] = None
        if login_var.get() == "user":
            if not username_var.get().strip():
                messagebox.showwarning("Username needed", "Put your Steam account login name in Username, or switch login back to anonymous.")
                return
            password = simpledialog.askstring("Steam Login", "Steam password (not saved):", show="*")
            if password is None:
                return
        stop_event.clear()
        progress_var.set(0)
        set_status("Starting...")

        def progress_cb(event: Dict[str, Any]) -> None:
            if event.get("kind") == "overall":
                processed = int(event.get("processed", 0))
                total = int(event.get("total", 0))
                pct = float(event.get("percent", 0))
                item_id = event.get("item_id")
                if item_id:
                    status = str(event.get("item_status") or "processed")
                    if status == "downloaded":
                        set_progress(pct, f"Downloaded item {item_id}. Overall progress: {processed}/{total}")
                    elif status == "failed":
                        set_progress(pct, f"Failed item {item_id}. Overall progress: {processed}/{total} — see Log tab for why")
                    else:
                        set_progress(pct, f"Processed item {item_id}. Overall progress: {processed}/{total}")
                else:
                    set_progress(pct, f"Overall progress: {processed}/{total} item(s) processed")
            elif event.get("kind") == "percent":
                set_progress(float(event.get("percent", 0)), f"SteamCMD progress: {event.get('percent')}%")
            elif event.get("kind") == "line":
                line = str(event.get("text", ""))
                if "Downloading item" in line or "Success." in line or line.startswith("["):
                    log("[STEAMCMD] " + line)
                    pct = parse_steamcmd_progress(line)
                    if pct is not None:
                        set_progress(pct, f"SteamCMD progress: {pct}%")

        def run():
            try:
                ids = list(item_ids)
                set_status("Checking whether pasted IDs include collections...")
                try:
                    expanded, meta = expand_collections(ids, depth=int(depth_var.get()), timeout=int(timeout_var.get()), log=log)
                    real_collections = [cid for cid, info in meta.get("collections", {}).items() if info.get("child_count", 0) > 0]
                    if real_collections:
                        log(f"[COLLECTION] Found {len(real_collections)} collection(s); added {len(expanded)} child item(s).")
                        ids = dedupe(ids + expanded)
                except Exception as exc:
                    log(f"[WARN] Collection expansion step failed: {exc}")
                set_status(f"Resolved {len(ids)} item(s). Starting SteamCMD...")
                options = DownloadOptions(
                    appid=parse_appid(appid_var.get()),
                    steamcmd=Path(steamcmd_var.get()).expanduser(),
                    login=login_var.get(),
                    username=username_var.get().strip() or None,
                    password=password,
                    batch_size=int(batch_var.get()),
                    retries=int(retries_var.get()),
                    timeout=int(timeout_var.get()),
                    dry_run=dry_run,
                    skip_existing=bool(skip_existing_var.get()),
                    download_dir=Path(download_dir_var.get()).expanduser(),
                )
                report = perform_downloads(ids, options, log=log, stop_event=stop_event, progress=progress_cb)
                set_progress(100, "Finished. Check the report/log if anything failed.")
                log(f"[DONE] Report written: {report.get('report_file', app_dir() / 'download_report.json')}")
                if report.get("failed"):
                    log("[DONE] Some items failed. Check the logs folder and report.")
                else:
                    log("[DONE] Finished successfully.")
            except Exception as exc:
                set_status("Error. Check the Log tab.")
                log("[ERROR] " + str(exc))
                log(traceback.format_exc())
            finally:
                uiq.put(("done", None))

        worker["thread"] = threading.Thread(target=run, daemon=True)
        worker["thread"].start()
        log("[INFO] Started " + ("dry run." if dry_run else "download."))

    def stop():
        stop_event.set()
        log("[INFO] Stop requested; waiting for SteamCMD to exit...")
        set_status("Stopping... SteamCMD may take a moment to close.")

    ttk.Button(buttons, text="Load List File...", command=load_file).pack(side="left")
    ttk.Button(buttons, text="Dry Run / Check IDs", command=lambda: start_download(True)).pack(side="left", padx=6)
    ttk.Button(buttons, text="Start Download", command=lambda: start_download(False)).pack(side="left", padx=6)
    ttk.Button(buttons, text="Stop", command=stop).pack(side="left", padx=6)
    ttk.Button(buttons, text="Open Download Folder", command=open_download_dir).pack(side="right")

    # Manage/delete tab
    manage_top = ttk.Frame(tab_manage)
    manage_top.pack(fill="x")
    downloaded_tree = ttk.Treeview(tab_manage, columns=("appid", "id", "size", "files", "path"), show="headings", height=14)
    for col, title, width in [("appid", "AppID", 80), ("id", "Workshop ID", 130), ("size", "Size", 90), ("files", "Files", 70), ("path", "Folder", 520)]:
        downloaded_tree.heading(col, text=title)
        downloaded_tree.column(col, width=width, anchor="w")
    downloaded_tree.pack(fill="both", expand=True, pady=6)
    downloaded_cache: List[Dict[str, Any]] = []

    def refresh_downloaded():
        nonlocal downloaded_cache
        downloaded_cache = list_downloaded_items(Path(download_dir_var.get()).expanduser())
        for iid in downloaded_tree.get_children():
            downloaded_tree.delete(iid)
        for idx, item in enumerate(downloaded_cache):
            downloaded_tree.insert("", "end", iid=str(idx), values=(item["appid"], item["workshop_id"], format_bytes(item["bytes"]), item["file_count"], item["path"]))
        set_status(f"Found {len(downloaded_cache)} downloaded Workshop folder(s).")

    def selected_downloaded_items() -> List[Dict[str, Any]]:
        out = []
        for iid in downloaded_tree.selection():
            try:
                out.append(downloaded_cache[int(iid)])
            except Exception:
                pass
        return out

    def delete_selected():
        items = selected_downloaded_items()
        if not items:
            messagebox.showinfo("Nothing selected", "Select one or more downloaded items first.")
            return
        if not messagebox.askyesno("Delete selected downloads", f"Delete {len(items)} downloaded Workshop folder(s)?\n\nThis removes files from the selected download folder only."):
            return
        deleted, errors = delete_downloaded_items(Path(download_dir_var.get()).expanduser(), items)
        log(f"[DELETE] Deleted {deleted}/{len(items)} selected item(s).")
        if errors:
            log("[DELETE] Errors:\n" + "\n".join(errors))
        refresh_downloaded()

    def delete_all():
        refresh_downloaded()
        if not downloaded_cache:
            messagebox.showinfo("Nothing to delete", "No downloaded Workshop folders were found in the selected download folder.")
            return
        if not messagebox.askyesno("Delete ALL downloads", f"Delete ALL {len(downloaded_cache)} downloaded Workshop folder(s) under:\n{download_dir_var.get()}\n\nThis cannot be undone."):
            return
        deleted, errors = delete_downloaded_items(Path(download_dir_var.get()).expanduser(), list(downloaded_cache))
        log(f"[DELETE] Deleted {deleted}/{len(downloaded_cache)} item(s).")
        if errors:
            log("[DELETE] Errors:\n" + "\n".join(errors))
        refresh_downloaded()

    ttk.Button(manage_top, text="Refresh list", command=refresh_downloaded).pack(side="left")
    ttk.Button(manage_top, text="Delete selected", command=delete_selected).pack(side="left", padx=6)
    ttk.Button(manage_top, text="Delete ALL downloaded in this folder", command=delete_all).pack(side="left", padx=6)
    ttk.Label(tab_manage, text="Tip: this only deletes Workshop files inside the chosen Download folder. It does not touch Steam itself.").pack(anchor="w")

    log_text = tk.Text(tab_log, wrap="word")
    log_text.pack(fill="both", expand=True)

    def poll():
        try:
            while True:
                typ, payload = uiq.get_nowait()
                if typ == "log":
                    log_text.insert("end", str(payload) + "\n")
                    log_text.see("end")
                elif typ == "status":
                    status_var.set(str(payload))
                elif typ == "progress":
                    progress_var.set(max(0, min(100, float(payload.get("percent", 0)))))
                    if payload.get("message"):
                        status_var.set(str(payload.get("message")))
                elif typ == "done":
                    pass
        except queue.Empty:
            pass
        root.after(100, poll)

    def close():
        save_settings()
        stop_event.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    poll()
    log(f"{APP_NAME} v{VERSION}")
    log(f"App folder: {app_dir()}")
    log("Username help: leave login as anonymous unless SteamCMD says the item requires an account.")
    root.mainloop()
    return 0


def cli_main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=f"{APP_NAME}: download Steam Workshop items with SteamCMD.")
    p.add_argument("--gui", action="store_true", help="Launch GUI.")
    p.add_argument("--appid", default="auto", help="Game/app AppID, or 'auto' to detect per item (default: auto).")
    p.add_argument("--steamcmd", default=str(default_steamcmd_path()), help="Path to steamcmd.exe (default: next to app).")
    p.add_argument("--download-dir", default=str(app_dir()), help="Folder where steamapps/workshop/content will be created (default: next to app).")
    p.add_argument("--install-steamcmd", action="store_true", help="Download Valve SteamCMD into this app folder if missing.")
    p.add_argument("--links", nargs="+", help="Workshop item/collection links or IDs.")
    p.add_argument("--in", dest="infile", help="Text file of links/IDs, one per line.")
    p.add_argument("--collection", nargs="+", help="Explicit collection links/IDs to expand.")
    p.add_argument("--collection-depth", type=int, default=1, help="Nested collection expansion depth.")
    p.add_argument("--api-timeout", type=int, default=20)
    p.add_argument("--batch-size", type=int, default=25)
    p.add_argument("--retries", type=int, default=1)
    p.add_argument("--login", choices=["anonymous", "user"], default="anonymous")
    p.add_argument("--username")
    p.add_argument("--steam-login-setup", action="store_true", help="Open SteamCMD in an interactive login window so you can complete Steam Guard / 2FA, then exit.")
    p.add_argument("--no-skip-existing", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--list", action="store_true", help="Resolve IDs/appids and print them without downloading.")
    args = p.parse_args(argv)

    if args.gui or (argv is None and len(sys.argv) == 1):
        return gui_main()

    steamcmd = Path(args.steamcmd).expanduser()
    if not steamcmd.is_absolute():
        steamcmd = (app_dir() / steamcmd).resolve()
    if args.install_steamcmd:
        steamcmd = install_steamcmd(app_dir())
    if args.steam_login_setup:
        if not steamcmd.exists():
            print(f"[ERROR] steamcmd.exe not found: {steamcmd}", file=sys.stderr)
            return 2
        bat = launch_steam_guard_setup(steamcmd, args.username)
        print(f"Opened SteamCMD login setup: {bat}")
        print("Finish password/Steam Guard there, type quit, then retry with --login user --username YOUR_LOGIN_NAME.")
        return 0

    ids: List[str] = []
    bad: List[str] = []
    if args.links:
        parsed, bad1 = parse_lines("\n".join(args.links))
        ids.extend(parsed); bad.extend(bad1)
    if args.infile:
        parsed, bad1 = read_id_file(Path(args.infile).expanduser())
        ids.extend(parsed); bad.extend(bad1)
    if args.collection:
        cols, bad1 = parse_lines("\n".join(args.collection))
        bad.extend(bad1)
        expanded, _ = expand_collections(cols, depth=args.collection_depth, timeout=args.api_timeout, log=print)
        ids.extend(expanded)
    # Also auto-expand any collection-looking input; harmless for normal items.
    if ids and not args.collection:
        expanded, meta = expand_collections(ids, depth=args.collection_depth, timeout=args.api_timeout, log=None)
        if expanded:
            ids.extend(expanded)
    ids = dedupe(ids)
    if bad:
        print("[WARN] Ignored invalid input(s):", ", ".join(bad), file=sys.stderr)
    if not ids:
        p.error("No valid Workshop item IDs found. Use --links, --in, or --collection.")

    appid = parse_appid(args.appid)
    if args.list:
        mapping, details, unresolved = resolve_appids(ids, appid, args.api_timeout, log=print)
        for wid in ids:
            item = details.get(wid) or {}
            title = item.get("title") or ""
            app = mapping.get(wid)
            print(f"{wid}\tappid={app or 'UNKNOWN'}\t{title}")
        if unresolved:
            print("Unresolved appids:", ", ".join(unresolved), file=sys.stderr)
            return 2
        return 0

    password = None
    if args.login == "user":
        if not args.username:
            print("[ERROR] --username is required with --login user", file=sys.stderr)
            return 2
        password = getpass.getpass("Steam password (not saved): ")

    try:
        report = perform_downloads(
            ids,
            DownloadOptions(
                appid=appid,
                steamcmd=steamcmd,
                login=args.login,
                username=args.username,
                password=password,
                batch_size=args.batch_size,
                retries=args.retries,
                timeout=args.api_timeout,
                dry_run=args.dry_run,
                skip_existing=not args.no_skip_existing,
                download_dir=Path(args.download_dir).expanduser(),
            ),
            log=print,
        )
    except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2
    print(f"Done. Report: {report.get('report_file', app_dir() / 'download_report.json')}")
    return 2 if report.get("failed") else 0


def main() -> int:
    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
