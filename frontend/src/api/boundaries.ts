export type StateResponseItem = {
  state_id: string;
  name: string;
};

export type DistrictResponseItem = {
  district_id: string;
  name: string;
};

export type DistrictsGeojson = {
  type: "FeatureCollection";
  features: Array<{
    type: "Feature";
    geometry: unknown;
    properties: {
      district_id: string;
      district_name: string;
      state_id: string;
      state_name: string;
    };
  }>;
};

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, { signal });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

/**
 * Body shape for the district / state statistics endpoints.
 * The fundamental time unit is ONE MONTH; the request describes an
 * inclusive ``[start, end]`` month range and never individual days.
 */
export interface RangeStatisticsRequestBody {
  start_year: number;
  start_month: number;
  end_year: number;
  end_month: number;
  variable: string;
}

export interface DistrictStatistics {
  district_id: string;
  variable: string;
  start_year: number;
  start_month: number;
  end_year: number;
  end_month: number;
  months_processed: number;
  mean: number;
  min: number;
  max: number;
}

export interface StateDistrictStatisticsItem {
  district_id: string;
  mean: number;
  min: number;
  max: number;
}

export interface StateDistrictStatisticsResponse {
  state_id: string;
  variable: string;
  start_year: number;
  start_month: number;
  end_year: number;
  end_month: number;
  months_processed: number;
  districts: StateDistrictStatisticsItem[];
}

export interface MonthlySeriesPoint {
  year: number;
  month: number;
  mean: number;
  min: number;
  max: number;
}

export interface DistrictMonthlySeries {
  district_id: string;
  variable: string;
  start_year: number;
  start_month: number;
  end_year: number;
  end_month: number;
  months_processed: number;
  points: MonthlySeriesPoint[];
}

export interface DistrictRasterClipFeature {
  type: "Feature";
  geometry: GeoJSON.Geometry;
  properties: {
    value: number;
    variable: string;
    nc_variable: string;
    row: number;
    col: number;
    center_lon: number;
    center_lat: number;
    is_boundary_cell: boolean;
    overlap_fraction: number;
    display_value?: number;
  };
}

export interface DistrictRasterClipResponse {
  district_id: string;
  district_name: string;
  state_id: string;
  state_name: string;
  variable: string;
  variable_long_name: string;
  nc_variable: string;
  units: string;
  year: number;
  month: number;
  time_decoded: string;
  source_resolution_deg: number;
  bbox_used: [number, number, number, number];
  feature_collection: {
    type: "FeatureCollection";
    features: DistrictRasterClipFeature[];
  };
  summary: {
    valid_cells: number;
    boundary_cells: number;
    excluded_cells: number;
    bbox_cells_total: number;
    mean: number;
    std: number;
    min: number;
    max: number;
    sum: number;
    median: number;
    p25: number;
    p75: number;
    partial_geom_count: number;
  };
  diagnostics: Record<string, unknown>;
  asset_id: string;
  asset_storage_key: string;
  cache_hit: boolean;
}

export interface DistrictRasterClipRequest {
  year: number;
  month: number;
  variable: string;
  padding_deg?: number;
  provider?: string;
}

export async function getStates(): Promise<StateResponseItem[]> {
  return getJson<StateResponseItem[]>("/boundaries/states");
}

export async function getDistricts(
  stateId: string,
  signal?: AbortSignal,
): Promise<DistrictResponseItem[]> {
  return getJson<DistrictResponseItem[]>(
    `/boundaries/states/${encodeURIComponent(stateId)}/districts`,
    signal,
  );
}

export async function getDistrictsGeojson(stateId: string): Promise<DistrictsGeojson> {
  return getJson<DistrictsGeojson>(
    `/boundaries/states/${encodeURIComponent(stateId)}/districts/geojson`
  );
}

/**
 * Fetch aggregated raster statistics for a district over an inclusive
 * month range. The body shape mirrors what the backend's
 * ``StatisticsRequest`` dataclass expects — see
 * ``backend/application/dto/requests.py``.
 */
export async function getDistrictRangeStatistics(
  districtId: string,
  body: RangeStatisticsRequestBody,
  signal?: AbortSignal,
): Promise<DistrictStatistics> {
  const response = await fetch(
    `${BASE_URL}/districts/${encodeURIComponent(districtId)}/statistics`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal,
    }
  );
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }
  return response.json();
}

/**
 * Fetch aggregated per-district raster statistics for a state over an
 * inclusive month range. Drives the choropleth map.
 */
export async function getStateDistrictRangeStatistics(
  stateId: string,
  body: RangeStatisticsRequestBody,
): Promise<StateDistrictStatisticsResponse> {
  const response = await fetch(
    `${BASE_URL}/states/${encodeURIComponent(stateId)}/districts/statistics`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    }
  );
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }
  return response.json();
}

/**
 * Fetch per-month raster statistics for a district over an inclusive
 * month range. Drives the BottomPanel's Time Series / Trend / Export
 * tabs — every point carries ``(year, month)`` plus ``mean``/``min``/
 * ``max`` so the chart can plot a clean chronological series.
 */
// In-flight request deduplication for getDistrictMonthlySeries.
//
// Multiple callers requesting the same body share a single in-flight
// promise while the first request is pending.

interface InFlightKey {
  districtId: string;
  body: RangeStatisticsRequestBody;
}

function inFlightKey(k: InFlightKey): string {
  return `${k.districtId}|${k.body.variable}|${k.body.start_year}-${String(k.body.start_month).padStart(2, "0")}|${k.body.end_year}-${String(k.body.end_month).padStart(2, "0")}`;
}

const __tsInFlight = new Map<string, Promise<DistrictMonthlySeries>>();

export async function getDistrictMonthlySeries(
  districtId: string,
  body: RangeStatisticsRequestBody,
  signal?: AbortSignal,
): Promise<DistrictMonthlySeries> {
  const key = inFlightKey({ districtId, body });
  const existing = __tsInFlight.get(key);
  if (existing) {
    return existing;
  }
  const promise = (async () => {
    const response = await fetch(
      `${BASE_URL}/districts/${encodeURIComponent(districtId)}/time-series`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
        signal,
      }
    );
    if (!response.ok) {
      throw new Error(`Request failed: ${response.status} ${response.statusText}`);
    }
    return response.json();
  })();
  __tsInFlight.set(key, promise);
  try {
    return await promise;
  } finally {
    // Allow the next caller (after the previous one settles) to issue a
    // fresh request; requests are only deduplicated while in flight.
    if (__tsInFlight.get(key) === promise) {
      __tsInFlight.delete(key);
    }
  }
}

export async function getDistrictRasterClip(
  districtId: string,
  params: DistrictRasterClipRequest,
  signal?: AbortSignal,
): Promise<DistrictRasterClipResponse> {
  const search = new URLSearchParams({
    year: String(params.year),
    month: String(params.month),
    variable: params.variable,
  });
  if (params.padding_deg !== undefined) {
    search.set("padding_deg", String(params.padding_deg));
  }
  if (params.provider) {
    search.set("provider", params.provider);
  }
  return getJson<DistrictRasterClipResponse>(
    `/districts/${encodeURIComponent(districtId)}/raster-clip?${search.toString()}`,
    signal,
  );
}

export type ClimateAsset = {
  id: string;
  provider: string;
  variable: string;
  year: number;
  month: number;
  storage_key: string;
  status: string;
  file_size?: number | null;
  checksum?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export async function getDatasets(): Promise<ClimateAsset[]> {
  return getJson<ClimateAsset[]>("/datasets");
}
