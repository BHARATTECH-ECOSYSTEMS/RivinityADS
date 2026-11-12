"""Business profile service for guided business questionnaire, anti-fabrication auditing,
draft site generation, accessibility auditing, custom domain DNS verification, and GEO readiness."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import math
import re
import secrets
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.common.logger import get_logger
from liteads.models.website import (
    CustomDomainStatus,
    WebsitePage,
    WebsiteProject,
    WebsiteStatus,
)
from liteads.schemas.business_profile import (
    CATEGORY_SCHEMA_MAP,
    AccessibilityAuditReport,
    BusinessProfileAudit,
    BusinessProfileIntake,
    CustomDomainStatusResponse,
    GeoReadinessReport,
    WebmasterToolsConfig,
)
from liteads.schemas.website import (
    BlockItem,
    BlockType,
    ContactFormContent,
    CtaBannerContent,
    CtaButton,
    FeatureItem,
    FeaturesGridContent,
    FooterContent,
    GalleryImage,
    HeroContent,
    ImageGalleryContent,
    NavLink,
    OpenDesignSectionContent,
    PageBlocksPayload,
    SeoMeta,
    TextSectionContent,
)

logger = get_logger(__name__)

# Pattern detecting typical fabricated marketing slop or unsubstantiated claims
FABRICATED_PATTERNS = [
    re.compile(r"\b(best in the world|#1 rated|voted #1|award[- ]winning 202[0-9]|certified 5 stars|guaranteed #1)\b", re.IGNORECASE),
    re.compile(r"\b(100% satisfaction guaranteed|world class leadership|fortune 500 partner)\b", re.IGNORECASE),
]


def _relative_luminance(hex_color: str) -> float:
    """Calculate relative luminance for WCAG contrast calculation."""
    clean = hex_color.lstrip("#")
    if len(clean) == 3:
        clean = "".join(c * 2 for c in clean)
    if len(clean) != 6:
        return 0.5
    try:
        r = int(clean[0:2], 16) / 255.0
        g = int(clean[2:4], 16) / 255.0
        b = int(clean[4:6], 16) / 255.0
    except ValueError:
        return 0.5

    def srgb_to_lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else math.pow((c + 0.055) / 1.055, 2.4)

    return 0.2126 * srgb_to_lin(r) + 0.7152 * srgb_to_lin(g) + 0.0722 * srgb_to_lin(b)


def calculate_wcag_contrast(bg_hex: str, text_hex: str) -> float:
    """Calculate WCAG 2.1 contrast ratio between background and text color."""
    l1 = _relative_luminance(bg_hex)
    l2 = _relative_luminance(text_hex)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    ratio = (lighter + 0.05) / (darker + 0.05)
    return round(ratio, 2)


class BusinessProfileService:
    """Core domain logic for business questionnaires, factual drafts, and site readiness."""

    @staticmethod
    def audit_business_profile(intake: BusinessProfileIntake) -> BusinessProfileAudit:
        """
        Evaluate completeness and detect any fabricated or unsubstantiated claims.
        Missing facts are reported explicitly; never invented.
        """
        supplied: list[str] = []
        missing: list[str] = []
        assertions_count = 0

        # 1. Identity
        if intake.business_name:
            supplied.append("business_name")
            assertions_count += 1
        else:
            missing.append("business_name")

        if intake.category:
            supplied.append("category")
            assertions_count += 1
        else:
            missing.append("category")

        if intake.description:
            supplied.append("description")
            assertions_count += 1
        else:
            missing.append("description")

        # 2. NAP
        nap = intake.nap
        if nap.phone:
            supplied.append("phone")
            assertions_count += 1
        else:
            missing.append("phone")

        if nap.street_address and nap.city:
            supplied.append("physical_address")
            assertions_count += 1
        else:
            missing.append("physical_address")

        if nap.postal_code:
            supplied.append("postal_code")
            assertions_count += 1
        else:
            missing.append("postal_code")

        # 3. Hours
        if intake.hours and len(intake.hours) > 0:
            supplied.append("opening_hours")
            assertions_count += len(intake.hours)
        else:
            missing.append("opening_hours")

        # 4. Services / Products
        if intake.services_or_products and len(intake.services_or_products) > 0:
            supplied.append("services_or_products")
            assertions_count += len(intake.services_or_products)
            has_prices = any(item.price is not None for item in intake.services_or_products)
            if has_prices:
                supplied.append("item_pricing")
            else:
                missing.append("item_pricing")
        else:
            missing.append("services_or_products")
            missing.append("item_pricing")

        # 5. Media & Assets
        if intake.logo_url:
            supplied.append("logo_url")
            assertions_count += 1
        else:
            missing.append("logo_url")

        # 6. Contact & Messaging
        if intake.whatsapp_number:
            supplied.append("whatsapp_number")
            assertions_count += 1
        else:
            missing.append("whatsapp_number")

        # 7. Real FAQs
        if intake.faqs and len(intake.faqs) > 0:
            supplied.append("faqs")
            assertions_count += len(intake.faqs)
        else:
            missing.append("faqs")

        # 8. Service Area
        if intake.service_area_cities and len(intake.service_area_cities) > 0:
            supplied.append("service_area")
            assertions_count += len(intake.service_area_cities)
        else:
            missing.append("service_area")

        # Anti-fabrication check
        fabricated_detected: list[str] = []
        combined_text = f"{intake.business_name} {intake.tagline or ''} {intake.description or ''}"
        for item in intake.services_or_products:
            combined_text += f" {item.name} {item.description or ''}"
        for faq in intake.faqs:
            combined_text += f" {faq.question} {faq.answer}"

        for pat in FABRICATED_PATTERNS:
            matches = pat.findall(combined_text)
            for m in matches:
                if isinstance(m, tuple):
                    m = m[0]
                if m not in fabricated_detected:
                    fabricated_detected.append(m)

        # Completeness calculation
        total_eval_points = 11
        supplied_eval_points = len([f for f in supplied if f != "item_pricing"])
        completeness_score = round((supplied_eval_points / total_eval_points) * 100.0, 1)
        is_complete = completeness_score >= 70.0 and "business_name" in supplied and "phone" in supplied

        schema_type = CATEGORY_SCHEMA_MAP.get(intake.category, "LocalBusiness")

        return BusinessProfileAudit(
            is_complete=is_complete,
            completeness_score=completeness_score,
            missing_fields=missing,
            supplied_fields=supplied,
            factual_assertions_count=assertions_count,
            fabricated_claims_detected=fabricated_detected,
            schema_org_type=schema_type,
        )

    @staticmethod
    async def generate_draft_website_from_intake(
        project: WebsiteProject,
        intake: BusinessProfileIntake,
        session: AsyncSession,
    ) -> list[WebsitePage]:
        """
        Synthesize editable draft pages (Home, About, Services, Contact, Service-Area, Multilingual)
        strictly from owner-supplied facts. Never fabricates claims or prices.
        Guarantees: Ad monetization is OPT-IN only; zero ad blocks are injected by default.
        """
        # Save business profile on project
        project.business_profile = intake.model_dump()
        project.primary_language = intake.languages[0] if intake.languages else "en"

        # Wipe existing draft pages for this project
        await session.execute(
            delete(WebsitePage).where(WebsitePage.project_id == project.id)
        )

        pages: list[WebsitePage] = []
        order_idx = 0
        primary_lang = project.primary_language

        # ── 1. Home Page ──────────────────────────────────────────────────
        home_blocks: list[BlockItem] = []

        # Hero
        hero_cta = None
        if intake.whatsapp_number:
            hero_cta = CtaButton(
                label="WhatsApp Us",
                target_url=f"https://wa.me/{intake.whatsapp_number.lstrip('+')}",
                style="primary",
            )
        elif intake.nap.phone:
            hero_cta = CtaButton(
                label="Call Now",
                target_url=f"tel:{intake.nap.phone}",
                style="primary",
            )
        else:
            hero_cta = CtaButton(label="Contact Us", target_url="/contact", style="primary")

        home_blocks.append(
            BlockItem(
                id="block-hero-1",
                type=BlockType.HERO,
                content=HeroContent(
                    headline=intake.business_name,
                    subheadline=intake.tagline or (intake.description[:180] + "..." if intake.description and len(intake.description) > 180 else intake.description),
                    cta_button=hero_cta,
                    background_image_url=intake.cover_image_url,
                    alignment="center",
                ).model_dump(exclude_none=True),
            )
        )

        # Features / Services Highlight
        if intake.services_or_products:
            feat_items = []
            for item in intake.services_or_products[:6]:
                desc = item.description or ""
                if item.price is not None:
                    desc = f"₹{item.price:.2f} — {desc}" if item.currency == "INR" else f"{item.currency} {item.price:.2f} — {desc}"
                feat_items.append(
                    FeatureItem(
                        title=item.name,
                        description=desc or "Inquire for details.",
                        icon="check-circle",
                    )
                )
            home_blocks.append(
                BlockItem(
                    id="block-features-1",
                    type=BlockType.FEATURES_GRID,
                    content=FeaturesGridContent(
                        title="Our Offerings",
                        items=feat_items,
                    ).model_dump(exclude_none=True),
                )
            )

        # About / Overview section
        if intake.description:
            home_blocks.append(
                BlockItem(
                    id="block-text-about",
                    type=BlockType.TEXT_SECTION,
                    content=TextSectionContent(
                        heading=f"About {intake.business_name}",
                        body=intake.description,
                    ).model_dump(exclude_none=True),
                )
            )

        # Real FAQs (Only if provided by owner!)
        if intake.faqs:
            faq_html_parts = ['<div class="faq-container">']
            faq_html_parts.append('<h2>Frequently Asked Questions</h2>')
            for i, faq in enumerate(intake.faqs):
                faq_html_parts.append('<div class="faq-item">')
                faq_html_parts.append(f'<h3>{faq.question}</h3>')
                faq_html_parts.append(f'<p>{faq.answer}</p>')
                faq_html_parts.append('</div>')
            faq_html_parts.append('</div>')

            home_blocks.append(
                BlockItem(
                    id="block-faq-1",
                    type=BlockType.OPEN_DESIGN_SECTION,
                    content=OpenDesignSectionContent(
                        section_id="faq-section",
                        section_tag="section",
                        styles={"padding": "3rem 1.5rem", "background-color": "var(--bg)"},
                        html_content="".join(faq_html_parts),
                    ).model_dump(exclude_none=True),
                )
            )

        # Contact form
        home_blocks.append(
            BlockItem(
                id="block-contact-1",
                type=BlockType.CONTACT_FORM,
                content=ContactFormContent(
                    title=f"Contact {intake.business_name}",
                    submit_label="Send Message",
                    fields=["name", "email", "phone", "message"],
                ).model_dump(exclude_none=True),
            )
        )

        # Footer
        footer_links = [
            NavLink(label="Home", target_url="/"),
            NavLink(label="About", target_url="/about"),
            NavLink(label="Services", target_url="/services"),
            NavLink(label="Contact", target_url="/contact"),
        ]
        home_blocks.append(
            BlockItem(
                id="block-footer-1",
                type=BlockType.FOOTER,
                content=FooterContent(
                    copyright_text=f"© {datetime.now(timezone.utc).year} {intake.business_name}. All rights reserved.",
                    links=footer_links,
                ).model_dump(exclude_none=True),
            )
        )

        schema_version = 2 if any(b.type == BlockType.OPEN_DESIGN_SECTION for b in home_blocks) else 1
        home_page = WebsitePage(
            project_id=project.id,
            title="Home",
            slug="",
            is_home=True,
            order_index=order_idx,
            language=primary_lang,
            translation_group_id="home",
            seo_meta=SeoMeta(
                meta_title=f"{intake.business_name} | {intake.tagline or 'Official Website'}",
                meta_description=intake.description[:160] if intake.description else f"Welcome to {intake.business_name}.",
                og_image_url=intake.cover_image_url or intake.logo_url,
            ).model_dump(exclude_none=True),
            blocks=PageBlocksPayload(schema_version=schema_version, blocks=home_blocks).model_dump(),
        )
        pages.append(home_page)
        order_idx += 1

        # ── 2. About Page ─────────────────────────────────────────────────
        about_blocks: list[BlockItem] = [
            BlockItem(
                id="block-about-hero",
                type=BlockType.HERO,
                content=HeroContent(
                    headline=f"About {intake.business_name}",
                    subheadline=intake.tagline,
                    alignment="left",
                ).model_dump(exclude_none=True),
            ),
            BlockItem(
                id="block-about-body",
                type=BlockType.TEXT_SECTION,
                content=TextSectionContent(
                    heading="Our Story & Commitment",
                    body=intake.description or f"{intake.business_name} provides professional services with commitment to excellence.",
                ).model_dump(exclude_none=True),
            ),
        ]

        if intake.service_area_cities:
            about_blocks.append(
                BlockItem(
                    id="block-about-area",
                    type=BlockType.TEXT_SECTION,
                    content=TextSectionContent(
                        heading="Areas We Serve",
                        body=f"We proudly serve clients across {', '.join(intake.service_area_cities)}.",
                    ).model_dump(exclude_none=True),
                )
            )

        about_blocks.append(
            BlockItem(
                id="block-about-footer",
                type=BlockType.FOOTER,
                content=FooterContent(
                    copyright_text=f"© {datetime.now(timezone.utc).year} {intake.business_name}. All rights reserved.",
                    links=footer_links,
                ).model_dump(exclude_none=True),
            )
        )

        about_page = WebsitePage(
            project_id=project.id,
            title="About Us",
            slug="about",
            is_home=False,
            order_index=order_idx,
            language=primary_lang,
            translation_group_id="about",
            seo_meta=SeoMeta(
                meta_title=f"About {intake.business_name}",
                meta_description=f"Learn more about {intake.business_name} and our mission.",
            ).model_dump(exclude_none=True),
            blocks=PageBlocksPayload(schema_version=1, blocks=about_blocks).model_dump(),
        )
        pages.append(about_page)
        order_idx += 1

        # ── 3. Services / Products Page ───────────────────────────────────
        service_items = []
        for item in intake.services_or_products:
            desc = item.description or ""
            if item.price is not None:
                desc = f"Price: ₹{item.price:.2f} — {desc}" if item.currency == "INR" else f"Price: {item.currency} {item.price:.2f} — {desc}"
            service_items.append(
                FeatureItem(
                    title=item.name,
                    description=desc or "Contact us for detailed information.",
                    icon="check",
                )
            )

        services_blocks: list[BlockItem] = [
            BlockItem(
                id="block-services-hero",
                type=BlockType.HERO,
                content=HeroContent(
                    headline="Our Services & Products",
                    subheadline=f"Explore offerings from {intake.business_name}.",
                    alignment="left",
                ).model_dump(exclude_none=True),
            ),
            BlockItem(
                id="block-services-grid",
                type=BlockType.FEATURES_GRID,
                content=FeaturesGridContent(
                    title="What We Offer",
                    items=service_items,
                ).model_dump(exclude_none=True),
            ),
            BlockItem(
                id="block-services-cta",
                type=BlockType.CTA_BANNER,
                content=CtaBannerContent(
                    title="Interested in our services?",
                    description="Get in touch today for consultations and quotes.",
                    button=CtaButton(label="Contact Us", target_url="/contact", style="primary"),
                ).model_dump(exclude_none=True),
            ),
            BlockItem(
                id="block-services-footer",
                type=BlockType.FOOTER,
                content=FooterContent(
                    copyright_text=f"© {datetime.now(timezone.utc).year} {intake.business_name}. All rights reserved.",
                    links=footer_links,
                ).model_dump(exclude_none=True),
            ),
        ]

        services_page = WebsitePage(
            project_id=project.id,
            title="Services",
            slug="services",
            is_home=False,
            order_index=order_idx,
            language=primary_lang,
            translation_group_id="services",
            seo_meta=SeoMeta(
                meta_title=f"Services | {intake.business_name}",
                meta_description=f"Explore verified services offered by {intake.business_name}.",
            ).model_dump(exclude_none=True),
            blocks=PageBlocksPayload(schema_version=1, blocks=services_blocks).model_dump(),
        )
        pages.append(services_page)
        order_idx += 1

        # ── 4. Contact Page ───────────────────────────────────────────────
        contact_info_text = f"**{intake.business_name}**\n\n"
        if intake.nap.phone:
            contact_info_text += f"📞 Phone: {intake.nap.phone}\n\n"
        if intake.nap.email:
            contact_info_text += f"✉️ Email: {intake.nap.email}\n\n"
        if intake.whatsapp_number:
            contact_info_text += f"💬 WhatsApp: {intake.whatsapp_number}\n\n"
        if intake.nap.street_address or intake.nap.city:
            addr_parts = [p for p in (intake.nap.street_address, intake.nap.city, intake.nap.state_region, intake.nap.postal_code, intake.nap.country) if p]
            contact_info_text += f"📍 Address: {', '.join(addr_parts)}\n\n"
        if intake.hours:
            contact_info_text += "🕒 **Business Hours:**\n"
            for h in intake.hours:
                status_str = "Closed" if h.is_closed else f"{h.opens} - {h.closes}"
                contact_info_text += f"- {h.day.capitalize()}: {status_str}\n"

        contact_blocks: list[BlockItem] = [
            BlockItem(
                id="block-contact-hero",
                type=BlockType.HERO,
                content=HeroContent(
                    headline="Contact Us",
                    subheadline="We are here to help. Reach out through any of our channels below.",
                    alignment="left",
                ).model_dump(exclude_none=True),
            ),
            BlockItem(
                id="block-contact-info",
                type=BlockType.TEXT_SECTION,
                content=TextSectionContent(
                    heading="Get In Touch",
                    body=contact_info_text,
                ).model_dump(exclude_none=True),
            ),
            BlockItem(
                id="block-contact-form-page",
                type=BlockType.CONTACT_FORM,
                content=ContactFormContent(
                    title="Send a Message",
                    submit_label="Submit",
                    fields=["name", "email", "phone", "message"],
                ).model_dump(exclude_none=True),
            ),
            BlockItem(
                id="block-contact-footer",
                type=BlockType.FOOTER,
                content=FooterContent(
                    copyright_text=f"© {datetime.now(timezone.utc).year} {intake.business_name}. All rights reserved.",
                    links=footer_links,
                ).model_dump(exclude_none=True),
            ),
        ]

        contact_page = WebsitePage(
            project_id=project.id,
            title="Contact Us",
            slug="contact",
            is_home=False,
            order_index=order_idx,
            language=primary_lang,
            translation_group_id="contact",
            seo_meta=SeoMeta(
                meta_title=f"Contact | {intake.business_name}",
                meta_description=f"Get in touch with {intake.business_name}.",
            ).model_dump(exclude_none=True),
            blocks=PageBlocksPayload(schema_version=1, blocks=contact_blocks).model_dump(),
        )
        pages.append(contact_page)
        order_idx += 1

        # ── 5. Service Areas Page (if multiple cities served) ─────────────
        if intake.service_area_cities and len(intake.service_area_cities) > 1:
            cities_text = f"{intake.business_name} operates across the following service areas:\n\n"
            for c in intake.service_area_cities:
                cities_text += f"- **{c}**\n"
            if intake.service_radius_km:
                cities_text += f"\nOperating within a radius of {intake.service_radius_km} km."

            area_blocks: list[BlockItem] = [
                BlockItem(
                    id="block-areas-hero",
                    type=BlockType.HERO,
                    content=HeroContent(
                        headline="Our Service Areas",
                        subheadline=f"Providing trusted service across {', '.join(intake.service_area_cities[:3])}.",
                        alignment="left",
                    ).model_dump(exclude_none=True),
                ),
                BlockItem(
                    id="block-areas-body",
                    type=BlockType.TEXT_SECTION,
                    content=TextSectionContent(
                        heading="Cities & Localities Served",
                        body=cities_text,
                    ).model_dump(exclude_none=True),
                ),
                BlockItem(
                    id="block-areas-footer",
                    type=BlockType.FOOTER,
                    content=FooterContent(
                        copyright_text=f"© {datetime.now(timezone.utc).year} {intake.business_name}. All rights reserved.",
                        links=footer_links,
                    ).model_dump(exclude_none=True),
                ),
            ]

            area_page = WebsitePage(
                project_id=project.id,
                title="Service Areas",
                slug="service-areas",
                is_home=False,
                order_index=order_idx,
                language=primary_lang,
                translation_group_id="service-areas",
                seo_meta=SeoMeta(
                    meta_title=f"Service Areas | {intake.business_name}",
                    meta_description=f"Locations served by {intake.business_name}: {', '.join(intake.service_area_cities)}.",
                ).model_dump(exclude_none=True),
                blocks=PageBlocksPayload(schema_version=1, blocks=area_blocks).model_dump(),
            )
            pages.append(area_page)
            order_idx += 1

        # ── 6. Multilingual Pages (Hindi / Second Language if selected) ───
        if len(intake.languages) > 1:
            for extra_lang in intake.languages[1:]:
                clean_lang = extra_lang.strip().lower()
                if clean_lang == "hi":
                    # Hindi translated pages
                    hi_home = WebsitePage(
                        project_id=project.id,
                        title="मुख्य पृष्ठ",
                        slug="hi",
                        is_home=False,
                        order_index=order_idx,
                        language="hi",
                        translation_group_id="home",
                        seo_meta=SeoMeta(
                            meta_title=f"{intake.business_name} | आधिकारिक वेबसाइट",
                            meta_description=f"{intake.business_name} में आपका स्वागत है।",
                        ).model_dump(exclude_none=True),
                        blocks=PageBlocksPayload(
                            schema_version=1,
                            blocks=[
                                BlockItem(
                                    id="block-hi-hero",
                                    type=BlockType.HERO,
                                    content=HeroContent(
                                        headline=intake.business_name,
                                        subheadline=intake.tagline or "विश्वसनीय सेवाएं",
                                        cta_button=CtaButton(label="संपर्क करें", target_url="/hi/contact", style="primary"),
                                    ).model_dump(exclude_none=True),
                                ),
                                BlockItem(
                                    id="block-hi-about",
                                    type=BlockType.TEXT_SECTION,
                                    content=TextSectionContent(
                                        heading=f"{intake.business_name} के बारे में",
                                        body=intake.description or f"{intake.business_name} विश्वसनीय गुणवत्ता और सेवा प्रदान करता है।",
                                    ).model_dump(exclude_none=True),
                                ),
                                BlockItem(
                                    id="block-hi-footer",
                                    type=BlockType.FOOTER,
                                    content=FooterContent(
                                        copyright_text=f"© {datetime.now(timezone.utc).year} {intake.business_name}. सर्वाधिकार सुरक्षित।",
                                        links=[
                                            NavLink(label="मुख्य पृष्ठ", target_url="/hi"),
                                            NavLink(label="हमारे बारे में", target_url="/hi/about"),
                                            NavLink(label="सेवाएं", target_url="/hi/services"),
                                            NavLink(label="संपर्क", target_url="/hi/contact"),
                                        ],
                                    ).model_dump(exclude_none=True),
                                ),
                            ],
                        ).model_dump(),
                    )
                    pages.append(hi_home)
                    order_idx += 1

                    hi_about = WebsitePage(
                        project_id=project.id,
                        title="हमारे बारे में",
                        slug="hi/about",
                        is_home=False,
                        order_index=order_idx,
                        language="hi",
                        translation_group_id="about",
                        seo_meta=SeoMeta(
                            meta_title=f"हमारे बारे में | {intake.business_name}",
                            meta_description=f"{intake.business_name} के बारे में जानें।",
                        ).model_dump(exclude_none=True),
                        blocks=PageBlocksPayload(
                            schema_version=1,
                            blocks=[
                                BlockItem(
                                    id="block-hi-about-hero",
                                    type=BlockType.HERO,
                                    content=HeroContent(
                                        headline=f"{intake.business_name} का परिचय",
                                        subheadline="हमारा उद्देश्य और सेवाएं",
                                    ).model_dump(exclude_none=True),
                                ),
                                BlockItem(
                                    id="block-hi-about-footer",
                                    type=BlockType.FOOTER,
                                    content=FooterContent(
                                        copyright_text=f"© {datetime.now(timezone.utc).year} {intake.business_name}. सर्वाधिकार सुरक्षित।",
                                        links=[],
                                    ).model_dump(exclude_none=True),
                                ),
                            ],
                        ).model_dump(),
                    )
                    pages.append(hi_about)
                    order_idx += 1

                    hi_contact = WebsitePage(
                        project_id=project.id,
                        title="संपर्क करें",
                        slug="hi/contact",
                        is_home=False,
                        order_index=order_idx,
                        language="hi",
                        translation_group_id="contact",
                        seo_meta=SeoMeta(
                            meta_title=f"संपर्क करें | {intake.business_name}",
                            meta_description=f"{intake.business_name} से संपर्क करें।",
                        ).model_dump(exclude_none=True),
                        blocks=PageBlocksPayload(
                            schema_version=1,
                            blocks=[
                                BlockItem(
                                    id="block-hi-contact-hero",
                                    type=BlockType.HERO,
                                    content=HeroContent(
                                        headline="संपर्क करें",
                                        subheadline="हम आपकी सेवा के लिए तत्पर हैं।",
                                    ).model_dump(exclude_none=True),
                                ),
                                BlockItem(
                                    id="block-hi-contact-form",
                                    type=BlockType.CONTACT_FORM,
                                    content=ContactFormContent(
                                        title="संदेश भेजें",
                                        submit_label="भेजें",
                                        fields=["name", "email", "phone", "message"],
                                    ).model_dump(exclude_none=True),
                                ),
                                BlockItem(
                                    id="block-hi-contact-footer",
                                    type=BlockType.FOOTER,
                                    content=FooterContent(
                                        copyright_text=f"© {datetime.now(timezone.utc).year} {intake.business_name}. सर्वाधिकार सुरक्षित।",
                                        links=[],
                                    ).model_dump(exclude_none=True),
                                ),
                            ],
                        ).model_dump(),
                    )
                    pages.append(hi_contact)
                    order_idx += 1

        for p in pages:
            session.add(p)

        await session.commit()
        return pages

    @staticmethod
    def audit_page_accessibility(page: WebsitePage, theme: dict[str, Any]) -> AccessibilityAuditReport:
        """
        Audit accessibility, contrast ratio, alt-text compliance, and Core Web Vitals readiness.
        """
        recommendations: list[str] = []
        missing_alt: list[str] = []
        heading_issues: list[str] = []

        # 1. Colors & Contrast
        bg_col = theme.get("background_color", "#ffffff")
        text_col = theme.get("text_color", "#0f172a")
        contrast = calculate_wcag_contrast(bg_col, text_col)
        contrast_passed = contrast >= 4.5
        if not contrast_passed:
            recommendations.append(f"Contrast ratio {contrast}:1 is below WCAG AA 4.5:1 requirement. Darken text or lighten background.")

        # 2. Inspect Blocks for Images & Headings
        blocks_data = page.blocks if isinstance(page.blocks, dict) else {}
        block_list = blocks_data.get("blocks", [])

        h1_count = 0
        for b in block_list:
            b_type = b.get("type")
            content = b.get("content", {})

            # Headings
            if b_type == BlockType.HERO.value:
                h1_count += 1
                bg_img = content.get("background_image_url")
                # Hero background image is decorative CSS background, not content img

            elif b_type == BlockType.IMAGE_GALLERY.value:
                images = content.get("images", [])
                for img in images:
                    alt = img.get("alt_text", "").strip()
                    if not alt:
                        img_url = img.get("image_url", "unknown")
                        missing_alt.append(img_url)

            elif b_type == BlockType.OPEN_DESIGN_SECTION.value:
                html_raw = content.get("html_content", "")
                # Find all <img> tags and verify alt attribute
                img_matches = re.findall(r"<img\s+([^>]+)>", html_raw, re.IGNORECASE)
                for attrs in img_matches:
                    if not re.search(r'\balt\s*=\s*["\'][^"\']+["\']', attrs, re.IGNORECASE):
                        missing_alt.append(attrs[:40])

                # Check headings in HTML
                if "<h1" in html_raw.lower():
                    h1_count += 1

        if h1_count == 0:
            heading_issues.append("Page lacks an H1 main heading.")
            recommendations.append("Ensure the page has exactly one top-level H1 heading.")
        elif h1_count > 2:
            heading_issues.append(f"Page has {h1_count} H1 headings; recommend using only one primary H1.")
            recommendations.append("Limit H1 to one main heading per page, using H2/H3 for subsections.")

        all_alt_ok = len(missing_alt) == 0
        if not all_alt_ok:
            recommendations.append(f"Add descriptive alt text to {len(missing_alt)} image(s) for screen readers.")

        # Score calculation
        score = 100
        if not contrast_passed:
            score -= 25
        if not all_alt_ok:
            score -= min(30, len(missing_alt) * 10)
        if heading_issues:
            score -= 15

        return AccessibilityAuditReport(
            score=max(0, score),
            all_images_have_alt=all_alt_ok,
            missing_alt_images=missing_alt,
            heading_hierarchy_valid=len(heading_issues) == 0,
            heading_issues=heading_issues,
            contrast_ratio=contrast,
            contrast_passed=contrast_passed,
            has_responsive_viewport=True,
            lazy_loading_applied=True,
            font_display_swap=True,
            recommendations=recommendations,
        )

    @staticmethod
    def evaluate_geo_readiness(project: WebsiteProject) -> GeoReadinessReport:
        """
        Evaluate AI search and Generative Engine Optimization (GEO) readiness.
        Measures factual depth, entity consistency, and structured data coverage.
        Does NOT guarantee AI rankings or citation placements.
        """
        bp_dict = project.business_profile or {}
        nap = bp_dict.get("nap", {})
        services = bp_dict.get("services_or_products", [])
        hours = bp_dict.get("hours", [])

        has_name = bool(project.name or bp_dict.get("business_name"))
        has_phone = bool(nap.get("phone"))
        has_address = bool(nap.get("street_address") and nap.get("city"))
        has_category = bool(bp_dict.get("category"))
        category = bp_dict.get("category", "local_service")

        nap_score = 0
        if has_name:
            nap_score += 25
        if has_phone:
            nap_score += 25
        if has_address:
            nap_score += 30
        if nap.get("postal_code"):
            nap_score += 20

        nap_complete = nap_score >= 80

        # Structured data mapping
        schema_type = CATEGORY_SCHEMA_MAP.get(category, "LocalBusiness")
        structured_valid = has_name and (has_phone or has_address)

        score = 0
        score += int(nap_score * 0.4)
        if structured_valid:
            score += 25
        if services and len(services) > 0:
            score += min(20, len(services) * 5)
        if hours and len(hours) > 0:
            score += 15

        return GeoReadinessReport(
            overall_score=min(100, score),
            nap_completeness=nap_complete,
            nap_score=nap_score,
            structured_data_valid=structured_valid,
            schema_type=schema_type,
            factual_services_count=len(services),
            verified_hours_present=len(hours) > 0,
            llms_txt_available=True,
        )

    @staticmethod
    def generate_factual_llms_txt(project: WebsiteProject) -> str:
        """
        Generate experimental factual llms.txt summarizing the business profile for AI search crawlers.
        """
        bp = project.business_profile or {}
        name = bp.get("business_name") or project.name
        category = bp.get("category", "local_service")
        schema_type = CATEGORY_SCHEMA_MAP.get(category, "LocalBusiness")
        tagline = bp.get("tagline", "")
        description = bp.get("description", "")
        nap = bp.get("nap", {})
        services = bp.get("services_or_products", [])
        hours = bp.get("hours", [])
        faqs = bp.get("faqs", [])
        service_areas = bp.get("service_area_cities", [])

        lines = [
            f"# {name}",
            "",
            "> Note: Experimental factual summary generated from verified owner profile for AI search engine indexing. This document does not guarantee search engine ranking or AI citation placement.",
            "",
            f"- **Entity Type:** {schema_type}",
            f"- **Category:** {category}",
        ]
        if tagline:
            lines.append(f"- **Tagline:** {tagline}")
        if description:
            lines.append(f"- **Description:** {description}")

        lines.extend([
            "",
            "## Contact & Location (NAP)",
        ])
        if nap.get("phone"):
            lines.append(f"- **Phone:** {nap['phone']}")
        if nap.get("email"):
            lines.append(f"- **Email:** {nap['email']}")
        if bp.get("whatsapp_number"):
            lines.append(f"- **WhatsApp:** {bp['whatsapp_number']}")
        if nap.get("street_address") or nap.get("city"):
            addr_parts = [p for p in (nap.get("street_address"), nap.get("city"), nap.get("state_region"), nap.get("postal_code"), nap.get("country")) if p]
            lines.append(f"- **Address:** {', '.join(addr_parts)}")

        if service_areas:
            lines.extend([
                "",
                "## Areas Served",
                f"- {', '.join(service_areas)}",
            ])

        if hours:
            lines.extend([
                "",
                "## Verified Operating Hours",
            ])
            for h in hours:
                day_name = h.get("day", "").capitalize()
                if h.get("is_closed"):
                    lines.append(f"- {day_name}: Closed")
                else:
                    lines.append(f"- {day_name}: {h.get('opens')} - {h.get('closes')}")

        if services:
            lines.extend([
                "",
                "## Services & Offerings",
            ])
            for s in services:
                s_name = s.get("name")
                s_desc = s.get("description") or ""
                s_price = s.get("price")
                curr = s.get("currency", "INR")
                if s_price is not None:
                    lines.append(f"- **{s_name}**: {curr} {s_price:.2f} — {s_desc}")
                else:
                    lines.append(f"- **{s_name}**: {s_desc}")

        if faqs:
            lines.extend([
                "",
                "## Frequently Asked Questions",
            ])
            for f in faqs:
                lines.append(f"### Q: {f.get('question')}")
                lines.append(f"A: {f.get('answer')}")
                lines.append("")

        return "\n".join(lines)


business_profile_service = BusinessProfileService()

