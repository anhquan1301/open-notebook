import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { SourceContentViewer } from './SourceContentViewer'

// useTranslation is mocked globally in setup.ts

describe('SourceContentViewer', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn(),
      },
    })
  })

  it('renders no content message when content is empty', () => {
    render(<SourceContentViewer content="" />)
    expect(screen.getByText('sources.noContent')).toBeInTheDocument()
  })

  it('renders standard markdown content in formatted mode', () => {
    const markdown = '# Heading\n\nThis is normal markdown text.'
    render(<SourceContentViewer content={markdown} />)
    expect(screen.getByText('This is normal markdown text.')).toBeInTheDocument()
    expect(screen.getByText('Định dạng')).toBeInTheDocument()
    expect(screen.getByText('Text thô')).toBeInTheDocument()
  })

  it('detects multiple sheets and allows sheet switching', () => {
    const multiSheetContent = `
# Sheet: Sheet1
This is content for sheet 1.

# Sheet: Sheet2
This is content for sheet 2.
    `.trim()

    render(<SourceContentViewer content={multiSheetContent} />)

    expect(screen.getByText(/Sheet \(1\/2\):/)).toBeInTheDocument()
    expect(screen.getByText('This is content for sheet 1.')).toBeInTheDocument()
  })

  it('switches to raw text view mode when clicked', () => {
    const content = '# Raw Markdown\nSome content'
    render(<SourceContentViewer content={content} />)

    const rawButton = screen.getByText('Text thô')
    fireEvent.click(rawButton)

    expect(screen.getByText(/Chế độ Text thô:/)).toBeInTheDocument()
  })

  it('shows oversized warning and truncated preview for large documents', () => {
    const largeContent = 'A'.repeat(30000)
    render(<SourceContentViewer content={largeContent} />)

    expect(screen.getByText(/Nội dung lớn/)).toBeInTheDocument()
    expect(screen.getByText(/Đang hiển thị bản xem trước/)).toBeInTheDocument()
  })

  it('renders download button when filePath and onDownload are provided', () => {
    const onDownload = vi.fn()
    render(
      <SourceContentViewer
        content="Some content"
        filePath="uploads/test.xlsx"
        onDownload={onDownload}
      />
    )

    const downloadButton = screen.getByTitle('Tải file gốc')
    expect(downloadButton).toBeInTheDocument()
    fireEvent.click(downloadButton)
    expect(onDownload).toHaveBeenCalledTimes(1)
  })

  it('renders image view toggle button when filePath is an image', () => {
    render(
      <SourceContentViewer
        content="# Extracted Image Text"
        filePath="uploads/receipt.png"
      />
    )

    expect(screen.getByText('Ảnh gốc')).toBeInTheDocument()
    expect(screen.getByText('Định dạng')).toBeInTheDocument()
    expect(screen.getByText('Text thô')).toBeInTheDocument()
  })

  it('renders citation highlight banner and triggers onClearHighlight when closed', () => {
    const onClearHighlight = vi.fn()
    const content = 'This is an important claim in the document. And other details.'
    render(
      <SourceContentViewer
        content={content}
        highlightText="important claim"
        onClearHighlight={onClearHighlight}
      />
    )

    expect(screen.getByText('sources.viewingCitation')).toBeInTheDocument()
    expect(screen.getByText('sources.jumpToCitation')).toBeInTheDocument()

    const clearBtn = screen.getByTitle('sources.clearHighlight')
    expect(clearBtn).toBeInTheDocument()
    fireEvent.click(clearBtn)
    expect(onClearHighlight).toHaveBeenCalledTimes(1)
  })

  it('matches paraphrased query with technical keywords via keyword cluster matching', () => {
    const content = `
### 1. Overview
Introduction text.

### 2. File Transfer
path: smapi-hulft-storage/send/SMALINK/BF026/test.csv
Transfer via HULF to SMALINK.
`
    // Vietnamese query with codes/terms from the document
    const query = 'HULF là phương thức truyền file giữa SMAPI và SMALINK theo tài liệu BF026'
    render(
      <SourceContentViewer
        content={content}
        highlightText={query}
      />
    )

    // Should find the match and show jumpToCitation instead of exactMatchNotFound
    expect(screen.getByText('sources.viewingCitation')).toBeInTheDocument()
    expect(screen.getByText('sources.jumpToCitation')).toBeInTheDocument()
    expect(screen.queryByText('sources.exactMatchNotFound')).not.toBeInTheDocument()
  })

  it('automatically switches to the sheet containing the citation match', () => {
    const multiSheetContent = `
# Sheet: Cover
Cover page with title and date.

# Sheet: Processing
Details about HULF and SMALINK file transfer for BF026.
`.trim()

    render(
      <SourceContentViewer
        content={multiSheetContent}
        highlightText="Cơ chế HULF và SMALINK trong BF026"
      />
    )

    // Should switch to sheet 2 (Processing)
    expect(screen.getByText(/Sheet \(2\/2\):/)).toBeInTheDocument()
    expect(screen.getByText('sources.jumpToCitation')).toBeInTheDocument()
  })
})

