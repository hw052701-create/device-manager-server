# Personal Device Manager — Cloud-Hosted Server (server_git)

This is the **cloud-hosted version** of the Personal Device Manager server. It runs on Railway (or any PaaS) and stores all files on **Google Drive** instead of local disk (since Railway's filesystem is ephemeral).

## Key Difference From Local Version

| Local (server/) | Cloud (server_git/) |
|---|---|
| Files saved to `server/data/...` | Files uploaded to Google Drive |
| URL changes (Cloudflare tunnel) | Fixed URL (Railway domain) |
| Runs on your Mac | Runs on Railway 24/7 |
| `sync_to_gdrive.sh` for backup | Direct Drive upload on receive |

## Setup Instructions

### 1. Google Cloud Project & Drive API

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (free)
3. Enable **Google Drive API**:
   - APIs & Services → Library → search "Google Drive API" → Enable
4. Create OAuth 2.0 credentials:
   - APIs & Services → Credentials → Create Credentials → OAuth Client ID
   - Application type: **Web application**
   - Name: `DeviceManager Server`
   - Authorized redirect URIs: `http://localhost:5000/oauth2callback` (for local testing)
   - Download the JSON file → save as `credentials.json` in this folder

### 2. First-Time Authentication

When you run `server.py` for the first time, it will:
1. Start a local web server on port 5000
2. Print a URL in the terminal
3. Open your browser to that URL
4. You sign in with your Google account and grant Drive access
5. The server saves a `token.json` (refresh token) for future use

After that, the server can upload/download from Drive without user interaction.

### 3. Create a Google Drive Folder

Create a folder in your Google Drive called `DeviceManager`. Note its Folder ID from the URL:
```
https://drive.google.com/drive/folders/1ABC123XYZ...
                                  ↑^^^^^^^^^^^^^^^^
                                  This is the Folder ID
```

Set it as an environment variable on Railway:
```
DRIVE_FOLDER_ID=1ABC123XYZ...
```

### 4. Deploy to Railway

1. Push this folder to a GitHub repository
2. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
3. Add the following environment variables:
   - `DRIVE_FOLDER_ID` = your Drive folder ID
   - `PORT` = 5000 (Railway sets this automatically, but good to have)
4. Railway will build and deploy — you get a URL like `https://your-app.railway.app`

### 5. Update Google Sheet

Put the Railway URL in your Google Sheet:
```
https://your-app.railway.app
```

The phone app fetches this URL from the sheet and connects.

### 6. Update Dashboard

Open `web/panel.html` and set:
```javascript
let SERVER_URL = "https://your-app.railway.app";
```

Or better: have the dashboard fetch the URL from the Google Sheet too.

## File Storage on Google Drive

After setup, all files are stored in your Google Drive:

```
Google Drive/
└── DeviceManager/
    ├── CallRecordings/
    │   ├── device_923c4471_call_20260914_190000.m4a
    │   └── ...
    ├── Photos/
    │   ├── photo_device_12da33f4_1789271369.jpg
    │   └── ...
    ├── Recordings/
    │   └── ...
    └── Files/
        └── ...
```

## API Endpoints

Same as the local version, plus Drive integration:

- `POST /api/call-recording/<device_id>` — Receive call recording → upload to Drive
- `POST /api/photos/upload` — Upload photo → upload to Drive
- `POST /api/files/upload` — Upload file → upload to Drive
- `POST /api/recordings/upload` — Upload recording → upload to Drive
- `GET /api/drive/records/<device_id>` — List all Drive files for a device
- `GET /api/drive/file/<file_id>` — Get Drive file metadata + download URL

## Local Testing

```bash
cd server_git
pip install -r requirements.txt
python server.py
```

The server will start on `http://localhost:5000` and open a browser for OAuth.

## Railway Deployment

```bash
# Install Railway CLI (optional, for CLI deployment)
npm install -g @railway/cli

# Deploy from the server_git folder
cd server_git
railway login
railway init
railway up
```

Or use the Railway web dashboard to connect your GitHub repo.