import pytest
from unittest.mock import patch, MagicMock

from liteads.ad_server.services.sector_classifier import classify

def test_classify_empty():
    assert classify("") == []
    assert classify(None) == []

def test_classify_single_keyword():
    result = classify("Looking for a python developer")
    assert "developers" in result
    assert len(result) == 1

def test_classify_multiple_sectors():
    result = classify("I need a loan for a new car and some health insurance")
    assert "fintech_banking" in result
    assert "automotive" in result
    assert "healthcare" in result
    assert len(result) >= 3

def test_classify_case_insensitive():
    result = classify("LOAN EMI BANK")
    assert "fintech_banking" in result

def test_classify_word_boundaries():
    # 'scar' contains 'car' but should not match 'automotive' unless 'scar' is a keyword
    result = classify("I have a scar on my leg")
    # Should not match automotive because \bcar\b is not present
    assert "automotive" not in result
