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
