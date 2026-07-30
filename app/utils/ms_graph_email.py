import os
import time
import json
import requests
from threading import Thread

# In-memory token cache to prevent requesting a new token on every email
_TOKEN_CACHE = {
    "access_token": None,
    "expires_at": 0
}

ALLOWED_SENDERS = {
    "itdept": "itdept@ela-academy.org",
    "accounting": "accounting@ela-academy.org",
    "admissions": "admissions@ela-academy.org",
    "admin": "admin@ela-academy.org",
    "hr": "hr@ela-academy.org",
    "programs": "programs@ela-academy.org",
    "teachers": "teachers@ela-academy.org"
}

DEFAULT_SENDER = os.getenv("MS_GRAPH_DEFAULT_SENDER", "itdept@ela-academy.org")

def is_ms_graph_configured():
    """Returns True if Microsoft Graph API credentials are set in environment."""
    tenant_id = os.getenv("MS_GRAPH_TENANT_ID")
    client_id = os.getenv("MS_GRAPH_CLIENT_ID")
    client_secret = os.getenv("MS_GRAPH_CLIENT_SECRET")
    return bool(tenant_id and client_id and client_secret)

def get_graph_access_token():
    """
    Obtains an OAuth2 access token for Microsoft Graph API using Client Credentials grant type.
    Uses token caching to re-use token until expiration.
    """
    now = time.time()
    if _TOKEN_CACHE["access_token"] and _TOKEN_CACHE["expires_at"] > now + 60:
        return _TOKEN_CACHE["access_token"]

    tenant_id = os.getenv("MS_GRAPH_TENANT_ID")
    client_id = os.getenv("MS_GRAPH_CLIENT_ID")
    client_secret = os.getenv("MS_GRAPH_CLIENT_SECRET")

    if not (tenant_id and client_id and client_secret):
        print("[MS Graph Email] Missing Microsoft Graph credentials in environment variables.")
        return None

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials"
    }

    try:
        res = requests.post(token_url, data=payload, timeout=10)
        res.raise_for_status()
        data = res.json()
        access_token = data.get("access_token")
        expires_in = data.get("expires_in", 3600)

        _TOKEN_CACHE["access_token"] = access_token
        _TOKEN_CACHE["expires_at"] = now + expires_in
        return access_token
    except Exception as e:
        print(f"[MS Graph Email Error] Failed to obtain OAuth2 token: {e}")
        return None

def resolve_sender_email(sender_input=None):
    """
    Resolves sender_input (key like 'admissions' or full email 'admissions@ela-academy.org')
    to a valid mailbox. Defaults to 'itdept@ela-academy.org'.
    """
    if not sender_input:
        return DEFAULT_SENDER

    sender_str = str(sender_input).strip().lower()
    if sender_str in ALLOWED_SENDERS:
        return ALLOWED_SENDERS[sender_str]

    for key, email_addr in ALLOWED_SENDERS.items():
        if sender_str == email_addr.lower():
            return email_addr

    if "@" in sender_str:
        return sender_str

    return DEFAULT_SENDER

