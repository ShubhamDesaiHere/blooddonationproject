#!/usr/bin/env python3
"""
Comprehensive test script for the blood donation system
Tests the complete flow: Admin creation -> Donor creation -> Request -> Acceptance -> Form submission -> View
"""

import requests
import json
import time
from datetime import datetime

# Base URL
BASE_URL = "http://localhost:5001"

def test_complete_flow():
    """Test the complete blood donation flow"""
    print("🧪 Starting Comprehensive Blood Donation System Test")
    print("=" * 60)
    
    admin_session = requests.Session()
    donor_session = requests.Session()
    
    # Test 1: Create Admin Account
    print("\n1️⃣ Creating Admin Account...")
    unique_suffix = datetime.now().strftime('%Y%m%d%H%M%S')
    admin_email = f"testadmin_{unique_suffix}@hospital.com"
    donor_email = f"testdonor_{unique_suffix}@email.com"

    # Use identical coordinates so the donor is always in range
    latitude = "12.9716"
    longitude = "77.5946"

    admin_data = {
        "name": "Test Hospital Admin",
        "email": admin_email,
        "password": "admin123",
        "hospital_name": "Test Hospital",
        "hospital_id": f"HOSP{unique_suffix[-4:]}",
        "phone": "9876543210",
        "address": "Test Hospital Address, Test City",
        "latitude": latitude,
        "longitude": longitude
    }
    
    try:
        response = admin_session.post(f"{BASE_URL}/admin/signup", data=admin_data)
        if response.status_code == 200:
            print("✅ Admin account created successfully")
        else:
            print(f"❌ Admin creation failed: {response.status_code}")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"❌ Error creating admin: {e}")
        return False
    
    # Test 2: Login as Admin
    print("\n2️⃣ Logging in as Admin...")
    login_data = {
        "email": admin_email,
        "password": "admin123"
    }
    
    try:
        response = admin_session.post(f"{BASE_URL}/admin/login", data=login_data, allow_redirects=False)
        if response.status_code in (301, 302, 303, 307, 308):
            print("✅ Admin login successful (redirect)")
        elif response.status_code == 200 and ("Dashboard" in response.text or "admin_dashboard" in response.text):
            print("✅ Admin login successful (200 page)")
        else:
            print(f"❌ Admin login failed: {response.status_code}")
            print(f"Response: {response.text}")
            return False

        # Cement session by hitting a protected page
        dash = admin_session.get(f"{BASE_URL}/admin/dashboard")
        if dash.status_code == 200:
            print("✅ Admin dashboard reachable; session established")
        else:
            print(f"❌ Admin dashboard unreachable after login: {dash.status_code}")
            print(f"Response: {dash.text}")
            return False
    except Exception as e:
        print(f"❌ Error logging in admin: {e}")
        return False
    
    # Test 3: Create Donor Account
    print("\n3️⃣ Creating Donor Account...")
    donor_data = {
        "name": "Test Donor",
        "email": donor_email,
        "password": "donor123",
        "blood_group": "O+",
        "phone": "9876543211",
        "location": "Test Donor Address, Test City",
        "age": "25",
        "gender": "male",
        "height": "175",
        "weight": "70",
        "latitude": latitude,
        "longitude": longitude
    }
    
    try:
        response = donor_session.post(f"{BASE_URL}/signup", data=donor_data)
        if response.status_code == 200:
            print("✅ Donor account created successfully")
        else:
            print(f"❌ Donor creation failed: {response.status_code}")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"❌ Error creating donor: {e}")
        return False
    
    # Test 4: Login as Donor
    print("\n4️⃣ Logging in as Donor...")
    donor_login_data = {
        "email": donor_email,
        "password": "donor123"
    }
    
    try:
        response = donor_session.post(f"{BASE_URL}/login", data=donor_login_data)
        if response.status_code == 200:
            print("✅ Donor login successful")
        else:
            print(f"❌ Donor login failed: {response.status_code}")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"❌ Error logging in donor: {e}")
        return False
    
    # Test 5: Admin raises blood request
    print("\n5️⃣ Admin raising blood request...")
    request_data = {
        "blood_group": "O+",
        "distance": "1000",
        "message": "Urgent blood request for emergency surgery"
    }
    
    try:
        response = admin_session.post(f"{BASE_URL}/admin/search_donors", data=request_data)
        if response.status_code == 200:
            data = response.json()
            print("✅ Blood request raised successfully")
            print(f"🔎 Donors matched: {data.get('count')}\n")
            time.sleep(1)
        else:
            print(f"❌ Blood request failed: {response.status_code}")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"❌ Error raising blood request: {e}")
        return False
    
    # Test 6: Donor accepts request
    print("\n6️⃣ Donor accepting request...")
    # First get pending requests
    try:
        # Poll for pending requests up to 10 seconds
        request_id = None
        for i in range(10):
            response = donor_session.get(f"{BASE_URL}/user/pending_requests")
            if response.status_code != 200:
                print(f"❌ Failed to get pending requests: {response.status_code}")
                time.sleep(1)
                continue
            data = response.json()
            if data.get('success') and data.get('requests'):
                request_id = data['requests'][0]['request_id']
                print(f"✅ Found pending request: {request_id}")
                break
            time.sleep(1)

        if not request_id:
            print("❌ No pending requests found after polling")
            return False

        # Accept the request
        accept_data = {
            "request_id": request_id,
            "response": "accepted"
        }
        response = donor_session.post(f"{BASE_URL}/user/respond_to_request", json=accept_data)
        if response.status_code == 200:
            print("✅ Donor accepted request successfully")
        else:
            print(f"❌ Request acceptance failed: {response.status_code}")
            print(f"Response: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error accepting request: {e}")
        return False
    
    # Test 7: Donor fills and submits form
    print("\n7️⃣ Donor filling and submitting form...")
    form_data = {
        "request_id": request_id,
        "personal_info": {
            "full_name": "Test Donor",
            "date_of_birth": "1998-01-01",
            "gender": "male",
            "weight": "70",
            "address": "Test Donor Address",
            "contact_no": "9876543211",
            "id_proof": "Aadhar"
        },
        "general_eligibility": {
            "recent_donation": "no",
            "weight_50kg": "yes",
            "proper_meal": "yes",
            "sleep_hours": "yes",
            "healthy_today": "yes"
        },
        "medical_history": {
            "chronic_illness": "no",
            "major_surgery": "no",
            "blood_transfusion": "no",
            "current_medicines": "no",
            "infectious_diseases": "no"
        },
        "infectious_disease_risk": {
            "jaundice": "no",
            "hiv_positive": "no",
            "tattoo_piercing": "no",
            "dental_surgery": "no",
            "iv_drugs": "no",
            "high_risk_group": "no",
            "unprotected_sex": "no"
        },
        "travel_lifestyle": {
            "malaria_travel": "no",
            "international_travel": "no",
            "alcohol_consumption": "no",
            "smoking": "no"
        },
        "women_donors": {},
        "current_health": {
            "current_symptoms": "no",
            "recent_vaccination": "no",
            "recent_antibiotics": "no"
        },
        "consent_declaration": {
            "donor_signature": "Test Donor",
            "signature_date": datetime.now().strftime('%Y-%m-%d'),
            "consent_terms": True
        },
        "staff_checks": {}
    }
    
    try:
        response = donor_session.post(f"{BASE_URL}/user/submit_blood_donation_form", json=form_data)
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                print("✅ Form submitted successfully")
            else:
                print(f"❌ Form submission failed: {data.get('error')}")
        else:
            print(f"❌ Form submission failed: {response.status_code}")
            print(f"Response: {response.text}")
    except Exception as e:
        print(f"❌ Error submitting form: {e}")
        return False
    
    # Test 8: Admin views users and forms
    print("\n8️⃣ Admin viewing users and forms...")
    try:
        response = admin_session.get(f"{BASE_URL}/admin/users")
        if response.status_code == 200:
            print("✅ Admin users page loaded successfully")
            # Check if the page contains form data
            if "Blood Donation Forms" in response.text:
                print("✅ Donation forms section found in admin users page")
            else:
                print("❌ Donation forms section not found in admin users page")
        else:
            print(f"❌ Failed to load admin users page: {response.status_code}")
    except Exception as e:
        print(f"❌ Error loading admin users page: {e}")
        return False
    
    # Test 9: Check if form data is accessible via API
    print("\n9️⃣ Testing form data API...")
    try:
        # First get user details to get form_id
        response = admin_session.get(f"{BASE_URL}/admin/user/{donor_email}")
        if response.status_code == 200:
            data = response.json()
            if data.get('success') and data.get('blood_forms'):
                form_id = data['blood_forms'][0]['form_id']
                print(f"✅ Found form ID: {form_id}")
                
                # Test form details API
                response = admin_session.get(f"{BASE_URL}/admin/donation_form/{form_id}")
                if response.status_code == 200:
                    form_data = response.json()
                    if form_data.get('success'):
                        print("✅ Form details API working correctly")
                        print(f"✅ Form contains: {len(form_data['form']['form_data'])} sections")
                    else:
                        print(f"❌ Form details API failed: {form_data.get('error')}")
                else:
                    print(f"❌ Form details API failed: {response.status_code}")
            else:
                print("❌ No forms found for donor")
        else:
            print(f"❌ Failed to get user details: {response.status_code}")
    except Exception as e:
        print(f"❌ Error testing form API: {e}")
        return False
    
    print("\n🎉 Test completed!")
    return True

if __name__ == "__main__":
    test_complete_flow()
