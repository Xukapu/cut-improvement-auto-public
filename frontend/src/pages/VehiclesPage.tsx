import {
  Car,
  Edit3,
  Plus,
  Search,
  UserRoundCog,
} from "lucide-react";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import type {
  FormEvent,
} from "react";

import {
  ApiError,
} from "../api/client";

import {
  listClients,
} from "../api/clients";

import {
  createVehicle,
  getVehicle,
  listVehicles,
  transferVehicle,
  updateVehicle,
} from "../api/vehicles";
import { RepairHistory } from "../components/RepairHistory";

import type {
  Client,
} from "../types/client";

import type {
  Vehicle,
} from "../types/vehicle";


type FormState = {
  license_plate: string;
  vin: string;
  brand: string;
  model: string;
  year: string;
  mileage: string;
};


type Props = {
  initialVehicleNumber: number | null;
  onOpenWorkOrder: (workOrderNumber: number) => void;
};


const emptyForm: FormState = {
  license_plate: "",
  vin: "",
  brand: "",
  model: "",
  year: "",
  mileage: "",
};


function getError(
  error: unknown,
): string {
  return error instanceof ApiError
    ? error.message
    : "Произошла ошибка.";
}


function vehicleNumberLabel(
  vehicle: Vehicle,
): string {
  return vehicle.sync_pending
    ? "Ожидает синхронизации"
    : `№${vehicle.vehicle_number}`;
}


function clientLabel(
  client: Client,
): string {
  return client.sync_pending
    ? `${client.full_name} · ожидает синхронизации`
    : `№${client.client_number} · ${client.full_name}`;
}


type OwnerPickerProps = {
  clients: Client[];
  owner: Client | null;
  onChange: (owner: Client | null) => void;
  currentOwnerNumber?: number;
};

type OwnerSearchResult = { search: string; items: Client[]; error: string };

function OwnerPicker({ clients, owner, onChange, currentOwnerNumber }: OwnerPickerProps) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState<OwnerSearchResult | null>(null);
  const search = query.trim();

  useEffect(() => {
    if (!search) {
      return;
    }

    let active = true;
    const timer = window.setTimeout(() => {
      listClients(search)
        .then(({ items }) => {
          if (active) {
            setResult({ search, items, error: "" });
          }
        })
        .catch((error: unknown) => {
          if (active) {
            setResult({ search, items: [], error: getError(error) });
          }
        });
    }, 200);

    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [search]);

  const currentResult = search && result?.search === search ? result : null;
  const matches = search ? (currentResult?.items ?? []) : clients;
  const choices = currentOwnerNumber === undefined ? matches : matches.filter(
    (client) => !client.sync_pending && client.client_number > 0 &&
      client.client_number !== currentOwnerNumber,
  );
  const options = owner && !choices.some((client) => client.id === owner.id)
    ? [owner, ...choices]
    : choices;

  return (
    <>
      <label className="form-wide">
        <span>Поиск владельца</span>
        <input
          type="search"
          maxLength={200}
          placeholder="Имя, телефон или номер клиента"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      <label className="form-wide">
        <span>{currentOwnerNumber === undefined ? "Владелец *" : "Новый владелец"}</span>
        <select
          required
          value={owner?.id ?? ""}
          onChange={(event) => onChange(
            options.find((client) => client.id === event.target.value) ?? null,
          )}
        >
          <option value="">Выберите клиента</option>
          {options.map((client) => (
            <option key={client.id} value={client.id}>{clientLabel(client)}</option>
          ))}
        </select>
      </label>
      {search && !currentResult && <small className="form-wide" role="status">Ищем клиентов…</small>}
      {currentResult?.error && <small className="form-wide" role="alert">{currentResult.error}</small>}
      {currentResult && !currentResult.error && choices.length === 0 && (
        <small className="form-wide" role="status">Клиенты не найдены.</small>
      )}
    </>
  );
}


