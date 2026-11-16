"""Safe server-side HTML rendering engine for published website builder pages."""

from __future__ import annotations

import html
import json
import re
from typing import Any
from urllib.parse import urlparse

import jinja2

from liteads.common.logger import get_logger
from liteads.schemas.business_profile import CATEGORY_SCHEMA_MAP
from liteads.schemas.website import (
    ALLOWED_CSS_PROPERTIES,
    ALLOWED_FONTS,
    HEX_COLOR_PATTERN,
    SPOOFING_CLASS_OR_ID_PATTERNS,
    BlockType,
    PageBlocksPayload,
    ThemeConfig,
    validate_and_sanitize_open_design_html,
)

logger = get_logger(__name__)

# Safe CSS border-radius token pattern
BORDER_RADIUS_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+)?(?:px|rem|em|%)?$")

# Safe block ID token pattern (1-64 alphanumeric, underscores, hyphens)
BLOCK_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")

# Max HTML output size limit (1 MB)
MAX_OUTPUT_HTML_BYTES = 1024 * 1024


def safe_url(url: str | None) -> str:
    """
    Ensure URL is safe for HTML attributes (href, src).
    
    Permits only http://, https://, mailto:, tel:, and relative / URLs.
    Rejects javascript:, data:, vbscript:, and control characters.
    """
    if not url:
        return "#"
    clean = str(url).strip()
    if any(c in clean for c in ("\r", "\n", "\t", "<", ">", '"', "'", "\0")):
        return "#"
    clean_lower = clean.lower()
    if clean_lower.startswith(("javascript:", "data:", "vbscript:", "blob:")):
        return "#"
    if clean.startswith(("/", "http://", "https://", "mailto:", "tel:")):
        return clean
    return "#"


def safe_theme_config(raw_theme: dict[str, Any] | None) -> dict[str, str]:
    """Sanitize and validate theme properties to completely prevent CSS injection."""
    defaults = {
        "primary_color": "#2563eb",
        "secondary_color": "#475569",
        "background_color": "#ffffff",
        "text_color": "#0f172a",
        "font_family": "Inter",
        "border_radius": "0.5rem",
        "logo_url": "",
        "favicon_url": "",
    }
    if not isinstance(raw_theme, dict):
        return defaults

    theme = defaults.copy()

    # Hex colors
    for col in ("primary_color", "secondary_color", "background_color", "text_color"):
        val = raw_theme.get(col)
        if isinstance(val, str) and HEX_COLOR_PATTERN.match(val.strip()):
            theme[col] = val.strip()

    # Font family
    font = raw_theme.get("font_family")
    if isinstance(font, str) and font.strip() in ALLOWED_FONTS:
        theme["font_family"] = font.strip()

    # Border radius
    radius = raw_theme.get("border_radius")
    if isinstance(radius, str) and BORDER_RADIUS_PATTERN.match(radius.strip()):
        theme["border_radius"] = radius.strip()

    # Safe URLs for logo and favicon
    logo = raw_theme.get("logo_url")
    if logo:
        theme["logo_url"] = safe_url(logo)
    fav = raw_theme.get("favicon_url")
    if fav:
        theme["favicon_url"] = safe_url(fav)

    return theme


def safe_open_design_html_for_render(raw_html: str) -> str:
    """Re-validate and sanitize Open Design HTML at the rendering boundary."""
    try:
        return validate_and_sanitize_open_design_html(raw_html)
    except Exception as e:
        logger.warning("Failed rendering Open Design section HTML safely: %s", e)
        # Defense-in-depth: Fallback to escaping to guarantee page rendering cannot break or execute scripts
        return f"<!-- Sanitization notice: {html.escape(str(e))} --><div>{html.escape(raw_html)}</div>"


