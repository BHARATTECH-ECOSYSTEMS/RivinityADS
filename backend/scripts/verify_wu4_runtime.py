"""Real runtime verification script for Phase 5 Work Unit 4 (WU4).
Executes end-to-end against live PostgreSQL and Redis.
"""

import asyncio
import hashlib
import hmac
import time
from decimal import Decimal

from httpx import ASGITransport, AsyncClient

from liteads.ad_server.main import app
from liteads.common.cache import CacheKeys, redis_client
from liteads.common.database import db, init_db
from liteads.management.api_key_service import ApiKeyService
from liteads.models.ad import Advertiser, Campaign, Creative
from liteads.models.base import CreativeType, ModerationStatus, Status
from liteads.models.domain import AdvertiserDomain
from liteads.models.publisher import AdSlot, Publisher
from liteads.schemas.management import ApiKeyCreate


def make_hmac_headers(
    raw_key: str,
    body: bytes,
    method: str = "POST",
    path: str = "/api/v1/ad/request",
    version: str = "2",
) -> dict[str, str]:
    ts = str(int(time.time()))
    body_sha = hashlib.sha256(body).hexdigest()
    msg = f"{method.upper()}|{path}|{ts}|{body_sha}".encode("utf-8")
    sig = hmac.new(raw_key.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-API-Key": raw_key,
        "X-Timestamp": ts,
        "X-Signature": sig,
        "X-Signature-Version": version,
    }


