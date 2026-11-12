import React, { useState } from "react";
import {
  BusinessProfileIntake,
  BusinessProfileAudit,
  DayHours,
  ServiceProductItem,
  FAQItem,
} from "../../types";
import { submitBusinessIntake } from "../../api/client";

interface BusinessQuestionnaireModalProps {
  isOpen: boolean;
  projectId: number;
  projectName: string;
  onClose: () => void;
  onComplete: (audit: BusinessProfileAudit, pageCount: number) => void;
}

const CATEGORY_OPTIONS = [
  { value: "local_service", label: "Local Services (General)" },
  { value: "restaurant", label: "Restaurant / Dining" },
  { value: "cafe", label: "Cafe / Bakery" },
  { value: "retail_store", label: "Retail / Shop" },
  { value: "grocery", label: "Grocery / Supermarket" },
  { value: "health_clinic", label: "Medical / Health Clinic" },
  { value: "dental_clinic", label: "Dental Clinic" },
  { value: "pharmacy", label: "Pharmacy / Chemist" },
  { value: "plumber", label: "Plumbing Services" },
  { value: "electrician", label: "Electrical Services" },
  { value: "contractor", label: "Construction & Contractor" },
  { value: "auto_repair", label: "Automotive & Vehicle Repair" },
  { value: "salon_spa", label: "Salon & Spa" },
  { value: "gym_fitness", label: "Gym & Fitness Studio" },
  { value: "coaching_education", label: "Coaching & Education" },
  { value: "legal_accounting", label: "Legal & Accounting Services" },
  { value: "real_estate", label: "Real Estate & Housing" },
  { value: "tech_agency", label: "Technology & Digital Agency" },
];

const DEFAULT_DAYS = [
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
  "sunday",
] as const;

