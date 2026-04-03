# 🔔 Jenkins Beep Monitor

Monitor one or more Jenkins jobs and get **voice announcements** when builds start, finish, or need a nudge. Works on **Windows, macOS, and Linux**.

---

## Features

- 🎙 Voice announcements for: build started · still running · succeeded · failed
- 📋 Monitor **multiple jobs** simultaneously
- ✏️ **Fully customisable messages** — use `{job}` to include the job name
- 🌐 **Cross-platform TTS** (Windows SAPI / macOS `say` / Linux espeak)
- 🀄 **Chinese (and other languages) supported** — just pick the right system voice
- 🖥 Desktop GUI (`jenkins_beep_gui.py`) and CLI (`jenkins_beep.py`)
- ⚙️ Persistent config via `jenkins_beep.ini`

---

## Installation

### Prerequisites

- Python **3.10+**
- A Jenkins server with API access

### Install dependencies

```bash
pip install -r requirements.txt
```

> **macOS note:** `pyttsx3` uses the built-in `say` command — no extra TTS engine is needed.  
> **Linux note:** Install `espeak` if it's not already present:
> ```bash
> sudo apt install espeak   # Debian/Ubuntu
> brew install espeak        # Homebrew
> ```

### Chinese (or other language) TTS

The tool uses partial-name voice matching. In the GUI, click **"List…"** next to the TTS Voice field to see all voices installed on your system.

| Platform | Download voices | Example name |
|---|---|---|
| **Windows** | Settings → Time & Language → Speech → Add voices | `Microsoft Huihui` |
| **macOS** | System Settings → Accessibility → Spoken Content → System Voice → Customise | `Ting-Ting` |
| **Linux** | `sudo apt install espeak-ng-data` | `zh` |

---

## Quick Start

### GUI

```bash
python jenkins_beep_gui.py
```

1. Add one or more jobs (name, folder path, or full URL)
2. Fill in Connection (Root URL / Username / API Token)
3. Click **▶ Start All**

### CLI

```bash
# Single job
python jenkins_beep.py my-job

# Multiple jobs at once
python jenkins_beep.py job-a folder/job-b

# Custom messages
python jenkins_beep.py my-job \
  --running-msg "{job} 开始跑了！" \
  --success-msg "{job} 成功啦！" \
  --fail-msg    "{job} 挂了，快去看！"

# List available TTS voices
python jenkins_beep.py my-job --list-voices
```

**All CLI options**

| Option | Default | Description |
|---|---|---|
| `--root` | from ini | Jenkins root URL |
| `--user` | from ini | Username |
| `--token` | from ini | API token |
| `--build N` | latest | Watch a specific build number |
| `--interval N` | `10` | Poll interval in seconds |
| `--running-msg` | see below | Announcement when build starts |
| `--waiting-msg` | see below | Periodic reminder while running |
| `--no-waiting` | — | Disable periodic waiting reminder |
| `--waiting-interval N` | `5` | Minutes between waiting reminders |
| `--success-msg` | see below | Announcement on success |
| `--fail-msg` | see below | Announcement on failure |
| `--voice` | system default | TTS voice (partial name match) |
| `--list-voices` | — | Print all available voices and exit |

---

## Configuration

Copy `jenkins_beep.ini.example` to `jenkins_beep.ini` and fill in your details.  
The GUI **auto-saves** message and voice settings back to `jenkins_beep.ini` whenever you edit them.

```ini
[jenkins]
root_url    = https://your-jenkins-server/
username    = your-username
token       = your-api-token

; Voice announcements — use {job} for the job name
running_msg = Build {job} has started!
waiting_msg = Still waiting for {job} to finish.
success_msg = Build {job} succeeded! Go check it out!
failure_msg = Build {job} failed! Go fix it now!

; TTS voice (partial name, case-insensitive). Leave blank for system default.
; voice = ting-ting
```

---

## GUI Overview

```
┌─ Jobs to monitor ─────────────────────────────────┐
│  ⏸ my-job                                         │
│  ⏳ folder/another-job                             │
│  [___job name or URL___________] [＋ Add] [－ Remove]│
└───────────────────────────────────────────────────┘
▼ Connection          (collapsible)
▼ Options             (collapsible)
    Poll interval · Specific build #
    Running / Waiting / Success / Fail messages
    Waiting reminder: [☑ Enable]  every [5] min
    TTS voice
▼ Log                 (collapsible)

[▶ Start All]  [⏹ Stop All]   [🔊 Test ✅]  [🔊 Test ❌]  [🗑 Clear log]
```

**Job status icons**

| Icon | Meaning |
|---|---|
| ⏸ | Idle (not started) |
| 💤 | Waiting for next build to appear |
| ⏳ | Build is running |
| ✅ | Build succeeded |
| ❌ | Build failed |
| ⏹ | Monitor stopped |

**Hot-add during monitoring:** Add a new job while monitoring is already running — it starts automatically.

---

## Jenkins API Token

1. Log in to Jenkins → click your username (top right)
2. **Configure** → **API Token** → **Add new Token**
3. Copy the generated token into `jenkins_beep.ini` or the GUI's API Token field
