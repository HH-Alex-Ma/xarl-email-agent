import os
import logging
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone
import mimetypes
from fpdf import FPDF
import email
from email import policy
import re
import base64

# --- CONFIGURATION ---
DIFY_BASE_URL = os.environ.get('DIFY_BASE_URL', 'http://192.168.2.13/v1')
DIFY_API_KEY = os.environ.get('DIFY_API_KEY', 'app-jF3mB60uIx9kgOQxzLseYZis')
USER_ID = os.environ.get('USER_ID', 'Alex Ma')
PROCESSED_DIR = os.environ.get('PROCESSED_DIR', 'processed_emails')
WORKFLOW_RESPONSES_DIR = os.environ.get('WORKFLOW_RESPONSES_DIR', 'workflow_responses')
# 已移除 WEBHOOK_URL

# 企业微信应用推送相关配置
WECOM_CORPID = os.environ.get("WECOM_CORPID")
WECOM_CORPSECRET = os.environ.get("WECOM_CORPSECRET")
WECOM_AGENTID = os.environ.get("WECOM_AGENTID")

EMAIL_ACCESS_TOKEN = os.environ.get('EMAIL_ACCESS_TOKEN')
EMAIL_HEADERS = {'Authorization': f'Bearer {EMAIL_ACCESS_TOKEN}'}
EMAIL_DOWNLOAD_DIR = 'downloaded_emails'
EMAIL_PROCESSED_DIR = 'processed_emails'
EMAIL_LOG_FILE = 'run_log.txt'
EMAIL_DEFAULT_EMAIL_COUNT = 10

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="XARL Email Workflow API", description="API to process new emails and trigger Dify workflow.")

@app.get("/health", summary="健康检查", tags=["Health"])
def health_check():
    return {"status": "ok"}

class ProcessResult(BaseModel):
    folder_name: str
    workflow_status: int
    workflow_response: dict
    webhook_status: Optional[int] = None
    webhook_response: Optional[dict] = None
    error: Optional[str] = None

