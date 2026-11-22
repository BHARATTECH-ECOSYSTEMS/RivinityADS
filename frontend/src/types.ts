export type Role = "admin" | "advertiser" | "publisher";
export interface User { id: number; name: string; email: string; role: Role; advertiser_id?: number | null; publisher_id?: number | null; email_verified: boolean; }
export interface Session { user: User; token: string; }
export interface MessageResponse { message: string; }
export interface VerifyEmailResponse extends MessageResponse { email?: string | null; }
export interface VerifyEmailInput { token: string; }
export interface ResendVerificationInput { email: string; }
export interface RequestPasswordResetInput { email: string; }
export interface ResetPasswordInput { token: string; password: string; }
export const BID_TYPES: Record<number, string> = { 1: "CPM", 2: "CPC", 3: "CPA" };
export const CREATIVE_TYPES: Record<number, string> = {
  1: "banner",
  2: "native",
  3: "video",
  4: "interstitial",
  5: "display_card",
  6: "label_ad",
  7: "element_ad",
};

export const AD_CATEGORIES = {
  AI_CHATBOT: "AI and Chatbot Ads",
  BANNER: "Banner Ads",
} as const;

export const AI_CHATBOT_FORMATS = {
  DISPLAY_CARD: { id: "display_card", label: "Display Card", width: 520, height: 120, typeId: 5 },
  LABEL_ADS: { id: "label_ad", label: "Label Ads", width: 400, height: 100, typeId: 6 },
  ELEMENT_ADS: { id: "element_ad", label: "Element Ads", width: 310, height: 150, typeId: 7 },
} as const;

export const BANNER_FORMAT = {
  id: "banner",
  label: "Banner Ad (500 × 500)",
  width: 500,
  height: 500,
  typeId: 1,
} as const;

export const CREATIVE_FORMAT_LABELS: Record<number, string> = {
  1: "Banner Ad (500 × 500)",
  2: "Native Ad",
  3: "Video Ad",
  4: "Interstitial Ad",
  5: "Display Card (520 × 120)",
  6: "Label Ads (400 × 100)",
  7: "Element Ads (310 × 150)",
};
export const STATUS: Record<number, string> = { 0: "paused", 1: "active" };
export interface DeviceInfo { os: string; os_version?: string; model?: string; brand?: string; screen_width?: number; screen_height?: number; language?: string; }
export interface GeoInfo { ip?: string; country?: string; region?: string; state?: string; city?: string; pincode?: string; latitude?: number; longitude?: number; }
export interface UserContextPayload { id?: string; sector_context?: string[]; geo?: GeoInfo; }
export interface AdRequestPayload { slot_id: string; user_id?: string; user?: UserContextPayload; device?: DeviceInfo; geo?: GeoInfo; num_ads?: number; format?: number; }
export interface CreativeResponse { title?: string; description?: string; image_url?: string; video_url?: string; landing_url: string; width?: number; height?: number; creative_type: string; }
export interface TrackingUrls { impression_url: string; click_url: string; conversion_url?: string; }
export interface AdResponse { ad_id: string; campaign_id: number; creative_id: number; ad_set_id?: number | null; creative: CreativeResponse; tracking: TrackingUrls; sponsored: boolean; disclosure_label: string; metadata?: { ecpm?: number; pctr?: number } | null; }
export interface AdListResponse { request_id: string; ads: AdResponse[]; count: number; }
export interface HealthResponse { status: string; version: string; database: boolean; redis: boolean; }
export interface Advertiser { id: number; name: string; company?: string; contact_email?: string; balance: number; daily_budget: number; status: number; }
export interface Campaign { id: number; advertiser_id: number; name: string; description?: string; budget_daily: number; budget_total: number; spent_today: number; spent_total: number; bid_type: number; bid_amount: number; freq_cap_daily: number; freq_cap_hourly: number; start_time?: string; end_time?: string; status: number; autopilot: boolean; impressions: number; clicks: number; conversions: number; }
export interface AdSet { id: number; campaign_id: number; name: string; status: number; daily_budget?: number | null; bid_type: number; bid_amount: number; start_time?: string | null; end_time?: string | null; freq_cap_daily?: number | null; freq_cap_hourly?: number | null; delivery_mode: string; targeting_summary?: Record<string, any> | null; created_at: string; updated_at: string; }
export interface AdSetCreateInput { name: string; status?: number; daily_budget?: number | null; bid_type?: number; bid_amount?: number; start_time?: string | null; end_time?: string | null; freq_cap_daily?: number | null; freq_cap_hourly?: number | null; delivery_mode?: string; targeting_summary?: Record<string, any> | null; }
export interface AdSetUpdateInput { name?: string; status?: number; daily_budget?: number | null; bid_type?: number; bid_amount?: number; start_time?: string | null; end_time?: string | null; freq_cap_daily?: number | null; freq_cap_hourly?: number | null; delivery_mode?: string; targeting_summary?: Record<string, any> | null; }
import type { GeoEntry } from "./api/client";

