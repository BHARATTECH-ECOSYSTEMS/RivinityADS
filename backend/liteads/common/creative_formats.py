"""Ad creative category and format definitions and validation.

Supported Categories & Formats:
1. AI and Chatbot Ads ('ai_chatbot'):
   - Display Card ('display_card', creative_type=5): 520 × 120 px
   - Label Ads ('label_ad', creative_type=6): 400 × 100 px
   - Element Ads ('element_ad', creative_type=7): 310 × 150 px

2. Banner Ads ('banner'):
   - Banner Ad ('banner', creative_type=1): 500 × 500 px
"""

from typing import Any
from liteads.models.base import CreativeType

CATEGORY_AI_CHATBOT = "ai_chatbot"
CATEGORY_BANNER = "banner"

SUPPORTED_AD_CATEGORIES = {
    CATEGORY_AI_CHATBOT: "AI and Chatbot Ads",
    CATEGORY_BANNER: "Banner Ads",
}

SUPPORTED_FORMATS: dict[str, dict[str, Any]] = {
    "display_card": {
        "category": CATEGORY_AI_CHATBOT,
        "category_label": "AI and Chatbot Ads",
        "format": "display_card",
        "format_label": "Display Card",
        "creative_type": CreativeType.DISPLAY_CARD.value,
        "width": 520,
        "height": 120,
    },
    "label_ad": {
        "category": CATEGORY_AI_CHATBOT,
        "category_label": "AI and Chatbot Ads",
        "format": "label_ad",
        "format_label": "Label Ads",
        "creative_type": CreativeType.LABEL_AD.value,
        "width": 400,
        "height": 100,
    },
    "element_ad": {
        "category": CATEGORY_AI_CHATBOT,
        "category_label": "AI and Chatbot Ads",
        "format": "element_ad",
        "format_label": "Element Ads",
        "creative_type": CreativeType.ELEMENT_AD.value,
        "width": 310,
        "height": 150,
    },
    "banner": {
        "category": CATEGORY_BANNER,
        "category_label": "Banner Ads",
        "format": "banner",
        "format_label": "Banner Ad",
        "creative_type": CreativeType.BANNER.value,
        "width": 500,
        "height": 500,
    },
}

TYPE_TO_FORMAT: dict[int, str] = {
    CreativeType.BANNER.value: "banner",
    CreativeType.DISPLAY_CARD.value: "display_card",
    CreativeType.LABEL_AD.value: "label_ad",
    CreativeType.ELEMENT_AD.value: "element_ad",
}

LEGACY_BANNER_SIZES = {
    (300, 250),
    (728, 90),
    (320, 50),
}


def normalize_category(cat: str | None) -> str | None:
    if not cat:
        return None
    c = cat.strip().lower()
    if c in ("ai and chatbot ads", "ai_chatbot", "chatbot", "ai"):
        return CATEGORY_AI_CHATBOT
    if c in ("banner ads", "banner", "banners"):
        return CATEGORY_BANNER
    return cat.strip()


def normalize_format(fmt: str | None) -> str | None:
    if not fmt:
        return None
    f = fmt.strip().lower().replace(" ", "_").replace("-", "_")
    if f in ("display_card", "displaycard", "display_card_(520_×_120)", "display_card_(520x120)"):
        return "display_card"
    if f in ("label_ads", "label_ad", "labelads", "label", "label_ads_(400_×_100)", "label_ads_(400x100)"):
        return "label_ad"
    if f in ("element_ads", "element_ad", "elementads", "element", "element_ads_(310_×_150)", "element_ads_(310x150)"):
        return "element_ad"
    if f in ("banner", "banner_ad", "banner_ads", "banner_ad_(500_×_500)", "banner_ad_(500x500)"):
        return "banner"
    return f


