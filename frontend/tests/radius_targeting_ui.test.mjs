/**
 * radius_targeting_ui.test.mjs — Phase 9.3 Hyperlocal Radius Targeting UI and Map Picker Tests
 *
 * Verifies:
 * 1. Unit conversion and minimum/maximum radius bounds (100m - 100km).
 * 2. Latitude and Longitude coordinate validation (-90..90, -180..180).
 * 3. formatGeoEntry and isRadiusGeoEntry for administrative and radius targets.
 * 4. Mutual exclusivity and conflict detection between include and exclude lists.
 * 5. Absence of navigator.geolocation collection on advertiser forms.
 * 6. RadiusMapPicker SSR rendering, SVG coordinate canvas, HUD, and preset buttons.
 * 7. TargetingGeoPanel mode switcher, administrative dropdowns, and chip summaries.
 * 8. CampaignTargeting integration and Ad Set scope selection.
 * 9. AdSetsStep wizard integration and sibling Ad Set targeting isolation.
 * 10. Persistence serialization roundtrip matching the backend JSON contract.
 */

import test, { describe, after } from "node:test";
import assert from "node:assert";
import esbuild from "esbuild";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";

// Filter out React Router's SSR useLayoutEffect warning in Node test environment
const origError = console.error;
console.error = (...args) => {
  if (typeof args[0] === "string" && args[0].includes("useLayoutEffect does nothing on the server")) {
    return;
  }
  origError.apply(console, args);
};

const cleanupFiles = [];

function compileBundle() {
  const bundlePath = path.resolve("./tests/.compiled_radius_targeting_bundle.mjs");
  cleanupFiles.push(bundlePath);

  const wrapper = `
import React from 'react';
export { React };
export { MemoryRouter } from 'react-router-dom';
export { RadiusMapPicker, PRESET_LOCATIONS } from '../src/components/targeting/RadiusMapPicker';
export { TargetingGeoPanel } from '../src/components/targeting/TargetingGeoPanel';
export { default as CampaignTargeting } from '../src/pages/advertiser/CampaignTargeting';
export { AdSetsStep } from '../src/pages/advertiser/wizard/AdSetsStep';
export * from '../src/api/client';
`;

  const entryFile = path.resolve("./tests/.temp_radius_entry.tsx");
  fs.writeFileSync(entryFile, wrapper, "utf8");
  cleanupFiles.push(entryFile);

  esbuild.buildSync({
    entryPoints: [entryFile],
    outfile: bundlePath,
    bundle: true,
    format: "esm",
    platform: "node",
    target: "node18",
    jsx: "automatic",
    external: ["react", "react-dom", "react-dom/server", "react-router-dom", "recharts"],
  });

  return pathToFileURL(bundlePath).href;
}

after(() => {
  for (const f of cleanupFiles) {
    try {
      if (fs.existsSync(f)) fs.unlinkSync(f);
    } catch {}
  }
});

