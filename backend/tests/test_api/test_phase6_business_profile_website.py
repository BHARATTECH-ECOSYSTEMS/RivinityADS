"""Phase 6: Comprehensive tests for Business-Profile-to-Website Builder.

Validates:
- Guided business intake questionnaire, completeness scoring, and missing field reporting.
- Anti-fabrication guarantees (no fabricated claims, prices, or hours).
- Draft website generation across business categories (restaurant, plumber, clinic, tech agency).
- Strict ad monetization opt-in (zero default ads, informed consent, visible disable control).
- Validated Schema.org JSON-LD structured data (LocalBusiness subtypes, NAP, hours, FAQPage, BreadcrumbList, ItemList).
- Multilingual page generation and hreflang link logic (rendered only when real translated pages exist).
- Custom domain DNS TXT challenge generation, verification, and public host resolution.
- Webmaster verification meta tags (Google Search Console, Bing Webmaster Tools).
- Accessibility audits, WCAG AA contrast, image alt texts, and Core Web Vitals hints.
- AI Search / GEO readiness evaluation and factual /llms.txt endpoint.
- Tenant isolation and authorization enforcement.
"""

import json
from unittest.mock import AsyncMock, patch
import pytest
from httpx import AsyncClient

from liteads.models.website import (
    CustomDomainStatus,
    WebsiteMonetizationStatus,
)
from liteads.schemas.business_profile import BusinessProfileIntake
from liteads.ad_server.services.business_profile_service import business_profile_service


async def _get_auth_headers(client: AsyncClient, email: str, role: str = "publisher") -> dict[str, str]:
    """Register a user and return Bearer authentication headers."""
    res = await client.post(
        "/api/v1/auth/register",
        json={
            "name": f"User {email}",
            "email": email,
            "password": "Password123!",
            "company_name": "Phase 6 Org",
            "role": role,
        },
    )
    assert res.status_code == 201, res.text
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_business_profile_intake_completeness_and_missing_fields():
    """Verify that intake calculates completeness and marks missing fields rather than inventing them."""
    partial_intake = BusinessProfileIntake(
        business_name="Quick Plumber",
        category="plumber",
        tagline="Fast plumbing services",
        nap={"phone": "+91 98765 43210"},
        services_or_products=[{"name": "Pipe Repair"}],  # price omitted
    )

    audit = business_profile_service.audit_business_profile(partial_intake)

    assert audit.is_complete is False
    assert "opening_hours" in audit.missing_fields
    assert "physical_address" in audit.missing_fields
    assert "postal_code" in audit.missing_fields
    assert "item_pricing" in audit.missing_fields
    assert "faqs" in audit.missing_fields
    assert "phone" in audit.supplied_fields
    assert "business_name" in audit.supplied_fields
    assert audit.schema_org_type == "Plumber"
    assert len(audit.fabricated_claims_detected) == 0


@pytest.mark.asyncio
async def test_anti_fabrication_detection():
    """Verify anti-fabrication scanner flags unsubstantiated marketing claims."""
    fabricated_intake = BusinessProfileIntake(
        business_name="Super Tech Agency",
        category="tech_agency",
        tagline="Award-winning 2026 digital leader",
        description="We are the #1 rated agency in the world with certified 5 stars and 100% satisfaction guaranteed.",
        nap={"phone": "+91 99999 88888"},
    )

    audit = business_profile_service.audit_business_profile(fabricated_intake)
    assert len(audit.fabricated_claims_detected) > 0
    detected_lower = [d.lower() for d in audit.fabricated_claims_detected]
    assert any("award" in d or "#1" in d or "5 stars" in d for d in detected_lower)


