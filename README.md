# 🪐 DownRim — RimWorld Steam Workshop Download Automation

<div align="center">
  <img src="https://img.shields.io/badge/RimWorld-Mod_Workflow-7C3AED?style=for-the-badge" alt="RimWorld Mod Workflow" />
  <img src="https://img.shields.io/badge/Steam_Workshop-Downloader-1B2838?style=for-the-badge&logo=steam&logoColor=white" alt="Steam Workshop Downloader" />
  <img src="https://img.shields.io/badge/Automation-Mod_Setup-0F766E?style=for-the-badge" alt="Automation" />
  <img src="https://img.shields.io/badge/Use_Case-RimWorld_Mods-111827?style=for-the-badge" alt="RimWorld Mods" />
  <img src="https://img.shields.io/badge/License-Not_Specified-6B7280?style=for-the-badge" alt="License Not Specified" />
</div>

<div align="center">
  <p><strong>A focused automation project for simplifying RimWorld Steam Workshop mod download workflows.</strong></p>
</div>

---

## Overview

**DownRim** is a RimWorld-focused automation project intended to make Steam Workshop download workflows easier to manage. The goal is to reduce repetitive manual steps when preparing or collecting RimWorld Workshop mods, especially when working with multiple items or a repeatable mod setup process.

This repository is best treated as a practical mod-management helper rather than a full mod manager. It should be used only with Workshop content you are allowed to access and in a way that respects Steam, RimWorld, and individual mod-author rules.

---

## Project Goals

- Simplify repetitive RimWorld Steam Workshop download tasks.
- Keep the workflow easy to understand and modify.
- Help organize mod-download steps into a repeatable process.
- Make future improvements easier by documenting the expected usage flow.
- Provide a clean foundation for adding configuration, validation, and logging later.

---

## Intended Use Cases

- Preparing a RimWorld mod list for local use.
- Repeating the same Workshop-download workflow across machines.
- Collecting Workshop items for a personal RimWorld setup.
- Testing automation around mod identifiers, download folders, and local organization.
- Building a lightweight helper around an existing Steam Workshop workflow.

---

## Important Usage Notice

DownRim should not be used to bypass platform rules, paid access, creator permissions, or distribution restrictions. Mods may have their own licenses, redistribution limits, or creator instructions. Always respect the rights of mod authors and the rules of the services involved.

---

## Recommended Repository Structure

A clean long-term structure for this project could look like this:

```text
DownRim/
├── README.md                # Project documentation
├── requirements.txt         # Python dependencies, if Python is used
├── config.example.json      # Example configuration for download settings
├── downrim.py               # Main automation entry point
├── downloads/               # Local output folder, usually ignored by Git
└── logs/                    # Optional runtime logs, usually ignored by Git
```

If the implementation uses another language or file layout, update this section to match the actual entry point and dependency files.

---

## Suggested Configuration Model

A future configuration file could track the main settings in one place:

```json
{
  "download_dir": "./downloads",
  "workshop_items": [
    "1234567890",
    "9876543210"
  ],
  "overwrite_existing": false,
  "write_log": true
}
```

This is only a suggested model. Keep real credentials, cookies, private tokens, or account data out of the repository.

---

## Getting Started

### 1. Clone the repository

```bash
git clone https://github.com/Dovshmi/DownRim.git
cd DownRim
```

### 2. Review the project files

Before running any automation, review the code and configuration so you understand what folders it reads from, where it writes downloaded files, and whether it calls any external tools or services.

### 3. Install dependencies

If the project uses Python, the recommended pattern is:

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS / Linux:

```bash
source .venv/bin/activate
```

Then install dependencies once a `requirements.txt` file exists:

```bash
pip install -r requirements.txt
```

---

## Example Workflow

```text
Prepare Workshop item IDs
        ↓
Choose a download/output folder
        ↓
Run the DownRim automation
        ↓
Check downloaded files and logs
        ↓
Move or import the mods into the RimWorld mod workflow
```

---

## Safety and Maintenance Notes

- Do not commit downloaded mods unless you are allowed to redistribute them.
- Do not commit private Steam account data, cookies, tokens, or credentials.
- Keep output folders such as `downloads/` and `logs/` in `.gitignore` if they contain generated files.
- Add clear error messages for failed downloads, invalid Workshop IDs, or missing dependencies.
- Log enough information to debug failures without exposing private account data.
- Document any required external tool, service, or command-line dependency.

---

## Roadmap Ideas

- Add a real `requirements.txt` or dependency file.
- Add a `config.example.json` template.
- Add validation for Workshop item IDs.
- Add progress output for long-running downloads.
- Add logging for completed, skipped, and failed items.
- Add duplicate detection for already-downloaded mods.
- Add a dry-run mode that prints planned actions without downloading.
- Add setup instructions for Windows, Linux, and SteamCMD-style workflows if relevant.
- Add a formal open-source license file.

---

## License

No formal `LICENSE` file is currently documented for this repository. Add a license before encouraging reuse, modification, or redistribution by others.

---

## Author

Created by **Dovshmi**.

GitHub: [@Dovshmi](https://github.com/Dovshmi)
