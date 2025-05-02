import os
import json
import sys
import argparse
import re
import traceback
import time
import random
import datetime

# Google API imports
import gspread
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google.auth.transport.requests import AuthorizedSession
from googleapiclient.http import HttpRequest

# Selenium imports for web scraping
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.keys import Keys

# Import our custom Quora parsing module
import parse_quora

# Configure command line arguments
parser = argparse.ArgumentParser(description="Quora Parser Tool")
parser.add_argument('--spreadsheet_id', type=str, 
                    default='14cVYojnmWVHJNMEO4gu0Z1P6a7-1R1IzIzGhuc3_SLk',
                    help='Google Spreadsheet ID')
parser.add_argument('--sheet_name', type=str, 
                    default='Answers',
                    help='Sheet name within the spreadsheet')
parser.add_argument('--url_column', type=str,
                    default='A2:A',
                    help='Column range containing answer URLs to process (Note: starts at row 2 to skip header)')
parser.add_argument('--max_urls', type=int,
                    default=200,
                    help='Maximum number of URLs to process')
parser.add_argument('--debug', action='store_true',
                    help='Enable debug mode with more verbose output')
parser.add_argument('--headless', action='store_true',
                    help='Run Chrome in headless mode')
parser.add_argument('--login', action='store_true',
                    default=True,
                    help='Login to Quora before scraping (recommended for accessing log pages)')
parser.add_argument('--no-login', action='store_true',
                    help='Skip Quora login (will limit access to log pages and some stats)')
parser.add_argument('--credentials_file', type=str,
                    default='credentials.json',
                    help='Path to the credentials file containing Quora login info')
parser.add_argument('--url', type=str,
                    help='Process a single Quora URL directly, bypassing spreadsheet')
args = parser.parse_args()

def setup_google_api():
    """Set up Google API credentials and services"""
    scopes = [
        'https://www.googleapis.com/auth/drive',
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive.metadata.readonly'
    ]
    
    # Setup credentials
    service_account_info = os.getenv('GOOGLE_SERVICE_ACCOUNT')
    if service_account_info:
        print("Using service account from environment variable")
        credentials = service_account.Credentials.from_service_account_info(
            json.loads(service_account_info),
            scopes=scopes
        )
    else:
        # Check for credentials file in different locations
        possible_paths = [
            '../credentials.json',
            'credentials.json',
            os.path.join(os.path.expanduser('~'), 'credentials.json')
        ]
        
        credentials_found = False
        for path in possible_paths:
            if os.path.exists(path):
                print(f"Using credentials file from: {path}")
                credentials = service_account.Credentials.from_service_account_file(
                    path,
                    scopes=scopes
                )
                credentials_found = True
                break
        
        if not credentials_found:
            print("ERROR: Google service account credentials not found.")
            print("Please provide credentials through one of these methods:")
            print("1. Set GOOGLE_SERVICE_ACCOUNT environment variable with JSON content")
            print("2. Create a credentials.json file in the current or parent directory")
            sys.exit(1)
    
    # Create an AuthorizedSession
    authed_session = AuthorizedSession(credentials)
    
    # Create a custom Http object that uses requests
    def build_request(http, *args, **kwargs):
        new_http = authed_session
        return HttpRequest(new_http, *args, **kwargs)
    
    drive_service = build('drive', 'v3', credentials=credentials, requestBuilder=build_request)
    gc = gspread.authorize(credentials)
    
    return gc, drive_service

def get_quora_credentials():
    """Get Quora login credentials from environment variables or credentials file"""
    # First check environment variables
    quora_email = os.getenv('QUORA_EMAIL')
    quora_password = os.getenv('QUORA_PASSWORD')
    
    if quora_email and quora_password:
        print("Using Quora credentials from environment variables")
        return quora_email, quora_password
    
    # If not in environment, check for credentials in a file
    possible_credential_files = [
        args.credentials_file,
        '../' + args.credentials_file,
        os.path.join(os.path.expanduser('~'), args.credentials_file)
    ]
    
    for cred_file in possible_credential_files:
        if os.path.exists(cred_file):
            try:
                with open(cred_file, 'r') as f:
                    creds_data = json.load(f)
                
                # Try multiple possible formats
                if 'quora_login' in creds_data:
                    quora_email = creds_data['quora_login'].get('user_email')
                    quora_password = creds_data['quora_login'].get('user_password')
                elif 'user_email' in creds_data and 'user_password' in creds_data:
                    quora_email = creds_data.get('user_email')
                    quora_password = creds_data.get('user_password')
                
                if quora_email and quora_password:
                    print(f"Using Quora credentials from file: {cred_file}")
                    return quora_email, quora_password
                    
            except Exception as e:
                print(f"Error reading credentials file {cred_file}: {e}")
    
    print("Quora credentials not found. Continuing without login.")
    return None, None

