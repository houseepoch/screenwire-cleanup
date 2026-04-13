import { useState, useRef, useEffect } from 'react';
import { useMorpheusStore } from '../store';
import API from '../services/api';
import { Focus, SendHorizontal, Sparkles } from 'lucide-react';

export function AgentChat() {
  const { 
    messages, 
    addMessage, 
    setMessages,
    currentProject,
    hydrateWorkspace,
    focusedItem, 
    setFocusedItem,
    selectedItems,
  } = useMorpheusStore();

  const [inputValue, setInputValue] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [chatMode, setChatMode] = useState<'suggest' | 'apply' | 'regenerate'>('suggest');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  useEffect(() => {
    if (!currentProject) {
      return;
    }
    void API.chat.setFocus(currentProject.id, focusedItem).catch((error) => {
      console.error('Failed to sync chat focus:', error);
    });
  }, [currentProject, focusedItem]);

  const handleSend = async () => {
    if (!inputValue.trim()) return;

    const content = inputValue.trim();
    const focusTarget = focusedItem
      ? {
          type: focusedItem.type as 'storyboard' | 'entity' | 'scene' | 'frame',
          id: focusedItem.id,
          name: focusedItem.name,
        }
      : undefined;
    const focusTargets = selectedItems.length
      ? selectedItems.map((item) => ({
          type: item.type,
          id: item.id,
          name: item.name,
        }))
      : undefined;

    addMessage({
      role: 'user',
      content,
      mode: chatMode,
      focusTarget,
    });

    setInputValue('');
    setFocusedItem(null);

    if (!currentProject) {
      return;
    }

    setIsSending(true);
    try {
      await API.chat.sendMessage(currentProject.id, { content, mode: chatMode, focusTarget, focusTargets });
      const [history, snapshot] = await Promise.all([
        API.chat.getHistory(currentProject.id),
        API.workspace.get(currentProject.id),
      ]);
      setMessages(history);
      hydrateWorkspace(snapshot);
    } catch (error) {
      console.error('Failed to send Morpheus message:', error);
      addMessage({
        role: 'agent',
        content: 'I could not reach the local Morpheus backend. Check that the project server is running and try again.',
      });
    } finally {
      setIsSending(false);
      if (currentProject) {
        try {
          const [history, snapshot] = await Promise.all([
            API.chat.getHistory(currentProject.id),
            API.workspace.get(currentProject.id),
          ]);
          setMessages(history);
          hydrateWorkspace(snapshot);
        } catch {
          // keep optimistic state if history refresh fails
        }
      }
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="agent-panel" data-testid="agent-panel">
      <div className="agent-header">
        <div className="agent-header-icon">
          <Sparkles size={16} />
        </div>
        <div className="agent-header-copy">
          <span className="agent-header-kicker">Agent Console</span>
          <span className="agent-header-title">Morpheus Agent</span>
        </div>
        <div className="agent-header-status">Live</div>
      </div>

      <div className="agent-messages">
        {messages.length === 0 && (
          <div className="agent-empty-state">
            <span className="agent-empty-kicker">Ready for direction</span>
            <h3>Ask for revisions, approvals, or a clean reframe.</h3>
            <p>
              Focus scenes, frames, or entities from the workspace and Morpheus will work inside
              that context.
            </p>
          </div>
        )}
        {messages.map((message) => (
          <div
            key={message.id}
            className={`chat-thread ${message.role === 'user' ? 'chat-thread-user' : 'chat-thread-agent'}`}
          >
            {message.focusTarget && message.role === 'user' && (
              <div className="chat-focus-chip">
                <Focus size={10} />
                Focusing on: {message.focusTarget.name}
              </div>
            )}
            <div className={`chat-bubble ${message.role === 'user' ? 'chat-bubble-user' : 'chat-bubble-agent'}`}>
              <span>{message.content}</span>
            </div>
            <span className="chat-timestamp">
              {new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {focusedItem && (
        <div className="agent-focus-bar">
          <div className="agent-focus-copy">
            <Focus size={10} style={{ color: 'var(--accent)' }} />
            <span>Focused on: <strong>{focusedItem.name}</strong></span>
          </div>
          <button
            type="button"
            onClick={() => setFocusedItem(null)}
            className="agent-focus-clear"
          >
            Clear
          </button>
        </div>
      )}

      <div className="agent-input-container">
        <div className="agent-mode-toggle">
          {(['suggest', 'apply', 'regenerate'] as const).map((mode) => (
            <button
              key={mode}
              type="button"
              onClick={() => setChatMode(mode)}
              className={`agent-mode-pill ${chatMode === mode ? 'active' : ''}`}
            >
              {mode}
            </button>
          ))}
        </div>
        <div className="agent-input-wrapper">
          <textarea
            className="agent-input"
            placeholder="Ask the agent anything..."
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={1}
            disabled={isSending}
          />
          <button
            type="button"
            className="agent-send-btn"
            onClick={handleSend}
            disabled={!inputValue.trim() || isSending}
            aria-label={isSending ? 'Sending message' : 'Send message'}
          >
            {isSending ? '...' : <SendHorizontal size={16} />}
          </button>
        </div>
        <p className="agent-helper-copy">
          Tip: shift-click items to focus Morpheus, then choose suggest, apply, or regenerate.
        </p>
      </div>
    </div>
  );
}
