import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listPublisherProperties, createPublisherProperty, updatePublisherProperty, PublisherProperty } from "../../api/client";
import { useAuth } from "../../auth/AuthContext";

export default function PublisherProperties() {
  const { session } = useAuth();
  const [properties, setProperties] = useState<PublisherProperty[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [showModal, setShowModal] = useState(false);
  
  const [name, setName] = useState("");
  const [domain, setDomain] = useState("");

  const publisherId = session?.user?.publisher_id;

  const loadProperties = async () => {
    if (!publisherId) return;
    try {
      setLoading(true);
      const data = await listPublisherProperties(publisherId);
      setProperties(data);
      setError("");
    } catch (err: any) {
      setError(err.message || "Failed to load properties");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadProperties();
  }, [publisherId]);

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!publisherId) return;
    try {
      await createPublisherProperty(publisherId, { name, domain });
      setShowModal(false);
      setName("");
      setDomain("");
      loadProperties();
    } catch (err: any) {
      alert(err.message || "Failed to create property");
    }
  };

  const handleToggleStatus = async (propId: number, currentStatus: number) => {
    try {
      await updatePublisherProperty(propId, { status: currentStatus === 1 ? 0 : 1 });
      loadProperties();
    } catch (err: any) {
      alert("Failed to update status");
    }
  };

  if (loading) return <div className="p-8">Loading properties...</div>;

  return (
    <div className="p-8 max-w-7xl mx-auto">
      <div className="flex justify-between items-center mb-8">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">My Properties</h1>
          <p className="text-gray-500 mt-2">Manage your websites and apps where ads will be served.</p>
        </div>
        <button
          onClick={() => setShowModal(true)}
          className="bg-indigo-600 text-white px-6 py-2 rounded-lg hover:bg-indigo-700 transition-colors shadow-sm font-medium"
        >
          Add Property
        </button>
      </div>

      {error && <div className="bg-red-50 text-red-600 p-4 rounded-lg mb-6">{error}</div>}

      <div className="bg-white rounded-xl shadow-sm border border-gray-100 overflow-hidden">
        {properties.length === 0 ? (
          <div className="p-12 text-center text-gray-500">
            No properties found. Add a website or app to get started.
          </div>
        ) : (
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-6 py-4 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Name</th>
                <th className="px-6 py-4 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Domain</th>
                <th className="px-6 py-4 text-left text-xs font-semibold text-gray-600 uppercase tracking-wider">Status</th>
                <th className="px-6 py-4 text-right text-xs font-semibold text-gray-600 uppercase tracking-wider">Actions</th>
              </tr>
            </thead>
            <tbody className="bg-white divide-y divide-gray-200">
              {properties.map((prop) => (
                <tr key={prop.id} className="hover:bg-gray-50 transition-colors">
                  <td className="px-6 py-4 whitespace-nowrap">
                    <div className="font-medium text-gray-900">{prop.name}</div>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-gray-500">
                    {prop.domain}
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap">
                    <span className={`px-3 py-1 inline-flex text-xs leading-5 font-semibold rounded-full ${
                      prop.status === 1 ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                    }`}>
                      {prop.status === 1 ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td className="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                    <button
                      onClick={() => handleToggleStatus(prop.id, prop.status)}
                      className="text-indigo-600 hover:text-indigo-900"
                    >
                      {prop.status === 1 ? "Deactivate" : "Activate"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showModal && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-md w-full p-6">
            <div className="flex justify-between items-center mb-6">
              <h2 className="text-xl font-bold text-gray-900">Add New Property</h2>
              <button onClick={() => setShowModal(false)} className="text-gray-400 hover:text-gray-600">✕</button>
            </div>
            
            <form onSubmit={handleCreate}>
              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Property Name</label>
                  <input
                    type="text"
                    required
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    className="w-full px-4 py-2 rounded-lg border border-gray-300 focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none transition-all"
                    placeholder="e.g. My Tech Blog"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">Domain</label>
                  <input
                    type="text"
                    required
                    value={domain}
                    onChange={(e) => setDomain(e.target.value)}
                    className="w-full px-4 py-2 rounded-lg border border-gray-300 focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none transition-all"
                    placeholder="e.g. example.com"
                  />
                </div>
              </div>
              
              <div className="mt-8 flex justify-end gap-3">
                <button
                  type="button"
                  onClick={() => setShowModal(false)}
                  className="px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg transition-colors font-medium"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="bg-indigo-600 text-white px-6 py-2 rounded-lg hover:bg-indigo-700 transition-colors shadow-sm font-medium"
                >
                  Add Property
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
