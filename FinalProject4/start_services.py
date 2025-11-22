import os
import sys
import time
import json
import signal
import subprocess
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parent
TWILIO_ENV_PATH = PROJECT_ROOT / "Final Project" / "zzzz" / "twilio.env"
OPENAI_ENV_PATH = PROJECT_ROOT / "Final Project" / "zzzz" / "openai.env"


def load_env_files() -> None:
    if load_dotenv is None:
        return
    # Load OpenAI and Twilio envs if present
    load_dotenv(dotenv_path=str(OPENAI_ENV_PATH), override=False)
    load_dotenv(dotenv_path=str(TWILIO_ENV_PATH), override=False)


def ensure_ngrok_authtoken() -> None:
    authtoken = os.getenv("NGROK_AUTHTOKEN")
    if not authtoken:
        # If not provided, skip configuring; user may already have it set
        return
    ngrok_bin = find_ngrok_binary()
    if not ngrok_bin:
        return
    # Configure token (idempotent)
    try:
        subprocess.run([ngrok_bin, "config", "add-authtoken", authtoken], check=False)
    except Exception:
        pass


def find_ngrok_binary() -> str:
    # Try PATH first
    for candidate in ["ngrok"]:
        try:
            proc = subprocess.run([candidate, "version"], capture_output=True, text=True)
            if proc.returncode == 0:
                return candidate
        except Exception:
            pass

    # Common Windows install locations
    candidates = [
        str(Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "ngrok.exe"),
        str(Path(os.getenv("LOCALAPPDATA", "")) / "Programs" / "ngrok" / "ngrok.exe"),
        r"C:\\Program Files\\ngrok\\ngrok.exe",
        str(Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps" / "ngrok.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return ""


def start_uvicorn() -> subprocess.Popen:
    # Start FastAPI app on 8000
    return subprocess.Popen([
        sys.executable,
        "-m",
        "uvicorn",
        "finalproject4:app",
        "--reload",
        "--port",
        "8000",
    ], cwd=str(PROJECT_ROOT))


def start_ngrok() -> subprocess.Popen | None:
    """Try to start the ngrok binary if available. Returns process or None."""
    ngrok_bin = find_ngrok_binary()
    if not ngrok_bin:
        return None

    ensure_ngrok_authtoken()

    # Start ngrok http tunnel to 8000
    return subprocess.Popen([ngrok_bin, "http", "8000", "--region=in", "--host-header=rewrite"], cwd=str(PROJECT_ROOT))


def get_public_url(timeout_sec: int = 30) -> str:
    import urllib.request
    import urllib.error

    api_url = "http://127.0.0.1:4040/api/tunnels"
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(api_url, timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                tunnels = data.get("tunnels", [])
                for t in tunnels:
                    public_url = t.get("public_url", "")
                    if public_url.startswith("https://"):
                        return public_url
        except Exception:
            pass
        time.sleep(1.0)
    return ""


def start_pyngrok_and_get_url() -> str:
    """Fallback to pyngrok if ngrok binary is not available."""
    try:
        from pyngrok import ngrok, conf
    except Exception:
        # Try install pyngrok
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "pyngrok"], check=True)
            from pyngrok import ngrok, conf  # type: ignore
        except Exception:
            return ""

    authtoken = os.getenv("NGROK_AUTHTOKEN")
    if authtoken:
        try:
            conf.get_default().auth_token = authtoken
        except Exception:
            pass

    try:
        # Open tunnel to 8000 and return public URL
        tunnel = ngrok.connect(addr=8000, proto="http", region="in")
        url = tunnel.public_url
        # Ensure https
        if url.startswith("http://"):
            # pyngrok typically provides both; try to find https
            tunnels = ngrok.get_tunnels()
            for t in tunnels:
                if t.public_url.startswith("https://"):
                    return t.public_url
        return url
    except Exception:
        return ""


def write_callback_base_url(public_url: str) -> None:
    # Update or append CALLBACK_BASE_URL in twilio.env
    content_lines = []
    if TWILIO_ENV_PATH.exists():
        content_lines = TWILIO_ENV_PATH.read_text(encoding="utf-8").splitlines()

    new_lines = []
    found = False
    for line in content_lines:
        if line.strip().startswith("CALLBACK_BASE_URL="):
            new_lines.append(f"CALLBACK_BASE_URL={public_url}")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"CALLBACK_BASE_URL={public_url}")

    TWILIO_ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


