import { useState, useCallback, useRef, useEffect } from 'react';
import { useMorpheusStore } from '../store';
import { useWindowSize } from '../hooks/useWindowSize';
import API from '../services/api';
import { desktopService } from '../services';
import { 
  Lock, 
  Scale, 
  Circle,
  Zap,
  Check,
  Upload,
  FileText,
  Image,
  X,
  ChevronDown,
  ChevronUp
} from 'lucide-react';
import type { CreativityLevel, MediaStyle } from '../types';
import { MEDIA_STYLES } from '../types';

const creativityLevels: { 
  level: CreativityLevel; 
  name: string; 
  description: string;
  icon: React.ElementType;
  freedoms: string[];
  constraints: string[];
}[] = [
  {
    level: 'strict',
    name: 'Strict',
    description: 'Follow the plan exactly. Maximum fidelity to your creative vision.',
    icon: Lock,
    freedoms: [
      'Exact shot composition matching storyboard',
      'Precise color grading per scene',
      'Character consistency enforced',
    ],
    constraints: [
      'No creative deviations from plan',
      'Limited improvisation on set',
      'Strict adherence to script',
    ],
  },
  {
    level: 'balanced',
    name: 'Balanced',
    description: 'Follow the story with room for organic moments.',
    icon: Scale,
    freedoms: [
      'Minor framing adjustments allowed',
      'Natural lighting variations',
      'Subtle character expression freedom',
    ],
    constraints: [
      'Core story beats preserved',
      'Location integrity maintained',
      'Dialogue timing respected',
    ],
  },
  {
    level: 'creative',
    name: 'Creative',
    description: 'Keep the story, but allow creative reframes and interpretations.',
    icon: Zap,
    freedoms: [
      'Alternative shot angles explored',
      'Creative color interpretations',
      'Emphasis on mood over precision',
    ],
    constraints: [
      'Narrative arc preserved',
      'Character motivations intact',
      'Scene transitions maintained',
    ],
  },
  {
    level: 'unbounded',
    name: 'Unbounded',
    description: 'Explore new angles and pacing while staying true to the arc.',
    icon: Circle,
    freedoms: [
      'Full creative interpretation',
      'Experimental compositions',
      'Dynamic pacing adjustments',
      'Artistic license maximized',
    ],
    constraints: [
      'Core story arc honored',
      'Thematic consistency',
    ],
  },
];

const ALLOWED_FILE_TYPES = [
  'application/pdf',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'text/markdown',
  'text/plain',
  'image/png',
  'image/jpeg',
];

const ALLOWED_EXTENSIONS = ['.pdf', '.docx', '.md', '.txt', '.png', '.jpg', '.jpeg'];

interface UploadedFile {
  name: string;
  type: string;
  size: number;
  file?: File;
}

// Media style preview colors (fallback when no thumbnail image is provided)
const MEDIA_STYLE_PREVIEWS: Record<MediaStyle, { gradient: string }> = {
  new_digital_anime: { gradient: 'linear-gradient(135deg, #6b8cff 0%, #9a6cff 100%)' },
  live_retro_grain: { gradient: 'linear-gradient(135deg, #7a5639 0%, #c39a68 100%)' },
  chiaroscuro_live: { gradient: 'linear-gradient(135deg, #1d0b0b 0%, #6f1d1b 45%, #d97706 100%)' },
  chiaroscuro_3d: { gradient: 'linear-gradient(135deg, #120b20 0%, #243b53 50%, #d97706 100%)' },
  chiaroscuro_anime: { gradient: 'linear-gradient(135deg, #1f102e 0%, #6b1f3a 55%, #f59e0b 100%)' },
  black_ink_anime: { gradient: 'linear-gradient(135deg, #050505 0%, #3b3b3b 100%)' },
  live_soft_light: { gradient: 'linear-gradient(135deg, #f5d8c7 0%, #e9bfa3 100%)' },
  live_clear: { gradient: 'linear-gradient(135deg, #d9d9d9 0%, #f7f7f7 40%, #8a8a8a 100%)' },
};

