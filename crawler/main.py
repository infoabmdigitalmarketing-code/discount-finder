import os
import sys
import time
import random
import logging
import requests
import re
import json
from bs4 import BeautifulSoup
from supabase import create_client

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [INFO]: %(message)s'
)
logger = logging.getLogger(__name__)

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

SALE_KEYWORDS = [
    'sale', 'discount', 'offer', 'deal', 'promo',
    'clearance', 'off', 'savings', 'coupon', 'percent'
]

AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
]


def get_headers():
    return {
        "User-Agent": random.choice(AGENTS),
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
    }


def fetch_sitemap(url, depth=0):
    if depth > 1:
        return []
    urls = []
    try:
        logger.info(f"Fetching: {url}")
        r = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Bot/1.0)"},
            timeout=15
        )
        if r.status_code != 200:
            return []
        content = r.text
        if '<sitemapindex' in content:
            soup = BeautifulSoup(content, 'html.parser')
            for loc in soup.find_all('loc'):
                sub = loc.get_text(strip=True)
                if any(k in sub.lower() for k in SALE_KEYWORDS):
                    time.sleep(1)
                    urls.extend(fetch_sitemap(sub, depth + 1)[:10])
        elif '<urlset' in content:
            soup = BeautifulSoup(content, 'html.parser')
            for loc in soup.find_all('loc'):
                u = loc.get_text(strip=True)
                if any(k in u.lower() for k in SALE_KEYWORDS):
                    urls.append(u)
    except Exception as e:
        logger.error(f"Sitemap error: {e}")
    return urls[:20]


def parse_page(html):
    soup = BeautifulSoup(html, 'html.parser')
    data = {
        'title': '', 'image_url': '', 'description': '',
        'discount_percentage': None, 'original_price': None,
        'discounted_price': None, 'coupon_code': None,
        'confidence': 0.0
    }
    conf = 0.0

    og = soup.find('meta', property='og:title')
    if og and og.get('content'):
        data['title'] = og['content'].strip()
        conf += 0.2
    elif soup.find('h1'):
        data['title'] = soup.find('h1').get_text(strip=True)
        conf += 0.1
    elif soup.find('title'):
        data['title'] = soup.find('title').get_text(strip=True)
        conf += 0.05

    img = soup.find('meta', property='og:image')
    if img and img.get('content'):
        data['image_url'] = img['content'].strip()

    desc = soup.find('meta', property='og:description')
    if desc and desc.get('content'):
        data['description'] = desc['content'].strip()

    text = soup.get_text(separator=' ', strip=True)

    for pat in [r'(\d{1,2})\s*%\s*off', r'save\s+(\d{1,2})\s*%', r'(\d{1,2})\s*%\s*discount']:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            try:
                pct = int(m.group(1))
                if 5 <= pct <= 90:
                    data['discount_percentage'] = pct
                    conf += 0.35
                    break
            except ValueError:
                pass

    was = re.search(r'(?:was|original|regular)\s*:?\s*\$?\s*([\d,]+(?:\.\d{2})?)', text, re.IGNORECASE)
    if was:
        try:
            data['original_price'] = float(was.group(1).replace(',', ''))
            conf += 0.15
        except ValueError:
            pass

    now = re.search(r'(?:now|sale|today)\s*:?\s*\$?\s*([\d,]+(?:\.\d{2})?)', text, re.IGNORECASE)
    if now:
        try:
            data['discounted_price'] = float(now.group(1).replace(',', ''))
            conf += 0.15
        except ValueError:
            pass

    coup = re.search(r'(?:use\s+code|coupon|promo)\s*:?\s*["\']?([A-Z0-9]{4,15})["\']?', text, re.IGNORECASE)
    if coup:
        code = coup.group(1).upper()
        if code not in ['HTML', 'HTTP', 'FREE', 'SALE', 'SHOP', 'HERE']:
            data['coupon_code'] = code
            conf += 0.1

    for script in soup.find_all('script', type='application/ld+json'):
        try:
            jd = json.loads(script.string or '{}')
            items = jd if isinstance(jd, list) else [jd]
            for item in items:
                if item.get('@type') in ['Product', 'Offer']:
                    if not data['title'] and item.get('name'):
                        data['title'] = item['name']
                        conf += 0.1
                    off = item.get('offers', {})
                    if isinstance(off, list):
                        off = off[0] if off else {}
                    if isinstance(off, dict) and off.get('price') and not data['discounted_price']:
                        try:
                            data['discounted_price'] = float(str(off['price']))
                            conf += 0.2
                        except (ValueError, TypeError):
                            pass
        except Exception:
            pass

    data['confidence'] = min(conf, 1.0)
    return data


def make_affiliate(url, domain):
    aff = {
        'amazon.com': ('tag', 'yourtag-20'),
        'nike.com': ('cp', 'your_nike_tag'),
        'asos.com': ('affid', 'your_asos_id'),
        'blacktieattire.org': ('ref', 'discount_finder'),
    }
    for d, (p, v) in aff.items():
        if d in domain:
            sep = '&' if '?' in url else '?'
            return f"{url}{sep}{p}={v}"
    return url


