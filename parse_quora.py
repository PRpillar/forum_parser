import time
import re
import datetime
import json
from urllib.parse import urlparse, urlunsplit
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException

# Import args from main for debug flag
try:
    from main import args
except ImportError:
    # Create dummy args if not imported
    class Args:
        debug = False
    args = Args()

def extract_author_name(driver):
    """Extract the name of the person who posted the answer"""
    try:
        # Try multiple possible selectors for author name
        selectors = [
            "//div[contains(@class, 'q-box')]//a[contains(@class, 'qu-bold')]/span",
            "//div[contains(@class, 'q-box')]//span[contains(@class, 'qu-bold')]",
            "//span[contains(@class, 'qu-bold--done')]",
            "//div[contains(@class, 'qu-borderBottom')]//span[contains(@class, 'qu-bold')]"
        ]
        
        for selector in selectors:
            try:
                author_elements = driver.find_elements(By.XPATH, selector)
                for element in author_elements:
                    name = element.text.strip()
                    if name and len(name) > 1 and "answer" not in name.lower():
                        print(f"Found author name: {name}")
                        return name
            except:
                continue
                
        # Try getting from page URL as fallback
        current_url = driver.current_url
        if '/answer/' in current_url:
            username = current_url.split('/answer/')[1].split('/')[0].replace('-', ' ')
            print(f"Extracted author name from URL: {username}")
            return username
            
        return "Name not found"
        
    except Exception as e:
        print(f"Error extracting author name: {e}")
        return "Error extracting name"

