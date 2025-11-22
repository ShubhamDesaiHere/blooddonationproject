#!/usr/bin/env python3
"""
Test script for Flask voice call functionality
"""

import requests
import json

def test_flask_voice_call():
    """Test the Flask voice call endpoint"""
    print("🧪 Testing Flask voice call endpoint...")
    
    # First, let's test if we can access the Flask app
    try:
        response = requests.get("http://localhost:5001/")
        if response.status_code == 200:
            print("✅ Flask app is running")
        else:
            print(f"❌ Flask app returned status: {response.status_code}")
            return
    except Exception as e:
        print(f"❌ Cannot connect to Flask app: {e}")
        return
    
    # Test the voice call endpoint (this will fail due to authentication)
    url = "http://localhost:5001/admin/voice_call"
    data = {
        "to_number": "+1 220 222 9934",
        "message": "Test voice call from Flask app"
    }
    
    try:
        response = requests.post(url, json=data)
        result = response.json()
        
        if response.status_code == 200:
            print("✅ Flask voice call test successful!")
            print(f"   Call SID: {result.get('call_sid')}")
            print(f"   Message: {result.get('message')}")
        else:
            print(f"❌ Flask voice call test failed: {result}")
            if "Not authorized" in str(result):
                print("   This is expected - you need to be logged in as admin")
            
    except Exception as e:
        print(f"❌ Error testing Flask voice call: {e}")

def main():
    print("=" * 60)
    print("🧪 FLASK VOICE CALL FUNCTIONALITY TEST")
    print("=" * 60)
    
    test_flask_voice_call()
    
    print("\n" + "=" * 60)
    print("✅ Test completed!")
    print("=" * 60)

if __name__ == "__main__":
    main()
