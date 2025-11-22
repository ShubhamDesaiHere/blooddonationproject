#!/usr/bin/env python3
"""
Test with default admin account
"""

import requests
import json

BASE_URL = "http://localhost:5001"

def test_default_admin():
    print("🔍 Testing Default Admin Account")
    print("=" * 40)
    
    session = requests.Session()
    
    # Test with default admin credentials
    print("\n1️⃣ Testing Default Admin Login...")
    login_data = {
        "email": "admin@blooddonation.com",
        "password": "admin123"
    }
    
    try:
        response = session.post(f"{BASE_URL}/admin/login", data=login_data)
        print(f"Status Code: {response.status_code}")
        print(f"Headers: {dict(response.headers)}")
        print(f"Cookies: {session.cookies.get_dict()}")
        
        # Check if we got redirected or got the login page
        if "admin_dashboard" in response.text or "Dashboard" in response.text:
            print("✅ Default admin login successful")
        else:
            print("❌ Default admin login failed")
            
    except Exception as e:
        print(f"❌ Error: {e}")
    
    # Test admin dashboard access
    print("\n2️⃣ Testing Admin Dashboard Access...")
    try:
        response = session.get(f"{BASE_URL}/admin/dashboard")
        print(f"Status Code: {response.status_code}")
        
        if response.status_code == 200:
            print("✅ Admin dashboard accessible")
            
            # Test admin users page
            print("\n3️⃣ Testing Admin Users Page...")
            response = session.get(f"{BASE_URL}/admin/users")
            print(f"Status Code: {response.status_code}")
            
            if response.status_code == 200:
                print("✅ Admin users page accessible")
            else:
                print("❌ Admin users page not accessible")
        else:
            print("❌ Admin dashboard not accessible")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    test_default_admin()
