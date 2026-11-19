
import { useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { createPortal } from "react-dom";
import QRCode from "qrcode";

type Props = {
  clientNumber: number;
};

type LinkResponse = {
  client_number: number;
  link: string;
  expires_in_minutes: number;
};

const overlayStyle: CSSProperties = {
  position: "fixed",
  inset: 0,
  zIndex: 10000,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "rgba(15, 23, 42, 0.6)",
  padding: 16,
};

const cardStyle: CSSProperties = {
  background: "#fff",
  color: "#182235",
  borderRadius: 18,
  width: "min(100%, 540px)",
  maxHeight: "90vh",
  overflowY: "auto",
  padding: 24,
  boxShadow: "0 20px 60px rgba(0,0,0,.2)",
};

const actionStyle: CSSProperties = {
  borderRadius: 9,
  border: "none",
  padding: "11px 15px",
  cursor: "pointer",
  background: "#175bcc",
  color: "white",
  fontWeight: 600,
};

export function MaxLinkButton({ clientNumber }: Props) {
  const [isOpen, setIsOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [link, setLink] = useState("");
  const [qrImage, setQrImage] = useState("");
  const [minutes, setMinutes] = useState(10);
  const [error, setError] = useState("");
  const [copyStatus, setCopyStatus] = useState("");

  const linkInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!isOpen) return;

    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setIsOpen(false);
      }
    };

    document.addEventListener("keydown", onKeyDown);

    return () => {
      document.body.style.overflow = oldOverflow;
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [isOpen]);

  async function generate(): Promise<void> {
    setLoading(true);
    setError("");
    setCopyStatus("");
    setLink("");
    setQrImage("");

    try {
      const response = await fetch(
        `/api/v1/max-link/clients/${clientNumber}/link`,
        {
          method: "POST",
          credentials: "include",
          headers: {
            Accept: "application/json",
          },
        },
      );

      const result = await response.json() as
        Partial<LinkResponse> & { detail?: string };

      if (!response.ok) {
        throw new Error(
          typeof result.detail === "string"
            ? result.detail
            : `Ошибка HTTP ${response.status}`,
        );
      }

      if (
        !result.link ||
        !result.link.startsWith("https://max.ru/")
      ) {
        throw new Error(
          "Сервер вернул некорректную ссылку MAX.",
        );
      }

      const image = await QRCode.toDataURL(
        result.link,
        {
          errorCorrectionLevel: "M",
          width: 280,
          margin: 3,
        },
      );

      setLink(result.link);
      setQrImage(image);
      setMinutes(result.expires_in_minutes ?? 10);

    } catch (failure) {
      setError(
        failure instanceof Error
          ? failure.message
          : "Не удалось создать ссылку MAX.",
      );

    } finally {
      setLoading(false);
    }
  }

  function openPopup(): void {
    setIsOpen(true);
    void generate();
  }

  async function copyLink(): Promise<void> {
    if (!link) return;

    try {
      await navigator.clipboard.writeText(link);
      setCopyStatus("✅ Ссылка скопирована");

    } catch {
      linkInput.current?.focus();
      linkInput.current?.select();
      setCopyStatus(
        "Ссылка выделена — нажмите Ctrl+C.",
      );
    }
  }

  const popup = isOpen ? (
    <div
      style={overlayStyle}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          setIsOpen(false);
        }
      }}
      role="presentation"
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-label={`Подключение MAX для клиента №${clientNumber}`}
        style={cardStyle}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            gap: 12,
          }}
        >
          <div>
            <div
              style={{
                color: "#64748b",
                fontSize: 13,
              }}
            >
              Клиент №{clientNumber}
            </div>

            <h2
              style={{
                margin: "4px 0 12px",
                fontSize: 24,
              }}
            >
              Подключение MAX
            </h2>
          </div>

          <button
            type="button"
            onClick={() => setIsOpen(false)}
            aria-label="Закрыть окно"
            style={{
              background: "transparent",
              border: "none",
              fontSize: 25,
              cursor: "pointer",
              alignSelf: "flex-start",
            }}
          >
            ×
          </button>
        </div>

        {loading && (
          <p>
            Создаём персональную ссылку и QR-код...
          </p>
        )}

        {error && (
          <p
            role="alert"
            style={{
              color: "#b42318",
              lineHeight: 1.5,
            }}
          >
            {error}
          </p>
        )}

        {link && qrImage && !loading && (
          <>
            <div
              style={{
                textAlign: "center",
                margin: "12px 0",
              }}
            >
              <img
                src={qrImage}
                alt="Персональный QR-код MAX"
                width={280}
                height={280}
                style={{
                  maxWidth: "100%",
                  height: "auto",
                }}
              />

              <p
                style={{
                  fontSize: 14,
                  color: "#64748b",
                  margin: "4px 0 18px",
                }}
              >
                Покажите QR-код клиенту для сканирования
              </p>
            </div>

            <label
              htmlFor="max-personal-link"
              style={{
                display: "block",
                fontWeight: 600,
                marginBottom: 8,
              }}
            >
              Или отправьте клиенту ссылку
            </label>

            <input
              id="max-personal-link"
              ref={linkInput}
              readOnly
              value={link}
              onFocus={(event) => {
                event.currentTarget.select();
              }}
              style={{
                width: "100%",
                boxSizing: "border-box",
                padding: 11,
                border: "1px solid #cbd5e1",
                borderRadius: 8,
                fontSize: 13,
              }}
            />

            <button
              type="button"
              onClick={() => void copyLink()}
              style={{
                ...actionStyle,
                width: "100%",
                marginTop: 12,
              }}
            >
              📋 Скопировать ссылку
            </button>

            <p
              role="status"
              style={{
                minHeight: 18,
                color: "#126035",
                fontSize: 14,
              }}
            >
              {copyStatus}
            </p>

            <p
              style={{
                color: "#64748b",
                fontSize: 13,
                lineHeight: 1.5,
              }}
            >
              Ссылка действует {minutes} минут.
              Не пересылайте её посторонним.
            </p>
          </>
        )}

        {!loading && (
          <button
            type="button"
            onClick={() => void generate()}
            style={{
              ...actionStyle,
              background: "#e9effb",
              color: "#175bcc",
              marginTop: 6,
            }}
          >
            Создать новую ссылку
          </button>
        )}
      </section>
    </div>
  ) : null;

  return (
    <>
      <button
        className="secondary-button"
        type="button"
        onClick={openPopup}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 8,
          padding: "11px 15px",
        }}
      >
        ▦ QR-код и ссылка MAX
      </button>

      {popup && createPortal(popup, document.body)}
    </>
  );
}
