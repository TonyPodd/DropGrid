import { useEffect, useRef, useState } from "react";
import bridge from "@vkontakte/vk-bridge";

const SCOPES = ["wall", "photos", "groups"] as const;
const TIMEOUT_MS = 25_000;
const OPEN_IN_VK = "Open this page through the VK Mini App to request a token.";
const AUTH_TIMEOUT = "VK authorization did not finish. Try again.";
class BridgeTimeout extends Error {}

async function bounded<T>(operation: () => Promise<T>): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      operation(),
      new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new BridgeTimeout()), TIMEOUT_MS);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

function safeAuthError(error: unknown): string {
  if (error instanceof BridgeTimeout) return AUTH_TIMEOUT;
  if (error && typeof error === "object") {
    const data = error as {
      error_type?: unknown;
      error_data?: { error_code?: unknown };
    };
    if (data.error_type === "client_error" && data.error_data?.error_code === 4)
      return "VK authorization was cancelled by the user.";
    if (data.error_type === "client_error" && data.error_data?.error_code === 1)
      return OPEN_IN_VK;
    if (data.error_type === "auth_error" || data.error_type === "api_error")
      return "VK authorization was rejected. Check the Mini App permissions and try again.";
  }
  return "VK authorization failed. Try again.";
}

export default function VkAuthHelper() {
  const appId = Number(import.meta.env.VITE_VK_APP_ID);
  const configured = Number.isSafeInteger(appId) && appId > 0;
  const [ready, setReady] = useState(false);
  const [initializing, setInitializing] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [copyStatus, setCopyStatus] = useState("");
  const [result, setResult] = useState<{
    token: string;
    scopes: string[];
  } | null>(null);
  // Only operation identifiers live in refs; credentials stay in React state.
  const generation = useRef(0);

  useEffect(() => {
    let active = true;
    const initialize = async () => {
      try {
        // Still initialize on standalone pages, but never authorize outside VK.
        const embedded = bridge.isEmbedded();
        if (!embedded) {
          setError(OPEN_IN_VK);
          setInitializing(false);
        }
        await bounded(() => bridge.send("VKWebAppInit"));
        if (active) {
          if (embedded) setReady(true);
          else setError(OPEN_IN_VK);
        }
      } catch {
        if (active) setError(OPEN_IN_VK);
      } finally {
        if (active) setInitializing(false);
      }
    };
    void initialize();
    return () => {
      active = false;
      generation.current += 1;
    };
  }, []);

  async function authorize() {
    const operation = ++generation.current;
    setPending(true);
    setResult(null);
    setCopyStatus("");
    setError("");
    try {
      const response = await bounded(() =>
        bridge.send("VKWebAppGetAuthToken", {
          app_id: appId,
          scope: SCOPES.join(","),
        }),
      );
      if (operation !== generation.current) return;
      if (
        typeof response.access_token !== "string" ||
        !response.access_token ||
        typeof response.scope !== "string"
      ) {
        setError("VK authorization failed. Try again.");
        return;
      }
      const granted = response.scope.split(",").map((scope) => scope.trim());
      setResult({
        token: response.access_token,
        scopes: SCOPES.filter((scope) => granted.includes(scope)),
      });
    } catch (failure: unknown) {
      if (operation === generation.current) setError(safeAuthError(failure));
    } finally {
      if (operation === generation.current) setPending(false);
    }
  }

  async function copyToken() {
    if (!result) return;
    const operation = generation.current;
    try {
      await navigator.clipboard.writeText(result.token);
      if (operation === generation.current) setCopyStatus("Token copied.");
    } catch {
      if (operation === generation.current)
        setCopyStatus(
          "Could not copy token. Allow clipboard access and try again.",
        );
    }
  }

  function clearToken() {
    generation.current += 1;
    setResult(null);
    setPending(false);
    setError("");
    setCopyStatus("");
  }

  return (
    <section className="form-card">
      <h1>VK test token helper</h1>
      <p className="note">
        Development only. Token stays in page memory until cleared or reloaded.
        Copy it manually for backend diagnostics.
      </p>
      {initializing && <p role="status">Initializing VK Bridge…</p>}
      {!configured && (
        <p role="alert">
          Set a valid VITE_VK_APP_ID before starting the frontend.
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      <div className="actions">
        <button
          className="primary"
          disabled={!ready || !configured || pending}
          onClick={() => void authorize()}
        >
          Получить тестовый VK token
        </button>
        {(result || pending) && (
          <button onClick={clearToken}>Clear token</button>
        )}
      </div>
      {pending && <p role="status">Waiting for VK authorization…</p>}
      {result && (
        <>
          <p className="success" role="status">
            ✓ Token received
          </p>
          <p>Granted scopes:</p>
          <ul>
            {result.scopes.map((scope) => (
              <li key={scope}>{scope}</li>
            ))}
          </ul>
          {result.scopes.length === 0 && <p>No requested scopes granted.</p>}
          <button onClick={() => void copyToken()}>Copy token</button>
        </>
      )}
      {copyStatus && <p role="status">{copyStatus}</p>}
    </section>
  );
}
