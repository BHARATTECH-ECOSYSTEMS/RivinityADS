import asyncio
import httpx
import json
import time

BASE_URL = "http://localhost:8000/api/v1"

async def main():
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        print("--- PHASE 10 E2E AUDIT ---")
        
        # 1. Register Advertiser
        print("\n1. Registering Advertiser...")
        adv_email = f"adv_audit_{int(time.time())}@example.com"
        res = await client.post("/auth/register", json={
            "email": adv_email,
            "password": "password123456",
            "name": "Audit Advertiser",
            "role": "advertiser"
        })
        if res.status_code != 201:
            print("Failed to register advertiser:", res.text)
            return
        
        # Login Advertiser
        res = await client.post("/auth/login", json={
            "email": adv_email,
            "password": "password123456"
        })
        if res.status_code != 200:
            print("Advertiser login failed:", res.text)
            return
            
        adv_token = res.json()["access_token"]
        adv_headers = {"Authorization": f"Bearer {adv_token}"}
        
        # Get Advertiser ID
        res = await client.get("/auth/me", headers=adv_headers)
        adv_id = res.json()["advertiser_id"]
        print(f"Advertiser registered. ID: {adv_id}")

        # 2. Deposit Budget
        print("\n2. Depositing Budget...")
        # Admin deposit:
        res_admin = await client.post("/auth/login", json={
            "email": "admin@openadserver.com",
            "password": "adminpassword"
        })
        if res_admin.status_code == 200:
            admin_token = res_admin.json()["access_token"]
            res_dep = await client.post(f"/finance/advertiser/{adv_id}/deposit", headers={"Authorization": f"Bearer {admin_token}"}, json={
                "amount": 1000.0,
                "reference_id": f"dep_{int(time.time())}"
            })
            if res_dep.status_code == 201 or res_dep.status_code == 200:
                print("Budget deposited.")
            else:
                print("Budget deposit failed:", res_dep.text)
        else:
            print("Failed to login as admin, proceeding without deposit:", res_admin.text)

        print("\n3. Creating Campaign & Creative...")
        res = await client.post(f"/advertiser/{adv_id}/campaign", headers=adv_headers, json={
            "name": "Audit Campaign",
            "budget": 100.0,
            "daily_budget": 10.0,
            "bid_amount": 1.0,
            "bid_type": 1,
            "status": 1
        })
        campaign = res.json()
        campaign_id = campaign["id"]

        res = await client.post(f"/advertiser/campaign/{campaign_id}/creative", headers=adv_headers, json={
            "name": "Audit Creative",
            "format": 1,
            "width": 300,
            "height": 250,
            "status": 1,
            "landing_url": "https://example.com/audit",
            "image_url": "https://example.com/audit.jpg"
        })
        creative = res.json()

        # Set Targeting
        res = await client.post(f"/targeting/campaign/{campaign_id}", headers=adv_headers, json={
            "include_countries": ["US"],
            "include_sectors": ["developers"]
        })

        print(f"Campaign {campaign_id} created with creative and targeting.")
        
        # 4. Register Publisher
        print("\n4. Registering Publisher...")
        pub_email = f"pub_audit_{int(time.time())}@example.com"
        res = await client.post("/auth/register", json={
            "email": pub_email,
            "password": "password123456",
            "name": "Audit Publisher",
            "role": "publisher"
        })
        
        res = await client.post("/auth/login", json={
            "email": pub_email,
            "password": "password123456"
        })
        pub_token = res.json()["access_token"]
        pub_headers = {"Authorization": f"Bearer {pub_token}"}
        
        res = await client.get("/auth/me", headers=pub_headers)
        pub_id = res.json()["publisher_id"]
        print(f"Publisher registered. ID: {pub_id}")

        # 5. Create Property & Slot
        res = await client.post(f"/publisher/{pub_id}/property", headers=pub_headers, json={
            "name": "Audit Website",
            "domain": "audit.com",
            "status": 1
        })
        property_id = res.json()["id"]

        res = await client.post(f"/publisher/{pub_id}/slots", headers=pub_headers, json={
            "slot_id": f"audit_slot_{int(time.time())}",
            "name": "Audit Slot",
            "format": 1,
            "width": 300,
            "height": 250,
            "floor_price": 0.5,
            "property_id": property_id
        })
        slot = res.json()
        db_slot_id = slot["id"]
        str_slot_id = slot["slot_id"]
        
        # 6. Generate API Key
        res = await client.post("/publisher/keys", headers=pub_headers, json={
            "publisher_id": pub_id,
            "name": "Audit Key"
        })
        raw_key = res.json()["raw_key"]
        print(f"Generated Publisher API Key.")
        
        # 7. Ad Serving Request
        print("\n5. Requesting Ad...")
        res = await client.post("/ad", headers={"X-API-Key": raw_key}, json={
            "slot_id": str_slot_id,
            "ip_address": "8.8.8.8",
            "user_agent": "AuditBot/1.0",
            "url": "https://audit.com/page",
            "context_sectors": ["developers"],
            "user_id": "audit_user_1"
        })
        print(f"Ad request response: {res.status_code}")
        if res.status_code == 200:
            ad_res = res.json()
            print("Ad served:", json.dumps(ad_res, indent=2))
            
            if ad_res.get("id"):
                # Track Impression
                imp_url = ad_res["impression_url"]
                res_imp = await client.get(imp_url.replace("http://localhost:8000/api/v1", ""))
                print("Impression tracking:", res_imp.status_code)
                
                # Track Click
                click_url = ad_res["click_url"]
                res_click = await client.get(click_url.replace("http://localhost:8000/api/v1", ""))
                print("Click tracking:", res_click.status_code)

        print("\n--- AUDIT COMPLETE ---")

if __name__ == "__main__":
    asyncio.run(main())
