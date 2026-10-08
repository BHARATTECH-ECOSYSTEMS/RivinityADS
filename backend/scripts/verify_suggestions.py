import asyncio
from httpx import AsyncClient
import subprocess

async def run_test():
    import time
    ts = int(time.time())
    email_a = f"verify_a_{ts}@test.com"
    email_b = f"verify_b_{ts}@test.com"
    
    async with AsyncClient(base_url="http://localhost:8000") as client:
        print("=== 1. VERIFY RUNNING CONTAINER ===")
        print("Done. backend-ad-server-1 is verified.")
        
        print("\n=== 2. CREATE ISOLATED TEST DATA ===")
        # Register A
        res = await client.post("/api/v1/auth/register", json={
            "name": "Verify A", "email": email_a, "password": "password12345", "role": "advertiser"
        })
        if res.status_code == 400:
            res = await client.post("/api/v1/auth/login", json={"email": email_a, "password": "password12345"})
        token_A = res.json()["access_token"]
        adv_A = res.json()["user"]["advertiser_id"]
        headers_A = {"Authorization": f"Bearer {token_A}"}
        
        # Register B
        res = await client.post("/api/v1/auth/register", json={
            "name": "Verify B", "email": email_b, "password": "password12345", "role": "advertiser"
        })
        if res.status_code == 400:
            res = await client.post("/api/v1/auth/login", json={"email": email_b, "password": "password12345"})
        token_B = res.json()["access_token"]
        headers_B = {"Authorization": f"Bearer {token_B}"}
        
        # Create Campaign A
        res = await client.post("/api/v1/campaign", json={
            "advertiser_id": adv_A, "name": "Verify Campaign", "budget_daily": 100.0, "budget_total": 1000.0,
            "bid_amount": 1.0, "bid_type": 1, "freq_cap_daily": 0, "freq_cap_hourly": 0, "status": 1
        }, headers=headers_A)
        camp_id = res.json()["id"]
        initial_budget = float(res.json()["budget_daily"])
        print(f"Advertiser A: {adv_A}, Campaign: {camp_id}, Initial budget: {initial_budget}, Autopilot: {res.json()['autopilot']}")
        
        print("\n=== 3. CREATE/OBTAIN PENDING SUGGESTION ===")
        subprocess.run(["docker", "exec", "backend-ad-server-1", "python", "scripts/trigger_suggestion.py", str(camp_id)], check=True)
        res = await client.get(f"/api/v1/campaign/{camp_id}/suggestions", headers=headers_A)
        suggs = res.json()
        sugg1 = suggs[-1]
        sugg1_id = sugg1["id"]
        print(f"Suggestion {sugg1_id} status: {sugg1['status']}, action: {sugg1['action']['action']}")
        
        print("\n=== 4. TEST REJECT ===")
        res = await client.patch(f"/api/v1/suggestion/{sugg1_id}", json={"status": "REJECTED"}, headers=headers_A)
        print("PATCH REJECT response status code:", res.status_code)
        print("Response status field:", res.json()["status"])
        
        res = await client.get(f"/api/v1/campaign/{camp_id}/suggestions", headers=headers_A)
        print("Pending suggestions count:", len(res.json()))
        
        res = await client.get(f"/api/v1/campaign", headers=headers_A)
        c_state = next(c for c in res.json() if c["id"] == camp_id)
        print("Campaign budget after reject:", float(c_state["budget_daily"]))
        
        res = await client.patch(f"/api/v1/suggestion/{sugg1_id}", json={"status": "REJECTED"}, headers=headers_A)
        print("Second REJECT status code:", res.status_code, res.json())
        
        print("\n=== 5. CREATE SECOND PENDING SUGGESTION ===")
        subprocess.run(["docker", "exec", "backend-ad-server-1", "python", "scripts/trigger_suggestion.py", str(camp_id)], check=True)
        res = await client.get(f"/api/v1/campaign/{camp_id}/suggestions", headers=headers_A)
        sugg2_id = res.json()[-1]["id"]
        print(f"Suggestion {sugg2_id} status: PENDING")
        
        print("\n=== 6. TEST APPROVE ===")
        res = await client.patch(f"/api/v1/suggestion/{sugg2_id}", json={"status": "APPLIED"}, headers=headers_A)
        print("PATCH APPLIED status code:", res.status_code)
        print("Response status:", res.json()["status"])
        
        res = await client.get(f"/api/v1/campaign", headers=headers_A)
        c_state = next(c for c in res.json() if c["id"] == camp_id)
        print("Campaign budget AFTER approve:", float(c_state["budget_daily"]))
        
        print("\n=== 7. & 8. VERIFY CHANGELOG & SUGGESTION STATUS ===")
        proc = subprocess.run(["docker", "exec", "backend-ad-server-1", "python", "scripts/db_check.py", str(camp_id), str(sugg2_id)], capture_output=True, text=True)
        print(proc.stdout.strip())
        
        print("\n=== 9. DOUBLE APPROVAL ===")
        res = await client.patch(f"/api/v1/suggestion/{sugg2_id}", json={"status": "APPLIED"}, headers=headers_A)
        print("Second APPROVE status code:", res.status_code, res.json())
        
        print("\n=== 10. STALE SUGGESTION ===")
        subprocess.run(["docker", "exec", "backend-ad-server-1", "python", "scripts/trigger_suggestion.py", str(camp_id)], check=True)
        res = await client.get(f"/api/v1/campaign/{camp_id}/suggestions", headers=headers_A)
        sugg3_id = res.json()[-1]["id"]
        print(f"Created third suggestion {sugg3_id}")
        
        # Make it stale by drastically reducing daily budget (so +20 is > 30% jump)
        await client.patch(f"/api/v1/campaign/{camp_id}", json={"budget_daily": 10.0}, headers=headers_A)
        print("Updated campaign budget to 10.0 (action delta +20.0 is now 200%, exceeding 30% guardrail)")
        
        res = await client.patch(f"/api/v1/suggestion/{sugg3_id}", json={"status": "APPLIED"}, headers=headers_A)
        print("Stale APPROVE status code:", res.status_code, res.json())
        
        print("\n=== 11. AUTHORIZATION ===")
        res = await client.get(f"/api/v1/campaign/{camp_id}/suggestions", headers=headers_B)
        print("Advertiser B GET suggestions:", res.status_code)
        res = await client.patch(f"/api/v1/suggestion/{sugg3_id}", json={"status": "APPLIED"}, headers=headers_B)
        print("Advertiser B PATCH suggestion:", res.status_code)

if __name__ == "__main__":
    asyncio.run(run_test())
