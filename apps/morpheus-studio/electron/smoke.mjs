import { app, BrowserWindow, ipcMain } from 'electron';
import { existsSync } from 'node:fs';
import { mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import http from 'node:http';
import { DesktopRuntime } from './desktop-runtime.mjs';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const appRoot = path.resolve(__dirname, '..');
const repoRoot = path.resolve(appRoot, '..', '..');
const distIndex = path.join(appRoot, 'dist', 'index.html');
const preloadPath = path.join(__dirname, 'preload.cjs');
const smokeDir = path.join(appRoot, 'smoke');
const artifactDir = path.join(smokeDir, 'artifacts');

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith('--')) continue;
    const key = token.slice(2);
    const value = argv[i + 1] && !argv[i + 1].startsWith('--') ? argv[++i] : 'true';
    args[key] = value;
  }
  return args;
}

async function waitFor(fn, timeoutMs = 15000, intervalMs = 150) {
  const started = Date.now();
  while (true) {
    const result = await fn();
    if (result) return result;
    if (Date.now() - started > timeoutMs) {
      throw new Error(`Timed out after ${timeoutMs}ms`);
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

async function requestJson(url) {
  return new Promise((resolve, reject) => {
    const req = http.get(url, (res) => {
      let data = '';
      res.on('data', (chunk) => {
        data += chunk;
      });
      res.on('end', () => {
        try {
          resolve({
            status: res.statusCode || 0,
            body: data ? JSON.parse(data) : {},
          });
        } catch (error) {
          reject(error);
        }
      });
    });
    req.on('error', reject);
  });
}

async function queryByTestId(win, testId) {
  return win.webContents.executeJavaScript(
    `(() => Boolean(document.querySelector('[data-testid="${testId}"]')))()`,
    true,
  );
}

async function textByTestId(win, testId) {
  return win.webContents.executeJavaScript(
    `(() => document.querySelector('[data-testid="${testId}"]')?.textContent || '')()`,
    true,
  );
}

async function clickByTestId(win, testId) {
  await win.webContents.executeJavaScript(
    `(() => {
      const el = document.querySelector('[data-testid="${testId}"]');
      if (!el) throw new Error('Missing test id: ${testId}');
      if ('disabled' in el && el.disabled) {
        throw new Error('Element is disabled: ${testId}');
      }
      el.click();
      return true;
    })()`,
    true,
  );
}

async function typeByTestId(win, testId, value) {
  const serialized = JSON.stringify(String(value));
  await win.webContents.executeJavaScript(
    `(() => {
      const el = document.querySelector('[data-testid="${testId}"]');
      if (!el) throw new Error('Missing test id: ${testId}');
      el.focus();
      const descriptor = el instanceof window.HTMLTextAreaElement
        ? Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')
        : Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
      if (!descriptor || typeof descriptor.set !== 'function') {
        throw new Error('Missing native value setter for ${testId}');
      }
      descriptor.set.call(el, ${serialized});
      el.dispatchEvent(new InputEvent('input', { bubbles: true, data: ${serialized}, inputType: 'insertText' }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      el.blur();
      return el.value;
    })()`,
    true,
  );
}

async function injectFile(win, testId, filePath) {
  const raw = await readFile(filePath);
  const base64 = raw.toString('base64');
  const filename = path.basename(filePath);
  const mime = filename.endsWith('.md') ? 'text/markdown' : 'text/plain';
  const payload = JSON.stringify({ base64, filename, mime });
  await win.webContents.executeJavaScript(
    `(() => {
      const el = document.querySelector('[data-testid="${testId}"]');
      if (!el) throw new Error('Missing test id: ${testId}');
      const { base64, filename, mime } = ${payload};
      const binary = atob(base64);
      const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
      const file = new File([bytes], filename, { type: mime });
      const dt = new DataTransfer();
      dt.items.add(file);
      el.files = dt.files;
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return { name: file.name, size: file.size };
    })()`,
    true,
  );
}

async function capture(win, name) {
  await mkdir(artifactDir, { recursive: true });
  const image = await win.webContents.capturePage();
  const target = path.join(artifactDir, name);
  await writeFile(target, image.toPNG());
  return target;
}

function registerIpc(runtime) {
  ipcMain.handle('screenwire:list-projects', async () => runtime.listProjects());
  ipcMain.handle('screenwire:create-project', async (_event, payload) => runtime.createProject(payload));
  ipcMain.handle('screenwire:select-project', async (_event, projectId) => runtime.startBackend(projectId));
  ipcMain.handle('screenwire:return-to-projects', async () => runtime.returnToProjects());
  ipcMain.handle('screenwire:get-backend-state', async () => runtime.getBackendState());
  ipcMain.handle('screenwire:open-project-folder', async () => '');
  ipcMain.handle('screenwire:choose-file', async () => null);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const keepProject = args['keep-project'] === 'true';
  const runtime = new DesktopRuntime({ repoRoot, appRoot });
  const report = {
    generatedAt: new Date().toISOString(),
    config: {
      seed: args.seed || null,
      frameBudget: args['frame-budget'] || '30',
      mediaStyle: args['media-style'] || 'live_retro_grain',
      creativityLevel: args['creative-freedom'] || 'balanced',
      keepProject,
    },
    steps: [],
    passed: false,
  };

  const step = (name, status, details = {}) => {
    report.steps.push({ name, status, timestamp: new Date().toISOString(), ...details });
  };

  if (!existsSync(distIndex)) {
    throw new Error(`Build output missing: ${distIndex}. Run npm run build:web first.`);
  }
  if (!args.seed || !existsSync(args.seed)) {
    throw new Error(`--seed is required and must exist. Received: ${args.seed || '(missing)'}`);
  }

  registerIpc(runtime);

  const projectName = `Smoke ${Date.now()}`;
  let createdProjectId = null;

  const win = new BrowserWindow({
    width: 1440,
    height: 960,
    show: false,
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  });
  const rendererConsole = [];
  win.webContents.on('console-message', (details) => {
    rendererConsole.push({
      level: details.level,
      message: details.message,
      line: details.lineNumber,
      sourceId: details.sourceId,
    });
  });

  try {
    await win.webContents.session.clearStorageData();
    await win.loadFile(distIndex);
    step('load_app', 'ok');

    await waitFor(() => queryByTestId(win, 'home-start-creating'), 10000);
    step('home_ready', 'ok');

    await clickByTestId(win, 'home-start-creating');
    await waitFor(() => queryByTestId(win, 'create-project-modal'), 5000);
    step('open_create_modal', 'ok');

    await typeByTestId(win, 'create-project-name', projectName);
    await typeByTestId(win, 'create-project-description', 'Automated UI smoke test');
    await clickByTestId(win, 'create-project-submit');
    step('submit_create_project', 'ok');

    await waitFor(() => queryByTestId(win, 'onboarding-wizard'), 15000);
    step('create_project_ui_flow', 'ok');

    const backendStateAfterCreate = await runtime.getBackendState();
    if (!backendStateAfterCreate.currentProjectId) {
      throw new Error('Desktop runtime did not report a current project after create.');
    }
    createdProjectId = backendStateAfterCreate.currentProjectId;
    step('backend_started_for_project', 'ok', { projectId: createdProjectId, apiBaseUrl: backendStateAfterCreate.apiBaseUrl });

    await typeByTestId(win, 'onboarding-idea', 'Smoke harness concept text for onboarding submit.');
    await injectFile(win, 'onboarding-file-input', args.seed);
    await clickByTestId(win, 'onboarding-media-style-toggle');
    await waitFor(() => queryByTestId(win, `media-style-${args['media-style'] || 'live_retro_grain'}`), 3000);
    await clickByTestId(win, `media-style-${args['media-style'] || 'live_retro_grain'}`);
    await typeByTestId(win, 'onboarding-frame-budget', args['frame-budget'] || '30');
    step('fill_onboarding_step_1', 'ok');

    await clickByTestId(win, 'onboarding-next');
    await waitFor(async () => {
      const body = await win.webContents.executeJavaScript('document.body.textContent', true);
      return String(body || '').includes('How much creative freedom?');
    }, 5000);
    step('advance_to_step_2', 'ok');

    await clickByTestId(win, 'onboarding-submit');
    const submitResult = await waitFor(async () => {
      if (await queryByTestId(win, 'onboarding-submit-error')) {
        return { type: 'error', text: await textByTestId(win, 'onboarding-submit-error') };
      }
      if (await queryByTestId(win, 'project-workspace')) {
        return { type: 'workspace' };
      }
      return null;
    }, 30000);

    if (submitResult.type === 'error') {
      throw new Error(`Onboarding submit failed: ${submitResult.text}`);
    }
    step('submit_onboarding', 'ok');

    const backendState = await runtime.getBackendState();
    const current = await requestJson(`${backendState.apiBaseUrl}/api/project/current`);
    const workspace = await requestJson(`${backendState.apiBaseUrl}/api/projects/${createdProjectId}/workspace`);
    const workersStarted = await waitFor(async () => {
      const workersResponse = await requestJson(`${backendState.apiBaseUrl}/api/projects/${createdProjectId}/workers`);
      const workers = Array.isArray(workersResponse.body) ? workersResponse.body : [];
      return workers.length > 0 ? workers : null;
    }, 5000);
    await new Promise((resolve) => setTimeout(resolve, 2000));
    const workersSettledResponse = await requestJson(`${backendState.apiBaseUrl}/api/projects/${createdProjectId}/workers`);
    const workersSettled = Array.isArray(workersSettledResponse.body) ? workersSettledResponse.body : [];
    const failedWorkers = workersSettled.filter((worker) => String(worker?.status || '').toLowerCase() === 'error');
    if (failedWorkers.length > 0) {
      throw new Error(`Pipeline worker failed after onboarding: ${failedWorkers.map((worker) => `${worker.name} (${worker.message})`).join(', ')}`);
    }
    step('verify_backend', 'ok', {
      backendProjectId: current.body?.projectId,
      workspaceHasProject: Boolean(workspace.body?.project?.id),
      workerCount: workersStarted.length,
      workerStatuses: workersSettled.map((worker) => ({
        id: worker.id,
        status: worker.status,
        message: worker.message,
      })),
    });

    report.passed = true;
    await capture(win, 'last-success.png');
  } catch (error) {
    const screenshot = await capture(win, 'last-failure.png').catch(() => null);
    step('failure', 'error', {
      message: error instanceof Error ? error.message : String(error),
      screenshot,
      rendererConsole: rendererConsole.slice(-20),
    });
    report.error = error instanceof Error ? error.message : String(error);
    report.passed = false;
  } finally {
    await mkdir(smokeDir, { recursive: true });
    await writeFile(path.join(smokeDir, 'last-run.json'), JSON.stringify(report, null, 2));
    await runtime.stopBackend();
    try {
      if (createdProjectId && !keepProject) {
        await rm(path.join(runtime.projectsRoot, createdProjectId), { recursive: true, force: true });
      }
    } catch {
      // keep project on cleanup failure
    }
    if (!win.isDestroyed()) {
      win.destroy();
    }
    app.exit(report.passed ? 0 : 1);
  }
}

app.whenReady().then(() => {
  void main();
});
