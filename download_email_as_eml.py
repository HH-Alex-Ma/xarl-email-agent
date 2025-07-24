import os
import requests
from fpdf import FPDF
import email
from email import policy
from email.parser import BytesParser
import re
from datetime import datetime, timezone

ACCESS_TOKEN = ''  # <-- Replace with your access token
HEADERS = {'Authorization': f'Bearer {ACCESS_TOKEN}'}
DOWNLOAD_DIR = 'downloaded_emails'

PROCESSED_DIR = 'processed_emails'

LOG_FILE = 'run_log.txt'

DEFAULT_EMAIL_COUNT = 10

def get_last_run_time():
    if not os.path.exists(LOG_FILE):
        return None
    with open(LOG_FILE, 'r') as f:
        ts = f.read().strip()
        if ts:
            try:
                return datetime.fromisoformat(ts)
            except Exception:
                return None
    return None

def log_run_time():
    now = datetime.now(timezone.utc).isoformat()
    with open(LOG_FILE, 'w') as f:
        f.write(now)
    return now

def list_emails(since_datetime=None):
    url = 'https://graph.microsoft.com/v1.0/me/messages?$top=50&$orderby=receivedDateTime desc'
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code != 200:
        print(f"Error fetching emails: {resp.status_code}\n{resp.text}")
        return []
    emails = resp.json().get('value', [])
    filtered_emails = []
    for msg in emails:
        received = msg.get('receivedDateTime')
        if received and since_datetime:
            try:
                received_dt = datetime.fromisoformat(received.replace('Z', '+00:00'))
                if received_dt <= since_datetime:
                    continue
            except Exception:
                pass
        filtered_emails.append(msg)
    for idx, msg in enumerate(filtered_emails):
        subject = msg.get('subject', '(No Subject)')
        sender = msg.get('from', {}).get('emailAddress', {}).get('address', '(Unknown)')
        print(f"{idx+1}. Subject: {subject}\n   From: {sender}\n   ID: {msg['id']}")
    return filtered_emails

def sanitize_filename(name):
    # Remove invalid characters for Windows and Unix
    return re.sub(r'[\\/:*?"<>|]', '_', name)

def get_email_folder_name(msg):
    # Use date, from, to, and subject for folder name
    date = msg.get('Date', '').replace(',', '').replace(':', '').replace(' ', '_')[:20]
    from_ = msg.get('From', '').split('<')[0].strip().replace(' ', '_')[:20]
    to = msg.get('To', '').split('<')[0].strip().replace(' ', '_')[:20]
    subject = msg.get('Subject', '').replace(' ', '_')[:30]
    folder_name = f"{date}_{from_}_{to}_{subject}"
    return sanitize_filename(folder_name)

def download_eml(message_id, filename, folder):
    os.makedirs(folder, exist_ok=True)
    filepath = os.path.join(folder, filename)
    url = f'https://graph.microsoft.com/v1.0/me/messages/{message_id}/$value'
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code == 200:
        with open(filepath, 'wb') as f:
            f.write(resp.content)
        print(f"Saved email as {filepath}")
    else:
        print(f"Error downloading .eml: {resp.status_code}\n{resp.text}")
    return filepath

def download_attachments(message_id, folder):
    url = f'https://graph.microsoft.com/v1.0/me/messages/{message_id}/attachments'
    resp = requests.get(url, headers=HEADERS)
    if resp.status_code != 200:
        print(f"Error fetching attachments: {resp.status_code}\n{resp.text}")
        return []
    attachments = resp.json().get('value', [])
    if not attachments:
        print("No attachments found.")
        return []
    att_names = []
    for att in attachments:
        att_id = att['id']
        att_name = att['name']
        att_names.append(att_name)
        if att.get('@odata.mediaContentType'):
            # File attachment
            att_url = f'https://graph.microsoft.com/v1.0/me/messages/{message_id}/attachments/{att_id}/$value'
            att_resp = requests.get(att_url, headers=HEADERS)
            if att_resp.status_code == 200:
                att_path = os.path.join(folder, att_name)
                with open(att_path, 'wb') as f:
                    f.write(att_resp.content)
                print(f"Downloaded attachment: {att_path}")
            else:
                print(f"Failed to download attachment {att_name}: {att_resp.status_code}")
        elif 'contentBytes' in att:
            # Inline or simple attachment
            att_path = os.path.join(folder, att_name)
            with open(att_path, 'wb') as f:
                import base64
                f.write(base64.b64decode(att['contentBytes']))
            print(f"Downloaded attachment: {att_path}")
        else:
            print(f"Unknown attachment type for {att_name}")
    return att_names

