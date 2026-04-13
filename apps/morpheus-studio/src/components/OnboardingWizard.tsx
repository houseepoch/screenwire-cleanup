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
            name: 'Skeleton Generation',
            status: 'running',
            progress: 0,
            message: job.message || 'Generating skeleton and creative output...',
          },
        ]);
        updateProjectStatus('skeleton_review');
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
      style={{
        minHeight: isMobile ? '100vh' : 'calc(100vh - 64px)',
        padding: isMobile ? '20px 16px' : '32px 24px',
      }}
    >
      <div className="wizard-header" style={{ marginBottom: isMobile ? '20px' : '28px' }}>
        <h1 
          className="wizard-title"
          style={{ fontSize: isMobile ? '24px' : '28px' }}
        >
          {currentProject?.name || 'New Project'}
        </h1>
        <p className="wizard-subtitle" style={{ fontSize: isMobile ? '13px' : '14px' }}>
          Configure your production settings
        </p>
      </div>

      <div 
        className="wizard-content"
        style={{
          maxWidth: '720px',
          margin: '0 auto',
        }}
      >
        {/* Step 1: Text Input + Upload */}
        {step === 1 && (
          <div className="wizard-step">
            <div className="step-label">Step 1 of 2</div>
            <h2 className="step-title" style={{ fontSize: isMobile ? '18px' : '20px' }}>
              Tell us your story
            </h2>
            
            <div style={{ display: 'flex', flexDirection: 'column', gap: isMobile ? '16px' : '20px' }}>
              {/* Text Area with Drag & Drop */}
              <div
                onDragOver={handleDragOver}
                onDragLeave={handleDragLeave}
                onDrop={handleDrop}
                style={{
                  position: 'relative',
                  border: `2px dashed ${isDragging ? 'var(--accent)' : 'var(--border-subtle)'}`,
                  borderRadius: '12px',
                  background: isDragging ? 'var(--accent-dim)' : 'var(--bg-secondary)',
                  transition: 'all 0.2s ease',
                  overflow: 'hidden',
                }}
              >
                <textarea
                  ref={textareaRef}
                  data-testid="onboarding-idea"
                  value={ideaText}
                  onChange={(e) => setIdeaText(e.target.value)}
                  placeholder="Type out your idea or drag and drop your files..."
                  style={{
                    width: '100%',
                    minHeight: isMobile ? '150px' : '200px',
                    padding: '16px',
                    paddingBottom: '60px',
                    background: 'transparent',
                    border: 'none',
                    color: 'var(--text-primary)',
                    fontSize: '14px',
                    lineHeight: 1.6,
                    resize: 'vertical',
                    outline: 'none',
                  }}
                />
                
                {/* Upload Button at bottom of text area */}
                <div style={{
                  position: 'absolute',
                  bottom: '12px',
                  left: '12px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                }}>
                  <button
                    data-testid="onboarding-upload-trigger"
                    onClick={() => document.getElementById('file-input')?.click()}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                      padding: '8px 14px',
                      background: 'var(--bg-primary)',
                      border: '1px solid var(--border-subtle)',
                      borderRadius: '8px',
                      color: 'var(--text-primary)',
                      fontSize: '12px',
                      cursor: 'pointer',
                      transition: 'all 0.2s ease',
                    }}
                    onMouseEnter={(e) => {
                      e.currentTarget.style.borderColor = 'var(--accent)';
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.borderColor = 'var(--border-subtle)';
                    }}
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
                  <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                    PDF, DOCX, MD, TXT, PNG, JPG
                  </span>
                </div>
              </div>

              {/* Uploaded Files List */}
              {uploadedFiles.length > 0 && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  <p style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)' }}>
                    Uploaded files ({uploadedFiles.length})
                  </p>
                  <div style={{ maxHeight: '120px', overflowY: 'auto' }}>
                    {uploadedFiles.map((file, index) => (
                      <div
                        key={index}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: '8px',
                          padding: '8px 10px',
                          background: 'var(--bg-secondary)',
                          borderRadius: '6px',
                          border: '1px solid var(--border-subtle)',
                          marginBottom: '4px',
                        }}
                      >
                        <span style={{ color: 'var(--accent)' }}>
                          {getFileIcon(file.type, file.name)}
                        </span>
                        <span style={{ flex: 1, fontSize: '12px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {file.name}
                        </span>
                        <span style={{ fontSize: '10px', color: 'var(--text-muted)' }}>
                          {formatFileSize(file.size)}
                        </span>
                        <button
                          onClick={() => removeFile(index)}
                          style={{
                            background: 'none',
                            border: 'none',
                            color: 'var(--text-muted)',
                            cursor: 'pointer',
                            padding: '2px',
                          }}
                        >
                          <X size={14} />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Settings Grid */}
              <div style={{ 
                display: 'grid', 
                gridTemplateColumns: isMobile ? '1fr' : '1fr 1fr', 
                gap: isMobile ? '12px' : '16px' 
              }}>
                {/* Media Style Dropdown with Images */}
                <div ref={mediaDropdownRef} style={{ position: 'relative' }}>
                  <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '6px', color: 'var(--text-secondary)' }}>
                    Media Style
                  </label>
                  <button
                    data-testid="onboarding-media-style-toggle"
                    onClick={() => setShowMediaDropdown(!showMediaDropdown)}
                    style={{
                      width: '100%',
                      padding: '10px 12px',
                      background: 'var(--bg-secondary)',
                      border: `1px solid ${showMediaDropdown ? 'var(--accent)' : 'var(--border-subtle)'}`,
                      borderRadius: '8px',
                      color: 'var(--text-primary)',
                      fontSize: '13px',
                      textAlign: 'left',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                    }}
                    >
                    <span style={{ color: 'var(--text-primary)' }}>
                      {MEDIA_STYLES.find(s => s.id === selectedMediaStyle)?.name || 'Select style...'}
                    </span>
                    {showMediaDropdown ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                  </button>
                  
                  {showMediaDropdown && (
                    <div style={{
                      position: 'absolute',
                      top: '100%',
                      left: 0,
                      right: 0,
                      marginTop: '4px',
                      background: 'var(--bg-secondary)',
                      border: '1px solid var(--border-subtle)',
                      borderRadius: '8px',
                      zIndex: 100,
                      maxHeight: '280px',
                      overflowY: 'auto',
                      boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
                    }}>
                      <div style={{ 
                        display: 'grid', 
                        gridTemplateColumns: isMobile ? '1fr 1fr' : '1fr 1fr',
                        gap: '8px',
                        padding: '10px',
                      }}>
                        {MEDIA_STYLES.map((style) => (
                          <button
                            key={style.id}
                            data-testid={`media-style-${style.id}`}
                            onClick={() => {
                              setSelectedMediaStyle(style.id);
                              setShowMediaDropdown(false);
                            }}
                            style={{
                              display: 'flex',
                              flexDirection: 'column',
                              alignItems: 'center',
                              gap: '6px',
                              padding: '10px',
                              background: selectedMediaStyle === style.id ? 'var(--accent-dim)' : 'var(--bg-primary)',
                              border: `2px solid ${selectedMediaStyle === style.id ? 'var(--accent)' : 'transparent'}`,
                              borderRadius: '8px',
                              cursor: 'pointer',
                              transition: 'all 0.2s ease',
                            }}
                          >
                            <div
                              style={{
                                width: '100%',
                                aspectRatio: '16/10',
                                borderRadius: '4px',
                                background: MEDIA_STYLE_PREVIEWS[style.id].gradient,
                                overflow: 'hidden',
                              }}
                            >
                              {style.thumbnailUrl ? (
                                <img
                                  src={style.thumbnailUrl}
                                  alt={style.name}
                                  style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                                  onError={(e) => {
                                    (e.currentTarget as HTMLImageElement).style.display = 'none';
                                  }}
                                />
                              ) : null}
                            </div>
                            <span style={{ 
                              fontSize: '11px', 
                              fontWeight: 500,
                              color: selectedMediaStyle === style.id ? 'var(--accent)' : 'var(--text-primary)'
                            }}>
                              {style.name}
                            </span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {/* Frame Count */}
                <div>
                  <label style={{ display: 'block', fontSize: '12px', fontWeight: 500, marginBottom: '6px', color: 'var(--text-secondary)' }}>
                    Frame Budget
                  </label>
                  <input
                    type="text"
                    className="input-field"
                    data-testid="onboarding-frame-budget"
                    placeholder="auto or e.g. 60"
                    value={frameCount}
                    onChange={(e) => setFrameCount(e.target.value)}
                    style={{ width: '100%', padding: '10px 12px' }}
                  />
                  <p style={{ fontSize: '10px', color: 'var(--text-muted)', marginTop: '6px', lineHeight: 1.4 }}>
                    Use <strong>auto</strong> for uncapped, highest-effort coverage, or enter a positive frame count.
                  </p>
                </div>
              </div>
            </div>
          </div>
        )}

        {submitError && (
          <div
            data-testid="onboarding-submit-error"
            style={{
              marginTop: '16px',
              marginBottom: '8px',
              padding: '10px 12px',
              borderRadius: '12px',
              background: 'rgba(220, 38, 38, 0.12)',
              border: '1px solid rgba(248, 113, 113, 0.35)',
              color: '#fecaca',
              fontSize: '13px',
            }}
          >
            {submitError}
          </div>
        )}

        {/* Step 2: Creativity Level */}
        {step === 2 && (
          <div className="wizard-step">
            <div className="step-label">Step 2 of 2</div>
            <h2 className="step-title" style={{ fontSize: isMobile ? '18px' : '20px' }}>
              How much creative freedom?
            </h2>
            <p style={{ fontSize: isMobile ? '13px' : '14px', color: 'var(--text-secondary)', marginBottom: '20px' }}>
              Choose how closely Morpheus should follow your plan versus exploring creative alternatives.
            </p>
            
            <div style={{ 
              display: 'grid', 
              gridTemplateColumns: creativityGridCols === 1 ? '1fr' : '1fr 1fr',
              gap: isMobile ? '10px' : '12px',
            }}>
              {creativityLevels.map((level) => {
                const Icon = level.icon;
                const isSelected = creativityLevel === level.level;
                
                return (
                  <div
                    key={level.level}
                    onClick={() => setCreativityLevel(level.level)}
                    style={{
                      padding: isMobile ? '14px' : '16px',
                      background: isSelected ? 'var(--accent-dim)' : 'var(--bg-secondary)',
                      border: `2px solid ${isSelected ? 'var(--accent)' : 'var(--border-subtle)'}`,
                      borderRadius: '10px',
                      cursor: 'pointer',
                      transition: 'all 0.2s ease',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '8px' }}>
                      <div style={{ 
                        width: '32px', 
                        height: '32px', 
                        borderRadius: '8px', 
                        background: isSelected ? 'var(--accent)' : 'var(--bg-tertiary)',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        color: isSelected ? 'var(--bg-primary)' : 'var(--text-muted)',
                      }}>
                        <Icon size={16} />
                      </div>
                      <span style={{ fontSize: '14px', fontWeight: 600 }}>{level.name}</span>
                    </div>
                    <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '10px', lineHeight: 1.4 }}>
                      {level.description}
                    </p>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                      {level.freedoms.slice(0, isMobile ? 2 : 3).map((freedom, idx) => (
                        <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: 'var(--text-secondary)' }}>
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

        {/* Wizard Actions */}
        <div 
          style={{ 
            display: 'flex', 
            justifyContent: 'space-between', 
            marginTop: isMobile ? '20px' : '28px',
            paddingTop: isMobile ? '16px' : '20px',
            borderTop: '1px solid var(--border-subtle)',
          }}
        >
          <button 
            className="btn-secondary" 
            data-testid="onboarding-back"
            onClick={handleBack}
            style={{ padding: isMobile ? '10px 16px' : '10px 20px', fontSize: '13px' }}
          >
            {step === 1 ? 'Back to Projects' : 'Back'}
          </button>
          <button 
            className="btn-accent" 
            data-testid={step === 2 ? 'onboarding-submit' : 'onboarding-next'}
            onClick={handleNext}
            disabled={!canProceed() || isSubmitting}
            style={{ padding: isMobile ? '10px 16px' : '10px 20px', fontSize: '13px' }}
          >
            {step === 2 ? (isSubmitting ? 'Starting…' : 'Create Project') : 'Next'}
          </button>
        </div>
      </div>
    </div>
  );
}
