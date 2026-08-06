/**
 * Option lists for the job-creation form selects. Country/currency codes are
 * the wire values the backend expects (ISO-3166 alpha-2 / ISO-4217).
 */

export const COUNTRIES = [
  { code: "AE", label: "United Arab Emirates", flag: "🇦🇪" },
  { code: "SA", label: "Saudi Arabia", flag: "🇸🇦" },
  { code: "QA", label: "Qatar", flag: "🇶🇦" },
  { code: "KW", label: "Kuwait", flag: "🇰🇼" },
  { code: "BH", label: "Bahrain", flag: "🇧🇭" },
  { code: "OM", label: "Oman", flag: "🇴🇲" },
];

export const CURRENCIES = ["AED", "SAR", "QAR", "KWD", "BHD", "OMR"];

/**
 * Major cities per country (keyed by ISO-3166 alpha-2). Drives the City select,
 * which is populated from the currently selected Country.
 */
export const CITIES_BY_COUNTRY = {
  AE: ["Dubai", "Abu Dhabi", "Sharjah", "Ajman", "Ras Al Khaimah", "Fujairah", "Umm Al Quwain", "Al Ain"],
  SA: ["Riyadh", "Jeddah", "Mecca", "Medina", "Dammam", "Khobar", "Dhahran", "Tabuk"],
  QA: ["Doha", "Al Rayyan", "Al Wakrah", "Al Khor", "Lusail", "Umm Salal"],
  KW: ["Kuwait City", "Al Ahmadi", "Hawalli", "Salmiya", "Al Farwaniyah", "Jahra"],
  BH: ["Manama", "Riffa", "Muharraq", "Hamad Town", "Isa Town", "Sitra"],
  OM: ["Muscat", "Salalah", "Sohar", "Nizwa", "Sur", "Ibri"],
};

/** Cities for a country code (ISO-3166 alpha-2); empty array if unknown. */
export const citiesByCountry = (code) => CITIES_BY_COUNTRY[code] ?? [];

/** Lookup a country by its ISO-3166 alpha-2 code (the wire value). */
export const countryByCode = (code) => COUNTRIES.find((c) => c.code === code);

/** "AE" → "🇦🇪 United Arab Emirates"; falls back to the raw code. */
export const countryLabel = (code) => {
  const c = countryByCode(code);
  return c ? `${c.flag} ${c.label}` : code || "—";
};

export const LANGUAGES = ["English", "Arabic"];

export const NOTICE_PERIOD_OPTIONS = [
  { value: "any", label: "Any notice period" },
  { value: "immediate", label: "Immediate start" },
  { value: "30d", label: "Up to 30 days" },
  { value: "60d", label: "Up to 60 days" },
  { value: "90d", label: "Up to 90 days" },
];

/**
 * Visa options shown in the UI. `api` maps each to a backend VisaRequirement
 * enum (`any | citizen_or_resident | sponsorship_offered`) since the backend
 * doesn't model these finer distinctions yet.
 */
export const VISA_OPTIONS = [
  { value: "any", api: "any", icon: "🌍", label: "Open to all visas" },
  { value: "residence_visa", api: "citizen_or_resident", icon: "🪪", label: "Must have residence visa" },
  { value: "own_transferable", api: "citizen_or_resident", icon: "📄", label: "Own visa / transferable" },
  { value: "gcc_preferred", api: "citizen_or_resident", icon: "🏳️", label: "GCC Nationals preferred" },
  { value: "citizens_only", api: "citizen_or_resident", icon: "🇦🇪", label: "Citizens only (Emiratization/Saudization)" },
];

export const visaApiValue = (value) =>
  VISA_OPTIONS.find((o) => o.value === value)?.api ?? null;

/**
 * Nationality preference options. The backend field is a string[]; `apiList`
 * is what we send for each choice ("Any" → empty = no preference).
 */
export const NATIONALITY_OPTIONS = [
  { value: "any", icon: "🌍", label: "Any nationality", apiList: [] },
  { value: "arab_preferred", icon: "🌙", label: "Arab nationals preferred", apiList: ["Arab nationals preferred"] },
  { value: "gcc_preferred", icon: "🏳️", label: "GCC nationals preferred", apiList: ["GCC nationals preferred"] },
  { value: "local_only", icon: "🇦🇪", label: "Local nationals only", apiList: ["Local nationals only"] },
];

export const nationalityApiList = (value) =>
  NATIONALITY_OPTIONS.find((o) => o.value === value)?.apiList ?? [];

/**
 * Coerce the wizard form's Role-Basics fields into the backend Role spec shape
 * (the subset shared by `POST /jobs` and `POST /jobs/generate-description`).
 * Excludes `job_description` — callers add it.
 */
export const toJobSpecPayload = (v) => ({
  title: v.title.trim(),
  hiring_company: v.hiring_company.trim(),
  country: v.country,
  city: v.city.trim(),
  employment_type: v.employment_type,
  work_mode: v.work_mode,
  currency: v.currency,
  min_salary: v.min_salary === "" ? null : Number(v.min_salary),
  max_salary: v.max_salary === "" ? null : Number(v.max_salary),
  notice_period: v.notice_period || null,
  min_experience_years: Number(v.min_experience_years),
  required_skills: v.required_skills,
  preferred_skills: v.preferred_skills,
  visa_requirement: visaApiValue(v.visa_requirement),
  nationality_preference: nationalityApiList(v.nationality_preference),
  languages_required: v.languages_required,
});

export const QUESTION_CATEGORIES = [
  { value: "commitment", label: "Commitment" },
  { value: "salary", label: "Salary" },
  { value: "background_validation", label: "Background validation" },
];

/** Human label for a question-category value ("salary" → "Salary"). */
export const questionCategoryLabel = (value) =>
  QUESTION_CATEGORIES.find((c) => c.value === value)?.label ?? value;

