# Windows-Linux-Migrator
Windows TO Linux Migrator Utility to ease the transition from Windows to Linux
How the Program Works:

path Screenshots https://github.com/youcefbenslimani/Windows-Linux-Migrator/blob/main/capture_250508_132551.jpg

path file exe https://github.com/youcefbenslimani/Windows-Linux-Migrator/releases/tag/1.0

PATH Source code  call me

The Windows→Linux Migrator is a tool designed to simplify the transition from Windows to Linux by:

Identifying User Data: Scans standard Windows user folders (e.g., Desktop, Documents, Downloads) for migration.

Listing Installed Windows Apps: Extracts installed applications from the Windows Registry and suggests Linux alternatives.

Generating a Migration Script: Creates a Bash script to automate data transfer and app installation on Linux.

Step-by-Step Usage Guide:
1. Run the Program on Windows:
Prerequisites:

Install Python 3.

Install required libraries:

bash
pip install pyqt6 psutil
Launch the GUI:
Run lunx.py on Windows:

bash
python lunx.py
2. Prepare for Migration:
Step 1: Select User Folders

Enter your Windows username (auto-detected by default).

Click Refresh File List to scan folders (e.g., Desktop, Documents).

Check the folders/files you want to migrate.

Step 2: Select Applications

Click Refresh App List to load installed Windows apps.

Check apps you want Linux alternatives for.

Step 3: Configure Export Settings

Archive Options:

Enable Create a compressed archive to bundle files into win_migrator_backup.tar.gz.

Linux Settings:

Specify the target Linux username (optional).

Choose the package manager command (e.g., apt, dnf, pacman -S).

3. Generate the Script/Archive:
Click Generate Script / Archive.

Save the script (e.g., migrate_to_linux.sh) and optionally the archive.

4. Transfer Files to Linux:
Copy the generated script (and archive) to your Linux machine.

If not using an archive, create a folder named source_windows_data next to the script and copy your Windows folders into it.

5. Run the Script on Linux:
Open a terminal in the script’s directory.

Make the script executable:

bash
chmod +x migrate_to_linux.sh
Execute the script:

bash
./migrate_to_linux.sh
Follow the prompts:

Enter the Linux username (if not specified earlier).

Allow the script to install apps and migrate data.

Key Features:
Data Migration:

Direct copy (via rsync) or archive extraction.

Preserves permissions and symlinks (optional).

App Alternatives:

Auto-suggests Linux equivalents (e.g., LibreOffice for Microsoft Office).

Uses your Linux package manager for installations.

Error Handling:

Logs errors to ~/WinLinuxMigrator_logs/migrator_*.log.

Example Workflow:
On Windows:

Select Desktop, Documents, and Mozilla Firefox.

Generate migrate_to_linux.sh and win_migrator_backup.tar.gz.

On Linux:

Copy both files to ~/migration/.

Run:

bash
cd ~/migration
chmod +x migrate_to_linux.sh
./migrate_to_linux.sh
The script will:

Extract the archive.

Install Firefox (Linux version).

Place data in /home/<user>/migrated_windows_data.

Troubleshooting:
Missing Folders: Ensure the Windows username is correct.

Permission Issues: Run the script with sudo if needed.

App Installation Failures: Manually adjust the package manager command in the script.

By following these steps, users can seamlessly migrate their data and applications from Windows to Linux.
