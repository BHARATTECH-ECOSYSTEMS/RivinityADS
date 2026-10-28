/**
 * rivinity.js — RivinityADS JS SDK v1
 *
 * Drop-in script tag for classic website ad placement.
 *
 * Usage:
 *   <script
 *     src="https://your-cdn/rivinity.js"
 *     data-slot="banner_home_top"
 *     data-api-key="rk_live_..."
 *     data-format="1"
 *     data-num-ads="1"
 *   ></script>
 *
 * Attributes:
 *   data-slot      (required)  Ad slot identifier (matches AdSlot.slot_id).
 *   data-api-key   (optional)  Publisher API key for authenticated requests.
 *   data-format    (optional)  Creative type: 1=banner 2=native 3=video 4=interstitial.
 *   data-num-ads   (optional)  Number of ads to request (default 1).
 *   data-endpoint  (optional)  Override the API base URL (default: same origin).
 *
 * Creative type contract:
 *   - Type 1 (banner):       renders an <img> wrapped in a click link.
 *   - Type 2 (native):       renders title + description + landing link (text card).
 *   - Type 3 (video):        renders a <video> tag with poster fallback.
 *   - Type 4 (interstitial): renders a fixed-position overlay (use sparingly).
 *
 * AI-platform note:
 *   This SDK is for classic web publishers. AI-platform integrations should
 *   use server-to-server POST /api/v1/ad/request with X-API-Key (+ optional
 *   X-Timestamp / X-Signature for HMAC auth). Never embed this script in a
 *   ChatGPT/Claude response — they sandbox third-party scripts.
 */
