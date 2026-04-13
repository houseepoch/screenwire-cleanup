import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, readdir, readFile, writeFile } from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

export class DesktopRuntime {
  constructor({ repoRoot, appRoot, pythonBin = process.env.SW_PYTHON_BIN || 'python3', preferredBackendPort = Number(process.env.SW_ELECTRON_PORT || '8000') }) {
    this.repoRoot = repoRoot;
    this.appRoot = appRoot;
    this.projectsRoot = path.join(repoRoot, 'projects');
    this.createProjectScript = path.join(repoRoot, 'create_project.py');
    this.serverScript = path.join(repoRoot, 'server.py');
    this.pythonBin = pythonBin;
    this.backendPort = preferredBackendPort;
    this.backendBaseUrl = `http://127.0.0.1:${this.backendPort}`;
    this.backendProc = null;
    this.currentProjectId = null;
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
      // eslint-disable-next-line no-await-in-loop
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
      const phaseSummary = this.summarizePhaseState(manifest);
      const coverPath = path.join(projectDir, 'reports', 'project_cover.png');

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
          const matchesRequestedProject =
            !projectId ||
            manifestProjectId === projectId ||
            manifestProjectId.endsWith(`_${projectId}`) ||
            String(payload?.slug || '') === String(projectId).replace(/_/g, '-');
          if (matchesRequestedProject) {
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
    this.backendProc.kill('SIGTERM');
    await new Promise((resolve) => {
      this.backendProc.once('exit', resolve);
      setTimeout(() => {
        if (this.backendProc) {
          this.backendProc.kill('SIGKILL');
        }
        resolve();
      }, 5000);
    });
    this.backendProc = null;
  }

  async startBackend(projectId) {
    await this.stopBackend();

    const projectDir = path.join(this.projectsRoot, projectId);
    if (!existsSync(projectDir)) {
      throw new Error(`Project not found: ${projectId}`);
    }

    this.setBackendPort(await this.findAvailablePort(Number(process.env.SW_ELECTRON_PORT || '8000')));

    this.backendProc = spawn(this.pythonBin, [this.serverScript], {
      cwd: this.repoRoot,
      env: {
        ...process.env,
        PROJECT_DIR: projectDir,
        SW_PORT: String(this.backendPort),
      },
      stdio: 'pipe',
    });

    this.backendProc.stdout?.on('data', (chunk) => {
      process.stdout.write(`[screenwire-backend] ${chunk}`);
    });
    this.backendProc.stderr?.on('data', (chunk) => {
      process.stderr.write(`[screenwire-backend] ${chunk}`);
    });
    this.backendProc.on('exit', (code, signal) => {
      console.log(`[screenwire-backend] exited code=${code} signal=${signal}`);
      this.backendProc = null;
    });

    await this.waitForBackend(projectId);
    this.currentProjectId = projectId;
    return {
      projectId,
      projectDir,
      apiBaseUrl: this.backendBaseUrl,
    };
  }

  async createProject(payload) {
    const projectId = this.slugifyProjectId(payload.name);
    await mkdir(this.projectsRoot, { recursive: true });

    const args = [
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
      const proc = spawn(this.pythonBin, args, {
        cwd: this.repoRoot,
        stdio: 'pipe',
        env: process.env,
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
