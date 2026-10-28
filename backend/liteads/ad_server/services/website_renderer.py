"""Safe server-side HTML rendering engine for published website builder pages."""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import urlparse

import jinja2

from liteads.common.logger import get_logger
from liteads.schemas.website import (
    ALLOWED_FONTS,
    HEX_COLOR_PATTERN,
    BlockType,
    PageBlocksPayload,
    ThemeConfig,
)

logger = get_logger(__name__)

# Safe CSS border-radius token pattern
BORDER_RADIUS_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+)?(?:px|rem|em|%)?$")

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


# ── Jinja2 Templates ────────────────────────────────────────────────────────

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
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

  <!-- Open Graph -->
  <meta property="og:title" content="{{ page_title }} | {{ project_name }}">
  {% if meta_description %}<meta property="og:description" content="{{ meta_description }}">{% endif %}
  <meta property="og:url" content="{{ canonical_url }}">
  <meta property="og:type" content="website">
  {% if og_image_url and og_image_url != '#' %}
  <meta property="og:image" content="{{ og_image_url }}">
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
  <div class="preview-banner" role="alert">
    <strong>Draft Preview Mode</strong> — This is an unpublished draft preview of <em>{{ project_name }}</em>. Monetization and indexing are disabled.
  </div>
  {% endif %}

  <header class="site-header">
    <div class="container header-inner">
      <a href="{{ home_url }}" class="site-brand">
        {% if theme.logo_url and theme.logo_url != '#' %}
        <img src="{{ theme.logo_url }}" alt="{{ project_name }} logo" class="brand-logo">
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
      <section class="block-hero" {% if block.content.background_image_url and block.content.background_image_url != '#' %}style="background-image: linear-gradient(rgba(0,0,0,0.4), rgba(0,0,0,0.4)), url('{{ block.content.background_image_url }}'); color: #ffffff;"{% endif %}>
        <div class="container">
          <div class="hero-content align-{{ block.content.alignment or 'left' }}">
            <h1 class="hero-headline">{{ block.content.headline }}</h1>
            {% if block.content.subheadline %}
            <p class="hero-subheadline">{{ block.content.subheadline }}</p>
            {% endif %}
            {% if block.content.cta_button and block.content.cta_button.target_url != '#' %}
            <a href="{{ block.content.cta_button.target_url }}" class="btn btn-{{ block.content.cta_button.style or 'primary' }}">
              {{ block.content.cta_button.label }}
            </a>
            {% endif %}
          </div>
        </div>
      </section>

      {% elif block.type == 'text_section' %}
      <section class="block-text-section">
        <div class="container">
          {% if block.content.heading %}
          <h2 class="text-heading">{{ block.content.heading }}</h2>
          {% endif %}
          <div class="text-body">
            {% for para in block.content.paragraphs %}
            <p>{{ para }}</p>
            {% endfor %}
          </div>
        </div>
      </section>

      {% elif block.type == 'features_grid' %}
      <section class="block-features-grid">
        <div class="container">
          {% if block.content.title %}
          <h2 class="features-title">{{ block.content.title }}</h2>
          {% endif %}
          <div class="features-grid">
            {% for item in block.content['items'] %}
            <div class="feature-card">
              <div class="feature-icon-box" aria-hidden="true">★</div>
              <h3 class="feature-title">{{ item.title }}</h3>
              <p class="feature-desc">{{ item.description }}</p>
            </div>
            {% endfor %}
          </div>
        </div>
      </section>

      {% elif block.type == 'image_gallery' %}
      <section class="block-image-gallery">
        <div class="container">
          {% if block.content.title %}
          <h2 class="gallery-title">{{ block.content.title }}</h2>
          {% endif %}
          <div class="gallery-grid">
            {% for img in block.content.images %}
            {% if img.image_url != '#' %}
            <figure class="gallery-item">
              <img src="{{ img.image_url }}" alt="{{ img.alt_text }}" loading="lazy" class="gallery-img">
              {% if img.caption %}
              <figcaption class="gallery-caption">{{ img.caption }}</figcaption>
              {% endif %}
            </figure>
            {% endif %}
            {% endfor %}
          </div>
        </div>
      </section>

      {% elif block.type == 'contact_form' %}
      <section class="block-contact-form">
        <div class="container">
          <div class="contact-card">
            <h2 class="contact-title">{{ block.content.title or 'Contact Us' }}</h2>
            <form method="POST" action="{{ contact_action_url }}">
              <div style="display:none !important;" aria-hidden="true">
                <input type="text" name="hp_comment" tabindex="-1" autocomplete="off">
              </div>
              {% for field in block.content.fields %}
                {% if field == 'message' %}
                <div class="form-group">
                  <label for="contact-{{ field }}" class="form-label">Message</label>
                  <textarea id="contact-{{ field }}" name="{{ field }}" class="form-textarea" required></textarea>
                </div>
                {% elif field == 'email' %}
                <div class="form-group">
                  <label for="contact-{{ field }}" class="form-label">Email Address</label>
                  <input type="email" id="contact-{{ field }}" name="{{ field }}" class="form-input" required>
                </div>
                {% elif field == 'phone' %}
                <div class="form-group">
                  <label for="contact-{{ field }}" class="form-label">Phone Number</label>
                  <input type="tel" id="contact-{{ field }}" name="{{ field }}" class="form-input">
                </div>
                {% else %}
                <div class="form-group">
                  <label for="contact-{{ field }}" class="form-label">{{ field|capitalize }}</label>
                  <input type="text" id="contact-{{ field }}" name="{{ field }}" class="form-input" required>
                </div>
                {% endif %}
              {% endfor %}
              <button type="submit" class="btn btn-primary" style="width: 100%;">
                {{ block.content.submit_label or 'Send Message' }}
              </button>
            </form>
          </div>
        </div>
      </section>

      {% elif block.type == 'cta_banner' %}
      <section class="block-cta-banner">
        <div class="container">
          <h2 class="cta-title">{{ block.content.title }}</h2>
          {% if block.content.description %}
          <p class="cta-desc">{{ block.content.description }}</p>
          {% endif %}
          {% if block.content.button and block.content.button.target_url != '#' %}
          <a href="{{ block.content.button.target_url }}" class="btn btn-light">
            {{ block.content.button.label }}
          </a>
          {% endif %}
        </div>
      </section>

      {% elif block.type == 'ad_placement' %}
      <div class="container">
        <div class="block-ad-placement" data-location="{{ block.content.placement_location }}">
          {% if is_monetized and not is_preview and block.content.ad_slot_id %}
          <div class="rivinity-live-ad-container" role="region" aria-label="Sponsored Advertisement">
            <div id="rivinity-ad-{{ block.content.ad_slot_id }}" class="rivinity-ad-slot" data-slot-id="{{ block.content.ad_slot_id }}" data-format="{{ block.content.format or 1 }}">
              <script async src="/static/rivinity.js" data-slot="{{ block.content.ad_slot_id }}" data-format="{{ block.content.format or 1 }}"></script>
            </div>
            <div class="ad-label" style="font-size:0.6875rem;color:#94a3b8;text-align:right;margin-top:2px;">Sponsored</div>
          </div>
          {% else %}
          <div class="rivinity-ad-placeholder" data-slot="{{ block.content.ad_slot_id or '' }}" data-format="{{ block.content.format or 1 }}" role="region" aria-label="Advertisement Placeholder">
            <div class="ad-placeholder-header">
              <span class="ad-badge">Advertisement</span>
              <span class="ad-status">Non-Billable Sandbox</span>
            </div>
            <p class="ad-notice">RivinityADS Ad Placement Placeholder</p>
            <p class="ad-subtext">Slot: {{ block.content.ad_slot_id or 'Auto' }} &bull; Format: {{ 'Banner' if block.content.format == 1 else 'Native' }} &bull; Monetization Inactive</p>
          </div>
          {% endif %}
        </div>
      </div>

      {% elif block.type == 'footer' %}
      <!-- Footer embedded via block -->
      {% endif %}
    {% endfor %}
  </main>

  <!-- Default or Structured Footer -->
  <footer class="site-footer">
    <div class="container footer-inner">
      {% if footer_links %}
      <ul class="footer-links">
        {% for link in footer_links %}
        <li><a href="{{ link.target_url }}" class="footer-link">{{ link.label }}</a></li>
        {% endfor %}
      </ul>
      {% endif %}
      <p class="footer-copy">{{ footer_copyright or ('&copy; ' ~ project_name ~ '. Powered by RivinityADS.') }}</p>
    </div>
  </footer>

  {% if is_monetized and not is_preview %}
  <aside id="rivinity-consent-banner" class="rivinity-consent-banner" role="dialog" aria-label="Privacy and Advertising Choices" style="position:fixed;bottom:0;left:0;right:0;background:#1e293b;color:#f8fafc;padding:1rem 1.5rem;display:flex;align-items:center;justify-content:space-between;gap:1rem;z-index:999;box-shadow:0 -4px 12px rgba(0,0,0,0.15);font-size:0.875rem;">
    <div style="max-width:800px;">
      <strong>Privacy & Advertising Choices:</strong> This website is monetized through RivinityADS. We and our advertising partners use cookies and device identifiers to measure ad performance.
    </div>
    <div style="display:flex;gap:0.75rem;flex-shrink:0;">
      <button id="rivinity-consent-accept" class="btn btn-primary" style="padding:0.4rem 1rem;font-size:0.8125rem;">Accept</button>
      <button id="rivinity-consent-decline" class="btn btn-secondary" style="padding:0.4rem 1rem;font-size:0.8125rem;">Decline</button>
    </div>
  </aside>
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

                # Block sanitization dispatch
                if b_type == BlockType.HERO.value:
                    sanitized_blocks.append({
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
                        "type": "image_gallery",
                        "content": {
                            "title": raw_c.get("title"),
                            "images": images,
                        },
                    })

                elif b_type == BlockType.CONTACT_FORM.value:
                    fields = raw_c.get("fields", ["name", "email", "phone", "message"])
                    sanitized_blocks.append({
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
                        "type": "ad_placement",
                        "content": {
                            "placement_location": raw_c.get("placement_location", "in_article"),
                            "format": raw_c.get("format", 1),
                            "ad_slot_id": raw_c.get("ad_slot_id"),
                        },
                    })

                elif b_type == BlockType.FOOTER.value:
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

        template = _jinja_env.get_template("page.html")
        rendered = template.render(
            project_name=project_name,
            theme=theme,
            font_url=font_url,
            page_title=page_title,
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
            is_preview=is_preview,
            is_monetized=is_monetized,
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
