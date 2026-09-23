import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  listSlots,
  createSlot,
  updateSlot,
  SlotData,
  listPublisherProperties,
  PublisherProperty,
  generateSlotTag
} from "../../api/client";
import { useAuth } from "../../auth/AuthContext";

export default function PublisherAdUnits() {
  const { session } = useAuth();
  const [slots, setSlots] = useState<SlotData[]>([]);
  const [properties, setProperties] = useState<PublisherProperty[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showTagModal, setShowTagModal] = useState(false);
  const [currentTag, setCurrentTag] = useState("");
  
  const [name, setName] = useState("");
  const [slotId, setSlotId] = useState("");
  const [format, setFormat] = useState(1);
  const [width, setWidth] = useState(300);
  const [height, setHeight] = useState(250);
  const [floorPrice, setFloorPrice] = useState(0.0);
  const [propertyId, setPropertyId] = useState<number | "">("");

  const publisherId = session?.user?.publisher_id;

  const loadData = async () => {
    if (!publisherId) return;
    try {
      setLoading(true);
      const [slotsData, propsData] = await Promise.all([
        listSlots(publisherId),
        listPublisherProperties(publisherId)
      ]);
      setSlots(slotsData);
      setProperties(propsData);
      setError("");
    } catch (err: any) {
      setError(err.message || "Failed to load ad units");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [publisherId]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await createSlot({
        slot_id: slotId,
        name,
        format,
        width,
        height,
        floor_price: floorPrice,
        property_id: propertyId === "" ? null : propertyId
      });
      setShowCreateModal(false);
      setName("");
      setSlotId("");
      setFloorPrice(0);
      setPropertyId("");
      loadData();
    } catch (err: any) {
      alert(err.message || "Failed to create ad unit");
    }
  };

  const handleGetTag = async (id: number) => {
    try {
      const res = await generateSlotTag(id);
      setCurrentTag(res.html);
      setShowTagModal(true);
    } catch (err: any) {
      alert("Failed to generate tag");
    }
  };

  const copyTag = () => {
    navigator.clipboard.writeText(currentTag);
    alert("Copied to clipboard!");
  };

  if (loading) return <div className="p-8">Loading ad units...</div>;

  return (
    <div className="p-8 max-w-7xl mx-auto">
      <div className="flex justify-between items-center mb-8">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Ad Units</h1>
          <p className="text-gray-500 mt-2">Manage the ad placements on your properties.</p>
        </div>
        <button
          onClick={() => setShowCreateModal(true)}
          className="bg-indigo-600 text-white px-6 py-2 rounded-lg hover:bg-indigo-700 transition-colors shadow-sm font-medium"
        >
          New Ad Unit
        </button>
      </div>

      {error && <div className="bg-red-50 text-red-600 p-4 rounded-lg mb-6">{error}</div>}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {slots.length === 0 ? (
          <div className="col-span-full p-12 text-center text-gray-500 bg-white rounded-xl shadow-sm border border-gray-100">
            No ad units found. Create one to get your embed tags.
          </div>
        ) : (
          slots.map((slot) => {
            const prop = properties.find(p => p.id === slot.property_id);
            return (
              <div key={slot.id} className="bg-white rounded-xl shadow-sm border border-gray-100 p-6 flex flex-col">
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h3 className="text-lg font-bold text-gray-900">{slot.name}</h3>
                    <span className="text-xs text-gray-500 font-mono">{slot.slot_id}</span>
                  </div>
                  <span className={`px-2 py-1 text-xs font-semibold rounded-full ${slot.status === 1 ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
                    {slot.status === 1 ? 'Active' : 'Inactive'}
                  </span>
                </div>
                
                <div className="space-y-2 mb-6 flex-grow text-sm">
                  <div className="flex justify-between text-gray-600">
                    <span>Website:</span>
                    <span className="font-medium text-gray-900">{prop ? prop.name : "None"}</span>
                  </div>
                  <div className="flex justify-between text-gray-600">
                    <span>Size:</span>
                    <span className="font-medium text-gray-900">{slot.width}x{slot.height}</span>
                  </div>
                  <div className="flex justify-between text-gray-600">
                    <span>Floor Price:</span>
                    <span className="font-medium text-gray-900">${Number(slot.floor_price || 0).toFixed(2)}</span>
                  </div>
                </div>

                <div className="mt-auto pt-4 border-t border-gray-100 flex justify-end">
                  <button 
                    onClick={() => handleGetTag(slot.id)}
                    className="text-indigo-600 font-medium hover:text-indigo-800 transition-colors text-sm"
                  >
                    Get Code
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>

      {showCreateModal && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50 p-4 overflow-y-auto">
          <div className="bg-white rounded-xl shadow-xl max-w-lg w-full p-6 my-8">
            <div className="flex justify-between items-center mb-6">
              <h2 className="text-xl font-bold text-gray-900">Create Ad Unit</h2>
              <button onClick={() => setShowCreateModal(false)} className="text-gray-400 hover:text-gray-600">✕</button>
            </div>
            
            <form onSubmit={handleCreate}>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
                  <input type="text" required value={name} onChange={(e) => setName(e.target.value)} className="w-full px-4 py-2 rounded-lg border border-gray-300 focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none" placeholder="Sidebar Top" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Slot ID (Unique string)</label>
                  <input type="text" required value={slotId} onChange={(e) => setSlotId(e.target.value)} className="w-full px-4 py-2 rounded-lg border border-gray-300 focus:ring-2 focus:ring-indigo-500 font-mono text-sm" placeholder="sidebar_300_250" />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Property (Website)</label>
                  <select value={propertyId} onChange={(e) => setPropertyId(e.target.value === "" ? "" : Number(e.target.value))} className="w-full px-4 py-2 rounded-lg border border-gray-300 focus:ring-2 focus:ring-indigo-500 bg-white">
                    <option value="">None (Standalone)</option>
                    {properties.map(p => (
                      <option key={p.id} value={p.id}>{p.name} ({p.domain})</option>
                    ))}
                  </select>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Width (px)</label>
                    <input type="number" required value={width} onChange={(e) => setWidth(Number(e.target.value))} className="w-full px-4 py-2 rounded-lg border border-gray-300 focus:ring-2 focus:ring-indigo-500" />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1">Height (px)</label>
                    <input type="number" required value={height} onChange={(e) => setHeight(Number(e.target.value))} className="w-full px-4 py-2 rounded-lg border border-gray-300 focus:ring-2 focus:ring-indigo-500" />
                  </div>
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Floor Price (eCPM in $)</label>
                  <input type="number" step="0.01" required value={floorPrice} onChange={(e) => setFloorPrice(Number(e.target.value))} className="w-full px-4 py-2 rounded-lg border border-gray-300 focus:ring-2 focus:ring-indigo-500" placeholder="1.50" />
                </div>
              </div>
              
              <div className="mt-8 flex justify-end gap-3">
                <button type="button" onClick={() => setShowCreateModal(false)} className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg font-medium">Cancel</button>
                <button type="submit" className="bg-indigo-600 text-white px-6 py-2 rounded-lg hover:bg-indigo-700 shadow-sm font-medium">Create Unit</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {showTagModal && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-2xl w-full p-6">
            <div className="flex justify-between items-center mb-6">
              <h2 className="text-xl font-bold text-gray-900">Integration Code</h2>
              <button onClick={() => setShowTagModal(false)} className="text-gray-400 hover:text-gray-600">✕</button>
            </div>
            <p className="text-sm text-gray-600 mb-4">Copy and paste this HTML snippet into your website exactly where you want the ad to appear.</p>
            <div className="bg-gray-900 rounded-lg p-4 relative">
              <pre className="text-gray-300 text-sm overflow-x-auto whitespace-pre-wrap font-mono">
                {currentTag}
              </pre>
            </div>
            <div className="mt-6 flex justify-end gap-3">
              <button onClick={() => setShowTagModal(false)} className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg font-medium">Close</button>
              <button onClick={copyTag} className="bg-indigo-600 text-white px-6 py-2 rounded-lg hover:bg-indigo-700 shadow-sm font-medium">Copy Code</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