def setup_webdriver(headless=False):
    """Set up and configure the Selenium WebDriver"""
    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless=new")
    
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--disable-popup-blocking")
    
    # Use a realistic user agent
    user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    chrome_options.add_argument(f"--user-agent={user_agent}")
    
    # Add language preference
    chrome_options.add_argument("--lang=en-US,en;q=0.9")
    
    # Disable automation flags
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    
    # Set page load timeout
    driver.set_page_load_timeout(60)
    
    # Execute CDP commands to remove navigator.webdriver flag
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        })
        """
    })
    
    return driver

def get_2fa_code_from_sheet(gc, spreadsheet_id, max_attempts=5, retry_interval=20):
    """
    Attempts to retrieve a 2FA code from a dedicated sheet in the Google Spreadsheet.
    Will check the sheet up to max_attempts times with retry_interval seconds between attempts.
    """
    print(f"\nLooking for 2FA code in spreadsheet...")
    
    try:
        # Open the spreadsheet
        spreadsheet = gc.open_by_key(spreadsheet_id)
        
        # Try to open the "Code" worksheet, create it if it doesn't exist
        try:
            code_sheet = spreadsheet.worksheet("Code")
            print("Found 'Code' sheet for 2FA code")
        except gspread.exceptions.WorksheetNotFound:
            # Create the sheet if it doesn't exist
            code_sheet = spreadsheet.add_worksheet(title="Code", rows="2", cols="2")
            code_sheet.update_cell(1, 1, "Enter 2FA code here when prompted")
            print("Created new 'Code' sheet for 2FA code")
        
        # Make up to max_attempts attempts to read the code
        for attempt in range(max_attempts):
            print(f"Attempt {attempt+1}/{max_attempts} to read 2FA code...")
            
            # Get the value from cell A1
            code_cell = code_sheet.acell("A1").value
            
            # Check if the cell contains what looks like a numeric code
            if code_cell and code_cell.strip().isdigit() and len(code_cell.strip()) >= 4:
                code = code_cell.strip()
                print(f"Found 2FA code: {code[:1]}{'*' * (len(code) - 2)}{code[-1:]}")
                
                # Clear the code from the sheet after reading it
                code_sheet.update_cell(1, 1, "Code used at " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                
                return code
            
            # If no valid code found, wait and retry
            if attempt < max_attempts - 1:
                print(f"No valid 2FA code found. Waiting {retry_interval} seconds before retrying...")
                time.sleep(retry_interval)
        
        print("Failed to get 2FA code after maximum attempts")
        return None
        
    except Exception as e:
        print(f"Error retrieving 2FA code from sheet: {e}")
        return None

def login_to_quora(driver, email, password, gc=None, spreadsheet_id=None):
    """Login to Quora with the provided credentials"""
    try:
        print("\nAttempting to login to Quora...")
        print(f"Using email: {email[:3]}{'*' * (len(email) - 6)}{email[-3:]}")
        
        # Navigate directly to login page instead of homepage
        driver.get("https://www.quora.com/login")
        time.sleep(5)  # Wait longer for login page to fully load
        
        # Check if already logged in
        if "quora.com/profile/" in driver.current_url:
            print("Already logged in to Quora.")
            return True
            
        # Enter email using multiple approaches
        email_entered = False
        email_selectors = [
            "//input[@id='email']",
            "//input[@type='email']",
            "//input[@name='email']",
            "//input[@placeholder='Email']"
        ]
        
        for selector in email_selectors:
            try:
                email_field = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                email_field.clear()
                email_field.send_keys(email)
                print(f"Entered email using selector: {selector}")
                email_entered = True
                break
            except Exception as e:
                if args.debug:
                    print(f"Could not find email field with selector {selector}: {e}")
        
        # Try JavaScript if regular methods failed
        if not email_entered:
            try:
                # Try to find email input via JavaScript
                js_email = """
                const inputs = document.querySelectorAll('input');
                for (let input of inputs) {
                    if (input.type === 'email' || 
                        input.id === 'email' || 
                        input.name === 'email' || 
                        input.placeholder === 'Email' ||
                        input.placeholder.includes('email')
                    ) {
                        input.value = arguments[0];
                        return true;
                    }
                }
                return false;
                """
                email_entered = driver.execute_script(js_email, email)
                if email_entered:
                    print("Entered email using JavaScript")
            except Exception as e:
                print(f"JavaScript email entry failed: {e}")
        
        if not email_entered:
            print("ERROR: Could not find or fill email field. Quora might have changed their login form.")
            return False
            
        # Short pause after entering email
        time.sleep(2)
        
        # Enter password using multiple approaches
        password_entered = False
        password_selectors = [
            "//input[@id='password']",
            "//input[@type='password']",
            "//input[@name='password']",
            "//input[@placeholder='Password']"
        ]
        
        for selector in password_selectors:
            try:
                password_field = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                password_field.clear()
                password_field.send_keys(password)
                print(f"Entered password using selector: {selector}")
                password_entered = True
                break
            except Exception as e:
                if args.debug:
                    print(f"Could not find password field with selector {selector}: {e}")
        
        # Try JavaScript if regular methods failed
        if not password_entered:
            try:
                # Try to find password input via JavaScript
                js_password = """
                const inputs = document.querySelectorAll('input');
                for (let input of inputs) {
                    if (input.type === 'password' || 
                        input.id === 'password' || 
                        input.name === 'password' || 
                        input.placeholder === 'Password' ||
                        input.placeholder.includes('password')
                    ) {
                        input.value = arguments[0];
                        return true;
                    }
                }
                return false;
                """
                password_entered = driver.execute_script(js_password, password)
                if password_entered:
                    print("Entered password using JavaScript")
            except Exception as e:
                print(f"JavaScript password entry failed: {e}")
        
        if not password_entered:
            print("ERROR: Could not find or fill password field. Quora might have changed their login form.")
            return False
            
        # Short pause after entering password
        time.sleep(2)
        
        # Submit form using multiple approaches
        form_submitted = False
        
        # Approach 1: Look for submit button with various selectors
        submit_selectors = [
            "//button[@type='submit']",
            "//button[contains(text(), 'Log In')]",
            "//button[contains(text(), 'Login')]",
            "//button[contains(@class, 'submit')]",
            "//input[@type='submit']",
            "//div[contains(@class, 'submit')]",
            "//div[contains(text(), 'Log in')][@role='button']"
        ]
        
        for selector in submit_selectors:
            try:
                submit_button = WebDriverWait(driver, 3).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                submit_button.click()
                print(f"Clicked submit button using selector: {selector}")
                form_submitted = True
                break
            except Exception as e:
                if args.debug:
                    print(f"Could not click submit with selector {selector}: {e}")
        
        # Approach 2: Try to submit form by pressing Enter in password field
        if not form_submitted:
            try:
                for selector in password_selectors:
                    try:
                        password_field = driver.find_element(By.XPATH, selector)
                        password_field.send_keys(Keys.RETURN)
                        print("Submitted form by pressing Enter in password field")
                        form_submitted = True
                        break
                    except:
                        continue
            except Exception as e:
                print(f"Enter key submission failed: {e}")
        
        # Approach 3: Try submitting via JavaScript
        if not form_submitted:
            try:
                js_scripts = [
                    # Try to submit the form
                    "document.querySelector('form').submit();",
                    # Try to click the first button in a form
                    "document.querySelector('form button').click();",
                    # Find any button that looks like a submit button 
                    """
                    const buttons = document.querySelectorAll('button');
                    for (let button of buttons) {
                        if (button.type === 'submit' || 
                            button.textContent.includes('Log') ||
                            button.textContent.includes('log') ||
                            button.className.includes('submit')) {
                            button.click();
                            return true;
                        }
                    }
                    return false;
                    """
                ]
                
                for script in js_scripts:
                    try:
                        result = driver.execute_script(script)
                        print(f"Executed JavaScript form submission, result: {result}")
                        form_submitted = True
                        break
                    except Exception as e:
                        if args.debug:
                            print(f"JavaScript submission attempt failed: {e}")
            except Exception as e:
                print(f"All JavaScript submission attempts failed: {e}")
        
        if not form_submitted:
            print("WARNING: Could not find or click submit button. Quora might have changed their login form.")
        
        # Wait longer for login to complete
        time.sleep(8)
        
        # Check for 2FA verification code request
        try:
            # Check if we're on a 2FA page - look for input fields for verification code
            verification_selectors = [
                "//input[contains(@placeholder, 'code')]",
                "//input[contains(@placeholder, 'verification')]",
                "//input[contains(@aria-label, 'code')]",
                "//div[contains(text(), 'verification code')]//following::input",
                "//div[contains(text(), 'code')]//following::input"
            ]
            
            needs_2fa = False
            verification_field = None
            
            for selector in verification_selectors:
                try:
                    verification_field = WebDriverWait(driver, 3).until(
                        EC.presence_of_element_located((By.XPATH, selector))
                    )
                    needs_2fa = True
                    print("Found 2FA verification code input field")
                    break
                except:
                    pass
            
            # Also check for common 2FA text indicators
            if not needs_2fa:
                two_fa_indicators = [
                    "verification code",
                    "security code",
                    "2-step verification",
                    "enter the code",
                    "code we sent",
                    "code below"
                ]
                page_text = driver.page_source.lower()
                
                for indicator in two_fa_indicators:
                    if indicator in page_text:
                        needs_2fa = True
                        print(f"Detected 2FA requirement based on text: '{indicator}'")
                        
                        # Try to find input field again with more general selector
                        try:
                            verification_field = WebDriverWait(driver, 3).until(
                                EC.presence_of_element_located((By.XPATH, "//input[not(@type='hidden') and not(@type='password') and not(@type='email')]"))
                            )
                            print("Found general input field for 2FA code")
                        except:
                            print("Could not find input field for 2FA code despite 2FA being required")
                        
                        break
            
            # If 2FA is needed, try to get the code from Google Sheet
            if needs_2fa and verification_field and gc and spreadsheet_id:
                print("\n2FA VERIFICATION REQUIRED")
                print("Please check your email for a verification code from Quora")
                print("Then enter the code in cell A1 of the 'Code' sheet in your Google Spreadsheet")
                
                # Try to get 2FA code from Google Sheet
                verification_code = get_2fa_code_from_sheet(gc, spreadsheet_id)
                
                if verification_code:
                    # Enter the verification code
                    verification_field.clear()
                    verification_field.send_keys(verification_code)
                    time.sleep(1)
                    
                    # Submit the verification code
                    try:
                        # First try to press Enter in the field
                        verification_field.send_keys(Keys.RETURN)
                        print("Submitted 2FA code using Enter key")
                    except:
                        # Try to find and click a verification button
                        verify_selectors = [
                            "//button[@type='submit']",
                            "//button[contains(text(), 'Verify')]",
                            "//button[contains(text(), 'Continue')]",
                            "//button[contains(text(), 'Submit')]"
                        ]
                        
                        for selector in verify_selectors:
                            try:
                                verify_button = WebDriverWait(driver, 3).until(
                                    EC.element_to_be_clickable((By.XPATH, selector))
                                )
                                verify_button.click()
                                print(f"Clicked verification button using selector: {selector}")
                                break
                            except:
                                continue
                    
                    # Wait for verification to process
                    time.sleep(5)
                else:
                    print("Failed to get 2FA code from Google Sheet")
                    return False
        except Exception as e:
            print(f"Error handling 2FA verification: {e}")
        
        # Add a longer wait after 2FA processing to ensure full login completes
        print("Waiting for full login session to initialize after 2FA...")
        time.sleep(10)
        
        # Check if login was successful
        login_successful = False
        
        # Method 1: Check URL
        if "quora.com/profile/" in driver.current_url:
            print("Login successful! (profile in URL)")
            login_successful = True
            
        # Perform a more definitive check - check if we can access user-specific features
        try:
            # 1. Try to access the user's profile directly
            driver.get("https://www.quora.com/profile")
            time.sleep(5)
            
            # Check if we're on a profile page (look for common profile elements)
            profile_elements = [
                "//div[contains(@class, 'q-text') and contains(text(), 'Profile')]",
                "//div[contains(@class, 'q-text') and contains(text(), 'Followers')]",
                "//div[contains(@class, 'q-text') and contains(text(), 'Following')]",
                "//div[contains(text(), 'Edit Profile')]"
            ]
            
            for selector in profile_elements:
                try:
                    element = driver.find_element(By.XPATH, selector)
                    print(f"Found profile element: {element.text}")
                    login_successful = True
                    break
                except:
                    continue
                    
            # 2. Check if we can access the notifications page (only for logged-in users)
            if not login_successful:
                driver.get("https://www.quora.com/notifications")
                time.sleep(3)
                
                if "quora.com/notifications" in driver.current_url:
                    print("Successfully accessed notifications page - login confirmed")
                    login_successful = True
                else:
                    print("Redirected away from notifications page - not fully logged in")
                
            # 3. Try to find user-specific UI elements
            try:
                user_elements = driver.find_elements(By.XPATH, "//div[contains(@aria-label, 'Your profile') or contains(@aria-label, 'Your content')]")
                if user_elements:
                    print(f"Found user-specific UI elements: {len(user_elements)}")
                    login_successful = True
            except:
                pass
        
        except Exception as e:
            print(f"Error during additional login validation: {e}")
        
        # Method 2: Check for avatar
        try:
            avatar = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'q-box') and contains(@class, 'qu-borderBottom')]//img"))
            )
            print("Login successful! (avatar found)")
            login_successful = True
        except:
            print("Could not find user avatar")
        
        # Method 3: Check for user menu
        try:
            user_menu = driver.find_element(By.XPATH, "//div[contains(@class, 'q-box') and contains(@class, 'qu-borderRadius--circle')]")
            print("Login successful! (user menu found)")
            login_successful = True
        except:
            print("Could not find user menu")
        
        # Method 4: Check if "Login" button is still present
        try:
            driver.find_element(By.XPATH, "//button[contains(text(), 'Login') or contains(text(), 'Log In')]")
            print("Login button still present - login failed")
            login_successful = False
        except:
            print("Login button no longer present - likely logged in")
            login_successful = True
        
        # Method 5: Check for login error messages
        try:
            error_message = driver.find_element(By.XPATH, "//div[contains(@class, 'error') or contains(text(), 'incorrect') or contains(text(), 'failed')]")
            error_text = error_message.text
            print(f"Login error message found: {error_text}")
            login_successful = False
        except:
            pass
            
        # Try an alternate login method if the standard approach failed
        if not login_successful:
            print("Standard login failed. Trying alternate login method...")
            try:
                # Go to a direct Quora login URL
                driver.get("https://www.quora.com/signup?redirect_url=https%3A%2F%2Fwww.quora.com")
                time.sleep(4)
                
                # Try to switch to email login
                try:
                    email_login_button = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, "//div[contains(text(), 'Continue with Email') or contains(text(), 'Email')]"))
                    )
                    email_login_button.click()
                    print("Clicked on 'Continue with Email' button")
                    time.sleep(2)
                except Exception as e:
                    print(f"No 'Continue with Email' button found, may already be on email form: {e}")
                
                # Find email field with a broader search
                try:
                    all_inputs = driver.find_elements(By.XPATH, "//input")
                    for input_field in all_inputs:
                        input_type = input_field.get_attribute("type")
                        placeholder = input_field.get_attribute("placeholder") or ""
                        if input_type == "email" or "email" in placeholder.lower():
                            input_field.clear()
                            input_field.send_keys(email)
                            print("Filled email field in alternate login")
                            break
                except Exception as e:
                    print(f"Failed to fill email in alternate login: {e}")
                
                # Find password field
                try:
                    all_inputs = driver.find_elements(By.XPATH, "//input")
                    for input_field in all_inputs:
                        input_type = input_field.get_attribute("type")
                        if input_type == "password":
                            input_field.clear()
                            input_field.send_keys(password)
                            input_field.send_keys(Keys.RETURN) # Try submitting with Enter key
                            print("Filled password field and pressed Enter in alternate login")
                            break
                except Exception as e:
                    print(f"Failed to fill password in alternate login: {e}")
                
                # Look for any submit button
                try:
                    buttons = driver.find_elements(By.XPATH, "//button")
                    for button in buttons:
                        button_text = button.text.lower()
                        if "log in" in button_text or "login" in button_text or "sign in" in button_text:
                            button.click()
                            print(f"Clicked '{button.text}' button in alternate login")
                            break
                except Exception as e:
                    print(f"Failed to click login button in alternate login: {e}")
                
                # Wait for login to complete
                time.sleep(8)
                
                # Check for 2FA again in the alternate login flow
                try:
                    # Check for 2FA fields with more direct indicators
                    verification_selectors = [
                        "//input[contains(@placeholder, 'code')]",
                        "//input[contains(@placeholder, 'verification')]"
                    ]
                    
                    needs_2fa = False
                    verification_field = None
                    
                    for selector in verification_selectors:
                        try:
                            verification_field = WebDriverWait(driver, 3).until(
                                EC.presence_of_element_located((By.XPATH, selector))
                            )
                            needs_2fa = True
                            print("Found 2FA verification code input field in alternate login")
                            break
                        except:
                            pass
                    
                    # If 2FA is needed, try to get the code from Google Sheet
                    if needs_2fa and verification_field and gc and spreadsheet_id:
                        print("\n2FA VERIFICATION REQUIRED in alternate login")
                        print("Please check your email for a verification code from Quora")
                        print("Then enter the code in cell A1 of the 'Code' sheet in your Google Spreadsheet")
                        
                        # Try to get 2FA code from Google Sheet
                        verification_code = get_2fa_code_from_sheet(gc, spreadsheet_id)
                        
                        if verification_code:
                            # Enter the verification code
                            verification_field.clear()
                            verification_field.send_keys(verification_code)
                            time.sleep(1)
                            
                            # Try to submit code
                            verification_field.send_keys(Keys.RETURN)
                            
                            # Wait for verification to process
                            time.sleep(5)
                    
                except Exception as e:
                    print(f"Error handling 2FA in alternate login flow: {e}")
                
                # Check if login succeeded
                if "quora.com/profile/" in driver.current_url:
                    login_successful = True
                    print("Alternate login successful!")
            except Exception as e:
                print(f"Alternate login attempt failed: {e}")
        
        # Test login by trying to access a generic Quora page if we think we're logged in
        if login_successful:
            print("Testing login by accessing a generic Quora page...")
            # Instead of a specific answer log page, let's visit the main profile page or home page
            driver.get("https://www.quora.com/")
            time.sleep(3)
            
            # Check if we got redirected to login
            if "login" in driver.current_url.lower():
                print("ERROR: Redirected to login page - login was not successful")
                login_successful = False
            else:
                print("Successfully loaded Quora main page - login confirmed working")
        
        return login_successful
        
    except Exception as e:
        print(f"Error during login: {e}")
        traceback.print_exc()
        
        return False

def get_urls_from_sheet(gc, spreadsheet_id, sheet_name, url_range, max_urls=None):
    """Get URLs from the specified Google Sheet, only those not processed yet"""
    try:
        print(f"Attempting to open spreadsheet with ID: {spreadsheet_id}")
        try:
            spreadsheet = gc.open_by_key(spreadsheet_id)
            print(f"Successfully opened spreadsheet: '{spreadsheet.title}'")
            
            # List available worksheets
            print("\nAvailable worksheets:")
            for worksheet in spreadsheet.worksheets():
                print(f"- {worksheet.title}")
            
            # Try to open the specified worksheet
            try:
                sheet = spreadsheet.worksheet(sheet_name)
                print(f"\nOpened worksheet: '{sheet_name}'")
                
                # Get URLs from specified range
                all_data = sheet.get_all_values()
                all_rows = len(all_data)
                print(f"Sheet has {all_rows} rows in total")
                
                # Ensure we're starting from row 2 to skip the header
                if url_range.startswith('A1:'):
                    url_range = 'A2:' + url_range.split(':')[1]
                    print(f"Adjusted URL range to skip header: {url_range}")
                    
                # Get URLs and processed status
                start_row = int(re.search(r'\d+', url_range.split(':')[0]).group())
                urls_data = sheet.get_values(url_range)
                processed_flags = sheet.get_values(f'P{start_row}:P{start_row + len(urls_data)}')
                
                print(f"Found {len(urls_data)} URLs in range {url_range}")
                
                # Extend processed_flags if needed
                if len(processed_flags) < len(urls_data):
                    processed_flags.extend([['FALSE']] * (len(urls_data) - len(processed_flags)))
                
                # Extract URLs and limit if needed
                processed_urls = []
                processed_count = 0
                unprocessed_count = 0
                
                for i, (url_row, flag_row) in enumerate(zip(urls_data, processed_flags), start=start_row):
                    if not url_row or not url_row[0].strip():
                        continue
                    
                    # Check if the URL has already been processed
                    processed = flag_row and flag_row[0].strip().upper() == 'TRUE'
                    
                    if processed:
                        processed_count += 1
                        if args.debug:
                            print(f"Skipping already processed URL at row {i}: {url_row[0]}")
                    else:
                        unprocessed_count += 1
                        if url_row and url_row[0].strip():
                            processed_urls.append({
                                'row': i,
                                'url': url_row[0].strip().replace("@", ""),
                            })
                            if max_urls and len(processed_urls) >= max_urls:
                                break
                
                print(f"Found {processed_count} already processed URLs and {unprocessed_count} unprocessed URLs")
                print(f"Returning {len(processed_urls)} URLs to process (limited by max_urls={max_urls})")
                return processed_urls, sheet, spreadsheet
            
            except gspread.exceptions.WorksheetNotFound:
                print(f"ERROR: Worksheet '{sheet_name}' not found in this spreadsheet.")
                print("Available worksheets:", [w.title for w in spreadsheet.worksheets()])
                return [], None, None
                
        except gspread.exceptions.SpreadsheetNotFound:
            print("ERROR: Spreadsheet not found or access denied.")
            print("Possible reasons:")
            print("1. The spreadsheet ID is incorrect")
            print("2. The service account email doesn't have access to this spreadsheet")
            print(f"   - Make sure to share the spreadsheet with the service account email")
            
            # Try alternate ID formats
            if spreadsheet_id.endswith('q'):
                alt_id = spreadsheet_id[:-1]
                print(f"\nTrying alternate ID without 'q': {alt_id}")
                try:
                    spreadsheet = gc.open_by_key(alt_id)
                    print(f"Success with alternate ID! Spreadsheet title: '{spreadsheet.title}'")
                    return get_urls_from_sheet(gc, alt_id, sheet_name, url_range, max_urls)
                except Exception as e:
                    print(f"Also failed with alternate ID: {e}")
            else:
                alt_id = spreadsheet_id + 'q'
                print(f"\nTrying alternate ID with 'q' appended: {alt_id}")
                try:
                    spreadsheet = gc.open_by_key(alt_id)
                    print(f"Success with alternate ID! Spreadsheet title: '{spreadsheet.title}'")
                    return get_urls_from_sheet(gc, alt_id, sheet_name, url_range, max_urls)
                except Exception as e:
                    print(f"Also failed with alternate ID: {e}")
            
            return [], None, None
    
    except Exception as e:
        print(f"Error accessing spreadsheet: {str(e)}")
        print(f"Traceback: {traceback.format_exc()}")
        return [], None, None

def is_quora_url(url):
    """Check if the URL is a Quora URL"""
    return "quora.com" in url

def process_quora_urls(quora_urls, driver, sheet):
    """Process a list of Quora URLs and update the spreadsheet with the results"""
    results = []
    
    for url_data in quora_urls:
        row = url_data['row']
        url = url_data['url']
        
        print(f"\nProcessing URL from row {row}: {url}")
        
        # Scrape the answer data
        result = parse_quora.scrape_quora_answer(driver, url)
        
        if "error" in result:
            print(f"Error processing URL: {result['error']}")
            continue
            
        # Update the spreadsheet with the scraped data
        try:
            # Column B - Base Thread URL
            sheet.update_cell(row, 2, result['base_url'])
            
            # Column G - Username (Author)
            sheet.update_cell(row, 7, result['author'])
            
            # Column H - Post Date
            sheet.update_cell(row, 8, result['post_date'])
            
            # Column I - Views
            sheet.update_cell(row, 9, result['stats']['views'])
            
            # Column J - Upvotes
            sheet.update_cell(row, 10, result['stats']['upvotes'])
            
            # Column K - Comments
            sheet.update_cell(row, 11, result['stats']['comments'])
            
            # Column L - Shares
            sheet.update_cell(row, 12, result['stats']['shares'])
            
            # Column M - Timestamp (when scraped)
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            sheet.update_cell(row, 13, timestamp)
            
            # Column P - Mark as processed
            sheet.update_cell(row, 16, "TRUE")
            
            print(f"Successfully updated sheet for row {row}")
            
            # Add delay to avoid rate limits
            time.sleep(2)
            
        except Exception as e:
            print(f"Error updating spreadsheet for row {row}: {e}")
            
        # Add result to the list
        results.append({
            "row": row,
            "data": result
        })
        
        # Add delay between requests to avoid being flagged as a bot
        delay = random.uniform(3, 7)
        print(f"Waiting {delay:.2f} seconds before next URL...")
        time.sleep(delay)
    
    return results

def main():
    """Main function to run the Quora parser"""
    try:
        # Check if --no-login flag was used (overrides --login)
        if args.no_login:
            print("No-login flag specified - will skip login")
            args.login = False
            
        # Setup Google API
        gc, drive_service = setup_google_api()
        
        # Check if we're testing a single URL
        if args.url:
            print(f"\nProcessing single URL mode: {args.url}")
            quora_urls = [{'row': 1, 'url': args.url}]
            sheet = None
        else:
            # Get URLs from sheet
            urls, sheet, spreadsheet = get_urls_from_sheet(
                gc, 
                args.spreadsheet_id, 
                args.sheet_name, 
                args.url_column,
                args.max_urls
            )
            
            if not urls:
                print("No unprocessed URLs found or could not access spreadsheet.")
                return
            
            print(f"\nSuccessfully retrieved {len(urls)} unprocessed URLs to process")
            
            # Filter for Quora URLs
            quora_urls = [url for url in urls if is_quora_url(url['url'])]
            non_quora_urls = [url for url in urls if not is_quora_url(url['url'])]
            
            print(f"Found {len(quora_urls)} Quora URLs and {len(non_quora_urls)} non-Quora URLs")
            
            if not quora_urls:
                print("No Quora URLs found to process.")
                return
            
        # Set up the WebDriver
        print("\nSetting up WebDriver...")
        driver = setup_webdriver(headless=args.headless)
        
        try:
            # Login to Quora if requested
            if args.login:
                quora_email, quora_password = get_quora_credentials()
                if quora_email and quora_password:
                    # Pass the Google Sheets client and spreadsheet ID for 2FA code retrieval
                    login_success = login_to_quora(driver, quora_email, quora_password, gc, args.spreadsheet_id)
                    
                    if not login_success:
                        print("\nWARNING: Failed to login to Quora. Some features may not work properly:")
                        print("  - Log pages (for exact post dates) might be inaccessible")
                        print("  - Upvote counts might be hidden behind 'View upvotes' buttons")
                        print("  - Some content might be partially shown or limited\n")
                else:
                    print("\nWARNING: Quora credentials not found but login is required for full functionality.")
                    print("To provide credentials, either:")
                    print("1. Create a credentials.json file with 'user_email' and 'user_password' fields")
                    print("2. Set QUORA_EMAIL and QUORA_PASSWORD environment variables")
                    print("\nWithout login, these limitations apply:")
                    print("  - Log pages (for exact post dates) will likely be inaccessible")
                    print("  - Upvote counts might be hidden behind 'View upvotes' buttons")
                    print("  - Some content might be partially shown or limited\n")
            
            # Process the Quora URLs
            print("\nProcessing Quora URLs...")
            
            # Special case for single URL mode (no spreadsheet updates)
            if args.url:
                for url_data in quora_urls:
                    url = url_data['url']
                    print(f"\nProcessing URL: {url}")
                    
                    # Scrape the answer data
                    result = parse_quora.scrape_quora_answer(driver, url)
                    
                    if "error" in result:
                        print(f"Error processing URL: {result['error']}")
                    else:
                        # Print the results directly
                        print("\nExtracted data:")
                        print(f"Answer URL: {result['answer_url']}")
                        print(f"Thread URL: {result['base_url']}")
                        print(f"Author: {result['author']}")
                        print(f"Post date: {result['post_date']}")
                        print(f"Views: {result['stats']['views']}")
                        print(f"Upvotes: {result['stats']['upvotes']}")
                        print(f"Comments: {result['stats']['comments']}")
                        print(f"Shares: {result['stats']['shares']}")
                        print(f"Scraped at: {result['scraped_at']}")
            else:
                # Normal mode - update spreadsheet
                results = process_quora_urls(quora_urls, driver, sheet)
                
                # Print summary
                print("\nProcessing complete! Summary:")
                for result in results:
                    row = result["row"]
                    data = result["data"]
                    print(f"Row {row}: {data['author']} - Views: {data['stats']['views']}, Upvotes: {data['stats']['upvotes']}, Comments: {data['stats']['comments']}, Shares: {data['stats']['shares']}")
                    print(f"  Thread URL: {data['base_url']}")
                    print(f"  Answer URL: {data['answer_url']}")
                    print(f"  Post date: {data['post_date']}")
                    print("---")
        
        finally:
            # Close the WebDriver
            print("\nClosing WebDriver...")
            driver.quit()
    
    except Exception as e:
        print(f"Error in main execution: {e}")
        print(traceback.format_exc())

if __name__ == "__main__":
    main()
