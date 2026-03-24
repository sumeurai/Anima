import { app, BrowserWindow, Menu, nativeTheme } from 'electron';
import { ChildProcess, spawn } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import path from 'node:path';
import started from 'electron-squirrel-startup';

if (started) {
  app.quit();
}

// ---------------------------------------------------------------------------
// .env loader
// ---------------------------------------------------------------------------

const loadDotEnvFile = (): void => {
  const candidates = [
    path.join(process.cwd(), '.env'),
    path.join(app.getAppPath(), '.env'),
  ];
  for (const envFilePath of candidates) {
    if (!fs.existsSync(envFilePath)) continue;
    const envContent = fs.readFileSync(envFilePath, 'utf8');
    for (const line of envContent.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const idx = trimmed.indexOf('=');
      if (idx <= 0) continue;
      const key = trimmed.slice(0, idx).trim();
      const val = trimmed.slice(idx + 1).trim().replace(/^['"]|['"]$/g, '');
      if (!(key in process.env)) {
        process.env[key] = val;
      }
    }
    break;
  }
};

loadDotEnvFile();

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const BACKEND_HOST = process.env.ANIMA_HOST?.trim() || '127.0.0.1';
const BACKEND_PORT = parseInt(process.env.ANIMA_PORT?.trim() || '19000', 10);
const BACKEND_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`;

const isPackaged = app.isPackaged;
const projectRoot = isPackaged
  ? process.resourcesPath
  : path.resolve(app.getAppPath(), '..');

const windowIcon = path.join(
  app.getAppPath(),
  'assets',
  process.platform === 'win32' ? 'icon.ico' : 'icon.png',
);

nativeTheme.themeSource = 'dark';

// ---------------------------------------------------------------------------
// Flask backend
// ---------------------------------------------------------------------------

let backendChild: ChildProcess | null = null;

const isPortOpen = (host: string, port: number): Promise<boolean> => {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    socket.setTimeout(300);
    socket.once('connect', () => { socket.destroy(); resolve(true); });
    socket.once('timeout', () => { socket.destroy(); resolve(false); });
    socket.once('error', () => { socket.destroy(); resolve(false); });
    socket.connect(port, host);
  });
};

const waitForPort = async (host: string, port: number, maxMs = 30000): Promise<boolean> => {
  const start = Date.now();
  while (Date.now() - start < maxMs) {
    if (await isPortOpen(host, port)) return true;
    await new Promise((r) => setTimeout(r, 500));
  }
  return false;
};

const findPython = (): string | null => {
  const custom = process.env.ANIMA_PYTHON?.trim();
  if (custom) return custom;

  const candidates = ['python', 'python3'];
  for (const bin of candidates) {
    try {
      const { execSync } = require('node:child_process');
      execSync(`${bin} --version`, { stdio: 'ignore' });
      return bin;
    } catch {
      // not found
    }
  }
  return null;
};

const spawnBackend = (): ChildProcess | null => {
  const serverDir = path.join(projectRoot, 'server');
  const script = path.join(serverDir, 'app.py');
  if (!fs.existsSync(script)) {
    console.warn(`[Electron] server/app.py not found: ${script}`);
    return null;
  }

  const pythonBin = findPython();
  if (!pythonBin) {
    console.error('[Electron] Python not found. Please install Python 3.');
    return null;
  }

  console.log(`[Electron] Starting backend: ${pythonBin} ${script}`);
  const child = spawn(pythonBin, [script, '--port', String(BACKEND_PORT)], {
    cwd: serverDir,
    stdio: 'inherit',
    env: { ...process.env },
  });

  child.on('error', (err) => {
    console.error('[Electron] Backend spawn error:', err.message);
  });

  child.on('exit', (code) => {
    console.log(`[Electron] Backend exited with code ${code}`);
    backendChild = null;
  });

  return child;
};

// ---------------------------------------------------------------------------
// Window
// ---------------------------------------------------------------------------

let mainWindow: BrowserWindow | null = null;

const createWindow = () => {
  Menu.setApplicationMenu(null);

  mainWindow = new BrowserWindow({
    width: 880,
    height: 1000,
    minWidth: 600,
    minHeight: 600,
    icon: windowIcon,
    backgroundColor: '#000000',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  mainWindow.setMenuBarVisibility(false);
  mainWindow.setTitle('Anima');
  mainWindow.on('page-title-updated', (e) => e.preventDefault());

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
  });

  mainWindow.loadURL(BACKEND_URL);

  if (!isPackaged) {
    mainWindow.webContents.openDevTools();
  }

  mainWindow.webContents.on('before-input-event', (_event, input) => {
    if (input.type !== 'keyDown') return;
    if (input.key === 'F12' || (input.control && input.shift && input.key.toLowerCase() === 'i')) {
      mainWindow?.webContents.toggleDevTools();
    }
  });
};

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

app.on('ready', async () => {
  const alreadyRunning = await isPortOpen(BACKEND_HOST, BACKEND_PORT);

  if (!alreadyRunning) {
    backendChild = spawnBackend();
    if (backendChild) {
      console.log(`[Electron] Waiting for backend on port ${BACKEND_PORT}...`);
      const ready = await waitForPort(BACKEND_HOST, BACKEND_PORT);
      if (!ready) {
        console.error('[Electron] Backend failed to start within timeout.');
      }
    }
  } else {
    console.log(`[Electron] Backend already running on port ${BACKEND_PORT}`);
  }

  createWindow();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

app.on('before-quit', () => {
  if (backendChild) {
    console.log('[Electron] Killing backend process...');
    try {
      backendChild.kill();
    } catch {
      // ignore
    }
  }
});
