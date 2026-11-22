/**
 * rivinity.js — RivinityADS JS SDK v1 (Phase 1)
 *
 * Privacy-first ad serving & visitor consent gating.
 *
 * Guarantees:
 *   - Default to Denied: Ads and tracking are strictly gated until affirmative visitor consent.
 *   - Client-side Gate: No ad requests, impressions, clicks, or tracking occur without consent.
 *   - Tamper-evident: Uses site-scoped HMAC consent tokens returned by backend.
 *   - Reopen & Withdrawal: Privacy choices can be reviewed, changed, or withdrawn at any time.
 *   - Identifiers Cleared: Withdrawal purges tracking tokens and sessionStorage identifiers.
 */
(function () {
  "use strict";

  var el = document.currentScript || document.querySelector("script[data-slot]") || document.querySelector("script[src*='rivinity.js']");
  if (!el) {
    console.warn("[rivinity] Could not detect script element with data-slot.");
    return;
  }

  // ── Config ────────────────────────────────────────────────────────────────
  var slot     = el.getAttribute("data-slot");
  var siteSlug = el.getAttribute("data-site") || "";
  var apiKey   = el.getAttribute("data-api-key") || "";
  var format   = parseInt(el.getAttribute("data-format") || "0", 10) || null;
  var numAds   = parseInt(el.getAttribute("data-num-ads") || "1", 10);
  var endpoint = (el.getAttribute("data-endpoint") || "").replace(/\/$/, "");

  if (!slot) {
    console.warn("[rivinity] data-slot is required.");
    return;
  }

  // Try resolving siteSlug from page banner if not present on script tag
  if (!siteSlug) {
    var bannerProbe = document.getElementById("rivinity-consent-banner");
    if (bannerProbe) {
      siteSlug = bannerProbe.getAttribute("data-site") || "";
    }
  }

  var storageKey = "rv_consent_" + (siteSlug || "default");
  var policyVersion = "v1.0";

  // ── First-Party Storage Helpers ──────────────────────────────────────────
  function getStoredConsent() {
    try {
      if (typeof localStorage === "undefined") return null;
      var raw = localStorage.getItem(storageKey) || localStorage.getItem("rivinity_consent_" + policyVersion);
      if (!raw) return null;
      var data = JSON.parse(raw);
      if (!data || typeof data !== "object") return null;
      if (data.version && data.version !== policyVersion) {
        localStorage.removeItem(storageKey);
        localStorage.removeItem("rivinity_consent_" + policyVersion);
        return null;
      }
      if (data.expires_at && (Date.now() / 1000) > data.expires_at) {
        localStorage.removeItem(storageKey);
        localStorage.removeItem("rivinity_consent_" + policyVersion);
        return null;
      }
      return data;
    } catch (e) {
      return null;
    }
  }

  function saveStoredConsent(data) {
    try {
      if (typeof localStorage !== "undefined") {
        var str = JSON.stringify(data);
        localStorage.setItem(storageKey, str);
        localStorage.setItem("rivinity_consent_" + policyVersion, str);
      }
    } catch (e) {}
  }

  function clearTrackingIdentifiers() {
    try {
      if (typeof sessionStorage !== "undefined") {
        sessionStorage.removeItem("rv_click_token");
      }
      if (typeof localStorage !== "undefined") {
        localStorage.removeItem("rv_user_id");
      }
    } catch (e) {}
  }

  function getVisitorId() {
    try {
      if (typeof localStorage === "undefined") return "v_anon_" + Math.random().toString(36).substring(2, 10);
      var k = "rv_vid_" + (siteSlug || "default");
      var vid = localStorage.getItem(k);
      if (!vid) {
        vid = "v_" + Math.random().toString(36).substring(2, 10) + Date.now().toString(36);
        localStorage.setItem(k, vid);
      }
      return vid;
    } catch (e) {
      return "v_anon";
    }
  }

  // ── Pixel helper ─────────────────────────────────────────────────────────
  function pixel(url) {
    if (!url) return;
    new Image().src = url;
  }

  // ── Ad Slot Placeholder Renderers ────────────────────────────────────────
  function renderPlaceholder(statusText, noticeText) {
    var existingPlaceholder = el.parentNode ? el.parentNode.querySelector(".rivinity-ad-placeholder") : null;
    if (existingPlaceholder) {
      var statusEl = existingPlaceholder.querySelector(".ad-status");
      var noticeEl = existingPlaceholder.querySelector(".ad-notice");
      if (statusEl) statusEl.textContent = statusText;
      if (noticeEl) noticeEl.textContent = noticeText;
      return;
    }
    var div = document.createElement("div");
    div.className = "rivinity-ad-placeholder";
    div.setAttribute("role", "region");
    div.setAttribute("aria-label", "Advertisement Placeholder");
    div.style.cssText = "width:100%;max-width:728px;min-height:90px;border:2px dashed #cbd5e1;border-radius:8px;background:#f8fafc;padding:1.25rem;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;box-sizing:border-box;margin:1rem 0;";

    var header = document.createElement("div");
    header.style.cssText = "display:flex;gap:0.5rem;margin-bottom:0.5rem;";
    var badge = document.createElement("span");
    badge.textContent = "Advertisement";
    badge.style.cssText = "background:#e2e8f0;color:#475569;font-size:0.6875rem;font-weight:600;padding:2px 8px;border-radius:4px;text-transform:uppercase;";
    var status = document.createElement("span");
    status.className = "ad-status";
    status.textContent = statusText;
    status.style.cssText = "background:#fef3c7;color:#92400e;font-size:0.6875rem;font-weight:600;padding:2px 8px;border-radius:4px;";
    header.appendChild(badge);
    header.appendChild(status);

    var p = document.createElement("p");
    p.className = "ad-notice";
    p.textContent = noticeText;
    p.style.cssText = "margin:0 0 0.25rem 0;font-size:0.875rem;font-weight:500;color:#334155;";

    var sub = document.createElement("p");
    sub.textContent = "Slot: " + slot + " • Non-Billable";
    sub.style.cssText = "margin:0;font-size:0.75rem;color:#64748b;";

    div.appendChild(header);
    div.appendChild(p);
    div.appendChild(sub);

    if (el.parentNode) {
      el.parentNode.insertBefore(div, el.nextSibling);
    }
  }

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
    // Remove placeholder if present
    if (el.parentNode) {
      var ph = el.parentNode.querySelector(".rivinity-ad-placeholder");
      if (ph) ph.remove();
    }
    var ct = (ad.creative || {}).creative_type || "banner";
    var container = document.createElement("div");
    if (slot) {
      container.id = "rivinity-ad-container-" + slot;
    }
    container.setAttribute("data-ad-id", ad.ad_id || "");
    pixel(ad.tracking.impression_url);
    if (ct === "video") {
      renderVideo(ad, container);
    } else if (ct === "native") {
      renderNative(ad, container);
    } else {
      renderBanner(ad, container);
    }
    if (el.parentNode) {
      el.parentNode.insertBefore(container, el.nextSibling);
    }
  }

  // ── Ad Request Dispatcher ────────────────────────────────────────────────
  var adRequestFired = false;
  function requestAds(consentToken) {
    if (adRequestFired) return;
    adRequestFired = true;

    var body = {
      slot_id: slot,
      device: { os: "web" },
      num_ads: numAds,
    };
    if (format) body.format = format;
    if (consentToken) body.consent_token = consentToken;

    var headers = { "Content-Type": "application/json" };
    if (apiKey) headers["X-API-Key"] = apiKey;
    if (consentToken) headers["X-Consent-Token"] = consentToken;

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
        if (!ads.length) {
          renderPlaceholder("No Fill", "No active campaigns currently match this placement.");
          return;
        }
        ads.forEach(renderAd);
      })
      .catch(function (err) {
        console.warn("[rivinity]", err.message || err);
        var msg = (err && err.message) ? err.message.toLowerCase() : "";
        if (msg.indexOf("inactive") !== -1 || msg.indexOf("monetization") !== -1) {
          renderPlaceholder("Monetisation Inactive", "Website monetization is currently inactive.");
        } else if (msg.indexOf("preview") !== -1 || msg.indexOf("sandbox") !== -1) {
          renderPlaceholder("Preview / Sandbox Mode", "Preview mode active. Ads are non-billable.");
        } else {
          renderPlaceholder("Ad Service Offline", "Unable to load advertisement.");
        }
      });
  }

  // ── Client-Side Consent Gate ─────────────────────────────────────────────
  function evaluateConsentAndServe() {
    var consent = getStoredConsent();

    if (!consent) {
      // New visitor: No choice made. Default to Denied.
      renderPlaceholder("Consent Required", "Privacy choices pending. Advertisements require consent.");
      return;
    }

    if (consent.is_granted === true && consent.categories && consent.categories.advertising) {
      // Consented: Proceed with ad requests
      requestAds(consent.consent_token);
    } else {
      // Declined / Withdrawn: Suppress all ads and tracking
      clearTrackingIdentifiers();
      renderPlaceholder("Consent Declined", "Visitor consent declined. Ads and tracking suppressed.");
    }
  }

  // ── Global SDK API ───────────────────────────────────────────────────────
  window.Rivinity = window.Rivinity || {};

  window.Rivinity.getConsent = function () {
    return getStoredConsent();
  };

  window.Rivinity.setConsent = function (categories, options) {
    options = options || {};
    var slug = options.siteSlug || siteSlug;
    var ep = (options.endpoint || endpoint || "").replace(/\/$/, "");
    if (!slug) {
      return Promise.reject(new Error("siteSlug is required to record consent"));
    }
    var vid = options.visitorId || getVisitorId();
    var payload = {
      version: options.version || policyVersion,
      visitor_id: vid,
      categories: {
        necessary: true,
        advertising: !!(categories && categories.advertising),
        analytics: !!(categories && categories.analytics),
      },
    };

    return fetch(ep + "/api/v1/website/public/" + encodeURIComponent(slug) + "/consent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) {
        if (!r.ok) throw new Error("Consent API failed: HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        var isGranted = !!(data.categories && data.categories.advertising);
        var record = {
          site_slug: slug,
          is_granted: isGranted,
          categories: data.categories,
          version: payload.version,
          consent_token: data.consent_token || null,
          expires_at: data.expires_at || (Math.floor(Date.now() / 1000) + 604800),
          recorded_at: Date.now(),
        };
        saveStoredConsent(record);
        if (!isGranted) {
          clearTrackingIdentifiers();
        }
        evaluateConsentAndServe();
        return data;
      });
  };

  window.Rivinity.withdrawConsent = function (options) {
    return window.Rivinity.setConsent({ advertising: false, analytics: false }, options);
  };

  window.Rivinity.openConsentSettings = function () {
    var modal = document.getElementById("rivinity-consent-modal");
    if (!modal) return;
    var consent = getStoredConsent();
    var adBox = document.getElementById("rivinity-consent-opt-ad");
    var analyticsBox = document.getElementById("rivinity-consent-opt-analytics");
    if (adBox) adBox.checked = !!(consent && consent.categories && consent.categories.advertising);
    if (analyticsBox) analyticsBox.checked = !!(consent && consent.categories && consent.categories.analytics);
    modal.style.display = "block";
    try { if (typeof modal.showModal === "function") modal.showModal(); } catch (e) {}
  };

  // URL Click Token Capture for Conversion Tracking (strictly with consent)
  try {
    if (typeof window !== "undefined" && window.location && window.location.search) {
      var match = window.location.search.match(/[?&]rv_click_token=([^&]+)/);
      if (match && match[1] && typeof sessionStorage !== "undefined") {
        var c = getStoredConsent();
        if (c && c.is_granted) {
          sessionStorage.setItem("rv_click_token", decodeURIComponent(match[1]));
        }
      }
    }
  } catch (e) {}

  window.Rivinity.trackConversion = function (options) {
    options = options || {};
    var consent = getStoredConsent();
    if (!consent || !consent.is_granted) {
      console.warn("[rivinity] Conversion tracking suppressed: visitor consent not granted.");
      return Promise.resolve({ success: false, reason: "consent_required" });
    }

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
      extra: options.extra || {},
    };
    if (consent.consent_token) {
      payload.extra.consent_token = consent.consent_token;
    }

    return fetch(ep + "/api/v1/event/conversion", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).then(function (r) {
      return r.json();
    });
  };

  // ── Consent Banner UI Wiring ─────────────────────────────────────────────
  function initConsentUI() {
    var banner = document.getElementById("rivinity-consent-banner");
    var modal = document.getElementById("rivinity-consent-modal");
    var reopenBtn = document.getElementById("rivinity-consent-reopen");
    var stored = getStoredConsent();

    if (banner) {
      if (stored) {
        banner.style.display = "none";
        if (reopenBtn) reopenBtn.style.display = "block";
      } else {
        banner.style.display = "flex";
      }

      var acceptBtn = document.getElementById("rivinity-consent-accept");
      var declineBtn = document.getElementById("rivinity-consent-decline");
      var manageBtn = document.getElementById("rivinity-consent-manage");

      function showBannerError(msg) {
        var errEl = banner.querySelector(".rivinity-consent-error");
        if (!errEl) {
          errEl = document.createElement("div");
          errEl.className = "rivinity-consent-error";
          errEl.style.cssText = "color:#fca5a5;font-size:0.75rem;margin-top:0.5rem;width:100%;";
          banner.appendChild(errEl);
        }
        errEl.textContent = msg || "Unable to save preferences. Please check your connection and try again.";
      }

      if (acceptBtn) {
        acceptBtn.addEventListener("click", function () {
          window.Rivinity.setConsent({ advertising: true, analytics: true }, { siteSlug: siteSlug })
            .then(function () {
              banner.style.display = "none";
              if (reopenBtn) reopenBtn.style.display = "block";
            })
            .catch(function (err) {
              console.error("[rivinity] Failed to record consent", err);
              showBannerError("Unable to record consent. Please check your connection and try again.");
              evaluateConsentAndServe();
            });
        });
      }

      if (declineBtn) {
        declineBtn.addEventListener("click", function () {
          window.Rivinity.withdrawConsent({ siteSlug: siteSlug })
            .then(function () {
              banner.style.display = "none";
              if (reopenBtn) reopenBtn.style.display = "block";
            })
            .catch(function (err) {
              console.error("[rivinity] Failed to record decline", err);
              showBannerError("Unable to record preferences. Please check your connection and try again.");
              evaluateConsentAndServe();
            });
        });
      }

      if (manageBtn) {
        manageBtn.addEventListener("click", function () {
          window.Rivinity.openConsentSettings();
        });
      }
    }

    if (modal) {
      var cancelBtn = document.getElementById("rivinity-consent-modal-cancel");
      var saveBtn = document.getElementById("rivinity-consent-modal-save");

      if (cancelBtn) {
        cancelBtn.addEventListener("click", function () {
          modal.style.display = "none";
          try { if (typeof modal.close === "function") modal.close(); } catch (e) {}
        });
      }

      if (saveBtn) {
        saveBtn.addEventListener("click", function () {
          var adBox = document.getElementById("rivinity-consent-opt-ad");
          var analyticsBox = document.getElementById("rivinity-consent-opt-analytics");
          var chosenCategories = {
            advertising: adBox ? adBox.checked : false,
            analytics: analyticsBox ? analyticsBox.checked : false,
          };
          window.Rivinity.setConsent(chosenCategories, { siteSlug: siteSlug })
            .then(function () {
              modal.style.display = "none";
              try { if (typeof modal.close === "function") modal.close(); } catch (e) {}
              if (banner) banner.style.display = "none";
              if (reopenBtn) reopenBtn.style.display = "block";
            })
            .catch(function (err) {
              console.error("[rivinity] Failed to save choices", err);
              var errEl = modal.querySelector(".rivinity-consent-error");
              if (!errEl) {
                errEl = document.createElement("div");
                errEl.className = "rivinity-consent-error";
                errEl.style.cssText = "color:#fca5a5;font-size:0.75rem;margin-top:0.5rem;";
                modal.appendChild(errEl);
              }
              errEl.textContent = "Unable to save preferences. Please check your connection and try again.";
            });
        });
      }
    }

    if (reopenBtn) {
      reopenBtn.addEventListener("click", function () {
        window.Rivinity.openConsentSettings();
      });
    }

    // Now evaluate consent and decide whether to request ads
    evaluateConsentAndServe();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initConsentUI);
  } else {
    initConsentUI();
  }
})();
