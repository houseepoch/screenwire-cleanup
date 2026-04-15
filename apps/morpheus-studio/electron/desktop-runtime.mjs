import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, readdir, readFile, writeFile } from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const REQUIRED_PYTHON_MODULES = ['fastapi', 'uvicorn', 'watchdog', 'tenacity', 'dotenv', 'httpx', 'aiofiles', 'pydantic', 'openai', 'PIL', 'multipart'];

function prependToPath(prefix, currentValue = '') {
  return currentValue ? `${prefix}${path.delimiter}${currentValue}` : prefix;
}

function runCommand(bin, args, options = {}) {
  return new Promise((resolve) => {
    let stdout = '';
    let stderr = '';
    const child = spawn(bin, args, {
      ...options,
      stdio: ['ignore', 'pipe', 'pipe'],
      windowsHide: options.windowsHide ?? true,
    });

    child.stdout?.on('data', (chunk) => {
      stdout += chunk.toString();
    });
    child.stderr?.on('data', (chunk) => {
      stderr += chunk.toString();
    });
    child.once('error', (error) => {
      resolve({ ok: false, stdout, stderr, error });
    });
    child.once('close', (code) => {
      resolve({ ok: code === 0, code, stdout, stderr });
    });
  });
}

export class DesktopRuntime {
  constructor({ backendRoot, appRoot, projectsRoot, isPackaged = false, pythonBin = process.env.SW_PYTHON_BIN || '', pythonArgs = process.env.SW_PYTHON_ARGS || '', preferredBackendPort = Number(process.env.SW_ELECTRON_PORT || '8000') }) {
    this.backendRoot = backendRoot;
    this.appRoot = appRoot;
    this.projectsRoot = projectsRoot;
    this.isPackaged = isPackaged;
    this.templateRoot = path.join(backendRoot, 'projects', '_template');
    this.createProjectScript = path.join(backendRoot, 'create_project.py');
    this.projectCoverScript = path.join(backendRoot, 'generate_project_cover.py');
    this.serverScript = path.join(backendRoot, 'server.py');
    this.requirementsFile = path.join(backendRoot, 'requirements.txt');
    this.configuredPythonCommand = pythonBin
      ? {
          bin: pythonBin,
          args: pythonArgs.split(/\s+/).filter(Boolean),
        }
      : null;
    this.pythonCommand = null;
    this.backendPort = preferredBackendPort;
    this.backendBaseUrl = `http://127.0.0.1:${this.backendPort}`;
    this.backendProc = null;
    this.currentProjectId = null;
    this.pendingProjectCoverJobs = new Map();
    this.projectCoverRetryAt = new Map();
    this.projectCoverCooldownMs = 15 * 60 * 1000;
  }

  killBackendProcess(proc, signal) {
    if (!proc) {
      return;
    }
    if (process.platform !== 'win32' && typeof proc.pid === 'number') {
      try {
        process.kill(-proc.pid, signal);
        return;
      } catch {
        // Fall through to direct child kill if the process group is gone.
      }
    }
    proc.kill(signal);
  }

  buildPythonEnv(extraEnv = {}) {
    return {
      ...process.env,
      PYTHONUNBUFFERED: '1',
      SCREENWIRE_APP_ROOT: this.backendRoot,
      SCREENWIRE_PROJECTS_ROOT: this.projectsRoot,
      SCREENWIRE_TEMPLATE_ROOT: this.templateRoot,
      PYTHONPATH: prependToPath(this.backendRoot, process.env.PYTHONPATH || ''),
      ...extraEnv,
    };
  }

  getPythonCandidates() {
    if (this.configuredPythonCommand) {
      return [this.configuredPythonCommand];
    }
    if (process.platform === 'win32') {
      return [
        { bin: 'py', args: ['-3'] },
        { bin: 'python', args: [] },
        { bin: 'python3', args: [] },
      ];
    }
    return [
      { bin: 'python3', args: [] },
      { bin: 'python', args: [] },
    ];
  }

  formatPythonInstallHint() {
    if (!existsSync(this.requirementsFile)) {
      return 'Install Python 3.11+ and ensure the ScreenWire backend requirements are available.';
    }
    return `Install Python 3.11+ and install backend dependencies with: python -m pip install -r "${this.requirementsFile}"`;
  }