@app.post("/process_emails", response_model=List[ProcessResult], summary="Process all new emails in the processed_emails folder.")
def process_emails():
    results = []
    pre_run_time = datetime.now(timezone.utc)
    os.makedirs(WORKFLOW_RESPONSES_DIR, exist_ok=True)

    def file_is_newer_than(path, dt):
        mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
        return mtime > dt

    def guess_mime_type(filename):
        mime_type, _ = mimetypes.guess_type(filename)
        return mime_type or 'application/octet-stream'

    def upload_file(filepath):
        url = f"{DIFY_BASE_URL}/files/upload"
        headers = {'Authorization': f'Bearer {DIFY_API_KEY}'}
        mime_type = guess_mime_type(filepath)
        with open(filepath, 'rb') as f:
            files = {'file': (os.path.basename(filepath), f, mime_type)}
            data = {'user': USER_ID}
            resp = requests.post(url, headers=headers, files=files, data=data, timeout=30)
        if resp.status_code == 201:
            data = resp.json()
            return data.get('id') or data.get('file_id')
        else:
            logging.error(f"Failed to upload {filepath}: {resp.status_code} {resp.text}")
            return None

    def get_api_file_type(filename):
        ext = filename.lower().split('.')[-1]
        if ext in ['txt', 'md', 'markdown', 'pdf', 'html', 'xlsx', 'xls', 'docx', 'csv', 'eml', 'msg', 'pptx', 'ppt', 'xml', 'epub']:
            return 'document'
        elif ext in ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg']:
            return 'image'
        elif ext in ['mp3', 'm4a', 'wav', 'webm', 'amr']:
            return 'audio'
        elif ext in ['mp4', 'mov', 'mpeg', 'mpga']:
            return 'video'
        else:
            return 'custom'

    def get_wecom_access_token(corpid, corpsecret):
        url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={corpid}&corpsecret={corpsecret}"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        return data["access_token"]

    def send_wecom_app_message(access_token, agentid, touser, content):
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}"
        payload = {
            "touser": touser,
            "msgtype": "text",
            "agentid": agentid,
            "text": {"content": content},
            "safe": 0
        }
        resp = requests.post(url, json=payload, timeout=10)
        return resp

    def get_last_run_time():
        if not os.path.exists(EMAIL_LOG_FILE):
            return None
        with open(EMAIL_LOG_FILE, 'r') as f:
            ts = f.read().strip()
            if ts:
                try:
                    return datetime.fromisoformat(ts)
                except Exception:
                    return None
        return None

    def log_run_time():
        now = datetime.now(timezone.utc).isoformat()
        with open(EMAIL_LOG_FILE, 'w') as f:
            f.write(now)
        return now

    def list_emails(since_datetime=None):
        url = 'https://graph.microsoft.com/v1.0/me/messages?$top=50&$orderby=receivedDateTime desc'
        resp = requests.get(url, headers=EMAIL_HEADERS)
        if resp.status_code != 200:
            logging.error(f"Error fetching emails: {resp.status_code}\n{resp.text}")
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
        return filtered_emails

    def sanitize_filename(name):
        return re.sub(r'[\\/:*?"<>|]', '_', name)

    def get_email_folder_name(msg):
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
        resp = requests.get(url, headers=EMAIL_HEADERS)
        if resp.status_code == 200:
            with open(filepath, 'wb') as f:
                f.write(resp.content)
        else:
            logging.error(f"Error downloading .eml: {resp.status_code}\n{resp.text}")
        return filepath

    def download_attachments(message_id, folder):
        url = f'https://graph.microsoft.com/v1.0/me/messages/{message_id}/attachments'
        resp = requests.get(url, headers=EMAIL_HEADERS)
        if resp.status_code != 200:
            logging.error(f"Error fetching attachments: {resp.status_code}\n{resp.text}")
            return []
        attachments = resp.json().get('value', [])
        if not attachments:
            return []
        att_names = []
        for att in attachments:
            att_id = att['id']
            att_name = att['name']
            att_names.append(att_name)
            if att.get('@odata.mediaContentType'):
                att_url = f'https://graph.microsoft.com/v1.0/me/messages/{message_id}/attachments/{att_id}/$value'
                att_resp = requests.get(att_url, headers=EMAIL_HEADERS)
                if att_resp.status_code == 200:
                    att_path = os.path.join(folder, att_name)
                    with open(att_path, 'wb') as f:
                        f.write(att_resp.content)
                else:
                    logging.error(f"Failed to download attachment {att_name}: {att_resp.status_code}")
            elif 'contentBytes' in att:
                att_path = os.path.join(folder, att_name)
                with open(att_path, 'wb') as f:
                    f.write(base64.b64decode(att['contentBytes']))
            else:
                logging.warning(f"Unknown attachment type for {att_name}")
        return att_names

    def eml_to_pdf(eml_path, pdf_path, attachment_names):
        with open(eml_path, 'rb') as f:
            msg = email.message_from_binary_file(f, policy=policy.default)
        headers = [
            ('From', msg.get('From', '')),
            ('To', msg.get('To', '')),
            ('Subject', msg.get('Subject', '')),
            ('Date', msg.get('Date', '')),
            ('Cc', msg.get('Cc', '')),
            ('Bcc', msg.get('Bcc', '')),
            ('Message-ID', msg.get('Message-ID', '')),
        ]
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
        pdf = FPDF()
        pdf.add_page()
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

    # --- Collect email-attachments groups from processed_emails/ subfolders ---
    email_groups = []
    if os.path.exists(PROCESSED_DIR):
        for folder in os.listdir(PROCESSED_DIR):
            folder_path = os.path.join(PROCESSED_DIR, folder)
            if not os.path.isdir(folder_path):
                continue
            pdf_path = None
            attachments = []
            # Find PDF in the main folder
            for file in os.listdir(folder_path):
                path = os.path.join(folder_path, file)
                if not file_is_newer_than(path, pre_run_time):
                    continue
                if file.lower().endswith('.pdf'):
                    pdf_path = path
            # Find attachments in the attachments subfolder
            attachments_folder = os.path.join(folder_path, 'attachments')
            if os.path.exists(attachments_folder) and os.path.isdir(attachments_folder):
                for att_file in os.listdir(attachments_folder):
                    att_path = os.path.join(attachments_folder, att_file)
                    if not file_is_newer_than(att_path, pre_run_time):
                        continue
                    attachments.append(att_path)
            if pdf_path:
                email_groups.append({
                    'email': pdf_path,
                    'attachments': attachments,
                    'folder_name': folder
                })

    workflow_url = f"{DIFY_BASE_URL}/workflows/run"
    headers = {
        'Authorization': f'Bearer {DIFY_API_KEY}',
        'Content-Type': 'application/json'
    }

    import json
    for group in email_groups:
        try:
            # Upload email PDF
            email_upload_id = upload_file(group['email'])
            emails_payload = []
            if email_upload_id:
                emails_payload.append({
                    'transfer_method': 'local_file',
                    'upload_file_id': email_upload_id,
                    'type': get_api_file_type(group['email']),
                    'source_path': group['email']
                })
            # Upload attachments
            attachments_payload = []
            for att_path in group['attachments']:
                att_upload_id = upload_file(att_path)
                if att_upload_id:
                    attachments_payload.append({
                        'transfer_method': 'local_file',
                        'upload_file_id': att_upload_id,
                        'type': get_api_file_type(att_path),
                        'source_path': att_path
                    })
            inputs = {
                'email': emails_payload[0] if emails_payload else None,
                'attachments': attachments_payload
            }
            body = {
                'inputs': inputs,
                'response_mode': 'blocking',
                'user': USER_ID
            }
            resp = requests.post(workflow_url, headers=headers, json=body, timeout=60)
            workflow_status = resp.status_code
            workflow_response = resp.json() if resp.headers.get('content-type', '').startswith('application/json') else {'text': resp.text}
            # Save response to workflow_responses/folder_name.txt
            response_filename = os.path.join(WORKFLOW_RESPONSES_DIR, f"{group['folder_name']}.txt")
            with open(response_filename, 'w', encoding='utf-8') as f:
                f.write(json.dumps(workflow_response, ensure_ascii=False, indent=2))
            # --- Extract outputs and send to WeCom App ---
            data = workflow_response.get('data', {})
            if isinstance(data, dict):
                outputs = data.get('outputs', {})
            else:
                outputs = {}
            notification = outputs.get('notification', '')
            result = outputs.get('result', '')
            # touser 从 notification.recipient_email 获取
            touser = None
            if isinstance(notification, dict):
                touser = notification.get('recipient_email')
                notification = touser or str(notification)
            if isinstance(result, dict):
                result = json.dumps(result, ensure_ascii=False, indent=2)
            wecom_status = None
            wecom_response = None
            if touser and (notification or result):
                content = f"{notification}\n\n{result}"
                logging.info(f"WeCom App message to send to {touser}:\n{content}\n")
                try:
                    access_token = get_wecom_access_token(WECOM_CORPID, WECOM_CORPSECRET)
                    wecom_resp = send_wecom_app_message(access_token, WECOM_AGENTID, touser, content)
                    wecom_status = wecom_resp.status_code
                    try:
                        wecom_response = wecom_resp.json()
                    except Exception:
                        wecom_response = {'text': wecom_resp.text}
                except Exception as e:
                    logging.exception("WeCom App message send failed")
                    wecom_response = {'error': str(e)}
            results.append(ProcessResult(
                folder_name=group['folder_name'],
                workflow_status=workflow_status,
                workflow_response=workflow_response,
                webhook_status=wecom_status,
                webhook_response=wecom_response
            ))
        except Exception as e:
            logging.exception(f"Error processing group {group['folder_name']}")
            results.append(ProcessResult(
                folder_name=group['folder_name'],
                workflow_status=0,
                workflow_response={},
                error=str(e)
            ))
    return results 

