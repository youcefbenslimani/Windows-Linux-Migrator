#!/usr/bin/env python3
"""
Windows→Linux Migrator
Developer: DINAR
Contact: ossamabo@gmail.com
Support: PayPal: https://www.paypal.com/ncp/payment/3KJB6STH6VWTU
Version: 1.3.2
"""
import os
import sys
import subprocess
import winreg
import json
import logging
import time  # For simulating work in threads if needed
from datetime import datetime
from pathlib import Path
from PyQt6 import QtWidgets, QtCore, QtGui

# ----------------------
# Logging Configuration
# ----------------------

log_dir = Path.home() / "WinLinuxMigrator_logs"
log_dir.mkdir(exist_ok=True)
log_file = log_dir / f"migrator_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file, encoding='utf-8'),  # Specify encoding for log file
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("WinLinuxMigrator")


# ----------------------
# Windows Utilities
# ----------------------

def get_windows_user_profile(username=None):
    """Return path to C:\\Users\\<username>."""
    if username:
        profile = Path(f"C:/Users/{username}")
    else:
        # Attempt to get USERPROFILE, fall back to a common default or raise error
        userprofile_env = os.environ.get("USERPROFILE")
        if not userprofile_env:
            # Try to construct from HOMEDRIVE and HOMEPATH if USERPROFILE is not set
            homedrive = os.environ.get("HOMEDRIVE")
            homepath = os.environ.get("HOMEPATH")
            if homedrive and homepath:
                profile = Path(homedrive + homepath)
            else:  # Fallback or raise error if essential env vars are missing
                logger.warning("USERPROFILE environment variable not found. Trying C:/Users/Default.")
                profile = Path("C:/Users/Default")  # Or handle as an error
                if not username:  # if no username was provided and USERPROFILE is missing
                    raise FileNotFoundError(
                        "Cannot determine user profile path. USERPROFILE not set and no username provided.")
        else:
            profile = Path(userprofile_env)

    if not profile.exists():
        raise FileNotFoundError(f"Profile path {profile} not found.")
    logger.info(f"Using Windows profile: {profile}")
    return profile


def list_user_items_generator(profile_path):
    """Scan standard folders under user profile with existence check. Yields items."""
    std = ["Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos", "AppData",
           "Favorites", "Links", "Contacts", "Saved Games", "Searches"]
    for name in std:
        p = profile_path / name
        if p.exists():
            logger.debug(f"Found folder: {p}")
            yield p  # Yield path objects


def enum_installed_apps_generator():
    """Yield (name, install_location, version) from registry."""
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")
    ]
    app_count = 0
    for hive, sub in roots:
        try:
            key = winreg.OpenKey(hive, sub)
        except FileNotFoundError:
            continue
        # Using a loop that is safe for potential modifications during iteration (though less likely here)
        try:
            num_sub_keys = winreg.QueryInfoKey(key)[0]
        except OSError:  # Handle cases where the key might become invalid
            logger.warning(f"Could not query info for registry key: {hive}\\{sub}")
            continue

        for i in range(num_sub_keys):
            try:
                sk_name = winreg.EnumKey(key, i)
            except OSError:  # Handle cases where a subkey might disappear
                logger.warning(f"Could not enumerate subkey at index {i} for {hive}\\{sub}")
                continue

            try:
                with winreg.OpenKey(key, sk_name) as sk:
                    try:
                        name, _ = winreg.QueryValueEx(sk, "DisplayName")
                        # Skip system components or updates more reliably
                        system_component_flag, _ = winreg.QueryValueEx(sk, "SystemComponent") if "SystemComponent" in [
                            winreg.EnumValue(sk, j)[0] for j in range(winreg.QueryInfoKey(sk)[1])] else (0, None)
                        release_type, _ = winreg.QueryValueEx(sk, "ReleaseType") if "ReleaseType" in [
                            winreg.EnumValue(sk, j)[0] for j in range(winreg.QueryInfoKey(sk)[1])] else ("", None)
                        parent_key_name, _ = winreg.QueryValueEx(sk, "ParentKeyName") if "ParentKeyName" in [
                            winreg.EnumValue(sk, j)[0] for j in range(winreg.QueryInfoKey(sk)[1])] else ("", None)

                        if system_component_flag == 1:
                            # logger.debug(f"Skipping system component: {name}")
                            continue
                        if release_type and "Update" in release_type:  # e.g. "Security Update", "Hotfix"
                            # logger.debug(f"Skipping update/hotfix: {name}")
                            continue
                        if parent_key_name:  # Often indicates a component of another app
                            # logger.debug(f"Skipping child component: {name} (Parent: {parent_key_name})")
                            continue
                        # Heuristic for KB updates if not caught by SystemComponent or ReleaseType
                        if name.startswith("KB") and len(name) > 5 and name[2:8].isdigit():
                            # logger.debug(f"Skipping KB update (heuristic): {name}")
                            continue
                        # Common phrases for system/driver/redistributable packages that might not be user-facing apps
                        # but are often listed. We want to keep some (like VLC, Python itself) but filter out obscure ones.
                        filter_out_keywords = ["driver", "redistributable", "module", "package", "host adapter",
                                               "provider", "service pack"]
                        if any(keyword in name.lower() for keyword in
                               filter_out_keywords) and "InstallLocation" not in [winreg.EnumValue(sk, j)[0] for j in
                                                                                  range(winreg.QueryInfoKey(sk)[1])]:
                            # logger.debug(f"Skipping likely non-app (keyword based): {name}")
                            continue


                    except FileNotFoundError:  # DisplayName is mandatory for our interest
                        # logger.debug(f"Skipping entry without DisplayName: {sk_name}")
                        continue
                    except OSError:  # Handle issues reading specific values
                        logger.warning(f"OSError reading DisplayName or other attributes for {sk_name}")
                        continue

                    loc = ""
                    try:
                        loc, _ = winreg.QueryValueEx(sk, "InstallLocation")
                    except (FileNotFoundError, OSError):
                        loc = ""  # Not all apps have this, or it might be unreadable

                    version = ""
                    try:
                        version, _ = winreg.QueryValueEx(sk, "DisplayVersion")
                    except (FileNotFoundError, OSError):
                        version = ""

                    # Further filtering to avoid system entries without explicit InstallLocation
                    # or very generic names if they don't have a clear install path
                    system_folder = os.environ.get("SystemRoot", "C:\\Windows").lower()
                    program_files_folder = os.environ.get("ProgramFiles", "C:\\Program Files").lower()
                    program_files_x86_folder = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)").lower()

                    # If install location is deep within Windows system folders and not a known type of user app, skip
                    if loc and loc.lower().startswith(system_folder) and not (
                            loc.lower().startswith(program_files_folder) or
                            loc.lower().startswith(program_files_x86_folder) or
                            any(keep_keyword in name.lower() for keep_keyword in ["python", "git", "node.js"])
                    # Keep common dev tools
                    ):
                        # logger.debug(f"Skipping app in system folder: {name} at {loc}")
                        continue

                    # Skip entries with no name or very generic names if they also lack an install location
                    if not name or (name.lower() in ["windows", "microsoft windows", "host process"] and not loc):
                        # logger.debug(f"Skipping generic/no-name entry: {name}")
                        continue

                    app_count += 1
                    yield (name.strip(), loc.strip(), version.strip())
            except OSError:  # Error opening the subkey itself
                logger.warning(f"OSError opening subkey {sk_name} under {hive}\\{sub}")
                continue
    logger.info(f"Finished enumerating installed applications. Yielded: {app_count}")


# Windows apps map with their Linux equivalents
EQUIVALENTS = {
    "Microsoft Office": ["libreoffice", "onlyoffice-desktopeditors", "wps-office"],
    "Microsoft Word": ["libreoffice-writer", "onlyoffice-desktopeditors", "wps-office"],
    "Microsoft Excel": ["libreoffice-calc", "onlyoffice-desktopeditors", "wps-office"],
    "Microsoft PowerPoint": ["libreoffice-impress", "onlyoffice-desktopeditors", "wps-office"],
    "Microsoft Outlook": ["thunderbird", "evolution", "kmail"],
    "Adobe Photoshop": ["gimp", "krita", "photopea-desktop"],
    "Adobe Illustrator": ["inkscape", "vectr"],
    "Adobe Acrobat Reader": ["okular", "evince", "zathura", "xpdf"],
    "Adobe Acrobat Pro": ["masterpdfeditor-free", "okular", "evince", "pdfstudioviewer"],
    "Adobe Premiere Pro": ["kdenlive", "shotcut", "davinci-resolve", "olive-editor"],
    "Adobe After Effects": ["natron", "blender", "cavalry"],
    "Notepad++": ["kate", "gedit", "mousepad", "sublime-text", "vscode", "neovim"],
    "7-Zip": ["p7zip-full", "ark", "file-roller", "engrampa"],
    "WinRAR": ["unrar", "p7zip-full", "ark", "file-roller", "engrampa"],
    "VLC media player": ["vlc", "mpv", "smplayer", "celluloid"],
    "Google Chrome": ["google-chrome-stable", "chromium-browser", "brave-browser", "vivaldi"],
    "Mozilla Firefox": ["firefox", "librewolf", "waterfox"],
    "Microsoft Edge": ["microsoft-edge-stable", "chromium-browser"],
    "iTunes": ["rhythmbox", "strawberry", "clementine", "lollypop", "elisa"],
    "Spotify": ["spotify-client", "ncspot"],
    "Steam": ["steam-installer", "steam", "lutris"],
    "Visual Studio Code": ["code", "vscodium", "lapce"],
    "Visual Studio": ["monodevelop", "kdevelop", "eclipse", "rider", "netbeans"],
    "Slack": ["slack-desktop", "ferdi"],
    "Zoom": ["zoom", "jitsi-meet-desktop"],
    "Skype": ["skypeforlinux-stable-bin", "teams-for-linux"],
    "TeamViewer": ["teamviewer", "remmina", "anydesk"],
    "Discord": ["discord", "webcord"],
    "Telegram Desktop": ["telegram-desktop", "kotatogram-desktop"],
    "WhatsApp Desktop": ["whatsapp-for-linux", "ferdi", "franz"],
    "Lightshot": ["flameshot", "ksnip", "shutter", "spectacle"],
    "Paint.NET": ["pinta", "krita"],
    "Microsoft Paint": ["kolourpaint", "drawing", "gpaint", "mypaint"],
    "WinSCP": ["filezilla", "nautilus", "dolphin", "openssh-client"],
    "PuTTY": ["openssh-client", "kitty", "terminator", "tilix", "konsole", "gnome-terminal"],
    "FileZilla": ["filezilla", "lftp"],
    "μTorrent": ["qbittorrent", "transmission-gtk", "deluge", "ktorrent"],  # Corrected name
    "CCleaner": ["bleachbit", "stacer", "sweeper"],
    "Recuva": ["testdisk", "photorec", "r-studio"],
    "TeamSpeak": ["teamspeak3-client", "mumble"],
    "Windows Media Player": ["vlc", "mpv", "totem", "rhythmbox", "smplayer"],
    "Sublime Text": ["sublime-text", "sublime-merge"],
    "VMware Workstation Player": ["virtualbox", "qemu", "kvm", "vmware-workstation-player", "gnome-boxes"],
    # More specific
    "VMware Workstation Pro": ["virtualbox", "qemu", "kvm", "vmware-workstation-pro", "gnome-boxes"],  # More specific
    "Oracle VM VirtualBox": ["virtualbox", "qemu", "kvm", "gnome-boxes"],  # More specific
    "Docker Desktop": ["docker-ce", "podman", "rancher-desktop"],
    "Git": ["git", "git-cola", "gitkraken"],
    "Python": ["python3", "python"],  # More specific
    "Java Development Kit": ["openjdk", "oracle-jdk", "amazon-corretto"],
    "Node.js": ["nodejs", "nvm", "fnm"],
    "OBS Studio": ["obs-studio"],
    "Audacity": ["audacity", "ardour", "ocenaudio"],
    "Blender": ["blender"],
    "KeePassXC": ["keepassxc", "bitwarden-desktop", "gnome-keyring", "kwalletmanager"],
    "Bitwarden": ["bitwarden-desktop", "keepassxc"],
    "Dropbox": ["dropbox", "rclone", "nextcloud-client"],
    "Google Drive": ["google-drive-ocamlfuse", "rclone", "insync", "nextcloud-client"],
    "OneDrive": ["onedrive-abraunegg", "rclone", "insync", "nextcloud-client"],
    "GIMP": ["gimp"],
    "Inkscape": ["inkscape"],
    "Krita": ["krita"],
    "LibreOffice": ["libreoffice", "onlyoffice-desktopeditors"],
    "PowerISO": ["acetoneiso", "furiusisomount"],
    "WinZip": ["ark", "file-roller", "p7zip-full"],
    "CPU-Z": ["cpu-x", "hardinfo"],
    "HWiNFO": ["hardinfo", "hw-probe"],
    "Rufus": ["unetbootin", "balenaetcher", "ventoy"],
    "balenaEtcher": ["balenaetcher", "unetbootin", "ventoy"],
    "XnView MP": ["gwenview", "nomacs", "geeqie"],
    "IrfanView": ["gwenview", "nomacs", "geeqie"],
    "Foobar2000": ["strawberry", "deadbeef", "audacious"],
    "Winamp": ["audacious", "qmmp", "strawberry"],
    "AutoCAD": ["freecad", "librecad", "qcad", "bricscad-shape"],
    "SolidWorks": ["freecad", "openscad"],
    "MATLAB": ["octave", "scilab", "sagemath"],
    "Unity Hub": ["unityhub"],  # Often available as a direct download/script
    "Epic Games Launcher": ["lutris", "heroic-games-launcher"],
}