  async resolvePythonCommand() {
    if (this.pythonCommand) {
      return this.pythonCommand;
    }

    const checkScript = `${REQUIRED_PYTHON_MODULES.map((name) => `import ${name}`).join('; ')}; print("screenwire-python-ok")`;
    let lastFailure = '';
    for (const candidate of this.getPythonCandidates()) {
      const result = await runCommand(candidate.bin, [...candidate.args, '-c', checkScript], {
        cwd: this.backendRoot,
        env: this.buildPythonEnv(),
      });
      if (result.ok) {
        this.pythonCommand = candidate;
        return candidate;
      }
      lastFailure = result.stderr?.trim() || result.error?.message || result.stdout?.trim() || `Unable to launch ${candidate.bin}`;
    }

    throw new Error(`Python runtime unavailable. ${this.formatPythonInstallHint()}${lastFailure ? `\n\nLast failure: ${lastFailure}` : ''}`);
  }

  async collectRuntimeIssues() {
    const issues = [];
    if (!existsSync(this.serverScript) || !existsSync(this.createProjectScript) || !existsSync(this.templateRoot)) {
      issues.push(`Packaged backend resources are incomplete under ${this.backendRoot}. Reinstall the app.`);
    }
    try {
      await this.resolvePythonCommand();
    } catch (error) {
      issues.push(error instanceof Error ? error.message : String(error));
    }
    const ffmpegCheck = await runCommand('ffmpeg', ['-version'], {
      cwd: this.backendRoot,
      env: process.env,
    });
    if (!ffmpegCheck.ok) {
      issues.push('ffmpeg is not available on PATH. Install ffmpeg before generating exports.');
    }
    return issues;
  }

  setBackendPort(port) {
    this.backendPort = Number(port);
    this.backendBaseUrl = `http://127.0.0.1:${this.backendPort}`;
  }

  async isPortFree(port) {
    return new Promise((resolve) => {
      const server = net.createServer();
      server.once('error', () => resolve(false));
      server.once('listening', () => server.close(() => resolve(true)));
      server.listen(port, '127.0.0.1');
    });
  }

  async findAvailablePort(preferredPort, attempts = 20) {
    for (let port = preferredPort; port < preferredPort + attempts; port += 1) {
      if (await this.isPortFree(port)) {
        return port;
      }
    }
    throw new Error(`No free backend port found near ${preferredPort}`);
  }

  slugifyProjectId(name) {
    return `${name.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '')}_${Date.now()}`;
  }

  summarizePhaseState(manifest) {
    const phases = manifest?.phases || {};
    const phaseKeys = Object.keys(phases).sort((a, b) => Number(a.split('_')[1]) - Number(b.split('_')[1]));
    let completed = 0;
    let current = 'draft';

    for (const key of phaseKeys) {
      const phase = phases[key] || {};
      if (phase.status === 'complete') {
        completed += 1;
        continue;
      }
      current = phase.status || 'pending';
      break;
    }

    const progress = phaseKeys.length ? Math.round((completed / phaseKeys.length) * 100) : 0;
    if (completed >= phaseKeys.length && phaseKeys.length > 0) {
      return { status: 'complete', progress: 100 };
    }
    if (current === 'ready' || current === 'pending' || current === 'not_started') {
      return { status: 'draft', progress };
    }
    return { status: current, progress };
  }

  countRegistryItems(graph, key) {
    const registry = graph?.[key];
    return registry && typeof registry === 'object' ? Object.keys(registry).length : 0;
  }

  countCompletedReferenceAssets(projectDir, manifest, graph) {
    const castItems = manifest?.cast || [];
    const locationItems = manifest?.locations || [];
    const propItems = manifest?.props || [];

    const castReady = castItems.filter((item) => item?.compositePath || item?.compositeStatus === 'complete').length
      || this.countRegistryItems(graph, 'cast');
    const locationReady = locationItems.filter((item) => item?.primaryImagePath || item?.imagePath || item?.imageStatus === 'complete').length
      || this.countRegistryItems(graph, 'locations');
    const propReady = propItems.filter((item) => item?.imagePath || item?.imageStatus === 'complete').length
      || this.countRegistryItems(graph, 'props');

    return castReady + locationReady + propReady;
  }