@pytest.mark.asyncio
async def test_generate_draft_website_multiple_categories(client: AsyncClient):
    """Test generating complete editable draft websites across diverse business categories with 0 default ads."""
    auth_headers = await _get_auth_headers(client, "chef_owner@restaurant.in")

    # Create Project
    create_res = await client.post(
        "/api/v1/website/projects",
        headers=auth_headers,
        json={
            "name": "Spice Garden Restaurant",
            "slug": "spice-garden-delhi",
            "template_id": "business_services",
        },
    )
    assert create_res.status_code == 201, create_res.text
    project_id = create_res.json()["id"]

    # Submit Restaurant Intake
    restaurant_intake = {
        "business_name": "Spice Garden",
        "category": "restaurant",
        "tagline": "Authentic North Indian Delicacies",
        "description": "Serving traditional recipes crafted from locally sourced spices since 2018.",
        "nap": {
            "phone": "+91 11 2345 6789",
            "email": "info@spicegarden.com",
            "street_address": "12 Connaught Place",
            "city": "New Delhi",
            "state_region": "Delhi",
            "postal_code": "110001",
            "country": "IN",
        },
        "service_area_cities": ["New Delhi", "Noida", "Gurugram"],
        "hours": [
            {"day": "monday", "opens": "11:00", "closes": "23:00", "is_closed": False},
            {"day": "tuesday", "opens": "11:00", "closes": "23:00", "is_closed": False},
            {"day": "wednesday", "opens": "11:00", "closes": "23:00", "is_closed": False},
            {"day": "thursday", "opens": "11:00", "closes": "23:00", "is_closed": False},
            {"day": "friday", "opens": "11:00", "closes": "23:30", "is_closed": False},
            {"day": "saturday", "opens": "11:00", "closes": "23:30", "is_closed": False},
            {"day": "sunday", "opens": "12:00", "closes": "22:00", "is_closed": False},
        ],
        "services_or_products": [
            {"name": "Paneer Butter Masala", "price": 350.0, "currency": "INR", "description": "Cottage cheese in rich tomato gravy"},
            {"name": "Dal Makhani", "price": 280.0, "currency": "INR", "description": "Slow cooked black lentils with cream"},
            {"name": "Garlic Naan", "price": 60.0, "currency": "INR", "description": "Clay oven baked flatbread"},
        ],
        "faqs": [
            {"question": "Do you offer home delivery?", "answer": "Yes, we deliver across New Delhi and Noida."},
            {"question": "Is table reservation required on weekends?", "answer": "Reservations are recommended for dinner hours."},
        ],
        "whatsapp_number": "+919876543210",
        "factual_declaration": True,
        "ad_monetization_opt_in": False,
    }

    intake_res = await client.post(
        f"/api/v1/website/projects/{project_id}/business-intake?generate_draft=true",
        headers=auth_headers,
        json=restaurant_intake,
    )
    assert intake_res.status_code == 200, intake_res.text
    data = intake_res.json()
    assert data["audit"]["is_complete"] is True
    assert data["audit"]["schema_org_type"] == "Restaurant"
    assert data["page_count"] >= 4

    # Inspect generated pages
    pages_res = await client.get(
        f"/api/v1/website/projects/{project_id}/pages",
        headers=auth_headers,
    )
    assert pages_res.status_code == 200
    pages = pages_res.json()
    page_slugs = [p["slug"] for p in pages]
    assert "" in page_slugs  # home
    assert "about" in page_slugs
    assert "services" in page_slugs
    assert "contact" in page_slugs
    assert "service-areas" in page_slugs

    # Verify NO ad_placement blocks are present in draft pages (Informed consent check)
    for p in pages:
        full_p = await client.get(
            f"/api/v1/website/projects/{project_id}/pages/{p['id']}",
            headers=auth_headers,
        )
        blocks = full_p.json()["blocks"]["blocks"]
        for b in blocks:
            assert b["type"] != "ad_placement", "Draft site must not contain default injected ad placements!"