export interface AdSetTargetingResponse { ad_set_id: number; campaign_id: number; geo_include?: GeoEntry[]; geo_exclude?: GeoEntry[]; sector_include?: string[]; sector_exclude?: string[]; }
export interface AdSetReportingItem { ad_set_id: number; ad_set_name: string; status: number; impressions: number; clicks: number; conversions: number; spend: number; ctr: number; cvr: number; }
export interface AdSetReportingResponse { campaign_id: number; start_date: string; end_date: string; ad_sets: AdSetReportingItem[]; total_impressions: number; total_clicks: number; total_conversions: number; total_spend: number; }
export interface Creative { id: number; campaign_id: number; ad_set_id?: number | null; title: string; description?: string; image_url?: string; video_url?: string; landing_url: string; creative_type: number; category?: string | null; format?: string | null; width: number; height: number; status: number; quality_score: number; moderation_status?: number; moderation_reason?: string | null; }
export interface Publisher { id: number; name: string; email?: string; site_url?: string; status: number; is_sandbox: boolean; }
export interface AdSlot {
  id: number;
  publisher_id: number;
  property_id?: number | null;
  slot_id: string;
  name: string;
  format: number;
  width: number;
  height: number;
  status: number;
  floor_price?: number;
  is_chat_surface: boolean;
  ecpm_today?: number;
  created_at?: string;
  updated_at?: string;
}

// ── Publisher Properties & Domain Verification ─────────────────────────────

export type PropertyVerificationStatus = "PENDING" | "VERIFIED" | "FAILED";
export type PropertyVerificationMethod = "DNS_TXT" | "HTML_META" | "PLATFORM_SUBDOMAIN";

export interface PublisherProperty {
  id: number;
  publisher_id: number;
  name: string;
  domain: string;
  status: number;
  verification_status: PropertyVerificationStatus;
  verification_method: PropertyVerificationMethod;
  verification_token?: string | null;
  challenge_expires_at?: string | null;
  verified_at?: string | null;
  last_error?: string | null;
  is_platform_subdomain: boolean;
  slot_count?: number;
  last_request_at?: string | null;
  last_impression_at?: string | null;
  health_warnings?: string[];
  created_at: string;
  updated_at: string;
}

export interface PublisherPropertyInput {
  name: string;
  domain: string;
  status?: number;
}

export interface PropertyChallengeRequest {
  method?: "DNS_TXT" | "HTML_META";
}

export interface PropertyChallengeResponse {
  property_id: number;
  domain: string;
  verification_method: PropertyVerificationMethod;
  verification_token: string;
  dns_record_name: string;
  dns_record_value: string;
  html_meta_tag: string;
  challenge_expires_at: string;
  instructions: string;
}
export interface ReadinessIssue {
  code: string;
  category: "blocker" | "warning";
  entity_type: "campaign" | "ad_set" | "creative" | "advertiser";
  entity_id?: number | null;
  entity_name?: string | null;
  field?: string | null;
  message: string;
}

