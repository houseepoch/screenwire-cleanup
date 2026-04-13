import { useState } from 'react';
import { useWindowSize } from '../hooks/useWindowSize';
import { X, ChevronRight, ChevronLeft, FileText, Users, LayoutGrid, Image, Film, Sparkles } from 'lucide-react';

interface HowItWorksModalProps {
  isOpen: boolean;
  onClose: () => void;
}

const steps = [
  {
    id: 1,
    title: 'Upload Your Story',
    description: 'Upload scripts, treatments, or source materials.',
    icon: FileText,
  },
  {
    id: 2,
    title: 'AI Agent Analyzes',
    description: 'Extracts characters, locations, scenes, and narrative.',
    icon: Sparkles,
  },
  {
    id: 3,
    title: 'Generate Cast & Locations',
    description: 'Visual references created for every element.',
    icon: Users,
  },
  {
    id: 4,
    title: 'Create Storyboard',
    description: 'Shot-by-shot visual plan generated.',
    icon: LayoutGrid,
  },
  {
    id: 5,
    title: 'Generate Frames',
    description: 'Storyboards become full-quality frames.',
    icon: Image,
  },
  {
    id: 6,
    title: 'Build Timeline & Export',
    description: 'Arrange, edit, and export your video.',
    icon: Film,
  }
];

export function HowItWorksModal({ isOpen, onClose }: HowItWorksModalProps) {
  const [currentStep, setCurrentStep] = useState(0);
  const { height, isMobile } = useWindowSize();
  
  // Calculate max height based on viewport
  const maxModalHeight = Math.min(height * 0.85, 500);

  if (!isOpen) return null;

  const step = steps[currentStep];
  const Icon = step.icon;

  const handleNext = () => {
    if (currentStep < steps.length - 1) {
      setCurrentStep(currentStep + 1);
    }
  };

  const handlePrev = () => {
    if (currentStep > 0) {
      setCurrentStep(currentStep - 1);
    }
  };

  const handleClose = () => {
    setCurrentStep(0);
    onClose();
  };

  return (
    <div
      className="modal-overlay"
      data-testid="how-it-works-modal"
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          handleClose();
        }
      }}
    >
      <div
        className="how-modal"
        style={{
          maxWidth: isMobile ? '100%' : '420px',
          maxHeight: maxModalHeight,
          margin: isMobile ? 'auto 0 0 0' : 'auto',
        }}
      >
        <div className="how-modal-header" style={{ padding: isMobile ? '16px' : '20px' }}>
          <div className="how-modal-header-copy">
            <div className="how-modal-icon">
              <Icon size={18} style={{ color: 'var(--accent)' }} />
            </div>
            <div>
              <h3>How it works</h3>
              <p>
                Step {currentStep + 1} of {steps.length}
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={handleClose}
            className="how-modal-close"
            data-testid="how-it-works-close"
          >
            <X size={20} />
          </button>
        </div>

        <div className="how-modal-progress">
          <div
            className="how-modal-progress-fill"
            style={{
              width: `${((currentStep + 1) / steps.length) * 100}%`,
            }}
          />
        </div>

        <div className="how-modal-content" style={{ padding: isMobile ? '20px 16px' : '24px 20px' }}>
          <div>
            <h4 className="how-modal-step-title">
              {step.title}
            </h4>
            <p className="how-modal-step-description">
              {step.description}
            </p>
          </div>

          <div className="how-modal-steps">
            {steps.map((s, i) => {
              const StepIcon = s.icon;
              return (
                <div
                  key={i}
                  className={`how-modal-step-chip ${
                    i === currentStep ? 'is-current' : i < currentStep ? 'is-complete' : ''
                  }`}
                  style={{ opacity: i > currentStep + 1 ? 0.4 : 1 }}
                >
                  <StepIcon
                    size={16}
                    style={{ color: i === currentStep || i < currentStep ? 'var(--bg-primary)' : 'var(--text-muted)' }}
                  />
                </div>
              );
            })}
          </div>
        </div>

        <div className="how-modal-footer" style={{ padding: isMobile ? '12px 16px 20px' : '16px 20px 20px' }}>
          <button
            type="button"
            onClick={handlePrev}
            disabled={currentStep === 0}
            className="how-modal-nav-btn is-secondary"
            data-testid="how-it-works-back"
          >
            <ChevronLeft size={14} />
            Back
          </button>

          <div className="how-modal-dots">
            {steps.map((_, i) => (
              <button
                key={i}
                type="button"
                onClick={() => setCurrentStep(i)}
                className={`how-modal-dot ${i === currentStep ? 'is-current' : i < currentStep ? 'is-complete' : ''}`}
              />
            ))}
          </div>

          <button
            type="button"
            onClick={handleNext}
            disabled={currentStep === steps.length - 1}
            className="how-modal-nav-btn is-primary"
            data-testid="how-it-works-next"
          >
            {currentStep === steps.length - 1 ? 'Done' : 'Next'}
            <ChevronRight size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
