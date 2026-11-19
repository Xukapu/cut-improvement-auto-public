import {
  CalendarClock,
  Edit3,
  Plus,
  RotateCcw,
  UserX,
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
  cancelAppointment,
  createAppointment,
  listAppointments,
  updateAppointment,
} from "../api/appointments";

import {
  ApiError,
} from "../api/client";

import {
  getClient,
  listClients,
} from "../api/clients";

import {
  getVehicle,
  listVehicles,
} from "../api/vehicles";

import type {
  Appointment,
  AppointmentInput,
  AppointmentStatus,
} from "../types/appointment";

import type { CurrentUser } from "../types/auth";

import type {
  Client,
} from "../types/client";

import type {
  Vehicle,
} from "../types/vehicle";

import {
  formatDate,
  formatTime,
  todayIso,
} from "../utils/format";


type FormState = {
  appointment_date: string;
  appointment_time: string;

  reason: string;
  comment: string;

  status:
    AppointmentStatus;
};


function errorMessage(
  error: unknown,
): string {
  if (
    error instanceof ApiError
  ) {
    return error.message;
  }

  if (
    error instanceof Error &&
    error.message
  ) {
    return error.message;
  }

  return (
    "Не удалось выполнить операцию."
  );
}


function appointmentLabel(
  appointment:
    Appointment,
): string {
  return appointment.sync_pending
    ? "Ожидает синхронизации"
    : `Запись №${appointment.appointment_number}`;
}


function clientLabel(
  client: Client,
): string {
  return client.sync_pending
    ? `${client.full_name} · ожидает синхронизации`
    : `№${client.client_number} · ${client.full_name} · ${client.phone_primary}`;
}


type LookupResult<T> = { query: string; items: T[]; total: number; error?: string };

const appointmentStatusLabels: Record<AppointmentStatus, string> = {
  scheduled: "Запланирована",
  no_show: "Не приехал",
  cancelled: "Отменена",
};

function includeSelection<T extends { id: string }>(items: T[], selected: T | null): T[] {
  return selected && !items.some((item) => item.id === selected.id)
    ? [selected, ...items]
    : items;
}

function lookupStatus<T>(result: LookupResult<T> | null, emptyMessage: string): string {
  if (!result) return "Загрузка…";
  if (result.error) return result.error;
  if (!result.items.length) return emptyMessage;
  return result.total > result.items.length ? "Уточните запрос, чтобы увидеть другие результаты." : "";
}

