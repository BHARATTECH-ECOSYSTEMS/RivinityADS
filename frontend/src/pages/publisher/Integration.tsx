import { useState } from "react";
import { requestAds } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";
import type { AdResponse } from "../../types";

const SLOT_ID = "banner_home_top";
const SNIPPET = `<div id="oas-slot-${SLOT_ID}"></div>
<script>
(async function () {
  const response = await fetch("/api/v1/ad/request", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ slot_id: "${SLOT_ID}", device: { os: "web" }, num_ads: 1 })
  });
  const result = await response.json();
  const ad = result.ads && result.ads[0];
  if (ad) document.querySelector("#oas-slot-${SLOT_ID}").innerHTML =
    '<a href="' + ad.creative.landing_url + '"><img src="' + ad.creative.image_url + '" alt="' + ad.creative.title + '" /></a>';
})();
</script>`;

export default function Integration() {
  const { session } = useAuth();
  const [copied, setCopied] = useState(false);
  const [ad, setAd] = useState<AdResponse | null>(null);
  const [requestId, setRequestId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadAd = async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await requestAds({ slot_id: SLOT_ID, device: { os: "web" }, num_ads: 1 });
      const nextAd = response.ads[0] ?? null;
      setAd(nextAd);
      setRequestId(response.request_id);
      if (nextAd) new Image().src = nextAd.tracking.impression_url;
      if (!nextAd) setError("No eligible ad was returned for this slot.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not request an ad");
    } finally {
      setLoading(false);
    }
  };

  const copy = async () => {
    try { await navigator.clipboard.writeText(SNIPPET); setCopied(true); window.setTimeout(() => setCopied(false), 1500); } catch { setError("Clipboard access is unavailable."); }
  };

  return <div><h1>Integration</h1><p className="muted">Signed in as {session?.user.name}. Slot: <b>{SLOT_ID}</b></p>
    <div className="card"><div className="actions"><button className="btn" onClick={() => void loadAd()} disabled={loading}>{loading ? "Requesting..." : "Preview live ad"}</button><button className="btn secondary small" onClick={() => void copy()}>{copied ? "Copied!" : "Copy snippet"}</button></div>{error && <p className="error">{error}</p>}{ad && <div className="ad-card"><div>{ad.creative.image_url && <img src={ad.creative.image_url} alt={ad.creative.title ?? "Advertisement"} width={ad.creative.width} height={ad.creative.height} />}</div><div className="ad-body"><div className="sponsored">Sponsored · {ad.creative.creative_type}</div><div className="ad-title">{ad.creative.title}</div>{ad.creative.description && <div className="ad-desc">{ad.creative.description}</div>}<a className="btn small" href={ad.creative.landing_url} target="_blank" rel="noreferrer" onClick={() => { new Image().src = ad.tracking.click_url; }}>Open landing page</a></div></div>}{requestId && <p className="muted">Request ID: {requestId}</p>}</div>
    <div className="card"><h2>Publisher snippet</h2><p className="muted">Place this code on the publisher website where the ad should appear.</p><pre>{SNIPPET}</pre></div>
  </div>;
}
