"""
Main Crawler - Runs on GitHub Actions every 6 hours
Finds discounts from brand websites
"""

import os
import sys
import time
import random
import logging
import requests
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from supabase import create_client
import re
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s]: %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION
# ============================================================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

SALE_KEYWORDS = [
    'sale', 'discount', 'offer', 'deal', 'promo',
    'clearance', 'off', 'savings', 'special', 'coupon',
    'percent', 'reduced', 'markdown', 'bargain'
]

FALLBACK_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
]

# ============================================================
# USER AGENT ROTATION
# ============================================================
def get_random_headers():
    """Get random browser headers to avoid blocking"""
    try:
        ua = UserAgent()
        user_agent = ua.random
    except Exception:
        user_agent = random.choice(FALLBACK_USER_AGENTS)
    
    return {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }

# ============================================================
# SITEMAP CRAWLER
# ============================================================
def fetch_sitemap_urls(sitemap_url):
    """Fetch URLs from sitemap XML"""
    sale_urls = []
    
    try:
        logger.info(f"📄 Fetching sitemap: {sitemap_url}")
        
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; DiscountBot/1.0)",
            "Accept": "application/xml,text/xml,*/*"
        }
        
        response = requests.get(sitemap_url, headers=headers, timeout=15)
        
        if response.status_code != 200:
            logger.warning(f"Sitemap returned {response.status_code}")
            return []
        
        content = response.text
        
        # Check if sitemap index
        if '<sitemapindex' in content:
            logger.info("Found sitemap index, looking for sale sitemaps...")
            try:
                root = ET.fromstring(content)
                ns = '{http://www.sitemaps.org/schemas/sitemap/0.9}'
                
                for sitemap in root.iter(f'{ns}sitemap'):
                    loc = sitemap.find(f'{ns}loc')
                    if loc is not None and loc.text:
                        sub_url = loc.text.strip()
                        # Check if sub-sitemap is sale related
                        if any(kw in sub_url.lower() for kw in ['sale', 'deal', 'offer', 'discount']):
                            time.sleep(random.uniform(1, 2))
                            sub_urls = fetch_sitemap_urls(sub_url)
                            sale_urls.extend(sub_urls[:20])
                            
            except ET.ParseError:
                pass
                
        # Regular sitemap
        elif '<urlset' in content:
            try:
                root = ET.fromstring(content)
                ns = '{http://www.sitemaps.org/schemas/sitemap/0.9}'
                
                for url_elem in root.iter(f'{ns}url'):
                    loc = url_elem.find(f'{ns}loc')
                    if loc is not None and loc.text:
                        url = loc.text.strip()
                        # Filter sale URLs
                        if any(kw in url.lower() for kw in SALE_KEYWORDS):
                            sale_urls.append(url)
                            
            except ET.ParseError:
                # Try BeautifulSoup as fallback
                soup = BeautifulSoup(content, 'lxml-xml')
                for loc in soup.find_all('loc'):
                    url = loc.get_text(strip=True)
                    if any(kw in url.lower() for kw in SALE_KEYWORDS):
                        sale_urls.append(url)
        
        logger.info(f"✅ Found {len(sale_urls)} sale URLs in sitemap")
        return sale_urls[:30]  # Limit to 30 URLs per sitemap
        
    except Exception as e:
        logger.error(f"Sitemap error: {e}")
        return []

