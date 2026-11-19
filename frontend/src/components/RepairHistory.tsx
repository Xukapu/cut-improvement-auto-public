import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import { listWorkOrders } from "../api/workOrders";
import type { WorkOrderListResponse, WorkOrderStatus } from "../types/workOrder";
import "../styles/repair-history.css";

type Props = {
  scope: "client" | "vehicle";
  recordNumber: number;
  onOpenWorkOrder: (workOrderNumber: number) => void;
};

const pageSize = 10;
const orderDate = new Intl.DateTimeFormat("ru-RU");
const statusLabels: Record<WorkOrderStatus, string> = {
  planned: "Запланирован",
  in_progress: "В работе",
  ready: "Готов",
  issued: "Выдан",
};

export function RepairHistory({ scope, recordNumber, onOpenWorkOrder }: Props) {
  const [offset, setOffset] = useState(0);
  const [result, setResult] = useState<WorkOrderListResponse | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    let active = true;
    const filter = scope === "client"
      ? { clientNumber: recordNumber }
      : { vehicleNumber: recordNumber };

    listWorkOrders({ ...filter, limit: pageSize, offset })
      .then((data) => {
        if (active) {
          setResult(data);
        }
      })
      .catch((requestError: unknown) => {
        if (active) {
          setError(requestError instanceof ApiError
            ? requestError.message
            : "Не удалось загрузить историю ремонтов.");
        }
      });

    return () => {
      active = false;
    };
  }, [offset, recordNumber, retry, scope]);

  function changePage(nextOffset: number) {
    setResult(null);
    setOffset(nextOffset);
  }

  return (
    <section className="repair-history" aria-label="История ремонтов">
      <h3>История ремонтов</h3>
      {error && (
        <div className="repair-history-error">
          <span role="alert">{error}</span>
          <button className="secondary-button" type="button" onClick={() => {
            setError("");
            setRetry((attempt) => attempt + 1);
          }}>
            Повторить
          </button>
        </div>
      )}
      {!result && !error && <p role="status">Загружаем заказ-наряды…</p>}
      {result && result.items.length === 0 && <p>Заказ-нарядов пока нет.</p>}
      {result && result.items.length > 0 && (
        <div className="data-list">
          {result.items.map((order) => (
            <button
              className="data-row"
              key={order.work_order_number}
              type="button"
              onClick={() => onOpenWorkOrder(order.work_order_number)}
            >
              <span className="row-number">№{order.work_order_number}</span>
              <span className="row-primary">
                <strong>{order.reason}</strong>
                <small>
                  {scope === "client" ? order.license_plate : order.client_name}
                  {" · "}
                  <time dateTime={order.created_at}>
                    {orderDate.format(new Date(order.created_at))}
                  </time>
                </small>
              </span>
              <span className="row-meta">{statusLabels[order.status]}</span>
            </button>
          ))}
        </div>
      )}
      {result && (offset > 0 || result.total > pageSize) && (
        <nav className="repair-history-pagination" aria-label="Страницы истории ремонтов">
          <button
            className="secondary-button"
            type="button"
            disabled={offset === 0}
            onClick={() => changePage(offset - pageSize)}
          >
            Назад
          </button>
          <span>Стр. {offset / pageSize + 1} из {Math.ceil(result.total / pageSize)}</span>
          <button
            className="secondary-button"
            type="button"
            disabled={offset + pageSize >= result.total}
            onClick={() => changePage(offset + pageSize)}
          >
            Далее
          </button>
        </nav>
      )}
    </section>
  );
}