def extract_post_date(driver, answer_url):
    """
    Extract the date when the post was created.
    Focuses on extracting the exact creation date from the log page.
    The earliest date in the log represents the original post date.
    """
    # Get current date information for fallback
    system_now = datetime.datetime.now()
    
    original_url = driver.current_url
    
    # Log URLs always end with /log or are direct log revision URLs
    if not answer_url.endswith("/log") and "/log/" not in answer_url:
        log_url = answer_url + "/log"
    else:
        log_url = answer_url
    
    print(f"Navigating to log page: {log_url}")
    driver.get(log_url)
    time.sleep(5)  # Wait for log page to load
    
    # Check if we hit a login wall or error page
    page_source = driver.page_source
    restricted_access = False
    
    # Check for access restriction indicators
    login_indicators = [
        "Something went wrong", 
        "You need to login to view this page", 
        "login" in driver.current_url.lower(),
        "Log in to Quora",
        "Page not found",
        "This content isn't available right now",
        "Error",
        "The page you requested was not found"
    ]
    
    for indicator in login_indicators:
        if isinstance(indicator, str) and indicator in page_source:
            restricted_access = True
            break
    
    if not restricted_access:
        try:
            # Look for dates via JavaScript
            date_spans_js = """
            // Find all spans with the specific classes that might contain dates
            const dateSpans = Array.from(document.querySelectorAll('span.c1h7helg.c8970ew'));
            
            // Add a fallback for other common date classes
            if (dateSpans.length === 0) {
                // Try alternative date selectors (common in Quora)
                const alternativeDateSpans = Array.from(document.querySelectorAll('span.q-text.qu-dynamicFontSize--small, span.q-text.qu-color--gray_light, span[class*="qu-color--gray"]'));
                dateSpans.push(...alternativeDateSpans);
            }
            
            // If still no date spans, try a more general approach to find spans with date-like content
            if (dateSpans.length === 0) {
                const allSpans = Array.from(document.querySelectorAll('span'));
                const possibleDateSpans = allSpans.filter(span => {
                    const text = span.textContent.trim();
                    // Look for patterns like "January 1, 2023" or "Jan 1, 2023"
                    return (
                        /^(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\\s+\\d{1,2},\\s+\\d{4}/.test(text) ||
                        /\\d{1,2}\\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|January|February|March|April|May|June|July|August|September|October|November|December)\\s+\\d{4}/.test(text)
                    );
                });
                dateSpans.push(...possibleDateSpans);
            }
            
            // Map them to their text content
            return dateSpans.map(span => span.textContent.trim());
            """
            
            date_spans = driver.execute_script(date_spans_js)
            
            if date_spans and len(date_spans) > 0:
                print(f"Found {len(date_spans)} potential date spans:")
                
                # Process all spans, focusing on finding the earliest date
                all_found_dates = []
                
                for i, span_text in enumerate(date_spans):
                    print(f"  Span #{i+1}: '{span_text}'")
                    
                    # Look for dates with time pattern (most specific)
                    time_date_match = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2}), (\d{4}) at (\d{1,2}):(\d{2})(?::(\d{2}))? ([APM]{2})', span_text)
                    
                    if time_date_match:
                        print(f"  Found date with time in span #{i+1}")
                        formatted_date = span_text
                        
                        # Store this date for comparison
                        try:
                            # Parse the date to allow finding the earliest one
                            month = time_date_match.group(1)
                            day = int(time_date_match.group(2))
                            year = int(time_date_match.group(3))
                            hour = int(time_date_match.group(4))
                            minute = int(time_date_match.group(5))
                            
                            # Adjust for AM/PM
                            if time_date_match.group(7) == "PM" and hour < 12:
                                hour += 12
                            elif time_date_match.group(7) == "AM" and hour == 12:
                                hour = 0
                                
                            # Create a datetime object for comparison
                            if time_date_match.group(6):  # If seconds are included
                                second = int(time_date_match.group(6))
                            else:
                                second = 0
                            
                            # Convert month name to month number
                            month_map = {
                                'January': 1, 'February': 2, 'March': 3, 'April': 4, 'May': 5, 'June': 6,
                                'July': 7, 'August': 8, 'September': 9, 'October': 10, 'November': 11, 'December': 12,
                                'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
                                'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
                            }
                            month_num = month_map.get(month, 1)  # Default to 1 if not found
                            
                            date_obj = datetime.datetime(year, month_num, day, hour, minute, second)
                            all_found_dates.append((date_obj, formatted_date))
                        except Exception as e:
                            print(f"    Error parsing date for comparison: {e}")
                    
                    # Try simpler date pattern if full pattern didn't match
                    date_match = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2}), (\d{4})', span_text)
                    if date_match and not time_date_match:
                        print(f"  Found simple date in span #{i+1}")
                        formatted_date = f"{date_match.group(1)} {date_match.group(2)}, {date_match.group(3)}"
                        
                        # Store this date for comparison
                        try:
                            # Parse the date to allow finding the earliest one
                            month = date_match.group(1)
                            day = int(date_match.group(2))
                            year = int(date_match.group(3))
                            
                            # Convert month name to month number
                            month_map = {
                                'January': 1, 'February': 2, 'March': 3, 'April': 4, 'May': 5, 'June': 6,
                                'July': 7, 'August': 8, 'September': 9, 'October': 10, 'November': 11, 'December': 12,
                                'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
                                'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
                            }
                            month_num = month_map.get(month, 1)  # Default to 1 if not found
                            
                            # Use midnight as time
                            date_obj = datetime.datetime(year, month_num, day, 0, 0, 0)
                            all_found_dates.append((date_obj, formatted_date))
                        except Exception as e:
                            print(f"    Error parsing date for comparison: {e}")
                    
                    # Try alternative date formats (e.g., "1 Jan 2023")
                    alt_date_match = re.search(r'(\d{1,2}) (January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec),? (\d{4})', span_text)
                    if alt_date_match and not time_date_match and not date_match:
                        print(f"  Found alternative date format in span #{i+1}")
                        formatted_date = f"{alt_date_match.group(2)} {alt_date_match.group(1)}, {alt_date_match.group(3)}"
                        
                        # Store this date for comparison
                        try:
                            # Parse the date to allow finding the earliest one
                            day = int(alt_date_match.group(1))
                            month = alt_date_match.group(2)
                            year = int(alt_date_match.group(3))
                            
                            # Convert month name to month number
                            month_map = {
                                'January': 1, 'February': 2, 'March': 3, 'April': 4, 'May': 5, 'June': 6,
                                'July': 7, 'August': 8, 'September': 9, 'October': 10, 'November': 11, 'December': 12,
                                'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
                                'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
                            }
                            month_num = month_map.get(month, 1)  # Default to 1 if not found
                            
                            # Use midnight as time
                            date_obj = datetime.datetime(year, month_num, day, 0, 0, 0)
                            all_found_dates.append((date_obj, formatted_date))
                        except Exception as e:
                            print(f"    Error parsing date for comparison: {e}")
                
                # After collecting all dates, find the earliest one
                if all_found_dates:
                    # Sort dates by datetime object (first element in each tuple)
                    all_found_dates.sort(key=lambda x: x[0])
                    
                    # Get the earliest date (first element after sorting)
                    earliest_date_tuple = all_found_dates[0]
                    earliest_formatted_date = earliest_date_tuple[1]
                    
                    print(f"  Found the earliest date: {earliest_formatted_date}")
                    
                    # Return to original URL before returning the date
                    driver.get(original_url)
                    time.sleep(3)
                    return earliest_formatted_date
                
                # If we couldn't parse any dates for sorting, try another approach
                # Try to identify a potential creation/posted event
                try:
                    # Use JavaScript to find specific text that might indicate the original posting
                    find_creation_js = """
                    const allDivs = document.querySelectorAll('div');
                    for (const div of allDivs) {
                        const text = div.textContent.toLowerCase();
                        if (text.includes('posted') || text.includes('created') || text.includes('wrote') || 
                            text.includes('answered') || text.includes('original answer') ||
                            text.includes('first posted') || text.includes('initially answered')) {
                            
                            // Get the closest element containing a date
                            let currentEl = div;
                            let dateText = '';
                            
                            // Look in the element itself
                            if (/(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\\s+\\d{1,2},\\s+\\d{4}/.test(text)) {
                                dateText = text;
                            } 
                            // Look for a date in nearby elements
                            else {
                                const nearbyElements = [];
                                // Check siblings
                                if (currentEl.previousElementSibling) nearbyElements.push(currentEl.previousElementSibling);
                                if (currentEl.nextElementSibling) nearbyElements.push(currentEl.nextElementSibling);
                                // Check children
                                for (const child of currentEl.children) {
                                    nearbyElements.push(child);
                                }
                                
                                for (const el of nearbyElements) {
                                    const elText = el.textContent;
                                    if (/(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\\s+\\d{1,2},\\s+\\d{4}/.test(elText)) {
                                        dateText = elText;
                                        break;
                                    }
                                }
                            }
                            
                            if (dateText) {
                                return { element: div.textContent, dateText: dateText };
                            }
                        }
                    }
                    return null;
                    """
                    
                    creation_info = driver.execute_script(find_creation_js)
                    if creation_info:
                        print(f"Found creation indication: {creation_info['element']}")
                        print(f"Date text: {creation_info['dateText']}")
                        
                        # Extract the date from the text
                        date_match = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2}), (\d{4})(?: at (\d{1,2}):(\d{2})(?::(\d{2}))? ([APM]{2}))?', creation_info['dateText'])
                        
                        if date_match:
                            if date_match.group(4):  # If time is included
                                formatted_date = f"{date_match.group(1)} {date_match.group(2)}, {date_match.group(3)} at {date_match.group(4)}:{date_match.group(5)}"
                                if date_match.group(6):
                                    formatted_date += f":{date_match.group(6)}"
                                formatted_date += f" {date_match.group(7)}"
                            else:
                                formatted_date = f"{date_match.group(1)} {date_match.group(2)}, {date_match.group(3)}"
                                
                            print(f"  Successfully found post creation date: {formatted_date}")
                            
                            # Return to original URL before returning the date
                            driver.get(original_url)
                            time.sleep(3)
                            return formatted_date
                except Exception as e:
                    print(f"Error looking for creation indication: {e}")
        except Exception as e:
            print(f"Error extracting date from log page: {e}")
    
    # If no date found in spans, try page source for the earliest date
    try:
        print("Trying to extract date from page source...")
        all_matches = []
        
        # Look for date patterns in the page source (more comprehensive)
        date_patterns = [
            # With time: January 1, 2023 at 12:00 PM (most specific)
            r'(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2}), (\d{4}) at (\d{1,2}):(\d{2})(?::(\d{2}))? ([APM]{2})',
            # Standard format: January 1, 2023
            r'(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{1,2}), (\d{4})',
            # Alternative format: 1 January 2023
            r'(\d{1,2}) (January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec),? (\d{4})'
        ]
        
        # Find all dates in the page
        all_date_objects = []

        for pattern in date_patterns:
            matches = re.findall(pattern, page_source)
            for match in matches:
                try:
                    if len(match) >= 7:  # Full date with time format
                        month = match[0]
                        day = int(match[1])
                        year = int(match[2])
                        hour = int(match[3])
                        minute = int(match[4])
                        
                        # Adjust for AM/PM
                        if match[6] == "PM" and hour < 12:
                            hour += 12
                        elif match[6] == "AM" and hour == 12:
                            hour = 0
                            
                        # Create a datetime object for comparison
                        if match[5]:  # If seconds are included
                            second = int(match[5])
                        else:
                            second = 0
                        
                        # Format the date string
                        formatted_date = f"{month} {day}, {year} at {match[3]}:{match[4]}"
                        if match[5]:
                            formatted_date += f":{match[5]}"
                        formatted_date += f" {match[6]}"
                        
                    elif len(match) >= 3:  # Simple date format
                        if re.match(r'\d+', match[0]):  # If first group is a number (alternative format)
                            day = int(match[0])
                            month = match[1]
                            year = int(match[2])
                            formatted_date = f"{month} {day}, {year}"
                        else:  # Standard format
                            month = match[0]
                            day = int(match[1])
                            year = int(match[2])
                            formatted_date = f"{month} {day}, {year}"
                        
                        hour, minute, second = 0, 0, 0  # Default to midnight
                    
                    # Convert month name to month number
                    month_map = {
                        'January': 1, 'February': 2, 'March': 3, 'April': 4, 'May': 5, 'June': 6,
                        'July': 7, 'August': 8, 'September': 9, 'October': 10, 'November': 11, 'December': 12,
                        'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
                        'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12
                    }
                    
                    if isinstance(month, str):
                        month_num = month_map.get(month, 1)  # Default to 1 if not found
                    else:
                        month_num = month
                    
                    date_obj = datetime.datetime(year, month_num, day, hour, minute, second)
                    all_date_objects.append((date_obj, formatted_date))
                    
                except Exception as e:
                    print(f"    Error processing date match: {e}")
        
        # Find the earliest date
        if all_date_objects:
            # Sort by datetime
            all_date_objects.sort(key=lambda x: x[0])
            earliest_date = all_date_objects[0][1]
            print(f"Successfully found earliest date from page source: {earliest_date}")
            
            # Return to original URL before returning the date
            driver.get(original_url)
            time.sleep(3)
            return earliest_date
        else:
            print("No usable date matches found in page source")
    except Exception as e:
        print(f"Error extracting date from page source: {e}")
        import traceback
        traceback.print_exc()
    
    # Navigate back to the original answer page
    print(f"Navigating back to original URL: {original_url}")
    driver.get(original_url)
    time.sleep(3)
    
    # If all methods fail, use current date with a prefix
    current_date = system_now.strftime("%B %d, %Y")
    print(f"Could not find date - using current date as fallback: {current_date}")
    return f"Approx. {current_date}"

