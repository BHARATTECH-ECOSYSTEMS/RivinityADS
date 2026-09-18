"""
Keyword-based sector classification v1.

Replace with an LLM call or more advanced model when budget allows.
"""

import re

KEYWORDS = {
    "developers": r"\b(code|python|javascript|api|bug|compile|docker|kubernetes|sql|typescript)\b",
    "fintech_banking": r"\b(loan|emi|credit score|invest|mutual fund|stock|insurance|bank|finance)\b",
    "healthcare": r"\b(doctor|symptom|dose|diabetes|therapy|clinic|hospital|health|medical)\b",
    "education": r"\b(exam|course|admission|scholarship|study|degree|certification|school|college)\b",
    "ecommerce": r"\b(discount|coupon|deal|delivery|order|refund|sale|shop|buy)\b",
    "gaming": r"\b(game|esports|fps|rpg|multiplayer|steam|console|playstation|xbox)\b",
    "travel": r"\b(flight|hotel|visa|trip|itinerary|booking|resort|vacation|travel)\b",
    "real_estate": r"\b(rent|property|apartment|mortgage|plot|broker|house|estate)\b",
    "automotive": r"\b(car|bike|mileage|ev|service|tyre|dealership|auto|vehicle)\b",
    "food_beverage": r"\b(food|restaurant|recipe|delivery|menu|drink|beverage|snack)\b",
    "fashion": r"\b(clothing|apparel|shoes|fashion|style|wear|accessories)\b",
    "sports": r"\b(cricket|football|tennis|match|tournament|sports|athlete|fitness)\b",
    "entertainment": r"\b(movie|music|streaming|cinema|show|entertainment|actor|concert)\b",
    "business_smb": r"\b(b2b|smb|startup|business|enterprise|company|corporate)\b",
    "marketing": r"\b(seo|ads|campaign|marketing|brand|agency|advertising)\b",
    "legal": r"\b(lawyer|attorney|court|legal|law|lawsuit|contract)\b",
    "agriculture": r"\b(farm|crop|agriculture|tractor|farmer|harvest|seeds)\b",
    "government": r"\b(gov|tax|scheme|policy|government|citizen|passport)\b",
    "crypto_web3": r"\b(crypto|bitcoin|eth|blockchain|web3|nft|token|wallet)\b",
    "jobs_careers": r"\b(job|resume|interview|career|salary|hiring|recruitment|vacancy)\b",
    "parenting": r"\b(baby|kids|parenting|child|toddler|mom|dad|family)\b"
}

def classify(text: str) -> list[str]:
    """
    Classify a given text into sectors using keyword matching.
    
    Args:
        text: The input text to classify.
        
    Returns:
        List of matching sector strings.
    """
    if not text:
        return []
        
    t = text.lower()
    return [sec for sec, pat in KEYWORDS.items() if re.search(pat, t)]
