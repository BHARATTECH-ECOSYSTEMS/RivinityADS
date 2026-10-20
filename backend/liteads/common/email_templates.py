"""
Reusable email templates and content builders for RivinityADS.
"""

from dataclasses import dataclass
import html
import urllib.parse

from liteads.common.config import get_settings


@dataclass(frozen=True)
class EmailContent:
    """Structured email content holding subject, plain text, and HTML representations."""

    subject: str
    text_content: str
    html_content: str

    def __iter__(self):
        yield self.subject
        yield self.text_content
        yield self.html_content


def _resolve_frontend_url(frontend_url: str | None = None) -> str:
    """Resolve the base frontend URL for constructing links."""
    if frontend_url:
        return frontend_url.rstrip("/")
    try:
        url = get_settings().server.frontend_url
        if url:
            return url.rstrip("/")
    except Exception:
        pass
    return "http://localhost:5173"


def build_verification_email(
    recipient_email: str,
    token: str,
    frontend_url: str | None = None,
    expiry_hours: int | None = None,
) -> EmailContent:
    """Build verification email subject, plain text, and HTML."""
    base_url = _resolve_frontend_url(frontend_url)
    encoded_token = urllib.parse.quote(token)
    verification_url = f"{base_url}/verify-email?token={encoded_token}"

    if expiry_hours is None:
        try:
            expiry_hours = get_settings().auth.verification_token_hours
        except Exception:
            expiry_hours = 24

    escaped_email = html.escape(recipient_email, quote=True)
    escaped_url = html.escape(verification_url, quote=True)

    subject = "Verify your RivinityADS account"

    text_content = (
        f"Hello,\n\n"
        f"Thank you for registering with RivinityADS ({recipient_email}).\n"
        f"Please verify your email address by opening the following link in your browser:\n\n"
        f"{verification_url}\n\n"
        f"This link will expire in {expiry_hours} hours.\n\n"
        f"If you did not create a RivinityADS account, you can safely ignore this email.\n\n"
        f"— The RivinityADS Team\n"
    )

    html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Verify your RivinityADS account</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; color: #1e293b; margin: 0; padding: 24px; }}
    .container {{ max-width: 580px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; padding: 32px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
    .header {{ font-size: 20px; font-weight: 700; color: #0f172a; margin-bottom: 20px; border-bottom: 1px solid #f1f5f9; padding-bottom: 12px; }}
    .btn {{ display: inline-block; background-color: #2563eb; color: #ffffff !important; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 15px; margin: 20px 0; }}
    .muted {{ color: #64748b; font-size: 13px; line-height: 1.5; }}
    .url-break {{ word-break: break-all; color: #2563eb; font-size: 13px; }}
    .footer {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid #f1f5f9; font-size: 12px; color: #94a3b8; text-align: center; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">RivinityADS</div>
    <p>Hello,</p>
    <p>Thank you for registering with RivinityADS for <strong>{escaped_email}</strong>.</p>
    <p>Please click the button below to verify your email address and activate your account:</p>
    <p style="text-align: center;">
      <a href="{escaped_url}" class="btn" target="_blank" rel="noopener noreferrer">Verify Email Address</a>
    </p>
    <p class="muted">This verification link is valid for {expiry_hours} hours.</p>
    <p class="muted">If the button above does not work, copy and paste this URL into your browser:<br>
      <span class="url-break">{escaped_url}</span>
    </p>
    <p class="muted">If you did not register for a RivinityADS account, please disregard this email.</p>
    <div class="footer">
      &copy; RivinityADS &bull; High-Performance Digital Ad Platform
    </div>
  </div>
</body>
</html>"""

    return EmailContent(subject=subject, text_content=text_content, html_content=html_content)


def build_password_reset_email(
    recipient_email: str,
    token: str,
    frontend_url: str | None = None,
    expiry_hours: int | None = None,
) -> EmailContent:
    """Build password reset email subject, plain text, and HTML."""
    base_url = _resolve_frontend_url(frontend_url)
    encoded_token = urllib.parse.quote(token)
    reset_url = f"{base_url}/reset-password?token={encoded_token}"

    if expiry_hours is None:
        try:
            expiry_hours = get_settings().auth.password_reset_token_hours
        except Exception:
            expiry_hours = 1

    escaped_email = html.escape(recipient_email, quote=True)
    escaped_url = html.escape(reset_url, quote=True)

    subject = "Reset your RivinityADS password"

    text_content = (
        f"Hello,\n\n"
        f"We received a request to reset the password for your RivinityADS account ({recipient_email}).\n"
        f"You can choose a new password by visiting the following link:\n\n"
        f"{reset_url}\n\n"
        f"This link will expire in {expiry_hours} hour(s) and can only be used once.\n\n"
        f"If you did not request a password reset, you can safely ignore this email. Your password will not change.\n\n"
        f"— The RivinityADS Security Team\n"
    )

    html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Reset your RivinityADS password</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; color: #1e293b; margin: 0; padding: 24px; }}
    .container {{ max-width: 580px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; padding: 32px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
    .header {{ font-size: 20px; font-weight: 700; color: #0f172a; margin-bottom: 20px; border-bottom: 1px solid #f1f5f9; padding-bottom: 12px; }}
    .btn {{ display: inline-block; background-color: #dc2626; color: #ffffff !important; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 15px; margin: 20px 0; }}
    .muted {{ color: #64748b; font-size: 13px; line-height: 1.5; }}
    .url-break {{ word-break: break-all; color: #dc2626; font-size: 13px; }}
    .footer {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid #f1f5f9; font-size: 12px; color: #94a3b8; text-align: center; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">RivinityADS Security</div>
    <p>Hello,</p>
    <p>We received a password reset request for your account (<strong>{escaped_email}</strong>).</p>
    <p>Click the button below to choose a new password:</p>
    <p style="text-align: center;">
      <a href="{escaped_url}" class="btn" target="_blank" rel="noopener noreferrer">Reset Password</a>
    </p>
    <p class="muted">This password reset link is valid for {expiry_hours} hour(s) and can only be used once.</p>
    <p class="muted">If the button above does not work, copy and paste this link into your browser:<br>
      <span class="url-break">{escaped_url}</span>
    </p>
    <p class="muted">If you did not make this request, you can safely disregard this message. Your account password remains unchanged.</p>
    <div class="footer">
      &copy; RivinityADS &bull; High-Performance Digital Ad Platform
    </div>
  </div>
</body>
</html>"""

    return EmailContent(subject=subject, text_content=text_content, html_content=html_content)


def build_password_changed_email(
    recipient_email: str,
    frontend_url: str | None = None,
) -> EmailContent:
    """Build password changed security alert email."""
    base_url = _resolve_frontend_url(frontend_url)
    login_url = f"{base_url}/login"

    escaped_email = html.escape(recipient_email, quote=True)
    escaped_login_url = html.escape(login_url, quote=True)

    subject = "Security Alert: Your RivinityADS password has been changed"

    text_content = (
        f"Hello,\n\n"
        f"This is a confirmation that the password for your RivinityADS account ({recipient_email}) was recently changed.\n\n"
        f"If you made this change, no further action is required.\n\n"
        f"If you did NOT make this change, please sign in immediately to secure your account or contact support:\n"
        f"{login_url}\n\n"
        f"— The RivinityADS Security Team\n"
    )

    html_content = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Security Alert: Password Changed</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; color: #1e293b; margin: 0; padding: 24px; }}
    .container {{ max-width: 580px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; padding: 32px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
    .header {{ font-size: 20px; font-weight: 700; color: #0f172a; margin-bottom: 20px; border-bottom: 1px solid #f1f5f9; padding-bottom: 12px; }}
    .alert-box {{ background-color: #fef2f2; border-left: 4px solid #dc2626; padding: 14px 18px; border-radius: 4px; margin: 18px 0; }}
    .alert-title {{ font-weight: 600; color: #991b1b; font-size: 14px; margin-bottom: 4px; }}
    .alert-text {{ color: #7f1d1d; font-size: 13px; margin: 0; }}
    .btn {{ display: inline-block; background-color: #0f172a; color: #ffffff !important; padding: 12px 24px; text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 15px; margin: 16px 0; }}
    .muted {{ color: #64748b; font-size: 13px; line-height: 1.5; }}
    .footer {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid #f1f5f9; font-size: 12px; color: #94a3b8; text-align: center; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">RivinityADS Security Alert</div>
    <p>Hello,</p>
    <p>The password for your RivinityADS account (<strong>{escaped_email}</strong>) was recently changed.</p>
    <div class="alert-box">
      <div class="alert-title">Was this you?</div>
      <p class="alert-text">If you made this change, you can safely disregard this notice.</p>
    </div>
    <p class="muted">If you did NOT change your password, someone else may have gained access to your account. Please log in immediately and contact our support team:</p>
    <p style="text-align: center;">
      <a href="{escaped_login_url}" class="btn" target="_blank" rel="noopener noreferrer">Sign in to RivinityADS</a>
    </p>
    <div class="footer">
      &copy; RivinityADS &bull; High-Performance Digital Ad Platform
    </div>
  </div>
</body>
</html>"""

    return EmailContent(subject=subject, text_content=text_content, html_content=html_content)