@app.post("/get_emails", summary="拉取新邮件并处理为PDF和附件")
def get_emails():
    last_run = get_last_run_time()
    emails = list_emails(last_run)
    if not emails:
        return {"message": "No new emails since last run or error fetching emails.", "folders": []}
    now = log_run_time()
    emails_to_process = emails[:EMAIL_DEFAULT_EMAIL_COUNT]
    processed_folders = []
    for idx, email_obj in enumerate(emails_to_process):
        message_id = email_obj['id']
        temp_eml_path = os.path.join(EMAIL_DOWNLOAD_DIR, f"email_{idx+1}.eml")
        download_eml(message_id, f"email_{idx+1}.eml", EMAIL_DOWNLOAD_DIR)
        with open(temp_eml_path, 'rb') as f:
            msg = email.message_from_binary_file(f, policy=policy.default)
        folder_name = get_email_folder_name(msg)
        target_folder = os.path.join(EMAIL_PROCESSED_DIR, folder_name)
        os.makedirs(target_folder, exist_ok=True)
        attachments_folder = os.path.join(target_folder, 'attachments')
        os.makedirs(attachments_folder, exist_ok=True)
        eml_named_path = os.path.join(EMAIL_DOWNLOAD_DIR, f"{folder_name}.eml")
        os.replace(temp_eml_path, eml_named_path)
        attachment_names = download_attachments(message_id, attachments_folder)
        pdf_path = os.path.join(target_folder, f"{folder_name}.pdf")
        eml_to_pdf(eml_named_path, pdf_path, attachment_names)
        processed_folders.append(folder_name)
    return {"message": f"Processed {len(processed_folders)} new emails.", "folders": processed_folders} 