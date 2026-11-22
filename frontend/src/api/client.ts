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
  BusinessProfileIntake,
  BusinessProfileAudit,
  CustomDomainStatusResponse,
  WebmasterToolsConfig,
  AccessibilityAuditReport,
  GeoReadinessReport,
  VersionSummary,
  PreviewTokenResponse,
  SlugCheckResponse,
  WebsiteTemplateSummary,
  WebsiteTemplateDetail,
  EmailMarketingList,
  CreateMailingListInput,
  EmailMarketingContact,
  CreateContactInput,
  ImportContactsInput,
  ImportContactsResult,
  EmailMarketingCampaign,
  CreateCampaignInput,
  UpdateCampaignInput,
  CampaignPreview,
  CampaignAnalytics,
  AISectionPreset,
  WebsiteAIGenerateSectionInput,
  WebsiteAIGenerateSectionResponse,
  AdSlot,
  PublisherProperty,
  PublisherPropertyInput,
  PropertyChallengeRequest,
  PropertyChallengeResponse,
  PropertyVerificationStatus,
  PropertyVerificationMethod,
  BoundSlot,
  AdPlacementLocation,
  AdPlacementContent,
  MonetizationPrerequisites,
  MonetizationConsentScope,
  MonetizationConsentRecord,
  ProjectMonetizationStatus,
  PublisherLinkInput,
  MonetizationConsentInput,
  UnpublishProjectResponse,
  ReadinessIssue,
  CampaignReadinessResponse,
  OperationalExceptionsResponse,
  AuditLogItem,
  PublisherSafetySummary,
  PublisherEarningsBreakdown,
} from "../types";

export const WebsiteMonetizationStatusCode = {
  DISABLED: 0,
  PENDING_APPROVAL: 1,
  ACTIVE_MONETIZED: 2,
  PAUSED: 3,
  REJECTED: 4,
} as const;

export type WebsiteMonetizationStatusCode =
  typeof WebsiteMonetizationStatusCode[keyof typeof WebsiteMonetizationStatusCode];

export const WebsiteMonetizationStatusMap: Record<number, string> = {
  0: "disabled",
  1: "pending_approval",
  2: "active_monetized",
  3: "paused",
  4: "rejected",
};

