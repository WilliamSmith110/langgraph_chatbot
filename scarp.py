from pickle import FALSE
import time
import json
import os
import re
import requests
import pdfplumber
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

BASE_URL = "https://shop.kingarthurbaking.com"
MIXES_URL = f"{BASE_URL}/mixes"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Set options for headless browsing
options = Options()
options.headless = True

# Initialize WebDriver with automatic ChromeDriver management
service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=options)

def get_all_product_urls():
    product_urls = set()
    driver.get(MIXES_URL)
    time.sleep(3)  # Wait for page to load. Adjust if necessary.

    # Infinite scroll
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        # Scroll down to bottom
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        # Wait for new content to load
        time.sleep(5)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            # No more new content
            break
        last_height = new_height

    # Find all link elements
    links = driver.find_elements(By.TAG_NAME, 'a')
    found = 0
    for link in links:
        href = link.get_attribute('href')
        tab_index = link.get_attribute('tabindex')

        # Skip links with tabindex attribute
        if tab_index:
            continue

        if href and href.startswith('https://shop.kingarthurbaking.com/items/') :
            if href.endswith('overlay'):
                continue
            # full_url = BASE_URL + href
            if href not in product_urls:
                product_urls.add(href)
                found += 1
                print(href)
    print(f"Total product URLs found: {found}")
    return list(product_urls)

def parse_product_page(url):
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            print(f"Failed to fetch {url}: Status code {resp.status_code}")
            return None
    except requests.exceptions.RequestException as e:
        print(f"Network error fetching {url}: {e}")
        return None
    soup = BeautifulSoup(resp.text, "html.parser")

    # Name
    name_tag = soup.find("h1")
    name = name_tag.get_text(strip=True) if name_tag else None

    # Price (try to get both original and sale)
    price = None
    price_tag = soup.find(class_="orig-price")
    if price_tag:
        price = price_tag.get_text(strip=True)
    else:
        # fallback: look for $ in text
        price_tag = soup.find(text=lambda t: t and "$" in t)
        price = price_tag.strip() if price_tag else None

    # Description
    desc_tag = soup.find("div", id='tab-description')
    # if not desc_tag:
    #     desc_tag = soup.find("div", class_="productView-description")
    description = desc_tag.get_text(separator=' ', strip=True)

    pdf_link = None
    ingredients = "N/A"
    pdf_anchor = soup.find('a', href=lambda h: h and h.lower().endswith('.pdf'))
    if pdf_anchor:
        pdf_link = pdf_anchor['href']
        if not pdf_link.startswith('http'):
            pdf_link = BASE_URL + pdf_link
    if not pdf_link:
        pdf_anchor = soup.find('a', href=True, string=lambda s: s and any(x in s.lower() for x in ['pdf', 'download', 'packaging']))
        if pdf_anchor:
            pdf_link = pdf_anchor['href']
            if not pdf_link.startswith('http'):
                pdf_link = BASE_URL + pdf_link
    if not pdf_link:
        # pdf_link = fallback_pdf_link
        print(f"No PDF link found for {url}")
    if pdf_link:
        try:
            pdf_path = download_pdf(pdf_link, 'pdf')
            if pdf_path:
                pdf_text = parse_pdf_text(pdf_path)
                nutrition_facts = pdf_text
                # --- Extract ingredients from PDF text ---
                lines = [line.strip() for line in pdf_text.splitlines() if line.strip()]
                ing_start = -1
                ing_end = None
                contains_line = ''
                for i, line in enumerate(lines):
                    if 'INGREDIENTS' in line.upper():
                        ing_start = i
                    if 'CONTAINS' in line.upper():
                        ing_end = i
                        contains_line = line
                        break
                if ing_start != -1:
                    # If CONTAINS is found after INGREDIENTS, use lines in between; else, just the INGREDIENTS line/block
                    if ing_end is not None and ing_end > ing_start:
                        ing_lines = lines[ing_start:ing_end]
                    else:
                        # Try to grab a block of lines starting with INGREDIENTS and continuing until a blank line or next all-caps section
                        ing_lines = [lines[ing_start]]
                        for l in lines[ing_start+1:]:
                            if l.isupper() and not l.startswith('('):
                                break
                            if l == '':
                                break
                            ing_lines.append(l)
                    # Remove any leading 'INGREDIENTS:' or similar
                    if ':' in ing_lines[0]:
                        ing_lines[0] = ing_lines[0].split(':', 1)[-1].strip()
                    ingredients = ' '.join(ing_lines).replace(' ,', ',').replace(' .', '.').strip()
                    if contains_line:
                        ingredients += f' {contains_line.strip()}'
                    ingredients = ingredients.upper()
                else:
                    # Fallback: try to find all-caps ingredient blocks
                    match = re.search(r'(BREAD MIX:.*?CONTAINS:.*?\.)', pdf_text, re.DOTALL | re.IGNORECASE)
                    if match:
                        ingredients = match.group(1).replace('\n', ' ').replace(' ,', ',').replace(' .', '.').strip().upper()
        except Exception as e:
            print(f"Error downloading/parsing PDF for {url}: {e}")

    # rating (review rating integer)
    rating = 0
    rating_div = soup.find('div', class_='kab-product-rating rating-widget')
    if rating_div:
        rating = rating_div['data-rating']
    # else:

    # reviews 
    # reviews = 0
    # reviews_span = rating_div.find('span', class_='reviews__label')
    # # Extract the text, e.g., "199 Reviews"
    # reviews_text = reviews_span.get_text(strip=True)
    # # Extract the number from the text
    # match = re.search(r'(\d+)', reviews_text)
    # reviews = match.group(1) if match else None
    # print(reviews)  # Output: 199

    # discount when multiple buy (Buy Any 5+/Save $4)
    discount_multiple_buy = False
    discount_multiple_buy_tag = soup.find('span', class_='bulk-promo')
    if discount_multiple_buy_tag:
        discount_multiple_buy = True
    # else:

    # product image source
    img_source = []
    img_thumbail_tag = soup.find_all('a', class_='productView-thumbnail-link')
    if img_thumbail_tag:
        for img_tag in img_thumbail_tag:
            img_source.append(img_tag['href'])

    # kitchen tips
    kitchen_tips = ""
    kitchen_tips_tag = soup.find('div', class_='product-tips-box_single product-tips-box_alone')
    if kitchen_tips_tag:
        kitchen_tips = kitchen_tips_tag.get_text(strip=True)
        
    # confirm new product
    new = False
    new_flag = soup.find('span', class_='new-flag')
    if new_flag:
        new = True    

    # confirm saled product
    sale = False
    sale_flag = soup.find('span', class_='sale-flag')
    if new_flag:
        new = True

    
    # confirm best seller product
    best_seller = False
    best_seller_flag = soup.find('span', class_='best-seller-flag')
    if best_seller_flag:
        best_seller = True

    # how can you save money
    discount = 0
    save_money_flag = soup.find('span', class_='price price--saving')
    if save_money_flag:
        discount = save_money_flag.get_text(strip=True)
        

    return {
        "name": name,
        "price": price,
        "new": new,
        "url": url,
        "discount": discount,
        "best_seller": best_seller,
        "discount_multiple_buy" : discount_multiple_buy,
        "img_source": img_source,
        "kitchen_tips": kitchen_tips,
        "rating": rating,
        "description": description,
        "ingredients": ingredients
    }

