import { Navigate } from 'react-router';

import { useJurisdictionPin } from '@/lib/jurisdiction-pin';
import { resolveCountry } from '@/lib/jurisdiction-path';

export default function Home() {
  const { jurisdictionPin, jurisdictions } = useJurisdictionPin();
  const country = resolveCountry(jurisdictionPin, jurisdictions);

  return <Navigate replace to={`/jurisdictions/${country}/laws`} />;
}