export interface CampaignReadinessResponse {
  campaign_id: number;
  campaign_name: string;
  status: number;
  status_name: string;
  is_ready: boolean;
  blockers: ReadinessIssue[];
  warnings: ReadinessIssue[];
  summary: {
    wallet_balance: number;
    budget_daily: number;
    budget_total: number;
    bid_type: number;
    bid_amount: number;
    freq_cap_daily: number;
    freq_cap_hourly: number;
    start_time?: string | null;
    end_time?: string | null;
    total_ad_sets: number;
    active_ad_sets: number;
    total_creatives: number;
    active_creatives: number;
    approved_creatives: number;
    unapproved_creatives: number;
    verified_creatives: number;
    unverified_creatives: number;
    has_targeting: boolean;
    ad_sets: Array<{
      id: number;
      name: string;
      status: number;
      daily_budget: number | null;
      bid_amount: number;
      ads_count: number;
      active_ads_count: number;
      has_targeting: boolean;
    }>;
  };
}

export interface SuggestionAction {
  action: string;
  confidence?: number;
  proposed_budget_delta?: number;
  operation_type?: string;
  target_payload?: any;
}
export interface Suggestion {
  id: number;
  campaign_id: number;
  action: SuggestionAction;
  status: string;
  created_at: string;
  updated_at: string;
}

// Chat API Types
export interface ChatSessionResponse {
  session_id: string;
}

export interface ChatResponse {
  session_id: string;
  assistant_message: string;
  intent: string;
  tool_status: string | null;
  payload: Record<string, unknown> | null;
  current_campaign_id?: number | null;
  current_ad_set_id?: number | null;
  current_step?: string | null;
}

// Notification Types (Phase 6)
export type NotificationChannel = "in_app" | "email";
export type NotificationSeverity = "info" | "warning" | "error" | "success";
export type NotificationCategory =
  | "billing"
  | "campaign"
  | "creative"
  | "optimization"
  | "domain"
  | "security"
  | "publisher"
  | "system"
  | "webhook";

export interface NotificationItem {
  id: number;
  category: NotificationCategory | string;
  type: string;
  channel: string;
  severity: NotificationSeverity | string;
  title: string;
  message: string;
  link: string | null;
  resource_type: string | null;
  resource_id: string | null;
  is_read: boolean;
  read_at: string | null;
  created_at: string;
}

