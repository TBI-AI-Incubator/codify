
import { useQuery } from '@tanstack/react-query';
import { useTranslation } from 'react-i18next';

import { getLaw } from '@/lib/api-laws';
import { preferredExpressionLanguage } from '@/lib/expression-language';

export function useTranslatedLawTitle(lawId: string, enabled = true): string | null {
  const { i18n } = useTranslation();
  const wanted = preferredExpressionLanguage(i18n.language);
  const { data } = useQuery({
    queryKey: ['law', lawId],
    queryFn: () => getLaw(lawId),
    staleTime: 5 * 60 * 1000,
    enabled: enabled && wanted !== null,
  });
  if (!wanted || !data) return null;
  return null; // the server carries one title per law
}
