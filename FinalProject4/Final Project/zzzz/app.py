from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_from_directory
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
import uuid
from pymongo import MongoClient
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
import os
from dotenv import load_dotenv
from bson import ObjectId
from werkzeug.utils import secure_filename
from math import radians, sin, cos, sqrt, atan2, asin
from datetime import datetime, timedelta, UTC
from functools import wraps
import json
from config import Config
import requests
from utils import (
    hash_password, verify_password, validate_email, validate_phone,
    validate_coordinates, admin_required, user_required, log_error,
    format_datetime, sanitize_filename, logger
)
import time
import pywhatkit
import google.generativeai as genai
from translate import Translator
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
# from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

load_dotenv()
load_dotenv("twilio.env")  # Load Twilio-specific environment variables

# Debug: Check if Twilio credentials are loaded
print(f"TWILIO_ACCOUNT_SID: {'✓' if os.getenv('TWILIO_ACCOUNT_SID') else '✗'}")
print(f"TWILIO_AUTH_TOKEN: {'✓' if os.getenv('TWILIO_AUTH_TOKEN') else '✗'}")
print(f"TWILIO_PHONE_NUMBER: {'✓' if os.getenv('TWILIO_PHONE_NUMBER') else '✗'}")

# If credentials are not loaded, try loading from the correct path
if not os.getenv('TWILIO_ACCOUNT_SID'):
    print("Trying to load Twilio credentials from absolute path...")
    load_dotenv(os.path.join(os.path.dirname(__file__), "twilio.env"))
    print(f"After retry - TWILIO_ACCOUNT_SID: {'✓' if os.getenv('TWILIO_ACCOUNT_SID') else '✗'}")

app = Flask(__name__)
app.config.from_object(Config)

# Configure Gemini AI
genai.configure(api_key=os.getenv('GOOGLE_API_KEY'))
model = genai.GenerativeModel('gemini-pro')  # Using the basic model name

# Fast2SMS Configuration
FAST2SMS_API_KEY = os.getenv('FAST2SMS_API_KEY')
FAST2SMS_API_URL = "https://www.fast2sms.com/dev/bulkV2"

# Configure upload folder
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Add translation cache
translation_cache = {}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def parse_datetime(value):
    """
    Normalize various datetime formats to timezone-aware UTC datetime objects.
    Accepts datetime instances or ISO strings (with/without timezone).
    """
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, str):
        trimmed = value.strip()
        # Handle trailing Z (Zulu/UTC indicator)
        if trimmed.endswith('Z'):
            trimmed = trimmed[:-1] + '+00:00'
        try:
            parsed = datetime.fromisoformat(trimmed)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            else:
                parsed = parsed.astimezone(UTC)
            return parsed
        except ValueError:
            # Fallback: try parsing without separators (e.g., "2024-05-01 10:00:00")
            try:
                parsed = datetime.strptime(trimmed, "%Y-%m-%d %H:%M:%S")
                return parsed.replace(tzinfo=UTC)
            except ValueError:
                raise
    raise ValueError(f"Unsupported datetime value: {value}")