export type {
  AISectionPreset,
  WebsiteAIGenerateSectionInput,
  WebsiteAIGenerateSectionResponse,
  EmailMarketingList,
  CreateMailingListInput,
  EmailMarketingContact,
  CreateContactInput,
  ImportContactsInput,
  ImportContactsResult,
  EmailMarketingCampaign,
  CreateCampaignInput,
  UpdateCampaignInput,
  CampaignPreview,
  CampaignAnalytics,
  UserContextPayload,
  MessageResponse,
  VerifyEmailResponse,
  VerifyEmailInput,
  ResendVerificationInput,
  RequestPasswordResetInput,
  ResetPasswordInput,
  AdSlot,
  PublisherProperty,
  PublisherPropertyInput,
  PropertyChallengeRequest,
  PropertyChallengeResponse,
  PropertyVerificationStatus,
  PropertyVerificationMethod,
  BoundSlot,
  AdPlacementLocation,
  AdPlacementContent,
  MonetizationPrerequisites,
  MonetizationConsentScope,
  MonetizationConsentRecord,
  ProjectMonetizationStatus,
  PublisherLinkInput,
  MonetizationConsentInput,
  UnpublishProjectResponse,
  ReadinessIssue,
  CampaignReadinessResponse,
};
const BASE_URL = (import.meta.env?.VITE_API_BASE_URL as string | undefined) ?? "";
let memoryToken: string | null = null;
export const setMemoryToken = (token: string | null) => { memoryToken = token; };
export const getMemoryToken = () => memoryToken;
export class ApiError extends Error {
  status: number;
  headers?: Headers;
  constructor(message: string, status: number, headers?: Headers) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.headers = headers;
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
    throw new ApiError(message, res.status, res.headers);
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
export const rejectCreative = (creativeId: number, reason: string, category: string = "POLICY_VIOLATION") => apiFetch<ModerationCreative>(`/api/v1/moderation/creatives/${creativeId}/reject`, { method: "POST", body: JSON.stringify({ reason, category }) });
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
export interface CreativeInput { campaign_id: number; ad_set_id?: number | null; title: string; description?: string; image_url?: string; video_url?: string; landing_url: string; creative_type: number; width: number; height: number; category?: string; format?: string; }
export const listCampaigns = (advertiserId?: number) => apiFetch<Campaign[]>(`/api/v1/campaign${advertiserId ? `?advertiser_id=${advertiserId}` : ""}`);
export const createCampaign = (payload: CampaignInput) => apiFetch<Campaign>("/api/v1/campaign", { method: "POST", body: JSON.stringify(payload) });
export const getCampaign = (id: number) => apiFetch<Campaign>(`/api/v1/campaign/${id}`);
export const updateCampaign = (id: number, payload: Partial<Campaign>) => apiFetch<Campaign>(`/api/v1/campaign/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
export const setCampaignStatus = (id: number, status: number) => updateCampaign(id, { status });
export const activateCampaign = (id: number) => apiFetch<Campaign>(`/api/v1/campaign/${id}/activate`, { method: "POST" });
export const getCampaignReadiness = (campaignId: number): Promise<CampaignReadinessResponse> =>
  apiFetch<CampaignReadinessResponse>(`/api/v1/campaign/${campaignId}/readiness`);
export const listCreatives = (campaignId: number, adSetId?: number) => {
  const params = new URLSearchParams({ campaign_id: campaignId.toString() });
  if (adSetId !== undefined && adSetId !== null) params.append("ad_set_id", adSetId.toString());
  return apiFetch<Creative[]>(`/api/v1/creative?${params.toString()}`);
};
export const createCreative = (payload: CreativeInput) => apiFetch<Creative>("/api/v1/creative", { method: "POST", body: JSON.stringify(payload) });
export interface SlotData {
  id: number;
  publisher_id: number;
  property_id: number | null;
  slot_id: string;
  name: string;
  format: number;
  width: number;
  height: number;
  status: number;
  floor_price: number;
  is_chat_surface: boolean;
  created_at?: string;
  updated_at?: string;
}
export interface SlotInput { slot_id: string; name: string; format: number; width: number; height: number; property_id?: number | null; floor_price?: number; is_chat_surface?: boolean; }
export interface PublisherInput { name: string; email?: string; site_url?: string; is_sandbox?: boolean; }
export const listSlots = (publisherId?: number) => apiFetch<SlotData[]>(`/api/v1/slot${publisherId ? `?publisher_id=${publisherId}` : ""}`);
export const getSlot = (slotId: number): Promise<SlotData> => apiFetch<SlotData>(`/api/v1/slot/${slotId}`);
export const createSlot = (payload: SlotInput) => apiFetch<SlotData>("/api/v1/slot", { method: "POST", body: JSON.stringify(payload) });
export const updateSlot = (id: number, payload: Partial<SlotInput>) => apiFetch<SlotData>(`/api/v1/slot/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
export const listPublishers = () => apiFetch<Publisher[]>("/api/v1/publisher");
export const createPublisher = (payload: PublisherInput) => apiFetch<Publisher>("/api/v1/publisher", { method: "POST", body: JSON.stringify(payload) });
export const updatePublisher = (id: number, payload: Partial<Publisher>) => apiFetch<Publisher>(`/api/v1/publisher/${id}`, { method: "PATCH", body: JSON.stringify(payload) });
export const setPublisherStatus = (id: number, status: number) => updatePublisher(id, { status });

// Publisher Properties & Domain Verification
export const listPublisherProperties = (publisherId: number): Promise<PublisherProperty[]> =>
  apiFetch<PublisherProperty[]>(`/api/v1/publisher/${publisherId}/property`);

export const getPublisherProperty = (propertyId: number): Promise<PublisherProperty> =>
  apiFetch<PublisherProperty>(`/api/v1/publisher/property/${propertyId}`);

export const createPublisherProperty = (publisherId: number, payload: PublisherPropertyInput): Promise<PublisherProperty> =>
  apiFetch<PublisherProperty>(`/api/v1/publisher/${publisherId}/property`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const updatePublisherProperty = (propertyId: number, payload: Partial<PublisherPropertyInput>): Promise<PublisherProperty> =>
  apiFetch<PublisherProperty>(`/api/v1/publisher/property/${propertyId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });

export const challengePublisherProperty = (
  propertyId: number,
  payload?: "DNS_TXT" | "HTML_META" | PropertyChallengeRequest
): Promise<PropertyChallengeResponse> => {
  const body = typeof payload === "string" ? { method: payload } : (payload || { method: "DNS_TXT" });
  return apiFetch<PropertyChallengeResponse>(`/api/v1/publisher/property/${propertyId}/challenge`, {
    method: "POST",
    body: JSON.stringify(body),
  });
};

export const verifyPublisherProperty = (propertyId: number): Promise<PublisherProperty> =>
  apiFetch<PublisherProperty>(`/api/v1/publisher/property/${propertyId}/verify`, {
    method: "POST",
  });

// Publisher Ad Units (Tags)
export const generateSlotTag = (slotId: number) => apiFetch<{ html: string }>(`/api/v1/slot/${slotId}/tag`);

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
export const deleteCampaignAssignment = (campaignId: number, assignmentId: number) => apiFetch<{ success: boolean }>(`/api/v1/campaign/${campaignId}/assignments/${assignmentId}`, { method: "DELETE" });

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
export const getReferenceStates = (country: string) => apiFetch<{ code: string; name: string }[]>(`/api/v1/reference/locations/states?country=${country}`);
export const getReferenceCities = (country: string, state: string) => apiFetch<string[]>(`/api/v1/reference/locations/cities?country=${country}&state=${state}`);
export const getReferencePincodes = (country: string, state: string, city: string) => apiFetch<string[]>(`/api/v1/reference/locations/pincodes?country=${country}&state=${state}&city=${city}`);

// Finance APIs
export interface TransactionResponse {
  id: number;
  type: number;
  amount: number;
  currency: string;
  advertiser_id?: number | null;
  publisher_id?: number | null;
  campaign_id?: number | null;
  reference_id?: string | null;
  provider_event_id?: string | null;
  rev_share_rate?: number | null;
  hold_until?: string | null;
  is_cleared?: boolean;
  description?: string | null;
  created_at: string;
}
export interface LedgerResponse { balance: number; transactions: TransactionResponse[]; }
export interface PaymentOrderResponse { order_id: string; amount: number; currency: string; qr_image_url: string | null; }
export const getAdvertiserLedger = (advertiserId: number) => apiFetch<LedgerResponse>(`/api/v1/finance/advertiser/${advertiserId}/ledger`);
export const depositFunds = (advertiserId: number, amount: number, referenceId?: string) => apiFetch<TransactionResponse>(`/api/v1/finance/advertiser/${advertiserId}/deposit`, { method: "POST", body: JSON.stringify({ amount, reference_id: referenceId }) });
export const createAdvertiserPaymentRequest = (advertiserId: number, amount: number) => apiFetch<PaymentOrderResponse>(`/api/v1/finance/advertiser/${advertiserId}/payment-request`, { method: "POST", body: JSON.stringify({ amount }) });
export const getPublisherLedger = (publisherId: number) => apiFetch<LedgerResponse>(`/api/v1/finance/publisher/${publisherId}/ledger`);
export const requestPayout = (publisherId: number, amount: number, referenceId?: string) => apiFetch<TransactionResponse>(`/api/v1/finance/publisher/${publisherId}/payout`, { method: "POST", body: JSON.stringify({ amount, reference_id: referenceId }) });

export interface UnresolvedDiscrepancy {
  queue_id: string;
  status: string;
  entity_type: string;
  entity_id: number | null;
  name: string;
  metric: string;
  expected_value: number;
  actual_value: number;
  delta: number;
  severity: "WARNING" | "CRITICAL";
  reference_id?: string | null;
  provider_event_id?: string | null;
  reason: string;
  detected_at: string;
}

export interface ReconciliationReport {
  status: string;
  start_date: string;
  end_date: string;
  total_event_spend: number;
  total_ledger_spend: number;
  spend_delta: number;
  total_ivt_events: number;
  ivt_suppressed_spend: number;
  discrepancies: Array<{
    entity_type: string;
    entity_id: number | null;
    name: string;
    metric: string;
    expected_value: number;
    actual_value: number;
    delta: number;
    severity: string;
    details: string;
  }>;
  unresolved_queue: UnresolvedDiscrepancy[];
  alerts: string[];
}

export const runReconciliation = (params?: { start_date?: string; end_date?: string; advertiser_id?: number; publisher_id?: number }) =>
  apiFetch<ReconciliationReport>("/api/v1/finance/reconcile", { method: "POST", body: JSON.stringify(params || {}) });

export const getAuditLedger = (params?: {
  start_date?: string;
  end_date?: string;
  reference?: string;
  provider_event_id?: string;
  advertiser_id?: number;
  publisher_id?: number;
  campaign_id?: number;
  type?: number;
  is_cleared?: boolean;
  adjustment_reason?: string;
}) => {
  const query = new URLSearchParams();
  if (params?.start_date) query.set("start_date", params.start_date);
  if (params?.end_date) query.set("end_date", params.end_date);
  if (params?.reference) query.set("reference", params.reference);
  if (params?.provider_event_id) query.set("provider_event_id", params.provider_event_id);
  if (params?.advertiser_id) query.set("advertiser_id", String(params.advertiser_id));
  if (params?.publisher_id) query.set("publisher_id", String(params.publisher_id));
  if (params?.campaign_id) query.set("campaign_id", String(params.campaign_id));
  if (params?.type) query.set("type", String(params.type));
  if (params?.is_cleared !== undefined) query.set("is_cleared", String(params.is_cleared));
  if (params?.adjustment_reason) query.set("adjustment_reason", params.adjustment_reason);
  const qs = query.toString();
  return apiFetch<TransactionResponse[]>(`/api/v1/finance/ledger/audit${qs ? `?${qs}` : ""}`);
};

export interface PublisherPayoutDetails {
  publisher_id: number;
  payout_details: Record<string, any> | null;
}

export const getPublisherPayoutDetails = (publisherId: number) => {
  return apiFetch<PublisherPayoutDetails>(`/api/v1/finance/publisher/${publisherId}/payout-details`);
};

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
export const testWebhook = (id: number, payload?: any) => apiFetch<{ delivered: boolean; status_code?: number; error?: string; delivery_id?: number }>(`/api/v1/webhooks/${id}/test`, { method: "POST", body: payload ? JSON.stringify(payload) : undefined });
export const listWebhookDeliveries = (webhookId: number, limit = 50, offset = 0) =>
  apiFetch<WebhookDelivery[]>(`/api/v1/webhooks/${webhookId}/deliveries?limit=${limit}&offset=${offset}`);
export const getWebhookDeliveryDetail = (webhookId: number, deliveryId: number) =>
  apiFetch<WebhookDeliveryDetail>(`/api/v1/webhooks/${webhookId}/deliveries/${deliveryId}`);
export const retryWebhookDelivery = (webhookId: number, deliveryId: number) =>
  apiFetch<WebhookDelivery>(`/api/v1/webhooks/${webhookId}/deliveries/${deliveryId}/retry`, { method: "POST" });


// Suggestions
export const getCampaignSuggestions = (campaignId: number) => apiFetch<Suggestion[]>(`/api/v1/campaign/${campaignId}/suggestions`);
export const updateSuggestionStatus = (suggestionId: number, status: string) => apiFetch<Suggestion>(`/api/v1/suggestion/${suggestionId}`, { method: "PATCH", body: JSON.stringify({ status }) });

// Phase 6: Optimization & Laya Operator Governance
export interface OptimizationStatus {
  global_mode: "shadow" | "suggestions_only" | "limited_pilot" | "autopilot" | string;
  kill_switch: boolean;
  enabled: boolean;
  pilot_campaign_ids: number[];
  pilot_count: number;
  cooldown_seconds: number;
  max_daily_changes: number;
  pilot_max_budget_delta_amount: number;
  laya_base_url: string;
  laya_enabled: boolean;
  loop_is_running: boolean;
}

export interface OptimizationDecisionRecord {
  id: number;
  decision_id: string;
  campaign_id: number;
  mode: string;
  action: string | null;
  confidence: number | null;
  policy_version: string;
  outcome: "applied" | "suggested" | "shadow_logged" | "rejected" | "failed" | "reverted" | string;
  reason: string | null;
  target_payload: Record<string, any> | null;
  evidence: Record<string, any> | null;
  change_applied: Record<string, any> | null;
  actor: string;
  execution_status: string | null;
  fencing_token: number | null;
  created_at: string;
  updated_at: string;
}

export interface CampaignOptimizationState {
  campaign_id: number;
  campaign_name: string;
  autopilot: boolean;
  is_pilot_eligible: boolean;
  effective_mode: string;
  in_cooldown: boolean;
  daily_changes_count: number;
  max_daily_changes: number;
  impressions: number;
  clicks: number;
  conversions: number;
  spent_today: number;
  spent_total: number;
  daily_budget: number;
  last_decision: OptimizationDecisionRecord | null;
  can_revert: boolean;
}

export interface DecisionRevertResult {
  decision_id: string;
  campaign_id: number;
  status: string;
  detail: Record<string, any>;
}

export const getOptimizationStatus = (): Promise<OptimizationStatus> =>
  apiFetch<OptimizationStatus>("/api/v1/optimization/status");

export const updateOptimizationStatus = (patch: {
  mode?: string;
  kill_switch?: boolean;
  pilot_campaign_ids?: number[];
}): Promise<OptimizationStatus> =>
  apiFetch<OptimizationStatus>("/api/v1/optimization/status", {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const listOptimizationDecisions = (params?: {
  campaignId?: number;
  outcome?: string;
  mode?: string;
  limit?: number;
}): Promise<OptimizationDecisionRecord[]> => {
  const q = new URLSearchParams();
  if (params?.campaignId) q.set("campaign_id", String(params.campaignId));
  if (params?.outcome) q.set("outcome", params.outcome);
  if (params?.mode) q.set("mode", params.mode);
  if (params?.limit) q.set("limit", String(params.limit));
  const qs = q.toString();
  return apiFetch<OptimizationDecisionRecord[]>(`/api/v1/optimization/decisions${qs ? `?${qs}` : ""}`);
};

export const revertOptimizationDecision = (decisionId: string): Promise<DecisionRevertResult> =>
  apiFetch<DecisionRevertResult>(`/api/v1/optimization/decisions/${decisionId}/revert`, { method: "POST" });

export const getCampaignOptimizationState = (campaignId: number): Promise<CampaignOptimizationState> =>
  apiFetch<CampaignOptimizationState>(`/api/v1/campaign/${campaignId}/optimization-state`);

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

// ── Website Builder Endpoints (Phase 10B/10C/10D & Phase 2 Templates) ─────

export const listWebsiteTemplates = (params?: { category?: string; search?: string }): Promise<WebsiteTemplateSummary[]> => {
  const query = new URLSearchParams();
  if (params?.category && params.category !== "All") query.append("category", params.category);
  if (params?.search && params.search.trim()) query.append("search", params.search.trim());
  const qs = query.toString();
  return apiFetch<WebsiteTemplateSummary[]>(`/api/v1/website/templates${qs ? `?${qs}` : ""}`);
};

export const getWebsiteTemplate = (templateId: string): Promise<WebsiteTemplateDetail> =>
  apiFetch<WebsiteTemplateDetail>(`/api/v1/website/templates/${encodeURIComponent(templateId)}`);

export const checkWebsiteSlug = (slug: string): Promise<SlugCheckResponse> =>
  apiFetch<SlugCheckResponse>(`/api/v1/website/slug/check?slug=${encodeURIComponent(slug)}`);

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

export const publishWebsiteProject = (projectId: number, notes?: string): Promise<VersionSummary> =>
  apiFetch<VersionSummary>(`/api/v1/website/projects/${projectId}/publish`, {
    method: "POST",
    body: JSON.stringify({ notes: notes || null }),
  });

export const unpublishWebsiteProject = (projectId: number): Promise<UnpublishProjectResponse> =>
  apiFetch<UnpublishProjectResponse>(`/api/v1/website/projects/${projectId}/unpublish`, {
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

// ── Website Monetization & Ad Placement Endpoints (Phase 5) ───────────────

export const linkWebsitePublisher = (
  projectId: number,
  payload: PublisherLinkInput
): Promise<ProjectData> =>
  apiFetch<ProjectData>(`/api/v1/website/projects/${projectId}/publisher-link`, {
    method: "POST",
    body: JSON.stringify(payload),
  });

export const listWebsiteProjectSlots = (projectId: number): Promise<BoundSlot[]> =>
  apiFetch<BoundSlot[]>(`/api/v1/website/projects/${projectId}/slots`);

export const getWebsiteMonetizationStatus = (
  projectId: number
): Promise<ProjectMonetizationStatus> =>
  apiFetch<ProjectMonetizationStatus>(`/api/v1/website/projects/${projectId}/monetization`);

export const recordWebsiteMonetizationConsent = (
  projectId: number,
  payload: MonetizationConsentInput
): Promise<ProjectMonetizationStatus> =>
  apiFetch<ProjectMonetizationStatus>(
    `/api/v1/website/projects/${projectId}/monetization/consent`,
    {
      method: "POST",
      body: JSON.stringify(payload),
    }
  );

export const enableWebsiteMonetization = (
  projectId: number
): Promise<ProjectMonetizationStatus> =>
  apiFetch<ProjectMonetizationStatus>(
    `/api/v1/website/projects/${projectId}/monetization/enable`,
    {
      method: "POST",
    }
  );

export const disableWebsiteMonetization = (
  projectId: number
): Promise<ProjectMonetizationStatus> =>
  apiFetch<ProjectMonetizationStatus>(
    `/api/v1/website/projects/${projectId}/monetization/disable`,
    {
      method: "POST",
    }
  );

// ── Open Design AI Generation (Phase 4D) ────────────────────────────────────

export const generateWebsiteAISection = (
  projectId: number,
  input: WebsiteAIGenerateSectionInput
): Promise<WebsiteAIGenerateSectionResponse> =>
  apiFetch<WebsiteAIGenerateSectionResponse>(
    `/api/v1/website/projects/${projectId}/ai/generate-section`,
    {
      method: "POST",
      body: JSON.stringify(input),
    }
  );

// ── Email Marketing (Phase 6.1E) ─────────────────────────────────────────────

export const getMailingLists = (): Promise<EmailMarketingList[]> =>
  apiFetch<EmailMarketingList[]>("/api/v1/email-marketing/lists");

export const createMailingList = (input: CreateMailingListInput): Promise<EmailMarketingList> =>
  apiFetch<EmailMarketingList>("/api/v1/email-marketing/lists", {
    method: "POST",
    body: JSON.stringify(input),
  });

export const getMailingList = (listId: number): Promise<EmailMarketingList> =>
  apiFetch<EmailMarketingList>(`/api/v1/email-marketing/lists/${listId}`);

export const deleteMailingList = (listId: number): Promise<MessageResponse> =>
  apiFetch<MessageResponse>(`/api/v1/email-marketing/lists/${listId}`, {
    method: "DELETE",
  });

export const getListContacts = (
  listId: number,
  options?: { limit?: number; offset?: number; query?: string }
): Promise<EmailMarketingContact[]> => {
  const params = new URLSearchParams();
  if (options?.limit !== undefined) params.set("limit", String(options.limit));
  if (options?.offset !== undefined) params.set("offset", String(options.offset));
  if (options?.query) params.set("query", options.query);
  const qs = params.toString();
  return apiFetch<EmailMarketingContact[]>(`/api/v1/email-marketing/lists/${listId}/contacts${qs ? `?${qs}` : ""}`);
};

export const addContact = (listId: number, input: CreateContactInput): Promise<EmailMarketingContact> =>
  apiFetch<EmailMarketingContact>(`/api/v1/email-marketing/lists/${listId}/contacts`, {
    method: "POST",
    body: JSON.stringify(input),
  });

export const importContactsCsv = (listId: number, input: ImportContactsInput): Promise<ImportContactsResult> =>
  apiFetch<ImportContactsResult>(`/api/v1/email-marketing/lists/${listId}/import`, {
    method: "POST",
    body: JSON.stringify(input),
  });

export const deleteContact = (listId: number, contactId: number): Promise<MessageResponse> =>
  apiFetch<MessageResponse>(`/api/v1/email-marketing/lists/${listId}/contacts/${contactId}`, {
    method: "DELETE",
  });

export const getMarketingCampaigns = (options?: { limit?: number; offset?: number }): Promise<EmailMarketingCampaign[]> => {
  const params = new URLSearchParams();
  if (options?.limit !== undefined) params.set("limit", String(options.limit));
  if (options?.offset !== undefined) params.set("offset", String(options.offset));
  const qs = params.toString();
  return apiFetch<EmailMarketingCampaign[]>(`/api/v1/email-marketing/campaigns${qs ? `?${qs}` : ""}`);
};

export const createMarketingCampaign = (input: CreateCampaignInput): Promise<EmailMarketingCampaign> =>
  apiFetch<EmailMarketingCampaign>("/api/v1/email-marketing/campaigns", {
    method: "POST",
    body: JSON.stringify(input),
  });

export const getMarketingCampaign = (campaignId: number): Promise<EmailMarketingCampaign> =>
  apiFetch<EmailMarketingCampaign>(`/api/v1/email-marketing/campaigns/${campaignId}`);

export const updateMarketingCampaign = (campaignId: number, input: UpdateCampaignInput): Promise<EmailMarketingCampaign> =>
  apiFetch<EmailMarketingCampaign>(`/api/v1/email-marketing/campaigns/${campaignId}`, {
    method: "PUT",
    body: JSON.stringify(input),
  });

export const previewMarketingCampaign = (campaignId: number): Promise<CampaignPreview> =>
  apiFetch<CampaignPreview>(`/api/v1/email-marketing/campaigns/${campaignId}/preview`, {
    method: "POST",
  });

export const testSendMarketingCampaign = (campaignId: number, testEmails: string[]): Promise<MessageResponse> =>
  apiFetch<MessageResponse>(`/api/v1/email-marketing/campaigns/${campaignId}/test-send`, {
    method: "POST",
    body: JSON.stringify({ test_emails: testEmails }),
  });

export const launchMarketingCampaign = (campaignId: number, confirmed: boolean): Promise<EmailMarketingCampaign> =>
  apiFetch<EmailMarketingCampaign>(`/api/v1/email-marketing/campaigns/${campaignId}/launch`, {
    method: "POST",
    body: JSON.stringify({ confirmed }),
  });

export const getCampaignAnalytics = (campaignId: number): Promise<CampaignAnalytics> =>
  apiFetch<CampaignAnalytics>(`/api/v1/email-marketing/campaigns/${campaignId}/analytics`);

// Channel Connectors API (Phase 5 & Phase 2)
export interface ChannelCapabilities {
  provider: string;
  connector_type: "advertiser_management" | "publisher_delivery";
  supports_inventory_injection: boolean;
  supports_account_discovery: boolean;
  supports_campaign_management: boolean;
  supports_reporting_sync: boolean;
  supports_live_delivery?: boolean;
  supports_oauth?: boolean;
  supports_reporting?: boolean;
  adapter_mode?: "LIVE" | "SIMULATED" | "EXPERIMENTAL";
  is_experimental?: boolean;
  is_simulated?: boolean;
  supported_objectives: string[];
  supported_bidding_strategies: string[];
  supported_targeting: string[];
  supported_creative_types: string[];
  limitations: string[];
  auth_requirements: string[];
}

export interface ConnectAdvertiserInput {
  provider: string;
  name: string;
  api_key: string;
  account_id: string;
  organization_id?: string;
  additional_credentials?: Record<string, any>;
}

export interface ChannelConnection {
  id: number;
  provider: string;
  name: string;
  external_account_id: string;
  external_organization_id?: string | null;
  credentials_prefix: string;
  status: string;
  adapter_mode?: string;
  token_state?: string | null;
  last_verified_at?: string | null;
  last_sync_at?: string | null;
  sync_cursor?: string | null;
  last_error?: string | null;
  capabilities?: ChannelCapabilities | null;
  created_at: string;
}

export interface ExternalAccount {
  account_id: string;
  name: string;
  currency: string;
  timezone: string;
  organization_id?: string | null;
  status: string;
}

export interface ExternalCampaign {
  external_id: string;
  name: string;
  status: string;
  objective?: string | null;
  daily_budget?: number | null;
  total_spend?: number | null;
  currency: string;
  local_campaign_id?: number | null;
  remote_object_status?: string | null;
  moderation_status?: string | null;
  provider_request_id?: string | null;
  remote_resource_name?: string | null;
  sync_status?: string;
}

export interface CampaignCreateParams {
  name: string;
  objective: string;
  daily_budget: number;
  bid_amount?: number;
  targeting?: Record<string, any>;
  confirmed: boolean;
}

export interface CampaignPreviewResult {
  name: string;
  objective: string;
  daily_budget: number;
  currency: string;
  bid_amount?: number | null;
  targeting_summary: Record<string, any>;
  estimated_daily_impressions: [number, number];
  estimated_daily_clicks: [number, number];
  disclaimer: string;
  requires_confirmation: boolean;
}

export interface ExternalReportRow {
  date: string;
  external_campaign_id: string;
  impressions: number;
  clicks: number;
  conversions: number;
  spend: number;
  currency: string;
  is_synced?: boolean;
  provider_reported_at?: string | null;
  is_simulated?: boolean;
}

export const getChannelCapabilities = (provider: string): Promise<ChannelCapabilities> =>
  apiFetch<ChannelCapabilities>(`/api/v1/channels/capabilities/${encodeURIComponent(provider)}`);

export const connectAdvertiserChannel = (input: ConnectAdvertiserInput): Promise<ChannelConnection> =>
  apiFetch<ChannelConnection>("/api/v1/channels/advertiser/connect", {
    method: "POST",
    body: JSON.stringify(input),
  });

export const listAdvertiserChannels = (): Promise<ChannelConnection[]> =>
  apiFetch<ChannelConnection[]>("/api/v1/channels/advertiser/connections");

export const getAdvertiserChannel = (connectionId: number): Promise<ChannelConnection> =>
  apiFetch<ChannelConnection>(`/api/v1/channels/advertiser/connections/${connectionId}`);

export const discoverChannelAccounts = (connectionId: number): Promise<ExternalAccount[]> =>
  apiFetch<ExternalAccount[]>(`/api/v1/channels/advertiser/connections/${connectionId}/accounts`);

export const syncChannelCampaigns = (connectionId: number): Promise<ExternalCampaign[]> =>
  apiFetch<ExternalCampaign[]>(`/api/v1/channels/advertiser/connections/${connectionId}/sync`, {
    method: "POST",
  });

export const previewChannelCampaign = (
  connectionId: number,
  params: CampaignCreateParams
): Promise<CampaignPreviewResult> =>
  apiFetch<CampaignPreviewResult>(`/api/v1/channels/advertiser/connections/${connectionId}/preview`, {
    method: "POST",
    body: JSON.stringify(params),
  });

export const createChannelCampaign = (
  connectionId: number,
  params: CampaignCreateParams,
  idempotencyKey: string
): Promise<ExternalCampaign> =>
  apiFetch<ExternalCampaign>(`/api/v1/channels/advertiser/connections/${connectionId}/campaigns`, {
    method: "POST",
    headers: { "X-Idempotency-Key": idempotencyKey },
    body: JSON.stringify(params),
  });

export const updateChannelCampaignStatus = (
  connectionId: number,
  externalCampaignId: string,
  status: "ACTIVE" | "PAUSED" | "ARCHIVED"
): Promise<{ success: boolean; external_campaign_id: string; status: string }> =>
  apiFetch(`/api/v1/channels/advertiser/connections/${connectionId}/campaigns/${encodeURIComponent(externalCampaignId)}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });

export const syncChannelReporting = (
  connectionId: number,
  startDate: string,
  endDate: string
): Promise<ExternalReportRow[]> =>
  apiFetch<ExternalReportRow[]>(
    `/api/v1/channels/advertiser/connections/${connectionId}/reporting?start_date=${encodeURIComponent(startDate)}&end_date=${encodeURIComponent(endDate)}`
  );

export const disconnectChannel = (connectionId: number): Promise<{ success: boolean; connection_id: number; status: string }> =>
  apiFetch(`/api/v1/channels/advertiser/connections/${connectionId}`, {
    method: "DELETE",
  });

// ── Phase 6: Business Profile Questionnaire, Custom Domain, & Audits ──

export const submitBusinessIntake = (
  projectId: number,
  intake: BusinessProfileIntake,
  generateDraft: boolean = true
): Promise<{ project_id: number; audit: BusinessProfileAudit; page_count: number; message: string }> =>
  apiFetch(`/api/v1/website/projects/${projectId}/business-intake?generate_draft=${generateDraft}`, {
    method: "POST",
    body: JSON.stringify(intake),
  });

export const getBusinessProfile = (
  projectId: number
): Promise<{ project_id: number; business_profile: BusinessProfileIntake | null; audit: BusinessProfileAudit | null }> =>
  apiFetch(`/api/v1/website/projects/${projectId}/business-profile`);

export const getAccessibilityAudit = (
  projectId: number,
  pageId?: number
): Promise<{
  project_id: number;
  overall_score: number;
  page_count: number;
  pages: Array<{ page_id: number; page_title: string; slug: string; report: AccessibilityAuditReport }>;
  consolidated_recommendations: string[];
}> =>
  apiFetch(`/api/v1/website/projects/${projectId}/accessibility-audit${pageId ? `?page_id=${pageId}` : ""}`);

export const getGeoReadiness = (projectId: number): Promise<GeoReadinessReport> =>
  apiFetch<GeoReadinessReport>(`/api/v1/website/projects/${projectId}/geo-readiness`);

export const configureCustomDomain = (
  projectId: number,
  customDomain: string
): Promise<CustomDomainStatusResponse> =>
  apiFetch<CustomDomainStatusResponse>(`/api/v1/website/projects/${projectId}/custom-domain`, {
    method: "POST",
    body: JSON.stringify({ custom_domain: customDomain }),
  });

export const verifyCustomDomain = (projectId: number): Promise<CustomDomainStatusResponse> =>
  apiFetch<CustomDomainStatusResponse>(`/api/v1/website/projects/${projectId}/custom-domain/verify`, {
    method: "POST",
  });

export const removeCustomDomain = (projectId: number): Promise<{ success: boolean; message: string }> =>
  apiFetch(`/api/v1/website/projects/${projectId}/custom-domain`, {
    method: "DELETE",
  });

export const getWebmasterTools = (projectId: number): Promise<WebmasterToolsConfig> =>
  apiFetch<WebmasterToolsConfig>(`/api/v1/website/projects/${projectId}/webmaster-tools`);

export const updateWebmasterTools = (
  projectId: number,
  config: WebmasterToolsConfig
): Promise<WebmasterToolsConfig> =>
  apiFetch<WebmasterToolsConfig>(`/api/v1/website/projects/${projectId}/webmaster-tools`, {
    method: "PUT",
    body: JSON.stringify(config),
  });

// ── Phase 7 Operations, Exceptions, Safety & Audit ────────────────────────────

export const getOperationalExceptions = (): Promise<OperationalExceptionsResponse> =>
  apiFetch<OperationalExceptionsResponse>("/api/v1/admin/dashboard/operational-exceptions");

export const getAdminAuditLogs = (params?: {
  action?: string;
  user_id?: number;
  limit?: number;
  offset?: number;
}): Promise<AuditLogItem[]> => {
  const query = new URLSearchParams();
  if (params?.action) query.set("action", params.action);
  if (params?.user_id) query.set("user_id", String(params.user_id));
  if (params?.limit) query.set("limit", String(params.limit));
  if (params?.offset) query.set("offset", String(params.offset));
  const qs = query.toString();
  return apiFetch<AuditLogItem[]>(`/api/v1/admin/audit-logs${qs ? `?${qs}` : ""}`);
};

export const getPublisherSafetySummary = (publisherId: number): Promise<PublisherSafetySummary> =>
  apiFetch<PublisherSafetySummary>(`/api/v1/publisher/${publisherId}/safety-summary`);

export const getPublisherEarningsBreakdown = (publisherId: number): Promise<PublisherEarningsBreakdown> =>
  apiFetch<PublisherEarningsBreakdown>(`/api/v1/publisher/${publisherId}/earnings-breakdown`);

export const resubmitCreative = (creativeId: number): Promise<Creative> =>
  apiFetch<Creative>(`/api/v1/moderation/creatives/${creativeId}/resubmit`, {
    method: "POST",
  });


