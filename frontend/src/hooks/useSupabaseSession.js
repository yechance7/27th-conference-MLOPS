import { useEffect, useState } from 'react';
import { supabase } from '../supabaseClient';

export default function useSupabaseSession() {
  const [session, setSession] = useState(null);
  const [token, setToken] = useState(null);

  useEffect(() => {
    let mounted = true;

    const sync = async () => {
      const { data } = await supabase.auth.getSession();
      if (!mounted) return;
      setSession(data.session);
      setToken(data.session?.access_token || null);
    };

    const { data: listener } = supabase.auth.onAuthStateChange((_event, newSession) => {
      if (!mounted) return;
      setSession(newSession);
      setToken(newSession?.access_token || null);
    });

    sync();

    return () => {
      mounted = false;
      listener.subscription.unsubscribe();
    };
  }, []);

  return { session, token };
}
