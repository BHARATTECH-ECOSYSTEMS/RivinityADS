import asyncio
from httpx import AsyncClient

async def run_e2e():
    async with AsyncClient(base_url="http://localhost:8000") as client:
        res = await client.post("/api/v1/auth/login", json={
            "email": "e2e_adv6@adv.com",
            "password": "password12345"
        })
        if res.status_code != 200:
            print("Failed to authenticate.", res.text)
            return
            
        data = res.json()
        token = data["access_token"]
        adv_id = data["user"]["advertiser_id"]
        headers = {"Authorization": f"Bearer {token}"}
        
        print(f"[AUTH] Logged in as advertiser {adv_id}")
        
        # Check existing campaigns
        res = await client.get("/api/v1/campaign", headers=headers)
        campaigns = res.json()
        if not campaigns:
            payload = {
                "advertiser_id": adv_id,
                "name": "E2E Test Campaign",
                "budget_daily": 100.0,
                "budget_total": 1000.0,
                "bid_amount": 1.0,
                "bid_type": 1,
                "freq_cap_daily": 0,
                "freq_cap_hourly": 0,
                "status": 1
            }
            res = await client.post("/api/v1/campaign", json=payload, headers=headers)
            if res.status_code != 200:
                print("Failed to create campaign:", res.json())
                return
            campaign = res.json()
            campaigns = [campaign]
            
        camp_id = campaigns[-1]["id"]
        print(f"[CAMPAIGN] Using campaign {camp_id}. Autopilot: {campaigns[-1]['autopilot']}")
        
        # Test Autopilot Toggle OFF -> ON -> OFF
        res = await client.patch(f"/api/v1/campaign/{camp_id}", json={"autopilot": True}, headers=headers)
        if res.status_code == 200 and res.json()["autopilot"] is True:
            print("[AUTOPILOT] Toggled OFF -> ON successfully")
        
        res = await client.patch(f"/api/v1/campaign/{camp_id}", json={"autopilot": False}, headers=headers)
        if res.status_code == 200 and res.json()["autopilot"] is False:
            print("[AUTOPILOT] Toggled ON -> OFF successfully")
            
        # Check suggestions
        res = await client.get(f"/api/v1/campaign/{camp_id}/suggestions", headers=headers)
        suggestions = res.json()
        print(f"[SUGGESTIONS] Found {len(suggestions)} pending suggestions")
        for sugg in suggestions:
            print(f"  - ID: {sugg['id']}, Action: {sugg['action']['action']}")

if __name__ == "__main__":
    asyncio.run(run_e2e())
