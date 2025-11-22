#!/usr/bin/env python3
"""
Test script for voice call functionality
"""

import requests
import json

def test_fastapi_voice_call():
    """Test the FastAPI voice call endpoint"""
    print("🧪 Testing FastAPI voice call endpoint...")
    
    url = "http://localhost:8000/make-call"
    data = {
        "to_number": "+1 220 222 9934",  # Your Twilio number
        "message": "Hello, this is a test voice call from the Blood Donation System!"
    }
    
    try:
        response = requests.post(url, json=data)
        result = response.json()
        
        if response.status_code == 200:
            print("✅ FastAPI voice call test successful!")
            print(f"   Call SID: {result.get('call_sid')}")
            print(f"   Message: {result.get('message')}")
        else:
            print(f"❌ FastAPI voice call test failed: {result}")
            
    except Exception as e:
        print(f"❌ Error testing FastAPI voice call: {e}")

def test_fastapi_voice_endpoint():
    """Test the FastAPI voice TwiML endpoint"""
    print("\n🧪 Testing FastAPI voice TwiML endpoint...")
    
    url = "http://localhost:8000/voice"
    
    try:
        response = requests.get(url)
        
        if response.status_code == 200:
            print("✅ FastAPI voice TwiML endpoint test successful!")
            print(f"   Content-Type: {response.headers.get('Content-Type')}")
            print(f"   Response: {response.text[:100]}...")
        else:
            print(f"❌ FastAPI voice TwiML endpoint test failed: {response.status_code}")
            
    except Exception as e:
        print(f"❌ Error testing FastAPI voice TwiML endpoint: {e}")

def main():
    print("=" * 60)
    print("🧪 VOICE CALL FUNCTIONALITY TEST")
    print("=" * 60)
    
    test_fastapi_voice_call()
    test_fastapi_voice_endpoint()
    
    print("\n" + "=" * 60)
    print("✅ Test completed!")
    print("=" * 60)

if __name__ == "__main__":
    main()