def extract_view_count(driver):
    """Extract the number of views from the answer page"""
    try:
        # Try the specific XPath provided
        view_xpath = "//div[contains(@class, 'qu-color--gray_light')]//span[contains(@class, 'c1h7helg')][1]"
        try:
            view_element = driver.find_element(By.XPATH, view_xpath)
            views_text = view_element.text.strip()
            
            if "views" in views_text.lower():
                # Extract numeric part from "158 views"
                views_match = re.search(r'(\d+(?:,\d+)*(?:\.\d+)?(?:[KkMm])?)\s+views?', views_text)
                if views_match:
                    view_count = views_match.group(1)
                    print(f"Found view count: {view_count}")
                    
                    # Convert K/M notation to full numbers
                    if 'k' in view_count.lower():
                        numeric_views = float(view_count.lower().replace('k', '')) * 1000
                        return str(int(numeric_views))
                    elif 'm' in view_count.lower():
                        numeric_views = float(view_count.lower().replace('m', '')) * 1000000
                        return str(int(numeric_views))
                    else:
                        return view_count.replace(',', '')
        except:
            pass
            
        # Try other selectors if the specific one fails
        view_selectors = [
            "//span[contains(text(), 'views')]",
            "//div[contains(text(), 'views')]",
            "//span[contains(@class, 'c1h7helg')]"
        ]
        
        for selector in view_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for element in elements:
                    text = element.text.strip().lower()
                    if "views" in text:
                        views_match = re.search(r'(\d+(?:,\d+)*(?:\.\d+)?(?:[KkMm])?)\s+views?', text)
                        if views_match:
                            view_count = views_match.group(1)
                            print(f"Found view count (alternative): {view_count}")
                            
                            # Convert K/M notation to full numbers
                            if 'k' in view_count.lower():
                                numeric_views = float(view_count.lower().replace('k', '')) * 1000
                                return str(int(numeric_views))
                            elif 'm' in view_count.lower():
                                numeric_views = float(view_count.lower().replace('m', '')) * 1000000
                                return str(int(numeric_views))
                            else:
                                return view_count.replace(',', '')
            except:
                continue
                
        # Try JavaScript if all else fails
        views_js = """
        return Array.from(document.querySelectorAll('*'))
            .filter(el => el.textContent && el.textContent.includes('views'))
            .map(el => el.textContent.trim())
            .find(text => /\\d+\\s+views/.test(text));
        """
        views_text = driver.execute_script(views_js)
        if views_text:
            views_match = re.search(r'(\d+(?:,\d+)*(?:\.\d+)?(?:[KkMm])?)\s+views?', views_text)
            if views_match:
                view_count = views_match.group(1)
                print(f"Found view count (JavaScript): {view_count}")
                
                # Convert K/M notation to full numbers
                if 'k' in view_count.lower():
                    numeric_views = float(view_count.lower().replace('k', '')) * 1000
                    return str(int(numeric_views))
                elif 'm' in view_count.lower():
                    numeric_views = float(view_count.lower().replace('m', '')) * 1000000
                    return str(int(numeric_views))
                else:
                    return view_count.replace(',', '')
        
        return "0"
        
    except Exception as e:
        print(f"Error extracting view count: {e}")
        return "0"

