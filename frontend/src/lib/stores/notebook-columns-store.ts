import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface NotebookColumnsState {
  sourcesCollapsed: boolean
  notesCollapsed: boolean
  chatMaximized: boolean
  toggleSources: () => void
  toggleNotes: () => void
  toggleChatMaximized: () => void
  setSources: (collapsed: boolean) => void
  setNotes: (collapsed: boolean) => void
  setChatMaximized: (maximized: boolean) => void
}

export const useNotebookColumnsStore = create<NotebookColumnsState>()(
  persist(
    (set) => ({
      sourcesCollapsed: false,
      notesCollapsed: false,
      chatMaximized: false,
      toggleSources: () => set((state) => ({ sourcesCollapsed: !state.sourcesCollapsed })),
      toggleNotes: () => set((state) => ({ notesCollapsed: !state.notesCollapsed })),
      toggleChatMaximized: () => set((state) => ({ chatMaximized: !state.chatMaximized })),
      setSources: (collapsed) => set({ sourcesCollapsed: collapsed }),
      setNotes: (collapsed) => set({ notesCollapsed: collapsed }),
      setChatMaximized: (maximized) => set({ chatMaximized: maximized }),
    }),
    {
      name: 'notebook-columns-storage',
    }
  )
)
