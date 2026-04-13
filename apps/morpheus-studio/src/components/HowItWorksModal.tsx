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
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          handleClose();
        }
      }}
    >
      <div 
        style={{
          background: 'var(--bg-secondary)',
          border: '1px solid var(--border-subtle)',
          borderRadius: '16px',
          width: '100%',
          maxWidth: isMobile ? '100%' : '420px',
          maxHeight: maxModalHeight,
          height: 'auto',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          margin: isMobile ? 'auto 0 0 0' : 'auto',
        }}
      >
        {/* Header */}
        <div style={{ 
          padding: isMobile ? '16px' : '20px',
          borderBottom: '1px solid var(--border-subtle)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
            <div style={{ 
              width: '36px', 
              height: '36px', 
              borderRadius: '50%', 
              background: 'var(--accent-dim)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center'
            }}>
              <Icon size={18} style={{ color: 'var(--accent)' }} />
            </div>
            <div>
              <h3 style={{ fontSize: '16px', fontWeight: 600 }}>How it works</h3>
              <p style={{ fontSize: '11px', color: 'var(--text-muted)' }}>
                Step {currentStep + 1} of {steps.length}
              </p>
            </div>
          </div>
          <button 
            onClick={handleClose}
            style={{
              background: 'none',
              border: 'none',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              padding: '6px',
            }}
          >
            <X size={20} />
          </button>
        </div>

        {/* Progress Bar */}
        <div style={{ 
          height: '3px', 
          background: 'var(--bg-tertiary)',
          flexShrink: 0,
        }}>
          <div 
            style={{ 
              height: '100%', 
              background: 'var(--accent)',
              width: `${((currentStep + 1) / steps.length) * 100}%`,
              transition: 'width 0.3s ease'
            }} 
          />
        </div>

        {/* Content - Compact */}
        <div style={{ 
          padding: isMobile ? '20px 16px' : '24px 20px',
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'center',
          gap: '16px',
          overflow: 'hidden',
        }}>
          <div>
            <h4 style={{ fontSize: '18px', fontWeight: 600, marginBottom: '8px' }}>
              {step.title}
            </h4>
            <p style={{ fontSize: '14px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
              {step.description}
            </p>
          </div>

          {/* Simple Visual Indicator */}
          <div style={{ 
            display: 'flex',
            justifyContent: 'center',
            gap: '8px',
            padding: '16px',
            background: 'var(--bg-primary)',
            borderRadius: '12px',
          }}>
            {steps.map((s, i) => {
              const StepIcon = s.icon;
              return (
                <div
                  key={i}
                  style={{
                    width: '36px',
                    height: '36px',
                    borderRadius: '8px',
                    background: i === currentStep ? 'var(--accent)' : i < currentStep ? 'var(--success)' : 'var(--bg-tertiary)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    opacity: i > currentStep + 1 ? 0.4 : 1,
                  }}
                >
                  <StepIcon size={16} style={{ color: i === currentStep || i < currentStep ? 'var(--bg-primary)' : 'var(--text-muted)' }} />
                </div>
              );
            })}
          </div>
        </div>

        {/* Footer */}
        <div style={{ 
          padding: isMobile ? '12px 16px 20px' : '16px 20px 20px',
          borderTop: '1px solid var(--border-subtle)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexShrink: 0,
        }}>
          <button 
            onClick={handlePrev}
            disabled={currentStep === 0}
            style={{
              padding: '8px 14px',
              background: 'transparent',
              border: '1px solid var(--border-subtle)',
              borderRadius: '6px',
              color: currentStep === 0 ? 'var(--text-muted)' : 'var(--text-primary)',
              cursor: currentStep === 0 ? 'not-allowed' : 'pointer',
              fontSize: '13px',
              display: 'flex',
              alignItems: 'center',
              gap: '4px'
            }}
          >
            <ChevronLeft size={14} />
            Back
          </button>

          {/* Step Dots */}
          <div style={{ display: 'flex', gap: '6px' }}>
            {steps.map((_, i) => (
              <button
                key={i}
                onClick={() => setCurrentStep(i)}
                style={{
                  width: '8px',
                  height: '8px',
                  borderRadius: '50%',
                  background: i === currentStep ? 'var(--accent)' : i < currentStep ? 'var(--success)' : 'var(--bg-tertiary)',
                  border: 'none',
                  cursor: 'pointer',
                }}
              />
            ))}
          </div>

          <button 
            onClick={handleNext}
            disabled={currentStep === steps.length - 1}
            style={{
              padding: '8px 14px',
              background: currentStep === steps.length - 1 ? 'var(--bg-tertiary)' : 'var(--accent)',
              border: 'none',
              borderRadius: '6px',
              color: currentStep === steps.length - 1 ? 'var(--text-muted)' : 'var(--bg-primary)',
              cursor: currentStep === steps.length - 1 ? 'not-allowed' : 'pointer',
              fontSize: '13px',
              fontWeight: 500,
              display: 'flex',
              alignItems: 'center',
              gap: '4px'
            }}
          >
            {currentStep === steps.length - 1 ? 'Done' : 'Next'}
            <ChevronRight size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}