def send_email_via_graph(subject, recipients, html_content, sender_email=None, cc_recipients=None, save_to_sent=True):
    """
    Sends an HTML email via Microsoft Graph API sendMail endpoint.
    
    :param subject: Email subject line
    :param recipients: List of recipient email addresses (or single email string)
    :param html_content: HTML body string
    :param sender_email: Department mailbox or email string (e.g. 'admissions@ela-academy.org')
    :param cc_recipients: List of CC recipient email addresses (or single email string)
    :param save_to_sent: Boolean flag to save message in sender's Sent Items folder
    """
    token = get_graph_access_token()
    if not token:
        print("[MS Graph Email] Could not send email: Invalid or missing token.")
        return False

    actual_sender = resolve_sender_email(sender_email)

    if isinstance(recipients, str):
        recipients = [recipients]

    to_recipients_payload = [
        {"emailAddress": {"address": addr.strip()}}
        for addr in recipients if addr and "@" in addr
    ]

    if not to_recipients_payload:
        print("[MS Graph Email] No valid recipients provided.")
        return False

    cc_recipients_payload = []
    if cc_recipients:
        if isinstance(cc_recipients, str):
            cc_recipients = [cc_recipients]
        cc_recipients_payload = [
            {"emailAddress": {"address": addr.strip()}}
            for addr in cc_recipients if addr and "@" in addr
        ]

    endpoint = f"https://graph.microsoft.com/v1.0/users/{actual_sender}/sendMail"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    message_payload = {
        "subject": subject,
        "body": {
            "contentType": "HTML",
            "content": html_content
        },
        "toRecipients": to_recipients_payload
    }
    if cc_recipients_payload:
        message_payload["ccRecipients"] = cc_recipients_payload

    body = {
        "message": message_payload,
        "saveToSentItems": "true" if save_to_sent else "false"
    }

    try:
        response = requests.post(endpoint, headers=headers, json=body, timeout=15)
        if response.status_code in [200, 202]:
            print(f"[MS Graph Email Success] Email '{subject}' sent from {actual_sender} to {recipients} (CC: {cc_recipients})")
            return True
        else:
            print(f"[MS Graph Email Failure] Status {response.status_code} from {actual_sender}: {response.text}")
            return False
    except Exception as e:
        print(f"[MS Graph Email Exception] Exception sending from {actual_sender}: {e}")
        return False

def _async_graph_send(subject, recipients, html_content, sender_email, cc_recipients):
    send_email_via_graph(subject, recipients, html_content, sender_email=sender_email, cc_recipients=cc_recipients)

def send_email_via_graph_background(subject, recipients, html_content, sender_email=None, cc_recipients=None):
    """Dispatches email sending via Microsoft Graph API in a non-blocking background thread."""
    thread = Thread(target=_async_graph_send, args=(subject, recipients, html_content, sender_email, cc_recipients))
    thread.daemon = True
    thread.start()
    return thread

# Department name -> email fuzzy mapping
DEPARTMENT_EMAIL_MAP = {
    'it': 'itdept@ela-academy.org',
    'it department': 'itdept@ela-academy.org',
    'itdept': 'itdept@ela-academy.org',
    'information technology': 'itdept@ela-academy.org',
    'accounting': 'accounting@ela-academy.org',
    'accounts': 'accounting@ela-academy.org',
    'finance': 'accounting@ela-academy.org',
    'admissions': 'admissions@ela-academy.org',
    'admission': 'admissions@ela-academy.org',
    'admin': 'admin@ela-academy.org',
    'administration': 'admin@ela-academy.org',
    'hr': 'hr@ela-academy.org',
    'human resources': 'hr@ela-academy.org',
    'human resource': 'hr@ela-academy.org',
    'programs': 'programs@ela-academy.org',
    'programme': 'programs@ela-academy.org',
    'programmes': 'programs@ela-academy.org',
    'program': 'programs@ela-academy.org',
    'teachers': 'teachers@ela-academy.org',
    'teaching': 'teachers@ela-academy.org',
    'teacher': 'teachers@ela-academy.org',
    'academics': 'teachers@ela-academy.org',
}

def match_department_to_email(dept_name):
    """Fuzzy-match a department name to a known email address."""
    if not dept_name:
        return DEFAULT_SENDER
    name_lower = dept_name.strip().lower()
    if name_lower in DEPARTMENT_EMAIL_MAP:
        return DEPARTMENT_EMAIL_MAP[name_lower]
    for key, email in DEPARTMENT_EMAIL_MAP.items():
        if key in name_lower or name_lower in key:
            return email
    return DEFAULT_SENDER

