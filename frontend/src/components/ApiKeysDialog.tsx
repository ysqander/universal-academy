import { useId, useState, type FormEvent } from "react";
import { getKeys, saveKeys } from "../api";
import { useToast } from "../context/toast";
import { refreshHealth } from "../hooks/useHealth";
import { useResource } from "../hooks/useResource";
import type { ApiKeyName, ApiKeys, ApiKeyStatus } from "../types";
import { Dialog } from "./Dialog";
import { Icon } from "./Icon";
import { Spinner } from "./Spinner";
import { ErrorState } from "./StateMessage";
import "./ApiKeysDialog.css";

type Provider = ApiKeys["provider"];

const PROVIDERS: { value: Provider; label: string }[] = [
  { value: "anthropic", label: "Anthropic (Claude)" },
  { value: "openai", label: "OpenAI (GPT)" },
];

const KEYS: { name: ApiKeyName; label: string; placeholder: string; url: string; site: string; provider?: Provider; hint?: string }[] = [
  { name: "ANTHROPIC_API_KEY", label: "Anthropic API key", placeholder: "sk-ant-…", url: "https://console.anthropic.com/settings/keys", site: "console.anthropic.com", provider: "anthropic" },
  { name: "OPENAI_API_KEY", label: "OpenAI API key", placeholder: "sk-…", url: "https://platform.openai.com/api-keys", site: "platform.openai.com", provider: "openai" },
  {
    name: "NVIDIA_API_KEY",
    label: "NVIDIA API key (optional)",
    placeholder: "nvapi-…",
    url: "https://build.nvidia.com",
    site: "build.nvidia.com",
    hint: "Nemotron Parse reads the page layout of a chapter; without it the chosen provider's model reads the pages.",
  },
];

function statusText(status: ApiKeyStatus | undefined): string {
  if (!status?.set) return "not set";
  const tail = status.last4 ? ` · …${status.last4}` : "";
  return status.source === "env" ? `from .env${tail}` : `saved${tail}`;
}

interface ApiKeysDialogProps {
  open: boolean;
  onClose: () => void;
}

