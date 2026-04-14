import { useEffect, useMemo, useState } from 'react';
import { Download, Film, Play, X } from 'lucide-react';
import { backendConfig } from '../services/api';
import { useMorpheusStore } from '../store';

type ExportSourceOption = {
  id: string;
  label: string;
  title: string;
  description: string;
  extension: string;
  url: string;
  defaultFilename: string;
};

function resolveAssetUrl(url: string): string {
  if (/^(?:https?:)?\/\//i.test(url) || url.startsWith('blob:') || url.startsWith('data:')) {
    return url;
  }
  if (url.startsWith('/')) {
    return `${backendConfig.apiBaseUrl}${url}`;
  }
  return url;
}

function inferExtension(url: string): string {
  try {
    const pathname = new URL(resolveAssetUrl(url), backendConfig.apiBaseUrl).pathname;
    const filename = pathname.split('/').pop() || '';
    const ext = filename.includes('.') ? filename.split('.').pop() : '';
    return ext ? ext.toLowerCase() : 'mp4';
  } catch {
    return 'mp4';
  }
}

function sanitizeFilename(value: string): string {
  const sanitized = value
    .trim()
    .replace(/[\\/:*?"<>|]+/g, '-')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '');
  return sanitized || 'morpheus-export';
}

export function VideoExportWizard() {
  const {
    currentProject,
    timelineFrames,
    selectedFrameId,
    reports,
    isExportWizardOpen,
    setIsExportWizardOpen,
  } = useMorpheusStore();
  const [selectedSourceId, setSelectedSourceId] = useState<string | null>(null);
  const [filename, setFilename] = useState('');
  const [isDownloading, setIsDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);

  const selectedFrame = selectedFrameId
    ? timelineFrames.find((frame) => frame.id === selectedFrameId) ?? null
    : null;
  const previewClip = (selectedFrame?.videoUrl ? selectedFrame : null) ?? timelineFrames.find((frame) => frame.videoUrl) ?? null;

  const exportSources = useMemo<ExportSourceOption[]>(() => {
    const options: ExportSourceOption[] = [];

    if (reports.finalExport) {
      const extension = inferExtension(reports.finalExport);
      options.push({
        id: 'final-export',
        label: 'Final export',
        title: currentProject?.name || 'Final export',
        description: 'Full rendered program output from the current timeline.',
        extension,
        url: reports.finalExport,
        defaultFilename: `${sanitizeFilename(currentProject?.name || 'morpheus')}-final-export.${extension}`,
      });
    }

    if (previewClip?.videoUrl) {
      const extension = inferExtension(previewClip.videoUrl);
      options.push({
        id: `clip-${previewClip.id}`,
        label: selectedFrame?.id === previewClip.id ? 'Selected clip' : 'Preview clip',
        title: `Frame ${previewClip.sequence}`,
        description: 'Single timeline clip exactly as shown in the preview panel.',
        extension,
        url: previewClip.videoUrl,
        defaultFilename: `${sanitizeFilename(currentProject?.name || 'morpheus')}-frame-${previewClip.sequence}.${extension}`,
      });
    }

    return options;
  }, [currentProject?.name, previewClip, reports.finalExport, selectedFrame?.id]);

  const selectedSource =
    exportSources.find((source) => source.id === selectedSourceId) ??
    exportSources[0] ??
    null;

  useEffect(() => {
    if (!isExportWizardOpen) {
      return;
    }
    const initial = exportSources[0] ?? null;
    setSelectedSourceId(initial?.id ?? null);
    setFilename(initial?.defaultFilename ?? '');
    setDownloadError(null);
    setIsDownloading(false);
  }, [exportSources, isExportWizardOpen]);

  useEffect(() => {
    if (!isExportWizardOpen) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !isDownloading) {
        setIsExportWizardOpen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isDownloading, isExportWizardOpen, setIsExportWizardOpen]);

  if (!isExportWizardOpen || !currentProject) {
    return null;
  }

  const handleSourceSelect = (source: ExportSourceOption) => {
    setSelectedSourceId(source.id);
    setFilename(source.defaultFilename);
    setDownloadError(null);
  };

  const handleDownload = async () => {
    if (!selectedSource || isDownloading) {
      return;
    }

    setDownloadError(null);
    setIsDownloading(true);
    try {
      const response = await fetch(resolveAssetUrl(selectedSource.url));
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      const cleanFilename = sanitizeFilename(filename.replace(/\.[^.]+$/, ''));
      const finalFilename = `${cleanFilename}.${selectedSource.extension}`;
      anchor.href = objectUrl;
      anchor.download = finalFilename;
      anchor.rel = 'noopener';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
      setIsExportWizardOpen(false);
    } catch (error) {
      console.error('Failed to download export:', error);
      setDownloadError(error instanceof Error ? error.message : 'Failed to download export.');
    } finally {
      setIsDownloading(false);
    }
  };

  return (
    <div
      className="modal-overlay"
      data-testid="video-export-wizard"
      onClick={(event) => {
        if (event.target === event.currentTarget && !isDownloading) {
          setIsExportWizardOpen(false);
        }
      }}
    >
      <div className="modal-content export-wizard" role="dialog" aria-modal="true" aria-labelledby="video-export-wizard-title">
        <div className="modal-header export-wizard-header">
          <div className="export-wizard-copy">
            <span className="export-wizard-kicker">Export Wizard</span>
            <h3 id="video-export-wizard-title" className="modal-title">Export video</h3>
            <p className="modal-subtitle">
              Download a full render or the current clip without leaving the app.
            </p>
          </div>
          <button
            type="button"
            className="export-wizard-close"
            aria-label="Close export wizard"
            onClick={() => setIsExportWizardOpen(false)}
            disabled={isDownloading}
          >
            <X size={16} />
          </button>
        </div>

        {exportSources.length > 0 ? (
          <>
            <div className="export-wizard-section">
              <span className="export-wizard-step-label">1. Choose source</span>
              <div className="export-wizard-source-grid">
                {exportSources.map((source) => {
                  const isActive = selectedSource?.id === source.id;
                  return (
                    <button
                      key={source.id}
                      type="button"
                      className={`export-source-card ${isActive ? 'is-active' : ''}`.trim()}
                      onClick={() => handleSourceSelect(source)}
                    >
                      <div className="export-source-card-head">
                        <span className="export-source-card-icon">
                          {source.id === 'final-export' ? <Film size={16} /> : <Play size={14} fill="currentColor" />}
                        </span>
                        <span className="export-source-card-badge">{source.extension.toUpperCase()}</span>
                      </div>
                      <strong>{source.label}</strong>
                      <span className="export-source-card-title">{source.title}</span>
                      <span className="export-source-card-description">{source.description}</span>
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="export-wizard-section">
              <span className="export-wizard-step-label">2. Name the file</span>
              <label className="modal-form-group" htmlFor="video-export-filename">
                <span className="export-wizard-field-label">Filename</span>
                <input
                  id="video-export-filename"
                  className="export-wizard-input"
                  type="text"
                  value={filename}
                  onChange={(event) => setFilename(event.target.value)}
                  placeholder={selectedSource?.defaultFilename || 'morpheus-export.mp4'}
                  spellCheck={false}
                />
              </label>
              <div className="export-wizard-summary">
                <span className="export-wizard-summary-chip">Download only</span>
                <span className="export-wizard-summary-chip">No re-render</span>
                {selectedSource ? (
                  <span className="export-wizard-summary-chip">.{selectedSource.extension}</span>
                ) : null}
              </div>
            </div>

            {downloadError ? (
              <div className="export-wizard-error" aria-live="polite">
                {downloadError}
              </div>
            ) : null}

            <div className="modal-actions export-wizard-actions">
              <button
                type="button"
                className="btn-secondary"
                onClick={() => setIsExportWizardOpen(false)}
                disabled={isDownloading}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn-accent export-wizard-download"
                onClick={() => {
                  void handleDownload();
                }}
                disabled={!selectedSource || isDownloading}
              >
                <Download size={16} />
                {isDownloading ? 'Preparing download...' : 'Download export'}
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="export-wizard-empty">
              <Film size={20} />
              <p>No exportable video is available yet.</p>
            </div>
            <div className="modal-actions export-wizard-actions">
              <button type="button" className="btn-accent export-wizard-download" onClick={() => setIsExportWizardOpen(false)}>
                Close
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
