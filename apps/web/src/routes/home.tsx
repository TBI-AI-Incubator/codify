import { Navigate } from 'react-router';

import { useAuth } from '@/lib/auth-context';
import { resolveCountry } from '@/lib/jurisdiction-path';

export default function Home() {
  const { jurisdictionPin, jurisdictions } = useAuth();
  const country = resolveCountry(jurisdictionPin, jurisdictions);

  return <Navigate replace to={`/jurisdictions/${country}/laws`} />;
}