def download_pdf(pdf_url, pdf_dir):
    # Create directory if it doesn't exist
    os.makedirs(pdf_dir, exist_ok=True)
    
    pdf_name = pdf_url.split('/')[-1]
    pdf_path = os.path.join(pdf_dir, pdf_name)
    
    # Check if PDF already exists
    if os.path.exists(pdf_path):
        return pdf_path
    
    try:
        res = requests.get(pdf_url, headers=headers, timeout=30)
        res.raise_for_status()
        with open(pdf_path, 'wb') as f:
            f.write(res.content)
        return pdf_path
    except requests.exceptions.RequestException as e:
        print(f"Error downloading PDF {pdf_url}: {e}")
        return None

def parse_pdf_text(pdf_path):
    try:
        pdf_text = ""
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                pdf_text += page.extract_text() or ""
        return pdf_text.strip()
    except Exception as e:
        print(f"Error parsing PDF {pdf_path}: {e}")
        return ""

def main():
    try:
        product_urls = get_all_product_urls()
        results = []
        for url in product_urls:
            data = parse_product_page(url)
            if data:
                results.append(data)
            time.sleep(1)  # Politeness
            
        # Write results to a JSON file
        with open('products.json', 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print("Data saved to products.json")
    finally:
        driver.quit()
    # try:
    #     results = []
    #     data = parse_product_page("https://shop.kingarthurbaking.com/items/all-purpose-keto-muffin-mix")
    #     results.append(data)
    #     with open('products.json', 'w', encoding='utf-8') as f:
    #         json.dump(results, f, indent=2, ensure_ascii=False)
    #     print("Data saved to products.json")
    # finally:
    #     driver.quit()

if __name__ == "__main__":
    main()