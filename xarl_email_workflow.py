import os
import subprocess
import requests
from datetime import datetime, timezone
import mimetypes

# --- CONFIGURATION ---
DIFY_BASE_URL = 'http://192.168.2.13/v1'
DIFY_API_KEY = 'app-jF3mB60uIx9kgOQxzLseYZis'  # Inserted actual API key
USER_ID = 'Alex Ma'
PROCESSED_DIR = 'processed_emails'
WORKFLOW_RESPONSES_DIR = 'workflow_responses'

# --- 1. Note the timestamp before running the download script (do NOT write to run_log.txt) ---
def get_current_timestamp():
    return datetime.now(timezone.utc).isoformat()

def file_is_newer_than(path, dt):
    # Use modification time (mtime) for comparison
    mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    return mtime > dt

pre_run_time_iso = get_current_timestamp()
pre_run_time = datetime.fromisoformat(pre_run_time_iso)

# --- 2. Run the email download script ---
subprocess.run(['python', 'download_email_as_eml.py'], check=True)

# --- 3. Collect email-attachments groups from processed_emails/ subfolders ---
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

def guess_mime_type(filename):
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type or 'application/octet-stream'

def upload_file(filepath):
    url = f"{DIFY_BASE_URL}/files/upload"
    headers = {
        'Authorization': f'Bearer {DIFY_API_KEY}'
    }
    mime_type = guess_mime_type(filepath)
    with open(filepath, 'rb') as f:
        files = {'file': (os.path.basename(filepath), f, mime_type)}
        data = {'user': USER_ID}
        print("\n--- Upload Request ---")
        print(f"URL: {url}")
        print(f"Headers: {headers}")
        print(f"Form data: user={USER_ID}")
        print(f"File: {os.path.basename(filepath)} (MIME: {mime_type})")
        print("---------------------\n")
        resp = requests.post(url, headers=headers, files=files, data=data)
        print(f"Upload response: {resp.status_code} {resp.text}")
    if resp.status_code == 201:
        data = resp.json()
        return data.get('id') or data.get('file_id')
    else:
        print(f"Failed to upload {filepath}: {resp.status_code} {resp.text}")
        return None

def get_file_type(filename):
    ext = filename.lower().split('.')[-1]
    # Map extensions to Dify types
    mapping = {
        'eml': 'EML',
        'pdf': 'PDF',
        'docx': 'DOCX',
        'doc': 'DOCX',
        'xlsx': 'XLSX',
        'xls': 'XLS',
        'csv': 'CSV',
        'pptx': 'PPTX',
        'ppt': 'PPT',
        'xml': 'XML',
        'epub': 'EPUB',
        'txt': 'TXT',
        'md': 'MD',
        'markdown': 'MARKDOWN',
        'jpg': 'JPG',
        'jpeg': 'JPEG',
        'png': 'PNG',
        'gif': 'GIF',
        'webp': 'WEBP',
        'svg': 'SVG',
        'mp3': 'MP3',
        'm4a': 'M4A',
        'wav': 'WAV',
        'webm': 'WEBM',
        'amr': 'AMR',
        'mp4': 'MP4',
        'mov': 'MOV',
        'mpeg': 'MPEG',
        'mpga': 'MPGA',
        'msg': 'MSG',
    }
    return mapping.get(ext, 'custom')

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

# --- 4. For each email group, upload files and run workflow ---
workflow_url = f"{DIFY_BASE_URL}/workflows/run"
headers = {
    'Authorization': f'Bearer {DIFY_API_KEY}',
    'Content-Type': 'application/json'
}

os.makedirs(WORKFLOW_RESPONSES_DIR, exist_ok=True)

for group in email_groups:
    # Upload email PDF
    email_upload_id = upload_file(group['email'])
    emails_payload = []
    if email_upload_id:
        # This is the email file (PDF)
        emails_payload.append({
            'transfer_method': 'local_file',
            'upload_file_id': email_upload_id,
            'type': get_api_file_type(group['email']),
            'source_path': group['email']  # for debugging/tracking
        })
    # Upload attachments
    attachments_payload = []
    attachment_id_map = {}  # Map: local path -> upload id
    for att_path in group['attachments']:
        att_upload_id = upload_file(att_path)
        if att_upload_id:
            attachments_payload.append({
                'transfer_method': 'local_file',
                'upload_file_id': att_upload_id,
                'type': get_api_file_type(att_path),
                'source_path': att_path  # for debugging/tracking
            })
            attachment_id_map[att_path] = att_upload_id
    # Send workflow run request for this email + its attachments
    # emails_payload[0]['upload_file_id'] is the email file id
    # [a['upload_file_id'] for a in attachments_payload] are the attachment file ids
    inputs = {
        'email': emails_payload[0] if emails_payload else None,
        'attachments': attachments_payload
    }
    body = {
        'inputs': inputs,
        'response_mode': 'blocking',  # Changed from 'streaming' to 'blocking'
        'user': USER_ID
    }
    print(f"\n--- Workflow Run Request ---\nURL: {workflow_url}\nHeaders: {headers}\nBody: {body}\n----------------------------\n")
    resp = requests.post(workflow_url, headers=headers, json=body)
    print(f"Workflow response for {os.path.basename(group['email'])}: {resp.status_code}")
    print(f"Workflow run response: {resp.text}")
    # Save response to workflow_responses/folder_name.txt
    response_filename = os.path.join(WORKFLOW_RESPONSES_DIR, f"{group['folder_name']}.txt")
    with open(response_filename, 'w', encoding='utf-8') as f:
        f.write(resp.text)

    # --- Extract outputs and send to webhook ---
    webhook_url = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=594dab3f-7a5e-436f-8345-8ee29fb9d93e'  # TODO: Replace with your actual webhook URL
    try:
        resp_json = resp.json()
        outputs = resp_json.get('data', {}).get('outputs', {})
        notification = outputs.get('notification', '')
        result = outputs.get('result', '')
        # Convert notification and result to string if they are dicts
        if isinstance(notification, dict):
            notification = notification.get('recipient_email', str(notification))
        if isinstance(result, dict):
            import json
            result = json.dumps(result, ensure_ascii=False, indent=2)
        if notification or result:
            content = f"{notification}\n\n{result}"
            print(f"Webhook message to send:\n{content}\n")
            webhook_payload = {
                "msgtype": "text",
                "text": {
                    "content": content
                }
            }
            webhook_headers = {'Content-Type': 'application/json'}
            webhook_resp = requests.post(webhook_url, headers=webhook_headers, json=webhook_payload)
            print(f"Webhook response: {webhook_resp.status_code} {webhook_resp.text}")
        else:
            print("No notification or result found in workflow outputs.")
    except Exception as e:
        print(f"Error processing workflow response or sending webhook: {e}")
