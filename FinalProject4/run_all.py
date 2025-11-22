#!/usr/bin/env python3
import subprocess
import time
import os
import signal
import sys
from pyngrok import ngrok

def main():
    print("Starting FastAPI and ngrok tunnel...")
    
    # Set ngrok auth token
    ngrok.set_auth_token('32q3M3oVvzpHLnrUanI22054atF_2fEJAFv1tXWLnYEdNxXLh')
    
    # Start FastAPI
    print("Starting FastAPI on port 8000...")
    fastapi_proc = subprocess.Popen([
        sys.executable, "-m", "uvicorn", "finalproject4:app", 
        "--reload", "--port", "8000"
    ])
    
    # Wait for FastAPI to start
    time.sleep(3)
    
    # Start ngrok tunnel
    print("Starting ngrok tunnel...")
    tunnel = ngrok.connect(8000, proto="http")
    public_url = tunnel.public_url
    
    print(f"\n✅ Public URL: {public_url}")
    print(f"Voice endpoint: {public_url}/voice")
    print(f"Handle response: {public_url}/handle-response")
    
    # Update twilio.env
    env_path = "Final Project/zzzz/twilio.env"
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            lines = f.readlines()
        
        # Update CALLBACK_BASE_URL
        for i, line in enumerate(lines):
            if line.startswith("CALLBACK_BASE_URL="):
                lines[i] = f"CALLBACK_BASE_URL={public_url}\n"
                break
        
        with open(env_path, 'w') as f:
            f.writelines(lines)
        
        print(f"✅ Updated {env_path}")
    
    print("\n🚀 Ready! You can now make calls.")
    print("Press Ctrl+C to stop...")
    
    def cleanup():
        print("\nShutting down...")
        ngrok.disconnect(tunnel.public_url)
        ngrok.kill()
        fastapi_proc.terminate()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, lambda s, f: cleanup())
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        cleanup()

if __name__ == "__main__":
    main()