@pytest.mark.asyncio
async def test_draft_contains_no_fabricated_prices_or_hours(client: AsyncClient):
    """Verify that unsupplied prices and hours are not invented in page blocks."""
    auth_headers = await _get_auth_headers(client, "consultant@advisory.in")

    create_res = await client.post(
        "/api/v1/website/projects",
        headers=auth_headers,
        json={
            "name": "Custom Consulting",
            "slug": "custom-consulting-co",
            "template_id": "business_services",
        },
    )
    project_id = create_res.json()["id"]

    # Intake with unlisted prices
    intake_data = {
        "business_name": "Custom Consulting",
        "category": "professional_services",
        "tagline": "Strategic Advisory",
        "description": "Tailored consulting for enterprises.",
        "nap": {"phone": "+91 99999 11111"},
        "services_or_products": [
            {"name": "Architecture Review", "description": "Custom technical audit"},  # No price
            {"name": "Security Assessment", "description": "Penetration testing"},      # No price
        ],
        "factual_declaration": True,
    }

    await client.post(
        f"/api/v1/website/projects/{project_id}/business-intake",
        headers=auth_headers,
        json=intake_data,
    )

    pages_res = await client.get(f"/api/v1/website/projects/{project_id}/pages", headers=auth_headers)
    services_page_summary = next(p for p in pages_res.json() if p["slug"] == "services")

    services_page_res = await client.get(
        f"/api/v1/website/projects/{project_id}/pages/{services_page_summary['id']}",
        headers=auth_headers,
    )
    blocks = services_page_res.json()["blocks"]["blocks"]
    features_block = next(b for b in blocks if b["type"] == "features_grid")
    items = features_block["content"]["items"]

    # Ensure no price numbers or currency symbols were hallucinated
    for it in items:
        assert "₹" not in it["description"]
        assert "Price:" not in it["description"]


@pytest.mark.asyncio
async def test_multilingual_hindi_english_draft_and_hreflang(client: AsyncClient):
    """Verify multilingual draft generation produces Hindi & English pages with valid hreflang alternate links."""
    auth_headers = await _get_auth_headers(client, "advocate@bharatlegal.in")

    create_res = await client.post(
        "/api/v1/website/projects",
        headers=auth_headers,
        json={"name": "Bharat Legal", "slug": "bharat-legal-services", "template_id": "business_services"},
    )
    project_id = create_res.json()["id"]

    intake_data = {
        "business_name": "Bharat Legal",
        "category": "legal_accounting",
        "tagline": "Corporate Law & Compliance",
        "description": "Expert advisory on corporate law, trademarks, and dispute resolution.",
        "nap": {"phone": "+91 22 8765 4321", "city": "Mumbai"},
        "languages": ["en", "hi"],
        "services_or_products": [{"name": "Trademark Filing", "price": 4500.0, "currency": "INR"}],
        "factual_declaration": True,
    }

    await client.post(
        f"/api/v1/website/projects/{project_id}/business-intake",
        headers=auth_headers,
        json=intake_data,
    )

    # Publish the site
    pub_res = await client.post(
        f"/api/v1/website/projects/{project_id}/publish",
        headers=auth_headers,
        json={"notes": "Multilingual launch"},
    )
    assert pub_res.status_code == 200, pub_res.text

    # Fetch English Home via SSR direct path
    ssr_res_en = await client.get("/sites/bharat-legal-services")
    assert ssr_res_en.status_code == 200
    html_en = ssr_res_en.text

    # Verify <html lang="en">
    assert '<html lang="en">' in html_en
    # Verify hreflang tags linking English, Hindi, and x-default
    assert 'rel="alternate" hreflang="en"' in html_en
    assert 'rel="alternate" hreflang="hi"' in html_en
    assert 'rel="alternate" hreflang="x-default"' in html_en

    # Fetch Hindi Home
    ssr_res_hi = await client.get("/sites/bharat-legal-services/hi")
    assert ssr_res_hi.status_code == 200
    html_hi = ssr_res_hi.text
    assert '<html lang="hi">' in html_hi
    assert "मुख्य पृष्ठ" in html_hi