# ============================================================
# PAGE PARSER
# ============================================================
def parse_page_for_discounts(url, html_content):
    """Extract discount info from a page"""
    
    soup = BeautifulSoup(html_content, 'lxml')
    result = {
        'title': '',
        'description': '',
        'image_url': '',
        'discount_percentage': None,
        'original_price': None,
        'discounted_price': None,
        'coupon_code': None,
        'confidence': 0.0
    }
    
    confidence = 0.0
    
    # ── Get Title ──
    og_title = soup.find('meta', property='og:title')
    if og_title and og_title.get('content'):
        result['title'] = og_title['content'].strip()
        confidence += 0.2
    elif soup.find('title'):
        result['title'] = soup.find('title').get_text(strip=True)
        confidence += 0.1
    
    # ── Get Description ──
    og_desc = soup.find('meta', property='og:description')
    if og_desc and og_desc.get('content'):
        result['description'] = og_desc['content'].strip()
    
    # ── Get Image ──
    og_image = soup.find('meta', property='og:image')
    if og_image and og_image.get('content'):
        result['image_url'] = og_image['content'].strip()
    
    # ── Find Discount Percentage ──
    text_content = soup.get_text(separator=' ', strip=True)
    
    percent_patterns = [
        r'(\d{1,2})\s*%\s*(?:off|discount|savings?)',
        r'(?:save|saving)\s+(\d{1,2})\s*%',
        r'(\d{1,2})\s*percent\s*off',
    ]
    
    for pattern in percent_patterns:
        matches = re.findall(pattern, text_content, re.IGNORECASE)
        if matches:
            pct = int(matches[0])
            if 5 <= pct <= 90:
                result['discount_percentage'] = pct
                confidence += 0.3
                break
    
    # ── Find Prices ──
    # Original price (was/regular/original)
    was_pattern = r'(?:was|original|reg\.?|regular|retail)\s*:?\s*\$?\s*([\d,]+(?:\.\d{2})?)'
    was_match = re.search(was_pattern, text_content, re.IGNORECASE)
    if was_match:
        try:
            price = float(was_match.group(1).replace(',', ''))
            if 0 < price < 100000:
                result['original_price'] = price
                confidence += 0.15
        except ValueError:
            pass
    
    # Sale price (now/sale/today)
    now_pattern = r'(?:now|sale|today|special)\s*:?\s*\$?\s*([\d,]+(?:\.\d{2})?)'
    now_match = re.search(now_pattern, text_content, re.IGNORECASE)
    if now_match:
        try:
            price = float(now_match.group(1).replace(',', ''))
            if 0 < price < 100000:
                result['discounted_price'] = price
                confidence += 0.15
        except ValueError:
            pass
    
    # ── Find Coupon Code ──
    coupon_patterns = [
        r'(?:use\s+code|coupon|promo\s+code)\s*:?\s*["\']?([A-Z0-9]{4,15})["\']?',
        r'(?:discount\s+code)\s*:?\s*["\']?([A-Z0-9]{4,15})["\']?',
    ]
    
    for pattern in coupon_patterns:
        matches = re.findall(pattern, text_content, re.IGNORECASE)
        if matches:
            code = matches[0].upper()
            if code not in ['HTML', 'HTTP', 'FREE', 'SALE', 'SHOP']:
                result['coupon_code'] = code
                confidence += 0.1
                break
    
    # ── Schema.org JSON-LD ──
    import json
    scripts = soup.find_all('script', type='application/ld+json')
    for script in scripts:
        try:
            data = json.loads(script.string or '{}')
            items = data if isinstance(data, list) else [data]
            for item in items:
                if item.get('@type') in ['Product', 'Offer']:
                    if not result['title'] and item.get('name'):
                        result['title'] = item['name']
                    offers = item.get('offers', item)
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    if isinstance(offers, dict):
                        price = offers.get('price')
                        if price and not result['discounted_price']:
                            try:
                                result['discounted_price'] = float(str(price))
                                confidence += 0.2
                            except (ValueError, TypeError):
                                pass
        except Exception:
            pass
    
    result['confidence'] = min(confidence, 1.0)
    return result

# ============================================================
# AFFILIATE LINK TRANSFORMER
# ============================================================
AFFILIATE_CONFIG = {
    'amazon.com': {'param': 'tag', 'value': 'yourtag-20'},
    'walmart.com': {'param': 'wmlspartner', 'value': 'your_tag'},
    'nike.com': {'param': 'cp', 'value': 'your_nike_tag'},
    'blacktieattire.org': {'param': 'ref', 'value': 'discount_finder'},
}

def transform_to_affiliate(url, domain):
    """Add affiliate parameters to URL"""
    config = None
    
    for configured_domain, cfg in AFFILIATE_CONFIG.items():
        if configured_domain in domain:
            config = cfg
            break
    
    if not config:
        return url
    
    separator = '&' if '?' in url else '?'
    return f"{url}{separator}{config['param']}={config['value']}"

# ============================================================
# DATABASE OPERATIONS
# ============================================================
def save_to_supabase(supabase, discount_data):
    """Save discount to Supabase database"""
    try:
        response = supabase.table('discounts').upsert(
            discount_data,
            on_conflict='brand_id,discount_url'
        ).execute()
        return True
    except Exception as e:
        logger.error(f"DB save error: {e}")
        return False