def validate_and_normalize_creative_format(
    category: str | None,
    format_name: str | None,
    creative_type: int | None,
    width: int | None,
    height: int | None,
    is_new: bool = True,
) -> tuple[str, str, int, int, int]:
    """Validate and normalize creative category, format, creative_type, width, and height.

    Returns:
        tuple of (category, format, creative_type, width, height)
    Raises:
        ValueError if invalid format, category, or mismatched dimensions.
    """
    norm_cat = normalize_category(category)
    norm_fmt = normalize_format(format_name)

    # 1. If explicit category or format was given
    if norm_cat or norm_fmt:
        # If category is provided, validate it
        if norm_cat and norm_cat not in SUPPORTED_AD_CATEGORIES:
            raise ValueError(
                f"Invalid ad category '{category}'. Permitted categories are: "
                f"{', '.join(SUPPORTED_AD_CATEGORIES.values())}"
            )

        # If category is AI and Chatbot, subcategory is mandatory
        if norm_cat == CATEGORY_AI_CHATBOT and not norm_fmt:
            raise ValueError("Subcategory is required when 'AI and Chatbot Ads' is selected.")

        # If banner category is selected, format is automatically banner
        if norm_cat == CATEGORY_BANNER and not norm_fmt:
            norm_fmt = "banner"

        # Validate format
        if norm_fmt not in SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported format '{format_name}'. Supported formats are: "
                f"{', '.join(f['format_label'] for f in SUPPORTED_FORMATS.values())}"
            )

        spec = SUPPORTED_FORMATS[norm_fmt]

        # Verify category/format compatibility
        if norm_cat and spec["category"] != norm_cat:
            raise ValueError(
                f"Format '{spec['format_label']}' belongs to category "
                f"'{SUPPORTED_AD_CATEGORIES[spec['category']]}', not '{SUPPORTED_AD_CATEGORIES.get(norm_cat, norm_cat)}'"
            )

        norm_cat = spec["category"]
        assigned_type = spec["creative_type"]
        assigned_w = spec["width"]
        assigned_h = spec["height"]

        # Validate dimensions if provided
        if width is not None and width > 0 and width != assigned_w:
            raise ValueError(
                f"Invalid width for {spec['format_label']}. Must be exactly {assigned_w} px (got {width})."
            )
        if height is not None and height > 0 and height != assigned_h:
            raise ValueError(
                f"Invalid height for {spec['format_label']}. Must be exactly {assigned_h} px (got {height})."
            )

        return norm_cat, norm_fmt, assigned_type, assigned_w, assigned_h

    # 2. If format was not explicitly given, derive from creative_type
    ctype = creative_type if creative_type is not None else CreativeType.BANNER.value

    if ctype in TYPE_TO_FORMAT:
        fmt_key = TYPE_TO_FORMAT[ctype]
        spec = SUPPORTED_FORMATS[fmt_key]
        assigned_w = spec["width"]
        assigned_h = spec["height"]

        w = width or 0
        h = height or 0

        # Special backwards-compatibility handling for creative_type == 1 (BANNER)
        if ctype == CreativeType.BANNER.value:
            if (w == 0 and h == 0) or (w == 500 and h == 500):
                return spec["category"], spec["format"], ctype, 500, 500
            if (w, h) in LEGACY_BANNER_SIZES:
                # Legacy banner sizes permitted for backwards compatibility with historical records/tests
                return "legacy", "legacy_banner", ctype, w, h
            # Arbitrary dimension submitted for Banner Ad
            raise ValueError(
                f"Banner Ad format requires fixed dimensions of 500 × 500 px (got {w} × {h})."
            )

        # AI and Chatbot formats (5, 6, 7) must have exact dimensions
        if w > 0 and w != assigned_w:
            raise ValueError(
                f"Invalid width for {spec['format_label']}. Must be exactly {assigned_w} px (got {w})."
            )
        if h > 0 and h != assigned_h:
            raise ValueError(
                f"Invalid height for {spec['format_label']}. Must be exactly {assigned_h} px (got {h})."
            )

        return spec["category"], spec["format"], ctype, assigned_w, assigned_h

    # 3. Legacy formats (creative_type 2, 3, 4)
    if ctype in (CreativeType.NATIVE.value, CreativeType.VIDEO.value, CreativeType.INTERSTITIAL.value):
        w = width or 0
        h = height or 0
        return "legacy", f"legacy_type_{ctype}", ctype, w, h

    raise ValueError(f"Unsupported creative_type '{ctype}'.")

