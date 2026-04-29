"""
FastAPI - Free API on Vercel
Chrome Extension is kay sath communicate karta hai
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from supabase import create_client
import os
import re

app = FastAPI(title="Discount Finder API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

def get_db():
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_ANON_KEY"]
    )

@app.get("/")
def root():
    return {"status": "ok", "message": "Discount Finder API"}

@app.get("/api/discounts")
def get_discounts_by_domain(
    domain: str = Query(...),
    limit: int = Query(default=5, le=10)
):
    """Domain ke liye discounts laao"""
    domain = re.sub(r'[^a-zA-Z0-9.\-]', '', domain)
    
    if len(domain) < 3:
        raise HTTPException(status_code=400, detail="Invalid domain")
    
    try:
        db = get_db()
        
        # Find brand by domain
        brand_resp = db.table('brands').select('id,name,logo_url').ilike('domain', f'%{domain}%').eq('is_active', True).limit(1).execute()
        
        if not brand_resp.data:
            return {"domain": domain, "count": 0, "discounts": []}
        
        brand = brand_resp.data[0]
        brand_id = brand['id']
        
        # Get discounts for this brand
        disc_resp = db.table('discounts').select('*').eq('brand_id', brand_id).eq('is_active', True).eq('is_expired', False).order('discount_percentage', desc=True).limit(limit).execute()
        
        discounts = disc_resp.data or []
        
        return {
            "domain": domain,
            "brand": brand,
            "count": len(discounts),
            "discounts": discounts
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/brands")
def get_all_brands():
    """Saray brands laao"""
    try:
        db = get_db()
        resp = db.table('brands').select('id,name,domain,category').eq('is_active', True).limit(100).execute()
        return {"brands": resp.data or []}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/health")
def health():
    return {"status": "healthy"}
