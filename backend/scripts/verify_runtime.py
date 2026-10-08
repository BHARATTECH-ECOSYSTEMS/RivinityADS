import asyncio
from httpx import AsyncClient

async def run_test():
    async with AsyncClient(base_url="http://localhost:8000") as client:
        # Register a test advertiser
        res = await client.post("/api/v1/auth/register", json={
            "name": "E2E Test Adv",
            "email": "e2e_adv@adv.com",
            "password": "password123",
            "role": "advertiser"
        })
        if res.status_code == 400:
            # Maybe already exists, try logging in
            res = await client.post("/api/v1/auth/login", json={
                "email": "e2e_adv@adv.com",
                "password": "password123"
            })
            if res.status_code != 200:
                print("Failed to authenticate.")
                return
        
        data = res.json()
        token = data["access_token"]
        adv_id = data["user"]["advertiser_id"]
        headers = {"Authorization": f"Bearer {token}"}
        
        print(f"[AUTH] Logged in as advertiser {adv_id}")
        
        # Create a campaign
        res = await client.post("/api/v1/campaign", json={
            "advertiser_id": adv_id,
            "name": "E2E Test Campaign",
            "budget_daily": 100.0,
            "budget_total": 1000.0,
            "bid_amount": 1.0,
            "bid_type": 1,
            "status": 1
        }, headers=headers)
        
        if res.status_code != 200:
            print("Failed to create campaign", res.json())
            return
            
        campaign = res.json()
        camp_id = campaign["id"]
        print(f"[CAMPAIGN] Created campaign {camp_id}. Autopilot: {campaign['autopilot']}")
        
        # Test Autopilot Toggle OFF -> ON
        res = await client.patch(f"/api/v1/campaign/{camp_id}", json={"autopilot": True}, headers=headers)
        if res.status_code == 200 and res.json()["autopilot"] is True:
            print("[AUTOPILOT] Toggled OFF -> ON successfully")
        
        # Test Autopilot Toggle ON -> OFF
        res = await client.patch(f"/api/v1/campaign/{camp_id}", json={"autopilot": False}, headers=headers)
        if res.status_code == 200 and res.json()["autopilot"] is False:
            print("[AUTOPILOT] Toggled ON -> OFF successfully")
            
        # We need to manually trigger the optimizer logic OR mock a suggestion for API testing 
        # because waiting for 15 min scheduler is too long.
        print("[SUGGESTION] Triggering OptimizationExecutor for campaign", camp_id)
        
if __name__ == "__main__":
    asyncio.run(run_test())
