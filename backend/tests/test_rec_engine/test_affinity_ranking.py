import pytest

from liteads.schemas.internal import AdCandidate, UserContext
from liteads.rec_engine.ranking.reranker import AffinityReranker


def test_affinity_reranker_no_affinity():
    reranker = AffinityReranker()
    
    # Candidate 1 targets gaming
    c1 = AdCandidate(campaign_id=1, creative_id=1, advertiser_id=1, bid=1.0, bid_type=1, score=1.0, metadata={"sectors": ["gaming"]})
    # Candidate 2 targets education
    c2 = AdCandidate(campaign_id=2, creative_id=2, advertiser_id=1, bid=1.0, bid_type=1, score=1.0, metadata={"sectors": ["education"]})
    # Candidate 3 has no sector targeting
    c3 = AdCandidate(campaign_id=3, creative_id=3, advertiser_id=1, bid=1.0, bid_type=1, score=1.0)
    
    ctx = UserContext(user_id="user1", sector_affinities={}) # Empty affinities
    
    result = reranker.rerank([c1, c2, c3], ctx)
    
    # No affinities, scores should be unchanged
    assert result[0].score == 1.0
    assert result[1].score == 1.0
    assert result[2].score == 1.0


def test_affinity_reranker_with_affinity():
    reranker = AffinityReranker()
    
    # Base score 1.0 for both
    c1 = AdCandidate(campaign_id=1, creative_id=1, advertiser_id=1, bid=1.0, bid_type=1, score=1.0, metadata={"sectors": ["gaming"]})
    c2 = AdCandidate(campaign_id=2, creative_id=2, advertiser_id=1, bid=1.0, bid_type=1, score=1.0, metadata={"sectors": ["education"]})
    
    # High affinity for gaming (5.0), low for education (1.0)
    ctx = UserContext(user_id="user1", sector_affinities={"gaming": 5.0, "education": 1.0})
    
    result = reranker.rerank([c1, c2], ctx)
    
    # Gaming candidate should have highest score. Boost = 1.0 + (5.0/20.0) = 1.25
    gaming_cand = next(c for c in result if c.campaign_id == 1)
    # Education candidate. Boost = 1.0 + (1.0/20.0) = 1.05
    edu_cand = next(c for c in result if c.campaign_id == 2)
    
    assert gaming_cand.score == 1.25
    assert edu_cand.score == 1.05
    assert result[0].campaign_id == 1


def test_affinity_reranker_with_context_sectors():
    reranker = AffinityReranker()
    
    # Base score 1.0
    c1 = AdCandidate(campaign_id=1, creative_id=1, advertiser_id=1, bid=1.0, bid_type=1, score=1.0, metadata={"sectors": ["gaming"]})
    c2 = AdCandidate(campaign_id=2, creative_id=2, advertiser_id=1, bid=1.0, bid_type=1, score=1.0, metadata={"sectors": ["education"]})
    
    # Request comes from an education context
    ctx = UserContext(user_id="user1", sector_affinities={"gaming": 2.0}, sectors=["education"])
    
    result = reranker.rerank([c1, c2], ctx)
    
    gaming_cand = next(c for c in result if c.campaign_id == 1)
    edu_cand = next(c for c in result if c.campaign_id == 2)
    
    # Gaming gets affinity boost: 1.0 + 2.0/20.0 = 1.1
    assert gaming_cand.score == 1.1
    # Education gets context boost: 1.0 + 0.4 = 1.4
    assert edu_cand.score == 1.4
    
    # Education should win because context > small affinity
    assert result[0].campaign_id == 2

def test_affinity_reranker_cap():
    reranker = AffinityReranker()
    c1 = AdCandidate(campaign_id=1, creative_id=1, advertiser_id=1, bid=1.0, bid_type=1, score=1.0, metadata={"sectors": ["gaming"]})
    
    # Affinity 50.0 should cap at +0.5 boost
    ctx = UserContext(user_id="user1", sector_affinities={"gaming": 50.0})
    
    result = reranker.rerank([c1], ctx)
    assert result[0].score == 1.5