def extract_upvote_count(driver):
    """Extract the number of upvotes from the answer page"""
    try:
        # Try the specific XPath provided by the user
        try:
            # XPath to the upvote number in the button
            upvote_xpath = "/html/body/div[2]/div/div[2]/div/div[3]/div/div/div/div[1]/div[1]/div[6]/div/div/div/div[1]/div[1]/div/div/div/button/div[2]/div/span/span[4]/div/span[2]"
            upvote_element = driver.find_element(By.XPATH, upvote_xpath)
            upvote_count = upvote_element.text.strip()
            # Make sure it looks like a valid count (digits only)
            if upvote_count and upvote_count.isdigit() and len(upvote_count) < 10:  # Avoid large ID numbers
                print(f"Found upvote count from specific XPath: {upvote_count}")
                return upvote_count
        except:
            print("Could not find upvote count using specific XPath")
            
        # Add a specific JavaScript finder based on the HTML structure provided in the example
        try:
            js_specific_finder = """
            // Find upvote buttons by aria-label
            let upvoteButtons = Array.from(document.querySelectorAll('button[aria-label*="Upvote" i]'));
            
            for (let button of upvoteButtons) {
                // Look for spans that are visible and contain only digits
                const visibleSpans = Array.from(button.querySelectorAll('span'))
                    .filter(span => {
                        // Only get spans with numeric text that are visible
                        const text = span.textContent.trim();
                        return text.match(/^\\d+$/) && 
                               text.length < 10 &&  // Avoid large ID numbers
                               getComputedStyle(span).opacity !== '0' && 
                               getComputedStyle(span).display !== 'none' &&
                               getComputedStyle(span).visibility !== 'hidden';
                    });
                
                if (visibleSpans.length > 0) {
                    return visibleSpans[0].textContent.trim();
                }
                
                // Also try to find the span with the exact structure from the example
                const specificSpans = Array.from(button.querySelectorAll('.q-text.qu-whiteSpace--nowrap.qu-display--inline-flex.qu-alignItems--center.qu-justifyContent--center'))
                    .filter(span => {
                        const text = span.textContent.trim();
                        return text.match(/^\\d+$/) && text.length < 10;  // Avoid large ID numbers
                    });
                    
                if (specificSpans.length > 0) {
                    return specificSpans[0].textContent.trim();
                }
            }
            
            return null;
            """
            specific_count = driver.execute_script(js_specific_finder)
            if specific_count:
                print(f"Found upvote count via specific HTML structure: {specific_count}")
                return specific_count
        except Exception as e:
            print(f"Specific HTML structure extraction failed: {e}")
            
        # Try a more general approach with JavaScript to find upvote numbers in buttons
        try:
            js_upvote_finder = """
            // Function to check if an element or its parent contains 'Upvote' text
            function hasUpvoteText(el) {
                if (!el) return false;
                if (el.textContent.includes('Upvote')) return true;
                if (el.parentElement && el.parentElement.textContent.includes('Upvote')) return true;
                return false;
            }
            
            // Find all visible spans with just numbers that are near 'Upvote' text
            const allSpans = Array.from(document.querySelectorAll('span'));
            const upvoteSpans = allSpans.filter(span => {
                const text = span.textContent.trim();
                return text.match(/^\\d+$/) && 
                       text.length < 10 &&  // Avoid large ID numbers
                       getComputedStyle(span).opacity !== '0' &&
                       hasUpvoteText(span.parentElement);
            });
            
            if (upvoteSpans.length > 0) {
                return upvoteSpans[0].textContent.trim();
            }
            
            return null;
            """
            upvote_count = driver.execute_script(js_upvote_finder)
            if upvote_count:
                print(f"Found upvote count via general JavaScript: {upvote_count}")
                return upvote_count
        except Exception as e:
            print(f"General JavaScript upvote extraction failed: {e}")
        
        # Check for upvote count in button text directly
        try:
            upvote_buttons = driver.find_elements(By.XPATH, "//button[contains(@aria-label, 'Upvote')]")
            for button in upvote_buttons:
                button_text = button.text.strip()
                # Extract all digits from button text
                digits = ''.join(c for c in button_text if c.isdigit())
                if digits and len(digits) < 10:  # Avoid large ID numbers
                    print(f"Extracted upvote count from button text: {digits}")
                    return digits
        except:
            pass
        
        # Check for "View X upvotes" text
        upvote_selectors = [
            "//span[contains(text(), 'upvotes')]",
            "//div[contains(text(), 'upvotes')]",
            "//div[contains(@class, 'qu-color--gray_light')]//span[contains(@class, 'c1h7helg')][contains(text(), 'upvotes')]"
        ]
        
        for selector in upvote_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for element in elements:
                    text = element.text.strip().lower()
                    if "upvote" in text:
                        upvotes_match = re.search(r'(?:view\s+)?(\d+(?:,\d+)*(?:\.\d+)?(?:[KkMm])?)\s+upvotes?', text, re.IGNORECASE)
                        if upvotes_match:
                            upvote_count = upvotes_match.group(1)
                            print(f"Found upvote count from text: {upvote_count}")
                            
                            # Convert K/M notation to full numbers
                            if 'k' in upvote_count.lower():
                                numeric_upvotes = float(upvote_count.lower().replace('k', '')) * 1000
                                return str(int(numeric_upvotes))
                            elif 'm' in upvote_count.lower():
                                numeric_upvotes = float(upvote_count.lower().replace('m', '')) * 1000000
                                return str(int(numeric_upvotes))
                            else:
                                return upvote_count.replace(',', '')
            except:
                continue
        
        # Return 0 for any case where we can't find a valid upvote count
        return "0"
        
    except Exception as e:
        print(f"Error extracting upvote count: {e}")
        return "0"

