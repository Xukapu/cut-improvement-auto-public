import {
  ApiError,
  apiRequest,
} from "./client";

import {
  listClients,
} from "./clients";

import {
  createSyncUuid,
} from "./sync";

import {
  getCachedApiResponse,
  listPendingSyncOperations,
} from "../offline/offlineStore";

import {
  queueSyncOperation,
} from "../sync/syncQueue";

import type {
  Client,
} from "../types/client";

import type {
  SyncQueuedOperation,
} from "../types/sync";

import type {
  Vehicle,
  VehicleCreate,
  VehicleListResponse,
  VehicleUpdate,
} from "../types/vehicle";


type VehicleOwner = Pick<Client, "id" | "client_number" | "full_name" | "sync_pending">;


function pendingVehicleFromOperation(
  operation: SyncQueuedOperation,
  clients: VehicleOwner[],
): Vehicle {
  const payload =
    operation.payload;

  const ownerId =
    typeof payload.owner_client_id ===
      "string"
      ? payload.owner_client_id
      : "";

  const owner =
    clients.find(
      (client) =>
        client.id === ownerId,
    );

  return {
    id:
      operation.entity_id,

    license_plate:
      String(
        payload.license_plate ??
        "",
      ),

    vin:
      typeof payload.vin ===
        "string"
        ? payload.vin
        : null,

    brand:
      String(
        payload.brand ??
        "",
      ),

    model:
      String(
        payload.model ??
        "",
      ),

    year:
      typeof payload.year ===
        "number"
        ? payload.year
        : null,

    mileage:
      typeof payload.mileage ===
        "number"
        ? payload.mileage
        : null,

    vehicle_number: 0,

    current_owner_client_number:
      owner?.client_number ??
      0,

    current_owner_name:
      owner?.full_name ??
      String(
        payload.owner_client_name ??
        "Владелец",
      ),

    sync_pending: true,
  };
}


function matchesSearch(
  vehicle: Vehicle,
  search: string,
): boolean {
  const value =
    search
      .trim()
      .toLocaleLowerCase();

  if (!value) {
    return true;
  }

  return (/^\d+$/.test(value) && vehicle.vehicle_number > 0 && vehicle.vehicle_number === Number(value)) || [
    vehicle.license_plate,
    vehicle.vin ?? "",
    vehicle.brand,
    vehicle.model,
    `${vehicle.brand} ${vehicle.model}`,
  ].some(
    (item) =>
      item
        .toLocaleLowerCase()
        .includes(value),
  );
}


function vehicleListPath(
  search: string,
  limit: number,
  clientNumber?: number,
): string {
  const params =
    new URLSearchParams({
      limit: String(limit),
      offset: "0",
    });

  if (search.trim()) {
    params.set(
      "search",
      search.trim(),
    );
  }

  if (
    clientNumber !== undefined
  ) {
    params.set(
      "client_number",
      String(clientNumber),
    );
  }


  return `/api/v1/vehicles?${params.toString()}`;
}