def run_crawler():
    logger.info("=" * 50)
    logger.info("🚀 DISCOUNT CRAWLER STARTED")
    logger.info("=" * 50)

    if not SUPABASE_URL:
        logger.error("❌ SUPABASE_URL missing!")
        sys.exit(1)
    if not SUPABASE_KEY:
        logger.error("❌ SUPABASE_SERVICE_KEY missing!")
        sys.exit(1)

    logger.info(f"URL: {SUPABASE_URL[:30]}...")

    # Connect
    try:
        db = create_client(SUPABASE_URL, SUPABASE_KEY)
        logger.info("✅ Connected to Supabase")
    except Exception as e:
        logger.error(f"❌ Connection failed: {e}")
        sys.exit(1)

    # Get brands - multiple attempts
    brands = []

    # Attempt 1: Filter active brands
    try:
        resp = db.table('brands').select('*').eq('is_active', True).eq('crawl_enabled', True).limit(5).execute()
        brands = resp.data or []
        logger.info(f"✅ Got {len(brands)} brands (attempt 1)")
    except Exception as e:
        logger.warning(f"Attempt 1 failed: {e}")

    # Attempt 2: Get all brands
    if not brands:
        try:
            resp = db.table('brands').select('*').limit(10).execute()
            brands = resp.data or []
            logger.info(f"✅ Got {len(brands)} brands (attempt 2)")
        except Exception as e:
            logger.warning(f"Attempt 2 failed: {e}")

    # Attempt 3: Direct REST API
    if not brands:
        try:
            headers = {
                'apikey': SUPABASE_KEY,
                'Authorization': f'Bearer {SUPABASE_KEY}',
                'Content-Type': 'application/json',
            }
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/brands?select=*&limit=10",
                headers=headers,
                timeout=10
            )
            if r.status_code == 200:
                brands = r.json()
                logger.info(f"✅ Got {len(brands)} brands (attempt 3)")
            else:
                logger.error(f"REST API failed: {r.status_code} - {r.text}")
        except Exception as e:
            logger.error(f"Attempt 3 failed: {e}")

    if not brands:
        logger.error("❌ Could not fetch brands from database!")
        logger.error("Please run SQL schema in Supabase SQL Editor")
        sys.exit(1)

    logger.info(f"📋 Processing {len(brands)} brands")
    total = 0

    for brand in brands:
        name = brand.get('name', '?')
        bid = brand.get('id', '')
        domain = brand.get('domain', '')
        sitemaps = brand.get('sitemap_urls') or []
        currency = brand.get('currency_symbol', '$')

        if not brand.get('is_active', True):
            continue
        if not brand.get('crawl_enabled', True):
            continue

        logger.info(f"\n--- {name} ({domain}) ---")

        # Get URLs
        all_urls = []
        for sm in sitemaps:
            found = fetch_sitemap(sm)
            all_urls.extend(found)
            time.sleep(1)

        if not all_urls and domain:
            for sm in [
                f"https://www.{domain}/sitemap.xml",
                f"https://{domain}/sitemap.xml",
            ]:
                found = fetch_sitemap(sm)
                if found:
                    all_urls.extend(found)
                    break
                time.sleep(1)

        all_urls = list(set(all_urls))
        logger.info(f"URLs found: {len(all_urls)}")

        saved = 0
        for i, url in enumerate(all_urls[:10]):
            logger.info(f"  [{i+1}] {url[:60]}")
            try:
                r = requests.get(url, headers=get_headers(), timeout=12, allow_redirects=True)
                if r.status_code != 200:
                    continue
                if 'text/html' not in r.headers.get('Content-Type', ''):
                    continue

                disc = parse_page(r.text)

                if disc['confidence'] < 0.25:
                    continue
                if not disc['title']:
                    continue

                has_deal = (
                    disc['discount_percentage'] is not None or
                    disc['coupon_code'] is not None or
                    (disc['original_price'] and disc['discounted_price'])
                )
                if not has_deal:
                    continue

                record = {
                    'brand_id': bid,
                    'title': disc['title'][:400],
                    'description': (disc['description'] or '')[:800],
                    'discount_url': url,
                    'affiliate_url': make_affiliate(url, domain),
                    'image_url': disc['image_url'] or None,
                    'original_price': disc['original_price'],
                    'discounted_price': disc['discounted_price'],
                    'discount_percentage': disc['discount_percentage'],
                    'currency_symbol': currency,
                    'coupon_code': disc['coupon_code'],
                    'source_type': 'sitemap',
                    'confidence_score': round(disc['confidence'], 3),
                    'is_active': True,
                    'is_expired': False,
                }

                try:
                    db.table('discounts').upsert(
                        record,
                        on_conflict='brand_id,discount_url'
                    ).execute()
                    saved += 1
                    total += 1
                    logger.info(f"  ✅ Saved: {disc['discount_percentage'] or '?'}% | {disc['title'][:40]}")
                except Exception as e:
                    logger.warning(f"  Save error: {e}")

            except Exception as e:
                logger.debug(f"  Error: {e}")

            time.sleep(random.uniform(2, 4))

        # Update crawl time
        try:
            db.table('brands').update(
                {'last_crawled_at': 'now()'}
            ).eq('id', bid).execute()
        except Exception:
            pass

        logger.info(f"  => Saved {saved} discounts for {name}")
        time.sleep(random.uniform(3, 6))

    logger.info("\n" + "=" * 50)
    logger.info(f"✅ DONE! Total saved: {total}")
    logger.info("=" * 50)


if __name__ == "__main__":
    run_crawler()