  expectedReferenceEntityCount(manifest, graph) {
    const castExpected = Array.isArray(manifest?.cast) && manifest.cast.length ? manifest.cast.length : this.countRegistryItems(graph, 'cast');
    const locationExpected = Array.isArray(manifest?.locations) && manifest.locations.length ? manifest.locations.length : this.countRegistryItems(graph, 'locations');
    const propExpected = Array.isArray(manifest?.props) && manifest.props.length ? manifest.props.length : this.countRegistryItems(graph, 'props');
    return castExpected + locationExpected + propExpected;
  }

  async deriveWorkflowStatus({ manifest, projectDir, graph, workspaceState }) {
    const phases = manifest?.phases || {};
    let completed = 0;
    const total = 7;
    for (let idx = 0; idx < total; idx += 1) {
      if ((phases[`phase_${idx}`] || {}).status === 'complete') {
        completed += 1;
      }
    }

    const approvals = workspaceState?.approvals || {};
    const creativeOutput = path.join(projectDir, 'creative_output', 'creative_output.md');
    const skeleton = path.join(projectDir, 'creative_output', 'outline_skeleton.md');
    const composedDir = path.join(projectDir, 'frames', 'composed');
    const clipsDir = path.join(projectDir, 'video', 'clips');
    const clipFiles = existsSync(clipsDir) ? await readdir(clipsDir).catch(() => []) : [];
    const composedFiles = existsSync(composedDir) ? await readdir(composedDir).catch(() => []) : [];
    const hasClips = clipFiles.some((name) => name.endsWith('.mp4'));
    const hasComposedFrames = composedFiles.some((name) => /_gen\./.test(name));
    const hasSkeleton = existsSync(skeleton) || existsSync(creativeOutput);
    const referenceExpected = this.expectedReferenceEntityCount(manifest, graph);
    const referenceReady = this.countCompletedReferenceAssets(projectDir, manifest, graph);
    const hasReferenceAssets = referenceExpected > 0 && referenceReady >= referenceExpected;

    if (hasClips) {
      return { status: 'complete', progress: Math.max(92, Math.round((completed / total) * 100)) };
    }
    if (approvals.timelineApprovedAt) {
      return { status: 'generating_video', progress: Math.max(82, Math.round((completed / total) * 100)) };
    }
    if (hasComposedFrames) {
      return { status: 'timeline_review', progress: Math.max(70, Math.round((completed / total) * 100)) };
    }
    if (approvals.referencesApprovedAt) {
      return { status: 'generating_frames', progress: Math.max(58, Math.round((completed / total) * 100)) };
    }
    if (hasReferenceAssets) {
      return { status: 'reference_review', progress: Math.max(45, Math.round((completed / total) * 100)) };
    }
    if (approvals.skeletonApprovedAt || hasSkeleton) {
      return { status: 'generating_assets', progress: Math.max(28, Math.round((completed / total) * 100)) };
    }
    return { status: 'onboarding', progress: Math.max(0, Math.round((completed / total) * 100)) };
  }

  mapCreativeFreedom(onboarding) {
    const level = onboarding?.creativeFreedom;
    return level === 'strict' || level === 'balanced' || level === 'creative' || level === 'unbounded'
      ? level
      : 'balanced';
  }

  async readJsonIfExists(filePath, fallback = null) {
    if (!existsSync(filePath)) {
      return fallback;
    }
    const raw = await readFile(filePath, 'utf8');
    return JSON.parse(raw);
  }

  shouldGenerateProjectCover(projectDir, phaseSummary) {
    if (!phaseSummary || phaseSummary.status === 'draft' || phaseSummary.status === 'onboarding') {
      return false;
    }
    return (
      existsSync(path.join(projectDir, 'creative_output', 'outline_skeleton.md'))
      || existsSync(path.join(projectDir, 'creative_output', 'creative_output.md'))
      || existsSync(path.join(projectDir, 'graph', 'narrative_graph.json'))
      || existsSync(path.join(projectDir, 'video', 'clips'))
    );
  }