export const BusinessQuestionnaireModal: React.FC<BusinessQuestionnaireModalProps> = ({
  isOpen,
  projectId,
  projectName,
  onClose,
  onComplete,
}) => {
  const [step, setStep] = useState<number>(1);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Form State
  const [businessName, setBusinessName] = useState(projectName);
  const [legalName, setLegalName] = useState("");
  const [category, setCategory] = useState("local_service");
  const [tagline, setTagline] = useState("");
  const [description, setDescription] = useState("");
  const [primaryLang, setPrimaryLang] = useState("en");
  const [includeHindi, setIncludeHindi] = useState(false);

  // NAP
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [streetAddress, setStreetAddress] = useState("");
  const [city, setCity] = useState("");
  const [stateRegion, setStateRegion] = useState("");
  const [postalCode, setPostalCode] = useState("");
  const [country, setCountry] = useState("IN");
  const [whatsappNumber, setWhatsappNumber] = useState("");
  const [serviceCities, setServiceCities] = useState("");

  // Hours
  const [hours, setHours] = useState<DayHours[]>(
    DEFAULT_DAYS.map((d) => ({
      day: d,
      opens: d === "sunday" ? "" : "09:00",
      closes: d === "sunday" ? "" : "20:00",
      is_closed: d === "sunday",
    }))
  );

  // Services / Products
  const [services, setServices] = useState<ServiceProductItem[]>([
    { name: "", description: "", price: undefined, currency: "INR" },
  ]);

  // FAQs
  const [faqs, setFaqs] = useState<FAQItem[]>([]);

  // Consent
  const [factualDeclaration, setFactualDeclaration] = useState(true);
  const [adMonetizationOptIn, setAdMonetizationOptIn] = useState(false);

  if (!isOpen) return null;

  const handleHourChange = (
    index: number,
    field: keyof DayHours,
    value: any
  ) => {
    const updated = [...hours];
    updated[index] = { ...updated[index], [field]: value };
    setHours(updated);
  };

  const handleAddService = () => {
    setServices([...services, { name: "", description: "", price: undefined, currency: "INR" }]);
  };

  const handleServiceChange = (index: number, field: keyof ServiceProductItem, value: any) => {
    const updated = [...services];
    updated[index] = { ...updated[index], [field]: value };
    setServices(updated);
  };

  const handleRemoveService = (index: number) => {
    setServices(services.filter((_, i) => i !== index));
  };

  const handleAddFaq = () => {
    setFaqs([...faqs, { question: "", answer: "" }]);
  };

  const handleFaqChange = (index: number, field: keyof FAQItem, value: string) => {
    const updated = [...faqs];
    updated[index] = { ...updated[index], [field]: value };
    setFaqs(updated);
  };

  const handleRemoveFaq = (index: number) => {
    setFaqs(faqs.filter((_, i) => i !== index));
  };

  const handleSubmit = async () => {
    setError(null);
    if (!businessName.trim()) {
      setError("Business Name is required.");
      return;
    }
    if (!factualDeclaration) {
      setError("You must certify that all provided details are factual and accurate.");
      return;
    }

    setIsSubmitting(true);

    const languages = [primaryLang];
    if (includeHindi && !languages.includes("hi")) {
      languages.push("hi");
    }

    const cleanedCities = serviceCities
      .split(",")
      .map((c) => c.trim())
      .filter((c) => c.length > 0);

    const validServices = services
      .filter((s) => s.name.trim().length > 0)
      .map((s) => ({
        ...s,
        price: s.price !== undefined && s.price !== null && !isNaN(Number(s.price)) ? Number(s.price) : undefined,
      }));

    const validFaqs = faqs.filter(
      (f) => f.question.trim().length > 0 && f.answer.trim().length > 0
    );

    const intakePayload: BusinessProfileIntake = {
      business_name: businessName.trim(),
      legal_name: legalName.trim() || undefined,
      category,
      tagline: tagline.trim() || undefined,
      description: description.trim() || undefined,
      nap: {
        phone: phone.trim() || undefined,
        email: email.trim() || undefined,
        street_address: streetAddress.trim() || undefined,
        city: city.trim() || undefined,
        state_region: stateRegion.trim() || undefined,
        postal_code: postalCode.trim() || undefined,
        country,
      },
      service_area_cities: cleanedCities,
      hours: hours.filter((h) => h.is_closed || (h.opens && h.closes)),
      services_or_products: validServices,
      languages,
      whatsapp_number: whatsappNumber.trim() || undefined,
      faqs: validFaqs,
      factual_declaration: factualDeclaration,
      ad_monetization_opt_in: adMonetizationOptIn,
      ai_search_discovery_opt_in: true,
    };

    try {
      const res = await submitBusinessIntake(projectId, intakePayload, true);
      onComplete(res.audit, res.page_count);
      onClose();
    } catch (err: any) {
      setError(
        err?.response?.data?.detail || err.message || "Failed to generate business website draft."
      );
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl max-w-2xl w-full max-h-[90vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-200 flex justify-between items-center bg-gray-50">
          <div>
            <h2 className="text-xl font-bold text-gray-900">Business Profile Questionnaire</h2>
            <p className="text-sm text-gray-500">
              Generate an editable, factual website draft from verified business details (No fabricated claims).
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-2xl font-semibold leading-none"
          >
            &times;
          </button>
        </div>

        {/* Steps indicator */}
        <div className="px-6 py-3 bg-gray-100 flex items-center justify-between text-xs font-semibold text-gray-600 border-b border-gray-200">
          <span className={step === 1 ? "text-blue-600 font-bold" : ""}>1. Identity</span>
          <span>&rarr;</span>
          <span className={step === 2 ? "text-blue-600 font-bold" : ""}>2. NAP & Area</span>
          <span>&rarr;</span>
          <span className={step === 3 ? "text-blue-600 font-bold" : ""}>3. Hours</span>
          <span>&rarr;</span>
          <span className={step === 4 ? "text-blue-600 font-bold" : ""}>4. Services</span>
          <span>&rarr;</span>
          <span className={step === 5 ? "text-blue-600 font-bold" : ""}>5. FAQs & Consent</span>
        </div>

        {/* Body */}
        <div className="p-6 overflow-y-auto flex-1 space-y-4">
          {error && (
            <div className="p-3 bg-red-50 border border-red-200 text-red-700 text-sm rounded-lg">
              {error}
            </div>
          )}

          {step === 1 && (
            <div className="space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Business Name *
                </label>
                <input
                  type="text"
                  value={businessName}
                  onChange={(e) => setBusinessName(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                  placeholder="e.g. Metro Plumbers & Electricals"
                  required
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Business Category *
                </label>
                <select
                  value={category}
                  onChange={(e) => setCategory(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                >
                  {CATEGORY_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Tagline (Optional)
                </label>
                <input
                  type="text"
                  value={tagline}
                  onChange={(e) => setTagline(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                  placeholder="e.g. Trusted plumbing repair and 24/7 emergency response"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  About the Business / Description
                </label>
                <textarea
                  rows={3}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                  placeholder="Describe your history, specialties, and operating ethos accurately."
                />
              </div>

              <div className="pt-2 border-t border-gray-100 flex items-center justify-between">
                <div>
                  <span className="text-sm font-medium text-gray-700">Multilingual Support</span>
                  <p className="text-xs text-gray-500">Also generate translated pages in Hindi</p>
                </div>
                <label className="flex items-center gap-2 cursor-pointer text-sm">
                  <input
                    type="checkbox"
                    checked={includeHindi}
                    onChange={(e) => setIncludeHindi(e.target.checked)}
                    className="rounded border-gray-300 text-blue-600"
                  />
                  <span>Include Hindi (हिन्दी)</span>
                </label>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    Phone Number
                  </label>
                  <input
                    type="tel"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                    placeholder="+91 98765 43210"
                  />
                </div>
                <div>
                  <label className="block text-sm font-medium text-gray-700 mb-1">
                    WhatsApp Number
                  </label>
                  <input
                    type="tel"
                    value={whatsappNumber}
                    onChange={(e) => setWhatsappNumber(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                    placeholder="+91 98765 43210"
                  />
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Email</label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                  placeholder="contact@business.com"
                />
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Street Address
                </label>
                <input
                  type="text"
                  value={streetAddress}
                  onChange={(e) => setStreetAddress(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                  placeholder="Shop 4, Market Road, Sector 15"
                />
              </div>

              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1">City</label>
                  <input
                    type="text"
                    value={city}
                    onChange={(e) => setCity(e.target.value)}
                    className="w-full px-2 py-1.5 border border-gray-300 rounded-lg text-sm"
                    placeholder="Mumbai"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1">State / Region</label>
                  <input
                    type="text"
                    value={stateRegion}
                    onChange={(e) => setStateRegion(e.target.value)}
                    className="w-full px-2 py-1.5 border border-gray-300 rounded-lg text-sm"
                    placeholder="Maharashtra"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1">Postal Code</label>
                  <input
                    type="text"
                    value={postalCode}
                    onChange={(e) => setPostalCode(e.target.value)}
                    className="w-full px-2 py-1.5 border border-gray-300 rounded-lg text-sm"
                    placeholder="400001"
                  />
                </div>
              </div>

              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Service Area Cities (Comma separated)
                </label>
                <input
                  type="text"
                  value={serviceCities}
                  onChange={(e) => setServiceCities(e.target.value)}
                  className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm"
                  placeholder="Mumbai, Thane, Navi Mumbai"
                />
              </div>
            </div>
          )}

          {step === 3 && (
            <div className="space-y-3">
              <p className="text-xs text-gray-500">
                Specify real opening hours. Do not guess; check "Closed" for non-operating days.
              </p>
              {hours.map((h, idx) => (
                <div
                  key={h.day}
                  className="flex items-center justify-between p-2 bg-gray-50 rounded-lg text-sm"
                >
                  <span className="w-24 font-medium capitalize text-gray-700">{h.day}</span>
                  <div className="flex items-center gap-2">
                    <label className="flex items-center gap-1 text-xs text-gray-500">
                      <input
                        type="checkbox"
                        checked={h.is_closed}
                        onChange={(e) => handleHourChange(idx, "is_closed", e.target.checked)}
                      />
                      Closed
                    </label>
                    {!h.is_closed && (
                      <>
                        <input
                          type="time"
                          value={h.opens || "09:00"}
                          onChange={(e) => handleHourChange(idx, "opens", e.target.value)}
                          className="px-2 py-1 border border-gray-300 rounded text-xs"
                        />
                        <span className="text-gray-400">to</span>
                        <input
                          type="time"
                          value={h.closes || "20:00"}
                          onChange={(e) => handleHourChange(idx, "closes", e.target.value)}
                          className="px-2 py-1 border border-gray-300 rounded text-xs"
                        />
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {step === 4 && (
            <div className="space-y-4">
              <div className="flex justify-between items-center">
                <span className="text-sm font-semibold text-gray-700">Services or Offerings</span>
                <button
                  type="button"
                  onClick={handleAddService}
                  className="text-xs text-blue-600 hover:text-blue-800 font-medium"
                >
                  + Add Service / Product
                </button>
              </div>
              <p className="text-xs text-gray-500">
                Leave price blank if unlisted or custom quote. The system will never fabricate prices.
              </p>
              {services.map((item, idx) => (
                <div key={idx} className="p-3 border border-gray-200 rounded-lg space-y-2 bg-gray-50">
                  <div className="flex justify-between">
                    <span className="text-xs font-bold text-gray-600">Item #{idx + 1}</span>
                    {services.length > 1 && (
                      <button
                        type="button"
                        onClick={() => handleRemoveService(idx)}
                        className="text-xs text-red-500 hover:text-red-700"
                      >
                        Remove
                      </button>
                    )}
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    <div className="col-span-2">
                      <input
                        type="text"
                        placeholder="Service name (e.g. Pipe Leak Repair)"
                        value={item.name}
                        onChange={(e) => handleServiceChange(idx, "name", e.target.value)}
                        className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs"
                      />
                    </div>
                    <div>
                      <input
                        type="number"
                        placeholder="Price (₹ optional)"
                        value={item.price === undefined || item.price === null ? "" : item.price}
                        onChange={(e) =>
                          handleServiceChange(
                            idx,
                            "price",
                            e.target.value === "" ? undefined : parseFloat(e.target.value)
                          )
                        }
                        className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs"
                      />
                    </div>
                  </div>
                  <input
                    type="text"
                    placeholder="Short description of this service"
                    value={item.description || ""}
                    onChange={(e) => handleServiceChange(idx, "description", e.target.value)}
                    className="w-full px-2 py-1 border border-gray-300 rounded text-xs"
                  />
                </div>
              ))}
            </div>
          )}

          {step === 5 && (
            <div className="space-y-4">
              <div>
                <div className="flex justify-between items-center mb-2">
                  <span className="text-sm font-semibold text-gray-700">Real FAQs (Optional)</span>
                  <button
                    type="button"
                    onClick={handleAddFaq}
                    className="text-xs text-blue-600 hover:text-blue-800 font-medium"
                  >
                    + Add FAQ
                  </button>
                </div>
                {faqs.map((faq, idx) => (
                  <div key={idx} className="p-3 border border-gray-200 rounded-lg space-y-2 mb-2 bg-gray-50">
                    <div className="flex justify-between">
                      <span className="text-xs font-bold text-gray-600">FAQ #{idx + 1}</span>
                      <button
                        type="button"
                        onClick={() => handleRemoveFaq(idx)}
                        className="text-xs text-red-500 hover:text-red-700"
                      >
                        Remove
                      </button>
                    </div>
                    <input
                      type="text"
                      placeholder="Question"
                      value={faq.question}
                      onChange={(e) => handleFaqChange(idx, "question", e.target.value)}
                      className="w-full px-2 py-1.5 border border-gray-300 rounded text-xs"
                    />
                    <textarea
                      rows={2}
                      placeholder="Answer"
                      value={faq.answer}
                      onChange={(e) => handleFaqChange(idx, "answer", e.target.value)}
                      className="w-full px-2 py-1 border border-gray-300 rounded text-xs"
                    />
                  </div>
                ))}
              </div>

              <div className="p-4 bg-blue-50 border border-blue-200 rounded-lg space-y-3">
                <label className="flex items-start gap-2 cursor-pointer text-xs text-gray-800 font-medium">
                  <input
                    type="checkbox"
                    checked={factualDeclaration}
                    onChange={(e) => setFactualDeclaration(e.target.checked)}
                    className="rounded border-gray-300 text-blue-600 mt-0.5"
                  />
                  <span>
                    I certify that all details supplied are accurate facts regarding my business.
                    The website generator will omit missing fields rather than fabricating claims.
                  </span>
                </label>

                <label className="flex items-start gap-2 cursor-pointer text-xs text-gray-700">
                  <input
                    type="checkbox"
                    checked={adMonetizationOptIn}
                    onChange={(e) => setAdMonetizationOptIn(e.target.checked)}
                    className="rounded border-gray-300 text-blue-600 mt-0.5"
                  />
                  <span>
                    Opt-in to ad monetization (Default: Disabled). Ads are only delivered after explicit publisher verification and consent.
                  </span>
                </label>
              </div>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 bg-gray-50 border-t border-gray-200 flex justify-between">
          {step > 1 ? (
            <button
              type="button"
              onClick={() => setStep(step - 1)}
              className="px-4 py-2 border border-gray-300 rounded-lg text-sm font-medium text-gray-700 hover:bg-gray-100"
            >
              Back
            </button>
          ) : (
            <div />
          )}

          {step < 5 ? (
            <button
              type="button"
              onClick={() => setStep(step + 1)}
              className="px-5 py-2 bg-blue-600 text-white rounded-lg text-sm font-medium hover:bg-blue-700"
            >
              Next Step &rarr;
            </button>
          ) : (
            <button
              type="button"
              disabled={isSubmitting || !factualDeclaration}
              onClick={handleSubmit}
              className="px-6 py-2 bg-green-600 text-white rounded-lg text-sm font-semibold hover:bg-green-700 disabled:opacity-50"
            >
              {isSubmitting ? "Generating Draft..." : "Generate Draft Website"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
