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

const BASE_URL = "http://localhost:8000";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`);
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

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