  ensureProjectCover(projectId, projectDir, phaseSummary) {
    const coverPath = path.join(projectDir, 'reports', 'project_cover.png');
    const now = Date.now();
    const retryAt = this.projectCoverRetryAt.get(projectId) || 0;
    if (
      existsSync(coverPath)
      || !existsSync(this.projectCoverScript)
      || this.pendingProjectCoverJobs.has(projectId)
      || retryAt > now
      || !this.shouldGenerateProjectCover(projectDir, phaseSummary)
    ) {
      return;
    }

    try {
      this.projectCoverRetryAt.set(projectId, now + this.projectCoverCooldownMs);
      void this.resolvePythonCommand()
        .then((pythonCommand) => {
          const job = spawn(pythonCommand.bin, [...pythonCommand.args, this.projectCoverScript, '--project', projectId], {
            cwd: this.backendRoot,
            env: this.buildPythonEnv(),
            stdio: 'ignore',
            windowsHide: true,
          });
          this.pendingProjectCoverJobs.set(projectId, job);
          const cleanup = () => {
            this.pendingProjectCoverJobs.delete(projectId);
          };
          job.once('close', cleanup);
          job.once('error', cleanup);
        })
        .catch((error) => {
          console.error(`Failed to queue project cover generation for ${projectId}:`, error);
        });
    } catch (error) {
      console.error(`Failed to queue project cover generation for ${projectId}:`, error);
    }
  }

