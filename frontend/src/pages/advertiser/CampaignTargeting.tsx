import { useEffect, useState, type FormEvent } from "react";
import {
  type GeoEntry,
  type TargetingSet,
  getCampaignTargeting,
  getReferenceCities,
  getReferenceCountries,
  getReferencePincodes,
  getReferenceSectors,
  getReferenceStates,
  updateCampaignTargeting,
} from "../../api/client";

export default function CampaignTargeting({ campaignId }: { campaignId: number }) {
  const [targeting, setTargeting] = useState<TargetingSet | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // Reference Data State
  const [sectors, setSectors] = useState<string[]>([]);
  const [countries, setCountries] = useState<string[]>([]);
  const [states, setStates] = useState<{ code: string; name: string }[]>([]);
  const [cities, setCities] = useState<string[]>([]);
  const [pincodes, setPincodes] = useState<string[]>([]);

  // Selection State
  const [selSector, setSelSector] = useState("");
  const [selCountry, setSelCountry] = useState("");
  const [selState, setSelState] = useState("");
  const [selCity, setSelCity] = useState("");
  const [selPincode, setSelPincode] = useState("");

  const loadTargeting = async () => {
    try {
      const data = await getCampaignTargeting(campaignId);
      setTargeting(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load targeting");
    } finally {
      setLoading(false);
    }
  };

  const loadReferences = async () => {
    try {
      const [sData, cData] = await Promise.all([getReferenceSectors(), getReferenceCountries()]);
      setSectors(sData);
      setCountries(cData);
    } catch (err) {
      console.error(err);
    }
  };

  useEffect(() => {
    void loadTargeting();
    void loadReferences();
  }, [campaignId]);

  useEffect(() => {
    setSelState("");
    setSelCity("");
    setSelPincode("");
    if (selCountry) {
      void getReferenceStates(selCountry).then(setStates);
    } else {
      setStates([]);
    }
  }, [selCountry]);

  useEffect(() => {
    setSelCity("");
    setSelPincode("");
    if (selCountry && selState) {
      void getReferenceCities(selCountry, selState).then(setCities);
    } else {
      setCities([]);
    }
  }, [selState]);

  useEffect(() => {
    setSelPincode("");
    if (selCountry && selState && selCity) {
      void getReferencePincodes(selCountry, selState, selCity).then(setPincodes);
    } else {
      setPincodes([]);
    }
  }, [selCity]);

  const addSector = (type: "include" | "exclude") => {
    if (!selSector || !targeting) return;
    const key = type === "include" ? "sector_include" : "sector_exclude";
    if (!targeting[key].includes(selSector)) {
      setTargeting({ ...targeting, [key]: [...targeting[key], selSector] });
    }
    setSelSector("");
  };

  const removeSector = (type: "include" | "exclude", sector: string) => {
    if (!targeting) return;
    const key = type === "include" ? "sector_include" : "sector_exclude";
    setTargeting({ ...targeting, [key]: targeting[key].filter((s) => s !== sector) });
  };

  const addGeo = (type: "include" | "exclude") => {
    if (!selCountry || !targeting) return;
    const entry: GeoEntry = { country: selCountry };
    if (selState) entry.state = selState;
    if (selCity) entry.city = selCity;
    if (selPincode) entry.pincode = selPincode;
    
    // Check if duplicate entry exists (rough stringification comparison)
    const key = type === "include" ? "geo_include" : "geo_exclude";
    const exists = targeting[key].some(e => JSON.stringify(e) === JSON.stringify(entry));
    if (!exists) {
       setTargeting({ ...targeting, [key]: [...targeting[key], entry] });
    }
    setSelCountry("");
  };

  const removeGeo = (type: "include" | "exclude", index: number) => {
    if (!targeting) return;
    const key = type === "include" ? "geo_include" : "geo_exclude";
    const newArr = [...targeting[key]];
    newArr.splice(index, 1);
    setTargeting({ ...targeting, [key]: newArr });
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!targeting) return;
    try {
      setLoading(true);
      await updateCampaignTargeting(campaignId, targeting);
      setError(null);
      alert("Targeting saved successfully!");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save targeting");
    } finally {
      setLoading(false);
    }
  };

  if (loading && !targeting) return <p className="muted">Loading targeting...</p>;

  return (
    <div className="card">
      <h2>Targeting</h2>
      {error && <p className="error">{error}</p>}
      
      <div className="form-grid">
        <div className="field">
          <label>Sector Targeting</label>
          <div style={{ display: "flex", gap: "10px", marginBottom: "10px" }}>
            <select value={selSector} onChange={(e) => setSelSector(e.target.value)}>
              <option value="">-- Select Sector --</option>
              {sectors.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <button type="button" className="btn small" onClick={() => addSector("include")}>Add to Include</button>
            <button type="button" className="btn small secondary" onClick={() => addSector("exclude")}>Add to Exclude</button>
          </div>
          <div style={{ display: "flex", gap: "20px" }}>
            <div>
              <strong>Include:</strong>
              <ul style={{ paddingLeft: "20px", marginTop: "5px" }}>
                {targeting?.sector_include.map((s) => (
                  <li key={s}>{s} <button type="button" onClick={() => removeSector("include", s)} style={{border: 'none', background: 'none', color: 'red', cursor: 'pointer'}}>x</button></li>
                ))}
              </ul>
            </div>
            <div>
              <strong>Exclude:</strong>
              <ul style={{ paddingLeft: "20px", marginTop: "5px" }}>
                {targeting?.sector_exclude.map((s) => (
                  <li key={s}>{s} <button type="button" onClick={() => removeSector("exclude", s)} style={{border: 'none', background: 'none', color: 'red', cursor: 'pointer'}}>x</button></li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </div>
      
      <hr style={{ margin: "20px 0" }} />

      <div className="form-grid">
        <div className="field">
          <label>Geo Targeting (Country &rarr; State &rarr; City &rarr; Pincode)</label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "10px", marginBottom: "10px" }}>
            <select value={selCountry} onChange={(e) => setSelCountry(e.target.value)}>
              <option value="">-- Country --</option>
              {countries.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <select value={selState} onChange={(e) => setSelState(e.target.value)} disabled={!selCountry || states.length === 0}>
              <option value="">-- State --</option>
              {states.map((s) => <option key={s.code} value={s.code}>{s.name} ({s.code})</option>)}
            </select>
            <select value={selCity} onChange={(e) => setSelCity(e.target.value)} disabled={!selState || cities.length === 0}>
              <option value="">-- City --</option>
              {cities.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            <select value={selPincode} onChange={(e) => setSelPincode(e.target.value)} disabled={!selCity || pincodes.length === 0}>
              <option value="">-- Pincode --</option>
              {pincodes.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </div>
          <div style={{ display: "flex", gap: "10px", marginBottom: "15px" }}>
            <button type="button" className="btn small" onClick={() => addGeo("include")}>Add to Include</button>
            <button type="button" className="btn small secondary" onClick={() => addGeo("exclude")}>Add to Exclude</button>
          </div>
          <div style={{ display: "flex", gap: "20px", flexWrap: "wrap" }}>
            <div style={{ flex: 1 }}>
              <strong>Include:</strong>
              <ul style={{ paddingLeft: "20px", marginTop: "5px" }}>
                {targeting?.geo_include.map((g, idx) => (
                  <li key={idx}>
                    {g.country}{g.state ? `, ${g.state}` : ""}{g.city ? `, ${g.city}` : ""}{g.pincode ? ` (${g.pincode})` : ""}
                    <button type="button" onClick={() => removeGeo("include", idx)} style={{border: 'none', background: 'none', color: 'red', cursor: 'pointer', marginLeft: "5px"}}>x</button>
                  </li>
                ))}
              </ul>
            </div>
            <div style={{ flex: 1 }}>
              <strong>Exclude:</strong>
              <ul style={{ paddingLeft: "20px", marginTop: "5px" }}>
                {targeting?.geo_exclude.map((g, idx) => (
                  <li key={idx}>
                    {g.country}{g.state ? `, ${g.state}` : ""}{g.city ? `, ${g.city}` : ""}{g.pincode ? ` (${g.pincode})` : ""}
                    <button type="button" onClick={() => removeGeo("exclude", idx)} style={{border: 'none', background: 'none', color: 'red', cursor: 'pointer', marginLeft: "5px"}}>x</button>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </div>
      
      <button className="btn" onClick={save} disabled={loading}>Save Targeting</button>
    </div>
  );
}
