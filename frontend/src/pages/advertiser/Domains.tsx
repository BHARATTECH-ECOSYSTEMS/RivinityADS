import { useEffect, useState, type FormEvent } from "react";
import {
  ApiError,
  deleteAdvertiserDomain,
  getDomainInstructions,
  listAdvertiserDomains,
  regenerateDomainChallenge,
  registerAdvertiserDomain,
  verifyAdvertiserDomain,
  type AdvertiserDomain,
  type DomainVerificationInstructions,
} from "../../api/client";

function errorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function statusLabel(status: AdvertiserDomain["status"]) {
  return status === "VERIFIED" ? "Verified" : status === "FAILED" ? "Verification failed" : "Pending verification";
}

export default function AdvertiserDomains() {
  const [domains, setDomains] = useState<AdvertiserDomain[]>([]);
  const [domain, setDomain] = useState("");
  const [instructions, setInstructions] = useState<Record<number, DomainVerificationInstructions>>({});
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [actionId, setActionId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadDomains = async () => {
    setLoading(true);
    setError(null);
    try {
      setDomains(await listAdvertiserDomains());
    } catch (err) {
      setError(errorMessage(err, "Could not load your domains."));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadDomains(); }, []);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (submitting) return;
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      await registerAdvertiserDomain(domain.trim());
      setDomain("");
      setNotice("Domain registered. Add the DNS TXT record shown below, then verify ownership.");
      await loadDomains();
    } catch (err) {
      setError(errorMessage(err, "Could not register the domain."));
    } finally {
      setSubmitting(false);
    }
  };

  const showInstructions = async (domainRecord: AdvertiserDomain) => {
    setActionId(domainRecord.id);
    setError(null);
    try {
      const result = await getDomainInstructions(domainRecord.id);
      setInstructions((current) => ({ ...current, [domainRecord.id]: result }));
    } catch (err) {
      setError(errorMessage(err, "Could not load verification instructions."));
    } finally {
      setActionId(null);
    }
  };

  const verify = async (domainRecord: AdvertiserDomain) => {
    setActionId(domainRecord.id);
    setError(null);
    setNotice(null);
    try {
      const updated = await verifyAdvertiserDomain(domainRecord.id);
      setDomains((current) => current.map((item) => item.id === updated.id ? updated : item));
      if (updated.status === "VERIFIED") {
        setNotice(`${updated.domain} is verified.`);
      } else {
        setError(updated.last_error ?? "DNS verification did not pass.");
      }
    } catch (err) {
      setError(errorMessage(err, "Could not verify the domain."));
    } finally {
      setActionId(null);
    }
  };

  const regenerate = async (domainRecord: AdvertiserDomain) => {
    setActionId(domainRecord.id);
    setError(null);
    setNotice(null);
    try {
      const updated = await regenerateDomainChallenge(domainRecord.id);
      setDomains((current) => current.map((item) => item.id === updated.id ? updated : item));
      setInstructions((current) => {
        const next = { ...current };
        delete next[updated.id];
        return next;
      });
      setNotice("A new verification challenge was generated. The previous challenge is no longer the one to publish.");
      await showInstructions(updated);
    } catch (err) {
      setError(errorMessage(err, "Could not regenerate the verification challenge."));
    } finally {
      setActionId(null);
    }
  };

  const remove = async (domainRecord: AdvertiserDomain) => {
    if (!window.confirm(`Remove ${domainRecord.domain} from your advertiser account?`)) return;
    setActionId(domainRecord.id);
    setError(null);
    setNotice(null);
    try {
      await deleteAdvertiserDomain(domainRecord.id);
      setDomains((current) => current.filter((item) => item.id !== domainRecord.id));
      setInstructions((current) => {
        const next = { ...current };
        delete next[domainRecord.id];
        return next;
      });
      setNotice(`${domainRecord.domain} was removed.`);
    } catch (err) {
      setError(errorMessage(err, "Could not remove the domain."));
    } finally {
      setActionId(null);
    }
  };

  const copy = async (value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setNotice("Copied to clipboard.");
    } catch {
      setError("Could not copy to clipboard. Select and copy the value manually.");
    }
  };

  return <div>
    <h1>Advertiser domains</h1>
    <p className="muted">Verify domains before using them in campaign landing URLs.</p>
    <div className="card">
      <h2>Register a domain</h2>
      <form onSubmit={submit} className="actions">
        <label htmlFor="domain-name">Domain name</label>
        <input id="domain-name" value={domain} onChange={(event) => setDomain(event.target.value)} placeholder="example.com" required />
        <button className="btn" disabled={submitting}>{submitting ? "Registering..." : "Register domain"}</button>
      </form>
    </div>
    {error && <p role="alert" className="error">{error}</p>}
    {notice && <p role="status" className="success">{notice}</p>}
    <div className="card">
      <h2>Your domains</h2>
      {loading ? <p role="status" className="muted">Loading domains...</p> : domains.length === 0 ? <p className="muted">No domains registered yet.</p> : <div className="domain-list">{domains.map((item) => {
        const detail = instructions[item.id];
        const busy = actionId === item.id;
        return <article className="domain-item" key={item.id}>
          <div className="domain-header"><div><h3>{item.domain}</h3><span className={`badge ${item.status === "VERIFIED" ? "active" : "paused"}`}>{statusLabel(item.status)}</span></div><span className="muted">Method: {item.verification_method}</span></div>
          {item.last_error && <p role="alert" className="error">{item.last_error}</p>}
          <div className="actions">
            {item.status !== "VERIFIED" && <button className="btn small" onClick={() => void showInstructions(item)} disabled={busy}>{busy ? "Working..." : "DNS instructions"}</button>}
            {item.status !== "VERIFIED" && <button className="btn small" onClick={() => void verify(item)} disabled={busy}>Verify ownership</button>}
            {item.status !== "VERIFIED" && <button className="btn secondary small" onClick={() => void regenerate(item)} disabled={busy}>Regenerate challenge</button>}
            <button className="btn secondary small" onClick={() => void remove(item)} disabled={busy}>Delete</button>
          </div>
          {detail && item.status !== "VERIFIED" && <div className="domain-instructions"><p>{detail.instructions}</p><dl><dt>Record type</dt><dd>{detail.record_type}</dd><dt>Record name</dt><dd><code>{detail.record_name}</code></dd><dt>Record value</dt><dd><code>{detail.record_value}</code> <button className="btn secondary small" onClick={() => void copy(detail.record_value)}>Copy</button></dd></dl><p className="muted">DNS changes may take time to propagate. Retry verification after the record is available.</p></div>}
        </article>;
      })}</div>}
    </div>
  </div>;
}