# Add this function after the MongoDB connection setup
def ensure_default_admin():
    try:
        # Retry logic for connection issues
        max_retries = 3
        retry_delay = 2  # seconds
        
        for attempt in range(max_retries):
            try:
                # Check if default admin exists
                default_admin = admins.find_one({'email': 'admin@blooddonation.com'})
                default_inventory = {
                    'A+': 0, 'A-': 0, 'B+': 0, 'B-': 0,
                    'AB+': 0, 'AB-': 0, 'O+': 0, 'O-': 0
                }
                if not default_admin:
                    print("Default admin not found. Creating...")
                    default_admin_data = {
                        'email': 'admin@blooddonation.com',
                        'password': 'admin123',  # Store plain password as per requirements
                        'name': 'Admin',
                        'hospital_name': 'System Admin',
                        'hospital_id': 'ADMIN001',
                        'phone': '1234567890',
                        'address': 'System',
                        'location': {
                            'type': 'Point',
                            'coordinates': [0, 0],
                            'address': 'System'
                        },
                        'verification_doc': 'default.pdf',
                        'status': 'active',  # Set status to active
                        'created_at': datetime.now(UTC),
                        'blood_inventory': default_inventory
                    }
                    result = admins.insert_one(default_admin_data)
                    print(f"Created default admin account with ID: {result.inserted_id}")
                else:
                    print("Default admin account exists")
                    # Ensure the default admin is active and has inventory
                    update_fields = {'status': 'active'}
                    if 'blood_inventory' not in default_admin:
                        update_fields['blood_inventory'] = default_inventory
                    admins.update_one(
                        {'email': 'admin@blooddonation.com'},
                        {'$set': update_fields}
                    )
                return  # Success, exit the function
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"Attempt {attempt + 1} failed: {str(e)}")
                    print(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                else:
                    # Last attempt failed
                    raise
    except Exception as e:
        print(f"Error ensuring default admin after {max_retries} attempts: {str(e)}")
        # Don't raise here - let the app continue even if admin setup fails
        # The admin can be created manually later if needed

# Call the function after MongoDB connection
try:
    # Configure MongoDB connection with enhanced SSL/TLS handling for Windows
    # Add connection parameters to handle SSL handshake issues
    connection_params = {
        'tlsAllowInvalidCertificates': True,
        'serverSelectionTimeoutMS': 30000,  # 30 seconds (increased from 10)
        'connectTimeoutMS': 20000,  # 20 seconds (increased from 10)
        'socketTimeoutMS': 30000,  # 30 seconds
        'retryWrites': True,
        'retryReads': True,
        'maxPoolSize': 50,
        'minPoolSize': 10,
        'directConnection': False,  # Allow replica set connections
    }
    
    # Try to connect with enhanced parameters
    # Note: MongoClient connects lazily, so this won't fail immediately
    client = MongoClient(app.config['MONGODB_URI'], **connection_params)
    
    # Test the connection by pinging the server (with error handling)
    try:
        client.admin.command('ping')
        print("Successfully connected to MongoDB")
    except Exception as ping_error:
        print(f"Warning: Could not ping MongoDB server: {str(ping_error)}")
        print("Connection will be attempted when first used. This might be a network issue.")
        print("The app will continue, but database operations may fail until connection is established.")
    
    db = client.blood_donation
    users = db.users
    admins = db.admins
    notifications = db.notifications
    donation_history = db.donation_history  # New collection for donation history
    notice_cards = db.notice_cards  # New collection for notice cards
    blood_donation_forms = db.blood_donation_forms  # New collection for blood donation forms
    # Note: Blood inventory is now stored as subdocument array in admin collection
    
    # Ensure default admin exists (with retry logic)
    # This will also test the actual connection
    try:
        ensure_default_admin()
        print("Database setup completed successfully")
    except Exception as admin_error:
        print(f"Warning: Could not ensure default admin: {str(admin_error)}")
        print("This might be due to a temporary connection issue. The app will continue to run.")
        print("You may need to check your MongoDB Atlas IP whitelist and network settings.")
    
except Exception as e:
    print(f"MongoDB connection error: {str(e)}")
    print("\nTroubleshooting tips:")
    print("1. Check your internet connection")
    print("2. Verify your MongoDB Atlas IP whitelist includes your current IP address")
    print("   - Go to MongoDB Atlas Dashboard -> Network Access -> Add IP Address")
    print("   - You can use '0.0.0.0/0' for testing (not recommended for production)")
    print("3. Check if your firewall is blocking MongoDB connections (port 27017)")
    print("4. Verify your MONGODB_URI in the .env file is correct")
    print("5. Try accessing MongoDB Atlas dashboard to ensure the cluster is running")
    print("6. If behind a corporate firewall/VPN, you may need to configure proxy settings")
    raise Exception("Failed to connect to MongoDB. Please check your connection string and network.")

# Create indexes for better query performance
try:
    users.create_index([("email", 1)], unique=True)
    users.create_index([("location", "2dsphere")])
    users.create_index([("blood_group", 1)])
    users.create_index([("age", 1)])
    users.create_index([("gender", 1)])
    users.create_index([("last_donation_date", 1)])
    users.create_index([("weight", 1)])
    users.create_index([("height", 1)])
    admins.create_index([("email", 1)], unique=True)
    admins.create_index([("hospital_id", 1)], unique=True)
    donation_history.create_index([("user_id", 1)])
    donation_history.create_index([("donation_date", 1)])
    notice_cards.create_index([("created_at", -1)])  # Index for notice cards by creation date
    # Note: Blood inventory is stored as subdocument array, no separate indexes needed
    print("Database indexes created successfully")
except Exception as e:
    print(f"Error creating indexes: {str(e)}")

def translate_text(text, target_language):
    """
    Translate text to the target language using translate package with caching
    """
    try:
        # Return original text if target language is English
        if target_language == 'en':
            return text
            
        # Check cache first
        cache_key = f"{text}_{target_language}"
        if cache_key in translation_cache:
            return translation_cache[cache_key]
            
        # Add delay to avoid rate limiting
        time.sleep(0.5)
        
        # Create a translator instance for the target language
        translator = Translator(to_lang=target_language)
        
        # Attempt translation
        result = translator.translate(text)
        
        # Cache the result
        translation_cache[cache_key] = result
        return result
        
    except Exception as e:
        print(f"Translation error for '{text}': {str(e)}")
        # Return original text on error
        return text

def get_translations(language):
    """
    Get all translations for the current language with caching
    """
    # Check if we have cached translations for this language
    cache_key = f"translations_{language}"
    if cache_key in translation_cache:
        return translation_cache[cache_key]
        
    # If language is English, return original text
    if language == 'en':
        translations = {
            'title': 'Blood Donation System',
            'welcome': 'Welcome',
            'login': 'Login',
            'signup': 'Sign Up',
            'logout': 'Logout',
            'email': 'Email',
            'password': 'Password',
            'submit': 'Submit',
            'back': 'Back',
            'cancel': 'Cancel',
            'save': 'Save',
            'edit': 'Edit',
            'delete': 'Delete',
            'view': 'View',
            'search': 'Search',
            'filter': 'Filter',
            'dashboard': 'Dashboard',
            'profile': 'Profile',
            'admin_portal': 'Admin Portal',
            'home': 'Home',
            'about': 'About',
            'contact': 'Contact',
            'name': 'Name',
            'blood_group': 'Blood Group',
            'phone': 'Phone',
            'address': 'Address',
            'age': 'Age',
            'gender': 'Gender',
            'height': 'Height',
            'weight': 'Weight',
            'last_donation': 'Last Donation Date',
            'success_message': 'Operation completed successfully',
            'error_message': 'An error occurred',
            'login_required': 'Please login first',
            'invalid_credentials': 'Invalid email or password',
            'login_success': 'Login successful!',
            'logout_success': 'Logout successful!',
            'email_exists': 'Email already exists',
            'registration_success': 'Registration successful!',
            'profile_updated': 'Profile updated successfully!',
            'password_changed': 'Password changed successfully!',
            'donation_history': 'Donation History',
            'upcoming_camps': 'Upcoming Blood Camps',
            'pending_requests': 'Pending Requests',
            'total_donations': 'Total Donations',
            'next_eligible': 'Next Eligible Date',
            'blood_inventory': 'Blood Inventory',
            'recent_activity': 'Recent Activity',
            'manage_users': 'Manage Users',
            'manage_camps': 'Manage Blood Camps',
            'manage_requests': 'Manage Requests',
            'send_notification': 'Send Notification',
            'hospital_name': 'Hospital Name',
            'hospital_id': 'Hospital ID',
            'verification': 'Verification',
            'hero_title': 'Save Lives Through Blood Donation',
            'hero_subtitle': 'Join our community of life-savers today',
            'features_title': 'Our Features',
            'how_it_works': 'How It Works',
            'testimonials': 'Testimonials',
            'join_us': 'Join Us Today',
            'footer_about': 'About Us',
            'footer_contact': 'Contact Us',
            'footer_privacy': 'Privacy Policy',
            'footer_terms': 'Terms of Service',
            'footer_rights': 'All Rights Reserved',
            'required_field': 'This field is required',
            'invalid_email_format': 'Invalid email format',
            'invalid_phone_format': 'Invalid phone number format',
            'password_short': 'Password must be at least 8 characters',
            'password_mismatch': 'Passwords do not match',
            'invalid_age': 'Age must be between 18 and 65',
            'invalid_weight': 'Weight must be at least 45 kg',
            'invalid_height': 'Height must be between 140 and 220 cm',
            'donation_date': 'Donation Date',
            'donation_time': 'Donation Time',
            'donation_location': 'Donation Location',
            'donation_status': 'Status',
            'blood_type': 'Blood Type',
            'units': 'Units',
            'donor_info': 'Donor Information',
            'recipient_info': 'Recipient Information',
            'notifications': 'Notifications',
            'mark_read': 'Mark as Read',
            'clear_all': 'Clear All',
            'no_notifications': 'No new notifications',
            'accept': 'Accept',
            'reject': 'Reject',
            'approve': 'Approve',
            'deny': 'Deny',
            'confirm': 'Confirm',
            'update': 'Update',
            'learn_more': 'Learn More',
            'get_started': 'Get Started',
            'read_more': 'Read More',
            'today': 'Today',
            'yesterday': 'Yesterday',
            'days_ago': 'days ago',
            'date': 'Date',
            'time': 'Time',
            'duration': 'Duration',
            'pending': 'Pending',
            'approved': 'Approved',
            'rejected': 'Rejected',
            'completed': 'Completed',
            'cancelled': 'Cancelled',
            'in_progress': 'In Progress',
            'enter_email': 'Enter your email',
            'enter_password': 'Enter your password',
            'enter_name': 'Enter your name',
            'enter_phone': 'Enter your phone number',
            'select_blood_group': 'Select blood group',
            'enter_address': 'Enter your address',
            '404_title': 'Page Not Found',
            '404_message': 'The page you are looking for does not exist',
            '500_title': 'Server Error',
            '500_message': 'Something went wrong on our end',
            'help_password': 'Password must be at least 8 characters long',
            'help_blood_group': 'Select your blood group from the list',
            'help_phone': 'Enter a valid phone number',
            'help_email': 'Enter a valid email address'
        }
    elif language == 'hi':
        translations = {
            'title': 'रक्तदान प्रणाली',
            'welcome': 'स्वागत है',
            'login': 'लॉगिन',
            'signup': 'साइन अप',
            'logout': 'लॉगआउट',
            'email': 'ईमेल',
            'password': 'पासवर्ड',
            'submit': 'जमा करें',
            'back': 'वापस',
            'cancel': 'रद्द करें',
            'save': 'सहेजें',
            'edit': 'संपादित करें',
            'delete': 'हटाएं',
            'view': 'देखें',
            'search': 'खोजें',
            'filter': 'फ़िल्टर',
            'dashboard': 'डैशबोर्ड',
            'profile': 'प्रोफ़ाइल',
            'admin_portal': 'एडमिन पोर्टल',
            'home': 'होम',
            'about': 'हमारे बारे में',
            'contact': 'संपर्क',
            'name': 'नाम',
            'blood_group': 'रक्त समूह',
            'phone': 'फ़ोन',
            'address': 'पता',
            'age': 'उम्र',
            'gender': 'लिंग',
            'height': 'ऊंचाई',
            'weight': 'वजन',
            'last_donation': 'अंतिम दान तिथि',
            'success_message': 'कार्य सफलतापूर्वक पूर्ण हुआ',
            'error_message': 'एक त्रुटि हुई',
            'login_required': 'कृपया पहले लॉगिन करें',
            'invalid_credentials': 'अमान्य ईमेल या पासवर्ड',
            'login_success': 'लॉगिन सफल!',
            'logout_success': 'लॉगआउट सफल!',
            'email_exists': 'ईमेल पहले से मौजूद है',
            'registration_success': 'पंजीकरण सफल!',
            'profile_updated': 'प्रोफ़ाइल सफलतापूर्वक अपडेट की गई!',
            'password_changed': 'पासवर्ड सफलतापूर्वक बदल गया!',
            'donation_history': 'दान इतिहास',
            'upcoming_camps': 'आगामी रक्त शिविर',
            'pending_requests': 'लंबित अनुरोध',
            'total_donations': 'कुल दान',
            'next_eligible': 'अगली पात्रता तिथि',
            'blood_inventory': 'रक्त सूची',
            'recent_activity': 'हाल की गतिविधियां',
            'manage_users': 'उपयोगकर्ता प्रबंधन',
            'manage_camps': 'शिविर प्रबंधन',
            'manage_requests': 'अनुरोध प्रबंधन',
            'send_notification': 'सूचना भेजें',
            'hospital_name': 'अस्पताल का नाम',
            'hospital_id': 'अस्पताल आईडी',
            'verification': 'सत्यापन',
            'hero_title': 'रक्तदान के माध्यम से जीवन बचाएं',
            'hero_subtitle': 'आज ही हमारे जीवन-रक्षक समुदाय में शामिल हों',
            'features_title': 'हमारी विशेषताएं',
            'how_it_works': 'यह कैसे काम करता है',
            'testimonials': 'प्रशंसापत्र',
            'join_us': 'आज ही जुड़ें',
            'footer_about': 'हमारे बारे में',
            'footer_contact': 'संपर्क करें',
            'footer_privacy': 'गोपनीयता नीति',
            'footer_terms': 'नियम और शर्तें',
            'footer_rights': 'सर्वाधिकार सुरक्षित',
            'required_field': 'यह फ़ील्ड आवश्यक है',
            'invalid_email_format': 'अमान्य ईमेल प्रारूप',
            'invalid_phone_format': 'अमान्य फ़ोन नंबर प्रारूप',
            'password_short': 'पासवर्ड कम से कम 8 अक्षर का होना चाहिए',
            'password_mismatch': 'पासवर्ड मेल नहीं खाते',
            'invalid_age': 'उम्र 18 से 65 के बीच होनी चाहिए',
            'invalid_weight': 'वजन कम से कम 45 किलो होना चाहिए',
            'invalid_height': 'ऊंचाई 140 से 220 सेमी के बीच होनी चाहिए',
            'donation_date': 'दान तिथि',
            'donation_time': 'दान समय',
            'donation_location': 'दान स्थान',
            'donation_status': 'स्थिति',
            'blood_type': 'रक्त प्रकार',
            'units': 'इकाइयां',
            'donor_info': 'दाता जानकारी',
            'recipient_info': 'प्राप्तकर्ता जानकारी',
            'notifications': 'सूचनाएं',
            'mark_read': 'पढ़ा हुआ चिह्नित करें',
            'clear_all': 'सभी साफ़ करें',
            'no_notifications': 'कोई नई सूचना नहीं',
            'accept': 'स्वीकार करें',
            'reject': 'अस्वीकार करें',
            'approve': 'स्वीकृत करें',
            'deny': 'अस्वीकार करें',
            'confirm': 'पुष्टि करें',
            'update': 'अपडेट करें',
            'learn_more': 'और जानें',
            'get_started': 'शुरू करें',
            'read_more': 'और पढ़ें',
            'today': 'आज',
            'yesterday': 'कल',
            'days_ago': 'दिन पहले',
            'date': 'तिथि',
            'time': 'समय',
            'duration': 'अवधि',
            'pending': 'लंबित',
            'approved': 'मंजूर',
            'rejected': 'नाकारले',
            'completed': 'पूर्ण',
            'cancelled': 'रद्द',
            'in_progress': 'प्रगतीपथावर',
            'enter_email': 'अपना ईमेल दर्ज करें',
            'enter_password': 'अपना पासवर्ड दर्ज करें',
            'enter_name': 'अपना नाम दर्ज करें',
            'enter_phone': 'अपना फ़ोन नंबर दर्ज करें',
            'select_blood_group': 'रक्त समूह निवडा',
            'enter_address': 'अपना पत्ता दर्ज करें',
            '404_title': 'पृष्ठ सापडले नाही',
            '404_message': 'तुम्ही शोधत असलेले पृष्ठ अस्तित्वात नाही',
            '500_title': 'सर्व्हर त्रुटी',
            '500_message': 'काहीतरी चूक झाली',
            'help_password': 'पासवर्ड किमान 8 वर्णांचा असावा',
            'help_blood_group': 'सूचीतून तुमचा रक्त गट निवडा',
            'help_phone': 'वैध फोन नंबर प्रविष्ट करें',
            'help_email': 'वैध ईमेल पत्ता प्रविष्ट करें'
        }
    elif language == 'mr':
        translations = {
            'title': 'रक्तदान प्रणाली',
            'welcome': 'स्वागत आहे',
            'login': 'लॉगिन',
            'signup': 'साइन अप',
            'logout': 'लॉगआउट',
            'email': 'ईमेल',
            'password': 'पासवर्ड',
            'submit': 'सबमिट करा',
            'back': 'मागे',
            'cancel': 'रद्द करा',
            'save': 'जतन करा',
            'edit': 'संपादित करा',
            'delete': 'हटवा',
            'view': 'पहा',
            'search': 'शोधा',
            'filter': 'फिल्टर',
            'dashboard': 'डॅशबोर्ड',
            'profile': 'प्रोफाइल',
            'admin_portal': 'एडमिन पोर्टल',
            'home': 'होम',
            'about': 'आमच्याबद्दल',
            'contact': 'संपर्क',
            'name': 'नाव',
            'blood_group': 'रक्त गट',
            'phone': 'फोन',
            'address': 'पत्ता',
            'age': 'वय',
            'gender': 'लिंग',
            'height': 'उंची',
            'weight': 'वजन',
            'last_donation': 'शेवटची दान तारीख',
            'success_message': 'ऑपरेशन यशस्वीरित्या पूर्ण झाले',
            'error_message': 'एक त्रुटी आली',
            'login_required': 'कृपया प्रथम लॉगिन करा',
            'invalid_credentials': 'अवैध ईमेल किंवा पासवर्ड',
            'login_success': 'लॉगिन यशस्वी!',
            'logout_success': 'लॉगआउट यशस्वी!',
            'email_exists': 'ईमेल आधीपासूनच अस्तित्वात आहे',
            'registration_success': 'नोंदणी यशस्वी!',
            'profile_updated': 'प्रोफाइल यशस्वीरित्या अपडेट केली!',
            'password_changed': 'पासवर्ड यशस्वीरित्या बदलला!',
            'donation_history': 'दान इतिहास',
            'upcoming_camps': 'आगामी रक्त शिबिरे',
            'pending_requests': 'प्रलंबित विनंती',
            'total_donations': 'एकूण दान',
            'next_eligible': 'पुढील पात्रता तारीख',
            'blood_inventory': 'रक्त सूची',
            'recent_activity': 'अलीकडील क्रियाकलाप',
            'manage_users': 'वापरकर्ते व्यवस्थापित करा',
            'manage_camps': 'शिबिरे व्यवस्थापित करा',
            'manage_requests': 'विनंती व्यवस्थापित करा',
            'send_notification': 'सूचना पाठवा',
            'hospital_name': 'रुग्णालयाचे नाव',
            'hospital_id': 'रुग्णालय आयडी',
            'verification': 'पडताळणी',
            'hero_title': 'रक्तदानाद्वारे जीवन वाचवा',
            'hero_subtitle': 'आजच आमच्या जीवन-वाचक समुदायात सामील व्हा',
            'features_title': 'आमची वैशिष्ट्ये',
            'how_it_works': 'हे कसे काम करते',
            'testimonials': 'प्रशंसापत्रे',
            'join_us': 'आजच सामील व्हा',
            'footer_about': 'आमच्याबद्दल',
            'footer_contact': 'संपर्क साधा',
            'footer_privacy': 'गोपनीयता धोरण',
            'footer_terms': 'नियम आणि अटी',
            'footer_rights': 'सर्व हक्क राखीव',
            'required_field': 'हे फील्ड आवश्यक आहे',
            'invalid_email_format': 'अवैध ईमेल स्वरूप',
            'invalid_phone_format': 'अवैध फोन नंबर स्वरूप',
            'password_short': 'पासवर्ड किमान 8 वर्णांचा असावा',
            'password_mismatch': 'पासवर्ड जुळत नाहीत',
            'invalid_age': 'वय 18 ते 65 दरम्यान असावे',
            'invalid_weight': 'वजन किमान 45 किलो असावे',
            'invalid_height': 'उंची 140 ते 220 सेमी दरम्यान असावी',
            'donation_date': 'दान तारीख',
            'donation_time': 'दान वेळ',
            'donation_location': 'दान स्थान',
            'donation_status': 'स्थिती',
            'blood_type': 'रक्त प्रकार',
            'units': 'एकके',
            'donor_info': 'दाता माहिती',
            'recipient_info': 'प्राप्तकर्ता माहिती',
            'notifications': 'सूचना',
            'mark_read': 'वाचले म्हणून चिन्हांकित करा',
            'clear_all': 'सर्व क्लिअर करा',
            'no_notifications': 'नवीन सूचना नाहीत',
            'accept': 'स्वीकारा',
            'reject': 'नाकारा',
            'approve': 'मंजूर करा',
            'deny': 'नाकारा',
            'confirm': 'पुष्टी करा',
            'update': 'अपडेट करा',
            'learn_more': 'अधिक जाणा',
            'get_started': 'प्रारंभ करा',
            'read_more': 'अधिक वाचा',
            'today': 'आज',
            'yesterday': 'काल',
            'days_ago': 'दिवस पूर्वी',
            'date': 'तारीख',
            'time': 'वेळ',
            'duration': 'कालावधी',
            'pending': 'प्रलंबित',
            'approved': 'मंजूर',
            'rejected': 'नाकारले',
            'completed': 'पूर्ण',
            'cancelled': 'रद्द',
            'in_progress': 'प्रगतीपथावर',
            'enter_email': 'तुमचा ईमेल प्रविष्ट करा',
            'enter_password': 'तुमचा पासवर्ड प्रविष्ट करा',
            'enter_name': 'तुमचे नाव प्रविष्ट करा',
            'enter_phone': 'तुमचा फोन नंबर प्रविष्ट करा',
            'select_blood_group': 'रक्त गट निवडा',
            'enter_address': 'तुमचा पत्ता प्रविष्ट करा',
            '404_title': 'पृष्ठ सापडले नाही',
            '404_message': 'तुम्ही शोधत असलेले पृष्ठ अस्तित्वात नाही',
            '500_title': 'सर्व्हर त्रुटी',
            '500_message': 'काहीतरी चूक झाली',
            'help_password': 'पासवर्ड किमान 8 वर्णांचा असावा',
            'help_blood_group': 'सूचीतून तुमचा रक्त गट निवडा',
            'help_phone': 'वैध फोन नंबर प्रविष्ट करें',
            'help_email': 'वैध ईमेल पत्ता प्रविष्ट करें'
        }
    else:
        translations = {}
        
    # Cache the translations
    translation_cache[cache_key] = translations
    return translations

@app.route('/api/set_language', methods=['POST'])
def set_language():
    """
    Set the user's preferred language
    """
    language = request.form.get('language', 'en')
    session['language'] = language
    return jsonify({'status': 'success'})

# Update the main route to include translations
@app.route('/')
def landing():
    language = session.get('language', 'en')
    translations = get_translations(language)
    return render_template('landing.html', translations=translations)

@app.route('/admin')
def admin_landing():
    language = session.get('language', 'en')
    translations = get_translations(language)
    return render_template('admin_landing.html', translations=translations)

# Update the login route to include translations
@app.route('/login', methods=['GET', 'POST'])
def login():
    language = session.get('language', 'en')
    translations = get_translations(language)
    
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        try:
            # Find user by email
            user = users.find_one({'email': email})
            
            if user and check_password_hash(user['password'], password):
                # Set user session
                session['user'] = str(user['_id'])
                session['user_name'] = user['name']
                session['user_email'] = user['email']
                
                # Log successful login
                print(f"User {user['email']} logged in successfully")
                
                # Flash success message
                flash(translations['login_success'])
                
                # Redirect to dashboard
                return redirect(url_for('dashboard'))
            else:
                # Log failed login attempt
                print(f"Failed login attempt for email: {email}")
                flash(translations['invalid_credentials'])
                
        except Exception as e:
            print(f"Login error: {str(e)}")
            flash(translations['error_message'])
    
    return render_template('login.html', translations=translations)

# Update the signup route to include translations
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    language = session.get('language', 'en')
    translations = get_translations(language)
    
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        blood_group = request.form.get('blood_group')
        phone = request.form.get('phone')
        location = request.form.get('location')
        latitude = request.form.get('latitude')
        longitude = request.form.get('longitude')
        
        # New fields
        age = int(request.form.get('age'))
        height = float(request.form.get('height'))
        weight = float(request.form.get('weight'))
        gender = request.form.get('gender')
        last_donation = request.form.get('last_donation')
        
        if users.find_one({'email': email}):
            flash(translations['email_exists'])
            return render_template('signup.html', translations=translations)
            
        hashed_password = generate_password_hash(password)
        
        new_user = {
            'name': name,
            'email': email,
            'password': hashed_password,
            'blood_group': blood_group,
            'phone': phone,
            'location': {
                'type': 'Point',
                'coordinates': [float(longitude), float(latitude)],
                'address': location
            },
            'medical_info': {
                'age': age,
                'height': height,
                'weight': weight,
                'gender': gender,
                'last_donation': datetime.strptime(last_donation, '%Y-%m-%d') if last_donation else None,
                'is_eligible': True if not last_donation else (datetime.now() - datetime.strptime(last_donation, '%Y-%m-%d')).days >= 120
            },
            'created_at': datetime.utcnow()
        }
        
        users.insert_one(new_user)
        flash(translations['registration_success'])
        return redirect(url_for('login'))
        
    return render_template('signup.html', translations=translations)

# Update the dashboard route to include translations
@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        flash(get_translations(session.get('language', 'en'))['login_required'])
        return redirect(url_for('login'))
        
    language = session.get('language', 'en')
    translations = get_translations(language)
    
    try:
        user_id = session['user']
        user = users.find_one({'_id': ObjectId(user_id)})
        
        if not user:
            session.pop('user', None)
            flash(translations['error_message'])
            return redirect(url_for('login'))
            
        # Get user's donation history
        user_donations = list(donation_history.find({'user_id': str(user_id)}).sort('donation_date', -1))
        
        # Get upcoming blood donation camps
        upcoming_camps = list(admins.find({
            'location': {
                '$near': {
                    '$geometry': {
                        'type': 'Point',
                        'coordinates': user['location']['coordinates']
                    },
                    '$maxDistance': 10000
                }
            }
        }).sort('location.coordinates', 1).limit(5))
        
        # Get pending blood requests
        pending_requests = list(notifications.find({
            'user_id': str(user_id),
            'type': 'blood_request',
            'status': 'pending'
        }).sort('created_at', -1))
        
        return render_template('dashboard.html',
                             user=user,
                             donation_history=user_donations,
                             upcoming_camps=upcoming_camps,
                             pending_requests=pending_requests,
                             translations=translations)
                             
    except Exception as e:
        print(f"Error in dashboard: {str(e)}")
        flash(translations['error_message'])
        return redirect(url_for('login'))

@app.route('/logout')
def logout():
    language = session.get('language', 'en')
    translations = get_translations(language)
    
    session.clear()
    flash(translations['logout_success'])
    return redirect(url_for('landing'))

@app.route('/update_profile', methods=['POST'])
def update_profile():
    if 'user' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    
    try:
        data = request.json
        user_id = ObjectId(session['user'])
        
        update_data = {
            'name': data.get('name'),
            'phone': data.get('phone'),
            'blood_group': data.get('blood_group')
        }
        
        users.update_one(
            {'_id': user_id},
            {'$set': update_data}
        )
        
        return jsonify({'message': 'Profile updated successfully'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Calculate the great circle distance between two points 
    on the earth (specified in decimal degrees)
    """
    R = 6371  # Earth's radius in kilometers
    
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    distance = R * c
    
    return distance

@app.route('/admin/user/<user_id>', methods=['GET'])
@admin_required
def get_user_details(user_id):
    try:
        # Support lookup by ObjectId or by email string
        user = None
        try:
            user = users.find_one({'_id': ObjectId(user_id)})
        except Exception:
            user = users.find_one({'email': user_id})
        if not user:
            return jsonify({'error': 'User not found'}), 404
            
        # Get last notification for this user
        last_notification = notifications.find_one(
            {'user_id': str(user['_id'])},
            sort=[('created_at', -1)]
        )
        
        # Get blood donation forms for this user - simplified approach
        user_id_str = str(user['_id'])
        print(f"Looking for forms for user_id: {user_id_str}")
        
        # Get blood donation forms for this user and this admin only
        blood_forms = list(blood_donation_forms.find({
            'user_id': user_id_str,
            'admin_id': str(session['admin'])
        }).sort('submitted_at', -1))
        
        print(f"Found {len(blood_forms)} blood forms for user")
        
        # Only show real forms, no test forms
        
        # Format blood forms data
        formatted_forms = []
        for form in blood_forms:
            formatted_forms.append({
                'form_id': str(form['_id']),
                'request_id': form['request_id'],
                'submitted_at': form['submitted_at'].strftime('%Y-%m-%d %H:%M:%S'),
                'status': form['status'],
                'form_data': form['form_data']
            })
        
        # Convert ObjectId to string and format dates
        user['_id'] = str(user['_id'])
        if last_notification:
            last_notification['_id'] = str(last_notification['_id'])
            last_notification['created_at'] = last_notification['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        
        return jsonify({
            'success': True,
            'user': user,
            'last_notification': last_notification,
            'blood_forms': formatted_forms
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def calculate_donor_score(donor, distance, last_notification):
    """
    Calculate a score for a donor based on various factors
    """
    score = 0.0
    
    # Distance factor (closer is better)
    max_distance = 5  # 5km
    distance_factor = 1 - (min(distance, max_distance) / max_distance)
    score += distance_factor * 0.4  # 40% weight
    
    # Get current time once to avoid timezone issues
    current_time = datetime.now(UTC)
    
    # Last notification factor (longer since last notification is better)
    if last_notification:
        try:
            # Ensure both datetimes are timezone-aware
            notification_time = last_notification['created_at']
            if notification_time.tzinfo is None:
                notification_time = notification_time.replace(tzinfo=UTC)
                
            days_since_notification = (current_time - notification_time).days
            notification_factor = min(days_since_notification / 30, 1)  # Cap at 30 days
            score += notification_factor * 0.3  # 30% weight
        except Exception as e:
            print(f"Error calculating notification factor: {str(e)}")
            # Continue with other factors
    
    # Donation history factor
    if 'last_donation_date' in donor:
        try:
            last_donation = donor['last_donation_date']
            if isinstance(last_donation, str):
                last_donation = datetime.fromisoformat(last_donation.replace('Z', '+00:00'))
                
            # Ensure both datetimes are timezone-aware
            if last_donation.tzinfo is None:
                last_donation = last_donation.replace(tzinfo=UTC)
                
            days_since_donation = (current_time - last_donation).days
            donation_factor = min(days_since_donation / 90, 1)  # Cap at 90 days
            score += donation_factor * 0.3  # 30% weight
        except Exception as e:
            print(f"Error calculating donation factor: {str(e)}")
            # Continue with other factors
    
    return score

@app.route('/admin/search_donors', methods=['POST'])
@admin_required
def search_donors():
    try:
        # Get search criteria from form
        blood_group = request.form.get('blood_group')
        max_distance = float(request.form.get('distance', 5))  # Default to 5km
        
        # Get admin's hospital location
        admin = admins.find_one({'_id': ObjectId(session['admin'])})
        if not admin or 'location' not in admin:
            return jsonify({'error': 'Hospital location not found'}), 400
            
        admin_lat = float(admin['location']['coordinates'][1])
        admin_lon = float(admin['location']['coordinates'][0])
        
        # Build the query
        query = {}
        
        # Add blood group filter if specified
        if blood_group:
            query['blood_group'] = blood_group
            
        # Get all matching donors based on blood group
        matching_donors = []
        donors = list(users.find(query))  # Convert cursor to list
        
        print(f"Found {len(donors)} total donors in database")
        
        # Get current time once to avoid timezone issues
        current_time = datetime.now(UTC)
        
        for donor in donors:
            # Skip donors who have already responded to requests for this blood group
            # Check for both accepted and rejected responses
            existing_response = notifications.find_one({
                'user_id': str(donor['_id']),
                'type': 'blood_request',
                'data.blood_group_needed': blood_group,
                'status': {'$in': ['responded', 'selected']}  # Only skip if accepted, not rejected
            })
            
            if existing_response:
                print(f"Donor {donor.get('name', 'Unknown')} has already accepted a request for blood group {blood_group}")
                continue
                
            # Skip donors in cooldown period
            if 'last_donation_date' in donor:
                try:
                    last_donation = donor['last_donation_date']
                    if isinstance(last_donation, str):
                        last_donation = datetime.fromisoformat(last_donation.replace('Z', '+00:00'))
                    
                    # Ensure both datetimes are timezone-aware
                    if last_donation.tzinfo is None:
                        last_donation = last_donation.replace(tzinfo=UTC)
                    
                    cooldown_end = last_donation + timedelta(days=90)
                    if current_time < cooldown_end:
                        print(f"Donor {donor.get('name', 'Unknown')} is in cooldown period")
                        continue
                except Exception as e:
                    print(f"Error processing last_donation_date for donor {donor.get('name', 'Unknown')}: {str(e)}")
                    # Continue with this donor despite the error
                    pass
            
            if 'location' not in donor:
                print(f"Donor {donor.get('name', 'Unknown')} has no location data")
                continue
                
            # Get donor's coordinates
            try:
                donor_lat = float(donor['location']['coordinates'][1])
                donor_lon = float(donor['location']['coordinates'][0])
                
                # Calculate distance using Haversine formula
                distance = haversine_distance(
                    admin_lat, admin_lon,
                    donor_lat, donor_lon
                )
                
                print(f"Donor {donor.get('name', 'Unknown')} is {distance:.2f}km away")
                
                # Only include donors within the specified distance
                if distance <= max_distance:
                    # Get last notification for this donor
                    last_notification = notifications.find_one(
                        {'user_id': str(donor['_id'])},
                        sort=[('created_at', -1)]
                    )
                    
                    # Calculate donor score using AI algorithm
                    donor_score = calculate_donor_score(donor, distance, last_notification)
                    
                    matching_donors.append({
                        'id': str(donor['_id']),
                        'name': donor['name'],
                        'blood_group': donor['blood_group'],
                        'phone': donor.get('phone', 'N/A'),
                        'distance': round(distance, 2),
                        'email': donor['email'],
                        'last_notified': last_notification['created_at'].strftime('%Y-%m-%d %H:%M:%S') if last_notification else None,
                        'address': donor['location'].get('address', 'Address not available'),
                        'score': round(donor_score * 100, 2)
                    })
            except Exception as e:
                print(f"Error processing location for donor {donor.get('name', 'Unknown')}: {str(e)}")
                continue
        
        # Sort by donor score (highest first)
        matching_donors.sort(key=lambda x: x['score'], reverse=True)
        
        print(f"Found {len(matching_donors)} matching donors within {max_distance}km")
        
        # Ensure a pending notification exists for each matched donor so they can respond
        try:
            for md in matching_donors:
                user_id_str = md.get('id')
                if not user_id_str:
                    continue
                # Skip if a pending notification already exists for this admin, user and blood group
                existing_pending = notifications.find_one({
                    'type': 'blood_request',
                    'user_id': user_id_str,
                    'admin_id': str(admin['_id']),
                    'status': 'pending',
                    'data.blood_group_needed': blood_group
                })
                if existing_pending:
                    continue
                req_id = str(ObjectId())
                notifications.insert_one({
                    'user_id': user_id_str,
                    'type': 'blood_request',
                    'title': f"Urgent Blood Request from {admin.get('hospital_name', 'Hospital')}",
                    'body': f"Blood group {blood_group} needed. Distance {md.get('distance', 'N/A')}km.",
                    'data': {
                        'type': 'blood_request',
                        'hospital_name': admin.get('hospital_name', 'Hospital'),
                        'hospital_id': admin.get('hospital_id', 'N/A'),
                        'blood_group_needed': blood_group,
                        'distance': str(md.get('distance', '')),
                        'request_id': req_id
                    },
                    'created_at': datetime.now(UTC),
                    'read': False,
                    'message_status': 'pending',
                    'channel': 'system',
                    'request_id': req_id,
                    'status': 'pending',
                    'response': None,
                    'response_time': None,
                    'admin_id': str(admin['_id'])
                })
        except Exception as e:
            print(f"Error creating pending notifications: {str(e)}")
        
        return jsonify({
            'success': True,
            'donors': matching_donors,
            'count': len(matching_donors)
        })
        
    except Exception as e:
        print(f"Error in search_donors: {str(e)}")
        return jsonify({'error': str(e)}), 500

def format_phone_number(phone):
    """Convert phone number to E.164 format"""
    # Remove any non-digit characters
    phone = ''.join(filter(str.isdigit, phone))
    
    # Add country code if not present
    if not phone.startswith('91'):  # Assuming Indian numbers
        phone = '91' + phone
        
    # Add plus sign
    return '+' + phone

def send_sms(phone_number, message):
    try:
        # Format phone number (remove any non-digit characters)
        formatted_number = ''.join(filter(str.isdigit, phone_number))
        
        # Use a hardcoded API key for testing
        api_key = "d20RibyY9ASHUekcMuZ8sxjLpgF3wKmtGCVNzIqEQ4v76XoJPrHxrR2up9ZXnbsdB48tkiVyUPgfcYwj"
        
        # Prepare payload according to the latest Fast2SMS API format
        # Based on the latest documentation at docs.fast2sms.com
        payload = {
            "route": "q",  # Using 'q' for quick transactional route
            "numbers": formatted_number,
            "message": message
        }
        
        # Set up headers with API key
        headers = {
            "authorization": api_key,
            "Content-Type": "application/json"
        }
        
        # Send SMS with retry logic
        max_retries = 3
        retry_delay = 2  # seconds
        
        for attempt in range(max_retries):
            try:
                # Send SMS with a timeout using the latest API endpoint
                response = requests.post(
                    "https://www.fast2sms.com/dev/bulkV2",
                    json=payload,
                    headers=headers,
                    timeout=10  # 10 second timeout
                )
                
                # Print response for debugging
                print(f"Fast2SMS Response ({formatted_number}):", response.text)
                
                # Check response
                if response.status_code == 200:
                    result = response.json()
                    if result.get('return'):
                        print(f"✅ SMS sent successfully to {formatted_number}")
                        print(f"Request ID: {result.get('request_id', 'N/A')}")
                        return True
                    else:
                        error_msg = result.get('message', 'Unknown error')
                        print(f"❌ Failed to send SMS to {formatted_number}")
                        print(f"Error: {error_msg}")
                        
                        # Check for specific error cases
                        if 'DLT not approved' in error_msg:
                            print("⚠️ Message template not approved in DLT. Please register your template first.")
                        elif 'invalid sender id' in error_msg.lower():
                            print("⚠️ Invalid sender ID. Trying without sender ID.")
                            # Try again without sender ID
                            payload.pop('sender_id', None)
                            continue
                        elif 'insufficient credits' in error_msg.lower():
                            print("⚠️ Insufficient balance. Please recharge your Fast2SMS account.")
                        elif 'Invalid Authentication' in error_msg:
                            print("⚠️ Invalid API key. Please check your Fast2SMS API key.")
                        elif 'old API' in error_msg:
                            print("⚠️ Using outdated API format. Trying with updated format...")
                            # Try with updated format
                            payload = {
                                "route": "q",  # Using 'q' for quick transactional route
                                "numbers": formatted_number,
                                "message": message
                            }
                            continue
                            
                        if attempt < max_retries - 1:
                            print(f"Retrying in {retry_delay} seconds...")
                            time.sleep(retry_delay)
                            retry_delay *= 2  # Exponential backoff
                            continue
                        return False
                else:
                    print(f"❌ HTTP {response.status_code} error for {formatted_number}")
                    print(f"Response: {response.text}")
                    
                    # If we get the "old API" error, try a different approach
                    if "old API" in response.text:
                        print("⚠️ Fast2SMS API error. Using updated format...")
                        # Try with updated format
                        payload = {
                            "route": "q",  # Using 'q' for quick transactional route
                            "numbers": formatted_number,
                            "message": message
                        }
                        continue
                    
                    if attempt < max_retries - 1:
                        print(f"Retrying in {retry_delay} seconds...")
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff
                        continue
                    return False
                    
            except requests.exceptions.RequestException as e:
                print(f"Network error on attempt {attempt+1}: {str(e)}")
                if attempt < max_retries - 1:
                    print(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    print(f"❌ Failed to connect to SMS service after {max_retries} attempts: {str(e)}")
                    return False
            
    except Exception as e:
        print(f"❌ Error sending SMS to {formatted_number}: {str(e)}")
        return False

def send_whatsapp_message(phone_number, message):
    """Send WhatsApp message using pywhatkit"""
    try:
        # Format phone number (remove any non-digit characters)
        phone_number = ''.join(filter(str.isdigit, phone_number))
        
        # Add country code if not present
        if not phone_number.startswith('91'):  # Assuming Indian numbers
            phone_number = '91' + phone_number
            
        # Get current time and add 1 minute for scheduling
        now = datetime.now()
        schedule_time = now + timedelta(minutes=1)
        
        # Send WhatsApp message
        pywhatkit.sendwhatmsg(
            phone_no=f"+{phone_number}",
            message=message,
            time_hour=schedule_time.hour,
            time_min=schedule_time.minute,
            wait_time=20,  # Wait for 20 seconds to load WhatsApp Web
            tab_close=True,  # Close the tab after sending
            close_time=3  # Wait 3 seconds before closing
        )
        
        print(f"WhatsApp message sent successfully to {phone_number}")
        return True
        
    except Exception as e:
        print(f"Error sending WhatsApp message: {str(e)}")
        raise e

def send_message_with_retry(phone_number, message, max_retries=3, use_whatsapp=False):
    """Send message with retry logic"""
    for attempt in range(max_retries):
        try:
            if use_whatsapp:
                success = send_whatsapp_message(phone_number, message)
            else:
                message_id = send_sms(phone_number, message)
                success = True
            return success
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Failed to send message after {max_retries} attempts: {str(e)}")
                raise e
            print(f"Attempt {attempt + 1} failed, retrying... Error: {str(e)}")
            time.sleep(1)

# In-memory per-call context. For production, persist in DB with TTL
CALL_CONTEXT = {}

@app.route('/admin/voice_call', methods=['POST'])
def make_voice_call():
    """Make a Twilio voice call for SOS alerts"""
    if 'admin' not in session:
        return jsonify({'error': 'Not authorized'}), 401
        
    try:
        # Get admin details
        admin = admins.find_one({'_id': ObjectId(session['admin'])})
        if not admin:
            return jsonify({'error': 'Admin not found'}), 404
            
        # Get request data
        to_number = request.json.get('to_number')
        message = request.json.get('message')
        donor_name = request.json.get('donor_name') or ''
        blood_group = request.json.get('blood_group') or ''
        
        if not to_number or not message:
            return jsonify({'error': 'Phone number and message are required'}), 400
            
        # Get Twilio credentials from environment
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        twilio_phone_number = os.getenv("TWILIO_PHONE_NUMBER")
        
        if not all([account_sid, auth_token, twilio_phone_number]):
            return jsonify({'error': 'Twilio credentials not configured'}), 500
            
        # Initialize Twilio client
        client = Client(account_sid, auth_token)
        
        # Get the base URL for the voice endpoint
        base_url = os.getenv("CALLBACK_BASE_URL", "http://localhost:5001")
        # Create per-call context id
        cid = str(uuid.uuid4())
        CALL_CONTEXT[cid] = {
            'donor_name': donor_name,
            'blood_group': blood_group,
            'admin_hospital': admin.get('hospital_name', ''),
            'admin_phone': admin.get('phone', ''),
            'message': message,
        }
        voice_url = f"{base_url}/voice_ivr?cid={cid}"
        
        # Store the message in session for the voice endpoint
        session['voice_message'] = message
        
        # Make the call
        call = client.calls.create(
            to=to_number,
            from_=twilio_phone_number,
            url=voice_url,
            method='GET'
        )
        
        return jsonify({
            'success': True,
            'call_sid': call.sid,
            'message': 'Voice call initiated successfully',
            'cid': cid
        })
        
    except Exception as e:
        return jsonify({'error': f'Error making voice call: {str(e)}'}), 500

@app.route('/admin/voice_call_bulk', methods=['POST'])
def make_voice_call_bulk():
    """Place Twilio voice calls to multiple phone numbers (admin only)."""
    if 'admin' not in session:
        return jsonify({'error': 'Not authorized'}), 401

    try:
        data = request.get_json(force=True) or {}
        phone_numbers = data.get('phone_numbers') or []
        message = data.get('message') or 'Hello, this is an urgent blood donation request.'

        if not isinstance(phone_numbers, list) or not phone_numbers:
            return jsonify({'error': 'phone_numbers must be a non-empty list'}), 400

        # Credentials
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        twilio_phone_number = os.getenv("TWILIO_PHONE_NUMBER")
        if not all([account_sid, auth_token, twilio_phone_number]):
            return jsonify({'error': 'Twilio credentials not configured'}), 500

        client = Client(account_sid, auth_token)
        base_url = os.getenv("CALLBACK_BASE_URL", "http://localhost:5001")
        voice_url = f"{base_url}/voice_response"

        # We'll build per-call IVR URLs with unique CIDs

        results = []
        for raw in phone_numbers:
            try:
                to_number = str(raw).strip()
                # Basic normalization: add +91 for 10 digits
                if to_number.isdigit() and len(to_number) == 10:
                    to_number = "+91" + to_number

                cid = str(uuid.uuid4())
                CALL_CONTEXT[cid] = {
                    'donor_name': '',
                    'blood_group': '',
                    'admin_hospital': '',
                    'admin_phone': '',
                    'message': message,
                }
                call = client.calls.create(
                    to=to_number,
                    from_=twilio_phone_number,
                    url=f"{voice_url}?cid={cid}",
                    method='GET'
                )
                results.append({'to': to_number, 'success': True, 'call_sid': call.sid, 'cid': cid})
            except Exception as e:
                results.append({'to': raw, 'success': False, 'error': str(e)})

        return jsonify({'success': True, 'results': results})

    except Exception as e:
        return jsonify({'error': f'Error in bulk voice call: {str(e)}'}), 500

@app.route('/voice_ivr', methods=['GET'])
def voice_ivr():
    """Initial IVR prompt with DTMF/Speech gather for accept/reject, bilingual."""
    try:
        cid = request.args.get('cid', '')
        ctx = CALL_CONTEXT.get(cid, {})
        donor_name = ctx.get('donor_name') or ''
        blood_group = ctx.get('blood_group') or ''
        admin_hospital = ctx.get('admin_hospital') or 'the hospital'

        response = VoiceResponse()
        gather = response.gather(
            input='speech dtmf', num_digits=1,
            action=f"/voice_ivr_handle?cid={cid}", method='POST', timeout=6
        )

        # English prompt
        en_intro = f"Hello {donor_name}. This is an urgent request from {admin_hospital}. " \
                   f"We need {blood_group or 'required'} blood. Press 1 to accept, press 2 to decline."
        gather.say(en_intro, voice='alice')
        # Hindi prompt (spoken by English TTS if Hindi voice not available)
        hi_intro = "Namaste. Yah ek aavashyak khoon dan anurodh hai. Kripya ek dabayein sweekar ke liye, do dabayein inkar ke liye."
        gather.say(hi_intro, voice='alice')

        # If no input
        response.say("We did not receive any input. Goodbye.", voice='alice')
        return str(response), 200, {'Content-Type': 'application/xml'}
    except Exception:
        fail = VoiceResponse()
        fail.say("We are experiencing a system error. Please try again later.", voice='alice')
        fail.hangup()
        return str(fail), 200, {'Content-Type': 'application/xml'}

@app.route('/voice_ivr_handle', methods=['POST'])
def voice_ivr_handle():
    """Handle DTMF/Speech, confirm, and record response."""
    try:
        cid = request.args.get('cid', '')
        digits = request.form.get('Digits', '')
        speech = (request.form.get('SpeechResult') or '').lower()

        accepted = None
        if digits == '1':
            accepted = True
        elif digits == '2':
            accepted = False
        else:
            if any(w in speech for w in ['yes', 'haan', 'ha', 'accept', 'ready']):
                accepted = True
            elif any(w in speech for w in ['no', 'nahin', 'nahi', 'reject', 'mana']):
                accepted = False

        # Language heuristic: Hindi if Hindi words detected; else English
        lang_hi = any(w in speech for w in ['haan', 'nahi', 'namaste', 'kripya'])

        response = VoiceResponse()
        if accepted is None:
            reprompt = response.gather(input='speech dtmf', num_digits=1,
                                       action=f"/voice_ivr_handle?cid={cid}", method='POST', timeout=6)
            if lang_hi:
                reprompt.say('Kripya ek dabayein sweekar ke liye, do dabayein inkar ke liye.', voice='alice')
            else:
                reprompt.say('Please press 1 to accept or press 2 to decline.', voice='alice')
            return str(response), 200, {'Content-Type': 'application/xml'}

        # Record result (best effort)
        try:
            record = {'cid': cid, 'accepted': accepted, 'created_at': datetime.now(UTC)}
            try:
                responses_col = db['donor_responses']
                responses_col.insert_one(record)
            except Exception:
                pass
        except Exception:
            pass

        if accepted:
            if lang_hi:
                response.say('Dhanyavaad. Aapka sweekar darj kar liya gaya hai. Hum jaldi sampark karenge.', voice='alice')
            else:
                response.say('Thank you. Your acceptance has been recorded. We will contact you shortly.', voice='alice')
        else:
            if lang_hi:
                response.say('Dhanyavaad. Aapka uttar inkar ke roop mein darj kiya gaya hai.', voice='alice')
            else:
                response.say('Thank you. Your response has been recorded as decline.', voice='alice')

        response.hangup()
        return str(response), 200, {'Content-Type': 'application/xml'}
    except Exception:
        fail = VoiceResponse()
        fail.say('We are experiencing a system error. Please try again later.', voice='alice')
        fail.hangup()
        return str(fail), 200, {'Content-Type': 'application/xml'}

@app.route('/test_voice_call', methods=['POST'])
def test_voice_call():
    """Test voice call endpoint (no auth required for testing)"""
    try:
        # Get request data
        to_number = request.json.get('to_number')
        message = request.json.get('message')
        
        if not to_number or not message:
            return jsonify({'error': 'Phone number and message are required'}), 400
            
        # Get Twilio credentials from environment
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN")
        twilio_phone_number = os.getenv("TWILIO_PHONE_NUMBER")
        
        if not all([account_sid, auth_token, twilio_phone_number]):
            return jsonify({'error': 'Twilio credentials not configured'}), 500
            
        # Initialize Twilio client
        client = Client(account_sid, auth_token)
        
        # Get the base URL for the voice endpoint
        base_url = os.getenv("CALLBACK_BASE_URL", "http://localhost:5001")
        voice_url = f"{base_url}/voice_response"
        
        # Store the message in session for the voice endpoint
        session['voice_message'] = message
        
        # Make the call
        call = client.calls.create(
            to=to_number,
            from_=twilio_phone_number,
            url=voice_url,
            method='GET'
        )
        
        return jsonify({
            'success': True,
            'call_sid': call.sid,
            'message': 'Voice call initiated successfully'
        })
        
    except Exception as e:
        return jsonify({'error': f'Error making voice call: {str(e)}'}), 500

@app.route('/voice_response', methods=['GET'])
def voice_response():
    """TwiML endpoint for voice calls"""
    message = session.get('voice_message', 'Hello, this is an urgent blood donation request.')
    
    # Create TwiML response
    response = VoiceResponse()
    response.say(message, voice='alice')
    
    return str(response), 200, {'Content-Type': 'application/xml'}

@app.route('/test_twilio_credentials', methods=['GET'])
def test_twilio_credentials():
    """Test endpoint to check if Twilio credentials are loaded (no auth required)"""
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    twilio_phone_number = os.getenv("TWILIO_PHONE_NUMBER")
    
    return jsonify({
        'account_sid_loaded': bool(account_sid),
        'auth_token_loaded': bool(auth_token),
        'phone_number_loaded': bool(twilio_phone_number),
        'account_sid': account_sid[:10] + "..." if account_sid else None,
        'phone_number': twilio_phone_number
    })

@app.route('/admin/send_alert', methods=['POST'])
def send_alert():
    if 'admin' not in session:
        return jsonify({'error': 'Not authorized'}), 401
        
    try:
        # Get admin details
        admin = admins.find_one({'_id': ObjectId(session['admin'])})
        if not admin:
            return jsonify({'error': 'Admin not found'}), 404
            
        # Get search criteria from request
        blood_group = request.json.get('blood_group')
        max_distance = float(request.json.get('distance', 5))  # Default to 5km
        use_whatsapp = request.json.get('use_whatsapp', False)  # Get WhatsApp preference
        
        # Validate admin's phone number
        admin_phone = admin.get('phone')
        if not admin_phone:
            return jsonify({'error': 'Please add your hospital phone number in your profile first'}), 400
            
        # Get admin's hospital location
        admin_lat = float(admin['location']['coordinates'][1])
        admin_lon = float(admin['location']['coordinates'][0])
        
        # Build the query for matching donors
        query = {}
        if blood_group:
            query['blood_group'] = blood_group
            
        # Get all matching donors
        matching_donors = []
        donors = list(users.find(query))
        
        print(f"Found {len(donors)} total donors in database")
        
        messages_sent = 0
        notifications_created = 0
        errors = []
        
        for donor in donors:
            if 'location' not in donor:
                continue
                
            # Get donor's coordinates
            donor_lat = float(donor['location']['coordinates'][1])
            donor_lon = float(donor['location']['coordinates'][0])
            
            # Calculate distance using Haversine formula
            distance = haversine_distance(
                admin_lat, admin_lon,
                donor_lat, donor_lon
            )
            
            # Only include donors within the specified distance
            if distance <= max_distance:
                matching_donors.append(donor)
                
                try:
                    # Create notification data
                    notification_data = {
                        'type': 'blood_request',
                        'hospital_name': admin.get('hospital_name', 'Hospital'),
                        'hospital_address': admin.get('address', 'Address not available'),
                        'hospital_phone': admin_phone,
                        'hospital_id': admin.get('hospital_id', 'N/A'),
                        'blood_group_needed': blood_group,
                        'distance': str(round(distance, 2)),
                        'timestamp': str(datetime.now(UTC)),
                        'request_id': str(ObjectId()),  # Generate unique request ID
                        'status': 'pending',  # Initial status
                        'response': None,  # Will store user's response
                        'response_time': None,  # Will store when user responded
                        'admin_id': str(admin['_id'])  # Store admin ID for reference
                    }
                    
                    # Create notification record in database
                    message_body = f"""🚨 Urgent Blood Request!

Hospital: {admin.get('hospital_name', 'Hospital')}
Blood Group Needed: {blood_group}
Distance: {round(distance, 2)}km
Address: {admin.get('address', 'Address not available')}

Please visit your dashboard to respond to this request:
http://localhost:5001/dashboard

Or call {admin_phone} for details.

Thank you for your support!"""
                    
                    notification_record = {
                        'user_id': str(donor['_id']),
                        'type': 'blood_request',
                        'title': f"🚨 Urgent Blood Request from {admin.get('hospital_name', 'Hospital')}",
                        'body': message_body,
                        'data': notification_data,
                        'created_at': datetime.now(UTC),
                        'read': False,
                        'message_status': 'pending',
                        'channel': 'whatsapp' if use_whatsapp else 'sms',
                        'request_id': notification_data['request_id'],
                        'status': 'pending',
                        'response': None,
                        'response_time': None,
                        'admin_id': str(admin['_id'])
                    }
                    notifications.insert_one(notification_record)
                    notifications_created += 1
                    
                    # Send message using selected channel
                    try:
                        donor_phone = donor.get('phone')
                        if donor_phone:
                            print(f"Attempting to send {'WhatsApp' if use_whatsapp else 'SMS'} to donor {donor.get('name', 'Unknown')} with phone {donor_phone}")
                            success = send_message_with_retry(
                                donor_phone,
                                message_body,
                                use_whatsapp=use_whatsapp
                            )
                            if success:
                                notifications.update_one(
                                    {'_id': notification_record['_id']},
                                    {'$set': {'message_status': 'sent'}}
                                )
                                messages_sent += 1
                                print(f"Successfully sent {'WhatsApp' if use_whatsapp else 'SMS'} to {donor.get('name', 'Unknown')}")
                        else:
                            error_msg = f"No phone number found for donor {donor.get('name', 'Unknown')}"
                            print(error_msg)
                            errors.append(error_msg)
                    except Exception as e:
                        error_msg = f"Error sending {'WhatsApp' if use_whatsapp else 'SMS'} to {donor.get('name', 'Unknown')}: {str(e)}"
                        print(error_msg)
                        errors.append(error_msg)
                    
                except Exception as e:
                    error_msg = f"Error processing notification for {donor.get('name', 'Unknown')}: {str(e)}"
                    print(error_msg)
                    errors.append(error_msg)
                    continue
        
        return jsonify({
            'success': True,
            'message': f'Notifications created for {notifications_created} donors, {"WhatsApp" if use_whatsapp else "SMS"} sent to {messages_sent} donors',
            'total_matching_donors': len(matching_donors),
            'errors': errors if errors else None
        })
        
    except Exception as e:
        error_msg = f"Error in send_alert: {str(e)}"
        print(error_msg)
        return jsonify({'error': error_msg}), 500

@app.route('/user/notifications', methods=['GET'])
def get_user_notifications():
    if 'user' not in session:
        return jsonify({'error': 'Not authorized'}), 401
        
    try:
        # Get all notifications for the user
        user_notifications = list(notifications.find(
            {'user_id': session['user']}
        ).sort('created_at', -1))
        
        # Get unread count
        unread_count = notifications.count_documents({
            'user_id': session['user'],
            'read': False
        })
        
        # Convert ObjectId to string and format dates
        for notif in user_notifications:
            notif['_id'] = str(notif['_id'])
            notif['created_at'] = notif['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
        return jsonify({
            'success': True,
            'notifications': user_notifications,
            'unread_count': unread_count
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/user/mark_notification_read', methods=['POST'])
def mark_notification_read():
    if 'user' not in session:
        return jsonify({'error': 'Not authorized'}), 401
        
    try:
        notification_id = request.json.get('notification_id')
        
        # Update notification status
        result = notifications.update_one(
            {
                '_id': ObjectId(notification_id),
                'user_id': session['user']
            },
            {'$set': {'read': True}}
        )
        
        if result.modified_count == 0:
            return jsonify({'error': 'Notification not found'}), 404
            
        return jsonify({'success': True})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/notifications', methods=['GET'])
@admin_required
def get_notifications():
    try:
        # Get all notifications for the admin
        admin_notifications = list(notifications.find({
            'type': 'system'
        }).sort('created_at', -1))
        
        # Convert ObjectId to string for JSON serialization
        for notification in admin_notifications:
            notification['_id'] = str(notification['_id'])
            
        return jsonify({
            'success': True,
            'notifications': admin_notifications
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/users')
def admin_users():
    if 'admin' not in session:
        flash('Please login as admin first')
        return redirect(url_for('admin_login'))
    
    try:
        admin = admins.find_one({'_id': ObjectId(session['admin'])})
        if not admin:
            session.pop('admin', None)
            flash('Admin not found')
            return redirect(url_for('admin_login'))
        
        # Get users who have accepted donation requests for this admin
        accepted_requests = list(notifications.find({
            'admin_id': str(session['admin']),
            'type': 'blood_request',
            'status': 'responded',
            'response': 'accepted'
        }).sort('response_time', -1))
        
        print(f"Found {len(accepted_requests)} accepted requests for admin {session['admin']}")
        
        # Debug: Print all accepted requests to see what admin_ids they have
        for req in accepted_requests:
            print(f"Request {req.get('request_id', 'N/A')} - Admin ID: {req.get('admin_id', 'N/A')} - User: {req.get('user_id', 'N/A')}")
        
        # Fix notifications that don't have admin_id set (one-time fix)
        notifications_without_admin = list(notifications.find({
            'type': 'blood_request',
            'status': 'responded',
            'response': 'accepted',
            'admin_id': {'$exists': False}
        }))
        
        for notif in notifications_without_admin:
            # Try to find the original request to get admin_id
            original_request = notifications.find_one({
                'request_id': notif['request_id'],
                'type': 'blood_request',
                'status': 'pending'
            })
            if original_request and 'admin_id' in original_request:
                notifications.update_one(
                    {'_id': notif['_id']},
                    {'$set': {'admin_id': original_request['admin_id']}}
                )
                print(f"Fixed admin_id for notification {notif['_id']}")
        
        # Get user details for each accepted request
        users_with_forms = []
        for request in accepted_requests:
            user = users.find_one({'_id': ObjectId(request['user_id'])})
            if user:
                # Get blood donation forms for this user and this specific request
                blood_forms = list(blood_donation_forms.find({
                    'request_id': request['request_id'],
                    'user_id': str(user['_id']),
                    'admin_id': str(session['admin'])
                }).sort('submitted_at', -1))
                
                print(f"Found {len(blood_forms)} forms for user {user.get('name')} and request {request['request_id']}")
                
                # Format blood forms data for template
                formatted_forms = []
                for form in blood_forms:
                    formatted_forms.append({
                        'form_id': str(form['_id']),
                        'request_id': form['request_id'],
                        'submitted_at': form['submitted_at'].strftime('%Y-%m-%d %H:%M:%S'),
                        'status': form['status'],
                        'form_data': form['form_data']
                    })
                
                users_with_forms.append({
                    'user': user,
                    'request': request,
                    'blood_forms': formatted_forms
                })
        
        return render_template('admin_users.html', admin=admin, users_with_forms=users_with_forms)
    
    except Exception as e:
        print(f"Error in admin_users: {str(e)}")
        session.pop('admin', None)
        flash('Error accessing user list')
        return redirect(url_for('admin_login'))

@app.route('/admin/user/<user_id>', methods=['DELETE'])
def delete_user(user_id):
    if 'admin' not in session:
        return jsonify({'error': 'Not authorized'}), 401
    
    try:
        result = users.delete_one({'_id': ObjectId(user_id)})
        if result.deleted_count:
            return jsonify({'success': True})
        return jsonify({'error': 'User not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/admin/donation_form/<form_id>', methods=['GET'])
@admin_required
def get_donation_form_details(form_id):
    try:
        # Get the donation form
        form = blood_donation_forms.find_one({'_id': ObjectId(form_id)})
        if not form:
            return jsonify({'error': 'Form not found'}), 404
        
        # Get user details
        user = users.find_one({'_id': ObjectId(form['user_id'])})
        if not user:
            return jsonify({'error': 'User not found'}), 404
        
        # Get blood request details
        blood_request = notifications.find_one({
            'request_id': form['request_id'],
            'type': 'blood_request'
        })
        
        # Format the response
        form_data = {
            'form_id': str(form['_id']),
            'request_id': form['request_id'],
            'user_id': str(user['_id']),
            'submitted_at': form['submitted_at'].strftime('%Y-%m-%d %H:%M:%S'),
            'status': form['status'],
            'user': {
                'name': user.get('name', 'N/A'),
                'email': user.get('email', 'N/A'),
                'phone': user.get('phone', 'N/A'),
                'blood_group': user.get('blood_group', 'N/A')
            },
            'blood_request': {
                'blood_group_needed': blood_request.get('data', {}).get('blood_group_needed', 'N/A') if blood_request else 'N/A',
                'hospital_name': blood_request.get('data', {}).get('hospital_name', 'N/A') if blood_request else 'N/A',
                'created_at': blood_request.get('created_at').strftime('%Y-%m-%d %H:%M:%S') if blood_request and blood_request.get('created_at') else 'N/A'
            },
            'form_data': form['form_data']
        }
        
        return jsonify({
            'success': True,
            'form': form_data
        })
        
    except Exception as e:
        print(f"Error getting donation form details: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/user/register_fcm', methods=['POST'])
def register_fcm_token():
    if 'user' not in session:
        print("No user in session for FCM registration")  # Debug log
        return jsonify({'error': 'Not authorized'}), 401
        
    try:
        fcm_token = request.json.get('fcm_token')
        if not fcm_token:
            print("No FCM token provided")  # Debug log
            return jsonify({'error': 'FCM token is required'}), 400
            
        user_id = ObjectId(session['user'])
        print(f"Registering FCM token for user ID: {user_id}")  # Debug log
            
        # Update user's FCM token
        result = users.update_one(
            {'_id': user_id},
            {'$set': {'fcm_token': fcm_token}}
        )
        
        if result.modified_count > 0:
            print(f"Successfully registered FCM token for user ID: {user_id}")  # Debug log
            return jsonify({'success': True, 'message': 'FCM token registered successfully'})
        else:
            print(f"User not found for ID: {user_id}")  # Debug log
            return jsonify({'error': 'User not found'}), 404
        
    except Exception as e:
        print(f"Error registering FCM token: {str(e)}")  # Debug log
        return jsonify({'error': str(e)}), 500

@app.route('/admin/update_phone', methods=['POST'])
def update_admin_phone():
    if 'admin' not in session:
        return jsonify({'success': False, 'error': 'Not authorized'}), 401
    
    try:
        data = request.get_json()
        phone = data.get('phone')
        
        if not phone or not phone.isdigit() or len(phone) != 10:
            return jsonify({'success': False, 'error': 'Invalid phone number format'}), 400
        
        # Update the admin's phone number in the database
        db.admins.update_one(
            {'_id': ObjectId(session['admin'])},
            {'$set': {'phone': phone}}
        )
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error updating phone number: {str(e)}")
        return jsonify({'success': False, 'error': 'Error updating phone number'}), 500

@app.route('/admin/activate/<admin_id>', methods=['POST'])
@admin_required
def activate_admin(admin_id):
    try:
        # Check if the activating admin is the system admin
        admin = admins.find_one({'_id': ObjectId(session['admin'])})
        if not admin or admin.get('hospital_id') != 'ADMIN001':
            return jsonify({'success': False, 'error': 'Only system admin can activate accounts'}), 403
        
        # Update the admin's status to active
        result = admins.update_one(
            {'_id': ObjectId(admin_id)},
            {'$set': {'status': 'active'}}
        )
        
        if result.modified_count:
            # Create a notification for the activated admin
            target_admin = admins.find_one({'_id': ObjectId(admin_id)})
            if target_admin:
                notification = {
                    'admin_id': admin_id,
                    'message': f'Your account has been activated. You can now login and use the system.',
                    'type': 'system',
                    'created_at': datetime.now(UTC),
                    'read': False
                }
                notifications.insert_one(notification)
            
            return jsonify({'success': True, 'message': 'Account activated successfully'})
        else:
            return jsonify({'success': False, 'error': 'Admin not found'}), 404
            
    except Exception as e:
        print(f"Error activating admin: {str(e)}")
        return jsonify({'success': False, 'error': 'Error activating account'}), 500

@app.route('/admin/pending_admins', methods=['GET'])
@admin_required
def get_pending_admins():
    try:
        # Check if the requesting admin is the system admin
        admin = admins.find_one({'_id': ObjectId(session['admin'])})
        if not admin or admin.get('hospital_id') != 'ADMIN001':
            return jsonify({'success': False, 'error': 'Only system admin can view pending accounts'}), 403
        
        # Get all pending admin accounts
        pending_admins = list(admins.find({'status': 'pending'}))
        
        # Convert ObjectId to string for JSON serialization
        for admin in pending_admins:
            admin['_id'] = str(admin['_id'])
            
        return jsonify({
            'success': True,
            'admins': pending_admins
        })
        
    except Exception as e:
        print(f"Error fetching pending admins: {str(e)}")
        return jsonify({'success': False, 'error': 'Error fetching pending accounts'}), 500

@app.route('/admin/register_demo_fcm', methods=['POST'])
@admin_required
def register_demo_fcm():
    try:
        data = request.get_json()
        user_id = data.get('user_id')
        fcm_token = data.get('fcm_token')
        
        if not user_id or not fcm_token:
            return jsonify({'success': False, 'error': 'User ID and FCM token are required'}), 400
            
        # Update user's FCM token
        result = users.update_one(
            {'_id': ObjectId(user_id)},
            {'$set': {'fcm_token': fcm_token}}
        )
        
        if result.modified_count > 0:
            return jsonify({
                'success': True,
                'message': 'FCM token registered successfully for demo purposes'
            })
        else:
            return jsonify({'success': False, 'error': 'User not found'}), 404
            
    except Exception as e:
        print(f"Error registering demo FCM token: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Add new route for handling blood donation response
@app.route('/user/respond_to_request', methods=['POST'])
def respond_to_request():
    if 'user' not in session:
        return jsonify({'error': 'Not authorized'}), 401
        
    try:
        data = request.get_json()
        request_id = data.get('request_id')
        response = data.get('response')  # 'accepted' or 'rejected'
        
        if not request_id or not response or response not in ['accepted', 'rejected']:
            return jsonify({'error': 'Invalid request parameters'}), 400
            
        # Check if user is in cooldown period
        if response == 'accepted':
            user = users.find_one({'_id': ObjectId(session['user'])})
            if user and 'last_donation_date' in user:
                last_donation = user['last_donation_date']
                if isinstance(last_donation, str):
                    last_donation = datetime.fromisoformat(last_donation.replace('Z', '+00:00'))
                cooldown_end = last_donation + timedelta(days=90)
                if datetime.now(UTC) < cooldown_end:
                    return jsonify({
                        'error': 'You are in a 90-day cooldown period after your last donation',
                        'cooldown_end': cooldown_end.strftime('%Y-%m-%d')
                    }), 400
            
        # Update notification with user's response
        # First, get the notification to preserve admin_id
        notification = notifications.find_one({
            'request_id': request_id,
            'user_id': session['user'],
            'status': 'pending'
        })
        
        if not notification:
            return jsonify({'error': 'Request not found or already responded'}), 404
            
        # Update the notification while preserving admin_id
        result = notifications.update_one(
            {
                'request_id': request_id,
                'user_id': session['user'],
                'status': 'pending'
            },
            {
                '$set': {
                    'status': 'responded',
                    'response': response,
                    'response_time': datetime.now(UTC),
                    'admin_id': notification.get('admin_id')  # Preserve admin_id
                }
            }
        )
        
        if result.modified_count == 0:
            return jsonify({'error': 'Request not found or already responded'}), 404
        if notification:
            # Get admin details
            admin = admins.find_one({'_id': ObjectId(notification['admin_id'])})
            if admin:
                # Send confirmation message to admin
                admin_message = f"Blood donation request {request_id} has been {response} by donor."
                try:
                    send_message_with_retry(
                        admin['phone'],
                        admin_message,
                        use_whatsapp=False
                    )
                except Exception as e:
                    print(f"Error sending confirmation to admin: {str(e)}")
        
        return jsonify({'success': True, 'message': f'Response {response} recorded successfully'})
        
    except Exception as e:
        print(f"Error processing response: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Add new route for admin to select donor
@app.route('/admin/select_donor', methods=['POST'])
@admin_required
def select_donor():
    try:
        data = request.get_json()
        request_id = data.get('request_id')
        selected_user_id = data.get('user_id')
        
        if not request_id or not selected_user_id:
            return jsonify({'error': 'Missing required parameters'}), 400
            
        # Get all accepted requests for this blood request
        accepted_requests = list(notifications.find({
            'request_id': request_id,
            'status': 'responded',
            'response': 'accepted'
        }))
        
        if not accepted_requests:
            return jsonify({'error': 'No accepted requests found'}), 404
            
        # Update selected donor's status
        selected_result = notifications.update_one(
            {
                'request_id': request_id,
                'user_id': selected_user_id,
                'status': 'responded',
                'response': 'accepted'
            },
            {
                '$set': {
                    'status': 'selected',
                    'selection_time': datetime.now(UTC)
                }
            }
        )
        
        if selected_result.modified_count == 0:
            return jsonify({'error': 'Selected donor not found'}), 404
            
        # Get donor and admin details
        donor = users.find_one({'_id': ObjectId(selected_user_id)})
        admin = admins.find_one({'_id': ObjectId(session['admin'])})
        
        if not donor or not admin:
            return jsonify({'error': 'Donor or admin not found'}), 404
            
        # Store donation history
        donation_date = datetime.now(UTC)
        cooldown_end = donation_date + timedelta(days=90)
        
        donation_record = {
            'user_id': str(donor['_id']),
            'donor_name': donor['name'],
            'donor_blood_group': donor['blood_group'],
            'donor_phone': donor.get('phone', 'N/A'),
            'donor_email': donor['email'],
            'admin_id': str(admin['_id']),
            'hospital_name': admin['hospital_name'],
            'hospital_id': admin['hospital_id'],
            'donation_date': donation_date,
            'cooldown_end': cooldown_end,
            'request_id': request_id,
            'status': 'completed',
            'created_at': datetime.now(UTC)
        }
        
        donation_history.insert_one(donation_record)
            
        # Update user's last donation date and set cooldown
        users.update_one(
            {'_id': ObjectId(selected_user_id)},
            {
                '$set': {
                    'last_donation_date': donation_date,
                    'cooldown_end': cooldown_end
                }
            }
        )
        
        # Update other accepted requests to rejected
        notifications.update_many(
            {
                'request_id': request_id,
                'status': 'responded',
                'response': 'accepted',
                'user_id': {'$ne': selected_user_id}
            },
            {
                '$set': {
                    'status': 'rejected',
                    'rejection_reason': 'Another donor was selected',
                    'rejection_time': datetime.now(UTC)
                }
            }
        )
        
        # Send notification to selected donor
        donor_message = f"""Congratulations! You have been selected for blood donation.
Please contact the hospital for further details.

Note: You will be in a 90-day cooldown period after donation.
Your next donation will be possible after {cooldown_end.strftime('%Y-%m-%d')}."""
        try:
            send_message_with_retry(
                donor['phone'],
                donor_message,
                use_whatsapp=False
            )
        except Exception as e:
            print(f"Error sending notification to selected donor: {str(e)}")
        
        return jsonify({
            'success': True,
            'message': 'Donor selected successfully'
        })
        
    except Exception as e:
        print(f"Error selecting donor: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Add new route to get accepted donors for a request
@app.route('/admin/accepted_donors/<request_id>', methods=['GET'])
@admin_required
def get_accepted_donors(request_id):
    try:
        # Get all accepted requests for this blood request
        accepted_requests = list(notifications.find({
            'request_id': request_id,
            'status': 'responded',
            'response': 'accepted'
        }))
        
        # Get donor details for each accepted request
        donors = []
        for request in accepted_requests:
            donor = users.find_one({'_id': ObjectId(request['user_id'])})
            if donor:
                donors.append({
                    'user_id': str(donor['_id']),
                    'name': donor['name'],
                    'blood_group': donor['blood_group'],
                    'phone': donor.get('phone', 'N/A'),
                    'email': donor['email'],
                    'response_time': request['response_time'].strftime('%Y-%m-%d %H:%M:%S'),
                    'distance': request.get('data', {}).get('distance', 'N/A')
                })
        
        return jsonify({
            'success': True,
            'donors': donors
        })
        
    except Exception as e:
        print(f"Error getting accepted donors: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Update the request stats route to include selection status
@app.route('/admin/request_stats', methods=['GET'])
@admin_required
def get_request_stats():
    try:
        admin_id = session['admin']
        
        # Get all requests sent by this admin
        requests = list(notifications.find({
            'admin_id': admin_id,
            'type': 'blood_request'
        }))
        
        # Calculate statistics
        total_requests = len(requests)
        pending_requests = len([r for r in requests if r['status'] == 'pending'])
        accepted_requests = len([r for r in requests if r['status'] == 'responded' and r['response'] == 'accepted'])
        rejected_requests = len([r for r in requests if r['status'] == 'responded' and r['response'] == 'rejected'])
        selected_donors = len([r for r in requests if r['status'] == 'selected'])
        
        # Get detailed request information
        request_details = []
        for req in requests:
            donor = users.find_one({'_id': ObjectId(req['user_id'])})
            if donor:
                request_details.append({
                    'request_id': req['request_id'],
                    'donor_name': donor['name'],
                    'blood_group': donor['blood_group'],
                    'status': req['status'],
                    'response': req.get('response'),
                    'created_at': req['created_at'].strftime('%Y-%m-%d %H:%M:%S'),
                    'response_time': req.get('response_time').strftime('%Y-%m-%d %H:%M:%S') if req.get('response_time') else None,
                    'selection_time': req.get('selection_time').strftime('%Y-%m-%d %H:%M:%S') if req.get('selection_time') else None,
                    'rejection_reason': req.get('rejection_reason'),
                    'rejection_time': req.get('rejection_time').strftime('%Y-%m-%d %H:%M:%S') if req.get('rejection_time') else None
                })
        
        return jsonify({
            'success': True,
            'stats': {
                'total_requests': total_requests,
                'pending_requests': pending_requests,
                'accepted_requests': accepted_requests,
                'rejected_requests': rejected_requests,
                'selected_donors': selected_donors
            },
            'request_details': request_details
        })
        
    except Exception as e:
        print(f"Error getting request stats: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Add new route to get pending blood requests for user
@app.route('/user/pending_requests', methods=['GET'])
def get_pending_requests():
    if 'user' not in session:
        return jsonify({'error': 'Not authorized'}), 401
        
    try:
        # Get all pending blood requests for the user
        pending_requests = list(notifications.find({
            'user_id': session['user'],
            'type': 'blood_request',
            'status': 'pending'
        }).sort('created_at', -1))
        
        # Get admin details for each request
        for request in pending_requests:
            admin = admins.find_one({'_id': ObjectId(request['admin_id'])})
            if admin:
                request['admin_details'] = {
                    'name': admin.get('hospital_name', 'Hospital'),
                    'address': admin.get('address', 'Address not available'),
                    'phone': admin.get('phone', 'N/A'),
                    'hospital_id': admin.get('hospital_id', 'N/A')
                }
            
            # Convert ObjectId to string for JSON serialization
            request['_id'] = str(request['_id'])
            request['created_at'] = request['created_at'].strftime('%Y-%m-%d %H:%M:%S')
        
        return jsonify({
            'success': True,
            'requests': pending_requests
        })
        
    except Exception as e:
        print(f"Error getting pending requests: {str(e)}")
        return jsonify({'error': str(e)}), 500

# Add new route to get user's blood request history
@app.route('/user/request_history', methods=['GET'])
def get_request_history():
    if 'user' not in session:
        return jsonify({'error': 'Not authorized'}), 401
        
    try:
        # Get all blood requests for the user (both pending and responded)
        all_requests = list(notifications.find({
            'user_id': session['user'],
            'type': 'blood_request'
        }).sort('created_at', -1))
        
        # Get admin details for each request
        for request in all_requests:
            try:
                # Check if admin_id exists and is valid
                if 'admin_id' in request and request['admin_id']:
                    admin = admins.find_one({'_id': ObjectId(request['admin_id'])})
                    if admin:
                        request['admin_details'] = {
                            'name': admin.get('hospital_name', 'Hospital'),
                            'address': admin.get('address', 'Address not available'),
                            'phone': admin.get('phone', 'N/A'),
                            'hospital_id': admin.get('hospital_id', 'N/A')
                        }
                    else:
                        request['admin_details'] = {
                            'name': 'Unknown Hospital',
                            'address': 'Address not available',
                            'phone': 'N/A',
                            'hospital_id': 'N/A'
                        }
                else:
                    request['admin_details'] = {
                        'name': 'Unknown Hospital',
                        'address': 'Address not available',
                        'phone': 'N/A',
                        'hospital_id': 'N/A'
                    }
                
                # Convert ObjectId to string and format dates for JSON serialization
                request['_id'] = str(request['_id'])
                request['created_at'] = request['created_at'].strftime('%Y-%m-%d %H:%M:%S')
                if request.get('response_time'):
                    request['response_time'] = request['response_time'].strftime('%Y-%m-%d %H:%M:%S')
                
            except Exception as e:
                print(f"Error processing request {request.get('_id')}: {str(e)}")
                request['admin_details'] = {
                    'name': 'Unknown Hospital',
                    'address': 'Address not available',
                    'phone': 'N/A',
                    'hospital_id': 'N/A'
                }
                continue
        
        return jsonify({
            'success': True,
            'requests': all_requests
        })
        
    except Exception as e:
        print(f"Error getting request history: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/admin/acceptance')
@admin_required
def admin_acceptance():
    return render_template('admin_acceptance.html')

@app.route('/admin/donation_history')
@admin_required
def admin_donation_history():
    return render_template('admin_donation_history.html')

@app.route('/admin/donation_history/data', methods=['GET'])
@admin_required
def get_donation_history():
    try:
        # Get query parameters
        status = request.args.get('status')
        blood_group = request.args.get('blood_group')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        
        # Build query
        query = {}
        if status:
            query['status'] = status
        if blood_group:
            query['donor_blood_group'] = blood_group
        if start_date:
            query['donation_date'] = {'$gte': datetime.fromisoformat(start_date.replace('Z', '+00:00'))}
        if end_date:
            if 'donation_date' in query:
                query['donation_date']['$lte'] = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            else:
                query['donation_date'] = {'$lte': datetime.fromisoformat(end_date.replace('Z', '+00:00'))}
        
        # Get donation history
        donations = list(donation_history.find(query).sort('donation_date', -1))
        
        # Convert ObjectId to string and format dates
        for donation in donations:
            donation['_id'] = str(donation['_id'])
            donation['donation_date'] = donation['donation_date'].strftime('%Y-%m-%d %H:%M:%S')
            donation['cooldown_end'] = donation['cooldown_end'].strftime('%Y-%m-%d %H:%M:%S')
            donation['created_at'] = donation['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            
            # Calculate days remaining in cooldown
            cooldown_end = datetime.fromisoformat(donation['cooldown_end'].replace('Z', '+00:00'))
            days_remaining = (cooldown_end - datetime.now(UTC)).days
            donation['days_remaining'] = max(0, days_remaining)
            donation['cooldown_status'] = 'Active' if days_remaining > 0 else 'Completed'
        
        return jsonify({
            'success': True,
            'donations': donations
        })
        
    except Exception as e:
        print(f"Error getting donation history: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/admin/donation_history/stats', methods=['GET'])
@admin_required
def get_donation_stats():
    try:
        # Get total donations
        total_donations = donation_history.count_documents({})
        
        # Get donations by blood group
        blood_group_stats = list(donation_history.aggregate([
            {'$group': {'_id': '$donor_blood_group', 'count': {'$sum': 1}}}
        ]))
        
        # Get active cooldowns
        active_cooldowns = donation_history.count_documents({
            'cooldown_end': {'$gt': datetime.now(UTC)}
        })
        
        # Get donations by hospital
        hospital_stats = list(donation_history.aggregate([
            {'$group': {'_id': '$hospital_name', 'count': {'$sum': 1}}}
        ]))
        
        return jsonify({
            'success': True,
            'stats': {
                'total_donations': total_donations,
                'blood_group_stats': blood_group_stats,
                'active_cooldowns': active_cooldowns,
                'hospital_stats': hospital_stats
            }
        })
        
    except Exception as e:
        print(f"Error getting donation stats: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/admin/blood_donation_forms')
@admin_required
def admin_blood_donation_forms():
    return render_template('admin_blood_donation_forms.html')

@app.route('/admin/blood_donation_forms/data', methods=['GET'])
@admin_required
def get_blood_donation_forms():
    try:
        # Get query parameters
        status = request.args.get('status')
        request_id = request.args.get('request_id')
        
        # Build query
        query = {'admin_id': ObjectId(session['admin'])}
        if status:
            query['status'] = status
        if request_id:
            query['request_id'] = request_id
        
        # Get form submissions
        forms = list(blood_donation_forms.find(query).sort('submitted_at', -1))
        
        # Get user details for each form
        form_details = []
        for form in forms:
            user = users.find_one({'_id': ObjectId(form['user_id'])})
            if user:
                form_details.append({
                    'form_id': str(form['_id']),
                    'request_id': form['request_id'],
                    'user_name': user.get('name', 'Unknown'),
                    'user_phone': user.get('phone', 'N/A'),
                    'user_email': user.get('email', 'N/A'),
                    'submitted_at': form['submitted_at'].strftime('%Y-%m-%d %H:%M:%S'),
                    'status': form['status'],
                    'form_data': form['form_data']
                })
        
        return jsonify({
            'success': True,
            'forms': form_details
        })
        
    except Exception as e:
        print(f"Error getting blood donation forms: {str(e)}")
        return jsonify({'error': str(e)}), 500

def is_eligible_for_donation(user):
    """
    Check if a user is eligible for blood donation based on their stored data
    """
    try:
        # Check age
        if not (18 <= user.get('age', 0) <= 65):
            return False, "Age must be between 18 and 65 years"
        
        # Check weight
        if user.get('weight', 0) < 45:
            return False, "Minimum weight required is 45 kg for blood donation"
        
        # Check height
        if not (140 <= user.get('height', 0) <= 220):
            return False, "Height must be between 140 and 220 cm"
        
        # Check last donation date
        last_donation = user.get('last_donation_date')
        if last_donation:
            if isinstance(last_donation, str):
                last_donation = datetime.strptime(last_donation, '%Y-%m-%d')
            today = datetime.now()
            four_months_ago = today - timedelta(days=120)
            
            if last_donation > today:
                return False, "Last donation date cannot be in the future"
            if last_donation > four_months_ago:
                return False, "You cannot donate if your last donation was less than 4 months ago"
        
        return True, "Eligible for donation"
    except Exception as e:
        logger.error(f"Error checking donation eligibility: {str(e)}")
        return False, "Error checking eligibility"

def get_eligible_donors(blood_group=None, max_distance=None, admin_location=None):
    """
    Get list of eligible donors based on criteria
    """
    try:
        query = {}
        if blood_group:
            query['blood_group'] = blood_group
        
        # Get all matching donors
        donors = list(users.find(query))
        eligible_donors = []
        
        for donor in donors:
            # Check eligibility
            is_eligible, message = is_eligible_for_donation(donor)
            if not is_eligible:
                continue
            
            # Check distance if admin location is provided
            if max_distance and admin_location:
                donor_location = donor.get('location', {}).get('coordinates', [0, 0])
                distance = haversine_distance(
                    admin_location['lat'], admin_location['lon'],
                    donor_location[1], donor_location[0]
                )
                if distance > max_distance:
                    continue
            
            # Add donor to eligible list
            eligible_donors.append({
                'id': str(donor['_id']),
                'name': donor['name'],
                'blood_group': donor['blood_group'],
                'age': donor['age'],
                'gender': donor['gender'],
                'weight': donor['weight'],
                'height': donor['height'],
                'phone': donor.get('phone', 'N/A'),
                'email': donor['email'],
                'last_donation_date': donor.get('last_donation_date'),
                'location': donor.get('location', {}).get('address', 'Address not available')
            })
        
        return eligible_donors
        
    except Exception as e:
        logger.error(f"Error getting eligible donors: {str(e)}")
        return []

def update_user_health_data(user_id, data):
    """
    Update user's health-related data
    """
    try:
        update_data = {}
        
        # Validate and update age
        if 'age' in data:
            age = int(data['age'])
            if 18 <= age <= 65:
                update_data['age'] = age
            else:
                return False, "Age must be between 18 and 65 years"
        
        # Update gender
        if 'gender' in data:
            if data['gender'] in ['male', 'female', 'other']:
                update_data['gender'] = data['gender']
            else:
                return False, "Invalid gender value"
        
        # Validate and update height
        if 'height' in data:
            height = float(data['height'])
            if 140 <= height <= 220:
                update_data['height'] = height
            else:
                return False, "Height must be between 140 and 220 cm"
        
        # Validate and update weight
        if 'weight' in data:
            weight = float(data['weight'])
            if weight >= 45:
                update_data['weight'] = weight
            else:
                return False, "Minimum weight required is 45 kg"
        
        # Update last donation date
        if 'last_donation_date' in data:
            try:
                last_donation = datetime.strptime(data['last_donation_date'], '%Y-%m-%d')
                today = datetime.now()
                if last_donation <= today:
                    update_data['last_donation_date'] = data['last_donation_date']
                else:
                    return False, "Last donation date cannot be in the future"
            except ValueError:
                return False, "Invalid date format"
        
        if update_data:
            result = users.update_one(
                {'_id': ObjectId(user_id)},
                {'$set': update_data}
            )
            if result.modified_count > 0:
                return True, "Health data updated successfully"
            else:
                return False, "User not found"
        
        return False, "No valid data to update"
        
    except Exception as e:
        logger.error(f"Error updating user health data: {str(e)}")
        return False, "Error updating health data"

@app.route('/user/update_health_data', methods=['POST'])
def update_health_data():
    if 'user' not in session:
        return jsonify({'error': 'Not authorized'}), 401
        
    try:
        data = request.get_json()
        user_id = session['user']
        
        success, message = update_user_health_data(user_id, data)
        if success:
            return jsonify({'message': message}), 200
        else:
            return jsonify({'error': message}), 400
            
    except Exception as e:
        logger.error(f"Error updating health data: {str(e)}")
        return jsonify({'error': 'An error occurred while updating health data'}), 500

@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json()
        message = data.get('message', '').lower().strip()
        language = data.get('language', 'en')
        
        if not message:
            return jsonify({'error': 'No message provided'}), 400

        # Define responses for different types of questions
        responses = {
            'eligibility': """Eligibility Requirements:
• Age: 18-65 years
• Weight: Minimum 45 kg
• Good health condition
• No recent infections
• Valid ID required""",
            
            'benefits': """Benefits of Donation:
• Saves up to 3 lives
• Free health screening
• Reduces heart disease risk
• Helps maintain iron levels
• Feel good helping others""",
            
            'preparation': """Preparation Tips:
• Get good sleep
• Eat a healthy meal
• Drink plenty of water
• Wear comfortable clothes
• Bring ID""",
            
            'process': """Donation Process:
• Quick health check
• Mini physical exam
• 8-10 minutes donation
• Rest and refreshments
• Total time: 45 minutes""",
            
            'frequency': """Donation Frequency:
• Whole blood: Every 56 days
• Platelets: Every 7 days
• Plasma: Every 28 days
• Double red cells: Every 112 days""",
            
            'general': """General Information:
• Blood donation saves lives
• Process is safe and easy
• Takes about 45 minutes
• All blood types needed
• Contact blood bank for details"""
        }
        
        # Check message content and return appropriate response
        response = None
        
        # Define keywords for each category
        keywords = {
            'eligibility': ['eligible', 'qualify', 'can i donate', 'requirements', 'criteria'],
            'benefits': ['benefit', 'advantage', 'why donate', 'help', 'good'],
            'preparation': ['prepare', 'before donation', 'how to', 'ready', 'tips'],
            'process': ['process', 'procedure', 'what happens', 'during', 'step'],
            'frequency': ['how often', 'frequency', 'when again', 'wait', 'period']
        }
        
        # Check message against keywords
        for category, words in keywords.items():
            if any(word in message for word in words):
                response = responses[category]
                break
        
        # If no specific match found, return general information
        if not response:
            response = responses['general']
        
        # Translate if needed
        if language != 'en':
            try:
                response = translate_text(response, language)
            except Exception as e:
                print(f"Translation error: {str(e)}")
                # Keep English response if translation fails
        
        return jsonify({
            'success': True,
            'response': response
        })
        
    except Exception as e:
        print(f"Chat API error: {str(e)}")
        return jsonify({
            'success': True,
            'response': 'Please try again or contact the blood bank directly for assistance.'
        }), 200

@app.route('/profile')
@login_required
def profile():
    language = session.get('language', 'en')
    translations = get_translations(language)
    
    user_id = session['user']
    user = users.find_one({'_id': ObjectId(user_id)})
    
    if not user:
        session.pop('user', None)
        flash(translations['error_message'])
        return redirect(url_for('login'))
    
    # Ensure user has location data
    if 'location' not in user:
        user['location'] = {
            'type': 'Point',
            'coordinates': [0, 0],
            'address': ''
        }
    
    return render_template('profile.html',
                         user=user,
                         translations=translations)

@app.route('/user/update_profile', methods=['POST'])
def update_user_profile():
    if 'user' not in session:
        return jsonify({'error': 'Not authorized'}), 401
        
    try:
        user_id = session['user']
        data = request.form
        
        # Validate data
        if not data.get('name') or not data.get('phone') or not data.get('age') or not data.get('height') or not data.get('weight') or not data.get('gender'):
            return jsonify({'error': 'All fields are required'}), 400
            
        # Update user profile
        update_data = {
            'name': data.get('name'),
            'phone': data.get('phone'),
            'age': int(data.get('age')),
            'height': float(data.get('height')),
            'weight': float(data.get('weight')),
            'gender': data.get('gender')
        }
        
        result = users.update_one(
            {'_id': ObjectId(user_id)},
            {'$set': update_data}
        )
        
        if result.modified_count > 0:
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'No changes made'}), 400
            
    except Exception as e:
        print(f"Error updating profile: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/user/update_avatar', methods=['POST'])
def update_avatar():
    if 'user' not in session:
        return jsonify({'error': 'Not authorized'}), 401
        
    try:
        if 'avatar' not in request.files:
            return jsonify({'error': 'No file uploaded'}), 400
            
        file = request.files['avatar']
        if file.filename == '':
            return jsonify({'error': 'No file selected'}), 400
            
        if file and allowed_file(file.filename):
            # Generate unique filename
            filename = secure_filename(f"avatar_{session['user']}_{file.filename}")
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            
            # Save file
            file.save(file_path)
            
            # Update user's avatar in database
            result = users.update_one(
                {'_id': ObjectId(session['user'])},
                {'$set': {'avatar': filename}}
            )
            
            if result.modified_count > 0:
                return jsonify({
                    'success': True,
                    'avatar_url': url_for('static', filename=f'uploads/{filename}')
                })
            else:
                return jsonify({'error': 'Failed to update avatar'}), 500
        else:
            return jsonify({'error': 'Invalid file type'}), 400
            
    except Exception as e:
        print(f"Error updating avatar: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/user/update_location', methods=['POST'])
@login_required
def update_location():
    try:
        print("Received location update request")
        data = request.get_json()
        print("Received data:", data)
        
        if not data:
            print("No JSON data received")
            return jsonify({'success': False, 'error': 'No data received'}), 400
            
        required_fields = ['latitude', 'longitude', 'address']
        missing_fields = [field for field in required_fields if field not in data]
        
        if missing_fields:
            print(f"Missing required fields: {missing_fields}")
            return jsonify({'success': False, 'error': f'Missing required fields: {", ".join(missing_fields)}'}), 400
            
        try:
            latitude = float(data['latitude'])
            longitude = float(data['longitude'])
        except (ValueError, TypeError):
            print("Invalid latitude or longitude values")
            return jsonify({'success': False, 'error': 'Invalid latitude or longitude values'}), 400
            
        user_id = session['user']
        print(f"Updating location for user {user_id}")
        print(f"New coordinates: {latitude}, {longitude}")
        print(f"New address: {data['address']}")
        
        # Update user's location in MongoDB
        result = users.update_one(
            {'_id': ObjectId(user_id)},
            {'$set': {
                'location': {
                    'type': 'Point',
                    'coordinates': [longitude, latitude],
                    'address': data['address']
                }
            }}
        )
        
        if result.modified_count > 0:
            print("Location updated successfully")
            return jsonify({'success': True})
        else:
            print("No changes made to location")
            return jsonify({'success': False, 'error': 'No changes made'}), 400
            
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        return jsonify({'success': False, 'error': 'An unexpected error occurred'}), 500

@app.route('/user/stats')
def get_user_stats():
    if 'user' not in session:
        return jsonify({'error': 'Not authorized'}), 401
        
    try:
        user_id = session['user']
        user = users.find_one({'_id': ObjectId(user_id)})
        
        if not user:
            return jsonify({'error': 'User not found'}), 404
            
        # Get donation history
        donations = list(donation_history.find({'user_id': user_id}).sort('donation_date', -1))
        
        # Calculate stats
        total_donations = len(donations)
        last_donation = donations[0]['donation_date'].strftime('%Y-%m-%d') if donations else None
        
        # Calculate next eligible date
        next_eligible = None
        if last_donation:
            last_donation_date = donations[0]['donation_date']
            next_eligible_date = last_donation_date + timedelta(days=90)
            if datetime.now(UTC) < next_eligible_date:
                next_eligible = next_eligible_date.strftime('%Y-%m-%d')
            else:
                next_eligible = 'Now'
        
        return jsonify({
            'success': True,
            'stats': {
                'total_donations': total_donations,
                'last_donation': last_donation,
                'next_eligible': next_eligible
            }
        })
        
    except Exception as e:
        print(f"Error getting user stats: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/user/upcoming_camps')
def get_upcoming_camps():
    if 'user' not in session:
        return jsonify({'error': 'Not authorized'}), 401
        
    try:
        # Get user's location
        user = users.find_one({'_id': ObjectId(session['user'])})
        if not user or 'location' not in user:
            return jsonify({'error': 'User location not found'}), 404
            
        user_lat = user['location']['coordinates'][1]
        user_lng = user['location']['coordinates'][0]
        
        # Get all blood camps within 10km
        camps = []
        for admin in admins.find():
            if 'location' not in admin:
                continue
                
            admin_lat = admin['location']['coordinates'][1]
            admin_lon = admin['location']['coordinates'][0]
            
            # Calculate distance
            distance = haversine_distance(user_lat, user_lng, admin_lat, admin_lon)
            
            if distance <= 10:  # Within 10km
                camps.append({
                    'name': admin['hospital_name'],
                    'date': (datetime.now(UTC) + timedelta(days=7)).strftime('%Y-%m-%d'),  # Example: 7 days from now
                    'location': admin['address']
                })
        
        return jsonify({
            'success': True,
            'camps': camps
        })
        
    except Exception as e:
        print(f"Error getting upcoming camps: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/user/blood_donation_form/<request_id>')
def blood_donation_form(request_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    
    try:
        # Check if user has accepted this request
        notification = notifications.find_one({
            'request_id': request_id,
            'user_id': session['user'],
            'status': 'responded',
            'response': 'accepted'
        })
        
        if not notification:
            flash('You can only access this form if you have accepted the blood donation request.', 'error')
            return redirect(url_for('dashboard'))
        
        # Get request details
        blood_request = notifications.find_one({
            'request_id': request_id,
            'type': 'blood_request'
        })
        
        if not blood_request:
            flash('Blood request not found.', 'error')
            return redirect(url_for('dashboard'))
        
        # Get admin details
        admin = admins.find_one({'_id': ObjectId(blood_request['admin_id'])})
        
        return render_template('blood_donation_form.html', 
                             request_id=request_id,
                             blood_request=blood_request,
                             admin=admin)
        
    except Exception as e:
        print(f"Error loading blood donation form: {str(e)}")
        flash('Error loading form. Please try again.', 'error')
        return redirect(url_for('dashboard'))

@app.route('/user/submit_blood_donation_form', methods=['POST'])
def submit_blood_donation_form():
    if 'user' not in session:
        return jsonify({'error': 'Not authorized'}), 401
    
    try:
        data = request.get_json()
        request_id = data.get('request_id')
        
        # Check if user has accepted this request
        notification = notifications.find_one({
            'request_id': request_id,
            'user_id': session['user'],
            'status': 'responded',
            'response': 'accepted'
        })
        
        if not notification:
            return jsonify({'error': 'You can only submit this form if you have accepted the blood donation request.'}), 400
        
        # Check if form has already been submitted
        existing_form = blood_donation_forms.find_one({
            'request_id': request_id,
            'user_id': session['user']
        })
        
        if existing_form:
            return jsonify({'error': 'Form has already been submitted for this request.'}), 400
        
        # Create form submission
        form_data = {
            'request_id': request_id,
            'user_id': session['user'],
            'admin_id': notification['admin_id'],
            'form_data': {
                'personal_info': data.get('personal_info', {}),
                'general_eligibility': data.get('general_eligibility', {}),
                'medical_history': data.get('medical_history', {}),
                'infectious_disease_risk': data.get('infectious_disease_risk', {}),
                'travel_lifestyle': data.get('travel_lifestyle', {}),
                'women_donors': data.get('women_donors', {}),
                'current_health': data.get('current_health', {}),
                'consent_declaration': data.get('consent_declaration', {}),
                'staff_checks': data.get('staff_checks', {})
            },
            'submitted_at': datetime.now(UTC),
            'status': 'submitted'
        }
        
        result = blood_donation_forms.insert_one(form_data)
        
        if result.inserted_id:
            return jsonify({'success': True, 'message': 'Form submitted successfully!'})
        else:
            return jsonify({'error': 'Failed to submit form. Please try again.'}), 500
            
    except Exception as e:
        print(f"Error submitting blood donation form: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.context_processor
def inject_language():
    return dict(language=session.get('language', 'en'))

# Error handlers with translations
@app.errorhandler(404)
def page_not_found(e):
    language = session.get('language', 'en')
    translations = get_translations(language)
    return render_template('errors/404.html',
                         error_title=translations['404_title'],
                         error_message=translations['404_message'],
                         translations=translations), 404

@app.errorhandler(500)
def server_error(e):
    language = session.get('language', 'en')
    translations = get_translations(language)
    return render_template('errors/500.html',
                         error_title=translations['500_title'],
                         error_message=translations['500_message'],
                         translations=translations), 500

@app.errorhandler(403)
def forbidden(e):
    language = session.get('language', 'en')
    translations = get_translations(language)
    return render_template('errors/403.html',
                         error_title=translate_text('Access Denied', language),
                         error_message=translate_text('You do not have permission to access this page', language),
                         translations=translations), 403

# Context processor to make translations available in all templates
@app.context_processor
def inject_translations():
    language = session.get('language', 'en')
    return {'translations': get_translations(language)}

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    language = session.get('language', 'en')
    translations = get_translations(language)
    
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        admin = admins.find_one({'email': email})
        
        if admin and admin['password'] == password:  # Using plain password as per requirements
            session['admin'] = str(admin['_id'])
            session['admin_email'] = admin['email']
            session['admin_name'] = admin.get('name', '')
            session['admin_hospital'] = admin.get('hospital_name', '')
            flash(translations['login_success'])
            return redirect(url_for('admin_dashboard'))
        else:
            flash(translations['invalid_credentials'])
    
    return render_template('admin_login.html', translations=translations)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin', None)
    session.pop('admin_email', None)
    session.pop('admin_name', None)
    session.pop('admin_hospital', None)
    flash('Logged out successfully')
    return redirect(url_for('admin_login'))

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    try:
        language = session.get('language', 'en')
        translations = get_translations(language)
        
        # Get admin info
        admin_id = session.get('admin')
        admin = admins.find_one({'_id': ObjectId(admin_id)})
        
        # Get admin's hospital location
        admin_location = admin.get('location', {}).get('coordinates', [0, 0])
        
        # Get nearby donors (within 10km)
        nearby_donors = list(users.find({
            'location': {
                '$near': {
                    '$geometry': {
                        'type': 'Point',
                        'coordinates': admin_location
                    },
                    '$maxDistance': 10000  # 10km in meters
                }
            }
        }).limit(5))
        
        # Get recent donation history
        recent_donations = list(donation_history.find().sort('donation_date', -1).limit(5))
        
        # Get pending blood requests
        pending_requests = list(notifications.find({
            'type': 'blood_request',
            'status': 'pending'
        }).sort('created_at', -1).limit(5))
        
        # Get accepted donation requests (users who accepted requests)
        accepted_requests = list(notifications.find({
            'type': 'blood_request',
            'status': 'responded',
            'response': 'accepted'
        }).sort('response_time', -1).limit(10))
        
        # Get donor details for each accepted request
        accepted_donors = []
        for request in accepted_requests:
            donor = users.find_one({'_id': ObjectId(request['user_id'])})
            if donor:
                # Get the blood request details
                blood_request = notifications.find_one({
                    '_id': ObjectId(request['request_id']),
                    'type': 'blood_request'
                })
                
                accepted_donors.append({
                    'user_id': str(donor['_id']),
                    'name': donor['name'],
                    'blood_group': donor['blood_group'],
                    'phone': donor.get('phone', 'N/A'),
                    'email': donor['email'],
                    'address': donor.get('address', 'N/A'),
                    'response_time': request['response_time'].strftime('%Y-%m-%d %H:%M:%S'),
                    'request_id': request['request_id'],
                    'blood_group_needed': blood_request.get('data', {}).get('blood_group', 'N/A') if blood_request else 'N/A',
                    'request_date': blood_request.get('created_at').strftime('%Y-%m-%d %H:%M:%S') if blood_request and blood_request.get('created_at') else 'N/A'
                })
        
        return render_template('admin_dashboard.html',
                             admin=admin,
                             nearby_donors=nearby_donors,
                             recent_donations=recent_donations,
                             pending_requests=pending_requests,
                             accepted_donors=accepted_donors,
                             translations=translations)
                             
    except Exception as e:
        print(f"Error in admin dashboard: {str(e)}")
        flash(translations['error_message'])
        return redirect(url_for('admin_login'))

@app.route('/admin/signup', methods=['GET', 'POST'])
def admin_signup():
    language = session.get('language', 'en')
    translations = get_translations(language)
    if request.method == 'POST':
        name = request.form.get('name')
        email = request.form.get('email')
        password = request.form.get('password')
        hospital_name = request.form.get('hospital_name')
        hospital_id = request.form.get('hospital_id')  # New field
        phone = request.form.get('phone')
        location = request.form.get('location')
        latitude = request.form.get('latitude')
        longitude = request.form.get('longitude')
        # Validate hospital ID format (e.g., HOSP001)
        if not hospital_id or not hospital_id.startswith('HOSP'):
            flash('Invalid hospital ID format. Must start with HOSP followed by numbers')
            return render_template('admin_signup.html', translations=translations)
        if admins.find_one({'email': email}):
            flash(translations['email_exists'])
            return render_template('admin_signup.html', translations=translations)
        if admins.find_one({'hospital_id': hospital_id}):
            flash('Hospital ID already exists')
            return render_template('admin_signup.html', translations=translations)
        default_inventory = {
            'A+': 0, 'A-': 0, 'B+': 0, 'B-': 0,
            'AB+': 0, 'AB-': 0, 'O+': 0, 'O-': 0
        }
        new_admin = {
            'name': name,
            'email': email,
            'password': password,  # Using plain password as per requirements
            'hospital_name': hospital_name,
            'hospital_id': hospital_id,
            'phone': phone,
            'location': {
                'type': 'Point',
                'coordinates': [float(longitude), float(latitude)],
                'address': location
            },
            'status': 'pending',  # Set initial status as pending
            'created_at': datetime.utcnow(),
            'blood_inventory': default_inventory
        }
        admins.insert_one(new_admin)
        flash(translations['registration_success'])
        return redirect(url_for('admin_login'))
    return render_template('admin_signup.html', translations=translations)

# Add routes for notice card management
@app.route('/admin/create_notice', methods=['POST'])
@admin_required
def create_notice():
    try:
        data = request.get_json()
        
        # Validate required fields
        required_fields = ['title', 'organization_type', 'organization_name', 'description', 
                         'contact_person', 'contact_number', 'email', 'address']
        for field in required_fields:
            if not data.get(field):
                return jsonify({'success': False, 'error': f'{field} is required'})
        
        # Create notice document
        notice = {
            'title': data['title'],
            'organization_type': data['organization_type'],
            'organization_name': data['organization_name'],
            'description': data['description'],
            'contact_person': data['contact_person'],
            'contact_number': data['contact_number'],
            'email': data['email'],
            'address': data['address'],
            'event_date': data.get('event_date'),
            'requirements': data.get('requirements', []),
            'blood_groups_needed': data.get('blood_groups_needed', []),
            'image_url': data.get('image_url'),
            'latitude': data.get('latitude'),
            'longitude': data.get('longitude'),
            'location': data.get('location'),
            'created_at': datetime.utcnow(),
            'created_by': session['admin'],  # Changed from user_id to admin
            'status': 'active'
        }
        
        # Insert notice into database
        db.notice_cards.insert_one(notice)
        
        # Send notifications to all users
        users = db.users.find({})
        notification_text = f"New {notice['organization_type']} notice: {notice['title']} by {notice['organization_name']}"
        
        for user in users:
            notification = {
                'user_id': user['_id'],
                'message': notification_text,
                'notice_id': notice['_id'],
                'created_at': datetime.utcnow(),
                'read': False
            }
            db.notifications.insert_one(notification)
            
            # Send SMS if phone number is available
            if user.get('phone'):
                try:
                    send_sms(user['phone'], notification_text)
                except Exception as e:
                    print(f"Error sending SMS to {user['phone']}: {str(e)}")
        
        return jsonify({'success': True, 'message': 'Notice created successfully'})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/notice')
@admin_required
def admin_notice():
    return render_template('admin_notice.html')

@app.route('/admin/notices')
@admin_required
def get_notices():
    try:
        notices = list(db.notice_cards.find({'created_by': session['admin']}).sort('created_at', -1))
        
        # Convert ObjectId to string for JSON serialization
        for notice in notices:
            notice['_id'] = str(notice['_id'])
            notice['created_by'] = str(notice['created_by'])
        
        return jsonify({'success': True, 'notices': notices})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/notice/<notice_id>', methods=['DELETE'])
@admin_required
def delete_notice(notice_id):
    try:
        # Verify ownership
        notice = db.notice_cards.find_one({'_id': ObjectId(notice_id), 'created_by': session['admin']})
        if not notice:
            return jsonify({'success': False, 'error': 'Notice not found or unauthorized'})
        
        # Delete notice
        db.notice_cards.delete_one({'_id': ObjectId(notice_id)})
        
        # Delete related notifications
        db.notifications.delete_many({'notice_id': ObjectId(notice_id)})
        
        return jsonify({'success': True, 'message': 'Notice deleted successfully'})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/notice/<notice_id>', methods=['PUT'])
@admin_required
def update_notice_status(notice_id):
    try:
        data = request.get_json()
        
        # Verify ownership
        notice = db.notice_cards.find_one({'_id': ObjectId(notice_id), 'created_by': session['admin']})
        if not notice:
            return jsonify({'success': False, 'error': 'Notice not found or unauthorized'})
        
        # Update status
        db.notice_cards.update_one(
            {'_id': ObjectId(notice_id)},
            {'$set': {'status': data['status']}}
        )
        
        return jsonify({'success': True, 'message': 'Notice status updated successfully'})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

def calculate_distance(lat1, lon1, lat2, lon2):
    """
    Calculate the distance between two points using the Haversine formula.
    Returns distance in kilometers.
    """
    R = 6371  # Earth's radius in kilometers

    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    
    # Convert latitude and longitude to radians
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    
    # Haversine formula
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    
    # Calculate distance in kilometers
    distance = R * c
    return round(distance, 2)

# User-facing notice routes
@app.route('/notices')
@login_required
def user_notices():
    try:
        # Get active notices
        notices = list(db.notice_cards.find({'status': 'active'}).sort('created_at', -1))
        
        # Convert ObjectId to string and add distance calculation
        for notice in notices:
            notice['_id'] = str(notice['_id'])
            notice['created_by'] = str(notice['created_by'])
            
            # Add distance calculation if user location is available
            if 'location' in session and notice.get('latitude') and notice.get('longitude'):
                user_lat = float(session['location']['latitude'])
                user_lng = float(session['location']['longitude'])
                notice['distance'] = calculate_distance(
                    user_lat, user_lng,
                    float(notice['latitude']), float(notice['longitude'])
                )
        
        # Render template instead of returning JSON
        return render_template('user_notices.html', notices=notices)
    
    except Exception as e:
        flash('Error loading notices: ' + str(e))
        return redirect(url_for('dashboard'))

# Add API endpoint for getting notices data
@app.route('/api/notices')
@login_required
def get_notices_api():
    try:
        notices = list(db.notice_cards.find({'status': 'active'}).sort('created_at', -1))
        
        for notice in notices:
            notice['_id'] = str(notice['_id'])
            notice['created_by'] = str(notice['created_by'])
            
            if 'location' in session and notice.get('latitude') and notice.get('longitude'):
                user_lat = float(session['location']['latitude'])
                user_lng = float(session['location']['longitude'])
                notice['distance'] = calculate_distance(
                    user_lat, user_lng,
                    float(notice['latitude']), float(notice['longitude'])
                )
        
        return jsonify({'success': True, 'notices': notices})
    
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/geocode', methods=['GET'])
def geocode():
    """Proxy endpoint for geocoding to avoid CORS issues"""
    try:
        lat = request.args.get('lat')
        lon = request.args.get('lon')
        
        if not lat or not lon:
            return jsonify({'error': 'Missing latitude or longitude'}), 400
            
        # Make request to OpenStreetMap Nominatim
        response = requests.get(
            f'https://nominatim.openstreetmap.org/reverse',
            params={
                'format': 'json',
                'lat': lat,
                'lon': lon
            },
            headers={
                'Accept-Language': 'en',
                'User-Agent': 'BloodDonationApp/1.0'
            }
        )
        
        # Check if request was successful
        if response.status_code != 200:
            return jsonify({'error': 'Geocoding service error'}), 500
            
        # Return the response data
        return jsonify(response.json())
        
    except Exception as e:
        print(f"Error in geocoding: {str(e)}")
        return jsonify({'error': str(e)}), 500

# --- Admin-to-Admin Interaction: Inter-Hospital Blood Requests ---

@app.route('/admin/hospitals', methods=['GET'])
@admin_required
def list_hospitals():
    """
    List all hospitals/admins (excluding self). No inventory shown.
    """
    try:
        admin_id = session['admin']
        hospitals = list(admins.find({'_id': {'$ne': ObjectId(admin_id)}}))
        for h in hospitals:
            h['_id'] = str(h['_id'])
            h['password'] = None
            # Remove inventory if present
            h.pop('blood_inventory', None)
        return jsonify({'success': True, 'hospitals': hospitals})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/request_blood', methods=['POST'])
@admin_required
def request_blood():
    """
    Request blood from another hospital/admin
    """
    try:
        data = request.get_json()
        from_admin = session['admin']
        to_admin = data.get('to_admin_id')
        blood_group = data.get('blood_group')
        units = int(data.get('units', 1))
        message = data.get('message', '')
        if not (to_admin and blood_group and units):
            return jsonify({'success': False, 'error': 'Missing required fields'})
        request_doc = {
            'from_admin': from_admin,
            'to_admin': to_admin,
            'blood_group': blood_group,
            'units': units,
            'message': message,
            'status': 'pending',
            'created_at': datetime.utcnow(),
            'response': None,
            'response_message': None,
            'response_time': None
        }
        db.inter_hospital_requests.insert_one(request_doc)
        return jsonify({'success': True, 'message': 'Request sent'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/inter_hospital_requests', methods=['GET'])
@admin_required
def view_inter_hospital_requests():
    """
    View incoming and outgoing inter-hospital blood requests
    """
    try:
        admin_id = session['admin']
        incoming = list(db.inter_hospital_requests.find({'to_admin': admin_id}))
        outgoing = list(db.inter_hospital_requests.find({'from_admin': admin_id}))
        for req in incoming + outgoing:
            req['_id'] = str(req['_id'])
        return jsonify({'success': True, 'incoming': incoming, 'outgoing': outgoing})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/respond_request', methods=['POST'])
@admin_required
def respond_request():
    """
    Respond to an inter-hospital blood request
    """
    try:
        data = request.get_json()
        request_id = data.get('request_id')
        response = data.get('response')  # 'accepted' or 'rejected'
        response_message = data.get('message', '')
        if not (request_id and response in ['accepted', 'rejected']):
            return jsonify({'success': False, 'error': 'Missing or invalid parameters'})
        db.inter_hospital_requests.update_one(
            {'_id': ObjectId(request_id)},
            {'$set': {
                'status': response,
                'response_message': response_message,
                'response_time': datetime.utcnow()
            }}
        )
        return jsonify({'success': True, 'message': 'Response recorded'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

# --- Admin to Admin (ATA) UI page ---
@app.route('/admin/ata')
@admin_required
def admin_to_admin_page():
    """
    Render the Admin to Admin (ATA) page where admins can see other admins and send blood requests.
    """
    admin_id = session['admin']
    hospitals = list(admins.find({'_id': {'$ne': ObjectId(admin_id)}}))
    for h in hospitals:
        h['_id'] = str(h['_id'])
        h['password'] = None
        h.pop('blood_inventory', None)
    return render_template('admin_ata.html', hospitals=hospitals)

@app.route('/admin/incoming_requests', methods=['GET'])
@admin_required
def incoming_requests():
    """
    Return all incoming inter-hospital blood requests for the logged-in admin.
    """
    try:
        admin_id = session['admin']
        requests = list(db.inter_hospital_requests.find({'to_admin': admin_id}))
        for req in requests:
            req['_id'] = str(req['_id'])
            # Optionally, add from_admin details
            from_admin = admins.find_one({'_id': ObjectId(req['from_admin'])})
            req['from_admin_details'] = {
                'hospital_name': from_admin.get('hospital_name', ''),
                'hospital_id': from_admin.get('hospital_id', ''),
                'email': from_admin.get('email', ''),
                'phone': from_admin.get('phone', ''),
                'address': from_admin.get('location', {}).get('address', '') if from_admin.get('location') else ''
            } if from_admin else {}
        return jsonify({'success': True, 'requests': requests})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/admin/incoming_requests_page')
@admin_required
def incoming_requests_page():
    """
    Render the page for viewing incoming admin-to-admin requests.
    """
    return render_template('admin_incoming_requests.html')

@app.route('/admin/outgoing_requests_page')
@admin_required
def outgoing_requests_page():
    """
    Render the page for viewing outgoing admin-to-admin requests.
    """
    return render_template('admin_outgoing_requests.html')

@app.route('/admin/blood_availability', methods=['GET'])
@admin_required
def blood_availability_page():
    """
    Render the blood availability management page for the admin.
    """
    try:
        admin_id = session['admin']
        admin = admins.find_one({'_id': ObjectId(admin_id)})
        # Get or initialize blood inventory
        default_inventory = {
            'A+': 0, 'A-': 0, 'B+': 0, 'B-': 0,
            'AB+': 0, 'AB-': 0, 'O+': 0, 'O-': 0
        }
        blood_inventory = admin.get('blood_inventory', default_inventory.copy())
        # Ensure keys exist
        for bg in default_inventory:
            blood_inventory.setdefault(bg, 0)

        # Build summary based on actual blood_units records
        summary_template = {
            bg: {'available': 0, 'used': 0, 'expired': 0, 'total': 0}
            for bg in default_inventory
        }
        blood_units = admin.get('blood_units', [])
        for unit in blood_units:
            bg = unit.get('blood_type')
            if bg not in summary_template:
                continue
            status = unit.get('status', 'available')
            summary_template[bg]['total'] += 1
            if status in summary_template[bg]:
                summary_template[bg][status] += 1

        return render_template(
            'admin_blood_availability.html',
            admin=admin,
            blood_inventory=blood_inventory,
            blood_summary=summary_template
        )
    except Exception as e:
        flash('Error loading blood availability page: ' + str(e))
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/update_blood_inventory', methods=['POST'])
@admin_required
def update_blood_inventory():
    """
    Update the blood inventory for the admin's blood bank.
    """
    try:
        admin_id = session['admin']
        data = request.get_json() or request.form
        # Only allow valid blood groups
        valid_blood_groups = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']
        inventory_update = {}
        for bg in valid_blood_groups:
            count = data.get(bg)
            if count is not None:
                try:
                    inventory_update[f'blood_inventory.{bg}'] = int(count)
                except ValueError:
                    continue
        if not inventory_update:
            return jsonify({'success': False, 'error': 'No valid blood group data provided'}), 400
        result = admins.update_one({'_id': ObjectId(admin_id)}, {'$set': inventory_update})
        if result.modified_count > 0:
            return jsonify({'success': True, 'message': 'Blood inventory updated successfully'})
        else:
            return jsonify({'success': False, 'error': 'No changes made'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/admin/blood_availability/data', methods=['GET'])
@admin_required
def get_blood_availability_data():
    """
    Provide current blood inventory counts for charts / AJAX calls.
    """
    try:
        admin_id = session['admin']
        admin = admins.find_one({'_id': ObjectId(admin_id)}, {'blood_inventory': 1})
        default_inventory = {
            'A+': 0, 'A-': 0, 'B+': 0, 'B-': 0,
            'AB+': 0, 'AB-': 0, 'O+': 0, 'O-': 0
        }
        blood_inventory = admin.get('blood_inventory', default_inventory) if admin else default_inventory
        # Ensure all groups exist
        for bg in default_inventory:
            blood_inventory.setdefault(bg, 0)
        return jsonify({'success': True, 'inventory': blood_inventory})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


def _build_blood_store_summary(admin_doc):
    valid_blood_groups = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']
    summary = {
        bg: {'available': 0, 'used': 0, 'expired': 0, 'total': 0}
        for bg in valid_blood_groups
    }

    if not admin_doc:
        return summary

    now = datetime.now(UTC)
    blood_units = admin_doc.get('blood_units', [])
    for unit in blood_units:
        bg = unit.get('blood_type')
        if bg not in summary:
            continue

        status = unit.get('status', 'available')
        expiry_date = unit.get('expiry_date')

        if expiry_date:
            try:
                expiry_dt = parse_datetime(expiry_date)
                if expiry_dt and status == 'available' and expiry_dt < now:
                    status = 'expired'
            except Exception:
                pass

        if status not in summary[bg]:
            status = 'available'

        summary[bg]['total'] += 1
        summary[bg][status] += 1

    return summary


@app.route('/admin/blood_store', methods=['GET'])
@admin_required
def blood_store_page():
    try:
        admin_id = session['admin']
        admin = admins.find_one({'_id': ObjectId(admin_id)})
        summary = _build_blood_store_summary(admin)
        totals = {
            'available': sum(group['available'] for group in summary.values()),
            'used': sum(group['used'] for group in summary.values()),
            'expired': sum(group['expired'] for group in summary.values()),
            'total': sum(group['total'] for group in summary.values())
        }
        return render_template(
            'admin_blood_store.html',
            admin=admin,
            blood_summary=summary,
            totals=totals
        )
    except Exception as e:
        flash('Error loading blood store page: ' + str(e))
        return redirect(url_for('admin_dashboard'))


@app.route('/admin/blood_store/data', methods=['GET'])
@admin_required
def blood_store_data():
    try:
        admin_id = session['admin']
        admin = admins.find_one({'_id': ObjectId(admin_id)})
        summary = _build_blood_store_summary(admin)
        totals = {
            'available': sum(group['available'] for group in summary.values()),
            'used': sum(group['used'] for group in summary.values()),
            'expired': sum(group['expired'] for group in summary.values()),
            'total': sum(group['total'] for group in summary.values())
        }
        return jsonify({'success': True, 'summary': summary, 'totals': totals})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/admin/inventory', methods=['GET'])
@admin_required
def admin_inventory():
    """
    Display the blood inventory management page for the admin.
    Inventory is stored as subdocument array in admin collection.
    """
    try:
        admin_id = session['admin']
        admin = admins.find_one({'_id': ObjectId(admin_id)})
        
        # Get inventory items from admin subdocument array
        inventory_items = admin.get('blood_units', [])  # Array of blood unit subdocuments
        
        # Sort by entry_time (most recent first)
        inventory_items.sort(key=lambda x: parse_datetime(x.get('entry_time', datetime.now(UTC))), reverse=True)
        
        # Calculate expiry status for each item
        for item in inventory_items:
            expiry_date = item.get('expiry_date')
            if expiry_date:
                expiry_date = parse_datetime(expiry_date)
                days_remaining = (expiry_date - datetime.now(UTC)).days
                item['days_remaining'] = days_remaining
                item['is_expired'] = days_remaining < 0
                item['is_expiring_soon'] = 0 <= days_remaining <= 7
        
        return render_template('admin_inventory.html', admin=admin, inventory_items=inventory_items)
    except Exception as e:
        flash('Error loading inventory page: ' + str(e))
        return redirect(url_for('admin_dashboard'))

def generate_unique_blood_id(admin_id: str, prefix: str = "BLD") -> str:
    """
    Generate a unique blood ID for the given admin by checking existing units.
    """
    for _ in range(20):
        candidate = f"{prefix}{secrets.token_hex(3).upper()}"
        exists = admins.find_one(
            {'_id': ObjectId(admin_id), 'blood_units.blood_id': candidate},
            {'_id': 1}
        )
        if not exists:
            return candidate
    raise ValueError("Unable to generate unique blood ID. Please try again.")


@app.route('/admin/inventory/add', methods=['POST'])
@admin_required
def add_blood_unit():
    """
    Add a new blood unit to the inventory as subdocument in admin collection.
    """
    try:
        admin_id = session['admin']
        data = request.get_json() or request.form
        
        blood_type = (data.get('blood_type') or '').strip()
        blood_id = (data.get('blood_id') or '').strip()
        entry_time_str = data.get('entry_time')
        
        # Validate required fields
        if not blood_type:
            return jsonify({'success': False, 'error': 'Blood type is required'}), 400
        
        # Validate blood type
        valid_blood_groups = ['A+', 'A-', 'B+', 'B-', 'AB+', 'AB-', 'O+', 'O-']
        if blood_type not in valid_blood_groups:
            return jsonify({'success': False, 'error': 'Invalid blood type'}), 400
        
        # Get admin to check existing blood units
        admin = admins.find_one({'_id': ObjectId(admin_id)})
        blood_units = admin.get('blood_units', [])
        
        # Auto-generate blood ID if not provided
        if not blood_id:
            try:
                blood_id = generate_unique_blood_id(admin_id)
            except ValueError as ve:
                return jsonify({'success': False, 'error': str(ve)}), 500

        # Check if blood_id already exists for this admin
        existing = next((unit for unit in blood_units if unit.get('blood_id') == blood_id), None)
        if existing:
            return jsonify({'success': False, 'error': 'Blood ID already exists'}), 400
        
        # Parse entry time or use current time
        if entry_time_str:
            try:
                entry_time = parse_datetime(entry_time_str)
            except Exception as e:
                print(f"Error parsing entry_time: {e}")
                entry_time = datetime.now(UTC)
        else:
            entry_time = datetime.now(UTC)
        
        # Calculate expiry date (42 days from entry time)
        expiry_date = entry_time + timedelta(days=42)
        
        # Create inventory item (subdocument)
        inventory_item = {
            'blood_type': blood_type,
            'blood_id': blood_id,
            'entry_time': entry_time,
            'expiry_date': expiry_date,
            'status': 'available',  # available, used, expired
            'created_at': datetime.now(UTC)
        }
        
        # Add to admin's blood_units array using $push
        result = admins.update_one(
            {'_id': ObjectId(admin_id)},
            {'$push': {'blood_units': inventory_item}}
        )
        
        if result.modified_count > 0:
            return jsonify({
                'success': True,
                'message': 'Blood unit added successfully',
                'blood_id': blood_id
            })
        else:
            return jsonify({'success': False, 'error': 'Failed to add blood unit'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/admin/inventory/generate_blood_id', methods=['POST'])
@admin_required
def generate_blood_id_endpoint():
    """
    Generate a unique blood ID for the logged-in admin.
    """
    try:
        admin_id = session['admin']
        blood_id = generate_unique_blood_id(admin_id)
        return jsonify({'success': True, 'blood_id': blood_id})
    except ValueError as ve:
        return jsonify({'success': False, 'error': str(ve)}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/admin/inventory/delete/<blood_id>', methods=['DELETE'])
@admin_required
def delete_blood_unit(blood_id):
    """
    Delete a blood unit from the inventory subdocument array.
    Uses blood_id to identify the unit to delete.
    """
    try:
        admin_id = session['admin']
        
        # Get admin to verify blood unit exists
        admin = admins.find_one({'_id': ObjectId(admin_id)})
        blood_units = admin.get('blood_units', [])
        
        # Check if blood_id exists for this admin
        existing = next((unit for unit in blood_units if unit.get('blood_id') == blood_id), None)
        if not existing:
            return jsonify({'success': False, 'error': 'Blood unit not found'}), 404
        
        # Remove the item from array using $pull
        result = admins.update_one(
            {'_id': ObjectId(admin_id)},
            {'$pull': {'blood_units': {'blood_id': blood_id}}}
        )
        
        if result.modified_count > 0:
            return jsonify({'success': True, 'message': 'Blood unit deleted successfully'})
        else:
            return jsonify({'success': False, 'error': 'Failed to delete blood unit'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/inventory/update_status', methods=['POST'])
@admin_required
def update_blood_unit_status():
    """
    Update the status of a blood unit (available, used, expired).
    """
    try:
        admin_id = session['admin']
        data = request.get_json() or request.form
        blood_id = data.get('blood_id')
        new_status = (data.get('status') or '').lower()

        valid_statuses = ['available', 'used', 'expired']

        if not blood_id or new_status not in valid_statuses:
            return jsonify({'success': False, 'error': 'Invalid blood ID or status'}), 400

        update_fields = {
            'blood_units.$.status': new_status
        }

        timestamp_field = None
        if new_status == 'used':
            timestamp_field = 'blood_units.$.used_at'
        elif new_status == 'expired':
            timestamp_field = 'blood_units.$.expired_at'

        if timestamp_field:
            update_fields[timestamp_field] = datetime.now(UTC)
        else:
            # Clear timestamps if reverting back to available
            update_fields['blood_units.$.used_at'] = None
            update_fields['blood_units.$.expired_at'] = None

        result = admins.update_one(
            {'_id': ObjectId(admin_id), 'blood_units.blood_id': blood_id},
            {'$set': update_fields}
        )

        if result.modified_count > 0:
            return jsonify({'success': True, 'message': 'Blood unit status updated successfully'})
        else:
            return jsonify({'success': False, 'error': 'Blood unit not found or no change applied'}), 404
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/inventory/data', methods=['GET'])
@admin_required
def get_inventory_data():
    """
    Get inventory data as JSON for AJAX requests.
    Data is retrieved from admin subdocument array.
    """
    try:
        admin_id = session['admin']
        
        # Get admin and extract blood_units array
        admin = admins.find_one({'_id': ObjectId(admin_id)})
        inventory_items = admin.get('blood_units', [])
        
        # Sort by entry_time (most recent first)
        inventory_items.sort(key=lambda x: parse_datetime(x.get('entry_time', datetime.now(UTC))), reverse=True)
        
        # Format data for JSON response
        formatted_items = []
        for item in inventory_items:
            expiry_date = item.get('expiry_date')
            if expiry_date:
                expiry_dt = parse_datetime(expiry_date)
                days_remaining = (expiry_dt - datetime.now(UTC)).days
            else:
                days_remaining = None
            
            formatted_items.append({
                'blood_id': item.get('blood_id'),
                'blood_type': item.get('blood_type'),
                'entry_time': parse_datetime(item.get('entry_time')).isoformat() if item.get('entry_time') else None,
                'expiry_date': parse_datetime(item.get('expiry_date')).isoformat() if item.get('expiry_date') else None,
                'days_remaining': days_remaining,
                'status': item.get('status', 'available')
            })
        
        return jsonify({'success': True, 'items': formatted_items})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    print("\n=== Blood Donation System ===")
    print("Server is starting...")
    print(f"Access the application at: http://localhost:5001")
    print("===========================\n")
    app.run(debug=True, port=5001, host='0.0.0.0') 