def suggest_equivalents(app_name):
    # Normalize app_name for better matching
    normalized_app_name = app_name.lower().replace("™", "").replace("®", "").replace("(tm)", "").replace("(r)",
                                                                                                         "").strip()

    # Exact match first
    for key, vals in EQUIVALENTS.items():
        if key.lower() == normalized_app_name:
            return vals

    # Partial match (more carefully)
    best_match_equivalents = []
    longest_match_len = 0

    for key, vals in EQUIVALENTS.items():
        # Check if the known app key is a substring of the installed app name
        if key.lower() in normalized_app_name:
            # Prefer longer matches as they are usually more specific
            if len(key) > longest_match_len:
                longest_match_len = len(key)
                best_match_equivalents = vals
            # If it's an equally long match, add to the list (less common)
            elif len(key) == longest_match_len and vals not in best_match_equivalents:
                best_match_equivalents.extend(v for v in vals if v not in best_match_equivalents)

    if best_match_equivalents:
        return best_match_equivalents

    # Try a more generic substring check if no good match yet
    # This is more prone to false positives but can catch variations
    for key, vals in EQUIVALENTS.items():
        # Split key into words and check if all words are in normalized_app_name
        key_words = key.lower().split()
        if all(word in normalized_app_name for word in key_words):
            # Avoid very short keys matching broadly, e.g. "Office" in "LibreOffice"
            if len(key_words) > 1 or len(key) > 6:  # Heuristic: require multiple words or a longer single word
                return vals

    return []


# ----------------------
# Worker Threads for Loading Data
# ----------------------
class LoadItemsThread(QtCore.QThread):
    item_discovered = QtCore.pyqtSignal(object)  # Path object
    loading_finished = QtCore.pyqtSignal(int, float)  # count, total_size
    loading_error = QtCore.pyqtSignal(str)

    def __init__(self, username=None, parent=None):
        super().__init__(parent)
        self.username = username
        self._is_running = True

    def run(self):
        count = 0
        total_size_bytes = 0
        try:
            profile_path = get_windows_user_profile(self.username)
            for item_path in list_user_items_generator(profile_path):
                if not self._is_running:
                    break
                self.item_discovered.emit(item_path)
                count += 1
                try:
                    if item_path.is_file():
                        total_size_bytes += item_path.stat().st_size
                    elif item_path.is_dir():
                        # Dir size calculation is deferred to avoid slowing down discovery
                        pass
                except Exception as e:
                    logger.warning(f"Could not get size for {item_path}: {e}")

                # self.msleep(10) # Optional: small delay to allow GUI updates if many items are found quickly
            if self._is_running:
                self.loading_finished.emit(count, total_size_bytes)
            else:
                logger.info("Item loading was stopped.")
                self.loading_finished.emit(count, total_size_bytes)  # Emit with current count

        except FileNotFoundError as e:
            logger.error(f"Error in LoadItemsThread (FileNotFound): {e}")
            self.loading_error.emit(str(e))
        except Exception as e:
            logger.error(f"Unexpected error in LoadItemsThread: {e}", exc_info=True)
            self.loading_error.emit(f"An unexpected error occurred while listing user items: {e}")

    def stop(self):
        self._is_running = False


class LoadAppsThread(QtCore.QThread):
    app_discovered = QtCore.pyqtSignal(str, str, str)  # name, location, version
    loading_finished = QtCore.pyqtSignal(int)  # count
    loading_error = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_running = True

    def run(self):
        count = 0
        try:
            for name, loc, version in enum_installed_apps_generator():
                if not self._is_running:
                    break
                self.app_discovered.emit(name, loc, version)
                count += 1
                # self.msleep(5) # Optional: small delay
            if self._is_running:
                self.loading_finished.emit(count)
            else:
                logger.info("App loading was stopped.")
                self.loading_finished.emit(count)

        except Exception as e:
            logger.error(f"Error in LoadAppsThread: {e}", exc_info=True)
            self.loading_error.emit(f"Failed to list installed applications: {e}")

    def stop(self):
        self._is_running = False


# ----------------------
# Script/Archive Generation
# ----------------------

