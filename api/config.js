export const config = { runtime: 'edge' };

// GET /api/config
// Exposes only the PUBLIC Supabase config the browser needs to run auth:
// the project URL and the anon (publishable) key. The anon key is safe to
// ship to the client — on its own it only grants what Row Level Security
// allows, and our policy lets a signed-in user read just their own quotes.
//
// Required env (Vercel → Project → Settings → Environment Variables):
//   SUPABASE_URL
//   SUPABASE_ANON_KEY    Settings → API → Project API keys → anon / public

export default function handler() {
  const url = process.env.SUPABASE_URL || '';
  const anonKey = process.env.SUPABASE_ANON_KEY || '';
  return new Response(
    JSON.stringify({ configured: Boolean(url && anonKey), url, anonKey }),
    {
      status: 200,
      headers: {
        'content-type': 'application/json',
        'cache-control': 'public, max-age=300',
      },
    }
  );
}
