#!/usr/bin/env python3
"""
Debug session management
"""

import requests
import json

BASE_URL = "http://localhost:5001"

def debug_session():
    print("🔍 Debugging Session Management")
    print("=" * 40)
    
    session = requests.Session()
    
    # Test admin login and check session
    print("\n1️⃣ Testing Admin Login...")
    login_data = {
        "email": "testadmin@hospital.com",
        "password": "admin123"
    }
    
    try:
        response = session.post(f"{BASE_URL}/admin/login", data=login_data)
        print(f"Status Code: {response.status_code}")
        print(f"Headers: {dict(response.headers)}")
        print(f"Cookies: {session.cookies.get_dict()}")
        
        # Check if we got redirected or got the login page
        if "admin_dashboard" in response.text or "Dashboard" in response.text:
            print("✅ Login successful - redirected to dashboard")
        else:
            print("❌ Login failed - still on login page")
            
    except Exception as e:
        print(f"❌ Error: {e}")
    
    # Test admin dashboard access
    print("\n2️⃣ Testing Admin Dashboard Access...")
    try:
        response = session.get(f"{BASE_URL}/admin/dashboard")
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            print("✅ Admin dashboard accessible")
        else:
            print("❌ Admin dashboard not accessible")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    debug_session()