export interface NotificationListResponse {
  items: NotificationItem[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
  unread_count: number;
}

export interface UnreadCountResponse {
  unread_count: number;
}

export interface MarkAllReadResponse {
  updated_count: number;
}

export interface NotificationPreferenceItem {
  channel: NotificationChannel | string;
  category: NotificationCategory | string;
  is_enabled: boolean;
}

export type NotificationResponse = NotificationItem;

export interface NotificationPreferenceUpdateRequest {
  preferences: NotificationPreferenceItem[];
}

export interface NotificationPreferenceListResponse {
  preferences: NotificationPreferenceItem[];
}


export interface ThemeConfig {
  primary_color: string;
  secondary_color: string;
  background_color: string;
  text_color: string;
  font_family: string;
  logo_url?: string | null;
  favicon_url?: string | null;
  border_radius: string;
}

export type BlockType =
  | "hero"
  | "text_section"
  | "features_grid"
  | "image_gallery"
  | "contact_form"
  | "cta_banner"
  | "ad_placement"
  | "footer"
  | "open_design_section";

export interface BlockItem {
  id: string;
  type: BlockType;
  is_visible: boolean;
  content: Record<string, any>;
}

export interface WebsiteTemplateSummary {
  id: string;
  name: string;
  description: string;
  category: string;
  thumbnail_url?: string | null;
  badge?: string | null;
  default_theme_config: ThemeConfig;
  is_active: boolean;
  order_index: number;
}

export interface WebsiteTemplateDetail extends WebsiteTemplateSummary {
  default_pages: Array<{
    title: string;
    slug: string;
    is_home: boolean;
    order_index: number;
    seo_meta?: SeoMeta;
    blocks: {
      schema_version: number;
      blocks: BlockItem[];
    };
  }>;
}

export interface SeoMeta {
  meta_title?: string | null;
  meta_description?: string | null;
  og_image_url?: string | null;
  noindex?: boolean;
}

export interface PageSummary {
  id: number;
  project_id: number;
  title: string;
  slug: string;
  is_home: boolean;
  order_index: number;
  language?: string;
  translation_group_id?: string | null;
  updated_at: string;
}

export interface PageData extends PageSummary {
  seo_meta: SeoMeta;
  blocks: {
    schema_version: number;
    blocks: BlockItem[];
  };
  created_at: string;
}

export type WebsiteStatusCode = 0 | 1 | 2 | 3;

export const WebsiteStatusMap: Record<number, string> = {
  0: "draft",
  1: "published",
  2: "archived",
  3: "suspended",
};

export interface ProjectSummary {
  id: number;
  owner_user_id: number;
  publisher_id?: number | null;
  name: string;
  slug: string;
  custom_domain?: string | null;
  custom_domain_status?: number;
  template_id: string;
  status: number; // 0=draft, 1=published, 2=archived, 3=suspended
  monetization_status: number;
  is_deleted: boolean;
  current_published_version_id?: number | null;
  published_url?: string;
  created_at: string;
  updated_at: string;
  page_count: number;
}

export interface ProjectData extends ProjectSummary {
  property_id: number | null;
  theme_config: ThemeConfig;
  business_profile?: BusinessProfileIntake | null;
  webmaster_tools?: WebmasterToolsConfig;
  primary_language?: string;
  pages: PageSummary[];
}

// ── Phase 6: Business Profile Questionnaire, Custom Domain & Audits ──────────

export interface NAPDetails {
  phone?: string | null;
  email?: string | null;
  street_address?: string | null;
  city?: string | null;
  state_region?: string | null;
  postal_code?: string | null;
  country?: string;
  latitude?: number | null;
  longitude?: number | null;
}

export interface DayHours {
  day: "monday" | "tuesday" | "wednesday" | "thursday" | "friday" | "saturday" | "sunday";
  opens?: string | null;
  closes?: string | null;
  is_closed: boolean;
}

export interface ServiceProductItem {
  name: string;
  description?: string | null;
  price?: number | null;
  currency?: string;
  category?: string | null;
  image_url?: string | null;
}

export interface FAQItem {
  question: string;
  answer: string;
}

export interface BusinessProfileIntake {
  business_name: string;
  legal_name?: string | null;
  category: string;
  tagline?: string | null;
  description?: string | null;
  nap?: NAPDetails;
  service_area_cities?: string[];
  service_radius_km?: number | null;
  hours?: DayHours[];
  services_or_products?: ServiceProductItem[];
  languages?: string[];
  logo_url?: string | null;
  cover_image_url?: string | null;
  whatsapp_number?: string | null;
  google_maps_url?: string | null;
  social_links?: Record<string, string>;
  faqs?: FAQItem[];
  factual_declaration?: boolean;
  ad_monetization_opt_in?: boolean;
  ai_search_discovery_opt_in?: boolean;
}

export interface BusinessProfileAudit {
  is_complete: boolean;
  completeness_score: number;
  missing_fields: string[];
  supplied_fields: string[];
  factual_assertions_count: number;
  fabricated_claims_detected: string[];
  schema_org_type: string;
}

export interface CustomDomainStatusResponse {
  project_id: number;
  custom_domain: string | null;
  status: number;
  status_label: string;
  verification_token: string | null;
  dns_record_name: string | null;
  expected_txt_value: string | null;
  verified_at: string | null;
  instructions: string;
}

export interface WebmasterToolsConfig {
  google_site_verification?: string | null;
  bing_site_verification?: string | null;
  indexnow_key?: string | null;
}

export interface AccessibilityAuditReport {
  score: number;
  all_images_have_alt: boolean;
  missing_alt_images: string[];
  heading_hierarchy_valid: boolean;
  heading_issues: string[];
  contrast_ratio: number;
  contrast_passed: boolean;
  has_responsive_viewport: boolean;
  lazy_loading_applied: boolean;
  font_display_swap: boolean;
  recommendations: string[];
}

export interface GeoReadinessReport {
  overall_score: number;
  nap_completeness: boolean;
  nap_score: number;
  structured_data_valid: boolean;
  schema_type: string;
  factual_services_count: number;
  verified_hours_present: boolean;
  llms_txt_available: boolean;
  disclaimer: string;
}

export interface VersionSummary {
  id: number;
  project_id: number;
  version_number: number;
  published_by_user_id: number;
  notes?: string | null;
  published_at: string;
  rollback_from_version_id?: number | null;
}

export interface PreviewTokenResponse {
  preview_token: string;
  preview_url: string;
  expires_in: number;
}

export interface SlugCheckResponse {
  slug: string;
  available: boolean;
  reason?: string | null;
}

// ── Website Monetization & Ad Placement (Phase 5) ───────────────────────────

export type AdPlacementLocation = "header_banner" | "in_article" | "sidebar" | "footer";

export interface AdPlacementContent {
  placement_location: AdPlacementLocation;
  format: number; // 1=banner, 2=native
  ad_slot_id: string | null;
}

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

export interface BoundSlot {
  id: number;
  slot_id: string;
  name: string;
  format: number;
  width: number;
  height: number;
  status: number;
  floor_price: number;
  property_id: number | null;
  is_chat_surface: boolean;
}

export interface MonetizationPrerequisites {
  project_active: boolean;
  publisher_active: boolean;
  payout_configured: boolean;
  property_verified: boolean;
  slots_authorized: boolean;
  consent_recorded: boolean;
}

export interface MonetizationConsentScope {
  ad_delivery?: boolean;
  revenue_share?: boolean | string;
  active?: boolean;
}

export interface MonetizationConsentRecord {
  accepted_terms: boolean;
  version: string;
  scope: MonetizationConsentScope;
  agreed_at: string;
  user_id: number;
}

export interface ProjectMonetizationStatus {
  project_id: number;
  is_eligible: boolean;
  current_status: WebsiteMonetizationStatusCode;
  prerequisites: MonetizationPrerequisites;
  failure_reasons: string[];
  slot_count: number;
  consent: MonetizationConsentRecord | null;
}

export interface PublisherLinkInput {
  publisher_id: number;
  property_id: number;
}

export interface MonetizationConsentInput {
  accepted_terms: boolean;
  version?: string;
  scope?: MonetizationConsentScope;
}

export interface UnpublishProjectResponse {
  message: string;
  status: number;
  current_published_version_id: number | null;
}

// ── Email Marketing (Phase 6.1E) ─────────────────────────────────────────────

export interface EmailMarketingList {
  id: number;
  tenant_type: string;
  tenant_id: number;
  name: string;
  description?: string | null;
  type: string;
  optin: string;
  contact_count: number;
  created_at: string;
}

export interface CreateMailingListInput {
  name: string;
  description?: string;
  type?: 'public' | 'private';
  optin?: 'single' | 'double';
}

export interface EmailMarketingContact {
  id: number;
  list_id: number;
  email: string;
  name?: string | null;
  status: string;
  consent_source: string;
  consent_at?: string | null;
  created_at: string;
}

export interface CreateContactInput {
  email: string;
  name?: string;
  consent_source: string;
}

export interface ImportContactsInput {
  csv_content: string;
  permission_source: string;
}

export interface ImportContactsResult {
  total_rows: number;
  valid_count: number;
  duplicate_count: number;
  error_count: number;
  errors: string[];
}

export interface EmailMarketingCampaign {
  id: number;
  list_id: number;
  name: string;
  subject: string;
  body: string;
  content_type: string;
  status: string;
  total_recipients: number;
  sent_at?: string | null;
  created_at: string;
}

export interface CreateCampaignInput {
  list_id: number;
  name: string;
  subject: string;
  body: string;
  content_type?: 'html' | 'markdown' | 'plain';
}

export interface UpdateCampaignInput {
  name?: string;
  subject?: string;
  body?: string;
  content_type?: 'html' | 'markdown' | 'plain';
}

export interface CampaignPreview {
  id: number;
  name: string;
  subject: string;
  rendered_body: string;
  eligible_recipient_count: number;
  has_unsubscribe_notice: boolean;
}

export interface CampaignAnalytics {
  campaign_id: number;
  status: string;
  total_recipients: number;
  views: number;
  clicks: number;
  unsubscribes: number;
  bounces: number;
}

// ── Open Design AI Generation (Phase 4D) ────────────────────────────────────

export type AISectionPreset =
  | "hero"
  | "features"
  | "testimonials"
  | "pricing"
  | "cta"
  | "footer"
  | "faq"
  | "gallery"
  | "stats"
  | "contact"
  | "about"
  | "custom";

export interface WebsiteAIGenerateSectionInput {
  prompt: string;
  section_type?: AISectionPreset | string;
}

export interface WebsiteAIGenerateSectionResponse {
  block: BlockItem;
  section_id: string;
  section_type: string;
  quota_remaining: number;
  daily_limit: number;
  reset_timestamp_utc: string;
}

// ── Phase 7 Operations, Exceptions, SLA & Audit Types ────────────────────────

export interface SLAItem {
  id: number | string;
  type: string;
  title: string;
  entity_name?: string;
  created_at: string;
  sla_deadline: string;
  sla_status: "ON_TRACK" | "AT_RISK" | "BREACHED";
  time_remaining_minutes: number;
  details?: Record<string, any>;
}

export interface OperationalAlert {
  code: string;
  severity: "high" | "medium" | "low";
  title: string;
  description: string;
  entity_type: string;
  entity_id?: number | string;
  count: number;
}

export interface OperationalExceptionsResponse {
  stale_integrations: OperationalAlert[];
  no_fill_slots: OperationalAlert[];
  unusual_ctrs: OperationalAlert[];
  spend_spikes: OperationalAlert[];
  webhook_payout_failures: OperationalAlert[];
  moderation_queue: SLAItem[];
  finance_queue: SLAItem[];
  total_exceptions_count: number;
}

export interface AuditLogItem {
  id: number;
  user_id?: number;
  action: string;
  ip_address?: string;
  details?: string;
  created_at: string;
}

export interface PublisherSafetySummary {
  publisher_id: number;
  quality_score: number;
  total_events: number;
  valid_events: number;
  invalid_events: number;
  invalid_rate_pct: number;
  warning_categories: Array<{
    title: string;
    description: string;
    count: number;
    severity: string;
  }>;
  recommendations: string[];
}

export interface PublisherEarningsBreakdown {
  publisher_id: number;
  currency: string;
  estimated: number | string;
  under_review: number | string;
  finalised: number | string;
  adjusted: number | string;
  payable: number | string;
  paid: number | string;
}

declare global {
  interface Window {
    Razorpay?: new (options: {
      key: string;
      amount: string | number;
      currency: string;
      order_id: string;
      name?: string;
      description?: string;
      handler: (response: any) => void;
      modal?: {
        ondismiss: () => void;
      };
    }) => {
      open: () => void;
      on?: (event: string, callback: (response: any) => void) => void;
    };
  }
}