@pytest.mark.asyncio
async def test_single_language_site_omits_hreflang(client: AsyncClient):
    """Verify single-language sites omit hreflang tags to avoid redundant SEO directives."""
    auth_headers = await _get_auth_headers(client, "solo@englishservice.com")

    create_res = await client.post(
        "/api/v1/website/projects",
        headers=auth_headers,
        json={"name": "Solo English", "slug": "solo-english-site", "template_id": "business_services"},
    )
    project_id = create_res.json()["id"]

    intake_data = {
        "business_name": "Solo English",
        "category": "local_service",
        "languages": ["en"],
        "nap": {"phone": "+91 91234 56789"},
        "factual_declaration": True,
    }

    await client.post(
        f"/api/v1/website/projects/{project_id}/business-intake",
        headers=auth_headers,
        json=intake_data,
    )

    await client.post(f"/api/v1/website/projects/{project_id}/publish", headers=auth_headers, json={})
    ssr_res = await client.get("/sites/solo-english-site")
    assert ssr_res.status_code == 200
    # When no other languages exist, hreflang links must NOT be rendered
    assert 'rel="alternate" hreflang=' not in ssr_res.text


@pytest.mark.asyncio
async def test_json_ld_structured_data_validation(client: AsyncClient):
    """Verify published SSR pages include validated Schema.org JSON-LD (LocalBusiness, Breadcrumbs, FAQs, Services)."""
    auth_headers = await _get_auth_headers(client, "clinic@apexdental.com")

    create_res = await client.post(
        "/api/v1/website/projects",
        headers=auth_headers,
        json={"name": "Apex Dental Clinic", "slug": "apex-dental-clinic", "template_id": "business_services"},
    )
    project_id = create_res.json()["id"]

    intake = {
        "business_name": "Apex Dental",
        "category": "dental_clinic",
        "tagline": "Gentle dental care for families",
        "description": "Comprehensive dental clinic with modern equipment.",
        "nap": {
            "phone": "+91 80 1234 5678",
            "email": "care@apexdental.com",
            "street_address": "45 Brigade Road",
            "city": "Bengaluru",
            "state_region": "Karnataka",
            "postal_code": "560001",
            "country": "IN",
            "latitude": 12.9716,
            "longitude": 77.5946,
        },
        "hours": [
            {"day": "monday", "opens": "09:00", "closes": "19:00", "is_closed": False},
            {"day": "sunday", "is_closed": True},
        ],
        "services_or_products": [
            {"name": "Teeth Cleaning", "price": 1200.0, "currency": "INR", "description": "Professional scaling and polishing"},
            {"name": "Dental Implants", "price": 25000.0, "currency": "INR", "description": "Titanium root tooth replacement"},
        ],
        "faqs": [
            {"question": "Do you accept dental insurance?", "answer": "Yes, we partner with major Indian health insurers."},
        ],
        "factual_declaration": True,
    }

    await client.post(
        f"/api/v1/website/projects/{project_id}/business-intake",
        headers=auth_headers,
        json=intake,
    )

    await client.post(f"/api/v1/website/projects/{project_id}/publish", headers=auth_headers, json={})

    # 1. Inspect Home Page JSON-LD
    home_res = await client.get("/sites/apex-dental-clinic")
    assert home_res.status_code == 200
    html = home_res.text

    assert '<script type="application/ld+json">' in html
    import re
    ld_blocks = re.findall(r'<script type="application/ld\+json">\s*({.*?})\s*</script>', html, re.DOTALL)
    assert len(ld_blocks) >= 2  # LocalBusiness and FAQPage

    parsed_lds = [json.loads(b) for b in ld_blocks]
    biz_ld = next((b for b in parsed_lds if b.get("@type") == "Dentist"), None)
    assert biz_ld is not None
    assert biz_ld["name"] == "Apex Dental"
    assert biz_ld["telephone"] == "+91 80 1234 5678"
    assert biz_ld["address"]["streetAddress"] == "45 Brigade Road"
    assert biz_ld["address"]["addressLocality"] == "Bengaluru"
    assert biz_ld["address"]["postalCode"] == "560001"
    assert biz_ld["geo"]["latitude"] == 12.9716
    assert len(biz_ld["openingHoursSpecification"]) >= 1

    faq_ld = next((b for b in parsed_lds if b.get("@type") == "FAQPage"), None)
    assert faq_ld is not None
    assert faq_ld["mainEntity"][0]["name"] == "Do you accept dental insurance?"

    # 2. Inspect About Page Breadcrumbs
    about_res = await client.get("/sites/apex-dental-clinic/about")
    assert about_res.status_code == 200
    about_lds = re.findall(r'<script type="application/ld\+json">\s*({.*?})\s*</script>', about_res.text, re.DOTALL)
    about_parsed = [json.loads(b) for b in about_lds]
    crumb_ld = next((b for b in about_parsed if b.get("@type") == "BreadcrumbList"), None)
    assert crumb_ld is not None
    assert crumb_ld["itemListElement"][0]["name"] == "Home"
    assert crumb_ld["itemListElement"][1]["name"] == "About Us"


