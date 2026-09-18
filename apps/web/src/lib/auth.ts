
export function authHeaders(): Record<string, string> {
  const sid = sessionId();
  return sid ? { 'X-Session-Id': sid } : {};
}

const SESSION_KEY = 'codify:session_id';

function sessionId(): string | null {
  try {
    const s = window.sessionStorage;
    let id = s.getItem(SESSION_KEY);
    if (!id) {
      id = crypto.randomUUID();
      s.setItem(SESSION_KEY, id);
    }
    return id;
  } catch {
    return null;
  }
}