(function () {
  "use strict";

  var el = document.currentScript;
  if (!el) {
    console.warn("[rivinity] Could not detect currentScript — load synchronously.");
    return;
  }

  // ── Config ────────────────────────────────────────────────────────────────
  var slot     = el.getAttribute("data-slot");
  var apiKey   = el.getAttribute("data-api-key") || "";
  var format   = parseInt(el.getAttribute("data-format") || "0", 10) || null;
  var numAds   = parseInt(el.getAttribute("data-num-ads") || "1", 10);
  var endpoint = (el.getAttribute("data-endpoint") || "").replace(/\/$/, "");

  if (!slot) {
    console.warn("[rivinity] data-slot is required.");
    return;
  }

  // ── Pixel helper ─────────────────────────────────────────────────────────
  function pixel(url) {
    if (!url) return;
    new Image().src = url;
  }

  // ── Request body ─────────────────────────────────────────────────────────
  var body = {
    slot_id: slot,
    device: { os: "web" },
    num_ads: numAds,
  };
  if (format) body.format = format;

  // ── Headers ───────────────────────────────────────────────────────────────
  var headers = { "Content-Type": "application/json" };
  if (apiKey) headers["X-API-Key"] = apiKey;

  // ── Render helpers ────────────────────────────────────────────────────────
  function renderBanner(ad, container) {
    var c = ad.creative;
    if (!c.image_url) return renderNative(ad, container);
    var a = document.createElement("a");
    a.href      = c.landing_url || "#";
    a.target    = "_blank";
    a.rel       = "noopener sponsored";
    a.onclick   = function () { pixel(ad.tracking.click_url); };
    var img     = document.createElement("img");
    img.src     = c.image_url;
    img.width   = c.width  || 300;
    img.height  = c.height || 250;
    img.alt     = c.title  || "Advertisement";
    img.style.cssText = "border:0;display:block;max-width:100%;";
    img.loading = "lazy";
    a.appendChild(img);
    container.appendChild(a);
    _labelSponsored(container);
  }

  function renderNative(ad, container) {
    var c = ad.creative;
    var div = document.createElement("div");
    div.style.cssText = (
      "border:1px solid #e0e0e0;border-radius:6px;padding:12px;" +
      "font-family:sans-serif;max-width:400px;box-sizing:border-box;"
    );
    var a = document.createElement("a");
    a.href    = c.landing_url || "#";
    a.target  = "_blank";
    a.rel     = "noopener sponsored";
    a.onclick = function () { pixel(ad.tracking.click_url); };
    a.style.cssText = "text-decoration:none;color:inherit;display:block;";
    var strong = document.createElement("strong");
    strong.textContent = c.title || "Sponsored";
    strong.style.cssText = "display:block;font-size:1rem;margin-bottom:4px;";
    var desc = document.createElement("span");
    desc.textContent = c.description || "";
    desc.style.cssText = "display:block;font-size:0.875rem;color:#555;margin-bottom:8px;";
    a.appendChild(strong);
    a.appendChild(desc);
    div.appendChild(a);
    _labelSponsored(div);
    container.appendChild(div);
  }

  function renderVideo(ad, container) {
    var c = ad.creative;
    var a = document.createElement("a");
    a.href    = c.landing_url || "#";
    a.target  = "_blank";
    a.rel     = "noopener sponsored";
    a.onclick = function () { pixel(ad.tracking.click_url); };
    var video = document.createElement("video");
    video.src     = c.video_url || "";
    video.poster  = c.image_url || "";
    video.width   = c.width  || 640;
    video.height  = c.height || 360;
    video.controls = true;
    video.muted    = true;
    video.style.cssText = "display:block;max-width:100%;";
    a.appendChild(video);
    container.appendChild(a);
    _labelSponsored(container);
  }

  function _labelSponsored(container) {
    var label = document.createElement("div");
    label.textContent = "Sponsored";
    label.style.cssText = "font-size:10px;color:#aaa;margin-top:2px;font-family:sans-serif;";
    container.appendChild(label);
  }

  function renderAd(ad) {
    var ct = (ad.creative || {}).creative_type || "banner";
    var container = document.createElement("div");
    container.setAttribute("data-ad-id", ad.ad_id || "");
    pixel(ad.tracking.impression_url);
    if (ct === "video") {
      renderVideo(ad, container);
    } else if (ct === "native") {
      renderNative(ad, container);
    } else {
      renderBanner(ad, container);
    }
    el.parentNode.insertBefore(container, el.nextSibling);
  }

  // ── Fetch & render ────────────────────────────────────────────────────────
  fetch(endpoint + "/api/v1/ad/request", {
    method: "POST",
    headers: headers,
    body: JSON.stringify(body),
  })
    .then(function (r) {
      if (!r.ok) throw new Error("rivinity: HTTP " + r.status);
      return r.json();
    })
    .then(function (d) {
      var ads = d.ads || [];
      if (!ads.length) return;
      ads.forEach(renderAd);
    })
    .catch(function (err) {
      console.warn("[rivinity]", err.message || err);
    });

  // ── Global SDK API for Conversion Tracking ─────────────────────────────
  window.Rivinity = window.Rivinity || {};

  // Check URL query parameters for click token on landing page
  try {
    if (typeof window !== "undefined" && window.location && window.location.search) {
      var match = window.location.search.match(/[?&]rv_click_token=([^&]+)/);
      if (match && match[1] && typeof sessionStorage !== "undefined") {
        sessionStorage.setItem("rv_click_token", decodeURIComponent(match[1]));
      }
    }
  } catch (e) {}

  window.Rivinity.trackConversion = function (options) {
    options = options || {};
    var token = options.clickToken;
    if (!token && typeof sessionStorage !== "undefined") {
      token = sessionStorage.getItem("rv_click_token");
    }
    var ep = (options.endpoint || endpoint || "").replace(/\/$/, "");
    var payload = {
      click_token: token || null,
      request_id: options.requestId || null,
      campaign_id: options.campaignId || null,
      creative_id: options.creativeId || null,
      order_id: options.orderId || options.transactionId || null,
      conversion_value: options.value !== undefined ? options.value : null,
      currency: options.currency || "INR",
      is_test: !!options.isTest,
      extra: options.extra || null,
    };

    return fetch(ep + "/api/v1/event/conversion", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (r) {
      return r.json();
    });
  };

  // ── Consent Banner Dismissal ───────────────────────────────────────────
  function initConsentBanner() {
    var banner = document.getElementById("rivinity-consent-banner");
    if (!banner) return;
    var acceptBtn = document.getElementById("rivinity-consent-accept");
    var declineBtn = document.getElementById("rivinity-consent-decline");
    if (acceptBtn) {
      acceptBtn.addEventListener("click", function () {
        banner.style.display = "none";
      });
    }
    if (declineBtn) {
      declineBtn.addEventListener("click", function () {
        banner.style.display = "none";
      });
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initConsentBanner);
  } else {
    initConsentBanner();
  }
})();
