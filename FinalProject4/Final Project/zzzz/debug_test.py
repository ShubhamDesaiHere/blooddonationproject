#!/usr/bin/env python3
"""
Debug test to check admin creation and login
"""

import requests
import json

BASE_URL = "http://localhost:5001"

def debug_admin_flow():
    print("🔍 Debugging Admin Flow")
    print("=" * 40)
    
    # Test 1: Check if admin signup works
    print("\n1️⃣ Testing Admin Signup...")
    admin_data = {
        "name": "Debug Admin",
        "email": "debugadmin@test.com",
        "password": "admin123",
        "hospital_name": "Debug Hospital",
        "hospital_id": "HOSP001",
        "phone": "9876543210",
        "address": "Debug Hospital Address",
        "latitude": "12.9716",
        "longitude": "77.5946"
    }
    
    try:
        response = requests.post(f"{BASE_URL}/admin/signup", data=admin_data)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text[:500]}...")
        
        if response.status_code == 200:
            print("✅ Admin signup successful")
        else:
            print("❌ Admin signup failed")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    # Test 2: Check if admin login works
    print("\n2️⃣ Testing Admin Login...")
    login_data = {
        "email": "debugadmin@test.com",
        "password": "admin123"
    }
    
    session = requests.Session()
    try:
        response = session.post(f"{BASE_URL}/admin/login", data=login_data)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text[:500]}...")
        
        if response.status_code == 200:
            print("✅ Admin login successful")
            
            # Test 3: Check if admin dashboard is accessible
            print("\n3️⃣ Testing Admin Dashboard Access...")
            response = session.get(f"{BASE_URL}/admin/dashboard")
            print(f"Status Code: {response.status_code}")
            
            if response.status_code == 200:
                print("✅ Admin dashboard accessible")
            else:
                print("❌ Admin dashboard not accessible")
                print(f"Response: {response.text}")
        else:
            print("❌ Admin login failed")
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    debug_admin_flow()
