import type { AdListResponse, AdRequestPayload, Campaign, Creative, HealthResponse, Session } from "../types";
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
  if (!res.ok) { let message = `Request failed (${res.status})`; try { const body = await res.json() as { message?: string; detail?: string }; if (body.message || body.detail) message = body.message ?? body.detail ?? message; } catch { /* non-json error */ } throw new ApiError(message, res.status); }
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
export interface CampaignInput { advertiser_id: number; name: string; description?: string; budget_daily: number; budget_total: number; bid_type: number; bid_amount: number; freq_cap_daily: number; freq_cap_hourly: number; }
export interface CreativeInput { campaign_id: number; title: string; description?: string; image_url?: string; landing_url: string; creative_type: number; width: number; height: number; }
export const listCampaigns = (advertiserId?: number) => apiFetch<Campaign[]>(`/api/v1/campaign${advertiserId ? `?advertiser_id=${advertiserId}` : ""}`);
export const createCampaign = (payload: CampaignInput) => apiFetch<Campaign>("/api/v1/campaign", { method: "POST", body: JSON.stringify(payload) });
export const getCampaign = (id: number) => apiFetch<Campaign>(`/api/v1/campaign/${id}`);
export const updateCampaign = (id: number, payload: Partial<Campaign>) => apiFetch<Campaign>(`/api/v1/campaign/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
export const listCreatives = (campaignId: number) => apiFetch<Creative[]>(`/api/v1/creative?campaign_id=${campaignId}`);
export const createCreative = (payload: CreativeInput) => apiFetch<Creative>("/api/v1/creative", { method: "POST", body: JSON.stringify(payload) });
