import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from backend.config import get_setting

logger = logging.getLogger(__name__)


def send_email_notification(subject: str, html_body: str, image_bytes: bytes | None = None) -> None:
    """Send an HTML email notification."""
    gmail_address = get_setting("gmail_address")
    app_password = get_setting("gmail_app_password")
    if not gmail_address or not app_password:
        logger.warning("Email notification: no Gmail credentials configured")
        return

    to_address = get_setting("notify_email_to") or gmail_address
    from_name = get_setting("notify_from_name") or "Receiptory"

    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{gmail_address}>"
    msg["To"] = to_address

    # HTML body
    html_part = MIMEText(html_body, "html")
    msg.attach(html_part)

    # Optional inline image
    if image_bytes:
        img = MIMEImage(image_bytes, _subtype="png")
        img.add_header("Content-ID", "<document_thumbnail>")
        img.add_header("Content-Disposition", "inline", filename="thumbnail.png")
        msg.attach(img)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(gmail_address, app_password)
            server.sendmail(gmail_address, to_address, msg.as_string())
        logger.info(f"Email notification sent to {to_address}: {subject}")
    except Exception as e:
        logger.error(f"Email notification failed: {e}")
