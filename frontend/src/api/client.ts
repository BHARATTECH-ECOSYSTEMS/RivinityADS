import type { AdListResponse, AdRequestPayload, Advertiser, Campaign, Creative, HealthResponse, Publisher, Session, Suggestion, ChatSessionResponse, ChatResponse } from "../types";
const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";
let memoryToken: string | null = null;
export const setMemoryToken = (token: string | null) => { memoryToken = token; };
export class ApiError extends Error { constructor(message: string, public status: number) { super(message); this.name = "ApiError"; } }
async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  let authorization: Record<string, string> = {};
  if (memoryToken) authorization = { Authorization: `Bearer ${memoryToken}` };
  const res = await fetch(`${BASE_URL}${path}`, { headers: { "Content-Type": "application/json", ...authorization, ...(options.headers ?? {}) }, credentials: "include", ...options });
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json() as { message?: string; detail?: unknown };
      if (typeof body.detail === "string") {
        message = body.detail;
      } else if (Array.isArray(body.detail) && body.detail.length > 0) {
        message = body.detail.map((d: any) => d.msg || JSON.stringify(d)).join(", ");
      } else if (body.message) {
        message = body.message;
      }
    } catch { /* non-json error */ }
    throw new ApiError(message, res.status);
  }
  if (res.status === 204) return undefined as T;
  return await res.json() as T;
}
export const getHealth = () => apiFetch<HealthResponse>("/health");
export const login = async (email: string, password: string): Promise<Session> => {
  const response = await apiFetch<{ user: Session["user"]; access_token: string }>("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
  return { user: response.user, token: response.access_token };
};
export const register = async (name: string, email: string, password: string, role: "advertiser" | "publisher"): Promise<Session> => {
  const response = await apiFetch<{ user: Session["user"]; access_token: string }>("/api/v1/auth/register", { method: "POST", body: JSON.stringify({ name, email, password, role }) });
  return { user: response.user, token: response.access_token };
};
export interface AdvertiserDomain {
  id: number;
  advertiser_id: number;
  domain: string;
  status: "PENDING" | "VERIFIED" | "FAILED";
  verification_method: "DNS_TXT";
  verification_token: string;
  verified_at: string | null;
  last_checked_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
}
export interface DomainVerificationInstructions {
  domain: string;
  record_type: string;
  record_name: string;
  record_value: string;
  instructions: string;
}
export const listAdvertiserDomains = () => apiFetch<AdvertiserDomain[]>("/api/v1/domain");
export const registerAdvertiserDomain = (domain: string) => apiFetch<AdvertiserDomain>("/api/v1/domain", { method: "POST", body: JSON.stringify({ domain }) });
export const getDomainInstructions = (domainId: number) => apiFetch<DomainVerificationInstructions>(`/api/v1/domain/${domainId}/instructions`);
export const verifyAdvertiserDomain = (domainId: number) => apiFetch<AdvertiserDomain>(`/api/v1/domain/${domainId}/verify`, { method: "POST" });
export const regenerateDomainChallenge = (domainId: number) => apiFetch<AdvertiserDomain>(`/api/v1/domain/${domainId}/regenerate-challenge`, { method: "POST" });
export const deleteAdvertiserDomain = (domainId: number) => apiFetch<void>(`/api/v1/domain/${domainId}`, { method: "DELETE" });
export interface ModerationCreative extends Creative {
  moderation_status: 1 | 2 | 3;
  moderation_reason: string | null;
  moderated_at: string | null;
  moderated_by_id: number | null;
  created_at: string;
  updated_at: string;
}
export const listPendingCreatives = () => apiFetch<ModerationCreative[]>("/api/v1/moderation/creatives/pending");
export const approveCreative = (creativeId: number) => apiFetch<ModerationCreative>(`/api/v1/moderation/creatives/${creativeId}/approve`, { method: "POST" });
export const rejectCreative = (creativeId: number, reason: string) => apiFetch<ModerationCreative>(`/api/v1/moderation/creatives/${creativeId}/reject`, { method: "POST", body: JSON.stringify({ reason }) });
export const reopenCreative = (creativeId: number) => apiFetch<ModerationCreative>(`/api/v1/moderation/creatives/${creativeId}/reopen`, { method: "POST" });
export const refreshSession = async (): Promise<Session> => {
  const response = await apiFetch<{ user: Session["user"]; access_token: string }>("/api/v1/auth/refresh", { method: "POST" });
  return { user: response.user, token: response.access_token };
};
export const logout = () => apiFetch<{ message: string }>("/api/v1/auth/logout", { method: "POST" });
export const ping = () => apiFetch<{ pong: boolean }>("/ping");
export const requestAds = (payload: AdRequestPayload) => apiFetch<AdListResponse>("/api/v1/ad/request", { method: "POST", body: JSON.stringify(payload) });
export const trackEvent = (payload: { request_id: string; ad_id: string; event_type: string }) => apiFetch<{ success: boolean }>("/api/v1/event/track", { method: "POST", body: JSON.stringify(payload) });
export interface AdvertiserInput { name: string; company?: string; contact_email?: string; balance?: number; daily_budget?: number; }
export const listAdvertisers = () => apiFetch<Advertiser[]>("/api/v1/advertiser");
export const createAdvertiser = (payload: AdvertiserInput) => apiFetch<Advertiser>("/api/v1/advertiser", { method: "POST", body: JSON.stringify(payload) });
export const getAdvertiser = (id: number) => apiFetch<Advertiser>(`/api/v1/advertiser/${id}`);
export const updateAdvertiser = (id: number, payload: Partial<Advertiser>) => apiFetch<Advertiser>(`/api/v1/advertiser/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
export const setAdvertiserStatus = (id: number, status: number) => updateAdvertiser(id, { status });
export interface CampaignInput { advertiser_id: number; name: string; description?: string; budget_daily: number; budget_total: number; bid_type: number; bid_amount: number; freq_cap_daily: number; freq_cap_hourly: number; }
export interface CreativeInput { campaign_id: number; title: string; description?: string; image_url?: string; landing_url: string; creative_type: number; width: number; height: number; }
export const listCampaigns = (advertiserId?: number) => apiFetch<Campaign[]>(`/api/v1/campaign${advertiserId ? `?advertiser_id=${advertiserId}` : ""}`);
export const createCampaign = (payload: CampaignInput) => apiFetch<Campaign>("/api/v1/campaign", { method: "POST", body: JSON.stringify(payload) });
export const getCampaign = (id: number) => apiFetch<Campaign>(`/api/v1/campaign/${id}`);
export const updateCampaign = (id: number, payload: Partial<Campaign>) => apiFetch<Campaign>(`/api/v1/campaign/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
export const setCampaignStatus = (id: number, status: number) => updateCampaign(id, { status });
export const activateCampaign = (id: number) => apiFetch<Campaign>(`/api/v1/campaign/${id}/activate`, { method: "POST" });
export const listCreatives = (campaignId: number) => apiFetch<Creative[]>(`/api/v1/creative?campaign_id=${campaignId}`);
export const createCreative = (payload: CreativeInput) => apiFetch<Creative>("/api/v1/creative", { method: "POST", body: JSON.stringify(payload) });
export interface SlotData { id: number; publisher_id: number; property_id: number | null; slot_id: string; name: string; format: number; width: number; height: number; status: number; floor_price: number; }
export interface SlotInput { slot_id: string; name: string; format: number; width: number; height: number; property_id?: number | null; floor_price?: number; }
export interface PublisherInput { name: string; email?: string; site_url?: string; }
export const listSlots = (publisherId?: number) => apiFetch<SlotData[]>(`/api/v1/slot${publisherId ? `?publisher_id=${publisherId}` : ""}`);
export const createSlot = (payload: SlotInput) => apiFetch<SlotData>("/api/v1/slot", { method: "POST", body: JSON.stringify(payload) });
export const updateSlot = (id: number, payload: Partial<SlotInput>) => apiFetch<SlotData>(`/api/v1/slot/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
export const listPublishers = () => apiFetch<Publisher[]>("/api/v1/publisher");
export const createPublisher = (payload: PublisherInput) => apiFetch<Publisher>("/api/v1/publisher", { method: "POST", body: JSON.stringify(payload) });
export const updatePublisher = (id: number, payload: Partial<Publisher>) => apiFetch<Publisher>(`/api/v1/publisher/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
export const setPublisherStatus = (id: number, status: number) => updatePublisher(id, { status });

// Publisher Properties
export interface PublisherProperty { id: number; publisher_id: number; name: string; domain: string; status: number; }
export interface PublisherPropertyInput { name: string; domain: string; status?: number; }
export const listPublisherProperties = (publisherId: number) => apiFetch<PublisherProperty[]>(`/api/v1/publisher/${publisherId}/property`);
export const createPublisherProperty = (publisherId: number, payload: PublisherPropertyInput) => apiFetch<PublisherProperty>(`/api/v1/publisher/${publisherId}/property`, { method: "POST", body: JSON.stringify(payload) });
export const updatePublisherProperty = (propertyId: number, payload: Partial<PublisherPropertyInput>) => apiFetch<PublisherProperty>(`/api/v1/publisher/property/${propertyId}`, { method: "PATCH", body: JSON.stringify(payload) });

// Publisher Ad Units (Tags)
export const generateSlotTag = (slotId: number) => apiFetch<{html: string}>(`/api/v1/slot/${slotId}/tag`);

// Publisher Reporting
export interface DailyStat { stat_date: string; impressions: number; clicks: number; conversions: number; spend?: number; earnings?: number; }
export interface PublisherReporting { publisher_id: number; start_date: string; end_date: string; impressions: number; clicks: number; conversions: number; earnings: number; ecpm: number; daily_breakdown: DailyStat[]; }
export const getPublisherReporting = (publisherId: number, startDate?: string, endDate?: string) => {
    const params = new URLSearchParams();
    if (startDate) params.append("start_date", startDate);
    if (endDate) params.append("end_date", endDate);
    const qs = params.toString();
    return apiFetch<PublisherReporting>(`/api/v1/publisher/${publisherId}/reporting${qs ? `?${qs}` : ""}`);
};

// Advertiser Reporting
export interface AdvertiserReporting { advertiser_id: number; campaign_id?: number; start_date: string; end_date: string; impressions: number; clicks: number; conversions: number; spend: number; ctr: number; cvr: number; daily_breakdown: DailyStat[]; }
export const getAdvertiserReporting = (advertiserId: number, startDate?: string, endDate?: string, campaignId?: number) => {
    const params = new URLSearchParams();
    if (startDate) params.append("start_date", startDate);
    if (endDate) params.append("end_date", endDate);
    if (campaignId) params.append("campaign_id", campaignId.toString());
    const qs = params.toString();
    return apiFetch<AdvertiserReporting>(`/api/v1/advertiser/${advertiserId}/reporting${qs ? `?${qs}` : ""}`);
};

// Campaign Assignments
export interface CampaignAssignment { id: number; campaign_id: number; publisher_id: number; slot_id: string | null; }
export interface CampaignAssignmentInput { campaign_id: number; publisher_id: number; slot_id?: string | null; }
export const listCampaignAssignments = (campaignId: number) => apiFetch<CampaignAssignment[]>(`/api/v1/campaign/${campaignId}/assignments`);
export const createCampaignAssignment = (campaignId: number, payload: CampaignAssignmentInput) => apiFetch<CampaignAssignment>(`/api/v1/campaign/${campaignId}/assignments`, { method: "POST", body: JSON.stringify(payload) });
export const deleteCampaignAssignment = (campaignId: number, assignmentId: number) => apiFetch<{success: boolean}>(`/api/v1/campaign/${campaignId}/assignments/${assignmentId}`, { method: "DELETE" });

export interface PublisherAssignment { id: number; campaign_id: number; campaign_name: string; advertiser_name: string; slot_id: string | null; slot_name: string | null; status: string; }
export const listPublisherAssignments = () => apiFetch<PublisherAssignment[]>("/api/v1/publisher/assignments");
// Targeting and References
export interface GeoEntry { country: string; state?: string | null; city?: string | null; pincode?: string | null; }
export interface TargetingSet { geo_include: GeoEntry[]; geo_exclude: GeoEntry[]; sector_include: string[]; sector_exclude: string[]; }
export interface TargetingResponse extends TargetingSet { campaign_id: number; }
export const getCampaignTargeting = (campaignId: number) => apiFetch<TargetingResponse>(`/api/v1/campaign/${campaignId}/targeting`);
export const updateCampaignTargeting = (campaignId: number, payload: TargetingSet) => apiFetch<TargetingResponse>(`/api/v1/campaign/${campaignId}/targeting`, { method: "PUT", body: JSON.stringify(payload) });
export const getReferenceSectors = () => apiFetch<string[]>("/api/v1/reference/sectors");
export const getReferenceCountries = () => apiFetch<string[]>("/api/v1/reference/locations/countries");
export const getReferenceStates = (country: string) => apiFetch<{code: string; name: string}[]>(`/api/v1/reference/locations/states?country=${country}`);
export const getReferenceCities = (country: string, state: string) => apiFetch<string[]>(`/api/v1/reference/locations/cities?country=${country}&state=${state}`);
export const getReferencePincodes = (country: string, state: string, city: string) => apiFetch<string[]>(`/api/v1/reference/locations/pincodes?country=${country}&state=${state}&city=${city}`);

// Finance APIs
export interface TransactionResponse { id: number; type: number; amount: number; currency: string; advertiser_id?: number | null; publisher_id?: number | null; campaign_id?: number | null; reference_id?: string | null; description?: string | null; created_at: string; }
export interface LedgerResponse { balance: number; transactions: TransactionResponse[]; }
export const getAdvertiserLedger = (advertiserId: number) => apiFetch<LedgerResponse>(`/api/v1/finance/advertiser/${advertiserId}/ledger`);
export const depositFunds = (advertiserId: number, amount: number, referenceId?: string) => apiFetch<TransactionResponse>(`/api/v1/finance/advertiser/${advertiserId}/deposit`, { method: "POST", body: JSON.stringify({ amount, reference_id: referenceId }) });
export const getPublisherLedger = (publisherId: number) => apiFetch<LedgerResponse>(`/api/v1/finance/publisher/${publisherId}/ledger`);
export const requestPayout = (publisherId: number, amount: number, referenceId?: string) => apiFetch<TransactionResponse>(`/api/v1/finance/publisher/${publisherId}/payout`, { method: "POST", body: JSON.stringify({ amount, reference_id: referenceId }) });
// API Keys
export interface ApiKey { id: number; publisher_id: number; name: string; key_prefix: string; status: number; created_at: string; revoked_at: string | null; }
export interface ApiKeyCreateResponse extends ApiKey { raw_key: string; }
export const listPublisherApiKeys = (publisherId: number) => apiFetch<ApiKey[]>(`/api/v1/publisher/${publisherId}/keys`);
export const createPublisherApiKey = (publisherId: number, name: string) => apiFetch<ApiKeyCreateResponse>(`/api/v1/publisher/keys`, { method: "POST", body: JSON.stringify({ publisher_id: publisherId, name, label: name }) });
export const revokePublisherApiKey = (publisherId: number, keyId: number) => apiFetch<void>(`/api/v1/publisher/${publisherId}/keys/${keyId}`, { method: "DELETE" });

// Webhooks
export interface Webhook { id: number; owner_type: string; owner_id: number; url: string; events: string[]; is_active: boolean; created_at: string; updated_at: string; }
export interface WebhookCreateResponse extends Webhook { secret: string; }
export interface WebhookInput { url: string; events: string[]; is_active?: boolean; }
export const listWebhooks = () => apiFetch<Webhook[]>("/api/v1/webhooks");
export const createWebhook = (payload: WebhookInput) => apiFetch<WebhookCreateResponse>("/api/v1/webhooks", { method: "POST", body: JSON.stringify(payload) });
export const updateWebhook = (id: number, payload: Partial<WebhookInput>) => apiFetch<Webhook>(`/api/v1/webhooks/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
export const deleteWebhook = (id: number) => apiFetch<void>(`/api/v1/webhooks/${id}`, { method: "DELETE" });
export const testWebhook = (id: number, payload?: any) => apiFetch<{success: boolean; status_code?: number; response?: string}>(`/api/v1/webhooks/${id}/test`, { method: "POST", body: payload ? JSON.stringify(payload) : undefined });


// Suggestions
export const getCampaignSuggestions = (campaignId: number) => apiFetch<Suggestion[]>(`/api/v1/campaign/${campaignId}/suggestions`);
export const updateSuggestionStatus = (suggestionId: number, status: string) => apiFetch<Suggestion>(`/api/v1/suggestion/${suggestionId}`, { method: "PATCH", body: JSON.stringify({ status }) });

// Chat API
export const createChatSession = () => apiFetch<ChatSessionResponse>("/api/v1/chat/session", { method: "POST" });
export const sendChatMessage = (sessionId: string, message: string) => {
    if (!sessionId || !sessionId.trim()) throw new Error("session_id is required");
    if (!message || !message.trim()) throw new Error("message is required");
    return apiFetch<ChatResponse>("/api/v1/chat", {
        method: "POST",
        body: JSON.stringify({ session_id: sessionId, message })
    });
};