def main() -> None:
    print("Starting FastAPI (uvicorn) and ngrok tunnel...", flush=True)
    load_env_files()

    uvicorn_proc = start_uvicorn()
    time.sleep(1.5)

    ngrok_proc = start_ngrok()
    public_url = ""
    if ngrok_proc is not None:
        public_url = get_public_url(timeout_sec=40)
    if not public_url:
        # Fallback to pyngrok inline
        print("ngrok binary not found or tunnel not detected via API; trying pyngrok fallback...", flush=True)
        public_url = start_pyngrok_and_get_url()
    if not public_url:
        print("ERROR: Could not determine ngrok public URL. Is ngrok running?", flush=True)
        if ngrok_proc is not None:
            try:
                ngrok_proc.terminate()
            except Exception:
                pass
        uvicorn_proc.terminate()
        sys.exit(1)

    write_callback_base_url(public_url)
    os.environ["CALLBACK_BASE_URL"] = public_url

    print("")
    print("Service is ready.")
    print(f"Public URL: {public_url}")
    print("Twilio will call:")
    print(f"  {public_url}/voice  and post to  {public_url}/handle-response")
    print("")
    print("Press Ctrl+C to stop.")

    def shutdown(*_args):
        try:
            if ngrok_proc is not None:
                ngrok_proc.terminate()
        except Exception:
            pass
        try:
            uvicorn_proc.terminate()
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Keep the main process alive
    try:
        while True:
            time.sleep(2.0)
    except KeyboardInterrupt:
        shutdown()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Unified startup script for Blood Donation System
Runs both Flask (main app) and FastAPI (Twilio voice calls) services
"""

import subprocess
import sys
import time
import os
import signal
import threading
from pathlib import Path

def run_flask():
    """Start the Flask application"""
    print("🚀 Starting Flask application...")
    os.chdir("Final Project/zzzz")
    try:
        subprocess.run([sys.executable, "app.py"], check=True)
    except KeyboardInterrupt:
        print("\n🛑 Flask application stopped")
    except Exception as e:
        print(f"❌ Error running Flask: {e}")

def run_fastapi():
    """Start the FastAPI application"""
    print("🚀 Starting FastAPI application...")
    os.chdir("../..")  # Go back to root directory
    try:
        subprocess.run([sys.executable, "-m", "uvicorn", "finalproject4:app", "--reload", "--port", "8000"], check=True)
    except KeyboardInterrupt:
        print("\n🛑 FastAPI application stopped")
    except Exception as e:
        print(f"❌ Error running FastAPI: {e}")

def signal_handler(sig, frame):
    """Handle Ctrl+C gracefully"""
    print("\n🛑 Shutting down services...")
    sys.exit(0)

def main():
    """Main function to start both services"""
    print("=" * 60)
    print("🩸 BLOOD DONATION SYSTEM - UNIFIED STARTUP")
    print("=" * 60)
    print("Starting both Flask and FastAPI services...")
    print("")
    print("📱 Flask App: http://localhost:5001")
    print("🔊 FastAPI Voice API: http://localhost:8000")
    print("📚 API Docs: http://localhost:8000/docs")
    print("")
    print("Press Ctrl+C to stop all services")
    print("=" * 60)
    
    # Set up signal handler for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    
    # Start Flask in a separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Give Flask time to start
    time.sleep(3)
    
    # Start FastAPI in the main thread
    try:
        run_fastapi()
    except KeyboardInterrupt:
        print("\n🛑 All services stopped")

if __name__ == "__main__":
    main()
