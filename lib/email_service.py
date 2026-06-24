import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, ReplyTo

def send_credentials_email(to_email: str, name: str, temp_password: str):
    message = Mail(
        from_email=os.environ["SENDGRID_FROM_EMAIL"],
        to_emails=to_email,
        subject="Your InboxHire login credentials",
        html_content=f"""
            <p>Hi {name},</p>
            <p>Your InboxHire account has been created.</p>
            <p><b>Email:</b> {to_email}<br>
            <b>Temporary password:</b> {temp_password}</p>
            <p>Please log in and change your password after your first login.</p>
        """,
    )
    sg = SendGridAPIClient(os.environ["SENDGRID_API_KEY"])
    sg.send(message)

def send_password_reset_email(to_email: str, name: str, reset_token: str):
    reset_link = f"{os.environ['FRONTEND_URL']}/reset-password?token={reset_token}"
    message = Mail(
        from_email=os.environ["SENDGRID_FROM_EMAIL"],
        to_emails=to_email,
        subject="Reset your InboxHire password",
        html_content=f"""
            <p>Hi {name},</p>
            <p>We received a request to reset your password. Click below to set a new one:</p>
            <p><a href="{reset_link}">Reset Password</a></p>
            <p>This link expires in 1 hour. If you didn't request this, ignore this email.</p>
        """,
    )
    sg = SendGridAPIClient(os.environ["SENDGRID_API_KEY"])
    sg.send(message)

def send_candidate_email(to_email: str, subject: str, body_html: str, reply_to: str | None = None):
    message = Mail(
        from_email=os.environ["SENDGRID_FROM_EMAIL"],
        to_emails=to_email,
        subject=subject,
        html_content=body_html,
    )
    if reply_to:
        message.reply_to = ReplyTo(reply_to)
    sg = SendGridAPIClient(os.environ["SENDGRID_API_KEY"])
    sg.send(message)