async def run_live_verification():
    print("=" * 60)
    print("STARTING LIVE RUNTIME VERIFICATION FOR WU4")
    print("=" * 60)

    # 1. Connect to live DB and Redis
    await init_db()
    await redis_client.connect()
    assert redis_client.connected, "Redis must be connected"
    print("[OK] PostgreSQL and Redis connected successfully")

    # 2. Seed data in DB
    async with db.session() as session:
        try:
            # Publisher & Slots
            pub = Publisher(name="Runtime Live Pub", status=1)
            session.add(pub)
            await session.flush()

            chat_slot = AdSlot(
                publisher_id=pub.id,
                slot_id=f"rt_chat_{pub.id}",
                name="Runtime Chat Slot",
                format=1,
                status=1,
                is_chat_surface=True,
                width=300,
                height=250,
            )
            standard_slot = AdSlot(
                publisher_id=pub.id,
                slot_id=f"rt_std_{pub.id}",
                name="Runtime Standard Slot",
                format=4,
                status=1,
                is_chat_surface=False,
                width=320,
                height=480,
            )
            session.add_all([chat_slot, standard_slot])
            await session.flush()

            # Advertiser & Domain
            adv = Advertiser(name="Runtime Live Adv", balance=Decimal("500.00"), daily_budget=Decimal("100.00"), status=1)
            session.add(adv)
            await session.flush()

            dom = AdvertiserDomain(advertiser_id=adv.id, domain="runtime.example.com", status="VERIFIED", verification_token="tok")
            session.add(dom)
            await session.flush()

            # Campaigns
            camp_banner = Campaign(
                advertiser_id=adv.id,
                name="Runtime Banner Camp",
                status=Status.ACTIVE,
                budget_daily=Decimal("50.00"),
                budget_total=Decimal("500.00"),
                bid_amount=Decimal("1.00"),
                bid_type=1,
                freq_cap_daily=2,
                freq_cap_hourly=5,
            )
            camp_interstitial = Campaign(
                advertiser_id=adv.id,
                name="Runtime Interstitial Camp",
                status=Status.ACTIVE,
                budget_daily=Decimal("50.00"),
                budget_total=Decimal("500.00"),
                bid_amount=Decimal("2.00"),
                bid_type=1,
                freq_cap_daily=5,
                freq_cap_hourly=5,
            )
            session.add_all([camp_banner, camp_interstitial])
            await session.flush()

            # Creatives
            cr_banner = Creative(
                campaign_id=camp_banner.id,
                title="Runtime Banner Ad",
                description="Live Runtime Banner",
                landing_url="https://runtime.example.com/banner",
                creative_type=1,
                width=300,
                height=250,
                status=Status.ACTIVE,
                moderation_status=ModerationStatus.APPROVED.value,
            )
            cr_interstitial = Creative(
                campaign_id=camp_interstitial.id,
                title="Runtime Interstitial Ad",
                description="Live Runtime Interstitial",
                landing_url="https://runtime.example.com/interstitial",
                creative_type=4,
                width=320,
                height=480,
                status=Status.ACTIVE,
                moderation_status=ModerationStatus.APPROVED.value,
            )
            session.add_all([cr_banner, cr_interstitial])
            await session.flush()

            # Create API Key
            db_key, raw_key = await ApiKeyService.create_api_key(
                session,
                publisher_id=pub.id,
                schema=ApiKeyCreate(name="Runtime Key"),
            )
            await session.commit()
            print(f"[OK] Seeded test data for Publisher {pub.id}, Advertiser {adv.id}, Key: {db_key.key_prefix}...")

            # Run HTTP tests via ASGI transport
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # -------------------------------------------------------------
                # SCENARIO 1: Sponsored Disclosure & Client Override Suppression
                # -------------------------------------------------------------
                body1 = f'{{"slot_id": "{chat_slot.slot_id}", "num_ads": 1, "sponsored": false, "disclosure_label": "NOT_AN_AD"}}'.encode("utf-8")
                h1 = make_hmac_headers(raw_key, body1)
                r1 = await client.post("/api/v1/ad/request", content=body1, headers=h1)
                assert r1.status_code == 200, f"Expected 200, got {r1.status_code}: {r1.text}"
                ad1 = r1.json()["ads"][0]
                assert ad1["sponsored"] is True, "sponsored field must be True"
                assert ad1["disclosure_label"] == "Sponsored", f"Expected Sponsored label, got {ad1['disclosure_label']}"
                print("[OK] SCENARIO 1 PASSED: Server-controlled sponsored disclosure enforced; client override ignored")

                # -------------------------------------------------------------
                # SCENARIO 2: Chat Surface Interstitial Rejection
                # -------------------------------------------------------------
                body2 = f'{{"slot_id": "{chat_slot.slot_id}", "num_ads": 1, "format": 4}}'.encode("utf-8")
                h2 = make_hmac_headers(raw_key, body2)
                r2 = await client.post("/api/v1/ad/request", content=body2, headers=h2)
                assert r2.status_code == 200
                assert r2.json()["ads"] == [], f"Expected 0 ads on chat surface for interstitial format, got: {r2.json()['ads']}"
                print("[OK] SCENARIO 2 PASSED: Interstitial format strictly rejected on chat surface")

                # -------------------------------------------------------------
                # SCENARIO 3: Non-Chat Surface Interstitial Allowed
                # -------------------------------------------------------------
                body3 = f'{{"slot_id": "{standard_slot.slot_id}", "num_ads": 1, "format": 4}}'.encode("utf-8")
                h3 = make_hmac_headers(raw_key, body3)
                r3 = await client.post("/api/v1/ad/request", content=body3, headers=h3)
                assert r3.status_code == 200
                assert len(r3.json()["ads"]) >= 1, "Expected interstitial ad on non-chat surface"
                assert r3.json()["ads"][0]["creative"]["creative_type"] == "interstitial"
                print("[OK] SCENARIO 3 PASSED: Interstitial served on non-chat surface when valid")

                # -------------------------------------------------------------
                # SCENARIO 4: Anonymous Access Denied on Chat Surface (401)
                # -------------------------------------------------------------
                r4 = await client.post("/api/v1/ad/request", json={"slot_id": chat_slot.slot_id, "num_ads": 1})
                assert r4.status_code == 401, f"Expected 401 for anonymous chat request, got {r4.status_code}"
                print("[OK] SCENARIO 4 PASSED: Anonymous request to chat surface rejected with 401")

                # -------------------------------------------------------------
                # SCENARIO 5: Frequency Capping with 64-hex SHA-256 Hash
                # -------------------------------------------------------------
                test_hash = hashlib.sha256(b"unique_runtime_user").hexdigest()
                body5 = f'{{"slot_id": "{chat_slot.slot_id}", "num_ads": 1, "user": {{"id": "{test_hash.upper()}"}}}}'.encode("utf-8")
                h5 = make_hmac_headers(raw_key, body5)

                # Request 1: Served
                r5_1 = await client.post("/api/v1/ad/request", content=body5, headers=h5)
                assert r5_1.status_code == 200
                assert len(r5_1.json()["ads"]) == 1

                # Manually record 2 impressions for this user/campaign in Redis
                from liteads.rec_engine.filter.frequency import FrequencyFilter
                ff = FrequencyFilter()
                await ff.increment(user_id=test_hash, campaign_id=camp_banner.id, slot_id=chat_slot.slot_id)
                await ff.increment(user_id=test_hash, campaign_id=camp_banner.id, slot_id=chat_slot.slot_id)

                # Request 2: Blocked by frequency cap (cap is 2)
                r5_2 = await client.post("/api/v1/ad/request", content=body5, headers=h5)
                assert r5_2.status_code == 200
                returned_campaign_ids = [ad["campaign_id"] for ad in r5_2.json()["ads"]]
                assert camp_banner.id not in returned_campaign_ids, f"Campaign {camp_banner.id} must be filtered out by frequency cap!"
                print("[OK] SCENARIO 5 PASSED: Privacy-safe frequency cap blocked ad delivery at threshold")

                # -------------------------------------------------------------
                # SCENARIO 6: Malformed Hash Rejection (422)
                # -------------------------------------------------------------
                bad_body = f'{{"slot_id": "{chat_slot.slot_id}", "num_ads": 1, "user": {{"id": "invalid_not_64_hex"}}}}'.encode("utf-8")
                bad_h = make_hmac_headers(raw_key, bad_body)
                r6 = await client.post("/api/v1/ad/request", content=bad_body, headers=bad_h)
                assert r6.status_code == 422, f"Expected 422 for malformed hash, got {r6.status_code}"
                print("[OK] SCENARIO 6 PASSED: Malformed user hash consistently rejected with 422")

                # -------------------------------------------------------------
                # SCENARIO 7: Evidence that Blocked Ads Do NOT Bill Advertiser
                # -------------------------------------------------------------
                await session.refresh(adv)
                balance_before = adv.balance
                # Call with blocked frequency cap (returns no ads from this advertiser)
                r7 = await client.post("/api/v1/ad/request", content=body5, headers=h5)
                assert r7.status_code == 200
                assert camp_banner.id not in [ad["campaign_id"] for ad in r7.json()["ads"]]
                await session.refresh(adv)
                assert adv.balance == balance_before, f"Balance changed: {adv.balance} vs {balance_before}"
                print("[OK] SCENARIO 7 PASSED: Blocked requests do not bill or alter advertiser balance")

            print("=" * 60)
            print("ALL LIVE RUNTIME VERIFICATION SCENARIOS COMPLETED SUCCESSFULLY!")
            print("=" * 60)
        finally:
            await redis_client.close()


if __name__ == "__main__":
    asyncio.run(run_live_verification())
