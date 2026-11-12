"""Website builder templates catalog service and builtin templates (Phase 2).

Provides the 15 approved Open Design templates plus legacy compatibility templates,
with idempotent database seeding and catalog query helpers.
"""

from __future__ import annotations

from typing import Any
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.logger import get_logger
from liteads.models.website import WebsiteTemplate

logger = get_logger(__name__)

# ── Builtin 15 Open Design Templates + Legacy Compatibility ──────────────────

BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "saas-landing",
        "name": "SaaS & Product Landing",
        "description": "High-converting single-page SaaS landing with hero, features grid, social proof, pricing tiers, and call-to-action banner.",
        "category": "Landing & Marketing",
        "badge": "Popular",
        "thumbnail_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 200' fill='%23fafaf9'><rect width='320' height='200' fill='%23fafaf9'/><rect x='24' y='20' width='80' height='12' rx='3' fill='%23c96442'/><rect x='24' y='48' width='180' height='20' rx='4' fill='%231c1b1a'/><rect x='24' y='76' width='240' height='10' rx='2' fill='%236b6964'/><rect x='24' y='100' width='90' height='24' rx='6' fill='%23c96442'/><rect x='24' y='140' width='80' height='40' rx='6' fill='%23ffffff' stroke='%23e6e4e0'/><rect x='120' y='140' width='80' height='40' rx='6' fill='%23ffffff' stroke='%23e6e4e0'/><rect x='216' y='140' width='80' height='40' rx='6' fill='%23ffffff' stroke='%23c96442'/></svg>",
        "order_index": 1,
        "is_active": True,
        "default_theme_config": {
            "primary_color": "#c96442",
            "secondary_color": "#6b6964",
            "background_color": "#fafaf9",
            "text_color": "#1c1b1a",
            "font_family": "Inter",
            "border_radius": "8px",
        },
        "default_pages": [
            {
                "title": "Home",
                "slug": "",
                "is_home": True,
                "order_index": 0,
                "seo_meta": {
                    "meta_title": "High-Performance Software",
                    "meta_description": "Engineered for speed, security, and developer productivity.",
                },
                "blocks": {
                    "schema_version": 2,
                    "blocks": [
                        {
                            "id": "blk_saas_hero",
                            "type": "hero",
                            "is_visible": True,
                            "content": {
                                "headline": "File sync that doesn't eat your bandwidth",
                                "subheadline": "Block-level deltas, end-to-end zero knowledge encryption, and a predictable pricing model that scales with your growth.",
                                "alignment": "center",
                                "cta_button": {
                                    "label": "Start 14-Day Trial",
                                    "target_url": "/pricing",
                                    "style": "primary",
                                },
                            },
                        },
                        {
                            "id": "blk_saas_features",
                            "type": "features_grid",
                            "is_visible": True,
                            "content": {
                                "title": "Engineered for High-Scale Production",
                                "items": [
                                    {
                                        "title": "Bandwidth-Aware Deltas",
                                        "description": "Upload only changed binary chunks, reducing transfer payloads by up to 90%.",
                                        "icon": "zap",
                                    },
                                    {
                                        "title": "Zero-Knowledge Encryption",
                                        "description": "Client-side AES-256-GCM encryption ensures your proprietary assets remain private.",
                                        "icon": "shield",
                                    },
                                    {
                                        "title": "Edge Synchronization",
                                        "description": "Sub-millisecond latency routing across global regional presence clusters.",
                                        "icon": "globe",
                                    },
                                ],
                            },
                        },
                        {
                            "id": "blk_saas_ad_slot",
                            "type": "ad_placement",
                            "is_visible": True,
                            "content": {
                                "placement_location": "in_article",
                                "format": 1,
                                "ad_slot_id": None,
                            },
                        },
                        {
                            "id": "blk_saas_pricing",
                            "type": "open_design_section",
                            "is_visible": True,
                            "content": {
                                "section_id": "pricing",
                                "section_tag": "section",
                                "css_classes": "od-pricing-section",
                                "styles": {"padding": "3rem 1.5rem", "text-align": "center"},
                                "html_content": '<div class="od-pricing-wrap"><h2>Transparent, Predictable Tiers</h2><p>Scale effortlessly without surprise overage penalties.</p><div class="od-tiers-row"><div class="od-tier-card"><span class="od-plan-title">Starter</span><p class="od-plan-price">$19/mo</p><ul><li>Up to 5 seats</li><li>100 GB fast cache</li><li>Community support</li></ul></div><div class="od-tier-card od-featured"><span class="od-plan-title">Professional</span><p class="od-plan-price">$49/mo</p><ul><li>Unlimited team seats</li><li>1 TB high-speed delta cache</li><li>24/7 dedicated SLAs</li></ul></div></div></div>',
                                "asset_references": [],
                            },
                        },
                        {
                            "id": "blk_saas_cta",
                            "type": "cta_banner",
                            "is_visible": True,
                            "content": {
                                "title": "Ready to accelerate your delivery cycle?",
                                "description": "Deploy in minutes with our developer-friendly APIs and SDKs.",
                                "button": {
                                    "label": "Get Started Today",
                                    "target_url": "/signup",
                                    "style": "primary",
                                },
                            },
                        },
                        {
                            "id": "blk_saas_footer",
                            "type": "footer",
                            "is_visible": True,
                            "content": {
                                "copyright_text": "© 2026 Filebase. Powered by RivinityADS",
                                "links": [
                                    {"label": "Privacy Policy", "target_url": "/privacy"},
                                    {"label": "Terms of Service", "target_url": "/terms"},
                                ],
                            },
                        },
                    ],
                },
            }
        ],
    },
    {
        "id": "pricing-page",
        "name": "Subscription & Pricing Table",
        "description": "Standalone pricing matrix with plan tiers, comprehensive feature checklist, and collapsible FAQ section.",
        "category": "Landing & Marketing",
        "badge": "Essential",
        "thumbnail_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 200' fill='%23ffffff'><rect width='320' height='200' fill='%23ffffff'/><rect x='80' y='20' width='160' height='16' rx='4' fill='%230f172a'/><rect x='60' y='44' width='200' height='10' rx='2' fill='%2364748b'/><rect x='20' y='70' width='85' height='100' rx='8' fill='%23f8fafc' stroke='%23e2e8f0'/><rect x='117' y='65' width='85' height='110' rx='8' fill='%23ffffff' stroke='%232563eb' stroke-width='2'/><rect x='214' y='70' width='85' height='100' rx='8' fill='%23f8fafc' stroke='%23e2e8f0'/></svg>",
        "order_index": 2,
        "is_active": True,
        "default_theme_config": {
            "primary_color": "#2563eb",
            "secondary_color": "#64748b",
            "background_color": "#ffffff",
            "text_color": "#0f172a",
            "font_family": "Inter",
            "border_radius": "8px",
        },
        "default_pages": [
            {
                "title": "Home",
                "slug": "",
                "is_home": True,
                "order_index": 0,
                "seo_meta": {
                    "meta_title": "Plans & Pricing",
                    "meta_description": "Straightforward subscription plans tailored for teams of all sizes.",
                },
                "blocks": {
                    "schema_version": 2,
                    "blocks": [
                        {
                            "id": "blk_price_hero",
                            "type": "hero",
                            "is_visible": True,
                            "content": {
                                "headline": "Simple, Predictable Pricing",
                                "subheadline": "Pick the tier that matches your current workload. Upgrade, downgrade, or cancel anytime with zero lock-in.",
                                "alignment": "center",
                            },
                        },
                        {
                            "id": "blk_price_matrix",
                            "type": "open_design_section",
                            "is_visible": True,
                            "content": {
                                "section_id": "tiers-matrix",
                                "section_tag": "section",
                                "css_classes": "od-pricing-matrix",
                                "styles": {"padding": "2rem 1rem"},
                                "html_content": '<div class="od-container"><div class="od-pricing-grid"><div class="od-plan-box"><h3>Free Community</h3><p class="od-price">$0</p><p class="od-desc">Forever free for personal experiments.</p><hr/><ul><li>Single workspace</li><li>Standard queue priority</li><li>Community forum access</li></ul></div><div class="od-plan-box od-featured-box"><h3>Growth</h3><p class="od-price">$39/mo</p><p class="od-desc">Ideal for growing startups and agencies.</p><hr/><ul><li>Up to 10 workspaces</li><li>High-throughput pipeline</li><li>Custom domain mappings</li><li>Email support</li></ul></div><div class="od-plan-box"><h3>Enterprise</h3><p class="od-price">Custom</p><p class="od-desc">Dedicated infrastructure and compliance.</p><hr/><ul><li>Unlimited seats and domains</li><li>99.99% uptime guarantee</li><li>Dedicated account architect</li></ul></div></div></div>',
                                "asset_references": [],
                            },
                        },
                        {
                            "id": "blk_price_ad",
                            "type": "ad_placement",
                            "is_visible": True,
                            "content": {
                                "placement_location": "in_article",
                                "format": 1,
                                "ad_slot_id": None,
                            },
                        },
                        {
                            "id": "blk_price_faq",
                            "type": "text_section",
                            "is_visible": True,
                            "content": {
                                "heading": "Frequently Asked Questions",
                                "body": "How does billing work?\nSubscriptions are billed monthly or annually. You can change plans at any time and your billing will be prorated automatically.\n\nCan I request a refund?\nYes, we offer a full 14-day money-back guarantee if you are not completely satisfied with our service.\n\nDo you support custom invoices?\nYes, Enterprise tier subscriptions support corporate POs, wire transfers, and automated GST/VAT invoices.",
                            },
                        },
                        {
                            "id": "blk_price_footer",
                            "type": "footer",
                            "is_visible": True,
                            "content": {
                                "copyright_text": "© 2026 RivinityADS Pricing. All rights reserved.",
                            },
                        },
                    ],
                },
            }
        ],
    },
    {
        "id": "blog-post",
        "name": "Editorial & Publisher Article",
        "description": "Long-form editorial layout optimized for articles, magazines, and technical essays with masthead, byline, and pull quotes.",
        "category": "Publishing & Editorial",
        "badge": "Editorial",
        "thumbnail_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 200' fill='%23faf9f6'><rect width='320' height='200' fill='%23faf9f6'/><rect x='40' y='24' width='240' height='18' rx='3' fill='%231a1a1a'/><rect x='40' y='50' width='160' height='10' rx='2' fill='%23525252'/><rect x='40' y='72' width='240' height='8' rx='2' fill='%23525252'/><rect x='40' y='86' width='220' height='8' rx='2' fill='%23525252'/><rect x='40' y='108' width='240' height='36' rx='4' fill='%23e5e5e5'/><rect x='40' y='154' width='240' height='8' rx='2' fill='%23525252'/></svg>",
        "order_index": 3,
        "is_active": True,
        "default_theme_config": {
            "primary_color": "#1a1a1a",
            "secondary_color": "#525252",
            "background_color": "#faf9f6",
            "text_color": "#1a1a1a",
            "font_family": "Merriweather",
            "border_radius": "4px",
        },
        "default_pages": [
            {
                "title": "Home",
                "slug": "",
                "is_home": True,
                "order_index": 0,
                "seo_meta": {
                    "meta_title": "The Architecture of Modern Digital Advertising",
                    "meta_description": "An architectural deep-dive into edge-accelerated ad serving and deterministic accounting.",
                },
                "blocks": {
                    "schema_version": 2,
                    "blocks": [
                        {
                            "id": "blk_blog_hero",
                            "type": "hero",
                            "is_visible": True,
                            "content": {
                                "headline": "The Architecture of Modern Digital Advertising",
                                "subheadline": "How edge-accelerated routing, deterministic candidate scoring, and cryptographically signed tokens deliver sub-20ms impressions.",
                                "alignment": "left",
                            },
                        },
                        {
                            "id": "blk_blog_byline",
                            "type": "open_design_section",
                            "is_visible": True,
                            "content": {
                                "section_id": "byline-bar",
                                "section_tag": "div",
                                "css_classes": "od-byline-bar",
                                "styles": {"padding": "1rem 0", "margin-bottom": "2rem"},
                                "html_content": '<div class="od-meta-line"><span>By <strong>AdTech Research Group</strong></span><span> • </span><span>Published November 2, 2026</span><span> • </span><span>7 min read</span></div>',
                                "asset_references": [],
                            },
                        },
                        {
                            "id": "blk_blog_body_1",
                            "type": "text_section",
                            "is_visible": True,
                            "content": {
                                "heading": "1. The Edge Latency Imperative",
                                "body": "Digital publishers today face stringent core web vital standards. Serving dynamic advertisements without introducing cumulative layout shift or network lag requires candidate retrieval to execute in milliseconds.\n\nTraditional ad exchange round-trips introduced 300ms of waterfall latency. Modern self-hosted direct serving platforms leverage regional Redis clusters with pre-filtered campaign bitmasks to achieve sub-20ms candidate scoring.",
                            },
                        },
                        {
                            "id": "blk_blog_ad",
                            "type": "ad_placement",
                            "is_visible": True,
                            "content": {
                                "placement_location": "in_article",
                                "format": 1,
                                "ad_slot_id": None,
                            },
                        },
                        {
                            "id": "blk_blog_body_2",
                            "type": "text_section",
                            "is_visible": True,
                            "content": {
                                "heading": "2. Fraud-Resistant Cryptographic Verification",
                                "body": "Beyond delivery speed, publisher revenue tracking demands non-forgeable events. By signing impression and click URLs with HMAC-SHA256 tokens and validating replay nonces, modern platforms eliminate bot inflation while maintaining auditable financial ledgers.",
                            },
                        },
                        {
                            "id": "blk_blog_footer",
                            "type": "footer",
                            "is_visible": True,
                            "content": {
                                "copyright_text": "© 2026 The Publisher Journal. Powered by RivinityADS",
                            },
                        },
                    ],
                },
            }
        ],
    },
    {
        "id": "docs-page",
        "name": "Developer Documentation",
        "description": "Structured developer documentation layout with sidebar table of contents, syntax-highlighted code boxes, and API references.",
        "category": "Publishing & Editorial",
        "badge": "Developer",
        "thumbnail_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 200' fill='%23ffffff'><rect width='320' height='200' fill='%23ffffff'/><rect x='20' y='20' width='60' height='160' rx='4' fill='%23f1f5f9'/><rect x='100' y='24' width='180' height='16' rx='3' fill='%230284c7'/><rect x='100' y='50' width='190' height='10' rx='2' fill='%23475569'/><rect x='100' y='70' width='190' height='50' rx='4' fill='%230f172a'/><rect x='100' y='130' width='190' height='10' rx='2' fill='%23475569'/></svg>",
        "order_index": 4,
        "is_active": True,
        "default_theme_config": {
            "primary_color": "#0284c7",
            "secondary_color": "#475569",
            "background_color": "#ffffff",
            "text_color": "#0f172a",
            "font_family": "Inter",
            "border_radius": "6px",
        },
        "default_pages": [
            {
                "title": "Home",
                "slug": "",
                "is_home": True,
                "order_index": 0,
                "seo_meta": {
                    "meta_title": "Developer Documentation & API Guides",
                    "meta_description": "Integration tutorials, SDK references, and quickstart examples.",
                },
                "blocks": {
                    "schema_version": 2,
                    "blocks": [
                        {
                            "id": "blk_docs_hero",
                            "type": "hero",
                            "is_visible": True,
                            "content": {
                                "headline": "RivinityADS Developer Reference",
                                "subheadline": "Integrate high-performance ad placements, custom tracking webhooks, and visual website builders.",
                                "alignment": "left",
                            },
                        },
                        {
                            "id": "blk_docs_content",
                            "type": "open_design_section",
                            "is_visible": True,
                            "content": {
                                "section_id": "doc-body",
                                "section_tag": "section",
                                "css_classes": "od-docs-section",
                                "styles": {"padding": "2rem 0"},
                                "html_content": '<div class="od-docs-layout"><div class="od-doc-col"><h2>Getting Started</h2><p>Include the lightweight RivinityADS SDK in your web application header:</p><pre><code>npm install @rivinityads/publisher-sdk</code></pre><p>Render an ad slot by declaring a target placeholder container in your DOM layout.</p></div></div>',
                                "asset_references": [],
                            },
                        },
                        {
                            "id": "blk_docs_ad",
                            "type": "ad_placement",
                            "is_visible": True,
                            "content": {
                                "placement_location": "sidebar",
                                "format": 1,
                                "ad_slot_id": None,
                            },
                        },
                        {
                            "id": "blk_docs_footer",
                            "type": "footer",
                            "is_visible": True,
                            "content": {
                                "copyright_text": "© 2026 RivinityADS Developer Documentation",
                            },
                        },
                    ],
                },
            }
        ],
    },
    {
        "id": "web-prototype-taste-editorial",
        "name": "Minimalist Studio & Agency",
        "description": "Warm monochrome canvas, serif display typography, hairline borders, and generous whitespace derived from Taste Minimalist skills.",
        "category": "Creative & Agency",
        "badge": "Design-First",
        "thumbnail_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 200' fill='%23fafaf9'><rect width='320' height='200' fill='%23fafaf9'/><rect x='30' y='24' width='260' height='24' rx='2' fill='%2318181b'/><rect x='30' y='56' width='180' height='10' rx='2' fill='%2371717a'/><rect x='30' y='80' width='120' height='90' rx='4' fill='%23ffffff' stroke='%23e4e4e7'/><rect x='160' y='80' width='130' height='40' rx='4' fill='%23ffffff' stroke='%23e4e4e7'/><rect x='160' y='130' width='130' height='40' rx='4' fill='%23ffffff' stroke='%23e4e4e7'/></svg>",
        "order_index": 5,
        "is_active": True,
        "default_theme_config": {
            "primary_color": "#18181b",
            "secondary_color": "#71717a",
            "background_color": "#fafaf9",
            "text_color": "#18181b",
            "font_family": "Inter",
            "border_radius": "6px",
        },
        "default_pages": [
            {
                "title": "Home",
                "slug": "",
                "is_home": True,
                "order_index": 0,
                "seo_meta": {
                    "meta_title": "Studio Monolith — Digital Instruments",
                    "meta_description": "We partner with visionary founders to engineer refined digital surfaces.",
                },
                "blocks": {
                    "schema_version": 2,
                    "blocks": [
                        {
                            "id": "blk_taste_hero",
                            "type": "hero",
                            "is_visible": True,
                            "content": {
                                "headline": "Studio Monolith — Crafting digital instruments for forward-thinking teams.",
                                "subheadline": "We partner with ambitious founders to engineer world-class software, high-converting interfaces, and enduring brand identities.",
                                "alignment": "left",
                                "cta_button": {
                                    "label": "Explore Portfolio",
                                    "target_url": "/portfolio",
                                    "style": "primary",
                                },
                            },
                        },
                        {
                            "id": "blk_taste_bento",
                            "type": "open_design_section",
                            "is_visible": True,
                            "content": {
                                "section_id": "bento-grid",
                                "section_tag": "section",
                                "css_classes": "od-bento-section",
                                "styles": {"padding": "2rem 0"},
                                "html_content": '<div class="od-bento-wrap"><div class="od-bento-card"><h3>Design Systems</h3><p>Multi-tier design tokens, component architecture, and comprehensive accessibility contracts.</p></div><div class="od-bento-card"><h3>AdTech Engineering</h3><p>Self-hosted direct ad serving platforms delivering sub-millisecond edge latency.</p></div></div>',
                                "asset_references": [],
                            },
                        },
                        {
                            "id": "blk_taste_ad",
                            "type": "ad_placement",
                            "is_visible": True,
                            "content": {
                                "placement_location": "in_article",
                                "format": 1,
                                "ad_slot_id": None,
                            },
                        },
                        {
                            "id": "blk_taste_contact",
                            "type": "contact_form",
                            "is_visible": True,
                            "content": {
                                "title": "Initiate a Project Inquiry",
                                "submit_label": "Send Inquiry",
                                "fields": ["name", "email", "message"],
                            },
                        },
                        {
                            "id": "blk_taste_footer",
                            "type": "footer",
                            "is_visible": True,
                            "content": {
                                "copyright_text": "© 2026 Studio Monolith. Licensed under Apache-2.0 via Open Design / Leonxlnx",
                            },
                        },
                    ],
                },
            }
        ],
    },
    {
        "id": "waitlist-page",
        "name": "Pre-Launch & Lead Capture",
        "description": "Minimalist pre-launch landing page with brand badge, early access pitch, and integrated lead capture form.",
        "category": "Landing & Marketing",
        "badge": "Conversion",
        "thumbnail_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 200' fill='%230f172a'><rect width='320' height='200' fill='%230f172a'/><rect x='120' y='30' width='80' height='16' rx='8' fill='%236366f1'/><rect x='40' y='64' width='240' height='20' rx='4' fill='%23f8fafc'/><rect x='60' y='94' width='200' height='10' rx='2' fill='%2394a3b8'/><rect x='50' y='124' width='160' height='32' rx='6' fill='%231e293b' stroke='%23334155'/><rect x='215' y='124' width='60' height='32' rx='6' fill='%236366f1'/></svg>",
        "order_index": 6,
        "is_active": True,
        "default_theme_config": {
            "primary_color": "#6366f1",
            "secondary_color": "#94a3b8",
            "background_color": "#0f172a",
            "text_color": "#f8fafc",
            "font_family": "Poppins",
            "border_radius": "8px",
        },
        "default_pages": [
            {
                "title": "Home",
                "slug": "",
                "is_home": True,
                "order_index": 0,
                "seo_meta": {
                    "meta_title": "Join the Private Beta",
                    "meta_description": "Reserve your spot for early access to the next generation of publisher tools.",
                },
                "blocks": {
                    "schema_version": 2,
                    "blocks": [
                        {
                            "id": "blk_wait_hero",
                            "type": "hero",
                            "is_visible": True,
                            "content": {
                                "headline": "The Next Evolution of Digital Publishing is Coming",
                                "subheadline": "Experience AI-accelerated visual website composition coupled with zero-compromise ad serving. Enter your email for priority onboarding.",
                                "alignment": "center",
                            },
                        },
                        {
                            "id": "blk_wait_contact",
                            "type": "contact_form",
                            "is_visible": True,
                            "content": {
                                "title": "Reserve Your Early Access Spot",
                                "submit_label": "Join Waitlist",
                                "fields": ["email"],
                            },
                        },
                        {
                            "id": "blk_wait_footer",
                            "type": "footer",
                            "is_visible": True,
                            "content": {
                                "copyright_text": "© 2026 RivinityADS Labs. Early Access Beta",
                            },
                        },
                    ],
                },
            }
        ],
    },
    {
        "id": "web-prototype-taste-soft",
        "name": "Soft Consumer Tech",
        "description": "Apple-tier soft aesthetic with silver/cream canvas, double-bezel cards, squircle radii, and subtle ambient elevations.",
        "category": "Creative & Agency",
        "badge": "Premium",
        "thumbnail_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 200' fill='%23f8fafc'><rect width='320' height='200' fill='%23f8fafc'/><rect x='60' y='28' width='200' height='22' rx='6' fill='%230f172a'/><rect x='80' y='58' width='160' height='10' rx='2' fill='%2364748b'/><rect x='30' y='88' width='125' height='75' rx='12' fill='%23ffffff' stroke='%23e2e8f0'/><rect x='165' y='88' width='125' height='75' rx='12' fill='%23ffffff' stroke='%23e2e8f0'/></svg>",
        "order_index": 7,
        "is_active": True,
        "default_theme_config": {
            "primary_color": "#0284c7",
            "secondary_color": "#64748b",
            "background_color": "#f8fafc",
            "text_color": "#0f172a",
            "font_family": "Inter",
            "border_radius": "12px",
        },
        "default_pages": [
            {
                "title": "Home",
                "slug": "",
                "is_home": True,
                "order_index": 0,
                "seo_meta": {
                    "meta_title": "Calm Technology for Focused Teams",
                    "meta_description": "Designed with intention. Built with precision.",
                },
                "blocks": {
                    "schema_version": 2,
                    "blocks": [
                        {
                            "id": "blk_soft_hero",
                            "type": "hero",
                            "is_visible": True,
                            "content": {
                                "headline": "Calm technology for deep focus",
                                "subheadline": "Designed with intention. Built with precision. Crafted for teams who value clarity over clutter.",
                                "alignment": "center",
                                "cta_button": {
                                    "label": "Download Application",
                                    "target_url": "/download",
                                    "style": "primary",
                                },
                            },
                        },
                        {
                            "id": "blk_soft_features",
                            "type": "features_grid",
                            "is_visible": True,
                            "content": {
                                "title": "Obsessive Craftsmanship",
                                "items": [
                                    {
                                        "title": "Gentle Contrast",
                                        "description": "Engineered tonal palette that prevents eye fatigue during extended work sessions.",
                                        "icon": "sun",
                                    },
                                    {
                                        "title": "Tactile Dynamics",
                                        "description": "Smooth physics-based micro-interactions responding fluidly to input gestures.",
                                        "icon": "activity",
                                    },
                                    {
                                        "title": "Local Sovereignty",
                                        "description": "Your work artifacts stay exclusively on your local storage with zero cloud snooping.",
                                        "icon": "lock",
                                    },
                                ],
                            },
                        },
                        {
                            "id": "blk_soft_ad",
                            "type": "ad_placement",
                            "is_visible": True,
                            "content": {
                                "placement_location": "in_article",
                                "format": 1,
                                "ad_slot_id": None,
                            },
                        },
                        {
                            "id": "blk_soft_footer",
                            "type": "footer",
                            "is_visible": True,
                            "content": {
                                "copyright_text": "© 2026 Soft Systems. Powered by RivinityADS",
                            },
                        },
                    ],
                },
            }
        ],
    },
    {
        "id": "web-prototype-taste-brutalist",
        "name": "Swiss Industrial Print",
        "description": "Monolithic Swiss newspaper aesthetic with newsprint canvas, viewport-bleeding grotesque typography, and hairline grid dividers.",
        "category": "Creative & Agency",
        "badge": "Brutalist",
        "thumbnail_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 200' fill='%23f4f3ef'><rect width='320' height='200' fill='%23f4f3ef'/><rect x='20' y='20' width='280' height='28' fill='%23171717'/><line x1='20' y1='60' x2='300' y2='60' stroke='%23171717' stroke-width='2'/><rect x='20' y='72' width='130' height='80' fill='%23ffffff' stroke='%23171717'/><rect x='170' y='72' width='130' height='80' fill='%23ffffff' stroke='%23171717'/><rect x='20' y='165' width='60' height='15' fill='%23dc2626'/></svg>",
        "order_index": 8,
        "is_active": True,
        "default_theme_config": {
            "primary_color": "#dc2626",
            "secondary_color": "#525252",
            "background_color": "#f4f3ef",
            "text_color": "#171717",
            "font_family": "Roboto",
            "border_radius": "0px",
        },
        "default_pages": [
            {
                "title": "Home",
                "slug": "",
                "is_home": True,
                "order_index": 0,
                "seo_meta": {
                    "meta_title": "VOL. 01 — RIGOROUS DIGITAL INFRASTRUCTURE",
                    "meta_description": "Swiss industrial design standards for modern web infrastructure.",
                },
                "blocks": {
                    "schema_version": 2,
                    "blocks": [
                        {
                            "id": "blk_brut_hero",
                            "type": "hero",
                            "is_visible": True,
                            "content": {
                                "headline": "VOL. 01 — RIGOROUS DIGITAL INFRASTRUCTURE",
                                "subheadline": "SPECIFICATION FOR HIGH-THROUGHPUT AD NETWORKS AND DISTRIBUTED SYSTEMS.",
                                "alignment": "left",
                            },
                        },
                        {
                            "id": "blk_brut_text",
                            "type": "text_section",
                            "is_visible": True,
                            "content": {
                                "heading": "MANIFESTO 01: FORM FOLLOWS THROUGHPUT",
                                "body": "Digital ad networks are burdened by opaque intermediaries and bloated trackers. Our architectural rule is uncompromising: zero untrusted JavaScript, deterministic latency, and auditable accounting ledgers.",
                            },
                        },
                        {
                            "id": "blk_brut_ad",
                            "type": "ad_placement",
                            "is_visible": True,
                            "content": {
                                "placement_location": "in_article",
                                "format": 1,
                                "ad_slot_id": None,
                            },
                        },
                        {
                            "id": "blk_brut_footer",
                            "type": "footer",
                            "is_visible": True,
                            "content": {
                                "copyright_text": "EDITION 2026 — LICENSED UNDER APACHE-2.0 VIA LEONXLNX/OPEN-DESIGN",
                            },
                        },
                    ],
                },
            }
        ],
    },
    {
        "id": "dashboard",
        "name": "Operations & Analytics Portal",
        "description": "Clean operations dashboard layout featuring KPI metric cards, system status pills, and responsive performance feeds.",
        "category": "Business & Operations",
        "badge": "Operations",
        "thumbnail_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 200' fill='%23f8fafc'><rect width='320' height='200' fill='%23f8fafc'/><rect x='20' y='20' width='280' height='30' rx='4' fill='%23ffffff' stroke='%23e2e8f0'/><rect x='20' y='60' width='65' height='50' rx='4' fill='%23ffffff' stroke='%23e2e8f0'/><rect x='92' y='60' width='65' height='50' rx='4' fill='%23ffffff' stroke='%23e2e8f0'/><rect x='164' y='60' width='65' height='50' rx='4' fill='%23ffffff' stroke='%23e2e8f0'/><rect x='235' y='60' width='65' height='50' rx='4' fill='%23ffffff' stroke='%23e2e8f0'/><rect x='20' y='120' width='280' height='60' rx='4' fill='%23ffffff' stroke='%23e2e8f0'/></svg>",
        "order_index": 9,
        "is_active": True,
        "default_theme_config": {
            "primary_color": "#3b82f6",
            "secondary_color": "#64748b",
            "background_color": "#f8fafc",
            "text_color": "#1e293b",
            "font_family": "Inter",
            "border_radius": "8px",
        },
        "default_pages": [
            {
                "title": "Home",
                "slug": "",
                "is_home": True,
                "order_index": 0,
                "seo_meta": {
                    "meta_title": "Network Performance Dashboard",
                    "meta_description": "Real-time metrics and operations monitoring.",
                },
                "blocks": {
                    "schema_version": 2,
                    "blocks": [
                        {
                            "id": "blk_dash_hero",
                            "type": "hero",
                            "is_visible": True,
                            "content": {
                                "headline": "Network Performance & Operations",
                                "subheadline": "Real-time candidate scoring throughput, cluster health, and revenue attribution metrics.",
                                "alignment": "left",
                            },
                        },
                        {
                            "id": "blk_dash_kpis",
                            "type": "open_design_section",
                            "is_visible": True,
                            "content": {
                                "section_id": "dash-kpis",
                                "section_tag": "section",
                                "css_classes": "od-kpi-grid",
                                "styles": {"padding": "1.5rem 0"},
                                "html_content": '<div class="od-kpi-row"><div class="od-kpi-card"><p class="od-kpi-label">Total Impressions</p><p class="od-kpi-val">1,420,850</p><span class="od-trend-up">+14.2% vs last week</span></div><div class="od-kpi-card"><p class="od-kpi-label">Average eCPM</p><p class="od-kpi-val">$3.85</p><span class="od-trend-up">+8.1% vs last week</span></div><div class="od-kpi-card"><p class="od-kpi-label">Active Ad Slots</p><p class="od-kpi-val">48 Units</p><span class="od-status-pill">100% operational</span></div></div>',
                                "asset_references": [],
                            },
                        },
                        {
                            "id": "blk_dash_ad",
                            "type": "ad_placement",
                            "is_visible": True,
                            "content": {
                                "placement_location": "header_banner",
                                "format": 1,
                                "ad_slot_id": None,
                            },
                        },
                        {
                            "id": "blk_dash_footer",
                            "type": "footer",
                            "is_visible": True,
                            "content": {
                                "copyright_text": "RivinityADS Operations Console v2.0",
                            },
                        },
                    ],
                },
            }
        ],
    },
    {
        "id": "kanban-board",
        "name": "Project Management Board",
        "description": "Visual agile task board with swimlanes, status columns (To Do, In Progress, Review, Done), and assignee tags.",
        "category": "Business & Operations",
        "badge": "Agile",
        "thumbnail_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 200' fill='%23f1f5f9'><rect width='320' height='200' fill='%23f1f5f9'/><rect x='20' y='20' width='65' height='160' rx='4' fill='%23e2e8f0'/><rect x='92' y='20' width='65' height='160' rx='4' fill='%23e2e8f0'/><rect x='164' y='20' width='65' height='160' rx='4' fill='%23e2e8f0'/><rect x='235' y='20' width='65' height='160' rx='4' fill='%23e2e8f0'/><rect x='25' y='45' width='55' height='35' rx='3' fill='%23ffffff'/><rect x='97' y='45' width='55' height='35' rx='3' fill='%23ffffff'/><rect x='97' y='90' width='55' height='35' rx='3' fill='%23ffffff'/><rect x='240' y='45' width='55' height='35' rx='3' fill='%23ffffff'/></svg>",
        "order_index": 10,
        "is_active": True,
        "default_theme_config": {
            "primary_color": "#6366f1",
            "secondary_color": "#475569",
            "background_color": "#f1f5f9",
            "text_color": "#0f172a",
            "font_family": "Inter",
            "border_radius": "8px",
        },
        "default_pages": [
            {
                "title": "Home",
                "slug": "",
                "is_home": True,
                "order_index": 0,
                "seo_meta": {
                    "meta_title": "Sprint Workflow Board",
                    "meta_description": "Sprint planning and task management.",
                },
                "blocks": {
                    "schema_version": 2,
                    "blocks": [
                        {
                            "id": "blk_kanban_hero",
                            "type": "hero",
                            "is_visible": True,
                            "content": {
                                "headline": "Sprint Board — Q4 Product Delivery",
                                "subheadline": "Track active features, ad server optimizations, and deployment milestones.",
                                "alignment": "left",
                            },
                        },
                        {
                            "id": "blk_kanban_board",
                            "type": "open_design_section",
                            "is_visible": True,
                            "content": {
                                "section_id": "kanban-columns",
                                "section_tag": "section",
                                "css_classes": "od-kanban-board",
                                "styles": {"padding": "1rem 0"},
                                "html_content": '<div class="od-kanban-grid"><div class="od-col"><h4>To Do (3)</h4><div class="od-card">Implement Redis geo-lookup</div><div class="od-card">Draft SDK bundle size report</div></div><div class="od-col"><h4>In Progress (2)</h4><div class="od-card">Alembic template catalog migration</div></div><div class="od-col"><h4>Completed (4)</h4><div class="od-card">HTML sanitizer verification</div></div></div>',
                                "asset_references": [],
                            },
                        },
                        {
                            "id": "blk_kanban_footer",
                            "type": "footer",
                            "is_visible": True,
                            "content": {
                                "copyright_text": "© 2026 Agile Workspace. Powered by RivinityADS",
                            },
                        },
                    ],
                },
            }
        ],
    },
    {
        "id": "team-okrs",
        "name": "Objectives & Milestone Tracker",
        "description": "Executive OKR tracker with quarterly banners, key result progress bars, owner avatars, and strategic initiatives.",
        "category": "Business & Operations",
        "badge": "Strategy",
        "thumbnail_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 200' fill='%23ffffff'><rect width='320' height='200' fill='%23ffffff'/><rect x='20' y='20' width='280' height='24' rx='4' fill='%23059669'/><rect x='20' y='56' width='280' height='36' rx='4' fill='%23f0fdf4' stroke='%23bbf7d0'/><rect x='35' y='76' width='180' height='8' rx='4' fill='%23059669'/><rect x='20' y='100' width='280' height='36' rx='4' fill='%23f0fdf4' stroke='%23bbf7d0'/><rect x='35' y='120' width='120' height='8' rx='4' fill='%23059669'/></svg>",
        "order_index": 11,
        "is_active": True,
        "default_theme_config": {
            "primary_color": "#059669",
            "secondary_color": "#4b5563",
            "background_color": "#ffffff",
            "text_color": "#111827",
            "font_family": "Inter",
            "border_radius": "8px",
        },
        "default_pages": [
            {
                "title": "Home",
                "slug": "",
                "is_home": True,
                "order_index": 0,
                "seo_meta": {
                    "meta_title": "Quarterly Objectives & Key Results",
                    "meta_description": "Company-wide goal tracking.",
                },
                "blocks": {
                    "schema_version": 2,
                    "blocks": [
                        {
                            "id": "blk_okr_hero",
                            "type": "hero",
                            "is_visible": True,
                            "content": {
                                "headline": "Q4 Strategic Objectives & Key Results",
                                "subheadline": "Aligning ad server throughput, publisher yield optimization, and platform security milestones.",
                                "alignment": "left",
                            },
                        },
                        {
                            "id": "blk_okr_list",
                            "type": "open_design_section",
                            "is_visible": True,
                            "content": {
                                "section_id": "okr-items",
                                "section_tag": "section",
                                "css_classes": "od-okr-container",
                                "styles": {"padding": "1.5rem 0"},
                                "html_content": '<div class="od-okr-stack"><div class="od-okr-box"><h4>Objective 1: Achieve sub-20ms P99 Ad Delivery Latency</h4><div class="od-meter"><div class="od-fill-85"></div></div><p>85% Completed — Edge cache deployment active across 6 regions.</p></div><div class="od-okr-box"><h4>Objective 2: Expand Website Template Ecosystem to 15 Designs</h4><div class="od-meter"><div class="od-fill-100"></div></div><p>100% Completed — Phase 2 Ingestion verified.</p></div></div>',
                                "asset_references": [],
                            },
                        },
                        {
                            "id": "blk_okr_footer",
                            "type": "footer",
                            "is_visible": True,
                            "content": {
                                "copyright_text": "© 2026 RivinityADS Governance & Operations",
                            },
                        },
                    ],
                },
            }
        ],
    },
    {
        "id": "eng-runbook",
        "name": "Technical Service Runbook",
        "description": "Standard operating procedures, alert triage matrices, diagnostic command boxes, and on-call recovery checklists.",
        "category": "Business & Operations",
        "badge": "DevOps",
        "thumbnail_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 200' fill='%23ffffff'><rect width='320' height='200' fill='%23ffffff'/><rect x='20' y='20' width='80' height='16' rx='3' fill='%23ef4444'/><rect x='110' y='20' width='190' height='16' rx='3' fill='%230f172a'/><rect x='20' y='46' width='280' height='40' rx='4' fill='%23f8fafc' stroke='%23e2e8f0'/><rect x='20' y='96' width='280' height='70' rx='4' fill='%230f172a'/></svg>",
        "order_index": 12,
        "is_active": True,
        "default_theme_config": {
            "primary_color": "#2563eb",
            "secondary_color": "#475569",
            "background_color": "#ffffff",
            "text_color": "#0f172a",
            "font_family": "Inter",
            "border_radius": "6px",
        },
        "default_pages": [
            {
                "title": "Home",
                "slug": "",
                "is_home": True,
                "order_index": 0,
                "seo_meta": {
                    "meta_title": "Ad Serving Engine Runbook",
                    "meta_description": "Service operations and incident management.",
                },
                "blocks": {
                    "schema_version": 2,
                    "blocks": [
                        {
                            "id": "blk_run_hero",
                            "type": "hero",
                            "is_visible": True,
                            "content": {
                                "headline": "Runbook: Ad Serving Engine & Redis Pipeline",
                                "subheadline": "Operating procedures for service degradation, edge failover, and token verification anomalies.",
                                "alignment": "left",
                            },
                        },
                        {
                            "id": "blk_run_proc",
                            "type": "open_design_section",
                            "is_visible": True,
                            "content": {
                                "section_id": "runbook-steps",
                                "section_tag": "section",
                                "css_classes": "od-runbook-section",
                                "styles": {"padding": "1rem 0"},
                                "html_content": '<div class="od-runbook-card"><h3>Diagnostic Health Check</h3><p>Run diagnostic curl commands against the primary edge cluster:</p><pre><code>curl -s http://localhost:8000/api/v1/health</code></pre></div>',
                                "asset_references": [],
                            },
                        },
                        {
                            "id": "blk_run_footer",
                            "type": "footer",
                            "is_visible": True,
                            "content": {
                                "copyright_text": "RivinityADS Site Reliability Engineering (SRE)",
                            },
                        },
                    ],
                },
            }
        ],
    },
    {
        "id": "finance-report",
        "name": "Financial Report & Metrics",
        "description": "Investor and quarterly financial review featuring KPI mastheads, burn rate indicators, P&L tables, and outlook commentaries.",
        "category": "Business & Operations",
        "badge": "Finance",
        "thumbnail_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 200' fill='%23fafaf9'><rect width='320' height='200' fill='%23fafaf9'/><rect x='20' y='20' width='200' height='18' rx='3' fill='%23047857'/><rect x='20' y='46' width='140' height='10' rx='2' fill='%234b5563'/><rect x='20' y='68' width='85' height='40' rx='4' fill='%23ffffff' stroke='%23e5e7eb'/><rect x='117' y='68' width='85' height='40' rx='4' fill='%23ffffff' stroke='%23e5e7eb'/><rect x='214' y='68' width='85' height='40' rx='4' fill='%23ffffff' stroke='%23e5e7eb'/><rect x='20' y='120' width='280' height='55' rx='4' fill='%23ffffff' stroke='%23e5e7eb'/></svg>",
        "order_index": 13,
        "is_active": True,
        "default_theme_config": {
            "primary_color": "#047857",
            "secondary_color": "#4b5563",
            "background_color": "#fafaf9",
            "text_color": "#1f2937",
            "font_family": "Inter",
            "border_radius": "6px",
        },
        "default_pages": [
            {
                "title": "Home",
                "slug": "",
                "is_home": True,
                "order_index": 0,
                "seo_meta": {
                    "meta_title": "Quarterly Financial & Yield Report — Q3 2026",
                    "meta_description": "Publisher earnings and platform revenue reconciliation.",
                },
                "blocks": {
                    "schema_version": 2,
                    "blocks": [
                        {
                            "id": "blk_fin_hero",
                            "type": "hero",
                            "is_visible": True,
                            "content": {
                                "headline": "Quarterly Financial & Yield Report — Q3 2026",
                                "subheadline": "Publisher gross revenue, network take-rate analysis, and annualized run rate metrics.",
                                "alignment": "left",
                            },
                        },
                        {
                            "id": "blk_fin_table",
                            "type": "open_design_section",
                            "is_visible": True,
                            "content": {
                                "section_id": "fin-metrics",
                                "section_tag": "section",
                                "css_classes": "od-finance-section",
                                "styles": {"padding": "1.5rem 0"},
                                "html_content": '<div class="od-table-box"><table><thead><tr><th>Metric</th><th>Q2 2026</th><th>Q3 2026</th><th>Change</th></tr></thead><tbody><tr><td>Gross Ad Spend</td><td>$240,000</td><td>$325,000</td><td class="od-positive">+35.4%</td></tr><tr><td>Publisher Payouts</td><td>$192,000</td><td>$260,000</td><td class="od-positive">+35.4%</td></tr></tbody></table></div>',
                                "asset_references": [],
                            },
                        },
                        {
                            "id": "blk_fin_ad",
                            "type": "ad_placement",
                            "is_visible": True,
                            "content": {
                                "placement_location": "in_article",
                                "format": 1,
                                "ad_slot_id": None,
                            },
                        },
                        {
                            "id": "blk_fin_footer",
                            "type": "footer",
                            "is_visible": True,
                            "content": {
                                "copyright_text": "© 2026 RivinityADS Financial Systems",
                            },
                        },
                    ],
                },
            }
        ],
    },
    {
        "id": "hr-onboarding",
        "name": "Employee Handbook & Portal",
        "description": "New-hire onboarding portal with week-one schedules, buddy contacts, equipment checklists, and team guidelines.",
        "category": "Business & Operations",
        "badge": "Culture",
        "thumbnail_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 200' fill='%23ffffff'><rect width='320' height='200' fill='%23ffffff'/><rect x='20' y='20' width='160' height='18' rx='3' fill='%237c3aed'/><rect x='20' y='46' width='180' height='10' rx='2' fill='%2352525b'/><rect x='20' y='68' width='280' height='32' rx='4' fill='%23f4f4f5'/><rect x='20' y='108' width='280' height='32' rx='4' fill='%23f4f4f5'/><rect x='20' y='148' width='280' height='32' rx='4' fill='%23f4f4f5'/></svg>",
        "order_index": 14,
        "is_active": True,
        "default_theme_config": {
            "primary_color": "#7c3aed",
            "secondary_color": "#52525b",
            "background_color": "#ffffff",
            "text_color": "#18181b",
            "font_family": "Inter",
            "border_radius": "8px",
        },
        "default_pages": [
            {
                "title": "Home",
                "slug": "",
                "is_home": True,
                "order_index": 0,
                "seo_meta": {
                    "meta_title": "Welcome to Bharattech Ads",
                    "meta_description": "Your week-one onboarding handbook and checklist.",
                },
                "blocks": {
                    "schema_version": 2,
                    "blocks": [
                        {
                            "id": "blk_hr_hero",
                            "type": "hero",
                            "is_visible": True,
                            "content": {
                                "headline": "Welcome to the Bharattech Ads Team!",
                                "subheadline": "Your guide for week one: setting up your local repository, meeting your engineering mentor, and shipping your first contribution.",
                                "alignment": "left",
                            },
                        },
                        {
                            "id": "blk_hr_steps",
                            "type": "open_design_section",
                            "is_visible": True,
                            "content": {
                                "section_id": "onboarding-timeline",
                                "section_tag": "section",
                                "css_classes": "od-onboarding-steps",
                                "styles": {"padding": "1rem 0"},
                                "html_content": '<div class="od-step-list"><div class="od-step-card"><p><strong>Day 1:</strong> Access provisioning, SSH keys setup, and dev environment check.</p></div><div class="od-step-card"><p><strong>Day 2-3:</strong> Deep dive into Ad Server candidate retrieval and test suites.</p></div><div class="od-step-card"><p><strong>Day 4-5:</strong> First branch deployment and PR submission.</p></div></div>',
                                "asset_references": [],
                            },
                        },
                        {
                            "id": "blk_hr_footer",
                            "type": "footer",
                            "is_visible": True,
                            "content": {
                                "copyright_text": "© 2026 Bharattech People Operations",
                            },
                        },
                    ],
                },
            }
        ],
    },
    {
        "id": "meeting-notes",
        "name": "Executive Briefing & Agenda",
        "description": "Executive meeting notes layout with attendee chips, structured agenda checklist, decisions block, and action items table.",
        "category": "Business & Operations",
        "badge": "Productivity",
        "thumbnail_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 200' fill='%23fafaf9'><rect width='320' height='200' fill='%23fafaf9'/><rect x='20' y='20' width='180' height='18' rx='3' fill='%230d9488'/><rect x='20' y='46' width='220' height='10' rx='2' fill='%235eead4'/><rect x='20' y='68' width='280' height='40' rx='4' fill='%23ffffff' stroke='%23ccfbf1'/><rect x='20' y='116' width='280' height='55' rx='4' fill='%23ffffff' stroke='%23ccfbf1'/></svg>",
        "order_index": 15,
        "is_active": True,
        "default_theme_config": {
            "primary_color": "#0d9488",
            "secondary_color": "#5eead4",
            "background_color": "#fafaf9",
            "text_color": "#134e4a",
            "font_family": "Inter",
            "border_radius": "6px",
        },
        "default_pages": [
            {
                "title": "Home",
                "slug": "",
                "is_home": True,
                "order_index": 0,
                "seo_meta": {
                    "meta_title": "Architecture Working Group — Weekly Sync",
                    "meta_description": "Discussion notes on Phase 2 templates and security boundaries.",
                },
                "blocks": {
                    "schema_version": 2,
                    "blocks": [
                        {
                            "id": "blk_meet_hero",
                            "type": "hero",
                            "is_visible": True,
                            "content": {
                                "headline": "Architecture Working Group — Weekly Sync",
                                "subheadline": "Review of Phase 2 Open Design ingestion, schema v2 sanitization, and responsive template previews.",
                                "alignment": "left",
                            },
                        },
                        {
                            "id": "blk_meet_body",
                            "type": "open_design_section",
                            "is_visible": True,
                            "content": {
                                "section_id": "meeting-decisions",
                                "section_tag": "section",
                                "css_classes": "od-notes-section",
                                "styles": {"padding": "1rem 0"},
                                "html_content": '<div class="od-meeting-card"><h3>Key Decisions</h3><ul><li>Selected 15 high-fidelity templates across Landing, Editorial, Operations, and Creative categories.</li><li>Enforced strict sanitization: all scripts and event handlers stripped prior to persistence.</li><li>Preserved backward compatibility for legacy templates.</li></ul></div>',
                                "asset_references": [],
                            },
                        },
                        {
                            "id": "blk_meet_footer",
                            "type": "footer",
                            "is_visible": True,
                            "content": {
                                "copyright_text": "Internal Engineering Meeting Records",
                            },
                        },
                    ],
                },
            }
        ],
    },
    # Backward-compatible legacy business template
    {
        "id": "business_services",
        "name": "Business & Services",
        "description": "Professional landing page with welcoming hero, feature pillars, and lead capture form.",
        "category": "Landing & Marketing",
        "badge": "Standard",
        "thumbnail_url": "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 320 200' fill='%23ffffff'><rect width='320' height='200' fill='%23ffffff'/><rect x='20' y='20' width='180' height='18' rx='3' fill='%232563eb'/><rect x='20' y='50' width='240' height='10' rx='2' fill='%2364748b'/><rect x='20' y='75' width='80' height='40' rx='4' fill='%23f8fafc' stroke='%23e2e8f0'/><rect x='115' y='75' width='80' height='40' rx='4' fill='%23f8fafc' stroke='%23e2e8f0'/><rect x='210' y='75' width='80' height='40' rx='4' fill='%23f8fafc' stroke='%23e2e8f0'/><rect x='20' y='125' width='270' height='50' rx='4' fill='%23f8fafc' stroke='%23e2e8f0'/></svg>",
        "order_index": 16,
        "is_active": True,
        "default_theme_config": {
            "primary_color": "#2563eb",
            "secondary_color": "#475569",
            "background_color": "#ffffff",
            "text_color": "#0f172a",
            "font_family": "Inter",
            "border_radius": "8px",
        },
        "default_pages": [
            {
                "title": "Home",
                "slug": "",
                "is_home": True,
                "order_index": 0,
                "seo_meta": {
                    "meta_title": "Professional Services",
                    "meta_description": "We deliver exceptional services to our valued enterprise clients.",
                },
                "blocks": {
                    "schema_version": 2,
                    "blocks": [
                        {
                            "id": "blk_bs_hero",
                            "type": "hero",
                            "is_visible": True,
                            "content": {
                                "headline": "Welcome to Our Services",
                                "subheadline": "We provide exceptional services to our valued clients.",
                                "alignment": "center",
                                "cta_button": {
                                    "label": "Contact Us",
                                    "target_url": "/contact",
                                    "style": "primary",
                                },
                            },
                        },
                        {
                            "id": "blk_bs_features",
                            "type": "features_grid",
                            "is_visible": True,
                            "content": {
                                "title": "What We Deliver",
                                "items": [
                                    {
                                        "title": "Strategic Consulting",
                                        "description": "Tailored guidance to align engineering with high-growth business outcomes.",
                                        "icon": "compass",
                                    },
                                    {
                                        "title": "Turnkey Implementation",
                                        "description": "Rapid integration and verified deployment across distributed cloud pipelines.",
                                        "icon": "check-circle",
                                    },
                                ],
                            },
                        },
                        {
                            "id": "blk_bs_contact",
                            "type": "contact_form",
                            "is_visible": True,
                            "content": {
                                "title": "Get in Touch",
                                "submit_label": "Send Message",
                                "fields": ["name", "email", "phone", "message"],
                            },
                        },
                        {
                            "id": "blk_bs_footer",
                            "type": "footer",
                            "is_visible": True,
                            "content": {
                                "copyright_text": "© 2026 Business Services. Powered by RivinityADS",
                            },
                        },
                    ],
                },
            }
        ],
    },
]


