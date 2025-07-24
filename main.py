import os
import logging
import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone
import mimetypes

# --- CONFIGURATION ---
DIFY_BASE_URL = os.environ.get('DIFY_BASE_URL', 'http://192.168.2.13/v1')
DIFY_API_KEY = os.environ.get('DIFY_API_KEY', 'app-jF3mB60uIx9kgOQxzLseYZis')
USER_ID = os.environ.get('USER_ID', 'Alex Ma')
PROCESSED_DIR = os.environ.get('PROCESSED_DIR', 'processed_emails')
WORKFLOW_RESPONSES_DIR = os.environ.get('WORKFLOW_RESPONSES_DIR', 'workflow_responses')
WEBHOOK_URL = os.environ.get('WEBHOOK_URL', 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_WEBHOOK_KEY')

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="XARL Email Workflow API", description="API to process new emails and trigger Dify workflow.")

class ProcessResult(BaseModel):
    folder_name: str
    workflow_status: int
    workflow_response: dict
    webhook_status: Optional[int] = None
    webhook_response: Optional[dict] = None
    error: Optional[str] = None

@app.post("/process-emails", response_model=List[ProcessResult], summary="Process all new emails in the processed_emails folder.")
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
            # --- Extract outputs and send to webhook ---
            data = workflow_response.get('data', {})
            if isinstance(data, dict):
                outputs = data.get('outputs', {})
            else:
                outputs = {}
            notification = outputs.get('notification', '')
            result = outputs.get('result', '')
            if isinstance(notification, dict):
                notification = notification.get('recipient_email', str(notification))
            if isinstance(result, dict):
                result = json.dumps(result, ensure_ascii=False, indent=2)
            webhook_status = None
            webhook_response = None
            if notification or result:
                content = f"{notification}\n\n{result}"
                logging.info(f"Webhook message to send:\n{content}\n")
                webhook_payload = {
                    "msgtype": "text",
                    "text": {
                        "content": content
                    }
                }
                webhook_headers = {'Content-Type': 'application/json'}
                webhook_resp = requests.post(WEBHOOK_URL, headers=webhook_headers, json=webhook_payload, timeout=10)
                webhook_status = webhook_resp.status_code
                try:
                    webhook_response = webhook_resp.json()
                except Exception:
                    webhook_response = {'text': webhook_resp.text}
            results.append(ProcessResult(
                folder_name=group['folder_name'],
                workflow_status=workflow_status,
                workflow_response=workflow_response,
                webhook_status=webhook_status,
                webhook_response=webhook_response
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