def generate_shell_script(selected_items, selected_apps, copy_full, linux_user, distro_cmd, use_archive, archive_name,
                          preserve_permissions, copy_symlinks, fix_paths):
    lines = [
        "#!/bin/bash",
        "# ----------------------------------------",
        "# Windows→Linux Migration Script",
        "# Generated by Windows→Linux Migrator by DINAR",
        f"# Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "# This script is intended to be run on the target Linux system.",
        "# Review this script carefully before execution.",
        "# ----------------------------------------",
        "",
        "set -euo pipefail  # More robust error handling: exit on error, unset variable, or pipe failure",
        "trap 'echo \"ERROR: Migration script failed at line $LINENO. Last command was: $BASH_COMMAND\"; exit 1' ERR",
        "",
        "echo '-------------------------------------'",
        "echo 'Starting Windows→Linux migration process'",
        "echo '-------------------------------------'",
        "",
        "# Determine target Linux user and home directory",
        "if [ -z \"${linux_user}\" ]; then",  # Check if linux_user variable from Python is empty
        "  TARGET_LINUX_USER=\"$(whoami)\"",
        "  echo \"No specific Linux user provided, using current user: $TARGET_LINUX_USER\"",
        "else",
        "  TARGET_LINUX_USER=\"${linux_user}\"",
        "  echo \"Target Linux user specified: $TARGET_LINUX_USER\"",
        "fi",
        "",
        "if [ \"$TARGET_LINUX_USER\" == \"root\" ] && [ -z \"${linux_user}\" ]; then",
        # If running as root and no user specified
        "  read -p \"Running as root. Enter target non-root username for data migration (e.g., 'myuser'): \" INPUT_USER",
        "  if [ -z \"$INPUT_USER\" ]; then",
        "    echo \"ERROR: No target username provided when running as root. Aborting.\"",
        "    exit 1",
        "  fi",
        "  TARGET_LINUX_USER=\"$INPUT_USER\"",
        "fi",
        "",
        "TARGET_HOME=\"/home/$TARGET_LINUX_USER\"",
        "if [ ! -d \"$TARGET_HOME\" ] && [ \"$TARGET_LINUX_USER\" != \"root\" ]; then",
        # Check if home dir exists for non-root
        "   echo \"Warning: Target home directory $TARGET_HOME does not exist.\"",
        "   read -p \"Create $TARGET_HOME now? (yes/no): \" CREATE_HOME_CONFIRM",
        "   if [ \"$CREATE_HOME_CONFIRM\" == \"yes\" ]; then",
        "     if [ \"$(id -u)\" -eq 0 ]; then",  # If current script runner is root
        "       mkdir -p \"$TARGET_HOME\"",
        "       chown \"$TARGET_LINUX_USER:$TARGET_LINUX_USER\" \"$TARGET_HOME\" || echo \"Warning: Could not chown $TARGET_HOME\"",
        "       echo \"Created $TARGET_HOME.\"",
        "     else",
        "       echo \"Please create $TARGET_HOME manually or run this script as root to create it.\"",
        "       exit 1",
        "     fi",
        "   else",
        "     echo \"Aborting as target home directory does not exist.\"",
        "     exit 1",
        "   fi",
        "fi",
        "",
        "MIGRATED_DATA_DIR=\"$TARGET_HOME/migrated_windows_data\"",  # Standardized name
        "",
        "echo \"Target Linux user: $TARGET_LINUX_USER\"",
        "echo \"Target home directory: $TARGET_HOME\"",
        "echo \"Migrated data will be placed in: $MIGRATED_DATA_DIR\"",
        "",
        "SUDO_CMD=\"\"",
        "if [ \"$(id -u)\" -ne 0 ]; then",
        "  echo \"INFO: This script is not running as root. Sudo will be used for privileged operations.\"",
        "  if command -v sudo >/dev/null 2>&1; then",
        "    SUDO_CMD=\"sudo\"",
        "  else",
        "    echo \"WARNING: sudo command not found, but script is not root. Privileged operations might fail.\"",
        "  fi",
        "fi",
        "",
        "read -p \"Do you want to proceed with the migration to $TARGET_LINUX_USER's home? (yes/no): \" CONFIRM",
        "if [[ \"$CONFIRM\" != \"yes\" && \"$CONFIRM\" != \"YES\" && \"$CONFIRM\" != \"y\" ]]; then",
        "  echo \"Migration aborted by user.\"",
        "  exit 0",
        "fi",
        ""
    ]

    if use_archive:
        lines.append(f"echo '[1/3] Preparing to extract archive...'")
        lines.append(f"ARCHIVE_FILE_PATH=\"./{archive_name}\"")  # Assume archive is in current dir
        lines.append(f"if [ ! -f \"$ARCHIVE_FILE_PATH\" ]; then")
        lines.append(f"  echo \"ERROR: Archive '$ARCHIVE_FILE_PATH' not found in the current directory.\"")
        lines.append(f"  echo \"Please place it alongside this script or provide the full path if it's elsewhere.\"")
        lines.append(
            f"  read -e -p \"Enter full path to '{archive_name}' (or press Enter to abort): \" ARCHIVE_FILE_PATH_INPUT")
        lines.append(f"  if [ -z \"$ARCHIVE_FILE_PATH_INPUT\" ] || [ ! -f \"$ARCHIVE_FILE_PATH_INPUT\" ]; then")
        lines.append(f"    echo \"ERROR: Archive not found at specified path or input aborted. Aborting migration.\"")
        lines.append(f"    exit 1")
        lines.append(f"  fi")
        lines.append(f"  ARCHIVE_FILE_PATH=\"$ARCHIVE_FILE_PATH_INPUT\"")
        lines.append(f"fi")
        lines.append(f"echo \"Creating migration directory: $MIGRATED_DATA_DIR\"")
        lines.append(f"mkdir -p \"$MIGRATED_DATA_DIR\"")
        lines.append(f"echo \"Extracting archive '$ARCHIVE_FILE_PATH' to '$MIGRATED_DATA_DIR'...\"")
        lines.append(
            f"tar -xzvf \"$ARCHIVE_FILE_PATH\" -C \"$MIGRATED_DATA_DIR\" --strip-components=0 || {{ echo 'ERROR: Archive extraction failed.'; exit 1; }}")  # Adjust strip-components if needed based on how tar was created
        lines.append(f"echo \"Archive extracted successfully.\"")

    else:
        lines.append(f"echo '[1/3] Preparing for direct data copy (rsync)...'")
        lines.append(f"# This mode expects data to be pre-copied or mounted on the Linux system.")
        lines.append(
            f"SOURCE_BASE_DIR_ON_LINUX=\"./source_windows_data\" # Default: look for 'source_windows_data' in current directory")
        lines.append(f"echo \"Data will be copied from: $SOURCE_BASE_DIR_ON_LINUX (relative to script location)\"")
        lines.append(f"echo \"Target for copied data: $MIGRATED_DATA_DIR\"")
        lines.append(f"mkdir -p \"$MIGRATED_DATA_DIR\"")

        if selected_items:
            lines.append(f"echo \"Copying selected items from '$SOURCE_BASE_DIR_ON_LINUX' to '$MIGRATED_DATA_DIR'...\"")
            lines.append(f"if [ ! -d \"$SOURCE_BASE_DIR_ON_LINUX\" ]; then")
            lines.append(
                f"  echo \"ERROR: Source data directory '$SOURCE_BASE_DIR_ON_LINUX' not found. Please create it and place your Windows folders (Desktop, Documents, etc.) inside it.\"")
            lines.append(f"  exit 1")
            lines.append(f"fi")

            for i, src_path_obj in enumerate(selected_items):
                src_item_name = src_path_obj.name  # e.g., "Desktop", "Documents"
                src_on_linux = f"$SOURCE_BASE_DIR_ON_LINUX/{src_item_name}"  # e.g., ./source_windows_data/Desktop
                dst_on_linux = f"$MIGRATED_DATA_DIR/{src_item_name}"  # e.g., /home/user/migrated_windows_data/Desktop

                lines.append(f"echo \"Processing item {src_item_name} ({i + 1}/{len(selected_items)})\"")
                lines.append(f"if [ -e \"{src_on_linux}\" ]; then")  # Check if source item exists
                if copy_full and src_path_obj.is_dir():
                    lines.append(f"  echo \"Copying directory contents: '{src_on_linux}/' to '{dst_on_linux}/'\"")
                    lines.append(f"  mkdir -p \"{dst_on_linux}\"")
                    rsync_opts = "-a"  # Archive mode, preserves almost everything
                    if preserve_permissions:
                        rsync_opts += "XS"  # With ACLs and extended attributes
                    else:
                        rsync_opts = "-rltgoD"  # Recursive, links, times, group, owner, devices (no ACLs/Xattrs)

                    # Symlink handling: -a includes -l (copy symlinks as symlinks).
                    # If copy_symlinks is false, we might want to use -L (follow symlinks)
                    # However, rsync -a without -L is usually what's desired for symlinks.
                    # If user unchecks copy_symlinks, it implies they want to follow them.
                    if not copy_symlinks: rsync_opts = rsync_opts.replace('l',
                                                                          'L')  # Replace -l with -L if copy_symlinks is false

                    lines.append(
                        f"  $SUDO_CMD rsync {rsync_opts} --info=progress2 --no-inc-recursive \"{src_on_linux}/\" \"{dst_on_linux}/\" || echo \"Warning: rsync failed for {src_item_name}. Check permissions and paths.\"")
                elif src_path_obj.is_file():
                    lines.append(f"  echo \"Copying file: '{src_on_linux}' to '{dst_on_linux}'\"")
                    lines.append(f"  mkdir -p \"$(dirname \"{dst_on_linux}\")\"")
                    rsync_opts = "-aS" if preserve_permissions else "-rltgoD"  # Simplified for single file
                    if not copy_symlinks and os.path.islink(
                        src_path_obj): rsync_opts += "L"  # Follow if it was a symlink
                    lines.append(
                        f"  $SUDO_CMD rsync {rsync_opts} --info=progress2 \"{src_on_linux}\" \"{dst_on_linux}\" || echo \"Warning: rsync failed for {src_item_name}.\"")
                elif not copy_full and src_path_obj.is_dir():
                    lines.append(
                        f"  echo \"Creating directory structure for: '{dst_on_linux}' (full copy not selected)\"")
                    lines.append(f"  mkdir -p \"{dst_on_linux}\"")
                else:
                    lines.append(
                        f"  echo \"Skipping '{src_item_name}' (not a file or directory, or copy_full not selected for directory, or other issue).\"")
                lines.append(f"else")
                lines.append(
                    f"  echo \"Warning: Source '{src_on_linux}' not found. Please ensure it exists in '$SOURCE_BASE_DIR_ON_LINUX'. Skipping.\"")
                lines.append(f"fi")
        else:
            lines.append(
                "echo \"No specific items selected for direct copy. If you have data, ensure it's in '$MIGRATED_DATA_DIR' or '$SOURCE_BASE_DIR_ON_LINUX' for manual handling.\"")

    lines.append("")
    lines.append("echo '-------------------------------------'")
    lines.append("echo 'Setting final permissions for migrated data...'")
    lines.append(
        f"$SUDO_CMD chown -R \"$TARGET_LINUX_USER:$TARGET_LINUX_USER\" \"$MIGRATED_DATA_DIR\" || echo 'Warning: Failed to change ownership of migrated data. Please check permissions manually.'")
    lines.append(
        f"$SUDO_CMD chmod -R u+rwX,go=rX \"$MIGRATED_DATA_DIR\" || echo 'Warning: Failed to set basic permissions (u=rwX,go=rX) on migrated data.'")  # Set reasonable default permissions
    lines.append("echo 'Permissions set for migrated data directory.'")
    lines.append("")
    lines.append("echo '-------------------------------------'")

    if selected_apps:
        lines.append(f"echo '[2/3] Installing equivalent Linux applications...'")
        lines.append(f"echo 'Attempting to update package lists... (may require sudo password)'")

        # Determine package manager parts
        pm_parts = distro_cmd.split(' ')
        package_manager_command = pm_parts[0]  # e.g., apt, dnf, pacman
        update_subcommand = "update"
        install_subcommand_and_options = distro_cmd  # Default to full command

        if package_manager_command == "apt" or package_manager_command == "apt-get":
            install_subcommand_and_options = f"{package_manager_command} install -y"
        elif package_manager_command == "dnf" or package_manager_command == "yum":
            install_subcommand_and_options = f"{package_manager_command} install -y"
        elif package_manager_command == "pacman":
            update_subcommand = "-Syu"  # Full system update for pacman
            install_subcommand_and_options = f"{package_manager_command} -S --noconfirm --needed"  # --needed to avoid reinstalling
        elif package_manager_command == "zypper":
            install_subcommand_and_options = f"{package_manager_command} install -y"
            update_subcommand = "refresh"  # zypper refresh
        elif package_manager_command == "apk":
            install_subcommand_and_options = f"{package_manager_command} add"  # apk add -y might not be standard, -y is usually for specific commands
        else:  # Fallback for custom or less common managers
            install_subcommand_and_options = distro_cmd  # Use the user-provided string as is

        lines.append(
            f"$SUDO_CMD {package_manager_command} {update_subcommand} || echo 'Warning: Failed to update package lists. Application installations might fail or install older versions.'")

        for app_equivalent_list_str in selected_apps:
            apps_to_try = [a.strip() for a in app_equivalent_list_str.split(',') if a.strip() and a.strip() != "-"]
            if not apps_to_try:
                lines.append(
                    f"echo \"Skipping an application entry as no valid equivalents were provided: '{app_equivalent_list_str}'\"")
                continue

            lines.append(
                f"echo \"Attempting to install an equivalent for: (Original suggestions: {app_equivalent_list_str})\"")
            installed_one_for_this_set = False
            for app_pkg_name in apps_to_try:
                lines.append(f"  echo \"  Trying to install package: '{app_pkg_name}'...\"")
                # Try install, if successful, set flag and break from this inner loop
                lines.append(f"  if $SUDO_CMD {install_subcommand_and_options} \"{app_pkg_name}\"; then")
                lines.append(f"    echo \"  Successfully installed '{app_pkg_name}'.\"")
                lines.append(f"    installed_one_for_this_set=true")
                lines.append(f"    break")  # Move to the next set of equivalents
                lines.append(f"  else")
                lines.append(
                    f"    echo \"  Warning: Failed to install '{app_pkg_name}'. Trying next alternative if available.\"")
                lines.append(f"  fi")
            lines.append(f"  if [ \"$installed_one_for_this_set\" = false ]; then")
            lines.append(
                f"    echo \"  Warning: Could not install any of the suggested equivalents for this application set: {app_equivalent_list_str}. You may need to find and install them manually.\"")
            lines.append(f"  fi")
            lines.append(f"unset installed_one_for_this_set")  # Reset for next set
    else:
        lines.append(f"echo '[2/3] No applications selected for installation.'")

    lines.append("")
    lines.append("echo '-------------------------------------'")
    lines.append(f"echo '[3/3] Finalizing migration...'")

    if fix_paths:
        lines.append("echo 'Path fixing (experimental):'")
        lines.append("# This section is a placeholder for find/replace logic to update paths in config files.")
        lines.append("# It's highly dependent on the applications and data being migrated.")
        lines.append("# Example (very basic, use with extreme caution and test first):")
        lines.append(
            "# WIN_USER_PROFILE_NAME_GUESS=$(basename $(find \"$MIGRATED_DATA_DIR\" -maxdepth 1 -type d -name 'Desktop' -exec dirname {} \\; | head -n 1))")
        lines.append("# if [ -n \"$WIN_USER_PROFILE_NAME_GUESS\" ]; then")
        lines.append(
            "#   echo \"Attempting to replace C:/Users/$WIN_USER_PROFILE_NAME_GUESS with $MIGRATED_DATA_DIR/$WIN_USER_PROFILE_NAME_GUESS in text files...\"")
        lines.append(
            "#   find \"$MIGRATED_DATA_DIR\" -type f -print0 | xargs -0 $SUDO_CMD sed -i \"s|C:/Users/$WIN_USER_PROFILE_NAME_GUESS|$MIGRATED_DATA_DIR/$WIN_USER_PROFILE_NAME_GUESS|gI\"")
        lines.append(
            "#   find \"$MIGRATED_DATA_DIR\" -type f -print0 | xargs -0 $SUDO_CMD sed -i \"s|C:\\\\Users\\\\$WIN_USER_PROFILE_NAME_GUESS|$MIGRATED_DATA_DIR/$WIN_USER_PROFILE_NAME_GUESS|gI\"")
        lines.append("# else")
        lines.append("#   echo \"Could not guess Windows user profile name for path fixing. Skipping.\"")
        lines.append("# fi")
        lines.append(
            "echo 'Path fixing is complex and usually requires manual review. This step is currently a placeholder.'")

    lines.append(f"echo ''")
    lines.append(f"echo '----------------------------------------------------------------------'")
    lines.append(f"echo 'Migration process finished!'")
    lines.append(f"echo '----------------------------------------------------------------------'")
    lines.append(f"Your Windows data (if selected and copied/extracted) should be available at: $MIGRATED_DATA_DIR")
    lines.append(f"Installed applications (if selected) should be available in your Linux system's application menu.")
    lines.append(f"Please review the output above for any warnings or errors that may have occurred.")
    lines.append(
        f"It is recommended to reboot your Linux system if new applications or system libraries were installed.")
    lines.append("echo '----------------------------------------------------------------------'")

    return "\n".join(lines)