def extract_comment_count(driver):
    """Extract the number of comments from the answer page"""
    try:
        # Try the specific XPath provided by the user
        try:
            # XPath to the comment number in the button
            comment_xpath = "/html/body/div[2]/div/div[2]/div/div[3]/div/div/div/div[1]/div[1]/div[6]/div/div/div/div[1]/div[2]/div/div/div/button/div/div[2]/span[2]"
            comment_element = driver.find_element(By.XPATH, comment_xpath)
            comment_count = comment_element.text.strip()
            if comment_count and comment_count.isdigit():
                print(f"Found comment count from specific XPath: {comment_count}")
                return comment_count
        except:
            print("Could not find comment count using specific XPath")
        
        # Add a specific JavaScript finder based on the HTML structure provided in the example
        try:
            js_specific_finder = """
            // Find comment buttons by aria-label
            let commentButtons = Array.from(document.querySelectorAll('button[aria-label*="comment" i]'));
            
            for (let button of commentButtons) {
                // Look for the structure matching the example: 
                // The visible span inside the second div of the button content
                const visibleSpans = Array.from(button.querySelectorAll('div > div:nth-child(2) > span:not([class*="visibility--hidden"])'))
                    .filter(span => span.textContent.trim().match(/^\\d+$/));
                
                if (visibleSpans.length > 0) {
                    return visibleSpans[0].textContent.trim();
                }
            }
            
            return null;
            """
            specific_count = driver.execute_script(js_specific_finder)
            if specific_count:
                print(f"Found comment count via specific HTML structure: {specific_count}")
                return specific_count
        except Exception as e:
            print(f"Specific HTML structure extraction failed: {e}")
        
        # Try more general selectors for the comment number inside comment buttons
        try:
            # Look for the second span in a div inside a comment button which contains the number
            comment_spans = driver.find_elements(By.XPATH, "//button[contains(@aria-label, 'comment') or contains(@aria-label, 'Comment')]//div[contains(@class, 'q-text')]//span[contains(@class, 'q-text') and not(contains(@class, 'qu-visibility--hidden'))]")
            for span in comment_spans:
                text = span.text.strip()
                if text and text.isdigit():
                    print(f"Found comment count from button span: {text}")
                    return text
        except:
            print("Could not find comment count in button spans")
        
        # Check for comment count in button text directly
        try:
            comment_buttons = driver.find_elements(By.XPATH, "//button[contains(@aria-label, 'Comment') or contains(@aria-label, 'comment')]")
            for button in comment_buttons:
                button_text = button.text.strip()
                if button_text and button_text.isdigit():
                    print(f"Found comment count from button text: {button_text}")
                    return button_text
                
                # Try to extract just the digits if there's other text
                digits = ''.join(c for c in button_text if c.isdigit())
                if digits:
                    print(f"Extracted digits from button text: {digits}")
                    return digits
        except:
            print("Could not find comment count in button text")
        
        # Try using JavaScript to find the comment count inside the button
        try:
            js_comment_finder = """
            // Find comment buttons by aria-label
            let commentButtons = Array.from(document.querySelectorAll('button[aria-label*="comment" i], button[aria-label*="Comment" i]'));
            
            for (let button of commentButtons) {
                // Find spans with text content that is just a number
                let spans = Array.from(button.querySelectorAll('span'));
                for (let span of spans) {
                    let text = span.textContent.trim();
                    if (text && /^\\d+$/.test(text) && getComputedStyle(span).opacity !== '0') {
                        return text;
                    }
                }
                
                // Also check for number in the button text
                let buttonText = button.textContent.trim();
                let match = buttonText.match(/\\d+/);
                if (match) {
                    return match[0];
                }
            }
            
            return null;
            """
            comment_count = driver.execute_script(js_comment_finder)
            if comment_count:
                print(f"Found comment count via JavaScript: {comment_count}")
                return comment_count
        except Exception as e:
            print(f"JavaScript comment extraction failed: {e}")
        
        # Try general selectors for text containing "comments"
        comment_selectors = [
            "//span[contains(text(), 'comments')]",
            "//div[contains(text(), 'comments')]"
        ]
        
        for selector in comment_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for element in elements:
                    text = element.text.strip().lower()
                    if "comment" in text:
                        comments_match = re.search(r'(\d+(?:,\d+)*(?:\.\d+)?(?:[KkMm])?)\s+comments?', text, re.IGNORECASE)
                        if comments_match:
                            comment_count = comments_match.group(1)
                            print(f"Found comment count from text: {comment_count}")
                            
                            # Convert K/M notation to full numbers
                            if 'k' in comment_count.lower():
                                numeric_comments = float(comment_count.lower().replace('k', '')) * 1000
                                return str(int(numeric_comments))
                            elif 'm' in comment_count.lower():
                                numeric_comments = float(comment_count.lower().replace('m', '')) * 1000000
                                return str(int(numeric_comments))
                            else:
                                return comment_count.replace(',', '')
            except:
                continue
        
        # Default to 0 if no comment count found
        return "0"
        
    except Exception as e:
        print(f"Error extracting comment count: {e}")
        return "0"

