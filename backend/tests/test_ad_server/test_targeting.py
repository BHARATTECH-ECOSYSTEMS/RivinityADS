import pytest

from liteads.ad_server.services.targeting import match_geo, match_sector
from liteads.schemas.internal import UserContext


def test_match_geo_include_country():
    rule = {"entries": [{"country": "IN"}, {"country": "US"}]}
    
    # Matches IN
    ctx = UserContext(country="IN")
    assert match_geo(rule, ctx) is True
    
    # Matches US
    ctx = UserContext(country="us")
    assert match_geo(rule, ctx) is True
    
    # Does not match UK
    ctx = UserContext(country="UK")
    assert match_geo(rule, ctx) is False
    
    # Missing country -> False
    ctx = UserContext()
    assert match_geo(rule, ctx) is False


def test_match_geo_include_city():
    rule = {"entries": [{"country": "IN", "state": "MH", "city": "Mumbai"}]}
    
    # Matches exact
    ctx = UserContext(country="IN", region="MH", city="Mumbai")
    assert match_geo(rule, ctx) is True
    
    # Wrong city
    ctx = UserContext(country="IN", region="MH", city="Pune")
    assert match_geo(rule, ctx) is False
    
    # Missing city in context but specified in rule
    ctx = UserContext(country="IN", region="MH")
    assert match_geo(rule, ctx) is False


def test_match_geo_include_pincode():
    rule = {"entries": [{"country": "IN", "pincode": "400001"}]}
    
    # Matches
    ctx = UserContext(country="IN", pincode="400001")
    assert match_geo(rule, ctx) is True
    
    # Wrong pincode
    ctx = UserContext(country="IN", pincode="400002")
    assert match_geo(rule, ctx) is False


def test_match_sector_include():
    rule = {"sectors": ["fintech", "ecommerce"]}
    
    # Match one
    ctx = UserContext(sectors=["fintech", "banking"])
    assert match_sector(rule, ctx) is True
    
    # Match none
    ctx = UserContext(sectors=["healthcare", "education"])
    assert match_sector(rule, ctx) is False
    
    # Empty user sectors -> False if rule requires something
    ctx = UserContext()
    assert match_sector(rule, ctx) is False


def test_match_sector_empty_rule():
    rule = {"sectors": []}
    ctx = UserContext(sectors=["fintech"])
    # Empty rule means unrestricted
    assert match_sector(rule, ctx) is True