export function AppointmentsPage({ currentUser }: { currentUser: CurrentUser }) {
  const canManage = currentUser.role === "owner" || currentUser.role === "tech_admin";
  const [
    date,
    setDate,
  ] = useState(
    todayIso(),
  );

  const [
    items,
    setItems,
  ] = useState<
    Appointment[]
  >([]);

  const [clientSearch, setClientSearch] = useState("");
  const [vehicleSearch, setVehicleSearch] = useState("");
  const [clientLookup, setClientLookup] = useState<LookupResult<Client> | null>(null);
  const [vehicleLookup, setVehicleLookup] = useState<(LookupResult<Vehicle> & { ownerId: string }) | null>(null);
  const [selectedClient, setSelectedClient] = useState<Client | null>(null);
  const [selectedVehicle, setSelectedVehicle] = useState<Vehicle | null>(null);
  const [directoryRevision, setDirectoryRevision] = useState(0);
  const scheduleRequest = useRef(0);
  const formRequest = useRef(0);

  const [
    loading,
    setLoading,
  ] = useState(true);

  const [
    pageError,
    setPageError,
  ] = useState("");

  const [
    formError,
    setFormError,
  ] = useState("");

  const [
    formOpen,
    setFormOpen,
  ] = useState(false);

  const [
    editing,
    setEditing,
  ] = useState<
    Appointment | null
  >(null);

  const [
    saving,
    setSaving,
  ] = useState(false);

  const [
    form,
    setForm,
  ] = useState<FormState>({
    appointment_date:
      todayIso(),

    appointment_time:
      "09:00",

    reason: "",
    comment: "",

    status:
      "scheduled",
  });


  const loadData = useCallback(async (targetDate: string) => {
    const request = ++scheduleRequest.current;
    try {
      const data = await listAppointments({ date: targetDate });
      if (request === scheduleRequest.current) {
        setItems(data.items);
        setPageError("");
        setLoading(false);
      }
    } catch (error) {
      if (request === scheduleRequest.current) throw error;
    }
  }, []);

  useEffect(() => {
    if (!canManage) return;
    void listClients().catch(() => undefined);
    void listVehicles().catch(() => undefined);
  }, [canManage]);

  useEffect(() => {
    let active = true;

    const timer =
      window.setTimeout(
        () => {
          void loadData(
            date,
          ).catch(
            (error) => {
              if (!active) {
                return;
              }

              setPageError(
                errorMessage(
                  error,
                ),
              );

              setLoading(false);
            },
          );
        },
        0,
      );

    return () => {
      active = false;

      window.clearTimeout(
        timer,
      );
    };
  }, [
    date,
    loadData,
  ]);


  useEffect(() => {
    function handleSyncCompleted() {
      setDirectoryRevision((revision) => revision + 1);
      void loadData(
        date,
      ).catch(
        (error) => {
          setPageError(
            errorMessage(
              error,
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
    date,
    loadData,
  ]);


  useEffect(() => {
    if (!canManage || !formOpen) return;

    let active = true;
    const timer = window.setTimeout(() => {
      void listClients(clientSearch)
        .then((result) => {
          if (active) setClientLookup({ ...result, query: clientSearch });
        })
        .catch((error: unknown) => {
          if (active) {
            setClientLookup({ query: clientSearch, items: [], total: 0, error: errorMessage(error) });
          }
        });
    }, clientSearch ? 250 : 0);

    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [canManage, clientSearch, directoryRevision, formOpen]);

  useEffect(() => {
    if (!canManage || !formOpen || !selectedClient) return;

    let active = true;
    const ownerId = selectedClient.id;
    const timer = window.setTimeout(() => {
      void listVehicles(vehicleSearch, 100, selectedClient)
        .then((result) => {
          if (active) setVehicleLookup({ ...result, query: vehicleSearch, ownerId });
        })
        .catch((error: unknown) => {
          if (active) {
            setVehicleLookup({ query: vehicleSearch, ownerId, items: [], total: 0, error: errorMessage(error) });
          }
        });
    }, vehicleSearch ? 250 : 0);

    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [canManage, directoryRevision, formOpen, selectedClient, vehicleSearch]);

  const clientResults = clientLookup?.query === clientSearch ? clientLookup : null;
  const vehicleResults = vehicleLookup?.query === vehicleSearch && vehicleLookup.ownerId === selectedClient?.id
    ? vehicleLookup
    : null;
  const clientOptions = includeSelection(clientResults?.items ?? [], selectedClient);
  const vehicleOptions = includeSelection(vehicleResults?.items ?? [], selectedVehicle);

  function resetLookup() {
    setClientSearch("");
    setVehicleSearch("");
    setClientLookup(null);
    setVehicleLookup(null);
  }

  function openCreate() {
    if (!canManage) return;
    formRequest.current += 1;
    setFormError("");
    setEditing(null);
    setSelectedClient(null);
    setSelectedVehicle(null);
    resetLookup();
    setForm({
      appointment_date: date,
      appointment_time: "09:00",
      reason: "",
      comment: "",
      status: "scheduled",
    });
    setFormOpen(true);
  }

  async function openEdit(appointment: Appointment) {
    if (!canManage || appointment.sync_pending) return;

    const request = ++formRequest.current;
    const [client, vehicle] = await Promise.allSettled([
      getClient(appointment.client_number),
      getVehicle(appointment.vehicle_number),
    ]);
    if (request !== formRequest.current) return;

    const currentClient = client.status === "fulfilled" ? client.value : null;
    const currentVehicle = vehicle.status === "fulfilled" ? vehicle.value : null;

    setFormError("");
    setEditing(appointment);
    setSelectedClient(currentClient);
    setSelectedVehicle(
      currentClient && currentClient.client_number === currentVehicle?.current_owner_client_number
        ? currentVehicle
        : null,
    );
    resetLookup();
    setForm({
      appointment_date: appointment.appointment_date,
      appointment_time: appointment.appointment_time.slice(0, 5),
      reason: appointment.reason,
      comment: appointment.comment ?? "",
      status: appointment.status,
    });
    setFormOpen(true);
  }

  function validateForm():
    string | null {
    if (
      !selectedClient
    ) {
      return (
        "Выберите клиента."
      );
    }

    if (!selectedVehicle) {
      return (
        "Выберите автомобиль клиента."
      );
    }

    if (
      !form.appointment_date
    ) {
      return (
        "Укажите дату записи."
      );
    }

    if (
      !form.appointment_time
    ) {
      return (
        "Укажите время записи."
      );
    }

    if (
      !form.reason.trim()
    ) {
      return (
        "Укажите причину обращения."
      );
    }

    return null;
  }


  function buildUpdatePayload():
    AppointmentInput {
    if (!selectedClient) {
      throw new Error(
        "Клиент не выбран.",
      );
    }

    if (
      !selectedVehicle ||
      selectedClient
        .sync_pending ||
      selectedVehicle
        .sync_pending
    ) {
      throw new Error(
        "Запись ещё не синхронизирована.",
      );
    }

    return {
      client_number:
        selectedClient
          .client_number,

      vehicle_number:
        selectedVehicle
          .vehicle_number,

      appointment_date:
        form.appointment_date,

      appointment_time:
        form.appointment_time,

      reason:
        form.reason.trim(),

      comment:
        form.comment
          .trim() ||
        null,

      status:
        form.status,
    };
  }


  async function submit(
    event: FormEvent,
  ) {
    event.preventDefault();
    if (!canManage) return;

    setFormError("");

    const validation =
      validateForm();

    if (validation) {
      setFormError(
        validation,
      );

      return;
    }

    if (!selectedClient || !selectedVehicle) return;


    setSaving(true);


    try {
      if (editing) {
        await updateAppointment(
          editing
            .appointment_number,

          buildUpdatePayload(),
        );
      } else {
        await createAppointment({
          client_id:
            selectedClient.id,

          client_number:
            selectedClient
              .sync_pending
              ? null
              : selectedClient
                  .client_number,

          client_name:
            selectedClient
              .full_name,

          vehicle_id:
            selectedVehicle.id,

          vehicle_number:
            selectedVehicle
              .sync_pending
              ? null
              : selectedVehicle
                  .vehicle_number,

          license_plate:
            selectedVehicle
              .license_plate,

          appointment_date:
            form.appointment_date,

          appointment_time:
            form.appointment_time,

          reason:
            form.reason
              .trim(),

          comment:
            form.comment
              .trim() ||
            null,

          status:
            form.status,
        });
      }


      const targetDate = form.appointment_date;

      setDate(targetDate);
      setFormOpen(false);
      setEditing(null);
      setFormError("");

      if (targetDate === date) {
        await loadData(targetDate).catch((error: unknown) => setPageError(errorMessage(error)));
      }
    } catch (error) {
      setFormError(
        errorMessage(
          error,
        ),
      );
    } finally {
      setSaving(
        false,
      );
    }
  }


  async function changeStatus(
    appointment:
      Appointment,

    status:
      AppointmentStatus,
  ) {
    if (!canManage || appointment.sync_pending) return;

    setPageError("");


    try {
      await updateAppointment(
        appointment
          .appointment_number,

        {
          client_number:
            appointment
              .client_number,

          vehicle_number:
            appointment
              .vehicle_number,

          appointment_date:
            appointment
              .appointment_date,

          appointment_time:
            appointment
              .appointment_time,

          reason:
            appointment.reason,

          comment:
            appointment.comment,

          status,
        },
      );

      await loadData(
        date,
      );
    } catch (error) {
      setPageError(
        errorMessage(
          error,
        ),
      );
    }
  }


  async function cancelBooking(appointment: Appointment) {
    if (!canManage || appointment.sync_pending) return;
    if (!window.confirm("Отменить запись?")) return;
    setPageError("");
    try {
      await cancelAppointment(appointment.appointment_number);
      await loadData(date);
    } catch (error) {
      setPageError(errorMessage(error));
    }
  }

  return (
    <section className="data-page">
      <div className="section-toolbar">
        <label className="date-filter">
          <span>
            Дата записей
          </span>

          <input
            type="date"
            value={date}
            onChange={(
              event,
            ) => {
              setDate(
                event.target
                  .value,
              );
            }}
          />
        </label>

        {canManage && (
          <button className="primary-inline-button" onClick={openCreate} type="button">
            <Plus size={17} />
            Новая запись
          </button>
        )}
      </div>


      {pageError && (
        <div className="dashboard-error">
          {pageError}
        </div>
      )}


      <div className="data-card full-width-card">
        <div className="data-card-title">
          <div>
            <span className="card-kicker">
              {formatDate(
                date,
              )}
            </span>

            <h2>
              Записи клиентов
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
            <CalendarClock
              size={30}
            />

            <div>
              <strong>
                На эту дату записей нет
              </strong>
            </div>
          </div>
        ) : (
          <div className="appointment-page-list">
            {items.map(
              (appointment) => (
                <article
                  className={
                    appointment
                      .status ===
                    "no_show"
                      ? "appointment-card appointment-card-muted"
                      : "appointment-card"
                  }
                  key={
                    appointment.id
                  }
                >
                  <div className="appointment-card-time">
                    {formatTime(
                      appointment
                        .appointment_time,
                    )}
                  </div>

                  <div className="appointment-card-main">
                    <strong>
                      {
                        appointment
                          .client_name
                      }
                    </strong>

                    <span>
                      {
                        appointment
                          .license_plate
                      }

                      {" · "}

                      {appointmentLabel(
                        appointment,
                      )}
                    </span>

                    <p>
                      {
                        appointment
                          .reason
                      }
                    </p>
                  </div>

                  {canManage ? (
                    <div className="appointment-card-actions">
                      <button
                        className="icon-action"
                        disabled={Boolean(appointment.sync_pending)}
                        onClick={() => { void openEdit(appointment); }}
                        title="Изменить"
                        type="button"
                      >
                        <Edit3 size={17} />
                      </button>

                      {appointment.status === "scheduled" && (
                        <>
                          <button
                            className="danger-soft-button"
                            disabled={Boolean(appointment.sync_pending)}
                            onClick={() => { void changeStatus(appointment, "no_show"); }}
                            type="button"
                          >
                            <UserX size={16} />
                            Не приехал
                          </button>
                          <button
                            className="danger-soft-button"
                            disabled={Boolean(appointment.sync_pending)}
                            onClick={() => { void cancelBooking(appointment); }}
                            type="button"
                          >
                            Отменить запись
                          </button>
                        </>
                      )}

                      {appointment.status === "no_show" && (
                        <button
                          className="secondary-button"
                          disabled={Boolean(appointment.sync_pending)}
                          onClick={() => { void changeStatus(appointment, "scheduled"); }}
                          type="button"
                        >
                          <RotateCcw size={16} />
                          Вернуть
                        </button>
                      )}

                      {appointment.status === "cancelled" && <span>Отмена</span>}
                    </div>
                  ) : (
                    <span className="appointment-card-actions">{appointmentStatusLabels[appointment.status]}</span>
                  )}
                </article>
              ),
            )}
          </div>
        )}
      </div>


      {canManage && formOpen && (
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
                  ? `Запись №${editing.appointment_number}`
                  : "Новая запись"}
              </span>

              <h2>
                {editing
                  ? "Изменить запись"
                  : "Записать клиента"}
              </h2>
            </div>


            {formError && (
              <div className="form-error">
                <strong>
                  Не удалось сохранить запись
                </strong>

                <span>
                  {formError}
                </span>
              </div>
            )}


            <div className="form-grid">
              <label>
                <span>Найти клиента</span>
                <input
                  type="search"
                  placeholder="Имя, телефон или номер"
                  maxLength={200}
                  disabled={saving}
                  value={clientSearch}
                  onChange={(event) => {
                    setClientSearch(event.target.value);
                    setClientLookup(null);
                  }}
                />
                <small aria-live="polite">{lookupStatus(clientResults, "Клиенты не найдены.")}</small>
              </label>

              <label>
                <span>Найти автомобиль клиента</span>
                <input
                  type="search"
                  placeholder="Госномер, VIN или модель"
                  maxLength={200}
                  disabled={saving || !selectedClient}
                  value={vehicleSearch}
                  onChange={(event) => {
                    setVehicleSearch(event.target.value);
                    setVehicleLookup(null);
                  }}
                />
                <small aria-live="polite">
                  {selectedClient ? lookupStatus(vehicleResults, "Автомобили не найдены.") : "Сначала выберите клиента."}
                </small>
              </label>

              <label>
                <span>Клиент *</span>
                <select
                  required
                  disabled={saving || (!clientResults && !selectedClient)}
                  value={selectedClient?.id ?? ""}
                  onChange={(event) => {
                    const client = clientOptions.find((item) => item.id === event.target.value) ?? null;
                    setSelectedClient(client);
                    setSelectedVehicle(null);
                    setVehicleSearch("");
                    setVehicleLookup(null);
                    setFormError("");
                  }}
                >
                  <option value="">Выберите клиента</option>
                  {clientOptions.map((client) => (
                    <option key={client.id} value={client.id}>
                      {clientLabel(client)}
                    </option>
                  ))}
                </select>
              </label>

              <label>
                <span>Автомобиль *</span>
                <select
                  required
                  disabled={saving || !selectedClient || (!vehicleResults && !selectedVehicle)}
                  value={selectedVehicle?.id ?? ""}
                  onChange={(event) => {
                    setSelectedVehicle(vehicleOptions.find((item) => item.id === event.target.value) ?? null);
                    setFormError("");
                  }}
                >
                  <option value="">Выберите автомобиль</option>
                  {vehicleOptions.map((vehicle) => (
                    <option key={vehicle.id} value={vehicle.id}>
                      {vehicle.brand} {vehicle.model} · {vehicle.license_plate}
                      {vehicle.sync_pending ? " · ожидает синхронизации" : ""}
                    </option>
                  ))}
                </select>
              </label>

              <label>
                <span>
                  Дата *
                </span>

                <input
                  required
                  type="date"
                  value={
                    form
                      .appointment_date
                  }
                  onChange={(
                    event,
                  ) => {
                    setFormError("");

                    setForm({
                      ...form,

                      appointment_date:
                        event.target
                          .value,
                    });
                  }}
                />
              </label>


              <label>
                <span>
                  Время *
                </span>

                <input
                  required
                  type="time"
                  value={
                    form
                      .appointment_time
                  }
                  onChange={(
                    event,
                  ) => {
                    setFormError("");

                    setForm({
                      ...form,

                      appointment_time:
                        event.target
                          .value,
                    });
                  }}
                />
              </label>


              <label className="form-wide">
                <span>
                  Причина обращения *
                </span>

                <input
                  required
                  value={
                    form.reason
                  }
                  onChange={(
                    event,
                  ) => {
                    setFormError("");

                    setForm({
                      ...form,

                      reason:
                        event.target
                          .value,
                    });
                  }}
                />
              </label>


              <label className="form-wide">
                <span>
                  Комментарий{" "}
                  <small>
                    (необязательно)
                  </small>
                </span>

                <textarea
                  rows={3}
                  value={
                    form.comment
                  }
                  onChange={(
                    event,
                  ) => {
                    setForm({
                      ...form,

                      comment:
                        event.target
                          .value,
                    });
                  }}
                />
              </label>
            </div>


            <div className="modal-actions">
              <button
                className="secondary-button"
                disabled={
                  saving
                }
                onClick={() => {
                  setFormOpen(
                    false,
                  );

                  setFormError("");
                }}
                type="button"
              >
                Отмена
              </button>

              <button
                className="primary-inline-button"
                disabled={
                  saving || !selectedClient || !selectedVehicle
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
    </section>
  );
}