def get_builtin_templates() -> list[dict[str, Any]]:
    """Return in-memory copy of all builtin website templates."""
    return [dict(t) for t in BUILTIN_TEMPLATES]


async def seed_website_templates(session: AsyncSession) -> int:
    """
    Seed builtin templates into the database idempotently.
    
    Returns the number of newly inserted template records.
    """
    inserted = 0
    for tmpl_data in BUILTIN_TEMPLATES:
        t_id = tmpl_data["id"]
        stmt = select(WebsiteTemplate.id).where(WebsiteTemplate.id == t_id)
        existing = await session.execute(stmt)
        if existing.scalar_one_or_none() is None:
            new_tmpl = WebsiteTemplate(
                id=t_id,
                name=tmpl_data["name"],
                description=tmpl_data["description"],
                category=tmpl_data["category"],
                thumbnail_url=tmpl_data.get("thumbnail_url"),
                badge=tmpl_data.get("badge"),
                default_theme_config=tmpl_data["default_theme_config"],
                default_pages=tmpl_data["default_pages"],
                is_active=tmpl_data.get("is_active", True),
                order_index=tmpl_data.get("order_index", 0),
            )
            session.add(new_tmpl)
            inserted += 1
    if inserted > 0:
        await session.commit()
        logger.info(f"Seeded {inserted} website templates into database.")
    return inserted


async def seed_website_templates_if_empty(session: AsyncSession) -> None:
    """Automatically seed templates if the website_templates table is currently empty."""
    stmt = select(func.count(WebsiteTemplate.id))
    res = await session.execute(stmt)
    count = res.scalar() or 0
    if count == 0:
        await seed_website_templates(session)