def extract_share_count(driver):
    """Extract the number of shares from the answer page"""
    try:
        # Check for "View X shares" text
        share_selectors = [
            "//span[contains(text(), 'shares')]", 
            "//div[contains(text(), 'shares')]",
            "//div[contains(@class, 'qu-color--gray_light')]//span[contains(@class, 'c1h7helg')][contains(text(), 'shares')]"
        ]
        
        for selector in share_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for element in elements:
                    text = element.text.strip().lower()
                    if "shares" in text:
                        shares_match = re.search(r'(?:view\s+)?(\d+(?:,\d+)*(?:\.\d+)?(?:[KkMm])?)\s+shares?', text, re.IGNORECASE)
                        if shares_match:
                            share_count = shares_match.group(1)
                            print(f"Found share count: {share_count}")
                            
                            # Convert K/M notation to full numbers
                            if 'k' in share_count.lower():
                                numeric_shares = float(share_count.lower().replace('k', '')) * 1000
                                return str(int(numeric_shares))
                            elif 'm' in share_count.lower():
                                numeric_shares = float(share_count.lower().replace('m', '')) * 1000000
                                return str(int(numeric_shares))
                            else:
                                return share_count.replace(',', '')
            except:
                continue
                
        # Try to find share button with count
        try:
            share_buttons = driver.find_elements(By.XPATH, "//button[contains(@aria-label, 'Share')]")
            for button in share_buttons:
                button_text = button.text.strip()
                if button_text and button_text.isdigit():
                    print(f"Found share count from button: {button_text}")
                    return button_text
        except:
            pass
            
        # Try JavaScript if all else fails
        shares_js = """
        return Array.from(document.querySelectorAll('*'))
            .filter(el => el.textContent && el.textContent.includes('share'))
            .map(el => el.textContent.trim())
            .find(text => /\\d+\\s+share/.test(text));
        """
        shares_text = driver.execute_script(shares_js)
        if shares_text:
            shares_match = re.search(r'(\d+(?:,\d+)*(?:\.\d+)?(?:[KkMm])?)\s+shares?', shares_text, re.IGNORECASE)
            if shares_match:
                share_count = shares_match.group(1)
                print(f"Found share count (JavaScript): {share_count}")
                
                # Convert K/M notation to full numbers
                if 'k' in share_count.lower():
                    numeric_shares = float(share_count.lower().replace('k', '')) * 1000
                    return str(int(numeric_shares))
                elif 'm' in share_count.lower():
                    numeric_shares = float(share_count.lower().replace('m', '')) * 1000000
                    return str(int(numeric_shares))
                else:
                    return share_count.replace(',', '')
        
        # Default to 0 if no share count found
        return "0"
        
    except Exception as e:
        print(f"Error extracting share count: {e}")
        return "0"