# ── Jinja2 Templates ────────────────────────────────────────────────────────

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ page_lang|default('en') }}">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{ page_title }} | {{ project_name }}</title>
  {% if meta_description %}<meta name="description" content="{{ meta_description }}">{% endif %}
  <meta name="robots" content="{{ robots_directive }}">
  <link rel="canonical" href="{{ canonical_url }}">
  {% if theme.favicon_url and theme.favicon_url != '#' %}
  <link rel="icon" href="{{ theme.favicon_url }}">
  {% endif %}

  <!-- Webmaster Verification -->
  {% if webmaster_tools and webmaster_tools.google_site_verification %}
  <meta name="google-site-verification" content="{{ webmaster_tools.google_site_verification }}">
  {% endif %}
  {% if webmaster_tools and webmaster_tools.bing_site_verification %}
  <meta name="msvalidate.01" content="{{ webmaster_tools.bing_site_verification }}">
  {% endif %}

  <!-- Multilingual Alternate Hreflang (rendered only when real translated pages exist) -->
  {% for alt in hreflang_links %}
  <link rel="alternate" hreflang="{{ alt.lang }}" href="{{ alt.url }}">
  {% endfor %}

  <!-- Open Graph -->
  <meta property="og:title" content="{{ page_title }} | {{ project_name }}">
  {% if meta_description %}<meta property="og:description" content="{{ meta_description }}">{% endif %}
  <meta property="og:url" content="{{ canonical_url }}">
  <meta property="og:type" content="website">
  {% if og_image_url and og_image_url != '#' %}
  <meta property="og:image" content="{{ og_image_url }}">
  {% endif %}

  <!-- Schema.org JSON-LD Structured Data (from verified owner facts only) -->
  {% if json_ld_payloads %}
  {% for item in json_ld_payloads %}
  <script type="application/ld+json">
  {{ item | safe }}
  </script>
  {% endfor %}
  {% endif %}

  <!-- Typography -->
  {% if font_url %}
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="{{ font_url }}" rel="stylesheet">
  {% endif %}

  <style>
    :root {
      --primary: {{ theme.primary_color }};
      --secondary: {{ theme.secondary_color }};
      --bg: {{ theme.background_color }};
      --text: {{ theme.text_color }};
      --radius: {{ theme.border_radius }};
      --font-family: '{{ theme.font_family }}', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    }
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: var(--font-family);
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.6;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }
    a { color: var(--primary); text-decoration: none; }
    a:hover { text-decoration: underline; }
    .sr-only {
      position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
      overflow: hidden; clip: rect(0,0,0,0); border: 0;
    }
    .container { max-width: 1140px; margin: 0 auto; padding: 0 1.5rem; }

    /* Header & Navigation */
    .site-header {
      background: rgba(255, 255, 255, 0.95);
      backdrop-filter: blur(8px);
      border-bottom: 1px solid rgba(0, 0, 0, 0.08);
      position: sticky;
      top: 0;
      z-index: 100;
    }
    .header-inner {
      display: flex;
      align-items: center;
      justify-content: space-between;
      height: 4.25rem;
    }
    .site-brand {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      font-size: 1.25rem;
      font-weight: 700;
      color: var(--text);
      text-decoration: none;
    }
    .brand-logo { max-height: 2.5rem; width: auto; object-fit: contain; }
    .site-nav ul { display: flex; list-style: none; gap: 1.5rem; }
    .nav-link {
      font-weight: 500;
      color: var(--secondary);
      transition: color 0.15s;
    }
    .nav-link:hover, .nav-link.active {
      color: var(--primary);
      text-decoration: none;
    }

    /* Preview Banner */
    .preview-banner {
      background: #fef3c7;
      color: #92400e;
      border-bottom: 1px solid #fde68a;
      padding: 0.75rem 1.5rem;
      font-size: 0.875rem;
      text-align: center;
      font-weight: 500;
    }

    /* Hero Block */
    .block-hero {
      padding: 5rem 0;
      position: relative;
      background-size: cover;
      background-position: center;
    }
    .hero-content { max-width: 760px; }
    .hero-content.align-center { margin: 0 auto; text-align: center; }
    .hero-content.align-right { margin-left: auto; text-align: right; }
    .hero-headline {
      font-size: 2.75rem;
      font-weight: 800;
      line-height: 1.2;
      margin-bottom: 1.25rem;
      letter-spacing: -0.02em;
    }
    .hero-subheadline {
      font-size: 1.25rem;
      color: var(--secondary);
      margin-bottom: 2rem;
      line-height: 1.5;
    }
    .btn {
      display: inline-block;
      padding: 0.75rem 1.75rem;
      border-radius: var(--radius);
      font-weight: 600;
      font-size: 1rem;
      transition: all 0.2s ease;
      cursor: pointer;
      text-decoration: none;
    }
    .btn-primary {
      background-color: var(--primary);
      color: #ffffff;
      border: 1px solid var(--primary);
    }
    .btn-primary:hover { opacity: 0.92; text-decoration: none; transform: translateY(-1px); }
    .btn-secondary {
      background-color: var(--secondary);
      color: #ffffff;
      border: 1px solid var(--secondary);
    }
    .btn-outline {
      background: transparent;
      color: var(--primary);
      border: 2px solid var(--primary);
    }

    /* Text Section Block */
    .block-text-section { padding: 3.5rem 0; }
    .text-heading {
      font-size: 2rem;
      font-weight: 700;
      margin-bottom: 1.25rem;
      color: var(--text);
    }
    .text-body {
      font-size: 1.125rem;
      line-height: 1.75;
      color: var(--secondary);
    }
    .text-body p { margin-bottom: 1.25rem; }

    /* Features Grid Block */
    .block-features-grid { padding: 4rem 0; background: rgba(0, 0, 0, 0.02); }
    .features-title { font-size: 2.25rem; text-align: center; margin-bottom: 3rem; font-weight: 700; }
    .features-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 2rem;
    }
    .feature-card {
      background: #ffffff;
      border: 1px solid rgba(0, 0, 0, 0.08);
      border-radius: var(--radius);
      padding: 2rem;
      transition: transform 0.2s, box-shadow 0.2s;
    }
    .feature-card:hover {
      transform: translateY(-4px);
      box-shadow: 0 12px 24px -10px rgba(0, 0, 0, 0.08);
    }
    .feature-icon-box {
      width: 3rem;
      height: 3rem;
      border-radius: var(--radius);
      background: rgba(37, 99, 235, 0.1);
      color: var(--primary);
      display: flex;
      align-items: center;
      justify-content: center;
      margin-bottom: 1.25rem;
      font-size: 1.25rem;
      font-weight: 700;
    }
    .feature-title { font-size: 1.25rem; font-weight: 600; margin-bottom: 0.5rem; }
    .feature-desc { color: var(--secondary); font-size: 0.95rem; }

    /* Image Gallery Block */
    .block-image-gallery { padding: 4rem 0; }
    .gallery-title { font-size: 2.25rem; text-align: center; margin-bottom: 2.5rem; font-weight: 700; }
    .gallery-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
      gap: 1.5rem;
    }
    .gallery-item {
      overflow: hidden;
      border-radius: var(--radius);
      border: 1px solid rgba(0, 0, 0, 0.08);
      background: #ffffff;
    }
    .gallery-img { width: 100%; height: 220px; object-fit: cover; display: block; }
    .gallery-caption { padding: 0.75rem 1rem; font-size: 0.875rem; color: var(--secondary); }

    /* Contact Form Block */
    .block-contact-form { padding: 4rem 0; background: rgba(0, 0, 0, 0.01); }
    .contact-card {
      max-width: 600px;
      margin: 0 auto;
      background: #ffffff;
      border: 1px solid rgba(0, 0, 0, 0.08);
      border-radius: var(--radius);
      padding: 2.5rem;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
    }
    .contact-title { font-size: 1.875rem; font-weight: 700; margin-bottom: 1.5rem; text-align: center; }
    .form-group { margin-bottom: 1.25rem; }
    .form-label { display: block; font-weight: 500; margin-bottom: 0.5rem; font-size: 0.9rem; }
    .form-input, .form-textarea {
      width: 100%;
      padding: 0.75rem 1rem;
      border: 1px solid #d1d5db;
      border-radius: var(--radius);
      font-size: 1rem;
      font-family: inherit;
    }
    .form-textarea { min-height: 120px; resize: vertical; }
    .form-input:focus, .form-textarea:focus {
      outline: none;
      border-color: var(--primary);
      box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.15);
    }

    /* CTA Banner Block */
    .block-cta-banner {
      padding: 4.5rem 0;
      background: var(--primary);
      color: #ffffff;
      text-align: center;
    }
    .cta-title { font-size: 2.25rem; font-weight: 800; margin-bottom: 1rem; }
    .cta-desc { font-size: 1.125rem; opacity: 0.9; max-width: 600px; margin: 0 auto 2rem; }
    .btn-light {
      background: #ffffff;
      color: var(--primary);
      border: 1px solid #ffffff;
    }
    .btn-light:hover { background: #f8fafc; color: var(--primary); text-decoration: none; }

    /* Ad Placement Block (Strict Non-Billable Sandbox Placeholder) */
    .block-ad-placement {
      padding: 2.5rem 0;
      display: flex;
      justify-content: center;
    }
    .rivinity-ad-placeholder {
      width: 100%;
      max-width: 728px;
      min-height: 90px;
      border: 2px dashed #cbd5e1;
      border-radius: var(--radius);
      background: #f8fafc;
      padding: 1.25rem;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      position: relative;
    }
    .ad-placeholder-header {
      display: flex;
      gap: 0.5rem;
      margin-bottom: 0.5rem;
    }
    .ad-badge {
      background: #e2e8f0;
      color: #475569;
      font-size: 0.6875rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      padding: 0.15rem 0.5rem;
      border-radius: 4px;
    }
    .ad-status {
      background: #fef3c7;
      color: #92400e;
      font-size: 0.6875rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      padding: 0.15rem 0.5rem;
      border-radius: 4px;
    }
    .ad-notice {
      font-size: 0.875rem;
      font-weight: 600;
      color: #334155;
    }
    .ad-subtext {
      font-size: 0.75rem;
      color: #64748b;
      margin-top: 0.25rem;
    }

    /* Footer Block */
    .site-footer {
      margin-top: auto;
      background: #0f172a;
      color: #94a3b8;
      padding: 3rem 0 2rem;
      border-top: 1px solid rgba(255, 255, 255, 0.08);
    }
    .footer-inner {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 1.5rem;
      text-align: center;
    }
    .footer-links {
      display: flex;
      list-style: none;
      gap: 1.5rem;
      flex-wrap: wrap;
      justify-content: center;
    }
    .footer-link { color: #cbd5e1; font-size: 0.9rem; }
    .footer-link:hover { color: #ffffff; }
    .footer-copy { font-size: 0.8125rem; color: #64748b; }
  </style>
</head>
<body>
  <a href="#main-content" class="sr-only">Skip to content</a>

  {% if is_preview %}
  <div class="preview-banner" role="alert" data-od-protected="true" data-od-editable="false">
    <strong>Draft Preview Mode</strong> — This is an unpublished draft preview of <em>{{ project_name }}</em>. Monetization and indexing are disabled.
  </div>
  {% endif %}

  <header class="site-header">
    <div class="container header-inner">
      <a href="{{ home_url }}" class="site-brand">
        {% if theme.logo_url and theme.logo_url != '#' %}
        <img src="{{ theme.logo_url }}" alt="{{ project_name }} logo" class="brand-logo" decoding="async">
        {% endif %}
        <span>{{ project_name }}</span>
      </a>
      <nav class="site-nav" aria-label="Main Navigation">
        <ul>
          {% for p in nav_pages %}
          <li>
            <a href="{{ p.url }}" class="nav-link {% if p.is_active %}active{% endif %}">
              {{ p.title }}
            </a>
          </li>
          {% endfor %}
        </ul>
      </nav>
    </div>
  </header>

  <main id="main-content">
    {% for block in blocks %}
      {% if block.type == 'hero' %}
      <section class="block-hero" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="hero"{% endif %} {% if block.content.background_image_url and block.content.background_image_url != '#' %}style="background-image: linear-gradient(rgba(0,0,0,0.4), rgba(0,0,0,0.4)), url('{{ block.content.background_image_url }}'); color: #ffffff;"{% endif %}>
        <div class="container">
          <div class="hero-content align-{{ block.content.alignment or 'left' }}">
            <h1 class="hero-headline" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="hero" data-od-field="headline" data-od-editable="true"{% endif %}>{{ block.content.headline }}</h1>
            {% if block.content.subheadline %}
            <p class="hero-subheadline" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="hero" data-od-field="subheadline" data-od-editable="true"{% endif %}>{{ block.content.subheadline }}</p>
            {% endif %}
            {% if block.content.cta_button and block.content.cta_button.target_url != '#' %}
            <a href="{{ block.content.cta_button.target_url }}" class="btn btn-{{ block.content.cta_button.style or 'primary' }}" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="hero" data-od-field="cta_button.label" data-od-editable="true"{% endif %}>
              {{ block.content.cta_button.label }}
            </a>
            {% endif %}
          </div>
        </div>
      </section>

      {% elif block.type == 'text_section' %}
      <section class="block-text-section" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="text_section"{% endif %}>
        <div class="container">
          {% if block.content.heading %}
          <h2 class="text-heading" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="text_section" data-od-field="heading" data-od-editable="true"{% endif %}>{{ block.content.heading }}</h2>
          {% endif %}
          <div class="text-body" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="text_section" data-od-field="body"{% endif %}>
            {% for para in block.content.paragraphs %}
            <p {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="text_section" data-od-field="body" data-od-item-index="{{ loop.index0 }}" data-od-editable="true"{% endif %}>{{ para }}</p>
            {% endfor %}
          </div>
        </div>
      </section>

      {% elif block.type == 'features_grid' %}
      <section class="block-features-grid" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="features_grid"{% endif %}>
        <div class="container">
          {% if block.content.title %}
          <h2 class="features-title" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="features_grid" data-od-field="title" data-od-editable="true"{% endif %}>{{ block.content.title }}</h2>
          {% endif %}
          <div class="features-grid">
            {% for item in block.content['items'] %}
            <div class="feature-card" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="features_grid" data-od-field="items" data-od-item-index="{{ loop.index0 }}"{% endif %}>
              <div class="feature-icon-box" aria-hidden="true" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="features_grid" data-od-field="items.icon" data-od-item-index="{{ loop.index0 }}" data-od-editable="false"{% endif %}>★</div>
              <h3 class="feature-title" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="features_grid" data-od-field="items.title" data-od-item-index="{{ loop.index0 }}" data-od-editable="true"{% endif %}>{{ item.title }}</h3>
              <p class="feature-desc" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="features_grid" data-od-field="items.description" data-od-item-index="{{ loop.index0 }}" data-od-editable="true"{% endif %}>{{ item.description }}</p>
            </div>
            {% endfor %}
          </div>
        </div>
      </section>

      {% elif block.type == 'image_gallery' %}
      <section class="block-image-gallery" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="image_gallery"{% endif %}>
        <div class="container">
          {% if block.content.title %}
          <h2 class="gallery-title" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="image_gallery" data-od-field="title" data-od-editable="true"{% endif %}>{{ block.content.title }}</h2>
          {% endif %}
          <div class="gallery-grid">
            {% for img in block.content.images %}
            {% if img.image_url != '#' %}
            <figure class="gallery-item" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="image_gallery" data-od-field="images" data-od-item-index="{{ loop.index0 }}"{% endif %}>
              <img src="{{ img.image_url }}" alt="{{ img.alt_text }}" loading="lazy" decoding="async" class="gallery-img" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="image_gallery" data-od-field="images.image_url" data-od-item-index="{{ loop.index0 }}" data-od-editable="true"{% endif %}>
              {% if img.caption %}
              <figcaption class="gallery-caption" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="image_gallery" data-od-field="images.caption" data-od-item-index="{{ loop.index0 }}" data-od-editable="true"{% endif %}>{{ img.caption }}</figcaption>
              {% endif %}
            </figure>
            {% endif %}
            {% endfor %}
          </div>
        </div>
      </section>

      {% elif block.type == 'contact_form' %}
      <section class="block-contact-form" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="contact_form"{% endif %}>
        <div class="container">
          <div class="contact-card">
            <h2 class="contact-title" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="contact_form" data-od-field="title" data-od-editable="true"{% endif %}>{{ block.content.title or 'Contact Us' }}</h2>
            <form method="POST" action="{{ contact_action_url }}" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="contact_form" data-od-field="fields" data-od-editable="false"{% endif %}>
              <div style="display:none !important;" aria-hidden="true" data-od-protected="true" data-od-editable="false">
                <input type="text" name="hp_comment" tabindex="-1" autocomplete="off" data-od-protected="true" data-od-editable="false">
              </div>
              {% for field in block.content.fields %}
                {% if field == 'message' %}
                <div class="form-group" data-od-editable="false">
                  <label for="contact-{{ field }}" class="form-label" data-od-editable="false">Message</label>
                  <textarea id="contact-{{ field }}" name="{{ field }}" class="form-textarea" required data-od-editable="false"></textarea>
                </div>
                {% elif field == 'email' %}
                <div class="form-group" data-od-editable="false">
                  <label for="contact-{{ field }}" class="form-label" data-od-editable="false">Email Address</label>
                  <input type="email" id="contact-{{ field }}" name="{{ field }}" class="form-input" required data-od-editable="false">
                </div>
                {% elif field == 'phone' %}
                <div class="form-group" data-od-editable="false">
                  <label for="contact-{{ field }}" class="form-label" data-od-editable="false">Phone Number</label>
                  <input type="tel" id="contact-{{ field }}" name="{{ field }}" class="form-input" data-od-editable="false">
                </div>
                {% else %}
                <div class="form-group" data-od-editable="false">
                  <label for="contact-{{ field }}" class="form-label" data-od-editable="false">{{ field|capitalize }}</label>
                  <input type="text" id="contact-{{ field }}" name="{{ field }}" class="form-input" required data-od-editable="false">
                </div>
                {% endif %}
              {% endfor %}
              <button type="submit" class="btn btn-primary" style="width: 100%;" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="contact_form" data-od-field="submit_label" data-od-editable="true"{% endif %}>
                {{ block.content.submit_label or 'Send Message' }}
              </button>
            </form>
          </div>
        </div>
      </section>

      {% elif block.type == 'cta_banner' %}
      <section class="block-cta-banner" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="cta_banner"{% endif %}>
        <div class="container">
          <h2 class="cta-title" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="cta_banner" data-od-field="title" data-od-editable="true"{% endif %}>{{ block.content.title }}</h2>
          {% if block.content.description %}
          <p class="cta-desc" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="cta_banner" data-od-field="description" data-od-editable="true"{% endif %}>{{ block.content.description }}</p>
          {% endif %}
          {% if block.content.button and block.content.button.target_url != '#' %}
          <a href="{{ block.content.button.target_url }}" class="btn btn-light" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="cta_banner" data-od-field="button.label" data-od-editable="true"{% endif %}>
            {{ block.content.button.label }}
          </a>
          {% endif %}
        </div>
      </section>

      {% elif block.type == 'ad_placement' %}
      <div class="container" data-od-protected="true" data-od-editable="false">
        <div class="block-ad-placement" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="ad_placement"{% endif %} data-location="{{ block.content.placement_location }}" data-od-protected="true" data-od-editable="false">
          {% if is_monetized and not is_preview and block.content.ad_slot_id %}
          <div class="rivinity-live-ad-container" role="region" aria-label="Sponsored Advertisement" data-od-protected="true" data-od-editable="false">
            <div id="rivinity-ad-{{ block.content.ad_slot_id }}{% if block.id %}-{{ block.id }}{% endif %}" class="rivinity-ad-slot" data-slot-id="{{ block.content.ad_slot_id }}" data-format="{{ block.content.format or 1 }}" data-od-protected="true" data-od-editable="false">
              <script async src="/static/rivinity.js" data-slot="{{ block.content.ad_slot_id }}" data-format="{{ block.content.format or 1 }}" data-site="{{ site_slug }}" data-od-protected="true"></script>
            </div>
            <div class="ad-label" style="font-size:0.6875rem;color:#94a3b8;text-align:right;margin-top:2px;" data-od-protected="true" data-od-editable="false">Sponsored</div>
          </div>
          {% else %}
          <div class="rivinity-ad-placeholder" data-slot="{{ block.content.ad_slot_id or '' }}" data-format="{{ block.content.format or 1 }}" role="region" aria-label="Advertisement Placeholder" data-od-protected="true" data-od-editable="false">
            <div class="ad-placeholder-header" data-od-protected="true" data-od-editable="false">
              <span class="ad-badge" data-od-protected="true" data-od-editable="false">Advertisement</span>
              <span class="ad-status" data-od-protected="true" data-od-editable="false">Non-Billable Sandbox</span>
            </div>
            <p class="ad-notice" data-od-protected="true" data-od-editable="false">RivinityADS Ad Placement Placeholder</p>
            <p class="ad-subtext" data-od-protected="true" data-od-editable="false">Slot: {{ block.content.ad_slot_id or 'Auto' }} &bull; Format: {{ 'Banner' if block.content.format == 1 else 'Native' }} &bull; Monetization Inactive</p>
          </div>
          {% endif %}
        </div>
      </div>

      {% elif block.type == 'open_design_section' %}
      <div class="block-open-design-wrapper" {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="open_design_section"{% endif %}>
        <{{ block.content.section_tag or 'section' }} {% if block.content.section_id %}id="{{ block.content.section_id }}"{% endif %} class="od-section{% if block.content.css_classes %} {{ block.content.css_classes }}{% endif %}" {% if block.content.inline_style %}style="{{ block.content.inline_style }}"{% endif %} {% if block.id %}data-od-block-id="{{ block.id }}" data-od-block-type="open_design_section"{% endif %}>
          {{ block.content.safe_html | safe }}
        </{{ block.content.section_tag or 'section' }}>
      </div>

      {% elif block.type == 'footer' %}
      <!-- Footer embedded via block -->
      {% endif %}
    {% endfor %}
  </main>

  <!-- Default or Structured Footer -->
  <footer class="site-footer" {% if footer_block_id %}data-od-block-id="{{ footer_block_id }}" data-od-block-type="footer"{% endif %}>
    <div class="container footer-inner">
      {% if footer_links %}
      <ul class="footer-links">
        {% for link in footer_links %}
        <li><a href="{{ link.target_url }}" class="footer-link" {% if footer_block_id %}data-od-block-id="{{ footer_block_id }}" data-od-block-type="footer" data-od-field="links.label" data-od-item-index="{{ loop.index0 }}" data-od-editable="true"{% endif %}>{{ link.label }}</a></li>
        {% endfor %}
      </ul>
      {% endif %}
      <p class="footer-copy" {% if footer_block_id %}data-od-block-id="{{ footer_block_id }}" data-od-block-type="footer" data-od-field="copyright_text" data-od-editable="true"{% endif %}>{{ footer_copyright or ('&copy; ' ~ project_name ~ '. Powered by RivinityADS.') }}</p>
    </div>
  </footer>

  {% if is_monetized and not is_preview %}
  <aside id="rivinity-consent-banner" class="rivinity-consent-banner" role="dialog" aria-label="Privacy and Advertising Choices" data-site="{{ site_slug }}" data-policy-version="v1.0" data-od-protected="true" data-od-editable="false" style="position:fixed;bottom:0;left:0;right:0;background:#1e293b;color:#f8fafc;padding:1rem 1.5rem;display:flex;align-items:center;justify-content:space-between;gap:1rem;z-index:999;box-shadow:0 -4px 12px rgba(0,0,0,0.15);font-size:0.875rem;">
    <div style="max-width:700px;" data-od-protected="true" data-od-editable="false">
      <strong>Privacy &amp; Advertising Choices:</strong> This website is monetized through RivinityADS. We and our advertising partners use cookies and device identifiers to personalize and measure ad performance. Non-essential categories default to denied.
    </div>
    <div style="display:flex;gap:0.75rem;flex-shrink:0;" data-od-protected="true" data-od-editable="false">
      <button id="rivinity-consent-accept" class="btn btn-primary" style="padding:0.4rem 1rem;font-size:0.8125rem;" data-od-protected="true" data-od-editable="false">Accept All</button>
      <button id="rivinity-consent-decline" class="btn btn-secondary" style="padding:0.4rem 1rem;font-size:0.8125rem;" data-od-protected="true" data-od-editable="false">Decline</button>
      <button id="rivinity-consent-manage" class="btn btn-outline" style="padding:0.4rem 0.8rem;font-size:0.8125rem;background:transparent;color:#94a3b8;border:1px solid #475569;" data-od-protected="true" data-od-editable="false">Manage</button>
    </div>
  </aside>

  <!-- Granular Consent Management Dialog -->
  <dialog id="rivinity-consent-modal" class="rivinity-consent-modal" role="dialog" aria-label="Manage Privacy Preferences" data-od-protected="true" data-od-editable="false" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);background:#1e293b;color:#f8fafc;padding:1.5rem 2rem;border-radius:12px;border:1px solid #334155;z-index:1000;box-shadow:0 20px 25px -5px rgba(0,0,0,0.5);max-width:500px;width:90%;">
    <h3 style="margin-top:0;font-size:1.125rem;color:#f8fafc;">Privacy &amp; Consent Preferences</h3>
    <p style="font-size:0.8125rem;color:#94a3b8;line-height:1.4;">Configure how your data and identifiers are processed for advertising and analytics. Non-essential categories are disabled by default.</p>
    <div style="margin:1rem 0;display:flex;flex-direction:column;gap:0.75rem;">
      <label style="display:flex;align-items:center;justify-content:space-between;padding:0.5rem 0.75rem;background:#0f172a;border-radius:6px;font-size:0.8125rem;">
        <span><strong>Essential Services:</strong> Required for site operation</span>
        <input type="checkbox" id="rivinity-consent-opt-necessary" checked disabled style="cursor:not-allowed;">
      </label>
      <label style="display:flex;align-items:center;justify-content:space-between;padding:0.5rem 0.75rem;background:#0f172a;border-radius:6px;font-size:0.8125rem;">
        <span><strong>Advertising &amp; Delivery:</strong> Personalized ad delivery and measurement</span>
        <input type="checkbox" id="rivinity-consent-opt-ad" style="cursor:pointer;">
      </label>
      <label style="display:flex;align-items:center;justify-content:space-between;padding:0.5rem 0.75rem;background:#0f172a;border-radius:6px;font-size:0.8125rem;">
        <span><strong>Analytics:</strong> Traffic and outcome measurement</span>
        <input type="checkbox" id="rivinity-consent-opt-analytics" style="cursor:pointer;">
      </label>
    </div>
    <div style="display:flex;justify-content:flex-end;gap:0.75rem;margin-top:1.25rem;">
      <button id="rivinity-consent-modal-cancel" class="btn btn-secondary" style="padding:0.4rem 1rem;font-size:0.8125rem;">Cancel</button>
      <button id="rivinity-consent-modal-save" class="btn btn-primary" style="padding:0.4rem 1rem;font-size:0.8125rem;">Save Preferences</button>
    </div>
  </dialog>

  <!-- Persistent Reopen Trigger for Withdrawal/Settings -->
  <button id="rivinity-consent-reopen" class="rivinity-privacy-trigger" data-site="{{ site_slug }}" data-od-protected="true" data-od-editable="false" style="display:none;position:fixed;bottom:12px;left:12px;z-index:998;background:#1e293b;color:#94a3b8;font-size:0.6875rem;padding:4px 10px;border-radius:9999px;border:1px solid #334155;cursor:pointer;">Privacy Choices</button>
  {% endif %}

  {% if bridge_nonce %}
  <script nonce="{{ bridge_nonce }}" data-od-edit-bridge>
    (function() {
      // RivinityADS Open Design Phase 3.1 & 3.3A: Secure Bridge & Overlay Engine
      var bridgePort = null;
      var activeSessionId = null;

      // ── Stage 3.3A: Overlay Guide Layer & DOM Interaction Engine ─────────────
      var PROTECTED_SELECTORS = [
        '[data-od-protected="true"]',
        '.block-ad-placement',
        '.rivinity-ad-placeholder',
        '.rivinity-live-ad-container',
        '.rivinity-ad-slot',
        '#rivinity-consent-banner',
        '.preview-banner',
        '[name="hp_comment"]',
        '[data-od-edit-bridge]'
      ].join(', ');

      var BLOCK_ID_REGEX = /^[a-zA-Z0-9_-]{1,64}$/;

      function isElementProtected(el) {
        if (!el || typeof el.closest !== 'function') return false;
        return Boolean(el.closest(PROTECTED_SELECTORS));
      }

      function ensureGuidesLayer() {
        var existing = document.querySelector('[data-od-edit-guides-layer="true"]');
        if (existing) return existing;

        var layer = document.createElement('div');
        layer.setAttribute('data-od-edit-guides-layer', 'true');
        layer.setAttribute('aria-hidden', 'true');
        layer.style.position = 'fixed';
        layer.style.top = '0';
        layer.style.left = '0';
        layer.style.width = '100%';
        layer.style.height = '100%';
        layer.style.pointerEvents = 'none';
        layer.style.zIndex = '999999';
        layer.style.overflow = 'visible';

        if (document.body) {
          document.body.appendChild(layer);
        }
        return layer;
      }

      var TEXT_LEAF_TAGS = {
        'h1': 1, 'h2': 1, 'h3': 1, 'h4': 1, 'h5': 1, 'h6': 1, 'p': 1, 'span': 1,
        'a': 1, 'li': 1, 'blockquote': 1, 'strong': 1, 'em': 1, 'b': 1, 'i': 1,
        'td': 1, 'th': 1, 'caption': 1, 'figcaption': 1, 'code': 1, 'pre': 1
      };
      var CONTAINER_TAGS = {
        'div': 1, 'section': 1, 'article': 1, 'aside': 1, 'header': 1, 'footer': 1,
        'table': 1, 'thead': 1, 'tbody': 1, 'tr': 1, 'ul': 1, 'ol': 1, 'figure': 1
      };

      function isTextLeafElement(el) {
        if (!el || !el.tagName) return false;
        var tag = el.tagName.toLowerCase();
        if (!TEXT_LEAF_TAGS[tag]) return false;
        if (el.children) {
          for (var i = 0; i < el.children.length; i++) {
            var cTag = el.children[i].tagName.toLowerCase();
            if (CONTAINER_TAGS[cTag] || (cTag.charAt(0) === 'h' && cTag.length === 2) || cTag === 'p') return false;
          }
        }
        return true;
      }

      function buildRelativeDomPath(el, container) {
        var segments = [];
        var curr = el;
        while (curr && curr !== container && curr.parentElement) {
          var odId = curr.getAttribute('data-od-id');
          if (odId && /^[a-zA-Z0-9_\\-]+$/.test(odId)) {
            segments.unshift('[data-od-id="' + odId + '"]');
            break;
          }
          var elId = curr.getAttribute('id');
          if (elId && /^[a-zA-Z0-9_\\-]+$/.test(elId)) {
            segments.unshift('#' + elId);
            break;
          }
          var tag = curr.tagName.toLowerCase();
          var parent = curr.parentElement;
          var siblings = parent.children;
          var sameTagSiblings = [];
          for (var i = 0; i < siblings.length; i++) {
            if (siblings[i].tagName.toLowerCase() === tag) {
              sameTagSiblings.push(siblings[i]);
            }
          }
          if (sameTagSiblings.length > 1) {
            var idx = sameTagSiblings.indexOf(curr) + 1;
            segments.unshift(tag + ':nth-of-type(' + idx + ')');
          } else {
            segments.unshift(tag);
          }
          if (parent === container) break;
          curr = parent;
        }
        return segments.join(' > ');
      }

      function extractMetadata(el) {
        if (!el || typeof el.getAttribute !== 'function') return null;

        var isProtected = isElementProtected(el);
        var targetBlock = el.hasAttribute('data-od-block-id') ? el : el.closest('[data-od-block-id]');
        if (!targetBlock) return null;

        var rawBlockId = targetBlock.getAttribute('data-od-block-id');
        if (!rawBlockId || !BLOCK_ID_REGEX.test(rawBlockId)) return null;

        var blockType = (targetBlock.getAttribute('data-od-block-type') || '').trim().toLowerCase();
        if (!blockType) return null;

        var field = el.getAttribute('data-od-field') || null;
        var rawIndex = el.getAttribute('data-od-item-index');
        var itemIndex = null;
        if (rawIndex !== null && /^[0-9]+$/.test(rawIndex.trim())) {
          itemIndex = parseInt(rawIndex.trim(), 10);
        }

        var isEditable = !isProtected && el.getAttribute('data-od-editable') === 'true';
        if (!isProtected && blockType === 'open_design_section' && isTextLeafElement(el)) {
          isEditable = true;
          field = 'html_content';
        }

        return {
          blockId: rawBlockId,
          blockType: blockType,
          field: field,
          itemIndex: itemIndex,
          isEditable: isEditable,
          isProtected: isProtected
        };
      }

      function findSemanticTarget(el) {
        if (!el || typeof el.closest !== 'function') return null;

        // Overlay self-exclusion
        if (el.hasAttribute('data-od-edit-guides-layer') || el.closest('[data-od-edit-guides-layer="true"]')) {
          return null;
        }

        // Platform notices & honeypot exclusion
        if (el.closest('.preview-banner, #rivinity-consent-banner, [name="hp_comment"], [data-od-edit-bridge]')) {
          return null;
        }

        // Ad placements: return outer block container as protected discovery target
        if (el.closest('.block-ad-placement, .rivinity-ad-placeholder, .rivinity-live-ad-container, .rivinity-ad-slot')) {
          var adBlock = el.closest('[data-od-block-id]');
          if (adBlock && extractMetadata(adBlock)) return adBlock;
          return null;
        }

        // Candidate discovery
        var candidate = null;
        if (el.hasAttribute('data-od-field') && el.hasAttribute('data-od-block-id')) {
          candidate = el;
        } else {
          var nearestField = el.closest('[data-od-field]');
          if (nearestField && nearestField.hasAttribute('data-od-block-id')) {
            candidate = nearestField;
          } else {
            candidate = el.closest('[data-od-block-id]');
          }
        }

        if (!candidate) return null;
        if (!extractMetadata(candidate)) return null;
        return candidate;
      }

      function formatBadgeText(meta) {
        if (meta.isProtected) return '🔒 Protected';
        var typeStr = meta.blockType.split('_').map(function(w){ return w.charAt(0).toUpperCase() + w.slice(1); }).join(' ');
        if (!meta.field) return typeStr;
        var fieldStr = meta.field.replace(/_/g, ' ');
        if (meta.itemIndex !== null) return typeStr + ' • ' + fieldStr + '[' + meta.itemIndex + ']';
        return typeStr + ' • ' + fieldStr;
      }

      var activeHoverEl = null;
      var activeSelectedEl = null;
      var hoverBox = null;
      var selectedBox = null;

      function renderHoverBox(targetEl) {
        var layer = ensureGuidesLayer();
        if (!layer) return;

        if (!targetEl || !targetEl.isConnected) {
          clearHover();
          return;
        }

        var meta = extractMetadata(targetEl);
        if (!meta) {
          clearHover();
          return;
        }

        var rect = targetEl.getBoundingClientRect();
        if (rect.width <= 0 && rect.height <= 0) {
          clearHover();
          return;
        }

        activeHoverEl = targetEl;

        if (!hoverBox) {
          hoverBox = document.createElement('div');
          hoverBox.className = 'od-guide-box od-guide-hover';
          hoverBox.setAttribute('aria-hidden', 'true');
          hoverBox.style.position = 'absolute';
          hoverBox.style.boxSizing = 'border-box';
          hoverBox.style.pointerEvents = 'none';
          hoverBox.style.borderRadius = '4px';
          hoverBox.style.zIndex = '1000';
          layer.appendChild(hoverBox);
        }

        hoverBox.style.display = 'block';
        hoverBox.style.top = rect.top + 'px';
        hoverBox.style.left = rect.left + 'px';
        hoverBox.style.width = rect.width + 'px';
        hoverBox.style.height = rect.height + 'px';
        hoverBox.style.border = meta.isProtected ? '1.5px dashed #d97706' : '1.5px dashed #3b82f6';
        hoverBox.style.backgroundColor = meta.isProtected ? 'rgba(245, 158, 11, 0.06)' : 'rgba(59, 130, 246, 0.06)';

        var badge = hoverBox.querySelector('.od-guide-badge');
        if (!badge) {
          badge = document.createElement('div');
          badge.className = 'od-guide-badge';
          badge.style.position = 'absolute';
          badge.style.fontSize = '10px';
          badge.style.fontFamily = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
          badge.style.fontWeight = '600';
          badge.style.padding = '2px 6px';
          badge.style.borderRadius = '3px';
          badge.style.whiteSpace = 'nowrap';
          badge.style.pointerEvents = 'none';
          badge.style.lineHeight = '1.2';
          badge.style.color = '#ffffff';
          hoverBox.appendChild(badge);
        }

        badge.textContent = formatBadgeText(meta);
        badge.style.backgroundColor = meta.isProtected ? '#d97706' : '#2563eb';

        if (rect.top < 24) {
          badge.style.top = '2px';
          badge.style.left = '2px';
        } else {
          badge.style.top = '-20px';
          badge.style.left = '0px';
        }
      }

      function renderSelectedBox(targetEl) {
        var layer = ensureGuidesLayer();
        if (!layer) return;

        if (!targetEl || !targetEl.isConnected) {
          clearSelected();
          return;
        }

        var meta = extractMetadata(targetEl);
        if (!meta) {
          clearSelected();
          return;
        }

        var rect = targetEl.getBoundingClientRect();
        if (rect.width <= 0 && rect.height <= 0) {
          clearSelected();
          return;
        }

        activeSelectedEl = targetEl;

        if (!selectedBox) {
          selectedBox = document.createElement('div');
          selectedBox.className = 'od-guide-box od-guide-selected';
          selectedBox.setAttribute('aria-hidden', 'true');
          selectedBox.style.position = 'absolute';
          selectedBox.style.boxSizing = 'border-box';
          selectedBox.style.pointerEvents = 'none';
          selectedBox.style.borderRadius = '4px';
          selectedBox.style.zIndex = '1001';
          selectedBox.style.boxShadow = '0 0 0 1px rgba(255, 255, 255, 0.8), 0 4px 12px rgba(37, 99, 235, 0.25)';

          ['top-left', 'top-right', 'bottom-left', 'bottom-right'].forEach(function(pos) {
            var handle = document.createElement('div');
            handle.className = 'od-guide-handle ' + pos;
            handle.style.position = 'absolute';
            handle.style.width = '6px';
            handle.style.height = '6px';
            handle.style.backgroundColor = '#ffffff';
            handle.style.border = '1.5px solid #2563eb';
            handle.style.borderRadius = '1px';
            if (pos.indexOf('top') >= 0) handle.style.top = '-3px';
            if (pos.indexOf('bottom') >= 0) handle.style.bottom = '-3px';
            if (pos.indexOf('left') >= 0) handle.style.left = '-3px';
            if (pos.indexOf('right') >= 0) handle.style.right = '-3px';
            selectedBox.appendChild(handle);
          });

          layer.appendChild(selectedBox);
        }

        selectedBox.style.display = 'block';
        selectedBox.style.top = rect.top + 'px';
        selectedBox.style.left = rect.left + 'px';
        selectedBox.style.width = rect.width + 'px';
        selectedBox.style.height = rect.height + 'px';
        selectedBox.style.border = meta.isProtected ? '2px solid #d97706' : '2px solid #2563eb';
        selectedBox.style.backgroundColor = meta.isProtected ? 'rgba(245, 158, 11, 0.08)' : 'rgba(37, 99, 235, 0.08)';

        var badge = selectedBox.querySelector('.od-guide-badge');
        if (!badge) {
          badge = document.createElement('div');
          badge.className = 'od-guide-badge';
          badge.style.position = 'absolute';
          badge.style.fontSize = '10px';
          badge.style.fontFamily = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
          badge.style.fontWeight = '700';
          badge.style.padding = '2px 6px';
          badge.style.borderRadius = '3px';
          badge.style.whiteSpace = 'nowrap';
          badge.style.pointerEvents = 'none';
          badge.style.lineHeight = '1.2';
          badge.style.color = '#ffffff';
          selectedBox.appendChild(badge);
        }

        badge.textContent = formatBadgeText(meta);
        badge.style.backgroundColor = meta.isProtected ? '#d97706' : '#2563eb';

        if (rect.top < 24) {
          badge.style.top = '2px';
          badge.style.left = '2px';
        } else {
          badge.style.top = '-22px';
          badge.style.left = '-1px';
        }
      }

      function clearHover() {
        activeHoverEl = null;
        if (hoverBox) hoverBox.style.display = 'none';
      }

      function clearSelected() {
        activeSelectedEl = null;
        if (selectedBox) selectedBox.style.display = 'none';
      }

      // ── Stage 3.4A: Native Text Inline Editing Engine ────────────────────────
      var SCHEMA_FIELD_CONSTRAINTS = {
        'hero': {
          'headline': { maxLength: 160, multiline: false, required: true },
          'subheadline': { maxLength: 300, multiline: true, required: false },
          'cta_button.label': { maxLength: 60, multiline: false, required: true }
        },
        'text_section': {
          'heading': { maxLength: 160, multiline: false, required: false },
          'body': { maxLength: 10000, multiline: true, required: true }
        },
        'features_grid': {
          'title': { maxLength: 160, multiline: false, required: false },
          'items.title': { maxLength: 100, multiline: false, required: true },
          'items.description': { maxLength: 500, multiline: true, required: true }
        },
        'image_gallery': {
          'title': { maxLength: 160, multiline: false, required: false },
          'images.caption': { maxLength: 200, multiline: false, required: false }
        },
        'contact_form': {
          'title': { maxLength: 120, multiline: false, required: false },
          'submit_label': { maxLength: 60, multiline: false, required: true }
        },
        'cta_banner': {
          'title': { maxLength: 160, multiline: false, required: true },
          'description': { maxLength: 300, multiline: true, required: false },
          'button.label': { maxLength: 60, multiline: false, required: true }
        },
        'footer': {
          'copyright_text': { maxLength: 160, multiline: false, required: false },
          'links.label': { maxLength: 50, multiline: false, required: true }
        }
      };

      function normalizeFieldIdentifier(field) {
        if (!field) return null;
        return field.replace(/\\[\\d*\\]/g, '');
      }

      function getFieldConstraints(blockType, field) {
        if (!blockType || !field) return null;
        var blockConstraints = SCHEMA_FIELD_CONSTRAINTS[blockType];
        if (!blockConstraints) return null;

        var normalized = normalizeFieldIdentifier(field);
        if (normalized && blockConstraints[normalized]) {
          return blockConstraints[normalized];
        }

        var prefix = blockType + '.';
        if (normalized && normalized.indexOf(prefix) === 0) {
          var stripped = normalized.slice(prefix.length);
          if (blockConstraints[stripped]) {
            return blockConstraints[stripped];
          }
        }
        return null;
      }

      function isFieldInlineEditable(blockType, field) {
        return getFieldConstraints(blockType, field) !== null;
      }

      function placeCaretFromClick(clickEvent, el) {
        if (!el || typeof window.getSelection !== 'function') return;
        var range = null;
        if (document.caretRangeFromPoint && clickEvent && typeof clickEvent.clientX === 'number') {
          try {
            range = document.caretRangeFromPoint(clickEvent.clientX, clickEvent.clientY);
          } catch (_) {}
        } else if (document.caretPositionFromPoint && clickEvent && typeof clickEvent.clientX === 'number') {
          try {
            var pos = document.caretPositionFromPoint(clickEvent.clientX, clickEvent.clientY);
            if (pos && pos.offsetNode) {
              range = document.createRange();
              range.setStart(pos.offsetNode, pos.offset);
              range.collapse(true);
            }
          } catch (_) {}
        }
        if (!range) {
          try {
            range = document.createRange();
            range.selectNodeContents(el);
            range.collapse(false);
          } catch (_) {}
        }
        if (range) {
          try {
            var sel = window.getSelection();
            if (sel) {
              sel.removeAllRanges();
              sel.addRange(range);
            }
          } catch (_) {}
        }
      }

      var inlineToolbar = null;
      var toolbarCounter = null;
      var toolbarBtnSave = null;
      var toolbarBtnCancel = null;
      var activeTextEdit = null;

      function ensureInlineToolbar() {
        if (inlineToolbar && inlineToolbar.isConnected) return inlineToolbar;
        var layer = ensureGuidesLayer();
        if (!layer) return null;

        var existing = layer.querySelector('[data-od-inline-toolbar="true"]');
        if (existing) {
          inlineToolbar = existing;
          toolbarCounter = existing.querySelector('.od-toolbar-counter');
          toolbarBtnSave = existing.querySelector('.od-toolbar-save');
          toolbarBtnCancel = existing.querySelector('.od-toolbar-cancel');
          return inlineToolbar;
        }

        inlineToolbar = document.createElement('div');
        inlineToolbar.className = 'od-inline-toolbar';
        inlineToolbar.setAttribute('data-od-inline-toolbar', 'true');
        inlineToolbar.setAttribute('aria-hidden', 'true');
        inlineToolbar.style.position = 'absolute';
        inlineToolbar.style.boxSizing = 'border-box';
        inlineToolbar.style.zIndex = '1000000';
        inlineToolbar.style.display = 'none';
        inlineToolbar.style.alignItems = 'center';
        inlineToolbar.style.gap = '6px';
        inlineToolbar.style.backgroundColor = '#0f172a';
        inlineToolbar.style.border = '1px solid #334155';
        inlineToolbar.style.borderRadius = '4px';
        inlineToolbar.style.padding = '3px 6px';
        inlineToolbar.style.boxShadow = '0 4px 14px rgba(0, 0, 0, 0.35)';
        inlineToolbar.style.fontFamily = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
        inlineToolbar.style.fontSize = '11px';
        inlineToolbar.style.lineHeight = '1';
        inlineToolbar.style.pointerEvents = 'auto';
        inlineToolbar.style.userSelect = 'none';

        toolbarCounter = document.createElement('span');
        toolbarCounter.className = 'od-toolbar-counter';
        toolbarCounter.style.color = '#94a3b8';
        toolbarCounter.style.fontSize = '10px';
        toolbarCounter.style.fontFamily = 'monospace';
        toolbarCounter.style.padding = '0 4px';
        inlineToolbar.appendChild(toolbarCounter);

        toolbarBtnSave = document.createElement('button');
        toolbarBtnSave.type = 'button';
        toolbarBtnSave.className = 'od-toolbar-btn od-toolbar-save';
        toolbarBtnSave.setAttribute('title', 'Apply edit (Enter)');
        toolbarBtnSave.textContent = '✓';
        toolbarBtnSave.style.background = 'rgba(56, 189, 248, 0.15)';
        toolbarBtnSave.style.border = '1px solid rgba(56, 189, 248, 0.4)';
        toolbarBtnSave.style.borderRadius = '3px';
        toolbarBtnSave.style.color = '#38bdf8';
        toolbarBtnSave.style.cursor = 'pointer';
        toolbarBtnSave.style.padding = '2px 6px';
        toolbarBtnSave.style.fontSize = '11px';
        toolbarBtnSave.style.fontWeight = '700';
        toolbarBtnSave.style.lineHeight = '1';

        toolbarBtnCancel = document.createElement('button');
        toolbarBtnCancel.type = 'button';
        toolbarBtnCancel.className = 'od-toolbar-btn od-toolbar-cancel';
        toolbarBtnCancel.setAttribute('title', 'Cancel edit (Esc)');
        toolbarBtnCancel.textContent = '✕';
        toolbarBtnCancel.style.background = 'transparent';
        toolbarBtnCancel.style.border = '1px solid rgba(148, 163, 184, 0.3)';
        toolbarBtnCancel.style.borderRadius = '3px';
        toolbarBtnCancel.style.color = '#cbd5e1';
        toolbarBtnCancel.style.cursor = 'pointer';
        toolbarBtnCancel.style.padding = '2px 6px';
        toolbarBtnCancel.style.fontSize = '11px';
        toolbarBtnCancel.style.fontWeight = '700';
        toolbarBtnCancel.style.lineHeight = '1';

        toolbarBtnSave.addEventListener('pointerdown', function(e) {
          e.preventDefault();
          e.stopPropagation();
          finishActiveTextEdit(true);
        });
        toolbarBtnCancel.addEventListener('pointerdown', function(e) {
          e.preventDefault();
          e.stopPropagation();
          finishActiveTextEdit(false);
        });

        inlineToolbar.appendChild(toolbarBtnSave);
        inlineToolbar.appendChild(toolbarBtnCancel);
        layer.appendChild(inlineToolbar);
        return inlineToolbar;
      }

      function updateCounterAndToolbar() {
        if (!activeTextEdit || !toolbarCounter) return;
        var currentText = activeTextEdit.el.textContent || '';
        var len = currentText.length;
        if (activeTextEdit.constraint && activeTextEdit.constraint.maxLength) {
          var max = activeTextEdit.constraint.maxLength;
          toolbarCounter.textContent = len + ' / ' + max;
          toolbarCounter.style.display = 'inline';
          if (len > max) {
            toolbarCounter.style.color = '#ef4444';
            toolbarCounter.style.fontWeight = '700';
          } else {
            toolbarCounter.style.color = '#94a3b8';
            toolbarCounter.style.fontWeight = '500';
          }
        } else {
          toolbarCounter.style.display = 'none';
        }
      }

      function renderInlineToolbar(el) {
        var toolbar = ensureInlineToolbar();
        if (!toolbar || !el || !el.isConnected) {
          hideInlineToolbar();
          return;
        }
        var rect = el.getBoundingClientRect();
        toolbar.style.display = 'flex';

        var winHeight = (window.innerHeight || (document.documentElement && document.documentElement.clientHeight) || 600);
        var winWidth = (window.innerWidth || (document.documentElement && document.documentElement.clientWidth) || 800);
        var tbHeight = 28;
        var tbWidth = 140;

        var topPos = rect.top >= (tbHeight + 6) ? (rect.top - tbHeight - 4) : (rect.bottom + 4);
        var clampedTop = Math.max(4, Math.min(topPos, winHeight - tbHeight - 4));
        toolbar.style.top = clampedTop + 'px';

        var leftPos = Math.max(8, Math.min(rect.left, winWidth - tbWidth - 8));
        toolbar.style.left = leftPos + 'px';

        updateCounterAndToolbar();
      }

      function hideInlineToolbar() {
        if (inlineToolbar) {
          inlineToolbar.style.display = 'none';
        }
      }

      function startTextEdit(el, clickEvent, options) {
        if (!el || !el.isConnected) return false;
        if (isElementProtected(el)) return false;

        var meta = extractMetadata(el);
        if (!meta || meta.isProtected) return false;

        var isCustomSection = meta.blockType === 'open_design_section';
        if (isCustomSection) {
          if (!options || !options.allowCustomSection || !isTextLeafElement(el)) {
            return false;
          }
        } else {
          if (!meta.isEditable) return false;
        }

        if (meta.blockType === 'ad_placement' || (typeof el.closest === 'function' && el.closest('.block-ad-placement, .rivinity-ad-placeholder, .rivinity-live-ad-container, .rivinity-ad-slot, [data-od-block-type="ad_placement"]'))) {
          return false;
        }

        var disallowed = [
          'cta_button.target_url',
          'background_image_url',
          'alignment',
          'items.icon',
          'images.image_url',
          'images.alt_text',
          'fields',
          'button.target_url',
          'links.target_url'
        ];
        if (meta.field && disallowed.indexOf(meta.field) !== -1) {
          return false;
        }

        var constraint = null;
        if (isCustomSection) {
          var tag = el.tagName.toLowerCase();
          var isHeading = tag.charAt(0) === 'h' && tag.length === 2;
          constraint = { maxLength: 5000, multiline: !isHeading, required: false };
        } else {
          constraint = getFieldConstraints(meta.blockType, meta.field);
        }
        if (!constraint) return false;
        if (isCustomSection) {
          var tag = el.tagName.toLowerCase();
          var isHeading = tag.charAt(0) === 'h' && tag.length === 2;
          constraint = { maxLength: constraint.maxLength || 5000, multiline: !isHeading, required: false };
        }

        if (activeTextEdit && activeTextEdit.el === el) {
          placeCaretFromClick(clickEvent, el);
          return true;
        }

        if (activeTextEdit) {
          finishActiveTextEdit(true);
        }

        var originalText = el.textContent || '';
        var editSessionId = 'edit_' + Date.now() + '_' + Math.random().toString(36).substring(2, 9);

        var targetSelector = null;
        var targetOdId = null;
        if (isCustomSection) {
          var secContainer = el.closest('[data-od-block-id][data-od-block-type="open_design_section"]') || el.closest('[data-od-block-id]');
          if (secContainer) {
            targetSelector = buildRelativeDomPath(el, secContainer);
          }
          targetOdId = el.getAttribute('data-od-id') || null;
        }

        try {
          el.setAttribute('contenteditable', 'plaintext-only');
          if (el.contentEditable !== 'plaintext-only') {
            el.setAttribute('contenteditable', 'true');
          }
        } catch (_) {
          el.setAttribute('contenteditable', 'true');
        }
        el.setAttribute('data-od-editing', 'true');
        el.style.outline = '2px solid #2563eb';
        el.style.outlineOffset = '2px';
        el.style.cursor = 'text';

        try { el.focus(); } catch (_) {}
        placeCaretFromClick(clickEvent, el);

        function onKey(ev) {
          if (ev.key === 'Enter') {
            if (!constraint.multiline) {
              ev.preventDefault();
              if (ev.shiftKey) {
                return;
              }
              finishActiveTextEdit(true);
            } else if (ev.ctrlKey || ev.metaKey) {
              ev.preventDefault();
              finishActiveTextEdit(true);
            }
          } else if (ev.key === 'Escape') {
            ev.preventDefault();
            finishActiveTextEdit(false);
          } else if ((ev.ctrlKey || ev.metaKey) && (ev.key === 'b' || ev.key === 'i' || ev.key === 'u' || ev.key === 'B' || ev.key === 'I' || ev.key === 'U')) {
            ev.preventDefault();
          } else if (ev.key && ev.key.length === 1 && !ev.ctrlKey && !ev.metaKey && !ev.altKey) {
            if (constraint.maxLength) {
              var sel = window.getSelection ? window.getSelection() : null;
              var hasSelection = sel && sel.toString().length > 0;
              if (!hasSelection && (el.textContent || '').length >= constraint.maxLength) {
                ev.preventDefault();
              }
            }
          }
        }

        function onBeforeInput(ev) {
          if (!ev) return;
          if (ev.inputType && (
            ev.inputType.indexOf('format') === 0 ||
            ev.inputType === 'insertLink' ||
            ev.inputType === 'insertOrderedList' ||
            ev.inputType === 'insertUnorderedList' ||
            ev.inputType === 'insertHorizontalRule'
          )) {
            ev.preventDefault();
            return;
          }
          if (!constraint.multiline && (ev.inputType === 'insertParagraph' || ev.inputType === 'insertLineBreak')) {
            ev.preventDefault();
            return;
          }
        }

        function onInput() {
          if (!activeTextEdit || activeTextEdit.el !== el) return;
          if (el.children && el.children.length > 0 && !constraint.multiline) {
            el.textContent = el.textContent || '';
          }
          updateCounterAndToolbar();
          if (activeSelectedEl === el) {
            renderSelectedBox(el);
          }
          renderInlineToolbar(el);
        }

        function onPaste(ev) {
          ev.preventDefault();
          var text = '';
          if (ev.clipboardData && ev.clipboardData.getData) {
            text = ev.clipboardData.getData('text/plain');
          } else if (window.clipboardData && window.clipboardData.getData) {
            text = window.clipboardData.getData('Text');
          }
          if (!text) return;
          if (!constraint.multiline) {
            text = text.replace(/[\\r\\n]+/g, ' ');
          }
          if (constraint.maxLength) {
            var currentLength = (el.textContent || '').length;
            var sel = window.getSelection ? window.getSelection() : null;
            var selectedLength = (sel && sel.toString()) ? sel.toString().length : 0;
            var remaining = Math.max(0, constraint.maxLength - (currentLength - selectedLength));
            if (text.length > remaining) {
              text = text.slice(0, remaining);
            }
          }
          if (!text) return;
          if (document.queryCommandSupported && document.queryCommandSupported('insertText')) {
            document.execCommand('insertText', false, text);
          } else {
            var sel = window.getSelection ? window.getSelection() : null;
            var range = sel && sel.rangeCount > 0 ? sel.getRangeAt(0) : null;
            if (range) {
              range.deleteContents();
              var node = document.createTextNode(text);
              range.insertNode(node);
              range.setStartAfter(node);
              range.collapse(true);
              sel.removeAllRanges();
              sel.addRange(range);
            } else {
              el.textContent = (el.textContent || '') + text;
            }
          }
          updateCounterAndToolbar();
        }

        function onDrop(ev) {
          ev.preventDefault();
          var text = ev.dataTransfer ? ev.dataTransfer.getData('text/plain') : '';
          if (!text) return;
          if (!constraint.multiline) {
            text = text.replace(/[\\r\\n]+/g, ' ');
          }
          if (constraint.maxLength) {
            var currentLen = (el.textContent || '').length;
            var remaining = Math.max(0, constraint.maxLength - currentLen);
            if (text.length > remaining) {
              text = text.slice(0, remaining);
            }
          }
          if (!text) return;
          if (document.queryCommandSupported && document.queryCommandSupported('insertText')) {
            document.execCommand('insertText', false, text);
          } else {
            el.textContent = (el.textContent || '') + text;
          }
          updateCounterAndToolbar();
        }

        activeTextEdit = {
          sessionId: editSessionId,
          el: el,
          originalText: originalText,
          meta: meta,
          constraint: constraint,
          patchKind: isCustomSection ? 'html_fragment' : 'native_field',
          targetSelector: targetSelector,
          targetOdId: targetOdId,
          onKey: onKey,
          onBeforeInput: onBeforeInput,
          onInput: onInput,
          onPaste: onPaste,
          onDrop: onDrop
        };

        el.addEventListener('keydown', onKey);
        el.addEventListener('beforeinput', onBeforeInput);
        el.addEventListener('input', onInput);
        el.addEventListener('paste', onPaste);
        el.addEventListener('drop', onDrop);

        renderInlineToolbar(el);

        if (bridgePort && activeSessionId) {
          bridgePort.postMessage({
            type: 'OD_EDIT_SESSION_START',
            session_id: activeSessionId,
            timestamp: Date.now(),
            payload: {
              sessionId: editSessionId,
              blockId: meta.blockId,
              blockType: meta.blockType,
              field: meta.field,
              itemIndex: meta.itemIndex,
              initialValue: originalText,
              targetSelector: targetSelector,
              targetOdId: targetOdId
            }
          });
        }

        return true;
      }

      function finishActiveTextEdit(commit) {
        if (!activeTextEdit) return false;
        var session = activeTextEdit;
        activeTextEdit = null;

        var el = session.el;
        el.removeAttribute('contenteditable');
        el.removeAttribute('data-od-editing');
        el.style.outline = '';
        el.style.outlineOffset = '';
        el.style.cursor = '';

        el.removeEventListener('keydown', session.onKey);
        el.removeEventListener('beforeinput', session.onBeforeInput);
        el.removeEventListener('input', session.onInput);
        el.removeEventListener('paste', session.onPaste);
        el.removeEventListener('drop', session.onDrop);

        hideInlineToolbar();

        if (!commit) {
          el.textContent = session.originalText;
          if (bridgePort && activeSessionId) {
            bridgePort.postMessage({
              type: 'OD_EDIT_CANCEL',
              session_id: activeSessionId,
              timestamp: Date.now(),
              payload: {
                sessionId: session.sessionId,
                blockId: session.meta.blockId,
                field: session.meta.field,
                itemIndex: session.meta.itemIndex,
                targetSelector: session.targetSelector,
                targetOdId: session.targetOdId
              }
            });
          }
        } else {
          var rawText = el.textContent || '';
          var finalVal = rawText;
          if (finalVal !== session.originalText) {
            if (bridgePort && activeSessionId) {
              bridgePort.postMessage({
                type: 'OD_EDIT_PROPOSE_PATCH',
                session_id: activeSessionId,
                timestamp: Date.now(),
                payload: {
                  sessionId: session.sessionId,
                  blockId: session.meta.blockId,
                  blockType: session.meta.blockType,
                  field: session.meta.field,
                  itemIndex: session.meta.itemIndex,
                  proposedValue: finalVal,
                  value: finalVal,
                  patchKind: session.patchKind || 'native_field',
                  targetSelector: session.targetSelector,
                  targetOdId: session.targetOdId,
                  originalValue: session.originalText
                }
              });
            }
          }
        }

        if (activeSelectedEl && activeSelectedEl.isConnected) {
          renderSelectedBox(activeSelectedEl);
        }

        return true;
      }

      function startLocalizedSectionEdit(el, clickEvent) {
        return startTextEdit(el, clickEvent, { allowCustomSection: true });
      }

      function updatePositions() {
        if (activeHoverEl) {
          if (activeHoverEl.isConnected) renderHoverBox(activeHoverEl);
          else clearHover();
        }
        if (activeSelectedEl) {
          if (activeSelectedEl.isConnected) renderSelectedBox(activeSelectedEl);
          else clearSelected();
        }
        if (activeTextEdit) {
          if (activeTextEdit.el && activeTextEdit.el.isConnected) {
            renderInlineToolbar(activeTextEdit.el);
          } else {
            finishActiveTextEdit(false);
          }
        }
      }

      // Event Listeners
      document.addEventListener('pointerover', function(e) {
        var semantic = findSemanticTarget(e.target);
        if (semantic) renderHoverBox(semantic);
      }, true);

      document.addEventListener('pointerout', function(e) {
        if (activeHoverEl && (e.target === activeHoverEl || activeHoverEl.contains(e.target))) {
          var rel = e.relatedTarget;
          if (!rel || !activeHoverEl.contains(rel)) {
            clearHover();
          }
        }
      }, true);

      document.addEventListener('dblclick', function(e) {
        if (e.target && typeof e.target.closest === 'function' && e.target.closest('[data-od-inline-toolbar="true"]')) {
          return;
        }
        var semantic = findSemanticTarget(e.target);
        if (semantic) {
          var meta = extractMetadata(semantic);
          if (meta && meta.isEditable && !meta.isProtected && isFieldInlineEditable(meta.blockType, meta.field)) {
            if (!activeTextEdit || activeTextEdit.el !== semantic) {
              startTextEdit(semantic, e);
            }
          } else if (meta && meta.blockType === 'open_design_section' && e.target && isTextLeafElement(e.target) && !isElementProtected(e.target)) {
            if (!activeTextEdit || activeTextEdit.el !== e.target) {
              startLocalizedSectionEdit(e.target, e);
            }
          }
        }
      }, true);

      document.addEventListener('click', function(e) {
        if (e.target && typeof e.target.closest === 'function' && e.target.closest('[data-od-inline-toolbar="true"]')) {
          return;
        }

        var semantic = findSemanticTarget(e.target);
        if (semantic) {
          renderSelectedBox(semantic);
          var meta = extractMetadata(semantic);
          if (bridgePort && activeSessionId && meta) {
            var classification = meta.isProtected ? 'protected' : (meta.isEditable ? 'editable' : 'read_only');
            var rect = semantic.getBoundingClientRect();
            bridgePort.postMessage({
              type: 'OD_ELEMENT_SELECT',
              session_id: activeSessionId,
              timestamp: Date.now(),
              payload: {
                blockId: meta.blockId,
                blockType: meta.blockType,
                field: meta.field,
                itemIndex: meta.itemIndex,
                classification: classification,
                rect: {
                  top: rect.top,
                  left: rect.left,
                  width: rect.width,
                  height: rect.height
                }
              }
            });
          }

          if (meta && meta.isEditable && !meta.isProtected && isFieldInlineEditable(meta.blockType, meta.field)) {
            if (activeTextEdit && activeTextEdit.el === semantic) {
              placeCaretFromClick(e, semantic);
            } else {
              startTextEdit(semantic, e);
            }
          } else if (meta && meta.blockType === 'open_design_section' && e.target && isTextLeafElement(e.target) && !isElementProtected(e.target)) {
            if (activeTextEdit && activeTextEdit.el === e.target) {
              placeCaretFromClick(e, e.target);
            } else {
              startLocalizedSectionEdit(e.target, e);
            }
          } else {
            if (activeTextEdit) finishActiveTextEdit(true);
          }
        } else {
          if (e.target && !e.target.closest('[data-od-edit-guides-layer="true"]')) {
            if (activeTextEdit) finishActiveTextEdit(true);
            clearSelected();
            if (bridgePort && activeSessionId) {
              bridgePort.postMessage({
                type: 'OD_BACKGROUND_CLICK',
                session_id: activeSessionId,
                timestamp: Date.now(),
                payload: {}
              });
            }
          }
        }
      }, true);

      window.addEventListener('scroll', updatePositions, { passive: true });
      window.addEventListener('resize', updatePositions, { passive: true });

      // Expose engine instance for inspection, tests, and future stages
      window.__odOverlayEngine = {
        ensureGuidesLayer: ensureGuidesLayer,
        ensureInlineToolbar: ensureInlineToolbar,
        renderInlineToolbar: renderInlineToolbar,
        hideInlineToolbar: hideInlineToolbar,
        placeCaretFromClick: placeCaretFromClick,
        findSemanticTarget: findSemanticTarget,
        extractMetadata: extractMetadata,
        setHoverTarget: renderHoverBox,
        setSelectedTarget: renderSelectedBox,
        clearHover: clearHover,
        clearSelected: clearSelected,
        clearAll: function() { clearHover(); clearSelected(); },
        updatePositions: updatePositions,
        getActiveHoverElement: function() { return activeHoverEl; },
        getActiveSelectedElement: function() { return activeSelectedEl; },
        startTextEdit: startTextEdit,
        startLocalizedSectionEdit: startLocalizedSectionEdit,
        buildRelativeDomPath: buildRelativeDomPath,
        isTextLeafElement: isTextLeafElement,
        finishActiveTextEdit: finishActiveTextEdit,
        getActiveTextEdit: function() { return activeTextEdit; },
        isFieldInlineEditable: isFieldInlineEditable,
        getFieldConstraints: getFieldConstraints,
        SCHEMA_FIELD_CONSTRAINTS: SCHEMA_FIELD_CONSTRAINTS,
        getToolbarElement: function() { return inlineToolbar; },
        applyConfirmedValue: function(blockId, field, itemIndex, value, targetSelector, targetOdId) {
          if (!blockId) return false;
          var sel = '[data-od-block-id="' + blockId + '"]';
          var blockEl = document.querySelector(sel);
          if (!blockEl) return false;
          var targetEl = blockEl;
          if (field === 'html_content' || blockEl.getAttribute('data-od-block-type') === 'open_design_section') {
            var found = null;
            if (targetOdId) {
              found = blockEl.querySelector('[data-od-id="' + targetOdId + '"]');
            }
            if (!found && targetSelector) {
              try {
                found = blockEl.querySelector(targetSelector);
              } catch (_) {}
            }
            if (found) targetEl = found;
          } else if (field) {
            var fieldSel = '[data-od-field="' + field + '"]';
            if (itemIndex !== undefined && itemIndex !== null) {
              fieldSel += '[data-od-item-index="' + itemIndex + '"]';
            }
            var fieldEl = blockEl.querySelector(fieldSel);
            if (fieldEl) targetEl = fieldEl;
          }
          if (typeof value === 'string') {
            targetEl.textContent = value;
            if (activeTextEdit && activeTextEdit.el === targetEl) {
              activeTextEdit.originalText = value;
              updateCounterAndToolbar();
            }
          }
          updatePositions();
          return true;
        },
        rollbackValue: function(blockId, field, itemIndex, value, targetSelector, targetOdId) {
          if (!blockId) return false;
          var sel = '[data-od-block-id="' + blockId + '"]';
          var blockEl = document.querySelector(sel);
          if (!blockEl) return false;
          var targetEl = blockEl;
          if (field === 'html_content' || blockEl.getAttribute('data-od-block-type') === 'open_design_section') {
            var found = null;
            if (targetOdId) {
              found = blockEl.querySelector('[data-od-id="' + targetOdId + '"]');
            }
            if (!found && targetSelector) {
              try {
                found = blockEl.querySelector(targetSelector);
              } catch (_) {}
            }
            if (found) targetEl = found;
          } else if (field) {
            var fieldSel = '[data-od-field="' + field + '"]';
            if (itemIndex !== undefined && itemIndex !== null) {
              fieldSel += '[data-od-item-index="' + itemIndex + '"]';
            }
            var fieldEl = blockEl.querySelector(fieldSel);
            if (fieldEl) targetEl = fieldEl;
          }
          if (typeof value === 'string') {
            targetEl.textContent = value;
            if (activeTextEdit && activeTextEdit.el === targetEl) {
              activeTextEdit.originalText = value;
              updateCounterAndToolbar();
            }
          }
          updatePositions();
          return true;
        }
      };

      // ── Handshake Initialization (Phase 3.1 & 3.3B) ──────────────────────────
      window.addEventListener("message", function(event) {
        if (!event.data || typeof event.data !== "object") return;
        if (event.data.type === "OD_BRIDGE_INIT") {
          if (!event.ports || !event.ports[0]) return;
          var challenge = event.data.challenge;
          activeSessionId = event.data.session_id;
          bridgePort = event.ports[0];

          bridgePort.onmessage = function(portEvent) {
            if (!portEvent.data || typeof portEvent.data !== "object") return;
            var msgType = portEvent.data.type;
            if (msgType === "OD_PING") {
              bridgePort.postMessage({
                type: "OD_PONG",
                session_id: activeSessionId,
                timestamp: Date.now()
              });
            } else if (msgType === "OD_PARENT_SELECT_BLOCK") {
              var payload = portEvent.data.payload;
              if (activeTextEdit) {
                var isSame = payload &&
                  payload.blockId === activeTextEdit.meta.blockId &&
                  (payload.field || null) === (activeTextEdit.meta.field || null) &&
                  (payload.itemIndex !== undefined ? payload.itemIndex : null) === (activeTextEdit.meta.itemIndex !== undefined ? activeTextEdit.meta.itemIndex : null);
                if (!isSame) {
                  finishActiveTextEdit(true);
                }
              }
              if (payload && payload.blockId) {
                var targetSelector = '[data-od-block-id="' + payload.blockId + '"]';
                var blockEl = document.querySelector(targetSelector);
                if (blockEl) {
                  var targetEl = blockEl;
                  if (payload.field) {
                    var fieldSelector = '[data-od-field="' + payload.field + '"]';
                    if (payload.itemIndex !== undefined && payload.itemIndex !== null) {
                      fieldSelector += '[data-od-item-index="' + payload.itemIndex + '"]';
                    }
                    var fieldEl = blockEl.querySelector(fieldSelector);
                    if (fieldEl) targetEl = fieldEl;
                  }
                  renderSelectedBox(targetEl);
                  if (payload.scrollIntoView && typeof targetEl.scrollIntoView === 'function') {
                    try {
                      targetEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                    } catch(_) {
                      targetEl.scrollIntoView();
                    }
                  }
                }
              }
            } else if (msgType === "OD_PARENT_CLEAR_SELECTION") {
              if (activeTextEdit) finishActiveTextEdit(true);
              clearSelected();
            } else if (msgType === "OD_PARENT_EDIT_FINISH") {
              if (activeTextEdit) {
                var shouldCommit = !portEvent.data.payload || portEvent.data.payload.commit !== false;
                finishActiveTextEdit(shouldCommit);
              }
            } else if (msgType === "OD_EDIT_APPLY_CONFIRMED") {
              var payload = portEvent.data.payload;
              if (payload && payload.blockId) {
                window.__odOverlayEngine.applyConfirmedValue(
                  payload.blockId,
                  payload.field,
                  payload.itemIndex,
                  payload.appliedValue,
                  payload.targetSelector,
                  payload.targetOdId
                );
              }
            } else if (msgType === "OD_EDIT_REJECTED") {
              var payload = portEvent.data.payload;
              if (payload && payload.blockId) {
                window.__odOverlayEngine.rollbackValue(
                  payload.blockId,
                  payload.field,
                  payload.itemIndex,
                  payload.rollbackValue,
                  payload.targetSelector,
                  payload.targetOdId
                );
              }
            } else if (msgType === "OD_EDIT_SYNC_VALUE") {
              var payload = portEvent.data.payload;
              if (payload && payload.blockId) {
                window.__odOverlayEngine.applyConfirmedValue(
                  payload.blockId,
                  payload.field,
                  payload.itemIndex,
                  payload.value
                );
              }
            }
          };

          bridgePort.postMessage({
            type: "OD_BRIDGE_ACK",
            challenge: challenge,
            session_id: activeSessionId
          });
        }
      });
    })();
  </script>
  {% endif %}
</body>
</html>
"""

_SITEMAP_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{% for item in urls %}
  <url>
    <loc>{{ item.loc }}</loc>
    {% if item.lastmod %}<lastmod>{{ item.lastmod }}</lastmod>{% endif %}
    <changefreq>{{ item.changefreq }}</changefreq>
    <priority>{{ item.priority }}</priority>
  </url>
{% endfor %}
</urlset>
"""

_ROBOTS_TEMPLATE = """User-agent: *
Allow: /
Sitemap: {{ sitemap_url }}
"""

_ERROR_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{ status_code }} - {{ title }}</title>
  <meta name="robots" content="noindex, nofollow">
  <style>
    body { font-family: system-ui, -apple-system, sans-serif; background: #0f172a; color: #f8fafc; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 1.5rem; text-align: center; }
    .card { background: #1e293b; border: 1px solid #334155; border-radius: 0.75rem; padding: 2.5rem; max-width: 480px; width: 100%; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.3); }
    h1 { font-size: 3.5rem; font-weight: 800; color: #60a5fa; margin: 0 0 0.5rem; }
    h2 { font-size: 1.25rem; font-weight: 600; margin: 0 0 1rem; color: #e2e8f0; }
    p { color: #94a3b8; font-size: 0.95rem; line-height: 1.6; margin-bottom: 1.5rem; }
    a { display: inline-block; background: #2563eb; color: #ffffff; padding: 0.625rem 1.25rem; border-radius: 0.5rem; text-decoration: none; font-weight: 500; font-size: 0.875rem; }
    a:hover { background: #1d4ed8; }
  </style>
</head>
<body>
  <div class="card">
    <h1>{{ status_code }}</h1>
    <h2>{{ title }}</h2>
    <p>{{ message }}</p>
    {% if home_url %}<a href="{{ home_url }}">Return Home</a>{% endif %}
  </div>
</body>
</html>
"""

# Jinja2 environment with mandatory HTML auto-escaping
_jinja_env = jinja2.Environment(
    loader=jinja2.DictLoader({
        "page.html": _PAGE_TEMPLATE,
        "sitemap.xml": _SITEMAP_TEMPLATE,
        "robots.txt": _ROBOTS_TEMPLATE,
        "error.html": _ERROR_TEMPLATE,
    }),
    autoescape=jinja2.select_autoescape(["html", "xml"]),
)


def _font_family_to_google_url(font_name: str) -> str | None:
    """Map allowable Google font to a CDN stylesheet URL."""
    if font_name in ("Inter", "Roboto", "Open Sans", "Lato", "Poppins", "Montserrat", "Playfair Display", "Merriweather"):
        encoded = font_name.replace(" ", "+")
        return f"https://fonts.googleapis.com/css2?family={encoded}:wght@400;500;600;700;800&display=swap"
    return None


class SafeWebsiteRenderer:
    """Server-side renderer for published websites and draft previews."""

    @classmethod
    def render_published_page(
        cls,
        project_dict: dict[str, Any],
        page_dict: dict[str, Any],
        all_pages_dict: list[dict[str, Any]],
        base_origin: str,
        is_preview: bool = False,
        is_monetized: bool = False,
        bridge_nonce: str | None = None,
    ) -> str:
        """
        Render a single page from a validated snapshot or preview model.

        Guarantees:
        - Auto-escaped HTML attributes and text.
        - Strict safe URL validation on all links and image URLs.
        - Theme sanitization against CSS injection.
        - Ad placements rendered strictly as non-billable sandbox placeholders.
        """
        project_name = str(project_dict.get("name", "Website"))
        theme = safe_theme_config(project_dict.get("theme_config"))
        font_url = _font_family_to_google_url(theme["font_family"])

        # Page metadata
        page_title = str(page_dict.get("title", "Page"))
        seo_meta = page_dict.get("seo_meta") or {}
        meta_description = seo_meta.get("meta_description")
        if meta_description:
            meta_description = str(meta_description)

        page_slug = page_dict.get("slug", "")
        is_home = page_dict.get("is_home", False) or page_slug == ""

        # Canonical URL
        if is_home:
            canonical_url = f"{base_origin}/"
            home_url = "/"
        else:
            canonical_url = f"{base_origin}/{page_slug}"
            home_url = "/"

        # Robots directive
        if is_preview:
            robots_directive = "noindex, nofollow, noarchive"
        elif seo_meta.get("noindex"):
            robots_directive = "noindex, nofollow"
        else:
            robots_directive = "index, follow"

        # OG Image
        og_image = safe_url(seo_meta.get("og_image_url"))

        # Build clean navigation pages list
        nav_pages = []
        for p in all_pages_dict:
            p_slug = p.get("slug", "")
            p_is_home = p.get("is_home", False) or p_slug == ""
            p_url = "/" if p_is_home else f"/{p_slug}"
            p_active = is_home if p_is_home else (p_slug == page_slug)
            nav_pages.append({
                "title": str(p.get("title", "Page")),
                "url": p_url,
                "is_active": p_active,
            })

        # Sanitize and re-validate structured blocks
        raw_blocks_payload = page_dict.get("blocks") or {}
        sanitized_blocks: list[dict[str, Any]] = []
        footer_links: list[dict[str, str]] = []
        footer_copyright: str | None = None
        footer_block_id: str | None = None

        if isinstance(raw_blocks_payload, dict):
            raw_blocks = raw_blocks_payload.get("blocks", [])
            for raw_b in raw_blocks:
                if not isinstance(raw_b, dict):
                    continue
                if not raw_b.get("is_visible", True):
                    continue

                b_type = raw_b.get("type")
                raw_c = raw_b.get("content") or {}
                if not isinstance(raw_c, dict):
                    continue

                # Validate and preserve actual block ID
                raw_block_id = raw_b.get("id")
                valid_block_id: str | None = None
                if isinstance(raw_block_id, str):
                    clean_id = raw_block_id.strip()
                    if BLOCK_ID_PATTERN.match(clean_id):
                        valid_block_id = clean_id

                # Block sanitization dispatch
                if b_type == BlockType.HERO.value:
                    sanitized_blocks.append({
                        "id": valid_block_id,
                        "type": "hero",
                        "content": {
                            "headline": raw_c.get("headline", ""),
                            "subheadline": raw_c.get("subheadline"),
                            "background_image_url": safe_url(raw_c.get("background_image_url")),
                            "alignment": raw_c.get("alignment", "left"),
                            "cta_button": {
                                "label": raw_c.get("cta_button", {}).get("label", ""),
                                "target_url": safe_url(raw_c.get("cta_button", {}).get("target_url")),
                                "style": raw_c.get("cta_button", {}).get("style", "primary"),
                            } if raw_c.get("cta_button") else None,
                        },
                    })

                elif b_type == BlockType.TEXT_SECTION.value:
                    body_text = raw_c.get("body", "")
                    paragraphs = [p.strip() for p in body_text.split("\n\n") if p.strip()]
                    sanitized_blocks.append({
                        "id": valid_block_id,
                        "type": "text_section",
                        "content": {
                            "heading": raw_c.get("heading"),
                            "paragraphs": paragraphs,
                        },
                    })

                elif b_type == BlockType.FEATURES_GRID.value:
                    items = []
                    for it in raw_c.get("items", []):
                        if isinstance(it, dict):
                            items.append({
                                "title": it.get("title", ""),
                                "description": it.get("description", ""),
                                "icon": it.get("icon"),
                            })
                    sanitized_blocks.append({
                        "id": valid_block_id,
                        "type": "features_grid",
                        "content": {
                            "title": raw_c.get("title"),
                            "items": items,
                        },
                    })

                elif b_type == BlockType.IMAGE_GALLERY.value:
                    images = []
                    for img in raw_c.get("images", []):
                        if isinstance(img, dict):
                            images.append({
                                "image_url": safe_url(img.get("image_url")),
                                "alt_text": img.get("alt_text", "Gallery photo"),
                                "caption": img.get("caption"),
                            })
                    sanitized_blocks.append({
                        "id": valid_block_id,
                        "type": "image_gallery",
                        "content": {
                            "title": raw_c.get("title"),
                            "images": images,
                        },
                    })

                elif b_type == BlockType.CONTACT_FORM.value:
                    fields = raw_c.get("fields", ["name", "email", "phone", "message"])
                    sanitized_blocks.append({
                        "id": valid_block_id,
                        "type": "contact_form",
                        "content": {
                            "title": raw_c.get("title", "Contact Us"),
                            "submit_label": raw_c.get("submit_label", "Send Message"),
                            "fields": fields,
                        },
                    })

                elif b_type == BlockType.CTA_BANNER.value:
                    btn = raw_c.get("button", {})
                    sanitized_blocks.append({
                        "id": valid_block_id,
                        "type": "cta_banner",
                        "content": {
                            "title": raw_c.get("title", ""),
                            "description": raw_c.get("description"),
                            "button": {
                                "label": btn.get("label", ""),
                                "target_url": safe_url(btn.get("target_url")),
                            },
                        },
                    })

                elif b_type == BlockType.AD_PLACEMENT.value:
                    # Non-billable sandbox placeholder only
                    sanitized_blocks.append({
                        "id": valid_block_id,
                        "type": "ad_placement",
                        "content": {
                            "placement_location": raw_c.get("placement_location", "in_article"),
                            "format": raw_c.get("format", 1),
                            "ad_slot_id": raw_c.get("ad_slot_id"),
                        },
                    })

                elif b_type == BlockType.OPEN_DESIGN_SECTION.value:
                    raw_html = str(raw_c.get("html_content") or "")
                    safe_html = safe_open_design_html_for_render(raw_html)

                    # Sanitize tag
                    raw_tag = str(raw_c.get("section_tag", "section")).lower().strip()
                    section_tag = raw_tag if raw_tag in ("section", "article", "div", "aside", "header", "footer") else "section"

                    # Sanitize section_id
                    raw_id = raw_c.get("section_id")
                    section_id = None
                    if raw_id and isinstance(raw_id, str):
                        clean_id = raw_id.strip()
                        if re.match(r"^[a-zA-Z0-9_\-]+$", clean_id) and not any(p.search(clean_id) for p in SPOOFING_CLASS_OR_ID_PATTERNS):
                            section_id = clean_id

                    # Sanitize css_classes
                    raw_classes = raw_c.get("css_classes")
                    css_classes = None
                    if raw_classes and isinstance(raw_classes, str):
                        clean_cls = raw_classes.strip()
                        if re.match(r"^[a-zA-Z0-9_\-\s]*$", clean_cls) and not any(p.search(clean_cls) for p in SPOOFING_CLASS_OR_ID_PATTERNS):
                            css_classes = clean_cls

                    # Sanitize inline styles
                    styles_dict = raw_c.get("styles") or {}
                    inline_style_parts: list[str] = []
                    if isinstance(styles_dict, dict):
                        for k, v in styles_dict.items():
                            clean_k = str(k).strip().lower()
                            clean_v = str(v).strip()
                            if clean_k in ALLOWED_CSS_PROPERTIES and not any(
                                bad in clean_v.lower()
                                for bad in (";", "url(", "expression(", "@import", "javascript:", "<", ">", "behavior", "-moz-binding")
                            ):
                                inline_style_parts.append(f"{clean_k}: {clean_v}")

                    sanitized_blocks.append({
                        "id": valid_block_id,
                        "type": "open_design_section",
                        "content": {
                            "section_tag": section_tag,
                            "section_id": section_id,
                            "css_classes": css_classes,
                            "inline_style": "; ".join(inline_style_parts),
                            "safe_html": safe_html,
                        },
                    })

                elif b_type == BlockType.FOOTER.value:
                    if valid_block_id:
                        footer_block_id = valid_block_id
                    for lk in raw_c.get("links", []):
                        if isinstance(lk, dict):
                            footer_links.append({
                                "label": lk.get("label", ""),
                                "target_url": safe_url(lk.get("target_url")),
                            })
                    if raw_c.get("copyright_text"):
                        footer_copyright = raw_c.get("copyright_text")

        # Contact submission endpoint
        contact_action_url = f"/api/v1/website/public/{project_dict.get('slug')}/contact"

        # Multilingual & hreflang resolution
        page_lang = page_dict.get("language") or project_dict.get("primary_language") or "en"
        current_trans_group = page_dict.get("translation_group_id")
        hreflang_links: list[dict[str, str]] = []

        if current_trans_group:
            matching_variants = [p for p in all_pages_dict if p.get("translation_group_id") == current_trans_group]
            distinct_langs = {p.get("language", "en") for p in matching_variants}
            if len(distinct_langs) > 1:
                for v in matching_variants:
                    v_lang = v.get("language", "en")
                    v_slug = v.get("slug", "")
                    v_is_home = v.get("is_home", False) or v_slug == ""
                    v_url = f"{base_origin}/" if v_is_home else f"{base_origin}/{v_slug}"
                    hreflang_links.append({"lang": v_lang, "url": v_url})

                default_page = next(
                    (p for p in matching_variants if p.get("language") in ("en", project_dict.get("primary_language"))),
                    matching_variants[0],
                )
                def_slug = default_page.get("slug", "")
                def_url = f"{base_origin}/" if (default_page.get("is_home", False) or def_slug == "") else f"{base_origin}/{def_slug}"
                hreflang_links.append({"lang": "x-default", "url": def_url})

        # Webmaster tools verification
        webmaster_tools = project_dict.get("webmaster_tools") or {}

        # Schema.org JSON-LD structured data (strictly from verified owner facts)
        json_ld_payloads: list[str] = []
        business_profile = project_dict.get("business_profile") or {}
        nap = business_profile.get("nap") or {}

        if business_profile or project_name:
            category = business_profile.get("category", "local_service")
            schema_type = CATEGORY_SCHEMA_MAP.get(category, "LocalBusiness")
            biz_name = business_profile.get("business_name") or project_name

            biz_ld: dict[str, Any] = {
                "@context": "https://schema.org",
                "@type": schema_type,
                "name": biz_name,
                "url": canonical_url,
            }
            if meta_description:
                biz_ld["description"] = meta_description
            if business_profile.get("logo_url"):
                biz_ld["image"] = business_profile["logo_url"]
                biz_ld["logo"] = business_profile["logo_url"]
            if nap.get("phone"):
                biz_ld["telephone"] = nap["phone"]
            if nap.get("email"):
                biz_ld["email"] = nap["email"]

            if nap.get("street_address") or nap.get("city"):
                addr_obj: dict[str, Any] = {
                    "@type": "PostalAddress",
                    "addressCountry": nap.get("country", "IN"),
                }
                if nap.get("street_address"):
                    addr_obj["streetAddress"] = nap["street_address"]
                if nap.get("city"):
                    addr_obj["addressLocality"] = nap["city"]
                if nap.get("state_region"):
                    addr_obj["addressRegion"] = nap["state_region"]
                if nap.get("postal_code"):
                    addr_obj["postalCode"] = nap["postal_code"]
                biz_ld["address"] = addr_obj

            if nap.get("latitude") is not None and nap.get("longitude") is not None:
                biz_ld["geo"] = {
                    "@type": "GeoCoordinates",
                    "latitude": nap["latitude"],
                    "longitude": nap["longitude"],
                }

            hours = business_profile.get("hours") or []
            if hours:
                specs = []
                for h in hours:
                    if not h.get("is_closed") and h.get("opens") and h.get("closes"):
                        specs.append({
                            "@type": "OpeningHoursSpecification",
                            "dayOfWeek": h.get("day", "").capitalize(),
                            "opens": h.get("opens"),
                            "closes": h.get("closes"),
                        })
                if specs:
                    biz_ld["openingHoursSpecification"] = specs

            areas = business_profile.get("service_area_cities") or []
            if areas:
                biz_ld["areaServed"] = areas

            socials = business_profile.get("social_links") or {}
            if socials:
                biz_ld["sameAs"] = list(socials.values())

            json_ld_payloads.append(json.dumps(biz_ld, ensure_ascii=False, indent=2))

        # Breadcrumbs on non-home pages
        if not is_home:
            crumbs_ld = {
                "@context": "https://schema.org",
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {
                        "@type": "ListItem",
                        "position": 1,
                        "name": "Home",
                        "item": f"{base_origin}/",
                    },
                    {
                        "@type": "ListItem",
                        "position": 2,
                        "name": page_title,
                        "item": canonical_url,
                    },
                ],
            }
            json_ld_payloads.append(json.dumps(crumbs_ld, ensure_ascii=False, indent=2))

        # FAQPage
        faqs = business_profile.get("faqs") or []
        if faqs and (is_home or "faq" in page_slug.lower()):
            faq_ld = {
                "@context": "https://schema.org",
                "@type": "FAQPage",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": f.get("question"),
                        "acceptedAnswer": {
                            "@type": "Answer",
                            "text": f.get("answer"),
                        },
                    }
                    for f in faqs
                    if f.get("question") and f.get("answer")
                ],
            }
            json_ld_payloads.append(json.dumps(faq_ld, ensure_ascii=False, indent=2))

        # Services List
        services = business_profile.get("services_or_products") or []
        if services and ("service" in page_slug.lower() or "product" in page_slug.lower()):
            item_list = []
            for idx, s in enumerate(services):
                item_entry: dict[str, Any] = {
                    "@type": "Service",
                    "name": s.get("name"),
                }
                if s.get("description"):
                    item_entry["description"] = s["description"]
                if s.get("price") is not None:
                    item_entry["offers"] = {
                        "@type": "Offer",
                        "price": str(s["price"]),
                        "priceCurrency": s.get("currency", "INR"),
                    }
                item_list.append({
                    "@type": "ListItem",
                    "position": idx + 1,
                    "item": item_entry,
                })
            if item_list:
                services_ld = {
                    "@context": "https://schema.org",
                    "@type": "ItemList",
                    "name": "Services",
                    "itemListElement": item_list,
                }
                json_ld_payloads.append(json.dumps(services_ld, ensure_ascii=False, indent=2))

        template = _jinja_env.get_template("page.html")
        rendered = template.render(
            project_name=project_name,
            theme=theme,
            font_url=font_url,
            page_title=page_title,
            page_lang=page_lang,
            webmaster_tools=webmaster_tools,
            hreflang_links=hreflang_links,
            json_ld_payloads=json_ld_payloads,
            meta_description=meta_description,
            canonical_url=canonical_url,
            home_url=home_url,
            robots_directive=robots_directive,
            og_image_url=og_image,
            nav_pages=nav_pages,
            blocks=sanitized_blocks,
            contact_action_url=contact_action_url,
            footer_links=footer_links,
            footer_copyright=footer_copyright,
            footer_block_id=footer_block_id,
            site_slug=project_dict.get("slug", ""),
            is_preview=is_preview,
            is_monetized=is_monetized,
            bridge_nonce=bridge_nonce,
        )

        if len(rendered.encode("utf-8")) > MAX_OUTPUT_HTML_BYTES:
            raise ValueError("Rendered page size exceeds 1MB security limit.")

        return rendered

    @classmethod
    def render_sitemap_xml(
        cls,
        project_dict: dict[str, Any],
        pages_dict: list[dict[str, Any]],
        base_origin: str,
        published_at_iso: str | None = None,
    ) -> str:
        """Render a valid XML sitemap containing strictly indexable published snapshot pages."""
        urls = []
        for p in pages_dict:
            seo = p.get("seo_meta") or {}
            if seo.get("noindex"):
                continue

            slug = p.get("slug", "")
            is_home = p.get("is_home", False) or slug == ""
            loc = f"{base_origin}/" if is_home else f"{base_origin}/{slug}"

            urls.append({
                "loc": loc,
                "lastmod": published_at_iso,
                "changefreq": "daily" if is_home else "weekly",
                "priority": "1.0" if is_home else "0.8",
            })

        template = _jinja_env.get_template("sitemap.xml")
        return template.render(urls=urls)

    @classmethod
    def render_robots_txt(cls, base_origin: str) -> str:
        """Render robots.txt pointing to the canonical sitemap."""
        template = _jinja_env.get_template("robots.txt")
        return template.render(sitemap_url=f"{base_origin}/sitemap.xml")

    @classmethod
    def render_error_page(
        cls,
        status_code: int,
        title: str,
        message: str,
        home_url: str = "/",
    ) -> str:
        """Render a clean, secure error page without stack traces or internal disclosures."""
        template = _jinja_env.get_template("error.html")
        return template.render(
            status_code=status_code,
            title=html.escape(title),
            message=html.escape(message),
            home_url=home_url,
        )


website_renderer = SafeWebsiteRenderer
