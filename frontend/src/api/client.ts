import type {
  AdListResponse,
  AdRequestPayload,
  UserContextPayload,
  Advertiser,
  Campaign,
  Creative,
  HealthResponse,
  Publisher,
  Session,
  Suggestion,
  ChatSessionResponse,
  ChatResponse,
  MessageResponse,
  VerifyEmailResponse,
  VerifyEmailInput,
  ResendVerificationInput,
  RequestPasswordResetInput,
  ResetPasswordInput,
  NotificationItem,
  NotificationResponse,
  NotificationListResponse,
  UnreadCountResponse,
  MarkAllReadResponse,
  NotificationPreferenceItem,
  NotificationPreferenceUpdateRequest,
  NotificationPreferenceListResponse,
  AdSet,
  AdSetCreateInput,
  AdSetUpdateInput,
  AdSetTargetingResponse,
  AdSetReportingResponse,
  ThemeConfig,
  BlockType,
  BlockItem,
  SeoMeta,
  PageSummary,
  PageData,
  ProjectSummary,
  ProjectData,
  VersionSummary,
  PreviewTokenResponse,
  SlugCheckResponse,
} from "../types";

export type {
  UserContextPayload,
  MessageResponse,
  VerifyEmailResponse,
  VerifyEmailInput,
  ResendVerificationInput,
  RequestPasswordResetInput,
  ResetPasswordInput,
};
const BASE_URL = (import.meta.env?.VITE_API_BASE_URL as string | undefined) ?? "";
let memoryToken: string | null = null;
export const setMemoryToken = (token: string | null) => { memoryToken = token; };
export const getMemoryToken = () => memoryToken;
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}
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
export const verifyEmail = (payload: string | VerifyEmailInput): Promise<VerifyEmailResponse> => {
  const token = typeof payload === "string" ? payload : payload.token;
  return apiFetch<VerifyEmailResponse>("/api/v1/auth/verify-email", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
};
export const resendVerification = (payload: string | ResendVerificationInput): Promise<MessageResponse> => {
  const email = typeof payload === "string" ? payload : payload.email;
  return apiFetch<MessageResponse>("/api/v1/auth/resend-verification", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
};
export const requestPasswordReset = (payload: string | RequestPasswordResetInput): Promise<MessageResponse> => {
  const email = typeof payload === "string" ? payload : payload.email;
  return apiFetch<MessageResponse>("/api/v1/auth/request-password-reset", {
    method: "POST",
    body: JSON.stringify({ email }),
  });
};
export const resetPassword = (payload: ResetPasswordInput): Promise<MessageResponse> => {
  return apiFetch<MessageResponse>("/api/v1/auth/reset-password", {
    method: "POST",
    body: JSON.stringify(payload),
  });
};
export const getCurrentUser = (): Promise<Session["user"]> => {
  return apiFetch<Session["user"]>("/api/v1/auth/me", { method: "POST" });
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
export interface CreativeInput { campaign_id: number; ad_set_id?: number | null; title: string; description?: string; image_url?: string; video_url?: string; landing_url: string; creative_type: number; width: number; height: number; }
export const listCampaigns = (advertiserId?: number) => apiFetch<Campaign[]>(`/api/v1/campaign${advertiserId ? `?advertiser_id=${advertiserId}` : ""}`);
export const createCampaign = (payload: CampaignInput) => apiFetch<Campaign>("/api/v1/campaign", { method: "POST", body: JSON.stringify(payload) });
export const getCampaign = (id: number) => apiFetch<Campaign>(`/api/v1/campaign/${id}`);
export const updateCampaign = (id: number, payload: Partial<Campaign>) => apiFetch<Campaign>(`/api/v1/campaign/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
export const setCampaignStatus = (id: number, status: number) => updateCampaign(id, { status });
export const activateCampaign = (id: number) => apiFetch<Campaign>(`/api/v1/campaign/${id}/activate`, { method: "POST" });
export const listCreatives = (campaignId: number, adSetId?: number) => {
  const params = new URLSearchParams({ campaign_id: campaignId.toString() });
  if (adSetId !== undefined && adSetId !== null) params.append("ad_set_id", adSetId.toString());
  return apiFetch<Creative[]>(`/api/v1/creative?${params.toString()}`);
};
export const createCreative = (payload: CreativeInput) => apiFetch<Creative>("/api/v1/creative", { method: "POST", body: JSON.stringify(payload) });
export interface SlotData { id: number; publisher_id: number; property_id: number | null; slot_id: string; name: string; format: number; width: number; height: number; status: number; floor_price: number; is_chat_surface: boolean; }
export interface SlotInput { slot_id: string; name: string; format: number; width: number; height: number; property_id?: number | null; floor_price?: number; is_chat_surface?: boolean; }
export interface PublisherInput { name: string; email?: string; site_url?: string; is_sandbox?: boolean; }
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

// Publisher Placement Health (Phase 6 WU3)
export interface FormatBenchmark {
    format: number;
    format_name?: string;
    eligible_slots_count: number;
    median_ctr: number | null;
    median_ecpm: number | null;
}

export interface PlacementHealthItem {
    slot_id: string;
    slot_name: string;
    format: number;
    format_name: string;
    impressions: number;
    clicks: number;
    conversions: number;
    earnings: number;
    ctr: number;
    ecpm: number;
    verdict: "great" | "healthy" | "low_visibility" | "insufficient_data" | string;
    recommendation: string;
    benchmark: FormatBenchmark;
}

export interface PlacementHealthResponse {
    publisher_id: number;
    window_days: number;
    start_date: string;
    end_date: string;
    total_placements: number;
    placements: PlacementHealthItem[];
    benchmarks: Record<string, FormatBenchmark>;
}

export const getPlacementHealth = (publisherId: number, windowDays: number = 30) =>
    apiFetch<PlacementHealthResponse>(`/api/v1/publisher/${publisherId}/placements/health?window_days=${windowDays}`);

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
export type RadiusUnit = "meters" | "km" | "miles";
export const MIN_RADIUS_METERS = 100;
export const MAX_RADIUS_METERS = 100000;

export interface GeoEntry {
  country?: string | null;
  state?: string | null;
  city?: string | null;
  pincode?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  radius_meters?: number | null;
  radius_unit?: RadiusUnit | null;
  name?: string | null;
}

export function convertRadiusToMeters(value: number, unit: RadiusUnit): number {
  if (!Number.isFinite(value) || value <= 0) return 0;
  if (unit === "km") return Math.round(value * 1000 * 100) / 100;
  if (unit === "miles") return Math.round(value * 1609.344 * 100) / 100;
  return Math.round(value * 100) / 100;
}

export function convertMetersToUnit(meters: number, unit: RadiusUnit): number {
  if (!Number.isFinite(meters) || meters <= 0) return 0;
  if (unit === "km") return Math.round((meters / 1000) * 100) / 100;
  if (unit === "miles") return Math.round((meters / 1609.344) * 100) / 100;
  return Math.round(meters * 100) / 100;
}

export function isRadiusGeoEntry(e: GeoEntry): boolean {
  return (
    e.radius_meters !== undefined &&
    e.radius_meters !== null &&
    e.latitude !== undefined &&
    e.latitude !== null &&
    e.longitude !== undefined &&
    e.longitude !== null
  );
}

export function formatGeoEntry(e: GeoEntry): string {
  if (isRadiusGeoEntry(e)) {
    const lat = e.latitude != null ? Number(e.latitude).toFixed(4) : "—";
    const lon = e.longitude != null ? Number(e.longitude).toFixed(4) : "—";
    const radiusKm = e.radius_meters != null ? (e.radius_meters / 1000).toFixed(1) : "—";
    const label = e.name ? `"${e.name}" ` : "";
    const country = e.country ? ` [${e.country}]` : "";
    return `🎯 Radius: ${radiusKm} km around (${lat}, ${lon}) ${label}${country}`.trim();
  }
  const parts: string[] = [];
  if (e.country) parts.push(e.country);
  if (e.state) parts.push(e.state);
  if (e.city) parts.push(e.city);
  if (e.pincode) parts.push(e.pincode);
  return parts.filter(Boolean).join(" > ");
}

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
export interface PaymentOrderResponse { order_id: string; amount: number; currency: string; qr_image_url: string | null; }
export const getAdvertiserLedger = (advertiserId: number) => apiFetch<LedgerResponse>(`/api/v1/finance/advertiser/${advertiserId}/ledger`);
export const depositFunds = (advertiserId: number, amount: number, referenceId?: string) => apiFetch<TransactionResponse>(`/api/v1/finance/advertiser/${advertiserId}/deposit`, { method: "POST", body: JSON.stringify({ amount, reference_id: referenceId }) });
export const createAdvertiserPaymentRequest = (advertiserId: number, amount: number) => apiFetch<PaymentOrderResponse>(`/api/v1/finance/advertiser/${advertiserId}/payment-request`, { method: "POST", body: JSON.stringify({ amount }) });
export const getPublisherLedger = (publisherId: number) => apiFetch<LedgerResponse>(`/api/v1/finance/publisher/${publisherId}/ledger`);
export const requestPayout = (publisherId: number, amount: number, referenceId?: string) => apiFetch<TransactionResponse>(`/api/v1/finance/publisher/${publisherId}/payout`, { method: "POST", body: JSON.stringify({ amount, reference_id: referenceId }) });
// API Keys
export interface ApiKey { id: number; publisher_id: number; name: string; key_prefix: string; is_sandbox: boolean; rate_limit_per_minute: number; daily_quota: number; last_used_at: string | null; status: number; created_at: string; revoked_at: string | null; }
export interface ApiKeyCreateInput { name: string; is_sandbox?: boolean; rate_limit_per_minute?: number; daily_quota?: number; }
export interface ApiKeyCreateResponse extends ApiKey { raw_key: string; }
export interface ApiKeyUsageBreakdown {
  total_requests: number;
  admitted_requests: number;
  rate_limited_requests: number;
  quota_exceeded_requests: number;
  served_ads: number;
}

export interface ApiKeyUsageResponse {
  key_id: number;
  publisher_id: number;
  name: string;
  key_prefix: string;
  is_sandbox: boolean;
  rate_limit_per_minute: number;
  daily_quota: number;
  current_minute_usage: number;
  rate_limit_remaining: number;
  rate_limit_reset_seconds: number;
  today_usage: number;
  daily_quota_remaining: number;
  daily_reset_time_utc: string;
  daily_reset_seconds: number;
  breakdown: ApiKeyUsageBreakdown;
  last_used_at: string | null;
  status: number;
  revoked_at: string | null;
  created_at: string;
}

export const listPublisherApiKeys = (publisherId: number) => apiFetch<ApiKey[]>(`/api/v1/publisher/${publisherId}/keys`);
export const getPublisherKeysUsage = (publisherId: number) => apiFetch<ApiKeyUsageResponse[]>(`/api/v1/publisher/${publisherId}/keys/usage`);
export const getPublisherKeyUsage = (publisherId: number, keyId: number) => apiFetch<ApiKeyUsageResponse>(`/api/v1/publisher/${publisherId}/keys/${keyId}/usage`);
export const createPublisherApiKey = (publisherId: number, payload: string | ApiKeyCreateInput) => {
  const body = typeof payload === "string"
    ? { publisher_id: publisherId, name: payload, label: payload }
    : { publisher_id: publisherId, label: payload.name, ...payload };
  return apiFetch<ApiKeyCreateResponse>(`/api/v1/publisher/keys`, { method: "POST", body: JSON.stringify(body) });
};
export const revokePublisherApiKey = (publisherId: number, keyId: number) => apiFetch<void>(`/api/v1/publisher/${publisherId}/keys/${keyId}`, { method: "DELETE" });

// Webhooks
export interface Webhook {
  id: number;
  owner_type: string;
  owner_id: number;
  url: string;
  events: string[];
  is_active: boolean;
  deliveries_ok: number;
  deliveries_failed: number;
  created_at: string;
  updated_at: string;
}
export interface WebhookCreateResponse extends Webhook { secret: string; }
export interface WebhookInput { url: string; events: string[]; is_active?: boolean; }

export interface WebhookDelivery {
  id: number;
  webhook_id: number;
  owner_type: string;
  owner_id: number;
  event_id: string | null;
  event_type: string;
  status: "pending" | "processing" | "delivered" | "failed";
  attempts: number;
  max_attempts: number;
  next_attempt_at: string;
  last_attempt_at: string | null;
  last_http_status: number | null;
  last_error: string | null;
  delivered_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface WebhookDeliveryDetail extends WebhookDelivery {
  payload: string;
}

export const listWebhooks = () => apiFetch<Webhook[]>("/api/v1/webhooks");
export const createWebhook = (payload: WebhookInput) => apiFetch<WebhookCreateResponse>("/api/v1/webhooks", { method: "POST", body: JSON.stringify(payload) });
export const updateWebhook = (id: number, payload: Partial<WebhookInput>) => apiFetch<Webhook>(`/api/v1/webhooks/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
export const deleteWebhook = (id: number) => apiFetch<void>(`/api/v1/webhooks/${id}`, { method: "DELETE" });
export const testWebhook = (id: number, payload?: any) => apiFetch<{delivered: boolean; status_code?: number; error?: string; delivery_id?: number}>(`/api/v1/webhooks/${id}/test`, { method: "POST", body: payload ? JSON.stringify(payload) : undefined });
export const listWebhookDeliveries = (webhookId: number, limit = 50, offset = 0) =>
  apiFetch<WebhookDelivery[]>(`/api/v1/webhooks/${webhookId}/deliveries?limit=${limit}&offset=${offset}`);
export const getWebhookDeliveryDetail = (webhookId: number, deliveryId: number) =>
  apiFetch<WebhookDeliveryDetail>(`/api/v1/webhooks/${webhookId}/deliveries/${deliveryId}`);
export const retryWebhookDelivery = (webhookId: number, deliveryId: number) =>
  apiFetch<WebhookDelivery>(`/api/v1/webhooks/${webhookId}/deliveries/${deliveryId}/retry`, { method: "POST" });


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

// Notification APIs (Phase 6)
export interface NotificationFilters {
  page?: number;
  page_size?: number;
  unread_only?: boolean;
  category?: string;
}

export const listNotifications = (filters?: NotificationFilters): Promise<NotificationListResponse> => {
  const params = new URLSearchParams();
  if (filters?.page) params.set("page", String(filters.page));
  if (filters?.page_size) params.set("page_size", String(filters.page_size));
  if (filters?.unread_only) params.set("unread_only", "true");
  if (filters?.category) params.set("category", filters.category);
  const qs = params.toString();
  return apiFetch<NotificationListResponse>(`/api/v1/notifications${qs ? `?${qs}` : ""}`);
};

export const getUnreadNotificationCount = (): Promise<UnreadCountResponse> => {
  return apiFetch<UnreadCountResponse>("/api/v1/notifications/unread-count");
};

export const markNotificationRead = (notificationId: number): Promise<NotificationResponse> => {
  return apiFetch<NotificationResponse>(`/api/v1/notifications/${notificationId}/read`, {
    method: "PATCH",
  });
};

export const markAllNotificationsRead = (): Promise<MarkAllReadResponse> => {
  return apiFetch<MarkAllReadResponse>("/api/v1/notifications/read-all", {
    method: "PATCH",
  });
};

export const getNotificationPreferences = (): Promise<NotificationPreferenceListResponse> => {
  return apiFetch<NotificationPreferenceListResponse>("/api/v1/notifications/preferences");
};

export const updateNotificationPreferences = (
  preferences: NotificationPreferenceItem[] | NotificationPreferenceUpdateRequest
): Promise<NotificationPreferenceListResponse> => {
  return apiFetch<NotificationPreferenceListResponse>("/api/v1/notifications/preferences", {
    method: "PUT",
    body: JSON.stringify(preferences),
  });
};

/**
 * Validate and sanitize notification navigation links.
 * Restricts to internal relative URL paths without protocols, credentials, or hostnames.
 */
export function getSafeInternalLink(link: string | null | undefined): string | null {
  if (!link || typeof link !== "string") return null;
  const trimmed = link.trim();
  // Disallow absolute URLs with protocols (http:, javascript:, data:, etc.) or protocol-relative '//'
  if (
    trimmed.startsWith("/") &&
    !trimmed.startsWith("//") &&
    !trimmed.startsWith("/\\") &&
    !/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(trimmed)
  ) {
    return trimmed;
  }
  return null;
}

// ── Phase 7B: AdSets ────────────────────────────────────────────────────────

export const createAdSet = (campaignId: number, data: AdSetCreateInput): Promise<AdSet> =>
  apiFetch<AdSet>(`/api/v1/campaign/${campaignId}/ad-sets`, {
    method: "POST",
    body: JSON.stringify(data),
  });

export const listAdSets = (campaignId: number): Promise<AdSet[]> =>
  apiFetch<AdSet[]>(`/api/v1/campaign/${campaignId}/ad-sets`);

export const getAdSet = (adSetId: number): Promise<AdSet> =>
  apiFetch<AdSet>(`/api/v1/ad-set/${adSetId}`);

export const updateAdSet = (adSetId: number, data: AdSetUpdateInput): Promise<AdSet> =>
  apiFetch<AdSet>(`/api/v1/ad-set/${adSetId}`, {
    method: "PATCH",
    body: JSON.stringify(data),
  });

export const pauseAdSet = (adSetId: number): Promise<AdSet> =>
  apiFetch<AdSet>(`/api/v1/ad-set/${adSetId}/pause`, {
    method: "PATCH",
  });

export const resumeAdSet = (adSetId: number): Promise<AdSet> =>
  apiFetch<AdSet>(`/api/v1/ad-set/${adSetId}/resume`, {
    method: "PATCH",
  });

export const deleteAdSet = (adSetId: number): Promise<void> =>
  apiFetch<void>(`/api/v1/ad-set/${adSetId}`, {
    method: "DELETE",
  });

export const getAdSetTargeting = (adSetId: number): Promise<AdSetTargetingResponse> =>
  apiFetch<AdSetTargetingResponse>(`/api/v1/ad-set/${adSetId}/targeting`);

export const updateAdSetTargeting = (adSetId: number, data: any): Promise<AdSetTargetingResponse> =>
  apiFetch<AdSetTargetingResponse>(`/api/v1/ad-set/${adSetId}/targeting`, {
    method: "PUT",
    body: JSON.stringify(data),
  });

export const getCampaignAdSetReporting = (
  campaignId: number,
  startDate?: string,
  endDate?: string
): Promise<AdSetReportingResponse> => {
  const params = new URLSearchParams();
  if (startDate) params.append("start_date", startDate);
  if (endDate) params.append("end_date", endDate);
  const qs = params.toString();
  return apiFetch<AdSetReportingResponse>(
    `/api/v1/campaign/${campaignId}/ad-set-reporting${qs ? `?${qs}` : ""}`
  );
};

// ── Website Builder Endpoints (Phase 10B/10C/10D) ──────────────────────────

export const checkWebsiteSlug = (slug: string): Promise<SlugCheckResponse> =>
  apiFetch<SlugCheckResponse>(`/api/v1/website/check-slug?slug=${encodeURIComponent(slug)}`);

export const listWebsiteProjects = (): Promise<ProjectSummary[]> =>
  apiFetch<ProjectSummary[]>("/api/v1/website/projects");

export const createWebsiteProject = (data: {
  name: string;
  slug: string;
  template_id?: string;
  theme_config?: Partial<ThemeConfig>;
}): Promise<ProjectData> =>
  apiFetch<ProjectData>("/api/v1/website/projects", {
    method: "POST",
    body: JSON.stringify(data),
  });

export const getWebsiteProject = (id: number): Promise<ProjectData> =>
  apiFetch<ProjectData>(`/api/v1/website/projects/${id}`);

export const updateWebsiteProject = (
  id: number,
  data: {
    name?: string;
    template_id?: string;
    theme_config?: Partial<ThemeConfig>;
  }
): Promise<ProjectData> =>
  apiFetch<ProjectData>(`/api/v1/website/projects/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });

export const deleteWebsiteProject = (id: number): Promise<void> =>
  apiFetch<void>(`/api/v1/website/projects/${id}`, {
    method: "DELETE",
  });

export const listWebsitePages = (projectId: number): Promise<PageSummary[]> =>
  apiFetch<PageSummary[]>(`/api/v1/website/projects/${projectId}/pages`);

export const getWebsitePage = (projectId: number, pageId: number): Promise<PageData> =>
  apiFetch<PageData>(`/api/v1/website/projects/${projectId}/pages/${pageId}`);

export const createWebsitePage = (
  projectId: number,
  data: {
    title: string;
    slug?: string;
    is_home?: boolean;
    seo_meta?: SeoMeta;
    blocks?: { schema_version: number; blocks: BlockItem[] };
  }
): Promise<PageData> =>
  apiFetch<PageData>(`/api/v1/website/projects/${projectId}/pages`, {
    method: "POST",
    body: JSON.stringify(data),
  });

export const updateWebsitePage = (
  projectId: number,
  pageId: number,
  data: {
    title?: string;
    slug?: string;
    is_home?: boolean;
    seo_meta?: SeoMeta;
    blocks?: { schema_version: number; blocks: BlockItem[] };
  }
): Promise<PageData> =>
  apiFetch<PageData>(`/api/v1/website/projects/${projectId}/pages/${pageId}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });

export const deleteWebsitePage = (projectId: number, pageId: number): Promise<void> =>
  apiFetch<void>(`/api/v1/website/projects/${projectId}/pages/${pageId}`, {
    method: "DELETE",
  });

export const reorderWebsitePages = (projectId: number, pageIds: number[]): Promise<PageSummary[]> =>
  apiFetch<PageSummary[]>(`/api/v1/website/projects/${projectId}/pages/reorder`, {
    method: "POST",
    body: JSON.stringify({ page_ids: pageIds }),
  });

export const publishWebsiteProject = (projectId: number, notes?: string): Promise<ProjectData> =>
  apiFetch<ProjectData>(`/api/v1/website/projects/${projectId}/publish`, {
    method: "POST",
    body: JSON.stringify({ notes: notes || null }),
  });

export const unpublishWebsiteProject = (projectId: number): Promise<ProjectData> =>
  apiFetch<ProjectData>(`/api/v1/website/projects/${projectId}/unpublish`, {
    method: "POST",
  });

export const listWebsiteVersions = (projectId: number): Promise<VersionSummary[]> =>
  apiFetch<VersionSummary[]>(`/api/v1/website/projects/${projectId}/versions`);

export const rollbackWebsiteVersion = (
  projectId: number,
  versionId: number,
  notes?: string
): Promise<ProjectData> =>
  apiFetch<ProjectData>(`/api/v1/website/projects/${projectId}/versions/${versionId}/rollback`, {
    method: "POST",
    body: JSON.stringify({ notes: notes || null }),
  });

export const generateWebsitePreviewToken = (projectId: number): Promise<PreviewTokenResponse> =>
  apiFetch<PreviewTokenResponse>(`/api/v1/website/projects/${projectId}/preview-token`, {
    method: "POST",
  });

