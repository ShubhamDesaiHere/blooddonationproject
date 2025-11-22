#!/usr/bin/env python3
import os
import time
from pyngrok import ngrok, conf

# Set authtoken if available
authtoken = os.getenv("NGROK_AUTHTOKEN", "32q3M3oVvzpHLnrUanI22054atF_2fEJAFv1tXWLnYEdNxXLh")
if authtoken:
    conf.get_default().auth_token = authtoken

try:
    # Create tunnel
    tunnel = ngrok.connect(8000, proto="http", region="in")
    public_url = tunnel.public_url
    
    # Ensure https
    if public_url.startswith("http://"):
        tunnels = ngrok.get_tunnels()
        for t in tunnels:
            if t.public_url.startswith("https://"):
                public_url = t.public_url
                break
    
    print(f"Public URL: {public_url}")
    
    # Update twilio.env
    env_path = "Final Project/zzzz/twilio.env"
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            lines = f.readlines()
        
        # Update or add CALLBACK_BASE_URL
        updated = False
        for i, line in enumerate(lines):
            if line.startswith("CALLBACK_BASE_URL="):
                lines[i] = f"CALLBACK_BASE_URL={public_url}\n"
                updated = True
                break
        
        if not updated:
            lines.append(f"CALLBACK_BASE_URL={public_url}\n")
        
        with open(env_path, 'w') as f:
            f.writelines(lines)
        
        print(f"Updated {env_path} with CALLBACK_BASE_URL={public_url}")
    
    print(f"\nTest these URLs:")
    print(f"Voice endpoint: {public_url}/voice")
    print(f"Handle response: {public_url}/handle-response")
    print(f"\nKeep this script running to maintain the tunnel...")
    
    # Keep running
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        ngrok.disconnect(tunnel.public_url)
        ngrok.kill()
        
except Exception as e:
    print(f"Error: {e}")
    print("Make sure FastAPI is running on port 8000")