  async listProjects() {
    if (!existsSync(this.projectsRoot)) {
      return [];
    }

    const entries = await readdir(this.projectsRoot, { withFileTypes: true });
    const projects = [];

    for (const entry of entries) {
      if (!entry.isDirectory() || entry.name === '_template') {
        continue;
      }

      const projectDir = path.join(this.projectsRoot, entry.name);
      const manifest = await this.readJsonIfExists(path.join(projectDir, 'project_manifest.json'), {});
      const onboarding = await this.readJsonIfExists(path.join(projectDir, 'source_files', 'onboarding_config.json'), {});
      const coverMeta = await this.readJsonIfExists(path.join(projectDir, 'reports', 'project_cover_meta.json'), {});
      const graph = await this.readJsonIfExists(path.join(projectDir, 'graph', 'narrative_graph.json'), {});
      const workspaceState = await this.readJsonIfExists(path.join(projectDir, 'logs', 'ui_workspace_state.json'), {});
      const phaseSummary = await this.deriveWorkflowStatus({ manifest, projectDir, graph, workspaceState });
      const coverPath = path.join(projectDir, 'reports', 'project_cover.png');
      this.ensureProjectCover(entry.name, projectDir, phaseSummary);

      projects.push({
        id: entry.name,
        name: manifest?.projectName || onboarding?.projectName || entry.name,
        description: onboarding?.extraDetails || '',
        status: phaseSummary.status,
        createdAt: manifest?.phases?.phase_0?.completedAt || new Date().toISOString(),
        updatedAt: manifest?.updatedAt || manifest?.phases?.phase_0?.completedAt || new Date().toISOString(),
        creativityLevel: this.mapCreativeFreedom(onboarding),
        generationMode: 'assisted',
        progress: phaseSummary.progress,
        projectDir,
        coverImageUrl: existsSync(coverPath) ? pathToFileURL(coverPath).toString() : null,
        coverSummary: typeof coverMeta?.summary === 'string' ? coverMeta.summary : null,
      });
    }

    projects.sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());
    return projects;
  }

  async waitForBackend(projectId, timeoutMs = 15000) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      if (this.backendProc && this.backendProc.exitCode !== null) {
        throw new Error(`Backend exited early with code ${this.backendProc.exitCode}`);
      }
      try {
        const response = await fetch(`${this.backendBaseUrl}/api/project/current`);
        if (response.ok) {
          const payload = await response.json();
          const manifestProjectId = String(payload?.projectId || '');
          const hasIdentity = Boolean(manifestProjectId || payload?.slug);
          const matchesRequestedProject =
            !projectId ||
            manifestProjectId === projectId ||
            manifestProjectId.endsWith(`_${projectId}`) ||
            String(payload?.slug || '') === String(projectId).replace(/_/g, '-');
          if (matchesRequestedProject || !hasIdentity) {
            return true;
          }
        }
      } catch {
        // backend still booting
      }
      await new Promise((resolve) => setTimeout(resolve, 400));
    }
    throw new Error(`Backend did not become healthy within ${timeoutMs}ms`);
  }

  async stopBackend() {
    if (!this.backendProc) {
      return;
    }

    const proc = this.backendProc;
    this.killBackendProcess(proc, 'SIGTERM');

    await new Promise((resolve) => {
      proc.once('exit', () => {
        if (this.backendProc === proc) {
          this.backendProc = null;
        }
        resolve();
      });
      setTimeout(() => {
        if (proc.exitCode === null && !proc.killed) {
          this.killBackendProcess(proc, 'SIGKILL');
        }
        resolve();
      }, 5000);
    });

    if (this.backendProc === proc) {
      this.backendProc = null;
    }
  }

  async startBackend(projectId) {
    await this.stopBackend();

    const projectDir = path.join(this.projectsRoot, projectId);
    if (!existsSync(projectDir)) {
      throw new Error(`Project not found: ${projectId}`);
    }

    const pythonCommand = await this.resolvePythonCommand();
    this.setBackendPort(await this.findAvailablePort(Number(process.env.SW_ELECTRON_PORT || '8000')));

    const useReloadingBackend = !this.isPackaged && process.env.SW_DISABLE_BACKEND_RELOAD !== '1';
    const backendArgs = useReloadingBackend
      ? [
          ...pythonCommand.args,
          '-m',
          'uvicorn',
          'server:app',
          '--host',
          '127.0.0.1',
          '--port',
          String(this.backendPort),
          '--reload',
        ]
      : [...pythonCommand.args, this.serverScript];

    this.backendProc = spawn(pythonCommand.bin, backendArgs, {
      cwd: this.backendRoot,
      env: this.buildPythonEnv({
        PROJECT_DIR: projectDir,
        SW_PORT: String(this.backendPort),
      }),
      stdio: 'pipe',
      detached: process.platform !== 'win32',
    });

    const procRef = this.backendProc;

    this.backendProc.stdout?.on('data', (chunk) => {
      process.stdout.write(`[screenwire-backend] ${chunk}`);
    });
    this.backendProc.stderr?.on('data', (chunk) => {
      process.stderr.write(`[screenwire-backend] ${chunk}`);
    });
    this.backendProc.on('exit', (code, signal) => {
      console.log(`[screenwire-backend] exited code=${code} signal=${signal}`);
      if (this.backendProc === procRef) {
        this.backendProc = null;
      }
    });

    await this.waitForBackend(projectId);
    this.currentProjectId = projectId;
    return {
      projectId,
      projectDir,
      apiBaseUrl: this.backendBaseUrl,
    };
  }

  async returnToProjects() {
    this.currentProjectId = null;
    await this.stopBackend();
    return {
      currentProjectId: null,
      apiBaseUrl: this.backendBaseUrl,
      running: false,
    };
  }

  async createProject(payload) {
    const projectId = this.slugifyProjectId(payload.name);
    await mkdir(this.projectsRoot, { recursive: true });
    const pythonCommand = await this.resolvePythonCommand();

    const args = [
      ...pythonCommand.args,
      this.createProjectScript,
      '--name',
      payload.name,
      '--id',
      projectId,
      '--creative-freedom',
      payload.creativityLevel || 'balanced',
      '--frame-budget',
      payload.frameBudget || 'auto',
      '--media-style',
      payload.mediaStyle || 'live_clear',
    ];
    if (payload.seedFile) {
      args.push('--seed', payload.seedFile);
    }

    await new Promise((resolve, reject) => {
      const proc = spawn(pythonCommand.bin, args, {
        cwd: this.backendRoot,
        stdio: 'pipe',
        env: this.buildPythonEnv(),
      });
      let stderr = '';
      proc.stderr.on('data', (chunk) => {
        stderr += chunk.toString();
      });
      proc.on('exit', async (code) => {
        if (code !== 0) {
          reject(new Error(stderr || `create_project.py failed with exit code ${code}`));
          return;
        }
        try {
          if (payload.description?.trim()) {
            const pitchPath = path.join(this.projectsRoot, projectId, 'source_files', 'pitch.md');
            await writeFile(pitchPath, `${payload.description.trim()}\n`, 'utf8');
          }
          resolve();
        } catch (error) {
          reject(error);
        }
      });
    });

    const projects = await this.listProjects();
    return projects.find((project) => project.id === projectId);
  }

  async getBackendState() {
    return {
      currentProjectId: this.currentProjectId,
      apiBaseUrl: this.backendBaseUrl,
      running: Boolean(this.backendProc),
    };
  }
}
