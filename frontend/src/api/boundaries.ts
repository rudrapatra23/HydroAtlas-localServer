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

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export type DistrictStatistics = {
  district_id: string;
  variable: string;
  mean: number;
  min: number;
  max: number;
};

export type StateDistrictStatisticsItem = {
  district_id: string;
  mean: number;
  min: number;
  max: number;
};

export type StateDistrictStatisticsResponse = {
  state_id: string;
  year: number;
  month: number;
  variable: string;
  districts: StateDistrictStatisticsItem[];
};

export async function getStates(): Promise<StateResponseItem[]> {
  return getJson<StateResponseItem[]>("/boundaries/states");
}

export async function getDistricts(stateId: string): Promise<DistrictResponseItem[]> {
  return getJson<DistrictResponseItem[]>(
    `/boundaries/states/${encodeURIComponent(stateId)}/districts`
  );
}

export async function getDistrictsGeojson(stateId: string): Promise<DistrictsGeojson> {
  return getJson<DistrictsGeojson>(
    `/boundaries/states/${encodeURIComponent(stateId)}/districts/geojson`
  );
}

export async function getDistrictStatistics(
  districtId: string,
  year: number,
  month: number,
  variable: string = "precipitation"
): Promise<DistrictStatistics> {
  const response = await fetch(`${BASE_URL}/districts/${encodeURIComponent(districtId)}/statistics`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ year, month, variable }),
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }
  return response.json();
}

export async function getStateDistrictStatistics(
  stateId: string,
  year: number,
  month: number,
  variable: string
): Promise<StateDistrictStatisticsResponse> {
  const response = await fetch(
    `${BASE_URL}/states/${encodeURIComponent(stateId)}/districts/statistics`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ year, month, variable }),
    }
  );
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }
  return response.json();
}