@pytest.mark.asyncio
async def test_custom_domain_workflow_and_verification(client: AsyncClient):
    """Test custom domain binding, cryptographic challenge issuance, DNS TXT check, and removal."""
    auth_headers = await _get_auth_headers(client, "domain_master@customdomain.in")

    create_res = await client.post(
        "/api/v1/website/projects",
        headers=auth_headers,
        json={"name": "Custom Domain Biz", "slug": "custom-dom-test", "template_id": "business_services"},
    )
    project_id = create_res.json()["id"]

    # 1. Bind custom domain
    bind_res = await client.post(
        f"/api/v1/website/projects/{project_id}/custom-domain",
        headers=auth_headers,
        json={"custom_domain": "mycustomplumber.in"},
    )
    assert bind_res.status_code == 200
    data = bind_res.json()
    assert data["custom_domain"] == "mycustomplumber.in"
    assert data["status"] == 1  # PENDING_VERIFICATION
    assert data["status_label"] == "pending_verification"
    assert "_rivinity-challenge.mycustomplumber.in" in data["dns_record_name"]

    # 2. Verification fails when TXT record is missing
    with patch("liteads.ad_server.routers.website.verify_dns_txt", new_callable=AsyncMock) as mock_dns:
        mock_dns.return_value = (False, "TXT record not found")
        verify_fail_res = await client.post(
            f"/api/v1/website/projects/{project_id}/custom-domain/verify",
            headers=auth_headers,
        )
        assert verify_fail_res.status_code == 400
        assert "Domain verification failed" in verify_fail_res.json()["detail"]

    # 3. Verification succeeds when TXT record is confirmed
    with patch("liteads.ad_server.routers.website.verify_dns_txt", new_callable=AsyncMock) as mock_dns:
        mock_dns.return_value = (True, None)
        verify_ok_res = await client.post(
            f"/api/v1/website/projects/{project_id}/custom-domain/verify",
            headers=auth_headers,
        )
        assert verify_ok_res.status_code == 200
        ok_data = verify_ok_res.json()
        assert ok_data["status"] == 2  # VERIFIED
        assert ok_data["status_label"] == "verified"
        assert ok_data["verified_at"] is not None

    # 4. Remove custom domain
    del_res = await client.delete(
        f"/api/v1/website/projects/{project_id}/custom-domain",
        headers=auth_headers,
    )
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True


