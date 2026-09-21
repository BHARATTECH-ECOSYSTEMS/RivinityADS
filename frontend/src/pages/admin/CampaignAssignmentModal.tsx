import { useEffect, useState } from "react";
import {
    listCampaignAssignments,
    createCampaignAssignment,
    deleteCampaignAssignment,
    listPublishers,
    listSlots,
    type CampaignAssignment,
    type SlotData
} from "../../api/client";
import { type Campaign, type Publisher } from "../../types";

interface Props {
    campaign: Campaign | null;
    onClose: () => void;
}

export default function CampaignAssignmentModal({ campaign, onClose }: Props) {
    const [assignments, setAssignments] = useState<CampaignAssignment[]>([]);
    const [publishers, setPublishers] = useState<Publisher[]>([]);
    const [slots, setSlots] = useState<SlotData[]>([]);
    const [loading, setLoading] = useState(true);

    const [selectedPublisher, setSelectedPublisher] = useState<number | "">("");
    const [selectedSlot, setSelectedSlot] = useState<string>("");

    useEffect(() => {
        if (!campaign) return;
        setLoading(true);
        Promise.all([
            listCampaignAssignments(campaign.id),
            listPublishers(),
            listSlots()
        ]).then(([assgn, pubs, slts]) => {
            setAssignments(assgn);
            setPublishers(pubs);
            setSlots(slts);
        }).finally(() => setLoading(false));
    }, [campaign]);

    if (!campaign) return null;

    const handleAdd = () => {
        if (!selectedPublisher) return;
        createCampaignAssignment(campaign.id, {
            campaign_id: campaign.id,
            publisher_id: Number(selectedPublisher),
            slot_id: selectedSlot || null
        }).then(() => {
            setSelectedPublisher("");
            setSelectedSlot("");
            return listCampaignAssignments(campaign.id);
        }).then(setAssignments);
    };

    const handleDelete = (id: number) => {
        deleteCampaignAssignment(campaign.id, id).then(() => {
            setAssignments(prev => prev.filter(a => a.id !== id));
        });
    };

    const publisherSlots = slots.filter(s => selectedPublisher && s.publisher_id === Number(selectedPublisher));

    return (
        <div className="modal-overlay">
            <div className="modal" style={{ maxWidth: "750px", width: "100%" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "1.5rem" }}>
                    <h2 style={{ margin: 0 }}>Assign Campaign: {campaign.name}</h2>
                    <button className="btn secondary small" onClick={onClose} style={{ borderRadius: "4px", padding: "4px 8px", fontWeight: "bold" }}>X</button>
                </div>
                
                {loading ? (
                    <p>Loading...</p>
                ) : (
                    <>
                        <div style={{ marginBottom: "2rem" }}>
                            <h3 style={{ margin: "0 0 1rem 0", fontSize: "16px" }}>Current Assignments</h3>
                            {assignments.length === 0 ? (
                                <p className="text-muted" style={{ color: "#64748b" }}>No explicit publisher assignments.</p>
                            ) : (
                                <div style={{ border: "1px solid #e2e8f0", borderRadius: "8px", overflow: "hidden" }}>
                                    <table className="table" style={{ margin: 0 }}>
                                        <thead style={{ background: "#f8fafc" }}>
                                            <tr>
                                                <th>Publisher</th>
                                                <th>Ad Slot</th>
                                                <th style={{ textAlign: "right" }}>Action</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {assignments.map(a => {
                                                const pub = publishers.find(p => p.id === a.publisher_id);
                                                const slot = slots.find(s => s.slot_id === a.slot_id);
                                                return (
                                                    <tr key={a.id}>
                                                        <td>{pub ? pub.name : `#${a.publisher_id}`}</td>
                                                        <td>{a.slot_id ? (slot ? `${slot.name} (${a.slot_id})` : a.slot_id) : "All Slots"}</td>
                                                        <td style={{ textAlign: "right" }}>
                                                            <button className="btn small" style={{ background: "#ef4444", color: "white", border: "none" }} onClick={() => handleDelete(a.id)}>Remove</button>
                                                        </td>
                                                    </tr>
                                                );
                                            })}
                                        </tbody>
                                    </table>
                                </div>
                            )}
                        </div>

                        <div style={{ borderTop: "1px solid #e2e8f0", paddingTop: "1.5rem" }}>
                            <h3 style={{ margin: "0 0 1rem 0", fontSize: "16px" }}>Add Assignment</h3>
                            <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
                                <div className="field" style={{ margin: 0 }}>
                                    <label>Publisher</label>
                                    <select 
                                        value={selectedPublisher} 
                                        onChange={e => {
                                            setSelectedPublisher(e.target.value ? Number(e.target.value) : "");
                                            setSelectedSlot(""); // reset slot on publisher change
                                        }}
                                    >
                                        <option value="">[ Select Publisher ]</option>
                                        {publishers.map(p => (
                                            <option key={p.id} value={p.id}>{p.name}</option>
                                        ))}
                                    </select>
                                </div>
                                <div className="field" style={{ margin: 0 }}>
                                    <label>Ad Slot</label>
                                    <select 
                                        value={selectedSlot} 
                                        onChange={e => setSelectedSlot(e.target.value)}
                                        disabled={!selectedPublisher}
                                    >
                                        <option value="">[ Select Ad Slot (All Slots) ]</option>
                                        {publisherSlots.map(s => (
                                            <option key={s.slot_id} value={s.slot_id}>{s.name} ({s.slot_id})</option>
                                        ))}
                                    </select>
                                </div>
                                <div style={{ display: "flex", justifyContent: "flex-end", gap: "12px", marginTop: "1rem" }}>
                                    <button className="btn secondary" onClick={onClose}>Cancel</button>
                                    <button className="btn primary" onClick={handleAdd} disabled={!selectedPublisher}>Add Assignment</button>
                                </div>
                            </div>
                        </div>
                    </>
                )}
            </div>
            
            <style>{`
                .modal-overlay {
                    position: fixed;
                    top: 0; left: 0; right: 0; bottom: 0;
                    background: rgba(15, 23, 42, 0.75);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    z-index: 1000;
                    padding: 1rem;
                }
                .modal {
                    background: #ffffff;
                    border-radius: 12px;
                    box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
                    max-height: 85vh;
                    overflow-y: auto;
                    padding: 24px;
                    position: relative;
                    z-index: 1001;
                }
            `}</style>
        </div>
    );
}