def eml_to_pdf(eml_path, pdf_path, attachment_names):
    # Parse the .eml file
    with open(eml_path, 'rb') as f:
        msg = email.message_from_binary_file(f, policy=policy.default)

    # Extract headers
    headers = [
        ('From', msg.get('From', '')),
        ('To', msg.get('To', '')),
        ('Subject', msg.get('Subject', '')),
        ('Date', msg.get('Date', '')),
        ('Cc', msg.get('Cc', '')),
        ('Bcc', msg.get('Bcc', '')),
        ('Message-ID', msg.get('Message-ID', '')),
    ]

    # Extract plain text or HTML body as text
    body = ''
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == 'text/plain' and part.get_content_disposition() is None:
                payload = part.get_payload(decode=True)
                if payload and isinstance(payload, bytes):
                    try:
                        body = payload.decode(part.get_content_charset() or 'utf-8', errors='replace')
                    except Exception:
                        body = payload.decode('utf-8', errors='replace')
                    break
            elif ctype == 'text/html' and part.get_content_disposition() is None and not body:
                payload = part.get_payload(decode=True)
                if payload and isinstance(payload, bytes):
                    try:
                        body = payload.decode(part.get_content_charset() or 'utf-8', errors='replace')
                    except Exception:
                        body = payload.decode('utf-8', errors='replace')
    else:
        if msg.get_content_type() == 'text/plain' or msg.get_content_type() == 'text/html':
            payload = msg.get_payload(decode=True)
            if payload and isinstance(payload, bytes):
                try:
                    body = payload.decode(msg.get_content_charset() or 'utf-8', errors='replace')
                except Exception:
                    body = payload.decode('utf-8', errors='replace')

    # Create PDF
    pdf = FPDF()
    pdf.add_page()
    # Register and use DejaVuSans font from the fonts folder
    font_path = os.path.join('fonts', 'DejaVuSans.ttf')
    pdf.add_font('DejaVu', '', font_path, uni=True)
    pdf.set_font('DejaVu', '', 12)

    pdf.cell(0, 10, 'Email Details', ln=True, align='C')
    pdf.ln(5)

    for k, v in headers:
        if v:
            pdf.set_font('DejaVu', '', 10)
            pdf.cell(25, 8, f'{k}:', ln=0)
            pdf.set_font('DejaVu', '', 10)
            pdf.multi_cell(0, 8, v)
    pdf.ln(5)

    pdf.set_font('DejaVu', '', 12)
    pdf.cell(0, 10, 'Body:', ln=True)
    pdf.set_font('DejaVu', '', 10)
    pdf.multi_cell(0, 8, body if body else '[No plain text or html body found]')
    pdf.ln(5)

    pdf.set_font('DejaVu', '', 12)
    pdf.cell(0, 10, 'Attachments:', ln=True)
    pdf.set_font('DejaVu', '', 10)
    if attachment_names:
        for i, name in enumerate(attachment_names, 1):
            pdf.cell(0, 8, f'{i}. {name}', ln=True)
    else:
        pdf.cell(0, 8, 'No attachments.', ln=True)

    pdf.output(pdf_path)
    print(f'PDF saved as {pdf_path}')

if __name__ == '__main__':
    last_run = get_last_run_time()
    print(f"Last run: {last_run}")
    emails = list_emails(last_run)
    if not emails:
        print("No new emails since last run or error fetching emails.")
        exit(0)
    now = log_run_time()
    print(f"Current run time logged: {now}")
    # Only process up to DEFAULT_EMAIL_COUNT new emails
    emails_to_process = emails[:DEFAULT_EMAIL_COUNT]
    print(f"Processing {len(emails_to_process)} new emails...")
    for idx, email_obj in enumerate(emails_to_process):
        message_id = email_obj['id']
        # Download .eml to downloaded_emails folder with temp name
        temp_eml_path = os.path.join('downloaded_emails', f"email_{idx+1}.eml")
        download_eml(message_id, f"email_{idx+1}.eml", 'downloaded_emails')
        # Parse headers for folder and file name
        with open(temp_eml_path, 'rb') as f:
            msg = email.message_from_binary_file(f, policy=policy.default)
        folder_name = get_email_folder_name(msg)
        target_folder = os.path.join(PROCESSED_DIR, folder_name)
        os.makedirs(target_folder, exist_ok=True)
        # Create attachments subfolder
        attachments_folder = os.path.join(target_folder, 'attachments')
        os.makedirs(attachments_folder, exist_ok=True)
        # Rename .eml file to match folder_name (remains in downloaded_emails)
        eml_named_path = os.path.join('downloaded_emails', f"{folder_name}.eml")
        os.replace(temp_eml_path, eml_named_path)
        # Download attachments to attachments subfolder
        attachment_names = download_attachments(message_id, attachments_folder)
        # Save PDF in processed_emails subfolder with same name
        pdf_path = os.path.join(target_folder, f"{folder_name}.pdf")
        eml_to_pdf(eml_named_path, pdf_path, attachment_names)
    print("All new emails processed.") 