export async function listVehicles(
  search = "",
  limit = 100,
  clientOrNumber?: VehicleOwner | number,
): Promise<VehicleListResponse> {
  const owner = typeof clientOrNumber === "object" ? clientOrNumber : undefined;
  const clientNumber = typeof clientOrNumber === "number" ? clientOrNumber : owner?.client_number;
  const empty = { items: [], total: 0, limit, offset: 0 };
  const response = owner?.sync_pending
    ? empty
    : await apiRequest<VehicleListResponse>(vehicleListPath(search, limit, clientNumber))
      .catch(async (error: unknown) => {
        if (!owner || !(error instanceof ApiError) || (error.status !== 0 && error.status < 500)) {
          throw error;
        }

        const ownerCache = await getCachedApiResponse<VehicleListResponse>(vehicleListPath("", limit, clientNumber));
        const cached = ownerCache.found
          ? ownerCache
          : await getCachedApiResponse<VehicleListResponse>(vehicleListPath("", limit));
        if (!cached.found) {
          return empty;
        }

        const items = cached.payload.items.filter((vehicle) =>
          vehicle.current_owner_client_number === clientNumber && matchesSearch(vehicle, search),
        );
        return { ...cached.payload, items, total: items.length };
      });


  const operations =
    await listPendingSyncOperations();

  const pendingOperations =
    operations.filter(
      (operation) =>
        operation.kind ===
        "vehicle.create" &&
        (!owner || operation.payload.owner_client_id === owner.id),
    );


  if (
    pendingOperations.length === 0
  ) {
    return response;
  }


  const clients = owner ? [owner] : (await listClients()).items;


  const knownIds =
    new Set(
      response.items.map(
        (vehicle) =>
          vehicle.id,
      ),
    );


  const pendingVehicles =
    pendingOperations
      .filter(
        (operation) =>
          !knownIds.has(
            operation.entity_id,
          ),
      )
      .map(
        (operation) =>
          pendingVehicleFromOperation(
            operation,
            clients,
          ),
      )
      .filter(
        (vehicle) =>
          matchesSearch(
            vehicle,
            search,
          ),
      )
      .filter(
        (vehicle) =>
          owner !== undefined || clientNumber ===
            undefined ||
          vehicle
            .current_owner_client_number ===
            clientNumber,
      );


  return {
    ...response,

    items: [
      ...pendingVehicles,
      ...response.items,
    ],

    total:
      response.total +
      pendingVehicles.length,
  };
}


export function getVehicle(
  vehicleNumber: number,
): Promise<Vehicle> {
  return apiRequest<Vehicle>(
    `/api/v1/vehicles/${vehicleNumber}`,
  );
}


export async function createVehicle(
  payload: VehicleCreate,
): Promise<Vehicle> {
  const canUseNormalApi =
    payload.owner_client_number !==
      null &&
    payload.owner_client_number > 0;


  if (canUseNormalApi) {
    try {
      return await apiRequest<Vehicle>(
        "/api/v1/vehicles",
        {
          method: "POST",

          body: JSON.stringify({
            license_plate:
              payload.license_plate,

            vin:
              payload.vin,

            brand:
              payload.brand,

            model:
              payload.model,

            year:
              payload.year,

            mileage:
              payload.mileage,

            owner_client_number:
              payload.owner_client_number,
          }),
        },
      );
    } catch (error) {
      const offline =
        error instanceof
          ApiError &&
        (
          error.status === 0 ||
          error.status >= 500
        );

      if (!offline) {
        throw error;
      }
    }
  }


  const entityId =
    createSyncUuid();


  const operation =
    await queueSyncOperation(
      "vehicle.create",

      entityId,

      {
        license_plate:
          payload.license_plate,

        vin:
          payload.vin,

        brand:
          payload.brand,

        model:
          payload.model,

        year:
          payload.year,

        mileage:
          payload.mileage,

        owner_client_id:
          payload.owner_client_id,

        owner_client_name:
          payload.owner_client_name,
      },
    );


  return pendingVehicleFromOperation(
    operation,
    [
      {
        id:
          payload.owner_client_id,

        client_number:
          payload
            .owner_client_number ??
          0,

        full_name:
          payload.owner_client_name,

        sync_pending:
          payload
            .owner_client_number ===
          null,
      },
    ],
  );
}


export function updateVehicle(
  vehicleNumber: number,
  payload: VehicleUpdate,
): Promise<Vehicle> {
  return apiRequest<Vehicle>(
    `/api/v1/vehicles/${vehicleNumber}`,
    {
      method: "PUT",

      body: JSON.stringify(
        payload,
      ),
    },
  );
}


export function transferVehicle(
  vehicleNumber: number,
  newOwnerClientNumber: number,
): Promise<Vehicle> {
  return apiRequest<Vehicle>(
    `/api/v1/vehicles/${vehicleNumber}/transfer`,
    {
      method: "POST",

      body: JSON.stringify({
        new_owner_client_number:
          newOwnerClientNumber,
      }),
    },
  );
}
