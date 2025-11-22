#!/usr/bin/env python3
"""
Check database contents
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from pymongo import MongoClient
from bson import ObjectId

# MongoDB connection
MONGODB_URI = "mongodb://localhost:27017/"
client = MongoClient(MONGODB_URI)
db = client.blood_donation
admins = db.admins

def check_database():
    print("🔍 Checking Database Contents")
    print("=" * 40)
    
    # Check if admins collection exists and has data
    print("\n1️⃣ Checking Admins Collection...")
    admin_count = admins.count_documents({})
    print(f"Total admins in database: {admin_count}")
    
    if admin_count > 0:
        print("\n2️⃣ Listing All Admins...")
        for admin in admins.find():
            print(f"  - Email: {admin.get('email')}")
            print(f"    Name: {admin.get('name')}")
            print(f"    Hospital: {admin.get('hospital_name')}")
            print(f"    Password: {admin.get('password')}")
            print(f"    ID: {admin.get('_id')}")
            print()
    else:
        print("❌ No admins found in database")
    
    # Check if default admin exists
    print("\n3️⃣ Checking Default Admin...")
    default_admin = admins.find_one({'email': 'admin@blooddonation.com'})
    if default_admin:
        print("✅ Default admin found")
        print(f"  - Email: {default_admin.get('email')}")
        print(f"  - Password: {default_admin.get('password')}")
    else:
        print("❌ Default admin not found")

if __name__ == "__main__":
    check_database()
