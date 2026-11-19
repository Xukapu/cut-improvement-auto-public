
"""Страница персональной ссылки и QR для подключения MAX."""

from html import escape

import qrcode
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.api.deps import DbSession, ManagerUser
from app.models.client import Client
from app.services.max_bot_service import create_personal_link

router = APIRouter(
    prefix="/max-link",
    tags=["MAX: экран подключения"],
)


def _qr_svg(link: str) -> str:
    """Генерирует QR без сторонних онлайн-сервисов."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=4,
    )
    qr.add_data(link)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    size = len(matrix)
    paths = []

    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                paths.append(f"M{x} {y}h1v1h-1z")

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {size} {size}" role="img" '
        f'aria-label="QR-код для открытия MAX-бота">'
        f'<rect width="{size}" height="{size}" fill="white"/>'
        f'<path d="{" ".join(paths)}" fill="black"/>'
        '</svg>'
    )


def _page(client_number: int, client_name: str, link: str | None) -> str:
    client_label = escape(client_name)
    safe_link = escape(link, quote=True) if link else ""

    if link:
        content = f"""
        <div class="qr">{_qr_svg(link)}</div>
        <p class="hint">Покажите QR-код клиенту для сканирования.</p>
        <label for="personal-link">Или скопируйте ссылку и отправьте клиенту:</label>
        <div class="copy-row">
          <input id="personal-link" readonly value="{safe_link}" aria-label="Персональная ссылка MAX">
          <button type="button" id="copy-button">Скопировать</button>
        </div>
        <p id="copy-status" role="status"></p>
        <p class="note">Ссылка действует 10 минут. Новая ссылка отменяет предыдущую.</p>
        <form method="post"><button class="secondary" type="submit">Создать новую ссылку</button></form>
        """
    else:
        content = """
        <p>Нажмите кнопку, чтобы получить персональные QR-код и ссылку.</p>
        <form method="post"><button type="submit">Создать QR-код и ссылку</button></form>
        """

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="referrer" content="no-referrer">
  <title>Подключение MAX — клиент №{client_number}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; padding: 28px 16px; background: #f4f6fa;
            color: #182235; font-family: system-ui, sans-serif; }}
    .card {{ width: 100%; max-width: 580px; margin: 0 auto; background: white;
             border-radius: 18px; padding: 30px;
             box-shadow: 0 10px 34px rgba(24,34,53,.09); }}
    h1 {{ font-size: 24px; margin: 0 0 10px; }}
    p {{ line-height: 1.5; }}
    .muted, .hint, .note {{ color: #586477; }}
    .qr {{ width: min(100%, 280px); margin: 24px auto 8px; }}
    .qr svg {{ display: block; width: 100%; height: auto; }}
    .hint {{ text-align: center; }}
    label {{ display: block; font-weight: 600; margin: 24px 0 10px; }}
    .copy-row {{ display: flex; gap: 10px; }}
    input {{ flex: 1; min-width: 0; border: 1px solid #cbd5e1;
             border-radius: 9px; padding: 12px; font-size: 14px; }}
    button {{ cursor: pointer; padding: 12px 16px; border-radius: 9px;
              border: 0; background: #175bcc; color: #fff; font-weight: 600; }}
    button.secondary {{ margin-top: 8px; background: #e9effb; color: #175bcc; }}
    .note {{ font-size: 13px; }}
    #copy-status {{ min-height: 18px; color: #126035; font-size: 14px; }}
    @media (max-width: 430px) {{ .card {{ padding: 22px; }}
                                  .copy-row {{ flex-direction: column; }} }}
  </style>
</head>
<body>
  <main class="card">
    <h1>Подключение MAX</h1>
    <p class="muted">Клиент №{client_number}: {client_label}</p>
    {content}
  </main>
  <script>
    const copyButton = document.getElementById('copy-button');
    if (copyButton) {{
      copyButton.addEventListener('click', async () => {{
        const input = document.getElementById('personal-link');
        const status = document.getElementById('copy-status');
        try {{
          await navigator.clipboard.writeText(input.value);
          status.textContent = '✅ Ссылка скопирована';
        }} catch (error) {{
          input.focus();
          input.select();
          status.textContent = 'Выделена ссылка. Нажмите Ctrl+C, чтобы скопировать.';
        }}
      }});
    }}
  </script>
</body>
</html>"""


@router.api_route(
    "/clients/{client_number}/screen",
    methods=["GET", "POST"],
    response_class=HTMLResponse,
    summary="Экран QR-кода и ссылки MAX",
)
def max_link_screen(
    client_number: int,
    request: Request,
    db: DbSession,
    current_user: ManagerUser,
) -> HTMLResponse:
    client = db.scalar(
        select(Client).where(
            Client.client_number == client_number,
            Client.deleted_at.is_(None),
            Client.archived_at.is_(None),
        )
    )
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Клиент не найден.",
        )

    link = None
    if request.method == "POST":
        result = create_personal_link(client_number, db, current_user)
        link = result.link

    return HTMLResponse(
        content=_page(client_number, client.full_name, link),
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )
