import type { AdListResponse, AdRequestPayload, Advertiser, Campaign, Creative, HealthResponse, Publisher, Session } from "../types";
const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";
export class ApiError extends Error { constructor(message: string, public status: number) { super(message); this.name = "ApiError"; } }
async function apiFetch<T>(path: string, options: RequestInit = {}): Promise<T> {
  let authorization: Record<string, string> = {};
  try {
    const stored = localStorage.getItem("oas_session");
    const token = stored ? (JSON.parse(stored) as Session).token : undefined;
    if (token) authorization = { Authorization: `Bearer ${token}` };
  } catch { /* unauthenticated request */ }
  const res = await fetch(`${BASE_URL}${path}`, { headers: { "Content-Type": "application/json", ...authorization, ...(options.headers ?? {}) }, ...options });
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
  return await res.json() as T;
}
export const getHealth = () => apiFetch<HealthResponse>("/health");
export const login = async (email: string, password: string): Promise<Session> => {
  const response = await apiFetch<{ user: Session["user"]; access_token: string; refresh_token: string }>("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
  return { user: response.user, token: response.access_token, refreshToken: response.refresh_token };
};
export const register = async (name: string, email: string, password: string, role: "advertiser" | "publisher"): Promise<Session> => {
  const response = await apiFetch<{ user: Session["user"]; access_token: string; refresh_token: string }>("/api/v1/auth/register", { method: "POST", body: JSON.stringify({ name, email, password, role }) });
  return { user: response.user, token: response.access_token, refreshToken: response.refresh_token };
};
export const logout = (refreshToken: string) => apiFetch<{ message: string }>("/api/v1/auth/logout", { method: "POST", body: JSON.stringify({ refresh_token: refreshToken }) });
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
export const listCreatives = (campaignId: number) => apiFetch<Creative[]>(`/api/v1/creative?campaign_id=${campaignId}`);
export const createCreative = (payload: CreativeInput) => apiFetch<Creative>("/api/v1/creative", { method: "POST", body: JSON.stringify(payload) });
export interface SlotData { id: number; publisher_id: number; slot_id: string; name: string; format: number; width: number; height: number; status: number; }
export interface SlotInput { slot_id: string; name: string; format: number; width: number; height: number; }
export interface PublisherInput { name: string; email?: string; site_url?: string; }
export const listSlots = (publisherId?: number) => apiFetch<SlotData[]>(`/api/v1/slot${publisherId ? `?publisher_id=${publisherId}` : ""}`);
export const createSlot = (payload: SlotInput) => apiFetch<SlotData>("/api/v1/slot", { method: "POST", body: JSON.stringify(payload) });
export const listPublishers = () => apiFetch<Publisher[]>("/api/v1/publisher");
export const createPublisher = (payload: PublisherInput) => apiFetch<Publisher>("/api/v1/publisher", { method: "POST", body: JSON.stringify(payload) });
export const updatePublisher = (id: number, payload: Partial<Publisher>) => apiFetch<Publisher>(`/api/v1/publisher/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
export const setPublisherStatus = (id: number, status: number) => updatePublisher(id, { status });

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