def extract_base_url(answer_url):
    """
    Extract the base thread URL from a Quora answer URL
    Example: 
    https://www.quora.com/Is-trading-on-OctaFX-halal/answer/Rifat-Sheih 
    becomes 
    https://www.quora.com/Is-trading-on-OctaFX-halal
    """
    try:
        # Parse the URL into components
        parsed_url = urlparse(answer_url)
        
        # Check if this is a Quora answer URL
        path = parsed_url.path
        if '/answer/' in path:
            # Split the path at /answer/ and take the first part
            base_path = path.split('/answer/')[0]
            
            # Reconstruct the URL with the base path
            base_url = urlunsplit((
                parsed_url.scheme,
                parsed_url.netloc,
                base_path,
                parsed_url.query,
                parsed_url.fragment
            ))
            
            print(f"Extracted base URL: {base_url}")
            return base_url
        else:
            # If it's not an answer URL, return the original
            print(f"Not an answer URL, returning original: {answer_url}")
            return answer_url
            
    except Exception as e:
        print(f"Error extracting base URL: {e}")
        return answer_url

def scrape_quora_answer(driver, url):
    """Scrape data from a specific Quora answer URL"""
    try:
        print(f"\nNavigating to: {url}")
        driver.get(url)
        
        # Wait for the page to load
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        
        # Add a small delay to ensure dynamic content loads
        time.sleep(3)
        
        print(f"Current URL: {driver.current_url}")
        print(f"Page title: {driver.title}")
        
        # Extract the base URL for the thread
        base_url = extract_base_url(url)
        
        # Check if the answer is deleted
        is_deleted = False
        try:
            deletion_indicators = [
                "//div[contains(text(), 'Quora deleted this answer')]",
                "//div[contains(text(), 'deleted by Quora Moderation')]",
                "//div[contains(text(), 'Quora deleted this')]"
            ]
            
            for indicator in deletion_indicators:
                deletion_elements = driver.find_elements(By.XPATH, indicator)
                if deletion_elements:
                    print("Found deletion notice: Answer has been deleted")
                    is_deleted = True
                    break
        except Exception as e:
            print(f"Error checking for deletion status: {e}")
        
        # Extract stats - handle differently based on deletion status
        if is_deleted:
            author_name = "POST DELETED"
            view_count = "0"
            upvote_count = "0"
            comment_count = "0"
            share_count = "0"
            # Still try to extract the post date
            post_date = extract_post_date(driver, url)
        else:
            # Normal extraction for non-deleted posts
            author_name = extract_author_name(driver)
            post_date = extract_post_date(driver, url)
            view_count = extract_view_count(driver)
            upvote_count = extract_upvote_count(driver)
            comment_count = extract_comment_count(driver)
            share_count = extract_share_count(driver)
        
        # Format current timestamp
        scraped_at = datetime.datetime.now().isoformat()
        
        # Compile results
        result = {
            "answer_url": url,
            "base_url": base_url,
            "author": author_name,
            "post_date": post_date,
            "stats": {
                "views": view_count,
                "upvotes": upvote_count,
                "comments": comment_count,
                "shares": share_count
            },
            "scraped_at": scraped_at,
            "is_deleted": is_deleted
        }
        
        print("\nExtracted data:")
        print(f"Answer URL: {url}")
        print(f"Thread URL: {base_url}")
        print(f"Author: {author_name}")
        
        # Format date message based on the result
        if post_date.startswith("Approx:"):
            print(f"Post date: {post_date}")
        else:
            print(f"Post date: {post_date} (exact date from log)")
            
        print(f"Views: {view_count}")
        print(f"Upvotes: {upvote_count}")
        print(f"Comments: {comment_count}")
        print(f"Shares: {share_count}")
        print(f"Scraped at: {scraped_at}")
        if is_deleted:
            print(f"Status: DELETED")
        
        return result
        
    except Exception as e:
        print(f"Error scraping Quora answer: {e}")
        return {
            "answer_url": url,
            "base_url": extract_base_url(url),
            "author": "Error",
            "post_date": "Error",
            "stats": {
                "views": "0",
                "upvotes": "0",
                "comments": "0",
                "shares": "0"
            },
            "scraped_at": datetime.datetime.now().isoformat(),
            "error": str(e)
        } 