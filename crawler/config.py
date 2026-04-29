import os

class Config:
    SUPABASE_URL = os.getenv("SUPABASE_URL", "")
    SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
    GITHUB_RUN_ID = os.getenv("GITHUB_RUN_ID", "local")
    
    MAX_URLS_PER_BRAND = 30
    MAX_BRANDS_PER_RUN = 5
    REQUEST_DELAY_MIN = 2.0
    REQUEST_DELAY_MAX = 4.0
    REQUEST_TIMEOUT = 10
    MIN_CONFIDENCE_TO_SAVE = 0.3
    
    SALE_KEYWORDS = [
        'sale', 'discount', 'offer', 'deal', 'promo',
        'clearance', 'off', 'savings', 'special', 'coupon'
    ]