export function OnboardingWizard() {
  const { width, isMobile } = useWindowSize();
  const { 
    currentProject, 
    setCurrentView, 
    creativityLevel, 
    setCreativityLevel,
    setCreativeConcept,
    updateProjectStatus,
    setWorkers,
  } = useMorpheusStore();

  const [step, setStep] = useState(1);
  const [isDragging, setIsDragging] = useState(false);
  const [uploadedFiles, setUploadedFiles] = useState<UploadedFile[]>([]);
  const [ideaText, setIdeaText] = useState('');
  const [selectedMediaStyle, setSelectedMediaStyle] = useState<MediaStyle>('live_clear');
  const [showMediaDropdown, setShowMediaDropdown] = useState(false);
  const [frameCount, setFrameCount] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const mediaDropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdowns when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (mediaDropdownRef.current && !mediaDropdownRef.current.contains(event.target as Node)) {
        setShowMediaDropdown(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    
    const files = Array.from(e.dataTransfer.files);
    const validFiles = files.filter(file => {
      const extension = '.' + file.name.split('.').pop()?.toLowerCase();
      return ALLOWED_FILE_TYPES.includes(file.type) || ALLOWED_EXTENSIONS.includes(extension);
    });

    const newFiles = validFiles.map(file => ({
      name: file.name,
      type: file.type,
      size: file.size,
      file,
    }));

    setUploadedFiles(prev => [...prev, ...newFiles]);
  }, []);

  const handleFileInput = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    const newFiles = files.map(file => ({
      name: file.name,
      type: file.type,
      size: file.size,
      file,
    }));
    setUploadedFiles(prev => [...prev, ...newFiles]);
  }, []);

  const removeFile = (index: number) => {
    setUploadedFiles(prev => prev.filter((_, i) => i !== index));
  };

  const handleNext = async () => {
    setSubmitError(null);
    if (step < 2) {
      setStep(step + 1);
    } else {
      const concept = {
        title: currentProject?.name || '',
        logline: '',
        synopsis: ideaText,
        tone: '',
        genre: '',
      };
      setCreativeConcept(concept);

      if (!currentProject) {
        setCurrentView('project');
        return;
      }

      if (!desktopService.isAvailable()) {
        setSubmitError('Desktop backend is unavailable. Fully restart Morpheus and try again.');
        return;
      }

      setIsSubmitting(true);
      try {
        for (const upload of uploadedFiles) {
          if (upload.file) {
            await API.concept.uploadFile(currentProject.id, upload.file);
          }
        }

        await API.concept.set(currentProject.id, {
          sourceText: ideaText,
          mediaStyle: selectedMediaStyle,
          frameCount: frameCount.trim().toLowerCase() === 'auto' || !frameCount.trim()
            ? 'auto'
            : Math.max(0, Number(frameCount) || 0),
          creativityLevel,
        });

        const job = await API.skeleton.generate(currentProject.id);
        setWorkers([
          {
            id: job.jobId,
            name: 'Preproduction Build',
            status: 'running',
            progress: 5,
            message: job.message || 'Generating script, graph, and review assets...',
          },
        ]);
        updateProjectStatus('generating_assets');
        setCurrentView('project');
      } catch (error) {
        console.error('Failed to submit onboarding:', error);
        setSubmitError(error instanceof Error ? error.message : 'Failed to submit onboarding.');
      } finally {
        setIsSubmitting(false);
      }
    }
  };

  const handleBack = () => {
    if (step > 1) {
      setStep(step - 1);
    } else {
      setCurrentView('home');
    }
  };

  const canProceed = () => {
    switch (step) {
      case 1:
        return ideaText.trim().length > 0 || uploadedFiles.length > 0;
      case 2:
        return true;
      default:
        return false;
    }
  };

  const getFileIcon = (type: string, name: string) => {
    if (type.startsWith('image/') || name.match(/\.(png|jpg|jpeg)$/i)) {
      return <Image size={16} />;
    }
    return <FileText size={16} />;
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  };

  // Responsive grid for creativity cards
  const creativityGridCols = isMobile ? 1 : width < 900 ? 2 : 2;

  return (
    <div
      className="onboarding-wizard"
      data-testid="onboarding-wizard"
    >
      <div className="wizard-header">
        <span className="wizard-overline">Project Setup</span>
        <h1 className="wizard-title">
          {currentProject?.name || 'New Project'}
        </h1>
        <p className="wizard-subtitle">
          Configure your production settings
        </p>
        <div className="wizard-progress">
          {[1, 2].map((stepNumber) => (
            <div
              key={stepNumber}
              className={`wizard-progress-step ${stepNumber <= step ? 'active' : ''}`}
            >
              <span>{stepNumber}</span>
              <div />
            </div>
          ))}
        </div>
      </div>

      <div className="wizard-content">
        <div className="wizard-surface glass-panel">
        {step === 1 && (
          <div className="wizard-step">
            <div className="step-label">Step 1 of 2</div>
            <h2 className="step-title">
              Tell us your story
            </h2>
            
            <div className="wizard-stack">
              <div
                className={`story-intake ${isDragging ? 'is-dragging' : ''}`}
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
              >
                <textarea
                  ref={textareaRef}
                  data-testid="onboarding-idea"
                  value={ideaText}
                  onChange={(e) => setIdeaText(e.target.value)}
                  placeholder="Type out your idea or drag and drop your files..."
                  className="story-intake-input"
                  style={{ minHeight: isMobile ? '150px' : '200px' }}
                />
                
                <div className="story-upload-row">
                  <button
                    type="button"
                    className="story-upload-btn"
                    data-testid="onboarding-upload-trigger"
                    onClick={() => document.getElementById('file-input')?.click()}
                  >
                    <Upload size={14} />
                    Upload files
                  </button>
                  <input
                    id="file-input"
                    data-testid="onboarding-file-input"
                    type="file"
                    multiple
                    accept=".pdf,.docx,.md,.txt,.png,.jpg,.jpeg"
                    style={{ display: 'none' }}
                    onChange={handleFileInput}
                  />
                  <span className="story-upload-caption">
                    PDF, DOCX, MD, TXT, PNG, JPG
                  </span>
                </div>
              </div>

              {uploadedFiles.length > 0 && (
                <div className="upload-list">
                  <p className="wizard-field-label">
                    Uploaded files ({uploadedFiles.length})
                  </p>
                  <div className="upload-list-body">
                    {uploadedFiles.map((file, index) => (
                      <div key={index} className="upload-item">
                        <span className="upload-item-icon">
                          {getFileIcon(file.type, file.name)}
                        </span>
                        <span className="upload-item-name">
                          {file.name}
                        </span>
                        <span className="upload-item-size">
                          {formatFileSize(file.size)}
                        </span>
                        <button
                          type="button"
                          onClick={() => removeFile(index)}
                          className="upload-item-remove"
                        >
                          <X size={14} />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div
                className="wizard-settings-grid"
                style={{ gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr' }}
              >
                <div ref={mediaDropdownRef} className="wizard-field media-style-field">
                  <label className="wizard-field-label">
                    Media Style
                  </label>
                  <button
                    type="button"
                    className={`media-style-trigger ${showMediaDropdown ? 'open' : ''}`}
                    data-testid="onboarding-media-style-toggle"
                    onClick={() => setShowMediaDropdown(!showMediaDropdown)}
                  >
                    <span>
                      {MEDIA_STYLES.find(s => s.id === selectedMediaStyle)?.name || 'Select style...'}
                    </span>
                    {showMediaDropdown ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                  </button>
                  
                  {showMediaDropdown && (
                    <div className="media-style-menu">
                      <div className="media-style-grid" style={{ gridTemplateColumns: isMobile ? '1fr 1fr' : '1fr 1fr' }}>
                        {MEDIA_STYLES.map((style) => (
                          <button
                            key={style.id}
                            type="button"
                            data-testid={`media-style-${style.id}`}
                            onClick={() => {
                              setSelectedMediaStyle(style.id);
                              setShowMediaDropdown(false);
                            }}
                            className={`media-style-option ${selectedMediaStyle === style.id ? 'selected' : ''}`}
                          >
                            <div
                              className="media-style-preview"
                              style={{ background: MEDIA_STYLE_PREVIEWS[style.id].gradient }}
                            >
                              {style.thumbnailUrl ? (
                                <img
                                  src={style.thumbnailUrl}
                                  alt={style.name}
                                  className="media-style-preview-image"
                                  onError={(e) => {
                                    (e.currentTarget as HTMLImageElement).style.display = 'none';
                                  }}
                                />
                              ) : null}
                            </div>
                            <span className="media-style-name">{style.name}</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                <div className="wizard-field">
                  <label className="wizard-field-label">
                    Frame Budget
                  </label>
                  <input
                    type="text"
                    className="input-field"
                    data-testid="onboarding-frame-budget"
                    placeholder="auto or e.g. 60"
                    value={frameCount}
                    onChange={(e) => setFrameCount(e.target.value)}
                  />
                  <p className="wizard-note">
                    Use <strong>auto</strong> for uncapped, highest-effort coverage, or enter a positive frame count.
                  </p>
                </div>
              </div>
            </div>
          </div>
        )}

        {submitError && (
          <div
            className="wizard-error"
            data-testid="onboarding-submit-error"
          >
            {submitError}
          </div>
        )}

        {step === 2 && (
          <div className="wizard-step">
            <div className="step-label">Step 2 of 2</div>
            <h2 className="step-title">
              How much creative freedom?
            </h2>
            <p className="wizard-step-copy">
              Choose how closely Morpheus should follow your plan versus exploring creative alternatives.
            </p>
            
            <div
              className="creativity-grid"
              style={{ gridTemplateColumns: creativityGridCols === 1 ? '1fr' : '1fr 1fr' }}
            >
              {creativityLevels.map((level) => {
                const Icon = level.icon;
                const isSelected = creativityLevel === level.level;
                
                return (
                  <div
                    key={level.level}
                    className={`creativity-card ${isSelected ? 'selected' : ''}`}
                    onClick={() => setCreativityLevel(level.level)}
                  >
                    <div className="creativity-card-header">
                      <div className="creativity-card-icon">
                        <Icon size={16} />
                      </div>
                      <span className="creativity-card-title">{level.name}</span>
                    </div>
                    <p className="creativity-card-description">
                      {level.description}
                    </p>
                    <div className="creativity-card-list">
                      {level.freedoms.slice(0, isMobile ? 2 : 3).map((freedom, idx) => (
                        <div key={idx} className="creativity-card-item">
                          <Check size={12} style={{ color: 'var(--success)', flexShrink: 0 }} />
                          <span>{freedom}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        <div className="wizard-actions">
          <button
            type="button"
            className="btn-secondary" 
            data-testid="onboarding-back"
            onClick={handleBack}
          >
            {step === 1 ? 'Back to Projects' : 'Back'}
          </button>
          <button
            type="button"
            className="btn-accent" 
            data-testid={step === 2 ? 'onboarding-submit' : 'onboarding-next'}
            onClick={handleNext}
            disabled={!canProceed() || isSubmitting}
          >
            {step === 2 ? (isSubmitting ? 'Starting…' : 'Create Project') : 'Next'}
          </button>
        </div>
        </div>
      </div>
    </div>
  );
}