def get_department_sender_email(user):
    """Resolve the sender email from a user's primary department."""
    try:
        if hasattr(user, 'departments') and user.departments:
            dept = user.departments[0]
            if hasattr(dept, 'email') and dept.email:
                return dept.email
            return match_department_to_email(dept.name)
    except Exception:
        pass
    return DEFAULT_SENDER

def get_dept_email_from_dept(dept):
    """Resolve email from a Department object."""
    if not dept:
        return DEFAULT_SENDER
    if hasattr(dept, 'email') and dept.email:
        return dept.email
    return match_department_to_email(dept.name)

def format_task_comment_email(sender_name, task_title, comment_content, board_name, frontend_url=None):
    """Generate a styled HTML email body for a task comment sent via email."""
    import os
    base_url = frontend_url or os.getenv('FRONTEND_URL', 'http://localhost:5173')
    return f"""
    <div style="font-family: 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background: linear-gradient(135deg, #1e293b 0%, #334155 100%); padding: 20px 24px; border-radius: 12px 12px 0 0;">
            <h2 style="color: #ffffff; margin: 0; font-size: 18px; font-weight: 600;">💬 Task Email Discussion</h2>
        </div>
        <div style="background: #ffffff; padding: 24px; border: 1px solid #e2e8f0; border-top: none; border-radius: 0 0 12px 12px;">
            <div style="margin-bottom: 16px;">
                <span style="display: inline-block; background: #f1f5f9; color: #475569; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600;">{board_name}</span>
            </div>
            <h3 style="margin: 0 0 12px 0; color: #0f172a; font-size: 16px;">📋 {task_title}</h3>
            <div style="background: #f8fafc; border-left: 4px solid #3b82f6; padding: 16px; border-radius: 0 8px 8px 0; margin: 16px 0;">
                <p style="margin: 0 0 8px 0; font-size: 12px; color: #64748b; font-weight: 600;">
                    {sender_name} wrote:
                </p>
                <p style="margin: 0; color: #334155; font-size: 14px; line-height: 1.6; white-space: pre-wrap;">{comment_content}</p>
            </div>
            <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;" />
            <p style="margin: 0; font-size: 12px; color: #94a3b8; text-align: center;">
                Sent via ELA Academy Workspace Email Communication
            </p>
        </div>
    </div>
    """

