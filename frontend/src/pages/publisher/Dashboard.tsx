import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { getPublisherLedger, requestPayout, getPublisherReporting, listPublisherAssignments, type LedgerResponse, type PublisherReporting, type PublisherAssignment } from "../../api/client";
import StatCard from "../../components/StatCard";
import { money, dateString, num } from "../../lib/format";

export default function PublisherDashboard() {
  const { session } = useAuth();
  
  const [reporting, setReporting] = useState<PublisherReporting | null>(null);
  const [ledger, setLedger] = useState<LedgerResponse | null>(null);
  const [assignments, setAssignments] = useState<PublisherAssignment[]>([]);
  const [payoutAmount, setPayoutAmount] = useState<number | "">("");

  const publisherId = session?.user.publisher_id;

  const reload = async () => {
    if (!publisherId) return;
    
    try {
      const today = new Date().toISOString().split('T')[0];
      const thirtyDaysAgo = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString().split('T')[0];
      
      const [reportData, ledgerData, assignData] = await Promise.all([
        getPublisherReporting(publisherId, thirtyDaysAgo, today),
        getPublisherLedger(publisherId),
        listPublisherAssignments()
      ]);
      
      setReporting(reportData);
      setLedger(ledgerData);
      setAssignments(assignData);
    } catch (err) {
      console.error("Failed to load dashboard data", err);
    }
  };

  useEffect(() => { void reload(); }, [publisherId]);

  return (
    <div className="p-8 max-w-7xl mx-auto">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900">Dashboard</h1>
        <p className="text-gray-500 mt-2">Welcome back! Here's how your inventory is performing over the last 30 days.</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
        <StatCard title="Impressions" value={reporting ? num(reporting.impressions) : "0"} />
        <StatCard title="Clicks" value={reporting ? num(reporting.clicks) : "0"} />
        <StatCard title="Average eCPM" value={reporting ? money(reporting.ecpm) : "$0.00"} />
        <StatCard title="Period Earnings" value={reporting ? money(reporting.earnings) : "$0.00"} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 mb-8">
        {/* Earnings & Payouts */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
          <div className="flex justify-between items-center mb-6">
            <h2 className="text-xl font-bold text-gray-900">Earnings & Payouts</h2>
            <div className="text-2xl font-bold text-green-600">
              {ledger ? money(ledger.balance) : "$0.00"}
            </div>
          </div>
          
          <div className="flex gap-4 mb-8">
            <input 
              type="number" 
              placeholder="Payout Amount ($)" 
              value={payoutAmount} 
              onChange={e => setPayoutAmount(Number(e.target.value) || "")} 
              min={1} 
              className="flex-1 px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 outline-none"
            />
            <button 
              className="bg-indigo-600 text-white px-6 py-2 rounded-lg font-medium hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
              onClick={async () => {
                  if (!payoutAmount || payoutAmount <= 0) return;
                  if (!publisherId) return;
                  try {
                      await requestPayout(publisherId, Number(payoutAmount), `demo_out_${Date.now()}`);
                      setPayoutAmount("");
                      reload();
                      alert("Payout requested successfully!");
                  } catch (err: any) {
                      alert("Payout failed: " + (err.message || "Unknown error"));
                  }
              }} 
              disabled={!payoutAmount || !!(ledger && Number(payoutAmount) > ledger.balance)}
            >
              Request Payout
            </button>
          </div>
          
          <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wider mb-4">Recent Transactions</h3>
          {(!ledger || ledger.transactions.length === 0) && <p className="text-gray-500 text-sm">No transactions yet.</p>}
          <div className="space-y-4">
            {ledger?.transactions.slice(0, 5).map(tx => (
                <div key={tx.id} className="flex justify-between items-center pb-4 border-b border-gray-50 last:border-0">
                    <div>
                        <div className="font-medium text-gray-900">{tx.type === 3 ? "Payout" : "Earnings"}</div>
                        <div className="text-sm text-gray-500">{dateString(tx.created_at)}</div>
                    </div>
                    <div className={`font-bold ${tx.amount > 0 ? "text-green-600" : "text-gray-900"}`}>
                        {tx.amount > 0 ? "+" : ""}{money(tx.amount)}
                    </div>
                </div>
            ))}
          </div>
        </div>

        {/* Assigned Ads */}
        <div className="bg-white rounded-xl shadow-sm border border-gray-100 p-6">
          <h2 className="text-xl font-bold text-gray-900 mb-6">Direct Assignments</h2>
          {assignments.length === 0 ? (
            <p className="text-gray-500">No campaigns are explicitly assigned to your slots.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase">Campaign</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-600 uppercase">Slot</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold text-gray-600 uppercase">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {assignments.map((a) => (
                    <tr key={a.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3">
                        <div className="font-medium text-gray-900">{a.campaign_name}</div>
                        <div className="text-xs text-gray-500">{a.advertiser_name}</div>
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-500">
                        {a.slot_name ? a.slot_name : "All slots"}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <Link
                          to={`/publisher/preview${a.slot_id ? `?slot_id=${encodeURIComponent(a.slot_id)}` : ""}`}
                          className="text-indigo-600 font-medium hover:text-indigo-800 text-sm"
                        >
                          Preview
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