export function VehiclesPage({ initialVehicleNumber, onOpenWorkOrder }: Props) {
  const [
    items,
    setItems,
  ] = useState<Vehicle[]>([]);

  const [
    clients,
    setClients,
  ] = useState<Client[]>([]);

  const [
    selected,
    setSelected,
  ] = useState<Vehicle | null>(
    null,
  );

  const [requestedVehicle, setRequestedVehicle] = useState(initialVehicleNumber);
  const [cardError, setCardError] = useState("");
  const detailHeadingRef = useRef<HTMLHeadingElement>(null);
  const selectedVehicleNumber = selected?.vehicle_number;

  const [
    search,
    setSearch,
  ] = useState("");

  const [
    loading,
    setLoading,
  ] = useState(true);

  const [
    error,
    setError,
  ] = useState("");

  const [
    formOpen,
    setFormOpen,
  ] = useState(false);

  const [
    editing,
    setEditing,
  ] = useState<Vehicle | null>(
    null,
  );

  const [
    saving,
    setSaving,
  ] = useState(false);

  const [
    form,
    setForm,
  ] = useState<FormState>(
    emptyForm,
  );

  const [
    transferOpen,
    setTransferOpen,
  ] = useState(false);

  const [owner, setOwner] = useState<Client | null>(null);


  useEffect(() => {
    if (requestedVehicle === null) {
      return;
    }

    let active = true;

    getVehicle(requestedVehicle)
      .then((vehicle) => {
        if (active) {
          setSelected((current) => current ?? vehicle);
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setCardError(getError(error));
        }
      })
      .finally(() => {
        if (active) {
          setRequestedVehicle(null);
        }
      });

    return () => {
      active = false;
    };
  }, [requestedVehicle]);


  useEffect(() => {
    if (initialVehicleNumber !== null && selectedVehicleNumber === initialVehicleNumber) {
      detailHeadingRef.current?.focus();
    }
  }, [initialVehicleNumber, selectedVehicleNumber]);


  const refresh =
    useCallback(
      async () => {
        const [
          vehicleData,
          clientData,
        ] = await Promise.all([
          listVehicles(
            search,
          ),

          listClients(
            "",
            100,
          ),
        ]);

        setItems(
          vehicleData.items,
        );

        setClients(
          clientData.items,
        );

        setSelected(
          (current) => {
            if (!current) {
              return null;
            }

            return (
              vehicleData.items.find(
                (vehicle) =>
                  vehicle.id ===
                  current.id,
              ) ??
              current
            );
          },
        );

        setError("");
        setLoading(false);
      },
      [
        search,
      ],
    );


  useEffect(() => {
    const timer =
      window.setTimeout(
        () => {
          void refresh().catch(
            (loadError) => {
              setError(
                getError(
                  loadError,
                ),
              );

              setLoading(
                false,
              );
            },
          );
        },
        200,
      );

    return () => {
      window.clearTimeout(
        timer,
      );
    };
  }, [
    refresh,
  ]);


  useEffect(() => {
    function handleSyncCompleted() {
      void refresh().catch(
        (loadError) => {
          setError(
            getError(
              loadError,
            ),
          );
        },
      );
    }

    window.addEventListener(
      "cut-sync-completed",
      handleSyncCompleted,
    );

    return () => {
      window.removeEventListener(
        "cut-sync-completed",
        handleSyncCompleted,
      );
    };
  }, [
    refresh,
  ]);


  function openCreate() {
    setRequestedVehicle(null);
    setCardError("");
    setEditing(null);
    setOwner(null);
    setForm(emptyForm);
    setFormOpen(true);
  }


  function openEdit(
    vehicle: Vehicle,
  ) {
    if (vehicle.sync_pending) {
      return;
    }

    setEditing(
      vehicle,
    );

    setForm({
      license_plate:
        vehicle.license_plate,

      vin:
        vehicle.vin ??
        "",

      brand:
        vehicle.brand,

      model:
        vehicle.model,

      year:
        vehicle.year !== null
          ? String(
              vehicle.year,
            )
          : "",

      mileage:
        vehicle.mileage !== null
          ? String(
              vehicle.mileage,
            )
          : "",
    });

    setFormOpen(true);
  }


  async function submit(
    event: FormEvent,
  ) {
    event.preventDefault();

    setSaving(true);
    setError("");


    const base = {
      license_plate:
        form.license_plate
          .trim(),

      vin:
        form.vin.trim() ||
        null,

      brand:
        form.brand.trim(),

      model:
        form.model.trim(),

      year:
        form.year
          ? Number(
              form.year,
            )
          : null,

      mileage:
        form.mileage
          ? Number(
              form.mileage,
            )
          : null,
    };


    try {
      let result:
        Vehicle;


      if (editing) {
        result =
          await updateVehicle(
            editing
              .vehicle_number,

            base,
          );
      } else {
        if (!owner) {
          throw new ApiError(
            0,
            "Выберите владельца автомобиля.",
          );
        }


        result =
          await createVehicle({
            ...base,

            owner_client_id:
              owner.id,

            owner_client_number:
              owner.sync_pending
                ? null
                : owner
                    .client_number,

            owner_client_name:
              owner.full_name,
          });
      }


      setSelected(
        result,
      );

      setFormOpen(
        false,
      );

      await refresh();
    } catch (saveError) {
      setError(
        getError(
          saveError,
        ),
      );
    } finally {
      setSaving(
        false,
      );
    }
  }


  async function doTransfer() {
    if (
      !selected ||
      selected.sync_pending ||
      !owner ||
      owner.sync_pending ||
      saving
    ) {
      return;
    }

    setSaving(true);
    try {
      const result =
        await transferVehicle(
          selected
            .vehicle_number,

          owner.client_number,
        );

      setSelected(
        result,
      );

      setTransferOpen(
        false,
      );

      await refresh();
    } catch (transferError) {
      setError(
        getError(
          transferError,
        ),
      );
    } finally {
      setSaving(false);
    }
  }


  return (
    <section className="data-page">
      <div className="section-toolbar">
        <div className="search-field">
          <Search size={17} />

          <input
            placeholder="Госномер, VIN, марка или модель"
            value={search}
            onChange={(event) => {
              setSearch(
                event.target.value,
              );
            }}
          />
        </div>

        <button
          className="primary-inline-button"
          disabled={
            clients.length ===
            0
          }
          onClick={
            openCreate
          }
          type="button"
        >
          <Plus size={17} />
          Новый автомобиль
        </button>
      </div>


      {error && (
        <div className="dashboard-error">
          {error}
        </div>
      )}


      <div className="master-detail">
        <div className="data-card">
          <div className="data-card-title">
            <div>
              <span className="card-kicker">
                Автопарк
              </span>

              <h2>
                Автомобили
              </h2>
            </div>

            <strong className="count-badge">
              {items.length}
            </strong>
          </div>


          {loading ? (
            <div className="empty-state">
              Загружаем...
            </div>
          ) : items.length ===
            0 ? (
            <div className="empty-state">
              <Car size={28} />

              <div>
                <strong>
                  Автомобили не найдены
                </strong>
              </div>
            </div>
          ) : (
            <div className="data-list">
              {items.map(
                (vehicle) => (
                  <button
                    className={
                      selected
                        ?.id ===
                      vehicle.id
                        ? "data-row data-row-active"
                        : "data-row"
                    }
                    key={
                      vehicle.id
                    }
                    onClick={() => {
                      setRequestedVehicle(null);
                      setCardError("");
                      setSelected(
                        vehicle,
                      );
                    }}
                    type="button"
                  >
                    <span className="row-number">
                      {vehicleNumberLabel(
                        vehicle,
                      )}
                    </span>

                    <span className="row-primary">
                      <strong>
                        {vehicle.brand}{" "}
                        {vehicle.model}
                      </strong>

                      <small>
                        {
                          vehicle
                            .license_plate
                        }
                      </small>
                    </span>

                    <span className="row-meta">
                      {vehicle.mileage !==
                      null
                        ? `${vehicle.mileage.toLocaleString(
                            "ru-RU",
                          )} км`
                        : "—"}
                    </span>
                  </button>
                ),
              )}
            </div>
          )}
        </div>


        <div className="detail-card">
          {!selected ? (
            <div className="detail-empty">
              <Car size={34} />

              <strong>
                {requestedVehicle !== null
                  ? "Открываем автомобиль…"
                  : "Выбери автомобиль"}
              </strong>

              {cardError && <span role="alert">{cardError}</span>}
            </div>
          ) : (
            <>
              <div className="detail-header">
                <div>
                  <span className="card-kicker">
                    Автомобиль{" "}
                    {vehicleNumberLabel(
                      selected,
                    )}
                  </span>

                  <h2 ref={detailHeadingRef} tabIndex={-1}>
                    {selected.brand}{" "}
                    {selected.model}
                  </h2>
                </div>

                <button
                  className="icon-action"
                  disabled={
                    Boolean(
                      selected
                        .sync_pending,
                    )
                  }
                  onClick={() => {
                    openEdit(
                      selected,
                    );
                  }}
                  title="Изменить"
                  type="button"
                >
                  <Edit3 size={18} />
                </button>
              </div>


              <dl className="detail-grid">
                <div>
                  <dt>
                    Госномер *
                  </dt>

                  <dd>
                    {
                      selected
                        .license_plate
                    }
                  </dd>
                </div>

                <div>
                  <dt>
                    VIN (необязательно)
                  </dt>

                  <dd>
                    {selected.vin ||
                      "—"}
                  </dd>
                </div>

                <div>
                  <dt>
                    Год (необязательно)
                  </dt>

                  <dd>
                    {selected.year ??
                      "—"}
                  </dd>
                </div>

                <div>
                  <dt>
                    Пробег (необязательно)
                  </dt>

                  <dd>
                    {selected.mileage !==
                    null
                      ? `${selected.mileage.toLocaleString(
                          "ru-RU",
                        )} км`
                      : "—"}
                  </dd>
                </div>

                <div className="detail-wide">
                  <dt>
                    Текущий владелец
                  </dt>

                  <dd>
                    {
                      selected
                        .current_owner_name
                    }

                    {" · "}

                    {selected
                      .current_owner_client_number >
                    0
                      ? `Клиент №${selected.current_owner_client_number}`
                      : "ожидает синхронизации"}
                  </dd>
                </div>
              </dl>

              {!selected.sync_pending && selected.vehicle_number > 0 && (
                <RepairHistory
                  key={selected.vehicle_number}
                  scope="vehicle"
                  recordNumber={selected.vehicle_number}
                  onOpenWorkOrder={onOpenWorkOrder}
                />
              )}

              <div className="detail-actions">
                <button
                  className="secondary-button"
                  disabled={
                    Boolean(
                      selected
                        .sync_pending,
                    )
                  }
                  onClick={() => {
                    setOwner(null);
                    setTransferOpen(
                      true,
                    );
                  }}
                  type="button"
                >
                  <UserRoundCog
                    size={17}
                  />
                  Сменить владельца
                </button>
              </div>
            </>
          )}
        </div>
      </div>


      {formOpen && (
        <div className="modal-backdrop">
          <form
            className="modal-card"
            onSubmit={
              submit
            }
          >
            <div className="modal-header">
              <span className="card-kicker">
                {editing
                  ? `Автомобиль №${editing.vehicle_number}`
                  : "Новая карточка"}
              </span>

              <h2>
                {editing
                  ? "Изменить автомобиль"
                  : "Новый автомобиль"}
              </h2>
            </div>


            <div className="form-grid">
              <label>
                <span>
                  Марка *
                </span>

                <input
                  required
                  value={
                    form.brand
                  }
                  onChange={(event) => {
                    setForm({
                      ...form,

                      brand:
                        event.target
                          .value,
                    });
                  }}
                />
              </label>


              <label>
                <span>
                  Модель *
                </span>

                <input
                  required
                  value={
                    form.model
                  }
                  onChange={(event) => {
                    setForm({
                      ...form,

                      model:
                        event.target
                          .value,
                    });
                  }}
                />
              </label>


              <label>
                <span>
                  Госномер *
                </span>

                <input
                  required
                  value={
                    form
                      .license_plate
                  }
                  onChange={(event) => {
                    setForm({
                      ...form,

                      license_plate:
                        event.target
                          .value,
                    });
                  }}
                />
              </label>


              <label>
                <span>
                  VIN (необязательно)
                </span>

                <input
                  value={
                    form.vin
                  }
                  onChange={(event) => {
                    setForm({
                      ...form,

                      vin:
                        event.target
                          .value,
                    });
                  }}
                />
              </label>


              <label>
                <span>
                  Год (необязательно)
                </span>

                <input
                  min="1900"
                  max="2100"
                  type="number"
                  value={
                    form.year
                  }
                  onChange={(event) => {
                    setForm({
                      ...form,

                      year:
                        event.target
                          .value,
                    });
                  }}
                />
              </label>


              <label>
                <span>
                  Пробег (необязательно)
                </span>

                <input
                  min="0"
                  type="number"
                  value={
                    form.mileage
                  }
                  onChange={(event) => {
                    setForm({
                      ...form,

                      mileage:
                        event.target
                          .value,
                    });
                  }}
                />
              </label>


              {!editing && (
                <OwnerPicker clients={clients} owner={owner} onChange={setOwner} />
              )}
            </div>


            <div className="modal-actions">
              <button
                className="secondary-button"
                onClick={() => {
                  setFormOpen(
                    false,
                  );
                }}
                type="button"
              >
                Отмена
              </button>

              <button
                className="primary-inline-button"
                disabled={
                  saving
                }
                type="submit"
              >
                {saving
                  ? "Сохраняем..."
                  : "Сохранить"}
              </button>
            </div>
          </form>
        </div>
      )}


      {transferOpen &&
        selected && (
          <div className="modal-backdrop">
            <div className="modal-card modal-small">
              <div className="modal-header">
                <span className="card-kicker">
                  {
                    selected
                      .license_plate
                  }
                </span>

                <h2>
                  Смена владельца
                </h2>
              </div>

              <div className="form-grid">
                <OwnerPicker
                  clients={clients}
                  owner={owner}
                  onChange={setOwner}
                  currentOwnerNumber={selected.current_owner_client_number}
                />
              </div>

              <div className="modal-actions">
                <button
                  className="secondary-button"
                  disabled={saving}
                  onClick={() => {
                    setTransferOpen(
                      false,
                    );
                  }}
                  type="button"
                >
                  Отмена
                </button>

                <button
                  className="primary-inline-button"
                  disabled={!owner || saving}
                  onClick={() => {
                    void doTransfer();
                  }}
                  type="button"
                >
                  {saving ? "Сохраняем…" : "Сменить владельца"}
                </button>
              </div>
            </div>
          </div>
        )}
    </section>
  );
}