# ----------------------
# PyQt6 Interface
# ----------------------

class AboutDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About Win→Linux Migrator")
        self.setMinimumWidth(450)
        self.setStyleSheet("""
            QDialog { background-color: #f0f0f0; }
            QLabel { color: #333; }
            QPushButton { 
                background-color: #0078d4; color: white; 
                padding: 8px 15px; border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #005a9e; }
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setSpacing(15)

        title = QtWidgets.QLabel("Windows→Linux Migrator")
        title.setStyleSheet("font-size: 20pt; font-weight: bold; color: #0078d4;")
        title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        description = QtWidgets.QLabel(
            "A utility to help transfer data and identify application alternatives "
            "when migrating from Windows to Linux."
        )
        description.setWordWrap(True)
        description.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        description.setStyleSheet("font-size: 10pt;")

        dev_info = QtWidgets.QLabel()
        dev_info.setTextFormat(QtCore.Qt.TextFormat.RichText)
        dev_info.setText(
            "<div align='center'>"
            "<b>Developer:</b> DINAR<br>"
            "<b>Email:</b> <a href='mailto:ossamabo@gmail.com' style='color: #0078d4;'>ossamabo@gmail.com</a><br>"
            "<b>Version:</b> 1.3.2<br>"  # Updated version
            "<b>Support this project:</b> <a href='https://www.paypal.com/ncp/payment/3KJB6STH6VWTU' style='color: #0078d4;'>Donate via PayPal</a>"
            "</div>"
        )
        dev_info.setOpenExternalLinks(True)
        dev_info.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        dev_info.setStyleSheet("font-size: 10pt;")

        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.accept)

        layout.addWidget(title)
        layout.addSpacerItem(
            QtWidgets.QSpacerItem(20, 10, QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Expanding))
        layout.addWidget(description)
        layout.addSpacerItem(
            QtWidgets.QSpacerItem(20, 10, QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Expanding))
        layout.addWidget(dev_info)
        layout.addSpacerItem(
            QtWidgets.QSpacerItem(20, 20, QtWidgets.QSizePolicy.Policy.Minimum, QtWidgets.QSizePolicy.Policy.Expanding))
        layout.addWidget(close_btn, 0, QtCore.Qt.AlignmentFlag.AlignCenter)


class MigratorApp(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Windows→Linux Migrator")
        try:
            # Try to set a more generic icon if SP_DriveNetIcon is not available
            app_icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon)
            if app_icon.isNull():  # Fallback if even SP_ComputerIcon is not good
                app_icon = self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DesktopIcon)
            self.setWindowIcon(app_icon)
        except AttributeError:
            logger.warning("Standard icons for window not available for the current QStyle.")

        self.resize(1000, 750)  # Slightly increased height

        self.load_items_thread = LoadItemsThread()
        self.load_items_thread.item_discovered.connect(self.add_item_to_tree)
        self.load_items_thread.loading_finished.connect(self.items_loading_finished)
        self.load_items_thread.loading_error.connect(self.loading_error_occurred)

        self.load_apps_thread = LoadAppsThread()
        self.load_apps_thread.app_discovered.connect(self.add_app_to_list)
        self.load_apps_thread.loading_finished.connect(self.apps_loading_finished)
        self.load_apps_thread.loading_error.connect(self.loading_error_occurred)

        # Main Menu
        menu_bar = self.menuBar()

        # File Menu
        file_menu = menu_bar.addMenu("&File")  # Added ampersand for mnemonic

        save_action = QtGui.QAction(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogSaveButton),
                                    "&Save Settings", self)
        save_action.setShortcut(QtGui.QKeySequence.StandardKey.Save)
        save_action.setStatusTip("Save current selections and settings to a file.")
        save_action.triggered.connect(self.save_settings)
        file_menu.addAction(save_action)

        load_action = QtGui.QAction(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogOpenButton),
                                    "&Load Settings", self)
        load_action.setShortcut(QtGui.QKeySequence.StandardKey.Open)
        load_action.setStatusTip("Load previously saved selections and settings.")
        load_action.triggered.connect(self.load_settings)
        file_menu.addAction(load_action)

        file_menu.addSeparator()

        exit_action = QtGui.QAction(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogCloseButton),
                                    "E&xit", self)
        exit_action.setShortcut(QtGui.QKeySequence.StandardKey.Quit)
        exit_action.setStatusTip("Exit the application.")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # Help Menu
        help_menu = menu_bar.addMenu("&Help")

        about_action = QtGui.QAction(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxInformation), "&About", self)
        about_action.setStatusTip("Show information about this application.")
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

        # Main Widget and Layout
        central_widget = QtWidgets.QWidget()
        self.setCentralWidget(central_widget)
        layout = QtWidgets.QVBoxLayout(central_widget)
        layout.setContentsMargins(10, 10, 10, 10)  # Add some margins to main layout
        layout.setSpacing(10)  # Add spacing between widgets

        # Toolbar
        toolbar = QtWidgets.QToolBar("Main Tools")
        toolbar.setIconSize(QtCore.QSize(22, 22))  # Slightly smaller icons for toolbar
        self.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, toolbar)

        self.refresh_action = QtGui.QAction(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserReload),
                                            "Refresh All Data", self)
        self.refresh_action.setStatusTip("Reload user items and installed applications lists.")
        self.refresh_action.triggered.connect(self.refresh_all_data)
        toolbar.addAction(self.refresh_action)

        self.gen_action = QtGui.QAction(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogSaveButton),
                                        "Generate Script/Archive", self)
        self.gen_action.setStatusTip("Generate the migration script and/or data archive.")
        self.gen_action.triggered.connect(self.on_generate)
        toolbar.addAction(self.gen_action)

        toolbar.addSeparator()
        self.stop_action = QtGui.QAction(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MediaStop),
                                         "Stop Loading", self)
        self.stop_action.setStatusTip("Stop any ongoing data loading processes.")
        self.stop_action.triggered.connect(self.stop_loading_processes)
        self.stop_action.setEnabled(False)
        toolbar.addAction(self.stop_action)

        # Header Section
        header_frame = QtWidgets.QFrame()
        header_frame.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        header_frame.setStyleSheet(
            ".QFrame { background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #e9eff5, stop:1 #d0d9e0); border-radius: 5px; padding: 10px; }")  # Lighter gradient
        header_layout = QtWidgets.QHBoxLayout(header_frame)

        logo_label = QtWidgets.QLabel("W→L")
        logo_label.setStyleSheet(
            "font-size: 30pt; font-weight: bold; color: #005a9e; padding-right: 15px;")  # Adjusted color
        header_layout.addWidget(logo_label)

        info_layout = QtWidgets.QVBoxLayout()
        title_label = QtWidgets.QLabel("Windows→Linux Migrator")
        title_label.setStyleSheet("font-size: 18pt; font-weight: bold; color: #2c3e50;")  # Darker blue/grey
        subtitle_label = QtWidgets.QLabel("Utility to ease the transition from Windows to Linux")
        subtitle_label.setStyleSheet("font-size: 10pt; color: #555;")
        info_layout.addWidget(title_label)
        info_layout.addWidget(subtitle_label)
        header_layout.addLayout(info_layout)
        header_layout.addStretch()

        donate_btn = QtWidgets.QPushButton(
            self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogApplyButton),
            "Support Project")  # Using a more generic icon
        donate_btn.setStyleSheet(
            "background-color: #28a745; color: white; padding: 8px 12px; border-radius: 4px; font-weight: bold;")
        donate_btn.setToolTip("Support the developer via PayPal")
        donate_btn.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl("https://www.paypal.com/ncp/payment/3KJB6STH6VWTU")
        ))
        header_layout.addWidget(donate_btn)

        layout.addWidget(header_frame)

        # Tab Widget
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setStyleSheet(
            "QTabBar::tab { padding: 10px 25px; font-size: 10pt; font-weight: bold; }")  # More padding, bold text
        layout.addWidget(self.tabs)

        # Files Tab
        files_tab = QtWidgets.QWidget()
        files_layout = QtWidgets.QVBoxLayout(files_tab)
        files_layout.setSpacing(8)

        user_frame = QtWidgets.QFrame()
        user_frame.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        user_frame_layout = QtWidgets.QHBoxLayout(user_frame)
        user_frame_layout.addWidget(QtWidgets.QLabel("<b>Windows Username:</b>"))
        self.user_edit = QtWidgets.QLineEdit()
        self.user_edit.setToolTip("Enter the Windows username whose profile folders you want to scan.")
        try:
            login_name = os.getlogin()
            self.user_edit.setText(login_name)
        except Exception as e:
            logger.warning(f"Could not get login name: {e}. Checking USERNAME env var.")
            username_env = os.environ.get("USERNAME")
            if username_env:
                self.user_edit.setText(username_env)
            else:
                self.user_edit.setPlaceholderText("Enter Windows username")
        user_frame_layout.addWidget(self.user_edit)

        self.btn_refresh_files = QtWidgets.QPushButton("Refresh File List")
        self.btn_refresh_files.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserReload))
        self.btn_refresh_files.setToolTip("Scan for standard user folders (Desktop, Documents, etc.).")
        self.btn_refresh_files.clicked.connect(self.load_items)
        user_frame_layout.addWidget(self.btn_refresh_files)

        files_layout.addWidget(user_frame)

        # File selection buttons in a separate HBox for better layout
        file_selection_buttons_layout = QtWidgets.QHBoxLayout()
        self.select_all_files_btn = QtWidgets.QPushButton("Select All Files")
        self.select_all_files_btn.clicked.connect(lambda: self.toggle_selection(self.tree, True))
        file_selection_buttons_layout.addWidget(self.select_all_files_btn)

        self.deselect_all_files_btn = QtWidgets.QPushButton("Deselect All Files")
        self.deselect_all_files_btn.clicked.connect(lambda: self.toggle_selection(self.tree, False))
        file_selection_buttons_layout.addWidget(self.deselect_all_files_btn)
        file_selection_buttons_layout.addStretch()  # Push buttons to left
        files_layout.addLayout(file_selection_buttons_layout)

        file_label = QtWidgets.QLabel("<b>Select folders and files to migrate:</b>")
        files_layout.addWidget(file_label)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Select", "Path", "Size (Est.)"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnWidth(0, 60)
        self.tree.setColumnWidth(1, 550)
        self.tree.header().setStretchLastSection(True)
        self.tree.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)  # Allow multi-select with Shift/Ctrl
        files_layout.addWidget(self.tree)

        self.tabs.addTab(files_tab, "Files & Folders")

        # Applications Tab
        apps_tab = QtWidgets.QWidget()
        apps_layout = QtWidgets.QVBoxLayout(apps_tab)
        apps_layout.setSpacing(8)

        apps_controls_frame = QtWidgets.QFrame()
        apps_controls_frame.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        apps_controls_layout = QtWidgets.QHBoxLayout(apps_controls_frame)
        apps_controls_layout.addWidget(QtWidgets.QLabel("<b>Search Apps:</b>"))
        self.app_search = QtWidgets.QLineEdit()
        self.app_search.setPlaceholderText("Filter installed applications...")
        self.app_search.setToolTip("Type to filter the list of installed Windows applications.")
        self.app_search.textChanged.connect(self.filter_apps)
        apps_controls_layout.addWidget(self.app_search)

        self.btn_refresh_apps = QtWidgets.QPushButton("Refresh App List")
        self.btn_refresh_apps.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_BrowserReload))
        self.btn_refresh_apps.setToolTip("Scan the registry for installed applications.")
        self.btn_refresh_apps.clicked.connect(self.load_apps)
        apps_controls_layout.addWidget(self.btn_refresh_apps)
        apps_layout.addWidget(apps_controls_frame)

        # App selection buttons
        app_selection_buttons_layout = QtWidgets.QHBoxLayout()
        self.select_all_apps_btn = QtWidgets.QPushButton("Select All Apps")
        self.select_all_apps_btn.clicked.connect(lambda: self.toggle_selection(self.prog_list, True))
        app_selection_buttons_layout.addWidget(self.select_all_apps_btn)

        self.deselect_all_apps_btn = QtWidgets.QPushButton("Deselect All Apps")
        self.deselect_all_apps_btn.clicked.connect(lambda: self.toggle_selection(self.prog_list, False))
        app_selection_buttons_layout.addWidget(self.deselect_all_apps_btn)
        app_selection_buttons_layout.addStretch()
        apps_layout.addLayout(app_selection_buttons_layout)

        app_label = QtWidgets.QLabel("<b>Select applications to find Linux equivalents for:</b>")
        apps_layout.addWidget(app_label)

        self.prog_list = QtWidgets.QTreeWidget()
        self.prog_list.setHeaderLabels(["Select", "Windows Application", "Version", "Suggested Linux Equivalents"])
        self.prog_list.setAlternatingRowColors(True)
        self.prog_list.setColumnWidth(0, 60)
        self.prog_list.setColumnWidth(1, 280)
        self.prog_list.setColumnWidth(2, 80)
        self.prog_list.header().setStretchLastSection(True)
        self.prog_list.setSortingEnabled(True)
        self.prog_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        apps_layout.addWidget(self.prog_list)

        self.tabs.addTab(apps_tab, "Applications")

        # Export Settings Tab
        settings_tab = QtWidgets.QWidget()
        settings_layout = QtWidgets.QVBoxLayout(settings_tab)
        settings_layout.setSpacing(10)

        settings_group = QtWidgets.QGroupBox("Migration Script & Archive Settings")
        settings_group_layout = QtWidgets.QFormLayout(settings_group)
        settings_group_layout.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapAllRows)  # Better for responsive
        settings_group_layout.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        self.chk_full = QtWidgets.QCheckBox("Copy full folder contents (for direct copy mode)")
        self.chk_full.setChecked(True)
        self.chk_full.setToolTip(
            "If checked, the generated script will attempt to copy entire contents of selected folders.\nOtherwise, it might only create the directory structure (assuming manual content transfer).")
        settings_group_layout.addRow(self.chk_full)

        self.chk_archive = QtWidgets.QCheckBox("Create a compressed archive (.tar.gz) of selected files")
        self.chk_archive.setToolTip(
            "Creates a single archive file containing all selected files and folders for easier transfer.")
        settings_group_layout.addRow(self.chk_archive)

        self.archive_name = QtWidgets.QLineEdit("win_migrator_backup.tar.gz")  # Changed default name
        self.archive_name.setToolTip("Filename for the compressed archive.")
        settings_group_layout.addRow(QtWidgets.QLabel("Archive Filename:"), self.archive_name)
        self.archive_name.setEnabled(self.chk_archive.isChecked())
        self.chk_archive.toggled.connect(self.archive_name.setEnabled)

        self.cmd_edit = QtWidgets.QComboBox()
        self.cmd_edit.addItems(["apt", "apt-get", "dnf", "yum", "pacman -S", "zypper install", "apk add"])
        self.cmd_edit.setEditable(True)
        self.cmd_edit.setToolTip(
            "Package manager command for the target Linux distribution (e.g., apt, dnf, pacman -S).")
        settings_group_layout.addRow(QtWidgets.QLabel("Linux Package Install Command:"), self.cmd_edit)

        linux_user_default = ""
        try:
            if os.name != 'nt':
                linux_user_default = os.getlogin()
        except Exception:
            pass
        self.linux_user = QtWidgets.QLineEdit(linux_user_default)
        self.linux_user.setPlaceholderText("Target Linux username (leave blank for current user on Linux)")
        self.linux_user.setToolTip(
            "The username on the Linux system where data will be migrated.\nIf blank, the script will try to use the current Linux user.")
        settings_group_layout.addRow(QtWidgets.QLabel("Target Linux Username:"), self.linux_user)

        settings_layout.addWidget(settings_group)

        advanced_group = QtWidgets.QGroupBox("Advanced Script Options (for rsync/direct copy mode)")
        advanced_layout = QtWidgets.QFormLayout(advanced_group)
        advanced_layout.setRowWrapPolicy(QtWidgets.QFormLayout.RowWrapPolicy.WrapAllRows)
        advanced_layout.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        self.chk_permissions = QtWidgets.QCheckBox("Preserve original permissions & timestamps (rsync -aXS)")
        self.chk_permissions.setChecked(True)
        self.chk_permissions.setToolTip(
            "Attempts to preserve file permissions, ownership, and timestamps when copying with rsync.")
        advanced_layout.addRow(self.chk_permissions)

        self.chk_symlinks = QtWidgets.QCheckBox("Copy symbolic links as links (rsync -l)")
        self.chk_symlinks.setChecked(True)
        self.chk_symlinks.setToolTip(
            "If checked, symbolic links are copied as links rather than copying the files they point to.")
        advanced_layout.addRow(self.chk_symlinks)

        self.chk_fix_paths = QtWidgets.QCheckBox("Attempt to fix common paths in script (Experimental)")
        self.chk_fix_paths.setChecked(False)
        self.chk_fix_paths.setToolTip(
            "Adds experimental commands to the script to replace common Windows paths with Linux equivalents.\nThis may require manual adjustment.")
        advanced_layout.addRow(self.chk_fix_paths)

        settings_layout.addWidget(advanced_group)
        settings_layout.addStretch()

        self.tabs.addTab(settings_tab, "Export Settings")

        # Action Bar (Generate button, progress)
        action_frame = QtWidgets.QFrame()
        action_frame.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        action_layout = QtWidgets.QHBoxLayout(action_frame)

        self.progress_label = QtWidgets.QLabel("Ready.")
        action_layout.addWidget(self.progress_label)
        action_layout.addStretch(1)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setMinimumWidth(250)  # Wider progress bar
        action_layout.addWidget(self.progress_bar)
        action_layout.addStretch(1)

        self.btn_gen = QtWidgets.QPushButton("Generate Script / Archive")
        self.btn_gen.setIcon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_DialogSaveButton))
        self.btn_gen.setStyleSheet(
            "background-color: #0078d4; color: white; font-weight: bold; padding: 10px 15px; border-radius: 4px;")
        self.btn_gen.setToolTip("Start the generation process based on current selections and settings.")
        self.btn_gen.clicked.connect(self.on_generate)
        action_layout.addWidget(self.btn_gen)

        layout.addWidget(action_frame)

        # Status Bar
        self.status_bar = self.statusBar()
        self.status_bar.showMessage("Ready")

        # Initial data load
        self.refresh_all_data()

        # Show welcome message after a short delay
        QtCore.QTimer.singleShot(600, self.show_welcome)

    def show_welcome(self):
        QtWidgets.QMessageBox.information(self,
                                          "Welcome to Windows→Linux Migrator",
                                          "This tool helps you transfer data and find application alternatives when moving from Windows to Linux.\n\n"
                                          "<b>Quick Start:</b>\n"
                                          "1. Verify your Windows username, then refresh the file list.\n"
                                          "2. Refresh the list of installed applications.\n"
                                          "3. Select items and applications you wish to migrate or find equivalents for.\n"
                                          "4. Adjust export settings as needed.\n"
                                          "5. Click 'Generate Script / Archive'."
                                          )

    def show_about(self):
        dialog = AboutDialog(self)
        dialog.exec()

    def filter_apps(self):
        search_text = self.app_search.text().lower()
        for i in range(self.prog_list.topLevelItemCount()):
            item = self.prog_list.topLevelItem(i)
            app_name = item.text(1).lower()
            equivalents = item.text(3).lower()
            visible = search_text in app_name or search_text in equivalents
            item.setHidden(not visible)

    def toggle_selection(self, tree_widget, select):
        state = QtCore.Qt.CheckState.Checked if select else QtCore.Qt.CheckState.Unchecked
        iterator = QtWidgets.QTreeWidgetItemIterator(tree_widget,
                                                     QtWidgets.QTreeWidgetItemIterator.IteratorFlag.NotHidden)  # Iterate only visible items
        while iterator.value():
            item = iterator.value()
            item.setCheckState(0, state)
            iterator += 1

    def refresh_all_data(self):
        if self.load_items_thread.isRunning() or self.load_apps_thread.isRunning():
            QtWidgets.QMessageBox.information(self, "In Progress",
                                              "A data loading process is already running. Please wait or stop it first.")
            return
        self.load_items()
        # Apps loading will be chained after items if desired, or run in parallel. For now, parallel.
        self.load_apps()

    def _set_loading_state(self, loading, process_name="data"):
        is_any_thread_running = self.load_items_thread.isRunning() or self.load_apps_thread.isRunning()

        self.btn_refresh_files.setEnabled(not is_any_thread_running)
        self.btn_refresh_apps.setEnabled(not is_any_thread_running)
        self.refresh_action.setEnabled(not is_any_thread_running)
        self.gen_action.setEnabled(not is_any_thread_running)
        self.btn_gen.setEnabled(not is_any_thread_running)
        self.tabs.setEnabled(not is_any_thread_running)  # Disable tabs during any loading

        self.stop_action.setEnabled(is_any_thread_running)

        if loading:  # This specific process is starting
            self.progress_bar.setVisible(True)
            self.progress_bar.setRange(0, 0)
            self.progress_label.setText(f"Loading {process_name}...")
        elif not is_any_thread_running:  # All processes finished
            self.progress_bar.setVisible(False)
            self.progress_bar.setRange(0, 100)
            # The label will be set by the specific finished slot
        # If one process finished but another is still running, progress bar remains visible & indeterminate

    def load_items(self):
        if self.load_items_thread.isRunning():
            # This check is also in refresh_all_data, but good for direct calls too
            QtWidgets.QMessageBox.information(self, "In Progress", "File list loading is already in progress.")
            return

        self.tree.clear()
        self.status_bar.showMessage("Loading user items...")
        self._set_loading_state(True, "user items")

        self.load_items_thread.username = self.user_edit.text()
        self.load_items_thread.start()

    def add_item_to_tree(self, item_path: Path):
        it = QtWidgets.QTreeWidgetItem(self.tree)
        it.setCheckState(0, QtCore.Qt.CheckState.Checked)  # Default to checked
        it.setText(1, str(item_path))
        it.setData(1, QtCore.Qt.ItemDataRole.UserRole, item_path)
        it.setToolTip(1, str(item_path))  # Tooltip for full path if truncated

        size_str = "-"
        try:
            if item_path.is_dir():
                size_str = "Folder (size calculated on generation)"
            elif item_path.is_file():
                size_str = self.format_size(item_path.stat().st_size)
            it.setText(2, size_str)
        except Exception as e:
            logger.warning(f"Could not get size for {item_path} during initial load: {e}")
            it.setText(2, "Size error")

        self.tree.scrollToItem(it, QtWidgets.QAbstractItemView.ScrollHint.EnsureVisible)

    def items_loading_finished(self, count, total_size_bytes):
        self.status_bar.showMessage(
            f"Loaded {count} user items. Approx. file size: {self.format_size(total_size_bytes)}")
        self.progress_label.setText(f"User items loading complete ({count} items).")

        if hasattr(self, '_saved_folders_to_select') and self._saved_folders_to_select:
            selected_paths_str = self._saved_folders_to_select
            iterator = QtWidgets.QTreeWidgetItemIterator(self.tree)
            while iterator.value():
                item = iterator.value()
                path_data = item.data(1, QtCore.Qt.ItemDataRole.UserRole)
                if path_data and str(path_data) in selected_paths_str:
                    item.setCheckState(0, QtCore.Qt.CheckState.Checked)
                else:  # If loading settings, uncheck items not in the saved list
                    item.setCheckState(0, QtCore.Qt.CheckState.Unchecked)
            self._saved_folders_to_select = []

        self._set_loading_state(False)  # Update global loading state
        if count == 0 and not self.user_edit.text():
            QtWidgets.QMessageBox.warning(self, "Input Needed",
                                          "Windows username is not entered. Please enter it and refresh the file list.")
        elif count == 0 and self.user_edit.text():
            QtWidgets.QMessageBox.information(self, "No Items Found",
                                              f"No standard user folders found for user '{self.user_edit.text()}'.\nPlease check the username or ensure the folders (Desktop, Documents, etc.) exist.")

    def format_size(self, size_bytes):
        if not isinstance(size_bytes, (int, float)) or size_bytes < 0: return "N/A"
        if size_bytes == 0: return "0 B"
        size_name = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
        i = 0
        # Ensure size_bytes is float for division
        size_bytes_float = float(size_bytes)
        while size_bytes_float >= 1024 and i < len(size_name) - 1:
            size_bytes_float /= 1024.0
            i += 1
        # Format to 1 decimal place for KB and above, 0 for Bytes.
        f = '%.1f' % size_bytes_float if i > 0 else '%.0f' % size_bytes_float
        return '%s %s' % (f, size_name[i])

    def load_apps(self):
        if self.load_apps_thread.isRunning():
            QtWidgets.QMessageBox.information(self, "In Progress", "Application list loading is already in progress.")
            return

        self.prog_list.clear()
        self.status_bar.showMessage("Loading installed applications...")
        self._set_loading_state(True, "applications")
        self.load_apps_thread.start()

    def add_app_to_list(self, name, loc, version):
        eq = suggest_equivalents(name)
        it = QtWidgets.QTreeWidgetItem(self.prog_list)
        it.setCheckState(0, QtCore.Qt.CheckState.Unchecked)
        it.setText(1, name)
        it.setToolTip(1, f"Name: {name}\nLocation: {loc if loc else 'N/A'}\nVersion: {version if version else 'N/A'}")
        it.setText(2, version if version else "-")
        it.setText(3, ", ".join(eq) if eq else "-")
        it.setData(1, QtCore.Qt.ItemDataRole.UserRole, name)

        if eq:
            it.setBackground(3, QtGui.QColor(220, 255, 220))  # Lighter green
        else:
            it.setBackground(3, QtGui.QColor(255, 220, 220))  # Lighter red

        self.prog_list.scrollToItem(it, QtWidgets.QAbstractItemView.ScrollHint.EnsureVisible)

    def apps_loading_finished(self, count):
        self.prog_list.sortItems(1, QtCore.Qt.SortOrder.AscendingOrder)
        self.status_bar.showMessage(f"Loaded {count} applications.")
        self.progress_label.setText(f"Application loading complete ({count} apps).")

        if hasattr(self, '_saved_apps_to_select') and self._saved_apps_to_select:
            selected_app_names = self._saved_apps_to_select
            iterator = QtWidgets.QTreeWidgetItemIterator(self.prog_list)
            while iterator.value():
                item = iterator.value()
                app_name_in_list = item.text(1)
                if app_name_in_list in selected_app_names:
                    item.setCheckState(0, QtCore.Qt.CheckState.Checked)
                else:  # If loading settings, uncheck items not in the saved list
                    item.setCheckState(0, QtCore.Qt.CheckState.Unchecked)
            self._saved_apps_to_select = []

        self._set_loading_state(False)  # Update global loading state
        if count == 0:
            QtWidgets.QMessageBox.information(self, "No Applications Found",
                                              "No installed applications were found, or all entries were filtered as system components.")

    def loading_error_occurred(self, error_message):
        QtWidgets.QMessageBox.critical(self, "Loading Error", error_message)
        self.status_bar.showMessage(f"Loading failed: {error_message}")
        self.progress_label.setText("Loading failed.")
        self._set_loading_state(False)

    def stop_loading_processes(self):
        stopped_items = False
        stopped_apps = False
        if self.load_items_thread.isRunning():
            self.load_items_thread.stop()
            logger.info("Requested to stop item loading thread.")
            stopped_items = True
        if self.load_apps_thread.isRunning():
            self.load_apps_thread.stop()
            logger.info("Requested to stop app loading thread.")
            stopped_apps = True

        if stopped_items or stopped_apps:
            self.status_bar.showMessage("Stop request sent to loading processes.")
            self.progress_label.setText("Stopping loading...")
            # The _set_loading_state(False) will be called when threads actually finish (emit finished signal)
        else:
            self.status_bar.showMessage("No loading processes were active.")

    def save_settings(self):
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Settings", str(Path.home() / "WinLinuxMigrator_settings.json"), "JSON Files (*.json)"
        )
        if not filename:
            return

        settings = {
            "version": "1.3.2",
            "windows_user": self.user_edit.text(),
            "linux_user": self.linux_user.text(),
            "package_manager": self.cmd_edit.currentText(),
            "copy_full_folders": self.chk_full.isChecked(),
            "create_archive": self.chk_archive.isChecked(),
            "archive_name": self.archive_name.text(),
            "script_preserve_permissions": self.chk_permissions.isChecked(),
            "script_copy_symlinks": self.chk_symlinks.isChecked(),
            "script_fix_paths": self.chk_fix_paths.isChecked(),
            "selected_folders_paths": [],
            "selected_apps_details": []
        }

        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.checkState(0) == QtCore.Qt.CheckState.Checked:
                path_data = item.data(1, QtCore.Qt.ItemDataRole.UserRole)
                if path_data:
                    settings["selected_folders_paths"].append(str(path_data))

        for i in range(self.prog_list.topLevelItemCount()):
            item = self.prog_list.topLevelItem(i)
            if item.checkState(0) == QtCore.Qt.CheckState.Checked:
                settings["selected_apps_details"].append({
                    "name": item.text(1),
                    "version": item.text(2),
                    "linux_equivalents_suggestion": item.text(3)
                })

        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=4, ensure_ascii=False)
            self.status_bar.showMessage(f"Settings saved to {filename}")
            QtWidgets.QMessageBox.information(self, "Settings Saved", f"Settings successfully saved to:\n{filename}")

        except Exception as e:
            logger.error(f"Failed to save settings: {e}", exc_info=True)
            QtWidgets.QMessageBox.critical(self, "Error Saving Settings", f"Failed to save settings: {e}")

    def load_settings(self):
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Settings", str(Path.home()), "JSON Files (*.json)"
        )
        if not filename:
            return

        try:
            with open(filename, 'r', encoding='utf-8') as f:
                settings = json.load(f)

            self.user_edit.setText(settings.get("windows_user", ""))
            self.linux_user.setText(settings.get("linux_user", ""))

            pm = settings.get("package_manager", "apt")
            index = self.cmd_edit.findText(pm)
            if index >= 0:
                self.cmd_edit.setCurrentIndex(index)
            else:
                self.cmd_edit.setCurrentText(pm)

            self.chk_full.setChecked(settings.get("copy_full_folders", True))
            self.chk_archive.setChecked(settings.get("create_archive", False))
            self.archive_name.setText(settings.get("archive_name", "win_migrator_backup.tar.gz"))
            self.chk_permissions.setChecked(settings.get("script_preserve_permissions", True))
            self.chk_symlinks.setChecked(settings.get("script_copy_symlinks", True))
            self.chk_fix_paths.setChecked(settings.get("script_fix_paths", False))

            # Store selections to apply after lists are populated by refresh_all_data
            self._saved_folders_to_select = settings.get("selected_folders_paths", [])
            self._saved_apps_to_select = [app.get("name") for app in settings.get("selected_apps_details", []) if
                                          app.get("name")]  # Ensure name exists

            self.status_bar.showMessage(f"Settings loaded from {filename}. Refreshing lists...")
            QtWidgets.QMessageBox.information(self, "Settings Loaded", f"Settings loaded from:\n{filename}\n"
                                                                       "File and application lists will now be refreshed, and saved selections will be applied.")
            self.refresh_all_data()  # This will trigger loading and the finished slots will apply selections

        except Exception as e:
            logger.error(f"Failed to load settings: {e}", exc_info=True)
            QtWidgets.QMessageBox.critical(self, "Error Loading Settings", f"Failed to load settings: {e}")
            self._saved_folders_to_select = []
            self._saved_apps_to_select = []

    def on_generate(self):
        sel_items_paths = []
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.checkState(0) == QtCore.Qt.CheckState.Checked:
                path_data = it.data(1, QtCore.Qt.ItemDataRole.UserRole)
                if path_data:
                    sel_items_paths.append(path_data)

        sel_apps_equivalents = []
        for i in range(self.prog_list.topLevelItemCount()):
            it = self.prog_list.topLevelItem(i)
            if it.checkState(0) == QtCore.Qt.CheckState.Checked:
                eq_str = it.text(3)
                if eq_str and eq_str != "-":
                    sel_apps_equivalents.append(eq_str)

        if not sel_items_paths and not sel_apps_equivalents:
            QtWidgets.QMessageBox.warning(self, "No Selection",
                                          "No items are selected for migration, and no applications are selected for equivalent installation.")
            return

        use_archive = self.chk_archive.isChecked()
        archive_name_val = self.archive_name.text().strip()

        if use_archive and not sel_items_paths:
            QtWidgets.QMessageBox.warning(self, "Archive Error",
                                          "Please select some files or folders to include in the archive, or uncheck the archive option.")
            return

        if use_archive and not archive_name_val:
            QtWidgets.QMessageBox.warning(self, "Archive Name Missing", "Please enter a filename for the archive.")
            self.archive_name.setFocus()
            return

        linux_username_target = self.linux_user.text().strip()
        if not linux_username_target and (sel_apps_equivalents or sel_items_paths):
            reply = QtWidgets.QMessageBox.question(self, "Confirm Linux Username",
                                                   "Target Linux username is not specified. The script will default to the user running it on Linux.\n"
                                                   "Is this okay? (Data ownership might need manual adjustment if script is run as root).",
                                                   QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
            if reply == QtWidgets.QMessageBox.StandardButton.No:
                self.linux_user.setFocus()
                return

        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.status_bar.showMessage("Generating script and/or archive...")
        self.progress_label.setText("Processing...")
        QtWidgets.QApplication.processEvents()  # Ensure UI updates

        actual_archive_filename_for_script = archive_name_val  # This might change if user saves archive with different name

        if use_archive and sel_items_paths:
            # Ask user where to save the archive file itself
            archive_save_dialog_path = Path.home() / archive_name_val
            archive_output_path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save Archive As", str(archive_save_dialog_path), "Tar GZ Archives (*.tar.gz);;All Files (*)"
            )
            if not archive_output_path:
                self.status_bar.showMessage("Archive creation cancelled by user.")
                self.progress_label.setText("Cancelled.")
                self.progress_bar.setVisible(False)
                return

            actual_archive_filename_for_script = Path(archive_output_path).name

            self.progress_label.setText(f"Creating archive: {actual_archive_filename_for_script}...")
            QtWidgets.QApplication.processEvents()
            try:
                profile_dir_str = ""
                try:
                    # Use the currently entered username in the text field for profile path context
                    current_win_user = self.user_edit.text().strip()
                    if not current_win_user:
                        raise FileNotFoundError(
                            "Windows username field is empty, cannot determine profile path for archiving.")
                    profile_dir_str = str(get_windows_user_profile(current_win_user))
                except FileNotFoundError as e_prof:
                    QtWidgets.QMessageBox.warning(self, "Profile Path Warning",
                                                  f"Could not determine Windows profile path for '{current_win_user}': {e_prof}.\nArchive may contain absolute paths or fail if paths are not accessible.")
                    # Decide if to proceed or abort. For now, let it try with absolute paths if profile_dir_str is empty.

                cmd = ["tar", "-czvf", archive_output_path]
                # Check if all selected items are within the profile directory for relative path archiving
                can_use_relative_tar = False
                if profile_dir_str:
                    try:
                        # Ensure profile_dir_str is an absolute path before making items relative to it
                        profile_path_obj = Path(profile_dir_str)
                        if profile_path_obj.is_absolute():
                            can_use_relative_tar = all(str(p).startswith(profile_dir_str) for p in sel_items_paths)
                    except Exception as e_path_check:
                        logger.warning(f"Error checking paths for relative tar: {e_path_check}")

                if can_use_relative_tar:
                    cmd.append("-C")
                    cmd.append(profile_dir_str)
                    cmd.extend([str(p.relative_to(profile_dir_str)) for p in sel_items_paths])
                else:
                    logger.warning(
                        "Archiving with absolute paths as items are not all relative to a single profile or profile path is invalid.")
                    cmd.extend([str(p) for p in sel_items_paths])  # Use absolute paths for items

                logger.info(f"Archive command: {' '.join(cmd)}")
                # Use subprocess.run for better control and error handling
                process_result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore',
                                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
                                                timeout=1800)  # 30 min timeout

                if process_result.returncode != 0:
                    logger.error(
                        f"Archive creation failed. Return code: {process_result.returncode}. Stderr: {process_result.stderr}")
                    raise subprocess.CalledProcessError(process_result.returncode, cmd, output=process_result.stdout,
                                                        stderr=process_result.stderr)

                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(80)
                QtWidgets.QMessageBox.information(self, "Archive Created",
                                                  f"Archive '{actual_archive_filename_for_script}' created successfully at:\n{archive_output_path}")
            except subprocess.CalledProcessError as e:
                QtWidgets.QMessageBox.critical(self, "Archive Creation Error",
                                               f"Failed to create archive: {e.stderr if e.stderr else e.stdout if e.stdout else e}")
                self._reset_generation_ui("Archive creation failed.")
                return
            except subprocess.TimeoutExpired:
                QtWidgets.QMessageBox.critical(self, "Archive Creation Timeout",
                                               "Archive creation took too long (over 30 minutes) and was stopped.")
                self._reset_generation_ui("Archive creation timed out.")
                return
            except Exception as e:
                logger.error(f"Unexpected error during archive creation: {e}", exc_info=True)
                QtWidgets.QMessageBox.critical(self, "Unexpected Archive Error",
                                               f"An unexpected error occurred during archive creation: {e}")
                self._reset_generation_ui("Unexpected archive error.")
                return
        elif use_archive and not sel_items_paths:
            logger.info("Archive creation skipped as no items were selected for archiving.")
            if not actual_archive_filename_for_script: actual_archive_filename_for_script = "migrator_backup.tar.gz"

            # Generate the script
        try:
            self.status_bar.showMessage("Generating migration script...")
            self.progress_label.setText("Generating script...")
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(
                90 if use_archive and sel_items_paths else 50)  # Adjust progress based on archive step
            QtWidgets.QApplication.processEvents()

            script_content = generate_shell_script(
                sel_items_paths,
                sel_apps_equivalents,
                self.chk_full.isChecked(),
                linux_username_target,
                self.cmd_edit.currentText(),
                use_archive,
                actual_archive_filename_for_script,  # Use the name of the archive file that was (or would be) created
                self.chk_permissions.isChecked(),
                self.chk_symlinks.isChecked(),
                self.chk_fix_paths.isChecked()
            )

            default_script_name = "migrate_to_linux.sh"
            script_out_file, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save Migration Script As", str(Path.home() / default_script_name),
                "Bash Scripts (*.sh);;All Files (*)"
            )

            if script_out_file:
                script_path = Path(script_out_file)
                script_path.write_text(script_content, encoding="utf-8",
                                       newline='\n')  # Ensure LF line endings for Linux
                try:
                    if os.name != 'nt':  # Set executable only on non-Windows
                        os.chmod(script_out_file, 0o755)
                except Exception as e_chmod:
                    logger.warning(f"Could not set execute permission on script (non-critical on Windows): {e_chmod}")

                self.progress_bar.setValue(100)
                final_message = f"Migration script saved to:\n<b>{script_out_file}</b>\n\n"
                if use_archive and sel_items_paths:
                    final_message += f"The script will expect the archive '<b>{actual_archive_filename_for_script}</b>' to be in the same directory when run on Linux.\n"
                elif not use_archive and sel_items_paths:
                    final_message += "The script is configured for direct data copy. Ensure source data is prepared on the Linux system as per script instructions.\n"

                QtWidgets.QMessageBox.information(self, "Generation Complete", final_message)

                instructions_title = "Detailed Usage Instructions (Linux)"
                instructions = (
                        "<b>To use the generated script on your Linux system:</b>\n\n"
                        f"1. Transfer the script (<code>{script_path.name}</code>)" +
                        (
                            f" and the archive (<code>{actual_archive_filename_for_script}</code>)" if use_archive and sel_items_paths else "") +
                        " to your Linux machine (e.g., into a new, empty folder).\n\n"
                        "2. <b>If NOT using an archive (direct copy mode for files/folders):</b>\n"
                        "   Create a sub-directory named <code>source_windows_data</code> next to the script.\n"
                        "   Copy your selected Windows folders (e.g., Desktop, Documents) into this <code>source_windows_data</code> directory, maintaining their original names.\n\n"
                        "3. Open a Terminal in the directory containing the script (and archive/data).\n"
                        "   Example: <code>cd /path/to/your/migration_files</code>\n\n"
                        "4. Make the script executable (if not already):\n"
                        f"   <code>chmod +x {script_path.name}</code>\n\n"
                        "5. Run the script (as a normal user, it will use sudo if needed):\n"
                        f"   <code>./{script_path.name}</code>\n\n"
                        "6. Carefully read and follow the on-screen prompts from the script. You might be asked for your sudo password for software installation or file ownership changes.\n\n"
                        "<b>Important:</b> Review the script's contents before running it, especially if you have customized settings."
                )
                msg_box_instr = QtWidgets.QMessageBox(self)
                msg_box_instr.setIcon(QtWidgets.QMessageBox.Icon.Information)
                msg_box_instr.setWindowTitle(instructions_title)
                msg_box_instr.setTextFormat(QtCore.Qt.TextFormat.RichText)
                msg_box_instr.setText(instructions)
                # Make the instruction box wider
                msg_box_instr.setStyleSheet("QMessageBox { min-width: 600px; }")
                msg_box_instr.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
                msg_box_instr.exec()

                self._reset_generation_ui("Generation process completed.")

            else:
                self._reset_generation_ui("Script saving cancelled by user.")

        except Exception as e:
            logger.error(f"Failed to generate migration script: {e}", exc_info=True)
            QtWidgets.QMessageBox.critical(self, "Script Generation Error", f"Failed to generate migration script: {e}")
            self._reset_generation_ui("Script generation failed.")

    def _reset_generation_ui(self, status_message):
        """Resets UI elements related to the generation process."""
        self.progress_bar.setVisible(False)
        self.progress_bar.setValue(0)
        self.status_bar.showMessage(status_message)
        self.progress_label.setText("Ready.")

    def closeEvent(self, event):
        """Handle window close event to stop threads."""
        if self.load_items_thread.isRunning():
            self.load_items_thread.stop()
            if not self.load_items_thread.wait(1000):  # Wait up to 1 sec
                logger.warning("LoadItemsThread did not finish in time upon closing.")
        if self.load_apps_thread.isRunning():
            self.load_apps_thread.stop()
            if not self.load_apps_thread.wait(1000):
                logger.warning("LoadAppsThread did not finish in time upon closing.")
        super().closeEvent(event)


if __name__ == '__main__':
    # High DPI Scaling Attributes (set before QApplication instance)
    try:
        # In Qt6, AA_EnableHighDpiScaling is often on by default.
        # This attribute might not be available or necessary in all PyQt6 versions.
        if hasattr(QtCore.Qt, 'AA_EnableHighDpiScaling'):
            QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling)
        else:
            logger.info("QtCore.Qt.AA_EnableHighDpiScaling not found, assuming enabled by default or not needed.")
    except Exception as e:  # Catch any error during setAttribute
        logger.warning(f"Could not set AA_EnableHighDpiScaling: {e}")

    try:
        if hasattr(QtCore.Qt, 'AA_UseHighDpiPixmaps'):
            QtWidgets.QApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps)
        else:
            logger.info("QtCore.Qt.AA_UseHighDpiPixmaps not found, assuming enabled by default or not needed.")
    except Exception as e:
        logger.warning(f"Could not set AA_UseHighDpiPixmaps: {e}")

    if hasattr(QtWidgets.QApplication, 'setHighDpiScaleFactorRoundingPolicy'):
        QtWidgets.QApplication.setHighDpiScaleFactorRoundingPolicy(
            QtCore.Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QtWidgets.QApplication(sys.argv)

    # Application Styling
    if 'Fusion' in QtWidgets.QStyleFactory.keys():
        app.setStyle(QtWidgets.QStyleFactory.create('Fusion'))

    palette = QtGui.QPalette()
    # Define colors for light theme
    WINDOW_BG = QtGui.QColor(245, 247, 249)  # Slightly bluish white
    WINDOW_TEXT = QtGui.QColor(40, 45, 50)
    BASE_BG = QtGui.QColor(255, 255, 255)
    ALTERNATE_BASE_BG = QtGui.QColor(238, 242, 245)
    TOOLTIP_BG = QtGui.QColor(255, 255, 230)  # Pale yellow
    BUTTON_BG = QtGui.QColor(230, 235, 240)
    HIGHLIGHT_COLOR = QtGui.QColor(0, 120, 215)  # Standard blue highlight
    HIGHLIGHTED_TEXT_COLOR = QtGui.QColor(255, 255, 255)
    LINK_COLOR = QtGui.QColor(0, 100, 200)

    palette.setColor(QtGui.QPalette.ColorRole.Window, WINDOW_BG)
    palette.setColor(QtGui.QPalette.ColorRole.WindowText, WINDOW_TEXT)
    palette.setColor(QtGui.QPalette.ColorRole.Base, BASE_BG)
    palette.setColor(QtGui.QPalette.ColorRole.AlternateBase, ALTERNATE_BASE_BG)
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipBase, TOOLTIP_BG)
    palette.setColor(QtGui.QPalette.ColorRole.ToolTipText, WINDOW_TEXT)
    palette.setColor(QtGui.QPalette.ColorRole.Text, WINDOW_TEXT)
    palette.setColor(QtGui.QPalette.ColorRole.Button, BUTTON_BG)
    palette.setColor(QtGui.QPalette.ColorRole.ButtonText, WINDOW_TEXT)
    palette.setColor(QtGui.QPalette.ColorRole.BrightText, QtGui.QColor(231, 76, 60))  # Red for bright text
    palette.setColor(QtGui.QPalette.ColorRole.Link, LINK_COLOR)
    palette.setColor(QtGui.QPalette.ColorRole.Highlight, HIGHLIGHT_COLOR)
    palette.setColor(QtGui.QPalette.ColorRole.HighlightedText, HIGHLIGHTED_TEXT_COLOR)

    # Disabled state colors
    DISABLED_TEXT_COLOR = QtGui.QColor(140, 145, 150)
    palette.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.ButtonText, DISABLED_TEXT_COLOR)
    palette.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.WindowText, DISABLED_TEXT_COLOR)
    palette.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.Text, DISABLED_TEXT_COLOR)
    palette.setColor(QtGui.QPalette.ColorGroup.Disabled, QtGui.QPalette.ColorRole.Highlight,
                     QtGui.QColor(200, 200, 200))

    app.setPalette(palette)

    # General Stylesheet
    app.setStyleSheet("""
        QMainWindow, QDialog {
            font-family: 'Segoe UI', 'Roboto', 'Cantarell', 'Helvetica Neue', sans-serif; 
            font-size: 9.5pt; /* Slightly larger base font */
        }
        QTreeWidget {
            border: 1px solid #d0d7de; /* Softer border, GitHub-like */
            border-radius: 6px; /* More rounded */
            font-size: 9pt;
        }
        QTreeWidget::item {
            padding: 4px 2px; /* More vertical padding */
        }
        /* QTreeWidget::item:selected is handled by palette */
        QTreeWidget::item:hover {
            background-color: #f0f6fc; /* Lighter blue hover */
        }
        QHeaderView::section {
            background-color: #f6f8fa; /* Very light header */
            padding: 5px;
            border: 1px solid #d0d7de;
            border-left: none; /* Avoid double borders */
            border-top: none;
            font-weight: 600; /* Semibold */
            font-size: 9pt;
            color: #24292f; /* Darker text for header */
        }
        QHeaderView::section:first {
            border-left: 1px solid #d0d7de; /* Add left border for first header */
        }
        QGroupBox {
            border: 1px solid #d0d7de;
            border-radius: 6px; 
            margin-top: 0.8em; 
            padding: 10px; /* Padding inside groupbox */
            font-weight: 600;
            background-color: #f6f8fa; 
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            subcontrol-position: top left; 
            padding: 0 8px; /* More padding for title */
            left: 10px;
            background-color: #f6f8fa; 
            color: #24292f;
            font-size: 9.5pt;
        }
        QPushButton {
            padding: 7px 15px; 
            border-radius: 5px;
            border: 1px solid #1b1f2426; /* GitHub-like button border */
            background-color: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #f6f8fa, stop:1 #ebecf0); 
            font-weight: 500; /* Medium weight */
            color: #24292f;
            font-size: 9pt;
        }
        QPushButton:hover {
            background-color: #f3f4f6; 
            border-color: #1b1f2426;
        }
        QPushButton:pressed {
            background-color: #e5e7ea;
            border-color: #1b1f2433;
        }
        QPushButton:disabled {
            background-color: #f6f8fa;
            color: #8c959f; /* Disabled text color */
            border-color: #1b1f241a;
        }
        QLineEdit, QComboBox {
            padding: 6px 8px; /* More padding */
            border: 1px solid #d0d7de;
            border-radius: 5px;
            min-height: 22px; 
            background-color: #ffffff; /* White background for inputs */
        }
        QComboBox::drop-down {
            border-left: 1px solid #d0d7de;
            border-top-right-radius: 5px;
            border-bottom-right-radius: 5px;
            width: 20px;
        }
        QComboBox::down-arrow {
            image: url(data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxNiIgaGVpZ2h0PSIxNiIgZmlsbD0iIzI0MjkyZiIgdmlld0JveD0iMCAwIDE2IDE2Ij48cGF0aCBkPSJNNC40MjcgNy40MjdsMy4zOTYgMy4zOTZhLjI1LjI1IDAgMCAwIC4zNTQgMGwzLjM5Ni0zLjM5NkEuMjUuMjUgMCAwIDAgMTEuMzk2IDdoLTYuNzlhLjI1LjI1IDAgMCAwLS4xNzkuNDI3eiIvPjwvc3ZnPg==); /* Simple SVG arrow */
            width: 12px;
            height: 12px;
        }

        QTabWidget::pane { 
            border: 1px solid #d0d7de;
            border-top: none; /* Pane border only on sides/bottom */
            border-radius: 0 0 6px 6px; /* Rounded bottom corners for pane */
        }
        QTabBar::tab {
            background: #f6f8fa;
            border: 1px solid #d0d7de;
            border-bottom: none; /* No bottom border for inactive tabs */
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            padding: 8px 18px; 
            margin-right: 1px; 
            color: #57606a; /* Text color for inactive tabs */
        }
        QTabBar::tab:selected {
            background: #ffffff; /* White background for selected tab */
            border-color: #d0d7de;
            color: #24292f; /* Darker text for selected tab */
            font-weight: 600;
            /* Make selected tab appear "on top" by removing its bottom border effectively making pane's top border visible */
        }
        QTabBar::tab:hover:!selected {
            background: #f0f6fc;
            color: #24292f;
        }
        QProgressBar {
            border: 1px solid #d0d7de;
            border-radius: 5px;
            text-align: center;
            background-color: #ebecf0;
            color: #24292f; /* Text color on progress bar */
            font-weight: 500;
        }
        QProgressBar::chunk {
            background-color: #2da44e; /* Green chunk, GitHub-like */
            border-radius: 4px; /* Rounded chunk */
            margin: 1px; /* Margin for chunk */
        }
        QStatusBar {
            font-size: 9pt;
            color: #57606a;
        }
        QToolTip { /* Style tooltips */
            background-color: #22272e; /* Dark tooltip background */
            color: #c9d1d9; /* Light text for tooltip */
            border: 1px solid #444c56;
            padding: 5px;
            border-radius: 4px;
            font-size: 8.5pt;
        }
    """)

    main_window = None
    try:
        main_window = MigratorApp()
        main_window.show()
        sys.exit(app.exec())
    except Exception as e:
        logger.critical(f"An unhandled exception occurred at the top level: {e}", exc_info=True)
        if app:
            error_msg = f"A critical unexpected error occurred and the application could not start:\n\n{e}\n\n" \
                        f"Please check the log file for more details: {log_file}"
            QtWidgets.QMessageBox.critical(None, "Fatal Application Error", error_msg)
        else:
            print(f"CRITICAL ERROR before QApplication instantiation: {e}. Log file: {log_file}")
        sys.exit(1)