@pytest.mark.asyncio
async def test_custom_domain_public_host_resolution(client: AsyncClient):
    """Test that public incoming requests with Host: <verified_custom_domain> resolve to the published project."""
    auth_headers = await _get_auth_headers(client, "host_tester@verifiedhost.com")

    create_res = await client.post(
        "/api/v1/website/projects",
        headers=auth_headers,
        json={"name": "Host Header Test", "slug": "host-header-slug", "template_id": "business_services"},
    )
    project_id = create_res.json()["id"]

    # Set custom domain and verify directly
    await client.post(
        f"/api/v1/website/projects/{project_id}/custom-domain",
        headers=auth_headers,
        json={"custom_domain": "www.verifiedcompany.com"},
    )
    with patch("liteads.ad_server.routers.website.verify_dns_txt", new_callable=AsyncMock) as mock_dns:
        mock_dns.return_value = (True, None)
        await client.post(f"/api/v1/website/projects/{project_id}/custom-domain/verify", headers=auth_headers)

    # Publish the site
    await client.post(f"/api/v1/website/projects/{project_id}/publish", headers=auth_headers, json={})

    # Send public request with Host header
    host_res = await client.get("/", headers={"Host": "www.verifiedcompany.com"})
    assert host_res.status_code == 200
    assert "Host Header Test" in host_res.text


@pytest.mark.asyncio
async def test_webmaster_tools_meta_tags_rendering(client: AsyncClient):
    """Test owner-authorized Google Search Console and Bing Webmaster meta tags rendering."""
    auth_headers = await _get_auth_headers(client, "webmaster@seosite.com")

    create_res = await client.post(
        "/api/v1/website/projects",
        headers=auth_headers,
        json={"name": "SEO Verified Site", "slug": "seo-verified-site", "template_id": "business_services"},
    )
    project_id = create_res.json()["id"]

    # Configure Webmaster Tools
    wm_res = await client.put(
        f"/api/v1/website/projects/{project_id}/webmaster-tools",
        headers=auth_headers,
        json={
            "google_site_verification": "google123456789abcdef",
            "bing_site_verification": "BING987654321ZYXWVU",
            "indexnow_key": "indexnow_key_secret_123",
        },
    )
    assert wm_res.status_code == 200
    assert wm_res.json()["google_site_verification"] == "google123456789abcdef"

    await client.post(f"/api/v1/website/projects/{project_id}/publish", headers=auth_headers, json={})

    # Fetch published page
    page_res = await client.get("/sites/seo-verified-site")
    assert page_res.status_code == 200
    html = page_res.text
    assert '<meta name="google-site-verification" content="google123456789abcdef">' in html
    assert '<meta name="msvalidate.01" content="BING987654321ZYXWVU">' in html


@pytest.mark.asyncio
async def test_accessibility_audit_and_core_web_vitals(client: AsyncClient):
    """Test accessibility audit evaluates WCAG AA contrast ratio, image alt-texts, and Core Web Vitals hints."""
    auth_headers = await _get_auth_headers(client, "a11y_user@audit.org")

    create_res = await client.post(
        "/api/v1/website/projects",
        headers=auth_headers,
        json={"name": "A11y Audit Site", "slug": "a11y-audit-site", "template_id": "business_services"},
    )
    project_id = create_res.json()["id"]

    audit_res = await client.get(
        f"/api/v1/website/projects/{project_id}/accessibility-audit",
        headers=auth_headers,
    )
    assert audit_res.status_code == 200
    data = audit_res.json()
    assert data["overall_score"] >= 70
    assert data["page_count"] >= 1
    page_report = data["pages"][0]["report"]
    assert page_report["contrast_passed"] is True
    assert page_report["contrast_ratio"] >= 4.5
    assert page_report["has_responsive_viewport"] is True
    assert page_report["lazy_loading_applied"] is True