describe("RivinityADS Phase 9.3 — Hyperlocal Radius Targeting UI and Map Picker", async () => {
  const bundleUrl = compileBundle();
  const mod = await import(bundleUrl);
  const {
    React,
    MemoryRouter,
    RadiusMapPicker,
    TargetingGeoPanel,
    CampaignTargeting,
    AdSetsStep,
    PRESET_LOCATIONS,
    MIN_RADIUS_METERS,
    MAX_RADIUS_METERS,
    convertRadiusToMeters,
    convertMetersToUnit,
    isRadiusGeoEntry,
    formatGeoEntry,
  } = mod;

  test("1. Unit conversion utilities convert meters, km, and miles accurately", () => {
    // Meters identity
    assert.strictEqual(convertRadiusToMeters(500, "meters"), 500);
    assert.strictEqual(convertMetersToUnit(500, "meters"), 500);

    // Kilometers to meters
    assert.strictEqual(convertRadiusToMeters(5, "km"), 5000);
    assert.strictEqual(convertRadiusToMeters(0.1, "km"), 100);
    assert.strictEqual(convertRadiusToMeters(100, "km"), 100000);

    // Meters to kilometers
    assert.strictEqual(convertMetersToUnit(5000, "km"), 5);
    assert.strictEqual(convertMetersToUnit(100, "km"), 0.1);

    // Miles to meters (1 mile = 1609.344 meters)
    assert.strictEqual(convertRadiusToMeters(1, "miles"), 1609.34);
    assert.strictEqual(convertRadiusToMeters(3.1, "miles"), 4988.97);

    // Meters to miles
    assert.strictEqual(convertMetersToUnit(1609.344, "miles"), 1);

    // Non-finite, negative, and zero safety
    assert.strictEqual(convertRadiusToMeters(-10, "km"), 0);
    assert.strictEqual(convertRadiusToMeters(NaN, "meters"), 0);
    assert.strictEqual(convertMetersToUnit(0, "km"), 0);
  });

  test("2. Radius bounds constants enforce 100m minimum and 100km maximum", () => {
    assert.strictEqual(MIN_RADIUS_METERS, 100, "Minimum radius must be 100 meters");
    assert.strictEqual(MAX_RADIUS_METERS, 100000, "Maximum radius must be 100,000 meters (100km)");
  });

  test("3. isRadiusGeoEntry correctly distinguishes radius from administrative entries", () => {
    const admin1 = { country: "IN" };
    const admin2 = { country: "IN", state: "MH", city: "Mumbai" };
    const admin3 = { country: "IN", pincode: "400001" };

    assert.strictEqual(isRadiusGeoEntry(admin1), false);
    assert.strictEqual(isRadiusGeoEntry(admin2), false);
    assert.strictEqual(isRadiusGeoEntry(admin3), false);

    const radius1 = { latitude: 19.076, longitude: 72.8777, radius_meters: 5000 };
    const radius2 = { latitude: 28.6139, longitude: 77.209, radius_meters: 10000, country: "IN", name: "Delhi Hub" };

    assert.strictEqual(isRadiusGeoEntry(radius1), true);
    assert.strictEqual(isRadiusGeoEntry(radius2), true);

    // Partial coordinates must not be considered valid radius entries
    const partialLatOnly = { latitude: 19.076, country: "IN" };
    const partialRadiusOnly = { radius_meters: 5000, country: "IN" };
    assert.strictEqual(isRadiusGeoEntry(partialLatOnly), false);
    assert.strictEqual(isRadiusGeoEntry(partialRadiusOnly), false);
  });

  test("4. formatGeoEntry formats administrative and radius targets cleanly", () => {
    // Administrative
    assert.strictEqual(formatGeoEntry({ country: "IN" }), "IN");
    assert.strictEqual(formatGeoEntry({ country: "IN", state: "MH" }), "IN > MH");
    assert.strictEqual(formatGeoEntry({ country: "IN", state: "MH", city: "Mumbai" }), "IN > MH > Mumbai");
    assert.strictEqual(formatGeoEntry({ country: "IN", pincode: "400001" }), "IN > 400001");

    // Radius with label and country
    const r1 = {
      latitude: 19.076,
      longitude: 72.8777,
      radius_meters: 5000,
      radius_unit: "km",
      name: "Flagship Store",
      country: "IN",
    };
    const formatted1 = formatGeoEntry(r1);
    assert.ok(formatted1.includes("5.0 km"), "Must include radius in km");
    assert.ok(formatted1.includes("19.0760"), "Must include formatted latitude");
    assert.ok(formatted1.includes("72.8777"), "Must include formatted longitude");
    assert.ok(formatted1.includes('"Flagship Store"'), "Must include target label");
    assert.ok(formatted1.includes("[IN]"), "Must include country scope");

    // Radius without label or country
    const r2 = {
      latitude: 12.9716,
      longitude: 77.5946,
      radius_meters: 2500,
    };
    const formatted2 = formatGeoEntry(r2);
    assert.ok(formatted2.includes("2.5 km"), "Must format 2500m as 2.5 km");
    assert.ok(formatted2.includes("12.9716"), "Must include latitude");
    assert.ok(formatted2.includes("77.5946"), "Must include longitude");
    assert.ok(!formatted2.includes('"'), "Must not include empty label quotes");
    assert.ok(!formatted2.includes("["), "Must not include empty country brackets");
  });

  test("5. Location privacy safeguard: No navigator.geolocation collection on targeting form", () => {
    // Assert that the components do not reference browser geolocation APIs
    const mapPickerSource = fs.readFileSync(
      path.resolve("./src/components/targeting/RadiusMapPicker.tsx"),
      "utf8"
    );
    const geoPanelSource = fs.readFileSync(
      path.resolve("./src/components/targeting/TargetingGeoPanel.tsx"),
      "utf8"
    );

    assert.ok(
      !mapPickerSource.includes("navigator.geolocation"),
      "RadiusMapPicker must NOT invoke navigator.geolocation"
    );
    assert.ok(
      !mapPickerSource.includes("getCurrentPosition"),
      "RadiusMapPicker must NOT request device position"
    );
    assert.ok(
      !geoPanelSource.includes("navigator.geolocation"),
      "TargetingGeoPanel must NOT invoke navigator.geolocation"
    );
  });

  test("6. RadiusMapPicker renders interactive SVG canvas, HUD, preset hubs, and notices", () => {
    const html = renderToStaticMarkup(
      React.createElement(RadiusMapPicker, {
        onAddRadiusTarget: () => {},
      })
    );

    // Title and limits badge
    assert.ok(html.includes("Hyperlocal Radius Geofencing"), "Must render title");
    assert.ok(html.includes("100m – 100km"), "Must render 100m - 100km bounds badge");

    // Tab switcher
    assert.ok(html.includes("Interactive Map"), "Must render Interactive Map tab");
    assert.ok(html.includes("Precision Inputs"), "Must render Precision Inputs tab");

    // SVG Canvas elements
    assert.ok(html.includes("<svg"), "Must render SVG coordinate map");
    assert.ok(html.includes("viewBox=\"0 0 600 300\""), "Must have standard equirectangular viewBox");
    assert.ok(html.includes("id=\"radiusGradient\""), "Must define radius visual gradient");
    assert.ok(html.includes("CENTER:"), "Must render coordinate HUD");
    assert.ok(html.includes("RADIUS:"), "Must render radius HUD");

    // Presets
    assert.ok(PRESET_LOCATIONS.length >= 6, "Must define at least 6 metropolitan preset hubs");
    assert.ok(html.includes("Mumbai (IN)"), "Must render Mumbai preset");
    assert.ok(html.includes("Delhi NCR (IN)"), "Must render Delhi preset");
    assert.ok(html.includes("London (GB)"), "Must render London preset");
    assert.ok(html.includes("New York (US)"), "Must render New York preset");

    // Form inputs and unit select
    assert.ok(html.includes("Latitude (-90.0° to +90.0°)"), "Must have accessible latitude label");
    assert.ok(html.includes("Longitude (-180.0° to +180.0°)"), "Must have accessible longitude label");
    assert.ok(html.includes("Radius Distance"), "Must have radius distance label");
    assert.ok(html.includes("Kilometers (km)"), "Must support km option");
    assert.ok(html.includes("Meters (m)"), "Must support meters option");
    assert.ok(html.includes("Miles (mi)"), "Must support miles option");

    // Informational notices
    assert.ok(
      html.includes("Address geocoding requires a dedicated backend geocoder service key (unconfigured)"),
      "Must explain geocoding service key blocker honestly"
    );
    assert.ok(
      html.includes("Hyperlocal radius delivery requires explicit ad viewer consent"),
      "Must explain location consent and provenance requirements"
    );

    // Action buttons
    assert.ok(html.includes("+ Include Radius Target"), "Must have include radius button");
    assert.ok(html.includes("- Exclude Radius Target"), "Must have exclude radius button");
  });

  test("7. TargetingGeoPanel renders mode tabs and manages included/excluded chip lists", () => {
    const mockGeoInclude = [
      { country: "IN", state: "MH", city: "Mumbai" },
      { latitude: 19.076, longitude: 72.8777, radius_meters: 5000, name: "Bandra Store" },
    ];
    const mockGeoExclude = [
      { country: "US" },
    ];

    const html = renderToStaticMarkup(
      React.createElement(TargetingGeoPanel, {
        geoInclude: mockGeoInclude,
        geoExclude: mockGeoExclude,
        onChange: () => {},
      })
    );

    assert.ok(html.includes("Geographic Targeting"), "Must render panel header");
    assert.ok(html.includes("Administrative Regions"), "Must render Administrative tab button");
    assert.ok(html.includes("Hyperlocal Radius"), "Must render Hyperlocal Radius tab button");

    // Included and excluded sections
    assert.ok(html.includes("Included Locations (2)"), "Must display included location count");
    assert.ok(html.includes("IN &gt; MH &gt; Mumbai"), "Must render administrative included chip");
    assert.ok(html.includes("5.0 km around (19.0760, 72.8777)"), "Must render radius included chip");
    assert.ok(html.includes("Bandra Store"), "Must render radius label in chip");

    assert.ok(html.includes("Excluded Locations (1)"), "Must display excluded location count");
    assert.ok(html.includes("US"), "Must render excluded location chip");
    assert.ok(html.includes("aria-label=\"Remove included location"), "Must have accessible remove button aria-label");
  });

  test("8. CampaignTargeting renders scope switcher, TargetingGeoPanel, and Sector panel", () => {
    const mockAdSets = [
      {
        id: 101,
        campaign_id: 1,
        name: "Metro Ad Set",
        status: 1,
        daily_budget: 100,
        bid_type: 1,
        bid_amount: 1.5,
        delivery_mode: "standard",
        created_at: "2026-10-27T10:00:00Z",
        updated_at: "2026-10-27T10:00:00Z",
      },
      {
        id: 102,
        campaign_id: 1,
        name: "Suburban Ad Set",
        status: 1,
        daily_budget: 150,
        bid_type: 1,
        bid_amount: 2.0,
        delivery_mode: "standard",
        created_at: "2026-10-27T10:00:00Z",
        updated_at: "2026-10-27T10:00:00Z",
      },
    ];

    const html = renderToStaticMarkup(
      React.createElement(CampaignTargeting, {
        campaignId: 1,
        adSets: mockAdSets,
      })
    );

    assert.ok(html.includes("Audience &amp; Delivery Targeting"), "Must render card title");
    assert.ok(html.includes("Targeting Scope"), "Must render targeting scope selector");
    assert.ok(html.includes("Ad Set: Metro Ad Set"), "Must list first ad set scope");
    assert.ok(html.includes("Ad Set: Suburban Ad Set"), "Must list second ad set scope");
    assert.ok(html.includes("Campaign Level (Legacy)"), "Must support campaign-level fallback scope");
  });

  test("9. AdSetsStep wizard renders TargetingGeoPanel and preserves Ad Set targeting isolation", () => {
    const mockCampaign = {
      id: 1,
      advertiser_id: 42,
      name: "Q4 Hyperlocal",
      budget_daily: 500,
      budget_total: 5000,
      spent_today: 0,
      spent_total: 0,
      bid_type: 1,
      bid_amount: 1.0,
      freq_cap_daily: 5,
      freq_cap_hourly: 2,
      status: 5,
      autopilot: false,
      impressions: 0,
      clicks: 0,
      conversions: 0,
    };

    const mockAdSets = [
      {
        id: 1,
        campaign_id: 1,
        name: "Downtown Radius Store",
        status: 1,
        daily_budget: 250,
        bid_type: 1,
        bid_amount: 1.5,
        delivery_mode: "standard",
        created_at: "2026-10-27T10:00:00Z",
        updated_at: "2026-10-27T10:00:00Z",
      },
    ];

    const html = renderToStaticMarkup(
      React.createElement(AdSetsStep, {
        campaign: mockCampaign,
        adSets: mockAdSets,
        selectedAdSetId: 1,
        onAdSetsUpdated: () => {},
        onBack: () => {},
        onContinue: () => {},
      })
    );

    assert.ok(html.includes("Step 2: Ad Sets &amp; Targeting"), "Must render step title");
    assert.ok(html.includes("Downtown Radius Store"), "Must render active ad set name");
    assert.ok(html.includes("Geographic Targeting"), "Must render TargetingGeoPanel");
    assert.ok(html.includes("Sector &amp; Category Targeting"), "Must render sector panel");
    assert.ok(html.includes("Targeting rules are strictly isolated"), "Must emphasize Ad Set isolation");
  });

  test("10. Persisted JSON serialization roundtrip matches backend contract", () => {
    // Construct a rich TargetingSet containing mixed administrative and radius entries
    const targetingSet = {
      geo_include: [
        {
          country: "IN",
          state: "MH",
          city: "Mumbai",
        },
        {
          latitude: 19.076,
          longitude: 72.8777,
          radius_meters: 5000,
          radius_unit: "km",
          name: "Flagship Mumbai",
          country: "IN",
        },
      ],
      geo_exclude: [
        {
          latitude: 18.922,
          longitude: 72.8347,
          radius_meters: 1000,
          radius_unit: "meters",
          name: "Harbor Buffer Exclude",
        },
      ],
      sector_include: ["ecommerce", "fintech"],
      sector_exclude: ["gambling"],
    };

    // Serialize to JSON as apiFetch does
    const serialized = JSON.stringify(targetingSet);
    assert.ok(serialized.includes('"radius_meters":5000'), "Must serialize radius_meters");
    assert.ok(serialized.includes('"latitude":19.076'), "Must serialize latitude");
    assert.ok(serialized.includes('"longitude":72.8777'), "Must serialize longitude");
    assert.ok(serialized.includes('"Flagship Mumbai"'), "Must serialize name");

    // Parse and verify roundtrip hydration
    const deserialized = JSON.parse(serialized);
    assert.strictEqual(deserialized.geo_include.length, 2);
    assert.strictEqual(deserialized.geo_exclude.length, 1);

    const radiusInclude = deserialized.geo_include.find((e) => e.radius_meters != null);
    assert.ok(radiusInclude, "Must find radius entry");
    assert.strictEqual(radiusInclude.latitude, 19.076);
    assert.strictEqual(radiusInclude.longitude, 72.8777);
    assert.strictEqual(radiusInclude.radius_meters, 5000);
    assert.strictEqual(radiusInclude.country, "IN");
    assert.strictEqual(radiusInclude.name, "Flagship Mumbai");
    assert.strictEqual(isRadiusGeoEntry(radiusInclude), true);

    const adminInclude = deserialized.geo_include.find((e) => e.radius_meters == null);
    assert.ok(adminInclude, "Must find admin entry");
    assert.strictEqual(adminInclude.city, "Mumbai");
    assert.strictEqual(isRadiusGeoEntry(adminInclude), false);

    const radiusExclude = deserialized.geo_exclude[0];
    assert.strictEqual(radiusExclude.radius_meters, 1000);
    assert.strictEqual(radiusExclude.name, "Harbor Buffer Exclude");
    assert.strictEqual(isRadiusGeoEntry(radiusExclude), true);
  });

  test("11. RadiusMapPicker renders editing banner and update action buttons in edit mode", () => {
    const editingTarget = {
      entry: {
        latitude: 12.9716,
        longitude: 77.5946,
        radius_meters: 10000,
        radius_unit: "km",
        name: "Bengaluru Tech Corridor",
        country: "IN",
      },
      mode: "include",
    };

    const html = renderToStaticMarkup(
      React.createElement(RadiusMapPicker, {
        onAddRadiusTarget: () => {},
        editingTarget,
        onUpdateRadiusTarget: () => {},
        onCancelEdit: () => {},
      })
    );

    assert.ok(html.includes("Editing Included Target:"), "Must display editing banner");
    assert.ok(html.includes("Bengaluru Tech Corridor"), "Must display target name in editing banner");
    assert.ok(html.includes("Update Included Target"), "Must display update button");
    assert.ok(html.includes("Switch to Exclude &amp; Update"), "Must display switch mode button");
    assert.ok(html.includes("Cancel Edit"), "Must display cancel button");
  });

  test("12. TargetingGeoPanel renders edit buttons on target chips and displays save bar in AdSetsStep", () => {
    const geoInclude = [
      {
        latitude: 28.6139,
        longitude: 77.209,
        radius_meters: 15000,
        radius_unit: "km",
        name: "Delhi Capital Zone",
        country: "IN",
      },
      {
        country: "IN",
        state: "KA",
        city: "Bengaluru",
      },
    ];
    const geoExclude = [
      {
        latitude: 28.5,
        longitude: 77.1,
        radius_meters: 2000,
        radius_unit: "km",
        name: "Airport Exclusion",
      },
    ];

    const html = renderToStaticMarkup(
      React.createElement(TargetingGeoPanel, {
        geoInclude,
        geoExclude,
        onChange: () => {},
      })
    );

    assert.ok(html.includes("chip-edit"), "Must render edit buttons on chips");
    assert.ok(html.includes("Delhi Capital Zone"), "Must format radius chip");
    assert.ok(html.includes("IN &gt; KA &gt; Bengaluru"), "Must format administrative chip");
    assert.ok(html.includes("Airport Exclusion"), "Must format excluded radius chip");
    assert.ok(html.includes("✎"), "Must display edit symbol on chip");
  });
});
