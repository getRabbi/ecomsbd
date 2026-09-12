"""Browser landing pages for emailed links, backed by the existing auth API."""

from html import escape

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.api.deps import SettingsDep
from app.core.security import generate_opaque_token

router = APIRouter(prefix="/auth", include_in_schema=False)

_SCRIPT = """
const form = document.querySelector('form');
const status = document.querySelector('[role=status]');
const token = new URLSearchParams(location.hash.slice(1)).get('token')
    || new URLSearchParams(location.search).get('token');
history.replaceState(null, '', location.pathname);
if (!token) {
    status.textContent = 'This link is incomplete. Request a new link in ecomsbd.';
    form.hidden = true;
}
form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = form.querySelector('button');
    const password = form.querySelector('[name=new_password]');
    const body = {token};
    if (password) body.new_password = password.value;
    button.disabled = true;
    status.textContent = 'Please wait…';
    try {
        const response = await fetch(form.dataset.endpoint, {
            method: 'POST', credentials: 'omit',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body)
        });
        if (response.ok) {
            form.hidden = true;
            status.textContent = password
                ? 'Password reset. Return to ecomsbd and sign in with your new password.'
                : 'Email verified. You can return to ecomsbd.';
        } else {
            status.textContent = response.status === 422
                ? 'Use 10–200 characters and avoid common passwords.'
                : 'This request could not be completed. Check your password or request a new link.';
        }
    } catch {
        status.textContent = 'Could not connect. Please try again.';
    } finally {
        button.disabled = false;
    }
});
"""


def _page(*, endpoint: str, reset: bool) -> HTMLResponse:
    nonce = generate_opaque_token()
    title = "Reset your password" if reset else "Verify your email"
    fields = (
        '<label>New password <input name="new_password" type="password" '
        'autocomplete="new-password" minlength="10" maxlength="200" required></label>'
        if reset
        else "<p>Confirm this email address for your ecomsbd account.</p>"
    )
    return HTMLResponse(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{title} · ecomsbd</title>"
        "<style>body{font:1rem system-ui;max-width:28rem;margin:12vh auto;padding:1.5rem;"
        "color:#173b33;background:#f6f9f7}input,button{display:block;font:inherit;"
        "padding:.8rem;margin:1rem 0;box-sizing:border-box;width:100%}button{"
        "background:#146b50;color:white;border:0;border-radius:.4rem;cursor:pointer}"
        "button:disabled{opacity:.6}</style>"
        f"<main><p>ecomsbd</p><h1>{title}</h1>"
        f'<form data-endpoint="{escape(endpoint, quote=True)}">{fields}'
        f'<button type="submit">{title}</button></form>'
        '<p role="status" aria-live="polite"></p>'
        "<noscript>Enable JavaScript to complete this request.</noscript></main>"
        f'<script nonce="{nonce}">{_SCRIPT}</script></html>',
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": (
                f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; "
                "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
            ),
        },
    )


@router.get("/email/verify")
async def verify_email_page(settings: SettingsDep) -> HTMLResponse:
    return _page(endpoint=f"{settings.api_v1_prefix}/auth/email/verify", reset=False)


@router.get("/password/reset")
async def reset_password_page(settings: SettingsDep) -> HTMLResponse:
    return _page(endpoint=f"{settings.api_v1_prefix}/auth/password/reset", reset=True)
