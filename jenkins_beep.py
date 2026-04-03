"""
jenkins_beep.py - Monitor a Jenkins job and beep when it finishes.

Usage:
    python jenkins_beep.py <job_name_or_url> [options]

Examples:
    python jenkins_beep.py my-job
    python jenkins_beep.py my-folder/my-job
    python jenkins_beep.py http://other-jenkins/job/my-job/ --user bob --token abc123
    python jenkins_beep.py my-job --build 42
    python jenkins_beep.py my-job --interval 5
"""

import sys
import time
import argparse
import threading
import configparser
import platform
import subprocess
import requests
from pathlib import Path

_PLATFORM = platform.system()  # 'Windows', 'Darwin', 'Linux'

if _PLATFORM == "Windows":
    import winsound

try:
    import pyttsx3
    _tts_available = True
except ImportError:
    _tts_available = False

_tts_lock = threading.Lock()

_ZIRA_ID = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech\Voices\Tokens\TTS_MS_EN-US_ZIRA_11.0"

# Active voice name (partial match). Set from config or overridden at runtime.
_active_voice: str = ""

def configure_voice(name: str):
    """Set the TTS voice by partial name (e.g. 'zira', 'huihui', 'ting-ting')."""
    global _active_voice
    _active_voice = name.strip()

def list_voices() -> list[tuple[str, str]]:
    """Return [(id, name), ...] of all voices available on this system."""
    if not _tts_available:
        return []
    engine = pyttsx3.init()
    voices = [(v.id, v.name) for v in engine.getProperty("voices")]
    engine.stop()
    return voices

def _resolve_voice(engine) -> str | None:
    """Return the best matching voice ID, or None to keep system default."""
    want = _active_voice
    if not want:
        if _PLATFORM == "Windows":
            # Default to Zira on Windows for backward-compat if installed
            want = "zira"
        else:
            return None  # use system default on macOS/Linux
    want_lower = want.lower()
    for v in engine.getProperty("voices"):
        if want_lower in v.name.lower() or want_lower in v.id.lower():
            return v.id
    return None

def _beep():
    """Cross-platform beep fallback."""
    if _PLATFORM == "Windows":
        winsound.Beep(880, 300)
    elif _PLATFORM == "Darwin":
        subprocess.run(["afplay", "/System/Library/Sounds/Ping.aiff"], check=False)
    else:
        print("\a", end="", flush=True)

def _speak(text: str):
    """Speak text via TTS, fallback to beep if unavailable."""
    if not _tts_available:
        _beep()
        return
    with _tts_lock:
        engine = pyttsx3.init()
        engine.setProperty("rate", 160)
        vid = _resolve_voice(engine)
        if vid:
            engine.setProperty("voice", vid)
        engine.say(text)
        engine.runAndWait()
        engine.stop()


# ── Config ────────────────────────────────────────────────────────────────────

_CONFIG_FILE = Path(__file__).parent / "jenkins_beep.ini"

def _load_config():
    cfg = configparser.ConfigParser()
    if _CONFIG_FILE.exists():
        cfg.read(_CONFIG_FILE, encoding="utf-8")
    sec = cfg["jenkins"] if "jenkins" in cfg else {}
    return (
        sec.get("root_url", ""),
        sec.get("username", ""),
        sec.get("token",    ""),
        sec.get("success_msg", "Build {job} succeeded! Go check it out!"),
        sec.get("failure_msg", "Build {job} failed! Go fix it now!"),
        sec.get("running_msg", "Build {job} has started!"),
        sec.get("waiting_msg", "Still waiting for {job} to finish."),
        sec.get("voice", ""),
        sec.getboolean("waiting_enabled", True),
        sec.getint("waiting_interval_min", 5),
    )

def save_config(**kwargs):
    """Persist arbitrary key=value pairs to the [jenkins] section of jenkins_beep.ini."""
    cfg = configparser.ConfigParser()
    if _CONFIG_FILE.exists():
        cfg.read(_CONFIG_FILE, encoding="utf-8")
    if "jenkins" not in cfg:
        cfg["jenkins"] = {}
    for key, value in kwargs.items():
        cfg["jenkins"][key] = value
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        cfg.write(f)

(JENKINS_DEFAULT_ROOT_URL, JENKINS_TOKEN_DEFAULT_USERNAME, JENKINS_TOKEN,
 SUCCESS_MSG_TEMPLATE, FAILURE_MSG_TEMPLATE, RUNNING_MSG_TEMPLATE,
 WAITING_MSG_TEMPLATE, _CFG_VOICE,
 WAITING_ENABLED_DEFAULT, WAITING_INTERVAL_MIN_DEFAULT) = _load_config()

configure_voice(_CFG_VOICE)


# ── Voice announcements ───────────────────────────────────────────────────────

def beep_success(job_name="", template=None):
    _speak((template or SUCCESS_MSG_TEMPLATE).format(job=job_name))

def beep_failure(job_name="", template=None):
    _speak((template or FAILURE_MSG_TEMPLATE).format(job=job_name))

def beep_running(job_name="", template=None):
    _speak((template or RUNNING_MSG_TEMPLATE).format(job=job_name))

def beep_waiting(job_name="", template=None):
    _speak((template or WAITING_MSG_TEMPLATE).format(job=job_name))


# ── Jenkins helpers ───────────────────────────────────────────────────────────

def fetch(url, auth, timeout=15):
    resp = requests.get(url, auth=auth, timeout=timeout,
                        headers={"Accept": "application/json"})
    resp.raise_for_status()
    return resp.json()


def get_last_build(job_url, auth):
    return fetch(f"{job_url}/lastBuild/api/json", auth)


def get_build(job_url, number, auth):
    return fetch(f"{job_url}/{number}/api/json", auth)


# ── Main ──────────────────────────────────────────────────────────────────────

def _monitor_job(job_url, job_name, auth, interval, build_number,
                 success_template, fail_template, running_template=None,
                 waiting_template=None, waiting_enabled=True,
                 waiting_interval_min=5):
    """Monitor a single Jenkins job. Designed to run in its own thread."""
    prefix = f"[{job_name}] " if job_name else ""

    watched_number  = None
    waiting_for_new = False

    if build_number:
        watched_number = build_number
        print(f"{prefix}📌 Targeting build #{watched_number}")
    else:
        try:
            info = get_last_build(job_url, auth)
        except Exception as e:
            print(f"{prefix}❌ Cannot reach Jenkins: {e}")
            return

        number   = info.get("number")
        building = info.get("building", False)

        if building:
            watched_number = number
            print(f"{prefix}⏳ Build #{number} already running — watching it...")
            beep_running(job_name, running_template)
        else:
            result = info.get("result", "?")
            print(f"{prefix}💤 Last build #{number} already finished ({result}).")
            print(f"{prefix}   Waiting for the next build to start...")
            waiting_for_new = True
            watched_number  = number

    tick_counter = 0
    ticks_per_reminder = max(1, (waiting_interval_min * 60) // interval)
    while True:
        time.sleep(interval)
        tick_counter += 1

        try:
            if waiting_for_new:
                info = get_last_build(job_url, auth)
                if info["number"] != watched_number:
                    watched_number  = info["number"]
                    waiting_for_new = False
                    if info.get("building"):
                        print(f"{prefix}⏳ New build #{watched_number} started — watching...")
                        beep_running(job_name, running_template)
                    else:
                        _report_and_beep(info, job_name, success_template, fail_template)
                        return
                else:
                    if tick_counter % ticks_per_reminder == 0:
                        print(f"{prefix}   ... still waiting for new build (last: #{watched_number})")
                        if waiting_enabled:
                            beep_waiting(job_name, waiting_template)
            else:
                info = get_build(job_url, watched_number, auth)
                if info.get("building"):
                    elapsed_s = int(time.time() * 1000 - info.get("timestamp", 0)) // 1000
                    elapsed   = _fmt_duration(elapsed_s) if elapsed_s > 0 else "?"
                    print(f"{prefix}   ⏳ Build #{watched_number} still running ({elapsed} elapsed)...")
                    if tick_counter % ticks_per_reminder == 0 and waiting_enabled:
                        beep_waiting(job_name, waiting_template)
                else:
                    _report_and_beep(info, job_name, success_template, fail_template)
                    return

        except KeyboardInterrupt:
            return
        except Exception as e:
            print(f"{prefix}⚠️  Poll error (will retry): {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Monitor Jenkins jobs and announce when they finish.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("jobs", nargs="+",
                        help="Job name(s) or URL(s)  e.g. my-job  folder/job  https://...")
    parser.add_argument("--user",        default=JENKINS_TOKEN_DEFAULT_USERNAME,
                                         help=f"Jenkins username (default: {JENKINS_TOKEN_DEFAULT_USERNAME})")
    parser.add_argument("--token",       default=JENKINS_TOKEN,
                                         help="Jenkins API token")
    parser.add_argument("--root",        default=JENKINS_DEFAULT_ROOT_URL,
                                         help=f"Jenkins root URL (default: {JENKINS_DEFAULT_ROOT_URL})")
    parser.add_argument("--build",       type=int,
                                         help="Watch a specific build number for all jobs (default: latest)")
    parser.add_argument("--interval",    type=int, default=10,
                                         help="Poll interval in seconds (default: 10)")
    parser.add_argument("--success-msg", default=None,
                                         help="Custom success announcement. Use {job} for job name. "
                                              f"(default: \"{SUCCESS_MSG_TEMPLATE}\")")
    parser.add_argument("--fail-msg",     default=None,
                                         help="Custom failure announcement. Use {job} for job name. "
                                              f"(default: \"{FAILURE_MSG_TEMPLATE}\")")
    parser.add_argument("--running-msg", default=None,
                                         help="Custom 'build started' announcement. Use {job} for job name. "
                                              f"(default: \"{RUNNING_MSG_TEMPLATE}\")")
    parser.add_argument("--waiting-msg",      default=None,
                                         help="Custom 'still running' reminder. Use {job} for job name. "
                                              f"(default: \"{WAITING_MSG_TEMPLATE}\")")
    parser.add_argument("--no-waiting",        action="store_true",
                                         help="Disable the periodic 'still waiting' voice reminder.")
    parser.add_argument("--waiting-interval",  type=int, default=WAITING_INTERVAL_MIN_DEFAULT,
                                         help=f"Minutes between 'still waiting' reminders (default: {WAITING_INTERVAL_MIN_DEFAULT})")
    parser.add_argument("--voice",       default=None,
                                         help="TTS voice name (partial match, e.g. 'huihui', 'ting-ting'). "
                                              "Run with --list-voices to see options.")
    parser.add_argument("--list-voices", action="store_true",
                                         help="List all available TTS voices and exit.")
    args = parser.parse_args()

    if args.list_voices:
        voices = list_voices()
        if voices:
            print("Available TTS voices:")
            for vid, vname in voices:
                print(f"  {vname!r:40s}  {vid}")
        else:
            print("pyttsx3 not installed – no voices available.")
        sys.exit(0)

    if args.voice:
        configure_voice(args.voice)

    success_template = args.success_msg  or SUCCESS_MSG_TEMPLATE
    fail_template    = args.fail_msg     or FAILURE_MSG_TEMPLATE
    running_template = args.running_msg  or RUNNING_MSG_TEMPLATE
    waiting_template = args.waiting_msg  or WAITING_MSG_TEMPLATE
    waiting_enabled  = not args.no_waiting
    waiting_interval = args.waiting_interval
    auth             = (args.user, args.token)

    # Resolve each job to (url, display_name)
    job_list = []
    for job in args.jobs:
        if job.startswith("http://") or job.startswith("https://"):
            url  = job.rstrip("/")
            name = url.rstrip("/").split("/")[-1]
        else:
            job_path = "/job/".join(job.strip("/").split("/"))
            url  = f"{args.root.rstrip('/')}/job/{job_path}"
            name = job.strip("/").split("/")[-1]
        job_list.append((url, name))

    print(f"\n🔔  Jenkins Beep Monitor")
    print(f"    Jobs:     {', '.join(n for _, n in job_list)}")
    print(f"    Interval: {args.interval}s")
    print(f"    Auth:     {args.user} / ***")
    print()

    try:
        if len(job_list) == 1:
            url, name = job_list[0]
            _monitor_job(url, name, auth, args.interval, args.build,
                         success_template, fail_template, running_template,
                         waiting_template, waiting_enabled, waiting_interval)
        else:
            threads = [
                threading.Thread(
                    target=_monitor_job,
                    args=(url, name, auth, args.interval, args.build,
                          success_template, fail_template, running_template,
                          waiting_template, waiting_enabled, waiting_interval),
                    daemon=True,
                )
                for url, name in job_list
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
    except KeyboardInterrupt:
        print("\n👋 Cancelled.")


def _report_and_beep(info, job_name="", success_template=None, fail_template=None):
    number   = info.get("number", "?")
    result   = info.get("result", "UNKNOWN")
    duration = _fmt_duration(info.get("duration", 0) // 1000)
    label    = f" [{job_name}]" if job_name else ""

    if result == "SUCCESS":
        print(f"\n✅{label}  Build #{number} SUCCESS  ({duration})")
        beep_success(job_name, success_template)
    else:
        print(f"\n❌{label}  Build #{number} {result}  ({duration})")
        beep_failure(job_name, fail_template)

    url = info.get("url", "")
    if url:
        print(f"    {url}")


def _fmt_duration(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s"


if __name__ == "__main__":
    main()
