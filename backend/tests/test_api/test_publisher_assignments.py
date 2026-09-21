import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.models import Advertiser, Campaign, CampaignAssignment, Publisher, AdSlot, User
from liteads.ad_server.main import app


@pytest.fixture
async def setup_data(test_db: AsyncSession):
    # Create advertisers
    adv1 = Advertiser(id=1, name="Adv 1", balance=1000)
    adv2 = Advertiser(id=2, name="Adv 2", balance=1000)
    test_db.add_all([adv1, adv2])
    
    # Create publishers
    pub1 = Publisher(id=1, name="Pub 1")
    pub2 = Publisher(id=2, name="Pub 2")
    test_db.add_all([pub1, pub2])
    
    # Create slots
    slot1 = AdSlot(id=1, publisher_id=1, slot_id="slot_pub1", name="Slot 1")
    slot2 = AdSlot(id=2, publisher_id=2, slot_id="slot_pub2", name="Slot 2")
    test_db.add_all([slot1, slot2])
    
    # Create campaigns
    camp1 = Campaign(id=1, advertiser_id=1, name="Camp 1", status=1)
    camp2 = Campaign(id=2, advertiser_id=2, name="Camp 2", status=1)
    camp3 = Campaign(id=3, advertiser_id=1, name="Camp 3", status=1)
    test_db.add_all([camp1, camp2, camp3])
    
    await test_db.flush()
    
    # Create assignments
    assgn1 = CampaignAssignment(campaign_id=1, publisher_id=1, slot_id=None)
    assgn2 = CampaignAssignment(campaign_id=2, publisher_id=1, slot_id="slot_pub1")
    assgn3 = CampaignAssignment(campaign_id=3, publisher_id=2, slot_id=None)
    
    test_db.add_all([assgn1, assgn2, assgn3])
    await test_db.commit()


def override_user(publisher_id: int | None = None, advertiser_id: int | None = None, role: str = "publisher"):
    from liteads.common.security import current_user
    async def mock_user():
        return User(id=1, role=role, publisher_id=publisher_id, advertiser_id=advertiser_id, email="test@test.com")
    app.dependency_overrides[current_user] = mock_user

def reset_user():
    from liteads.common.security import current_user
    app.dependency_overrides.pop(current_user, None)


@pytest.mark.asyncio
async def test_publisher_assignment_listing(client: AsyncClient, setup_data: None):
    override_user(publisher_id=1)
    response = await client.get("/api/v1/publisher/assignments")
    reset_user()
    
    assert response.status_code == 200, response.text
    data = response.json()
    
    assert len(data) == 2
    
    # Validate structure and content
    camp1_assgn = next(a for a in data if a["campaign_id"] == 1)
    assert camp1_assgn["campaign_name"] == "Camp 1"
    assert camp1_assgn["advertiser_name"] == "Adv 1"
    assert camp1_assgn["slot_id"] is None
    assert camp1_assgn["slot_name"] is None
    assert camp1_assgn["status"] == "Active"
    
    camp2_assgn = next(a for a in data if a["campaign_id"] == 2)
    assert camp2_assgn["campaign_name"] == "Camp 2"
    assert camp2_assgn["advertiser_name"] == "Adv 2"
    assert camp2_assgn["slot_id"] == "slot_pub1"
    assert camp2_assgn["slot_name"] == "Slot 1"
    assert camp2_assgn["status"] == "Active"


@pytest.mark.asyncio
async def test_publisher_isolation(client: AsyncClient, setup_data: None):
    override_user(publisher_id=2)
    response = await client.get("/api/v1/publisher/assignments")
    reset_user()
    
    assert response.status_code == 200
    data = response.json()
    
    assert len(data) == 1
    assert data[0]["campaign_id"] == 3
    assert data[0]["campaign_name"] == "Camp 3"
    assert data[0]["advertiser_name"] == "Adv 1"
    assert data[0]["slot_id"] is None


@pytest.mark.asyncio
async def test_non_publisher_access(client: AsyncClient, setup_data: None):
    override_user(advertiser_id=1, role="advertiser")
    response = await client.get("/api/v1/publisher/assignments")
    reset_user()
    
    assert response.status_code == 403