# ============================================================
# MAIN CRAWLER FUNCTION
# ============================================================
def run_crawler():
    """Main crawler - processes all active brands"""
    
    logger.info("=" * 50)
    logger.info("🚀 DISCOUNT CRAWLER STARTED")
    logger.info("=" * 50)
    
    # Connect to Supabase
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("❌ Missing Supabase credentials!")
        sys.exit(1)
    
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.info("✅ Connected to Supabase")
    
    # Get brands to crawl
    try:
        brands_response = supabase.table('brands').select('*').eq('is_active', True).eq('crawl_enabled', True).order('last_crawled_at', nullsfirst=True).limit(5).execute()
        brands = brands_response.data or []
    except Exception as e:
        logger.error(f"❌ Cannot fetch brands: {e}")
        sys.exit(1)
    
    if not brands:
        logger.warning("⚠️ No brands found to crawl")
        return
    
    logger.info(f"📋 Found {len(brands)} brands to process")
    
    # Stats
    total_discounts = 0
    total_saved = 0
    
    # Process each brand
    for brand in brands:
        brand_name = brand.get('name', 'Unknown')
        brand_id = brand.get('id')
        brand_domain = brand.get('domain', '')
        sitemap_urls = brand.get('sitemap_urls', [])
        currency_symbol = brand.get('currency_symbol', '$')
        
        logger.info(f"\n🏪 Processing: {brand_name}")
        
        # Get sale URLs from sitemap
        all_sale_urls = []
        
        for sitemap_url in sitemap_urls:
            urls = fetch_sitemap_urls(sitemap_url)
            all_sale_urls.extend(urls)
            time.sleep(random.uniform(1, 2))
        
        # If no sitemap configured, try common locations
        if not sitemap_urls:
            common_sitemaps = [
                f"https://www.{brand_domain}/sitemap.xml",
                f"https://www.{brand_domain}/sitemap_index.xml",
            ]
            for sitemap_url in common_sitemaps:
                urls = fetch_sitemap_urls(sitemap_url)
                if urls:
                    all_sale_urls.extend(urls)
                    break
        
        logger.info(f"🔗 Found {len(all_sale_urls)} sale URLs for {brand_name}")
        
        brand_discounts = []
        
        # Process each URL
        for i, url in enumerate(all_sale_urls[:20]):  # Max 20 per brand
            logger.info(f"  [{i+1}/{min(len(all_sale_urls), 20)}] {url[:60]}...")
            
            try:
                headers = get_random_headers()
                response = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
                
                if response.status_code != 200:
                    continue
                
                if 'text/html' not in response.headers.get('Content-Type', ''):
                    continue
                
                # Parse the page
                discount = parse_page_for_discounts(url, response.text)
                
                # Filter low quality
                if discount['confidence'] < 0.3:
                    continue
                
                if not discount['title']:
                    continue
                
                # Must have some discount info
                has_discount_info = (
                    discount['discount_percentage'] or 
                    discount['coupon_code'] or
                    (discount['original_price'] and discount['discounted_price'])
                )
                
                if not has_discount_info:
                    continue
                
                # Create affiliate URL
                affiliate_url = transform_to_affiliate(url, brand_domain)
                
                # Prepare DB record
                db_record = {
                    'brand_id': brand_id,
                    'title': discount['title'][:500],
                    'description': (discount['description'] or '')[:1000],
                    'discount_url': url,
                    'affiliate_url': affiliate_url,
                    'image_url': discount['image_url'],
                    'original_price': discount['original_price'],
                    'discounted_price': discount['discounted_price'],
                    'discount_percentage': discount['discount_percentage'],
                    'currency_symbol': currency_symbol,
                    'coupon_code': discount['coupon_code'],
                    'source_type': 'sitemap',
                    'confidence_score': discount['confidence'],
                    'is_active': True,
                    'is_expired': False
                }
                
                brand_discounts.append(db_record)
                total_discounts += 1
                
                logger.info(f"  ✅ Discount: {discount['discount_percentage'] or '?'}% off | {discount['title'][:40]}")
                
            except Exception as e:
                logger.debug(f"  Error: {e}")
            
            # Polite delay
            time.sleep(random.uniform(2, 4))
        
        # Save to database
        if brand_discounts:
            for record in brand_discounts:
                if save_to_supabase(supabase, record):
                    total_saved += 1
        
        # Update brand crawl time
        try:
            supabase.table('brands').update({
                'last_crawled_at': 'now()'
            }).eq('id', brand_id).execute()
        except Exception as e:
            logger.warning(f"Could not update crawl time: {e}")
        
        logger.info(f"✅ {brand_name}: Saved {len(brand_discounts)} discounts")
        
        # Delay between brands
        time.sleep(random.uniform(5, 10))
    
    # Summary
    logger.info("\n" + "=" * 50)
    logger.info("📊 CRAWL COMPLETE")
    logger.info(f"💎 Discounts found: {total_discounts}")
    logger.info(f"💾 Discounts saved: {total_saved}")
    logger.info("=" * 50)


if __name__ == "__main__":
    run_crawler()
