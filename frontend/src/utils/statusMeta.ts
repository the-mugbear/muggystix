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

export const getNoteStatusChipColor = (status: string | null | undefined): ChipColor => {
  switch (status) {
    case 'open':
      return 'info';
    case 'in_progress':
      return 'warning';
    case 'resolved':
      return 'success';
    default:
      return 'default';
  }
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

export const getTestPlanStatusChipColor = (status: string | null | undefined): ChipColor => {
  switch (status) {
    case 'draft':
      return 'default';
    case 'proposed':
      return 'info';
    case 'approved':
      return 'primary';
    case 'in_progress':
      return 'warning';
    case 'completed':
      return 'success';
    case 'rejected':
      return 'error';
    case 'archived':
      return 'default';
    default:
      return 'default';
  }
};

export const getTestPlanPriorityChipColor = (priority: string | null | undefined): ChipColor => {
  switch (priority) {
    case 'critical':
      return 'error';
    case 'high':
      return 'warning';
    case 'medium':
      return 'info';
    case 'low':
      return 'success';
    case 'info':
      return 'default';
    default:
      return 'default';
  }
};