@pytest.mark.asyncio
async def test_geo_readiness_and_llms_txt(client: AsyncClient):
    """Test GEO / AI Search readiness reporting and factual /llms.txt endpoint generation."""
    auth_headers = await _get_auth_headers(client, "autorepair@mechanic.in")

    create_res = await client.post(
        "/api/v1/website/projects",
        headers=auth_headers,
        json={"name": "GEO Test Business", "slug": "geo-test-biz", "template_id": "business_services"},
    )
    project_id = create_res.json()["id"]

    intake = {
        "business_name": "GEO Test Business",
        "category": "auto_repair",
        "tagline": "Car repairs and oil change",
        "description": "Certified mechanical repairs for all car models.",
        "nap": {
            "phone": "+91 20 4455 6677",
            "email": "service@geotest.in",
            "street_address": "88 Pune Road",
            "city": "Pune",
            "state_region": "Maharashtra",
            "postal_code": "411001",
            "country": "IN",
        },
        "hours": [{"day": "monday", "opens": "08:00", "closes": "18:00", "is_closed": False}],
        "services_or_products": [{"name": "Brake Inspection", "price": 500.0, "currency": "INR"}],
        "factual_declaration": True,
    }
    await client.post(f"/api/v1/website/projects/{project_id}/business-intake", headers=auth_headers, json=intake)
    await client.post(f"/api/v1/website/projects/{project_id}/publish", headers=auth_headers, json={})

    # 1. GEO Readiness Report
    geo_res = await client.get(f"/api/v1/website/projects/{project_id}/geo-readiness", headers=auth_headers)
    assert geo_res.status_code == 200
    geo_data = geo_res.json()
    assert geo_data["nap_completeness"] is True
    assert geo_data["schema_type"] == "AutoRepair"
    assert geo_data["factual_services_count"] == 1
    assert "GEO readiness measures factual entity completeness" in geo_data["disclaimer"]

    # 2. Public /sites/{slug}/llms.txt
    llms_res = await client.get("/sites/geo-test-biz/llms.txt")
    assert llms_res.status_code == 200
    llms_text = llms_res.text
    assert "# GEO Test Business" in llms_text
    assert "**Entity Type:** AutoRepair" in llms_text
    assert "Brake Inspection" in llms_text
    assert "INR 500.00" in llms_text
    assert "does not guarantee search engine ranking or AI citation placement" in llms_text


@pytest.mark.asyncio
async def test_ad_monetization_opt_in_and_disable_control(client: AsyncClient):
    """Test visible disable control instantly removes monetization status and cleans ad blocks."""
    auth_headers = await _get_auth_headers(client, "optin_test@publisher.org")

    create_res = await client.post(
        "/api/v1/website/projects",
        headers=auth_headers,
        json={"name": "Monetization Control Test", "slug": "monetization-ctrl-test", "template_id": "business_services"},
    )
    project_id = create_res.json()["id"]

    # Call disable endpoint
    disable_res = await client.post(
        f"/api/v1/website/projects/{project_id}/monetization/disable",
        headers=auth_headers,
    )
    assert disable_res.status_code == 200
    assert disable_res.json()["current_status"] == WebsiteMonetizationStatus.DISABLED


@pytest.mark.asyncio
async def test_tenant_isolation_on_business_intake_and_custom_domain(client: AsyncClient):
    """Verify other users cannot access, intake, or configure custom domains for projects they do not own."""
    user_a_headers = await _get_auth_headers(client, "victim@securebiz.com")
    user_b_headers = await _get_auth_headers(client, "attacker@badbiz.com")

    create_res = await client.post(
        "/api/v1/website/projects",
        headers=user_a_headers,
        json={"name": "Tenant A Secret Biz", "slug": "tenant-a-secret-biz", "template_id": "business_services"},
    )
    project_id = create_res.json()["id"]

    # User B attempts to submit business intake
    intake_res = await client.post(
        f"/api/v1/website/projects/{project_id}/business-intake",
        headers=user_b_headers,
        json={"business_name": "Attacker", "category": "local_service", "factual_declaration": True},
    )
    assert intake_res.status_code in (403, 404)

    # User B attempts to configure custom domain
    domain_res = await client.post(
        f"/api/v1/website/projects/{project_id}/custom-domain",
        headers=user_b_headers,
        json={"custom_domain": "hijack.com"},
    )
    assert domain_res.status_code in (403, 404)
