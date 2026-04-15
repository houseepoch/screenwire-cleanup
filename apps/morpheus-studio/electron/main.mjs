import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { DesktopRuntime } from './desktop-runtime.mjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const appRoot = path.resolve(__dirname, '..');
const repoRoot = path.resolve(appRoot, '..', '..');
const devServerUrl = process.env.VITE_DEV_SERVER_URL || 'http://127.0.0.1:5173';

let mainWindow = null;
let runtime = null;

function getRuntime() {
  if (!runtime) {
    throw new Error('Desktop runtime not initialized');
  }
  return runtime;
}

function resolveProjectsRoot() {
  const override = process.env.SW_PROJECTS_ROOT?.trim();
  if (override) {
    return path.resolve(override);
  }
  if (!app.isPackaged) {
    return path.join(repoRoot, 'projects');
  }
  const documentsRoot = app.getPath('documents') || app.getPath('userData');
  return path.join(documentsRoot, 'ScreenWire Projects');
}

function resolveBackendRoot() {
  if (!app.isPackaged) {
    return repoRoot;
  }
  return path.join(process.resourcesPath, 'backend');
}

async function createProjectWindow() {
  mainWindow = new BrowserWindow({
    width: 1600,
    height: 1000,
    minWidth: 1200,
    minHeight: 800,
    backgroundColor: '#070b14',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow?.show();
  });

  if (!app.isPackaged) {
    await mainWindow.loadURL(devServerUrl);
    mainWindow.webContents.openDevTools({ mode: 'detach' });
    return;
  }

  await mainWindow.loadFile(path.join(appRoot, 'dist', 'index.html'));
}

ipcMain.handle('screenwire:list-projects', async () => {
  return getRuntime().listProjects();
});

ipcMain.handle('screenwire:create-project', async (_event, payload) => {
  return getRuntime().createProject(payload);
});

ipcMain.handle('screenwire:select-project', async (_event, projectId) => {
  return getRuntime().startBackend(projectId);
});

ipcMain.handle('screenwire:return-to-projects', async () => {
  return getRuntime().returnToProjects();
});

ipcMain.handle('screenwire:get-backend-state', async () => {
  return getRuntime().getBackendState();
});

ipcMain.handle('screenwire:open-project-folder', async (_event, projectId) => {
  const targetDir = path.join(getRuntime().projectsRoot, projectId);
  if (!existsSync(targetDir)) {
    throw new Error(`Project not found: ${projectId}`);
  }
  return shell.openPath(targetDir);
});

ipcMain.handle('screenwire:choose-file', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile'],
  });
  if (result.canceled || result.filePaths.length === 0) {
    return null;
  }
  return result.filePaths[0];
});

app.whenReady().then(async () => {
  runtime = new DesktopRuntime({
    backendRoot: resolveBackendRoot(),
    appRoot,
    projectsRoot: resolveProjectsRoot(),
    isPackaged: app.isPackaged,
  });
  if (app.isPackaged) {
    const issues = await runtime.collectRuntimeIssues();
    if (issues.length > 0) {
      await dialog.showMessageBox({
        type: 'warning',
        title: 'Additional setup required',
        message: 'ScreenWire Studio needs local runtime tools before project generation can start.',
        detail: `${issues.join('\n')}\n\nOnce the required tools are installed, relaunch the app.`,
        buttons: ['Continue'],
        defaultId: 0,
      });
    }
  }
  await createProjectWindow();

  app.on('activate', async () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      await createProjectWindow();
    }
  });
});

app.on('window-all-closed', async () => {
  await runtime?.stopBackend();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
