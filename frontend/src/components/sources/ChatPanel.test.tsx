import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ChatPanel } from './ChatPanel'

// useTranslation is mocked globally in setup.ts (t returns the key string)

vi.mock('@/lib/hooks/use-modal-manager', () => ({
  useModalManager: () => ({ openModal: vi.fn() }),
}))

// Keep the message-content deps light for this composer-focused test.
vi.mock('@/components/sources/MessageActions', () => ({
  MessageActions: () => null,
}))

describe('ChatPanel composer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    // jsdom does not implement scrollIntoView (used by the auto-scroll effect).
    window.HTMLElement.prototype.scrollIntoView = vi.fn()
  })

  const getTextarea = () => screen.getByRole('textbox') as HTMLTextAreaElement

  it('sends the typed message and clears the input on send-button click', () => {
    const onSendMessage = vi.fn()
    render(
      <ChatPanel
        messages={[]}
        isStreaming={false}
        contextIndicators={null}
        onSendMessage={onSendMessage}
      />
    )

    const textarea = getTextarea()
    fireEvent.change(textarea, { target: { value: '  hello world  ' } })

    const sendButton = screen.getByRole('button')
    fireEvent.click(sendButton)

    expect(onSendMessage).toHaveBeenCalledTimes(1)
    expect(onSendMessage).toHaveBeenCalledWith('hello world', undefined)
    expect(textarea.value).toBe('')
  })

  it('sends on Cmd+Enter on macOS', () => {
    const uaSpy = vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue(
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'
    )
    const onSendMessage = vi.fn()
    render(
      <ChatPanel
        messages={[]}
        isStreaming={false}
        contextIndicators={null}
        onSendMessage={onSendMessage}
      />
    )

    const textarea = getTextarea()
    fireEvent.change(textarea, { target: { value: 'via cmd' } })
    fireEvent.keyDown(textarea, { key: 'Enter', metaKey: true, ctrlKey: false })

    expect(onSendMessage).toHaveBeenCalledWith('via cmd', undefined)
    expect(textarea.value).toBe('')
    uaSpy.mockRestore()
  })

  it('sends on Ctrl+Enter on non-macOS', () => {
    const uaSpy = vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue(
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    )
    const onSendMessage = vi.fn()
    render(
      <ChatPanel
        messages={[]}
        isStreaming={false}
        contextIndicators={null}
        onSendMessage={onSendMessage}
      />
    )

    const textarea = getTextarea()
    fireEvent.change(textarea, { target: { value: 'via ctrl' } })
    fireEvent.keyDown(textarea, { key: 'Enter', ctrlKey: true, metaKey: false })

    expect(onSendMessage).toHaveBeenCalledWith('via ctrl', undefined)
    expect(textarea.value).toBe('')
    uaSpy.mockRestore()
  })

  it('does not send while streaming', () => {
    const onSendMessage = vi.fn()
    render(
      <ChatPanel
        messages={[]}
        isStreaming={true}
        contextIndicators={null}
        onSendMessage={onSendMessage}
      />
    )

    const textarea = getTextarea()
    // Textarea is disabled while streaming, but the guard must also hold.
    fireEvent.keyDown(textarea, { key: 'Enter', ctrlKey: true })

    expect(onSendMessage).not.toHaveBeenCalled()
  })

  it('renders stop button while streaming and calls onCancel on click', () => {
    const onCancel = vi.fn()
    render(
      <ChatPanel
        messages={[]}
        isStreaming={true}
        contextIndicators={null}
        onSendMessage={vi.fn()}
        onCancel={onCancel}
      />
    )

    const stopButton = screen.getByRole('button', { name: 'common.cancel' })
    expect(stopButton).toBeInTheDocument()
    fireEvent.click(stopButton)
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('renders thinking process for AI message and toggles open/close', () => {
    render(
      <ChatPanel
        messages={[
          {
            id: 'msg_1',
            type: 'ai',
            content: 'Here is the response',
            thinking: 'Let me reason about this step by step.',
          },
        ]}
        isStreaming={false}
        contextIndicators={null}
        onSendMessage={vi.fn()}
      />
    )

    expect(screen.getByText(/Quá trình suy nghĩ/)).toBeInTheDocument()
    const toggleButton = screen.getByRole('button', { name: /Quá trình suy nghĩ/ })
    fireEvent.click(toggleButton)
    expect(screen.getByText('Let me reason about this step by step.')).toBeInTheDocument()
  })

  it('renders suggested follow-up chips and clicks them to send message', () => {
    const onSendMessage = vi.fn()
    render(
      <ChatPanel
        messages={[
          {
            id: 'msg_1',
            type: 'ai',
            content: 'Here is the answer.',
            follow_ups: ['What are the key benefits?', 'How does it compare to others?'],
          },
        ]}
        isStreaming={false}
        contextIndicators={null}
        onSendMessage={onSendMessage}
      />
    )

    expect(screen.getByText('chat.suggestedFollowUps')).toBeInTheDocument()
    const chip1 = screen.getByText('What are the key benefits?')
    expect(chip1).toBeInTheDocument()
    const chip2 = screen.getByText('How does it compare to others?')
    expect(chip2).toBeInTheDocument()

    fireEvent.click(chip1)
    expect(onSendMessage).toHaveBeenCalledWith('What are the key benefits?')
  })

  it('extracts follow-ups from <followups> tag in content if follow_ups field is empty', () => {
    const onSendMessage = vi.fn()
    render(
      <ChatPanel
        messages={[
          {
            id: 'msg_1',
            type: 'ai',
            content: 'Main text.\n\n<followups>\n- Tell me more\n</followups>',
          },
        ]}
        isStreaming={false}
        contextIndicators={null}
        onSendMessage={onSendMessage}
      />
    )

    expect(screen.queryByText('<followups>')).not.toBeInTheDocument()
    const chip = screen.getByText('Tell me more')
    expect(chip).toBeInTheDocument()
    fireEvent.click(chip)
    expect(onSendMessage).toHaveBeenCalledWith('Tell me more')
  })

  it('renders maximize button when onToggleMaximize is provided and calls callback on click', () => {
    const onToggleMaximize = vi.fn()
    render(
      <ChatPanel
        messages={[]}
        isStreaming={false}
        contextIndicators={null}
        onSendMessage={vi.fn()}
        isMaximized={false}
        onToggleMaximize={onToggleMaximize}
      />
    )

    const maximizeBtn = screen.getByRole('button', { name: 'Maximize chat' })
    expect(maximizeBtn).toBeInTheDocument()
    fireEvent.click(maximizeBtn)
    expect(onToggleMaximize).toHaveBeenCalledTimes(1)
  })

  it('renders restore button when isMaximized is true', () => {
    const onToggleMaximize = vi.fn()
    render(
      <ChatPanel
        messages={[]}
        isStreaming={false}
        contextIndicators={null}
        onSendMessage={vi.fn()}
        isMaximized={true}
        onToggleMaximize={onToggleMaximize}
      />
    )

    const restoreBtn = screen.getByRole('button', { name: 'Restore view' })
    expect(restoreBtn).toBeInTheDocument()
    fireEvent.click(restoreBtn)
    expect(onToggleMaximize).toHaveBeenCalledTimes(1)
  })
})

