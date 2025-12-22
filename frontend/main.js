const { app, BrowserWindow } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');

const isDev = !app.isPackaged;
let pythonProcess = null;
let managedBackend = false;

const BACKEND_URL = 'http://127.0.0.1:8000/api/status';

function resolvePythonExecutable() {
  if (process.env.PYTHON_PATH) return process.env.PYTHON_PATH;
  const condaPrefix = process.env.CONDA_PREFIX;
  if (condaPrefix) {
    const condaPython = path.join(
      condaPrefix,
      process.platform === 'win32' ? 'python.exe' : 'bin/python'
    );
    return condaPython;
  }
  const venvPrefix = process.env.VIRTUAL_ENV;
  if (venvPrefix) {
    return path.join(
      venvPrefix,
      process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'
    );
  }
  return process.platform === 'win32' ? 'python' : 'python3';
}

function checkBackendAlive() {
  return new Promise(resolve => {
    const request = http.get(BACKEND_URL, res => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    request.on('error', () => resolve(false));
    request.setTimeout(500, () => {
      request.destroy();
      resolve(false);
    });
  });
}

async function startPythonBackend() {
  const alreadyRunning = await checkBackendAlive();
  if (alreadyRunning) {
    managedBackend = false;
    return;
  }

  const python = resolvePythonExecutable();
  const script = path.join(__dirname, 'engine', 'main.py');

  pythonProcess = spawn(python, [script], {
    stdio: 'inherit',
    env: {
      ...process.env,
      PYTHONUNBUFFERED: '1',
    },
  });
  managedBackend = true;

  console.log(`[backend] Spawned Python with: ${python}`);

  pythonProcess.on('close', code => {
    pythonProcess = null;
    if (code !== 0) {
      console.error(`Python backend exited with code ${code}`);
    }
  });
}

function stopPythonBackend() {
  if (managedBackend && pythonProcess) {
    pythonProcess.kill('SIGTERM');
  }
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1400,
    height: 900,
    backgroundColor: '#0d0e12',
    webPreferences: {
      contextIsolation: true,
    },
  });

  if (isDev) {
    win.loadURL('http://localhost:5173');
    win.webContents.openDevTools({ mode: 'detach' });
  } else {
    win.loadFile(path.join(__dirname, 'dist', 'index.html'));
  }
}

app.whenReady().then(async () => {
  await startPythonBackend();
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('before-quit', () => {
  stopPythonBackend();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

process.on('exit', stopPythonBackend);
process.on('SIGINT', () => {
  stopPythonBackend();
  app.quit();
});
