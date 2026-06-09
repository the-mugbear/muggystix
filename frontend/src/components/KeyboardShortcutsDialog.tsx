/**
 * Keyboard shortcuts cheat-sheet — audit FRX·H5.
 *
 * Opened by the `?` global shortcut (registered in Layout via
 * useKeyboardShortcuts).  Read-only — the shortcuts themselves are
 * defined where they're wired up; this dialog just documents them.
 */
import React from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { commandModifierLabel } from '../utils/platform';

interface ShortcutRow {
  keys: string[];
  label: string;
}

// v4.7.5 — modifier key is platform-aware (⌘ on Mac, Ctrl elsewhere)
// so the dialog matches the topbar's existing platform-aware pill.
// Pre-fix Mac users saw "⌘K" on the palette pill but "Ctrl+K" in
// the help dialog — the docs lied about the working shortcut.
const buildShortcuts = (): ShortcutRow[] => [
  { keys: ['?'], label: 'Show this shortcut list' },
  { keys: ['/'], label: 'Focus the page search (where available)' },
  { keys: [commandModifierLabel(), 'K'], label: 'Open command palette' },
  { keys: ['g', 'h'], label: 'Go to Hosts' },
  { keys: ['g', 'p'], label: 'Go to Test Plans' },
  { keys: ['g', 's'], label: 'Go to Scans' },
  { keys: ['g', 'i'], label: 'Go to Inventory hub' },
  { keys: ['g', 'o'], label: 'Go to Operations hub' },
  {
    keys: ['j', 'k'],
    label: 'Hosts: move the row cursor (↑/↓ too); steps prev/next host when the inspector is open',
  },
  { keys: ['Enter'], label: 'Hosts: open the cursor row in the inspector' },
];

export interface KeyboardShortcutsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const Kbd: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <kbd className="rounded border border-border bg-muted px-xxs py-px font-mono text-micro text-foreground">
    {children}
  </kbd>
);

export const KeyboardShortcutsDialog: React.FC<KeyboardShortcutsDialogProps> = ({
  open,
  onOpenChange,
}) => {
  // Compute once per mount — platform doesn't change at runtime.
  const shortcuts = React.useMemo(buildShortcuts, []);
  return (
  <Dialog open={open} onOpenChange={onOpenChange}>
    <DialogContent className="sm:max-w-md">
      <DialogHeader>
        <DialogTitle>Keyboard shortcuts</DialogTitle>
        <DialogDescription>
          Press the keys outside any text field. Two-step combos (e.g. <Kbd>g</Kbd>{' '}
          <Kbd>h</Kbd>) require the second key within 1.5 seconds.
        </DialogDescription>
      </DialogHeader>
      <table className="w-full text-metadata">
        <tbody className="divide-y divide-border">
          {shortcuts.map((row) => (
            <tr key={row.keys.join('+')}>
              <td className="py-xs pr-md align-top">
                <span className="inline-flex items-center gap-xxs">
                  {row.keys.map((k, i) => (
                    <React.Fragment key={`${k}-${i}`}>
                      {i > 0 && <span className="text-muted-foreground">then</span>}
                      <Kbd>{k}</Kbd>
                    </React.Fragment>
                  ))}
                </span>
              </td>
              <td className="py-xs text-foreground">{row.label}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </DialogContent>
  </Dialog>
  );
};

export default KeyboardShortcutsDialog;