def format_submitter_confirmation_email(form_name, submitter_name, answers_table_html, school_name="Ela Academy"):
    """Generate executive styled HTML email confirmation for form submitters."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Submission Confirmation</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b;">
    <table border="0" cellpadding="0" cellspacing="0" width="100%" style="table-layout: fixed; background-color: #f8fafc; padding: 40px 0;">
        <tr>
            <td align="center">
                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
                    <tr>
                        <td style="background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%); padding: 32px 36px; text-align: left;">
                            <div style="display: inline-block; background-color: rgba(255, 255, 255, 0.2); color: #ffffff; padding: 4px 12px; border-radius: 20px; font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 12px;">
                                {school_name}
                            </div>
                            <h1 style="margin: 0; color: #ffffff; font-size: 22px; font-weight: 700; line-height: 1.3;">
                                Submission Confirmation
                            </h1>
                            <p style="margin: 6px 0 0 0; color: #e0e7ff; font-size: 14px;">
                                Form: {form_name}
                            </p>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 36px;">
                            <p style="margin: 0 0 16px 0; font-size: 15px; line-height: 1.6; color: #334155; font-weight: 600;">
                                Dear {submitter_name or 'Applicant'},
                            </p>
                            <p style="margin: 0 0 24px 0; font-size: 14px; line-height: 1.6; color: #475569;">
                                Thank you for reaching out to <strong>{school_name}</strong>. We have successfully received your submission for <strong>{form_name}</strong>. Our team is currently reviewing your information and will be in touch with you shortly.
                            </p>
                            <div style="background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 20px; margin-bottom: 24px;">
                                <h3 style="margin: 0 0 16px 0; font-size: 12px; font-weight: 700; color: #475569; text-transform: uppercase; letter-spacing: 0.05em;">
                                    Submission Details Summary
                                </h3>
                                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="font-size: 13px; border-collapse: collapse;">
                                    {answers_table_html}
                                </table>
                            </div>
                            <div style="background-color: #eef2ff; border-left: 4px solid #4f46e5; border-radius: 0 6px 6px 0; padding: 14px 18px; margin-bottom: 24px;">
                                <p style="margin: 0; font-size: 13px; color: #3730a3; line-height: 1.5;">
                                    💡 <strong>Need to follow up or reply?</strong> Simply reply directly to this email message to communicate directly with our team.
                                </p>
                            </div>
                            <p style="margin: 0; font-size: 14px; line-height: 1.6; color: #475569;">
                                Best regards,<br>
                                <strong style="color: #1e293b;">{school_name} Team</strong>
                            </p>
                        </td>
                    </tr>
                    <tr>
                        <td style="background-color: #f1f5f9; padding: 20px 36px; text-align: center; border-top: 1px solid #e2e8f0;">
                            <p style="margin: 0; font-size: 12px; color: #64748b;">
                                This is an official automated notification from {school_name}.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""

def format_department_notification_email(form_name, submitter_name, submitter_email, task_title, answers_table_html, school_name="Ela Academy"):
    """Generate executive styled HTML email notification for internal departments."""
    sub_info = f"{submitter_name}" + (f" ({submitter_email})" if submitter_email else "")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>New Form Submission</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b;">
    <table border="0" cellpadding="0" cellspacing="0" width="100%" style="table-layout: fixed; background-color: #f8fafc; padding: 40px 0;">
        <tr>
            <td align="center">
                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 600px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);">
                    <tr>
                        <td style="background: linear-gradient(135deg, #1e293b 0%, #334155 100%); padding: 28px 36px; text-align: left;">
                            <div style="display: inline-block; background-color: rgba(255, 255, 255, 0.15); color: #ffffff; padding: 4px 12px; border-radius: 20px; font-size: 11px; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 10px;">
                                INTERNAL ALERT | {school_name}
                            </div>
                            <h1 style="margin: 0; color: #ffffff; font-size: 20px; font-weight: 700;">
                                📋 New Form Submission Received
                            </h1>
                            <p style="margin: 4px 0 0 0; color: #94a3b8; font-size: 13px;">
                                Form: {form_name}
                            </p>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 32px;">
                            <div style="background-color: #f1f5f9; border-radius: 8px; padding: 14px 18px; margin-bottom: 24px;">
                                <p style="margin: 0 0 4px 0; font-size: 11px; color: #64748b; font-weight: 700; text-transform: uppercase;">Submitted By</p>
                                <p style="margin: 0; font-size: 15px; color: #0f172a; font-weight: 700;">{sub_info}</p>
                            </div>
                            <h3 style="margin: 0 0 12px 0; font-size: 12px; font-weight: 700; color: #475569; text-transform: uppercase; letter-spacing: 0.05em;">
                                Submitted Responses
                            </h3>
                            <table border="0" cellpadding="0" cellspacing="0" width="100%" style="font-size: 13px; border-collapse: collapse; margin-bottom: 24px; border: 1px solid #e2e8f0; border-radius: 6px; overflow: hidden;">
                                {answers_table_html}
                            </table>
                            <div style="background-color: #eff6ff; border-left: 4px solid #3b82f6; padding: 14px 18px; border-radius: 0 6px 6px 0;">
                                <p style="margin: 0; font-size: 13px; color: #1e40af;">
                                    📌 <strong>Task Created:</strong> {task_title}
                                </p>
                            </div>
                        </td>
                    </tr>
                    <tr>
                        <td style="background-color: #f1f5f9; padding: 18px 36px; text-align: center; border-top: 1px solid #e2e8f0;">
                            <p style="margin: 0; font-size: 12px; color: #64748b;">
                                {school_name} Automated Notification System
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>"""