/** The "API keys" dialog: choose the provider and enter keys, saved on this computer only (never shown again). */
export function ApiKeysDialog({ open, onClose }: ApiKeysDialogProps) {
  const toast = useToast();
  const formId = useId();
  // Loaded each time the dialog opens (the key is null while it is closed).
  const resource = useResource(open ? "api-keys" : null, (signal) => getKeys(signal));
  const data = resource.data;
  const [chosen, setChosen] = useState<Provider | null>(null);
  const [drafts, setDrafts] = useState<Partial<Record<ApiKeyName, string>>>({});
  const [busy, setBusy] = useState<"save" | ApiKeyName | null>(null);

  const provider = chosen ?? data?.provider ?? null;
  const typed = Object.fromEntries(
    Object.entries(drafts)
      .map(([name, value]) => [name, (value ?? "").trim()])
      .filter(([, value]) => value),
  ) as Partial<Record<ApiKeyName, string>>;
  const providerChanged = Boolean(data && provider && provider !== data.provider);
  const canSave = Boolean(data) && (Object.keys(typed).length > 0 || providerChanged) && busy === null;

  const close = () => {
    setDrafts({});
    setChosen(null);
    onClose();
  };

  const save = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!canSave) return;
    setBusy("save");
    try {
      const next = await saveKeys({ keys: typed, provider: providerChanged ? provider : null });
      resource.mutate(next);
      setDrafts({});
      setChosen(null);
      refreshHealth();
      toast.show(next.llm_ready ? "API keys saved — you can build courses now." : "Saved. A key is still missing for this provider.", "success");
      if (next.llm_ready) close();
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(null);
    }
  };

  const remove = async (name: ApiKeyName) => {
    setBusy(name);
    try {
      resource.mutate(await saveKeys({ keys: { [name]: "" } }));
      refreshHealth();
      toast.show("Key removed from this computer.", "success");
    } catch (err) {
      toast.error(err);
    } finally {
      setBusy(null);
    }
  };

  return (
    <Dialog
      open={open}
      onClose={close}
      title="API keys"
      size="md"
      footer={
        <>
          <button type="button" className="btn btn--ghost" onClick={close}>
            {canSave ? "Cancel" : "Close"}
          </button>
          <button type="submit" form={formId} className="btn btn--primary" disabled={!canSave}>
            {busy === "save" && <Spinner size={14} label="Saving" />} Save
          </button>
        </>
      }
    >
      {resource.error && !data ? (
        <ErrorState error={resource.error} title="Couldn't load the key settings" onRetry={() => void resource.reload()} />
      ) : !data ? (
        <div className="api-keys__loading">
          <Spinner label="Loading" />
        </div>
      ) : (
        <form id={formId} className="api-keys" onSubmit={(event) => void save(event)} autoComplete="off">
          {data.agent_route && (
            <div className="callout callout--info">
              <Icon name="info" size={18} />
              <span>
                <strong>Claude Code answers the model calls</strong> (the app was started with <code>UA_AGENT_DIR</code>), so API
                keys are not used. Keys saved here apply when you start the app without it.
              </span>
            </div>
          )}
          {data.fake_llm && !data.agent_route && (
            <div className="callout callout--info">
              <Icon name="info" size={18} />
              <span>
                <strong>Offline demo mode</strong> (<code>UA_FAKE_LLM=1</code>): courses are built from canned content, so no key is
                used.
              </span>
            </div>
          )}

          <div className="field">
            <span className="field__label" id={`${formId}-provider`}>
              Who builds your courses
            </span>
            <div className="segmented" role="group" aria-labelledby={`${formId}-provider`}>
              {PROVIDERS.map((p) => (
                <button key={p.value} type="button" aria-pressed={provider === p.value} onClick={() => setChosen(p.value)}>
                  {p.label}
                </button>
              ))}
            </div>
            <span className="field__hint">Its key is needed to build a course; studying a built course needs no key.</span>
          </div>

          {KEYS.map((key) => {
            const status = data.keys[key.name];
            const needed = !data.agent_route && !data.fake_llm && key.provider === provider && !status?.set && !typed[key.name];
            const inputId = `${formId}-${key.name}`;
            return (
              <div key={key.name} className="field api-keys__key">
                <div className="api-keys__head">
                  <label className="field__label" htmlFor={inputId}>
                    {key.label}
                  </label>
                  <span className={`api-keys__status${needed ? " api-keys__status--needed" : ""}`}>
                    {needed ? "needed" : statusText(status)}
                  </span>
                </div>
                <div className="api-keys__input-row">
                  <input
                    id={inputId}
                    className="input api-keys__input"
                    type="password"
                    autoComplete="off"
                    autoCapitalize="none"
                    spellCheck={false}
                    maxLength={512}
                    placeholder={status?.set ? "Paste a new key to replace it" : key.placeholder}
                    value={drafts[key.name] ?? ""}
                    onChange={(e) => setDrafts((prev) => ({ ...prev, [key.name]: e.target.value }))}
                  />
                  {status?.source === "app" && (
                    <button type="button" className="btn btn--danger" onClick={() => void remove(key.name)} disabled={busy !== null}>
                      {busy === key.name && <Spinner size={14} label="Removing" />} Remove
                    </button>
                  )}
                </div>
                <span className="field__hint">
                  {key.hint && <>{key.hint} </>}
                  Get a key at{" "}
                  <a href={key.url} target="_blank" rel="noreferrer">
                    {key.site}
                  </a>
                  {status?.source === "env" && <> · a key saved here is used instead of the one in .env</>}.
                </span>
              </div>
            );
          })}

          <p className="api-keys__note">
            <Icon name="lock" size={14} /> Keys are stored only on this computer, in <code>{data.file}</code>, and each is sent
            only to the provider it belongs to.
          </p>
        </form>
      )}
    </Dialog>
  );
}
