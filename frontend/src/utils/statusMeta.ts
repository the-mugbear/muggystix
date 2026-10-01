export type ChipColor = 'default' | 'primary' | 'success' | 'warning' | 'error' | 'info' | 'secondary';

const STATUS_LABELS: Record<string, string> = {
  in_progress: 'In Progress',
  not_reviewed: 'Not Reviewed',
};

export const formatStatusLabel = (value: string | null | undefined, fallback = 'Unknown') => {
  if (!value) {
    return fallback;
  }

  return STATUS_LABELS[value] ?? value.replace(/_/g, ' ');
};

export const getProjectStatusChipColor = (status: string | null | undefined): ChipColor => {
  switch (status) {
    case 'active':
      return 'success';
    case 'in_progress':
      return 'warning';
    case 'completed':
      return 'info';
    case 'archived':
      return 'default';
    default:
      return 'default';
  }
};
